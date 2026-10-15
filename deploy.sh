#!/usr/bin/env bash
# Deploy one exact commit of the Notes app to the local kind cluster.
#   ./deploy.sh            deploy HEAD (pulls the CI image from GHCR, or builds locally if CI has not published it)
#   ./deploy.sh <git-sha>  deploy (or roll back to) a specific commit's image
set -euo pipefail
cd "$(dirname "$0")"

CTX=kind-notes NS=notes REPO=ghcr.io/om-midya/notes-devops
K="kubectl --context $CTX"
H="helm --kube-context $CTX"
SHA=$(git rev-parse "${1:-HEAD}")

# 1. Cluster (idempotent). Explicit --context everywhere, so other kube contexts are never touched.
kind get clusters | grep -qx notes || kind create cluster --config kind-config.yaml --wait 120s

# 2. Platform add-ons, installed once: metrics-server (for the HPA) and Prometheus/Grafana/Alertmanager.
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
$H status metrics-server -n kube-system >/dev/null 2>&1 || $H install metrics-server metrics-server/metrics-server \
  --version 3.14.0 -n kube-system --set 'args={--kubelet-insecure-tls}' --wait
$H status monitoring -n monitoring >/dev/null 2>&1 || $H install monitoring prometheus-community/kube-prometheus-stack \
  --version 92.10.1 -n monitoring --create-namespace -f monitoring-values.yaml --wait --timeout 10m

# 3. Namespace enforcing the "restricted" Pod Security Standard.
$K apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $NS
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/warn: restricted
EOF

# 4. DB secret generated on first deploy; it lives only in the cluster, never in git.
$K -n $NS get secret notes-db >/dev/null 2>&1 || $K -n $NS create secret generic notes-db --from-literal=password="$(openssl rand -hex 24)"

# 5. Image tagged with the full commit SHA. Prefer the CI-built image (the node pulls it from public GHCR);
#    otherwise build that commit locally and load it into the node.
if docker manifest inspect "$REPO:$SHA" >/dev/null 2>&1; then
  echo "using CI image $REPO:$SHA"
else
  echo "CI image not found, building $SHA locally"
  git diff --quiet HEAD || { echo "working tree is dirty: commit first so the image matches $SHA" >&2; exit 1; }
  [ "$SHA" = "$(git rev-parse HEAD)" ] || { echo "local build only works for HEAD; check out $SHA first" >&2; exit 1; }
  docker build -t "$REPO:$SHA" --build-arg APP_VERSION="$SHA" .
  kind load docker-image "$REPO:$SHA" --name notes
fi

# 6. Rolling release. If pods do not become ready in time, Helm rolls back to the last good revision.
$H upgrade --install notes chart/notes -n $NS --set image.tag="$SHA" --set monitoring.enabled=true \
  --rollback-on-failure --timeout 4m

# 7. Verify a real user journey and print the running version.
$H test notes -n $NS --logs
echo "deployed: $(curl -fsS localhost:8080/version)  ->  http://localhost:8080"
