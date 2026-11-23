"""Sonarr's statistics over the downloaded history.

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/arr/sonarr/sync.py downloads the history
into the database.

The functions take `days` - anything `_as_period` takes (a count of days or a ready Period) - and compute in the
app's zone.

Three notions almost everything stands on:

* **Import** - a file taken into the library (`*FolderImported`). That is what "was added".
* **The import's grab** - the grab (`grabbed`) with the same `download_id`, the last one before the import. The
  indexer, the client, the protocol and the source (RSS, search, a manual pick...) are taken from it. The Blackhole
  client does not report `download_id` with a grab - then the last not yet paired grab of the same item without
  `download_id` before the import is taken.
* **Upgrade** - deleting a file with the reason `Upgrade`. Sonarr writes it when taking a better version, so it pairs
  with an import of the same item within ten minutes (forward and back): that import is an upgrade, not a new
  episode.

Pairing and percentiles are computed in Python over rows picked by one query - SQL would need window functions for
it, which behave differently in SQLite and PostgreSQL. The pairing itself and the quantile are shared
(stats/shared.py); what an import, an upgrade and the grab's trigger are, this file knows.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from .. import db
from ..jellyfin import id_without_dashes, id_forms
from .periods import OLDEST_HISTORY, _as_period
from .shared import (
    BUCKETS,
    GRAB_WINDOW,
    UNKNOWN,
    UPGRADE_PAIRING_WINDOW,
    _local,
    _pair_grabs,
    _pair_upgrades,
    _quantile,
    _ranked,
    _shift,
    _time,
)

# How the grab was triggered (data.releaseSource) - the groups for "how".
TRIGGERS = {"Rss": "automatic", "Search": "automatic", "UserInvokedSearch": "user",
            "InteractiveSearch": "manual", "ReleasePush": "external"}

_COLUMNS = ("arr_id, event_type, happened_at, title, title_id, episode, item_id, quality, resolution, size_bytes, indexer,"
            " release_group, client, protocol, source, reason, download_id, released_at")


def _is_import(row: dict[str, Any]) -> bool:
    return str(row["event_type"]).endswith("FolderImported")


def _is_upgrade(row: dict[str, Any]) -> bool:
    return str(row["event_type"]).endswith("FileDeleted") and row["reason"] == "Upgrade"


def _analysis(days: Any) -> dict[str, Any]:
    """The period's events with the neighbours needed for pairing, already paired.

    A month before the period is picked too (grabs of the imports in the period) and ten minutes after it (the
    import of an upgrade right before the end). Only what is in the period counts into the numbers then.
    """
    period = _as_period(days)
    rows = db.query_all(
        f"SELECT {_COLUMNS} FROM sonarr_event WHERE happened_at >= ? AND happened_at < ? ORDER BY happened_at, arr_id",
        (_shift(period.since, -GRAB_WINDOW), _shift(period.until, UPGRADE_PAIRING_WINDOW)))
    rows = [dict(r) for r in rows]

    def in_period(r: dict[str, Any]) -> bool:
        return period.since <= r["happened_at"] < period.until

    grabs = [r for r in rows if r["event_type"] == "grabbed"]
    imports = [r for r in rows if _is_import(r)]
    upgrades = [r for r in rows if _is_upgrade(r)]
    import_by_upgrade = _pair_upgrades(upgrades, imports)
    return {
        "imports": [i for i in imports if in_period(i)],
        "grabs_list": [g for g in grabs if in_period(g)],
        "upgrades": [d for d in upgrades if in_period(d)],
        "grab_imports": _pair_grabs(grabs, imports),
        "upgrade_import": import_by_upgrade,
        "upgrade_imports": {i["arr_id"] for i in import_by_upgrade.values()},
    }


def sonarr_events(days: Any = 30) -> dict[str, Any]:
    """The period's events paired once - for a page that wants more statistics.

    Every statistic (`sonarr_additions`, `sonarr_heatmap`, `sonarr_speed`, `sonarr_sources`, `sonarr_upgrades`) takes the
    result as `analysis=` and does not have to pick and pair the events again. The page used to do it five times.
    """
    return _analysis(days)


# ---------------------------------------------------------------------------
# What was added and when
# ---------------------------------------------------------------------------

def sonarr_additions(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Imports by day (in the app's zone): count, size, new ones and upgrades. Only days with an import."""
    r = analysis or _analysis(days)
    by_day: dict[str, dict[str, Any]] = {}
    for i in r["imports"]:
        day = _local(i["happened_at"]).date().isoformat()
        row = by_day.setdefault(day, {"day": day, "count": 0, "size_bytes": 0, "new_count": 0, "upgrade_count": 0})
        row["count"] += 1
        row["size_bytes"] += int(i["size_bytes"] or 0)
        row["upgrade_count" if i["arr_id"] in r["upgrade_imports"] else "new_count"] += 1
    return [by_day[day] for day in sorted(by_day)]


def sonarr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Imports by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0] * 24 for _ in range(7)]
    for i in (analysis or _analysis(days))["imports"]:
        moment = _local(i["happened_at"])
        grid[moment.weekday()][moment.hour] += 1
    return grid


def sonarr_largest(days: Any = 30, limit: int = 10) -> list[dict[str, Any]]:
    """The largest imported files in the period. `title_id` is the series' id in Sonarr (see sonarr_detail_links)."""
    period = _as_period(days)
    # `size_bytes IS NOT NULL`: PostgreSQL sorts NULL on top with DESC, SQLite at the bottom.
    return [dict(r) for r in db.query_all(
        "SELECT title, title_id, episode, size_bytes, happened_at, quality FROM sonarr_event"
        " WHERE event_type LIKE ? AND happened_at >= ? AND happened_at < ? AND size_bytes IS NOT NULL"
        " ORDER BY size_bytes DESC, happened_at DESC LIMIT ?",
        ("%FolderImported", period.since, period.until, int(limit)))]


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------

def sonarr_speed(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """How long a download takes (grab -> import) and how soon after airing an episode is in the library.

    Times in seconds, the median and p90. An import before the release (a preview, a wrong date in the titles'
    database) does not count into "since the release", only its count shows - a negative time would drag the median
    to nonsense.

    Upgrades do not belong to "since the release" either: it asks how soon an episode was added to the library, and
    a better version of the same episode a day later would count every episode a second time and later. Upgrades
    do belong to the download time - they were downloaded.

    And only what **was waited for** counts: the series was already in Sonarr when the episode came out (the time
    the series was added <= `released_at`). Older seasons of a series added later would otherwise have years "since the
    release" and drag the numbers to nonsense. What does not count this way has only its count: `not_counted` = the
    series was added only after the episode's release, `unknown` = we do not know when it was added (Sonarr no
    longer has it, or the list of series was not downloaded yet after the upgrade) - about those neither can be
    said. Both times are text in UTC of the same shape, they compare as text.
    """
    r = analysis or _analysis(days)
    download_times = [(_time(i["happened_at"]) - _time(r["grab_imports"][i["arr_id"]]["happened_at"])).total_seconds()
                      for i in r["imports"] if i["arr_id"] in r["grab_imports"]]
    download_times = [s for s in download_times if s >= 0]
    with_release = [i for i in r["imports"] if i["released_at"] and i["arr_id"] not in r["upgrade_imports"]]
    added = {t["title_id"]: t["added_at"] for t in db.query_all_in(
        "SELECT title_id, added_at FROM sonarr_title WHERE added_at IS NOT NULL AND title_id IN ({})",
        sorted({i["title_id"] for i in with_release if i["title_id"] is not None}))}
    since_release, before_release, not_counted, unknown_count = [], 0, 0, 0
    for i in with_release:
        seconds = (_time(i["happened_at"]) - _time(i["released_at"])).total_seconds()
        added_at = added.get(i["title_id"])
        if seconds < 0:
            before_release += 1
        elif not added_at:
            unknown_count += 1
        elif added_at > i["released_at"]:
            not_counted += 1
        else:
            since_release.append(seconds)
    buckets = {name: 0 for name, _ in BUCKETS} | {"longer": 0}
    for seconds in since_release:
        buckets[next((name for name, bound in BUCKETS if seconds <= bound), "longer")] += 1
    return {
        "downloads": {"median": _quantile(download_times, 0.5), "p90": _quantile(download_times, 0.9), "count": len(download_times)},
        "since_release": {"median": _quantile(since_release, 0.5), "p90": _quantile(since_release, 0.9), "count": len(since_release),
                      "before_release": before_release, "not_counted": not_counted, "unknown": unknown_count, "buckets": buckets},
    }


# ---------------------------------------------------------------------------
# Sources and quality
# ---------------------------------------------------------------------------

def sonarr_sources(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Where the imported files came from: indexers, groups, clients, protocols, qualities, resolutions.

    It counts over the imports (what really was added), the size is the imported file's size. The indexer, the
    client and the protocol are from the import's grab; the group and the client, when there is no grab, from the
    import itself.
    """
    r = analysis or _analysis(days)
    groups = ("indexers", "groups", "clients", "protocols", "qualities", "resolutions")    # = the cards' names
    counts = {s: Counter() for s in groups}
    sizes = {s: Counter() for s in groups}
    for i in r["imports"]:
        g = r["grab_imports"].get(i["arr_id"]) or {}
        values = {
            "indexers": g.get("indexer"),
            "groups": g.get("release_group") or i["release_group"],
            "clients": g.get("client") or i["client"],
            "protocols": g.get("protocol"),
            "qualities": i["quality"],
            "resolutions": f"{i['resolution']}p" if i["resolution"] else "",
        }
        for group, value in values.items():
            name = value or UNKNOWN
            counts[group][name] += 1
            sizes[group][name] += int(i["size_bytes"] or 0)
    return {s: _ranked(counts[s], sizes[s]) for s in groups}


# ---------------------------------------------------------------------------
# Problems and activity
# ---------------------------------------------------------------------------

def _trigger(source: Any) -> str:
    return TRIGGERS.get(str(source or ""), "unknown")


def sonarr_upgrades(days: Any = 30, analysis: dict[str, Any] | None = None, titles: int = 10) -> dict[str, Any]:
    """How often files are replaced by a better version, from which to which and how it was triggered.

    `how` = what grabbed the version that brought the upgrade (automaticky, uzivatel, rucne, zvenku, nezname);
    `how_all` = the same for all grabs in the period, so that there is something to compare with. `titles` is how
    many of the most often upgraded titles to return (the card takes more and hides the rest in a window).
    """
    r = analysis or _analysis(days)
    upgrades = r["upgrades"]
    item_count = len({d["item_id"] for d in upgrades})
    transitions: Counter = Counter()
    by_title: Counter = Counter()
    title_id_by_name: dict[str, Any] = {}        # name -> the series' id in Sonarr (for the link, see sonarr_detail_links)
    trigger_counts: Counter = Counter()
    for d in upgrades:
        new_import = r["upgrade_import"].get(d["arr_id"])
        transitions[(d["quality"] or UNKNOWN, (new_import or {}).get("quality") or UNKNOWN)] += 1
        by_title[d["title"] or UNKNOWN] += 1
        title_id_by_name.setdefault(d["title"] or UNKNOWN, d["title_id"])
        g = r["grab_imports"].get(new_import["arr_id"]) if new_import else None
        trigger_counts[_trigger(g["source"]) if g else "unknown"] += 1
    return {
        "count": len(upgrades),
        "item_count": item_count,
        "per_item": round(len(upgrades) / item_count, 2) if item_count else 0,
        "transitions": [{"z": old_quality, "to": new_quality, "count": n} for (old_quality, new_quality), n in
                     sorted(transitions.items(), key=lambda p: (-p[1], p[0]))],
        "titles": [{"title": t, "count": n, "title_id": title_id_by_name.get(t)}
                   for t, n in sorted(by_title.items(), key=lambda p: (-p[1], p[0]))[:max(0, int(titles))]],
        "how": dict(trigger_counts),
        "how_all": dict(Counter(_trigger(g["source"]) for g in r["grabs_list"])),
    }


def sonarr_first() -> str:
    """The time of Sonarr's first downloaded event (UTC), empty without a history.

    The start of "the whole history" on the Sonarr tab - its history usually reaches far before the first play in
    Jellyscope. A nonsense date (before OLDEST_HISTORY) does not count, the same as with plays.
    """
    return str(db.query_value("SELECT MIN(happened_at) FROM sonarr_event WHERE happened_at >= ?",
                              (OLDEST_HISTORY,), default="") or "")


def sonarr_failures(days: Any = 30) -> int:
    """How many downloads failed in the period."""
    period = _as_period(days)
    return int(db.query_value(
        "SELECT COUNT(*) FROM sonarr_event WHERE event_type = 'downloadFailed' AND happened_at >= ? AND happened_at < ?",
        (period.since, period.until)) or 0)


# ---------------------------------------------------------------------------
# Links into the library
# ---------------------------------------------------------------------------

def sonarr_library(title_ids: Iterable[Any] | None = None) -> dict[int, dict[str, str]]:
    """Sonarr's series that are in the library: {the series' id in Sonarr: the detail and the poster}.

    The value is `{"detail_url", "poster_id", "poster_tag"}`: the address of the detail in the library and the
    series' poster for the route /image/ (the same as "Recently added", so the same file in the image cache).

    `title_ids` limits the search to the series the card really draws (a few dozen rows) - without them the whole
    Sonarr library would be looked up in the library on every page load. Without them (`None`) all series.

    Only series that really are in the library - a series Sonarr is still downloading is not in the result and the
    card leaves it without a link (a link leading nowhere is worse than none). It pairs by the series' Jellyfin id
    stored with Sonarr's series (arr/sonarr/titles.py), never by name: via the episodes' `items.series_id` ->
    `/series/<id>`. The address carries the id as the library stores it (with dashes, or without): the series page
    looks it up by that.

    An episode that disappeared from Jellyfin (`is_missing`) does not count.
    """
    if title_ids is None:
        rows = db.query_all("SELECT title_id, jellyfin_id FROM sonarr_title")
    else:
        rows = db.query_all_in("SELECT title_id, jellyfin_id FROM sonarr_title WHERE title_id IN ({})",
                               sorted({int(i) for i in title_ids if isinstance(i, int)}))
    series_by_id = {id_without_dashes(r["series_id"]): {"detail_url": f"/series/{r['series_id']}", "poster_id": str(r["series_id"]),
                                                        "poster_tag": str(r["fingerprint"] or "")}
                    for r in db.query_all_in(
                   "SELECT series_id, MAX(series_image_tag) AS fingerprint FROM items"
                   " WHERE type = 'Episode' AND is_missing = 0 AND series_id IN ({}) GROUP BY series_id ORDER BY series_id",
                   sorted({form for r in rows for form in id_forms(r["jellyfin_id"])}))}
    return {r["title_id"]: series_by_id[r["jellyfin_id"]] for r in rows if r["jellyfin_id"] in series_by_id}


def sonarr_detail_links(title_ids: Iterable[Any] | None = None) -> dict[int, str]:
    """Where a series' link from the cards leads: {the series' id in Sonarr: the address of the detail in the library} (see sonarr_library)."""
    return {title_id: title["detail_url"] for title_id, title in sonarr_library(title_ids).items()}


def sonarr_imports(items: Iterable[Any]) -> dict[int, list[str]]:
    """When Sonarr imported an episode's file: {the episode's id in Sonarr: the import times (UTC) from the oldest}.

    For statistics that lean on the episode from outside (Bazarr measures how long after the file's import the
    episode has subtitles). An episode without an import is not in the result.
    """
    result: dict[int, list[str]] = {}
    for row in db.query_all_in("SELECT item_id, happened_at FROM sonarr_event WHERE event_type LIKE ?"
                               " AND item_id IN ({}) ORDER BY happened_at", items, ("%FolderImported",)):
        result.setdefault(row["item_id"], []).append(row["happened_at"])
    return result
