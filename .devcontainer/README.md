# Codespaces / Dev Container

## Why this exists

Starting the FastAPI backend with **system Python** (or a bare `uvicorn` on `$PATH`)
often omits `boto3` even when AWS Codespaces secrets are set. The storage layer then
falls back to local mode (`real_s3=false`) and archives fail as NOT TRANSFERRED.

This bootstrap always creates/uses **`backend/venv`** and installs
**`backend/requirements.txt`** so `boto3` is importable from that interpreter.

## Start the backend (required pattern)

```bash
bash backend/run_api.sh
```

equivalent:

```bash
cd backend
./venv/bin/python -m uvicorn server:socket_app --host 0.0.0.0 --port 8000
```

Do **not** use system `python` / `python3` / system `uvicorn`. A Codespaces browser at
`https://<name>-3000.app.github.dev` posts to `https://<name>-8000.app.github.dev`.
If that process is system Python, Upload Center returns:

`Upload failed: private REAL S3 is unavailable. Product Excel cannot be stored safely.`
(`POST /api/upload/v2` → 503)

On a good start you must see: `Object storage: backend=REAL S3 real_s3=True`.

## S3 secrets (Codespaces)

Configure repository/Codespace secrets (names only — never commit values):

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `NMTS_S3_BUCKET`
- `AWS_REGION`
- Optional: `NMTS_STORAGE_ENV` (defaults via app config; often `dev`)

Optional local overlay (gitignored): `backend/.env.s3.local`.

Keep `ARCHIVE_PRUNE_ENABLED=false` unless you intentionally authorize prune.
