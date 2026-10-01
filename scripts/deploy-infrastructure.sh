#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

mode="${1:-deploy}"
if [[ $# -gt 1 || ( "$mode" != "deploy" && "$mode" != "--validate-only" ) ]]; then
  echo "Usage: $0 [--validate-only]" >&2
  exit 1
fi

for command in az python3; do
  require_command "$command"
done

for name in \
  AZURE_RESOURCE_GROUP \
  AZURE_LOCATION \
  RESOURCE_PREFIX \
  AKS_NAME \
  ACR_NAME \
  LOG_ANALYTICS_WORKSPACE_NAME \
  BACKEND_APP_NAME \
  FRONTEND_APP_NAME \
  IMAGE_TAG \
  GITHUB_TOKEN \
  MCD_MCP_TOKEN \
  OPENSANDBOX_API_KEY \
  OPENSANDBOX_SECURE_ACCESS_KEY \
  AKS_AUTHORIZED_IP_RANGES; do
  require_env "$name"
done
select_subscription

acr_server="$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)"
backend_image="${acr_server}/mcd-copilot-api:${IMAGE_TAG}"
frontend_image="${acr_server}/mcd-copilot-web:${IMAGE_TAG}"
sandbox_image="${acr_server}/mcd-copilot-sandbox:${IMAGE_TAG}"

IFS=',' read -r -a authorized_ranges <<<"$AKS_AUTHORIZED_IP_RANGES"
authorized_ranges_json="$(printf '%s\n' "${authorized_ranges[@]}" | python3 -c \
  'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"

export BACKEND_IMAGE="$backend_image"
export FRONTEND_IMAGE="$frontend_image"
export SANDBOX_IMAGE="$sandbox_image"
export AUTHORIZED_RANGES_JSON="$authorized_ranges_json"

parameters_file="$(mktemp)"
chmod 600 "$parameters_file"
cleanup() {
  rm -f "$parameters_file"
}
trap cleanup EXIT

python3 - "$parameters_file" <<'PY'
import json
import os
import sys

names = {
    "location": "AZURE_LOCATION",
    "resourcePrefix": "RESOURCE_PREFIX",
    "aksName": "AKS_NAME",
    "acrName": "ACR_NAME",
    "logAnalyticsWorkspaceName": "LOG_ANALYTICS_WORKSPACE_NAME",
    "backendAppName": "BACKEND_APP_NAME",
    "frontendAppName": "FRONTEND_APP_NAME",
    "backendImage": "BACKEND_IMAGE",
    "frontendImage": "FRONTEND_IMAGE",
    "sandboxImage": "SANDBOX_IMAGE",
    "githubToken": "GITHUB_TOKEN",
    "mcdMcpToken": "MCD_MCP_TOKEN",
    "opensandboxApiKey": "OPENSANDBOX_API_KEY",
    "opensandboxSecureAccessKey": "OPENSANDBOX_SECURE_ACCESS_KEY",
}
parameters = {name: {"value": os.environ[env]} for name, env in names.items()}
parameters["aksAuthorizedIpRanges"] = {
    "value": json.loads(os.environ["AUTHORIZED_RANGES_JSON"])
}
parameters["deployWorkloads"] = {"value": True}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(
        {
            "$schema": (
                "https://schema.management.azure.com/schemas/"
                "2019-04-01/deploymentParameters.json#"
            ),
            "contentVersion": "1.0.0.0",
            "parameters": parameters,
        },
        stream,
    )
PY

echo "Validating the complete infrastructure, including the three-node Kata pool..."
az deployment group validate \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file "$ROOT_DIR/infra/main.bicep" \
  --parameters "@$parameters_file" \
  --only-show-errors >/dev/null

if [[ "$mode" == "--validate-only" ]]; then
  az deployment group what-if \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --template-file "$ROOT_DIR/infra/main.bicep" \
    --parameters "@$parameters_file" \
    --no-pretty-print \
    --only-show-errors
  exit 0
fi

set_deploy_workloads() {
  python3 - "$parameters_file" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    document = json.load(stream)
document["parameters"]["deployWorkloads"]["value"] = sys.argv[2] == "true"
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(document, stream)
PY
}

set_deploy_workloads false
deployment_name="opensandbox-mcd-$(date -u +%Y%m%d%H%M%S)"
foundation_deployment="${deployment_name}-foundation"
workload_deployment="${deployment_name}-workloads"

echo "Deploying identities, networking, secrets, and prerequisite RBAC..."
az deployment group create \
  --name "$foundation_deployment" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file "$ROOT_DIR/infra/main.bicep" \
  --parameters "@$parameters_file" \
  --only-show-errors >/dev/null

deployment_output() {
  az deployment group show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$foundation_deployment" \
    --query "properties.outputs.$1.value" \
    --output tsv
}

wait_for_role() {
  local scope="$1"
  local principal_id="$2"
  local role_name="$3"
  local attempt
  for attempt in $(seq 1 10); do
    if az role assignment list \
      --scope "$scope" \
      --assignee-object-id "$principal_id" \
      --query "[].roleDefinitionName" \
      --output tsv 2>/dev/null | grep -Fxq "$role_name"; then
      echo "$role_name role is ready."
      return 0
    fi
    sleep 30
  done
  echo "$role_name role did not propagate within five minutes." >&2
  return 1
}

acr_id="$(az acr show --name "$ACR_NAME" --query id --output tsv)"
key_vault_name="$(deployment_output keyVaultName)"
key_vault_id="$(az keyvault show --name "$key_vault_name" --query id --output tsv)"
aks_subnet_id="$(deployment_output aksSubnetId)"
aks_principal_id="$(deployment_output aksIdentityPrincipalId)"
backend_principal_id="$(deployment_output backendIdentityPrincipalId)"
frontend_principal_id="$(deployment_output frontendIdentityPrincipalId)"

echo "Waiting for prerequisite RBAC propagation..."
wait_for_role "$aks_subnet_id" "$aks_principal_id" "Network Contributor"
wait_for_role "$acr_id" "$backend_principal_id" "AcrPull"
wait_for_role "$acr_id" "$frontend_principal_id" "AcrPull"
wait_for_role "$key_vault_id" "$backend_principal_id" "Key Vault Secrets User"

set_deploy_workloads true

echo "Deploying AKS and Container Apps..."
az deployment group create \
  --name "$workload_deployment" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file "$ROOT_DIR/infra/main.bicep" \
  --parameters "@$parameters_file" \
  --only-show-errors >/dev/null

kubelet_principal_id="$(az aks show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --query identityProfile.kubeletidentity.objectId \
  --output tsv)"
wait_for_role "$acr_id" "$kubelet_principal_id" "AcrPull"

bash "$ROOT_DIR/scripts/verify-kata.sh"

az deployment group show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$workload_deployment" \
  --query properties.outputs \
  --output json
