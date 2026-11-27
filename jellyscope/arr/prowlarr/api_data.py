"""Prowlarr in the API: its query statistics and every indexer's availability (api_arr, App.api_stats/api_indexers)."""
from __future__ import annotations

from typing import Any


def api_stats(days: int, state: dict[str, Any]) -> dict[str, Any]:
    from ... import stats

    numbers = stats.prowlarr_numbers(days)
    return {"days": days, "searches": int(numbers.get("searches") or 0), "rss": int(numbers.get("rss") or 0),
            "failed_queries": int(numbers.get("failed_count") or 0),
            "failure_share": round(float(numbers.get("failure_share") or 0), 4),
            "response_ms": None if numbers.get("response") is None else int(numbers["response"])}


def api_indexers(days: int, state: dict[str, Any]) -> dict[str, Any]:
    """The availability of every indexer over the period (the measured samples) with its state now (live)."""
    from ... import stats

    now = {row.get("id"): row for row in state.get("indexers") or []}
    rows = []
    for row in stats.prowlarr_availability(days).get("indexers") or []:
        live = now.get(row.get("indexer_id")) or {}
        enabled = live.get("enabled", not row.get("removed"))
        rows.append({"id": row.get("indexer_id"), "name": row.get("name"), "enabled": bool(enabled),
                     "status_now": "disabled" if not enabled else ("failing" if row.get("running") or live.get("blocked_until") else "ok"),
                     "availability_share": None if row.get("availability") is None else round(float(row["availability"]), 4),
                     "outages": int(row.get("outages") or 0), "longest_outage_seconds": row.get("longest"),
                     "queries": int(row.get("query_count") or 0),
                     "failed_query_share": round(float(row.get("failure_share") or 0), 4)})
    return {"days": days, "indexers": rows}
