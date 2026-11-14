"""Remediation. Dry run by default; only unattached volumes are ever deleted."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def cleanup_volumes(
    ec2: Any, volumes: list[dict[str, Any]], dry_run: bool, min_age_days: int
) -> dict[str, Any]:
    eligible = [v for v in volumes if not v["keep"] and v["age_days"] >= min_age_days]
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "min_age_days": min_age_days,
        "deleted_volumes": [],
        "would_delete_volumes": [],
    }
    for v in eligible:
        if dry_run:
            result["would_delete_volumes"].append(v["id"])
            continue
        try:
            # Re-check state right before deleting: it may have been attached since the scan.
            state = ec2.describe_volumes(VolumeIds=[v["id"]])["Volumes"][0]["State"]
            if state != "available":
                log.info("skipping %s: now %s", v["id"], state)
                continue
            ec2.delete_volume(VolumeId=v["id"])
            result["deleted_volumes"].append(v["id"])
            log.warning("deleted unattached volume %s (%s GB)", v["id"], v["size_gb"])
        except Exception:
            log.exception("failed to delete %s", v["id"])
    return result
