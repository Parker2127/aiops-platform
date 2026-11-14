import io
import json
import tarfile
from unittest.mock import MagicMock

import boto3
import joblib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from features import FEATURE_NAMES
from log_detector.alerts import Deduper, is_anomalous
from log_detector.config import Config
from log_detector.main import create_app, records_to_lines
from log_detector.model import Scorer, load_scorer
from log_detector.pipeline import Detector, LogItem
from log_detector.slack import SlackNotifier, format_anomaly, webhook_from_secret
from log_detector.sources import split_lines
from log_detector.triage import Triager, parse_triage

NORMAL = '{"level":"INFO","msg":"request completed","path":"/work","status":200,"latency_ms":20}'
BAD = '{"level":"ERROR","msg":"unhandled exception","status":500,"exc":"Traceback..."}'


class FakeModel:
    """decision_function: -0.2 for ERROR lines, +0.1 otherwise."""

    def decision_function(self, X):
        return np.where(X[:, 0] >= 3, -0.2, 0.1)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_detector(triager=None, notifier=None, clock=None):
    return Detector(
        scorer=Scorer(FakeModel(), threshold=0.0),
        deduper=Deduper(300, clock=clock or FakeClock()),
        triager=triager or Triager(None, "m", enabled=False),
        notifier=notifier or MagicMock(),
    )


# --- decision logic ---------------------------------------------------------


def test_is_anomalous_strictly_below_threshold():
    assert is_anomalous(-0.01, 0.0)
    assert not is_anomalous(0.0, 0.0)


def test_deduper_cooldown_and_suppressed_count():
    clock = FakeClock()
    d = Deduper(300, clock=clock)
    assert d.allow("exception") == (True, 0)
    assert d.allow("exception") == (False, 0)
    assert d.allow("exception") == (False, 0)
    assert d.allow("dependency") == (True, 0)  # different type is independent
    clock.t = 301
    assert d.allow("exception") == (True, 2)


def test_detector_alerts_once_per_type_in_window():
    notifier = MagicMock()
    det = make_detector(notifier=notifier)
    alerts = det.process([LogItem("p1", NORMAL), LogItem("p1", BAD), LogItem("p2", BAD), LogItem("p1", "")])
    assert len(alerts) == 1
    assert alerts[0].finding_type == "exception"
    assert alerts[0].pod == "p1"
    notifier.send.assert_called_once()


def test_detector_skips_normal_lines():
    notifier = MagicMock()
    make_detector(notifier=notifier).process([LogItem("p", NORMAL)] * 10)
    notifier.send.assert_not_called()


# --- LLM triage -------------------------------------------------------------


def test_triage_calls_bedrock_and_parses():
    client = MagicMock()
    reply = {
        "category": "database",
        "severity": "high",
        "summary": "s",
        "likely_cause": "c",
        "suggested_action": "a",
    }
    client.converse.return_value = {"output": {"message": {"content": [{"text": json.dumps(reply)}]}}}
    out = Triager(client, "us.anthropic.claude-haiku-5-5").triage("p", "exception", -0.2, BAD)
    assert out == reply
    assert client.converse.call_args.kwargs["modelId"] == "us.anthropic.claude-haiku-5-5"


def test_triage_failure_returns_none_and_alert_still_sent():
    client = MagicMock()
    client.converse.side_effect = RuntimeError("throttled")
    notifier = MagicMock()
    alerts = make_detector(triager=Triager(client, "m"), notifier=notifier).process([LogItem("p", BAD)])
    assert alerts[0].triage is None
    notifier.send.assert_called_once()


def test_parse_triage_handles_fences_and_bad_enums():
    out = parse_triage('```json\n{"category":"weird","severity":"apocalyptic","summary":"x"}\n```')
    assert out["category"] == "unknown"
    assert out["severity"] == "medium"
    with pytest.raises(ValueError):
        parse_triage("no json here")


def test_format_anomaly_includes_triage():
    alert = make_detector().process([LogItem("pod-a", BAD)])[0]
    alert.triage = {
        "category": "database",
        "severity": "critical",
        "summary": "S",
        "likely_cause": "C",
        "suggested_action": "A",
    }
    text = json.dumps(format_anomaly(alert))
    assert "pod-a" in text and "LLM triage" in text and ":fire:" in text


# --- AWS integration (moto) -------------------------------------------------


@mock_aws
def test_webhook_from_secret_plain_and_json():
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    sm.create_secret(Name="plain", SecretString="https://hooks.slack.com/services/T/B/x")
    sm.create_secret(Name="json", SecretString='{"webhook_url": "https://hooks.slack.com/services/T/B/y"}')
    sm.create_secret(Name="unset", SecretString="CHANGE_ME")
    assert webhook_from_secret(sm, "plain").endswith("/x")
    assert webhook_from_secret(sm, "json").endswith("/y")
    assert webhook_from_secret(sm, "unset") is None
    assert webhook_from_secret(sm, "missing") is None


@mock_aws
def test_load_scorer_from_sagemaker_tarball(tmp_path):
    joblib.dump(
        {"model": FakeModel(), "threshold": -0.05, "feature_names": list(FEATURE_NAMES), "trained_at": "t"},
        tmp_path / "model.joblib",
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tmp_path / "model.joblib", arcname="model.joblib")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="model-bucket")
    s3.put_object(Bucket="model-bucket", Key="models/job/output/model.tar.gz", Body=buf.getvalue())

    scorer = load_scorer(
        None, "s3://model-bucket/models/job/output/model.tar.gz", s3, threshold_override=None
    )
    assert scorer.threshold == -0.05
    assert (
        load_scorer(
            None, "s3://model-bucket/models/job/output/model.tar.gz", s3, threshold_override=0.1
        ).threshold
        == 0.1
    )


def test_load_scorer_rejects_feature_mismatch(tmp_path):
    joblib.dump({"model": FakeModel(), "threshold": 0, "feature_names": ["old"]}, tmp_path / "m.joblib")
    with pytest.raises(RuntimeError, match="different feature set"):
        load_scorer(str(tmp_path / "m.joblib"), None, None, None)


# --- input plumbing ---------------------------------------------------------


def test_split_lines_reassembles_chunks():
    assert list(split_lines([b'{"a":', b"1}\nsecond", b" line\n", b"tail"])) == [
        '{"a":1}',
        "second line",
        "tail",
    ]


def test_records_to_lines_formats():
    assert records_to_lines({"pod": "x", "lines": ["a", "b"]}) == [("x", "a"), ("x", "b")]
    fb = [{"log": "hello", "kubernetes": {"pod_name": "demo-1"}}, {"log": "bye"}]
    assert records_to_lines(fb) == [("demo-1", "hello"), ("fluent-bit", "bye")]
    with pytest.raises(ValueError):
        records_to_lines("nope")


def test_http_mode_ingest_end_to_end(monkeypatch):
    monkeypatch.setenv("MODE", "http")
    notifier = MagicMock(spec=SlackNotifier)
    det = make_detector(notifier=notifier)
    det.process = MagicMock(wraps=det.process)
    cfg = Config.from_env()
    with TestClient(create_app(cfg, det)) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.post("/ingest", json={"pod": "p", "lines": [NORMAL, BAD]}).json() == {"accepted": 2}
        assert client.post("/ingest", json="bad").status_code == 400
        for _ in range(50):
            if notifier.send.called:
                break
            import time

            time.sleep(0.05)
    notifier.send.assert_called_once()
