"""Scheduled tasks.

Things that should happen by themselves, but must be possible to start by
hand too and to turn off completely, for example:

  * **sync**   - synchronising the library from Jellyfin; with the data source
                 'ffprobe' the file analysis follows right after it
  * **recent** - only newly added titles; quick, can run often
  * **backup** - a backup of the database into the chosen folder

All of them have the same shape, described by the class `Task`. Thanks to
that the scheduler is written once and adding another task is a matter of
five lines, not another copy of the whole loop.

## Why the file analysis is part of the synchronisation

It used to be a task of its own with its own tick box. It did more harm
than good:

  * that task **did not ask about the chosen data source**. When a person had
    "Jellyfin API only" in Data collection and the task stayed on, ffprobe
    started anyway and overwrote the details from Jellyfin - the exact
    opposite of what the user set.
  * and the other way round: with the source 'ffprobe' and the task off, the
    titles of a full synchronisation stayed without any technical data at
    all, because they are no longer taken from Jellyfin.

Two tick boxes thus quietly decided whether the library had data or nothing.
Now there is one tick box ("synchronise the library") and **how** it is done
follows from Data collection. The manual analysis stays where it was - in
Settings by the choice of the source.

## How the scheduling works

There is no cron here. One loop runs in the background, which **once a
minute** looks whether some task is due - and if so, starts it. For tasks
that run once in hours that is plenty, and nothing else has to be installed
for it.

A task can become due in two ways, and each suits something else:

  * **every day at a given time** (the library synchronisation, the backup) -
    those are tasks that should run at night, when nobody watches. Were they
    counted from the last run, every manual start would shift the whole
    schedule and the task would in time "turn" into the afternoon.
  * **every N minutes** (newly added titles) - there the exact time does not
    matter, the point is to ask often.

The daily time is compared in the **local** time: whoever writes 3:30 means
half past three at home, not in UTC.

Each way asks about something else:

  * an interval task looks into `scan_log`, that is "when the task last
    ran" - whether the scheduler started it or a person.
  * a daily task looks at its own stamp `task_<key>_last_auto`, that is
    "when the scheduler last started it". A manual start is not written into
    it on purpose: when a person clicks "run now" at noon, the night run at
    3:30 should still happen.

Both survive a restart - one is in a table, the other in the settings.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
from typing import Any
from collections.abc import Awaitable, Callable, Iterator

from . import applog, db, i18n, purge, scanner, web_sessions
from .i18n import translate as _t
from .config import BASE_DIR

log = logging.getLogger("jellyscope.tasks")


class BackupError(i18n.TranslatableError):
    """A backup or a restore that failed for a reason a person can act on (a key and its values)."""

# How often the scheduler wakes up and checks whether something is due.
TICK_SECONDS = 60

# Imports, backups and the clean-up take no task lock (a sync may run beside them), but a backup restored under
# one of them would get its writes, waiting for the database, after the restore. They count themselves here for
# their whole run (data_job), and the restore is refused while the count is not zero. A count, not a flag: two of
# them can run at once.
_data_jobs = 0
_data_jobs_guard = threading.Lock()


@contextmanager
def data_job() -> Iterator[None]:
    """Marks an import, a backup or the clean-up as running for the whole block - an error ends the mark too."""
    global _data_jobs
    with _data_jobs_guard:
        _data_jobs += 1
    try:
        yield
    finally:
        with _data_jobs_guard:
            _data_jobs -= 1


def data_job_running() -> bool:
    """Does an import, a backup or the clean-up run right now (in this process)?"""
    return _data_jobs > 0


@dataclass(frozen=True)
class Task:
    """The description of one scheduled task."""

    key: str                                   # 'sync' / 'recent' / 'backup'
    name: str                                  # what the user is shown
    description: str
    runner: Callable[[], Awaitable[dict[str, Any]]]
    log_kind: str                              # the name it is written under into scan_log

    # A task is either daily (a key with the time "HH:MM"), or an interval one
    # (a key with a count of minutes). Exactly one pair is always filled in.
    time_setting: str = ""
    default_time: str = ""
    interval_setting: str = ""
    default_minutes: int = 0

    # When a manual start behaves differently than the scheduled one (Log
    # rotation: the scheduled one turns the logs only after the set count of
    # days, the manual one at once).
    manual_runner: Callable[[], Awaitable[dict[str, Any]]] | None = None
    # The date of the next real run, when it is not every day (Log rotation).
    next_run: Callable[[], date | None] | None = None

    @property
    def is_daily(self) -> bool:
        return bool(self.time_setting)

    @property
    def enabled_setting(self) -> str:
        return f"task_{self.key}_enabled"


async def _run_sync() -> dict[str, Any]:
    """The library synchronisation - and with the source 'ffprobe' the measuring right away.

    The order is given: first it must be known that a title exists and where
    its file lies, only then it can be measured. The analysis goes only over
    those that have no data yet (`only_missing`), so after the first run it is
    short - and when there is nothing to measure, it ends with a message and
    does nothing.

    The analysis comes AFTER the synchronisation, not inside it: both take the
    same lock, so inside they would block each other. For the same reason the
    quick synchronisation does it this way too, see scanner.sync_recent().
    """
    result = await scanner.sync_library()

    if result.get("status") != "ok":
        # An unfinished synchronisation (an error, or stopped on request) -
        # measuring an incomplete list makes no sense and with "stopped" it
        # would besides go against what the person wanted.
        return result

    if db.get_setting("tech_source") != "ffprobe":
        return result

    tech = await scanner.run_tech_scan(only_missing=True)
    result["tech"] = tech
    if tech.get("ok"):
        log.info("sync: measured %s files", tech["ok"])
    return result


async def _run_recent() -> dict[str, Any]:
    # Expired sign-in sessions go with the most frequent task that is on by default (and at start).
    web_sessions.clean_up()
    # The task determines the boundary itself from the last known title -
    # see scanner.sync_recent().
    return await scanner.sync_recent()


async def _run_backup() -> dict[str, Any]:
    return await backup_database()


async def _run_purge() -> dict[str, Any]:
    """The clean-up task (see _purge_run); no restore beside it."""
    with data_job():
        return await _purge_run()


async def _purge_run() -> dict[str, Any]:
    """Clears out the history older than the set boundary.

    The task runs only when it is on - the scheduler watches that. Still we
    ask here again: `purge.enabled()` is the only gate before deleting data
    and the task can be started by hand with a button too.
    """
    scan_id = scanner.start_task_log("purge")
    # The whole run is guarded, not only the deleting: an unexpected error in the overview or the backup before it
    # (a database locked by a VACUUM) left the run "running" in Settings -> Tasks until the next restart.
    try:
        if not purge.enabled():
            scanner.finish_task_log(scan_id, "ok",
                                    message="Clearing out is switched off, nothing was deleted.")
            return {"status": "ok", "deleted": 0}

        # A backup only when something will be deleted: copying 5 GB every night
        # for zero deleted rows would be work that protects nobody.
        if purge.overview()["leaving"]:
            backup = await backup_before_delete()
            if backup.get("status") != "ok":
                message = i18n.Message("Nothing is deleted without a backup: {reason}",
                                       reason=backup.get("reason") or i18n.Message("the backup failed"))
                log.warning("clean-up stopped: %s", message)
                scanner.finish_task_log(scan_id, "error", message=message)
                return {"status": "error", "deleted": 0, "message": message.translated()}

        result = await asyncio.to_thread(purge.delete_old)

        deleted = result["deleted"]
        scanner.finish_task_log(
            scan_id, "ok", total=deleted, ok=deleted,
            # Days are at least purge.MIN_DAYS (30), so one form of the word serves.
            message=(i18n.Message("Cleared out {n} playbacks older than {days} days.", n=deleted, days=result["days"])
                     if deleted else
                     i18n.Message("Nothing here is older than {days} days.", days=result["days"])))
    except Exception as exc:  # noqa: BLE001 - an unexpected error belongs into the log too
        scanner.finish_task_log(scan_id, "error", message=exc)
        raise
    return {"status": "ok", **result}


async def _run_updates() -> dict[str, Any]:
    """Asks GitHub whether a newer release came out.

    `forced=True` on purpose: when to ask is decided by this task's schedule,
    not by the own daily brake inside `updates`. That one would otherwise
    decide twice and the time set in the schedule would not hold.
    """
    from . import updates

    # The run goes into the task log: Settings → Tasks shows when it last ran and how it ended.
    scan_id = scanner.start_task_log("updates")
    try:
        result = await updates.check(forced=True)
    except Exception as error:   # noqa: BLE001 - a run in the tasks' log must not stay hanging as "running"
        scanner.finish_task_log(scan_id, "error", message=error)
        raise
    if result.get("status") == "error":
        scanner.finish_task_log(scan_id, "error", message=result.get("message") or "The check failed.")
        return {"status": "error",
                "message": result.get("message") or _t("The check failed.")}
    if result.get("is_newer"):
        message = i18n.Message("Version {version} is available.", version=result.get("found_one") or "?")
        scanner.finish_task_log(scan_id, "ok", message=message)
        return {"status": "ok", "message": message.translated()}
    scanner.finish_task_log(scan_id, "ok", message="You are running the latest release.")
    return {"status": "ok", "message": _t("You are running the latest release.")}


def rotation_days() -> int:
    """After how many days the logs turn (Tasks → Logs)."""
    return db.get_int_setting(applog.DAYS_KEY, 1, applog.MAX_DAYS, applog.DEFAULT_DAYS)


def rotation_count() -> int:
    """How many old logs are kept (Tasks → Logs)."""
    return db.get_int_setting(applog.COUNT_KEY, 1, applog.MAX_COUNT,
                              applog.DEFAULT_COUNT)


def _last_rotation() -> date | None:
    try:
        return date.fromisoformat(db.get_setting(applog.LAST_ROTATION_KEY, "").strip())
    except ValueError:
        return None


def next_rotation() -> date | None:
    """When the logs turn next time. None = at the task's nearest time."""
    last = _last_rotation()
    return last + timedelta(days=rotation_days()) if last else None


async def _rotate_logs() -> dict[str, Any]:
    """Turns the application and API logs at once - the manual "Run now" too."""
    scan_id = scanner.start_task_log("logrotate")
    try:
        count = rotation_count()
        result = await asyncio.to_thread(applog.rotate, count)
        db.set_setting(applog.LAST_ROTATION_KEY, date.today().isoformat())
        message = i18n.Message("Rotated: {files}. {count} old logs are kept.",
                               files=", ".join(result["rotated"]) or "-", count=count)
        if result["errors"]:
            message = i18n.join_messages([message, "; ".join(result["errors"])])
            log.warning("log rotation: %s", "; ".join(result["errors"]))
        state = "error" if result["errors"] and not result["rotated"] else "ok"
        scanner.finish_task_log(scan_id, state, message=message)
    except Exception as exc:  # noqa: BLE001 - the run in the task log must not stay "running"
        scanner.finish_task_log(scan_id, "error", message=exc)
        raise
    log.info("log rotation: %s", ", ".join(result["rotated"]) or "-")
    return {"status": state, "message": message.translated()}


async def _run_logrotate() -> dict[str, Any]:
    """The scheduled rotation: daily at the set time, but it turns only after N days.

    The day is counted by the calendar from the last rotation (a manual one
    too), so it always turns at the task's time - not at the moment somebody
    once clicked.
    """
    upcoming = next_rotation()
    if upcoming is not None and date.today() < upcoming:
        return {"status": "ok", "skipped": True}
    return await _rotate_logs()


async def _run_notifications() -> dict[str, Any]:
    """Goes through the watched things and sends what is needed.

    An interval task, not a daily one: when the collector stops collecting, it
    should be known today, not tomorrow morning. All the work and the
    protection against repeating is in notifications.check().
    """
    from . import notifications

    # The run goes into the task log like any other: by it the scheduler tells when the next one is due.
    scan_id = scanner.start_task_log("notifications")
    try:
        result = await notifications.check()
    except Exception as error:   # noqa: BLE001 - a run in the tasks' log must not stay hanging as "running"
        scanner.finish_task_log(scan_id, "error", message=error)
        raise
    scanner.finish_task_log(scan_id, "error" if result.get("status") == "error" else "ok",
                            message=result.get("task_log") or "")
    return result


async def _run_arr() -> dict[str, Any]:
    """Pulls the new history from all connected *Arr apps.

    All the work is in arr.registry.synchronize_all() - the synchronisation of
    one app is started by the "Sync now" button in Settings → *Arr
    too. Here the run is only written into the tasks' log (by it the scheduler
    tells when the next one is due) and the result is translated into a sentence.
    """
    from .arr import registry  # only here: the apps pull in the httpx client and its settings

    scan_id = scanner.start_task_log("arr")
    if not registry.connected():
        message = "No *Arr app is connected."  # the key: the task log is read in the reader's language
        scanner.finish_task_log(scan_id, "ok", message=message)
        return {"status": "ok", "message": message}

    results = await registry.synchronize_all()
    parts, failed = [], 0
    for app, result in results.items():
        name = registry.APPS[app].name
        if result.get("running"):
            # The "Sync now" button and the task met: it is pulling already.
            parts.append(i18n.Message("{name}: sync already running", name=name))
        elif "error" in result:
            failed += 1
            parts.append(i18n.Message("{name}: error - {error}", name=name, error=result["error"]))
        else:
            parts.append(i18n.Message("{name}: {n} new records", name=name, n=result["new_count"]))
    message = i18n.join_messages(parts, "; ")
    state = "error" if failed else "ok"
    scanner.finish_task_log(scan_id, state, total=len(results), ok=len(results) - failed, failed=failed,
                            message=message)
    return {"status": state, "message": message.translated()}


async def _run_seerr() -> dict[str, Any]:
    """Pulls all requests from Seerr (seerr.synchronize).

    The "Sync now" button in Settings → Seerr starts the same
    function. Here the run is only written into the tasks' log (by it the
    scheduler tells when the next one is due) and the result is translated
    into a sentence.
    """
    from . import seerr  # only here: seerr pulls in the httpx client and its settings

    scan_id = scanner.start_task_log("seerr")
    if not seerr.is_connected():
        message = "Seerr is not connected."  # the key: the task log is read in the reader's language
        scanner.finish_task_log(scan_id, "ok", message=message)
        return {"status": "ok", "message": message}
    try:
        result = await seerr.synchronize()
    except seerr.SeerrError as error:
        # ALREADY_RUNNING: the button and the task met - it is pulling already.
        state = "ok" if error.template == seerr.sync.ALREADY_RUNNING else "error"
        scanner.finish_task_log(scan_id, state, message=error)
        return {"status": state, "message": i18n.error_text(error)}
    except Exception as error:   # noqa: BLE001 - a run in the tasks' log must not stay hanging as "running"
        # A locked database, an error in a conversion... The synchronisation
        # wrote the state already; here the run is ended, so the scheduler
        # knows when the next one is due.
        log.exception("Seerr sync failed")
        scanner.finish_task_log(scan_id, "error", message=error)
        return {"status": "error", "message": i18n.error_text(error)}
    message = i18n.Message("{n} requests added, {total} in Seerr, {deleted} deleted.", n=result["new_count"],
                           total=result["total"], deleted=result["deleted_count"])
    scanner.finish_task_log(scan_id, "ok", total=result["total"], message=message)
    return {"status": "ok", "message": message.translated()}


async def _run_music() -> dict[str, Any]:
    """Pulls the artists, albums and tracks of Jellyfin's music libraries (music.synchronize).

    Here the run is only written into the tasks' log (by it the scheduler
    tells when the next one is due) and the result is translated into a sentence.
    """
    from . import music  # only here: music pulls in the Jellyfin client

    scan_id = scanner.start_task_log("music")
    try:
        result = await music.synchronize()
    except music.MusicError as error:
        state = "ok" if error.template == music.ALREADY_RUNNING else "error"
        scanner.finish_task_log(scan_id, state, message=error)
        return {"status": state, "message": i18n.error_text(error)}
    except Exception as error:   # noqa: BLE001 - a run in the tasks' log must not stay hanging as "running"
        log.exception("music sync failed")
        scanner.finish_task_log(scan_id, "error", message=error)
        return {"status": "error", "message": i18n.error_text(error)}
    if not result.get("library_count"):
        message = "Jellyfin has no music library."  # the key: the task log is read in the reader's language
        scanner.finish_task_log(scan_id, "ok", message=message)
        return {"status": "ok", "message": message}
    message = i18n.Message("Music: new items {n}; read artists {artists}, albums {albums}, tracks {tracks}.",
                           n=result["new_count"], artists=result["artist_count"], albums=result["album_count"],
                           tracks=result["track_count"])
    scanner.finish_task_log(scan_id, "ok", total=result["track_count"], message=message)
    return {"status": "ok", "message": message.translated()}


async def _run_books() -> dict[str, Any]:
    """Pulls the e-books and audiobooks of Jellyfin's books libraries (books.synchronize).

    Here the run is only written into the tasks' log (by it the scheduler
    tells when the next one is due) and the result is translated into a sentence.
    """
    from . import books  # only here: books pulls in the Jellyfin client

    scan_id = scanner.start_task_log("books")
    try:
        result = await books.synchronize()
    except books.BooksError as error:
        state = "ok" if error.template == books.ALREADY_RUNNING else "error"
        scanner.finish_task_log(scan_id, state, message=error)
        return {"status": state, "message": i18n.error_text(error)}
    except Exception as error:   # noqa: BLE001 - a run in the tasks' log must not stay hanging as "running"
        log.exception("books sync failed")
        scanner.finish_task_log(scan_id, "error", message=error)
        return {"status": "error", "message": i18n.error_text(error)}
    if not result.get("library_count"):
        message = "Jellyfin has no books library."  # the key: the task log is read in the reader's language
        scanner.finish_task_log(scan_id, "ok", message=message)
        return {"status": "ok", "message": message}
    message = i18n.Message("Books: new items {n}; read items {items}.", n=result["new_count"], items=result["item_count"])
    scanner.finish_task_log(scan_id, "ok", total=result["item_count"], message=message)
    return {"status": "ok", "message": message.translated()}


async def _run_tidy() -> dict[str, Any]:
    """Tidying the data in the background.

    All the work is in importers.tidy_data() - here the result is only
    translated into the sentence you see by the task. The button in Settings
    starts the same function, so the manual and the automatic run cannot part
    ways.
    """
    from . import importers  # only here - importers pull in scanner and tasks

    # The run goes into the task log: Settings → Tasks shows when it last ran and how it ended.
    scan_id = scanner.start_task_log("tidy")
    try:
        result = await importers.tidy_data()
    except Exception as error:   # noqa: BLE001 - a run in the tasks' log must not stay hanging as "running"
        scanner.finish_task_log(scan_id, "error", message=error)
        raise
    if not result["parts"]:
        scanner.finish_task_log(scan_id, "ok", message="There was nothing to tidy, the history is in order.")
        return {"status": "ok",
                "message": _t("There was nothing to tidy, the history is in order.")}

    message = i18n.join_messages([i18n.Message(template, **values) for template, values in result["parts"]], ", ")
    if result["left"]:
        message = i18n.join_messages([message, i18n.Message("{n} records remain unassigned.", n=result["left"])], ". ")
    scanner.finish_task_log(scan_id, "ok", message=message)
    return {"status": "ok", "message": message.translated()}


TASKS: dict[str, Task] = {
    task.key: task
    for task in [
        Task(
            key="sync",
            name="Library sync",
            description=(
                "Downloads the list of users, libraries and titles from "
                "Jellyfin. When ffprobe is picked in Data collection, an "
                "analysis of files without technical data follows."
            ),
            time_setting="library_sync_time",
            default_time="03:30",
            runner=_run_sync,
            log_kind="library",
        ),
        Task(
            key="recent",
            name="Recently added titles",
            description=(
                "Downloads only titles that are not in the library yet, by the date of "
                "the last one added. It barely touches Jellyfin, so the full sync can run far "
                "less often. With ffprobe as the data source it also measures the new files "
                "right away, so they do not wait for the next analysis."
            ),
            interval_setting="recent_sync_minutes",
            default_minutes=15,
            runner=_run_recent,
            log_kind="recent",
        ),
        Task(
            key="tidy",
            name="Data tidy-up",
            description=(
                "Puts both the history and the library straight: asks Jellyfin about records that lead nowhere, "
                "links them by name and episode number, merges duplicates, aligns names and brings back from the "
                "archive the episodes that are in the library again (typically after a file was replaced). It "
                "deletes nothing, it only repairs the links."
            ),
            time_setting="task_tidy_time",
            default_time="04:00",
            runner=_run_tidy,
            log_kind="tidy",
        ),
        Task(
            key="notifications",
            name="Notifications",
            description=(
                "Checks that the collector is collecting and that the disk is not filling up, and on the chosen "
                "day sends the weekly summary. A message goes out on change only. A watchdog that keeps "
                "talking gets muted, and then the message that mattered is missed. Where it sends and what it "
                "watches is in Notifications."
            ),
            interval_setting="notifications_minutes",
            default_minutes=15,
            runner=_run_notifications,
            log_kind="notifications",
        ),
        Task(
            key="arr",
            name="*Arr sync",
            description=(
                "Downloads new history from Sonarr, Radarr, Lidarr and Readarr: what was grabbed, from where, what was imported into "
                "the library and what was replaced by a better version. From Bazarr the subtitle history, from Prowlarr "
                "the queries to indexers and the grabbed releases. The whole history the first time, then only what is new. "
                "Apps are connected in Settings → *Arr; until one is connected, the task does nothing."
            ),
            interval_setting="arr_sync_minutes",
            default_minutes=60,
            runner=_run_arr,
            log_kind="arr",
        ),
        Task(
            key="seerr",
            name="Seerr sync",
            description=(
                "Downloads all movie and show requests from Seerr: who asked for what and when, how "
                "the request was handled and when the title reached the library. Jellyscope keeps a "
                "request deleted in Seerr. Seerr is connected in Settings → Seerr; until then the task does nothing."
            ),
            interval_setting="seerr_sync_minutes",
            default_minutes=60,
            runner=_run_seerr,
            log_kind="seerr",
        ),
        Task(
            key="music",
            name="Music sync",
            description=(
                "Downloads artists, albums and tracks from Jellyfin's music libraries: format, size, length and "
                "genres. Each run reads only what was added; once a day it goes through everything and notices what "
                "disappeared. Without a music library in Jellyfin it does nothing."
            ),
            interval_setting="music_sync_minutes",
            default_minutes=60,
            runner=_run_music,
            log_kind="music",
        ),
        Task(
            key="books_sync",
            name="Books sync",
            description=(
                "Downloads e-books and audiobooks from Jellyfin's books libraries: author, format, size and "
                "length. Each run reads only what was added; once a day it goes through everything and notices "
                "what disappeared. Without a books library in Jellyfin it does nothing."
            ),
            interval_setting="books_sync_minutes",
            default_minutes=60,
            runner=_run_books,
            log_kind="books",
        ),
        Task(
            key="updates",
            name="Check for updates",
            description=(
                "Asks GitHub whether a newer release is out. It installs nothing, it only tells "
                "you. It is the only connection anywhere other than Jellyfin, which is why it is off "
                "by default."
            ),
            time_setting="task_updates_time",
            default_time="05:00",
            runner=_run_updates,
            log_kind="updates",
        ),
        Task(
            key="purge",
            name="Clearing out the history",
            description=(
                "Deletes playbacks older than the limit that is set. It is off by default and has to be "
                "switched on by hand, together with how much history is kept. Playback that is "
                "running is not deleted, so the collector does not lose a session it has "
                "half-written. A database backup is taken first; without it nothing is deleted."
            ),
            time_setting="task_purge_time",
            default_time="04:45",
            runner=_run_purge,
            log_kind="purge",
        ),
        Task(
            key="logrotate",
            name="Log rotation",
            description=(
                "Puts the application log and the API log aside and starts new ones. How often that "
                "happens and how many old logs stay is under Logs below. Older ones are deleted; until "
                "then they can be downloaded in Settings → Log."
            ),
            time_setting="task_logrotate_time",
            default_time="00:05",
            runner=_run_logrotate,
            manual_runner=_rotate_logs,
            next_run=next_rotation,
            log_kind="logrotate",
        ),
        Task(
            key="backup",
            name="Database backup",
            description="Saves a copy of the database into the chosen folder and deletes surplus older backups.",
            time_setting="task_backup_time",
            default_time="04:30",
            runner=_run_backup,
            log_kind="backup",
        ),
    ]
}


# ---------------------------------------------------------------------------
# Database backup
# ---------------------------------------------------------------------------

# A suffix in the name of a backup made before deleting - so it is told apart
# from the night ones in the folder. It stays between "jellyscope-" and ".db",
# so the cleanup and the restore take it like any other.
BACKUP_BEFORE_DELETE = "before-delete"
# The same backup made before 1.9.0: still in the folder, it keeps its own queue too (the night ones must not lose
# their places to it). Known for good - old files stay on the disk.
OLD_BACKUP_BEFORE_DELETE = "pred-mazanim"
# The backup of the current state a restore makes before it overwrites the database.
BACKUP_BEFORE_RESTORE = "before-restore"
# The backup jellyscope.conversion makes before it renames the database: the way back to 1.8.x. Out of the
# usual pruning (backup_keep would push it out in a week), deleted after CONVERSION_BACKUP_DAYS days.
BACKUP_BEFORE_CONVERSION = "before-conversion"
CONVERSION_BACKUP_DAYS = 30


def backup_folder(reason: str = "") -> str:
    """Where backups go. Empty = nowhere (and that is an error for a regular backup).

    The backup before deleting has a fallback folder beside the database:
    deleting must not depend on whether somebody filled in a path - either a
    backup is made, or nothing is deleted.
    """
    target = db.get_setting("backup_path", "").strip()
    if not target and reason:
        target = str(fallback_backup_folder())
    return target


def fallback_backup_folder() -> Path:
    """Where the backup before deleting goes when no folder is set: data/backups beside the application - or
    data/zalohy, the folder of an installation from before 1.9.0, while it exists."""
    old = BASE_DIR / "data" / "zalohy"
    return old if old.is_dir() else BASE_DIR / "data" / "backups"


def shown_folder(path: Path) -> str:
    """A folder as Settings shows it: relative to the application when it lies in it (data/backups)."""
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


async def backup_before_delete() -> dict[str, Any]:
    """The backup without which nothing is deleted.

    It is called before the night clearing out and before forgetting a viewer
    by hand. Whoever set the boundary wrong or clicked the wrong name has
    somewhere to go back to. The caller should look at `status`: anything but
    "ok" means do not delete and say why.
    """
    return await backup_database(reason=BACKUP_BEFORE_DELETE)


async def backup_database(reason: str = "") -> dict[str, Any]:
    """Stores a copy of the database into the folder from the settings (see _write_backup); no restore beside it."""
    with data_job():
        return await _write_backup(reason)


async def _write_backup(reason: str) -> dict[str, Any]:
    """Stores a copy of the database into the folder from the settings.

    Each database is backed up by its own tool - SQLite by the built-in
    function `backup()`, PostgreSQL by the program `pg_dump`. Both take a
    consistent snapshot while running, which plain copying of the file cannot.

    `reason` is appended to the file's name (the backup before deleting) and
    allows a fallback folder when none is set - see backup_folder().
    """
    target = backup_folder(reason)
    if not target:
        failure = i18n.Message("No folder for backups is set.")
        return {"status": "error", "message": failure.translated(), "reason": failure}

    target_dir = Path(target)
    scan_id = scanner.start_task_log("backup")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        scanner.finish_task_log(scan_id, "error", message=exc)
        failure = i18n.Message("The folder cannot be created: {error}", error=exc)
        return {"status": "error", "message": failure.translated(), "reason": failure}

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    database = db.database_config()

    # Each database is backed up its own way. SQLite has backing up built in,
    # PostgreSQL has its own tool pg_dump for it.
    name = f"jellyscope-{stamp}" + (f"-{reason}" if reason else "")
    suffix = ".sql" if database.is_postgres else ".db"
    # The stamp has seconds. Two backups in the same second (forgetting twice
    # in a row) would otherwise overwrite each other - and the first one is
    # exactly the one a person will want to go back to.
    destination = _free_name(target_dir, name, suffix)
    if database.is_postgres:
        runner = lambda: _write_whole(lambda path: _dump_postgres(database, path), destination)  # noqa: E731
    else:
        runner = lambda: _write_whole(lambda path: _backup_sqlite(database, path), destination)  # noqa: E731

    try:
        size = await asyncio.to_thread(runner)
    except Exception as exc:  # noqa: BLE001
        log.exception("the backup failed")
        scanner.finish_task_log(scan_id, "error", message=exc)
        failure = i18n.Message("The backup failed: {error}", error=exc)
        return {"status": "error", "message": failure.translated(), "reason": failure}

    # A backup is the whole database - the accounts' passwords included (if
    # only as fingerprints) and who played what when. For the same reason as
    # database.json, then, only for the owner. On Windows there is no `chmod`,
    # the folder's rights hold there.
    try:
        destination.chmod(0o600)
    except OSError:
        pass

    removed = _prune_backups(target_dir)
    written = f"{destination.name} ({size / 1024 / 1024:.1f} MB)"

    scanner.finish_task_log(
        scan_id, "done", total=1, ok=1,
        message=(i18n.Message("{file}, older ones deleted: {removed}", file=written, removed=removed)
                 if removed else written),
    )
    return {"status": "ok", "file": str(destination), "size": size, "removed": removed}


def _free_name(folder: Path, name: str, suffix: str) -> Path:
    """The first free name: name, name-2, name-3… (also free of a backup being written under it, .partial)"""
    target = folder / f"{name}{suffix}"
    number = 2
    while target.exists() or _partial(target).exists():
        target = folder / f"{name}-{number}{suffix}"
        number += 1
    return target


def _partial(destination: Path) -> Path:
    """The temporary name a backup is written under until it is whole."""
    return destination.with_name(destination.name + ".partial")


def narrow_private_files() -> int:
    """At the start: what holds secrets and exists from before is made its owner's only. Returns how many paths.

    The launchers set umask 077, so whatever the app creates is its owner's only - but an installation from before
    kept its database (the apps' keys and passwords readably, the accounts' password hashes) with its -wal and
    -shm, the logs and the backups readable to every account on the machine (644). Files get 600, their folders in
    data/ 700; a backup folder of the administrator's own outside data/ keeps its rights (it may be shared on
    purpose), its backups are narrowed. Only the rights change - nothing is rewritten, moved or opened; only what
    the process owns; only what really lies in data/ (a folder in it that is a link out is not followed), apart
    from the database and the backup folder set by the administrator; a failure is logged and skipped, the start
    goes on as before. Windows has no such rights - the folder's access holds there.
    """
    if os.name == "nt":
        return 0
    outcomes: list[str] = []
    try:
        data = (BASE_DIR / "data").resolve()
        files, folders, configured = _private_paths(data)
        for path in files + folders:
            outcomes.append(_narrow(path, data, path in configured))
    except Exception as exc:  # noqa: BLE001 - the start must go on whatever happens here
        log.warning("the rights of the data files could not be narrowed: %s", exc)
    if outcomes.count("ignored"):
        log.warning("the file system ignores the rights: %s data files and folders stay readable to other accounts",
                    outcomes.count("ignored"))
    return outcomes.count("narrowed")


def _private_paths(data: Path) -> tuple[list[Path], list[Path], set[Path]]:
    """The files with secrets and the folders in data/ that hold them (narrow_private_files); missing ones too.

    The third: the files set by the administrator (the database, the backups in the backup folder of Settings),
    narrowed also when they lie outside data/.
    """
    files = [BASE_DIR / "data" / "secret_key", BASE_DIR / "data" / "database.json"]
    configured: set[Path] = set()
    database = db.database_config()
    if not database.is_postgres:
        path = Path(database.path)
        path = path if path.is_absolute() else BASE_DIR / path
        configured |= {path.with_name(path.name + suffix) for suffix in ("", "-wal", "-shm", "-journal")}
    update_backups = BASE_DIR / "data" / "before-update"       # deploy/update.sh's copies of the database
    folders = [BASE_DIR / "data", applog.log_dir(), update_backups]
    files += _entries(applog.log_dir(), "*") + _entries(update_backups, "*")
    target = db.get_setting("backup_path", "").strip()
    for folder in {fallback_backup_folder(), *([Path(target)] if target else [])}:
        backups = _entries(folder, "jellyscope-*.db") + _entries(folder, "jellyscope-*.sql")
        if target and folder == Path(target):
            configured |= set(backups)
        else:
            files += backups
        if folder.resolve().is_relative_to(data):
            folders.append(folder)
    return files + sorted(configured), folders, configured


def _entries(folder: Path, pattern: str) -> list[Path]:
    """What lies in a folder under a pattern, folders left out; nothing when the folder cannot be read."""
    try:
        return [path for path in folder.glob(pattern) if not path.is_dir()]
    except OSError:
        return []


def _narrow(path: Path, data: Path, configured: bool = False) -> str:
    """Takes the group's and the others' rights off one path the process owns.

    Only a path that really lies in data/ - a configured one anywhere, unless it is itself a link out. Returns
    "narrowed", "ignored" (chmod went through and the rights stayed: a file system without them, CIFS or drvfs)
    or "" (left as it was).
    """
    try:
        if not path.resolve().is_relative_to(data) and (path.is_symlink() or not configured):
            return ""
        found = path.stat()
        if found.st_uid != os.geteuid() or not found.st_mode & 0o077:
            return ""
        path.chmod(found.st_mode & 0o700)
        return "ignored" if path.stat().st_mode & 0o077 else "narrowed"
    except FileNotFoundError:
        return ""
    except (OSError, RuntimeError) as exc:
        log.warning("the rights of %s could not be narrowed: %s", path, exc)
        return ""


def remove_partial_backups() -> int:
    """At the start: deletes the backups a previous process left half-written (*.partial). Returns how many.

    `_write_whole` deletes its temporary file when the write fails, but a process that ends in the middle of it
    (a restart through execv, a stopped container, a crash) gets no exception: the file stayed for good - the size
    of the database each time, seen by neither Settings nor the pruning. Called before anything writes a backup.
    """
    target = db.get_setting("backup_path", "").strip()
    folders = {fallback_backup_folder(), *([Path(target)] if target else [])}
    removed = 0
    for folder in folders:
        try:
            leftovers = list(folder.glob("jellyscope-*.partial"))
        except OSError:
            continue
        for leftover in leftovers:
            try:
                leftover.unlink()
                removed += 1
            except OSError as exc:
                log.warning("backup %s could not be deleted: %s", leftover.name, exc)
    return removed


def _write_whole(write: Callable[[Path], int], destination: Path) -> int:
    """Writes a backup by `write` under a temporary name and gives it its own only when it is whole.

    A backup that fails half-way (a full disk, PostgreSQL gone in the middle of the export) must not stay
    with a backup's name: Settings would offer it for a restore and the pruning would count it, pushing
    out the good ones. The same as jellyscope.conversion.backup_before.
    """
    partial = _partial(destination)
    try:
        size = write(partial)
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return size


def _backup_sqlite(config: Any, destination: Path) -> int:
    """A SQLite backup through the built-in function `backup()`.

    Not plain copying of the file: when the database is being written to just
    then, a copy of the bytes can catch an unfinished transaction and a damaged
    file comes about. The built-in backup makes a consistent snapshot even
    while running.
    """
    path = Path(config.path)
    if not path.is_absolute():
        path = BASE_DIR / path

    source = sqlite3.connect(path)
    try:
        # Born private: the copy is the whole database (the accounts' password hashes, the apps' keys) - not
        # readable to others even while it is being written. An existing file (a partial one) keeps its rights.
        destination.touch(mode=0o600)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return destination.stat().st_size


# Where PostgreSQL's client tools lie on the single systems.
#
# On Debian and Ubuntu they can be installed **side by side in more
# versions** and in PATH there is usually the oldest one. Exactly that is why
# it happens that on Ubuntu 20.04 (client 12) a backup of server 17 fails with
# the message "aborting because of server version mismatch": pg_dump can back
# up a server of the same or an older version, never a newer one.
PG_BIN_PATTERNS = (
    "/usr/lib/postgresql/*/bin/pg_dump",     # Debian, Ubuntu
    "/usr/pgsql-*/bin/pg_dump",              # RHEL, Rocky, Alma
    "/opt/homebrew/opt/postgresql@*/bin/pg_dump",
    "/usr/local/opt/postgresql@*/bin/pg_dump",
)


def _tool_version(path: str) -> int:
    """The major version number of pg_dump. Returns 0 when it cannot be found out."""
    try:
        result = subprocess.run([path, "--version"], capture_output=True,
                                timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0
    text = result.stdout.decode("utf-8", errors="replace")
    match = re.search(r"(\d+)(?:\.\d+)?", text)
    return int(match.group(1)) if match else 0


def available_pg_dumps(configured_path: str = "") -> list[tuple[int, str]]:
    """All pg_dump there are on the machine - from the newest.

    A path set by hand takes precedence over everything else; whoever fills it
    in knows what they want.
    """
    found: dict[str, int] = {}

    if configured_path:
        path = Path(configured_path)
        if path.is_dir():
            path = path / "pg_dump"
        if path.is_file():
            return [(_tool_version(str(path)), str(path))]

    on_path = shutil.which("pg_dump")
    if on_path:
        found[on_path] = _tool_version(on_path)

    for pattern in PG_BIN_PATTERNS:
        for hit in glob.glob(pattern):
            if hit not in found and os.access(hit, os.X_OK):
                found[hit] = _tool_version(hit)

    return sorted(((version, path) for path, version in found.items()),
                  reverse=True)


def server_version() -> int:
    """The major version of the PostgreSQL server. 0 = we do not know (or it is SQLite)."""
    return _server_major(None)


def _server_major(config: Any) -> int:
    """The major version of the server we connect to. 0 = we do not know."""
    try:
        number = db.query_value("SHOW server_version_num", default=0)
        return int(number) // 10000 if number else 0
    except Exception:  # noqa: BLE001
        return 0


def _choose_pg_dump(config: Any) -> str:
    """Chooses a pg_dump this server can manage - or gives advice."""
    candidates = available_pg_dumps(db.get_setting("pg_dump_path", "").strip())
    server = _server_major(config)

    if not candidates:
        raise BackupError(
            "pg_dump could not be found. It is part of PostgreSQL's client tools - install them, or back up "
            "the database your own way and switch this task off.")

    # The newest that is enough for the server. When we do not know the
    # server's version, we simply take the newest available - it is the best guess.
    for version, path in candidates:
        if not server or version >= server:
            return path

    best, where = candidates[0]
    raise BackupError(
        "The installed pg_dump is version {installed}, but the server has version {server}. pg_dump can back "
        "up only a server of the same or an older version. Install the client {server} - on Debian and "
        "Ubuntu:\n    sudo apt install -y postgresql-client-{server}\n(it needs the PGDG repository: "
        "https://www.postgresql.org/download/)\nInstalled versions: {versions}. The path to the right "
        "pg_dump can also be filled in in Settings → Tasks.",
        installed=best, server=server, versions=", ".join(f"{v} ({c})" for v, c in candidates))


# The tables a backup is made of - all from the schema, in the order they can
# be filled in (the list is in db.TABLES, the database conversion shares it).
# A restore deletes them in the reverse order.
BACKED_UP_TABLES = db.TABLES


def _sql_value(value: Any) -> str:
    """One value written so that PostgreSQL takes it back.

    Strings are closed in apostrophes and an apostrophe inside is doubled -
    that is the standard notation and with the default
    `standard_conforming_strings` (on since version 9.1) it holds for
    backslashes too, which thus stay an ordinary character. Nothing but texts,
    numbers and NULL is in our schema.

    A text with a carriage return goes as an escape string (E'…\r…'): a restore
    that reads the file as text - the one of 1.8.x does - turns a bare \r and
    \r\n into \n (error pages of nginx in the task log end their lines with
    \r\n). In E'' a backslash is an escape, so it is doubled there.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    # PostgreSQL does not take the NUL character in a text even in a string - we drop it.
    text = str(value).replace("\x00", "")
    if "\r" in text:
        return "E'" + text.replace("\\", "\\\\").replace("'", "''").replace("\r", "\\r") + "'"
    return "'" + text.replace("'", "''") + "'"


def _read_export(source: Path) -> str:
    """An own export as it is written: without translating line ends (a text-mode read turns \r into \n - an
    export of 1.8.x and one before 1.9.0 have bare carriage returns in their texts).

    An older export made on Windows is the exception: text mode wrote every \n as \r\n there, inside a text
    too, so its header line ends with \r\n. It is read with universal newlines, as before - kept as it is,
    every line feed of a text would come back as \r\n. (Our writers write \n on every system now.)"""
    with source.open(encoding="utf-8", newline="") as file:
        content = file.read()
    first_line = content[:content.find("\n") + 1]
    if first_line.startswith("--") and first_line.endswith("\r\n"):
        return content.replace("\r\n", "\n").replace("\r", "\n")
    return content


def _dump_own(destination: Path) -> int:
    """A PostgreSQL backup by our own means, when pg_dump is not available.

    `pg_dump` is the better tool and stays the first choice. But it cannot
    always be got at: it can back up only a server of the same or an older
    version, and on an older system a newer client may not be installable at
    all - the PGDG repository for Ubuntu 20.04 no longer exists, because that
    version's support ended.

    Refusing to back up in such a situation would be the worst solution.
    Jellyscope knows its tables - it created them itself by `schema.sql` - and
    can export them itself: the schema plus the data as plain INSERTs. The
    result is restored with a simple `psql -f`.

    What this backup **does not contain**: anything someone else added into
    the database (foreign tables, views, functions, permissions). For a
    database Jellyscope uses for itself it is a complete backup; when you have
    something of your own in it too, use pg_dump.

    All tables are read **with one connection in one transaction** with a
    snapshot of the database (REPEATABLE READ) - as pg_dump does. Tables read
    each on its own could part ways when the synchronisation writes something
    between two of them: the backup would hold the old mark "what is counted
    already" (settings) and already new hourly sums of Prowlarr, and after a
    restore the same events would be counted a second time.
    """
    from . import db as _db          # only here, so no circular import comes about

    row_total = 0
    with destination.open("w", encoding="utf-8", newline="\n") as out, _db.connect() as conn:
        if conn.is_postgres:
            # Must be the transaction's first statement: from the first query on it reads from the snapshot.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        out.write(
            f"{OWN_BACKUP_MARKS[0]} (own export)\n"
            f"-- created: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            "--\n"
            "-- Restore:  psql -U <user> -d <database> -f this-file.sql\n"
            "--\n"
            "-- Holds the schema and the data of the tables of Jellyscope. Foreign tables,\n"
            "-- views and permissions are not in it - that is what pg_dump is for.\n\n"
            # Without this it would depend on the settings of the server being
            # restored into. Texts are escaped by doubling the apostrophe (see
            # _sql_value) and that holds only when a backslash is an ordinary
            # character. With `standard_conforming_strings = off` a title's
            # name `\'; DROP TABLE ...` from Jellyfin would end the string and
            # psql would run the rest as a statement. The default has been on
            # since 9.1 - here it is left to us, though, not to the server.
            "SET standard_conforming_strings = on;\n"
            "BEGIN;\n\n"
        )

        # The schema is taken from the same file the database on PostgreSQL is
        # created from, so the backup cannot part ways with the real shape of
        # the tables. Literally, without translating: `dialect.translate()` is
        # for queries with parameters (question marks, per cents), not for a
        # file psql should read.
        #
        # It used to take schema.sql for SQLite here and translate it - but
        # PostgreSQL does not know `AUTOINCREMENT`, so the backup could not be
        # restored at all. It was found out only by a restore on a real server.
        out.write(_db.SCHEMA_POSTGRES.read_text(encoding="utf-8"))
        out.write("\n\n")
        # Columns that came only with a migration (db.MIGRATIONS): the schema
        # does not know them and without them the INSERTs below would not go
        # into a clean database. `IF NOT EXISTS`, so a restore into a running
        # database breaks nothing.
        for table, columns in _db.MIGRATIONS.items():
            for column, kind in columns.items():
                out.write(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                          f"{column} {kind};\n")
        out.write("\n")

        for table in BACKED_UP_TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                continue
            columns = list(rows[0].keys())
            out.write(f"-- {table}: {len(rows)} rows\n")
            for row in rows:
                values = ", ".join(_sql_value(row[s]) for s in columns)
                out.write(
                    f"INSERT INTO {table} ({', '.join(columns)})"
                    f" VALUES ({values}) ON CONFLICT DO NOTHING;\n"
                )
            out.write("\n")
            row_total += len(rows)

        out.write("COMMIT;\n")

    log.info("built-in PostgreSQL backup: %d rows", row_total)
    return destination.stat().st_size


def _dump_postgres(config: Any, destination: Path) -> int:
    """A PostgreSQL backup through `pg_dump`.

    We could write our own copying of the tables, but it would be a worse
    backup than the one the database can do itself - `pg_dump` handles a
    consistent snapshot, the order of dependencies and the indexes. When the
    tool is missing, we say so out loud instead of making an incomplete backup
    and pretending it is done.

    Which pg_dump is used is chosen by `_choose_pg_dump()` - that matters more
    than one would expect, see the comment at PG_BIN_PATTERNS.
    """
    # When no usable pg_dump is found, not backing up at all would be the
    # worst solution - see `_dump_own()`.
    try:
        tool = _choose_pg_dump(config)
    except RuntimeError as exc:
        log.warning("pg_dump cannot be used (%s), falling back to the built-in export", exc)
        return _dump_own(destination)
    return _pg_dump(tool, config, destination)


def _pg_dump(tool: str, config: Any, destination: Path) -> int:
    """Runs pg_dump `tool` into `destination`. Returns the size; an error is a BackupError."""
    environment = dict(os.environ)
    # The password is passed in an environment variable, not on the command
    # line - a process's arguments are visible to everyone on the machine.
    if config.password:
        environment["PGPASSWORD"] = config.password

    # pg_dump opens the file itself (the -f switch), instead of us slipping it
    # our own descriptor through redirecting the output.
    #
    # The redirect was here originally and in some environments failed with
    # "could not write to output file: Bad file descriptor". A process that
    # gets someone else's descriptor may not be able to work with it -
    # especially when the application runs under a service manager that does
    # its own things with the standard outputs. With `-f` pg_dump is
    # responsible for the file and a possible error is its own, understandable
    # ("could not open output file ...: Permission denied").
    result = subprocess.run(
        [tool, "-h", config.host, "-p", str(config.port),
         "-U", config.user, "-d", config.database, "--no-password",
         "-f", str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=1800,
        check=False,
    )

    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        if detail:
            raise BackupError("pg_dump reported an error: {detail}", detail=detail)
        raise BackupError("pg_dump ended with code {code}.", code=result.returncode)

    # An empty file is worse than none: it would pass for a finished backup.
    if not destination.exists() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise BackupError("pg_dump ended without an error, but the file is empty.")

    return destination.stat().st_size


def _prune_backups(directory: Path) -> int:
    """Keeps only the last N backups **of each kind**, deletes the older ones.

    Without this the folder with the backups would grow forever, until it
    filled the disk - and a backup that fills the disk does more harm than good.

    There are two kinds and each has its own queue: the regular ones (the
    night task) and those before deleting. A shared queue was a mistake:
    whoever forgets three viewers a day makes three extra backups - and with
    "keep 7" they would push out the night backups, exactly those the backing
    up is for. Each kind protects against something else, so they have no
    room to take from each other.
    """
    keep = db.get_int_setting("backup_keep", 1, 365, 7)
    try:
        all_files = (list(directory.glob("jellyscope-*.db"))
                     + list(directory.glob("jellyscope-*.sql")))
        queues: dict[bool, list[Path]] = {True: [], False: []}
        conversion_backups = []
        for file in all_files:
            if BACKUP_BEFORE_CONVERSION in file.name:
                conversion_backups.append(file)
            else:
                queues[BACKUP_BEFORE_DELETE in file.name or OLD_BACKUP_BEFORE_DELETE in file.name].append(file)
    except OSError:
        return 0

    removed = 0
    # The backup before the conversion counts its days by the date in its name (jellyscope-YYYYMMDD-…).
    oldest = date.today() - timedelta(days=CONVERSION_BACKUP_DAYS)
    for file in conversion_backups:
        try:
            made = datetime.strptime(file.name.split("-")[1], "%Y%m%d").date()
        except (IndexError, ValueError):
            continue
        if made < oldest:
            try:
                file.unlink()
                removed += 1
            except OSError:
                pass
    for files in queues.values():
        readable = []
        for file in files:
            try:
                readable.append((file.stat().st_mtime, file))
            except OSError as exc:
                # Vanished since the listing, a link to nothing, a stale share: only this file is skipped - the rest of
                # its queue is pruned all the same, or keep would stop holding for good.
                log.warning("backup %s skipped by the pruning, its time cannot be read: %s", file.name, exc)
        readable.sort(key=lambda pair: pair[0], reverse=True)
        for _, stale in readable[keep:]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def list_backups() -> list[dict[str, Any]]:
    """The backups lying in the folder now - so the UI shows that they work."""
    target = db.get_setting("backup_path", "").strip()
    if not target:
        return []

    directory = Path(target)
    if not directory.is_dir():
        return []

    found = []
    try:
        paths = list(directory.glob("jellyscope-*.db")) + list(directory.glob("jellyscope-*.sql"))
    except OSError:
        return []
    for path in paths:
        try:
            found.append((path, path.stat()))
        except OSError:
            continue        # vanished meanwhile, a link to nothing: the others are listed all the same
    result = []
    for path, stat in sorted(found, key=lambda pair: pair[1].st_mtime, reverse=True)[:10]:
        result.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(
                stat.st_mtime, tz=UTC
            ).strftime(db.TIME_FORMAT),
        })
    return result


def backup_file(name: str) -> Path | None:
    """Translates a backup's name to a file in the folder with the backups.

    The name comes from the browser, so it must not be treated as a path -
    `../../.env` is a "name" too. So only the bare name is taken, it must fit
    the shape backups have, and the result is checked once more against what
    really lies in the folder. The same care as with the log viewer.
    """
    target = db.get_setting("backup_path", "").strip()
    if not target or not name:
        return None
    if name != Path(name).name:
        return None
    if not name.startswith("jellyscope-") or not name.endswith((".db", ".sql")):
        return None

    path = Path(target) / name
    return path if path.is_file() else None


# By what a backup Jellyscope made itself is told (see `_dump_own`). Such one
# it can restore too; the output of pg_dump not. The first mark is written, the second
# one is that of the backups written before 1.9.0 (in Czech) - they restore as well.
OWN_BACKUP_MARKS = ("-- Jellyscope backup", "-- Záloha Jellyscope")


def _is_own_export(head: str) -> bool:
    """Is the beginning of a .sql backup our own export (either version's header)?"""
    return any(mark in head for mark in OWN_BACKUP_MARKS)


def restore_backup(name: str) -> dict[str, Any]:
    """Restores the database from a backup. Overwrites everything that is in it now.

    Before anything is overwritten, **a backup of the current state** is made.
    A restore is the only operation in the application that can delete
    everything at once - and a person who mixes up a row in the list would
    otherwise lose it.

    What can be restored:

      * **SQLite** - the backup's content goes back into the database by the
        built-in `backup()` (see _restore_sqlite).
      * **PostgreSQL, our own export** - it is plain SQL we wrote ourselves,
        so we can run it too.
      * **PostgreSQL, the output of pg_dump** - that contains COPY blocks and
        other things that cannot be safely "replayed" row by row. That is what
        `psql` is for; when it is at hand, it is used, otherwise we say with
        which command to do it by hand.

    After a restore the application needs a restart: it has settings in memory
    and open connections to the database that no longer hold.
    """
    source = backup_file(name)
    if source is None:
        return {"status": "error", "message": "That backup could not be found."}

    config = db.database_config()
    if config.is_postgres and source.suffix == ".db":
        return {"status": "error",
                "message": "This backup is from SQLite, but the application runs on PostgreSQL."}
    if not config.is_postgres and source.suffix == ".sql":
        return {"status": "error",
                "message": "This backup is from PostgreSQL, but the application runs on SQLite."}

    # A safeguard: what is there now is stored first.
    try:
        safety = _backup_before_restore(config)
    except Exception as exc:  # noqa: BLE001
        log.exception("the safety backup before restoring could not be made")
        return {"status": "error",
                "message": i18n.translate_with(
                    "The current state could not be backed up first: {error}. So the restore did not start.",
                    None, error=i18n.error_text(exc))}

    try:
        if config.is_postgres:
            _restore_postgres(config, source)
        else:
            _restore_sqlite(config, source)
    except Exception as exc:  # noqa: BLE001
        log.exception("restoring the backup failed")
        return {"status": "error",
                "message": i18n.translate_with(
                    "The restore failed: {error}. The current state stayed backed up in {file}.",
                    None, error=i18n.error_text(exc), file=safety.name)}

    log.info("database restored from backup %s", name)
    # Everybody signs in again - before the restart the page asks for next (web_sessions.reset).
    try:
        web_sessions.reset()
    except Exception:  # noqa: BLE001 - the backup is restored; a failure here must not hide that
        log.exception("the sign-in sessions could not be reset after the restore")
    return {"status": "ok", "file": name, "safety": safety.name}


def _backup_before_restore(config: Any) -> Path:
    """Stores the current state before the restore overwrites it."""
    folder = Path(db.get_setting("backup_path", "").strip())
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if config.is_postgres:
        target = folder / f"jellyscope-{stamp}-{BACKUP_BEFORE_RESTORE}.sql"
        _write_whole(lambda path: _dump_postgres(config, path), target)
    else:
        target = folder / f"jellyscope-{stamp}-{BACKUP_BEFORE_RESTORE}.db"
        _write_whole(lambda path: _backup_sqlite(config, path), target)
    # The whole database, like any backup: for the owner only (see _run_backup).
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def _restore_sqlite(config: Any, source: Path) -> None:
    """Puts the backup's content back into the database.

    First it is checked that it really is a SQLite database and that our
    tables are in it. Overwriting a running database with a foreign file would
    break the application and it could be put back only by hand.

    Not by copying the file: the collector, the tasks and the web keep their
    own connections meanwhile. A connection open across a copy still holds the
    old journal (WAL) and writes it into the new file when it closes - the
    restore is undone or the file damaged. The built-in `backup()` into the
    live database goes through its locks and its journal like any write: it
    waits for a write under way, and the others see the restored content.

    The backup is opened by an encoded URI (a # or ? in the folder's name would
    end the path) as immutable: read-only, without the journal files a WAL
    database would otherwise leave beside it.
    """
    conn = sqlite3.connect(source.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "playback" not in tables or "items" not in tables:
            raise BackupError("There are no Jellyscope tables in the file - it is not its backup.")

        target = Path(config.path)
        if not target.is_absolute():
            target = BASE_DIR / target

        live = sqlite3.connect(target, timeout=60.0)
        try:
            conn.backup(live)
        finally:
            live.close()
    finally:
        conn.close()


def _jellyscope_tables() -> set[str]:
    """Every table name Jellyscope has had: today's schema and the bin, both names of every entry of the
    rename map (schema_names), the old shared *Arr tables and the orphaned api_pouziti."""
    from . import schema_names

    # A TABLE named like the lock view is the bin of 1.8.x (a restored backup of it, before the restart).
    return (set(db.TABLES) | {db.PLAYBACK_BIN, schema_names.LOCK_VIEW} | {t.old for t in schema_names.TABLES}
            | {t.new for t in schema_names.TABLES} | set(db._OLD_ARR_TABLES) | {"api_pouziti"})


def _jellyscope_drops(conn: Any) -> list[str]:
    """The statements that drop every table of Jellyscope there is - of either names - so a restored backup
    is the only content; the restart after the restore converts it (jellyscope.conversion). Tables of
    anything else stay."""
    from . import schema_names

    rows = conn.execute("SELECT table_name AS name FROM information_schema.tables"
                        " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'").fetchall()
    views = {str(dict(r)["name"]) for r in conn.execute(
        "SELECT table_name AS name FROM information_schema.views WHERE table_schema = current_schema()").fetchall()}
    # The lock view first (schema_names.LOCK_VIEW), when it is a view: a backup of 1.8.x brings a TABLE of its
    # name (dropped with the tables below - a second restore before the restart), and the restart after the
    # restore makes the view again.
    drops = [f"DROP VIEW {schema_names.LOCK_VIEW}"] if schema_names.LOCK_VIEW in views else []
    return drops + [
        f"DROP TABLE IF EXISTS {table} CASCADE"
        for table in sorted({str(dict(r)["name"]) for r in rows} & _jellyscope_tables())]


def _drop_jellyscope_tables(conn: Any) -> None:
    """Drops every table of Jellyscope (_jellyscope_drops) in the connection's transaction."""
    for statement in _jellyscope_drops(conn):
        conn.execute(statement)


def _restore_postgres(config: Any, source: Path) -> None:
    """Replays a backup into PostgreSQL."""
    head = source.read_text(encoding="utf-8", errors="replace")[:200]

    if _is_own_export(head):
        # Our own export is plain SQL we wrote ourselves - we know its shape,
        # so we can run it directly. First every table of Jellyscope goes (of
        # either names - an old backup brings the old ones), in the same
        # transaction as the export: a failure leaves the database as it was.
        content = _read_export(source)
        if not content.rstrip().endswith("COMMIT;"):
            # An export cut off half-way (an older version wrote it straight under the final name) holds
            # only part of the tables: replayed, it would drop them all and commit what is there.
            raise BackupError("The backup is incomplete - its writing was cut off before the end")
        with db.connect() as conn:
            _drop_jellyscope_tables(conn)
            # Directly through the driver, not through our wrapper: that one
            # translates every query (question marks to %s, doubles per cents)
            # and the backup's data has both - titles' names with a question
            # mark, texts with per cents. The file is finished SQL without
            # parameters; it should run as it is.
            with conn._raw.cursor() as cursor:
                cursor.execute(content)
            conn.commit()
        db.close_pool()
        return

    # The output of pg_dump. psql below drops the tables and replays the file in one transaction - a file with no
    # statements (cut off, or only named like a backup) would commit the drops and restore nothing. pg_dump ends its
    # plain output with this line (followed by "\unrestrict ..." since PostgreSQL 17.6), so a file without it is
    # refused before anything is dropped.
    if not _pg_dump_is_whole(source):
        raise BackupError("The backup is incomplete - its writing was cut off before the end")
    tool = _find_psql()
    if not tool:
        raise BackupError(
            "This backup is from pg_dump, and restoring it needs psql, which was not found on this machine. "
            "Restore it by hand:\n    psql -h {host} -p {port} -U {user} -d {database} -f {file}",
            host=config.host, port=config.port, user=config.user, database=config.database, file=source)

    # psql drops the tables and replays the dump in ONE transaction (-1): when it stops on an error, the
    # database is as it was - not empty until somebody notices.
    with db.connect() as conn:
        drops = _jellyscope_drops(conn)

    environment = dict(os.environ)
    if config.password:
        environment["PGPASSWORD"] = config.password

    result = subprocess.run(
        [tool, "-h", config.host, "-p", str(config.port), "-U", config.user,
         "-d", config.database, "--no-password", "-v", "ON_ERROR_STOP=1", "--single-transaction",
         *(["-c", "; ".join(drops)] if drops else []), "-f", str(source)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, timeout=3600, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
        if detail:
            raise BackupError("psql reported an error: {detail}", detail=detail)
        raise BackupError("psql ended with code {code}.", code=result.returncode)
    db.close_pool()


PG_DUMP_END = "-- PostgreSQL database dump complete"


def _pg_dump_is_whole(source: Path) -> bool:
    """Whether a pg_dump file reached its end: the closing comment pg_dump writes last, within its final lines."""
    with source.open("rb") as file:
        file.seek(0, os.SEEK_END)
        file.seek(max(0, file.tell() - 4096))
        tail = file.read().decode("utf-8", errors="replace")
    return PG_DUMP_END in tail


def _find_psql() -> str | None:
    """psql, if it is on the machine - looked for the same way as pg_dump."""
    candidates = [shutil.which("psql")]
    for pattern in PG_BIN_PATTERNS:
        candidates.extend(glob.glob(pattern.replace("pg_dump", "psql")))
    found = [c for c in candidates if c and os.access(c, os.X_OK)]
    if not found:
        return None
    # The newest version - the same thinking as with pg_dump.
    return max(found, key=_tool_version)


def delete_backup(name: str) -> bool:
    """Deletes one backup. Returns whether it worked."""
    path = backup_file(name)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError as exc:
        log.warning("backup %s could not be deleted: %s", name, exc)
        return False
    log.info("backup deleted: %s", name)
    return True


def free_space(path_text: str) -> int | None:
    """The free space in a folder - worth saying in advance, before a backup fails."""
    try:
        return shutil.disk_usage(path_text).free
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def is_enabled(task: Task) -> bool:
    return db.get_setting(task.enabled_setting, "0") == "1"


def interval_minutes(task: Task) -> int:
    if task.is_daily:
        return 0
    return db.get_int_setting(task.interval_setting, 0, 10080, task.default_minutes)


def daily_time(task: Task) -> str:
    """The daily task's time as "HH:MM". Nonsense in the settings is replaced by the default."""
    if not task.is_daily:
        return ""
    return valid_time(db.get_setting(task.time_setting, task.default_time),
                      task.default_time)


def valid_time(text: str, fallback: str) -> str:
    """Guards the shape "HH:MM". Nothing else may get into the scheduler."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return f"{hour:02d}:{minute:02d}"
    return fallback


def time_choices(task: Task) -> dict[str, Any]:
    """The time split into the hour and the minute - for two fields in the form.

    Why two fields and not `<input type="time">` or two `<select>`s: both open
    their own pop-up window that the browser draws itself. CSS does not reach
    it and the direction it unfolds in is decided by the room on the screen -
    at the bottom row of a table it pops up. Two numbers do not open at all
    and look like the rest of the form.
    """
    hour, minute = daily_time(task).split(":")
    return {"hour": hour, "minute": minute}


def status(task: Task) -> dict[str, Any]:
    """Everything the Settings page needs to know about a task."""
    last = scanner.last_scan(task.log_kind)
    due_in = _minutes_until_due(task, last)
    row = {
        "task": task,
        "enabled": is_enabled(task),
        "daily": task.is_daily,
        "time": daily_time(task),
        "minutes": interval_minutes(task),
        "last": last,
        "due_in": due_in,
        # For a daily task the time is shown right away instead of "in 12.3 h"
        # - that is the number the person wrote into the settings, so they
        # recognise it.
        "next_is_today": (task.is_daily and due_in is not None
                          and datetime.now() < _todays_target(task, datetime.now())),
    }
    if task.is_daily:
        row.update(time_choices(task))
    # A task that does not run every day: the date when it really does something.
    upcoming = task.next_run() if task.next_run else None
    row["next_date"] = (upcoming.strftime("%d.%m.")
                          if upcoming and upcoming > date.today() else "")
    return row


def all_statuses() -> list[dict[str, Any]]:
    return [status(task) for task in TASKS.values()]


def _local_time(utc_text: str) -> datetime | None:
    """A time from the database (UTC) converted to the local one. None when it cannot be read.

    Times are always stored into the database in UTC - see db.utcnow(). But a
    person enters the daily time of a task in the time they live in, so both
    must meet on one side. We convert to the local one, because "every day at
    3:30" should mean 3:30 on the clock on the wall.
    """
    try:
        naive = datetime.strptime(
            str(utc_text).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=UTC).astimezone().replace(tzinfo=None)


def _todays_target(task: Task, now: datetime) -> datetime:
    """Today's time of a daily task in the local time."""
    hour, minute = (int(part) for part in daily_time(task).split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _minutes_until_due(task: Task, last: dict[str, Any] | None) -> float | None:
    """In how many minutes the task runs. None = never (off)."""
    if not is_enabled(task):
        return None

    if task.is_daily:
        now = datetime.now()
        target = _todays_target(task, now)
        if _daily_task_due(task, now, target):
            return 0.0
        # It ran already (or it is still ahead of us) - until the next time.
        next_time = target if now < target else target + timedelta(days=1)
        return max(0.0, (next_time - now).total_seconds() / 60)

    minutes = interval_minutes(task)
    if minutes <= 0:
        return None
    if last is None:
        return 0.0
    return max(0.0, minutes - _minutes_elapsed(last))


def _minutes_elapsed(last: dict[str, Any]) -> float:
    """How many minutes passed since the last run.

    Counted in UTC, not on the local clock: when the clocks go back, an hour
    repeats, and a run from its first pass would seem to be in the future -
    every interval task would wait about an hour.
    """
    try:
        started = datetime.strptime(str(last["started_at"]).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return 0.0
    return (datetime.now(UTC) - started.replace(tzinfo=UTC)).total_seconds() / 60


def last_automatic_run(task: Task) -> datetime | None:
    """When the scheduler last started this task (local time).

    On purpose it does not look into `scan_log`: there are **manual** starts
    there too and the schedule must not follow them. When a person clicks "run
    now" at noon, the night run at 3:30 should still happen - if only because
    the library changes again between noon and morning.
    """
    text = db.get_setting(_last_run_key(task), "")
    return _local_time(text) if text else None


def _last_run_key(task: Task) -> str:
    return f"task_{task.key}_last_auto"


def _record_automatic_run(task: Task) -> None:
    """Writes that the task is starting from the scheduler just now.

    It is written BEFORE the start, not after it: a task easily runs ten
    minutes and the scheduler wakes up every minute - were the stamp to come
    only at the end, it would start it again meanwhile.
    """
    db.set_setting(_last_run_key(task), db.utcnow())


def _daily_task_due(task: Task, now: datetime, target: datetime) -> bool:
    """Should the daily task run right now?

    The condition is simple: today's time has come and the scheduler has not
    handled it yet. Thanks to that:
      * a manual start **does not touch** the schedule at all - it neither
        shifts it nor cancels today's run,
      * when the application was not running at 3:30, the task catches up at
        the start instead of skipping the whole day.
    """
    if now < target:
        return False
    last = last_automatic_run(task)
    return last is None or last < target


def _is_due(task: Task) -> bool:
    if not is_enabled(task):
        return False

    if task.is_daily:
        now = datetime.now()
        return _daily_task_due(task, now, _todays_target(task, now))

    last = scanner.last_scan(task.log_kind)
    minutes = interval_minutes(task)
    if minutes <= 0:
        return False
    if last is None:
        return True
    return _minutes_elapsed(last) >= minutes


async def run_now(key: str) -> dict[str, Any]:
    """A manual start of a task from Settings."""
    task = TASKS.get(key)
    if task is None:
        return {"status": "error", "message": "Unknown task."}
    return await (task.manual_runner or task.runner)()


def deferred_compaction_time(now: datetime | None = None) -> datetime | None:
    """When the deferred rewrite of the database is done (local time). None = nothing waits.

    "At night" means the time of the Clearing out history task - even when it
    is off, it has a time set; by default 04:45. The first such time **after**
    the request is taken: whoever clicks at 04:50 does not get the rewrite in
    five minutes, but tomorrow.
    """
    text = purge.compaction_pending()
    if not text:
        return None
    now = now or datetime.now()
    requested = _local_time(text)
    if requested is None:
        # An unreadable value - better to finish it right away than to leave it hanging forever.
        return now
    target = _todays_target(TASKS["purge"], requested)
    if requested >= target:
        target += timedelta(days=1)
    return target


async def _finish_deferred_compaction() -> None:
    """At the time empties the bin and rewrites the database's file.

    The order is the whole point of the pair: as long as the rows are in the
    bin, they are in the database and `VACUUM` would only rearrange them. Only
    here the forgetting really holds - and the rewrite right after makes sure
    not a trace of them is left in the file.
    """
    now = datetime.now()
    target = deferred_compaction_time(now)
    if target is None or now < target:
        return
    log.info("deferred database rewrite: starting")
    await asyncio.to_thread(purge.empty_bin)
    await asyncio.to_thread(purge.finish_deferred_compaction)


async def run_scheduler() -> None:
    """The loop in the background. Once a minute it checks what is due already.

    It must never fail - were it to die, the tasks would quietly stop running
    and nobody would notice. Hence the wide `except`.
    """
    await asyncio.sleep(25)  # let the application start

    while True:
        try:
            for task in TASKS.values():
                # A task we know nothing about yet (a fresh installation or a
                # change just deployed) is **not caught up**. We only note "we
                # count from now" and the first run comes at its time.
                #
                # Without this, after deploying at half past three in the
                # afternoon the night synchronisation started at once: today's
                # time had come and no record of a run existed, so it looked
                # like a missed time. Only what we know should have run is
                # caught up - not the first acquaintance.
                if task.is_daily and last_automatic_run(task) is None:
                    _record_automatic_run(task)
                    log.info("task %s: counting the schedule from now, "
                             "first run at %s", task.key, daily_time(task))
                    continue

                if _is_due(task):
                    log.info("scheduled task: %s", i18n.UiName(task.name))
                    if task.is_daily:
                        _record_automatic_run(task)
                    try:
                        result = await task.runner()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - one failing task must not hold up the ones after it
                        log.exception("task %s failed", task.key)
                        continue
                    log.info("task %s finished: %s", task.key, result.get("status"))

            # It is not a task - it has no switching on nor a schedule, only a
            # debt after forgetting a viewer by hand that is paid off at night.
            await _finish_deferred_compaction()


        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("the task scheduler failed")

        await asyncio.sleep(TICK_SECONDS)
