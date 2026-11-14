#!/usr/bin/env bash
# Remove everything that bills. Safe to re-run; each step skips what is already gone.
#   ./scripts/teardown.sh                 # asks before terraform destroy
#   AUTO_APPROVE=1 ./scripts/teardown.sh
# Keeps: the Terraform state bucket (infra/bootstrap, pennies/month) so redeploy is one apply away.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
CLUSTER="aiops-dev"

if kubectl cluster-info >/dev/null 2>&1; then
  echo ">> 1/4 Deleting Argo CD root app (cascades to every child app and its resources)"
  if kubectl -n argocd get application root >/dev/null 2>&1; then
    kubectl -n argocd delete application root --wait=false
    for _ in $(seq 1 60); do
      left="$(kubectl -n argocd get applications --no-headers 2>/dev/null | wc -l | tr -d ' ')"
      [[ "${left}" == "0" ]] && break
      echo "   waiting on ${left} application(s)..."
      sleep 10
    done
  fi

  echo ">> 2/4 Removing anything that would orphan AWS resources"
  # LoadBalancer Services create ELBs and PVCs create EBS volumes outside Terraform.
  kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}' \
    | while read -r ns name; do [[ -n "${ns}" ]] && kubectl -n "${ns}" delete svc "${name}"; done
  kubectl delete pvc -A --all --wait=true --timeout=5m || true

  echo ">> 3/4 Uninstalling Argo CD"
  helm uninstall argocd -n argocd 2>/dev/null || true
  for ns in argocd demo log-detector keda-scaler finops monitoring keda; do
    kubectl delete namespace "${ns}" --ignore-not-found --wait=false
  done
else
  echo ">> cluster unreachable - skipping in-cluster cleanup"
fi

echo ">> 4/4 terraform destroy"
cd "${ROOT}/infra/envs/dev"
terraform init -input=false >/dev/null
if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
  terraform destroy -auto-approve
else
  terraform destroy
fi

echo
echo ">> Leftover check (${REGION}) - every list below should be empty"
echo "-- EBS volumes tagged for the cluster:"
aws ec2 describe-volumes --region "${REGION}" \
  --filters "Name=tag-key,Values=kubernetes.io/cluster/${CLUSTER}" \
  --query 'Volumes[].[VolumeId,Size,State]' --output text
echo "-- Load balancers:"
aws elbv2 describe-load-balancers --region "${REGION}" --query 'LoadBalancers[].LoadBalancerName' --output text
echo "-- NAT gateways (not deleted):"
aws ec2 describe-nat-gateways --region "${REGION}" \
  --filter "Name=state,Values=available,pending" --query 'NatGateways[].NatGatewayId' --output text
echo "-- Unassociated Elastic IPs:"
aws ec2 describe-addresses --region "${REGION}" --query 'Addresses[?AssociationId==null].PublicIp' --output text
echo "-- Running SageMaker training jobs:"
aws sagemaker list-training-jobs --region "${REGION}" --status-equals InProgress \
  --query 'TrainingJobSummaries[].TrainingJobName' --output text
echo
echo "Done. State bucket kept: aiops-tfstate-$(aws sts get-caller-identity --query Account --output text)"
