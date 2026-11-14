"""IsolationForest training entry point.

SageMaker script mode (scikit-learn container): reads SM_CHANNEL_TRAIN, writes
model.joblib to SM_MODEL_DIR, hyperparameters arrive as --kebab-case CLI args.
Locally it runs as-is:

    python ml/generate_logs.py && python ml/train.py   # -> ml/model/model.joblib
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_recall_fscore_support

from features import FEATURE_NAMES, extract_many

HERE = Path(__file__).parent


def load(train_dir: Path) -> tuple[list[str], np.ndarray | None]:
    lines: list[str] = []
    labels: list[int] = []
    files = sorted(train_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"no *.jsonl files in {train_dir} (run ml/generate_logs.py)")
    for f in files:
        with f.open() as fh:
            for raw in fh:
                row = json.loads(raw)
                lines.append(row["line"])
                if "label" in row:
                    labels.append(int(row["label"]))
    return lines, (np.asarray(labels) if len(labels) == len(lines) else None)


def train(
    lines: list[str],
    labels: np.ndarray | None,
    n_estimators: int,
    contamination: float,
    random_state: int,
) -> tuple[dict, dict]:
    X = extract_many(lines)
    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    ).fit(X)

    # decision_function < 0 <=> predicted anomaly; the detector compares to this threshold.
    threshold = 0.0
    scores = model.decision_function(X)
    metrics: dict[str, float] = {
        "n_lines": float(len(lines)),
        "flagged_rate": float((scores < threshold).mean()),
    }
    if labels is not None:
        p, r, f1, _ = precision_recall_fscore_support(
            labels, (scores < threshold).astype(int), average="binary", zero_division=0
        )
        metrics |= {"precision": float(p), "recall": float(r), "f1": float(f1)}

    bundle = {
        "model": model,
        "threshold": threshold,
        "feature_names": list(FEATURE_NAMES),
        "trained_at": datetime.now(UTC).isoformat(),
        "params": {"n_estimators": n_estimators, "contamination": contamination},
        "metrics": metrics,
    }
    return bundle, metrics


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--train", type=Path, default=Path(os.environ.get("SM_CHANNEL_TRAIN", HERE / "data" / "train"))
    )
    p.add_argument("--model-dir", type=Path, default=Path(os.environ.get("SM_MODEL_DIR", HERE / "model")))
    p.add_argument("--n-estimators", type=int, default=200)
    p.add_argument("--contamination", type=float, default=0.05)
    p.add_argument("--random-state", type=int, default=42)
    args, _unknown = p.parse_known_args(argv)  # SageMaker may pass extra args

    lines, labels = load(args.train)
    bundle, metrics = train(lines, labels, args.n_estimators, args.contamination, args.random_state)

    args.model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_dir / "model.joblib")
    (args.model_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # "name=value;" lines are scraped by the SageMaker MetricDefinitions regexes.
    for k, v in metrics.items():
        print(f"{k}={v:.4f};")
    print(f"saved model to {args.model_dir / 'model.joblib'}")


if __name__ == "__main__":
    main()
