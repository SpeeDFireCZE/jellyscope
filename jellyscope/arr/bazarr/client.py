"""Bazarr's API client (subtitles for Sonarr and Radarr).

Bazarr has its own API under `<address>/api/` - other paths and answers than Sonarr and Radarr, hence
a client of its own. The shared core (the key only in a header, errors without the key in their
text, redirects not followed) is in `services.HttpClient`. Bazarr's key header is called `X-API-KEY`;
HTTP header names ignore case, so the core's `X-Api-Key` is the same one.

Jellyscope only reads from Bazarr (GET). From the answers it takes only the listed fields
(`HISTORY_FIELDS`, `MISSING_FIELDS`): paths to subtitles on the disk, descriptions and lists of
matches are never loaded.
"""
from __future__ import annotations

from typing import Any

import httpx

from ...jellyfin import DEFAULT_TIMEOUT
from ...services import UNEXPECTED, ServiceError, HttpClient

NAME = "Bazarr"
# What happened to the subtitles (`action` in Bazarr's history) - keys for the statistics and cards.
ACTIONS = {0: "deleted", 1: "grabbed", 2: "manual", 3: "upgrade", 4: "uploaded", 5: "synced",
           6: "translated", 7: "extracted"}
# Actions by which subtitles came to be or were replaced - "acquired subtitles". Syncing the timing
# and deleting are not among them.
ACQUIRED = (1, 2, 3, 4, 6, 7)
# The fields read from the history (from the record and from the embedded language).
HISTORY_FIELDS = ("data", "total", "action", "seriesTitle", "title", "episode_number", "sonarrSeriesId", "sonarrEpisodeId",
                  "radarrId", "language", "name", "code2", "code3", "hi", "forced", "provider", "score", "subs_id",
                  "parsed_timestamp")
# From the list of missing subtitles the languages are enough; names and paths are not read.
MISSING_FIELDS = ("data", "total", "missing_subtitles", "name", "code2", "code3", "hi", "forced")
HISTORY_PATHS = {"episode": "episodes/history", "movie": "movies/history"}
MISSING_PATHS = {"episode": "episodes/wanted", "movie": "movies/wanted"}


class BazarrError(ServiceError):
    """Anything that goes wrong while talking to Bazarr (see services.ServiceError)."""


class BazarrClient(HttpClient):
    """The connection to Bazarr. `url` may have a path behind a reverse proxy too
    (`http://nas/bazarr` -> `http://nas/bazarr/api/...`)."""

    error_class = BazarrError

    def __init__(self, url: str, key: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None) -> None:
        super().__init__(NAME, url, "/api/", key, transport, timeout)

    async def _data(self, path: str, kind: type) -> Any:
        """The content of the answer's `data` key; a shape other than expected is an error."""
        data = (await self._object(path)).get("data")
        if not isinstance(data, kind):
            raise self.error_class(UNEXPECTED, name=self.name, path=path)
        return data

    async def status(self) -> dict[str, Any]:
        """Bazarr's status (`system/status`): `bazarr_version`, `timezone`, the versions of Sonarr and Radarr."""
        return await self._data("system/status", dict)

    async def _page(self, path: str, fields: tuple[str, ...], start: int, length: int,
                    timeout: httpx.Timeout | None = None) -> dict[str, Any]:
        """A page of a paged list: `{"data": [...], "total": n}` with the listed fields only.

        `total` is the count of all records, or None when the answer does not carry it (or it makes no
        sense): whoever pages then reads as long as pages come.
        """
        answer = await self._json_with_list(path, fields, {"start": start, "length": length}, timeout=timeout)
        data = answer.get("data") if isinstance(answer, dict) else None
        if not isinstance(data, list):
            raise self.error_class(UNEXPECTED, name=self.name, path=path)
        records = [r for r in data if isinstance(r, dict)]
        total = answer.get("total")
        known = isinstance(total, int) and not isinstance(total, bool) and total >= len(records)
        return {"data": records, "total": total if known else None}

    async def history(self, kind: str, start: int, length: int) -> dict[str, Any]:
        """A page of the subtitle history, newest first. `kind` is "episode" or "movie".

        Bazarr cannot do "only newer than..." and its records have no number; `total` is the count of
        all records. The event's time is only in `parsed_timestamp` (text, see bazarr/times.py).
        """
        return await self._page(HISTORY_PATHS[kind], HISTORY_FIELDS, start, length, timeout=DEFAULT_TIMEOUT)

    async def missing(self, kind: str, length: int) -> dict[str, Any]:
        """The first `length` episodes or movies without subtitles - only the languages they lack - and `total`.

        Read for the live card a page waits for: with the client's short timeout, not the long one of
        the history during a sync.
        """
        return await self._page(MISSING_PATHS[kind], MISSING_FIELDS, 0, length)

    async def badges(self) -> dict[str, Any]:
        """The counts for Bazarr's header: `episodes` and `movies` (subtitles missing), `providers` (throttled), `status` (warnings)."""
        return await self._object("badges")

    async def providers(self) -> list[dict[str, Any]]:
        """The enabled subtitle providers: `name`, `status` ("Good" or the error's name), `retry` (when Bazarr tries again)."""
        return [p for p in await self._data("providers", list) if isinstance(p, dict)]

    async def health(self) -> list[dict[str, Any]]:
        """Bazarr's warnings: `object` (what it concerns, often a path) and `issue` (what is wrong)."""
        return [w for w in await self._data("system/health", list) if isinstance(w, dict)]
