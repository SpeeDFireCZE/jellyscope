"""Prowlarr's API client (indexers for Sonarr, Radarr and other apps).

Prowlarr belongs to the Servarr family: the API under `<address>/api/v1/`, the key in the header
`X-Api-Key`. The shared core (the key only in a header, errors without the key in their text,
redirects not followed) is in `services.HttpClient`.

Jellyscope only reads from Prowlarr (GET) and takes only the listed fields from the answers. Here it
is not just a saving: the history holds **the addresses of the queries to indexers with their keys**
(`url`, `infoUrl`) and the lists of indexers and apps their settings (`fields`, with keys too). None of
it is loaded - it is dropped already while the answer is parsed, so it cannot get into the database,
the log or a page.
"""
from __future__ import annotations

from typing import Any

import httpx

from ...jellyfin import DEFAULT_TIMEOUT
from ...services import UNEXPECTED, ServiceError, HttpClient

NAME = "Prowlarr"
# The kind of a history event (`eventType`) -> what Jellyscope calls it. What is not here is skipped.
KINDS = {"indexerQuery": "query", "indexerRss": "rss", "indexerAuth": "auth", "releaseGrabbed": "grabbed"}
# The fields read from the history - from the page, from the record and from its `data`.
HISTORY_FIELDS = ("totalRecords", "records", "id", "indexerId", "date", "successful", "eventType", "data",
                  "elapsedTime", "queryResults", "cached", "source", "grabTitle", "grabMethod", "downloadClientName")
INDEXER_FIELDS = ("id", "name", "protocol", "privacy", "enable")
APP_FIELDS = ("id", "name", "implementation", "syncLevel")
BLOCK_FIELDS = ("indexerId", "disabledTill", "mostRecentFailure", "initialFailure")
HEALTH_FIELDS = ("type", "source", "message")


class ProwlarrError(ServiceError):
    """Anything that goes wrong while talking to Prowlarr (see services.ServiceError)."""


class ProwlarrClient(HttpClient):
    """The connection to Prowlarr. `url` may have a path behind a reverse proxy too
    (`http://nas/prowlarr` -> `http://nas/prowlarr/api/v1/...`)."""

    error_class = ProwlarrError

    def __init__(self, url: str, key: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None) -> None:
        super().__init__(NAME, url, "/api/v1/", key, transport, timeout)

    async def status(self) -> dict[str, Any]:
        """The app's name and version (`appName`, `version`) - the connection test."""
        return await self._object("system/status")

    async def history(self, page: int, size: int) -> dict[str, Any]:
        """One page of the history from the newest event (pages from 1): `{"records": [...], "total": n}`.

        Sorted by date - Prowlarr cannot sort by the event's number, but the numbers follow the time.
        `total` is the count of all events, or None when the answer does not carry it; a page past the
        end is empty. A longer read limit: the first sync goes through the whole history.
        """
        answer = await self._json_with_list("history", HISTORY_FIELDS, {"page": page, "pageSize": size, "sortKey": "date",
                                                                    "sortDirection": "descending"}, timeout=DEFAULT_TIMEOUT)
        records = answer.get("records") if isinstance(answer, dict) else None
        if not isinstance(records, list):
            raise self.error_class(UNEXPECTED, name=self.name, path="history")
        total = answer.get("totalRecords")
        return {"records": [r for r in records if isinstance(r, dict)],
                "total": total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None}

    async def indexers(self) -> list[dict[str, Any]]:
        """The indexers: `id`, `name`, `protocol` (usenet / torrent), `privacy`, `enable`. Settings with keys are not read.

        An answer that is not a list (a proxy's page, an error object with status 200) is an error - not
        "no indexers": the sync would mark every stored one as removed by it.
        """
        answer = await self._json_with_list("indexer", INDEXER_FIELDS)
        if not isinstance(answer, list):
            raise self.error_class(UNEXPECTED, name=self.name, path="indexer")
        return [record for record in answer if isinstance(record, dict)]

    async def blocked(self) -> list[dict[str, Any]]:
        """The indexers Prowlarr switched off for a while because of failures: `indexerId`, `disabledTill`,
        `mostRecentFailure`, `initialFailure`. An indexer without failures is not here."""
        return await self._list("indexerstatus", BLOCK_FIELDS)

    async def applications(self) -> list[dict[str, Any]]:
        """The apps whose indexers Prowlarr manages: `name`, `implementation`, `syncLevel`. Without keys."""
        return await self._list("applications", APP_FIELDS)

    async def health(self) -> list[dict[str, Any]]:
        """Prowlarr's warnings: `type` (warning / error), `source`, `message`."""
        return await self._list("health", HEALTH_FIELDS)
