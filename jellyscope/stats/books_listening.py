"""Books statistics: listening to audiobooks, reading e-books and their rankings.

Part of the statistics package (see jellyscope/stats/__init__.py). A row of the play history with the kind
"AudioBook" (listening) or "Book" (reading in Jellyfin's reader - if Jellyfin reports it at all) belongs here
(`only_books`); the book, its author and its work are added to it by the table `book` that jellyscope/books/sync.py
fills.

A page picks the period's rows once (`books_analysis`) and every card computes from the same rows, like the music
cards.

Two notions:

* **time** - the seconds really listened to or read, every row counts. Listening and reading are told apart
  (`listening`, `reading`), because one is an audio length and the other the time a reader stayed open;
* **a work** - what a person calls "a book": the several files of one audiobook (parts, chapters) are one work.
  The work's key is the kind and the book's stored `title_key` (title and author, folded), see `work_key`.

A book the sync does not know yet (added and played before the next run) counts right away - under its name from
the history, without an author, and alone (its own work).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .. import db, formatting
from .kinds import only_books
from .periods import OLDEST_HISTORY, _as_period
from .shared import local_fast

TICKS = 10_000_000
# A book that somebody has opened for at least this many seconds is "read" ("what nobody reads" leaves it out).
READ_FROM_SECONDS = 30
AUDIOBOOK = "audiobook"
BOOK = "book"
_SHARED_TEXT_COLUMNS = ("user_id", "user_name", "client", "device_name", "item_id", "item_name", "title", "author",
                        "genres", "work")


def work_key(kind: str, title_key: Any, item_id: Any) -> str:
    """The key of a work: the kind and the stored title key; a book without one (not synced yet) is its own work.

    A key is `<kind>:<title_key>` or `<kind>:id:<item id>`: a title_key always contains "|" (title|author), so a
    part starting with "id:" and without "|" is an item id, never a title key.
    """
    return f"{kind}:{title_key}" if title_key else f"{kind}:id:{item_id}"


def _split(text: Any) -> list[str]:
    return [part.strip() for part in str(text or "").split("|") if part.strip()]


def books_works_index(library_id: str | None = None) -> dict[str, dict[str, Any]]:
    """Every work of the library (present or not) by its key: the parts of an audiobook grouped.

    The files of a work in two libraries are one work here; with `library_id` only the files of that library are
    grouped, so a library's own views count the work in every library it is in.

    A work carries the representative part's `id` (the one with a cover, then the earliest added; it is where the
    work's page and cover come from), `title`, `author`, `series`, `year`, `genres`, `formats` ({format: files,
    size}), `size_bytes`, `runtime_ticks`, `file_count`, `added_at` (the last part added), `library_id` and
    `missing` (all the parts vanished from Jellyfin).
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    where, params = ("", ()) if library_id is None else (" WHERE library_id = ?", (library_id,))
    for row in db.query_all("SELECT id, kind, title, author, series, year, genres, format, size_bytes, runtime_ticks,"
                            " cover_tag, added_at, library_id, missing, title_key FROM book" + where, params):
        groups[work_key(row["kind"], row["title_key"], row["id"])].append(dict(row))
    works: dict[str, dict[str, Any]] = {}
    for key, parts in groups.items():
        present = [p for p in parts if not p["missing"]] or parts
        # The representative: with a cover first, then the earliest added (an unknown date last), the id last.
        rep = min(present, key=lambda p: (not p["cover_tag"], p["added_at"] is None, str(p["added_at"] or ""), p["id"]))
        formats: dict[str, dict[str, int]] = {}
        for part in present:
            entry = formats.setdefault(str(part["format"] or ""), {"files": 0, "size_bytes": 0})
            entry["files"] += 1
            entry["size_bytes"] += int(part["size_bytes"] or 0)
        dates = [str(p["added_at"]) for p in present if p["added_at"]]
        works[key] = {
            "key": key, "id": rep["id"], "kind": rep["kind"], "title": rep["title"], "author": rep["author"] or "",
            "series": rep["series"] or "", "year": next((p["year"] for p in present if p["year"]), None),
            "genres": next((p["genres"] for p in present if p["genres"]), ""), "cover_tag": rep["cover_tag"] or "",
            "formats": formats, "size_bytes": sum(int(p["size_bytes"] or 0) for p in present),
            "runtime_ticks": sum(int(p["runtime_ticks"] or 0) for p in present), "file_count": len(present),
            "added_at": max(dates) if dates else None, "library_id": rep["library_id"],
            "missing": all(p["missing"] for p in parts), "ids": [p["id"] for p in parts]}
    return works


def books_analysis(days: Any, *, library_id: str | None = None, work: str | None = None,
                   works: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The period's listening and reading with the book and its work - once for the whole page.

    Returns {"rows": [...], "since", "until", "works"}. A row carries `reading` (True for an e-book), `work` (the
    work's key) and `local` (the time in the app's zone); `works` is the index of books_works_index (pass one already
    read, so a page reads the table of books once). `library_id` asks only for one library (the Activity tab of a
    books library), `work` for one work (its page) - the database returns only that work's rows.
    """
    period = _as_period(days)
    where, params = "", [period.since, period.until]
    if library_id is not None:
        where += " AND b.library_id = ?"
        params.append(library_id)
    if work is not None:
        kind, _, rest = work.partition(":")
        if rest.startswith("id:") and "|" not in rest:   # a book the sync does not know: its own work, by the item
            where += " AND p.item_id = ?"
            params.append(rest[3:])
        else:
            where += " AND b.kind = ? AND b.title_key = ?"
            params += [kind, rest]
    # Row by row (query_iter): the whole history may be hundreds of thousands of rows.
    rows = db.query_iter(
        f"""
        SELECT p.id, p.session_key, p.user_id, COALESCE(u.name, p.user_name) AS user_name,
               p.item_id, p.item_name, p.item_type, p.started_at, p.watched_seconds, p.play_method,
               p.transcode_reasons, p.client, p.device_name,
               b.title, b.author, b.genres, b.kind, b.title_key
          FROM playback p
          LEFT JOIN users u ON u.id = p.user_id
          LEFT JOIN book b ON b.id = p.item_id
         WHERE p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           {only_books("p.")}{where}
         ORDER BY p.started_at, p.id
        """,
        tuple(params),
    )
    zone = formatting.zone()
    texts: dict[Any, Any] = {}
    share = texts.setdefault
    result = []
    for r in rows:
        r["reading"] = r["item_type"] == "Book"
        r["work"] = work_key(r["kind"] or (BOOK if r["reading"] else AUDIOBOOK), r.pop("title_key"), r["item_id"])
        r["title"] = r["title"] or r["item_name"] or ""
        r["author"] = r["author"] or ""
        for column in _SHARED_TEXT_COLUMNS:
            value = r[column]
            r[column] = share(value, value)
        r["watched_seconds"] = int(r["watched_seconds"] or 0)
        r["local"] = local_fast(r["started_at"], zone)
        result.append(r)
    return {"rows": result, "since": period.since, "until": period.until,
            "works": books_works_index() if works is None else works}


def books_first() -> str | None:
    """When the history of books starts (the first row of listening or reading), or None."""
    first = db.query_value(f"SELECT MIN(started_at) FROM playback WHERE watched_seconds > 0 AND started_at >= ?{only_books()}",
                           (OLDEST_HISTORY,), default=None)
    return str(first) if first else None


def reading_reported() -> bool:
    """Has Jellyfin ever reported reading an e-book (a row of the kind "Book")? When not, the cards say so.

    Through the books table (`item_id IN (SELECT id FROM book)`): the index on the item is used and the search does not
    run to the end of the whole history when Jellyfin reports no reading.
    """
    return bool(db.query_value(
        f"SELECT 1 FROM playback WHERE item_id IN (SELECT id FROM book) AND item_type = 'Book' AND watched_seconds > 0{only_books()}"
        " LIMIT 1", default=None))


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def books_numbers(analysis: dict[str, Any]) -> dict[str, int]:
    """Listening to audiobooks and reading time (seconds), readers, different works and different authors."""
    rows = analysis["rows"]
    return {"listening": sum(x["watched_seconds"] for x in rows if not x["reading"]),
            "reading": sum(x["watched_seconds"] for x in rows if x["reading"]),
            "reader_count": len({x["user_id"] or x["user_name"] for x in rows}),
            "work_count": len({x["work"] for x in rows}),
            "author_count": len({x["author"] for x in rows if x["author"]})}


def books_by_day(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Time by day (in the app's zone): seconds of listening and of reading. Only days with some."""
    by_day: dict[str, dict[str, Any]] = {}
    for x in analysis["rows"]:
        day = x["local"].date().isoformat()
        row = by_day.setdefault(day, {"day": day, "listening": 0, "reading": 0})
        row["reading" if x["reading"] else "listening"] += x["watched_seconds"]
    return [by_day[day] for day in sorted(by_day)]


def books_heatmap(analysis: dict[str, Any]) -> list[list[float]]:
    """Hours by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0.0] * 24 for _ in range(7)]
    for x in analysis["rows"]:
        moment = x["local"]
        grid[moment.weekday()][moment.hour] += x["watched_seconds"] / 3600
    return grid


def books_readers(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Readers and listeners from the most hours: seconds and the work they spent the most time with."""
    people: dict[str, dict[str, Any]] = {}
    works_by_person: dict[str, Counter] = defaultdict(Counter)
    for x in analysis["rows"]:
        person_key = x["user_id"] or "name:" + str(x["user_name"])
        person = people.setdefault(person_key, {"user_id": x["user_id"], "user_name": x["user_name"], "seconds": 0})
        person["seconds"] += x["watched_seconds"]
        works_by_person[person_key][x["work"]] += x["watched_seconds"]
    for person_key, person in people.items():
        top = sorted(works_by_person[person_key].items(), key=lambda pair: (-pair[1], pair[0]))
        info = _work_info(analysis, top[0][0]) if top else None
        person["work_id"] = info["id"] if info else None
        person["work"] = info["title"] if info else ""
    return sorted(people.values(), key=lambda c: (-c["seconds"], str(c["user_name"] or "").lower()))


def _work_info(analysis: dict[str, Any], key: str) -> dict[str, Any] | None:
    """A work from the library; one the sync does not know is made from its first row of the history.

    Such a work has no `id`: it is not in `book`, so it has no page (a link would answer 404) - the title is text only.
    """
    known = analysis["works"].get(key)
    if known is not None:
        return known
    for x in analysis["rows"]:
        if x["work"] == key:
            return {"id": None, "title": x["title"], "author": x["author"], "cover_tag": "",
                    "kind": BOOK if x["reading"] else AUDIOBOOK, "genres": x["genres"] or ""}
    return None


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------

def books_works(analysis: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    """The most read and listened works: id (the page), title, author, kind, cover, seconds, readers."""
    groups: dict[str, dict[str, Any]] = {}
    people: dict[str, set[str]] = defaultdict(set)
    for x in analysis["rows"]:
        group = groups.setdefault(x["work"], {"key": x["work"], "seconds": 0})
        group["seconds"] += x["watched_seconds"]
        people[x["work"]].add(str(x["user_id"] or x["user_name"]))
    result = []
    for key, group in groups.items():
        info = _work_info(analysis, key) or {}
        result.append({**group, "id": info.get("id"), "title": info.get("title") or "", "author": info.get("author") or "",
                       "kind": info.get("kind") or AUDIOBOOK, "cover_tag": info.get("cover_tag") or "",
                       "reader_count": len(people[key])})
    return sorted(result, key=lambda s: (-s["seconds"], s["title"].lower(), s["key"]))[:limit]


def books_authors(analysis: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
    """The most read authors (an unknown author is one group with the name "")."""
    groups: dict[str, dict[str, Any]] = {}
    people: dict[str, set[str]] = defaultdict(set)
    works: dict[str, set[str]] = defaultdict(set)
    for x in analysis["rows"]:
        group = groups.setdefault(x["author"], {"name": x["author"], "seconds": 0})
        group["seconds"] += x["watched_seconds"]
        people[x["author"]].add(str(x["user_id"] or x["user_name"]))
        works[x["author"]].add(x["work"])
    for name, group in groups.items():
        group["reader_count"] = len(people[name])
        group["work_count"] = len(works[name])
    return sorted(groups.values(), key=lambda s: (-s["seconds"], s["name"].lower()))[:limit]


def books_genres(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Genres by time. A book with more genres counts for each."""
    seconds_by_genre: Counter = Counter()
    for x in analysis["rows"]:
        for genre in set(_split(x["genres"])):
            seconds_by_genre[genre] += x["watched_seconds"]
    return [{"genre": z, "seconds": s} for z, s in sorted(seconds_by_genre.items(), key=lambda pair: (-pair[1], pair[0]))]


# ---------------------------------------------------------------------------
# Technique
# ---------------------------------------------------------------------------

def books_play_method(analysis: dict[str, Any]) -> dict[str, Any]:
    """Seconds of listening direct and transcoded and the transcode reasons (Jellyfin reports several, comma separated).

    Only audiobooks: an e-book is a file that is not played, so it has no play method.
    """
    direct = transcoded = 0
    reasons: Counter = Counter()
    for x in analysis["rows"]:
        if x["reading"]:
            continue
        if str(x["play_method"] or "").startswith("Transcode"):
            transcoded += x["watched_seconds"]
            for reason in str(x["transcode_reasons"] or "").split(","):
                if reason.strip():
                    reasons[reason.strip()] += 1
        else:
            direct += x["watched_seconds"]
    return {"direct": direct, "transcoded": transcoded,
            "reasons": [{"reason": d, "count": n} for d, n in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))]}


def books_apps(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """The apps listened and read in: seconds and how many devices."""
    groups: dict[str, dict[str, Any]] = {}
    devices: dict[str, set[str]] = defaultdict(set)
    for x in analysis["rows"]:
        app = str(x["client"] or "")
        group = groups.setdefault(app, {"app": app, "seconds": 0})
        group["seconds"] += x["watched_seconds"]
        devices[app].add(str(x["device_name"] or ""))
    for app, group in groups.items():
        group["device"] = len(devices[app])
    return sorted(groups.values(), key=lambda s: (-s["seconds"], s["app"].lower()))
