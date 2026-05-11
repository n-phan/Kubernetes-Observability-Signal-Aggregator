#!/usr/bin/env bash
#
# One-shot deploy of the K8s Observability Signal Aggregator demo onto a Linux
# host. Installs (idempotently):
#   1. k3s                       — single-node Kubernetes
#   2. Helm
#   3. kube-prometheus-stack     — Prometheus + node-exporter + kube-state-metrics
#      loki-stack                — Loki + Promtail
#   4. demo workloads            — Jaeger + service-a/service-b  (namespace obs-demo)
#   5. the aggregator API        — NodePort 30080         (namespace obs-demo)
#
# Run ON the target Linux server, from the repo root:
#       bash deploy.sh
#
# Demo images: the manifests expect obs/service-a:dev, obs/service-b:dev and obs/aggregator:dev
# in the node's containerd. This script gets them one of two ways:
#   a) if obs-images.tar (or $IMAGES_TAR) is next to this script, it imports it
#      (build it elsewhere with:  docker compose build  &&
#       docker tag $(docker compose images -q service-a) obs/service-a:dev  ... &&
#       docker save obs/service-a:dev ... obs/aggregator:dev -o obs-images.tar);
#   b) else, if this host has Docker and docker-compose.yml is present, it builds
#      and imports them directly.
#
# Optional env vars:
#   ANTHROPIC_API_KEY=sk-ant-...   enables "Analyze with AI" (you can also set the
#                                  key later in the frontend's Config LLM panel)
#   K3S_EXTRA_SAN=<ip-or-host>     extra SAN for the k3s API-server cert, e.g. a
#                                  Tailscale IP — needed to use kubectl from elsewhere
#   IMAGES_TAR=/path/to/tar        override the image tar location
#
# Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS_DEMO="obs-demo"
NS_MON="monitoring"
IMAGES_TAR="${IMAGES_TAR:-$SCRIPT_DIR/obs-images.tar}"
IMAGE_NAMES=(service-a service-b aggregator)

log()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ -f "$SCRIPT_DIR/k8s/demo.yaml" ] && [ -f "$SCRIPT_DIR/k8s/aggregator.yaml" ] \
  || die "Run this from the repo root — k8s/demo.yaml and k8s/aggregator.yaml not found next to the script."

# ── 1. k3s ────────────────────────────────────────────────────────────────
if have kubectl && kubectl get nodes >/dev/null 2>&1; then
  log "Kubernetes already reachable — skipping k3s install"
else
  log "Installing k3s..."
  exec_args="--write-kubeconfig-mode=644"
  [ -n "${K3S_EXTRA_SAN:-}" ] && exec_args="$exec_args --tls-san=${K3S_EXTRA_SAN}"
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="$exec_args" sh -
fi
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
[ -r "$KUBECONFIG" ] || die "Cannot read $KUBECONFIG. Re-run with sudo, or: sudo cp $KUBECONFIG ~/.kube/config && sudo chown \$(id -u):\$(id -g) ~/.kube/config && export KUBECONFIG=~/.kube/config"
log "Waiting for the node to be Ready..."
kubectl wait --for=condition=Ready node --all --timeout=180s

# ── 2. Helm ───────────────────────────────────────────────────────────────
if ! have helm; then
  log "Installing Helm..."
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# ── 3. Observability stack ────────────────────────────────────────────────
log "Updating Helm repos..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

log "Installing kube-prometheus-stack (Prometheus + node-exporter + kube-state-metrics)..."
helm upgrade --install kps prometheus-community/kube-prometheus-stack \
  -n "$NS_MON" --create-namespace --set grafana.enabled=false --wait --timeout 10m

log "Installing loki-stack (Loki + Promtail)..."
helm upgrade --install loki grafana/loki-stack -n "$NS_MON" --wait --timeout 10m

# ── 4. Demo images into k3s containerd ────────────────────────────────────
if [ -f "$IMAGES_TAR" ]; then
  log "Importing demo images from $IMAGES_TAR ..."
  sudo k3s ctr images import "$IMAGES_TAR"
elif have docker && [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
  log "No image tar found — building images with Docker and importing into containerd..."
  ( cd "$SCRIPT_DIR" && docker compose build "${IMAGE_NAMES[@]}" )
  ( cd "$SCRIPT_DIR"
    for n in "${IMAGE_NAMES[@]}"; do docker tag "$(docker compose images -q "$n")" "obs/$n:dev"; done
    docker save $(printf 'obs/%s:dev ' "${IMAGE_NAMES[@]}")
  ) | sudo k3s ctr images import -
else
  die "Demo images not available. Put obs-images.tar next to this script, or run from the repo root on a host with Docker. See the comment block at the top of this script."
fi
sudo k3s ctr images ls 2>/dev/null | grep -q 'obs/aggregator:dev' \
  || die "Image import failed — obs/aggregator:dev not found in containerd."

# ── 5. Deploy demo workloads + aggregator ─────────────────────────────────
log "Deploying demo workloads (Jaeger + service-a/service-b) into namespace $NS_DEMO..."
kubectl apply -f "$SCRIPT_DIR/k8s/demo.yaml"

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  log "Creating/updating the aggregator-secrets Secret (ANTHROPIC_API_KEY)..."
  kubectl -n "$NS_DEMO" create secret generic aggregator-secrets \
    --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
fi

log "Deploying the aggregator (NodePort 30080)..."
kubectl apply -f "$SCRIPT_DIR/k8s/aggregator.yaml"
# Pick up a freshly-imported image even if the deployment already existed.
kubectl -n "$NS_DEMO" rollout restart deployment/aggregator >/dev/null 2>&1 || true

log "Waiting for workloads to become ready..."
for d in jaeger service-a service-b aggregator; do
  kubectl -n "$NS_DEMO" rollout status "deployment/$d" --timeout=180s
done

NODE_IP="$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"

cat <<DONE

============================================================
  Done.

  Aggregator API:   http://${NODE_IP}:30080   (NodePort)
  Sanity check:     curl -s http://${NODE_IP}:30080/services
  Generate traffic: kubectl -n ${NS_DEMO} run hit --rm -i --image=curlimages/curl --restart=Never -- \\
                      sh -c 'for i in \$(seq 1 20); do curl -s -o /dev/null http://service-a:8001/api/data; done; echo ok'

  Run the frontend locally (on your workstation, in the repo):
      docker compose up -d --no-deps frontend          # http://localhost:8081
  then in the UI:  Setting -> API Endpoint & Namespace
      API Endpoint = http://${NODE_IP}:30080
      Namespace    = ${NS_DEMO}
  and pick a service from the sidebar -> Service list, then Query.

  Namespaces:  observability stack -> '${NS_MON}',  demo workloads -> '${NS_DEMO}'
============================================================
DONE
