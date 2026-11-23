"""Two periods of reading and listening side by side: the comparison of Books (/books/compare, web/books/compare.py).

A mirror of Music's comparison (music_comparison.py), which is laid out like Movies & series: groups of rows with the
difference (`_row`), the genres with their shares, and the charts of both periods side by side - the most read books
(to their page) and the most active readers (the page shows them only with "...including who read and listened"; their
rows carry `user_id`, so anonymising knows them). One set of template macros (templates/_compare.html) draws it.

The books' own accents: listening to audiobooks and reading e-books are two rows (one is the length of audio, the other
the time a reader stayed open), a multi-file audiobook is one work, and only audiobooks are played, so the play method
speaks about them alone.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..i18n import translate as _t
from .books_listening import (
    books_analysis,
    books_genres,
    books_numbers,
    books_play_method,
    books_readers,
    books_works,
    work_key,
)
from .overview_page import _overlap, _row, length_seconds
from .periods import Period, _as_period

TOP = 5     # the charts of both periods, like the video comparison


def _hours(seconds: Any) -> float:
    return float(seconds or 0) / 3600.0


def _added(period: Period) -> dict[str, int]:
    """Works added to the books library in the period (the files of one audiobook are one work) and their size."""
    rows = db.query_all(
        "SELECT id, kind, title_key, size_bytes FROM book WHERE missing = 0 AND added_at >= ? AND added_at < ?",
        (period.since, period.until))
    return {"works": len({work_key(r["kind"], r["title_key"], r["id"]) for r in rows}),
            "bytes": sum(int(r["size_bytes"] or 0) for r in rows)}


def _genre_rows(first: dict[str, Any], second: dict[str, Any]) -> list[dict[str, Any]]:
    """Hours of each genre in both periods with its share of the period's time - the union of both: a genre read only
    in one period must show."""
    a = {row["genre"]: row["seconds"] for row in books_genres(first)}
    b = {row["genre"]: row["seconds"] for row in books_genres(second)}
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
    """One column's charts: the most read books (to their page) and the most active readers."""
    top = [{"label": row["title"], "detail_url": f"/book/{row['id']}" if row.get("id") else "",
            "hours": _hours(row["seconds"])} for row in books_works(analysis, TOP)]
    users = [{"label": row["user_name"], "user_id": row["user_id"], "hours": _hours(row["seconds"])}
             for row in books_readers(analysis)[:TOP]]
    return {"period": period, "days": length_seconds(period) / 86400.0, "top": top, "users": users}


def books_comparison(first_spec: Any, second_spec: Any) -> dict[str, Any]:
    """The same statistics of reading and listening for two arbitrary periods, side by side (see the module)."""
    first, second = _as_period(first_spec), _as_period(second_spec)
    seconds_a, seconds_b = length_seconds(first), length_seconds(second)
    analysis_a, analysis_b = books_analysis(first), books_analysis(second)
    numbers_a, numbers_b = books_numbers(analysis_a), books_numbers(analysis_b)
    method_a, method_b = books_play_method(analysis_a), books_play_method(analysis_b)
    added_a, added_b = _added(first), _added(second)

    def share(method: dict[str, Any]) -> float:
        total = method["direct"] + method["transcoded"]
        return method["transcoded"] / total * 100 if total else 0.0

    def total(numbers: dict[str, int]) -> float:
        return _hours(numbers["listening"] + numbers["reading"])

    groups = [
        {"heading": _t("Reading and listening"), "rows": [
            _row(_t("Audiobooks listened"), "hours", _hours(numbers_a["listening"]), _hours(numbers_b["listening"])),
            _row(_t("E-books read"), "hours", _hours(numbers_a["reading"]), _hours(numbers_b["reading"])),
            _row(_t("Daily average"), "hours", total(numbers_a) / max(1.0, seconds_a / 86400.0),
                 total(numbers_b) / max(1.0, seconds_b / 86400.0)),
            _row(_t("Readers"), "number", numbers_a["reader_count"], numbers_b["reader_count"]),
            _row(_t("Works"), "number", numbers_a["work_count"], numbers_b["work_count"]),
            _row(_t("Authors"), "number", numbers_a["author_count"], numbers_b["author_count"]),
        ]},
        {"heading": _t("How it is played"), "rows": [
            _row(_t("Direct play"), "hours", _hours(method_a["direct"]), _hours(method_b["direct"])),
            _row(_t("Transcode"), "hours", _hours(method_a["transcoded"]), _hours(method_b["transcoded"])),
            _row(_t("Transcode share"), "percent", share(method_a), share(method_b)),
        ]},
        {"heading": _t("Library"), "rows": [
            _row(_t("Works added"), "number", added_a["works"], added_b["works"]),
            _row(_t("Data added"), "bytes", added_a["bytes"], added_b["bytes"]),
        ]},
    ]
    genres = _genre_rows(analysis_a, analysis_b)
    if genres:              # a library without genres has no empty table
        groups.append({"heading": _t("Which genre was read"), "rows": genres, "with_share": True})
    return {
        "a": _side(first, analysis_a), "b": _side(second, analysis_b),
        "groups": groups,
        "same_length": abs(seconds_a - seconds_b) <= max(seconds_a, seconds_b) * 0.01,
        "overlap": _overlap(first, second),
        "has_data": bool(analysis_a["rows"] or analysis_b["rows"]),
    }
