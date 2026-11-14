"""LLM triage of an anomalous log line via Amazon Bedrock (Converse API)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

CATEGORIES = ("database", "dependency", "application_error", "resource_exhaustion", "security", "unknown")
SEVERITIES = ("low", "medium", "high", "critical")

SYSTEM_PROMPT = (
    "You are an SRE triaging alerts from an ML log-anomaly detector. "
    "You get one log line the model flagged plus context. Classify it. "
    "Base every statement on the log line itself; if the cause is unclear, say so. "
    "Reply with ONLY a JSON object with keys: "
    f"category (one of {', '.join(CATEGORIES)}), severity (one of {', '.join(SEVERITIES)}), "
    "summary (one sentence), likely_cause (one sentence), suggested_action (one imperative sentence)."
)


def build_prompt(pod: str, finding_type: str, score: float, line: str) -> str:
    excerpt = line if len(line) <= 2000 else line[:2000] + "...(truncated)"
    return (
        f"Pod: {pod}\n"
        f"Detector bucket: {finding_type}\n"
        f"Anomaly score: {score:.4f} (lower is more anomalous, threshold 0)\n"
        f"Log line:\n{excerpt}"
    )


def parse_triage(text: str) -> dict[str, str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in model reply")
    raw = json.loads(match.group(0))
    out = {
        k: str(raw.get(k, "")).strip()
        for k in ("category", "severity", "summary", "likely_cause", "suggested_action")
    }
    if out["category"] not in CATEGORIES:
        out["category"] = "unknown"
    if out["severity"] not in SEVERITIES:
        out["severity"] = "medium"
    return out


class Triager:
    def __init__(self, bedrock_client: Any, model_id: str, enabled: bool = True) -> None:
        self._client = bedrock_client
        self._model_id = model_id
        self._enabled = enabled

    def triage(self, pod: str, finding_type: str, score: float, line: str) -> dict[str, str] | None:
        if not self._enabled:
            return None
        try:
            resp = self._client.converse(
                modelId=self._model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[
                    {"role": "user", "content": [{"text": build_prompt(pod, finding_type, score, line)}]}
                ],
                inferenceConfig={"maxTokens": 400},
            )
            text = "".join(block.get("text", "") for block in resp["output"]["message"]["content"])
            return parse_triage(text)
        except Exception:
            # The alert still goes out without triage; the LLM is an enrichment, not a dependency.
            log.exception("bedrock triage failed")
            return None
