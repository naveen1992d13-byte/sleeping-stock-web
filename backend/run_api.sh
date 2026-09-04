#!/usr/bin/env bash
# Start the NMTS API with the project venv only (required for REAL S3 / boto3).
# Usage: backend/run_api.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="$ROOT/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "backend/venv is missing. From repo root run: bash .devcontainer/post-create.sh" >&2
  exit 1
fi

if ! "$PY" -c "import boto3" 2>/dev/null; then
  echo "boto3 missing in backend/venv. Re-run: bash .devcontainer/post-create.sh" >&2
  exit 1
fi

echo "Starting NMTS API with $PY"
exec "$PY" -m uvicorn server:socket_app --host 0.0.0.0 --port 8000
