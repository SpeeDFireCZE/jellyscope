"""Prowlarr's statistics: queries to the indexers, the indexers' performance and grabbed releases.

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/arr/prowlarr/sync.py puts the data
into the database: queries as **hourly sums** (`prowlarr_hour`), grabbed releases one by one
(`prowlarr_grab`), the indexers' names in `prowlarr_indexer`.

All functions take `days` - anything `_as_period` takes (a count of days or a ready Period). The database sums; only
finished sums go into memory (by hour, by indexer, by app). A page asks for them once (`prowlarr_analysis`) and hands
them to every statistic as `analysis=`.

Notions:

* **A search** (`query`) is a query for a specific title, **RSS** the regular query "what is new". **Signing in**
  (`auth`) to an indexer does not belong among the queries: the indexer says separately how many times it failed.
* **The mean response** is the sum of responses divided by their count over the whole period (not a mean of the
  hourly means) and only from successful queries that did not come from Prowlarr's cache.
* **Found something** - the share of searches that returned at least one release out of all searches the indexer
  answered. A failed search says nothing about the indexer's content and has its own column.
* **A grabbed release** Prowlarr either downloads itself (`proxy` - it knows the result and the time), or only
  redirects the app to the indexer (`redirect`, always with usenet - it does not know the result and writes it
  as successful). Failures and the time are therefore counted only from the downloaded ones; where there was none,
  it is None.

The hours are stored in UTC. An hour belongs to the period - and to the day in the app's zone - where its start
belongs: the sum of one hour cannot be split any more. With zones shifted by half an hour the day thus ends half an
hour earlier.

Where there is nothing to compute from (no query, no response), it is None, not zero.
"""
from __future__ import annotations

from typing import Any

from .. import db
from .periods import OLDEST_HISTORY, _as_period
from .shared import _local

QUERY_KINDS = ("query", "rss")
_IN_PERIOD = "hour >= ? AND hour < ?"
_BY_HOUR = ("SELECT hour, kind, SUM(event_count) AS event_count, SUM(failed_count) AS failed_count FROM prowlarr_hour"
            f" WHERE {_IN_PERIOD} AND kind IN ('query', 'rss') GROUP BY hour, kind ORDER BY hour")
_BY_INDEXER = ("SELECT indexer_id, kind, SUM(event_count) AS event_count, SUM(failed_count) AS failed_count, SUM(response_sum) AS response_sum,"
               " SUM(response_count) AS response_count, SUM(with_results) AS with_results FROM prowlarr_hour"
               f" WHERE {_IN_PERIOD} GROUP BY indexer_id, kind")
_BY_APP = ("SELECT app, kind, SUM(event_count) AS event_count FROM prowlarr_hour"
           f" WHERE {_IN_PERIOD} AND kind IN ('query', 'rss') GROUP BY app, kind")
# "Stahovano" = Prowlarr knows the result: it downloaded itself, or the grab failed (only a downloaded one can fail).
_GRABBED_BY_INDEXER = ("SELECT indexer_id, COUNT(*) AS grab_count,"
                       " SUM(CASE WHEN method = 'proxy' OR succeeded = 0 THEN 1 ELSE 0 END) AS being_grabbed,"
                       " SUM(CASE WHEN method = 'redirect' AND succeeded = 1 THEN 1 ELSE 0 END) AS redirected,"
                       " SUM(CASE WHEN succeeded = 0 THEN 1 ELSE 0 END) AS failed_count,"
                       " COALESCE(SUM(response), 0) AS response_sum, COUNT(response) AS response_count"
                       " FROM prowlarr_grab WHERE happened_at >= ? AND happened_at < ? GROUP BY indexer_id")
_GRABBED_BY_APP = "SELECT app, COUNT(*) AS grab_count FROM prowlarr_grab WHERE happened_at >= ? AND happened_at < ? GROUP BY app"
_LATEST = ("SELECT id, happened_at, indexer_id, app, title, method, succeeded, response, client FROM prowlarr_grab"
           " WHERE happened_at >= ? AND happened_at < ? ORDER BY happened_at DESC, id DESC")


def _whole(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sums from the database as whole numbers (PostgreSQL returns SUM as a Decimal, an empty sum is NULL)."""
    return [{key: value if isinstance(value, str) else int(value or 0) for key, value in row.items()} for row in rows]


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _mean(total: int, count: int) -> int | None:
    return round(total / count) if count else None


def prowlarr_first() -> str:
    """The time of the first stored event (UTC) - the start of "the whole history" on the Prowlarr tab. Empty without data.

    For queries it is the start of the hour. A nonsense date (before OLDEST_HISTORY) does not count, the same as with
    plays.
    """
    first_times = [str(moment) for moment in (
        db.query_value("SELECT MIN(hour) FROM prowlarr_hour WHERE hour >= ?", (OLDEST_HISTORY,), default=""),
        db.query_value("SELECT MIN(happened_at) FROM prowlarr_grab WHERE happened_at >= ?", (OLDEST_HISTORY,), default="")) if moment]
    return min(first_times) if first_times else ""


def prowlarr_indexer_names() -> dict[int, dict[str, Any]]:
    """The indexers' names: {number: {"name", "protocol", "removed"}}. Those no longer in Prowlarr too."""
    return {int(r["id"]): {"name": r["name"], "protocol": r["protocol"], "removed": bool(r["removed"])}
            for r in db.query_all("SELECT id, name, protocol, removed FROM prowlarr_indexer")}


def prowlarr_analysis(days: Any = 30) -> dict[str, Any]:
    """The period's sums all statistics compute from: by hour, by indexer, by app, grabbed releases by indexer and by
    app, and the indexers' names. Five queries with sums, no single events."""
    period = _as_period(days)
    bounds = (period.since, period.until)
    return {"hours": _whole(db.query_all(_BY_HOUR, bounds)), "indexers": _whole(db.query_all(_BY_INDEXER, bounds)),
            "app": _whole(db.query_all(_BY_APP, bounds)), "grabbed_by_app": _whole(db.query_all(_GRABBED_BY_INDEXER, bounds)),
            "requested_app": _whole(db.query_all(_GRABBED_BY_APP, bounds)), "names": prowlarr_indexer_names()}


def prowlarr_numbers(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The period's numbers: searches, RSS queries, how many failed (count and share) and the mean response in ms."""
    queries = [x for x in (analysis or prowlarr_analysis(days))["indexers"] if x["kind"] in QUERY_KINDS]
    searches = sum(x["event_count"] for x in queries if x["kind"] == "query")
    rss = sum(x["event_count"] for x in queries if x["kind"] == "rss")
    failed = sum(x["failed_count"] for x in queries)
    return {"searches": searches, "rss": rss, "failed_count": failed, "failure_share": _share(failed, searches + rss),
            "response": _mean(sum(x["response_sum"] for x in queries), sum(x["response_count"] for x in queries))}


def prowlarr_by_day(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Searches, RSS queries and failed queries by day (in the app's zone). Only days when something was."""
    by_day: dict[str, dict[str, Any]] = {}
    for x in (analysis or prowlarr_analysis(days))["hours"]:
        day = _local(x["hour"]).date().isoformat()
        row = by_day.setdefault(day, {"day": day, "searches": 0, "rss": 0, "failed_count": 0})
        row["searches" if x["kind"] == "query" else "rss"] += x["event_count"]
        row["failed_count"] += x["failed_count"]
    return [by_day[day] for day in sorted(by_day)]


def prowlarr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Searches by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap). RSS runs the same all the time, it does not belong here."""
    grid = [[0] * 24 for _ in range(7)]
    for x in (analysis or prowlarr_analysis(days))["hours"]:
        if x["kind"] == "query":
            moment = _local(x["hour"])
            grid[moment.weekday()][moment.hour] += x["event_count"]
    return grid


def prowlarr_who(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Who asks: by app the searches, RSS queries and grabbed releases, from the most active. An unknown app is ""."""
    r = analysis or prowlarr_analysis(days)
    by_app: dict[str, dict[str, Any]] = {}
    for x in r["app"]:
        row = by_app.setdefault(x["app"], {"app": x["app"], "searches": 0, "rss": 0, "grabs": 0})
        row["searches" if x["kind"] == "query" else "rss"] += x["event_count"]
    for x in r["requested_app"]:
        by_app.setdefault(x["app"], {"app": x["app"], "searches": 0, "rss": 0, "grabs": 0})["grabs"] += x["grab_count"]
    return sorted(by_app.values(), key=lambda x: (-(x["searches"] + x["rss"] + x["grabs"]), x["app"]))


def prowlarr_indexers(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The indexers' performance, from the one with the most queries: queries (searches and RSS), how many failed and
    what share that is, the mean response in ms, how many answered searches found something, grabbed releases - and
    separately the failed sign-ins. The failure share counts from the queries standing beside it; sign-ins do not
    belong among them.

    `name` is None when the indexer has no name (it left Prowlarr long ago) - the card puts together the text
    "indexer no. N".
    """
    r = analysis or prowlarr_analysis(days)
    grabbed = {x["indexer_id"]: x["grab_count"] for x in r["grabbed_by_app"]}
    by_indexer: dict[int, list[dict[str, Any]]] = {number: [] for number in grabbed}
    for x in r["indexers"]:
        by_indexer.setdefault(x["indexer_id"], []).append(x)
    result = []
    for number, rows in by_indexer.items():
        queries = [x for x in rows if x["kind"] in QUERY_KINDS]
        searches = [x for x in rows if x["kind"] == "query"]
        failed = sum(x["failed_count"] for x in queries)
        names_entry = r["names"].get(number, {})
        result.append({
            "indexer_id": number, "name": names_entry.get("name"), "removed": bool(names_entry.get("removed")),
            "query_count": sum(x["event_count"] for x in queries), "failed_count": failed, "failure_share": _share(failed, sum(x["event_count"] for x in queries)),
            "login_failed": sum(x["failed_count"] for x in rows if x["kind"] == "auth"),
            "response": _mean(sum(x["response_sum"] for x in queries), sum(x["response_count"] for x in queries)),
            "found_share": _share(sum(x["with_results"] for x in searches), sum(x["event_count"] - x["failed_count"] for x in searches)),
            "grabs": grabbed.get(number, 0)})
    return sorted(result, key=lambda x: (-x["query_count"], -x["grabs"], x["indexer_id"]))


def prowlarr_grabbed(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The period's grabbed releases: how many, how many of them Prowlarr downloaded itself and how many it only
    redirected, how many failed and the mean time in ms until the indexer gave out the file (both only from the
    downloaded ones)."""
    rows = (analysis or prowlarr_analysis(days))["grabbed_by_app"]
    return {"grab_count": sum(x["grab_count"] for x in rows), "being_grabbed": sum(x["being_grabbed"] for x in rows),
            "redirected": sum(x["redirected"] for x in rows), "failed_count": sum(x["failed_count"] for x in rows),
            "response": _mean(sum(x["response_sum"] for x in rows), sum(x["response_count"] for x in rows))}


def prowlarr_grabbed_indexers(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Grabbed releases by indexer, from the most frequent: count, share of all, how many Prowlarr downloaded itself
    and how many failed. `failed_count` is None for an indexer where Prowlarr only redirected - it does not know the
    result."""
    r = analysis or prowlarr_analysis(days)
    total = sum(x["grab_count"] for x in r["grabbed_by_app"])
    return sorted(({"indexer_id": x["indexer_id"], "name": r["names"].get(x["indexer_id"], {}).get("name"), "grab_count": x["grab_count"],
                    "share": _share(x["grab_count"], total), "being_grabbed": x["being_grabbed"],
                    "failed_count": x["failed_count"] if x["being_grabbed"] else None} for x in r["grabbed_by_app"]),
                  key=lambda x: (-x["grab_count"], x["indexer_id"]))


def prowlarr_grabbed_latest(days: Any = 30, limit: int | None = 10, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The period's last grabbed releases from the newest: when (UTC), the release's name, the indexer, who took it,
    how (`method`), whether it succeeded and the time in ms. `limit` None = all."""
    period = _as_period(days)
    names = analysis["names"] if analysis else prowlarr_indexer_names()
    if limit is None:
        rows = db.query_all(_LATEST, (period.since, period.until))
    else:
        rows = db.query_all(_LATEST + " LIMIT ?", (period.since, period.until, max(0, int(limit))))
    return [{"happened_at": x["happened_at"], "title": x["title"], "indexer_id": int(x["indexer_id"]), "name": names.get(int(x["indexer_id"]), {}).get("name"),
             "app": x["app"], "method": x["method"], "succeeded": bool(x["succeeded"]), "response": x["response"], "client": x["client"]}
            for x in rows]
