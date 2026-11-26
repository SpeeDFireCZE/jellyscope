"""Music statistics: listening, rankings and technique.

Part of the statistics package (see jellyscope/stats/__init__.py). A listen is a row of the play history with the
kind "Audio" (`only_music`); the track, the album and the artist are added to it by the music tables that
jellyscope/music/sync.py fills.

A page picks the period's listening once (`music_analysis`) and every card computes from the same rows - the same as
Sonarr's and Radarr's statistics.

Two notions:

* **listening time** - the seconds really listened to, all rows are summed;
* **a play** - a listen of at least PLAY_FROM_SECONDS seconds, or at least half the track (for short ones). A
  skipped track adds time, not a play.

A track the sync does not know yet (added and played before the next run) counts right away - under its name from
the history, without an album and an artist.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .. import db, formatting
from .kinds import only_music
from .periods import OLDEST_HISTORY, _as_period
from .shared import local_fast

PLAY_FROM_SECONDS = 30          # seconds of listening from which it is a play (for shorter tracks half is enough)
TICKS = 10_000_000
# The analysis' columns in which the same texts repeat (viewer, app, artist, album, genres...). An analysis row gets
# one shared copy of them: with hundreds of thousands of listens it is hundreds of MB of memory (review 15. 11. 2026).
_SHARED_TEXT_COLUMNS = ("user_id", "user_name", "client", "device_name", "item_id", "item_name", "track_id", "track",
                        "artist_id", "artist", "album_id", "album", "album_artist_id", "album_artist", "genres")


def music_analysis(days: Any, *, user_id: str | None = None, artist_id: str | None = None,
                   album_id: str | None = None, library_id: str | None = None) -> dict[str, Any]:
    """The period's listening with the track, the album and the artist - once for the whole page.

    Returns {"rows": [...], "since", "until"}. A row carries `plays` (True/False) too. The pages of a viewer, an artist
    and an album ask only for their own listening (`user_id`, `artist_id` - the track's artist, as in the ranking -,
    `album_id`, and `library_id` - the Activity tab of a music library): the database then does not return the whole
    server's listening, which would be thrown away.
    """
    period = _as_period(days)
    where, params = "", [period.since, period.until]
    if user_id is not None:
        where += " AND p.user_id = ?"
        params.append(user_id)
    if artist_id is not None:
        where += " AND s.artist_id = ?"
        params.append(artist_id)
    if album_id is not None:
        where += " AND s.album_id = ?"
        params.append(album_id)
    if library_id is not None:
        where += " AND s.library_id = ?"
        params.append(library_id)
    # Row by row (query_iter): for the whole history it tends to be hundreds of thousands of listens and query_all
    # would hold them in memory twice - the raw rows and the dicts.
    rows = db.query_iter(
        f"""
        SELECT p.id, p.session_key, p.user_id, COALESCE(u.name, p.user_name) AS user_name,
               p.item_id, p.item_name, p.started_at, p.watched_seconds, p.play_method,
               p.transcode_reasons, p.client, p.device_name, p.bitrate, p.media_runtime_ticks,
               s.id AS track_id, s.name AS track, s.album_id, COALESCE(a.name, s.album) AS album,
               s.artist_id, s.artist, s.genres, s.runtime_ticks, s.bitrate AS track_bitrate,
               a.cover_tag, a.artist AS album_artist, a.artist_id AS album_artist_id
          FROM playback p
          LEFT JOIN users u ON u.id = p.user_id
          LEFT JOIN music_track s ON s.id = p.item_id
          LEFT JOIN music_album a ON a.id = s.album_id
         WHERE p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           {only_music("p.")}{where}
         ORDER BY p.started_at, p.id
        """,
        tuple(params),
    )
    zone = formatting.zone()
    texts: dict[Any, Any] = {}
    share = texts.setdefault
    result = []
    for r in rows:
        for column in _SHARED_TEXT_COLUMNS:
            value = r[column]
            r[column] = share(value, value)
        seconds = int(r["watched_seconds"] or 0)
        length = int(r["runtime_ticks"] or r["media_runtime_ticks"] or 0) / TICKS
        r["watched_seconds"] = seconds
        r["plays"] = seconds >= PLAY_FROM_SECONDS or (length > 0 and seconds * 2 >= length)
        # The local time (the app's zone) once here - the cards by day and by hour only read it.
        r["local"] = local_fast(r["started_at"], zone)
        result.append(r)
    return {"rows": result, "since": period.since, "until": period.until}


def music_first() -> str | None:
    """When the listening history starts (the first listen), or None."""
    first = db.query_value(f"SELECT MIN(started_at) FROM playback WHERE watched_seconds > 0 AND started_at >= ?{only_music()}",
                           (OLDEST_HISTORY,), default=None)
    return str(first) if first else None


# ---------------------------------------------------------------------------
# Listening
# ---------------------------------------------------------------------------

def music_numbers(analysis: dict[str, Any]) -> dict[str, int]:
    """Listening time (seconds), plays, listeners, different artists and different tracks."""
    rows = analysis["rows"]
    return {"seconds": sum(x["watched_seconds"] for x in rows),
            "plays": sum(1 for x in rows if x["plays"]),
            "listener_count": len({x["user_id"] or x["user_name"] for x in rows}),
            "artist_count": len({x["artist_id"] for x in rows if x["artist_id"]}),
            "track_count": len({x["item_id"] for x in rows if x["item_id"]})}


def music_by_day(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Listening by day (in the app's zone): seconds and plays. Only days with listening."""
    by_day: dict[str, dict[str, Any]] = {}
    for x in analysis["rows"]:
        day = x["local"].date().isoformat()
        row = by_day.setdefault(day, {"day": day, "seconds": 0, "plays": 0})
        row["seconds"] += x["watched_seconds"]
        row["plays"] += int(x["plays"])
    return [by_day[day] for day in sorted(by_day)]


def music_heatmap(analysis: dict[str, Any]) -> list[list[float]]:
    """Hours of listening by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0.0] * 24 for _ in range(7)]
    for x in analysis["rows"]:
        moment = x["local"]
        grid[moment.weekday()][moment.hour] += x["watched_seconds"] / 3600
    return grid


def music_listeners(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Listeners from the most hours: seconds, plays and their most listened artist."""
    people: dict[str, dict[str, Any]] = {}
    artists_by_person: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, str] = {}
    for x in analysis["rows"]:
        person_key = x["user_id"] or "name:" + str(x["user_name"])
        person = people.setdefault(person_key, {"user_id": x["user_id"], "user_name": x["user_name"], "seconds": 0, "plays": 0})
        person["seconds"] += x["watched_seconds"]
        person["plays"] += int(x["plays"])
        if x["artist_id"]:
            artists_by_person[person_key][x["artist_id"]] += x["watched_seconds"]
            names[x["artist_id"]] = x["artist"] or ""
    for person_key, person in people.items():
        top = sorted(artists_by_person[person_key].items(), key=lambda pair: (-pair[1], names.get(pair[0], "")))
        person["artist_id"] = top[0][0] if top else None
        person["artist"] = names.get(top[0][0], "") if top else ""
    return sorted(people.values(), key=lambda c: (-c["seconds"], str(c["user_name"] or "").lower()))


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------

def _ranking(analysis: dict[str, Any], key: str, fields: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    """Groups the listening by the column `key` (None = unknown); a row carries `fields` from the first listen."""
    groups: dict[Any, dict[str, Any]] = {}
    people: dict[Any, set[str]] = defaultdict(set)
    for x in analysis["rows"]:
        value = x[key]
        group = groups.setdefault(value, {"id": value, **{p: (x[p] or "") if value else "" for p in fields},
                                          "seconds": 0, "plays": 0})
        group["seconds"] += x["watched_seconds"]
        group["plays"] += int(x["plays"])
        people[value].add(str(x["user_id"] or x["user_name"]))
    for value, group in groups.items():
        group["listener_count"] = len(people[value])
    # A tie by the name: at that moment it is still under its key (fields[0] - "interpret", "album").
    return sorted(groups.values(), key=lambda s: (-s["seconds"], str(s.get(fields[0]) or "").lower()))[:limit]


def music_artists(analysis: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    """The most listened artists: id (None = unknown), name, seconds, plays, listeners."""
    result = _ranking(analysis, "artist_id", ("artist",), limit)
    for row in result:
        row["name"] = row.pop("artist")
    return result


def music_albums(analysis: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    """The most listened albums with the album's artist and cover."""
    result = _ranking(analysis, "album_id", ("album", "album_artist", "album_artist_id", "cover_tag"), limit)
    for row in result:
        row["name"] = row.pop("album")
        row["artist"] = row.pop("album_artist")
        row["artist_id"] = row.pop("album_artist_id")
    return result


def music_tracks(analysis: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    """The most listened tracks. A track the sync does not know has its name from the history."""
    groups: dict[str, dict[str, Any]] = {}
    people: dict[str, set[str]] = defaultdict(set)
    for x in analysis["rows"]:
        key = str(x["item_id"] or x["item_name"])
        group = groups.setdefault(key, {
            "id": x["item_id"], "name": x["track"] or x["item_name"] or "", "album_id": x["album_id"],
            "album": x["album"] or "", "artist_id": x["artist_id"], "artist": x["artist"] or "",
            "seconds": 0, "plays": 0})
        group["seconds"] += x["watched_seconds"]
        group["plays"] += int(x["plays"])
        people[key].add(str(x["user_id"] or x["user_name"]))
    for key, group in groups.items():
        group["listener_count"] = len(people[key])
    return sorted(groups.values(), key=lambda s: (-s["seconds"], s["name"].lower()))[:limit]


def music_genres(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Genres by listening time. A track with more genres counts for each."""
    seconds_by_genre: Counter = Counter()
    plays_by_genre: Counter = Counter()
    for x in analysis["rows"]:
        for genre in {z.strip() for z in str(x["genres"] or "").split("|") if z.strip()}:
            seconds_by_genre[genre] += x["watched_seconds"]
            plays_by_genre[genre] += int(x["plays"])
    return [{"genre": z, "seconds": s, "plays": plays_by_genre[z]} for z, s in sorted(seconds_by_genre.items(), key=lambda pair: (-pair[1], pair[0]))]


# ---------------------------------------------------------------------------
# Technique
# ---------------------------------------------------------------------------

def _is_transcode(method: Any) -> bool:
    """Like periods.transcode_condition: by the start (imported history carries "Transcode (v:... a:...)")."""
    return str(method or "").startswith("Transcode")


def music_play_method(analysis: dict[str, Any]) -> dict[str, Any]:
    """Seconds direct and transcoded, the transcode reasons (Jellyfin reports several at once, separated by a comma)."""
    direct = transcoded = 0
    reasons: Counter = Counter()
    for x in analysis["rows"]:
        if _is_transcode(x["play_method"]):
            transcoded += x["watched_seconds"]
            for reason in str(x["transcode_reasons"] or "").split(","):
                if reason.strip():
                    reasons[reason.strip()] += 1
        else:
            direct += x["watched_seconds"]
    return {"direct": direct, "transcoded": transcoded,
            "reasons": [{"reason": d, "count": n} for d, n in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))]}


def music_apps(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """The apps listened in: seconds, plays, how many devices."""
    groups: dict[str, dict[str, Any]] = {}
    devices: dict[str, set[str]] = defaultdict(set)
    for x in analysis["rows"]:
        app = str(x["client"] or "")
        group = groups.setdefault(app, {"app": app, "seconds": 0, "plays": 0})
        group["seconds"] += x["watched_seconds"]
        group["plays"] += int(x["plays"])
        devices[app].add(str(x["device_name"] or ""))
    for app, group in groups.items():
        group["device"] = len(devices[app])
    return sorted(groups.values(), key=lambda s: (-s["seconds"], s["app"].lower()))


def music_network(analysis: dict[str, Any]) -> dict[str, int]:
    """How much data flowed over the network while listening: time × the play's bitrate, otherwise the file's bitrate.

    It is an estimate (the collector does not know the exact flow, see stats/network.py). A listen without any
    bitrate does not count, it is only counted (`without_bitrate`).
    """
    total_bytes = without_bitrate = 0
    for x in analysis["rows"]:
        bitrate = int(x["bitrate"] or 0) or int(x["track_bitrate"] or 0)
        if bitrate > 0:
            total_bytes += x["watched_seconds"] * bitrate // 8
        else:
            without_bitrate += 1
    return {"byte_count": total_bytes, "without_bitrate": without_bitrate}
