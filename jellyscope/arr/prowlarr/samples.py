"""Samples of the indexers' status: who Prowlarr leaves out, measured and kept.

A block is what the history does not see: an indexer that refuses connections writes nothing there,
Prowlarr only leaves it out for a while (`indexerstatus`, "who is left out right now"). For the
indexers' availability over time Jellyscope therefore keeps samples of that answer
(`prowlarr_sample`). Mostly the background loop takes them (watch.py, every N seconds while the app
runs): it stores a sample when the state changes and every ten minutes (`EVERY`), and in between moves
the sample's last round (`measured_until`, `extend_watched`) - the table grows by about one row per
ten minutes while nothing changes, and the time the loop did not run is not measured. While the loop
measures (`watching`) nobody else samples. With it switched off the samples come as before: after the
Prowlarr part of the *Arr sync task (registry.synchronize_all -> `live.take_sample`, every run) and, at
most once every ten minutes, when a page loads the live state - out of the answers the live state
fetched anyway, without a request of its own.

A sample is a row of its own (indexer `RUN`) and a row for every indexer Prowlarr left out at that
moment: since when it fails, until when it is left out, its most recent failure. The row of its own is
there when nobody is left out too - otherwise "all fine" could not be told from "not measured"
(Jellyscope did not run, Prowlarr did not answer). It also keeps which indexers were there and enabled at
that moment (`enabled_ids`, `id_list`) and the usual interval in force (`interval_seconds`,
`sample_interval`): an indexer added later, removed or switched off by hand is not measured where it was
not there, and a gap is judged by the interval of its own time - not by the setting of today. The time is
the sample's own: the task runs when the scheduler gets to it and pages come when people look, so the
intervals are irregular.

A failed write must not bring the page or the sync down: it is only logged, the next sample tries again. A page's
sample is a write on the side, like the sessions' touch (web_sessions.SIDE_WRITE_WAIT_MS): it waits for another
writer (a VACUUM, a restore, a long import) only briefly and is skipped quietly - not the connection's whole lock
wait on every page. The task's sample waits as long as it takes. The loop's writes are on the side too: under
another writer's lock its round is skipped quietly - not measured, the chain breaks (watch.py) - instead of waiting
the whole lock wait at every round; any other failure (a table of an older backup restored, before the restart) is
logged once, then the loop is quiet until a write of its works again (`LOOP["failing"]`).
The samples go with the rest of Prowlarr's data: "Delete downloaded data" deletes them (sync._delete), an
indexer set aside after a renumbering takes its samples along (sync._set_aside_indexers), and the history
retention removes those older than the kept history (`delete_old_samples`, purge.delete_old).

How often samples come is what the availability counts a gap by (`sample_interval`, stored with every sample,
stats.prowlarr_availability): the loop's interval for its samples; for the others the *Arr sync task's interval while
it runs on its own, otherwise only the pages' ten minutes.

A sample the loop stores because the state changed moves a marker in the settings (`STATUS_CHANGED`, `status_marker`):
an open page redraws the availability with it (web/base.change_markers, the "arr" marker).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from ... import db, dialect
from ...web_sessions import SIDE_WRITE_WAIT_MS
from .settings import is_connected, watch_settings

log = logging.getLogger("jellyscope.arr")

# A page samples at most this often, counted from the last sample of any kind (the task's too); the task on every run.
EVERY = timedelta(minutes=10)
RUN = 0                         # the indexer number of a sample's own row
# The *Arr sync task's settings and its default interval (tasks.py, the task "arr" - this package does not import the
# tasks, which import the apps; tests/test_prowlarr_samples.py compares them).
TASK_ENABLED, TASK_MINUTES, TASK_DEFAULT_MINUTES = "task_arr_enabled", "arr_sync_minutes", 60
# PostgreSQL's SQLSTATE of a lock not had within lock_timeout (psycopg.errors.LockNotAvailable).
LOCK_NOT_AVAILABLE = "55P03"
# The rows of a sample. A second sample within the same second is the same one (the task and a page meeting): nothing.
STORE_RUN = ("INSERT INTO prowlarr_sample (sampled_at, indexer_id, enabled_ids, interval_seconds)"
             " VALUES (?, ?, ?, ?) ON CONFLICT (sampled_at, indexer_id) DO NOTHING")
STORE = ("INSERT INTO prowlarr_sample (sampled_at, indexer_id, failing_since, skipped_until, last_failure)"
         " VALUES (?, ?, ?, ?, ?) ON CONFLICT (sampled_at, indexer_id) DO NOTHING")
# The loop's sample: its own row knows its last round too (at first the moment it was taken), and moves it later.
STORE_WATCHED = ("INSERT INTO prowlarr_sample (sampled_at, indexer_id, enabled_ids, interval_seconds, measured_until)"
                 " VALUES (?, ?, ?, ?, ?) ON CONFLICT (sampled_at, indexer_id) DO NOTHING")
EXTEND = ("UPDATE prowlarr_sample SET measured_until = ?"
          " WHERE sampled_at = ? AND indexer_id = ? AND measured_until IS NOT NULL AND measured_until <= ?")
# The marker of a stored change of the indexers' status (a stamp that only grows; db.UNLOGGED_SETTINGS).
STATUS_CHANGED = "arr_prowlarr_status_changed"
# How many background loops run in this process (watch.watch_indexers_forever counts itself in and out), whether
# their writes fail right now (a failure is logged once, then quiet until a write works again) and whether Prowlarr
# answered the last round that asked it (None = no such round yet; `how_measured`).
LOOP: dict[str, Any] = {"running": 0, "failing": False, "answered": None}
# How the indexers' status is measured right now (`how_measured` - what the availability card says before the first
# sample): by the background loop, by the loop Prowlarr does not answer, by the *Arr sync task and pages.
BY_LOOP, NO_ANSWER, BY_TASK = "loop", "no_answer", "task"
# What `extend_watched` did: moved the sample's last round; found no such sample (its data were deleted - the loop
# stores a new one); could not write (another writer's lock, a broken table - the round is skipped, not measured).
MOVED, GONE, FAILED = "moved", "gone", "failed"
Row = tuple[int, str | None, str | None, str | None]


def id_list(numbers: Iterable[int]) -> str:
    """The indexers' numbers as the sample's own row keeps them: ",1,3,7," (none = ","). Every number between two
    commas, so a renumbering replaces ",5," whole (sync._set_aside_indexers) and never the 5 of 15."""
    return "".join(f",{n}" for n in sorted(set(numbers))) + ","


def _store(now: str, rows: list[tuple[int, str | None, str | None, str | None]], enabled: Iterable[int],
           every: timedelta | None) -> bool:
    """One transaction: the sample's own row and the rows of the indexers left out. False = not stored (too early, or this second is sampled)."""
    with db.connect() as conn:
        if every is not None:
            conn.execute(dialect.short_lock_wait(conn.kind, SIDE_WRITE_WAIT_MS))
            last = conn.execute("SELECT MAX(sampled_at) AS last FROM prowlarr_sample WHERE indexer_id = ?", (RUN,)).fetchone()
            since = (datetime.strptime(now, db.TIME_FORMAT) - every).strftime(db.TIME_FORMAT)
            if last and last["last"] and last["last"] > since:
                return False
        own = (now, RUN, id_list(enabled), int(sample_interval().total_seconds()))
        if conn.execute(STORE_RUN, own).rowcount != 1:
            return False
        if rows:
            conn.executemany(STORE, [(now, *row) for row in rows])
    return True


def store_sample(now: str, rows: list[tuple[int, str | None, str | None, str | None]], enabled: Iterable[int],
                 every: timedelta | None = None) -> bool:
    """Store the sample taken at `now` (UTC, db.TIME_FORMAT): `rows` are the indexers left out, as (number, failing
    since, left out until, most recent failure) - an empty list = nobody; `enabled` the numbers of the indexers there
    and enabled at that moment. The sample keeps the usual interval in force too (`sample_interval`). With `every`,
    only when the last sample is older than that (a page). Returns whether it was stored. Never raises: a sample must
    not bring anything down.
    """
    try:
        return _store(now, rows, enabled, every)
    except Exception as error:   # noqa: BLE001 - neither the page nor the sync may fail because of a sample
        if every is not None and _another_writer(error):
            return False         # a page's sample under another writer's lock: the next page tries again
        log.warning("prowlarr: could not store a sample of the indexers' status: %s", error)
        return False


def _another_writer(error: Exception) -> bool:
    """Whether the write gave up waiting for another writer's lock: SQLite busy or locked, PostgreSQL's lock_timeout."""
    if isinstance(error, sqlite3.OperationalError):
        return getattr(error, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return getattr(error, "sqlstate", None) == LOCK_NOT_AVAILABLE


def watching() -> bool:
    """Whether the background loop measures right now: it runs in this process, is switched on and Prowlarr is
    connected. Then neither the *Arr sync task nor a page samples - the loop's samples are closer together."""
    return LOOP["running"] > 0 and watch_settings()["enabled"] and is_connected()


def how_measured() -> str:
    """How the indexers' status is measured right now: BY_LOOP (the background loop measures - `watching`), NO_ANSWER
    (it does, but Prowlarr did not answer its last round) or BY_TASK (the loop does not measure: the samples come with
    the *Arr sync task and pages)."""
    if not watching():
        return BY_TASK
    return NO_ANSWER if LOOP["answered"] is False else BY_LOOP


def _logged_once(error: Exception) -> bool:
    """Whether the loop's write that failed with `error` is to be logged: not under another writer's lock (the round
    is only skipped), and only the first failure until a write works again - a line every round would say nothing new."""
    if _another_writer(error) or LOOP["failing"]:
        return False
    LOOP["failing"] = True
    return True


def _store_watched(now: str, rows: list[Row], enabled: Iterable[int], interval: int) -> bool:
    with db.connect() as conn:
        conn.execute(dialect.short_lock_wait(conn.kind, SIDE_WRITE_WAIT_MS))
        if conn.execute(STORE_WATCHED, (now, RUN, id_list(enabled), interval, now)).rowcount != 1:
            return False
        if rows:
            conn.executemany(STORE, [(now, *row) for row in rows])
    return True


def store_watched(now: str, rows: list[Row], enabled: Iterable[int], interval: int, changed: bool) -> bool:
    """Store the loop's sample taken at `now` (UTC): `rows` and `enabled` as with `store_sample`, `interval` the loop's
    seconds. `changed` = the state differs from the loop's last sample: the marker moves (`STATUS_CHANGED`). Returns
    whether it was stored; never raises (the loop tries again at its next round). A write on the side: under another
    writer's lock not stored, quietly."""
    try:
        stored = _store_watched(now, rows, enabled, interval)
        if stored and changed:
            db.set_setting(STATUS_CHANGED, str(time.time_ns()))
    except Exception as error:   # noqa: BLE001 - the loop must not fail because of a sample
        if _logged_once(error):
            log.warning("prowlarr: could not store a sample of the indexers' status: %s", error)
        return False
    LOOP["failing"] = False
    return stored


def extend_watched(sampled_at: str, now: str) -> str:
    """The loop saw the same state at `now`: its sample taken at `sampled_at` is measured up to `now`. Returns MOVED,
    GONE (no such sample - its data were deleted: the loop stores a new one) or FAILED (the write failed: the round is
    skipped). A write on the side, as `store_watched`."""
    try:
        with db.connect() as conn:
            conn.execute(dialect.short_lock_wait(conn.kind, SIDE_WRITE_WAIT_MS))
            moved = conn.execute(EXTEND, (now, sampled_at, RUN, now)).rowcount == 1
    except Exception as error:   # noqa: BLE001 - the loop must not fail because of a sample
        if _logged_once(error):
            log.warning("prowlarr: could not store a sample of the indexers' status: %s", error)
        return FAILED
    LOOP["failing"] = False
    return MOVED if moved else GONE


def status_marker() -> str:
    """The marker of the last stored change of the indexers' status ("" = none yet): the cards over the samples are
    redrawn when it moves (web/base.change_markers)."""
    return db.get_setting(STATUS_CHANGED, "")


def delete_old_samples(before: str) -> int:
    """Delete the samples taken before `before` (UTC, db.TIME_FORMAT) - the history retention. Returns how many rows."""
    with db.connect() as conn:
        return max(0, conn.execute("DELETE FROM prowlarr_sample WHERE sampled_at < ?", (before,)).rowcount)


def sample_interval() -> timedelta:
    """The usual time between two samples: the *Arr sync task's interval while it runs on its own (it samples on every
    run), otherwise the pages' ten minutes (`EVERY`) - then only pages that load the live state sample."""
    if db.get_setting(TASK_ENABLED, "0") == "1":
        minutes = db.get_int_setting(TASK_MINUTES, 0, 10080, TASK_DEFAULT_MINUTES)
        if minutes > 0:
            return timedelta(minutes=minutes)
    return EVERY
