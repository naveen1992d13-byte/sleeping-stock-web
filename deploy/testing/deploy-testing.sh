#!/usr/bin/env bash
# Rebuild and restart ONLY the isolated testing stack.
# Never touches production uvicorn on :8000, production nginx vhosts,
# /var/www/nmts-web, or the production DocumentDB database name.
set -euo pipefail

ROOT="${NMTS_TESTING_ROOT:-/opt/nmts-testing}"
PROD_ROOT="${NMTS_PROD_ROOT:-/home/ec2-user/sleeping-stock-web}"
FRONTEND_DEST="${NMTS_TESTING_FRONTEND:-/var/www/testing-frontend}"
SERVICE="nmts-backend-testing.service"
BRANCH="${1:-}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run as ec2-user (script uses sudo only for nginx static dir and systemd)." >&2
  exit 1
fi

if [[ ! -d "$ROOT/.git" && ! -f "$ROOT/.git" ]]; then
  echo "Testing checkout missing at $ROOT" >&2
  exit 1
fi

cd "$ROOT"

if [[ -n "$BRANCH" ]]; then
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull origin "$BRANCH"
else
  git fetch origin
  git pull --ff-only || true
fi

# Share production venv / node_modules to stay on the 8G disk; packages are identical.
if [[ ! -e "$ROOT/backend/venv" ]]; then
  ln -s "$PROD_ROOT/backend/venv" "$ROOT/backend/venv"
fi
if [[ ! -e "$ROOT/frontend/node_modules" && -d "$PROD_ROOT/frontend/node_modules" ]]; then
  ln -s "$PROD_ROOT/frontend/node_modules" "$ROOT/frontend/node_modules"
fi

if [[ ! -f "$ROOT/backend/.env" ]]; then
  echo "Missing $ROOT/backend/.env — copy deploy/testing/env.testing.example and fill secrets." >&2
  exit 1
fi
if grep -q '^DB_NAME=nmts$' "$ROOT/backend/.env"; then
  echo "Refusing to start testing backend with production DB_NAME=nmts" >&2
  exit 1
fi

echo "Installing backend requirements into testing venv..."
"$ROOT/backend/venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"

echo "Building testing frontend (API=https://testing.sleepingstock.in)..."
cd "$ROOT/frontend"
REACT_APP_BACKEND_URL=https://testing.sleepingstock.in \
  GENERATE_SOURCEMAP=false \
  NODE_OPTIONS=--max-old-space-size=2048 \
  npm run build

sudo mkdir -p "$FRONTEND_DEST"
sudo rsync -a --delete "$ROOT/frontend/build/" "$FRONTEND_DEST/"
sudo cp -f "$ROOT/deploy/testing/robots.txt" "$FRONTEND_DEST/robots.txt"
sudo chown -R nginx:nginx "$FRONTEND_DEST"

echo "Restarting $SERVICE only..."
sudo systemctl restart "$SERVICE"
sudo systemctl is-active --quiet "$SERVICE"
echo "Testing backend is active on 127.0.0.1:8001"
echo "Production service was not restarted."
