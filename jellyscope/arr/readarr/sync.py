"""The sync of Readarr's history into the database.

Readarr's history is a list of events: a grab (`grabbed`, one record per book), an import into the
library (`bookFileImported`, one record per book file), a failure, a deletion, a renaming and a
re-tagging of a file. The same API answers in Chaptarr and Bookshelf, the forks that continue Readarr.
The statistics are computed from its copy in the database - asking Readarr on every page view would be
slow, and the older records would be lost once Readarr itself purges them.

The first time the whole history is fetched, then only the new part: since the last successful sync
minus an hour (`OVERLAP`). The overlap covers records Readarr wrote just before the end of the last
sync, and different clocks of the two machines. Writing is an upsert by the record's id, so the
overlap doubles nothing - it only rewrites the same rows.

What is stored is in `event_from_history`; schema.sql describes the columns.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from ... import db, i18n, images
from ...jellyfin import digits_only
from ...services import SYNC_ALREADY_RUNNING as ALREADY_RUNNING
from ...services import utc_time, integer, read_state, write_state
from .client import NAME, SETTINGS_PREFIX, ReadarrClient, ReadarrError
from .posters import MEDIA_TYPE, clean_up_posters
from .settings import family_name, store_app_name
from .titles import delete_titles, sync_titles

log = logging.getLogger("jellyscope.arr")

BEGINNING = "2000-01-01T00:00:00Z"   # "from the beginning" (an invalid sync mark)
OVERLAP = timedelta(hours=1)
# The page size of the first sync (the whole history): about 3.5 MB of JSON.
PAGE_SIZE = 1000
# A safeguard against an endless loop, should the app page differently: 10 million records.
MAX_PAGES = 10_000

COLUMNS = ("arr_id", "event_type", "happened_at", "title", "author", "item_id", "title_id", "author_id", "quality",
           "size_bytes", "indexer", "release_group", "client", "protocol", "source", "reason",
           "download_id", "released_at", "cf_score")
# ON CONFLICT ... DO UPDATE works in SQLite and PostgreSQL alike. `excluded` is the row just tried
# to be inserted. Everything but the key is rewritten: Readarr may have filled in the name meanwhile
# (the book was added again) and the old row would otherwise keep the release's name.
UPSERT = (f"INSERT INTO readarr_event ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (arr_id) DO UPDATE SET "
          + ", ".join(f"{s} = excluded.{s}" for s in COLUMNS[1:]))

# Enumerations the API sends as a number in a string (protocol "1") or as a name.
PROTOCOLS = {"0": "", "1": "usenet", "2": "torrent", "unknown": "", "usenet": "usenet", "torrent": "torrent"}
SOURCES = ("Unknown", "Rss", "Search", "UserInvokedSearch", "InteractiveSearch", "ReleasePush")
# The event of a book file's import (the one that carries the client instance's name).
IMPORT = "bookFileImported"

SYNC_RUNNING = "{name} sync is running right now, the downloaded data cannot be deleted yet. Try again in a moment."

# Is Readarr "busy" right now? Its sync runs, or its downloaded data is being deleted. The button
# "Sync now", the scheduled task and the deletion can meet - the second one is refused.
# A set is enough, no lock: only code in the asyncio loop reads and changes it (never a thread) and
# there is no `await` between the check and the adding.
# The set has at most one item (the app's name): it changes without `global` and tests set it from outside.
_RUNNING: set[str] = set()


def sync_running() -> bool:
    """Is Readarr's sync running now? (Deleting the downloaded data waits for it.)"""
    return NAME in _RUNNING


def _key(what: str) -> str:
    return f"{SETTINGS_PREFIX}_{what}"


def _number(value: Any) -> int | None:
    """A number, even when it came as a string ("178129"). Nonsense = unknown (None)."""
    return integer(value, None)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _source(value: Any) -> str:
    text = _text(value)
    if digits_only(text):
        number = int(text)
        return SOURCES[number] if number < len(SOURCES) else ""
    return text


def _book_and_author(record: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    book, author = record.get("book") or {}, record.get("author") or {}
    return {
        # A deleted book: the history stays, but `book` is null. The release's name
        # ("Author - Book (1936) EPUB") is still better than nothing.
        "title": _text(book.get("title") or record.get("sourceTitle")),
        "author": _text(author.get("authorName")),
        # Only an import names its book file (`fileId`): it is the file's own id, not the history record's.
        # A grab, a deletion and the rest have none. Upgrades are paired by the book, not by the file -
        # the new edition's files have other ids.
        "item_id": _number(data.get("fileId")) or None,
        "title_id": _number(record.get("bookId")),
        "author_id": _number(record.get("authorId")),
        "released_at": utc_time(book.get("releaseDate")),
    }


def _client(event_type: str, data: dict[str, Any]) -> str:
    """The download client: an import carries the instance's name (`downloadClientName`, "SABnzbd main"), a grab and
    the failures the client's type (`downloadClient`, "SABnzbd") - the instance's name is the more telling one."""
    name, kind = _text(data.get("downloadClientName")), _text(data.get("downloadClient"))
    return (name or kind) if event_type == IMPORT else (kind or name)


def event_from_history(record: dict[str, Any]) -> dict[str, Any]:
    """One record from /api/v1/history/since as a row of Readarr's history."""
    data = record.get("data") or {}
    quality = (record.get("quality") or {}).get("quality") or {}
    event_type = _text(record.get("eventType"))
    row = {
        "arr_id": _number(record.get("id")),
        "event_type": event_type,
        "happened_at": utc_time(record.get("date")),
        "quality": _text(quality.get("name")),
        "size_bytes": _number(data.get("size")),
        "indexer": _text(data.get("indexer")),
        "release_group": _text(data.get("releaseGroup")),
        "client": _client(event_type, data),
        "protocol": PROTOCOLS.get(_text(data.get("protocol")).lower(), ""),
        "source": _source(data.get("releaseSource")),
        "reason": _text(data.get("reason")),
        "download_id": record.get("downloadId") or None,
        "cf_score": _number(data.get("customFormatScore", record.get("customFormatScore"))),
    }
    row.update(_book_and_author(record, data))
    return row


def _since() -> str | None:
    """Since when to ask: the last successful sync minus OVERLAP. None = the first time (the whole history)."""
    last = db.get_setting(_key("last_sync"), "")
    if not last:
        return None
    try:
        when = datetime.strptime(last, db.TIME_FORMAT) - OVERLAP
    except ValueError:
        return BEGINNING
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(records: Any) -> list[dict[str, Any]]:
    """Valid history records as rows of the table (without an id or a date they are dropped)."""
    if not isinstance(records, list):
        raise ReadarrError("{name} returned something other than a list on history/since.", name=NAME)
    rows = [event_from_history(r) for r in records if isinstance(r, dict)]
    return [r for r in rows if r["arr_id"] is not None and r["happened_at"]]


async def _page_by_page(client: ReadarrClient) -> tuple[int, int]:
    """The first sync: the whole history page by page, each written at once. Returns (new, total).

    The pages go oldest first, so what Readarr writes meanwhile arrives at the end and nothing is
    skipped. Converting JSON into rows and writing them run in a thread - a big history must not stop
    serving the other pages.
    """
    new = total = 0
    for page in range(1, MAX_PAGES + 1):
        answer = await client.history_page(page, PAGE_SIZE)
        records = answer.get("records") if isinstance(answer, dict) else None
        rows = await asyncio.to_thread(_rows, records if records is not None else [])
        new += await asyncio.to_thread(_write, rows)
        total += len(rows)
        if not records or len(records) < PAGE_SIZE or page * PAGE_SIZE >= int(answer.get("totalRecords") or 0):
            break
    return new, total


def event_count() -> int:
    """How many records of Readarr's history are downloaded."""
    return int(db.query_value("SELECT COUNT(*) FROM readarr_event") or 0)


def _write(rows: list[dict[str, Any]]) -> int:
    """Upsert in one transaction. Returns how many rows were added (not rewritten).

    What was added is told by the ids not yet in the table - a query by the primary key. Formerly
    every row was counted before and after the write, with the first sync of a big history twice for
    every page.
    """
    if not rows:
        return 0
    ids = [r["arr_id"] for r in rows]
    known = {r["arr_id"] for r in db.query_all_in(
        "SELECT arr_id FROM readarr_event WHERE arr_id IN ({})", ids)}
    with db.connect() as conn:
        conn.executemany(UPSERT, [tuple(r[s] for s in COLUMNS) for r in rows])
        conn.commit()
    return len(set(ids) - known)


def _write_state(result: str, new: int, error: Exception | None = None) -> None:
    """The card's state in Settings (the shared writer is services.write_state)."""
    write_state(_key("state"), result, new, error)


def sync_state() -> dict[str, Any]:
    """The last sync: when (UTC), the result ("ok" / "error" / ""), how many arrived, the error (translated)."""
    return read_state(_key("state"))


async def _check_app(client: ReadarrClient) -> None:
    """Make sure an app of the Readarr family answers (the same error as the connection test), and store which one.

    It runs before anything is fetched: another *Arr app at the address would give a history that is
    no books, and it would be written into the tables.
    """
    state = await client.status()
    name = family_name(state.get("appName"))
    if not name:
        raise ReadarrError("{other} answers at this address, not {name}.", other=str(state.get("appName") or "") or "?", name=NAME)
    store_app_name(name)


async def _fetch(transport: httpx.AsyncBaseTransport | None) -> dict[str, int]:
    url, key = db.get_setting(_key("url"), ""), db.get_setting(_key("api_key"), "")
    if not url or not key:
        raise ReadarrError("{name} has no address or API key filled in.", name=NAME)
    # The mark is the start of the sync, not its end: what Readarr writes during the fetch comes next
    # time (and the overlap covers it anyway).
    started = db.utcnow()
    since = _since()
    seen: set[Any] = set()        # book ids from the history just fetched (for the list of books)
    async with ReadarrClient(url, key, transport=transport) as client:
        await _check_app(client)
        if since is None:
            new, total = await _page_by_page(client)
        else:
            # The increment (an hour and a bit) is small: one answer. Converting and writing are
            # blocking work - in a thread, so the web runs meanwhile.
            rows = await asyncio.to_thread(_rows, await client.history_since(since))
            new, total = await asyncio.to_thread(_write, rows), len(rows)
            seen = {r["title_id"] for r in rows}
        # The list of books with their ISBNs (links from the cards into the library). After the history
        # and without effect on it: whatever goes wrong in it (even something nobody counted on), the
        # history is fetched and the sync is to end well - otherwise the mark would not move and the
        # state would report an error for ever.
        try:
            await sync_titles(client, seen)
            # Provisional covers of books that have arrived in the library meanwhile (the card "Downloading").
            await asyncio.to_thread(clean_up_posters)
        except Exception as error:      # noqa: BLE001 - see the comment above
            log.warning("could not process the titles of %s, the links will be filled in next time: %s: %s",
                        NAME, type(error).__name__, error)
    db.set_setting(_key("last_sync"), started)
    _write_state("ok", new)
    log.info("%s sync: %s new records of %s", NAME, new, total)
    return {"new_count": new, "total": total}


async def synchronize(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, int]:
    """Fetch Readarr's new history. Returns {"new_count", "total"}.

    An error is written into the state (and the exception goes on); the sync mark does not move on
    it, so the next time fetches what did not arrive this time too.
    """
    if NAME in _RUNNING:
        raise ReadarrError(ALREADY_RUNNING, name=NAME)
    _RUNNING.add(NAME)
    try:
        return await _fetch(transport)
    except ReadarrError as error:
        _write_state("error", 0, error)
        raise
    except Exception as error:
        _write_state("error", 0, ReadarrError("Sync failed: {reason}", reason=i18n.error_text(error)))
        raise
    finally:
        _RUNNING.discard(NAME)


def _delete() -> int:
    """Delete the rows, the sync mark and the state. Called only under the lock `_RUNNING`."""
    count = event_count()
    with db.connect() as conn:
        conn.execute("DELETE FROM readarr_event")
        conn.commit()
    delete_titles()
    # The provisional covers of its books go with them: once the app is disconnected nothing else would delete them.
    images.forget_kind(MEDIA_TYPE)
    db.set_setting(_key("last_sync"), "")
    db.set_setting(_key("state"), "")
    log.info("deleted the downloaded history of %s: %s records", NAME, count)
    return count


def delete_data() -> int:
    """Delete Readarr's downloaded history. Returns the count of deleted rows.

    It deletes the sync mark and the state too: the next sync (if Readarr is still connected) fetches
    the whole history again - it stayed in Readarr, this was only its copy. Refused during a sync: only
    what arrived after the deletion would stay, with a new mark.

    For calls outside the asyncio loop (scripts, tests). The web calls `delete_downloaded_data`, which
    locks Readarr for the time of the deletion.
    """
    if NAME in _RUNNING:
        raise ReadarrError(SYNC_RUNNING, name=NAME)
    return _delete()


async def delete_downloaded_data() -> int:
    """Delete the downloaded history in a thread and hold the sync's lock for that time.

    The check "no sync running" and the locking happen in the asyncio loop without an `await` between
    them, so nothing fits in between. Without the lock a sync started during the deletion (the
    scheduler, the button) would fetch only the increment, the deletion would delete its rows and the
    mark under its hands, and it would then write a new mark - the whole history would never be
    fetched again.
    """
    if NAME in _RUNNING:
        raise ReadarrError(SYNC_RUNNING, name=NAME)
    _RUNNING.add(NAME)
    try:
        return await asyncio.to_thread(_delete)
    finally:
        _RUNNING.discard(NAME)
