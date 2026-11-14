#!/usr/bin/env bash
# Port-forward a platform UI to localhost.
#   ./scripts/port-forward.sh argocd       -> http://localhost:8080
#   ./scripts/port-forward.sh grafana      -> http://localhost:3000
#   ./scripts/port-forward.sh prometheus   -> http://localhost:9090
#   ./scripts/port-forward.sh demo         -> http://localhost:8081
set -euo pipefail

target="${1:-}"
case "${target}" in
  argocd)
    echo "user: admin  password: $(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
    exec kubectl -n argocd port-forward svc/argocd-server 8080:80
    ;;
  grafana)
    echo "user: admin  password: $(kubectl -n monitoring get secret kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d)"
    exec kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
    ;;
  prometheus)
    exec kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
    ;;
  demo)
    exec kubectl -n demo port-forward svc/demo-app 8081:80
    ;;
  *)
    echo "usage: $0 {argocd|grafana|prometheus|demo}" >&2
    exit 1
    ;;
esac
