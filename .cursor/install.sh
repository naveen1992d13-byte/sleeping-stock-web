#!/usr/bin/env bash
# Cloud Agent install script for the NMTS / Sleeping Stock web app.
# Idempotent: safe to run repeatedly. Prepares the backend Python venv and the
# frontend (and, best-effort, mobile) node_modules after the repo is checked out.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Backend: Python venv + dependencies"
cd "$REPO_ROOT/backend"
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

echo "==> Frontend: node_modules + local backend URL"
cd "$REPO_ROOT/frontend"
# Prefer a clean, lockfile-driven install; fall back to npm install if the
# lockfile and package.json have drifted.
npm ci || npm install
# The committed frontend/.env points REACT_APP_BACKEND_URL at a dead Codespaces
# URL. CRA loads .env.local at higher priority, so point the dev frontend at the
# local backend. This file is gitignored and must not be committed.
cat > .env.local <<'EOF'
REACT_APP_BACKEND_URL=http://127.0.0.1:8000
EOF

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
