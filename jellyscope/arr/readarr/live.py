"""Readarr's live state: the queue, the calendar and health - and the counts of missing and cutoff.

Every request waits at most LIVE_TIMEOUT (3 s): the page waits for it. The counts of missing and cutoff are not
asked here: a big library counts them for long (Sonarr took over 20 s, 23. 11. 2026), so the sync counts them
(services.count_wanted) and this only adds the stored ones with their time, on every read.

Unlike the history (sync.py) this is not stored: it is "how it looks right now" and in an hour it
would not hold. The *Arr page asks when shown - but through a one-minute cache (`CACHE`), so
reloading the page or switching the period does not flood Readarr with requests.

An unavailable Readarr does not bring the page down: `live_state` returns `available: False` and the
error (without the key, translated), the statistics from the database still show.

The unit is the book: a queue record and a calendar entry are named "Author - Book" by the card
and carry the book's id in Readarr (`title_id`), by which the card finds the book in the library.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import db, i18n
from ...services import LIVE_TIMEOUT, integer, settings_fingerprint, stored_counts, utc_time
from .client import SETTINGS_PREFIX, ReadarrClient, ReadarrError

TTL = 60.0          # seconds in the cache
CALENDAR_DAYS = 7
# {"readarr": (when loaded - time.monotonic(), the address and the key's fingerprint, state, error)}.
# The address and the key are in it because of a change in Settings: a fix = a new load at once. The
# key only as a fingerprint (sha256) - the cache is not to hold the key a second time. A dict with one
# item, not a variable: tests empty it from outside (`CACHE.clear()`) and fill it with a prepared state.
CACHE: dict[str, tuple[float, str, dict[str, Any], ReadarrError | None]] = {}


def _book(record: dict[str, Any]) -> str:
    """The book's name: from the embedded book, else the record's own title (the release's name)."""
    book = record.get("book")
    return str((book.get("title") if isinstance(book, dict) else None) or record.get("title") or "?")


def _author(record: dict[str, Any]) -> str:
    author = record.get("author")
    return str((author.get("authorName") if isinstance(author, dict) else None) or "")


def _title(record: dict[str, Any]) -> str:
    """"Author - Book", or only the book when the author is not known."""
    author = _author(record)
    return f"{author} - {_book(record)}" if author else _book(record)


def _queue(answer: dict[str, Any]) -> dict[str, Any]:
    """The count of items in the queue and those with an error or a warning (and why)."""
    raw = answer.get("records")
    records = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    with_error = []
    for r in records:
        state = str(r.get("trackedDownloadStatus") or "").lower()
        if state not in ("warning", "error") and str(r.get("status") or "").lower() not in ("failed", "warning"):
            continue
        # An odd value (a text where a list belongs, a number in the list) is skipped, not an error of the whole state.
        statuses = [s for s in r.get("statusMessages") or [] if isinstance(s, dict)] if isinstance(r.get("statusMessages"), list) else []
        messages = [m for s in statuses for m in (s.get("messages") if isinstance(s.get("messages"), list) else [])] \
            or [s.get("title") for s in statuses if s.get("title")]
        with_error.append({
            "title": _title(r),
            "title_id": integer(r.get("bookId"), None),
            "state": str(r.get("trackedDownloadState") or r.get("status") or ""),
            "message": str(r.get("errorMessage") or "; ".join(str(m) for m in messages[:2])),
        })
    return {"count": integer(answer.get("totalRecords"), None) or len(records), "with_error": with_error}


def _calendar(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which books come out (by `releaseDate`), the nearest first. A calendar entry is the book itself (its `id`)."""
    result = []
    for r in records:
        if not isinstance(r, dict):
            continue
        statistics = r.get("statistics")
        files = statistics.get("bookFileCount") if isinstance(statistics, dict) else None
        result.append({"title": _title(r), "title_id": integer(r.get("id"), None), "at": utc_time(r.get("releaseDate")),
                       "kind": "", "has_file": bool(files)})
    return sorted(result, key=lambda r: (r["at"] or "", r["title"]))[:30]


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "queue": None, "missing": None, "cutoff": None,
            "calendar": None, "health": None}


async def _load(url: str, key: str,
                transport: httpx.AsyncBaseTransport | None) -> tuple[dict[str, Any], ReadarrError | None]:
    """Three requests at once. What fails stays None (unknown); if everything fails, Readarr is unavailable.

    None, not zero: "0 in the queue" or "nothing comes out" after a failed request would be a claim
    nobody checked. The returned error is the first one that happened - even when the rest loaded.
    """
    state = _empty()
    now = datetime.now(UTC)
    start, end = now.strftime("%Y-%m-%dT%H:%M:%SZ"), (now + timedelta(days=CALENDAR_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        client = ReadarrClient(url, key, transport=transport, timeout=LIVE_TIMEOUT)
    except ReadarrError as error:             # an invalid stored address
        return state, error
    async with client as c:
        results = await asyncio.gather(c.queue(), c.calendar(start, end), c.health(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, (ReadarrError, ValueError, TypeError, AttributeError)):
            raise error            # an error in the program, not in the app: let it show
    first = next((e if isinstance(e, ReadarrError) else ReadarrError("{reason}", reason=i18n.error_text(e)) for e in errors), None)
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
    """Readarr's state now (from the cache, if it is younger than a minute).

    The error is translated only here, on every read: the cache is shared by everyone and everyone is
    to read it in their own language.
    """
    url, key = db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")
    fingerprint = settings_fingerprint(url, key)
    cached = CACHE.get("readarr")
    if cached and cached[1] == fingerprint and time.monotonic() - cached[0] < TTL:
        _, _, state, error = cached
    else:
        state, error = await _load(url, key, transport)
        CACHE["readarr"] = (time.monotonic(), fingerprint, state, error)
    return {**state, **stored_counts(SETTINGS_PREFIX, fingerprint), "error": error.translated() if error else ""}
