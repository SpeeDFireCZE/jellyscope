"""Book covers from Readarr: provisional ones for the card "Stahuje se".

The card "Stahuje se" (downloading) shows books with a cover. A book from the books library has
its cover from Jellyfin (the /image/ route, put together by the cards). A book still downloading is not in
the library - Readarr itself gives it a cover: it keeps the cover of each of its books
(`mediacover`). Jellyscope never connects anywhere outside for a cover.

**One cover per book** in the image store (images.py, a file by the kind `book` and the book's
numeric id in Readarr), only a real image is stored and "none" is remembered for five minutes. A cover
can be asked for only for a book Jellyscope knows from Readarr (the list of books, or the queue
just read) and that is not in the library - only the book's number is taken from the request's
address.

Once the book arrives in the library the card switches to the cover from Jellyfin and the next sync
deletes the provisional file (`clean_up_posters`).
"""
from __future__ import annotations

import asyncio
import logging
import time

from ... import db, images
from ...services import ServiceError
from . import progress
from .client import SETTINGS_PREFIX, ReadarrClient
from .settings import is_connected

log = logging.getLogger("jellyscope.arr")

# How long the note "this book has no cover" holds (seconds).
MISSING_SECONDS = 300
# The kind of title in the image store (images.TITLE_KINDS).
MEDIA_TYPE = "book"
# {book id: when its cover was last not found - time.monotonic()}. Only in memory.
_MISSING: dict[int, float] = {}
MAX_REMEMBERED = 5000


def _connection() -> tuple[str, str]:
    """Readarr's address and key from the settings."""
    return db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")


def poster_missing(book_id: int | None) -> bool:
    """Is it known that the book has no cover (Readarr did not serve it a moment ago)?

    For the card "Stahuje se": its body changes every five seconds and with every swap the browser
    would ask for the missing cover again - and the placeholder would flash in place of the cover
    every time. While the note holds, the card draws the placeholder right away; then it offers the
    cover again and it is tried once more.
    """
    return time.monotonic() - _MISSING.get(book_id, float("-inf")) < MISSING_SECONDS


async def provisional_poster(book_id: int) -> tuple[bytes, str] | None:
    """The book's provisional cover with the image type. None = there is none.

    From the disk, else from Readarr - and what is found is stored. Only what really is an image by its
    content is stored (an app behind a proxy can send a login page instead of a cover). An unknown
    book, a book already in the library and a Readarr that is not connected end without a request
    outside.
    """
    # The database and the disk in a thread: the cover route runs in the server's loop and the loop
    # would wait for them as a whole.
    local = await asyncio.to_thread(_from_disk, book_id)
    if local is None:
        return None
    stored, url, key = local
    if stored:
        return stored
    if poster_missing(book_id):
        return None
    try:
        async with ReadarrClient(url, key) as client:
            content = await client.cover(book_id)
    except ServiceError as error:
        log.debug("the poster of title %s could not be downloaded from %s: %s", book_id, "readarr", error)
        content = None
    stored = await asyncio.to_thread(_store, book_id, content) if content else None
    if not stored:
        if len(_MISSING) > MAX_REMEMBERED:          # a safeguard: the memory does not grow for ever
            _MISSING.clear()
        _MISSING[book_id] = time.monotonic()
    return stored


def _from_disk(book_id: int) -> tuple[tuple[bytes, str] | None, str, str] | None:
    """The local part of the cover search: (the stored cover or None, Readarr's address and key).

    None = there is nothing to ask (Readarr is not connected, the book is unknown or already in the library).
    """
    if not is_connected():
        return None
    known = db.query_one("SELECT jellyfin_id FROM readarr_title WHERE title_id = ?", (book_id,))
    if known is None and not progress.in_queue(book_id):
        return None
    if known and known["jellyfin_id"]:
        return None
    return (images.load_title(MEDIA_TYPE, str(book_id)), *_connection())


def _store(book_id: int, content: bytes) -> tuple[bytes, str] | None:
    """Store a downloaded cover and return it as the store serves it. None = it was no image."""
    return images.load_title(MEDIA_TYPE, str(book_id)) if images.save_title(MEDIA_TYPE, str(book_id), content) else None


def clean_up_posters() -> int:
    """Delete the provisional covers of books that are in the library by now. Returns the count.

    A book in the library has its cover from Jellyfin and the cards switched to it by themselves -
    the provisional file would only lie on the disk. Called by Readarr's sync.
    """
    on_disk = sorted({book for kind, book in images.titles_on_disk() if kind == MEDIA_TYPE})
    if not on_disk:
        return 0
    books = db.query_all_in("SELECT title_id FROM readarr_title WHERE jellyfin_id <> '' AND title_id IN ({})",
                             [int(book) for book in on_disk])
    deleted = sum(images.forget_title(MEDIA_TYPE, str(r["title_id"])) for r in books)
    if deleted:
        log.info("deleted %s provisional posters of %s titles that are now in the library", deleted, "readarr")
    return deleted
