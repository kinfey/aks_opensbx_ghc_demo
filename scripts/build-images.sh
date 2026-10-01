#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

require_command az
require_env ACR_NAME
require_env IMAGE_TAG
select_subscription

if [[ "$(az acr show --name "$ACR_NAME" --query adminUserEnabled -o tsv)" != "false" ]]; then
  echo "The existing registry admin account must remain disabled." >&2
  exit 1
fi

echo "Building backend image..."
az acr build \
  --registry "$ACR_NAME" \
  --platform linux/amd64 \
  --image "mcd-copilot-api:${IMAGE_TAG}" \
  "$ROOT_DIR/src/backend" \
  --only-show-errors

echo "Building frontend image..."
az acr build \
  --registry "$ACR_NAME" \
  --platform linux/amd64 \
  --image "mcd-copilot-web:${IMAGE_TAG}" \
  "$ROOT_DIR/src/frontend" \
  --only-show-errors

echo "Building Kata sandbox image with the official Copilot CLI installer..."
az acr build \
  --registry "$ACR_NAME" \
  --platform linux/amd64 \
  --image "mcd-copilot-sandbox:${IMAGE_TAG}" \
  "$ROOT_DIR/sandbox" \
  --only-show-errors
