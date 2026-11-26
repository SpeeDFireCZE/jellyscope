# -*- coding: utf-8 -*-
r"""Prowlarr: the indexers' status measured continuously by a background loop (jellyscope/arr/prowlarr/watch.py).

The samples of the *Arr sync task come once an hour and a page's at most every ten minutes - an outage shorter than
that is missed and its end is known only within the hour. So the app runs a loop for as long as it runs (beside the
playback collector): every N seconds it asks Prowlarr who it leaves out and keeps the answer. Watched:

A) a round asks only for what a sample needs (the indexers and who is left out), with the live time limit; the first
   round stores a sample, a round that sees the same moves the sample's last round (`measured_until`), a change is
   stored at the round that sees it; while nothing changes a new sample comes every ten minutes - the table grows by
   about one row per ten minutes; the interval is the one from Settings;
B) the statistics: the time the loop ran is measured, an outage from its "failing since" to the round that saw it end;
   Prowlarr not answering stores nothing and the next answer starts a new sample - the time between is not measured;
   the loop stopped (the app down): measured only up to its last round;
C) the switch off, Prowlarr not connected: the loop asks nothing, stores nothing and logs nothing; the *Arr sync task
   and the pages sample exactly as before;
D) while the loop measures, neither the *Arr sync task nor a page samples; once it stops, they do again - and while
   it runs but does not measure (switched off, Prowlarr not connected) they do too;
E) the marker of the cards that refresh by themselves (the "arr" one) moves on a stored change only;
F) the loop never dies: an unexpected error is logged, it waits a little longer and goes on; cancelled, it ends;
G) the app's start runs it beside the collector and stops it at the shutdown - not in the demo mode;
H) its writes wait for another writer's lock only briefly: a round under it is skipped quietly (not measured); a
   broken table (an older backup restored, before the restart) is one line in the log, not one every round;
I) a round after the process was frozen (a host suspend, docker pause) does not stretch the sample across the freeze;
J) the pauses: never shorter than the interval (the backoff after an error too), a changed interval takes effect
   within a piece of the pause, and no request while it waits.

Prowlarr is not run: the answers are recorded (tests/prowlarr_responses/), the client talks to httpx.MockTransport
(tests/_apps.py). The rounds are called one by one with their own time (`watch_once(..., now=)`).

Spec: docs/superpowers/specs/2026-11-26-live-cards-design.md (B1a)

Run:
    .venv/bin/python tests/test_prowlarr_watch.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-watch.db")
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-it"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402

from _apps import PROWLARR_RESPONSES, connect_prowlarr, prowlarr_client, prowlarr_handler, recorded_response  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, collector, db, stats, tasks  # noqa: E402
from jellyscope.arr import prowlarr, registry  # noqa: E402
from jellyscope.stats import prowlarr as stats_prowlarr  # noqa: E402

failures = 0
db.init_db()
KEY = "secret-key"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


INDEXERS = recorded_response(PROWLARR_RESPONSES, "indexer")
ENABLED_IDS = "".join(f",{n}" for n in sorted(i["id"] for i in INDEXERS if i["enable"])) + ","
BASE = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)


def moment(minutes: float) -> str:
    """The time of a round `minutes` after the test's start (UTC)."""
    return (BASE + timedelta(minutes=minutes)).strftime(db.TIME_FORMAT)


# The recorded block expired long ago - for the test it lasts three more hours, failing since a known moment.
RECORDED_BLOCK = recorded_response(PROWLARR_RESPONSES, "indexerstatus")[0]
OUT = RECORDED_BLOCK["indexerId"]


def blocked(since_minutes: float) -> dict[str, Any]:
    return {**RECORDED_BLOCK, "disabledTill": (BASE + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "initialFailure": (BASE + timedelta(minutes=since_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")}


asked: list[str] = []
timeouts: list[float] = []
overrides: dict[str, Any] = {"indexerstatus": []}


def handler(request: httpx.Request) -> httpx.Response:
    timeouts.append(float((request.extensions.get("timeout") or {}).get("read") or 0))
    return prowlarr_handler(asked, overrides)(request)


substitute(prowlarr, "ProwlarrClient", prowlarr_client(handler))
connect_prowlarr(KEY)

captured: list[str] = []


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        captured.append(record.getMessage())


logging.getLogger("jellyscope").addHandler(Capture())
watch = prowlarr.watch
chain = watch.Chain()


def round_at(minutes: float) -> Any:
    """One round of the loop at its own time."""
    return asyncio.run(watch.watch_once(chain, now=moment(minutes)))


def own_rows() -> list[tuple[Any, ...]]:
    """The samples' own rows as (time, last round, interval, the indexers enabled then), oldest first."""
    return [(r["sampled_at"], r["measured_until"], r["interval_seconds"], r["enabled_ids"])
            for r in db.query_all("SELECT * FROM prowlarr_sample WHERE indexer_id = 0 ORDER BY sampled_at")]


def loop_rows() -> list[tuple[Any, ...]]:
    return [r for r in own_rows() if r[1] is not None]


def all_rows() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM prowlarr_sample", default=0) or 0)


def left_out_at(when: str) -> list[int]:
    return [r["indexer_id"] for r in db.query_all("SELECT indexer_id FROM prowlarr_sample WHERE sampled_at = ? AND indexer_id <> 0"
                                                  " ORDER BY indexer_id", (when,))]


def clear() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM prowlarr_sample")
    chain.sampled_at = ""


# The days around the test's rounds (the app's zone UTC, as the stored times).
db.set_setting("app_timezone", "UTC")
db.forget_settings()
AROUND = stats.period_between((BASE - timedelta(days=1)).date().isoformat(), (BASE + timedelta(days=1)).date().isoformat())


def availability(now: float) -> dict[str, dict[str, Any]]:
    """The availability by indexer name over the days around the test, computed at the moment `now`."""
    return {r["name"]: r for r in stats.prowlarr_availability(AROUND, now=moment(now))["indexers"]}


asyncio.run(prowlarr.synchronize())       # the indexers' names
NAMES = {i["id"]: i["name"] for i in INDEXERS}

print("--- A) a round, a change, ten minutes ---")
asked.clear()
timeouts.clear()
pause = round_at(0)
check(sorted(a.split("?")[0] for a in asked) == ["indexer", "indexerstatus"] and timeouts and max(timeouts) <= 3,
      f"a round asks only for the indexers and who is left out, with the live limit of 3 s ({asked}, {sorted(set(timeouts))})")
check(pause == 60 and own_rows() == [(moment(0), moment(0), 60, ENABLED_IDS)],
      f"the first round stores a sample of its own: its last round is itself, the interval from Settings, 60 s ({own_rows()})")
round_at(1)
round_at(2)
check(own_rows() == [(moment(0), moment(2), 60, ENABLED_IDS)] and all_rows() == 1,
      f"rounds that see the same store no row: they move the sample's last round ({own_rows()})")
overrides["indexerstatus"] = [blocked(2.5)]
round_at(3)
check([r[0] for r in own_rows()] == [moment(0), moment(3)] and own_rows()[0][1] == moment(2) and left_out_at(moment(3)) == [OUT],
      f"an indexer left out: a new sample at the round that saw it, the one before ends at its last round ({own_rows()})")
round_at(4)
overrides["indexerstatus"] = []
round_at(5)
check([r[:2] for r in own_rows()] == [(moment(0), moment(2)), (moment(3), moment(4)), (moment(5), moment(5))] and left_out_at(moment(5)) == [],
      f"fine again: stored at the round that saw it ({[r[:2] for r in own_rows()]})")
overrides["indexer"] = [{**i, "enable": False} if i["id"] != OUT else i for i in INDEXERS]
round_at(6)
del overrides["indexer"]
round_at(7)
check(len(own_rows()) == 5 and own_rows()[3][3] == f",{OUT}," and own_rows()[4][3] == ENABLED_IDS,
      f"indexers switched off and on again: each a sample of its own ({[r[3] for r in own_rows()]})")
before = all_rows()
for minute in range(8, 68):
    round_at(minute)
check(all_rows() - before == 6 and [r[0] for r in own_rows()[-6:]] == [moment(m) for m in (17, 27, 37, 47, 57, 67)],
      f"an hour without a change: one sample every ten minutes, six rows ({all_rows() - before})")
db.set_setting("arr_prowlarr_watch_interval", "15")
db.forget_settings()
check(round_at(68) == 15 and own_rows()[-1][:3] == (moment(68), moment(68), 15),
      f"the interval set to 15 s: the round says so and a new sample keeps it ({own_rows()[-1]})")
db.set_setting("arr_prowlarr_watch_interval", "60")
db.forget_settings()

print()
print("--- B) the statistics: measured while the loop ran ---")
clear()
for minute in range(0, 31):
    overrides["indexerstatus"] = [blocked(9.5)] if 10 <= minute < 15 else []
    round_at(minute)
overrides["indexerstatus"] = []
running = availability(30.5)
check(running[NAMES[OUT]]["measured"] == 1830 and running[NAMES[OUT]]["outages"] == 1 and running[NAMES[OUT]]["longest"] == 330
      and running[NAMES[OUT]]["running"] is False and all(r["measured"] == 1830 for r in running.values() if r["name"] in NAMES.values()),
      f"half an hour of rounds, the last one half a minute ago: measured up to now; the outage from its failing since to"
      f" the round that saw it fine, 5.5 min ({running[NAMES[OUT]]})")
stopped = availability(35)
check(stopped[NAMES[OUT]]["measured"] == 1800,
      f"the loop stopped (the app down): measured only up to its last round ({stopped[NAMES[OUT]]['measured']})")
clear()
captured.clear()
for minute in range(0, 16):
    overrides["indexerstatus"] = httpx.Response(500) if 6 <= minute < 9 else []
    round_at(minute)
overrides["indexerstatus"] = []
check([r[:2] for r in own_rows()] == [(moment(0), moment(5)), (moment(9), moment(15))],
      f"Prowlarr not answering: nothing stored, the next answer starts a new sample ({[r[:2] for r in own_rows()]})")
check(availability(40)[NAMES[OUT]]["measured"] == (5 + 6) * 60,
      "the rounds Prowlarr did not answer are not measured time")
check(not captured, f"and it is no error in the log - the live state on the page says Prowlarr does not answer ({captured[-1:]})")

print()
print("--- C) the switch off, Prowlarr not connected ---")
clear()
db.set_setting("arr_prowlarr_watch_enabled", "0")
db.forget_settings()
asked.clear()
captured.clear()
check(round_at(0) == 60 and asked == [] and own_rows() == [] and not captured, "the switch off: the loop asks nothing, stores nothing, logs nothing")
asyncio.run(registry.synchronize_all())
prowlarr.live.CACHE.clear()
asyncio.run(prowlarr.live_state())
check([r[1:3] for r in own_rows()] == [(None, 3600)], f"the *Arr sync task samples as before (a page within ten minutes not) ({own_rows()})")
clear()
prowlarr.live.CACHE.clear()
asyncio.run(prowlarr.live_state())
check([r[1:3] for r in own_rows()] == [(None, 3600)], f"a page samples as before ({own_rows()})")
db.set_setting("arr_prowlarr_watch_enabled", "1")
prowlarr.save_settings(False, "http://prowlarr.test", "")
db.forget_settings()
clear()
asked.clear()
for minute in range(3):
    round_at(minute)
check(asked == [] and own_rows() == [] and not captured, "Prowlarr not connected: the loop idles quietly - no request, no row, no line in the log")
connect_prowlarr(KEY)


async def with_the_loop(action: Any, first_sample: bool = True) -> Any:
    """Run the real loop in the background until its first round stored a sample (`first_sample` False - it measures
    nothing: until it runs and had its first round), then the action; stop the loop."""
    rows_before = len(loop_rows())
    job = asyncio.create_task(prowlarr.watch_indexers_forever())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if len(loop_rows()) > rows_before if first_sample else prowlarr.samples.LOOP["running"] > 0:
            break
    try:
        return await action()
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


print()
print("--- D) while the loop measures, neither the task nor a page samples ---")
clear()


async def task_and_page() -> None:
    measuring = prowlarr.samples.watching()
    await registry.synchronize_all()
    prowlarr.live.CACHE.clear()
    state = await prowlarr.live_state()
    check(measuring and state["available"], "(the loop measures, the page's live state loaded)")


asyncio.run(with_the_loop(task_and_page))
check(len(loop_rows()) == 1 and len(own_rows()) == 1,
      f"the *Arr sync task and a page store no sample of their own while the loop measures ({own_rows()})")
check(not prowlarr.samples.watching(), "the loop stopped: it measures no more")
clear()
asyncio.run(registry.synchronize_all())
check([r[1] for r in own_rows()] == [None], f"and the task samples again ({own_rows()})")


async def beside_the_loop(connected: bool) -> tuple[bool, bool, list[Any], list[Any]]:
    """While the loop runs: whether it runs and measures, then the samples of the *Arr sync task (with Prowlarr not
    connected the task does not get to Prowlarr - its sample is called straight) and of a page."""
    running, measuring = prowlarr.samples.LOOP["running"] > 0, prowlarr.samples.watching()
    clear()
    await (registry.synchronize_all() if connected else prowlarr.take_sample())
    task = [r[1:3] for r in own_rows()]
    clear()
    prowlarr.live.CACHE.clear()
    await prowlarr.live_state()
    return running, measuring, task, [r[1:3] for r in own_rows()]


db.set_setting("arr_prowlarr_watch_enabled", "0")
db.forget_settings()
found = asyncio.run(with_the_loop(lambda: beside_the_loop(True), first_sample=False))
check(found == (True, False, [(None, 3600)], [(None, 3600)]),
      f"the loop runs, switched off: it does not measure - the *Arr sync task and a page sample as before ({found})")
db.set_setting("arr_prowlarr_watch_enabled", "1")
prowlarr.save_settings(False, "http://prowlarr.test", "")
db.forget_settings()
found = asyncio.run(with_the_loop(lambda: beside_the_loop(False), first_sample=False))
check(found == (True, False, [(None, 3600)], [(None, 3600)]),
      f"the loop runs, Prowlarr not connected: it does not measure - the task's sample and a page's are stored ({found})")
connect_prowlarr(KEY)
db.forget_settings()
found = asyncio.run(with_the_loop(lambda: beside_the_loop(True)))
check(found == (True, True, [], []), f"the loop runs, switched on and connected: neither the task nor a page samples ({found})")
samples = prowlarr.samples
told = [samples.how_measured()]
samples.LOOP["running"] += 1               # as if the loop ran: its rounds are called one by one
try:
    overrides["indexerstatus"] = httpx.Response(500)
    round_at(0)
    told.append(samples.how_measured())
    overrides["indexerstatus"] = []
    round_at(1)
    told.append(samples.how_measured())
    db.set_setting("arr_prowlarr_watch_enabled", "0")
    round_at(2)
    told.append(samples.how_measured())
    db.set_setting("arr_prowlarr_watch_enabled", "1")
finally:
    samples.LOOP["running"] -= 1
check(told == [samples.BY_TASK, samples.NO_ANSWER, samples.BY_LOOP, samples.BY_TASK],
      f"how it is measured (the card before the first sample): by the task with no loop, the loop without Prowlarr's"
      f" answer, by the loop; switched off by the task again ({told})")

print()
print("--- E) the marker of a stored change ---")
from jellyscope.web import base as web_base  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
ADMIN = accounts.get_by_name("admin")
clear()
chain = watch.Chain()


def markers() -> tuple[str, str]:
    db.forget_settings()
    return prowlarr.status_marker(), web_base.change_markers(ADMIN)["arr"]


start = markers()
round_at(0)
first = markers()
check(first[0] != start[0] and first[1] != start[1], "the first sample is a change: the marker moves, and with it the arr marker of the cards")
round_at(1)
check(markers() == first, "a round that sees the same moves no marker")
for minute in range(2, 12):
    round_at(minute)
check(len(loop_rows()) == 2 and markers() == first, f"nor the sample of the ten minutes ({len(loop_rows())} samples)")
overrides["indexerstatus"] = [blocked(11.5)]
round_at(12)
changed = markers()
check(changed[0] != first[0] and changed[1] != first[1], "an indexer left out: both move")
round_at(13)
check(markers() == changed, "and stand while it stays left out")
overrides["indexerstatus"] = []
round_at(14)
check(markers()[0] != changed[0], "fine again: it moves")

print()
print("--- F) the loop never dies ---")
clear()
real_status = watch.status_sample
calls: list[int] = []


async def failing_twice(*args: Any, **kwargs: Any) -> Any:
    calls.append(1)
    if len(calls) <= 2:
        raise RuntimeError("the indexers went missing")
    return await real_status(*args, **kwargs)


restore = substitute(prowlarr, "status_sample", failing_twice)
watch.SECOND = 0.01
captured.clear()


async def survive() -> tuple[bool, bool, bool]:
    job = asyncio.create_task(prowlarr.watch_indexers_forever())
    for _ in range(500):
        await asyncio.sleep(0.01)
        if loop_rows():
            break
    alive = not job.done()
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    return alive, job.cancelled(), prowlarr.samples.watching()


alive, cancelled, still = asyncio.run(survive())
restore()
watch.SECOND = 1.0
check(alive and len(calls) >= 3 and len(loop_rows()) == 1,
      f"two rounds with an unexpected error: the loop goes on and the third round stores ({len(calls)} rounds, {loop_rows()})")
check(sum("measuring the indexers' status failed: the indexers went missing" in m for m in captured) == 2,
      f"each error is a line in the log ({captured[-2:]})")
check(cancelled and not still, "cancelled, the loop ends - and measures no more")

print()
print("--- G) the app's start runs it, not in the demo mode ---")
from jellyscope import demodata  # noqa: E402
from jellyscope.web import app as application  # noqa: E402
from jellyscope.web import start as web_start  # noqa: E402

events: list[str] = []


async def fake_loop() -> None:
    events.append("started")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        events.append("cancelled")
        raise


async def idle() -> None:
    await asyncio.Event().wait()


restores = [substitute(prowlarr, "watch_indexers_forever", fake_loop)]
real_collector, real_scheduler = collector.run_forever, tasks.run_scheduler
real_refresh, real_live = demodata.keep_refreshing, demodata.keep_live_forever
collector.run_forever = tasks.run_scheduler = demodata.keep_refreshing = demodata.keep_live_forever = idle


async def start_up() -> None:
    async with web_start.lifespan(application):
        await asyncio.sleep(0.05)


asyncio.run(start_up())
check(events == ["started", "cancelled"], f"the start runs the loop beside the collector, the shutdown stops it ({events})")
events.clear()
real_config = web_start.config
web_start.config = dataclasses.replace(real_config, demo_mode=True)
asyncio.run(start_up())
web_start.config = real_config
check(events == [], f"the demo mode has no Prowlarr: no loop ({events})")
collector.run_forever, tasks.run_scheduler = real_collector, real_scheduler
demodata.keep_refreshing, demodata.keep_live_forever = real_refresh, real_live
for undo in restores:
    undo()

print()
print("--- H) the loop's writes wait for another writer only briefly; a broken table is one line ---")
clear()
chain = watch.Chain()
# The lock is SQLite's (one writer for the whole file); on PostgreSQL the test runs through a copy without the file
# (.superpowers/tools/test_on_pg.py) and the short wait there is lock_timeout - nothing to hold the same way.
SQLITE_FILE = os.environ.get("DATABASE_PATH", "")
if SQLITE_FILE:
    round_at(0)
    other = sqlite3.connect(SQLITE_FILE, timeout=1, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")            # another writer holds the lock: a VACUUM, a restore, a long import
    tried: list[str] = []
    real_store = prowlarr.samples._store_watched


    def counted_store(*args: Any) -> bool:
        tried.append(args[0])
        return real_store(*args)


    restore = substitute(prowlarr, "_store_watched", counted_store)
    captured.clear()
    began = time.perf_counter()
    round_at(1)                                 # the same state: it would move the sample's last round
    spent = time.perf_counter() - began
    check(spent < 2 and not captured and chain.sampled_at == "" and tried == [],
          f"the last round not moved under another writer's lock: skipped quietly at once, no new sample tried - the round"
          f" is not measured ({spent:.1f} s, {captured[-1:]}, {tried})")
    overrides["indexerstatus"] = [blocked(1.5)]
    began = time.perf_counter()
    round_at(2)                                 # the chain broken: a new sample
    spent = time.perf_counter() - began
    check(spent < 2 and not captured and tried == [moment(2)] and chain.sampled_at == "",
          f"a new sample under the lock: skipped quietly at once too ({spent:.1f} s, {captured[-1:]})")
    other.execute("ROLLBACK")
    other.close()
    round_at(3)
    restore()
    check([r[:2] for r in own_rows()] == [(moment(0), moment(0)), (moment(3), moment(3))] and left_out_at(moment(3)) == [OUT],
          f"the lock gone: the next round stores a new sample, the rounds under the lock are not measured ({own_rows()})")
else:
    print("(skipped on PostgreSQL: another writer's lock is held as an SQLite file)")
overrides["indexerstatus"] = []
with db.connect() as conn:                  # an older backup restored, the app not restarted yet: no such column
    conn.execute("ALTER TABLE prowlarr_sample DROP COLUMN measured_until")
captured.clear()
for minute in range(4, 9):
    round_at(minute)
broken = [m for m in captured if "could not store a sample of the indexers' status" in m]
check(len(broken) == 1 and len(captured) == 1,
      f"a table without the column: one line in the log, then quiet - not a line every round ({len(broken)}, {captured[-1:]})")
with db.connect() as conn:
    conn.execute("ALTER TABLE prowlarr_sample ADD COLUMN measured_until TEXT")
round_at(9)
round_at(10)
check(len(captured) == 1 and loop_rows()[-1][:2] == (moment(9), moment(10)), f"it works again: stored, quietly ({loop_rows()[-1:]})")
with db.connect() as conn:
    conn.execute("ALTER TABLE prowlarr_sample DROP COLUMN measured_until")
round_at(11)
round_at(12)
with db.connect() as conn:
    conn.execute("ALTER TABLE prowlarr_sample ADD COLUMN measured_until TEXT")
check(len(captured) == 2, f"broken again after it worked: one more line ({len(captured)})")

print()
print("--- I) a frozen process does not stretch its sample across the freeze ---")
clear()
chain = watch.Chain()
for minute in (0, 1, 2, 7, 8):              # frozen for five minutes after the round at 2 (a host suspend, docker pause)
    round_at(minute)
check([r[:2] for r in own_rows()] == [(moment(0), moment(2)), (moment(7), moment(8))],
      f"the first round after the freeze starts a new sample ({[r[:2] for r in own_rows()]})")
check(availability(30)[NAMES[OUT]]["measured"] == 3 * 60, "the five minutes nobody measured are not measured time")
check(watch.ROUND_GAP_TIMES == stats_prowlarr.LOOP_GAP_TIMES,
      "the loop breaks its chain where the statistics stop measuring a step between its samples")

print()
print("--- J) the pauses: never shorter than the interval, a changed one in a moment, no request in between ---")
clear()
calls.clear()


async def always_failing(*args: Any, **kwargs: Any) -> Any:
    calls.append(1)
    raise RuntimeError("the indexers went missing")


async def for_a_while(seconds: float, action: Any = None) -> None:
    """Run the real loop for `seconds` (real ones), the action (if any) a tenth of a second in; then stop it."""
    job = asyncio.create_task(prowlarr.watch_indexers_forever())
    await asyncio.sleep(0.1)
    if action:
        action()
    await asyncio.sleep(seconds - 0.1)
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)


db.set_setting("arr_prowlarr_watch_interval", "600")
db.forget_settings()
restore = substitute(prowlarr, "status_sample", always_failing)
watch.SECOND = 0.001                         # 600 s of the loop = 0.6 s
captured.clear()
asyncio.run(for_a_while(0.4))
restore()
check(len(calls) == 1, f"an unexpected error, the interval 600 s: the next round only after the interval - not after"
                       f" 2, 4, 8 ... s ({len(calls)} rounds)")
calls.clear()


async def counted_status(*args: Any, **kwargs: Any) -> Any:
    calls.append(1)
    return await real_status(*args, **kwargs)


restore = substitute(prowlarr, "status_sample", counted_status)
watch.SECOND = 0.002                         # 600 s of the loop = 1.2 s, the 15 s one 0.03 s
asked.clear()
seen: list[int] = []
asyncio.run(for_a_while(0.25, lambda: seen.extend([len(calls), len(asked)])))
check(seen == [1, 2], f"while it waits the loop asks Prowlarr nothing: one round, its two requests ({seen})")
calls.clear()
asyncio.run(for_a_while(0.8, lambda: db.set_setting("arr_prowlarr_watch_interval", "15")))
check(len(calls) >= 3, f"the interval changed from 600 to 15 s during the pause: it takes effect at once, rounds every"
                       f" 15 s ({len(calls)} rounds in 0.8 s, 600 s of the loop would be 1.2 s)")
restore()
watch.SECOND = 1.0
db.set_setting("arr_prowlarr_watch_interval", "60")
db.forget_settings()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
