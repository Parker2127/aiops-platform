"""Background log generators: steady normal chatter, plus a chaos mode that emits
the kinds of lines the anomaly detector is trained to flag."""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable

log = logging.getLogger("demo-app")

_FAKE_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/demo_app/handlers.py", line 88, in checkout\n'
    "    order = repo.get_order(order_id)\n"
    '  File "/app/demo_app/repo.py", line 41, in get_order\n'
    '    raise ConnectionError("could not connect to db-primary:5432")\n'
    "ConnectionError: could not connect to db-primary:5432\n"
)


def _normal_line() -> None:
    kind = random.random()
    if kind < 0.4:
        log.info("cache hit key=user:%d", random.randint(1, 5000), extra={"component": "cache"})
    elif kind < 0.8:
        log.info(
            "db query ok table=orders rows=%d",
            random.randint(0, 50),
            extra={"component": "db", "latency_ms": round(random.lognormvariate(1.5, 0.4), 1)},
        )
    else:
        log.info("background job completed job=sync-inventory", extra={"component": "worker"})


def _anomalous_line() -> None:
    choice = random.randrange(5)
    if choice == 0:
        log.error(
            "unhandled exception in request handler",
            extra={
                "path": "/checkout",
                "method": "POST",
                "status": 500,
                "latency_ms": round(random.uniform(5, 40), 1),
                "exc_text_override": _FAKE_TRACEBACK,
            },
        )
    elif choice == 1:
        log.critical(
            "database connection pool exhausted max=20 waiting=%d",
            random.randint(80, 400),
            extra={"component": "db"},
        )
    elif choice == 2:
        log.error(
            "upstream timeout calling payments-api after 30000ms",
            extra={"path": "/checkout", "method": "POST", "status": 504, "latency_ms": 30000.0},
        )
    elif choice == 3:
        log.warning("memory usage 97%% of limit, OOMKill imminent rss=%dMi", random.randint(480, 510))
    else:
        log.error(
            "AccessDenied: permission denied reading s3://payments-exports/2026/ - refused",
            extra={"component": "exporter"},
        )


class Emitter:
    """Calls `emit` at `rate` lines/sec on a daemon thread while enabled."""

    def __init__(self, emit: Callable[[], None], rate: float, enabled: bool = False) -> None:
        self._emit = emit
        self._interval = 1.0 / rate if rate > 0 else 1.0
        self._enabled = threading.Event()
        if enabled:
            self._enabled.set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def start(self) -> None:
        self._thread.start()

    def set(self, on: bool) -> None:
        if on:
            self._enabled.set()
        else:
            self._enabled.clear()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._enabled.wait(timeout=1.0):
                self._emit()
                # jitter so lines don't arrive in a perfectly regular rhythm
                self._stop.wait(self._interval * random.uniform(0.5, 1.5))


def normal_emitter(rate: float) -> Emitter:
    return Emitter(_normal_line, rate, enabled=rate > 0)


def chaos_emitter(rate: float) -> Emitter:
    return Emitter(_anomalous_line, rate, enabled=False)
