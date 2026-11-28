# -*- coding: utf-8 -*-
"""A task runs at a fixed time every day, or every N minutes or hours - as chosen (28. 11. 2026).

Every task but the log rotation carries both schedules; the chosen one is stored as `task_<key>_mode`, and without it
the task keeps the kind it always had, so an update changes nothing. Switched to daily, a task does not run at once as
if it missed a night (an old stamp of a scheduled run) - it starts at its time. And a running task never starts again,
by the scheduler nor by hand; another task started by hand starts at once.

Run:
    .venv/bin/python tests/test_task_schedule_mode.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_task_schedule_mode.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "task-schedule-mode.db")
os.environ["SECRET_KEY"] = "task-schedule-mode-test-key-long-enough-0123"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _network import refuse_network  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, tasks, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


refuse_network()
db.init_db()


class FakeClock(datetime):
    current = datetime(2026, 8, 17, 15, 0, 0)      # Monday, three in the afternoon

    @classmethod
    def now(cls, tz: Any = None) -> datetime:   # type: ignore[override]
        return cls.current if tz is None else cls.current.astimezone(tz)


tasks.datetime = FakeClock                     # type: ignore[assignment]
REAL_UTCNOW = db.utcnow
db.utcnow = lambda: (FakeClock.current.astimezone(timezone.utc)  # type: ignore[assignment]
                     .replace(tzinfo=None).strftime(db.TIME_FORMAT))


def utc_text(when_local: datetime) -> str:
    return when_local.astimezone(timezone.utc).replace(tzinfo=None).strftime(db.TIME_FORMAT)


def record_run(kind: str, when_local: datetime) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM scan_log WHERE kind = ?", (kind,))
        conn.execute("INSERT INTO scan_log (kind, started_at, finished_at, status) VALUES (?,?,?,'done')",
                     (kind, utc_text(when_local), utc_text(when_local)))
        conn.commit()


print("--- every task carries both schedules, the kind it always had by default ---")
DAILY = {"sync", "tidy", "backup", "purge", "updates", "logrotate"}
INTERVAL = {"recent", "notifications", "arr", "seerr", "music", "books_sync"}
check(set(tasks.TASKS) == DAILY | INTERVAL, f"the twelve tasks ({sorted(tasks.TASKS)})")
check(all(t.time_setting and t.interval_setting for t in tasks.TASKS.values()), "each has a time and an interval setting")
check({k for k, t in tasks.TASKS.items() if tasks.is_daily(t)} == DAILY, "without a stored kind: daily as before")
check(all(tasks.daily_time(t) and tasks.interval_minutes(t) > 0 for t in tasks.TASKS.values()),
      "both schedules are readable whatever the kind")
check(tasks.daily_time(tasks.TASKS["arr"]) == "03:00" and tasks.interval_minutes(tasks.TASKS["sync"]) == 1440,
      "the other schedule's defaults: 03:00 and 24 hours")
from jellyscope import db as _db  # noqa: E402
check(not {t.time_setting for t in tasks.TASKS.values()} & set(_db.DROPPED_SETTINGS)
      and not {t.interval_setting for t in tasks.TASKS.values()} & set(_db.DROPPED_SETTINGS),
      "no schedule setting is among the ones deleted at start (task_backup_minutes is)")
FIXED = {"logrotate", "purge", "notifications"}
check(all(not tasks.TASKS[k].switchable for k in FIXED) and all(t.switchable for k, t in tasks.TASKS.items() if k not in FIXED),
      "every task switches but the log rotation, the clearing out and the notifications (28. 11.)")

print("--- daily -> every N ---")
sync = tasks.TASKS["sync"]
db.set_setting(sync.enabled_setting, "1")
db.set_setting(sync.interval_setting, "1")
tasks.set_mode(sync, daily=False)
db.forget_settings()
record_run(sync.log_kind, FakeClock.current - timedelta(minutes=2))
check(not tasks.is_daily(sync) and tasks._is_due(sync), "the library sync every minute: due two minutes after the last run")
record_run(sync.log_kind, FakeClock.current - timedelta(seconds=20))
check(not tasks._is_due(sync), "...not twenty seconds after it")
row = tasks.status(sync)
check(row["daily"] is False and row["minutes"] == 1 and row["time"] == "03:30", "the page row knows both", f"({row})")

print("--- every N -> daily: not at once, at its time ---")
arr = tasks.TASKS["arr"]
db.set_setting(arr.enabled_setting, "1")
db.set_setting(arr.time_setting, "03:00")
# A stamp of a scheduled run from a week ago (the task was daily once before): it must not look like a missed night.
db.set_setting(f"task_{arr.key}_last_auto", utc_text(FakeClock.current - timedelta(days=7)))
tasks.set_mode(arr, daily=True)
db.forget_settings()
check(tasks.is_daily(arr) and not tasks._is_due(arr), "switched at 15:00 with an old stamp: not due now")
due_in = tasks._minutes_until_due(arr, None)
check(due_in is not None and abs(due_in - 12 * 60) < 1, f"...due at 03:00 tomorrow ({due_in} min)")
FakeClock.current = FakeClock.current + timedelta(hours=12, minutes=1)
check(tasks._is_due(arr), "at 03:01 it is due")
FakeClock.current = FakeClock.current - timedelta(hours=12, minutes=1)
tasks.set_mode(arr, daily=True)
db.forget_settings()
check(tasks.is_daily(arr), "setting the same kind again changes nothing")

print("--- the log rotation stays daily ---")
rotation = tasks.TASKS["logrotate"]
tasks.set_mode(rotation, daily=False)
db.forget_settings()
check(tasks.is_daily(rotation) and db.get_setting(rotation.mode_setting, "") == "", "set_mode does not switch it")


print("--- a running task never starts again ---")
STARTS: list[str] = []
GATES: dict[str, asyncio.Event] = {}


def held(key: str, fail: bool = False):
    async def runner() -> dict[str, Any]:
        STARTS.append(key)
        await GATES[key].wait()
        if fail:
            raise RuntimeError("the task failed")
        return {"status": "ok"}
    return runner


real_tasks = dict(tasks.TASKS)
for key in ("arr", "seerr", "backup"):
    tasks.TASKS[key] = dataclasses.replace(real_tasks[key], runner=held(key), manual_runner=None)


async def two_at_once() -> tuple[dict, dict, bool, bool, bool]:
    STARTS.clear()
    GATES.update(arr=asyncio.Event(), seerr=asyncio.Event())
    first = asyncio.create_task(tasks.run_now("arr"))
    await asyncio.sleep(0.01)
    try:                                                 # the same task while it runs - a double click
        second = await asyncio.wait_for(tasks.run_now("arr"), 1)
    except asyncio.TimeoutError:
        second = {"status": "started a second time and waits"}
    other = asyncio.create_task(tasks.run_now("seerr"))  # another task - starts at once beside it
    await asyncio.sleep(0.01)
    both_running = tasks.is_running("arr") and tasks.is_running("seerr")
    GATES["arr"].set(); GATES["seerr"].set()
    done = await first
    await other
    return done, second, both_running, tasks.is_running("arr"), tasks.is_running("seerr")


done, second, both_running, still_arr, still_seerr = asyncio.run(two_at_once())
check(STARTS.count("arr") == 1 and second.get("status") == "running", "the same task twice: it runs once, the second is refused",
      f"({STARTS} {second})")
check("seerr" in STARTS and both_running, "another task by hand starts at once, beside the running one", f"({STARTS})")
check(done.get("status") == "ok" and not still_arr and not still_seerr, "after the end neither counts as running")


async def failing() -> bool:
    tasks.TASKS["arr"] = dataclasses.replace(real_tasks["arr"], runner=held("arr", fail=True), manual_runner=None)
    GATES["arr"] = asyncio.Event()
    GATES["arr"].set()
    try:
        await tasks.run_now("arr")
    except RuntimeError:
        pass
    return tasks.is_running("arr")


check(not asyncio.run(failing()), "a task that failed does not stay marked as running")
tasks.TASKS["arr"] = dataclasses.replace(real_tasks["arr"], runner=held("arr"), manual_runner=None)

print("--- the scheduler skips a running task ---")
arr, backup = tasks.TASKS["arr"], tasks.TASKS["backup"]
for task in tasks.TASKS.values():
    db.set_setting(task.enabled_setting, "1" if task.key in ("arr", "backup") else "0")
tasks.set_mode(arr, daily=False)
db.set_setting(arr.interval_setting, "1")
db.set_setting(backup.time_setting, "03:00")
db.set_setting(f"task_{backup.key}_last_auto", utc_text(FakeClock.current - timedelta(days=1)))
db.forget_settings()
FakeClock.current = FakeClock.current.replace(hour=3, minute=1)
record_run(arr.log_kind, FakeClock.current - timedelta(minutes=5))


async def tick_while_running() -> tuple[list[str], Any]:
    STARTS.clear()
    GATES.update(arr=asyncio.Event(), backup=asyncio.Event())
    by_hand = [asyncio.create_task(tasks.run_now(key)) for key in ("arr", "backup")]
    await asyncio.sleep(0.01)
    tick = asyncio.create_task(tasks._tick())
    await asyncio.sleep(0.05)
    started = list(STARTS)
    GATES["arr"].set(); GATES["backup"].set()
    await asyncio.gather(*by_hand)
    await tick
    return started, tasks.last_automatic_run(backup)


started, last_backup = asyncio.run(tick_while_running())
check(started.count("arr") == 1 and started.count("backup") == 1,
      "the arr every minute and the backup at its time, both running by hand: the scheduler starts neither again",
      f"({started})")
check(last_backup is not None and last_backup >= FakeClock.current.replace(hour=3, minute=0),
      "...the daily one counts as handled, so it does not start again right after", f"({last_backup})")


async def tick_after() -> list[str]:
    STARTS.clear()
    GATES.update(arr=asyncio.Event(), backup=asyncio.Event())
    GATES["arr"].set(); GATES["backup"].set()
    await tasks._tick()
    return list(STARTS)


check("backup" not in asyncio.run(tick_after()), "the next tick: the backup does not run again today")

print("--- Run now on the page ---")
# The real clock again: a session made on the fake clock's August would count as long expired.
db.utcnow = REAL_UTCNOW
accounts.create("boss", "longpassword", is_admin=True)
admin = TestClient(web.app)
admin.post("/login", data={"username": "boss", "password": "longpassword"})
STARTS.clear()
tasks._running.add("arr")          # the scheduler's run of the *Arr sync, right now
try:
    page = admin.post("/settings/tasks/run", data={"key": "arr"}).text
finally:
    tasks._running.discard("arr")
check("is already running" in page and "arr" not in STARTS, "Run now on a running task: it says so and starts nothing",
      f"({STARTS})")
for key, task in real_tasks.items():
    tasks.TASKS[key] = task


print("--- the Tasks page: daily at a time, or every N minutes or hours ---")
for key in ("sync", "arr"):
    db.set_setting(tasks.TASKS[key].mode_setting, "")
db.set_setting(tasks.TASKS["sync"].interval_setting, "1440")
db.forget_settings()


def field_value(html: str, name: str) -> str:
    match = re.search(r'<input[^>]*name="' + name + r'"[^>]*value="([^"]*)"', html) or \
        re.search(r'<input[^>]*value="([^"]*)"[^>]*name="' + name + r'"', html)
    return match.group(1) if match else ""


def checked_radio(html: str, name: str) -> str:
    for tag in re.findall(r'<input[^>]*name="' + name + r'"[^>]*>', html):
        if " checked" in tag:
            return re.search(r'value="([^"]*)"', tag).group(1)
    return ""


def selected_unit(html: str, key: str) -> str:
    select = re.search(r'<select[^>]*name="unit_' + key + r'"[^>]*>(.*?)</select>', html, re.S)
    if not select:
        return ""
    chosen = re.search(r'<option[^>]*value="(\w+)"[^>]*selected', select.group(1))
    return chosen.group(1) if chosen else ""


page = admin.get("/settings?section=tasks").text
check(checked_radio(page, "mode_sync") == "daily" and checked_radio(page, "mode_arr") == "interval",
      "each task shows its kind as a choice", f"({checked_radio(page, 'mode_sync')}, {checked_radio(page, 'mode_arr')})")
check(field_value(page, "interval_sync") == "24" and selected_unit(page, "sync") == "hours",
      "a daily task offers its interval too: 24 hours", f"({field_value(page, 'interval_sync')} {selected_unit(page, 'sync')})")
check('name="mode_logrotate"' not in page and 'name="time_logrotate_h"' in page
      and 'name="mode_purge"' not in page and 'name="time_purge_h"' in page, "the log rotation and the clearing out: only their time")


def save(**fields: str) -> str:
    form = {f"enabled_{k}": "1" for k in tasks.TASKS}
    form.update(fields)
    admin.post("/settings/tasks", data=form)
    db.forget_settings()
    return admin.get("/settings?section=tasks").text


page = save(mode_sync="interval", interval_sync="6", unit_sync="hours")
sync = tasks.TASKS["sync"]
check(not tasks.is_daily(sync) and tasks.interval_minutes(sync) == 360, f"6 hours saved as 360 minutes ({tasks.interval_minutes(sync)})")
check(checked_radio(page, "mode_sync") == "interval" and field_value(page, "interval_sync") == "6"
      and selected_unit(page, "sync") == "hours", "...and shown back as 6 hours")
page = save(mode_sync="interval", interval_sync="90", unit_sync="minutes")
check(tasks.interval_minutes(sync) == 90 and field_value(page, "interval_sync") == "90" and selected_unit(page, "sync") == "minutes",
      "90 minutes stay minutes (not a whole hour)")
save(mode_sync="interval", interval_sync="1", unit_sync="minutes")
check(tasks.interval_minutes(sync) == 1, "the library sync every minute: no limit")
save(mode_sync="interval", interval_sync="", unit_sync="minutes")
check(tasks.interval_minutes(sync) == 1, "a cleared interval keeps the saved one")
save(mode_sync="interval", interval_sync="200", unit_sync="hours")
check(tasks.interval_minutes(sync) == 10080, f"more than a week is a week ({tasks.interval_minutes(sync)})")
FakeClock.current = FakeClock.current.replace(hour=15, minute=0)
save(mode_arr="daily", time_arr_h="3", time_arr_m="0")
arr = tasks.TASKS["arr"]
check(tasks.is_daily(arr) and tasks.daily_time(arr) == "03:00" and not tasks._is_due(arr),
      "the *Arr sync switched to 03:00 at 15:00: daily, not due now")
save(mode_logrotate="interval")
check(tasks.is_daily(tasks.TASKS["logrotate"]), "a forged choice for the log rotation is ignored")


print("--- the unit agrees with the number (Czech) ---")
db.set_setting("ui_language", "cs")
for minutes, words in (("60", ("Každou", "hodinu")), ("120", ("Každé", "hodiny")), ("360", ("Každých", "hodin")),
                       ("1", ("Každou", "minutu")), ("15", ("Každých", "minut"))):
    db.set_setting(tasks.TASKS["sync"].interval_setting, minutes)
    db.forget_settings()
    page = admin.get("/settings?section=tasks").text
    row = page[page.index('name="mode_sync"'):page.index('name="enabled_recent"')]
    unit = selected_unit(page, "sync")
    shown = re.search(r'<option value="' + unit + r'"[^>]*>([^<]+)</option>', row).group(1).strip()
    every = re.findall(r'name="mode_sync" value="interval"[^>]*>\s*<span>([^<]+)</span>', row)
    check(every == [words[0]] and shown == words[1], f"{minutes} minutes: {words[0]} … {words[1]}", f"({every} {shown})")
db.set_setting("ui_language", "")
db.forget_settings()

print("--- Notifications keep their interval (the weekly summary has its own day and time) ---")
notif = tasks.TASKS["notifications"]
tasks.set_mode(notif, daily=True)
db.forget_settings()
check(not notif.switchable and not tasks.is_daily(notif), "Notifications cannot be switched to a time of day")
page = admin.get("/settings?section=tasks").text
check('name="mode_notifications"' not in page and 'name="interval_notifications"' in page
      and 'name="unit_notifications"' in page and 'name="time_notifications_h"' not in page,
      "the page: only its interval, no choice and no time")

print("--- Tidy data in History goes through the same guard ---")
STARTS.clear()
tasks._running.add("tidy")
try:
    page = admin.post("/settings/history/tidy").text
finally:
    tasks._running.discard("tidy")
check("is already running" in page, "the History's Tidy data while the task runs: it says so")


print("--- the small ones (code review 28. 11.) ---")
check(dataclasses.replace(tasks.TASKS["arr"]).daily_by_default is False
      and dataclasses.replace(tasks.TASKS["sync"]).daily_by_default is True,
      "dataclasses.replace keeps a task's declared kind (it used to turn an interval task daily)")

from jellyscope.books import sync as books_sync  # noqa: E402
from jellyscope.music import sync as music_sync  # noqa: E402

real_now = datetime.now(timezone.utc)
for module in (music_sync, books_sync):
    db.set_setting(module.FULL_PASS_KEY, (real_now - timedelta(hours=23, minutes=59)).replace(tzinfo=None).strftime(db.TIME_FORMAT))
    db.forget_settings()
    due_daily = module._full_pass_due()
    db.set_setting(module.FULL_PASS_KEY, (real_now - timedelta(hours=12)).replace(tzinfo=None).strftime(db.TIME_FORMAT))
    db.forget_settings()
    check(due_daily and not module._full_pass_due(),
          f"{module.__name__}: a daily run a minute earlier than yesterday's still does the full pass (not every other day)")

backup = tasks.TASKS["backup"]
db.set_setting("backup_path", "")
db.set_setting(backup.enabled_setting, "1")
tasks.set_mode(backup, daily=False)
db.set_setting(backup.interval_setting, "60")
db.forget_settings()
with db.connect() as conn:
    conn.execute("DELETE FROM scan_log WHERE kind = 'backup'")
    conn.commit()
outcome = asyncio.run(tasks.backup_database())
record = scanner_last = tasks.scanner.last_scan("backup") or {}
check(outcome.get("status") == "error" and record.get("status") == "error" and bool(record.get("finished_at")),
      "a backup without a folder leaves a failed record (the page shows it)", f"({outcome.get('status')} {record})")
check(not tasks._is_due(backup), "...so on an interval it waits for its interval, not every minute")

real_folder = tasks.backup_folder
tasks.backup_folder = lambda reason="": _tmp + "/backups"
try:
    db.set_setting("backup_path", _tmp + "/backups")
    db.forget_settings()
    record_run(backup.log_kind, FakeClock.current - timedelta(hours=2))
    before_regular = tasks.scanner.last_scan("backup")
    asyncio.run(tasks.backup_before_delete())
    after_regular = tasks.scanner.last_scan("backup")
finally:
    tasks.backup_folder = real_folder
check(before_regular and after_regular and before_regular["id"] == after_regular["id"] and tasks._is_due(backup),
      "a backup before deleting does not reset the regular backup's interval", f"({before_regular} {after_regular})")

db.set_setting(tasks.TASKS["arr"].enabled_setting, "1")
tasks.set_mode(tasks.TASKS["arr"], daily=False)
db.set_setting(tasks.TASKS["arr"].interval_setting, "0")
db.forget_settings()
page = admin.get("/settings?section=tasks").text
row = page[page.index('name="enabled_arr"'):page.index('name="enabled_seerr"')]
check("Only by hand" in row and "at next check" not in row, "an interval of 0: the next run says only by hand")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
