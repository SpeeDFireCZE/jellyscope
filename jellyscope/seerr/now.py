"""Seerr right now: the two live cards "Waiting for approval" and "Latest requests", loaded together.

Both lists - the pending requests (live.py) and the page of the newest ones (latest.py) - load at once
(asyncio.gather) through one client, every request waiting at most LIVE_TIMEOUT (3 s, like the live
state of the *Arr apps): the page waits for it, and a Seerr that accepts the connection and does not
answer must not hold it up. They are kept under ONE entry of the one-minute cache (`live.CACHE`), and
the names of titles the table does not know yet are looked up once for both (`live._add_titles`). So a
hanging Seerr costs a render one wait, not two, and the two cards always show the same moment: a
request approved meanwhile cannot read "waiting" in one card and "approved" in the other (a dashboard
holding only one of them, then the Seerr page). A list that fails keeps its own error; the other card
still shows.

The cache notices a change of the address and of the key (the key only as a fingerprint): fixed
settings load at once. A failure is kept too, so a Seerr that does not answer is asked once a minute,
not on every view. The error is translated on every read: the cache is shared and everyone reads it in
their own language.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable
from typing import Any

import httpx

from .. import db
from ..services import LIVE_TIMEOUT
from . import latest, live
from .client import SeerrClient, SeerrError

# A live card's state and its error, as kept in the cache.
Part = tuple[dict[str, Any], SeerrError | None]


async def _part(work: Awaitable[dict[str, Any]], empty: dict[str, Any]) -> Part:
    """One live card's state and its error: a list Seerr did not give leaves this card empty, not the other one."""
    try:
        return await work, None
    except SeerrError as error:
        return empty, error


async def _load(url: str, key: str, transport: httpx.AsyncBaseTransport | None) -> dict[str, Part]:
    """Both live cards at once: {"pending": (state, error), "latest": (state, error)}."""
    try:
        client = SeerrClient(url, key, transport=transport, timeout=LIVE_TIMEOUT)
    except SeerrError as error:                   # an invalid stored address or key
        return {"pending": (live._empty(), error), "latest": (latest.empty(), error)}
    async with client:
        pending_part, latest_part = await asyncio.gather(_part(live._read_pending(client), live._empty()),
                                                         _part(latest.read_page(client), latest.empty()))
        rows = [*pending_part[0]["requests"], *latest_part[0]["requests"]]
        await live._add_titles(client, rows)
    for row in rows:
        row["title"] = row["title"] or f"TMDB {row['tmdb_id'] or '?'}"
    return {"pending": pending_part, "latest": latest_part}


async def _cached(transport: httpx.AsyncBaseTransport | None) -> dict[str, Part]:
    """Both live cards' states through the one-minute cache `live.CACHE[live.CACHE_NAME]`."""
    url, key = db.get_setting("seerr_url", ""), db.get_setting("seerr_api_key", "")
    fingerprint = url + "|" + hashlib.sha256(key.encode()).hexdigest()
    cached = live.CACHE.get(live.CACHE_NAME)
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < live.TTL:
        return cached[2]
    parts = await _load(url, key, transport)
    live.CACHE[live.CACHE_NAME] = (time.monotonic(), fingerprint, parts)
    return parts


def _translated(part: Part) -> dict[str, Any]:
    state, error = part
    return {**state, "error": error.translated() if error else ""}


async def pending(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The pending requests now (from the cache shared with "Latest requests", if it is younger than a minute)."""
    return _translated((await _cached(transport))["pending"])


async def latest_requests(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Seerr's newest requests now (from the cache shared with "Waiting for approval", if it is younger than a minute).

    `requests` = rows in the shape of the table's rows, the newest first; `seen` = the ids the page held (a broken
    record too); `span_from` = from which id on the page holds every request (see latest._span_from).
    """
    return _translated((await _cached(transport))["latest"])
