"""The Seerr API v1 client (the merged Overseerr and Jellyseerr; both have the same API).

The connection, errors and the key's protection are in the shared core (`jellyscope/services.py`, the
same for Sonarr and Radarr) - only Seerr's requests are here.

    async with SeerrClient(url, key) as client:
        about = await client.about()
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..jellyfin import digits_only
from ..services import NOT_AN_API, ServiceError, HttpClient

NAME = "Seerr"

# The shape of a poster path as TMDB sends it (and Seerr passes it on): a slash, a name of letters and
# digits, an image extension. The path is put into the address of the image on Seerr - nothing else
# (another slash, dots, a question mark, a whole address) may get into it, even if Seerr's answer or
# the database said so.
POSTER_PATH_PATTERN = re.compile(r"/[A-Za-z0-9]+\.(?:jpg|jpeg|png|webp)")
# The poster width Jellyscope asks for (TMDB decides the sizes).
POSTER_WIDTH = "w342"


def _tmdb_id(value: Any) -> str:
    """A TMDB id as a text of the digits 0-9, else empty.

    It goes into the path of the request for a name (`movie/{id}`) and is compared with
    `items.tmdb_id`. Seerr's answer is input like any other: what is not a number is not used.
    """
    text = str(value if value is not None else "").strip()
    return text if digits_only(text) else ""


class SeerrError(ServiceError):
    """Anything that goes wrong while talking to Seerr (see services.ServiceError)."""


class SeerrClient(HttpClient):
    """The connection to Seerr. `url` may have a path behind a reverse proxy
    (`http://nas/seerr` -> `http://nas/seerr/api/v1/...`)."""

    error_class = SeerrError          # the error class the shared core raises (HttpClient reads `error_class`)

    def __init__(self, url: str, key: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None) -> None:
        super().__init__(NAME, url, "/api/v1/", key, transport, timeout)

    async def about(self) -> dict[str, Any]:
        """The version and a summary (`version`, `totalRequests`) - the connection test."""
        return await self._object("settings/about")

    async def public_settings(self) -> dict[str, Any]:
        """Seerr's public settings (`settings/public`) - what Seerr tells even a signed-out visitor.

        Jellyscope reads `cacheImages` from it ("Enable Image Caching": does Seerr serve images?)
        and `applicationUrl` (the address people open Seerr at).
        """
        return await self._object("settings/public")

    async def requests_page(self, skip: int, take: int, filter_: str = "all",
                            timeout: httpx.Timeout | None = None) -> dict[str, Any]:
        """One page of requests: `{"pageInfo": {...}, "results": [...]}`.

        The order asked for is oldest first (`sortDirection=asc`); Overseerr ignores it and sends the
        newest first - whoever cares about the order sorts by themselves. `timeout`: the sync gives a
        long one (a page of a hundred requests with titles), the live card keeps the short default -
        a slow Seerr must not hold a page up for minutes.
        """
        params = {"take": take, "skip": skip, "filter": filter_, "sort": "added", "sortDirection": "asc"}
        return await self._object("request", params, timeout=timeout)

    async def request(self, request_id: int) -> dict[str, Any] | None:
        """One request by its id. None = Seerr does not have it (404, deleted)."""
        try:
            return await self._object(f"request/{int(request_id)}")
        except SeerrError as error:
            if error.template == NOT_AN_API:
                return None
            raise

    async def title_info(self, media_type: str, tmdb_id: str) -> dict[str, str]:
        """The name and poster path of a title: `{"name": ..., "poster": ...}`.

        A movie is `movie/{id}` (`title`), a series `tv/{id}` (`name`); the poster path is
        `posterPath` in both. The request itself carries neither, Seerr takes them from TMDB. An
        empty name = not found; an empty poster = Seerr has none, or the path does not have the shape
        of a poster path (POSTER_PATH_PATTERN).
        """
        answer = await self._object(f"{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}")
        poster = str(answer.get("posterPath") or "")
        return {"name": str(answer.get("name" if media_type == "tv" else "title") or ""),
                "poster": poster if POSTER_PATH_PATTERN.fullmatch(poster) else ""}

    async def tmdb_poster(self, path: str) -> bytes | None:
        """A poster by its TMDB path, fetched through Seerr. None = Seerr does not serve it.

        Seerr passes TMDB images on at `/imageproxy/tmdb/…` - but only with "Enable Image Caching"
        on (Settings → General); otherwise it answers 404. So Jellyscope never connects to TMDB
        itself. A path without the shape of a poster path is not used at all.
        """
        if not POSTER_PATH_PATTERN.fullmatch(path or ""):
            return None
        return await self._image(f"{self._root}/imageproxy/tmdb/t/p/{POSTER_WIDTH}{path}")
