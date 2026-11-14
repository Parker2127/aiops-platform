"""Alert decision logic: anomaly threshold + per-finding-type rate limiting."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def is_anomalous(score: float, threshold: float) -> bool:
    return score < threshold


@dataclass
class Deduper:
    """At most one alert per finding type per `cooldown_seconds` (in-memory)."""

    cooldown_seconds: float
    clock: Callable[[], float] = time.monotonic
    _last: dict[str, float] = field(default_factory=dict)
    _suppressed: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str) -> tuple[bool, int]:
        """Returns (send?, how many were suppressed since the last send)."""
        now = self.clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.cooldown_seconds:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return False, 0
            self._last[key] = now
            return True, self._suppressed.pop(key, 0)


@dataclass
class Alert:
    pod: str
    finding_type: str
    score: float
    threshold: float
    line: str
    parsed: dict[str, Any]
    suppressed_since_last: int = 0
    triage: dict[str, str] | None = None

    @property
    def timestamp(self) -> str:
        return str(self.parsed.get("ts", "unknown"))
