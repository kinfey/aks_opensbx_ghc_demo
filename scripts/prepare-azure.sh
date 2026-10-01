#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

require_command az
require_env AZURE_RESOURCE_GROUP
require_env AZURE_LOCATION
select_subscription

group_location="$(az group show \
  --name "$AZURE_RESOURCE_GROUP" \
  --query location \
  --output tsv)"

if [[ "$(printf '%s' "$group_location" | tr '[:upper:]' '[:lower:]')" != \
  "$(printf '%s' "$AZURE_LOCATION" | tr '[:upper:]' '[:lower:]')" ]]; then
  echo "AZURE_LOCATION does not match the existing resource group's location." >&2
  exit 1
fi

echo "Registering required Azure resource providers..."
for provider in \
  Microsoft.App \
  Microsoft.ContainerRegistry \
  Microsoft.ContainerService \
  Microsoft.Insights \
  Microsoft.KeyVault \
  Microsoft.ManagedIdentity \
  Microsoft.Network \
  Microsoft.OperationalInsights; do
  az provider register --namespace "$provider" --wait --only-show-errors
done

echo "Checking the network feature registration (diagnostic only)..."
feature_namespace="Microsoft.Network"
feature_name="AllowBringYourOwnPublicIpAddress"
state="$(az feature show \
  --namespace "$feature_namespace" \
  --name "$feature_name" \
  --query properties.state \
  --output tsv)"

case "$state" in
  Registered)
    az provider register --namespace "$feature_namespace" --wait --only-show-errors
    ;;
  Pending|Registering|NotRegistered|Unregistered)
    echo "WARNING: $feature_namespace/$feature_name is $state; no registration change requested." >&2
    echo "Proceeding to ARM validation for API 2026-07-01; deployment and Kata kernel checks must still pass." >&2
    ;;
  *)
    echo "Unexpected feature registration state: ${state:-empty}." >&2
    exit 1
    ;;
esac
echo "Azure providers are ready; AKS acceptance is determined by ARM validation."
