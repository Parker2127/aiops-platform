import json

import generate_logs
import joblib
import numpy as np
import train

from features import FEATURE_NAMES, extract, extract_many, finding_type, parse


def test_parse_json_and_raw():
    assert parse('{"level":"INFO","msg":"ok"}')["msg"] == "ok"
    raw = parse("panic: boom")
    assert raw["_raw"] is True
    assert raw["msg"] == "panic: boom"


def test_feature_vector_shape_and_values():
    doc = {"level": "ERROR", "msg": "x", "status": 503, "latency_ms": 30000, "exc": "Traceback"}
    vec = extract(doc)
    assert len(vec) == len(FEATURE_NAMES)
    named = dict(zip(FEATURE_NAMES, vec, strict=True))
    assert named["level"] == 3
    assert named["status_class"] == 5
    assert named["is_5xx"] == 1
    assert named["has_exception"] == 1


def test_extract_many_empty():
    assert extract_many([]).shape == (0, len(FEATURE_NAMES))


def test_finding_types():
    assert finding_type({"exc": "Traceback"}) == "exception"
    assert finding_type({"msg": "database connection pool exhausted"}) == "resource_exhaustion"
    assert finding_type({"msg": "upstream timeout"}) == "dependency"
    assert finding_type({"msg": "request completed", "status": 502}) == "http_5xx"
    assert finding_type({"msg": "AccessDenied: permission denied"}) == "security"


def test_generator_rate_and_schema():
    rows = generate_logs.generate(2000, 0.05, seed=1)
    rate = sum(r["label"] for r in rows) / len(rows)
    assert 0.03 < rate < 0.07
    assert json.loads(rows[0]["line"])["logger"] == "demo-app" or rows[0]["label"] == 1


def test_train_end_to_end(tmp_path):
    data = tmp_path / "train"
    generate_logs.main(["--lines", "6000", "--out", str(data)])
    model_dir = tmp_path / "model"
    train.main(["--train", str(data), "--model-dir", str(model_dir), "--n-estimators", "100"])

    bundle = joblib.load(model_dir / "model.joblib")
    assert bundle["feature_names"] == list(FEATURE_NAMES)
    metrics = json.loads((model_dir / "metrics.json").read_text())
    assert metrics["recall"] > 0.8
    assert metrics["precision"] > 0.7

    # demo-app-style lines: normal request vs chaos traceback
    normal = '{"level":"INFO","logger":"demo-app","msg":"request completed","path":"/work","method":"GET","status":200,"latency_ms":21.3}'
    bad = '{"level":"ERROR","logger":"demo-app","msg":"unhandled exception in request handler","path":"/checkout","status":500,"exc":"Traceback (most recent call last):\\nConnectionError: db"}'
    scores = bundle["model"].decision_function(extract_many([normal, bad]))
    assert scores[0] > bundle["threshold"] > scores[1]
    assert np.isfinite(scores).all()
