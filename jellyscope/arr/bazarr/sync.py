"""The sync of the subtitle history from Bazarr into the table `bazarr_event`.

Bazarr cannot return "only newer than..." and its records have no number. So the history is read
page by page, newest first:

* **The first run** (until one has run through - the mark `arr_bazarr_last_sync` is empty) goes
  through every page.
* **Further runs** end at the first page that had nothing new.
* **Before a further run writes its first new record, it deletes the mark** and sets it only after a
  success. If it did not finish (an error, a crash, a shutdown), the newer pages are stored and the
  older ones not - the next run would end at the first page without anything new and the hole would
  stay. Without the mark it reads the whole history.

The pages **overlap** by a few records (`OVERLAP`): when Bazarr shortens the history between two
requests (a deleted series takes its records with it), the rest moves up and without the overlap the
same number of records would be skipped.

A record is recognised by its **fingerprint** (`fingerprint`): what happened to which episode or
movie, in what language, from whom and when. The write is an upsert by it, so nothing doubles, not
even when the pages move during the read (a new record arrived) or when a run was interrupted and is
read again. "When" is **the text of the time from Bazarr**, not its conversion to UTC: Bazarr stores
the local time without a zone, so the text of an old event stays the same, while the conversion
changes as soon as Bazarr starts reporting another zone (the container's TZ fixed) - a fingerprint
from the conversion would then store the whole history a second time. For the same reason an exact
time once stored is never rewritten. Two really different events with the same data in the same
second (two timing syncs of the same episode) have on the page the fingerprint of the first and
"the second such one" - Bazarr keeps them as two records.

Bazarr returns the event's **time** only as text in its system's format (bazarr/times.py). The format
is recognised from the page - and where the page itself does not decide, the one from the previous
page holds; the zone is from Bazarr's status. A record whose time cannot be read (or lies in the
future) stays without an exact time by itself: from the first run without a date (it then counts into
no period), from further runs with the time Jellyscope first saw it (`time_exact = 0`). The state
says so (`without_time`).

It is a copy: what Bazarr deletes from its history or stops returning (a title removed from Sonarr)
stays here. An app error deletes nothing and does not move the mark.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from collections import Counter
from datetime import datetime
from typing import Any

import httpx

from ... import db, i18n
from ...services import SYNC_ALREADY_RUNNING as ALREADY_RUNNING
from ...services import COUNT_TIMEOUT, integer, read_state, settings_fingerprint, write_state
from .client import NAME, BazarrClient, BazarrError
from .live import LANGUAGES_KEY, MISSING_LIMIT, _languages
from .times import FUTURE_MARGIN, detect_format, to_utc

log = logging.getLogger("jellyscope.arr")

PAGE_SIZE = 500            # history records per request
OVERLAP = 10               # by how many records a full page overlaps the previous one
MAX_PAGES = 10_000         # a safeguard against endless paging
MARK_KEY = "arr_bazarr_last_sync"
STATE_KEY = "arr_bazarr_state"
KINDS = ("episode", "movie")
# The kind as it goes into the fingerprint: the Czech word of the code before 1.9.0. The fingerprint is the table's
# primary key and nothing it is computed from can be computed again (it holds fields that are not stored) - a new
# word here would store every event again at the next sync after the conversion to English names.
FINGERPRINT_KIND = {"episode": "dil", "movie": "film"}
SYNC_RUNNING = "{name} sync is running right now, the downloaded data cannot be deleted yet. Try again in a moment."
COLUMNS = ("fingerprint", "kind", "title", "episode", "title_id", "item_id", "action", "language", "hi", "forced",
           "provider", "score", "happened_at", "time_exact", "synced_at")
# An upsert by the fingerprint. The time is never rewritten, only an exact one is filled in where there was
# none: a row that got the time it was first seen would otherwise "get younger" with every run, and an
# exact time would be recalculated after a change of Bazarr's zone by a zone the event did not happen in.
_FILL_IN = "excluded.time_exact = 1 AND bazarr_event.time_exact = 0"
UPSERT = (f"INSERT INTO bazarr_event ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (fingerprint) DO UPDATE SET title = excluded.title, synced_at = excluded.synced_at,"
          f" happened_at = CASE WHEN {_FILL_IN} THEN excluded.happened_at ELSE bazarr_event.happened_at END,"
          f" time_exact = CASE WHEN {_FILL_IN} THEN 1 ELSE bazarr_event.time_exact END")
_EPISODE = re.compile(r"(\d+)x(\d+)")
_SCORE = re.compile(r"(\d+(?:\.\d+)?)\s*%?")
# Is a sync (or a deletion) running? A set with at most one item: it changes without `global` and tests set it from outside.
_RUNNING: set[str] = set()


def sync_running() -> bool:
    """Is a sync (or a deletion of the downloaded data) running right now?"""
    return NAME in _RUNNING


def _score(value: Any) -> float | None:
    """The match in percent from the text "93.5%". What is no number in the range 0-100 is None."""
    match = _SCORE.fullmatch(str(value).strip()) if value not in (None, "") else None
    number = float(match.group(1)) if match else None
    return number if number is not None and 0 <= number <= 100 else None


def event_from_history(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    """A record of Bazarr's history as a row of the table - without the time (`time_text` is its text) and without the fingerprint.

    Another app's answer is input like any other: what is missing or makes no sense is empty (the
    language, the provider), None (the score, numbers) or -1 (an unknown action) - the record is
    always stored.
    """
    language = record.get("language") if isinstance(record.get("language"), dict) else {}
    episode = _EPISODE.fullmatch(str(record.get("episode_number") or "")) if kind == "episode" else None
    item = integer(record.get("sonarrEpisodeId" if kind == "episode" else "radarrId"), None)
    return {
        "kind": kind,
        "title": str(record.get("seriesTitle" if kind == "episode" else "title") or ""),
        "episode": f"S{int(episode.group(1)):02d}E{int(episode.group(2)):02d}" if episode else "",
        "title_id": integer(record.get("sonarrSeriesId"), None) if kind == "episode" else item,
        "item_id": item,
        "action": integer(record.get("action"), -1),
        "language": str(language.get("code2") or language.get("code3") or language.get("name") or ""),
        "hi": 1 if language.get("hi") is True else 0,
        "forced": 1 if language.get("forced") is True else 0,
        "provider": str(record.get("provider") or ""),
        "score": _score(record.get("score")),
        "subs_id": str(record.get("subs_id") or ""),
        "time_text": str(record.get("parsed_timestamp") or ""),
    }


def fingerprint(event: dict[str, Any], nth: int = 1) -> str:
    """What a record is recognised by when it has no number: what, with what, in which language, from whom and when (SHA-1).

    "When" is the text of the time as Bazarr sent it - that does not change for an event, not even
    when Bazarr's zone changes (see the module's introduction). `nth` tells the second and further
    record with the same data on the same page apart.
    """
    by = (FINGERPRINT_KIND[event["kind"]], event["item_id"], event["action"], event["language"], event["hi"], event["forced"],
          event["provider"], event["subs_id"], event["time_text"], *([nth] if nth > 1 else []))
    return hashlib.sha1("|".join(str(c) for c in by).encode(), usedforsecurity=False).hexdigest()


def _rows(kind: str, records: list[dict[str, Any]], zone: str, first_run: bool, now: str,
          preferred: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """A page of history as rows of the table. Returns (rows, the time format - None when not recognised).

    The format is recognised from the page; `preferred` is the format recognised earlier in the same
    run - it decides an ambiguous page and holds for a page that gives no format by itself too. A
    record without a readable time (or with a time in the future) stays without an exact time by
    itself: in the first run without a date (the old history would otherwise get today and make a
    column in the chart), in further ones with the time it was first seen.
    """
    events = [event_from_history(kind, r) for r in records]
    current = datetime.strptime(now, db.TIME_FORMAT)
    time_format = detect_format([e["time_text"] for e in events], current, preferred) or preferred
    # In UTC and with another zone too: two days of margin instead of one.
    latest = (current + 2 * FUTURE_MARGIN).strftime(db.TIME_FORMAT)
    same: Counter[str] = Counter()
    rows = []
    for event in events:
        date = to_utc(event["time_text"], time_format, zone) if time_format else None
        if date and date > latest:
            date = None
        base = fingerprint(event)
        same[base] += 1
        # The second and further same record on a page has a fingerprint of its own. The order holds only within a page:
        # a group of same records longer than the pages' overlap may be stored a few shorter, never longer.
        rows.append({**event, "fingerprint": base if same[base] == 1 else fingerprint(event, same[base]), "synced_at": now,
                     "happened_at": date if date else (None if first_run else now), "time_exact": 1 if date else 0})
    return rows, time_format


def _write(rows: list[dict[str, Any]], before_new: Callable[[], None] | None = None) -> int:
    """An upsert in one transaction. Returns how many rows were added (not rewritten).

    `before_new` is called right before the write when there is at least one new row among the rows -
    see `_mark_in_progress`.
    """
    if not rows:
        return 0
    fingerprints = [r["fingerprint"] for r in rows]
    known = {r["fingerprint"] for r in db.query_all_in("SELECT fingerprint FROM bazarr_event WHERE fingerprint IN ({})", fingerprints)}
    new = len(set(fingerprints) - known)
    if new and before_new:
        before_new()
    with db.connect() as conn:
        conn.executemany(UPSERT, [tuple(r[c] for c in COLUMNS) for r in rows])
        conn.commit()
    return new


def _mark_in_progress() -> None:
    """Delete the sync mark - called before a further run writes its first new record.

    From that moment "the history is not whole" holds: when the run does not finish (an app error, a
    crash of the process, a shutdown in the middle), the next one reads everything. A successful run
    sets the mark again at its end. A run that found nothing new does not touch it - a Bazarr that does
    not answer for an hour starts no read of the whole history.
    """
    if db.get_setting(MARK_KEY, ""):
        db.set_setting(MARK_KEY, "")


def event_count() -> int:
    """How many history records are downloaded."""
    return int(db.query_value("SELECT COUNT(*) FROM bazarr_event") or 0)


def sync_state() -> dict[str, Any]:
    """The last sync (as with *Arr) and `without_time`: are there stored records whose time could not be read?"""
    without_time = db.query_value("SELECT 1 FROM bazarr_event WHERE time_exact = 0 LIMIT 1")
    return {**read_state(STATE_KEY), "without_time": bool(without_time)}


async def _history(client: BazarrClient, kind: str, zone: str, first_run: bool, now: str, progress: dict[str, Any]) -> None:
    """Go through the history of one kind. After every page add to `progress` how many were added and how many
    were read (`new_count`, `total` - without the records of the pages' overlap), note when a record stayed
    without an exact time (`recognised`), and remember the last recognised format (`format`) for further pages.

    It ends with an empty page, with the end of the history by `total` (when Bazarr sent it), in further
    runs with the first page without anything new - and with a page that is the same as the one before
    it (a server that does not page and sends no count would otherwise hold the read up to
    `MAX_PAGES`). A full page overlaps the next one by `OVERLAP` records.
    """
    start = overlap = 0
    previous: set[str] = set()
    for _ in range(MAX_PAGES):
        page = await client.history(kind, start, PAGE_SIZE)
        records = page["data"]
        if not records:
            break
        # Conversion and writing are blocking work - in a thread, so the web runs meanwhile.
        rows, time_format = await asyncio.to_thread(_rows, kind, records, zone, first_run, now, progress["format"])
        fingerprints = {r["fingerprint"] for r in rows}
        if fingerprints == previous:
            break
        previous = fingerprints
        added = await asyncio.to_thread(_write, rows, None if first_run else _mark_in_progress)
        progress["new_count"] += added
        progress["total"] += max(0, len(rows) - overlap)
        progress["recognised"] = progress["recognised"] and all(r["time_exact"] for r in rows)
        progress["format"] = time_format or progress["format"]
        end = start + len(records)
        if (page["total"] is not None and end >= page["total"]) or (not first_run and added == 0):
            break
        overlap = min(OVERLAP, PAGE_SIZE // 2) if len(records) >= PAGE_SIZE else 0
        start = end - overlap


async def _store_missing_languages(client: BazarrClient, fingerprint: str) -> None:
    """The languages episodes and movies miss subtitles in, for the card of missing subtitles (live.LANGUAGES_KEY).

    Two big lists (thousands of items): read here with a long limit, not on every page view. A list that does not
    come keeps the last stored languages - the sync goes on, a warning goes to the log.
    """
    try:
        pages = await asyncio.gather(client.missing("episode", MISSING_LIMIT, timeout=COUNT_TIMEOUT),
                                     client.missing("movie", MISSING_LIMIT, timeout=COUNT_TIMEOUT))
    except (httpx.HTTPError, BazarrError, ValueError) as error:
        log.warning("bazarr: could not read the missing subtitles, the last known languages stay: %s",
                    type(error).__name__)
        return
    languages, incomplete = _languages(list(pages))
    db.set_setting(LANGUAGES_KEY, json.dumps({"fingerprint": fingerprint, "languages": languages,
                                              "incomplete": incomplete, "at": db.utcnow()}, ensure_ascii=False))


async def refresh_counts(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Read the lists of the missing and store the languages for the card of missing subtitles.

    Called by the *Arr sync task after the history (registry.synchronize_all), in the background - not by "Sync now"
    in Settings, which waits for its result: two big lists take long (23. 11. 2026).
    """
    url, key = db.get_setting("arr_bazarr_url", ""), db.get_setting("arr_bazarr_api_key", "")
    if not url or not key:
        return
    async with BazarrClient(url, key, transport=transport) as client:
        await _store_missing_languages(client, settings_fingerprint(url, key))


async def _fetch(transport: httpx.AsyncBaseTransport | None) -> dict[str, int]:
    url, key = db.get_setting("arr_bazarr_url", ""), db.get_setting("arr_bazarr_api_key", "")
    if not url or not key:
        raise BazarrError("{name} has no address or API key filled in.", name=NAME)
    now = db.utcnow()
    first_run = not db.get_setting(MARK_KEY, "")
    progress: dict[str, Any] = {"new_count": 0, "total": 0, "recognised": True, "format": None}
    async with BazarrClient(url, key, transport=transport) as client:
        zone = str((await client.status()).get("timezone") or "")
        for kind in KINDS:
            await _history(client, kind, zone, first_run, now, progress)
    if not progress["recognised"]:
        # At least one record of this run stayed without an exact time (on a page with an inherited format too).
        log.warning("bazarr: the time format in the history was not recognised, records stay without an exact time")
    db.set_setting(MARK_KEY, now)
    write_state(STATE_KEY, "ok", progress["new_count"])
    log.info("%s sync: %s new records of %s", NAME, progress["new_count"], progress["total"])
    return {"new_count": progress["new_count"], "total": progress["total"]}


async def synchronize(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, int]:
    """Fetch the new subtitle history. Returns {"new_count", "total"}.

    An error is written into the state (and the exception flies on); the sync mark does not move with
    it - and a run that already wrote something new has it deleted (`_mark_in_progress`) - so the next
    run reads what did not get read this time too.
    """
    if NAME in _RUNNING:
        raise BazarrError(ALREADY_RUNNING, name=NAME)
    _RUNNING.add(NAME)
    try:
        return await _fetch(transport)
    except BazarrError as error:
        write_state(STATE_KEY, "error", 0, error)
        raise
    except Exception as error:
        write_state(STATE_KEY, "error", 0, BazarrError("Sync failed: {reason}", reason=i18n.error_text(error)))
        raise
    finally:
        _RUNNING.discard(NAME)


def _delete() -> int:
    """Delete the rows, the sync mark and the state. Called only under the `_RUNNING` lock."""
    count = event_count()
    with db.connect() as conn:
        conn.execute("DELETE FROM bazarr_event")
        conn.commit()
    db.set_setting(MARK_KEY, "")
    db.set_setting(STATE_KEY, "")
    log.info("deleted the downloaded history of %s: %s records", NAME, count)
    return count


def delete_data() -> int:
    """Delete the downloaded subtitle history. Returns the count of deleted rows.

    It deletes the mark and the state too: the next sync (if Bazarr is connected) fetches the whole
    history again - it stayed in Bazarr, this was its copy. For calls outside the asyncio loop
    (scripts, tests); the web calls `delete_downloaded_data`, which locks the sync for the time of the
    deletion.
    """
    if NAME in _RUNNING:
        raise BazarrError(SYNC_RUNNING, name=NAME)
    return _delete()


async def delete_downloaded_data() -> int:
    """Delete the downloaded history in a thread and hold the sync lock meanwhile (as with *Arr)."""
    if NAME in _RUNNING:
        raise BazarrError(SYNC_RUNNING, name=NAME)
    _RUNNING.add(NAME)
    try:
        return await asyncio.to_thread(_delete)
    finally:
        _RUNNING.discard(NAME)
