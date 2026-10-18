# PHASE 1 (revised) REPORT — Testing only

Branch: `cursor/website-phase2-testing` (existing PR #79). No Production deploy/merge.

## A) Isolation check

### A1. Where do User Hub Brands 5 / Dealers 6 / Branches 8 come from?

**Not from `nmts_testing` alone.** They are the merged overlay view of Production DocumentDB `nmts` (read-only) + testing deltas − tombstones.

| Evidence | Value |
|----------|-------|
| Testing `.env` | `TESTING_OVERLAY_MODE=true`, `DB_NAME=nmts_testing`, `MONGO_READONLY_URL` → DocDB |
| Overlay wrap | `backend/server.py:125-131` → `testing_overlay.wrap_testing_database` |
| Merge | `backend/testing_overlay.py:205-243` `merge_documents` (`data_origin` default `production`) |
| Readonly resolve | `backend/testing_overlay.py:1540-1559` → Production DB name `nmts` |
| Live DocDB `nmts` counts | brands **5**, dealers **6**, branches **8**, users **5** |
| Live DocDB `nmts_testing` local | brands **3**, dealers **3**, branches **4**, users **5** (incl. testing.master) |
| Frontend Hub | `frontend/src/pages/Users.js:222-228` loads `/masters/brands|dealers|branches` + `/users/list` |

**Phase 0 discrepancy:** Phase 0 “nmts_testing nearly empty” probed the wrong Mongo endpoint (Atlas `MONGO_URL` / incomplete view). Runtime write URL is DocumentDB via `resolve_mongo_url()` (`adminsleepingstock@nmts-docdb-dev…`), and Hub counts additionally merge Production via `MONGO_READONLY_URL`.

### A2. Why does `admin@sleepingstock.in` exist in Testing?

| Fact | Evidence |
|------|----------|
| Source | Live Production row id `9f775365-74fe-4b21-bc33-a94ce73cc1c0` via overlay (`data_origin=production`) |
| Password hash | Same fingerprint `9a25900ae0c0c75e` as Production DocDB `nmts.users` — **not a Testing-local copy**; it is a read of Production |
| Prior behaviour | `roles.restore_mirrored_master_admin_visibility` actively un-tombstoned it on startup |

**Fix applied:**
1. Replace restore with `hide_mirrored_production_master_admin` (`backend/roles.py`) — tombs Production `admin@` in Testing and deletes snapshot copies of that credential from `nmts_testing`.
2. Block Testing login for mirrored Production Master Admin (`server.py` `/auth/login`).
3. Filter mirrored Production Master out of `/users/list`.
4. Clear Testing Data / fresh-start re-apply the hide after tombstone wipes.
5. Only Testing Master `testing.master@sleepingstock.in` remains the Testing sign-in.

## B) Master Admin protection

Guards (backend reject, not UI-only):

| Path | Guard |
|------|-------|
| `DELETE /users/{id}`, `DELETE /users/hub/{id}` | `_forbid_protected_master_admin` + self-delete block |
| `PUT /users/hub/{id}` role/status | demote/deactivate rejected for protected MA; mirrored Prod MA read-only |
| `PUT /profile/{id}/status` | `_forbid_protected_master_admin` |
| `PUT /users/{id}/reset-password` | mirrored Prod blocked; other actors cannot reset MA password |
| Overlay `delete_one` / `delete_many` / `update_one` | `ProductionWriteBlocked` for protected / mirrored |
| Clear Testing Data | per-row `is_protected_master_admin_doc` skip (`testing_cleanup.py`) |
| Fresh-start reset | preserves Testing Master; upserts parity only |
| Startup | `ensure_testing_master_user_exists` (password only on create) |

### Why the Master disappeared when “all testing users” were removed

Root cause chain (file:line):

1. **No permanent recreate:** `NMTS_DISABLE_DEFAULT_MASTER_SEED=true` → `ensure_master_user_exists` returns early (`server.py` seed gate) and that seed targets **`admin@`**, never `testing.master@`.
2. **Wipe path:** A prior live Clear Testing Data / snapshot / raw collection wipe removed `nmts_testing.users` rows. Older cleanup used a multi-clause `delete_many` filter; overlay `delete_many` on local-only collections also lacked MA checks. Once the raw Testing row was gone, overlay could still show Production `admin@` while Testing MA login 401’d.
3. **No startup ensure for Testing MA** (added now).

UI delete of a visible MA was already blocked when `role=master_admin` / email / `is_testing_master`; the failure mode was **missing recreate after a data-plane wipe**, not a successful authorized demote.

## C) Fresh-start reset

Script: `deploy/testing/reset_testing_fresh_start.py`

- Asserts `APP_ENV=testing`, `DB_NAME=nmts_testing`, `NMTS_STORAGE_ENV=testing`
- Dry-run inventory + S3 key prefix assert (`testing/` only)
- `--execute` wipes testing collections, preserves Testing Master, tombs Production hub entities, deletes `testing/` S3 objects
- Upserts `testing.master@…` with `NMTS_TESTING_MASTER_PASSWORD` only on create (or `--force-password-from-env`)

Runtime results: see section “Runtime verification” below (filled after execute).

## D) Stability (Testing unit only)

`deploy/testing/nmts-backend-testing.service`:
- `TimeoutStopSec=45`
- `Restart=on-failure`
- `MemoryMax=650M` / `MemoryHigh=550M`
- Existing `ExecStartPost` health curl retained

`deploy/testing/deploy-testing.sh`:
- `nginx -t` required before reload; abort on failure
- Longer wait loop for `/api/maintenance/status` before declaring deploy OK
- Production unit / shared prod vhost content untouched

## E) Banner commit consistency

`TestingBanner.js` visible SHA is **only** `/deployment.json` `commit_short` (no github/status override). Deploy script writes the same JSON to backend + www root + `X-NMTS-Build`.

## F) Verification

- `pytest` Phase-1 suite: see runtime log
- Harness: `backend/tests/harness_phase1_bc.py`
- Production fingerprint end-check: PID / `.env` sha / nginx sha / Product Hub count

## Runtime verification

_(filled after deploy + reset ×2)_
