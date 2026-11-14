#!/usr/bin/env bash
# Install Argo CD with Helm and hand the cluster over to the app-of-apps root.
# Idempotent: helm upgrade --install + kubectl apply are safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-10.18.0}"
REPO_URL="${REPO_URL:-https://github.com/Parker2127/aiops-platform.git}"

command -v helm >/dev/null || { echo "helm not found" >&2; exit 1; }
kubectl cluster-info >/dev/null || { echo "kubectl has no cluster; run aws eks update-kubeconfig" >&2; exit 1; }

echo ">> Installing Argo CD chart ${ARGOCD_CHART_VERSION}"
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd \
  --namespace argocd --create-namespace \
  --version "${ARGOCD_CHART_VERSION}" \
  --values "${ROOT}/gitops/bootstrap/argocd-values.yaml" \
  --wait --timeout 10m

# Private repo? Export GITHUB_TOKEN (read-only fine-grained PAT) and Argo gets a repo secret.
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  echo ">> Registering private repo credentials"
  kubectl -n argocd create secret generic aiops-repo \
    --from-literal=type=git \
    --from-literal=url="${REPO_URL}" \
    --from-literal=username=git \
    --from-literal=password="${GITHUB_TOKEN}" \
    --dry-run=client -o yaml \
    | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml \
    | kubectl apply -f -
fi

echo ">> Applying app-of-apps root"
kubectl apply -f "${ROOT}/gitops/bootstrap/root-app.yaml"

echo
echo "Argo CD admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
echo
echo
echo "Next: ./scripts/port-forward.sh argocd   (then https://localhost:8080, user admin)"
echo "      kubectl -n argocd get applications -w"
