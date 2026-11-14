"""CronJob entry point: collect -> (maybe) clean up -> narrate -> publish."""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from datetime import UTC, datetime
from typing import Any

import boto3

from finops_optimizer import actions, collectors, llm, report

log = logging.getLogger("finops-optimizer")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
        )


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


def slack_webhook(secrets: Any, secret_id: str | None) -> str | None:
    if not secret_id:
        return None
    try:
        value = secrets.get_secret_value(SecretId=secret_id)["SecretString"].strip()
    except Exception:
        log.exception("could not read slack secret")
        return None
    if value.startswith("{"):
        value = json.loads(value).get("webhook_url", "")
    return value if value.startswith("https://hooks.slack.com/") else None


def post_slack(url: str, payload: dict[str, Any]) -> None:
    req = urllib.request.Request(  # noqa: S310 - URL validated as https://hooks.slack.com/
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()


def k8s_core_v1() -> Any | None:
    try:
        from kubernetes import client, config

        config.load_incluster_config()
        return client.CoreV1Api()
    except Exception as e:
        log.warning("kubernetes scan skipped: %s", e)
        return None


def run(session: boto3.session.Session, now: datetime, core_v1: Any | None) -> dict[str, Any]:
    dry_run = _env_bool("DRY_RUN", True)
    bucket = os.environ.get("REPORT_BUCKET")
    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-5-5")

    ec2 = session.client("ec2")
    account = session.client("sts").get_caller_identity()["Account"]
    facts: dict[str, Any] = {
        "report_date": now.date().isoformat(),
        "account": account,
        "region": session.region_name,
        # Cost Explorer is a global API served from us-east-1.
        "cost": collectors.cost_summary(session.client("ce", region_name="us-east-1"), now.date()),
        "idle": collectors.idle_resources(ec2, now, int(os.environ.get("SNAPSHOT_AGE_DAYS", "30"))),
        "k8s": collectors.k8s_waste(core_v1) if core_v1 is not None else None,
    }
    facts["actions"] = actions.cleanup_volumes(
        ec2, facts["idle"]["unattached_volumes"], dry_run, int(os.environ.get("MIN_VOLUME_AGE_DAYS", "7"))
    )

    narrative, unverified = (None, [])
    if _env_bool("LLM_ENABLED", True):
        narrative, unverified = llm.narrate(session.client("bedrock-runtime"), model_id, facts)

    markdown = report.render_markdown(facts, narrative, unverified)
    report_uri = None
    if bucket:
        key = f"finops-reports/{facts['report_date']}.md"
        session.client("s3").put_object(
            Bucket=bucket, Key=key, Body=markdown.encode(), ContentType="text/markdown"
        )
        report_uri = f"s3://{bucket}/{key}"
        log.info("report written to %s", report_uri)

    webhook = slack_webhook(session.client("secretsmanager"), os.environ.get("SLACK_SECRET_ID"))
    if webhook:
        post_slack(webhook, report.slack_payload(facts, narrative, report_uri))
        log.info("report posted to slack")
    else:
        log.info("slack not configured; report follows\n%s", markdown)

    return {
        "facts": facts,
        "narrative": narrative,
        "unverified": unverified,
        "report_uri": report_uri,
        "markdown": markdown,
    }


def main() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("botocore").setLevel(logging.WARNING)
    session = boto3.session.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
    core_v1 = k8s_core_v1() if _env_bool("K8S_SCAN", True) else None
    result = run(session, collectors.now_utc(), core_v1)
    log.info(
        "done: 7d=%s forecast=%s waste=%s/mo",
        result["facts"]["cost"]["total"],
        result["facts"]["cost"]["forecast_30d"],
        result["facts"]["idle"]["est_monthly_waste"],
    )


if __name__ == "__main__":
    main()
