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
  ss -lptn "sport = :${port}" 2>/dev/null | awk '
    /pid=/ {
      if (match($0, /pid=[0-9]+/)) {
        print substr($0, RSTART+4, RLENGTH-4)
        exit
      }
    }
  '
}

PROD_PID_BEFORE="$(pid_on_port "$PROD_PORT" || true)"
if [[ -z "$PROD_PID_BEFORE" ]]; then
  echo "WARNING: production port ${PROD_PORT} is not listening; continuing with testing-only restart" >&2
else
  echo "Production :${PROD_PORT} pid ${PROD_PID_BEFORE} will be left untouched"
fi

cd "$ROOT"

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

# This instance has ~1Gi RAM; CRA OOMs if the testing backend is also resident.
if systemctl is-active --quiet "$SERVICE"; then
  echo "Stopping $SERVICE during frontend build to free RAM..."
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
  NODE_OPTIONS=--max-old-space-size=2048 \
  npm run build

cp -f "$DEPLOYMENT_JSON" "$ROOT/frontend/build/deployment.json"

sudo mkdir -p "$FRONTEND_DEST"
sudo rsync -a --delete "$ROOT/frontend/build/" "$FRONTEND_DEST/"
sudo cp -f "$DEPLOYMENT_JSON" "$FRONTEND_DEST/deployment.json"
sudo cp -f "$ROOT/deploy/testing/robots.txt" "$FRONTEND_DEST/robots.txt"
sudo chown -R nginx:nginx "$FRONTEND_DEST"

echo "Restarting $SERVICE only..."
sudo systemctl restart "$SERVICE"
sudo systemctl is-active --quiet "$SERVICE"

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

echo "Testing backend is active on 127.0.0.1:${TEST_PORT} pid ${TEST_PID_AFTER}"
echo "Production :${PROD_PORT} pid ${PROD_PID_AFTER:-none} unchanged"
echo "Deployed testing metadata: PR=${PR_NUMBER:-n/a} branch=${RESOLVED_BRANCH} sha=${RESOLVED_SHORT}"
