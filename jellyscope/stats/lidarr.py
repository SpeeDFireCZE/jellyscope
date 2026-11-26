"""Lidarr's statistics over the downloaded history.

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/arr/lidarr/sync.py downloads the history
into the database.

The functions take `days` - anything `_as_period` takes (a count of days or a ready Period) - and compute in the
app's zone.

The unit is the **album**. Lidarr writes one import per track (`trackFileImported`), so what "was added" is an
import of an album, put together from the tracks:

* **The album's import** - the tracks imported with the same `download_id` (and the same album; a day without a track
  ends the import - the same release grabbed again has the same `download_id`). Imports without
  a `download_id` (a manual import) are grouped by the album and the day (UTC): the tracks of one manual import
  come within seconds, a day is the same album's next import already. The album's time is its first track's, its
  size is the sum of its tracks, its quality the first track's.
* **The import's grab** - the grab (`grabbed`, one record per album) with the same `download_id`, the last one before
  the import. The indexer, the client, the protocol and the source (RSS, search, a manual pick...) are taken from
  it. A grab without `download_id` pairs by the album: the last not yet paired grab of the same album before the import.
* **Upgrade** - deleting a track's file with the reason `Upgrade`. Lidarr writes it when taking a better version.
  It is paired by the **album**, not by the track: tracks belong to a release (an edition) of the album, and an upgrade
  to another edition deletes the old files with the old release's track ids and imports the new ones under the new
  release's ids - no track id is shared. Lidarr also deletes the old files *before* the import starts and writes
  the track-imported events only after the whole album was moved, so a big album can come many minutes after the
  deletion. A deletion therefore pairs with the first import of the same album that starts from a minute before it
  to `ALBUM_UPGRADE_WINDOW` after it. An album import with at least one such deletion is **one** upgrade, however many
  tracks were deleted - and an album downloaded twice (MP3, then FLAC) is two imports of the album, the second an upgrade.

Pairing and percentiles are computed in Python over rows picked by one query - SQL would need window functions for
it, which behave differently in SQLite and PostgreSQL. The pairing itself and the quantile are shared
(stats/shared.py); what an album, an import and an upgrade are, this file knows.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from .. import db
from .periods import OLDEST_HISTORY, _as_period
from .shared import (
    BUCKETS,
    GRAB_WINDOW,
    UNKNOWN,
    _downloads,
    _local,
    _pair_grabs,
    _quantile,
    _ranked,
    _shift,
    _time,
)

# How the grab was triggered (data.releaseSource) - the groups for "how".
TRIGGERS = {"Rss": "automatic", "Search": "automatic", "UserInvokedSearch": "user",
            "InteractiveSearch": "manual", "ReleasePush": "external"}
# The window of pairing a deletion (Upgrade) with the album's next import: the import is written only after the whole
# album was moved and its tracks were imported one by one, so it starts long after the deletion for a big album (or
# a slow disk); three hours is generous and still far from a second upgrade of the same album. The import may also
# start a minute before the deletion (the same second ordered by id, clocks).
ALBUM_UPGRADE_WINDOW = timedelta(hours=3)
ALBUM_UPGRADE_SLACK = timedelta(minutes=1)
# Tracks of one download come within minutes; the same download_id a day after the last track is another import.
SAME_IMPORT = timedelta(days=1)

_COLUMNS = ("arr_id, event_type, happened_at, title, artist, item_id, title_id, artist_id, quality, size_bytes, indexer,"
            " release_group, client, protocol, source, reason, download_id, released_at")


def _is_import(row: dict[str, Any]) -> bool:
    return row["event_type"] == "trackFileImported"


def _is_upgrade(row: dict[str, Any]) -> bool:
    return row["event_type"] == "trackFileDeleted" and row["reason"] == "Upgrade"


def _import_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """What makes a track import part of the same album import: the download, or without one the album and the day."""
    if row["download_id"]:
        return ("download", row["download_id"], row["title_id"])
    return ("manual", row["title_id"], row["happened_at"][:10])


def _album_imports(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The track imports (sorted by time) put together into imports of albums (not yet marked as upgrades)."""
    by_key: dict[tuple[Any, ...], list[list[dict[str, Any]]]] = {}
    for track in tracks:
        parts = by_key.setdefault(_import_key(track), [])
        # The same download_id can repeat (the same release grabbed again): a track long after the last one is the next import.
        if parts and _time(track["happened_at"]) - _time(parts[-1][-1]["happened_at"]) <= SAME_IMPORT:
            parts[-1].append(track)
        else:
            parts.append([track])
    albums = []
    for part in (p for parts in by_key.values() for p in parts):
        first = part[0]
        albums.append({
            "arr_id": first["arr_id"], "happened_at": first["happened_at"], "title": first["title"], "artist": first["artist"],
            "title_id": first["title_id"], "artist_id": first["artist_id"],
            # A grab without a download_id pairs by the album, so the album's id is the item here (a grab has no track).
            "item_id": first["title_id"], "download_id": first["download_id"],
            "quality": first["quality"], "size_bytes": sum(int(t["size_bytes"] or 0) for t in part), "track_count": len(part),
            "release_group": next((t["release_group"] for t in part if t["release_group"]), ""),
            "client": next((t["client"] for t in part if t["client"]), ""),
            "released_at": next((t["released_at"] for t in part if t["released_at"]), None),
            "upgrade": False, "old_quality": "",
        })
    return sorted(albums, key=lambda a: (a["happened_at"], a["arr_id"]))


def _mark_upgrades(deletions: list[dict[str, Any]], albums: list[dict[str, Any]]) -> None:
    """Marks the album imports that replaced files: `upgrade` and `old_quality` (see the module's text for the window)."""
    by_album: dict[Any, list[dict[str, Any]]] = {}
    for a in albums:
        by_album.setdefault(a["title_id"], []).append(a)
    replaced: dict[int, list[dict[str, Any]]] = {}
    for d in deletions:
        moment = _time(d["happened_at"])
        for a in by_album.get(d["title_id"], []):      # sorted by time: the first one in the window
            if moment - ALBUM_UPGRADE_SLACK <= _time(a["happened_at"]) <= moment + ALBUM_UPGRADE_WINDOW:
                replaced.setdefault(a["arr_id"], []).append(d)
                break
    for a in albums:
        if a["arr_id"] in replaced:
            a["upgrade"] = True
            a["old_quality"] = min(replaced[a["arr_id"]], key=lambda d: d["arr_id"])["quality"]


def _analysis(days: Any) -> dict[str, Any]:
    """The period's events with the neighbours needed for pairing, already put together into albums and paired.

    A month before the period is picked too (grabs of the imports in the period) and three hours after it (the
    import of an upgrade right before the end). Only what is in the period counts into the numbers then.
    """
    period = _as_period(days)
    rows = db.query_all(
        f"SELECT {_COLUMNS} FROM lidarr_event WHERE happened_at >= ? AND happened_at < ? ORDER BY happened_at, arr_id",
        (_shift(period.since, -GRAB_WINDOW), _shift(period.until, ALBUM_UPGRADE_WINDOW)))
    rows = [dict(r) for r in rows]

    def in_period(r: dict[str, Any]) -> bool:
        return period.since <= r["happened_at"] < period.until

    # A grab belongs to an album (its record has no track): pairing without a download_id goes by the album.
    grabs = [{**r, "item_id": r["title_id"]} for r in rows if r["event_type"] == "grabbed"]
    tracks = [r for r in rows if _is_import(r)]
    deletions = [r for r in rows if _is_upgrade(r)]
    imports = _album_imports(tracks)
    _mark_upgrades(deletions, imports)
    grab_imports = _pair_grabs(grabs, imports)
    # What happened by the period's end (indexer reliability): a later import or failure is still downloading then.
    # A failure belongs to an album like a grab.
    failed_by_end = [{**r, "item_id": r["title_id"]} for r in rows
                     if r["event_type"] == "downloadFailed" and r["happened_at"] < period.until]
    imported_grabs = {grab_imports[i["arr_id"]]["arr_id"] for i in imports
                      if i["arr_id"] in grab_imports and i["happened_at"] < period.until}
    failed_grabs = {g["arr_id"] for g in _pair_grabs(grabs, failed_by_end).values()}
    # A release of several albums is a grab per album with one download_id: one download (stats/shared.py, _downloads).
    downloads = _downloads([g for g in grabs if g["happened_at"] < period.until], imported_grabs, failed_grabs)
    return {
        "imports": [i for i in imports if in_period(i)],
        "grabs_list": [g for g in grabs if in_period(g)],
        "upgrades": [i for i in imports if i["upgrade"] and in_period(i)],
        "grab_imports": grab_imports,
        "downloads": [d for d in downloads if in_period(d["grab"])],
    }


def lidarr_events(days: Any = 30) -> dict[str, Any]:
    """The period's events paired once - for a page that wants more statistics.

    Every statistic (`lidarr_additions`, `lidarr_heatmap`, `lidarr_speed`, `lidarr_sources`, `lidarr_upgrades`) takes the
    result as `analysis=` and does not have to pick and pair the events again.
    """
    return _analysis(days)


# ---------------------------------------------------------------------------
# What was added and when
# ---------------------------------------------------------------------------

def lidarr_additions(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Imports of albums by day (in the app's zone): count, tracks, size, new ones and upgrades. Only days with an import."""
    r = analysis or _analysis(days)
    by_day: dict[str, dict[str, Any]] = {}
    for i in r["imports"]:
        day = _local(i["happened_at"]).date().isoformat()
        row = by_day.setdefault(day, {"day": day, "count": 0, "track_count": 0, "size_bytes": 0, "new_count": 0, "upgrade_count": 0})
        row["count"] += 1
        row["track_count"] += i["track_count"]
        row["size_bytes"] += i["size_bytes"]
        row["upgrade_count" if i["upgrade"] else "new_count"] += 1
    return [by_day[day] for day in sorted(by_day)]


def lidarr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Imports of albums by day of the week and hour: 7 x 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0] * 24 for _ in range(7)]
    for i in (analysis or _analysis(days))["imports"]:
        moment = _local(i["happened_at"])
        grid[moment.weekday()][moment.hour] += 1
    return grid


def lidarr_largest(days: Any = 30, limit: int = 10, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The largest imported albums in the period. `title_id` is the album's id in Lidarr (see lidarr_library)."""
    imports = (analysis or _analysis(days))["imports"]
    ordered = sorted(imports, key=lambda i: (-i["size_bytes"], i["happened_at"], i["arr_id"]))
    keys = ("title", "artist", "title_id", "size_bytes", "track_count", "happened_at", "quality")
    return [{k: i[k] for k in keys} for i in ordered[:max(0, int(limit))]]


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------

def lidarr_speed(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """How long a download takes (grab -> the album's first track) and how soon after its release an album is in the library.

    Times in seconds, the median and p90. An import before the release (a preview, a wrong date in the
    database) does not count into "since the release", only its count shows - a negative time would drag the median
    to nonsense.

    Upgrades do not belong to "since the release" either: it asks how soon an album was added to the library, and
    a better version of the same album a day later would count every album a second time and later. Upgrades do
    belong to the download time - they were downloaded.

    And only what **was waited for** counts: the artist was already in Lidarr when the album came out (the time the
    artist was added <= `released_at`; Lidarr says it per artist, it is stored with each of its albums). The back
    catalogue of an artist added later would otherwise have years "since the release" and drag the numbers to nonsense.
    What does not count this way has only its count: `not_counted` = the artist was added only after the album's
    release, `unknown` = we do not know when it was added (Lidarr no longer has the album, or the list of albums was
    not downloaded yet after the upgrade). Both times are text in UTC of the same shape, they compare as text. An
    album without a release date has nothing to measure from and is left out.
    """
    r = analysis or _analysis(days)
    download_times = [(_time(i["happened_at"]) - _time(r["grab_imports"][i["arr_id"]]["happened_at"])).total_seconds()
                      for i in r["imports"] if i["arr_id"] in r["grab_imports"]]
    download_times = [s for s in download_times if s >= 0]
    with_release = [i for i in r["imports"] if i["released_at"] and not i["upgrade"]]
    added = {t["title_id"]: t["added_at"] for t in db.query_all_in(
        "SELECT title_id, added_at FROM lidarr_title WHERE added_at IS NOT NULL AND title_id IN ({})",
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

def lidarr_sources(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Where the imported albums came from: indexers, groups, clients, protocols, qualities.

    It counts over the album imports (what really was added), the size is the album's size. The indexer, the
    protocol are from the import's grab; the client's name from the import (a grab has only the client's type), the
    group, when there is no grab, from the import too. Music has no resolutions.
    """
    r = analysis or _analysis(days)
    groups = ("indexers", "groups", "clients", "protocols", "qualities")    # = the cards' names
    counts = {s: Counter() for s in groups}
    sizes = {s: Counter() for s in groups}
    for i in r["imports"]:
        g = r["grab_imports"].get(i["arr_id"]) or {}
        values = {
            "indexers": g.get("indexer"),
            "groups": g.get("release_group") or i["release_group"],
            # The import's own name is the client's instance in Lidarr; a grab carries the client's type name.
            "clients": i["client"] or g.get("client"),
            "protocols": g.get("protocol"),
            "qualities": i["quality"],
        }
        for group, value in values.items():
            name = value or UNKNOWN
            counts[group][name] += 1
            sizes[group][name] += i["size_bytes"]
    return {s: _ranked(counts[s], sizes[s]) for s in groups}


def lidarr_indexer_reliability(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """How reliable each indexer's releases are: of the period's downloads, how many were imported and how many failed.

    A download is one release: Lidarr writes a grab per album, so a release of several albums is several grabs with one
    `download_id` - one download (stats/shared.py, `_downloads`). It is paired with its imports and failures
    (`downloadFailed`) by `download_id` the same way as the sources (`_pair_grabs`), and only by the period's end: a
    download is imported when an album of it was imported by then, failed when it failed and nothing was imported,
    otherwise still downloading - neither a success nor a failure. The same release grabbed again after its outcome is
    another download. A grab without `download_id` (the Blackhole client) cannot be matched: it is a download of its own
    that counts into `unmatched` and into no success rate.

    Rows {name, downloads, imported, failed, unmatched, success}; `success` = imported / (imported + failed), None
    when nothing that can be matched finished. The most failures first, then the lowest success rate (None last),
    then the most downloads, then the name. It measures the releases, not whether the indexer's API was reachable.
    """
    r = analysis or _analysis(days)
    rows: dict[str, dict[str, Any]] = {}
    for d in r["downloads"]:
        g = d["grab"]
        name = g["indexer"] or UNKNOWN
        row = rows.setdefault(name, {"name": name, "downloads": 0, "imported": 0, "failed": 0, "unmatched": 0})
        row["downloads"] += 1
        if not g["download_id"]:
            row["unmatched"] += 1
        elif d["imported"]:
            row["imported"] += 1
        elif d["failed"]:
            row["failed"] += 1
    for row in rows.values():
        finished = row["imported"] + row["failed"]
        row["success"] = row["imported"] / finished if finished else None
    return sorted(rows.values(), key=lambda x: (-x["failed"], 2 if x["success"] is None else x["success"], -x["downloads"], x["name"]))


# ---------------------------------------------------------------------------
# Upgrades and activity
# ---------------------------------------------------------------------------

def _trigger(source: Any) -> str:
    return TRIGGERS.get(str(source or ""), "unknown")


def lidarr_upgrades(days: Any = 30, analysis: dict[str, Any] | None = None, titles: int = 10) -> dict[str, Any]:
    """How often albums are replaced by a better version, from which quality to which and how it was triggered.

    An upgrade is an import of an album that replaced files (one however many tracks it replaced).
    `how` = what grabbed the version that brought the upgrade (automatic, user, manual, external, unknown);
    `how_all` = the same for all grabs in the period, so that there is something to compare with. `titles` is how
    many of the most often upgraded albums to return (the card takes more and hides the rest in a window).
    """
    r = analysis or _analysis(days)
    upgrades = r["upgrades"]
    item_count = len({i["title_id"] if i["title_id"] is not None else (i["title"], i["artist"]) for i in upgrades})
    transitions: Counter = Counter()
    by_album: Counter = Counter()
    names: dict[Any, dict[str, Any]] = {}
    trigger_counts: Counter = Counter()
    for i in upgrades:
        transitions[(i["old_quality"] or UNKNOWN, i["quality"] or UNKNOWN)] += 1
        key = i["title_id"] if i["title_id"] is not None else (i["title"], i["artist"])
        by_album[key] += 1
        names.setdefault(key, {"title": i["title"] or UNKNOWN, "artist": i["artist"], "title_id": i["title_id"]})
        g = r["grab_imports"].get(i["arr_id"])
        trigger_counts[_trigger(g["source"]) if g else "unknown"] += 1
    ranked = sorted(by_album, key=lambda k: (-by_album[k], names[k]["title"], names[k]["artist"], str(k)))
    return {
        "count": len(upgrades),
        "item_count": item_count,
        "per_item": round(len(upgrades) / item_count, 2) if item_count else 0,
        "transitions": [{"old": old_quality, "new": new_quality, "count": n} for (old_quality, new_quality), n in
                        sorted(transitions.items(), key=lambda p: (-p[1], p[0]))],
        "titles": [{**names[k], "count": by_album[k]} for k in ranked[:max(0, int(titles))]],
        "how": dict(trigger_counts),
        "how_all": dict(Counter(_trigger(g["source"]) for g in r["grabs_list"])),
    }


def lidarr_first() -> str:
    """The time of Lidarr's first downloaded event (UTC), empty without a history.

    The start of "the whole history" on the Lidarr tab - its history usually reaches far before the first play in
    Jellyscope. A nonsense date (before OLDEST_HISTORY) does not count, the same as with plays.
    """
    return str(db.query_value("SELECT MIN(happened_at) FROM lidarr_event WHERE happened_at >= ?",
                              (OLDEST_HISTORY,), default="") or "")


def lidarr_failures(days: Any = 30) -> int:
    """How many downloads failed in the period."""
    period = _as_period(days)
    return int(db.query_value(
        "SELECT COUNT(*) FROM lidarr_event WHERE event_type = 'downloadFailed' AND happened_at >= ? AND happened_at < ?",
        (period.since, period.until)) or 0)


# ---------------------------------------------------------------------------
# Links into the library
# ---------------------------------------------------------------------------

def lidarr_library(title_ids: Iterable[Any] | None = None) -> dict[int, dict[str, str]]:
    """Lidarr's albums that have a counterpart in the music library: {the album's id in Lidarr: its library ids}.

    The value is `{"album_id", "cover_tag", "artist_id"}`: the album's id in Jellyfin (empty when the album is not
    in the library), the fingerprint of its cover and the artist's id in Jellyfin (empty when the artist is not
    there). The cards turn them into addresses with the helpers of the music cards (cards_music).

    `title_ids` limits the search to the albums the card really draws (a few dozen rows) - without them the whole
    Lidarr library would be looked up on every page load. Without them (`None`) all albums. A row with neither an
    album nor an artist in the library is not in the result: the card leaves it without a link (a link leading
    nowhere is worse than none).

    It pairs by the Jellyfin ids stored with Lidarr's albums (arr/lidarr/titles.py), never by name. A pairing is
    recomputed on every sync, so one can be stale in between: an album or an artist that has gone missing from the
    library meanwhile (`missing`) does not count.
    """
    if title_ids is None:
        rows = db.query_all("SELECT title_id, jellyfin_id, jellyfin_artist_id FROM lidarr_title ORDER BY title_id")
    else:
        rows = db.query_all_in("SELECT title_id, jellyfin_id, jellyfin_artist_id FROM lidarr_title WHERE title_id IN ({}) ORDER BY title_id",
                               sorted({int(i) for i in title_ids if isinstance(i, int)}))
    albums = {r["id"]: r["cover_tag"] for r in db.query_all_in(
        "SELECT id, cover_tag FROM music_album WHERE missing = 0 AND id IN ({}) ORDER BY id",
        sorted({r["jellyfin_id"] for r in rows if r["jellyfin_id"]}))}
    artists = {r["id"] for r in db.query_all_in(
        "SELECT id FROM music_artist WHERE missing = 0 AND id IN ({}) ORDER BY id",
        sorted({r["jellyfin_artist_id"] for r in rows if r["jellyfin_artist_id"]}))}
    result = {}
    for r in rows:
        album_id = r["jellyfin_id"] if r["jellyfin_id"] in albums else ""
        artist_id = r["jellyfin_artist_id"] if r["jellyfin_artist_id"] in artists else ""
        if album_id or artist_id:
            result[r["title_id"]] = {"album_id": album_id, "cover_tag": str(albums.get(album_id) or ""), "artist_id": artist_id}
    return result
