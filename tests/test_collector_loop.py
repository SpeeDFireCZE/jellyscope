# -*- coding: utf-8 -*-
r"""The collector's loop survives a database that is briefly unavailable.

The collector runs in the background for the whole life of the application and nothing restarts it. A database
that is locked for a while (the nightly VACUUM of the clear-out, a PostgreSQL restart, a full disk) fails the poll
- and storing that error is a database write too, so it fails as well. The error must not escape the loop: the
collector writes a line into the log, waits and polls again once the database is back.

Run:
    .venv/bin/python tests/test_collector_loop.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "collector-loop.db")
os.environ["SECRET_KEY"] = "test-key-collector-loop"

from jellyscope import collector, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("poll_interval", "2")

# What is "locked" right now: the Jellyfin answer is stored by the poll, the settings are read and written.
locked: set[str] = set()


def _locked() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is locked")


class FakeClient:
    """A Jellyfin that plays nothing; storing its answer fails while the database is locked."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def sessions(self) -> list[dict[str, Any]]:
        if "poll" in locked:
            raise _locked()
        return []


real_set_setting, real_get_int_setting = db.set_setting, db.get_int_setting


def set_setting(key: str, value: Any) -> None:
    if "write" in locked:
        raise _locked()
    real_set_setting(key, value)


def get_int_setting(*args: Any, **kwargs: Any) -> int:
    if "read" in locked:
        raise _locked()
    return real_get_int_setting(*args, **kwargs)


collector.JellyfinClient = FakeClient
db.set_setting, db.get_int_setting = set_setting, get_int_setting


class Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))       # with the traceback of log.exception()


lines = Lines()
logging.getLogger("jellyscope.collector").addHandler(lines)


async def lock_for_a_while(what: set[str], seconds: float = 0.5) -> tuple[bool, str, str]:
    """Run the collector, lock `what` for a moment, then wait for the next good poll.

    Returns (the collector still runs, the last poll before, the last poll after).
    """
    real_set_setting(collector.LAST_POLL_KEY, "")
    lines.lines.clear()
    locked.clear()
    locked.update(what)
    task = asyncio.create_task(collector.run_forever())
    await asyncio.sleep(seconds)
    alive = not task.done()
    before = db.get_setting(collector.LAST_POLL_KEY, "")
    locked.clear()
    after = ""
    for _ in range(80):
        await asyncio.sleep(0.1)
        after = db.get_setting(collector.LAST_POLL_KEY, "")
        if after or task.done():
            break
    alive = alive and not task.done()
    if task.done() and not task.cancelled() and task.exception() is not None:
        print("       the collector ended with", repr(task.exception()))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return alive, before, after


print("--- the poll fails and storing its error fails too ---")
alive, before, after = asyncio.run(lock_for_a_while({"poll", "write"}))
check(alive, "the collector keeps running while the database is locked")
check(not before and bool(after), f"and polls again once it is free ({after!r})")
check(any("database is locked" in line for line in lines.lines),
      f"the failure is written into the log ({[line.splitlines()[0] for line in lines.lines[:2]]})")

print()
print("--- reading the polling interval fails ---")
alive, before, after = asyncio.run(lock_for_a_while({"read", "write"}))
check(alive, "the collector keeps running when even the settings cannot be read")
check(not before and bool(after), f"and polls again once they can ({after!r})")
check(any("database is locked" in line for line in lines.lines),
      f"the failure is written into the log ({[line.splitlines()[0] for line in lines.lines[:2]]})")

print()
print("--- the database is locked when the collector starts ---")
# Closing the playbacks left open by the last run is a database write too, and it stood before the loop's try:
# a database locked at the start (a restore or an update just before the restart) ended the collector before
# its first poll, without a line in the log (the review of the audit fixes).
real_close_orphans = collector.close_orphans


def close_orphans() -> Any:
    if "orphans" in locked:
        raise _locked()
    return real_close_orphans()


collector.close_orphans = close_orphans
alive, before, after = asyncio.run(lock_for_a_while({"orphans"}))
collector.close_orphans = real_close_orphans
check(alive, "the collector starts even when the left-open playbacks cannot be closed right now")
check(bool(after), f"and it polls ({after!r})")
check(any("database is locked" in line for line in lines.lines),
      f"the failure is written into the log ({[line.splitlines()[0] for line in lines.lines[:2]]})")

print()
print("--- a good poll after the outage ---")
check(db.get_setting(collector.STATUS_KEY, "") == "ok", "the status says it collects again")

db.set_setting, db.get_int_setting = real_set_setting, real_get_int_setting

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
