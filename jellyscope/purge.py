"""Clearing out the history: what is deleted after a time and how to forget one viewer.

With a tool that remembers who watched what when, somebody will want it sooner
or later — for their own sake, for the people on their server, or because of
rules that bind them to it.

Three things this stands on:

* **By default it is off.** Deleting data must never start by itself: whoever
  did not ask for it must not lose anything just by updating. It is turned on
  in Settings and with it it is said how long a history is kept.

* **Before deleting, it is said how much.** Settings show how many sessions the
  current boundary would clear out, and the page says it before it is saved.
  "40 000 plays were deleted" is a bad moment for a surprise.

* **Live playback is not deleted.** A session running right now does not count
  into the boundary, even if it started the day before yesterday — the
  collector has it in progress and deleting it under it means creating it
  again right after, only without the start.

Forgetting a viewer is a one-off thing and it is the same deleting, only
chosen by the user instead of by the date. The account itself is in Jellyfin,
not here: the next sync sees it again and creates it — without the history we
forgot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from . import db

log = logging.getLogger("jellyscope.purge")

# How long the history is kept. The bounds are wide on purpose, but not
# bottomless: less than a month is no history any more, more than ten years is
# the same as off.
RETENTION_KEY = "history_retention_days"
MIN_DAYS = 30
MAX_DAYS = 3650
DEFAULT_DAYS = 365

# Rewriting the file after deleting (db.compact) takes time in proportion to
# the database's size - roughly a minute per 5 GB - and SQLite holds a lock on
# everything for that time. After the night's clearing out nobody minds; after
# a click on "Yes, forget" the whole application would however get stuck for
# that minute. So forgetting by hand only deletes and notes the file rewrite for
# the night. The value is the time of the request (UTC); empty = nothing waits.
DEFERRED_KEY = "db_compaction_pending"


def retention_days() -> int:
    """How many days of history are kept."""
    return db.get_int_setting(RETENTION_KEY, MIN_DAYS, MAX_DAYS, DEFAULT_DAYS)


def enabled() -> bool:
    """Is anything deleted at all? Without this nothing may be deleted."""
    return db.get_setting("task_purge_enabled", "0") == "1"


def clamp_days(days: Any) -> int:
    """The number of days deleting may go with.

    The dangerous is guarded, not the small. Zero and a negative number move
    the boundary to "now" or into the future, so **everything** would go - that
    must not pass, just like nonsense instead of a number. A shorter boundary
    than the settings allow we will not forbid though: whoever gave it right in
    the code knows what they do.

    The range for the value from the **settings** is guarded by
    `retention_days()`. There it is about what a person may enter in the form,
    and that is a different question.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    if days < 1:
        return DEFAULT_DAYS
    return min(days, MAX_DAYS)


def cutoff(days: int | None = None) -> str:
    """The date below which sessions are no longer kept (UTC, the database's shape).

    The bounds apply to a passed number of days too, not only to the one from
    the settings - here a number becomes a boundary, so here it must be guarded.
    """
    days = retention_days() if days is None else clamp_days(days)
    return (datetime.now(UTC) - timedelta(days=days)).strftime(db.TIME_FORMAT)


def overview(days: int | None = None) -> dict[str, Any]:
    """What the given setting would clear out - without doing it.

    It is drawn in Settings under the field with the number of days. A number a
    person can read before they click is the whole difference between "a
    setting" and "a surprise".
    """
    boundary = cutoff(days)
    row = db.query_one(
        "SELECT COUNT(*) AS count, MIN(started_at) AS oldest"
        " FROM playback WHERE started_at < ? AND is_active = 0",
        (boundary,),
    ) or {}
    total = db.query_one("SELECT COUNT(*) AS count FROM playback") or {}
    oldest_overall = db.query_one(
        "SELECT MIN(started_at) AS oldest FROM playback") or {}
    return {
        "threshold": boundary,
        "leaving": int(row.get("count") or 0),
        "oldest_leaving": row.get("oldest") or "",
        "total": int(total.get("count") or 0),
        "oldest": oldest_overall.get("oldest") or "",
    }


def delete_old(days: int | None = None) -> dict[str, Any]:
    """Deletes the sessions older than the boundary. Returns how many there were.

    `is_active = 0` is a condition, not extra caution: a session that plays
    now belongs to the collector and deleting it means creating it again in ten
    seconds, only without the start.
    """
    days = retention_days() if days is None else clamp_days(days)
    boundary = cutoff(days)
    with db.connect() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS count FROM playback"
            " WHERE started_at < ? AND is_active = 0", (boundary,)).fetchone()
        count = int((before or {"count": 0})["count"] or 0)
        if count:
            conn.execute(
                "DELETE FROM playback WHERE started_at < ? AND is_active = 0",
                (boundary,))
        conn.commit()

    if count:
        log.info("Cleared out %s playbacks older than %s days", count, days)
        # Only here are the data really gone - see db.compact(). It runs only
        # when something was deleted: it rewrites the whole file.
        if db.compact() and not bin_has_rows():
            # The rewrite order is cancelled only when it has nothing to cover
            # any more. When something is in the bin, it must stay: by it it is
            # told when to empty the bin, and without it the rows would get stuck there.
            db.set_setting(DEFERRED_KEY, "")
    return {"deleted": count, "threshold": boundary, "days": days}


def forget_user(user_id: str, to_bin: bool = False,
                backup: str = "") -> dict[str, Any]:
    """Deletes everything recorded about one viewer.

    The account itself is in Jellyfin - it cannot be deleted from here and that
    is not even to be tried. The next sync thus sees it again; the history we
    had about them however does not come back.

    `to_bin` decides whether the intervention can be taken back before the
    night. `True` (that is what the button in Settings does) **moves** the rows
    **to the bin**: they vanish from the statistics at once, but they can be
    returned until `empty_bin()` empties it at night. `False` deletes them
    right away and rewrites the database file at once - that is the path for
    whoever knows what they do and does not want to wait.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return {"deleted": 0, "name": ""}

    row = db.query_one(
        "SELECT name FROM users WHERE id = ?", (user_id,)) or {}
    name = row.get("name") or ""

    event = 0
    with db.connect() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) AS count FROM playback WHERE user_id = ?",
            (user_id,)).fetchone()
        count = int((count_row or {"count": 0})["count"] or 0)
        if count and to_bin:
            event = _create_event(conn, user_id, name, count, backup)
            shared = [s for s in db.columns_of(conn, "playback")
                      if s in set(db.columns_of(conn, db.PLAYBACK_BIN)) and s != "id"]
            column_list = ", ".join(shared)
            # The copy first, only then the deleting - should something go
            # wrong on the way, let the rows stay in playback, not nowhere.
            conn.execute(
                f"INSERT INTO {db.PLAYBACK_BIN} (event_id, {column_list})"
                f" SELECT ?, {column_list} FROM playback WHERE user_id = ?",
                (event, user_id))
        conn.execute("DELETE FROM playback WHERE user_id = ?", (user_id,))
        conn.commit()

    log.info("Viewer %s forgotten: %s playbacks deleted", name or user_id, count)
    if count and to_bin:
        # The rows are still in the bin, so rewriting the file makes no sense
        # now - it is done at night, when the bin is emptied.
        defer_compaction()
    elif count:
        # "Zapomeň toho diváka" is to mean that they are gone - not that they are
        # only no longer looked for. Without this their records stay lying in
        # the file until something overwrites them, and can be read from it.
        db.compact()
    return {"deleted": count, "name": name, "event": event}


# ---------------------------------------------------------------------------
# The bin: until it is emptied, forgetting can be taken back
# ---------------------------------------------------------------------------

IN_BIN = "bin"
BACKUP_ONLY = "backup"


def _create_event(conn: Any, user_id: str, name: str, count: int,
                  backup: str) -> int:
    """Writes one forgetting and returns its number."""
    conn.execute(
        "INSERT INTO forgettings (user_id, name, session_count, forgotten_at,"
        " backup, status) VALUES (?,?,?,?,?,?)",
        (user_id, name or user_id, count, db.utcnow(), backup, IN_BIN))
    row = conn.execute(
        "SELECT MAX(id) AS id FROM forgettings WHERE user_id = ?",
        (user_id,)).fetchone()
    return int(dict(row or {}).get("id") or 0)


def restore_from_bin(event: Any) -> dict[str, Any]:
    """Returns to the history what one forgetting cleared out into the bin.

    The whole group comes back at once - a viewer is either forgotten or not.
    The `id` column is not copied: the database gives out new numbers itself
    and nobody refers to them.
    """
    try:
        number = int(event)
    except (TypeError, ValueError):
        return {"returned": 0, "name": ""}

    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, status FROM forgettings WHERE id = ?",
            (number,)).fetchone()
        info = dict(row or {})
        if not info or str(info.get("status")) != IN_BIN:
            return {"returned": 0, "name": str(info.get("name") or "")}

        shared = [s for s in db.columns_of(conn, "playback")
                  if s in set(db.columns_of(conn, db.PLAYBACK_BIN)) and s != "id"]
        column_list = ", ".join(shared)
        count_row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {db.PLAYBACK_BIN} WHERE event_id = ?",
            (number,)).fetchone()
        count = int(dict(count_row or {}).get("count") or 0)
        conn.execute(
            f"INSERT INTO playback ({column_list})"
            f" SELECT {column_list} FROM {db.PLAYBACK_BIN} WHERE event_id = ?", (number,))
        conn.execute(f"DELETE FROM {db.PLAYBACK_BIN} WHERE event_id = ?", (number,))
        conn.execute("DELETE FROM forgettings WHERE id = ?", (number,))
        remaining = conn.execute(
            f"SELECT COUNT(*) AS count FROM {db.PLAYBACK_BIN}").fetchone()
        empty = int(dict(remaining or {}).get("count") or 0) == 0
        conn.commit()

    name = str(info.get("name") or "")
    log.info("Brought back from the bin: %s, %s playbacks", name, count)
    if empty:
        # The file rewrite was ordered because of a deleting that just did not
        # happen. On a big database it is a minute of lock - for nothing.
        db.set_setting(DEFERRED_KEY, "")
    return {"returned": count, "name": name}


def bin_has_rows() -> bool:
    """Does anything wait in the bin to be emptied?"""
    return bool(db.query_value(f"SELECT COUNT(*) FROM {db.PLAYBACK_BIN}", default=0))


def empty_bin() -> int:
    """Deletes what is in the bin. Returns how many rows went.

    This is the moment when forgetting really is forgetting. It is called at
    night, right before the file is rewritten - see tasks.
    """
    with db.connect() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {db.PLAYBACK_BIN}").fetchone()
        count = int(dict(count_row or {}).get("count") or 0)
        conn.execute(f"DELETE FROM {db.PLAYBACK_BIN}")
        conn.execute(
            "UPDATE forgettings SET status = ? WHERE status = ?",
            (BACKUP_ONLY, IN_BIN))
        conn.commit()
    if count:
        log.info("Bin emptied: %s playbacks are gone for good", count)
    return count


def forgettings(forget_older_without_backup: bool = True) -> list[dict[str, Any]]:
    """What was forgotten and what can still be done with it.

    Three states, and there is a difference between them a person is to see:

    * **in the bin** - can be returned with one button,
    * **only in a backup** - the bin was emptied, but the backup file still
      is there; it can be returned, only by hand, and the page says how,
    * **gone** - the backup was deleted meanwhile by the cleanup of older
      backups. Offering instructions for a file that does not exist would be a
      mockery, so such a row vanishes from the list.
    """
    rows = db.query_all(
        "SELECT id, user_id, name, session_count, forgotten_at, backup, status"
        " FROM forgettings ORDER BY id DESC")
    result: list[dict[str, Any]] = []
    to_drop: list[Any] = []
    for row in rows:
        info = dict(row)
        path = str(info.get("backup") or "")
        has_backup = bool(path) and Path(path).exists()
        if str(info.get("status")) != IN_BIN and not has_backup:
            to_drop.append(info["id"])
            continue
        info["backup_file"] = Path(path).name if path else ""
        info["has_backup"] = has_backup
        info["in_bin"] = str(info.get("status")) == IN_BIN
        result.append(info)

    if to_drop and forget_older_without_backup:
        with db.connect() as conn:
            for number in to_drop:
                conn.execute("DELETE FROM forgettings WHERE id = ?", (number,))
            conn.commit()
    return result


def defer_compaction() -> None:
    """Notes that the database file waits for a rewrite (at night)."""
    db.set_setting(DEFERRED_KEY, db.utcnow())
    log.info("Database rewrite deferred to the night-time slot")


def compaction_pending() -> str:
    """When somebody asked for the deferred rewrite (UTC text), or empty."""
    return db.get_setting(DEFERRED_KEY, "") or ""


def finish_deferred_compaction() -> bool:
    """Does the deferred file rewrite. Returns whether it succeeded.

    When not, the request stays - only with a new time, so it is tried again the
    next night, and not every minute.
    """
    if db.compact():
        db.set_setting(DEFERRED_KEY, "")
        return True
    db.set_setting(DEFERRED_KEY, db.utcnow())
    return False


def session_count(user_id: str) -> int:
    """How many plays we have about a viewer. Zero = there is nothing to forget."""
    user_id = (user_id or "").strip()
    if not user_id:
        return 0
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE user_id = ?", (user_id,),
        default=0) or 0)


def viewers() -> list[dict[str, Any]]:
    """Who is in the history - for the selection in Settings.

    We ask the history, not the users table: only what is written can be
    forgotten, and a viewer of whom nothing remained does not belong in the offer.
    """
    return db.query_all(
        """
        SELECT p.user_id                AS user_id,
               COALESCE(MAX(u.name), MAX(p.user_name)) AS name,
               COUNT(*)                 AS session_count,
               MIN(p.started_at)        AS since,
               MAX(p.started_at)        AS until
          FROM playback p
          LEFT JOIN users u ON u.id = p.user_id
         WHERE p.user_id IS NOT NULL
         GROUP BY p.user_id
         ORDER BY COUNT(*) DESC
        """
    )
