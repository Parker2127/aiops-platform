"""Render the report (deterministic tables + LLM commentary) for S3 and Slack."""

from __future__ import annotations

from typing import Any


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def render_markdown(facts: dict[str, Any], narrative: str | None, unverified: list[str]) -> str:
    cost, idle, k8s, actions = facts["cost"], facts["idle"], facts.get("k8s"), facts["actions"]
    lines = [
        f"# FinOps report - {facts['report_date']}",
        "",
        f"**Account:** {facts['account']}  |  **Mode:** {'dry run' if actions['dry_run'] else 'cleanup enabled'}",
        "",
        "## Summary",
        "",
        narrative or "_LLM commentary unavailable - data below is complete._",
    ]
    if unverified:
        lines += [
            "",
            f"> :warning: Commentary mentions figures not found in the collected data: {', '.join(unverified)}. Trust the tables.",
        ]

    lines += [
        "",
        f"## Spend ({cost['period']['start']} to {cost['period']['end']})",
        "",
        f"- 7-day total: **{_usd(cost['total'])}**",
        f"- 30-day forecast: **{_usd(cost['forecast_30d'])}**",
    ]
    if cost.get("trend"):
        t = cost["trend"]
        change = "n/a" if t["change_pct"] is None else f"{t['change_pct']:+.1f}%"
        lines.append(
            f"- Last day {_usd(t['last_day'])} vs prior daily avg {_usd(t['prior_daily_avg'])} ({change})"
        )
    lines += ["", "| Service | 7-day cost |", "|---|---:|"]
    lines += [f"| {s['service']} | {_usd(s['cost'])} |" for s in cost["top_services"]] or ["| (none) | |"]

    lines += ["", f"## Idle resources (est. {_usd(idle['est_monthly_waste'])}/month)", ""]
    if idle["unattached_volumes"]:
        lines += ["| Volume | Type | GB | Age (d) | Est./month | Keep tag |", "|---|---|---:|---:|---:|---|"]
        lines += [
            f"| {v['id']} | {v['type']} | {v['size_gb']} | {v['age_days']} | {_usd(v['est_monthly_cost'])} | {'yes' if v['keep'] else ''} |"
            for v in idle["unattached_volumes"]
        ]
    else:
        lines.append("- No unattached EBS volumes.")
    lines.append(
        f"- Unassociated Elastic IPs: {len(idle['unassociated_eips'])}"
        + "".join(f"\n  - {e['public_ip']} ({e['allocation_id']})" for e in idle["unassociated_eips"])
    )
    lines.append(f"- Snapshots older than {idle['snapshot_age_days']} days: {len(idle['old_snapshots'])}")

    if k8s:
        lines += [
            "",
            "## Kubernetes",
            "",
            f"- {len(k8s['without_requests'])} of {k8s['containers_scanned']} containers have no CPU/memory requests",
        ]
        lines += [
            f"  - `{c['namespace']}/{c['pod']}` ({c['container']}): missing {', '.join(c['missing'])}"
            for c in k8s["without_requests"][:15]
        ]

    lines += ["", "## Actions", ""]
    if actions["deleted_volumes"]:
        lines += [f"- Deleted {v}" for v in actions["deleted_volumes"]]
    if actions["would_delete_volumes"]:
        lines += [f"- Would delete {v} (set DRY_RUN=false to act)" for v in actions["would_delete_volumes"]]
    if not actions["deleted_volumes"] and not actions["would_delete_volumes"]:
        lines.append("- Nothing eligible for cleanup.")
    lines += [
        "",
        "_Cost figures from Cost Explorer (unblended). Waste figures are list-price estimates._",
        "",
    ]
    return "\n".join(lines)


def slack_payload(facts: dict[str, Any], narrative: str | None, report_uri: str | None) -> dict[str, Any]:
    cost, idle = facts["cost"], facts["idle"]
    head = (
        f":moneybag: FinOps {facts['report_date']} - 7d {_usd(cost['total'])}, "
        f"30d forecast {_usd(cost['forecast_30d'])}, idle waste ~{_usd(idle['est_monthly_waste'])}/mo"
    )
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{head}*"}}]
    if narrative:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": narrative[:2900]}})
    top = "\n".join(f"- {s['service']}: {_usd(s['cost'])}" for s in cost["top_services"][:5])
    if top:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Top services (7d)*\n{top}"}})
    if report_uri:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Full report: `{report_uri}`"}]}
        )
    return {"text": head, "blocks": blocks}
