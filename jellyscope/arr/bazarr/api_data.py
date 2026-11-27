"""Bazarr in the API: its subtitle statistics (api_arr reads them through the registry, App.api_stats).

The providers' state now (throttled or not, and the app's code why) from the live state; the app's own text of
when it tries again stays out - a sentence in the app's language, not a value.
"""
from __future__ import annotations

from typing import Any


def api_stats(days: int, state: dict[str, Any]) -> dict[str, Any]:
    from ... import stats

    analysis = stats.bazarr_analysis(days)
    numbers = stats.bazarr_numbers(days, analysis=analysis)
    return {
        "days": days,
        "obtained": int(numbers.get("obtained_count") or 0),
        "automatic": int(numbers.get("automatic") or 0),
        "manual": int(numbers.get("manual") or 0),
        "upgrades": int(numbers.get("upgrade_count") or 0),
        "synced": int(numbers.get("sync_count") or 0),
        "missing_episodes": state.get("episode_count"),
        "missing_movies": state.get("movie_count"),
        "providers": [{"name": row.get("provider"), "count": int(row.get("count") or 0),
                       "share": round(float(row.get("share") or 0) / 100, 4),
                       "score": None if row.get("score") is None else round(float(row["score"]), 1)}
                      for row in stats.bazarr_providers(days, analysis=analysis)],
        "providers_now": [{"name": row.get("name"), "throttled": not row.get("fine", True), "reason": row.get("state") or None}
                          for row in state.get("providers") or []],
    }
