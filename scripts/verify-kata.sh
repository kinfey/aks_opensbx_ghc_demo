#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

for command in az kubectl python3; do
  require_command "$command"
done
for name in AZURE_RESOURCE_GROUP AKS_NAME; do
  require_env "$name"
done
select_subscription

cluster_id="$(az aks show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_NAME" --query id --output tsv)"

az rest --method get \
  --url "https://management.azure.com${cluster_id}?api-version=2026-07-01" \
  --query properties --output json |
  python3 -c '
import json
import sys

cluster = json.load(sys.stdin)
pools = cluster.get("agentPoolProfiles", [])
expected = {
    "name": "kata",
    "count": 3,
    "vmSize": "Standard_D4s_v5",
    "osSKU": "AzureLinux",
    "workloadRuntime": "KataVmIsolation",
    "mode": "System",
    "provisioningState": "Succeeded",
}
if cluster.get("provisioningState") != "Succeeded" or len(pools) != 1:
    sys.exit("AKS must be Succeeded with exactly one node pool.")
for key, value in expected.items():
    if pools[0].get(key) != value:
        sys.exit(f"Kata pool verification failed: {key} must be {value}.")
if pools[0].get("nodeTaints"):
    sys.exit("The shared system/Kata pool must not have custom node taints.")
print("Confirmed API 2026-07-01: three D4s_v5 Azure Linux Kata nodes.")
'

az aks get-credentials \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --overwrite-existing \
  --only-show-errors

bash "$ROOT_DIR/scripts/verify-kata-runtime.sh"
