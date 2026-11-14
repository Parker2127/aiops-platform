from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from finops_optimizer import actions, collectors, llm, main, report

TODAY = date(2026, 11, 14)


def fake_ce(forecast_error: bool = False):
    ce = MagicMock()
    days = []
    for i in range(7):
        d = TODAY - timedelta(days=7 - i)
        days.append(
            {
                "TimePeriod": {"Start": d.isoformat()},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                        "Metrics": {"UnblendedCost": {"Amount": "1.50"}},
                    },
                    {
                        "Keys": ["Amazon Elastic Container Service for Kubernetes"],
                        "Metrics": {"UnblendedCost": {"Amount": "2.40"}},
                    },
                    {"Keys": ["Tax"], "Metrics": {"UnblendedCost": {"Amount": "0.001"}}},
                ],
            }
        )
    days[-1]["Groups"][0]["Metrics"]["UnblendedCost"]["Amount"] = "3.50"  # a spike on the last day
    ce.get_cost_and_usage.return_value = {"ResultsByTime": days}
    if forecast_error:
        ce.get_cost_forecast.side_effect = Exception("DataUnavailableException")
    else:
        ce.get_cost_forecast.return_value = {"Total": {"Amount": "123.456", "Unit": "USD"}}
    return ce


# --- Cost Explorer -----------------------------------------------------------------


def test_cost_summary_aggregates_and_ranks():
    s = collectors.cost_summary(fake_ce(), TODAY)
    assert s["total"] == pytest.approx(7 * 3.9 + 2.0, abs=0.01)
    assert s["top_services"][0]["service"] == "Amazon Elastic Container Service for Kubernetes"
    assert all(t["service"] != "Tax" for t in s["top_services"])  # sub-cent noise dropped
    assert s["forecast_30d"] == 123.46
    assert s["trend"]["last_day"] == 5.9
    assert s["trend"]["change_pct"] > 0


def test_cost_forecast_unavailable_is_none():
    assert collectors.cost_summary(fake_ce(forecast_error=True), TODAY)["forecast_30d"] is None


# --- idle resources + cleanup (moto) ---------------------------------------------------


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.session.Session(region_name="us-east-1")


def _seed(ec2):
    idle = ec2.create_volume(AvailabilityZone="us-east-1a", Size=100, VolumeType="gp3")["VolumeId"]
    kept = ec2.create_volume(
        AvailabilityZone="us-east-1a",
        Size=10,
        VolumeType="gp2",
        TagSpecifications=[{"ResourceType": "volume", "Tags": [{"Key": "aiops:keep", "Value": "true"}]}],
    )["VolumeId"]
    ec2.allocate_address(Domain="vpc")
    snap = ec2.create_snapshot(VolumeId=kept)["SnapshotId"]
    return idle, kept, snap


def test_idle_resources_finds_and_prices_waste(aws):
    ec2 = aws.client("ec2")
    idle, kept, snap = _seed(ec2)
    found = collectors.idle_resources(ec2, datetime.now(UTC) + timedelta(days=40), snapshot_age_days=30)

    vols = {v["id"]: v for v in found["unattached_volumes"]}
    assert vols[idle]["est_monthly_cost"] == 8.0  # 100 GB gp3 @ $0.08
    assert vols[kept]["keep"] is True
    assert len(found["unassociated_eips"]) == 1
    assert snap in {s["id"] for s in found["old_snapshots"]}
    # moto pre-seeds AMI snapshots under the mock account, so derive the snapshot share
    snaps = sum(x["est_monthly_cost_max"] for x in found["old_snapshots"])
    assert found["est_monthly_waste"] == pytest.approx(8.0 + 1.0 + 3.65 + snaps, abs=0.01)


def test_cleanup_dry_run_deletes_nothing(aws):
    ec2 = aws.client("ec2")
    idle, kept, _ = _seed(ec2)
    vols = collectors.idle_resources(ec2, datetime.now(UTC) + timedelta(days=10))["unattached_volumes"]
    result = actions.cleanup_volumes(ec2, vols, dry_run=True, min_age_days=7)
    assert result["would_delete_volumes"] == [idle]
    assert kept not in result["would_delete_volumes"]  # aiops:keep=true is never a candidate
    assert len(ec2.describe_volumes()["Volumes"]) == 2


def test_cleanup_real_run_deletes_only_eligible(aws):
    ec2 = aws.client("ec2")
    idle, kept, _ = _seed(ec2)
    vols = collectors.idle_resources(ec2, datetime.now(UTC) + timedelta(days=10))["unattached_volumes"]
    result = actions.cleanup_volumes(ec2, vols, dry_run=False, min_age_days=7)
    assert result["deleted_volumes"] == [idle]
    assert [v["VolumeId"] for v in ec2.describe_volumes()["Volumes"]] == [kept]


def test_cleanup_respects_min_age(aws):
    ec2 = aws.client("ec2")
    _seed(ec2)
    vols = collectors.idle_resources(ec2, datetime.now(UTC))["unattached_volumes"]  # age 0
    assert actions.cleanup_volumes(ec2, vols, dry_run=False, min_age_days=7)["deleted_volumes"] == []


# --- Kubernetes ----------------------------------------------------------------------


def test_k8s_waste_flags_missing_requests():
    def pod(ns, name, phase, requests):
        container = SimpleNamespace(name="c", resources=SimpleNamespace(requests=requests))
        return SimpleNamespace(
            metadata=SimpleNamespace(namespace=ns, name=name),
            status=SimpleNamespace(phase=phase),
            spec=SimpleNamespace(containers=[container]),
        )

    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value.items = [
        pod("demo", "good", "Running", {"cpu": "50m", "memory": "64Mi"}),
        pod("demo", "bad", "Running", {"cpu": "50m"}),
        pod("x", "none", "Running", None),
        pod("x", "done", "Succeeded", None),
    ]
    out = collectors.k8s_waste(core)
    assert out["containers_scanned"] == 3
    assert [(c["pod"], c["missing"]) for c in out["without_requests"]] == [
        ("bad", ["memory"]),
        ("none", ["cpu", "memory"]),
    ]


# --- LLM governance -------------------------------------------------------------------


FACTS = {
    "cost": {"total": 29.3, "forecast_30d": 123.46, "trend": {"change_pct": 41.2}},
    "idle": {"id": "vol-0abc1234", "est": 8.0},
}


def test_unverified_numbers_accepts_facts_and_rejects_inventions():
    ok = "Spend was $29.30 this week, up 41.2%; forecast $123.46. Delete vol-0abc1234 to save $8.00. 3 actions."
    assert llm.unverified_numbers(ok, FACTS) == []
    invented = "Forecast is $150.00 and you could save 37%."
    assert llm.unverified_numbers(invented, FACTS) == ["$150.00", "37%"]


def test_narrate_flags_hallucinated_figures():
    bedrock = MagicMock()
    bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": "Total $29.30, savings $999.99."}]}}
    }
    text, bad = llm.narrate(bedrock, "m", FACTS)
    assert text.startswith("Total")
    assert bad == ["$999.99"]


def test_narrate_failure_is_non_fatal():
    bedrock = MagicMock()
    bedrock.converse.side_effect = RuntimeError("AccessDenied")
    assert llm.narrate(bedrock, "m", FACTS) == (None, [])


# --- end to end ------------------------------------------------------------------------


def test_run_end_to_end_writes_report_to_s3(aws, monkeypatch):
    monkeypatch.setenv("REPORT_BUCKET", "reports-bucket")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("SLACK_SECRET_ID", raising=False)
    s3 = aws.client("s3")
    s3.create_bucket(Bucket="reports-bucket")
    _seed(aws.client("ec2"))

    bedrock = MagicMock()
    bedrock.converse.return_value = {"output": {"message": {"content": [{"text": "Spend is steady."}]}}}
    real_client = aws.client

    def client(name, **kw):
        if name == "ce":
            return fake_ce()
        if name == "bedrock-runtime":
            return bedrock
        return real_client(name, **kw)

    monkeypatch.setattr(aws, "client", client)
    now = datetime.now(UTC) + timedelta(days=10)
    result = main.run(aws, now, core_v1=None)

    key = f"finops-reports/{now.date().isoformat()}.md"
    body = s3.get_object(Bucket="reports-bucket", Key=key)["Body"].read().decode()
    assert "Spend is steady." in body
    assert "Would delete vol-" in body
    assert result["report_uri"] == f"s3://reports-bucket/{key}"
    payload = report.slack_payload(result["facts"], result["narrative"], result["report_uri"])
    assert "FinOps" in payload["text"]
