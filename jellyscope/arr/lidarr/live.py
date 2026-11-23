"""Lidarr's live state: the queue, the calendar and health - and the counts of missing and cutoff.

Every request waits at most LIVE_TIMEOUT (3 s): the page waits for it. The counts of missing and cutoff are not
asked here: a big library counts them for long (Sonarr took over 20 s, 23. 11. 2026), so the sync counts them
(services.count_wanted) and this only adds the stored ones with their time, on every read.

Unlike the history (sync.py) this is not stored: it is "how it looks right now" and in an hour it
would not hold. The *Arr page asks when shown - but through a one-minute cache (`CACHE`), so
reloading the page or switching the period does not flood Lidarr with requests.

An unavailable Lidarr does not bring the page down: `live_state` returns `available: False` and the
error (without the key, translated), the statistics from the database still show.

The unit is the album: a queue record and a calendar entry are named "Artist - Album" by the card
and carry the album's id in Lidarr (`title_id`), by which the card finds the album in the library.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import db, i18n
from ...services import LIVE_TIMEOUT, integer, settings_fingerprint, stored_counts, utc_time
from .client import SETTINGS_PREFIX, LidarrClient, LidarrError

TTL = 60.0          # seconds in the cache
CALENDAR_DAYS = 7
# {"lidarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error)}.
# The address and the key are in it because of a change in Settings: a fix = a new load at once. The
# key only as a fingerprint (sha256) - the cache is not to hold the key a second time. A dict with one
# item, not a variable: tests empty it from outside (`CACHE.clear()`) and fill it with a prepared state.
CACHE: dict[str, tuple[float, str, dict[str, Any], LidarrError | None]] = {}


def _album(record: dict[str, Any]) -> str:
    """The album's name: from the embedded album, else the record's own title (the release's name)."""
    return str((record.get("album") or {}).get("title") or record.get("title") or "?")


def _artist(record: dict[str, Any]) -> str:
    return str((record.get("artist") or {}).get("artistName") or "")


def _title(record: dict[str, Any]) -> str:
    """"Artist - Album", or only the album when the artist is not known."""
    artist = _artist(record)
    return f"{artist} - {_album(record)}" if artist else _album(record)


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
            "title_id": integer(r.get("albumId"), None),
            "state": str(r.get("trackedDownloadState") or r.get("status") or ""),
            "message": str(r.get("errorMessage") or "; ".join(str(m) for m in messages[:2])),
        })
    return {"count": int(answer.get("totalRecords") or len(records)), "with_error": with_error}


def _calendar(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which albums come out (by `releaseDate`), the nearest first. A calendar entry is the album itself (its `id`)."""
    result = []
    for r in records:
        if not isinstance(r, dict):
            continue
        tracks = (r.get("statistics") or {}).get("trackFileCount")
        result.append({"title": _title(r), "title_id": integer(r.get("id"), None), "at": utc_time(r.get("releaseDate")),
                       "kind": "", "has_file": bool(tracks)})
    return sorted(result, key=lambda r: (r["at"] or "", r["title"]))[:30]


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "queue": None, "missing": None, "cutoff": None,
            "calendar": None, "health": None}


async def _load(url: str, key: str,
                transport: httpx.AsyncBaseTransport | None) -> tuple[dict[str, Any], LidarrError | None]:
    """Three requests at once. What fails stays None (unknown); if everything fails, Lidarr is unavailable.

    None, not zero: "0 in the queue" or "nothing comes out" after a failed request would be a claim
    nobody checked. The returned error is the first one that happened - even when the rest loaded.
    """
    state = _empty()
    now = datetime.now(UTC)
    start, end = now.strftime("%Y-%m-%dT%H:%M:%SZ"), (now + timedelta(days=CALENDAR_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        client = LidarrClient(url, key, transport=transport, timeout=LIVE_TIMEOUT)
    except LidarrError as error:             # an invalid stored address
        return state, error
    async with client as c:
        results = await asyncio.gather(c.queue(), c.calendar(start, end), c.health(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (LidarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, LidarrError) else LidarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
    if len(errors) == len(results):
        return state, first
    queue, calendar, health = (None if isinstance(r, BaseException) else r for r in results)
    state["available"] = True
    state["queue"] = _queue(queue) if isinstance(queue, dict) else None
    state["calendar"] = _calendar(calendar) if isinstance(calendar, list) else None
    state["health"] = [{"type": str(h.get("type") or ""), "message": str(h.get("message") or "")}
                       for h in health if isinstance(h, dict)] if isinstance(health, list) else None
    return state, first


async def live_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Lidarr's state now (from the cache, if it is younger than a minute).

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")
    fingerprint = settings_fingerprint(url, key)
    cached = CACHE.get("lidarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        state, error = await _load(url, key, transport)
        CACHE["lidarr"] = (time.monotonic(), fingerprint, state, error)
    return {**state, **stored_counts(SETTINGS_PREFIX, fingerprint), "error": error.translated() if error else ""}
