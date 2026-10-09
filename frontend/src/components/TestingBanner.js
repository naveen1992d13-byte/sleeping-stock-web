import React, { useEffect, useMemo, useRef, useState } from 'react';
import { resolveApiUrl } from '@/backendUrl';
import { NmtsConfirmDialog } from '@/components/NmtsConfirmDialog';
import { isTestingUiEnabled } from '@/testingUi';

const MERGE_PHRASE = 'MERGE AND CLEAR TEST DATA';
const CLEAR_PHRASE = 'CLEAR TESTING DATA';

function shortSha(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.slice(0, 7);
}

function authHeaders() {
  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('token') : '';
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

function newOperationId(prefix) {
  const rand = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${rand}`;
}

export function TestingBanner({ user = null }) {
  const enabled = isTestingUiEnabled();
  const isMaster = String(user?.role || '').toLowerCase() === 'master';
  const api = resolveApiUrl();

  const [meta, setMeta] = useState(() => ({
    pr_number: process.env.REACT_APP_TESTING_PR || '',
    git_branch: process.env.REACT_APP_TESTING_BRANCH || '',
    commit_short: process.env.REACT_APP_TESTING_COMMIT || '',
    deployed_at_ist: process.env.REACT_APP_TESTING_DEPLOYED_AT || '',
  }));
  const [pr, setPr] = useState(null);
  const [inventory, setInventory] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState('');
  const [mergeOpen, setMergeOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const mergeOp = useRef('');
  const clearOp = useRef('');
  const inFlight = useRef(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/deployment.json', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled || !data || data.environment !== 'testing') return;
        setMeta({
          pr_number: data.pr_number || '',
          git_branch: data.git_branch || '',
          commit_short: data.commit_short || shortSha(data.commit),
          deployed_at_ist: data.deployed_at_ist || data.deployed_at || '',
        });
      } catch {
        /* keep build-time fallback */
      }
    })();
    return () => { cancelled = true; };
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${api}/testing/github/status`, { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setPr(data);
      } catch {
        /* banner still shows deployment.json */
      }
    };
    load();
    const timer = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, api]);

  const eligible = Boolean(pr?.eligible);
  const liveMerge = Boolean(pr?.live_merge_enabled);
  const mergeDisabled = !isMaster || !eligible || Boolean(busy) || inFlight.current;
  const ciLabel = pr?.ci?.label || 'unknown';
  const prTitle = pr?.title || '';
  const prNumber = pr?.pr_number || meta.pr_number || 'n/a';
  const testedSha = pr?.deployed_sha || meta.commit_short || 'unknown-sha';
  const targetBranch = pr?.base_branch || pr?.production_base_branch || 'main';

  const inventorySummary = useMemo(() => {
    if (!inventory) return [];
    const rows = [];
    Object.entries(inventory.collections || {}).forEach(([name, counts]) => {
      const n = Object.values(counts || {}).reduce((sum, val) => sum + Number(val || 0), 0);
      if (n > 0) rows.push(`${name}: ${JSON.stringify(counts)}`);
    });
    if (inventory.s3) {
      rows.push(`S3 ${inventory.s3.prefix || 'testing/'} objects=${inventory.s3.object_count || 0}`);
    }
    return rows;
  }, [inventory]);

  const openMerge = () => {
    if (mergeDisabled) return;
    mergeOp.current = newOperationId('merge');
    setTyped('');
    setMergeOpen(true);
    setMessage(null);
  };

  const openClear = async () => {
    if (!isMaster || busy) return;
    setMessage(null);
    try {
      const res = await fetch(`${api}/testing/cleanup/inventory`, { headers: authHeaders() });
      const data = await res.json();
      if (!res.ok) {
        setMessage({ kind: 'error', text: data.detail || 'Could not load Testing inventory' });
        return;
      }
      setInventory(data);
      clearOp.current = newOperationId('cleanup');
      setTyped('');
      setClearOpen(true);
    } catch (err) {
      setMessage({ kind: 'error', text: String(err.message || err) });
    }
  };

  const submitMerge = async () => {
    if (inFlight.current || typed !== MERGE_PHRASE) return;
    inFlight.current = true;
    setBusy('merge');
    setMessage({ kind: 'progress', text: 'Submitting merge request…' });
    try {
      const res = await fetch(`${api}/testing/github/merge`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          confirm_text: typed,
          operation_id: mergeOp.current,
        }),
      });
      const data = await res.json();
      if (res.status === 403) {
        setMessage({ kind: 'error', text: 'Merge is limited to Testing Master / Master Admin.' });
        return;
      }
      const status = data.status || (res.ok ? 'ok' : 'failed');
      if (status === 'dry_run' || status === 'deferred') {
        setMessage({ kind: 'success', text: data.receipt?.message || data.cleanup?.message || 'Eligibility passed. Live merge is disabled until approval.' });
      } else if (status === 'idempotent_replay') {
        setMessage({ kind: 'success', text: 'This merge operation was already submitted. Showing the stored receipt.' });
      } else if (status === 'merged_unverified') {
        setMessage({ kind: 'error', text: data.cleanup?.message || data.production_verification?.message || 'Merged, but Production verification failed. Testing data retained.' });
      } else if (data.ok) {
        setMessage({ kind: 'success', text: `Merge ${status}. ${data.cleanup?.message || data.receipt?.message || ''}` });
      } else {
        setMessage({ kind: 'error', text: data.error || data.detail || `Merge blocked (${status})` });
      }
      setMergeOpen(false);
    } catch (err) {
      setMessage({ kind: 'error', text: String(err.message || err) });
    } finally {
      inFlight.current = false;
      setBusy('');
    }
  };

  const submitClear = async () => {
    if (inFlight.current || typed !== CLEAR_PHRASE) return;
    inFlight.current = true;
    setBusy('clear');
    setMessage({ kind: 'progress', text: 'Submitting Testing cleanup…' });
    try {
      const res = await fetch(`${api}/testing/cleanup`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          confirm_text: typed,
          operation_id: clearOp.current,
          reason: 'manual',
        }),
      });
      const data = await res.json();
      if (res.status === 403) {
        setMessage({ kind: 'error', text: 'Cleanup is limited to Testing Master / Master Admin.' });
        return;
      }
      const status = data.status || (res.ok ? 'ok' : 'failed');
      if (status === 'dry_run' || status === 'idempotent_replay') {
        setMessage({ kind: 'success', text: data.receipt?.message || 'Cleanup dry-run recorded. Live delete is disabled until approval.' });
      } else if (data.ok && status === 'cleaned') {
        setMessage({ kind: 'success', text: 'Testing data cleared. Reloading Production read-only view.' });
        setTimeout(() => window.location.reload(), 1200);
      } else {
        setMessage({ kind: 'error', text: data.error || data.detail || `Cleanup ${status}` });
      }
      setClearOpen(false);
    } catch (err) {
      setMessage({ kind: 'error', text: String(err.message || err) });
    } finally {
      inFlight.current = false;
      setBusy('');
    }
  };

  if (!enabled) return null;

  const msgColor = message?.kind === 'error' ? '#FECACA' : message?.kind === 'progress' ? '#FDE68A' : '#BBF7D0';

  return (
    <div
      data-testid="testing-env-banner"
      role="status"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 80,
        background: '#92400E',
        color: '#FFFBEB',
        fontSize: 12,
        lineHeight: 1.4,
        padding: '6px 12px',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '8px 16px',
        justifyContent: 'center',
        alignItems: 'center',
        letterSpacing: '0.01em',
      }}
    >
      <strong>TESTING ENVIRONMENT</strong>
      <span data-testid="testing-pr-number">PR {prNumber}{prTitle ? ` — ${prTitle}` : ''}</span>
      <span data-testid="testing-commit-sha">{shortSha(testedSha) || meta.commit_short || 'unknown-sha'}</span>
      <span data-testid="testing-pr-status">PR {pr?.state || 'unknown'}{pr?.draft ? ' (draft)' : ''}{pr?.merged ? ' / merged' : ''}</span>
      <span data-testid="testing-ci-status">CI {ciLabel}</span>
      {pr?.blockers?.length ? <span title={pr.blockers.join(', ')}>Blocked: {pr.blockers.join(', ')}</span> : null}
      {isMaster ? (
        <>
          <button
            type="button"
            data-testid="testing-merge-button"
            disabled={mergeDisabled}
            onClick={openMerge}
            title={mergeDisabled ? (pr?.blockers || []).join(', ') || 'Merge unavailable' : 'Merge to Production'}
            style={{
              background: mergeDisabled ? '#78350F' : '#F59E0B',
              color: '#111827',
              border: 0,
              borderRadius: 6,
              padding: '3px 8px',
              fontWeight: 700,
              cursor: mergeDisabled ? 'not-allowed' : 'pointer',
            }}
          >
            Merge to Production
          </button>
          <button
            type="button"
            data-testid="testing-clear-button"
            disabled={Boolean(busy)}
            onClick={openClear}
            style={{
              background: 'transparent',
              color: '#FFFBEB',
              border: '1px solid #FDE68A',
              borderRadius: 6,
              padding: '3px 8px',
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            Clear Testing Data
          </button>
        </>
      ) : null}
      {message ? (
        <span data-testid="testing-banner-message" style={{ color: msgColor, fontWeight: 600 }}>
          {message.text}
        </span>
      ) : null}

      <NmtsConfirmDialog
        open={mergeOpen}
        title="Merge to Production"
        variant="danger"
        confirmLabel="Merge"
        loading={busy === 'merge'}
        onCancel={() => { if (!busy) setMergeOpen(false); }}
        onConfirm={submitMerge}
      >
        <p className="nmts-confirm-message">
          PR #{prNumber} — {prTitle || '(no title)'}
          <br />
          Tested commit: {testedSha}
          <br />
          Target Production branch: {targetBranch}
          <br />
          Live GitHub merge: {liveMerge ? 'enabled' : 'disabled (dry-run until approval)'}
        </p>
        <p className="nmts-confirm-message">
          Warning: a successful Production deployment will trigger Testing-data cleanup.
          Type {MERGE_PHRASE} to confirm.
        </p>
        <input
          aria-label="Merge confirmation phrase"
          data-testid="testing-merge-confirm-input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={MERGE_PHRASE}
          style={{ width: '100%', marginTop: 8, padding: 8, borderRadius: 8, border: '1px solid #D1D5DB' }}
        />
      </NmtsConfirmDialog>

      <NmtsConfirmDialog
        open={clearOpen}
        title="Clear Testing Data"
        variant="danger"
        confirmLabel="Clear Testing Data"
        loading={busy === 'clear'}
        onCancel={() => { if (!busy) setClearOpen(false); }}
        onConfirm={submitClear}
      >
        <p className="nmts-confirm-message">
          This deletes Testing-only database records, overlays, tombstones and objects
          under the Testing S3 prefix. Production data is not touched.
        </p>
        <ul style={{ maxHeight: 180, overflow: 'auto', fontSize: 12, margin: '8px 0' }}>
          {inventorySummary.length ? inventorySummary.map((row) => (
            <li key={row}>{row}</li>
          )) : <li>No Testing-created counts loaded.</li>}
        </ul>
        <p className="nmts-confirm-message">Type {CLEAR_PHRASE} to confirm.</p>
        <input
          aria-label="Clear confirmation phrase"
          data-testid="testing-clear-confirm-input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={CLEAR_PHRASE}
          style={{ width: '100%', marginTop: 8, padding: 8, borderRadius: 8, border: '1px solid #D1D5DB' }}
        />
      </NmtsConfirmDialog>
    </div>
  );
}

export default TestingBanner;
