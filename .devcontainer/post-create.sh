#!/usr/bin/env bash
# Codespaces / Dev Container bootstrap: project venv + full backend deps.
# Do NOT start the API with system Python — REAL S3 requires boto3 from this venv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

./venv/bin/python - <<'PY'
import sys
import boto3

exe = sys.executable
assert "backend/venv" in exe.replace("\\", "/"), exe
print("venv python:", exe)
print("boto3:", boto3.__version__)
PY

if [[ -d "$ROOT/frontend" && -f "$ROOT/frontend/package.json" ]]; then
  (cd "$ROOT/frontend" && npm install --legacy-peer-deps)
fi

echo "Codespace bootstrap complete. Start backend with:"
echo "  cd backend && ./venv/bin/python -m uvicorn server:socket_app --host 0.0.0.0 --port 8000"
