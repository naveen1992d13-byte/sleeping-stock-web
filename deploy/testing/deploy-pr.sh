#!/usr/bin/env bash
# Deploy one selected PR (exact branch + SHA) to the isolated testing stack.
# Never merges, never checks out production main as a deploy target, and never
# restarts production port 8000.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy-pr.sh --pr <number> [--sha <commit>] [--branch <name>]

Deploys the selected pull request's latest (or explicit) commit to
testing.sleepingstock.in by calling deploy-testing.sh. Does not merge the PR.
EOF
}

PR_NUMBER=""
HEAD_SHA=""
HEAD_REF=""

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
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "--pr must be a positive integer" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_TESTING="${NMTS_DEPLOY_TESTING:-}"
if [[ -z "$DEPLOY_TESTING" ]]; then
  if [[ -x /usr/local/bin/deploy-testing.sh ]]; then
    DEPLOY_TESTING=/usr/local/bin/deploy-testing.sh
  else
    DEPLOY_TESTING="${SCRIPT_DIR}/deploy-testing.sh"
  fi
fi
if [[ ! -x "$DEPLOY_TESTING" ]]; then
  echo "deploy-testing.sh not executable: $DEPLOY_TESTING" >&2
  exit 1
fi

ROOT="${NMTS_TESTING_ROOT:-/opt/nmts-testing}"
if [[ ! -d "$ROOT/.git" && ! -f "$ROOT/.git" ]]; then
  echo "Testing checkout missing at $ROOT" >&2
  exit 1
fi

resolve_from_github() {
  local api_url payload
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh is required to resolve PR #${PR_NUMBER} when --sha/--branch are omitted" >&2
    exit 1
  fi
  api_url="repos/naveen1992d13-byte/sleeping-stock-web/pulls/${PR_NUMBER}"
  payload="$(gh api "$api_url")"
  HEAD_SHA="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["head"]["sha"])')"
  HEAD_REF="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["head"]["ref"])')"
}

if [[ -z "$HEAD_SHA" || -z "$HEAD_REF" ]]; then
  resolve_from_github
fi

if [[ ! "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Refusing deploy: commit SHA must be a full 40-char hex object" >&2
  exit 1
fi

echo "Selected PR #${PR_NUMBER} branch=${HEAD_REF} sha=${HEAD_SHA}"
echo "This will replace whatever PR is currently on testing.sleepingstock.in"
echo "Production :8000 will not be restarted"

exec "$DEPLOY_TESTING" \
  --pr "$PR_NUMBER" \
  --sha "$HEAD_SHA" \
  --branch "$HEAD_REF"
