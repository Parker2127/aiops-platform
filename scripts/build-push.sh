#!/usr/bin/env bash
# Build linux/amd64 images, push to ECR, and pin the new tag in each app's
# kustomization.yaml. Then commit + push: Argo CD rolls it out (git is the deploy).
#   ./scripts/build-push.sh                 # all apps
#   ./scripts/build-push.sh demo-app        # one app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
TAG="${TAG:-$(git -C "${ROOT}" rev-parse --short HEAD)}"
APPS=("$@")
if [[ ${#APPS[@]} -eq 0 ]]; then
  APPS=(demo-app log-detector keda-scaler finops-optimizer)
fi

if [[ -n "$(git -C "${ROOT}" status --porcelain -- apps ml)" ]]; then
  echo "warning: uncommitted changes under apps/ or ml/ - tag ${TAG} won't match the source" >&2
fi

aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${REGISTRY}" >/dev/null

for app in "${APPS[@]}"; do
  image="${REGISTRY}/aiops/${app}:${TAG}"
  echo ">> ${app} -> ${image}"
  # Context is the repo root so log-detector can COPY ml/features.py.
  docker buildx build --platform linux/amd64 \
    -f "${ROOT}/apps/${app}/Dockerfile" \
    -t "${image}" --push "${ROOT}"

  kfile="${ROOT}/apps/${app}/k8s/kustomization.yaml"
  # Rewrite only the newTag line of this app's images entry.
  sed -i.bak -E "s|^([[:space:]]*newTag:).*|\1 \"${TAG}\"|" "${kfile}" && rm -f "${kfile}.bak"
done

echo
echo "Pinned tag ${TAG}. Ship it:"
echo "  git add apps/*/k8s/kustomization.yaml && git commit -m 'Deploy ${TAG}' && git push"
