#!/usr/bin/env bash
# Configure frontend API URL for GitHub Codespaces public port forwarding.
set -euo pipefail
if [ -n "${CODESPACE_NAME:-}" ]; then
  ENV_FILE="frontend/.env.local"
  BACKEND_URL="https://${CODESPACE_NAME}-8000.app.github.dev"
  cat > "$ENV_FILE" <<EOF
# Generated for GitHub Codespaces — browser must use forwarded backend URL.
REACT_APP_BACKEND_URL=${BACKEND_URL}
EOF
  echo "Wrote ${ENV_FILE} with REACT_APP_BACKEND_URL=${BACKEND_URL}"
fi
