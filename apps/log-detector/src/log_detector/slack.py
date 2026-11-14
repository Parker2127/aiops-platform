"""Slack incoming-webhook notifier. The webhook URL lives in Secrets Manager (IRSA)."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    "low": ":information_source:",
    "medium": ":warning:",
    "high": ":rotating_light:",
    "critical": ":fire:",
}


def webhook_from_secret(secrets_client: Any, secret_id: str) -> str | None:
    """Accepts a plain URL or {"webhook_url": "..."}. Returns None if unset."""
    try:
        value = secrets_client.get_secret_value(SecretId=secret_id)["SecretString"].strip()
    except Exception:
        log.exception("could not read slack secret %s; alerts will only be logged", secret_id)
        return None
    if value.startswith("{"):
        value = json.loads(value).get("webhook_url", "")
    return value if value.startswith("https://hooks.slack.com/") else None


def post_json(url: str, payload: dict[str, Any], timeout: float = 5.0) -> None:
    if not url.startswith("https://"):
        raise ValueError("refusing non-https webhook")
    req = urllib.request.Request(  # noqa: S310 - scheme checked above
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        resp.read()


class SlackNotifier:
    def __init__(self, webhook_url: str | None) -> None:
        self._url = webhook_url

    def send(self, payload: dict[str, Any]) -> bool:
        if not self._url:
            log.warning("slack not configured; alert payload follows", extra={"slack_payload": payload})
            return False
        try:
            post_json(self._url, payload)
            return True
        except Exception:
            log.exception("slack post failed")
            return False


def format_anomaly(alert: Any) -> dict[str, Any]:
    t = alert.triage
    sev = t["severity"] if t else "medium"
    head = f"{SEVERITY_EMOJI.get(sev, ':warning:')} Log anomaly in `{alert.pod}` ({alert.finding_type})"
    fields = [
        f"*Time:* {alert.timestamp}",
        f"*Score:* {alert.score:.3f} (threshold {alert.threshold:.3f})",
    ]
    if alert.suppressed_since_last:
        fields.append(f"*Similar alerts suppressed:* {alert.suppressed_since_last}")
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{head}*\n" + "\n".join(fields)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{alert.line[:1500]}```"}},
    ]
    if t:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*LLM triage* - {t['category']} / *{t['severity']}*\n"
                        f"{t['summary']}\n*Likely cause:* {t['likely_cause']}\n*Next step:* {t['suggested_action']}"
                    ),
                },
            }
        )
    else:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "_LLM triage unavailable_"}]}
        )
    return {"text": head, "blocks": blocks}
