"""One-line JSON log formatter. The log-detector's feature extractor (ml/features.py)
parses exactly these fields, so keep the schema in sync with it."""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime

# Extra fields that may be passed via logger.info(..., extra={...})
_FIELDS = ("path", "method", "status", "latency_ms", "component")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                doc[field] = value
        if record.exc_info:
            doc["exc"] = "".join(traceback.format_exception(*record.exc_info))
        elif getattr(record, "exc_text_override", None):
            doc["exc"] = record.exc_text_override
        return json.dumps(doc, separators=(",", ":"))


def configure(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # gunicorn/werkzeug access logs would duplicate ours in a different format
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    return logging.getLogger("demo-app")
