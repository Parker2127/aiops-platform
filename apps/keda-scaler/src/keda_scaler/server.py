"""KEDA ExternalScaler gRPC server that scales on *forecast* request rate."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent import futures
from dataclasses import dataclass
from datetime import UTC, datetime

import externalscaler_pb2 as pb
import externalscaler_pb2_grpc as pb_grpc
import grpc
from prometheus_client import Gauge, start_http_server

from keda_scaler.forecast import Forecast, forecast, replicas_for
from keda_scaler.prom import PromClient

log = logging.getLogger("keda-scaler")

METRIC_NAME = "forecast_rps"
CURRENT = Gauge("scaler_current_rps", "Observed request rate", ["scaled_object"])
FORECAST = Gauge("scaler_forecast_rps", "Forecast request rate at the horizon", ["scaled_object"])
REPORTED = Gauge(
    "scaler_reported_rps", "Value returned to KEDA (max of current, forecast)", ["scaled_object"]
)
EXPECTED = Gauge("scaler_expected_replicas", "ceil(reported / target)", ["scaled_object"])


@dataclass(frozen=True)
class Settings:
    query: str
    target_rps: float
    window_seconds: int
    step_seconds: int
    horizon_seconds: int
    alpha: float

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            query=os.environ.get("QUERY", 'sum(rate(http_requests_total{namespace="demo"}[1m]))'),
            target_rps=float(os.environ.get("TARGET_RPS", "50")),
            window_seconds=int(os.environ.get("WINDOW", "300")),
            step_seconds=int(os.environ.get("STEP", "15")),
            horizon_seconds=int(os.environ.get("FORECAST_HORIZON", "120")),
            alpha=float(os.environ.get("EWMA_ALPHA", "0.5")),
        )

    def with_metadata(self, md: dict[str, str]) -> Settings:
        """ScaledObject trigger metadata overrides the env defaults."""
        return Settings(
            query=md.get("query", self.query),
            target_rps=float(md.get("targetRps", self.target_rps)),
            window_seconds=int(md.get("window", self.window_seconds)),
            step_seconds=int(md.get("step", self.step_seconds)),
            horizon_seconds=int(md.get("horizon", self.horizon_seconds)),
            alpha=float(md.get("alpha", self.alpha)),
        )


class ForecastScaler(pb_grpc.ExternalScalerServicer):
    def __init__(
        self,
        settings: Settings,
        series_fn: Callable[[str, int, int], list[float]],
        cache_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._series = series_fn
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, Forecast]] = {}
        self._lock = threading.Lock()

    def _compute(self, ref: pb.ScaledObjectRef) -> tuple[Settings, Forecast]:
        s = self._settings.with_metadata(dict(ref.scalerMetadata))
        key = f"{ref.namespace}/{ref.name}|{s.query}"
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
        if hit and now - hit[0] < self._cache_seconds:
            return s, hit[1]
        try:
            series = self._series(s.query, s.window_seconds, s.step_seconds)
        except Exception:
            # Fail safe: keep the last known value instead of reporting 0 and collapsing replicas.
            log.exception("prometheus query failed")
            if hit:
                return s, hit[1]
            raise
        fc = forecast(series, s.step_seconds, s.horizon_seconds, s.alpha)
        with self._lock:
            self._cache[key] = (now, fc)

        so = f"{ref.namespace}/{ref.name}"
        CURRENT.labels(so).set(fc.current)
        FORECAST.labels(so).set(fc.forecast)
        REPORTED.labels(so).set(fc.value)
        EXPECTED.labels(so).set(replicas_for(fc.value, s.target_rps))
        _log(
            "forecast",
            scaled_object=so,
            current_rps=round(fc.current, 2),
            forecast_rps=round(fc.forecast, 2),
            reported_rps=round(fc.value, 2),
            expected_replicas=replicas_for(fc.value, s.target_rps),
            points=len(series),
        )
        return s, fc

    # --- gRPC methods -------------------------------------------------------

    def IsActive(self, request, context):
        try:
            _, fc = self._compute(request)
        except Exception as e:
            context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
        return pb.IsActiveResponse(result=fc.value > 0)

    def StreamIsActive(self, request, context):
        while context.is_active():
            try:
                _, fc = self._compute(request)
                yield pb.IsActiveResponse(result=fc.value > 0)
            except Exception:
                log.exception("stream compute failed")
            time.sleep(self._settings.step_seconds)

    def GetMetricSpec(self, request, context):
        s = self._settings.with_metadata(dict(request.scalerMetadata))
        return pb.GetMetricSpecResponse(
            metricSpecs=[
                pb.MetricSpec(
                    metricName=METRIC_NAME, targetSize=math.ceil(s.target_rps), targetSizeFloat=s.target_rps
                )
            ]
        )

    def GetMetrics(self, request, context):
        try:
            _, fc = self._compute(request.scaledObjectRef)
        except Exception as e:
            context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
        return pb.GetMetricsResponse(
            metricValues=[
                pb.MetricValue(
                    metricName=METRIC_NAME, metricValue=math.ceil(fc.value), metricValueFloat=fc.value
                )
            ]
        )


def _log(msg: str, **fields: object) -> None:
    log.info(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "msg": msg, **fields}))


def serve(port: int = 4350, metrics_port: int = 8080) -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    settings = Settings.from_env()
    prom = PromClient(
        os.environ.get("PROM_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc:9090")
    )
    start_http_server(metrics_port)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb_grpc.add_ExternalScalerServicer_to_server(ForecastScaler(settings, prom.range_series), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    _log("scaler started", grpc_port=port, metrics_port=metrics_port, **settings.__dict__)
    server.wait_for_termination()


if __name__ == "__main__":
    serve(int(os.environ.get("GRPC_PORT", "4350")), int(os.environ.get("METRICS_PORT", "8080")))
