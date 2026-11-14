# CLAUDE.md

## Project
AIOps platform on Amazon EKS. Personal portfolio project by a DevOps engineer.
Resume claim this project must fully demonstrate:
"Built an AI log-anomaly detector, AI-driven KEDA autoscaling and a FinOps
optimizer; shipped models via Argo CD GitOps. Stack: EKS, Python, LLMs,
KEDA, Argo CD, SageMaker."

## Stack
- Infra: Terraform >= 1.10, AWS provider 6.x, S3 remote state with native S3 locking (`use_lockfile = true`, no DynamoDB)
- Cluster: EKS 1.36 (standard support — never pick a version in extended support, it costs 6x), managed node group of 2 spot t3.medium workers, Terraform modules (no eksctl)
- Platform: Argo CD, KEDA, kube-prometheus-stack — all installed via Helm, managed by Argo CD after bootstrap
- Apps: Python 3.12 in containers, FastAPI/Flask, scikit-learn, boto3
- ML: SageMaker training job (script mode) -> model artifact in S3 -> in-cluster inference service
- LLM: Amazon Bedrock (Claude Haiku class) via boto3 for anomaly triage + FinOps summaries

## Layout
- `infra/bootstrap/` — one-time S3 state bucket (local state, applied once)
- `infra/modules/{vpc,eks,irsa}/` — reusable modules
- `infra/envs/dev/` — the only environment
- `ml/` — dataset generator, training script, SageMaker driver
- `apps/<name>/` — each app: `src/`, `tests/`, `Dockerfile`, `k8s/`
- `gitops/bootstrap/` — Argo CD app-of-apps root; `gitops/apps/` — one Application per workload
- `scripts/` — deploy, port-forward, loadtest, teardown

## Conventions
- All K8s workloads deploy ONLY through Argo CD (`gitops/`). Never `kubectl apply` app manifests by hand (bootstrapping Argo CD itself is the one exception).
- Terraform: reusable modules under `modules/`, one env under `envs/dev/`. Always `fmt` + `validate` + `plan` before apply; never apply without showing the plan.
- Python: type hints, structured JSON logging, pytest unit tests, ruff for lint/format.
- Every app ships a multi-stage Dockerfile (non-root user) and a K8s manifest set under `k8s/`.
- Idempotency: re-running any deploy script must be safe.
- Cost discipline: spot workers, single NAT gateway, teardown script must remove everything that bills.
- One commit per phase (or sub-phase) with a descriptive message — git history tells the build story.

## Environment
- AWS account 818651034041, region us-east-1.
- Config via env vars + SSM Parameter Store. No secrets in git. Slack webhook lives in AWS Secrets Manager, read via IRSA.

## Do not
- Do not commit `.tfstate`, `.terraform/`, model artifacts, datasets, or secrets.
- Do not use eksctl or console-created resources; everything in Terraform.
