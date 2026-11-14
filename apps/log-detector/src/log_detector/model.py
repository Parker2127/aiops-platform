"""Load the SageMaker-trained IsolationForest bundle and score log lines."""

from __future__ import annotations

import logging
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import joblib
import numpy as np

from features import FEATURE_NAMES, extract_many

log = logging.getLogger(__name__)


@dataclass
class Scorer:
    model: Any
    threshold: float
    trained_at: str = "unknown"

    def score(self, lines: list[str]) -> np.ndarray:
        """Lower = more anomalous. Below `threshold` means anomaly."""
        if not lines:
            return np.empty(0)
        return self.model.decision_function(extract_many(lines))


def download_model(s3_uri: str, s3_client: Any, dest: Path) -> Path:
    """Fetch a SageMaker model.tar.gz (or a bare model.joblib) and return the joblib path."""
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3":
        raise ValueError(f"not an s3 URI: {s3_uri}")
    local = dest / Path(parsed.path).name
    s3_client.download_file(parsed.netloc, parsed.path.lstrip("/"), str(local))
    if local.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(local) as tar:
            tar.extractall(dest, filter="data")
        return dest / "model.joblib"
    return local


def load_scorer(
    model_path: str | None,
    model_s3_uri: str | None,
    s3_client: Any,
    threshold_override: float | None,
) -> Scorer:
    if model_path:
        path = Path(model_path)
    elif model_s3_uri:
        path = download_model(model_s3_uri, s3_client, Path(tempfile.mkdtemp(prefix="model-")))
    else:
        raise RuntimeError("set MODEL_S3_URI or MODEL_PATH")

    bundle = joblib.load(path)  # our own artifact from our own bucket
    if list(bundle["feature_names"]) != list(FEATURE_NAMES):
        raise RuntimeError(
            "model was trained with a different feature set; retrain with the current ml/features.py"
        )
    threshold = threshold_override if threshold_override is not None else float(bundle["threshold"])
    log.info(
        "model loaded",
        extra={
            "trained_at": bundle.get("trained_at"),
            "threshold": threshold,
            "metrics": bundle.get("metrics"),
        },
    )
    return Scorer(bundle["model"], threshold, bundle.get("trained_at", "unknown"))
