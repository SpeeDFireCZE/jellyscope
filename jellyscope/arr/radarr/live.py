"""Radarr's live state: the queue, the calendar and health - and the counts of missing and cutoff.

Every request waits at most LIVE_TIMEOUT (3 s): the page waits for it. The counts of missing and cutoff are not
asked here: a big library counts them for long (Sonarr took over 20 s, 23. 11. 2026), so the sync counts them
(services.count_wanted) and this only adds the stored ones with their time, on every read.

Unlike the history (sync.py) this is not stored: it is "how it looks right now" and in an hour it
would not hold. The *Arr page asks when shown - but through a one-minute cache (`CACHE`), so
reloading the page or switching the period does not flood Radarr with requests.

An unavailable Radarr does not bring the page down: `live_state` returns `dostupna: False` and the
error (without the key, translated), the statistics from the database still show.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import db, i18n
from ...services import LIVE_TIMEOUT, integer, settings_fingerprint, stored_counts, utc_time
from .client import SETTINGS_PREFIX, RadarrClient, RadarrError

TTL = 60.0          # seconds in the cache
CALENDAR_DAYS = 7
# {"radarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error)}.
# The address and the key are in it because of a change in Settings: a fix = a new load at once. The
# key only as a fingerprint (sha256) - the cache is not to hold the key a second time. A dict with one
# item, not a variable: tests empty it from outside (`CACHE.clear()`) and fill it with a prepared state.
CACHE: dict[str, tuple[float, str, dict[str, Any], RadarrError | None]] = {}


def _title(record: dict[str, Any]) -> str:
    return str((record.get("movie") or {}).get("title") or record.get("title") or "?")


def _queue(answer: dict[str, Any]) -> dict[str, Any]:
    """The count of items in the queue and those with an error or a warning (and why)."""
    records = [r for r in answer.get("records") or [] if isinstance(r, dict)]
    with_error = []
    for r in records:
        state = str(r.get("trackedDownloadStatus") or "").lower()
        if state not in ("warning", "error") and str(r.get("status") or "").lower() not in ("failed", "warning"):
            continue
        statuses = r.get("statusMessages") or []
        messages = [m for s in statuses for m in (s.get("messages") or [])] or [s.get("title") for s in statuses if s.get("title")]
        with_error.append({
            "title": _title(r),
            # The movie's id in Radarr: by it the card finds the movie in the library.
            "title_id": integer(r.get("movieId"), None),
            "episode": "",
            "state": str(r.get("trackedDownloadState") or r.get("status") or ""),
            "message": str(r.get("errorMessage") or "; ".join(str(m) for m in messages[:2])),
        })
    return {"count": int(answer.get("totalRecords") or len(records)), "with_error": with_error}


def _calendar(records: list[dict[str, Any]], start: str) -> list[dict[str, Any]]:
    """Which movies come out: for each the nearest of its releases in the window (digital, physical, cinema).

    Radarr's calendar is movies straight away - the movie's id is the record's `id`.
    """
    result = []
    for r in records:
        if not isinstance(r, dict):
            continue
        dates = [(c, d) for c, d in ((utc_time(r.get("digitalRelease")), "digital"),
                                     (utc_time(r.get("physicalRelease")), "physical"),
                                     (utc_time(r.get("inCinemas")), "cinema")) if c]
        in_window = [p for p in dates if p[0] >= start] or dates
        when, kind = min(in_window) if in_window else (None, "")
        result.append({"title": str(r.get("title") or "?"), "title_id": integer(r.get("id"), None),
                       "episode": "", "at": when, "kind": kind, "has_file": bool(r.get("hasFile"))})
    return sorted(result, key=lambda r: (r["at"] or "", r["title"]))[:30]


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "queue": None, "missing": None, "cutoff": None,
            "calendar": None, "health": None}


async def _load(url: str, key: str,
                transport: httpx.AsyncBaseTransport | None) -> tuple[dict[str, Any], RadarrError | None]:
    """Three requests at once. What fails stays None (unknown); if everything fails, Radarr is unavailable.

    None, not zero: "0 in the queue" or "nothing airs" after a failed request would be a claim nobody
    checked. The returned error is the first one that happened - even when the rest loaded.
    """
    state = _empty()
    now = datetime.now(UTC)
    start, end = now.strftime("%Y-%m-%dT%H:%M:%SZ"), (now + timedelta(days=CALENDAR_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        client = RadarrClient(url, key, transport=transport, timeout=LIVE_TIMEOUT)
    except RadarrError as error:             # an invalid stored address
        return state, error
    async with client as c:
        results = await asyncio.gather(c.queue(), c.calendar(start, end), c.health(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (RadarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, RadarrError) else RadarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
    if len(errors) == len(results):
        return state, first
    queue, calendar, health = (None if isinstance(r, BaseException) else r for r in results)
    state["available"] = True
    state["queue"] = _queue(queue) if isinstance(queue, dict) else None
    state["calendar"] = _calendar(calendar, utc_time(start) or "") if isinstance(calendar, list) else None
    state["health"] = [{"type": str(h.get("type") or ""), "message": str(h.get("message") or "")}
                       for h in health if isinstance(h, dict)] if isinstance(health, list) else None
    return state, first


async def live_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Radarr's state now (from the cache, if it is younger than a minute).

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")
    fingerprint = settings_fingerprint(url, key)
    cached = CACHE.get("radarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        state, error = await _load(url, key, transport)
        CACHE["radarr"] = (time.monotonic(), fingerprint, state, error)
    return {**state, **stored_counts(SETTINGS_PREFIX, fingerprint), "error": error.translated() if error else ""}
