"""Entry point: load model, wire AWS clients, start the log source + HTTP server."""

from __future__ import annotations

import logging
import queue
import threading
from contextlib import asynccontextmanager
from typing import Any

import boto3
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import make_asgi_app

from log_detector import logs
from log_detector.alerts import Deduper
from log_detector.config import Config
from log_detector.model import load_scorer
from log_detector.pipeline import BatchWorker, Detector, LogItem
from log_detector.slack import SlackNotifier, webhook_from_secret
from log_detector.triage import Triager

log = logging.getLogger("log-detector")
MAX_QUEUE = 50_000


def build_detector(cfg: Config) -> Detector:
    session = boto3.session.Session(region_name=cfg.region)
    scorer = load_scorer(cfg.model_path, cfg.model_s3_uri, session.client("s3"), cfg.threshold)
    webhook = (
        webhook_from_secret(session.client("secretsmanager"), cfg.slack_secret_id)
        if cfg.slack_secret_id
        else None
    )
    return Detector(
        scorer=scorer,
        deduper=Deduper(cfg.alert_cooldown_seconds),
        triager=Triager(session.client("bedrock-runtime"), cfg.bedrock_model_id, cfg.llm_enabled),
        notifier=SlackNotifier(webhook),
    )


def records_to_lines(body: Any) -> list[tuple[str, str]]:
    """Accept Fluent Bit http-output JSON (list of records with `log`) or {"lines": [...]}."""
    if isinstance(body, dict) and isinstance(body.get("lines"), list):
        return [(str(body.get("pod", "http")), str(line)) for line in body["lines"]]
    if isinstance(body, list):
        out = []
        for rec in body:
            if isinstance(rec, dict):
                pod = (
                    rec.get("kubernetes", {}).get("pod_name", "fluent-bit")
                    if isinstance(rec.get("kubernetes"), dict)
                    else "fluent-bit"
                )
                out.append((str(pod), str(rec.get("log", rec.get("message", "")))))
        return out
    raise ValueError("expected a JSON list of records or {'lines': [...]}")


def create_app(cfg: Config, detector: Detector) -> FastAPI:
    q: queue.Queue[LogItem] = queue.Queue(maxsize=MAX_QUEUE)

    def sink(pod: str, line: str) -> None:
        try:
            q.put_nowait(LogItem(pod, line))
        except queue.Full:
            log.warning("queue full, dropping line", extra={"pod": pod})

    worker = BatchWorker(detector, q, cfg.batch_size, cfg.batch_seconds)
    stoppers = [worker.stop]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threading.Thread(target=worker.run, daemon=True, name="batch-worker").start()
        if cfg.mode == "k8s":
            from kubernetes import client, config

            from log_detector.sources import PodLogTailer

            config.load_incluster_config()
            tailer = PodLogTailer(client.CoreV1Api(), cfg.target_namespace, cfg.label_selector, sink)
            stoppers.append(tailer.stop)
            threading.Thread(target=tailer.run, daemon=True, name="pod-discovery").start()
        log.info("detector started", extra={"mode": cfg.mode, "threshold": detector.scorer.threshold})
        yield
        for stop in stoppers:
            stop()

    app = FastAPI(title="log-detector", lifespan=lifespan)
    app.mount("/metrics", make_asgi_app())

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": cfg.mode,
            "model_trained_at": detector.scorer.trained_at,
            "queue": q.qsize(),
        }

    @app.post("/ingest")
    async def ingest(request: Request) -> dict[str, int]:
        try:
            pairs = records_to_lines(await request.json())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        for pod, line in pairs:
            sink(pod, line)
        return {"accepted": len(pairs)}

    return app


def main() -> None:
    logs.configure()
    cfg = Config.from_env()
    app = create_app(cfg, build_detector(cfg))
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_config=None, access_log=False)  # noqa: S104


if __name__ == "__main__":
    main()
