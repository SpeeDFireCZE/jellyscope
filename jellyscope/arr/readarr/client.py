"""Readarr's API v1 client (and of its forks Chaptarr and Bookshelf, which keep the same API).

The API is at `<address>/api/v1/...` and the key goes in the `X-Api-Key` header. The connection,
errors and the key's protection are in the shared core (`jellyscope/services.py`) - only Readarr's
requests are here. `ReadarrError` carries a template and the values apart, so it can be translated
into the language of whoever reads it, and the key is never in it.

    async with ReadarrClient(url, key) as client:
        state = await client.status()
"""
from __future__ import annotations

from typing import Any

import httpx

from ...jellyfin import DEFAULT_TIMEOUT
from ...services import ServiceError, HttpClient, integer

# What the app calls itself (`appName` in /api/v1/system/status). By it the connection test tells
# that another app with the same API answers at Readarr's address.
NAME = "Readarr"
# The apps that answer to Readarr's API and are one connection for Jellyscope: Readarr itself (retired
# upstream) and the forks that continue it. The name they report is shown in the tab's subtitle.
FAMILY = ("Readarr", "Chaptarr", "Bookshelf")
# The prefix of the keys in the settings table: arr_readarr_url, arr_readarr_api_key, …
SETTINGS_PREFIX = "arr_readarr"

# What to embed into a record of the history: the book and the author, so the names need no
# separate look-up.
INCLUDE = {"includeBook": "true", "includeAuthor": "true"}
# What is taken from the list of books (see ReadarrClient.titles). The filter works at every level
# of the answer, so the nested objects (the editions, the author) are listed by the fields they are
# read for; the rest (images, links, ratings, overviews) is dropped already while the answer is parsed.
BOOK_FIELDS = ("id", "title", "authorId", "foreignBookId", "releaseDate", "added", "editions", "isbn13", "asin", "isEbook",
               "author", "authorName", "foreignAuthorId")
# What is taken from the list of editions (`edition?bookId=...`).
EDITION_FIELDS = ("bookId", "isbn13", "asin", "isEbook")
# How many books one request for editions asks about (the ids go into the address).
EDITION_CHUNK = 100
# What is taken from the list of authors.
AUTHOR_FIELDS = ("id", "authorName", "foreignAuthorId", "added")
# The fields read from the queue for the live card (from the record and from the embedded book and
# author). The app's messages and paths are left out on purpose.
QUEUE_FIELDS = ("records", "totalRecords", "id", "title", "author", "book", "authorName", "authorId", "bookId",
                "downloadId", "downloadClient", "protocol", "size", "sizeleft", "status", "trackedDownloadState",
                "trackedDownloadStatus", "timeleft", "estimatedCompletionTime")
QUEUE_PAGE_SIZE = 200
# The timeout of the command to refresh downloads. It is only a nudge: the app answers at once (it
# queues it), and when it does not answer at all, reading the queue must not wait on it as long as on
# the app itself.
COMMAND_TIMEOUT = httpx.Timeout(3.0)


class ReadarrError(ServiceError):
    """Anything that goes wrong while talking to Readarr (see services.ServiceError)."""


class ReadarrClient(HttpClient):
    """The connection to Readarr. Used as a context manager:

        async with ReadarrClient("http://nas:8787", key) as client:
            history = await client.history_since("2026-11-01T00:00:00Z")

    `url` may have a path behind a reverse proxy (`http://nas/readarr`) - the API is then at
    `http://nas/readarr/api/v1/...`. `transport` is for tests (httpx.MockTransport), otherwise not
    passed.
    """

    error_class = ReadarrError          # the error class the shared core raises (HttpClient reads `error_class`)

    def __init__(self, url: str, key: str,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None) -> None:
        super().__init__(NAME, url, "/api/v1/", key, transport, timeout)

    # -----------------------------------------------------------------
    # Requests
    # -----------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """The app's name and version (`appName`, `version`) - the connection test."""
        return await self._object("system/status")

    async def history_since(self, date: str) -> list[dict[str, Any]]:
        """The whole history since `date` (ISO 8601, UTC) without paging, with the book and the author.

        A longer read limit: the first sync fetches the whole history, and with a big library it has
        tens of thousands of records.
        """
        return await self._get("history/since", {"date": date, **INCLUDE}, timeout=DEFAULT_TIMEOUT) or []

    async def history_page(self, page: int, size: int) -> dict[str, Any]:
        """One page of the whole history, oldest first (the first sync).

        The whole history in one answer is hundreds of MB with a big library - every record carries
        the whole book and author. Page by page only one is in memory.
        """
        params = {"page": page, "pageSize": size, "sortKey": "date", "sortDirection": "ascending", **INCLUDE}
        return await self._get("history", params, timeout=DEFAULT_TIMEOUT) or {}

    async def titles(self) -> list[dict[str, Any]]:
        """Every book (`book`) as Readarr knows it, with its editions (their ISBN-13 and ASIN).

        By them (or by the titles) a book from the history is found in the library. Readarr can neither
        page nor shorten the list: it comes whole, every book in full. Hence `_list`: it parses it in a
        thread and keeps only `BOOK_FIELDS`.
        """
        return await self._list("book", BOOK_FIELDS, timeout=DEFAULT_TIMEOUT)

    async def editions(self, book_ids: list[int]) -> list[dict[str, Any]]:
        """The editions of the given books (`edition?bookId=…`), asked for in chunks of `EDITION_CHUNK`.

        The list of books carries only the monitored edition of a book; a book is found in the library
        by an ISBN of any of its editions, so they are asked for one by one.
        """
        found: list[dict[str, Any]] = []
        for start in range(0, len(book_ids), EDITION_CHUNK):
            found += await self._list("edition", EDITION_FIELDS, timeout=DEFAULT_TIMEOUT,
                                      params={"bookId": book_ids[start:start + EDITION_CHUNK]})
        return found

    async def authors(self) -> list[dict[str, Any]]:
        """Every author (`author`): the name, to name the books."""
        return await self._list("author", AUTHOR_FIELDS, timeout=DEFAULT_TIMEOUT)

    async def queue(self) -> dict[str, Any]:
        """The download queue (the first page, up to 100 items), with the book and the author.

        With the downloads the app did not pair with any author too (`includeUnknownAuthorItems`), like
        `whole_queue`: such an item waits for a manual import, and the count and the card of items with an
        error are to show it.
        """
        return await self._object("queue", {"pageSize": 100, "includeUnknownAuthorItems": "true", **INCLUDE})

    async def whole_queue(self) -> dict[str, Any]:
        """The whole queue in one page for the live card: `{"records": [...], "total": n}`.

        With the book and the author, so the names and ids are at hand at once - for a book added a
        minute ago too. Only QUEUE_FIELDS are read from every object: the app's messages and paths on
        the disk (`statusMessages`, `errorMessage`, `outputPath`) are never loaded, the card does not
        show them. `total` may be more than the records - a queue over QUEUE_PAGE_SIZE is not read to
        the end, the card only says there are more.

        With the downloads Readarr did not pair with any author too (`includeUnknownAuthorItems`):
        without the flag it leaves them out of the queue, the client would download and the card
        would claim nothing is happening. The card shows them under the release's name.
        """
        data = await self._json_with_list("queue", QUEUE_FIELDS, {"page": 1, "pageSize": QUEUE_PAGE_SIZE,
                                                                  "includeUnknownAuthorItems": "true", **INCLUDE})
        data = data if isinstance(data, dict) else {}
        records = data.get("records")
        records = [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []
        return {"records": records, "total": max(integer(data.get("totalRecords")) or 0, len(records))}

    async def refresh_downloads(self) -> None:
        """Tell Readarr to refresh the state of downloads at the clients now (like the Refresh button in the queue).

        By itself it does so only once a minute; it merges concurrent commands and handles this one in a
        moment. Its end is not waited for. It is the only thing Jellyscope sends to Readarr - it cancels,
        pauses and changes nothing.
        """
        await self._post("command", {"name": "RefreshMonitoredDownloads"}, timeout=COMMAND_TIMEOUT)

    async def record_count(self, path: str, timeout: httpx.Timeout | None = None) -> int:
        """The count of records of a paged list (`wanted/missing`, `wanted/cutoff`) - only one item is
        fetched, `totalRecords` is enough."""
        answer = await self._object(path, {"pageSize": 1}, timeout)
        return integer(answer.get("totalRecords")) or 0

    async def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Which books come out between `start` and `end` (ISO 8601), with the author."""
        return await self._get("calendar", {"start": start, "end": end, "includeAuthor": "true"}) or []

    async def health(self) -> list[dict[str, Any]]:
        """The app's health checks (warnings and errors from its System)."""
        return await self._get("health") or []

    async def cover(self, book_id: int) -> bytes | None:
        """The book's cover as Readarr keeps it. None = it has none.

        Readarr downloads covers to itself when a book is added, so it is an image from its disk - no
        request goes outside because of it.
        """
        return await self._image(f"mediacover/book/{int(book_id)}/cover.jpg")
