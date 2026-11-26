"""The live card "Waiting for approval" and what both live cards share: the one-minute cache, the names of fresh
titles.

Nothing is stored - it is "how it looks right now". The Seerr page asks when shown, but through a
one-minute cache (`CACHE`), so reloading the page or switching the period does not flood Seerr with
requests. The cache notices a change of the address and of the key (the key only as a fingerprint):
fixed settings load at once. Both live cards ("Latest requests" reads its page in latest.py) load
together under ONE entry of it - now.py, where `pending` and `latest_requests` are; here are the
pending requests' rows (`_read_pending`) and the names of titles the table does not know yet
(`_add_titles`, looked up once for both cards).

An unavailable Seerr does not bring the page down: `dostupny: False` and the error (without the key,
translated on every read - the cache is shared and everyone reads it in their own language); the
statistics from the database still show.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from .. import db
from ..jellyfin import id_without_dashes as normalize_id
from ..services import utc_time, integer
from .client import SeerrClient, SeerrError, _tmdb_id

TTL = 60.0              # seconds in the cache (now.py)
PAGE_SIZE = 50          # how many pending requests per request to Seerr
MAX_PAGES = 10          # at most 500 pending; nobody handles more at once
MAX_TITLES = 20         # how many names are looked up per load (the page waits for them)
CONCURRENT = 5          # how many requests for names go to Seerr at once
# Both live cards under one entry (now.py): {CACHE_NAME: (when, fingerprint of the settings, {"pending": (state, error),
# "latest": (state, error)})}.
CACHE_NAME = "seerr"
CACHE: dict[str, tuple[float, str, dict[str, tuple[dict[str, Any], SeerrError | None]]]] = {}
# What was looked up for fresh requests: {(media type, TMDB id): {"name": ..., "poster": ...}}.
# A request newer than the last sync is not in the table yet and without this the card would ask for
# its name on every load. The poster path comes in the same answer; by it the route /seerr/poster/…
# knows which image to ask Seerr for (see posters.py). Only in memory - the next sync stores it.
_TITLES: dict[tuple[str, str], dict[str, str]] = {}
MAX_REMEMBERED = 2000


def fresh_poster(media_type: str, tmdb_id: str) -> str | None:
    """The poster path of a title from a fresh pending request. None = the memory does not know the title.

    An empty text = the title is known, but Seerr has no poster for it.
    """
    title = _TITLES.get((media_type, tmdb_id))
    return None if title is None else title["poster"]


def _empty() -> dict[str, Any]:
    return {"available": False, "error": "", "count": 0, "requests": []}


def _rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pending requests for the card, the longest waiting first.

    The name from the database (the sync knows it), else the TMDB id; the requester as a Jellyscope
    viewer (`user_id`, `user_name`), else by the name from Seerr. Sorted here, not in Seerr: Overseerr
    ignores `sortDirection` and sends the newest first. The request's id is under the key
    `request_id`, not `id`: anonymising takes a row's `id` for a viewer's id (access.anonymise) and
    for a requester who is no Jellyscope viewer it would make a link to a page that does not exist.
    The title's `tmdb_id` and `jellyfin_id` are here for the poster (by them the card finds the title
    in the library, see cards_seerr.py), `is_4k` for the badge.
    """
    ids = [r["id"] for r in records]
    titles = {r["id"]: r["title"] for r in db.query_all_in(
        "SELECT id, title FROM seerr_request WHERE title <> '' AND id IN ({})", ids)}
    viewers = {normalize_id(r["id"]): (str(r["id"]), str(r["name"])) for r in db.query_all("SELECT id, name FROM users")}
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = []
    for record in records:
        # An odd value (a text or a number where an object belongs) is no requester or title, not an error of the card.
        requested_by = record.get("requestedBy")
        requested_by = requested_by if isinstance(requested_by, dict) else {}
        created = utc_time(record.get("createdAt"))
        user_id, user_name = (viewers.get(normalize_id(requested_by.get("jellyfinUserId")))
                              or ("", str(requested_by.get("displayName") or "")))
        media = record.get("media")
        media = media if isinstance(media, dict) else {}
        tmdb = _tmdb_id(media.get("tmdbId"))
        is_4k = bool(record.get("is4k"))
        rows.append({
            "request_id": record["id"],
            "media_type": str(record.get("type") or ""),
            "tmdb_id": tmdb,
            "jellyfin_id": normalize_id(media.get("jellyfinMediaId4k" if is_4k else "jellyfinMediaId")),
            "is_4k": is_4k,
            "title": titles.get(record["id"]) or "",
            "created_at": created,
            "waiting_seconds": max(0, int((now - datetime.strptime(created, db.TIME_FORMAT)).total_seconds()))
            if created else None,
            "user_id": user_id,
            "user_name": user_name,
        })
    return sorted(rows, key=lambda r: (r["created_at"] or "", r["request_id"]))


async def _add_titles(client: SeerrClient, rows: list[dict[str, Any]]) -> None:
    """Names of requests the table does not know yet (newer than the last sync).

    The live card exists for the fresh requests - "TMDB 603" tells the admin nothing. It asks Seerr
    (which takes the name from TMDB), the oldest requests first, at most MAX_TITLES per load and
    CONCURRENT at once: the page waits for the answer. What it found it remembers (`_TITLES`, with the
    poster path), so the next load fills in only the rest. A failed look-up leaves "TMDB id".
    """
    def known(row: dict[str, Any]) -> str:
        return (_TITLES.get((row["media_type"], row["tmdb_id"])) or {}).get("name", "")

    for row in rows:
        row["title"] = row["title"] or known(row)
    missing = list(dict.fromkeys((r["media_type"], r["tmdb_id"]) for r in rows if not r["title"] and r["tmdb_id"]))
    lock = asyncio.Semaphore(CONCURRENT)

    async def one(media_type: str, tmdb_id: str) -> None:
        async with lock:
            try:
                title = await client.title_info(media_type, tmdb_id)
            except SeerrError:
                return
        if title["name"]:
            _TITLES[(media_type, tmdb_id)] = title

    await asyncio.gather(*(one(media_type, tmdb_id) for media_type, tmdb_id in missing[:MAX_TITLES]))
    if len(_TITLES) > MAX_REMEMBERED:        # a safeguard: the memory does not grow for ever
        _TITLES.clear()
    for row in rows:
        row["title"] = row["title"] or known(row)


async def _read_pending(client: SeerrClient) -> dict[str, Any]:
    """All pending requests (page by page) as the card's state, the titles the table does not know yet still empty.
    Raises SeerrError."""
    records: dict[int, dict[str, Any]] = {}       # by id: a shift while paging doubles nothing
    total = 0
    for page in range(MAX_PAGES):
        answer = await client.requests_page(page * PAGE_SIZE, PAGE_SIZE, "pending")
        part = answer.get("results")
        part = [r for r in part if isinstance(r, dict) and integer(r.get("id"), None) is not None] \
            if isinstance(part, list) else []
        records.update({r["id"]: r for r in part})
        page_info = answer.get("pageInfo")
        total = integer(page_info.get("results") if isinstance(page_info, dict) else None, len(records))
        if len(part) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= total:
            break
    # Queries to the database are blocking work - in a thread, so the web runs meanwhile.
    rows = await asyncio.to_thread(_rows, list(records.values()))
    return {**_empty(), "available": True, "count": max(total, len(rows)), "requests": rows}
