"""Sonarr's API v3 client.

The API is at `<address>/api/v3/...` and the key goes in the `X-Api-Key` header. The connection,
errors and the key's protection are in the shared core (`jellyscope/services.py`) - only Sonarr's
requests are here. `SonarrError` carries a Czech template and the values apart, so it can be
translated into the language of whoever reads it, and the key is never in it.

    async with SonarrClient(url, key) as client:
        state = await client.status()
"""
from __future__ import annotations

from typing import Any

import httpx

from ...jellyfin import DEFAULT_TIMEOUT, digits_only
from ...services import ServiceError, HttpClient, integer

# What the app calls itself (`appName` in /api/v3/system/status). By it the connection test tells
# that Radarr answers at Sonarr's address.
NAME = "Sonarr"
# The prefix of the keys in the settings table: arr_sonarr_url, arr_sonarr_api_key, …
SETTINGS_PREFIX = "arr_sonarr"

# What to embed into a record of the history and the queue: the series and the episode, so the names
# need no separate look-up.
INCLUDE = {"includeSeries": "true", "includeEpisode": "true"}
# What is taken from the list of series (see SonarrClient.titles). The rest of a record (cast,
# images, seasons, files) is dropped already while the answer is parsed.
TITLE_FIELDS = ("id", "title", "tmdbId", "tvdbId", "imdbId", "added")
# The fields read from the queue for the live card (from the record and from the embedded series and
# episode). The app's messages and paths are left out on purpose.
QUEUE_FIELDS = ("records", "totalRecords", "id", "title", "series", "episode", "seriesId", "episodeId",
                "seasonNumber", "episodeNumber", "tmdbId", "tvdbId", "downloadId", "downloadClient", "protocol",
                "size", "sizeleft", "status", "trackedDownloadState", "trackedDownloadStatus", "timeleft",
                "estimatedCompletionTime")
QUEUE_PAGE_SIZE = 200
# The timeout of the command to refresh downloads. It is only a nudge: the app answers at once (it
# queues it), and when it does not answer at all, reading the queue must not wait on it as long as on
# the app itself.
COMMAND_TIMEOUT = httpx.Timeout(3.0)


class SonarrError(ServiceError):
    """Anything that goes wrong while talking to Sonarr (see services.ServiceError)."""


class SonarrClient(HttpClient):
    """The connection to Sonarr. Used as a context manager:

        async with SonarrClient("http://nas:8989", key) as client:
            history = await client.history_since("2026-11-01T00:00:00Z")

    `url` may have a path behind a reverse proxy (`http://nas/sonarr`) - the API is then at
    `http://nas/sonarr/api/v3/...`. `transport` is for tests (httpx.MockTransport), otherwise not
    passed.
    """

    error_class = SonarrError          # the error class the shared core raises (HttpClient reads `error_class`)

    def __init__(self, url: str, key: str,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None) -> None:
        super().__init__(NAME, url, "/api/v3/", key, transport, timeout)

    # -----------------------------------------------------------------
    # Requests
    # -----------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """The app's name and version (`appName`, `version`) - the connection test."""
        return await self._object("system/status")

    async def history_since(self, date: str) -> list[dict[str, Any]]:
        """The whole history since `date` (ISO 8601, UTC) without paging, with the series and the episode.

        A longer read limit: the first sync fetches the whole history, and with a big library it has
        tens of thousands of records.
        """
        return await self._get("history/since", {"date": date, **INCLUDE}, timeout=DEFAULT_TIMEOUT) or []

    async def history_page(self, page: int, size: int) -> dict[str, Any]:
        """One page of the whole history, oldest first (the first sync).

        The whole history in one answer (`history/since` from the year 2000) is hundreds of MB with a
        big library - every record carries the whole series. Page by page only one is in memory.
        """
        params = {"page": page, "pageSize": size, "sortKey": "date", "sortDirection": "ascending", **INCLUDE}
        return await self._get("history", params, timeout=DEFAULT_TIMEOUT) or {}

    async def queue(self) -> dict[str, Any]:
        """The download queue (the first page, up to 100 items)."""
        return await self._object("queue", {"pageSize": 100, **INCLUDE})

    async def whole_queue(self) -> dict[str, Any]:
        """The whole queue in one page for the live card "Stahuje se": `{"records": [...], "total": n}`.

        With the series and the episode, so the series' name and id are at hand at once - for a series
        added a minute ago too. Only QUEUE_FIELDS are read from every object: the app's messages and
        paths on the disk (`statusMessages`, `errorMessage`, `outputPath`) are never loaded, the card
        does not show them. `total` may be more than the records - a queue over QUEUE_PAGE_SIZE is
        not read to the end, the card only says there are more.

        With the downloads Sonarr did not pair with any series too (`includeUnknownSeriesItems`):
        without the flag it leaves them out of the queue, the client would download and the card would
        claim nothing is happening. The card shows them under the release's name.
        """
        data = await self._json_with_list("queue", QUEUE_FIELDS, {"page": 1, "pageSize": QUEUE_PAGE_SIZE,
                                                                  "includeUnknownSeriesItems": "true", **INCLUDE})
        data = data if isinstance(data, dict) else {}
        records = data.get("records")
        records = [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []
        return {"records": records, "total": max(integer(data.get("totalRecords")) or 0, len(records))}

    async def refresh_downloads(self) -> None:
        """Tell Sonarr to refresh the state of downloads at the clients now (like the Refresh button in the queue).

        By itself it does so only once a minute; it merges concurrent commands and handles this one in a
        moment. Its end is not waited for. It is the only thing Jellyscope sends to Sonarr - it cancels,
        pauses and changes nothing.
        """
        await self._post("command", {"name": "RefreshMonitoredDownloads"}, timeout=COMMAND_TIMEOUT)

    async def record_count(self, path: str) -> int:
        """The count of records of a paged list (`wanted/missing`, `wanted/cutoff`) - only one item is
        fetched, `totalRecords` is enough."""
        answer = await self._object(path, {"pageSize": 1})
        return integer(answer.get("totalRecords")) or 0

    async def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Which episodes air between `start` and `end` (ISO 8601), with the series."""
        return await self._get("calendar", {"start": start, "end": end, "includeSeries": "true"}) or []

    async def health(self) -> list[dict[str, Any]]:
        """The app's health checks (warnings and errors from its System)."""
        return await self._get("health") or []

    async def titles(self) -> list[dict[str, Any]]:
        """Every series (`series`) as Sonarr knows it.

        By their ids (TVDB, TMDB, IMDb) a series from the history is found in the library. Sonarr can
        neither page nor shorten the list: it comes whole, with every series in full. Hence `_list`:
        it parses it in a thread and keeps only `TITLE_FIELDS` of every series.
        """
        return await self._list("series", TITLE_FIELDS, timeout=DEFAULT_TIMEOUT)

    # -----------------------------------------------------------------
    # Posters (for Seerr's requests whose series is not in Jellyfin yet)
    # -----------------------------------------------------------------

    async def title_id(self, tvdb_id: str) -> int | None:
        """The series' id in Sonarr by its TVDB id. None = Sonarr does not have it.

        Sonarr filters `series?tvdbId=`. Only a record that really has the id looked for is taken:
        older versions do not know the parameter and send the whole list - the first record would then
        be another series with another poster.
        """
        wanted = str(tvdb_id)
        if not digits_only(wanted):
            return None
        answer = await self._get("series", {"tvdbId": wanted})
        for record in answer if isinstance(answer, list) else []:
            if isinstance(record, dict) and str(record.get("tvdbId")) == wanted:
                return integer(record.get("id"), None)
        return None

    async def poster(self, series_id: int) -> bytes | None:
        """The series' poster as Sonarr keeps it (250 px wide). None = it has none.

        Sonarr downloads posters to itself when a series is added, so it is an image from its disk -
        no request goes outside because of it.
        """
        return await self._image(f"mediacover/{int(series_id)}/poster-250.jpg")
