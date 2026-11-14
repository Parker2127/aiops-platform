#!/usr/bin/env bash
# Open-loop load ramp against the demo app, generated from inside the cluster
# (an ephemeral test pod, not a workload - it is deleted when the run ends).
#   ./scripts/loadtest.sh                       # 10 -> 300 rps over 5 min, hold 2 min
#   START=20 PEAK=200 RAMP=180 HOLD=60 ./scripts/loadtest.sh
# Watch:  kubectl -n demo get hpa -w      and Grafana "AIOps - Predictive autoscaling"
set -euo pipefail

START="${START:-10}"
PEAK="${PEAK:-300}"
RAMP="${RAMP:-300}"
HOLD="${HOLD:-120}"
LOAD_MS="${LOAD_MS:-20}"

read -r -d '' SCRIPT <<PY || true
import time, urllib.request
from concurrent.futures import ThreadPoolExecutor
URL = "http://demo-app.demo.svc.cluster.local/work?load=${LOAD_MS}"
START, PEAK, RAMP, HOLD = ${START}, ${PEAK}, ${RAMP}, ${HOLD}
ok = err = 0
def fire():
    global ok, err
    try:
        urllib.request.urlopen(URL, timeout=5).read(); ok += 1
    except Exception:
        err += 1
pool = ThreadPoolExecutor(max_workers=256)
t0 = last = time.time()
while (el := time.time() - t0) < RAMP + HOLD:
    rps = START + (PEAK - START) * min(el / RAMP, 1.0)
    pool.submit(fire)
    if time.time() - last >= 10:
        print(f"t={el:5.0f}s target={rps:5.0f} rps ok={ok} err={err}", flush=True); last = time.time()
    time.sleep(1.0 / rps)
pool.shutdown(wait=True)
print(f"done ok={ok} err={err}")
PY

kubectl -n demo delete pod loadgen --ignore-not-found --wait >/dev/null
echo ">> ramp ${START} -> ${PEAK} rps over ${RAMP}s, hold ${HOLD}s"
kubectl -n demo run loadgen --rm -i --restart=Never --image=python:3.12-slim \
  --overrides='{"spec":{"containers":[{"name":"loadgen","image":"python:3.12-slim","command":["python","-c"],"args":["'"$(printf '%s' "${SCRIPT}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1])')"'"],"resources":{"requests":{"cpu":"500m","memory":"128Mi"}}}]}}' \
  -- placeholder
