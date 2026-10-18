#!/usr/bin/env bash
# Rebuild and restart ONLY the isolated testing stack.
# Never touches production uvicorn on :8000, production nginx vhosts,
# /var/www/nmts-web, or the production DocumentDB database name.
set -euo pipefail

ROOT="${NMTS_TESTING_ROOT:-/opt/nmts-testing}"
PROD_ROOT="${NMTS_PROD_ROOT:-/home/ec2-user/sleeping-stock-web}"
FRONTEND_DEST="${NMTS_TESTING_FRONTEND:-/var/www/testing-frontend}"
SERVICE="nmts-backend-testing.service"
PROD_PORT=8000
TEST_PORT=8001

PR_NUMBER=""
HEAD_SHA=""
HEAD_REF=""
LEGACY_BRANCH=""

usage() {
  cat <<'EOF'
Usage:
  deploy-testing.sh [<branch>]
  deploy-testing.sh --pr <n> --sha <40-char-sha> --branch <name>

Checks out the selected git object in /opt/nmts-testing, builds the testing
frontend, writes deployment metadata, and restarts nmts-backend-testing only.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pr)
      PR_NUMBER="${2:-}"
      shift 2
      ;;
    --sha)
      HEAD_SHA="${2:-}"
      shift 2
      ;;
    --branch)
      HEAD_REF="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "$LEGACY_BRANCH" ]]; then
        echo "Unexpected extra argument: $1" >&2
        exit 1
      fi
      LEGACY_BRANCH="$1"
      shift
      ;;
  esac
done

if [[ -n "$LEGACY_BRANCH" && -z "$HEAD_REF" ]]; then
  HEAD_REF="$LEGACY_BRANCH"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run as ec2-user (script uses sudo only for nginx static dir and systemd)." >&2
  exit 1
fi

if [[ ! -d "$ROOT/.git" && ! -f "$ROOT/.git" ]]; then
  echo "Testing checkout missing at $ROOT" >&2
  exit 1
fi

pid_on_port() {
  local port="$1"
  python3 - "$port" <<'PY'
import glob, os, sys
port = int(sys.argv[1])
hexport = f"{port:04X}"
inodes = set()
for path in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        lines = open(path).read().splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        parts = line.split()
        if parts[1].split(":")[-1].upper() == hexport and parts[3] == "0A":
            inodes.add(parts[9])
for fd in glob.glob("/proc/[0-9]*/fd/[0-9]*"):
    try:
        target = os.readlink(fd)
    except OSError:
        continue
    if target.startswith("socket:[") and target[8:-1] in inodes:
        print(fd.split("/")[2])
        break
PY
}

PROD_PID_BEFORE="$(pid_on_port "$PROD_PORT" || true)"
if [[ -z "$PROD_PID_BEFORE" ]]; then
  echo "WARNING: production port ${PROD_PORT} is not listening; continuing with testing-only restart" >&2
else
  echo "Production :${PROD_PORT} pid ${PROD_PID_BEFORE} will be left untouched"
fi

cd "$ROOT"

# Host Testing secrets live in this untracked file. Preserve them across
# git checkout so removing tracked backend/.env from the repo cannot wipe
# the Testing runtime environment. Never print the file.
HOST_ENV="$ROOT/backend/.env"
HOST_ENV_KEEP="$(mktemp /tmp/nmts-testing-env.XXXXXX)"
chmod 600 "$HOST_ENV_KEEP"
if [[ -f "$HOST_ENV" ]]; then
  cp -a "$HOST_ENV" "$HOST_ENV_KEEP"
  git update-index --no-skip-worktree backend/.env >/dev/null 2>&1 || true
  git update-index --no-assume-unchanged backend/.env >/dev/null 2>&1 || true
  git checkout -- backend/.env >/dev/null 2>&1 || true
fi

git fetch origin --prune

if [[ -n "$HEAD_SHA" ]]; then
  if [[ ! "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "--sha must be a full 40-character commit" >&2
    exit 1
  fi
  if ! git cat-file -e "${HEAD_SHA}^{commit}" 2>/dev/null; then
    if [[ -n "$HEAD_REF" ]]; then
      git fetch origin "$HEAD_REF" || git fetch origin "+refs/heads/${HEAD_REF}:refs/remotes/origin/${HEAD_REF}" || true
    fi
    git fetch origin "$HEAD_SHA" || git fetch origin --tags || true
  fi
  if ! git cat-file -e "${HEAD_SHA}^{commit}" 2>/dev/null; then
    echo "Commit ${HEAD_SHA} is not available in the testing clone" >&2
    exit 1
  fi
  git checkout --detach "$HEAD_SHA"
elif [[ -n "$HEAD_REF" ]]; then
  git fetch origin "$HEAD_REF"
  git checkout "$HEAD_REF"
  git pull --ff-only origin "$HEAD_REF"
else
  git pull --ff-only || true
fi

if [[ ! -f "$HOST_ENV" && -s "$HOST_ENV_KEEP" ]]; then
  cp -a "$HOST_ENV_KEEP" "$HOST_ENV"
  chmod 600 "$HOST_ENV"
  echo "Restored untracked Testing host environment after git checkout"
fi
rm -f "$HOST_ENV_KEEP"

RESOLVED_SHA="$(git rev-parse HEAD)"
RESOLVED_SHORT="$(git rev-parse --short=7 HEAD)"
RESOLVED_BRANCH="${HEAD_REF:-$(git rev-parse --abbrev-ref HEAD)}"
if [[ "$RESOLVED_BRANCH" == "HEAD" ]]; then
  RESOLVED_BRANCH="${HEAD_REF:-detached}"
fi
if [[ -n "$HEAD_SHA" && "$RESOLVED_SHA" != "$HEAD_SHA" ]]; then
  echo "Checked-out SHA ${RESOLVED_SHA} does not match requested ${HEAD_SHA}" >&2
  exit 1
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
if ! grep -q '^DB_NAME=nmts_testing$' "$ROOT/backend/.env"; then
  echo "Refusing to start testing backend unless DB_NAME=nmts_testing" >&2
  exit 1
fi
if grep -q '^NMTS_STORAGE_ENV=dev$' "$ROOT/backend/.env"; then
  echo "Refusing to start testing backend with production S3 prefix NMTS_STORAGE_ENV=dev" >&2
  exit 1
fi

# Explicit testing environment flag. Never print .env values.
if grep -q '^APP_ENV=' "$ROOT/backend/.env"; then
  sed -i 's/^APP_ENV=.*/APP_ENV=testing/' "$ROOT/backend/.env"
else
  printf '\nAPP_ENV=testing\n' >> "$ROOT/backend/.env"
fi
if ! grep -q '^ARCHIVE_SCHEDULER_ENABLED=false$' "$ROOT/backend/.env"; then
  if grep -q '^ARCHIVE_SCHEDULER_ENABLED=' "$ROOT/backend/.env"; then
    sed -i 's/^ARCHIVE_SCHEDULER_ENABLED=.*/ARCHIVE_SCHEDULER_ENABLED=false/' "$ROOT/backend/.env"
  else
    printf 'ARCHIVE_SCHEDULER_ENABLED=false\n' >> "$ROOT/backend/.env"
  fi
fi
if ! grep -q '^ARCHIVE_PRUNE_ENABLED=false$' "$ROOT/backend/.env"; then
  if grep -q '^ARCHIVE_PRUNE_ENABLED=' "$ROOT/backend/.env"; then
    sed -i 's/^ARCHIVE_PRUNE_ENABLED=.*/ARCHIVE_PRUNE_ENABLED=false/' "$ROOT/backend/.env"
  else
    printf 'ARCHIVE_PRUNE_ENABLED=false\n' >> "$ROOT/backend/.env"
  fi
fi

DEPLOYED_AT="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
DEPLOYED_AT_IST="$(TZ=Asia/Kolkata date +'%Y-%m-%d %H:%M:%S IST')"
DEPLOYMENT_JSON="$ROOT/backend/deployment.json"
cat > "$DEPLOYMENT_JSON" <<EOF
{
  "environment": "testing",
  "app_env": "testing",
  "pr_number": ${PR_NUMBER:-null},
  "git_branch": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$RESOLVED_BRANCH"),
  "commit": "$RESOLVED_SHA",
  "commit_short": "$RESOLVED_SHORT",
  "deployed_at": "$DEPLOYED_AT",
  "deployed_at_ist": "$DEPLOYED_AT_IST",
  "backend_port": $TEST_PORT,
  "service": "nmts-backend-testing"
}
EOF
chmod 644 "$DEPLOYMENT_JSON"

echo "Installing backend requirements into testing venv..."
"$ROOT/backend/venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"

# Testing-only systemd unit: keep Restart=on-failure and the start health check.
# Never copy or modify the Production nmts-backend.service unit.
if [[ -f "$ROOT/deploy/testing/nmts-backend-testing.service" ]]; then
  echo "Installing $SERVICE unit (Testing health check only)..."
  sudo cp "$ROOT/deploy/testing/nmts-backend-testing.service" /etc/systemd/system/nmts-backend-testing.service
  sudo systemctl daemon-reload
fi

# This instance has ~1Gi RAM; CRA OOMs if the testing backend is also resident.
# Stopping :8001 here is the known 502 Connection-refused window. Keep that
# window as short as possible: build → stage static → start API → atomic switch.
if systemctl is-active --quiet "$SERVICE"; then
  echo "Stopping $SERVICE during frontend build to free RAM (expected brief Testing 502)..."
  sudo systemctl stop "$SERVICE"
fi

echo "Building testing frontend (API=https://testing.sleepingstock.in APP_ENV=testing)..."
cd "$ROOT/frontend"
REACT_APP_BACKEND_URL=https://testing.sleepingstock.in \
  REACT_APP_APP_ENV=testing \
  REACT_APP_TESTING_PR="${PR_NUMBER}" \
  REACT_APP_TESTING_BRANCH="${RESOLVED_BRANCH}" \
  REACT_APP_TESTING_COMMIT="${RESOLVED_SHORT}" \
  REACT_APP_TESTING_DEPLOYED_AT="${DEPLOYED_AT_IST}" \
  GENERATE_SOURCEMAP=false \
  NODE_OPTIONS=--max-old-space-size=1536 \
  npm run build

cp -f "$DEPLOYMENT_JSON" "$ROOT/frontend/build/deployment.json"
cp -f "$ROOT/deploy/testing/robots.txt" "$ROOT/frontend/build/robots.txt"

# Stage complete build away from the live root, then flip once.
STAGE_DEST="${FRONTEND_DEST}.incoming-${RESOLVED_SHORT}"
PREV_DEST="${FRONTEND_DEST}.prev"
sudo rm -rf "$STAGE_DEST"
sudo mkdir -p "$STAGE_DEST"
sudo rsync -a --delete "$ROOT/frontend/build/" "$STAGE_DEST/"
sudo cp -f "$DEPLOYMENT_JSON" "$STAGE_DEST/deployment.json"
sudo chown -R nginx:nginx "$STAGE_DEST"

echo "Restarting $SERVICE before atomic frontend switch..."
sudo systemctl restart "$SERVICE"
sudo systemctl is-active --quiet "$SERVICE" || true

# Wait briefly for API before flipping HTML so login is not stuck mid-switch.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:${TEST_PORT}/api/maintenance/status" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Atomically switching Testing static root..."
sudo rm -rf "$PREV_DEST"
if [[ -e "$FRONTEND_DEST" || -L "$FRONTEND_DEST" ]]; then
  sudo mv "$FRONTEND_DEST" "$PREV_DEST"
fi
sudo mv "$STAGE_DEST" "$FRONTEND_DEST"
sudo rm -rf "$PREV_DEST"

TEST_PID_AFTER=""
HEALTH_OK=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 15 18 20 24 30; do
  TEST_PID_AFTER="$(pid_on_port "$TEST_PORT" || true)"
  if [[ -n "$TEST_PID_AFTER" ]] && curl -fsS "http://127.0.0.1:${TEST_PORT}/api/maintenance/status" >/dev/null 2>&1; then
    HEALTH_OK="1"
    break
  fi
  sleep 2
done

PROD_PID_AFTER="$(pid_on_port "$PROD_PORT" || true)"
TEST_PID_AFTER="$(pid_on_port "$TEST_PORT" || true)"
if [[ -n "$PROD_PID_BEFORE" && "$PROD_PID_AFTER" != "$PROD_PID_BEFORE" ]]; then
  echo "ERROR: production :${PROD_PORT} pid changed (${PROD_PID_BEFORE} -> ${PROD_PID_AFTER})" >&2
  exit 1
fi
if [[ -z "$TEST_PID_AFTER" ]]; then
  echo "ERROR: testing :${TEST_PORT} is not listening after restart" >&2
  exit 1
fi
if [[ -z "$HEALTH_OK" ]]; then
  echo "ERROR: testing :${TEST_PORT} is listening but /api/maintenance/status is not healthy" >&2
  exit 1
fi

echo "Testing backend is active on 127.0.0.1:${TEST_PORT} pid ${TEST_PID_AFTER}"
echo "Production :${PROD_PORT} pid ${PROD_PID_AFTER:-none} unchanged"
echo "Deployed testing metadata: PR=${PR_NUMBER:-n/a} branch=${RESOLVED_BRANCH} sha=${RESOLVED_SHORT}"
