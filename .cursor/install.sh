#!/usr/bin/env bash
# Cloud Agent install: backend venv (+boto3) and frontend deps.
# Idempotent — safe to run repeatedly against cached state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The default base image may ship a Python without venv/ensurepip support.
# Install it once so `python3 -m venv` can bootstrap pip. Guarded so the
# script still works on images that already have it.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y python3-venv
fi

# Backend: project venv is required so boto3 is importable (REAL S3),
# otherwise the storage layer silently falls back to local mode.
cd "$ROOT/backend"
if [[ ! -x venv/bin/python ]]; then
  python3 -m venv venv
fi
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

# Frontend: .npmrc equivalent handled explicitly (peer-dep conflicts).
cd "$ROOT/frontend"
npm install --legacy-peer-deps

# Point the CRA dev server at the local backend. The committed frontend/.env
# can carry a non-local REACT_APP_BACKEND_URL; .env.local overrides it and is
# gitignored, so never commit it.
if [[ ! -f .env.local ]]; then
  echo "REACT_APP_BACKEND_URL=http://127.0.0.1:8000" > .env.local
fi

echo "sleeping-stock-web install complete."
