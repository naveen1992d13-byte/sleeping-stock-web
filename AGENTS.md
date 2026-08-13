# AGENTS.md

## Cursor Cloud specific instructions

This repo is the **NMTS / Sleeping Stock web app** — a dealer-network non-moving
(sleeping) stock tracking system. It has two services:

- `backend/` — FastAPI + Socket.IO app (`server.py`), data in MongoDB via `motor`.
- `frontend/` — React (Create React App via `@craco/craco`) single-page app.

Dependencies (Python venv, frontend/mobile `node_modules`) are installed by the
Cursor Cloud update script, so you normally don't need to install them again.

**Codespaces / Dev Containers:** use `.devcontainer/` (`post-create.sh` creates
`backend/venv` and installs `backend/requirements.txt`, including `boto3`).
Starting the API with system Python causes `ModuleNotFoundError: boto3` and the
storage layer falls back to local mode even when AWS secrets are set. Always
run from the project venv (see below). Details: `.devcontainer/README.md`.

### Running the backend

- The ASGI entrypoint that includes Socket.IO is `server:socket_app` (not just
  `server:app`). Run it **only** with the project venv:
  `cd backend && ./venv/bin/python -m uvicorn server:socket_app --host 0.0.0.0 --port 8000`
  (equivalent: `./venv/bin/uvicorn server:socket_app --host 0.0.0.0 --port 8000`).
  Do not use system `python` / system `uvicorn`.
- Config comes from `backend/.env` (committed). `MONGO_URL` points at a **shared
  hosted MongoDB Atlas cluster** with real-ish data — there is no local Mongo.
  Treat writes as affecting shared data; prefer clearly-labelled test values.
- For REAL S3, Codespaces secrets (or gitignored `backend/.env.s3.local`) must
  provide `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `NMTS_S3_BUCKET`,
  `AWS_REGION` (optional `NMTS_STORAGE_ENV`). Keep `ARCHIVE_PRUNE_ENABLED=false`
  unless prune is explicitly authorized.
- On startup the backend seeds a master admin if none exists. Default login:
  `admin@sleepingstock.in` / `admin123`.
- Known non-fatal startup log: a `Product Hub index creation failed ... E11000
  duplicate key` error on `request_headers` used to appear from a legacy full
  unique index on `(order_id, supplying_dealer, supplying_branch)` while
  historical retry/dispatch duplicates existed. Startup now uses a **partial
  unique index** (`status: Requested` only) so history is preserved and the
  error should no longer appear.

### Running the frontend

- Start it with `cd frontend && npm start` (CRA/craco dev server on port 3000).
- **Important gotcha:** the committed `frontend/.env` sets `REACT_APP_BACKEND_URL`
  to a dead GitHub Codespaces URL. For local dev the frontend must point at the
  local backend instead, or login fails with CORS errors. This is handled by a
  gitignored `frontend/.env.local` (created by the update script) containing:
  `REACT_APP_BACKEND_URL=http://127.0.0.1:8000`. CRA loads `.env.local` with
  higher priority than `.env`. If login can't reach the backend, verify this file
  exists. Do not commit it.

### Tests / lint

- Backend API tests: `cd backend && REACT_APP_BACKEND_URL=http://127.0.0.1:8000
  ./venv/bin/python -m pytest tests/test_api.py -v`. These hit a **running**
  backend over HTTP (start it first) and talk to the shared Atlas DB, so the run
  is slow (a few minutes). One test, `TestUsers::test_create_user`, is a known
  pre-existing failure: its payload omits the now-required `state` field, so the
  server correctly returns `400 State is required`.
- Frontend lint runs as part of `npm start` / `npm run build` (craco + eslint
  `react-hooks` rules). Current code compiles with only `exhaustive-deps`
  warnings.
