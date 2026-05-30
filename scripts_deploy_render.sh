#!/usr/bin/env bash
set -euo pipefail

# Render Blueprint deploy helper
# Prereq:
#   1) render CLI installed and logged in
#   2) run from repo root

if ! command -v render >/dev/null 2>&1; then
  echo "[ERROR] render CLI not found."
  echo "Install: npm i -g @renderinc/cli"
  exit 1
fi

if [ ! -f "render.yaml" ]; then
  echo "[ERROR] render.yaml not found in current directory"
  exit 1
fi

echo "[INFO] Applying Render Blueprint..."
render blueprint apply --output json || {
  echo "[ERROR] blueprint apply failed"
  exit 1
}

echo "[INFO] Done. Set these env vars in Render dashboard if not set yet:"
echo "AAE_SECRET_KEY"
echo "AAE_ADMIN_USER"
echo "AAE_ADMIN_PASSWORD"
echo ""
echo "Then open the service URL and verify:"
echo "/healthz"
echo "/terms"
echo "/privacy"
