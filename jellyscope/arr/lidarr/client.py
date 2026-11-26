"""Lidarr's API v1 client.

The API is at `<address>/api/v1/...` and the key goes in the `X-Api-Key` header. The connection,
errors and the key's protection are in the shared core (`jellyscope/services.py`) - only Lidarr's
requests are here. `LidarrError` carries a template and the values apart, so it can be translated
into the language of whoever reads it, and the key is never in it.

    async with LidarrClient(url, key) as client:
        state = await client.status()
"""
from __future__ import annotations

from typing import Any

import httpx

from ...jellyfin import DEFAULT_TIMEOUT
from ...services import ServiceError, HttpClient, integer

# What the app calls itself (`appName` in /api/v1/system/status). By it the connection test tells
# that another app with the same API answers at Lidarr's address.
NAME = "Lidarr"
# The prefix of the keys in the settings table: arr_lidarr_url, arr_lidarr_api_key, …
SETTINGS_PREFIX = "arr_lidarr"

# What to embed into a record of the history: the album and the artist, so the names need no
# separate look-up.
INCLUDE = {"includeAlbum": "true", "includeArtist": "true"}
# What is taken from the list of albums (see LidarrClient.titles). The filter works at every level
# of the answer, so the nested objects (the artist, the releases, the statistics) are listed by the
# fields they are read for; the rest (images, media, links, genres) is dropped already while the
# answer is parsed.
ALBUM_FIELDS = ("id", "title", "artistId", "foreignAlbumId", "releaseDate", "releases", "foreignReleaseId",
                "statistics", "trackCount", "artist", "artistName", "foreignArtistId", "added")
# What is taken from the list of artists.
ARTIST_FIELDS = ("id", "artistName", "foreignArtistId", "added")
# The fields read from the queue for the live card (from the record and from the embedded album and
# artist). The app's messages and paths are left out on purpose.
QUEUE_FIELDS = ("records", "totalRecords", "id", "title", "artist", "album", "artistName", "artistId", "albumId",
                "downloadId", "downloadClient", "protocol", "size", "sizeleft", "status", "trackedDownloadState",
                "trackedDownloadStatus", "timeleft", "estimatedCompletionTime", "trackFileCount", "trackHasFileCount")
QUEUE_PAGE_SIZE = 200
# The timeout of the command to refresh downloads. It is only a nudge: the app answers at once (it
# queues it), and when it does not answer at all, reading the queue must not wait on it as long as on
# the app itself.
COMMAND_TIMEOUT = httpx.Timeout(3.0)


class LidarrError(ServiceError):
    """Anything that goes wrong while talking to Lidarr (see services.ServiceError)."""


class LidarrClient(HttpClient):
    """The connection to Lidarr. Used as a context manager:

        async with LidarrClient("http://nas:8686", key) as client:
            history = await client.history_since("2026-11-01T00:00:00Z")

    `url` may have a path behind a reverse proxy (`http://nas/lidarr`) - the API is then at
    `http://nas/lidarr/api/v1/...`. `transport` is for tests (httpx.MockTransport), otherwise not
    passed.
    """

    error_class = LidarrError          # the error class the shared core raises (HttpClient reads `error_class`)

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
        """The whole history since `date` (ISO 8601, UTC) without paging, with the album and the artist.

        A longer read limit: the first sync fetches the whole history, and with a big library it has
        tens of thousands of records.
        """
        return await self._get("history/since", {"date": date, **INCLUDE}, timeout=DEFAULT_TIMEOUT) or []

    async def history_page(self, page: int, size: int) -> dict[str, Any]:
        """One page of the whole history, oldest first (the first sync).

        The whole history in one answer is hundreds of MB with a big library - every record carries
        the whole album and artist. Page by page only one is in memory.
        """
        params = {"page": page, "pageSize": size, "sortKey": "date", "sortDirection": "ascending", **INCLUDE}
        return await self._get("history", params, timeout=DEFAULT_TIMEOUT) or {}

    async def titles(self) -> list[dict[str, Any]]:
        """Every album (`album`) as Lidarr knows it, with the MusicBrainz ids of its releases.

        By them (or by the names) an album from the history is found in the library. Lidarr can
        neither page nor shorten the list: it comes whole, every album in full. Hence `_list`: it
        parses it in a thread and keeps only `ALBUM_FIELDS`.
        """
        return await self._list("album", ALBUM_FIELDS, timeout=DEFAULT_TIMEOUT)

    async def artists(self) -> list[dict[str, Any]]:
        """Every artist (`artist`): the name and the MusicBrainz id, to name the albums."""
        return await self._list("artist", ARTIST_FIELDS, timeout=DEFAULT_TIMEOUT)

    async def queue(self) -> dict[str, Any]:
        """The download queue (the first page, up to 100 items), with the album and the artist.

        With the downloads the app did not pair with any artist too (`includeUnknownArtistItems`), like
        `whole_queue`: such an item waits for a manual import, and the count and the card of items with an
        error are to show it.
        """
        return await self._object("queue", {"pageSize": 100, "includeUnknownArtistItems": "true", **INCLUDE})

    async def whole_queue(self) -> dict[str, Any]:
        """The whole queue in one page for the live card: `{"records": [...], "total": n}`.

        With the album and the artist, so the names and ids are at hand at once - for an album added a
        minute ago too. Only QUEUE_FIELDS are read from every object: the app's messages and paths on
        the disk (`statusMessages`, `errorMessage`, `outputPath`) are never loaded, the card does not
        show them. `total` may be more than the records - a queue over QUEUE_PAGE_SIZE is not read to
        the end, the card only says there are more.

        With the downloads Lidarr did not pair with any artist too (`includeUnknownArtistItems`):
        without the flag it leaves them out of the queue, the client would download and the card
        would claim nothing is happening. The card shows them under the release's name.
        """
        data = await self._json_with_list("queue", QUEUE_FIELDS, {"page": 1, "pageSize": QUEUE_PAGE_SIZE,
                                                                  "includeUnknownArtistItems": "true", **INCLUDE})
        data = data if isinstance(data, dict) else {}
        records = data.get("records")
        records = [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []
        return {"records": records, "total": max(integer(data.get("totalRecords")) or 0, len(records))}

    async def refresh_downloads(self) -> None:
        """Tell Lidarr to refresh the state of downloads at the clients now (like the Refresh button in the queue).

        By itself it does so only once a minute; it merges concurrent commands and handles this one in a
        moment. Its end is not waited for. It is the only thing Jellyscope sends to Lidarr - it cancels,
        pauses and changes nothing.
        """
        await self._post("command", {"name": "RefreshMonitoredDownloads"}, timeout=COMMAND_TIMEOUT)

    async def record_count(self, path: str, timeout: httpx.Timeout | None = None) -> int:
        """The count of records of a paged list (`wanted/missing`, `wanted/cutoff`) - only one item is
        fetched, `totalRecords` is enough."""
        answer = await self._object(path, {"pageSize": 1}, timeout)
        return integer(answer.get("totalRecords")) or 0

    async def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Which albums come out between `start` and `end` (ISO 8601), with the artist."""
        return await self._get("calendar", {"start": start, "end": end, "includeArtist": "true"}) or []

    async def health(self) -> list[dict[str, Any]]:
        """The app's health checks (warnings and errors from its System)."""
        return await self._get("health") or []

    async def cover(self, album_id: int) -> bytes | None:
        """The album's cover as Lidarr keeps it. None = it has none.

        Lidarr downloads covers to itself when an album is added, so it is an image from its disk -
        no request goes outside because of it.
        """
        return await self._image(f"mediacover/album/{int(album_id)}/cover.jpg")
