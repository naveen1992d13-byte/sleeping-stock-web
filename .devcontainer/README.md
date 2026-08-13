# Codespaces / Dev Container

## Why this exists

Starting the FastAPI backend with **system Python** (or a bare `uvicorn` on `$PATH`)
often omits `boto3` even when AWS Codespaces secrets are set. The storage layer then
falls back to local mode (`real_s3=false`) and archives fail as NOT TRANSFERRED.

This bootstrap always creates/uses **`backend/venv`** and installs
**`backend/requirements.txt`** so `boto3` is importable from that interpreter.

## Start the backend (required pattern)

```bash
cd backend
./venv/bin/python -m uvicorn server:socket_app --host 0.0.0.0 --port 8000
```

Do **not** use system `python` / system `uvicorn` for this app.

## S3 secrets (Codespaces)

Configure repository/Codespace secrets (names only — never commit values):

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `NMTS_S3_BUCKET`
- `AWS_REGION`
- Optional: `NMTS_STORAGE_ENV` (defaults via app config; often `dev`)

Optional local overlay (gitignored): `backend/.env.s3.local`.

Keep `ARCHIVE_PRUNE_ENABLED=false` unless you intentionally authorize prune.
