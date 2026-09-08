#!/usr/bin/env bash
# Cloud Agent install: idempotent bootstrap for the NMTS / Sleeping Stock app.
# - Backend runs from a project venv so boto3 (REAL S3) is importable.
# - Frontend gets node_modules plus a gitignored .env.local so the SPA talks
#   to the local backend instead of the dead Codespaces URL in frontend/.env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The default base image ships Python 3.12 but may lack venv/ensurepip support.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-venv python3-pip
  else
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip
  fi
fi

# --- Backend: project venv + dependencies ---
cd "$ROOT/backend"
if [[ ! -x venv/bin/python ]]; then
  python3 -m venv venv
fi
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python -c "import boto3, fastapi, motor, socketio; print('backend deps ok: boto3', boto3.__version__)"

# --- Frontend: node modules ---
cd "$ROOT/frontend"
npm install --legacy-peer-deps

# Point the CRA dev server at the local backend (gitignored; overrides frontend/.env).
if [[ ! -f .env.local ]]; then
  printf 'REACT_APP_BACKEND_URL=http://127.0.0.1:8000\n' > .env.local
fi

echo "NMTS install complete."
