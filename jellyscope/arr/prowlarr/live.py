"""Prowlarr's live state: the indexers, which of them do not work right now, the count of connected apps and the warnings.

It is not stored - it is "how it looks right now". The Prowlarr tab asks when shown, but through a
one-minute cache (`CACHE`), so reloading the page or switching the period does not flood Prowlarr with
requests. The cache notices a change of the address and of the key (the key only as a fingerprint):
fixed settings load at once.

Four requests at once: the indexers, their blocks (`indexerstatus`), the connected apps and the
warnings. **A block** is what the statistics from the history do not see: an indexer that does not
accept a connection writes no event into the history - Prowlarr only leaves it out for a while (a few
minutes first, longer when it repeats) and answers the apps with an error in its place.

An indexer is **unavailable** from two sources, because Prowlarr itself talks about it in two places: a
block holds only until its end (then the indexer is tried again on the next query), whereas the
warning "Indexers unavailable due to failures" is refreshed only now and then and names the indexer
even after the block expired. If only the first source were taken, the same indexer would say "fine"
and a card lower the warning. An indexer named in that warning is therefore `reported` (reported).

What does not load is None (unknown), not zero nor an empty list: "temporarily switched off 0" after a
failed request would be a claim nobody checked. An unavailable Prowlarr does not bring the page down:
`dostupna: False` and the error (without the key, translated on every read); the statistics from the
database still show.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import httpx

from ... import db, i18n
from ...services import utc_time, integer
from .client import ProwlarrClient, ProwlarrError

TTL = 60.0             # seconds in the cache
# Prowlarr's health checks that name unavailable indexers (briefly and for longer than six hours).
UNAVAILABLE_SOURCES = ("IndexerStatusCheck", "IndexerLongTermStatusCheck")
# Under the key "prowlarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error).
CACHE: dict[str, tuple[Any, ...]] = {}


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "indexers": None, "enabled_count": None, "unavailable_count": None,
            "app_count": None, "health": None, "warnings": None}


def _blocks(records: list[dict[str, Any]], now: str) -> dict[int, tuple[str, str | None]]:
    """The indexers Prowlarr leaves out right now: {number: (until when, failing since)} in UTC. A block that expired already does not count."""
    blocks = {}
    for record in records:
        number, until = integer(record.get("indexerId"), None), utc_time(record.get("disabledTill"))
        if number is not None and until and until > now:
            blocks[number] = (until, utc_time(record.get("initialFailure")))
    return blocks


def _names_in_text(text: str, names: list[str]) -> set[str]:
    """Which indexer names are written whole in a warning's text.

    It searches from the longest name and a place one name took no other can get: "arr-test" inside
    "arr-test-torrent" does not count. The separators do not matter - Prowlarr in another language
    writes the list of names with other punctuation.
    """
    taken: list[tuple[int, int]] = []
    found: set[str] = set()
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        start = text.find(name)
        while start >= 0:
            end = start + len(name)
            if not any(a < end and start < b for a, b in taken):
                taken.append((start, end))
                found.add(name)
            start = text.find(name, start + 1)
    return found


def _reported(names: list[str], health: list[dict[str, Any]] | None) -> set[str] | None:
    """The names of enabled indexers Prowlarr reports in the warning about unavailable indexers.

    None = unknown (the warnings did not load). A warning without names is the one Prowlarr writes when
    all of them fail ("All indexers are unavailable due to failures") - then all the enabled ones are
    reported.
    """
    if health is None:
        return None
    reported: set[str] = set()
    for record in health:
        if str(record.get("source") or "") in UNAVAILABLE_SOURCES:
            reported |= _names_in_text(str(record.get("message") or ""), names) or set(names)
    return reported


def _indexers(records: list[dict[str, Any]], blocks: dict[int, tuple[str, str | None]] | None,
              health: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The indexers with their state: those temporarily switched off by Prowlarr first, then those it reports as
    unavailable, then those switched off by hand, then the others; within that by name.

    `blocks` None = they failed to load: then for nobody is it known whether they are blocked.
    """
    reported = _reported([str(r.get("name") or "") for r in records if r.get("enable")], health) or set()
    rows = []
    for record in records:
        number, name = integer(record.get("id"), None), str(record.get("name") or "?")
        until, since = (blocks or {}).get(number, (None, None))
        rows.append({"id": number, "name": name, "protocol": str(record.get("protocol") or ""), "enabled": bool(record.get("enable")),
                     "blocked_until": until, "failing_since": since, "reported": bool(record.get("enable")) and name in reported})
    return sorted(rows, key=lambda r: (0 if r["blocked_until"] else 1 if r["reported"] else 2 if not r["enabled"] else 3, r["name"].casefold()))


async def _load(url: str, key: str, transport: httpx.AsyncBaseTransport | None) -> tuple[dict[str, Any], ProwlarrError | None]:
    """Four requests at once. What fails stays None (unknown); if everything fails, Prowlarr is unavailable.

    Returns (the state, the first error).
    """
    state = _empty()
    try:
        client = ProwlarrClient(url, key, transport=transport)
    except ProwlarrError as error:           # an invalid stored address or key
        return state, error
    async with client as c:
        results = await asyncio.gather(c.indexers(), c.blocked(), c.applications(), c.health(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (ProwlarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, ProwlarrError) else ProwlarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
    if len(errors) == len(results):
        return state, first
    indexers, blocked, applications, health = (None if isinstance(r, BaseException) else r for r in results)
    state["available"] = True
    blocks = None if blocked is None else _blocks(blocked, db.utcnow())
    if indexers is not None:
        state["indexers"] = _indexers(indexers, blocks, health)
        state["enabled_count"] = sum(r["enabled"] for r in state["indexers"])
        # Unavailability has two sources (blocks and warnings). If one is missing, the count is unknown.
        if blocks is not None and health is not None:
            state["unavailable_count"] = sum(bool(r["blocked_until"] or r["reported"]) for r in state["indexers"])
    if applications is not None:
        state["app_count"] = len(applications)
    if health is not None:
        state["health"] = [{"type": str(h.get("type") or ""), "message": str(h.get("message") or "")} for h in health]
        state["warnings"] = len(state["health"])
    return state, first


async def live_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Prowlarr's state now (from the cache, if it is younger than a minute).

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting("arr_prowlarr_url", ""), db.get_setting("arr_prowlarr_api_key", "")
    fingerprint = url + "|" + hashlib.sha256(key.encode()).hexdigest()
    cached = CACHE.get("prowlarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        state, error = await _load(url, key, transport)
        CACHE["prowlarr"] = (time.monotonic(), fingerprint, state, error)
    return {**state, "error": error.translated() if error else ""}
