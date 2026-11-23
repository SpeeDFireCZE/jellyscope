"""Readarr's statistics over the downloaded history.

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/arr/readarr/sync.py downloads the history
into the database.

The functions take `days` - anything `_as_period` takes (a count of days or a ready Period) - and compute in the
app's zone.

The unit is the **book**. Readarr writes one import per file (`bookFileImported`), so what "was added" is an
import of a book, put together from the files:

* **The book's import** - the files imported with the same `download_id` (and the same book; a day without a file
  ends the import - the same release grabbed again has the same `download_id`). Imports without
  a `download_id` (a manual import) are grouped by the book and the day (UTC): the files of one manual import
  come within seconds, a day is the same book's next import already. The book's time is its first file's, its
  size is the sum of its files, its quality the first file's.
* **The import's grab** - the grab (`grabbed`, one record per book) with the same `download_id`, the last one before
  the import. The indexer, the client, the protocol and the source (RSS, search, a manual pick...) are taken from
  it. A grab without `download_id` pairs by the book: the last not yet paired grab of the same book before the import.
* **Upgrade** - deleting a book file with the reason `Upgrade`. Readarr writes it when taking a better version.
  It is paired by the **book**, not by the file: files belong to a release (an edition) of the book, and an upgrade
  to another edition deletes the old files with the old release's file ids and imports the new ones under the new
  release's ids - no file id is shared. Readarr also deletes the old files *before* the import starts and writes
  the file-imported events only after the whole book was moved, so a big book can come many minutes after the
  deletion. A deletion therefore pairs with the first import of the same book that starts from a minute before it
  to `BOOK_UPGRADE_WINDOW` after it. A book import with at least one such deletion is **one** upgrade, however many
  files were deleted - and a book downloaded twice (MP3, then FLAC) is two imports of the book, the second an upgrade.

Pairing and percentiles are computed in Python over rows picked by one query - SQL would need window functions for
it, which behave differently in SQLite and PostgreSQL. The pairing itself and the quantile are shared
(stats/shared.py); what a book, an import and an upgrade are, this file knows.
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
# The window of pairing a deletion (Upgrade) with the book's next import: the import is written only after the whole
# book was moved and its files were imported one by one, so it starts long after the deletion for a big book (or
# a slow disk); three hours is generous and still far from a second upgrade of the same book. The import may also
# start a minute before the deletion (the same second ordered by id, clocks).
BOOK_UPGRADE_WINDOW = timedelta(hours=3)
BOOK_UPGRADE_SLACK = timedelta(minutes=1)
# Files of one download come within minutes; the same download_id a day after the last file is another import.
SAME_IMPORT = timedelta(days=1)

_COLUMNS = ("arr_id, event_type, happened_at, title, author, item_id, title_id, author_id, quality, size_bytes, indexer,"
            " release_group, client, protocol, source, reason, download_id, released_at")


def _is_import(row: dict[str, Any]) -> bool:
    return row["event_type"] == "bookFileImported"


def _is_upgrade(row: dict[str, Any]) -> bool:
    return row["event_type"] == "bookFileDeleted" and row["reason"] == "Upgrade"


def _import_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """What makes a file import part of the same book import: the download, or without one the book and the day."""
    if row["download_id"]:
        return ("download", row["download_id"], row["title_id"])
    return ("manual", row["title_id"], row["happened_at"][:10])


def _book_imports(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The file imports (sorted by time) put together into imports of books (not yet marked as upgrades)."""
    by_key: dict[tuple[Any, ...], list[list[dict[str, Any]]]] = {}
    for file in files:
        parts = by_key.setdefault(_import_key(file), [])
        # The same download_id can repeat (the same release grabbed again): a file long after the last one is the next import.
        if parts and _time(file["happened_at"]) - _time(parts[-1][-1]["happened_at"]) <= SAME_IMPORT:
            parts[-1].append(file)
        else:
            parts.append([file])
    books = []
    for part in (p for parts in by_key.values() for p in parts):
        first = part[0]
        books.append({
            "arr_id": first["arr_id"], "happened_at": first["happened_at"], "title": first["title"], "author": first["author"],
            "title_id": first["title_id"], "author_id": first["author_id"],
            # A grab without a download_id pairs by the book, so the book's id is the item here (a grab has no file).
            "item_id": first["title_id"], "download_id": first["download_id"],
            "quality": first["quality"], "size_bytes": sum(int(t["size_bytes"] or 0) for t in part), "file_count": len(part),
            "release_group": next((t["release_group"] for t in part if t["release_group"]), ""),
            "client": next((t["client"] for t in part if t["client"]), ""),
            "released_at": next((t["released_at"] for t in part if t["released_at"]), None),
            "upgrade": False, "old_quality": "",
        })
    return sorted(books, key=lambda a: (a["happened_at"], a["arr_id"]))


def _mark_upgrades(deletions: list[dict[str, Any]], books: list[dict[str, Any]]) -> None:
    """Marks the book imports that replaced files: `upgrade` and `old_quality` (see the module's text for the window)."""
    by_book: dict[Any, list[dict[str, Any]]] = {}
    for a in books:
        by_book.setdefault(a["title_id"], []).append(a)
    replaced: dict[int, list[dict[str, Any]]] = {}
    for d in deletions:
        moment = _time(d["happened_at"])
        for a in by_book.get(d["title_id"], []):      # sorted by time: the first one in the window
            if moment - BOOK_UPGRADE_SLACK <= _time(a["happened_at"]) <= moment + BOOK_UPGRADE_WINDOW:
                replaced.setdefault(a["arr_id"], []).append(d)
                break
    for a in books:
        if a["arr_id"] in replaced:
            a["upgrade"] = True
            a["old_quality"] = min(replaced[a["arr_id"]], key=lambda d: d["arr_id"])["quality"]


def _analysis(days: Any) -> dict[str, Any]:
    """The period's events with the neighbours needed for pairing, already put together into books and paired.

    A month before the period is picked too (grabs of the imports in the period) and three hours after it (the
    import of an upgrade right before the end). Only what is in the period counts into the numbers then.
    """
    period = _as_period(days)
    rows = db.query_all(
        f"SELECT {_COLUMNS} FROM readarr_event WHERE happened_at >= ? AND happened_at < ? ORDER BY happened_at, arr_id",
        (_shift(period.since, -GRAB_WINDOW), _shift(period.until, BOOK_UPGRADE_WINDOW)))
    rows = [dict(r) for r in rows]

    def in_period(r: dict[str, Any]) -> bool:
        return period.since <= r["happened_at"] < period.until

    # A grab belongs to a book (its record has no file): pairing without a download_id goes by the book.
    grabs = [{**r, "item_id": r["title_id"]} for r in rows if r["event_type"] == "grabbed"]
    files = [r for r in rows if _is_import(r)]
    deletions = [r for r in rows if _is_upgrade(r)]
    imports = _book_imports(files)
    _mark_upgrades(deletions, imports)
    return {
        "imports": [i for i in imports if in_period(i)],
        "grabs_list": [g for g in grabs if in_period(g)],
        "upgrades": [i for i in imports if i["upgrade"] and in_period(i)],
        "grab_imports": _pair_grabs(grabs, imports),
    }


def readarr_events(days: Any = 30) -> dict[str, Any]:
    """The period's events paired once - for a page that wants more statistics.

    Every statistic (`readarr_additions`, `readarr_heatmap`, `readarr_speed`, `readarr_sources`, `readarr_upgrades`) takes the
    result as `analysis=` and does not have to pick and pair the events again.
    """
    return _analysis(days)


# ---------------------------------------------------------------------------
# What was added and when
# ---------------------------------------------------------------------------

def readarr_additions(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Imports of books by day (in the app's zone): count, files, size, new ones and upgrades. Only days with an import."""
    r = analysis or _analysis(days)
    by_day: dict[str, dict[str, Any]] = {}
    for i in r["imports"]:
        day = _local(i["happened_at"]).date().isoformat()
        row = by_day.setdefault(day, {"day": day, "count": 0, "file_count": 0, "size_bytes": 0, "new_count": 0, "upgrade_count": 0})
        row["count"] += 1
        row["file_count"] += i["file_count"]
        row["size_bytes"] += i["size_bytes"]
        row["upgrade_count" if i["upgrade"] else "new_count"] += 1
    return [by_day[day] for day in sorted(by_day)]


def readarr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Imports of books by day of the week and hour: 7 x 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0] * 24 for _ in range(7)]
    for i in (analysis or _analysis(days))["imports"]:
        moment = _local(i["happened_at"])
        grid[moment.weekday()][moment.hour] += 1
    return grid


def readarr_largest(days: Any = 30, limit: int = 10, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The largest imported books in the period. `title_id` is the book's id in Readarr (see readarr_library)."""
    imports = (analysis or _analysis(days))["imports"]
    ordered = sorted(imports, key=lambda i: (-i["size_bytes"], i["happened_at"], i["arr_id"]))
    keys = ("title", "author", "title_id", "size_bytes", "file_count", "happened_at", "quality")
    return [{k: i[k] for k in keys} for i in ordered[:max(0, int(limit))]]


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------

def readarr_speed(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """How long a download takes (grab -> the book's first file) and how soon after its release a book is in the library.

    Times in seconds, the median and p90. An import before the release (a preview, a wrong date in the
    database) does not count into "since the release", only its count shows - a negative time would drag the median
    to nonsense.

    Upgrades do not belong to "since the release" either: it asks how soon a book was added to the library, and
    a better version of the same book a day later would count every book a second time and later. Upgrades do
    belong to the download time - they were downloaded.

    And only what **was waited for** counts: the book was already in Readarr when it came out (the time it was added
    <= `released_at`; Readarr says it per book, it is stored in `readarr_title.added_at`). An old book added to the
    library years after its release would otherwise have years "since the release" and drag the numbers to nonsense.
    What does not count this way has only its count: `not_counted` = the book was added only after its release,
    `unknown` = we do not know when it was added (Readarr no longer has the book, or the list of books was not
    downloaded yet). Both times are text in UTC of the same shape, they compare as text. A book without a release date
    has nothing to measure from and is left out.
    """
    r = analysis or _analysis(days)
    download_times = [(_time(i["happened_at"]) - _time(r["grab_imports"][i["arr_id"]]["happened_at"])).total_seconds()
                      for i in r["imports"] if i["arr_id"] in r["grab_imports"]]
    download_times = [s for s in download_times if s >= 0]
    with_release = [i for i in r["imports"] if i["released_at"] and not i["upgrade"]]
    added = {t["title_id"]: t["added_at"] for t in db.query_all_in(
        "SELECT title_id, added_at FROM readarr_title WHERE added_at IS NOT NULL AND title_id IN ({})",
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

def readarr_sources(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Where the imported books came from: indexers, groups, clients, protocols, qualities.

    It counts over the book imports (what really was added), the size is the book's size. The indexer, the
    protocol are from the import's grab; the client's name from the import (a grab has only the client's type), the
    group, when there is no grab, from the import too. Books have no resolutions.
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
            # The import's own name is the client's instance in Readarr; a grab carries the client's type name.
            "clients": i["client"] or g.get("client"),
            "protocols": g.get("protocol"),
            "qualities": i["quality"],
        }
        for group, value in values.items():
            name = value or UNKNOWN
            counts[group][name] += 1
            sizes[group][name] += i["size_bytes"]
    return {s: _ranked(counts[s], sizes[s]) for s in groups}


# ---------------------------------------------------------------------------
# Upgrades and activity
# ---------------------------------------------------------------------------

def _trigger(source: Any) -> str:
    return TRIGGERS.get(str(source or ""), "unknown")


def readarr_upgrades(days: Any = 30, analysis: dict[str, Any] | None = None, titles: int = 10) -> dict[str, Any]:
    """How often books are replaced by a better version, from which quality to which and how it was triggered.

    An upgrade is an import of a book that replaced files (one however many files it replaced).
    `how` = what grabbed the version that brought the upgrade (automatic, user, manual, external, unknown);
    `how_all` = the same for all grabs in the period, so that there is something to compare with. `titles` is how
    many of the most often upgraded books to return (the card takes more and hides the rest in a window).
    """
    r = analysis or _analysis(days)
    upgrades = r["upgrades"]
    item_count = len({i["title_id"] if i["title_id"] is not None else (i["title"], i["author"]) for i in upgrades})
    transitions: Counter = Counter()
    by_book: Counter = Counter()
    names: dict[Any, dict[str, Any]] = {}
    trigger_counts: Counter = Counter()
    for i in upgrades:
        transitions[(i["old_quality"] or UNKNOWN, i["quality"] or UNKNOWN)] += 1
        key = i["title_id"] if i["title_id"] is not None else (i["title"], i["author"])
        by_book[key] += 1
        names.setdefault(key, {"title": i["title"] or UNKNOWN, "author": i["author"], "title_id": i["title_id"]})
        g = r["grab_imports"].get(i["arr_id"])
        trigger_counts[_trigger(g["source"]) if g else "unknown"] += 1
    ranked = sorted(by_book, key=lambda k: (-by_book[k], names[k]["title"], names[k]["author"], str(k)))
    return {
        "count": len(upgrades),
        "item_count": item_count,
        "per_item": round(len(upgrades) / item_count, 2) if item_count else 0,
        "transitions": [{"old": old_quality, "new": new_quality, "count": n} for (old_quality, new_quality), n in
                        sorted(transitions.items(), key=lambda p: (-p[1], p[0]))],
        "titles": [{**names[k], "count": by_book[k]} for k in ranked[:max(0, int(titles))]],
        "how": dict(trigger_counts),
        "how_all": dict(Counter(_trigger(g["source"]) for g in r["grabs_list"])),
    }


def readarr_first() -> str:
    """The time of Readarr's first downloaded event (UTC), empty without a history.

    The start of "the whole history" on the Readarr tab - its history usually reaches far before the first play in
    Jellyscope. A nonsense date (before OLDEST_HISTORY) does not count, the same as with plays.
    """
    return str(db.query_value("SELECT MIN(happened_at) FROM readarr_event WHERE happened_at >= ?",
                              (OLDEST_HISTORY,), default="") or "")


def readarr_failures(days: Any = 30) -> int:
    """How many downloads failed in the period."""
    period = _as_period(days)
    return int(db.query_value(
        "SELECT COUNT(*) FROM readarr_event WHERE event_type = 'downloadFailed' AND happened_at >= ? AND happened_at < ?",
        (period.since, period.until)) or 0)


# ---------------------------------------------------------------------------
# Links into the library
# ---------------------------------------------------------------------------

def readarr_library(title_ids: Iterable[Any] | None = None) -> dict[int, dict[str, str]]:
    """Readarr's books that have a counterpart in the books library: {the book's id in Readarr: its library data}.

    The value is `{"book_id", "cover_tag"}`: the book's id in Jellyfin and the fingerprint of its cover. The cards turn
    them into an address with the helpers of the books cards (cards_books.book_link, cover). There is no author page,
    so the author is always a text.

    `title_ids` limits the search to the books the card really draws (a few dozen rows) - without them the whole
    Readarr library would be looked up on every page load. Without them (`None`) all books. A book that is not in the
    library is not in the result: the card leaves it without a link (a link leading nowhere is worse than none).

    It pairs by the Jellyfin id stored with Readarr's books (arr/readarr/titles.py: by ISBN, else by title and author),
    never by name here. A pairing is recomputed on every sync, so one can be stale in between: a book that has gone
    missing from the library meanwhile (`missing`) does not count.
    """
    if title_ids is None:
        rows = db.query_all("SELECT title_id, jellyfin_id FROM readarr_title WHERE jellyfin_id <> '' ORDER BY title_id")
    else:
        rows = db.query_all_in("SELECT title_id, jellyfin_id FROM readarr_title WHERE jellyfin_id <> '' AND title_id IN ({}) ORDER BY title_id",
                               sorted({int(i) for i in title_ids if isinstance(i, int)}))
    books = {r["id"]: r["cover_tag"] for r in db.query_all_in(
        "SELECT id, cover_tag FROM book WHERE missing = 0 AND id IN ({}) ORDER BY id",
        sorted({r["jellyfin_id"] for r in rows}))}
    return {r["title_id"]: {"book_id": r["jellyfin_id"], "cover_tag": str(books[r["jellyfin_id"]] or "")}
            for r in rows if r["jellyfin_id"] in books}
