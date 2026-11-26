"""Running tasks: the lock, stopping, progress and the record of a task (scan_log).

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import db, i18n

log = logging.getLogger("jellyscope.scanner")


# A lock that makes sure two scans never run at once. Without it two analyses started at the same time would
# load the disk needlessly and overwrite each other's results.
_scan_lock = asyncio.Lock()


def is_scan_running() -> bool:
    return _scan_lock.locked()


def task_lock() -> asyncio.Lock:
    """The lock under which tasks run that must not meet.

    The sync, the analysis of files - and downloading the detailed map (detailed_map.start_task), which
    would otherwise compete with the sync for the database and for memory.
    """
    return _scan_lock


# ---------------------------------------------------------------------------
# Stopping a running task
# ---------------------------------------------------------------------------
#
# A task is **not interrupted in the middle of work**. Only a flag is set and the loop notices it when it has
# finished the item at hand - only then does it end.
#
# Why this way and not task.cancel(): a hard interruption could come in the middle of a write into the
# database and leave a half batch or an unclosed transaction behind. This way it always ends at a place where
# the database is in order. The user waits a few seconds at most.
#
# The flag is a plain module variable, just like `_scan_lock` - both hold for one running process of the app.
# Jellyscope runs in one process (see run.py), so that is enough; with several workers this would stop holding
# just as the lock itself.
_stop_requested = False


def request_stop() -> bool:
    """Ask the running task to end. Returns whether anything runs at all."""
    global _stop_requested
    if not is_scan_running():
        return False
    _stop_requested = True
    log.info("the task was asked to stop")
    return True


def stop_requested() -> bool:
    """Is the running task waiting to end?

    The condition `is_scan_running()` is not there for nothing. The flag belongs **to the task it was set
    for** - when that ends, it tells nobody anything any more. Without it Settings would report "stopping"
    forever after every stop, and in tests the flag would leak from one task into the next.

    Clearing it when leaving the task would mean wrapping the whole body of the function in try/finally
    because of one variable. This way the same holds and it is visible in one place.
    """
    return _stop_requested and is_scan_running()
# ---------------------------------------------------------------------------
# Progress of a running task
# ---------------------------------------------------------------------------
#
# A sync of a big library takes minutes and used to give no sign of itself - the user looked at "a task is
# running" and had no way to tell whether it was at the start or nearly done.
#
# We find out the total in advance: Jellyfin sends it with every page as `TotalRecordCount`, so it is enough
# to ask for a page of size zero. Then we only add up what is done.
#
# We keep it in memory, not in the database: it is information about the process **running right now**, which
# interests nobody once it ends. Writing it into the database after every batch would be an extra write with
# no use.
_progress: dict[str, Any] = {"kind": None, "done": 0, "total": 0}


def progress() -> dict[str, Any]:
    """How much work is done. Empty when nothing runs."""
    if not is_scan_running() or not _progress["kind"]:
        return {}
    done, total = _progress["done"], _progress["total"]
    return {
        "kind": _progress["kind"],
        "done": done,
        "total": total,
        # We compute the percentage here, so the template need not. And only when we know the total at all -
        # otherwise "0 %" would lie that nothing runs.
        "percent": round(done / total * 100) if total else None,
    }


def _start_progress(kind: str, total: int = 0) -> None:
    _progress.update(kind=kind, done=0, total=max(0, int(total or 0)))


def _add_progress(amount: int) -> None:
    _progress["done"] += amount


def _clear_progress() -> None:
    _progress.update(kind=None, done=0, total=0)


def _clear_stop() -> None:
    """Drop the flag. Called at the start of every task.

    Without it a task started later would find the flag of the previous one and end right away.
    """
    global _stop_requested
    _stop_requested = False


# ---------------------------------------------------------------------------
# The record of a scan's course (so that the UI shows what is going on)
# ---------------------------------------------------------------------------

def start_task_log(kind: str) -> int:
    with db.connect() as conn:
        new_id = conn.insert_returning_id(
            "INSERT INTO scan_log (kind, started_at, status) VALUES (?, ?, 'running')",
            (kind, db.utcnow()),
        )
        _thin_records(conn, kind)
        return new_id


def versions() -> dict[str, str]:
    """The versions at once - the library, the finished tasks and the last finished run of each kind the cards show.

    One query, because they ask the same two tables and go together: **every call of /health** reaches for
    them, and the browser asks for it every ten seconds from every open tab. In more queries that meant more
    connections to the database instead of one - and the overhead of a connection is bigger here than the
    work itself. A kind's run is a look-up in the index idx_scan_log_kind (kind, id).

    The keys "arr", "seerr", "music" and "books" are the row of the kind's last finished run (the markers of
    the cards over that task's data, web/status.py); a run that is still going does not count yet. So is the
    library's: the last finished sync of the library or analysis of its files ("tech" runs after the sync and
    fills in the codecs, resolutions and dynamic range) - by its row, not its time: the analysis of a small
    library finishes within the sync's second. The three take the scan's lock (one at a time), so a later
    row also finishes later.

    `SELECT (subquery), (subquery)` without FROM is understood by SQLite and PostgreSQL.
    """
    row = db.query_one(
        """
        SELECT (SELECT MAX(id) FROM scan_log
                 WHERE kind IN ('library', 'recent', 'tech')
                   AND finished_at IS NOT NULL)    AS library_task_id,
               (SELECT COUNT(*) FROM items)                AS item_count,
               (SELECT MAX(id) FROM scan_log
                 WHERE finished_at IS NOT NULL)            AS last_task_id,
               (SELECT MAX(id) FROM scan_log
                 WHERE kind = 'arr' AND finished_at IS NOT NULL)   AS arr_task_id,
               (SELECT MAX(id) FROM scan_log
                 WHERE kind = 'seerr' AND finished_at IS NOT NULL) AS seerr_task_id,
               (SELECT MAX(id) FROM scan_log
                 WHERE kind = 'music' AND finished_at IS NOT NULL) AS music_task_id,
               (SELECT MAX(id) FROM scan_log
                 WHERE kind = 'books' AND finished_at IS NOT NULL) AS books_task_id
        """
    ) or {}
    return {
        "library": f"{row.get('library_task_id') or 0}:{row.get('item_count') or 0}",
        "tasks": str(row.get("last_task_id") or 0),
        **{kind: str(row.get(f"{kind}_task_id") or 0) for kind in ("arr", "seerr", "music", "books")},
    }


def library_version() -> str:
    """A fingerprint of how the library looks now.

    It serves one thing: the browser remembers it when the page loads and when it changes later, it knows it
    should reload the strip of recently added titles. Without it it would have to either refresh blindly over
    and over, or wait until a person refreshes the page - and a newly added movie would never show on a page
    already open.

    We compose it from the last finished task and the count of titles. The time alone is not enough: a task
    that found nothing finishes too, and redrawing because of it makes no sense.
    """
    return versions()["library"]


def tasks_version() -> str:
    """A fingerprint of how many tasks have finished.

    The page remembers it when it loads and when it changes, it knows some task ended meanwhile - and can
    refresh.

    Why not "watch whether a task is running right now and wait until it stops": because a recent sync over a
    small library takes a few seconds. When the page asks once in ten, the whole run fits comfortably into the
    gap between two requests - the task runs, the page never knows, and the strip "a task is running" stays
    hanging on it. Exactly that happened.

    `MAX(id)` is enough: the row number grows with every further task, so we need not rely on time (two tasks
    may end in the same second).
    """
    return versions()["tasks"]


def finish_task_log(
    scan_id: int,
    status: str,
    total: int = 0,
    ok: int = 0,
    failed: int = 0,
    message: str | i18n.Message | BaseException = "",
) -> None:
    """Closes a task's record. The message is stored as an English key and its values
    (i18n.message_parts) and read in the reader's language (i18n.stored_text); a text is its own key."""
    template, values = i18n.message_parts(message)
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE scan_log
               SET finished_at = ?, status = ?, items_total = ?,
                   items_ok = ?, items_failed = ?, message = ?, message_values = ?
             WHERE id = ?
            """,
            (db.utcnow(), status, total, ok, failed, template[:500], values, scan_id),
        )


# How many records of one kind of task we keep.
#
# The recent sync runs every fifteen minutes, so over ninety rows a day were added to the table - and nothing
# was ever deleted. In a year there would be over thirty thousand of them, and every request of /health (the
# browser asks every ten seconds from every open tab) would have to go through them.
#
# Three hundred is enough: Settings show the last run of every kind, the rest is history in case something
# needs investigating.
RECORDS_PER_KIND = 300


def _thin_records(conn: Any, kind: str) -> None:
    """Keep only the last `RECORDS_PER_KIND` records of the given kind.

    We look for the boundary with a query, not with a count: `DELETE ... LIMIT` is known to SQLite but not to
    PostgreSQL, and this shape is understood by both.
    """
    row = conn.execute(
        "SELECT id FROM scan_log WHERE kind = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
        (kind, RECORDS_PER_KIND - 1),
    ).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM scan_log WHERE kind = ? AND id < ?",
                 (kind, row["id"]))


def close_interrupted_task_logs() -> int:
    """At the start: runs the previous start left "running" (a restart, a crash) end as failed. Returns how many.

    Nothing runs yet at that moment. Without this the record stayed "running" for good and Settings → Tasks showed
    a running task that never ended.
    """
    with db.connect() as conn:
        return conn.execute(
            "UPDATE scan_log SET status = 'error', finished_at = ?, message = ?, message_values = NULL"
            " WHERE status = 'running'", (db.utcnow(), "Interrupted by a restart of the application.")).rowcount


def last_scan(kind: str) -> dict[str, Any] | None:
    return db.query_one(
        "SELECT * FROM scan_log WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
    )
