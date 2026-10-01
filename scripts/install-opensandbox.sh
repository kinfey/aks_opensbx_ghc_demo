#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

for command in az git helm kubectl; do
  require_command "$command"
done

for name in \
  AZURE_RESOURCE_GROUP \
  AKS_NAME \
  BACKEND_APP_NAME \
  OPENSANDBOX_API_KEY \
  OPENSANDBOX_SECURE_ACCESS_KEY; do
  require_env "$name"
done
select_subscription

OPENSANDBOX_REPOSITORY="${OPENSANDBOX_REPOSITORY:-https://github.com/opensandbox-group/OpenSandbox.git}"
OPENSANDBOX_REF="${OPENSANDBOX_REF:-3738975fc7b1da6875694f912b0422fe5d622064}"
SOURCE_DIR="$ROOT_DIR/.build/OpenSandbox"

mkdir -p "$ROOT_DIR/.build"
if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  git clone --filter=blob:none "$OPENSANDBOX_REPOSITORY" "$SOURCE_DIR"
fi
git -C "$SOURCE_DIR" remote set-url origin "$OPENSANDBOX_REPOSITORY"
git -C "$SOURCE_DIR" fetch --depth=1 origin "$OPENSANDBOX_REF"
git -C "$SOURCE_DIR" checkout --detach "$OPENSANDBOX_REF"

az aks get-credentials \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --overwrite-existing \
  --only-show-errors

if ! kubectl get runtimeclass kata-vm-isolation >/dev/null 2>&1; then
  echo "kata-vm-isolation RuntimeClass is missing; refusing to install OpenSandbox." >&2
  exit 1
fi

kubectl create namespace opensandbox-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace opensandbox --dry-run=client -o yaml | kubectl apply -f -

printf '%s' "$OPENSANDBOX_API_KEY" |
  kubectl create secret generic opensandbox-runtime-secrets \
    --namespace opensandbox-system \
    --from-file=api-key=/dev/stdin \
    --dry-run=client -o yaml |
  kubectl apply -f -

printf 'a=%s' "$OPENSANDBOX_SECURE_ACCESS_KEY" |
  kubectl create secret generic opensandbox-secure-access \
    --namespace opensandbox-system \
    --from-file=keys=/dev/stdin \
    --from-literal=active-key=a \
    --dry-run=client -o yaml |
  kubectl apply -f -

kubectl apply -f "$ROOT_DIR/k8s/opensandbox/batchsandbox-template-configmap.yaml"

helm upgrade --install opensandbox-base "$SOURCE_DIR/manifests/charts/base" \
  --set fastSandbox.namespaces.createSystem=false

helm upgrade --install opensandbox-controller "$SOURCE_DIR/manifests/charts/controller" \
  --namespace opensandbox-system \
  --values "$ROOT_DIR/k8s/opensandbox/controller-values.yaml"

helm upgrade --install opensandbox-ingress "$SOURCE_DIR/manifests/charts/ingress-gateway" \
  --namespace opensandbox-system \
  --values "$ROOT_DIR/k8s/opensandbox/ingress-values.yaml"

helm upgrade --install opensandbox-server "$SOURCE_DIR/manifests/charts/server" \
  --namespace opensandbox-system \
  --values "$ROOT_DIR/k8s/opensandbox/server-values.yaml"

kubectl apply -f "$ROOT_DIR/k8s/opensandbox/internal-services.yaml"

kubectl rollout status deployment/opensandbox-controller-manager \
  --namespace opensandbox-system --timeout=300s
kubectl rollout status deployment/opensandbox-ingress-gateway \
  --namespace opensandbox-system --timeout=300s
kubectl rollout status deployment/opensandbox-server \
  --namespace opensandbox-system --timeout=300s

wait_for_internal_ip() {
  local service_name="$1"
  local ip=""
  for _ in $(seq 1 90); do
    ip="$(kubectl get service "$service_name" \
      --namespace opensandbox-system \
      --output jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
    if [[ -n "$ip" ]]; then
      printf '%s' "$ip"
      return 0
    fi
    sleep 5
  done
  return 1
}

server_ip="$(wait_for_internal_ip opensandbox-server-internal)" || {
  echo "OpenSandbox lifecycle service did not receive an internal IP." >&2
  exit 1
}
gateway_ip="$(wait_for_internal_ip opensandbox-ingress-gateway-internal)" || {
  echo "OpenSandbox ingress gateway did not receive an internal IP." >&2
  exit 1
}

helm upgrade opensandbox-server "$SOURCE_DIR/manifests/charts/server" \
  --namespace opensandbox-system \
  --values "$ROOT_DIR/k8s/opensandbox/server-values.yaml" \
  --set-string "server.gateway.host=${gateway_ip}"

kubectl rollout status deployment/opensandbox-server \
  --namespace opensandbox-system --timeout=180s

sandbox_image="$(az containerapp show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$BACKEND_APP_NAME" \
  --query "properties.template.containers[0].env[?name=='SANDBOX_IMAGE'].value | [0]" \
  --output tsv)"
bash "$ROOT_DIR/scripts/warm-sandbox-image.sh" "$sandbox_image"

az containerapp update \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$BACKEND_APP_NAME" \
  --set-env-vars "OPENSANDBOX_DOMAIN=http://${server_ip}" \
  --only-show-errors >/dev/null

echo "OpenSandbox is available to the internal backend at http://${server_ip}."
