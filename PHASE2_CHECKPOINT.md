# Phase 2 Website Testing checkpoint

Date: 2026-10-15
Branch: `cursor/website-phase2-testing`
Base: `a71c25c` (not `main`)
`main` untouched: `ffe483a2cd8deb1433c564f30d4875dfe4659d86`

## Files changed
- `backend/reports_center.py` — first_present, IST helpers, terminal-only missed opportunity, stock-movement disabled, composite scope, Unknown aging, Excel totals skip Unit Value/MAV/Part Value
- `backend/analytics_center.py` — Unknown bucket, IST period, order-saving qty vs lines, `$top` fallback, part-type lookup without silent cap
- `backend/file_objects.py` — inline PDF/images, structured 404/502
- `backend/query_desk.py` — attachment auth/scope, S3 failure 502, preview inline
- `backend/notice_board.py` — preview inline
- `backend/server.py` — profile self-edit, email uniqueness, password_changed_at, JWT iat, order history IST filters, correction 409, physical enquiry filters
- `frontend/src/pages/NMTSMobile.js` — Physical Verification enquiry-only; Auto Sessions inline detail; no Today’s APOS list
- `frontend/src/pages/Products.js` — search only on Enter/Search
- `frontend/src/pages/DashboardLayout.js` + `Orders.js` — one keep-alive Order Desk instance; Order Number input
- `frontend/src/pages/OrderHistory.js` — From/To/Status/Enquiry, default today
- `frontend/src/pages/NoticeBoard.js`, `QueryDesk.js`, `utils/blobResponse.js` — inline open, JSON blob errors, revoke URLs
- `frontend/src/App.js`, `Profile.js` — refreshUser after self-edit
- `frontend/src/pages/Analytics.js`, `Reports.js` — Unknown aging, stale guards, IST, stock-movement disabled
- `.gitignore` — `backend/.env` untracked
- `backend/tests/test_phase2_reports_profile_unit.py`, `test_analytics_four_section.py`

## Tests run
- `pytest backend/tests/test_phase2_reports_profile_unit.py backend/tests/test_analytics_four_section.py` — 33 passed

## Safety
- Production backend `:8000` PID 1236056 cwd `/opt/nmts-production/backend` left running
- Testing backend `:8001` PID 1398625 cwd `/opt/nmts-testing/backend`
- Three distinct `.env` inodes: Production, Testing host, git checkout
- Host env backups: `/home/ec2-user/.nmts-backups/*.env.phase2` (not in git)
- No Production merge/deploy; no credential rotation; no git history rewrite
- No Testing/Production record writes in this phase (code-only)

## Rollback
- Restore Testing env: `cp /home/ec2-user/.nmts-backups/testing-backend.env.phase2 /opt/nmts-testing/backend/.env`
- Redeploy previous Testing SHA `a71c25c` with `deploy/testing/deploy-testing.sh`
- Production: no change; do not restart
