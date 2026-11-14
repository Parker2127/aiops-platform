"""Log-line feature extraction, shared by training (ml/train.py, SageMaker) and
inference (apps/log-detector). Any change here changes the model's input space:
retrain and re-pin the model after editing.

Input: one log line, ideally the demo-app JSON schema
  {"ts", "level", "logger", "msg", "path"?, "method"?, "status"?, "latency_ms"?, "exc"?}
Non-JSON lines are still scored (parse_failed=1, msg = raw line)."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

import numpy as np

FEATURE_NAMES: tuple[str, ...] = (
    "level",
    "status_class",
    "is_5xx",
    "log_latency_ms",
    "log_msg_len",
    "has_exception",
    "error_keywords",
    "digit_ratio",
    "upper_ratio",
    "unknown_path",
    "parse_failed",
)

LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "WARN": 2, "ERROR": 3, "CRITICAL": 4, "FATAL": 4}

ERROR_KEYWORDS = (
    "timeout",
    "timed out",
    "refused",
    "exhausted",
    "oom",
    "killed",
    "panic",
    "fatal",
    "denied",
    "failed",
    "unavailable",
    "segfault",
    "corrupt",
    "full",
)

KNOWN_PATHS = frozenset({"/", "/work", "/healthz", "/metrics", "/chaos/on", "/chaos/off"})


def parse(line: str) -> dict[str, Any]:
    line = line.strip()
    if line.startswith("{"):
        try:
            doc = json.loads(line)
            if isinstance(doc, dict):
                return doc
        except json.JSONDecodeError:
            pass
    return {"msg": line, "_raw": True}


def _ratio(text: str, pred) -> float:
    return sum(1 for c in text if pred(c)) / len(text) if text else 0.0


def extract(doc: dict[str, Any]) -> list[float]:
    msg = str(doc.get("msg", ""))
    exc = str(doc.get("exc", "") or "")
    text = f"{msg} {exc}".lower()
    status = doc.get("status")
    status = int(status) if isinstance(status, int | float) else 0
    latency = doc.get("latency_ms")
    latency = float(latency) if isinstance(latency, int | float) else 0.0
    path = doc.get("path")
    level = LEVELS.get(str(doc.get("level", "INFO")).upper(), 1)

    return [
        float(level),
        float(status // 100),
        1.0 if status >= 500 else 0.0,
        math.log1p(max(latency, 0.0)),
        math.log1p(len(msg)),
        1.0 if exc or "traceback" in text or "exception" in text else 0.0,
        float(sum(1 for k in ERROR_KEYWORDS if k in text)),
        _ratio(msg, str.isdigit),
        _ratio(msg, str.isupper),
        1.0 if path is not None and path not in KNOWN_PATHS else 0.0,
        1.0 if doc.get("_raw") else 0.0,
    ]


def extract_many(lines: Iterable[str]) -> np.ndarray:
    rows = [extract(parse(line)) for line in lines]
    if not rows:
        return np.empty((0, len(FEATURE_NAMES)))
    return np.asarray(rows, dtype=np.float64)


def finding_type(doc: dict[str, Any]) -> str:
    """Coarse bucket for an anomalous line, used to de-duplicate alerts."""
    text = f"{doc.get('msg', '')} {doc.get('exc', '') or ''}".lower()
    status = doc.get("status")
    if doc.get("exc") or "traceback" in text:
        return "exception"
    if "denied" in text or "forbidden" in text:
        return "security"
    if "oom" in text or "memory" in text or "exhausted" in text or "full" in text:
        return "resource_exhaustion"
    if "timeout" in text or "timed out" in text or "refused" in text:
        return "dependency"
    if isinstance(status, int) and status >= 500:
        return "http_5xx"
    if LEVELS.get(str(doc.get("level", "")).upper(), 1) >= 3:
        return "error_log"
    return "unusual_pattern"
