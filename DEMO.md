# 10-minute demo

Prerequisite: the [Quickstart](README.md#quickstart) is done, and every Argo app shows Synced/Healthy.
Open three terminals. 📸 marks a screenshot for `docs/screenshots/`.

## 1. The platform is GitOps (1 min)

```bash
terraform -chdir=infra/envs/dev state list | tee docs/screenshots/01-terraform-state.txt
./scripts/port-forward.sh argocd          # http://localhost:8080
```

**Expected:** the Argo CD UI shows `root` plus 6 apps: kube-prometheus-stack, keda, keda-scaler, demo-app, log-detector, finops-optimizer. All are **Synced / Healthy**.
📸 `02-argocd-apps.png`, the app tiles. Then open `demo-app` → *History and rollback* to show deploys are git commits.

```bash
kubectl get pods -A | tee docs/screenshots/03-pods.txt
```

## 2. The model came from SageMaker (1 min)

SageMaker console → *Training jobs* → `aiops-log-anomaly-<timestamp>`.
**Expected:** Status *Completed*, and the *Metrics* section shows precision ≈ 1.0 and recall ≈ 0.87. Billable time is under a minute (spot).
📸 `04-sagemaker-job.png`

```bash
grep MODEL_S3_URI apps/log-detector/k8s/kustomization.yaml   # the pinned artifact
git log --oneline -- apps/log-detector/k8s/kustomization.yaml # model releases = commits
kubectl -n log-detector logs deploy/log-detector | grep "model loaded"
```

## 3. Anomaly → model → LLM triage → Slack (3 min)

```bash
# terminal A
kubectl -n log-detector logs -f deploy/log-detector | grep --line-buffered "anomaly alert"

# terminal B
./scripts/port-forward.sh demo            # http://localhost:8081
curl -s -X POST localhost:8081/chaos/on
```

**Expected within ~30 s:**
- Terminal A prints `anomaly alert` lines for `exception`, `resource_exhaustion`, `dependency` and `security`. That's one per type, because the rest are deduplicated for 5 minutes.
- Slack receives an alert for each type with the pod, score and offending line, plus an **LLM triage** block: category, severity, likely cause, next step.

📸 `05-slack-anomaly.png`

```bash
curl -s -X POST localhost:8081/chaos/off
```

The talking point: the threshold is the model's `decision_function < 0`. In normal traffic there are no alerts, and the score distribution is the `detector_anomaly_score` histogram in Prometheus.

## 4. Scaling ahead of the load (4 min)

```bash
# terminal A
kubectl -n demo get hpa -w

# terminal B
./scripts/loadtest.sh                     # 10 -> 300 rps over 5 min, hold 2 min

# terminal C
./scripts/port-forward.sh grafana         # Dashboards -> "AIOps - Predictive autoscaling"
```

**Expected:**
- `kubectl get hpa` shows KEDA's `keda-hpa-demo-app` with the external metric `s0-forecast_rps` climbing.
- In the top Grafana panel, the **forecast rps** line runs ahead of **actual rps** during the ramp, and the **replicas** step line rises before actual load reaches each 50-rps threshold. Replicas level off near 6 at 300 rps, then step down slowly after the run.

📸 `06-grafana-predictive-scaling.png`. This is the key screenshot: replicas lead the load.

```bash
kubectl -n demo get hpa,deploy | tee docs/screenshots/07-hpa.txt
kubectl -n keda-scaler logs deploy/keda-scaler | tail -5   # current vs forecast vs expected replicas
```

## 5. FinOps report (1 min)

```bash
kubectl -n finops create job finops-manual-$(date +%s) --from=cronjob/finops-optimizer
kubectl -n finops logs -f job/$(kubectl -n finops get jobs -o name | tail -1 | cut -d/ -f2)
aws s3 ls s3://aiops-artifacts-818651034041/finops-reports/
```

**Expected:** Slack receives a "FinOps <date>" message with the 7-day total, the 30-day forecast, the estimated monthly idle-resource waste, the LLM commentary and the top services. The full markdown report is in S3.
📸 `08-finops-slack.png`

The talking point: the LLM never invents numbers. It only narrates the JSON, and the `unverified_numbers` check flags anything it didn't get from the data. `DRY_RUN=true`, so it only says what it *would* delete.

## Afterwards

```bash
./scripts/teardown.sh
```

## Evidence checklist

- [ ] `01-terraform-state.txt`: `terraform state list`
- [ ] `02-argocd-apps.png`: Argo CD, all apps synced
- [ ] `03-pods.txt`: `kubectl get pods -A`
- [ ] `04-sagemaker-job.png`: training job page with metrics
- [ ] `05-slack-anomaly.png`: Slack anomaly alert with LLM triage
- [ ] `06-grafana-predictive-scaling.png`: rps vs forecast vs replicas during the ramp
- [ ] `07-hpa.txt`: `kubectl get hpa,deploy`
- [ ] `08-finops-slack.png`: FinOps Slack report
