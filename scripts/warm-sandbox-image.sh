#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 || ! "$1" =~ ^[a-zA-Z0-9./:@_-]+$ ]]; then
  echo "Usage: $0 <sandbox-image-reference>" >&2
  exit 1
fi
name="opensandbox-image-warmup-$(date -u +%s)-$$"
cleanup() {
  if ! kubectl delete daemonset "$name" --namespace opensandbox \
    --ignore-not-found --wait=true --timeout=120s >/dev/null; then
    echo "ERROR: Could not clean up image warmup DaemonSet $name." >&2
    exit 1
  fi
}
trap cleanup EXIT
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: $name
  namespace: opensandbox
spec:
  selector:
    matchLabels:
      app: $name
  template:
    metadata:
      labels:
        app: $name
    spec:
      runtimeClassName: kata-vm-isolation
      nodeSelector:
        kubernetes.azure.com/kata-vm-isolation: "true"
      terminationGracePeriodSeconds: 5
      containers:
        - name: image-warmup
          image: $1
          imagePullPolicy: IfNotPresent
          command: ["sh", "-c", "sleep 1800"]
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "1"
              memory: "512Mi"
EOF
kubectl rollout status "daemonset/$name" --namespace opensandbox --timeout=600s
desired="$(kubectl get daemonset "$name" --namespace opensandbox \
  --output jsonpath='{.status.desiredNumberScheduled}')"
if [[ "$desired" != "3" ]]; then
  echo "Expected sandbox image warmup on exactly three Kata nodes." >&2
  exit 1
fi
echo "Sandbox image cached and started on all three Kata nodes."
