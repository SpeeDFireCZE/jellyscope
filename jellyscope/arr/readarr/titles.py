"""Readarr's books and their counterparts in the books library.

The history Jellyscope fetches from Readarr knows a book only by its id in Readarr and by its title.
So that the cards can link a book into the library, the sync also fetches Readarr's list of books and
authors - with the ISBN-13 and the ASIN of every edition of a book (the editions are asked for apart:
the list of books carries only the monitored one).

Pairing is with the books library Jellyscope already has (the table `book`), so no request to
Jellyfin is made here:

* a book pairs by an **ISBN-13** - any of the editions Readarr knows for the book may be the one the
  library's book carries (`book.isbn`, Jellyfin's `Isbn`; hyphens do not matter and an ISBN-10 is
  read as the ISBN-13 it stands for);
* a book without a shared ISBN pairs by its **title and its author**, folded the way the books
  section folds them (`books.book_title_key`, stored as `book.title_key`): case and diacritics do not
  matter. The title alone is not enough: two authors have a book called "Mort".

A book that is not in the library (or went missing from it) has an empty counterpart: its card shows
the title without a link. Pairing is recomputed on every sync - a local query, and a book that left
the library is not to keep a link that leads nowhere. Where several library books share one key (the
parts of a multi-file audiobook), the first by id is the counterpart - the same on every run.

Readarr cannot page the list of books (a big library has tens of MB of it), so it is not fetched
every time: only the first time, when the history brought a book Jellyscope does not know yet, and
otherwise once a day (`REFRESH`).

None of this is a condition of the history sync: when the lists or the pairing fail, the history is
fetched anyway and the links are filled in next time.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from ... import db
from ...books import book_title_key
from ...services import utc_time, integer
from .client import NAME, SETTINGS_PREFIX, ReadarrClient, ReadarrError

log = logging.getLogger("jellyscope.arr")

COLUMNS = ("title_id", "name", "author", "author_id", "foreign_book_id", "isbns", "asins", "is_ebook",
           "released_at", "added_at", "synced_at")
# `jellyfin_id` is left out of the upsert on purpose: only the pairing with the library writes it, so
# a list from the app does not delete a pairing already done.
UPSERT = (f"INSERT INTO readarr_title ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (title_id) DO UPDATE SET " + ", ".join(f"{s} = excluded.{s}" for s in COLUMNS[1:]))
# An ISBN-13 is thirteen digits (ASCII only: `str.isdigit` would let digits of other scripts in), an
# ISBN-10 nine digits and a digit or X. An ASIN is ten letters and digits. Anything else from the app
# is not stored (it would never pair anyway).
_ISBN13 = re.compile(r"[0-9]{13}")
_ISBN10 = re.compile(r"[0-9]{9}[0-9X]")
_ASIN = re.compile(r"[0-9A-Z]{10}")
# How often the list of books is refreshed even when the history brought nothing new: because of
# corrected ids and books that have no history yet.
REFRESH = timedelta(days=1)
# The version of what is stored from the list of books. A list fetched by an older version is fetched
# again once at once, not after a day.
LIST_VERSION = "1"
# A release date before this year is no date: .NET sends year 1 for an empty one.
OLDEST_YEAR = 1000
# The ISBNs (and the ASINs) of a book's editions are joined by this in one column.
SEPARATOR = "|"

# The settings key with the time of the last fetched list of books (UTC); the list's version is
# under MARK_KEY + "_version".
MARK_KEY = f"{SETTINGS_PREFIX}_titles"


def _needed(seen: set[int]) -> bool:
    """Is the list of books to be fetched? The first time, after a day, after an upgrade, or when the history brought an unknown book.

    `seen` are the book ids from the history just fetched. Only they are asked about, not the whole
    history: a book Readarr no longer has would be missing from the list for ever and the list would
    be fetched on every sync because of it.
    """
    # On the very first sync the history is read page by page and `seen` is empty (the pages are written
    # as they come), so the unknown-book check below could not tell anything: this version check is
    # what forces the first fetch (nothing is stored, so the version never matches).
    if (db.get_setting(MARK_KEY + "_version", "") or "") != LIST_VERSION:
        return True
    last = db.get_setting(MARK_KEY, "") or ""
    try:
        age = datetime.strptime(db.utcnow(), db.TIME_FORMAT) - datetime.strptime(last, db.TIME_FORMAT)
    except ValueError:
        return True                 # never yet (or a mark that is no time)
    if age >= REFRESH or age < timedelta(0):
        return True
    known = {r["title_id"] for r in db.query_all_in(
        "SELECT title_id FROM readarr_title WHERE title_id IN ({})", sorted(seen))}
    return bool(seen - known)


def _isbn13(value: Any) -> str:
    """An ISBN as an ISBN-13 of digits only ("978-80-7432-255-6" -> "9788074322556"), else empty.

    An ISBN-10 becomes the ISBN-13 with the same nine digits: 978 in front, a new check digit.
    """
    text = re.sub(r"[-\s]", "", str(value if value is not None else "")).upper()
    if _ISBN13.fullmatch(text):
        return text
    if _ISBN10.fullmatch(text):
        body = "978" + text[:9]
        total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(body))
        return body + str((10 - total % 10) % 10)
    return ""


def _asin(value: Any) -> str:
    text = str(value if value is not None else "").strip().upper()
    return text if _ASIN.fullmatch(text) else ""


def _date(value: Any) -> str | None:
    released = utc_time(value)
    # The year is read from the number before the first dash: `strftime` does not pad years under 1000 with zeros.
    if released and not OLDEST_YEAR <= int(released.split("-", 1)[0]) <= 9000:
        return None
    return released


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def title_rows(records: Any, authors: Any = None) -> list[dict[str, Any]]:
    """Readarr's list of books as rows of the table. A broken record is skipped.

    `authors` is Readarr's list of authors: it gives the books the author's name (the author embedded
    in a book is the fallback). Another app's answer is input like any other: a record without a
    numeric id is not used and an ISBN that is none is stored empty. Only a few fields are taken - the
    rest is let go at once.
    """
    by_author = {integer(a.get("id"), None): a for a in authors if isinstance(a, dict)} if isinstance(authors, list) else {}
    rows = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        book_id = integer(record.get("id"), None)
        if book_id is None:
            continue
        author_id = integer(record.get("authorId"), None)
        author = _dict(by_author.get(author_id)) or _dict(record.get("author"))
        editions = [e for e in record.get("editions") or [] if isinstance(e, dict)] if isinstance(record.get("editions"), list) else []
        rows.append({"title_id": book_id, "name": str(record.get("title") or ""), "author": str(author.get("authorName") or ""),
                     "author_id": author_id, "foreign_book_id": str(record.get("foreignBookId") or "").strip(),
                     "isbns": SEPARATOR.join(dict.fromkeys(i for i in (_isbn13(e.get("isbn13")) for e in editions) if i)),
                     "asins": SEPARATOR.join(dict.fromkeys(a for a in (_asin(e.get("asin")) for e in editions) if a)),
                     "is_ebook": int(any(e.get("isEbook") is True for e in editions)),
                     "released_at": _date(record.get("releaseDate")), "added_at": _date(record.get("added"))})
    return rows


def _store(rows: list[dict[str, Any]]) -> None:
    """Upsert the books. A book Readarr no longer has stays in the table - the history still points at it."""
    now = db.utcnow()
    with db.connect() as conn:
        conn.executemany(UPSERT, [tuple({**r, "synced_at": now}[s] for s in COLUMNS) for r in rows])
        conn.commit()


def _pair_books() -> int:
    """Give every Readarr book its book in the books library (or empty). Returns the count of paired books.

    It walks every stored book, those Readarr no longer has too: a book that left the library loses
    its pairing - the link would lead nowhere. A library book that went missing is not a counterpart.
    Where several library books fit, the first by id holds (the choice is the same on every run).
    """
    by_isbn: dict[str, str] = {}
    by_key: dict[str, str] = {}
    for r in db.query_all("SELECT id, isbn, title_key FROM book WHERE missing = 0 ORDER BY id"):
        if _isbn13(r["isbn"]):
            by_isbn.setdefault(_isbn13(r["isbn"]), r["id"])
        if r["title_key"]:
            by_key.setdefault(r["title_key"], r["id"])
    changes, paired = [], 0
    for r in db.query_all("SELECT title_id, name, author, isbns, jellyfin_id FROM readarr_title"):
        book = next((by_isbn[i] for i in str(r["isbns"]).split(SEPARATOR) if i in by_isbn), "")
        if not book and r["name"] and r["author"]:
            book = by_key.get(book_title_key(r["name"], r["author"]), "")
        paired += bool(book)
        if book != (r["jellyfin_id"] or ""):
            changes.append((book, r["title_id"]))
    if changes:
        with db.connect() as conn:
            conn.executemany("UPDATE readarr_title SET jellyfin_id = ? WHERE title_id = ?", changes)
            conn.commit()
    return paired


async def _fetch_list(client: ReadarrClient) -> int | None:
    """Fetch and store Readarr's list of books. Returns their count, None = it failed.

    Readarr's error deletes nothing and does not move the fetch mark: the next sync tries again. The
    list of authors only names the books: without it (it failed) the books are stored with the
    names embedded in them.
    """
    try:
        records = await client.titles()
    except ReadarrError as error:
        log.info("could not download the title list of %s, will retry next time: %s", NAME, error)
        return None
    try:
        authors = await client.authors()
    except ReadarrError as error:
        log.info("could not download the author list of %s, the books keep the names they came with: %s", NAME, error)
        authors = []
    # The list of books carries only the monitored edition of a book: the others are asked for apart.
    # When that fails the books keep the editions that came with them.
    try:
        by_book: dict[int, list[Any]] = {}
        for edition in await client.editions([i for i in (integer(r.get("id"), None) for r in records) if i is not None]):
            by_book.setdefault(integer(edition.get("bookId"), None), []).append(edition)   # type: ignore[arg-type]
        for record in records:
            if integer(record.get("id"), None) in by_book:
                record["editions"] = by_book[integer(record.get("id"), None)]
    except ReadarrError as error:
        log.info("could not download the editions of %s, the books keep the editions they came with: %s", NAME, error)
    # Conversion and writing in a thread, so the web runs meanwhile (as with the history).
    rows = await asyncio.to_thread(title_rows, records, authors)
    del records
    await asyncio.to_thread(_store, rows)
    db.set_setting(MARK_KEY, db.utcnow())
    db.set_setting(MARK_KEY + "_version", LIST_VERSION)
    return len(rows)


async def sync_titles(client: ReadarrClient, seen: Iterable[Any] = ()) -> int:
    """Refresh Readarr's list of books (if needed) and pair them with the books library.

    Called by the history sync with its open client; `seen` are the book ids from the history just
    fetched (see `_needed`). Returns the count of fetched books (0 = the list was not fetched this
    time or it failed). Neither Readarr's error nor the library's brings the history sync down: the
    links are extra, the numbers do not depend on them.
    """
    fetched = 0
    if await asyncio.to_thread(_needed, {i for i in seen if isinstance(i, int)}):
        fetched = await _fetch_list(client) or 0
    paired = await asyncio.to_thread(_pair_books)
    log.debug("%s titles: %s books matched to the library", NAME, paired)
    return fetched


def delete_titles() -> None:
    """Delete Readarr's stored books (together with the downloaded history)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM readarr_title")
        conn.commit()
    db.set_setting(MARK_KEY, "")
    db.set_setting(MARK_KEY + "_version", "")
