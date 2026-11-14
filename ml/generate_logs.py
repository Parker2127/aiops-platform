"""Generate a labelled synthetic log dataset that mirrors the demo-app's JSON logs.

    python ml/generate_logs.py                    # 100k lines -> ml/data/train/logs.jsonl
    python ml/generate_logs.py --lines 5000 --out /tmp/x

~95% normal traffic/chatter, ~5% anomalies. Labels are kept for evaluation only;
the IsolationForest itself trains unsupervised."""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

TRACEBACKS = [
    'Traceback (most recent call last):\n  File "/app/handlers.py", line 88, in checkout\n'
    "ConnectionError: could not connect to db-primary:5432\n",
    'Traceback (most recent call last):\n  File "/app/repo.py", line 12, in load\n'
    "KeyError: 'customer_id'\n",
    'Traceback (most recent call last):\n  File "/app/pay.py", line 51, in charge\n'
    "ValueError: invalid literal for int() with base 10: 'NaN'\n",
]


def _line(ts: datetime, level: str, msg: str, **extra: object) -> str:
    doc = {"ts": ts.isoformat(timespec="milliseconds"), "level": level, "logger": "demo-app", "msg": msg}
    doc.update({k: v for k, v in extra.items() if v is not None})
    return json.dumps(doc, separators=(",", ":"))


def normal(rng: random.Random, ts: datetime) -> str:
    r = rng.random()
    if r < 0.45:
        path = rng.choice(["/", "/work", "/work", "/work"])
        latency = rng.lognormvariate(3.0, 0.6) if path == "/work" else rng.lognormvariate(0.5, 0.4)
        status = 404 if rng.random() < 0.01 else 200
        return _line(
            ts,
            "INFO",
            "request completed",
            path=path,
            method="GET",
            status=status,
            latency_ms=round(latency, 1),
        )
    if r < 0.65:
        return _line(ts, "INFO", f"cache hit key=user:{rng.randint(1, 5000)}", component="cache")
    if r < 0.85:
        return _line(
            ts,
            "INFO",
            f"db query ok table={rng.choice(['orders', 'users', 'items'])} rows={rng.randint(0, 50)}",
            component="db",
            latency_ms=round(rng.lognormvariate(1.5, 0.4), 1),
        )
    if r < 0.97:
        job = rng.choice(["sync-inventory", "refresh-cache", "rollup-metrics"])
        return _line(ts, "INFO", f"background job completed job={job}", component="worker")
    if r < 0.99:
        return _line(ts, "WARNING", f"retrying request attempt={rng.randint(1, 2)}", component="http")
    return _line(ts, "INFO", "chaos mode disabled")


def anomaly(rng: random.Random, ts: datetime) -> str:
    r = rng.randrange(8)
    if r == 0:
        return _line(
            ts,
            "ERROR",
            "unhandled exception in request handler",
            path="/checkout",
            method="POST",
            status=500,
            latency_ms=round(rng.uniform(5, 40), 1),
            exc=rng.choice(TRACEBACKS),
        )
    if r == 1:
        return _line(
            ts,
            "CRITICAL",
            f"database connection pool exhausted max=20 waiting={rng.randint(50, 500)}",
            component="db",
        )
    if r == 2:
        ms = rng.choice([10000, 15000, 30000, 60000])
        return _line(
            ts,
            "ERROR",
            f"upstream timeout calling {rng.choice(['payments-api', 'inventory-api'])} after {ms}ms",
            path="/checkout",
            method="POST",
            status=504,
            latency_ms=float(ms),
        )
    if r == 3:
        return _line(ts, "WARNING", f"memory usage {rng.randint(92, 99)}% of limit, OOMKill imminent")
    if r == 4:
        return _line(
            ts,
            "ERROR",
            "AccessDenied: permission denied reading s3://exports/ - refused",
            component="exporter",
        )
    if r == 5:
        return _line(
            ts,
            "ERROR",
            "request completed",
            path="/work",
            method="GET",
            status=rng.choice([500, 502, 503]),
            latency_ms=round(rng.uniform(1, 30), 1),
        )
    if r == 6:
        return _line(ts, "CRITICAL", f"FATAL: disk full on /var/lib/data ({rng.randint(98, 100)}% used)")
    return f"panic: runtime error: invalid memory address or nil pointer dereference [signal SIGSEGV pc=0x{rng.getrandbits(24):x}]"


def generate(n: int, anomaly_rate: float, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    ts = datetime(2026, 11, 1, tzinfo=UTC)
    rows = []
    for _ in range(n):
        ts += timedelta(milliseconds=rng.randint(1, 400))
        is_anomaly = rng.random() < anomaly_rate
        rows.append({"line": anomaly(rng, ts) if is_anomaly else normal(rng, ts), "label": int(is_anomaly)})
    return rows


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lines", type=int, default=100_000)
    p.add_argument("--anomaly-rate", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "data" / "train")
    args = p.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    rows = generate(args.lines, args.anomaly_rate, args.seed)
    target = args.out / "logs.jsonl"
    with target.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    n_anom = sum(r["label"] for r in rows)
    print(f"wrote {len(rows)} lines ({n_anom} anomalous) to {target}")


if __name__ == "__main__":
    main()
