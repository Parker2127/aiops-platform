# AIOps Platform on Amazon EKS

An AI-driven operations platform on EKS:

1. **Log-anomaly detector** — IsolationForest trained as a SageMaker job, served in-cluster, flags anomalous app logs and posts LLM-triaged alerts to Slack.
2. **AI-driven KEDA autoscaling** — a Python KEDA external scaler that forecasts load and scales ahead of demand.
3. **FinOps optimizer** — nightly CronJob that pulls Cost Explorer data, finds idle resources, and posts an LLM-written cost report.

Everything on the cluster ships via **Argo CD GitOps**.

**Stack:** Terraform · EKS · Argo CD · KEDA · Prometheus · Python · SageMaker · Bedrock

> Work in progress — built phase by phase. See the git log for the build story.
