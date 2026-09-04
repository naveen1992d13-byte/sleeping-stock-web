#!/usr/bin/env bash
# Lightweight check after Codespace start — does not install secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/backend/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "WARNING: backend/venv missing — run .devcontainer/post-create.sh" >&2
  exit 0
fi

"$PY" - <<'PY'
import sys
try:
    import boto3
except ModuleNotFoundError as e:
    print("WARNING: boto3 missing in backend/venv — re-run post-create.sh", file=sys.stderr)
    raise SystemExit(0)
print(f"OK: {sys.executable} boto3={boto3.__version__}")
print("Start the API with backend/venv only (not system python3):")
print("  bash backend/run_api.sh")
print("If browser upload 503 REAL S3 unavailable, the running process is not this venv.")
PY
