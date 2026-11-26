"""The sync of Seerr's requests into the table `seerr_request`.

Requests number in the hundreds, not tens of thousands, and they change (pending → approved →
available). So every sync walks the **whole** list page by page and upserts every request by its id.
On top, Jellyscope works out what Seerr does not store:

* **times of state changes**: when it first saw the approval or the decline of a request it saw
  pending before (`updatedAt` at that moment). A request first seen already approved cannot tell the
  time and keeps NULL;
* **deletion in Seerr**: a request missing from a complete walk gets `deleted_at` - last year's
  statistics do not lose it. If it comes back, the mark goes. An error in the middle marks nothing
  (we do not know what is missing);
* **the title's name**: the request does not carry it. First from the Jellyscope library by TMDB id,
  the rest from Seerr (at most 5 requests at once), each only once;
* **the poster path** (`poster`) of titles that are not in the library - from the same Seerr answer
  as the name. By it Seerr can then be asked for the poster (posters.py); a title from the library
  has its poster from Jellyfin and needs no path.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from .. import db, i18n, images, stats
from ..arr import radarr, sonarr
from ..jellyfin import DEFAULT_TIMEOUT, id_without_dashes, id_forms
from ..services import SYNC_ALREADY_RUNNING as ALREADY_RUNNING
from ..services import utc_time, integer, read_state, write_state
from . import posters
from .client import NAME, SeerrClient, SeerrError, _tmdb_id

log = logging.getLogger("jellyscope.seerr")

PAGE_SIZE = 100
MAX_PAGES = 10_000              # a safeguard against an endless loop (a million requests)
CONCURRENT = 5                  # how many requests for single names and requests go to Seerr at once
DEFAULT_ADDED_AT_WINDOW = 60    # mediaAddedAt within a minute of the title's creation = Seerr's default value
AVAILABLE_STATES = (4, 5)       # the title's state: partly available, available

# The column `poster` is left out on purpose: the upsert would overwrite a looked-up poster path with
# an empty one on every sync. Only the look-up of titles writes it (_save_titles).
COLUMNS = ("id", "media_type", "tmdb_id", "tvdb_id", "jellyfin_id", "title", "seasons", "is_4k", "auto_request", "status", "media_status", "requester_seerr_id",
           "requester_name", "requester_jellyfin_id", "handled_by_name", "created_at", "updated_at", "available_at", "seen_pending",
           "approved_at", "declined_at", "deleted_at", "synced_at")
UPSERT = (f"INSERT INTO seerr_request ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (id) DO UPDATE SET " + ", ".join(f"{s} = excluded.{s}" for s in COLUMNS[1:]))

# Is a sync (or a deletion) running? A set with at most one item: it changes without `global` and tests set it from outside.
_RUNNING: set[str] = set()


# A Jellyfin id in the shape it can be compared in (lower case, without dashes). The one function for
# the whole app is in jellyfin.py; here only under the name the package's door offers it by
# (`seerr.normalize_id`).
normalize_id = id_without_dashes


def _available_since(media: dict[str, Any], title_state: int | None, is_4k: bool = False) -> str | None:
    """When the title arrived in the library - only when it really is there and the time is known.

    Seerr fills `mediaAddedAt` already when the title is created (with the creation time) and writes
    the real time only on a library scan, and only for movies. A value within a minute of
    `media.createdAt` is therefore the default, not the real one. For a 4K request the time is never
    known: `mediaAddedAt` belongs to the normal version of the title (the versions share one record)
    and the 4K one may have arrived months later.
    """
    if title_state not in AVAILABLE_STATES or is_4k:
        return None
    added, created = utc_time(media.get("mediaAddedAt")), utc_time(media.get("createdAt"))
    if not added:
        return None
    if created and abs((_dt(added) - _dt(created)).total_seconds()) <= DEFAULT_ADDED_AT_WINDOW:
        return None
    return added


def _dt(text: str) -> datetime:
    return datetime.strptime(text, db.TIME_FORMAT)


def request_row(record: dict[str, Any]) -> dict[str, Any]:
    """One request from Seerr as a row of `seerr_request` (without the fields the sync works out
    itself: the title, the times of state changes, the deletion)."""
    media = record.get("media") or {}
    requested_by = record.get("requestedBy") or {}
    handled_by = record.get("modifiedBy") or {}
    is_4k = bool(record.get("is4k"))
    title_state = media.get("status4k" if is_4k else "status")
    media_type = str(record.get("type") or media.get("mediaType") or "movie")
    return {
        "id": int(record["id"]),
        "media_type": media_type,
        "tmdb_id": _tmdb_id(media.get("tmdbId")),
        # The series' TVDB id: Sonarr finds the series by it (the request's poster, posters.py).
        "tvdb_id": _tmdb_id(media.get("tvdbId")),
        # The title's id in Jellyfin - a more reliable pairing with the library than TMDB (episodes
        # have no TMDB id in Jellyscope). Only a title Seerr found in the library has it.
        "jellyfin_id": normalize_id(media.get("jellyfinMediaId4k" if is_4k else "jellyfinMediaId")),
        "seasons": len(record.get("seasons") or []) if media_type == "tv" else None,
        "is_4k": int(is_4k),
        "auto_request": int(bool(record.get("isAutoRequest"))),
        "status": int(record.get("status") or 0),
        "media_status": int(title_state) if title_state is not None else None,
        "requester_seerr_id": requested_by.get("id"),
        "requester_name": str(requested_by.get("displayName") or ""),
        "requester_jellyfin_id": normalize_id(requested_by.get("jellyfinUserId")),
        "handled_by_name": str(handled_by.get("displayName") or ""),
        "created_at": utc_time(record.get("createdAt")),
        "updated_at": utc_time(record.get("updatedAt")),
        "available_at": _available_since(media, title_state, is_4k),
    }


def _write_page(rows: list[dict[str, Any]], now: str) -> int:
    """Upsert one page in one transaction; works out the times of state changes. Returns the count of new ones."""
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    stored = {r["id"]: dict(r) for r in db.query_all_in(
        "SELECT id, title, seen_pending, approved_at, declined_at FROM seerr_request WHERE id IN ({})", ids)}
    values = []
    for r in rows:
        old = stored.get(r["id"]) or {}
        saw_pending = bool(old.get("seen_pending"))
        r |= {"title": old.get("title") or "", "seen_pending": int(saw_pending or r["status"] == 1),
              "approved_at": old.get("approved_at"), "declined_at": old.get("declined_at"),
              "deleted_at": None, "synced_at": now}
        # Only from the state "approved" (2). A request first seen already done (5) or failed (4) has
        # the time of finishing or failing in `updatedAt` - it would pass for the time of approval.
        # An unknown time is better than a wrong one.
        if saw_pending and r["approved_at"] is None and r["status"] == 2:
            r["approved_at"] = r["updated_at"]
        if saw_pending and r["declined_at"] is None and r["status"] == 3:
            r["declined_at"] = r["updated_at"]
        values.append(tuple(r[s] for s in COLUMNS))
    with db.connect() as conn:
        conn.executemany(UPSERT, values)
        conn.commit()
    return len(ids) - len(stored)


def _page_rows(records: list[Any]) -> list[dict[str, Any]]:
    """Records from Seerr as rows. A broken record is skipped, the others go through.

    Another app's answer is input like any other: a record without an id or a creation time, with an
    id or a state that is no number, must not bring the whole sync down - and with it the marking of
    deleted requests and the look-up of names.
    """
    rows = []
    for record in records:
        if not isinstance(record, dict) or record.get("id") is None:
            continue
        try:
            row = request_row(record)
        except (TypeError, ValueError, AttributeError):
            log.debug("request %r from Seerr cannot be converted, skipping", record.get("id"))
            continue
        if row["created_at"]:
            rows.append(row)
    return rows


def _missing(seen: set[int]) -> list[int]:
    """Stored requests the walk did not see that are not marked deleted yet."""
    return [r["id"] for r in db.query_all("SELECT id FROM seerr_request WHERE deleted_at IS NULL")
            if r["id"] not in seen]


def _mark_deleted(ids: list[int], now: str) -> None:
    if ids:
        with db.connect() as conn:
            conn.executemany("UPDATE seerr_request SET deleted_at = ? WHERE id = ?", [(now, i) for i in ids])
            conn.commit()


async def _check_missing(client: SeerrClient, seen: set[int], now: str) -> int:
    """After a complete walk the requests missing from the list are checked one by one. Returns the count of deleted.

    A request that exists can be missing from the list too: when somebody deletes an earlier request
    during the paging, the next one moves to a page already fetched and the walk misses it. So every
    missing one is asked for by itself (`request/{id}`): 404 = really deleted, an answer = alive and
    it is stored. When the check fails (Seerr went down in the middle), nothing is marked - the next
    sync tries again.
    """
    missing = await asyncio.to_thread(_missing, seen)
    if not missing:
        return 0
    lock = asyncio.Semaphore(CONCURRENT)
    unknown = object()

    async def one(request_id: int) -> Any:
        async with lock:
            try:
                return await client.request(request_id)
            except SeerrError:
                return unknown

    answers = await asyncio.gather(*(one(i) for i in missing))
    deleted = [i for i, answer in zip(missing, answers) if answer is None]
    alive = [answer for answer in answers if isinstance(answer, dict)]
    await asyncio.to_thread(_mark_deleted, deleted, now)
    await asyncio.to_thread(_write_page, _page_rows(alive), now)
    unchecked = sum(answer is unknown for answer in answers)
    if unchecked:
        log.info("could not verify whether %s requests still exist in Seerr; will retry next time", unchecked)
    return len(deleted)


def _item_names(key_column: str, name_column: str, item_type: str, values: list[str]) -> dict[str, str]:
    """{value of the key: name} from the library for the items of the given type.

    `key_column` is the column searched by (`id`, `series_id`, `tmdb_id`), `name_column` the name
    (`name`, `series_name`). The search goes through `IN (...)` over the stored shape, so the query
    uses the index. The column names come only from the fixed list in `_library_names`, never from
    input.
    """
    rows = db.query_all_in(
        f"SELECT {key_column} AS key, MIN({name_column}) AS name FROM items"    # noqa: S608 - column names are constants
        f" WHERE type = ? AND {name_column} IS NOT NULL AND {key_column} IN ({{}}) GROUP BY {key_column}",
        sorted(set(values)), (item_type,))
    return {str(r["key"]): str(r["name"]) for r in rows if r["name"]}


def _library_names() -> None:
    """Names from the Jellyscope library: a movie's `items.name`, a series' `series_name` of its episodes.

    First by the title's id in Jellyfin (a movie = items.id, a series = series_id of the episodes). A
    movie is tried by its TMDB id as well. A series is not: an episode may carry its own TMDB id (the
    episode's id, another number space than the series') and a request for a series with the same
    number would get another series' name - for good. A series without an id in Jellyfin takes its
    name from Seerr. The id from Seerr is looked up in both shapes it may be stored in (without dashes
    and with them) - see jellyfin.id_forms.
    """
    without_name = db.query_all("SELECT id, media_type, tmdb_id, jellyfin_id FROM seerr_request WHERE title = ''")
    names: list[tuple[str, int]] = []
    for media_type, name_column, item_type, key_column in (("movie", "name", "Movie", "id"),
                                                           ("tv", "series_name", "Episode", "series_id")):
        requests = [r for r in without_name if r["media_type"] == media_type]
        by_id = {id_without_dashes(k): name for k, name in _item_names(
            key_column, name_column, item_type, [shape for r in requests for shape in id_forms(r["jellyfin_id"])]).items()}
        by_tmdb = _item_names("tmdb_id", name_column, item_type, [r["tmdb_id"] for r in requests if r["tmdb_id"]]) \
            if media_type == "movie" else {}
        for r in requests:
            name = by_id.get(r["jellyfin_id"]) or by_tmdb.get(r["tmdb_id"])
            if name:
                names.append((name, r["id"]))
    if names:
        with db.connect() as conn:
            conn.executemany("UPDATE seerr_request SET title = ? WHERE id = ?", names)
            conn.commit()


# How often the titles Seerr had no poster for (`poster = '-'`) are tried again.
POSTERS_RETRY = timedelta(days=1)
POSTERS_MARK_KEY = "seerr_posters_retry"      # setting: when it was tried last (UTC)
# Tried again are only requests that wait for approval or are not older than this. Only they show a
# poster (the pending card, the latest requests) - and a years-old handled request for a title that
# will never have a poster on TMDB would otherwise ask every day for ever.
POSTERS_RETRY_MAX_AGE = timedelta(days=90)
PENDING = 1                                    # the request's state: waiting for approval


def _posters_due() -> bool:
    """Has a day passed since the last try of titles without a poster? The first time (no mark) yes."""
    try:
        age = datetime.strptime(db.utcnow(), db.TIME_FORMAT) - datetime.strptime(
            db.get_setting(POSTERS_MARK_KEY, "") or "", db.TIME_FORMAT)
    except ValueError:
        return True
    return age >= POSTERS_RETRY or age < timedelta(0)


def _titles_to_look_up(without_poster_too: bool = False) -> list[tuple[str, str]]:
    """(media type, TMDB id) of the titles Seerr needs to be asked about.

    Either the request has no name yet, or it has no poster path and the title is not in the library
    (a title from the library has its poster from Jellyfin and needs no path). The poster path is
    found out whether Seerr serves images right now or not: so it is ready when somebody switches them
    on. `'-'` = Seerr had no poster: such a title is normally not asked about again, with
    `without_poster_too` it is (once a day - TMDB often adds a poster to a new title later), but only
    for requests that wait or are not older than POSTERS_RETRY_MAX_AGE.
    """
    poster_missing, params = "poster = ''", ()
    if without_poster_too:
        since = (datetime.strptime(db.utcnow(), db.TIME_FORMAT) - POSTERS_RETRY_MAX_AGE).strftime(db.TIME_FORMAT)
        poster_missing, params = "(poster = '' OR (poster = '-' AND (status = ? OR created_at >= ?)))", (PENDING, since)
    rows = db.query_all("SELECT DISTINCT media_type, tmdb_id, jellyfin_id, title, poster FROM seerr_request"
                        f" WHERE tmdb_id <> '' AND (title = '' OR {poster_missing})", params)
    in_library = stats.seerr_posters((r["media_type"], r["jellyfin_id"], r["tmdb_id"]) for r in rows)
    return sorted({(r["media_type"], r["tmdb_id"]) for r in rows
                   if r["title"] == "" or (r["media_type"], r["jellyfin_id"], r["tmdb_id"]) not in in_library})


def _save_titles(found: list[tuple[str, str, dict[str, str]]]) -> None:
    """Write the looked-up names (only where missing) and poster paths (only where there are none yet).

    A found path overwrites the dash too ("Seerr had no poster"); where there still is no poster, a
    dash is written only in place of an empty one.
    """
    if not found:
        return
    with db.connect() as conn:
        conn.executemany("UPDATE seerr_request SET title = ? WHERE media_type = ? AND tmdb_id = ? AND title = ''",
                         [(title["name"], media_type, tmdb_id) for media_type, tmdb_id, title in found if title["name"]])
        conn.executemany("UPDATE seerr_request SET poster = ? WHERE media_type = ? AND tmdb_id = ? AND poster IN ('', '-')",
                         [(title["poster"], media_type, tmdb_id) for media_type, tmdb_id, title in found if title["poster"]])
        conn.executemany("UPDATE seerr_request SET poster = '-' WHERE media_type = ? AND tmdb_id = ? AND poster = ''",
                         [(media_type, tmdb_id) for media_type, tmdb_id, title in found if not title["poster"]])
        conn.commit()


async def _titles_from_seerr(client: SeerrClient) -> None:
    """The remaining names and poster paths from Seerr - all at once, at most CONCURRENT at a time.

    A failed look-up (TMDB unavailable, 404) leaves both empty; the next sync tries again and this one
    does not go down. One line with the count goes to the log, not a line per title - a title TMDB
    does not know would remind of itself every hour for ever otherwise.

    Once a day the titles Seerr had no poster for join them (_posters_due). The mark moves only when
    at least something was found (or there was nothing to look for): a try that failed as a whole on
    an outage of Seerr or TMDB is repeated on the next sync, not in a day.
    """
    again = await asyncio.to_thread(_posters_due)
    titles = await asyncio.to_thread(_titles_to_look_up, again)
    lock = asyncio.Semaphore(CONCURRENT)

    async def one(media_type: str, tmdb_id: str) -> tuple[str, str, dict[str, str] | None]:
        async with lock:
            try:
                return media_type, tmdb_id, await client.title_info(media_type, tmdb_id)
            except SeerrError as error:
                log.debug("could not look up the title of %s %s: %s", media_type, tmdb_id, error)
                return media_type, tmdb_id, None

    results = await asyncio.gather(*(one(media_type, tmdb_id) for media_type, tmdb_id in titles))
    await asyncio.to_thread(_save_titles, [r for r in results if r[2] is not None])
    if again and (not results or any(r[2] is not None for r in results)):
        await asyncio.to_thread(db.set_setting, POSTERS_MARK_KEY, db.utcnow())
    not_found = sum(r[2] is None for r in results)
    if not_found:
        log.info("%s titles could not be looked up in Seerr; will retry next time", not_found)


def request_count() -> int:
    """How many requests are stored (those deleted in Seerr too)."""
    return int(db.query_value("SELECT COUNT(*) FROM seerr_request") or 0)


def _write_state(result: str, new: int, error: Exception | None = None) -> None:
    write_state("seerr_state", result, new, error)       # the shared writer: services.write_state


def sync_state() -> dict[str, Any]:
    """The last sync: when (UTC), the result ("ok" / "error" / ""), how many arrived, the error (translated)."""
    return read_state("seerr_state")


async def _walk(client: SeerrClient, now: str) -> tuple[int, set[int]]:
    """All pages of requests, each written at once (conversion and writing in a thread)."""
    new, seen = 0, set()
    for page in range(MAX_PAGES):
        # A long timeout: a page of a hundred requests with titles and requesters.
        answer = await client.requests_page(page * PAGE_SIZE, PAGE_SIZE, timeout=DEFAULT_TIMEOUT)
        records = answer.get("results")
        records = records if isinstance(records, list) else []
        rows = await asyncio.to_thread(_page_rows, records)
        new += await asyncio.to_thread(_write_page, rows, now)
        seen |= {r["id"] for r in rows}
        total = integer((answer.get("pageInfo") or {}).get("results"))
        if len(records) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= total:
            break
    return new, seen


async def _fetch(transport: httpx.AsyncBaseTransport | None) -> dict[str, int]:
    url, key = db.get_setting("seerr_url", ""), db.get_setting("seerr_api_key", "")
    if not url or not key:
        raise SeerrError("{name} has no address or API key filled in.", name=NAME)
    now = db.utcnow()
    async with SeerrClient(url, key, transport=transport) as client:
        new, seen = await _walk(client, now)
        deleted = await _check_missing(client, seen, now)
        await asyncio.to_thread(_library_names)
        await _titles_from_seerr(client)
    # A title that has arrived in the library meanwhile has its poster from Jellyfin: the provisional
    # one (from Radarr, Sonarr or Seerr) is not needed any more.
    await asyncio.to_thread(posters.clean_up_posters)
    # And whether Seerr serves images ("Enable Image Caching") - for the line in Settings and the hint
    # at the cards. A failed request does not bring the sync down.
    await posters.check_images(transport)
    _write_state("ok", new)
    log.info("%s sync: %s new requests of %s, %s deleted", NAME, new, len(seen), deleted)
    return {"new_count": new, "total": len(seen), "deleted_count": deleted}


async def synchronize(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, int]:
    """Walk all of Seerr's requests. Returns {"new_count", "total", "deleted_count"}.

    An error is written into the state (without the key) and the exception goes on; a concurrent sync
    (the button and the task) is refused with "already running".
    """
    if "seerr" in _RUNNING:
        raise SeerrError(ALREADY_RUNNING, name=NAME)
    _RUNNING.add("seerr")
    try:
        return await _fetch(transport)
    except SeerrError as error:
        _write_state("error", 0, error)
        raise
    except Exception as error:
        _write_state("error", 0, SeerrError("Sync failed: {reason}", reason=i18n.error_text(error)))
        raise
    finally:
        _RUNNING.discard("seerr")


def sync_running() -> bool:
    """Is a sync (or a deletion) running now? A second action is refused."""
    return "seerr" in _RUNNING


SYNC_RUNNING = "{name} sync is running right now, the downloaded data cannot be deleted yet. Try again in a moment."


def _forget_posters() -> int:
    """Delete the provisional posters of the requests. Returns the count.

    Only movies and series, and not those Radarr or Sonarr know: the same file is the poster of their card
    "Stahuje se" and their sync deletes it once the title is in the library. The covers of Lidarr's albums and
    Readarr's books are never Seerr's; the images from Jellyfin stay too.
    """
    kept = {("movie", tmdb_id) for tmdb_id in radarr.own_posters()} | {("tv", tmdb_id) for tmdb_id in sonarr.own_posters()}
    return sum(images.forget_title(kind, tmdb_id) for kind, tmdb_id in images.titles_on_disk()
               if kind in posters.APPS and (kind, tmdb_id) not in kept)


def _delete() -> int:
    """Delete the requests and the state. Called only under the lock `_RUNNING`."""
    count = request_count()
    with db.connect() as conn:
        conn.execute("DELETE FROM seerr_request")
        conn.commit()
    db.set_setting("seerr_state", "")
    db.set_setting(POSTERS_MARK_KEY, "")
    _forget_posters()
    posters.forget_missing()
    log.info("deleted the stored requests of %s: %s", NAME, count)
    return count


def delete_data() -> int:
    """Delete the stored requests and the state. Returns the count. Refused during a sync.

    The requests in Seerr stay; the next sync (with Seerr connected) fetches again those still there -
    the ones deleted in Seerr do not come back.

    For calls outside the asyncio loop (scripts, tests). The web calls `delete_downloaded_data`, which
    takes the lock for the time of the deletion.
    """
    if sync_running():
        raise SeerrError(SYNC_RUNNING, name=NAME)
    return _delete()


async def delete_downloaded_data() -> int:
    """Delete the stored requests in a thread and hold the sync's lock for that time.

    The check and the locking happen in the asyncio loop without an `await` between them. Without the
    lock a sync started during the deletion would write part of the requests into the table the
    deletion is just emptying.
    """
    if sync_running():
        raise SeerrError(SYNC_RUNNING, name=NAME)
    _RUNNING.add("seerr")
    try:
        return await asyncio.to_thread(_delete)
    finally:
        _RUNNING.discard("seerr")
