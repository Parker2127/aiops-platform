from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Config:
    mode: str  # "k8s" (tail pod logs) | "http" (POST /ingest, e.g. from Fluent Bit)
    model_s3_uri: str | None
    model_path: str | None
    threshold: float | None
    target_namespace: str
    label_selector: str
    slack_secret_id: str | None
    bedrock_model_id: str
    llm_enabled: bool
    region: str
    alert_cooldown_seconds: int
    batch_size: int
    batch_seconds: float
    port: int

    @classmethod
    def from_env(cls) -> Config:
        threshold = os.environ.get("THRESHOLD")
        return cls(
            mode=os.environ.get("MODE", "k8s"),
            model_s3_uri=os.environ.get("MODEL_S3_URI") or None,
            model_path=os.environ.get("MODEL_PATH") or None,
            threshold=float(threshold) if threshold else None,
            target_namespace=os.environ.get("TARGET_NAMESPACE", "demo"),
            label_selector=os.environ.get("LABEL_SELECTOR", "app=demo-app"),
            slack_secret_id=os.environ.get("SLACK_SECRET_ID") or None,
            bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-5-5"),
            llm_enabled=_bool("LLM_ENABLED", True),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            alert_cooldown_seconds=int(os.environ.get("ALERT_COOLDOWN_SECONDS", "300")),
            batch_size=int(os.environ.get("BATCH_SIZE", "200")),
            batch_seconds=float(os.environ.get("BATCH_SECONDS", "1.0")),
            port=int(os.environ.get("PORT", "8080")),
        )
