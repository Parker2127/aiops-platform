import json
import logging

import pytest

from demo_app.app import create_app
from demo_app.logjson import JsonFormatter


@pytest.fixture
def client():
    app = create_app(start_background=False)
    return app.test_client()


def test_healthz(client):
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_work_sleeps_and_caps_load(client):
    body = client.get("/work?load=1").get_json()
    assert body["slept_ms"] == 1


def test_work_rejects_bad_load(client):
    assert client.get("/work?load=abc").status_code == 400


def test_chaos_toggle(client):
    assert client.post("/chaos/on").get_json() == {"chaos": True}
    assert client.get("/").get_json()["chaos"] is True
    assert client.post("/chaos/off").get_json() == {"chaos": False}


def test_metrics_counts_requests(client):
    client.get("/work?load=0")
    text = client.get("/metrics").get_data(as_text=True)
    assert 'http_requests_total{method="GET",path="/work",status="200"}' in text


def test_json_formatter_single_line_with_fields():
    record = logging.LogRecord("demo-app", logging.ERROR, __file__, 1, "boom", None, None)
    record.status = 500
    record.exc_text_override = "Traceback...\nValueError: x\n"
    line = JsonFormatter().format(record)
    assert "\n" not in line
    doc = json.loads(line)
    assert doc["level"] == "ERROR"
    assert doc["status"] == 500
    assert doc["exc"].startswith("Traceback")
