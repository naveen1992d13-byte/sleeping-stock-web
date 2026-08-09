#!/usr/bin/env bash
# Per-boot startup for the NMTS / Sleeping Stock dev environment.
# Launches the backend (FastAPI + Socket.IO) and frontend (CRA/craco) dev
# servers in the background, idempotently, then returns. Logs go to /tmp/nmts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="/tmp/nmts"
mkdir -p "$LOG_DIR"

# Returns success if something is already listening on the given local TCP port.
listening() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3>&- 3<&-; }

if listening 8000; then
  echo "backend: already listening on :8000, leaving it running"
else
  echo "backend: starting uvicorn on :8000"
  ( cd "$REPO_ROOT/backend" \
      && nohup ./venv/bin/uvicorn server:socket_app --host 0.0.0.0 --port 8000 \
         >"$LOG_DIR/backend.log" 2>&1 & )
fi

if listening 3000; then
  echo "frontend: already listening on :3000, leaving it running"
else
  echo "frontend: starting CRA dev server on :3000"
  # The committed frontend/.env points REACT_APP_BACKEND_URL at a dead Codespaces
  # URL. A frontend/.env.local cannot override it here because craco.config.js
  # calls dotenv.config() at load time (reading .env first), and dotenv never
  # overrides an already-set variable. Exporting the variable in the shell wins,
  # because dotenv also won't override a real environment variable.
  ( cd "$REPO_ROOT/frontend" \
      && REACT_APP_BACKEND_URL="http://127.0.0.1:8000" BROWSER=none \
         nohup npm start >"$LOG_DIR/frontend.log" 2>&1 & )
fi

# Wait for the backend to accept connections (fast). Non-fatal on timeout so a
# slow boot never blocks the environment from coming up.
for _ in $(seq 1 30); do
  if listening 8000; then echo "backend: ready on :8000"; break; fi
  sleep 1
done

echo "start: backend + frontend launch issued (frontend compile continues in background; logs in $LOG_DIR)"
