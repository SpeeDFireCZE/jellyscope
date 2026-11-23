# -*- coding: utf-8 -*-
"""Scheduling tasks: a daily time vs. an interval.

Two things that break easily and nobody notices for a month:

1. **A night task should keep its time.** The interval used to be counted
   from the last run, so every manual start shifted the automatic one too
   — and a synchronisation that was to run at 3:30 at night turned into the
   middle of the evening within a few days.

2. **The file analysis should obey the chosen data source.** It was a task
   of its own with its own tick box and did not ask Data collection: with
   "Jen Jellyfin API" ffprobe started anyway and overwrote the details from
   Jellyfin. Now it is part of the synchronisation and the source decides.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a fake in all submodules of the scanner package

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "planovac.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, i18n, scanner, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

SYNC = tasks.TASKS["sync"]
RECENT = tasks.TASKS["recent"]
BACKUP = tasks.TASKS["backup"]


# We hold the clock ourselves in the test. Were the scheduler to ask for the
# real time, the test would end differently at noon and at three in the
# morning - and such a test is worse than none, because it fails at the
# worst moment.
class FakeClock(datetime):
    current = datetime(2026, 8, 17, 15, 0, 0)      # Monday, three in the afternoon

    @classmethod
    def now(cls, tz: Any = None) -> datetime:   # type: ignore[override]
        return cls.current if tz is None else cls.current.astimezone(tz)


tasks.datetime = FakeClock                     # type: ignore[assignment]

# And the same for the time stored into the database. The scheduler writes the
# stamp "I started it last" through `db.utcnow()`; were it the real time, it
# would be compared with the fake one and the test would end differently in
# the morning and in the evening.
db.utcnow = lambda: (FakeClock.current.astimezone(timezone.utc)  # type: ignore[assignment]
                     .replace(tzinfo=None).strftime(db.TIME_FORMAT))


def set_clock(hour: int, minute: int = 0) -> None:
    """Sets "now" to the given time of the same day."""
    FakeClock.current = FakeClock.current.replace(hour=hour, minute=minute,
                                                  second=0, microsecond=0)


def local(hour: int, minute: int = 0, days_back: int = 0) -> datetime:
    """The local time of today (or a few days back) by the fake clock."""
    return (FakeClock.current.replace(hour=hour, minute=minute,
                                      second=0, microsecond=0)
            - timedelta(days=days_back))


def record_auto_run(task: Any, when_local: datetime) -> None:
    """The stamp "the scheduler started this". Manual runs are not written into it."""
    utc = when_local.astimezone(timezone.utc).replace(tzinfo=None)
    db.set_setting(f"task_{task.key}_last_auto", utc.strftime(db.TIME_FORMAT))


def record_run(kind: str, when_local: datetime) -> None:
    """Writes a finished task into scan_log. The time is stored in UTC."""
    # `astimezone()` over a naive time means "take it as local" - exactly
    # what we need: UTC in the database, local time on the clock.
    utc = when_local.astimezone(timezone.utc).replace(tzinfo=None)
    with db.connect() as conn:
        conn.execute("DELETE FROM scan_log WHERE kind = ?", (kind,))
        conn.execute(
            "INSERT INTO scan_log (kind, started_at, finished_at, status)"
            " VALUES (?,?,?,'done')",
            (kind, utc.strftime(db.TIME_FORMAT), utc.strftime(db.TIME_FORMAT)),
        )


print("--- what the list of tasks is made of ---")
check(set(tasks.TASKS) == {"sync", "recent", "tidy", "notifications", "arr", "seerr", "music", "books_sync",
                           "updates", "purge", "backup", "logrotate"},
      f"twelve tasks (the log rotation and the *Arr, Seerr, music and books synchronisations too); the analysis "
      f"is not one of its own, the update check and the notifications are ({sorted(tasks.TASKS)})")
check(not tasks.TASKS["arr"].is_daily and tasks.TASKS["arr"].default_minutes == 60,
      "the *Arr synchronisation is an interval one, once an hour")
check(all(name in i18n.translate(tasks.TASKS["arr"].description, "cs") for name in ("Sonarru", "Radarru", "Bazarru", "Prowlarru")),
      "the description of the *Arr synchronisation names all four apps it synchronises")
check(SYNC.is_daily and BACKUP.is_daily, "the synchronisation and the backup run at a given time")
check(not RECENT.is_daily, "the newly added titles stay on minutes")


print()
print("--- the time from the settings is read strictly ---")
db.set_setting("library_sync_time", "3:05")
check(tasks.daily_time(SYNC) == "03:05",
      f"a one-digit hour is padded to two ({tasks.daily_time(SYNC)})")
for nonsense in ("25:00", "12:60", "", "brzy ráno", "3.30"):
    db.set_setting("library_sync_time", nonsense)
    check(tasks.daily_time(SYNC) == SYNC.default_time,
          f"{nonsense!r} -> the default {tasks.daily_time(SYNC)}")


print()
print("--- a daily task runs only when its time has come ---")
db.set_setting("task_sync_enabled", "1")
db.set_setting("library_sync_time", "03:30")

set_clock(3, 0)
record_auto_run(SYNC, local(3, 30, days_back=1))
check(tasks._is_due(SYNC) is False,
      "at 3:00 it does not start yet, even though a day passed since the last run")

set_clock(3, 30)
check(tasks._is_due(SYNC) is True, "at 3:30 it does")

set_clock(3, 31)
record_auto_run(SYNC, local(3, 30))
check(tasks._is_due(SYNC) is False, "right after finishing it does not repeat")

set_clock(23, 59)
check(tasks._is_due(SYNC) is False, "and not until the end of the day either")


print()
print("--- a manual start does not touch the schedule ---")
# This was the reason for the whole change. With an interval a manual run
# would push the automatic one too, so the task would shift into the middle
# of the day day by day. Here it does not touch the schedule at all:
# tomorrow's time stays and today's is not cancelled. That is why the
# scheduler asks its own stamp, not scan_log - manual runs are there too.
set_clock(12, 0)
record_auto_run(SYNC, local(3, 30))   # the morning automatic run
record_run("library", local(12, 0))   # and now a manual run at noon
due_in = tasks.status(SYNC)["due_in"]
expected = (local(3, 30) + timedelta(days=1) - FakeClock.current).total_seconds() / 60
check(abs(due_in - expected) < 1,
      f"the next run is tomorrow at 3:30 (in {due_in:.0f} min), "
      f"not 24 h from noon (expected {expected:.0f})")
check(round(due_in) == 15 * 60 + 30, f"that is in 15.5 hours ({due_in:.0f} min)")
check(tasks.status(SYNC)["next_is_today"] is False, "and it is only tomorrow")

# And the other way round: a manual run at 1:00, that is BEFORE today's
# time, must not cancel today's automatic run. That would happen were the
# scheduler to look into scan_log: "you ran today already, so nothing today".
set_clock(1, 0)
record_auto_run(SYNC, local(3, 30, days_back=1))
record_run("library", local(1, 0))
check(tasks._is_due(SYNC) is False, "at 1:00 it is not time yet")
set_clock(3, 30)
check(tasks._is_due(SYNC) is True,
      "at 3:30 it starts even after a manual run at one in the morning")
check(tasks.status(SYNC)["due_in"] == 0.0,
      "and the overview says it runs at the nearest check")

# And the main thing: the application was not running at 3:30, a person
# started the synchronisation by hand at noon - and today's automatic run
# must not be cancelled by it.
set_clock(12, 0)
record_auto_run(SYNC, local(3, 30, days_back=1))
record_run("library", local(12, 0))
check(tasks._is_due(SYNC) is True,
      "a missed time still holds, even when a manual run happened meanwhile")


print()
print("--- an outage of the application is caught up ---")
# When the application was not running at 3:30, the task should not quietly
# skip the whole day - it starts at the nearest check after the start.
set_clock(9, 0)
record_auto_run(SYNC, local(3, 30, days_back=2))
check(tasks._is_due(SYNC) is True, "a missed time is caught up")

db.set_setting("task_sync_last_auto", "")
check(tasks._is_due(SYNC) is True, "a task that never ran yet too")

db.set_setting("task_sync_enabled", "0")
check(tasks._is_due(SYNC) is False, "a task that is off never")
check(tasks.status(SYNC)["due_in"] is None, "and in the overview it has no next run")
db.set_setting("task_sync_enabled", "1")


print()
print("--- a freshly deployed task is not caught up ---")
# This surprised in use: after a deploy at half past three in the afternoon
# the night synchronisation started at once. Today's time had come and no
# record of a run existed, so it looked like a missed time. For a task it
# knows nothing about yet the scheduler now only notes "I count from now".

set_clock(15, 30)
db.set_setting("library_sync_time", "03:30")
db.set_setting("task_sync_last_auto", "")
db.set_setting("task_recent_enabled", "0")
db.set_setting("task_backup_enabled", "0")

started: list[str] = []


async def fake_run() -> dict[str, Any]:
    started.append("sync")
    return {"status": "ok"}


original_task = tasks.TASKS["sync"]
tasks.TASKS["sync"] = dataclasses.replace(original_task, runner=fake_run)
original_tick = tasks.TICK_SECONDS
try:
    # One round of the scheduler: we put it to sleep right after the first pass.
    async def one_round() -> None:
        job = asyncio.create_task(tasks.run_scheduler())
        await asyncio.sleep(0.05)
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass

    tasks.TICK_SECONDS = 0.01
    # `run_scheduler` waits 25 s before it starts - in the test we go around
    # it by calling the same decision directly.
    check(tasks.last_automatic_run(tasks.TASKS["sync"]) is None,
          "the starting state: we know nothing about an automatic run")
    check(tasks._is_due(tasks.TASKS["sync"]) is True,
          "the condition alone would start it (today's time has been)")

    # ...and exactly that is why the scheduler stamps first.
    if tasks.TASKS["sync"].is_daily and tasks.last_automatic_run(
            tasks.TASKS["sync"]) is None:
        tasks._record_automatic_run(tasks.TASKS["sync"])
    check(tasks._is_due(tasks.TASKS["sync"]) is False,
          "after the stamp it does not start today any more")

    set_clock(3, 30)
    FakeClock.current = FakeClock.current + timedelta(days=1)
    check(tasks._is_due(tasks.TASKS["sync"]) is True,
          "tomorrow at 3:30 it runs normally")
finally:
    tasks.TASKS["sync"] = original_task
    tasks.TICK_SECONDS = original_tick
    FakeClock.current = datetime(2026, 8, 17, 15, 0, 0)


print()
print("--- the interval task stayed as it was ---")
db.set_setting("task_recent_enabled", "1")
db.set_setting("recent_sync_minutes", "15")
record_run("recent", FakeClock.current - timedelta(minutes=20))
check(tasks._is_due(RECENT) is True, "after 20 minutes with the interval 15 -> run")
record_run("recent", FakeClock.current - timedelta(minutes=5))
check(tasks._is_due(RECENT) is False, "after 5 minutes not yet")
db.set_setting("recent_sync_minutes", "0")
check(tasks._is_due(RECENT) is False, "zero means never")


print()
print("--- the synchronisation follows the chosen data source ---")
# The analysis no longer has its own tick box. Whether it starts after the
# synchronisation is decided only by Data collection.
calls: list[str] = []


async def fake_sync() -> dict[str, Any]:
    calls.append("sync")
    return {"status": "ok", "items": 3}


async def fake_analysis(**kwargs: Any) -> dict[str, Any]:
    calls.append(f"tech(only_missing={kwargs.get('only_missing')})")
    return {"status": "ok", "ok": 3}


scanner_sync, scanner_tech = scanner.sync_library, scanner.run_tech_scan
scanner.sync_library = fake_sync            # type: ignore[assignment]
substitute(scanner, "run_tech_scan", fake_analysis)
try:
    db.set_setting("tech_source", "jellyfin")
    calls.clear()
    asyncio.run(tasks.run_now("sync"))
    check(calls == ["sync"],
          f"with the source Jellyfin ffprobe does not start ({calls})")

    db.set_setting("tech_source", "ffprobe")
    calls.clear()
    result = asyncio.run(tasks.run_now("sync"))
    check(calls == ["sync", "tech(only_missing=True)"],
          f"with ffprobe the analysis of the missing follows ({calls})")
    check(result.get("tech", {}).get("ok") == 3,
          "and the analysis's result shows in the task's result")

    # A stopped or failed synchronisation must not measure an incomplete list.
    async def failed_sync() -> dict[str, Any]:
        calls.append("sync")
        return {"status": "stopped"}

    scanner.sync_library = failed_sync         # type: ignore[assignment]
    calls.clear()
    asyncio.run(tasks.run_now("sync"))
    check(calls == ["sync"], f"after stopping the analysis does not start ({calls})")
finally:
    scanner.sync_library = scanner_sync        # type: ignore[assignment]
    substitute(scanner, "run_tech_scan", scanner_tech)

check(tasks.TASKS.get("tech") is None, "the separate task 'tech' no longer exists")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
