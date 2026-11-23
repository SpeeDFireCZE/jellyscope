"""Sonarr's live state: the queue, missing, cutoff, the calendar and health.

Unlike the history (sync.py) this is not stored: it is "how it looks right now" and in an hour it
would not hold. The *Arr page asks when shown - but through a one-minute cache (`CACHE`), so
reloading the page or switching the period does not flood Sonarr with requests.

An unavailable Sonarr does not bring the page down: `live_state` returns `dostupna: False` and the
error (without the key, translated), the statistics from the database still show.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import db, i18n
from ...services import utc_time, integer
from .client import SETTINGS_PREFIX, SonarrClient, SonarrError

TTL = 60.0          # seconds in the cache
CALENDAR_DAYS = 7
# {"sonarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error)}.
# The address and the key are in it because of a change in Settings: a fix = a new load at once. The
# key only as a fingerprint (sha256) - the cache is not to hold the key a second time. A dict with one
# item, not a variable: tests empty it from outside (`CACHE.clear()`) and fill it with a prepared state.
CACHE: dict[str, tuple[float, str, dict[str, Any], SonarrError | None]] = {}


def _episode(record: dict[str, Any]) -> str:
    episode = record.get("episode") or record
    season, number = episode.get("seasonNumber"), episode.get("episodeNumber")
    return f"S{season:02d}E{number:02d}" if isinstance(season, int) and isinstance(number, int) else ""


def _title(record: dict[str, Any]) -> str:
    return str((record.get("series") or {}).get("title") or record.get("title") or "?")


def _title_id(record: dict[str, Any]) -> int | None:
    """The series' id in Sonarr (`seriesId`): by it the card finds the series in the library."""
    return integer(record.get("seriesId"), None)


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
            "title_id": _title_id(r),
            "episode": _episode(r),
            "state": str(r.get("trackedDownloadState") or r.get("status") or ""),
            "message": str(r.get("errorMessage") or "; ".join(str(m) for m in messages[:2])),
        })
    return {"count": int(answer.get("totalRecords") or len(records)), "with_error": with_error}


def _calendar(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which episodes air (by `airDateUtc`), the nearest first."""
    result = []
    for r in records:
        if not isinstance(r, dict):
            continue
        result.append({"title": _title(r), "title_id": _title_id(r), "episode": _episode(r),
                       "at": utc_time(r.get("airDateUtc")), "kind": "", "has_file": bool(r.get("hasFile"))})
    return sorted(result, key=lambda r: (r["at"] or "", r["title"]))[:30]


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "queue": None, "missing": None, "cutoff": None,
            "calendar": None, "health": None}


async def _load(url: str, key: str,
                transport: httpx.AsyncBaseTransport | None) -> tuple[dict[str, Any], SonarrError | None]:
    """Five requests at once. What fails stays None (unknown); if everything fails, Sonarr is unavailable.

    None, not zero: "0 in the queue" or "nothing airs" after a failed request would be a claim nobody
    checked. The returned error is the first one that happened - even when the rest loaded.
    """
    state = _empty()
    now = datetime.now(UTC)
    start, end = now.strftime("%Y-%m-%dT%H:%M:%SZ"), (now + timedelta(days=CALENDAR_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        client = SonarrClient(url, key, transport=transport)
    except SonarrError as error:             # an invalid stored address
        return state, error
    async with client as c:
        results = await asyncio.gather(c.queue(), c.record_count("wanted/missing"), c.record_count("wanted/cutoff"),
                                       c.calendar(start, end), c.health(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (SonarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, SonarrError) else SonarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
    if len(errors) == len(results):
        return state, first
    queue, missing, cutoff, calendar, health = (None if isinstance(r, BaseException) else r for r in results)
    state["available"] = True
    state["queue"] = _queue(queue) if isinstance(queue, dict) else None
    state["missing"], state["cutoff"] = missing, cutoff
    state["calendar"] = _calendar(calendar) if isinstance(calendar, list) else None
    state["health"] = [{"type": str(h.get("type") or ""), "message": str(h.get("message") or "")}
                       for h in health if isinstance(h, dict)] if isinstance(health, list) else None
    return state, first


async def live_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Sonarr's state now (from the cache, if it is younger than a minute).

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")
    fingerprint = url + "|" + hashlib.sha256(key.encode()).hexdigest()
    cached = CACHE.get("sonarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        state, error = await _load(url, key, transport)
        CACHE["sonarr"] = (time.monotonic(), fingerprint, state, error)
    return {**state, "error": error.translated() if error else ""}
