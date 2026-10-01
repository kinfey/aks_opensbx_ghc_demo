#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

for name in \
  AZURE_SUBSCRIPTION \
  AZURE_RESOURCE_GROUP \
  AZURE_LOCATION \
  RESOURCE_PREFIX \
  AKS_NAME \
  ACR_NAME \
  LOG_ANALYTICS_WORKSPACE_NAME \
  BACKEND_APP_NAME \
  FRONTEND_APP_NAME \
  GITHUB_TOKEN \
  MCD_MCP_TOKEN \
  AKS_AUTHORIZED_IP_RANGES; do
  require_env "$name"
done

export IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d%H%M%S)}"
export OPENSANDBOX_API_KEY="${OPENSANDBOX_API_KEY:-$(openssl rand -hex 32)}"
export OPENSANDBOX_SECURE_ACCESS_KEY="${OPENSANDBOX_SECURE_ACCESS_KEY:-$(openssl rand -base64 32)}"

"$ROOT_DIR/scripts/prepare-azure.sh"
"$ROOT_DIR/scripts/deploy-infrastructure.sh" --validate-only
"$ROOT_DIR/scripts/build-images.sh"
"$ROOT_DIR/scripts/deploy-infrastructure.sh"
"$ROOT_DIR/scripts/install-opensandbox.sh"
"$ROOT_DIR/scripts/verify-deployment.sh"
