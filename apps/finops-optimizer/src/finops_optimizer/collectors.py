"""Data collectors. Every number in the final report comes from here, never from the LLM."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

# us-east-1 list prices (USD/month) used to estimate waste. Estimates, labelled as such.
EBS_GB_MONTH = {
    "gp3": 0.08,
    "gp2": 0.10,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}
SNAPSHOT_GB_MONTH = 0.05
IDLE_EIP_MONTH = 3.65  # $0.005/hr for any public IPv4 not attached to a running instance


def _money(x: float) -> float:
    return round(x, 2)


# --- Cost Explorer -------------------------------------------------------------


def cost_summary(ce: Any, today: date, days: int = 7, top_n: int = 8) -> dict[str, Any]:
    start = today - timedelta(days=days)
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": today.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    by_service: dict[str, float] = defaultdict(float)
    daily: list[dict[str, Any]] = []
    for day in resp["ResultsByTime"]:
        total = 0.0
        for group in day.get("Groups", []):
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            by_service[group["Keys"][0]] += amount
            total += amount
        daily.append({"date": day["TimePeriod"]["Start"], "cost": _money(total)})

    ranked = sorted(by_service.items(), key=lambda kv: kv[1], reverse=True)
    week_total = sum(by_service.values())
    top = [{"service": s, "cost": _money(c)} for s, c in ranked[:top_n] if c >= 0.01]

    # Day-over-day movers: last full day vs the average of the earlier days.
    trend = None
    if len(daily) >= 2:
        prior = [d["cost"] for d in daily[:-1]]
        avg_prior = sum(prior) / len(prior)
        last = daily[-1]["cost"]
        trend = {
            "last_day": _money(last),
            "prior_daily_avg": _money(avg_prior),
            "change_pct": round((last - avg_prior) / avg_prior * 100, 1) if avg_prior > 0 else None,
        }

    return {
        "period": {"start": start.isoformat(), "end": today.isoformat(), "days": days},
        "total": _money(week_total),
        "daily": daily,
        "top_services": top,
        "trend": trend,
        "forecast_30d": cost_forecast(ce, today),
    }


def cost_forecast(ce: Any, today: date, days: int = 30) -> float | None:
    try:
        resp = ce.get_cost_forecast(
            TimePeriod={"Start": today.isoformat(), "End": (today + timedelta(days=days)).isoformat()},
            Metric="UNBLENDED_COST",
            Granularity="MONTHLY",
        )
        return _money(float(resp["Total"]["Amount"]))
    except Exception as e:  # new accounts / too little history -> DataUnavailableException
        log.warning("cost forecast unavailable: %s", e)
        return None


# --- Idle AWS resources ------------------------------------------------------------


def _paginate(client: Any, op: str, key: str, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in client.get_paginator(op).paginate(**kwargs):
        items.extend(page[key])
    return items


def idle_resources(ec2: Any, now: datetime, snapshot_age_days: int = 30) -> dict[str, Any]:
    volumes = []
    for v in _paginate(
        ec2, "describe_volumes", "Volumes", Filters=[{"Name": "status", "Values": ["available"]}]
    ):
        tags = {t["Key"]: t["Value"] for t in v.get("Tags", [])}
        volumes.append(
            {
                "id": v["VolumeId"],
                "type": v["VolumeType"],
                "size_gb": v["Size"],
                "age_days": (now - v["CreateTime"]).days,
                "est_monthly_cost": _money(v["Size"] * EBS_GB_MONTH.get(v["VolumeType"], 0.10)),
                "keep": tags.get("aiops:keep", "").lower() == "true",
                "name": tags.get("Name"),
            }
        )

    eips = [
        {
            "allocation_id": a.get("AllocationId"),
            "public_ip": a.get("PublicIp"),
            "est_monthly_cost": IDLE_EIP_MONTH,
        }
        for a in ec2.describe_addresses()["Addresses"]
        if not a.get("AssociationId")
    ]

    cutoff = now - timedelta(days=snapshot_age_days)
    snapshots = [
        {
            "id": s["SnapshotId"],
            "size_gb": s["VolumeSize"],
            "age_days": (now - s["StartTime"]).days,
            # Snapshots are incremental; full volume size is an upper bound.
            "est_monthly_cost_max": _money(s["VolumeSize"] * SNAPSHOT_GB_MONTH),
        }
        for s in _paginate(ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"])
        if s["StartTime"] < cutoff
    ]

    return {
        "unattached_volumes": volumes,
        "unassociated_eips": eips,
        "old_snapshots": snapshots,
        "snapshot_age_days": snapshot_age_days,
        "est_monthly_waste": _money(
            sum(v["est_monthly_cost"] for v in volumes)
            + sum(e["est_monthly_cost"] for e in eips)
            + sum(s["est_monthly_cost_max"] for s in snapshots)
        ),
    }


# --- Kubernetes waste ----------------------------------------------------------------


def k8s_waste(core_v1: Any) -> dict[str, Any]:
    """Containers without CPU/memory requests: the scheduler can't bin-pack them,
    so nodes get over-provisioned to stay safe."""
    missing = []
    total = 0
    for pod in core_v1.list_pod_for_all_namespaces().items:
        if pod.status.phase not in ("Running", "Pending"):
            continue
        for c in pod.spec.containers:
            total += 1
            requests = (c.resources.requests or {}) if c.resources else {}
            absent = [r for r in ("cpu", "memory") if r not in requests]
            if absent:
                missing.append(
                    {
                        "namespace": pod.metadata.namespace,
                        "pod": pod.metadata.name,
                        "container": c.name,
                        "missing": absent,
                    }
                )
    return {"containers_scanned": total, "without_requests": missing}


def now_utc() -> datetime:
    return datetime.now(UTC)
