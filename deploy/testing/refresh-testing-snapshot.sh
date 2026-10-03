#!/usr/bin/env bash
# Manually refresh nmts_testing from current production nmts data (full collection mirror).
# Never writes to production nmts, never restarts port 8000, never copies S3 dev/ objects.
# Preserves Testing Master Admin and testing-created rows.
set -euo pipefail

ROOT="${NMTS_TESTING_ROOT:-/opt/nmts-testing}"
BACKEND="${ROOT}/backend"
SCRIPT="${BACKEND}/scripts/refresh_testing_snapshot.py"
if [[ ! -f "$SCRIPT" ]]; then
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/backend/scripts/refresh_testing_snapshot.py"
  BACKEND="$(cd "$(dirname "$SCRIPT")/.." && pwd)"
fi

if [[ ! -f "$BACKEND/.env" ]]; then
  echo "Missing testing backend .env" >&2
  exit 1
fi
if grep -q '^DB_NAME=nmts$' "$BACKEND/.env"; then
  echo "Refusing snapshot refresh against production DB_NAME=nmts" >&2
  exit 1
fi
if ! grep -q '^DB_NAME=nmts_testing$' "$BACKEND/.env"; then
  echo "Refusing snapshot refresh unless DB_NAME=nmts_testing" >&2
  exit 1
fi

PROD_PID_BEFORE="$(ss -lptn 'sport = :8000' 2>/dev/null | awk '/pid=/{if (match($0,/pid=[0-9]+/)) {print substr($0,RSTART+4,RLENGTH-4); exit}}' || true)"

export NMTS_TESTING_BACKEND="$BACKEND"
PYTHON="${BACKEND}/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${NMTS_PROD_ROOT:-/home/ec2-user/sleeping-stock-web}/backend/venv/bin/python"
fi

"$PYTHON" "$SCRIPT" "$@"

PROD_PID_AFTER="$(ss -lptn 'sport = :8000' 2>/dev/null | awk '/pid=/{if (match($0,/pid=[0-9]+/)) {print substr($0,RSTART+4,RLENGTH-4); exit}}' || true)"
if [[ -n "$PROD_PID_BEFORE" && "$PROD_PID_AFTER" != "$PROD_PID_BEFORE" ]]; then
  echo "ERROR: production :8000 pid changed during snapshot refresh" >&2
  exit 1
fi
echo "Production port 8000 was not restarted."
