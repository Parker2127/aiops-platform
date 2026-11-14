"""Minimal Prometheus range-query client."""

from __future__ import annotations

import time

import requests


class PromClient:
    def __init__(self, base_url: str, timeout: float = 5.0, session: requests.Session | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()

    def range_series(self, query: str, window_seconds: int, step_seconds: int) -> list[float]:
        """Values of a single-series query over [now - window, now]. Empty if no data."""
        end = time.time()
        resp = self._session.get(
            f"{self._base}/api/v1/query_range",
            params={"query": query, "start": end - window_seconds, "end": end, "step": step_seconds},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus error: {body.get('error')}")
        result = body["data"]["result"]
        if not result:
            return []
        if len(result) > 1:
            raise RuntimeError(f"query must return one series, got {len(result)}; wrap it in sum()")
        return [float(v) for _, v in result[0]["values"]]
