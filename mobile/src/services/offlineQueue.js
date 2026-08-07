// Offline Stock Verification queue (Part 15 / Part 22 of the spec).
//
// Stock Verification must keep working with no signal on a warehouse floor.
// Every submission is written to a local SQLite table first; a background
// sync (triggered by connectivity changes, app foregrounding, and a
// periodic timer) then pushes queued rows to the backend one at a time.
//
// Guarantees:
// - A record is removed from the local queue ONLY after the backend
//   confirms it was saved (2xx response).
// - A record that fails to sync (network/server error) stays queued and is
//   retried later — it is never silently dropped.
// - Each record carries a stable client-generated `client_id`. The backend
//   stores a unique (device_id, client_id) index, so if a sync attempt
//   succeeds on the server but the response is lost before we mark it
//   synced locally, retrying the same record is a no-op server-side —
//   no duplicate stock verification rows are ever created.
import * as SQLite from 'expo-sqlite';
import NetInfo from '@react-native-community/netinfo';
import * as Crypto from 'expo-crypto';
import { submitStockVerification, ApiError } from '../api';

const DB_NAME = 'sleeping_stock_offline.db';
const MAX_RETRY_BEFORE_BACKOFF = 3;

let dbPromise = null;
let isSyncing = false;
let netInfoUnsubscribe = null;
let periodicTimer = null;
const listeners = new Set();

function getDb() {
  if (!dbPromise) {
    dbPromise = SQLite.openDatabaseAsync(DB_NAME);
  }
  return dbPromise;
}

export async function initOfflineQueue() {
  const db = await getDb();
  await db.execAsync(`
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS verification_queue (
      client_id TEXT PRIMARY KEY NOT NULL,
      part_number TEXT NOT NULL,
      physical_qty REAL NOT NULL,
      location TEXT,
      remark TEXT,
      entry_method TEXT NOT NULL,
      verification_session_id TEXT,
      part_name TEXT,
      is_new_part INTEGER NOT NULL DEFAULT 0,
      verification_type TEXT NOT NULL DEFAULT 'physical',
      damage_qty REAL NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      sync_status TEXT NOT NULL DEFAULT 'pending',
      retry_count INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      last_attempt_at TEXT
    );
  `);

  // Safe migrations for users upgrading from version 1.1.x.
  for (const statement of [
    `ALTER TABLE verification_queue ADD COLUMN verification_session_id TEXT`,
    `ALTER TABLE verification_queue ADD COLUMN part_name TEXT`,
    `ALTER TABLE verification_queue ADD COLUMN is_new_part INTEGER NOT NULL DEFAULT 0`,
    `ALTER TABLE verification_queue ADD COLUMN verification_type TEXT NOT NULL DEFAULT 'physical'`,
    `ALTER TABLE verification_queue ADD COLUMN damage_qty REAL NOT NULL DEFAULT 0`,
  ]) {
    try {
      await db.execAsync(statement);
    } catch (error) {
      // Column already exists.
    }
  }
}

function notifyListeners() {
  listeners.forEach((fn) => {
    try {
      fn();
    } catch (error) {
      console.log('[offlineQueue] listener error', error);
    }
  });
}

/** Subscribe to queue changes (e.g. to refresh a "Pending sync: N" badge). */
export function subscribeToQueueChanges(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

/**
 * Adds a Stock Verification record to the local queue immediately.
 * Call this instead of calling the API directly from the Verification
 * screen — enqueueAndTrySync() handles both offline and online cases.
 */
export async function enqueueVerification({ partNumber, partName, physicalQty, location, remark, entryMethod, verificationSessionId, isNewPart, verificationType, damageQty }) {
  const db = await getDb();
  const clientId = Crypto.randomUUID();
  const createdAt = new Date().toISOString();
  await db.runAsync(
    `INSERT INTO verification_queue
      (client_id, part_number, part_name, physical_qty, location, remark, entry_method, verification_session_id, is_new_part, verification_type, damage_qty, created_at, sync_status, retry_count)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)`,
    [
      clientId,
      partNumber,
      partName || '',
      physicalQty,
      location || '',
      remark || '',
      entryMethod,
      verificationSessionId || '',
      isNewPart ? 1 : 0,
      verificationType || 'physical',
      Number(damageQty || 0),
      createdAt,
    ]
  );
  notifyListeners();
  return clientId;
}

/** Enqueues the record, then immediately attempts a sync if online. */
export async function enqueueAndTrySync(record) {
  const clientId = await enqueueVerification(record);
  // Fire-and-forget: the UI should not block on network availability.
  syncQueue().catch((error) => console.log('[offlineQueue] immediate sync failed', error));
  return clientId;
}

export async function getQueuedRecords() {
  const db = await getDb();
  return db.getAllAsync(`SELECT * FROM verification_queue ORDER BY created_at ASC`);
}

export async function getPendingCount() {
  const db = await getDb();
  const row = await db.getFirstAsync(
    `SELECT COUNT(*) as count FROM verification_queue WHERE sync_status != 'synced'`
  );
  return row?.count || 0;
}

/**
 * Pushes every queued record to the backend, one at a time (sequential, so
 * a burst of scans doesn't hammer the API or race the same request).
 * Safe to call repeatedly/concurrently — re-entrant calls are ignored.
 */
export async function syncQueue() {
  if (isSyncing) return { synced: 0, failed: 0, skipped: true };
  isSyncing = true;
  let synced = 0;
  let failed = 0;

  try {
    const netState = await NetInfo.fetch();
    if (!netState.isConnected || netState.isInternetReachable === false) {
      return { synced: 0, failed: 0, skipped: true, reason: 'offline' };
    }

    const db = await getDb();
    const rows = await db.getAllAsync(
      `SELECT * FROM verification_queue WHERE sync_status IN ('pending', 'failed') ORDER BY created_at ASC`
    );

    for (const row of rows) {
      // Simple exponential backoff: skip rows that failed recently and
      // haven't waited long enough yet, so a broken record doesn't spin.
      if (row.retry_count >= MAX_RETRY_BEFORE_BACKOFF && row.last_attempt_at) {
        const waitMs = Math.min(2 ** row.retry_count, 60) * 1000;
        const elapsed = Date.now() - new Date(row.last_attempt_at).getTime();
        if (elapsed < waitMs) continue;
      }

      await db.runAsync(
        `UPDATE verification_queue SET sync_status = 'syncing' WHERE client_id = ?`,
        [row.client_id]
      );

      try {
        await submitStockVerification({
          partNumber: row.part_number,
          partName: row.part_name || '',
          physicalQty: row.physical_qty,
          location: row.location,
          remark: row.remark,
          entryMethod: row.entry_method,
          clientId: row.client_id,
          verificationSessionId: row.verification_session_id || '',
          isNewPart: Boolean(row.is_new_part),
          verificationType: row.verification_type || 'physical',
          damageQty: row.damage_qty || 0,
        });
        // Confirmed saved server-side (or already had been, per client_id
        // idempotency) — safe to remove from the local queue now.
        await db.runAsync(`DELETE FROM verification_queue WHERE client_id = ?`, [row.client_id]);
        synced += 1;
      } catch (error) {
        failed += 1;
        const message = error instanceof ApiError ? error.message : String(error?.message || error);
        await db.runAsync(
          `UPDATE verification_queue
             SET sync_status = 'failed', retry_count = retry_count + 1,
                 last_error = ?, last_attempt_at = ?
           WHERE client_id = ?`,
          [message, new Date().toISOString(), row.client_id]
        );
        console.log('[offlineQueue] sync failed for', row.client_id, message);
        // If the whole connection just dropped mid-loop, stop this pass —
        // remaining rows will retry on the next trigger.
        if (error instanceof ApiError && (error.kind === 'network' || error.kind === 'timeout')) {
          break;
        }
      }
    }
  } finally {
    isSyncing = false;
    notifyListeners();
  }

  return { synced, failed, skipped: false };
}

/**
 * Wires automatic sync to: connectivity regained, and a periodic timer as
 * a backstop (covers cases like a flaky connection that NetInfo reports as
 * "connected" but the request still fails). Call once from App.js on
 * startup; call the returned teardown() on unmount.
 */
export function startAutoSync({ periodicIntervalMs = 60000 } = {}) {
  if (netInfoUnsubscribe) return () => {}; // already running

  netInfoUnsubscribe = NetInfo.addEventListener((state) => {
    if (state.isConnected && state.isInternetReachable !== false) {
      syncQueue().catch((error) => console.log('[offlineQueue] connectivity sync failed', error));
    }
  });

  periodicTimer = setInterval(() => {
    syncQueue().catch((error) => console.log('[offlineQueue] periodic sync failed', error));
  }, periodicIntervalMs);

  // Try once immediately on startup too (e.g. app was killed while offline
  // with records still queued).
  syncQueue().catch((error) => console.log('[offlineQueue] startup sync failed', error));

  return function stopAutoSync() {
    if (netInfoUnsubscribe) {
      netInfoUnsubscribe();
      netInfoUnsubscribe = null;
    }
    if (periodicTimer) {
      clearInterval(periodicTimer);
      periodicTimer = null;
    }
  };
}
