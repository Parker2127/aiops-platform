from concurrent import futures
from unittest.mock import MagicMock

import externalscaler_pb2 as pb
import externalscaler_pb2_grpc as pb_grpc
import grpc
import pytest

from keda_scaler.forecast import ewma, forecast, linear_fit, replicas_for
from keda_scaler.prom import PromClient
from keda_scaler.server import METRIC_NAME, ForecastScaler, Settings

STEP, HORIZON = 15, 120  # 8 steps ahead

# --- forecasting math --------------------------------------------------------


def test_ewma_smooths_toward_signal():
    assert ewma([10, 10, 10], 0.5) == [10, 10, 10]
    out = ewma([0, 100], 0.5)
    assert out == [0, 50]
    with pytest.raises(ValueError):
        ewma([1], 0)


def test_linear_fit_exact_line():
    slope, intercept = linear_fit([1, 3, 5, 7])
    assert slope == pytest.approx(2)
    assert intercept == pytest.approx(1)


def test_flat_series_forecasts_flat():
    fc = forecast([100.0] * 20, STEP, HORIZON)
    assert fc.forecast == pytest.approx(100.0)
    assert fc.value == pytest.approx(100.0)


def test_ramp_forecast_leads_current_load():
    ramp = [10.0 + 10 * i for i in range(20)]  # +10 rps per 15s step, now at 200
    fc = forecast(ramp, STEP, HORIZON)
    assert fc.current == 200
    assert fc.forecast > fc.current + 50  # sees ~8 steps ahead
    # the money claim: more replicas than purely reactive scaling would ask for
    assert replicas_for(fc.value, 50) > replicas_for(fc.current, 50)


def test_spike_never_reports_below_current():
    series = [50.0] * 19 + [400.0]
    fc = forecast(series, STEP, HORIZON)
    assert fc.value >= 400.0


def test_falling_load_holds_current_not_forecast():
    fall = [300.0 - 10 * i for i in range(20)]
    fc = forecast(fall, STEP, HORIZON)
    assert fc.forecast < fc.current
    assert fc.value == fc.current


def test_edge_cases():
    assert forecast([], STEP, HORIZON).value == 0
    assert forecast([5.0], STEP, HORIZON).value == 5.0
    assert forecast([float("nan"), 7.0, 7.0, 7.0], STEP, HORIZON).value == pytest.approx(7.0)
    assert forecast([5, 3, 1, 0, 0, 0], STEP, HORIZON).forecast == 0  # clamped at 0


def test_replicas_for():
    assert replicas_for(0, 50) == 1
    assert replicas_for(101, 50) == 3


# --- Prometheus client --------------------------------------------------------


def test_prom_client_parses_single_series():
    session = MagicMock()
    session.get.return_value.json.return_value = {
        "status": "success",
        "data": {"result": [{"values": [[1, "1.5"], [2, "2.5"]]}]},
    }
    assert PromClient("http://p:9090/", session=session).range_series("q", 60, 15) == [1.5, 2.5]
    assert session.get.call_args.args[0] == "http://p:9090/api/v1/query_range"


def test_prom_client_rejects_multiple_series():
    session = MagicMock()
    session.get.return_value.json.return_value = {"status": "success", "data": {"result": [{}, {}]}}
    with pytest.raises(RuntimeError, match="one series"):
        PromClient("http://p", session=session).range_series("q", 60, 15)


# --- gRPC server (real channel, in-process) ----------------------------------


@pytest.fixture
def stub_and_calls():
    calls = []
    clock = {"t": 0.0}

    def series_fn(query, window, step):
        calls.append(query)
        return [10.0 + 10 * i for i in range(20)]

    settings = Settings(
        "sum(rate(x[1m]))", target_rps=50, window_seconds=300, step_seconds=15, horizon_seconds=120, alpha=0.5
    )
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_ExternalScalerServicer_to_server(
        ForecastScaler(settings, series_fn, cache_seconds=5, clock=lambda: clock["t"]), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield pb_grpc.ExternalScalerStub(channel), calls, clock
    channel.close()
    server.stop(None)


def test_grpc_metric_spec_uses_metadata_target(stub_and_calls):
    stub, _, _ = stub_and_calls
    ref = pb.ScaledObjectRef(name="demo-app", namespace="demo", scalerMetadata={"targetRps": "25"})
    spec = stub.GetMetricSpec(ref).metricSpecs[0]
    assert spec.metricName == METRIC_NAME
    assert spec.targetSizeFloat == 25


def test_grpc_get_metrics_reports_forecast_and_caches(stub_and_calls):
    stub, calls, clock = stub_and_calls
    ref = pb.ScaledObjectRef(name="demo-app", namespace="demo")
    req = pb.GetMetricsRequest(scaledObjectRef=ref, metricName=METRIC_NAME)
    value = stub.GetMetrics(req).metricValues[0]
    assert value.metricValueFloat > 200  # ahead of the current 200 rps
    assert stub.IsActive(ref).result is True
    assert len(calls) == 1  # second call served from cache
    clock["t"] = 10
    stub.GetMetrics(req)
    assert len(calls) == 2


def test_prometheus_outage_keeps_last_value():
    results = [[100.0] * 10]

    def flaky(*_):
        if results:
            return results.pop()
        raise ConnectionError("prometheus down")

    clock = {"t": 0.0}
    settings = Settings("q", 50, 300, 15, 120, 0.5)
    scaler = ForecastScaler(settings, flaky, cache_seconds=5, clock=lambda: clock["t"])
    ref = pb.ScaledObjectRef(name="a", namespace="b")
    _, first = scaler._compute(ref)
    clock["t"] = 60
    _, second = scaler._compute(ref)
    assert second == first
