"""Statistics of the books library and the data of the book page and the books library in the Library.

Part of the statistics package (see jellyscope/stats/__init__.py), the second half of the books statistics (the
first is stats/books_listening.py - listening and reading). jellyscope/books/sync.py fills the library. What
disappeared from Jellyfin (`missing = 1`) does not count into the library's numbers; the book page shows it with a
flag, so that links from the history do not lead into emptiness.

Everything is counted in **works** (books_listening.books_works_index): the several files of one audiobook are one
work, an e-book in two formats too. The library's functions take the list of works once (`books_library_works`), so a
page with five cards reads the table of books once.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .. import db
from ..books import folded
from .books_listening import (
    AUDIOBOOK,
    BOOK,
    READ_FROM_SECONDS,
    TICKS,
    _split,
    books_analysis,
    books_first,
    books_works_index,
    work_key,
)
from .kinds import only_books
from .periods import whole_history_period

WORK_SORTS = ("added", "name", "author", "year", "size")
WORKS_PER_PAGE = 60
RECENT_LISTENS = 25
TOP_WORKS = 10


def _natural(text: Any) -> list[Any]:
    """A sort key that puts "Chapter 2" before "Chapter 10"."""
    return [int(part) if part.isdigit() else folded(part) for part in re.split(r"(\d+)", str(text or ""))]


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------

def books_library_works(library_id: str | None = None) -> list[dict[str, Any]]:
    """The works of the library that are in Jellyfin; `library_id`: of one Jellyfin library only.

    Without a library a work in two libraries is one work (the section-wide numbers); for one library only its own
    files are grouped, so the work counts in each library it is in (the library's tile, grid and Overview agree).
    """
    return [w for w in books_works_index(library_id).values() if not w["missing"]]


def books_library_summary(works: list[dict[str, Any]] | None = None, library_id: str | None = None) -> dict[str, int]:
    """E-books, audiobooks (works), authors, size (bytes) and the length of the audiobooks (seconds)."""
    works = books_library_works(library_id) if works is None else works
    return {"ebook_count": sum(1 for w in works if w["kind"] == BOOK),
            "audiobook_count": sum(1 for w in works if w["kind"] == AUDIOBOOK),
            "author_count": len({w["author"] for w in works if w["author"]}),
            "size_bytes": sum(w["size_bytes"] for w in works),
            "seconds": sum(w["runtime_ticks"] for w in works if w["kind"] == AUDIOBOOK) // TICKS}


def books_formats(works: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Formats by the file's extension: files, share (%), size. An unknown format ("") is always the last."""
    works = books_library_works() if works is None else works
    files: Counter = Counter()
    sizes: Counter = Counter()
    for work in works:
        for name, entry in work["formats"].items():
            files[name.upper()] += entry["files"]
            sizes[name.upper()] += entry["size_bytes"]
    total = sum(files.values())
    return [{"format": f, "file_count": n, "share": n / total * 100, "size_bytes": sizes[f]}
            for f, n in sorted(files.items(), key=lambda pair: (pair[0] == "", -pair[1], pair[0].lower()))]


def books_decades(works: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Works by the decade of release; works without a year at the end (`decades` None)."""
    counts: Counter = Counter()
    for work in books_library_works() if works is None else works:
        year = work["year"]
        counts[year // 10 * 10 if isinstance(year, int) and year > 0 else None] += 1
    return [{"decades": d, "work_count": n} for d, n in sorted(counts.items(), key=lambda pair: (pair[0] is None, pair[0] or 0))]


def books_library_genres(works: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Genres by the count of works (a work with more genres counts for each)."""
    counts: Counter = Counter()
    for work in books_library_works() if works is None else works:
        for genre in set(_split(work["genres"])):
            counts[genre] += 1
    return [{"genre": z, "work_count": n} for z, n in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))]


def books_newest(works: list[dict[str, Any]] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """The works added last (from the newest; an unknown date last)."""
    works = sorted(books_library_works() if works is None else works, key=lambda w: (folded(w["title"]), w["id"]))
    # A stable sort by the date alone, reversed: works of the same date keep the order by the title.
    dated = sorted((w for w in works if w["added_at"]), key=lambda w: str(w["added_at"]), reverse=True)
    return (dated + [w for w in works if not w["added_at"]])[:limit]


def books_largest(works: list[dict[str, Any]] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """The largest works on the disk."""
    works = books_library_works() if works is None else works
    return sorted(works, key=lambda w: (-w["size_bytes"], folded(w["title"]), w["id"]))[:limit]


def books_unread(works: list[dict[str, Any]] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Works nobody has opened in the whole history - the longest waiting (added first) first.

    The whole history, not the period: in a short period the card would list almost the whole library. A work
    counts as read when any of its files was open for at least READ_FROM_SECONDS seconds.
    """
    works = books_library_works() if works is None else works
    read = {str(r["item_id"]) for r in db.query_all(
        "SELECT DISTINCT item_id FROM playback WHERE item_id IN (SELECT id FROM book)"
        f" AND watched_seconds >= ?{only_books()}", (READ_FROM_SECONDS,))}
    unread = [w for w in works if not read.intersection(w["ids"])]
    return sorted(unread, key=lambda w: (w["added_at"] is None, str(w["added_at"] or ""), folded(w["title"]), w["id"]))[:limit]


# ---------------------------------------------------------------------------
# The books library in the Library: the grid and the book page
# ---------------------------------------------------------------------------

def library_book_works(library_id: str, search: str = "", sort: str = "added", page: int = 1,
                       per_page: int = WORKS_PER_PAGE) -> dict[str, Any]:
    """The works of one books library for its grid: search, sort, one page.

    Search looks into the title, the author and the series; case and diacritics do not matter and `%` or `_` are
    plain characters - the filtering is done here, not in SQL, so SQLite and PostgreSQL agree. Works that
    disappeared from Jellyfin are not in the grid (their page stays). Sort: added (newest first), name, author, year
    (newest first), size (largest first); anything else is "added". Returns {"works", "total", "page", "pages"}.
    """
    works = books_library_works(library_id)
    needle = folded(search.strip())
    if needle:
        works = [w for w in works if needle in folded(w["title"]) or needle in folded(w["author"])
                 or needle in folded(w["series"])]
    # By title first (stable sorts below keep it for equal keys), the id last - a fixed order everywhere.
    works.sort(key=lambda w: (folded(w["title"]), w["id"]))
    if sort == "author":
        works.sort(key=lambda w: (not w["author"], folded(w["author"])))      # the books without an author last
    elif sort == "year":
        works.sort(key=lambda w: (w["year"] is not None, w["year"] or 0), reverse=True)
    elif sort == "size":
        works.sort(key=lambda w: w["size_bytes"], reverse=True)
    elif sort != "name":
        works.sort(key=lambda w: str(w["added_at"] or ""), reverse=True)       # unknown date last
    total = len(works)
    pages = max(1, -(-total // per_page))
    page = min(max(1, int(page or 1)), pages)
    return {"works": works[(page - 1) * per_page:page * per_page], "total": total, "page": page, "pages": pages}


def _readers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for x in rows:
        person = people.setdefault(x["user_id"] or "name:" + str(x["user_name"]),
                                   {"user_id": x["user_id"], "user_name": x["user_name"], "seconds": 0})
        person["seconds"] += x["watched_seconds"]
    return sorted(people.values(), key=lambda c: (-c["seconds"], str(c["user_name"] or "").lower()))


def book_detail(book_id: str) -> dict[str, Any] | None:
    """The book page: the work of the file `book_id` with all its files and its time over the whole history.

    Any file of a multi-file audiobook leads to the same work. Every file is listed - one that vanished from
    Jellyfin with its flag - but only present files count into size and length. Returns None for an unknown id; a
    vanished book still has its page.
    """
    row = db.query_one("SELECT id, kind, title_key FROM book WHERE id = ?", (book_id,))
    if row is None:
        return None
    key = work_key(row["kind"], row["title_key"], row["id"])
    index = books_works_index()
    work = index[key]
    library = db.query_one("SELECT name FROM libraries WHERE id = ?", (work["library_id"],)) if work["library_id"] else None
    rows = books_analysis(whole_history_period(first=books_first()), work=key, works=index)["rows"]
    time_by_file: Counter = Counter()
    for x in rows:
        time_by_file[x["item_id"]] += x["watched_seconds"]
    files = [{**dict(r), "seconds": time_by_file.get(r["id"], 0)} for r in db.query_all_in(
        "SELECT id, name, format, size_bytes, runtime_ticks, missing FROM book WHERE id IN ({})", work["ids"])]
    files.sort(key=lambda f: (_natural(f["name"]), f["id"]))
    present = [f for f in files if not f["missing"]]
    return {"work": work, "library_name": library["name"] if library else "", "files": files,
            "size": sum(int(f["size_bytes"] or 0) for f in present),
            "seconds": sum(int(f["runtime_ticks"] or 0) for f in present) // TICKS,
            "listening": sum(x["watched_seconds"] for x in rows if not x["reading"]),
            "reading": sum(x["watched_seconds"] for x in rows if x["reading"]),
            "readers": _readers(rows)}


# ---------------------------------------------------------------------------
# A books library in the Library: its Overview and Activity tabs, and the Books section of the Library page
# ---------------------------------------------------------------------------
def books_library_overview(library_id: str | None = None, cards: tuple[str, ...] | None = None) -> dict[str, Any]:
    """What a books library holds now (no period): numbers and the data of the asked library cards.

    The keys of the cards are those of the books card registry (cards_books.py), so the page draws the same cards
    as the registry - here over one library, or over every books library when `library_id` is None (the Books
    section of the Library page). `cards`: the keys asked for (None = all), the page takes them from
    cards_books.PAGES["library"].
    """
    works = books_library_works(library_id)
    builders = {"books_formats": lambda: {"rows": books_formats(works)},
                "books_years": lambda: {"rows": books_decades(works)},
                "books_library_genres": lambda: {"rows": books_library_genres(works)},
                "books_largest": lambda: {"rows": books_largest(works)}}
    return {"numbers": books_library_summary(works), **{key: builders[key]() for key in (cards or tuple(builders))}}


def books_library_listening(library_id: str, days: Any) -> dict[str, Any]:
    """The Activity tab of a books library: listening and reading of the period, and the latest rows.

    `totals` (listening, reading, readers, works), the most read works and the readers (both as `label`/`hours`
    for a bar chart; a reader row keeps `user_id`, so anonymisation knows the label is a name), and the last
    RECENT_LISTENS rows whatever the period.
    """
    # The works index is not needed here (the titles come from the rows), so an empty one is given.
    analysis = books_analysis(days, library_id=library_id, works={})
    rows = analysis["rows"]
    works: dict[str, dict[str, Any]] = {}
    for row in rows:
        work = works.setdefault(row["work"], {"label": row["title"] or "?", "seconds": 0})
        work["seconds"] += row["watched_seconds"]
    top = sorted(works.values(), key=lambda a: (-a["seconds"], folded(a["label"])))[:TOP_WORKS]
    recent = db.query_all(
        "SELECT p.started_at, p.user_id, COALESCE(u.name, p.user_name) AS user_name, p.item_id, p.item_type, p.play_method,"
        " p.watched_seconds, b.title, b.author"
        " FROM playback p JOIN book b ON b.id = p.item_id LEFT JOIN users u ON u.id = p.user_id"
        f" WHERE b.library_id = ? AND p.watched_seconds > 0{only_books('p.')}"
        " ORDER BY p.started_at DESC, p.id DESC LIMIT ?", (library_id, RECENT_LISTENS))
    return {"totals": {"listening": sum(x["watched_seconds"] for x in rows if not x["reading"]),
                       "reading": sum(x["watched_seconds"] for x in rows if x["reading"]),
                       "reader_count": len({x["user_id"] or x["user_name"] for x in rows}), "work_count": len(works)},
            "top_works": [{"label": a["label"], "hours": a["seconds"] / 3600} for a in top],
            "readers": [{"label": p["user_name"] or "?", "user_id": p["user_id"], "hours": p["seconds"] / 3600}
                        for p in _readers(rows)],
            "recent": recent}
