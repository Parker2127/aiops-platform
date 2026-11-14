"""LLM narration layer. The model only narrates the JSON we collected; a post-check
flags any number in its text that doesn't trace back to that JSON."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a FinOps analyst writing a short daily cost note for an engineering team. "
    "You are given a JSON document of facts collected from AWS Cost Explorer, EC2 and Kubernetes. "
    "Rules: use ONLY numbers that appear in the JSON, copied exactly; never compute, round "
    "differently, extrapolate or invent figures; if data is missing say it is unavailable. "
    "Write plain markdown, no headings, at most 180 words: one sentence on overall spend and trend, "
    "then up to 3 bullet recommendations ranked by estimated monthly saving, naming the resource IDs."
)

_NUM = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?%?")


def _collect_numbers(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        out.add(round(float(obj), 2))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):  # e.g. dates, IDs inside strings
            out.add(round(float(m.group(1).replace(",", "") + "." + (m.group(2) or "0")), 2))


def unverified_numbers(text: str, facts: dict[str, Any]) -> list[str]:
    """Numbers in `text` not present in `facts`. Small integers (counts, list
    positions like '3 recommendations') are allowed through."""
    known: set[float] = set()
    _collect_numbers(facts, known)
    bad = []
    for m in _NUM.finditer(text):
        value = round(float(m.group(1).replace(",", "") + "." + (m.group(2) or "0")), 2)
        if value <= 10 and m.group(2) is None:
            continue
        if value not in known:
            bad.append(m.group(0))
    return bad


def narrate(bedrock: Any, model_id: str, facts: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Returns (narrative or None on failure, unverified numbers)."""
    try:
        resp = bedrock.converse(
            modelId=model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps(facts, indent=1, default=str)}]}],
            inferenceConfig={"maxTokens": 600},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
    except Exception:
        log.exception("bedrock narration failed; report will contain data only")
        return None, []
    bad = unverified_numbers(text, facts)
    if bad:
        log.warning("LLM narrative contains numbers not in the collected data: %s", bad)
    return text, bad
