#!/usr/bin/env bash
# Cloud Agent install: idempotent bootstrap for the NMTS / Sleeping Stock web app.
# Prepares the backend Python venv (with boto3 for real S3), the frontend Node
# dependencies, and a local frontend env file that points the SPA at the local
# backend. Safe to run repeatedly.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The base image ships python3.12 but not always the venv module (ensurepip).
# Install it once if missing so `python3 -m venv` works. Idempotent: apt-get is
# a no-op when the package is already present.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "==> Installing python3-venv (ensurepip missing)"
  sudo apt-get update -y
  sudo apt-get install -y --no-install-recommends python3-venv python3.12-venv
fi

echo "==> Backend: create venv and install requirements"
cd "$ROOT/backend"
# Recreate the venv if it is missing or broken (e.g. created before the venv
# module was available, so it has no pip). Keeps install idempotent.
if [[ ! -x "venv/bin/python" ]] || ! ./venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf venv
  python3 -m venv venv
fi
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

# Sanity check: the storage layer needs boto3 importable from THIS interpreter,
# otherwise it silently falls back to local mode even when AWS secrets are set.
./venv/bin/python - <<'PY'
import sys, boto3
exe = sys.executable.replace("\\", "/")
assert "backend/venv" in exe, exe
print("venv python:", exe)
print("boto3:", boto3.__version__)
PY

echo "==> Frontend: install node dependencies"
cd "$ROOT/frontend"
npm install --legacy-peer-deps

# The committed frontend/.env points REACT_APP_BACKEND_URL at a dead Codespaces
# URL. For local dev the SPA must reach the local backend instead, or login
# fails with CORS errors. CRA loads .env.local at higher priority than .env.
# This file is gitignored; regenerate it every install so it stays correct.
echo "==> Frontend: write .env.local pointing at local backend"
cat > "$ROOT/frontend/.env.local" <<'EOF'
REACT_APP_BACKEND_URL=http://127.0.0.1:8000
EOF

echo "Install complete."
echo "  Backend: cd backend && ./venv/bin/python -m uvicorn server:socket_app --host 0.0.0.0 --port 8000"
echo "  Frontend: cd frontend && npm start"
