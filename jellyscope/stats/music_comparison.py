"""Two periods of listening side by side: the comparison of Music (/music/compare, web/music/compare.py).

Music is laid out like Movies & series (the user, 22. 11. 2026), so this is the shape of `stats.comparison`
(overview_page.py) and one set of template macros (templates/_compare.html) draws both pages the same way: groups
of rows with the difference (`_row`), the genres with their shares like the languages, and the charts of both
periods side by side - the most played albums and the most active listeners (the page shows the listeners only
with "...including who listened"; their rows carry `user_id`, so anonymising knows them).
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..i18n import translate as _t
from .music_listening import (
    music_albums,
    music_analysis,
    music_genres,
    music_listeners,
    music_network,
    music_numbers,
    music_play_method,
)
from .overview_page import _overlap, _row, length_seconds
from .periods import Period, _as_period

TOP = 5     # the charts of both periods, like the video comparison


def _hours(seconds: Any) -> float:
    return float(seconds or 0) / 3600.0


def _added(period: Period) -> dict[str, int]:
    """Albums added to the music library in the period and the size of their tracks."""
    row = db.query_one(
        "SELECT COUNT(DISTINCT a.id) AS albums, COALESCE(SUM(t.size_bytes), 0) AS size"
        " FROM music_album a LEFT JOIN music_track t ON t.album_id = a.id AND t.missing = 0"
        " WHERE a.missing = 0 AND a.added_at >= ? AND a.added_at < ?", (period.since, period.until))
    found = dict(row or {})
    return {"albums": int(found.get("albums") or 0), "bytes": int(found.get("size") or 0)}


def _genre_rows(first: dict[str, Any], second: dict[str, Any]) -> list[dict[str, Any]]:
    """Hours of each genre in both periods with its share of the period's listening - the union of both, like the
    languages: a genre heard only in one period must show."""
    a = {row["genre"]: row["seconds"] for row in music_genres(first)}
    b = {row["genre"]: row["seconds"] for row in music_genres(second)}
    total_a = sum(x["watched_seconds"] for x in first["rows"]) or 0
    total_b = sum(x["watched_seconds"] for x in second["rows"]) or 0
    rows = []
    for genre in sorted(set(a) | set(b), key=lambda g: (-max(a.get(g, 0), b.get(g, 0)), g)):
        row = _row(genre, "hours", _hours(a.get(genre)), _hours(b.get(genre)))
        row["share_a"] = a.get(genre, 0) / total_a * 100 if total_a else 0.0
        row["share_b"] = b.get(genre, 0) / total_b * 100 if total_b else 0.0
        rows.append(row)
    return rows


def _side(period: Period, analysis: dict[str, Any]) -> dict[str, Any]:
    """One column's charts: the most played albums (to their page) and the most active listeners."""
    top = [{"label": row["name"], "detail_url": f"/album/{row['id']}" if row.get("id") else "",
            "hours": _hours(row["seconds"])} for row in music_albums(analysis, TOP)]
    users = [{"label": row["user_name"], "user_id": row["user_id"], "hours": _hours(row["seconds"])}
             for row in music_listeners(analysis)[:TOP]]
    return {"period": period, "days": length_seconds(period) / 86400.0, "top": top, "users": users}


def music_comparison(first_spec: Any, second_spec: Any) -> dict[str, Any]:
    """The same statistics of listening for two arbitrary periods, side by side (see the module)."""
    first, second = _as_period(first_spec), _as_period(second_spec)
    seconds_a, seconds_b = length_seconds(first), length_seconds(second)
    analysis_a, analysis_b = music_analysis(first), music_analysis(second)
    numbers_a, numbers_b = music_numbers(analysis_a), music_numbers(analysis_b)
    method_a, method_b = music_play_method(analysis_a), music_play_method(analysis_b)
    added_a, added_b = _added(first), _added(second)

    def albums(analysis: dict[str, Any]) -> int:
        return len({x["album_id"] for x in analysis["rows"] if x["album_id"]})

    def share(method: dict[str, Any]) -> float:
        total = method["direct"] + method["transcoded"]
        return method["transcoded"] / total * 100 if total else 0.0

    groups = [
        {"heading": _t("Listening"), "rows": [
            _row(_t("Listened"), "hours", _hours(numbers_a["seconds"]), _hours(numbers_b["seconds"])),
            _row(_t("Daily average"), "hours", _hours(numbers_a["seconds"]) / max(1.0, seconds_a / 86400.0),
                 _hours(numbers_b["seconds"]) / max(1.0, seconds_b / 86400.0)),
            _row(_t("Plays#starts"), "number", numbers_a["plays"], numbers_b["plays"]),
            _row(_t("Listeners"), "number", numbers_a["listener_count"], numbers_b["listener_count"]),
            _row(_t("Artists"), "number", numbers_a["artist_count"], numbers_b["artist_count"]),
            _row(_t("Albums"), "number", albums(analysis_a), albums(analysis_b)),
            _row(_t("Tracks"), "number", numbers_a["track_count"], numbers_b["track_count"]),
        ]},
        {"heading": _t("How music is played"), "rows": [
            _row(_t("Direct play"), "hours", _hours(method_a["direct"]), _hours(method_b["direct"])),
            _row(_t("Transcode"), "hours", _hours(method_a["transcoded"]), _hours(method_b["transcoded"])),
            _row(_t("Transcode share"), "percent", share(method_a), share(method_b)),
            _row(_t("Sent over the network"), "bytes", music_network(analysis_a)["byte_count"],
                 music_network(analysis_b)["byte_count"]),
        ]},
        {"heading": _t("Library"), "rows": [
            _row(_t("Albums added"), "number", added_a["albums"], added_b["albums"]),
            _row(_t("Data added"), "bytes", added_a["bytes"], added_b["bytes"]),
        ]},
    ]
    genres = _genre_rows(analysis_a, analysis_b)
    if genres:              # like the languages of the video comparison: a library without genres has no empty table
        groups.append({"heading": _t("Which genre was listened to"), "rows": genres, "with_share": True})
    return {
        "a": _side(first, analysis_a), "b": _side(second, analysis_b),
        "groups": groups,
        "same_length": abs(seconds_a - seconds_b) <= max(seconds_a, seconds_b) * 0.01,
        "overlap": _overlap(first, second),
        "has_data": bool(numbers_a["plays"] or numbers_b["plays"] or numbers_a["seconds"] or numbers_b["seconds"]),
    }
