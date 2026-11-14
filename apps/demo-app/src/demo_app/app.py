"""Demo workload: something to scale, something to break, something to log."""

from __future__ import annotations

import logging
import os
import random
import time

from flask import Flask, Response, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from demo_app import chaos, logjson

MAX_LOAD_MS = 5000
_UNTRACKED = {"/healthz", "/metrics"}

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
CHAOS = Gauge("demo_chaos_enabled", "1 while chaos mode is on")


def create_app(start_background: bool = True) -> Flask:
    log = logjson.configure(os.environ.get("LOG_LEVEL", "INFO"))
    app = Flask(__name__)

    background = chaos.normal_emitter(float(os.environ.get("BACKGROUND_LOG_RATE", "2")))
    chaos_mode = chaos.chaos_emitter(float(os.environ.get("CHAOS_LOG_RATE", "5")))
    if start_background:
        background.start()
        chaos_mode.start()
    app.extensions["chaos"] = chaos_mode

    @app.before_request
    def _start_timer() -> None:
        g.start = time.perf_counter()

    @app.after_request
    def _record(resp: Response) -> Response:
        if request.path in _UNTRACKED:
            return resp
        elapsed = time.perf_counter() - g.start
        REQUESTS.labels(request.method, request.path, str(resp.status_code)).inc()
        LATENCY.labels(request.path).observe(elapsed)
        level = logging.ERROR if resp.status_code >= 500 else logging.INFO
        log.log(
            level,
            "request completed",
            extra={
                "path": request.path,
                "method": request.method,
                "status": resp.status_code,
                "latency_ms": round(elapsed * 1000, 1),
            },
        )
        return resp

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify(status="ok")

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.get("/")
    def index() -> Response:
        return jsonify(service="demo-app", pod=os.environ.get("HOSTNAME", "local"), chaos=chaos_mode.enabled)

    @app.get("/work")
    def work() -> tuple[Response, int]:
        try:
            load_ms = int(request.args.get("load", "10"))
        except ValueError:
            return jsonify(error="load must be an integer (ms)"), 400
        load_ms = max(0, min(load_ms, MAX_LOAD_MS))
        time.sleep(load_ms / 1000)
        if chaos_mode.enabled and random.random() < 0.3:
            return jsonify(error="injected failure"), 500
        return jsonify(slept_ms=load_ms, pod=os.environ.get("HOSTNAME", "local")), 200

    @app.post("/chaos/on")
    def chaos_on() -> Response:
        chaos_mode.set(True)
        CHAOS.set(1)
        log.warning("chaos mode enabled")
        return jsonify(chaos=True)

    @app.post("/chaos/off")
    def chaos_off() -> Response:
        chaos_mode.set(False)
        CHAOS.set(0)
        log.info("chaos mode disabled")
        return jsonify(chaos=False)

    return app
