"""Lidarr in the API: its download statistics (api_arr reads them through the registry, App.api_stats)."""
from __future__ import annotations

from typing import Any


def api_stats(days: int, state: dict[str, Any]) -> dict[str, Any]:
    """The period's downloads, speed, upgrades, failures and the indexers' reliability; missing and cutoff from `state`."""
    from ... import stats
    from ...api_arr import download_stats    # shared by the four download apps; late - it is the API's

    return download_stats(days, state, events=stats.lidarr_events, speed=stats.lidarr_speed, upgrades=stats.lidarr_upgrades,
                          failures=stats.lidarr_failures, reliability=stats.lidarr_indexer_reliability)
