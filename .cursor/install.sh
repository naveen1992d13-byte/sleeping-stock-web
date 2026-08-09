#!/usr/bin/env bash
# Cloud Agent install script for the NMTS / Sleeping Stock web app.
# Idempotent: safe to run repeatedly. Prepares the backend Python venv and the
# frontend (and, best-effort, mobile) node_modules after the repo is checked out.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Backend: Python venv + dependencies"
cd "$REPO_ROOT/backend"
# The base image may lack the stdlib venv/ensurepip support (Debian/Ubuntu split
# these into a python3-venv package). Install it if creating a venv would fail.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "    python3 venv/ensurepip missing; installing python3-venv"
  pyver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  sudo apt-get update -y
  sudo apt-get install -y "python${pyver}-venv" || sudo apt-get install -y python3-venv
fi
# The venv committed to the repo was built on GitHub Codespaces and has dead
# symlinks (no working python), so (re)create it whenever it isn't usable.
# Require a working python that also has pip, otherwise recreate from scratch.
if ! ./venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "    creating fresh virtualenv at backend/venv"
  rm -rf venv
  python3 -m venv venv
fi
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

echo "==> Frontend: node_modules"
cd "$REPO_ROOT/frontend"
# Prefer a clean, lockfile-driven install; fall back to npm install if the
# lockfile and package.json have drifted.
npm ci || npm install
# NOTE: the committed frontend/.env points REACT_APP_BACKEND_URL at a dead
# Codespaces URL. A frontend/.env.local does NOT override it, because
# craco.config.js calls dotenv.config() at load time (reading .env first) and
# dotenv never overrides an already-set variable. The local backend URL is
# instead injected as a real environment variable by .cursor/start.sh, which
# does win. Nothing to configure here.

echo "==> Mobile (Expo): best-effort dependency install"
if [ -f "$REPO_ROOT/mobile/package.json" ]; then
  (
    cd "$REPO_ROOT/mobile"
    # .npmrc pins legacy-peer-deps=true; mobile is not part of the web e2e flow,
    # so a failure here is non-fatal for the environment.
    npm install
  ) || echo "    WARNING: mobile dependency install failed (non-fatal)"
fi

echo "==> Install complete"
