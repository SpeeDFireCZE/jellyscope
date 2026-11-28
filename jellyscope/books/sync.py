"""Books sync: the e-books and audiobooks of Jellyfin's books libraries.

Part of the books package (see jellyscope/books/__init__.py). Listening and reading themselves are not
here - the collector writes them into the playback history (`playback`, types "AudioBook" and
"Book"). This only fetches what the books libraries hold, so that they have a title, an author, a
format and a size.

Two modes in one task (`synchronize`), like the music sync:

* **incremental** - items from the newest added, read only up to the first page where everything
  is known already. A few requests every hour;
* **full pass** - when none has run yet or FULL_PASS_EVERY has passed since the last one. Goes
  through everything, fixes changed names, and what Jellyfin did not show gets `missing = 1` (the
  row stays - history and charts would lose their names). When reading ends with an error,
  nothing is marked missing.

After every run, imported history without a type whose item is a known book is marked as
listening / reading (`item_type = 'AudioBook'` / `'Book'`).

What Jellyfin returns about books (facts in docs/superpowers/specs/2026-11-23-books-design.md, answers
built by hand in tests/books_responses/ - the test Jellyfin has no books): libraries of the type
"books"; items of the types `Book` (an e-book, its author in `People` with `Type` "Author") and
`AudioBook` (`Album`, `AlbumArtist`, `Artists`, `RunTimeTicks`); a multi-file audiobook is several
`AudioBook` items with the same `Album` and `AlbumArtist`; `ProviderIds` may hold an `Isbn`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db, i18n
from ..jellyfin import BOOK_FIELDS, JellyfinClient, JellyfinError
from ..services import ServiceError, integer, read_state, utc_time, write_state

log = logging.getLogger("jellyscope.books")

NAME = "Books"                                  # a translation key - the name shown in the UI
STATE_KEY = "books_state"                       # the result of the last run (JSON, services.write_state)
FIRST_SYNC_KEY = "books_first_sync"             # set once the first run succeeded (content_kinds: before it books are not measured)
FULL_PASS_KEY = "books_full_pass"               # when the last full pass ran (UTC)
GONE_LIBRARIES_KEY = "books_vanished_libraries"   # books libraries with stored books the last run did not find in Jellyfin (JSON)
ITEM_TYPES = "Book,AudioBook"
PAGE_SIZE = 200                                 # items per request
FULL_PASS_EVERY = timedelta(hours=24)
# A task at a time every day starts a minute earlier or later than yesterday (when the scheduler's tick falls):
# without slack the full pass came about every other day (28. 11. 2026).
FULL_PASS_SLACK = timedelta(hours=1)
# A sentence of its own, not services.SYNC_ALREADY_RUNNING: "Synchronizace {nazev}" would read oddly.
ALREADY_RUNNING = "Books sync is already running."

def folded(text: Any) -> str:
    """Text without case and diacritics ("Čapek" = "capek"), the rule of the music library's search."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").strip().casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def book_title_key(title: str, author: str) -> str:
    """The key that makes the parts of a multi-file audiobook one book: the title and the author, folded.

    It is stored in the column `book.title_key` when a row is written, so that counts and charts group on
    it the same way on SQLite and PostgreSQL (their LOWER() folds different sets of letters).
    """
    return f"{folded(title)}|{folded(author)}"


# Is a sync running? A set of at most one element: it changes without `global` and tests set it
# from outside.
_RUNNING: set[str] = set()


class BooksError(ServiceError):
    """The books sync failed (Jellyfin did not answer, or it is running already)."""


# ---------------------------------------------------------------------------
# Jellyfin items to rows
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _genres(item: dict[str, Any]) -> str:
    genres = item.get("Genres")
    return "|".join(_text(g) for g in genres if _text(g)) if isinstance(genres, list) else ""


def _source(item: dict[str, Any]) -> dict[str, Any]:
    sources = item.get("MediaSources")
    return sources[0] if isinstance(sources, list) and sources and isinstance(sources[0], dict) else {}


def _author(item: dict[str, Any]) -> str:
    """The first People entry of the type Author, else AlbumArtist, else the first of Artists."""
    people = item.get("People")
    for person in people if isinstance(people, list) else []:
        if isinstance(person, dict) and _text(person.get("Type")).lower() == "author" and _text(person.get("Name")):
            return _text(person["Name"])
    if _text(item.get("AlbumArtist")):
        return _text(item["AlbumArtist"])
    artists = item.get("Artists")
    for artist in artists if isinstance(artists, list) else []:
        if _text(artist):
            return _text(artist)
    return ""


def _format(path: str, item: dict[str, Any]) -> str:
    """The file extension in lower case ("m4b"); without a path the first name of Jellyfin's container."""
    extension = posixpath.splitext(path.replace("\\", "/"))[1].lstrip(".").lower()
    return extension or _text(item.get("Container")).split(",")[0].lower()


def _provider_ids(item: dict[str, Any]) -> tuple[str, str]:
    """(ISBN, ProviderIds as JSON) - empty strings when Jellyfin has none."""
    ids = item.get("ProviderIds")
    if not isinstance(ids, dict) or not ids:
        return "", ""
    isbn = next((_text(v) for k, v in ids.items() if str(k).lower() == "isbn" and _text(v)), "")
    return isbn, json.dumps({str(k): _text(v) for k, v in ids.items() if _text(v)}, sort_keys=True, ensure_ascii=False)


def _image_tag(item: dict[str, Any]) -> str:
    tags = item.get("ImageTags")
    return _text(tags.get("Primary")) if isinstance(tags, dict) else ""


def _book_row(item: dict[str, Any], library: str, now: str) -> dict[str, Any]:
    row = _row(item, library, now)
    row["title_key"] = book_title_key(row["title"], row["author"])
    return row


def _row(item: dict[str, Any], library: str, now: str) -> dict[str, Any]:
    audio = item.get("Type") == "AudioBook"
    source = _source(item)
    path = _text(source.get("Path")) or _text(item.get("Path"))
    name = _text(item.get("Name"))
    isbn, provider_ids = _provider_ids(item)
    return {"id": _text(item.get("Id")), "kind": "audiobook" if audio else "book", "name": name,
            # The work: the audiobook's Album when it has one (the parts of a multi-file audiobook share it).
            "title": (_text(item.get("Album")) if audio else "") or name,
            "author": _author(item), "series": _text(item.get("SeriesName")),
            "year": integer(item.get("ProductionYear"), None), "genres": _genres(item),
            "format": _format(path, item) or _format("", source), "size_bytes": integer(source.get("Size"), None),
            "runtime_ticks": (integer(item.get("RunTimeTicks"), None) or integer(source.get("RunTimeTicks"), None)) if audio else None,
            # The file path puts the book on its disk (content_kinds.py); '' = Jellyfin gave none.
            "path": path, "isbn": isbn, "provider_ids": provider_ids, "cover_tag": _image_tag(item),
            "added_at": utc_time(item.get("DateCreated")), "library_id": library, "synced_at": now, "missing": 0}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _write(rows: list[dict[str, Any]]) -> None:
    """Upsert by id; an item that is back gets `missing` reset."""
    if not rows:
        return
    columns = list(rows[0])
    statement = (f"INSERT INTO book ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
                 f" ON CONFLICT (id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in columns[1:])}")
    with db.connect() as conn:
        conn.executemany(statement, [tuple(r[c] for c in columns) for r in rows])
        conn.commit()


def _gone_libraries(current: set[str]) -> int:
    """Books libraries with stored books that Jellyfin no longer has: their books are missing.

    A library is gone only when Jellyfin leaves it out **two runs in a row**: at its start (or with a
    token without rights) it returns an empty or incomplete list of libraries, and the books would vanish
    from the menu for an hour. Rows stay - history and charts still know the names. The record of the
    last full pass is cleared: should a library come back, the next run is a full pass (an incremental
    one would stop at the first known page). Returns the number of books.
    """
    stored = {str(r["library_id"]) for r in
              db.query_all("SELECT DISTINCT library_id FROM book WHERE missing = 0 AND library_id IS NOT NULL")}
    try:
        last_time = set(json.loads(db.get_setting(GONE_LIBRARIES_KEY, "") or "[]"))
    except (TypeError, ValueError):
        last_time = set()
    gone = stored - current
    confirmed = sorted(gone & last_time)
    db.set_setting(GONE_LIBRARIES_KEY, json.dumps(sorted(gone - set(confirmed))))
    if not confirmed:
        return 0
    placeholders = ", ".join("?" * len(confirmed))
    count = int(db.query_value(f"SELECT COUNT(*) FROM book WHERE missing = 0 AND library_id IN ({placeholders})",
                               tuple(confirmed)) or 0)
    with db.connect() as conn:
        conn.execute(f"UPDATE book SET missing = 1 WHERE missing = 0 AND library_id IN ({placeholders})", tuple(confirmed))
        conn.commit()
    db.set_setting(FULL_PASS_KEY, "")
    log.info("books: libraries disappeared from Jellyfin (%s), %s books missing", len(confirmed), count)
    return count


def _known_ids(ids: list[str]) -> set[str]:
    return {r["id"] for r in db.query_all_in("SELECT id FROM book WHERE id IN ({})", ids)} if ids else set()


# ---------------------------------------------------------------------------
# Reading from Jellyfin
# ---------------------------------------------------------------------------

async def _walk(client: Any, library: str, full: bool, now: str) -> tuple[int, int]:
    """Walk the items of one library page by page. Returns (read, new)."""
    read = new = 0
    start = 0
    while True:
        page = await client.items_page(start, PAGE_SIZE, ITEM_TYPES, None, library, sort_by="DateCreated,SortName",
                                       sort_order="Descending", fields=BOOK_FIELDS)
        items = [i for i in page.get("Items") or [] if isinstance(i, dict) and _text(i.get("Id"))]
        if not items:
            return read, new
        # The database in a thread: the task runs in the server's main loop, a big library means
        # hundreds of writes.
        known = await asyncio.to_thread(_known_ids, [_text(i["Id"]) for i in items])
        await asyncio.to_thread(_write, [_book_row(i, library, now) for i in items])
        read += len(items)
        new += len(items) - len(known)
        start += len(items)
        total = integer(page.get("TotalRecordCount"), None)
        # An incremental run ends on the first page where everything was known already.
        if (not full and len(known) == len(items)) or (total is not None and start >= total):
            return read, new


def _full_pass_due() -> bool:
    last = db.get_setting(FULL_PASS_KEY, "")
    if not last:
        return True
    try:
        when = datetime.strptime(last, db.TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return True
    return datetime.now(UTC) - when >= FULL_PASS_EVERY - FULL_PASS_SLACK


def _mark_missing(run: str, libraries: set[str]) -> int:
    """After a full pass: what Jellyfin did not show gets `missing = 1`. Returns the number of books.

    What this run saw has `synced_at` equal to `run` (the run's start time with microseconds -
    two runs in one second do not mix). Only in the `libraries` this run read: one Jellyfin left out
    of its list is not gone yet (_gone_libraries decides, after two runs).
    """
    before = int(db.query_value("SELECT COUNT(*) FROM book WHERE missing = 1") or 0)
    marks = ", ".join("?" * len(libraries))
    with db.connect() as conn:
        conn.execute(f"UPDATE book SET missing = 1 WHERE (synced_at IS NULL OR synced_at <> ?) AND library_id IN ({marks})",
                     (run, *sorted(libraries)))
        conn.commit()
    return int(db.query_value("SELECT COUNT(*) FROM book WHERE missing = 1") or 0) - before


# Ids of imported history (Playback Reporting, Jellystat) with dashes and capitals that match a known
# book once normalised. The import normalises ids only against items (importers/linking.py), where
# books are not.
_NORMALISED_ID = "LOWER(REPLACE(item_id, '-', ''))"
_ID_WITH_DASHES = f"item_id LIKE '%-%' AND {_NORMALISED_ID} IN (SELECT id FROM book)"


def _pair_history() -> int:
    """Imported history whose item is a known book (and not a movie or an episode) is listening / reading. Returns the rows.

    That is a row without a type or with a wrong one (an importer that knows only movies types it "Movie").

    First it normalises ids written with dashes to the form books have - otherwise the listening
    would not pair with its book (no title, no author). It changes only rows that would not pair.
    """
    # A server without books pays nothing for this every hour.
    if not db.query_value("SELECT 1 FROM book LIMIT 1", default=None):
        return 0
    with db.connect() as conn:
        conn.execute(f"UPDATE playback SET item_id = {_NORMALISED_ID} WHERE {_ID_WITH_DASHES}")
        conn.commit()
    where = ("(item_type IS NULL OR item_type NOT IN ('Book', 'AudioBook')) AND item_id IN (SELECT id FROM book)"
             " AND item_id NOT IN (SELECT id FROM items)")
    count = int(db.query_value(f"SELECT COUNT(*) FROM playback WHERE {where}") or 0)
    if count:
        with db.connect() as conn:
            conn.execute(f"UPDATE playback SET item_type = CASE WHEN (SELECT kind FROM book WHERE book.id = playback.item_id)"
                         f" = 'audiobook' THEN 'AudioBook' ELSE 'Book' END WHERE {where}")
            conn.commit()
    return count


async def _fetch(full: bool | None) -> dict[str, Any]:
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        libraries = [lib for lib in await client.virtual_folders() if isinstance(lib, dict) and lib.get("CollectionType") == "books"]
        gone = await asyncio.to_thread(_gone_libraries, {_text(lib.get("ItemId")) for lib in libraries})
        if not libraries:
            return {"library_count": 0, "missing": gone} if gone else {"library_count": 0}
        # After the gone libraries: when one is gone, the record of the full pass is cleared and this
        # run is a full one.
        full = await asyncio.to_thread(_full_pass_due) if full is None else full
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        # The keys of the result are shown in Settings > Tasks and stored with the task log.
        result: dict[str, Any] = {"library_count": len(libraries), "full": full, "item_count": 0, "new_count": 0}
        for library in libraries:
            read, new = await _walk(client, _text(library.get("ItemId")), full, now)
            result["item_count"] += read
            result["new_count"] += new
    # Work over whole tables (and the whole history) in a thread: the task runs in the server's main
    # loop and the web and the collector would stand still meanwhile.
    result["missing"] = gone
    if full:
        result["missing"] += await asyncio.to_thread(_mark_missing, now, {_text(lib.get("ItemId")) for lib in libraries})
        db.set_setting(FULL_PASS_KEY, now[:19])
    result["paired"] = await asyncio.to_thread(_pair_history)
    return result


async def synchronize(full: bool | None = None) -> dict[str, Any]:
    """Fetch the e-books and audiobooks of Jellyfin's books libraries.

    `full` = force (True) or forbid (False) a full pass; without it the time since the last one
    decides. Without a books library it returns {"library_count": 0} (and `missing` when that confirmed a
    library is gone - see _gone_libraries). A Jellyfin error is written into the state and raises
    BooksError.
    """
    if NAME in _RUNNING:
        raise BooksError(ALREADY_RUNNING)
    _RUNNING.add(NAME)
    try:
        result = await _fetch(full)
    except JellyfinError as error:
        log.warning("books sync failed: %s", error)
        write_state(STATE_KEY, "error", 0, error)
        raise BooksError("{reason}", reason=i18n.error_text(error)) from error
    finally:
        _RUNNING.discard(NAME)
    # The state even without a books library: the administrator sees in Settings > Tasks that the
    # run happened (and why the books vanished).
    write_state(STATE_KEY, "ok", int(result.get("new_count", 0)))
    if not db.get_setting(FIRST_SYNC_KEY, ""):
        db.set_setting(FIRST_SYNC_KEY, "1")
    if result.get("library_count"):
        log.info("books: %s new items (%s read)", result["new_count"], result["item_count"])
    return result


def sync_running() -> bool:
    return NAME in _RUNNING


def sync_state() -> dict[str, Any]:
    """The state of the last run (when, result, how many came, error) and the time of the last full pass."""
    return {**read_state(STATE_KEY), "full": db.get_setting(FULL_PASS_KEY, "")}


def has_books() -> bool:
    """Has Jellyscope anything to show in the Books section? At least one item that is in Jellyfin.

    A books library alone is not enough: an empty library would give a tab with nothing in it (as with
    music). The menu entry, the cards of the dashboards and the field in Settings follow this condition.
    """
    return bool(db.query_value("SELECT 1 FROM book WHERE missing = 0 LIMIT 1"))
