"""Structured JSON logging; any `extra={...}` keys are included in the line."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_STD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        doc.update({k: v for k, v in vars(record).items() if k not in _STD})
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str, separators=(",", ":"))


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("botocore", "urllib3", "kubernetes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
