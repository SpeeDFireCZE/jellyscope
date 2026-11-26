"""The sync of Prowlarr's history: hourly sums of the queries and the list of grabbed releases.

Prowlarr writes every query to an indexer into its history - tens of thousands of records a day. So
Jellyscope does not store them one by one: it builds **sums by hour**, indexer, app and kind out of them
(`prowlarr_hour`). Only the grabbed releases (`prowlarr_grab`) and the indexers' names
(`prowlarr_indexer`) are stored one by one.

A sum cannot be fixed like a row: what is added once stays there. So everything here guards a single
thing - that no event is counted twice and none is left out:

* Every event has a **number** in Prowlarr and the numbers only grow. Jellyscope remembers the number
  of the newest counted event (`arr_prowlarr_last_id`) and counts only events with a higher number.
* The history is read **from the newest** page by page, until a page comes on which there is nothing
  new any more (or an empty one - the end of the history). Prowlarr deletes old history by itself: if
  it were read from the oldest, a deletion during the read would move all the further pages and a
  piece of the history would be skipped. This way new records only move a page by a few places -
  repeated records are recognised by their number, against everything the run has already seen.
* Whether nothing is missing between two pages is told like this: a page that **overlaps** with what
  the run has already seen surely follows the previous one. A page without an overlap follows if **the
  count of all events did not drop** (`totalRecords`). When it dropped and there is no overlap,
  something disappeared from the history and a piece may have been skipped: the whole is read again
  (`ATTEMPTS`). An exact equality of the counts is not checked - Prowlarr finds the count separately,
  after the page's records, so it routinely differs by an event added meanwhile.
* **Nothing is written** during the read. The sums are built in memory and at the end they are
  written in **one transaction** together with the new number of the last event and with the
  indexers. An interrupted run (a connection error, a crash, a shutdown) leaves nothing behind - the
  next one starts where the last successful one ended.
* In that transaction the mark moves **only from the value the run started with**. Two processes over
  one database (two Jellyscope instances) so do not both add the same events: the second one finds
  out that someone else moved the mark meanwhile and writes nothing.
* A history that cannot be read reliably (the server keeps returning the same, has more pages than are
  read, does not sort from the newest) is an error - not a partial write. A mark moved past an unread
  piece would throw it away for good.
* Old events with a time in the future (Prowlarr's clock was ahead and then went back) lie above the
  new ones in the sorting. So a page full of such old events does not end the read.
* An event of an unknown kind is skipped and the mark goes on. A run in which there are unknown kinds
  and **nothing countable**, though, does not move the mark: if a future Prowlarr renamed the kinds,
  the history must not be thrown away silently. From `UNKNOWN_ERROR_AT` such events it is an error the
  admin notices. A kind skipped on purpose (`SKIPPED`) and a record with an unreadable time are not
  unknown - the mark always goes past those.

After Prowlarr is reinstalled (or an older backup of it restored) the numbers start again from a
smaller one. It is recognised by the time: an event newer than everything counted
(`arr_prowlarr_last_event_at`), and yet with a number below the mark - in one history that cannot
happen. Such a run counts events by time (everything newer than the last counted one) and sets the
number mark anew. **The indexers' numbers** start again then too: an indexer under whose number there
is now another name gets a (negative) number of its own and stays with its name and sums as removed -
the history of one indexer must not show under the name of another. The same holds when another one
later appears under the number of a removed indexer.

It is a copy: what Prowlarr deletes from its history stays here. What it deleted before Jellyscope
managed to read it (the sync did not run longer than Prowlarr keeps the history) cannot be caught up
on. An app error deletes nothing and does not move the marks.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import db, i18n
from ...services import SYNC_ALREADY_RUNNING as ALREADY_RUNNING
from ...services import utc_time, integer, read_state, write_state
from .client import KINDS, NAME, ProwlarrClient, ProwlarrError
from .samples import RUN

log = logging.getLogger("jellyscope.arr")

PAGE_SIZE = 1000           # history events per request
MAX_PAGES = 10_000         # a safeguard against endless paging
ATTEMPTS = 3               # how many times the read is tried when the history changes under it
NO_PROGRESS_LIMIT = 3      # how many pages in a row may consist only of records already seen (then the server does not page)
UNKNOWN_ERROR_AT = 100     # from how many events of an unknown kind (and no known one) the run is an error
# How far an event's time may be ahead of Jellyscope's clock and still not count as "in the future"
# (the clocks of two machines routinely differ by a few minutes).
FUTURE_FROM = timedelta(hours=1)
MARK_KEY = "arr_prowlarr_last_id"        # the number of the newest counted event
TIME_MARK_KEY = "arr_prowlarr_last_event_at"  # its time (UTC) - a history with new numbering is recognised by it
COUNT_KEY = "arr_prowlarr_counted"             # how many events are counted (so it is not summed from the tables on every view)
STATE_KEY = "arr_prowlarr_state"
SYNC_RUNNING = "{name} sync is running right now, the downloaded data cannot be deleted yet. Try again in a moment."
UNREADABLE = "The history of {name} could not be read reliably. Nothing was written; the next sync will try again."
ONLY_UNKNOWN = ("{name} sends only events Jellyscope does not know ({n}×). A newer version of Jellyscope is probably needed; "
                "nothing is being counted for now.")
# Bounds for values from another app: nonsense must not overflow a PostgreSQL column (and so stop the sync
# for good) nor inflate a sum's key.
MAX_NUMBER = 2**31 - 1
MAX_RESPONSE_MS = 600_000  # ms; a longer "response time" is no response time
GRAB_METHODS = {"proxy": "proxy", "redirect": "redirect"}
# Event kinds Jellyscope knows and does not count (they say nothing about queries or releases).
SKIPPED = ("indexerInfo", "unknown")
ADD_UP = ("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (hour, indexer_id, app, kind) DO UPDATE SET"
          " event_count = prowlarr_hour.event_count + excluded.event_count, failed_count = prowlarr_hour.failed_count + excluded.failed_count,"
          " response_sum = prowlarr_hour.response_sum + excluded.response_sum,"
          " response_count = prowlarr_hour.response_count + excluded.response_count,"
          " with_results = prowlarr_hour.with_results + excluded.with_results")
GRAB_COLUMNS = ("id", "happened_at", "indexer_id", "app", "title", "method", "succeeded", "response", "client")
STORE_GRAB = (f"INSERT INTO prowlarr_grab ({', '.join(GRAB_COLUMNS)}) VALUES ({', '.join('?' * len(GRAB_COLUMNS))})"
              " ON CONFLICT (id, happened_at) DO NOTHING")
STORE_INDEXER = ("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at) VALUES (?, ?, ?, ?, ?, 0, ?)"
                 " ON CONFLICT (id) DO UPDATE SET name = excluded.name, protocol = excluded.protocol, privacy = excluded.privacy,"
                 " enabled = excluded.enabled, removed = 0, seen_at = excluded.seen_at")
# The marks are written directly (not through db.set_setting): they must be in the same transaction as the sums.
MARK_UPSERT = "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value"
# The number of the last event moves only from the value the run started with (see `_move_mark`).
MARK_FROM_EMPTY = ("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value"
                   " WHERE settings.value IS NULL OR settings.value = ''")
MARK_FROM_VALUE = "UPDATE settings SET value = ? WHERE key = ? AND value = ?"
# Is a sync (or a deletion) running? A set with at most one item: it changes without `global` and tests set it from outside.
_RUNNING: set[str] = set()


class _HistoryChanged(Exception):
    """Something disappeared from the history between two pages so that a part may have been skipped - the read repeats."""


class _Unreadable(Exception):
    """The history cannot be read reliably (the reason is a text for the log) - repeating makes no sense, nothing is written."""


class _OtherProcess(Exception):
    """Someone else moved the number of the last event meanwhile (a second process over the same database)."""


def sync_running() -> bool:
    """Is a sync (or a deletion of the downloaded data) running right now?"""
    return NAME in _RUNNING


def _number(value: Any) -> int | None:
    """The number of an event or an indexer: a positive integer that fits the column. Anything else None."""
    number = None if isinstance(value, bool) else integer(value, None)
    return number if number is not None and 0 < number <= MAX_NUMBER else None


def event_from_history(record: dict[str, Any]) -> dict[str, Any] | None:
    """A record of Prowlarr's history -> an event as Jellyscope counts it. None = skip.

    An unknown event kind (`client.KINDS`) and a record without a readable number or time are skipped.
    Prowlarr sends the numbers in `data` as texts (`"elapsedTime": "2"`). `response` is None when Prowlarr
    does not know it - always for a grabbed release where it only redirected the app to the indexer
    (`method` = redirect); it does not know the result of such a grab either and writes it as
    successful.
    """
    kind = KINDS.get(str(record.get("eventType") or ""))
    number, date = _number(record.get("id")), utc_time(record.get("date"))
    if kind is None or number is None or date is None:
        return None
    data = record.get("data")
    data = data if isinstance(data, dict) else {}
    response, results = integer(data.get("elapsedTime"), None), integer(data.get("queryResults"), None)
    return {"id": number, "kind": kind, "happened_at": date, "indexer_id": _number(record.get("indexerId")) or 0,
            "app": str(data.get("source") or "").strip()[:60], "succeeded": bool(record.get("successful")),
            "response": response if response is not None and 0 <= response <= MAX_RESPONSE_MS else None,
            "from_cache": str(data.get("cached") or "").lower() in ("1", "true"),
            "result_count": results if results is not None and results >= 0 else None,
            "title": str(data.get("grabTitle") or "")[:300], "method": GRAB_METHODS.get(str(data.get("grabMethod") or "").lower(), ""),
            "client": str(data.get("downloadClientName") or "")[:100]}


def _add_up(progress: dict[str, Any], event: dict[str, Any]) -> None:
    """Add an event to what the run has gathered so far: a grabbed release to the list, the others to the sum of their hour.

    Only successful queries that did not come from Prowlarr's cache count into the response time (the
    cache answers at once and would skew the average); "found something" only successful ones with at
    least one release.
    """
    if event["kind"] == "grabbed":
        progress["grabbed_by_app"][event["id"], event["happened_at"]] = tuple(
            int(event[c]) if c == "succeeded" else event[c] for c in GRAB_COLUMNS)
        return
    total = progress["sums"].setdefault((event["happened_at"][:13] + ":00:00", event["indexer_id"], event["app"], event["kind"]),
                                          [0, 0, 0, 0, 0])
    total[0] += 1
    if not event["succeeded"]:
        total[1] += 1
        return
    if event["response"] is not None and not event["from_cache"]:
        total[2] += event["response"]
        total[3] += 1
    if (event["result_count"] or 0) > 0:
        total[4] += 1


def _process(records: list[dict[str, Any]], since: dict[str, Any], seen: set[int], progress: dict[str, Any]) -> dict[str, Any]:
    """Go through one page and add the new events to `progress`. Returns what the read needs to know about the page:
    `overlap` (does it overlap with what the run has already seen?), `unseen_count` (how many records the run has not seen
    yet), `newer_count` (how many are newer than the mark - the seen ones included), `first` and `last` (the time of the
    first and the last record, None without a readable time).

    "Newer than the mark" is by number (`since["id"]`); in a run after renumbering (`since["by_time"]`) by time
    (`since["time"]`). In a run by numbers it notices an event that is newer by time than everything counted and yet has a
    number below the mark: in one history that cannot happen (numbers only grow), so the numbers started again
    (`progress["renumbered"]`).
    """
    on_page: set[int] = set()
    overlap = False
    unseen = newer = 0
    times: list[str] = []
    for record in records:
        number = _number(record.get("id"))
        if number is None or number in on_page:
            continue
        on_page.add(number)
        date = utc_time(record.get("date"))
        if date:
            times.append(date)
        later = bool(since["time"]) and date is not None and date > since["time"]
        if not since["by_time"] and later and number <= since["id"]:
            progress["renumbered"] = True
        is_newer = later if since["by_time"] else number > since["id"]
        newer += is_newer
        if number in seen:
            overlap = True     # the page moved (events were added): the run has already seen this record
            continue
        seen.add(number)
        unseen += 1
        if not is_newer:
            continue
        progress["total"] += 1
        progress["id"] = max(progress["id"], number)
        progress["time"] = max(progress["time"], date or "")
        event = event_from_history(record)
        if event is not None:
            progress["new_count"] += 1
            _add_up(progress, event)
        elif (kind := str(record.get("eventType") or "")) not in KINDS and kind not in SKIPPED:
            progress["unknown_count"] += 1
    numbers = [n for n in (_number(r.get("id")) for r in records) if n is not None]
    return {"overlap": overlap, "unseen_count": unseen, "newer_count": newer,
            "first": times[0] if times else None, "last": times[-1] if times else None,
            "first_number": numbers[0] if numbers else None, "last_number": numbers[-1] if numbers else None}


def _oldest_first(s: dict[str, Any], last_before: str | None) -> bool:
    """Is the page (or how it follows the previous one) sorted from the oldest event?

    Told by the times on the page; where the edge times are equal (everything in one second, a single
    record), by the events' numbers - and between two pages without an overlap by the next one
    starting later than the previous one ended.
    """
    if s["first"] and s["last"] and s["first"] != s["last"]:
        return s["first"] < s["last"]
    if s["first_number"] is not None and s["last_number"] is not None and s["first_number"] < s["last_number"]:
        return True
    return bool(not s["overlap"] and last_before and s["first"] and s["first"] > last_before)


async def _read(client: ProwlarrClient, since: dict[str, Any]) -> dict[str, Any]:
    """Read the history from the newest event down to the mark and return what was added. Writes nothing.

    It ends with a page on which there is nothing newer than the mark, and with an empty page (the end
    of the history). A short page is no end: a server that returns fewer records than asked for would
    otherwise cut the history off silently. Neither is a page whose last record has a time in the
    future: those are old events from the time Prowlarr's clock was ahead, and the new ones (with the
    right time) lie below them in the sorting.

    A page follows the previous one when it overlaps with what the run has already seen, or when the
    count of all events did not drop. Without an overlap and with a lower count a part may have been
    skipped - `_HistoryChanged`.
    """
    progress: dict[str, Any] = {"sums": {}, "grabbed_by_app": {}, "id": 0 if since["by_time"] else since["id"], "time": since["time"],
                                "total": 0, "new_count": 0, "unknown_count": 0, "renumbered": False, "by_time": since["by_time"]}
    seen: set[int] = set()
    total_before: int | None = None
    last_before: str | None = None
    no_progress = 0
    future = (datetime.now(UTC) + FUTURE_FROM).strftime(db.TIME_FORMAT)
    for page in range(1, MAX_PAGES + 1):
        answer = await client.history(page, PAGE_SIZE)
        records, total = answer["records"], answer["total"]
        # Converting a page is blocking work - in a thread, so the web runs meanwhile.
        s = await asyncio.to_thread(_process, records, since, seen, progress)
        if _oldest_first(s, last_before):
            # From the oldest: after the first run the mark would stand at the end and nothing new would be found.
            raise _Unreadable("the server does not sort from the newest event")
        if not s["overlap"] and total is not None and total_before is not None and total < total_before:
            raise _HistoryChanged
        in_future = bool(s["last"]) and s["last"] > future
        if progress["renumbered"] or not records or (not s["newer_count"] and not in_future):
            return progress
        no_progress = 0 if s["unseen_count"] else no_progress + 1
        if no_progress >= NO_PROGRESS_LIMIT:
            raise _Unreadable("the server keeps returning the same records")
        total_before, last_before = total, s["last"]
    raise _Unreadable("the history has more pages than are read")


async def _history(client: ProwlarrClient, since: dict[str, Any]) -> dict[str, Any]:
    """What was added to the history since the mark. A read under which the history changed is repeated; after renumbering it reads by time."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            progress = await _read(client, since)
            if progress["renumbered"]:
                log.warning("prowlarr: event numbers started over (a different or restored installation), new events are taken by time")
                progress = await _read(client, {**since, "by_time": True})
            return progress
        except _HistoryChanged:
            log.info("prowlarr: the history changed while being read, reading it again (attempt %s of %s)", attempt, ATTEMPTS)
        except _Unreadable as reason:
            log.warning("prowlarr: the history cannot be read reliably: %s", reason)
            raise ProwlarrError(UNREADABLE, name=NAME) from None
    raise ProwlarrError("The history of {name} kept changing while it was being read. The next sync will try again.", name=NAME)


def _move_mark(conn: Any, original: str, new: int) -> bool:
    """Move the number of the last event only if the database still holds the one the run started with.

    The `_RUNNING` lock holds within one process. Two processes over one database would both read the
    same events and both add them - in the sums that would be for good. This write goes through for
    only one of them: for the other the row does not match (PostgreSQL also waits on it until the
    first transaction ends).
    """
    if original:
        return conn.execute(MARK_FROM_VALUE, (str(new), MARK_KEY, original)).rowcount == 1
    return conn.execute(MARK_FROM_EMPTY, (MARK_KEY, str(new))).rowcount == 1


def _indexer_rows(indexers: list[dict[str, Any]], now: str) -> dict[int, tuple[Any, ...]]:
    """The indexers from Prowlarr as rows of the table: {number: (number, name, protocol, privacy, enabled, seen)}."""
    rows = {}
    for indexer in indexers:
        number = _number(indexer.get("id"))
        if number is not None:
            rows[number] = (number, str(indexer.get("name") or "")[:200], str(indexer.get("protocol") or "")[:20],
                            str(indexer.get("privacy") or "")[:20], 1 if indexer.get("enable") else 0, now)
    return rows


def _set_aside_indexers(conn: Any, rows: dict[int, tuple[Any, ...]], renumbered: bool) -> None:
    """An indexer under whose number Prowlarr now has another one moves with its sums (and samples) under a negative number of its own.

    It happens after Prowlarr is reinstalled (the indexers' numbers start again): without this the old
    sums would get the new indexer's name. "Another one" is an indexer with another name when the run
    recognised a renumbering, or when the stored one was removed already - numbers are not used again
    within one installation. Another name for an indexer that was not removed is an ordinary renaming.
    """
    stored = conn.execute("SELECT id, name, removed FROM prowlarr_indexer WHERE id > 0").fetchall()
    lowest = min([0, *(int(r["id"]) for r in conn.execute("SELECT id FROM prowlarr_indexer WHERE id < 0").fetchall())])
    for r in stored:
        new = rows.get(int(r["id"]))
        same_name = new is not None and str(new[1]).strip().casefold() == str(r["name"]).strip().casefold()
        if new is None or same_name or not (renumbered or r["removed"]):
            continue
        lowest -= 1
        conn.execute("UPDATE prowlarr_hour SET indexer_id = ? WHERE indexer_id = ?", (lowest, r["id"]))
        conn.execute("UPDATE prowlarr_grab SET indexer_id = ? WHERE indexer_id = ?", (lowest, r["id"]))
        conn.execute("UPDATE prowlarr_sample SET indexer_id = ? WHERE indexer_id = ?", (lowest, r["id"]))
        # The samples' lists of the enabled indexers name it by its number between commas (samples.id_list).
        conn.execute("UPDATE prowlarr_sample SET enabled_ids = REPLACE(enabled_ids, ?, ?) WHERE indexer_id = ? AND enabled_ids LIKE ?",
                     (f",{r['id']},", f",{lowest},", RUN, f"%,{r['id']},%"))
        conn.execute("UPDATE prowlarr_indexer SET id = ?, removed = 1 WHERE id = ?", (lowest, r["id"]))


def _summed(conn: Any) -> int:
    """How many events are in the tables: the queries in the sums and the grabbed releases (summed over this connection)."""
    total = conn.execute("SELECT COALESCE(SUM(event_count), 0) AS n FROM prowlarr_hour").fetchone()
    releases = conn.execute("SELECT COUNT(*) AS n FROM prowlarr_grab").fetchone()
    return int(total["n"] or 0) + int(releases["n"] or 0)


def _write(progress: dict[str, Any], since: dict[str, Any], indexers: list[dict[str, Any]], now: str) -> None:
    """One transaction: move the number of the last event, rewrite the indexers, add the sums, store the grabbed
    releases, the time of the last event and the count of counted events.

    If the sums were written and the mark not (a crash between two writes), the next run would add the
    same events a second time - and that could no longer be recognised nor undone. The indexers are here
    too: a run that did not finish must leave nothing behind. An indexer that is no longer in Prowlarr
    stays (`removed = 1`) - the old numbers in the sums would otherwise have no name.
    """
    rows = _indexer_rows(indexers, now)
    with db.connect() as conn:
        if progress["total"] and not _move_mark(conn, since["text"], progress["id"]):
            raise _OtherProcess
        _set_aside_indexers(conn, rows, progress["by_time"])
        conn.execute("UPDATE prowlarr_indexer SET removed = 1")
        if rows:
            conn.executemany(STORE_INDEXER, list(rows.values()))
        if progress["new_count"]:
            # The count counted so far: the stored one, for data from before the counter summed from the tables once
            # (still before the sums are added below).
            stored = conn.execute("SELECT value FROM settings WHERE key = ?", (COUNT_KEY,)).fetchone()
            so_far = str(stored["value"] or "") if stored else ""
            so_far_count = int(so_far) if so_far.isdigit() else _summed(conn)
        if progress["sums"]:
            conn.executemany(ADD_UP, [(*key, *total) for key, total in progress["sums"].items()])
        inserted = 0
        for release in progress["grabbed_by_app"].values():
            # One by one because of the count: a release that is stored already (the same number and time) is neither inserted nor counted.
            inserted += max(0, conn.execute(STORE_GRAB, release).rowcount)
        if progress["new_count"]:
            added = sum(total[0] for total in progress["sums"].values()) + inserted
            conn.execute(MARK_UPSERT, (COUNT_KEY, str(so_far_count + added)))
        if progress["total"]:
            conn.execute(MARK_UPSERT, (TIME_MARK_KEY, progress["time"]))
    db.forget_settings()


def _mark() -> dict[str, Any]:
    """Where counting starts from: the number and the time of the newest counted event (0 and "" = nothing yet) and the number as stored."""
    stored = db.get_setting(MARK_KEY, "")
    number = _number(stored)
    if stored and number is None:
        # An unreadable mark cannot be taken as "from the start": the whole history would be added a second time.
        raise ProwlarrError("The stored number of the last event of {name} is damaged. Use Delete downloaded data and sync again.",
                            name=NAME)
    return {"id": number or 0, "time": db.get_setting(TIME_MARK_KEY, "") if number else "", "text": stored, "by_time": False}


def event_count() -> int:
    """How many history events are counted: the queries in the sums and the grabbed releases.

    Read from the settings (`COUNT_KEY`, the sync writes it in the same transaction as the sums):
    Settings → *Arr asks on every view and summing the whole table of sums for it would show with a big
    history. Data from before the counter is summed from the tables.
    """
    stored = db.get_setting(COUNT_KEY, "")
    if stored.isdigit():
        return int(stored)
    with db.connect() as conn:
        return _summed(conn)


def sync_state() -> dict[str, Any]:
    """The last sync: when, the result, how many were added, the error (as with the other apps)."""
    return read_state(STATE_KEY)


async def _fetch(transport: httpx.AsyncBaseTransport | None) -> dict[str, int]:
    url, key = db.get_setting("arr_prowlarr_url", ""), db.get_setting("arr_prowlarr_api_key", "")
    if not url or not key:
        raise ProwlarrError("{name} has no address or API key filled in.", name=NAME)
    since = _mark()
    async with ProwlarrClient(url, key, transport=transport) as client:
        indexers = await client.indexers()
        progress = await _history(client, since)
    if progress["unknown_count"] and not progress["new_count"]:
        # Unknown kinds and nothing countable: the mark stays (see the introduction), only the indexers are written.
        if progress["unknown_count"] >= UNKNOWN_ERROR_AT:
            raise ProwlarrError(ONLY_UNKNOWN, name=NAME, n=progress["unknown_count"])
        progress = {**progress, "sums": {}, "grabbed_by_app": {}, "total": 0, "unknown_only": progress["total"]}
    try:
        await asyncio.to_thread(_write, progress, since, indexers, db.utcnow())
    except _OtherProcess:
        # Another process over the same database counted the same events meanwhile: this run wrote nothing.
        raise ProwlarrError(ALREADY_RUNNING, name=NAME) from None
    total = progress.get("unknown_only", progress["total"])
    write_state(STATE_KEY, "ok", progress["new_count"])
    log.info("%s sync: %s new records of %s", NAME, progress["new_count"], total)
    return {"new_count": progress["new_count"], "total": total}


async def synchronize(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, int]:
    """Count what was added to Prowlarr's history. Returns {"new_count", "total"}.

    `total` is the count of events newer than the mark, `new_count` how many of them were counted (an
    event of an unknown kind is only skipped). An error is written into the state (and the exception
    flies on); nothing is written with it, so the next run starts where the last successful one ended.
    """
    if NAME in _RUNNING:
        raise ProwlarrError(ALREADY_RUNNING, name=NAME)
    _RUNNING.add(NAME)
    try:
        return await _fetch(transport)
    except ProwlarrError as error:
        if error.template != ALREADY_RUNNING:      # "already running" (another process) is no sync error
            write_state(STATE_KEY, "error", 0, error)
        raise
    except Exception as error:
        write_state(STATE_KEY, "error", 0, ProwlarrError("Sync failed: {reason}", reason=i18n.error_text(error)))
        raise
    finally:
        _RUNNING.discard(NAME)


def _delete() -> int:
    """Delete the sums, the grabbed releases, the indexers, the samples of their status, the marks and the state.
    Called only under the `_RUNNING` lock.

    The tables and the marks in one transaction: a mark without sums would never fetch the history again,
    sums without a mark would add it a second time next time.
    """
    count = event_count()
    with db.connect() as conn:
        conn.execute("DELETE FROM prowlarr_hour")
        conn.execute("DELETE FROM prowlarr_grab")
        conn.execute("DELETE FROM prowlarr_indexer")
        conn.execute("DELETE FROM prowlarr_sample")
        conn.execute(MARK_UPSERT, (MARK_KEY, ""))
        conn.execute(MARK_UPSERT, (TIME_MARK_KEY, ""))
        conn.execute(MARK_UPSERT, (COUNT_KEY, "0"))
    db.forget_settings()
    db.set_setting(STATE_KEY, "")
    log.info("deleted the downloaded history of %s: %s records", NAME, count)
    return count


def delete_data() -> int:
    """Delete everything downloaded from Prowlarr. Returns the count of deleted events.

    The next sync (if Prowlarr is connected) reads the whole history again - as much of it as Prowlarr
    still has. For calls outside the asyncio loop (scripts, tests); the web calls
    `delete_downloaded_data`, which locks the sync for the time of the deletion.
    """
    if NAME in _RUNNING:
        raise ProwlarrError(SYNC_RUNNING, name=NAME)
    return _delete()


async def delete_downloaded_data() -> int:
    """Delete the downloaded data in a thread and hold the sync lock meanwhile (as with *Arr)."""
    if NAME in _RUNNING:
        raise ProwlarrError(SYNC_RUNNING, name=NAME)
    _RUNNING.add(NAME)
    try:
        return await asyncio.to_thread(_delete)
    finally:
        _RUNNING.discard(NAME)
