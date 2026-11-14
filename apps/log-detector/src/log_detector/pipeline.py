"""Score batches of log lines and turn anomalies into (rate-limited, triaged) alerts."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from prometheus_client import Counter, Histogram

from features import finding_type, parse
from log_detector.alerts import Alert, Deduper, is_anomalous
from log_detector.model import Scorer
from log_detector.slack import SlackNotifier, format_anomaly
from log_detector.triage import Triager

log = logging.getLogger(__name__)

LINES = Counter("detector_lines_scored_total", "Log lines scored")
ANOMALIES = Counter("detector_anomalies_total", "Lines scored as anomalous", ["finding_type"])
ALERTS = Counter("detector_alerts_sent_total", "Alerts that passed rate limiting", ["finding_type"])
SCORES = Histogram(
    "detector_anomaly_score",
    "IsolationForest decision_function",
    buckets=(-0.3, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.2),
)


@dataclass(frozen=True)
class LogItem:
    pod: str
    line: str


class Detector:
    def __init__(self, scorer: Scorer, deduper: Deduper, triager: Triager, notifier: SlackNotifier) -> None:
        self.scorer = scorer
        self.deduper = deduper
        self.triager = triager
        self.notifier = notifier

    def process(self, items: list[LogItem]) -> list[Alert]:
        items = [i for i in items if i.line.strip()]
        if not items:
            return []
        scores = self.scorer.score([i.line for i in items])
        LINES.inc(len(items))
        alerts: list[Alert] = []
        for item, score in zip(items, scores, strict=True):
            SCORES.observe(float(score))
            if not is_anomalous(float(score), self.scorer.threshold):
                continue
            doc = parse(item.line)
            kind = finding_type(doc)
            ANOMALIES.labels(kind).inc()
            send, suppressed = self.deduper.allow(kind)
            if not send:
                continue
            alert = Alert(item.pod, kind, float(score), self.scorer.threshold, item.line, doc, suppressed)
            # Triage only after dedup, so a log storm costs one LLM call per type per cooldown.
            alert.triage = self.triager.triage(item.pod, kind, alert.score, item.line)
            self.notifier.send(format_anomaly(alert))
            ALERTS.labels(kind).inc()
            log.info(
                "anomaly alert",
                extra={"pod": item.pod, "finding_type": kind, "score": alert.score, "triage": alert.triage},
            )
            alerts.append(alert)
        return alerts


class BatchWorker:
    """Drains a queue into Detector.process in batches of N lines or T seconds."""

    def __init__(
        self, detector: Detector, q: queue.Queue[LogItem], batch_size: int, batch_seconds: float
    ) -> None:
        self._detector = detector
        self._q = q
        self._batch_size = batch_size
        self._batch_seconds = batch_seconds
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            batch: list[LogItem] = []
            try:
                batch.append(self._q.get(timeout=self._batch_seconds))
                while len(batch) < self._batch_size:
                    batch.append(self._q.get_nowait())
            except queue.Empty:
                pass
            if batch:
                try:
                    self._detector.process(batch)
                except Exception:
                    log.exception("batch processing failed")
