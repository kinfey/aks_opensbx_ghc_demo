#!/usr/bin/env bash
set -euo pipefail

kubectl get runtimeclass kata-vm-isolation >/dev/null
kubectl wait node \
  --selector='kubernetes.azure.com/kata-vm-isolation=true' \
  --for=condition=Ready --timeout=300s
nodes="$(kubectl get nodes \
  --selector='kubernetes.azure.com/kata-vm-isolation=true' --output name)"
node_count="$(printf '%s\n' "$nodes" | awk '/^node\// {count++} END {print count+0}')"
if [[ "$node_count" != "3" ]]; then
  echo "Expected exactly three registered Kata-capable Kubernetes nodes." >&2
  exit 1
fi
kubectl create namespace opensandbox --dry-run=client -o yaml | kubectl apply -f -

check_pod="kata-kernel-check-$(date -u +%s)-$$"
cleanup() {
  if ! kubectl delete pod "$check_pod" --namespace opensandbox \
    --ignore-not-found --wait=false >/dev/null; then
    echo "WARNING: Could not clean up Kata verification pod $check_pod." >&2
  fi
}
trap cleanup EXIT

kubectl run "$check_pod" \
  --namespace opensandbox \
  --image=mcr.microsoft.com/aks/fundamental/base-ubuntu:v0.0.11 \
  --restart=Never \
  --overrides='{
    "spec": {
      "runtimeClassName": "kata-vm-isolation",
      "nodeSelector": {"kubernetes.azure.com/kata-vm-isolation": "true"},
      "containers": [{
        "name": "kata-kernel-check",
        "image": "mcr.microsoft.com/aks/fundamental/base-ubuntu:v0.0.11",
        "command": ["sh", "-c", "uname -r"],
        "resources": {"limits": {"cpu": "1", "memory": "512Mi"}}
      }]
    }
  }' >/dev/null
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded \
  "pod/${check_pod}" --namespace opensandbox --timeout=180s

kernel="$(kubectl logs "$check_pod" --namespace opensandbox)"
if [[ "$kernel" != *mshv* ]]; then
  echo "Kernel '$kernel' does not prove Kata MSHV isolation; refusing to continue." >&2
  exit 1
fi
kubectl delete pod "$check_pod" --namespace opensandbox --wait=true >/dev/null
trap - EXIT
echo "Kata kernel: $kernel"
