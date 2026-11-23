# -*- coding: utf-8 -*-
r"""Forgetting a viewer by hand deletes at once, the database file is rewritten only at night.

`db.compact()` (VACUUM) rewrites the whole file and SQLite holds a lock for that
time - on a large database a minute, during which the app stands still. After
the night's tidying nobody minds; after a click on "Ano, zapomenout" it would
jam everything. That is why a click only deletes, and the app notes the rewrite
for the time of the History tidying task.

What is checked here:

* `to_bin=True` (the button) deletes the rows but leaves the file be - and **writes a debt**,
* `to_bin=False` deletes and rewrites at once: the trace disappears right away,
* the deadline is the first time of the tidying **after** the request - whoever clicks five minutes
  after it waits until tomorrow; whoever clicks before it gets it today,
* the scheduler pays the debt when the deadline comes - not earlier,
* the night's tidying, which rewrites by itself, keeps the debt while something
  waits in the bin,
* after a failed rewrite the debt stays (it is tried again the next night),
* the message and the Restore dialog say until when the intervention can be taken back.

Run:
    .venv/bin/python tests/test_deferred_compaction.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "odlozene.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, purge, tasks  # noqa: E402

failures = 0

# "There was no rewrite" is recognised by db.compact() not being called - not
# by bytes in the file. Linux distributions build SQLite with
# SECURE_DELETE, which zeroes deleted rows already at DELETE, so
# the trace would be missing even without a rewrite and the test would lie on Linux.
REWRITES = {"count": 0}
_original_compact = db.compact


def _counted_compact() -> bool:
    REWRITES["count"] += 1
    return _original_compact()


db.compact = _counted_compact


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def fill(user_id: str, mark: str, count: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (user_id, mark, db.utcnow()))
        for n in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,0)",
                (f"{user_id}-{n}", user_id, mark, f"i-{n}",
                 "2020-01-01 10:00:00", "2020-01-01 10:20:00", 1200))
        conn.commit()


def traces(mark: str) -> int:
    """How many times the name is in the database file (and in the journal beside it)."""
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    total = 0
    for suffix in ("", "-wal"):
        path = Path(str(DB) + suffix)
        if path.exists():
            total += path.read_bytes().count(mark.encode())
    return total


MARK = "MirekNeobvykleJmeno"
fill("u-mirek", MARK, 200)

print("--- forgetting by hand only deletes, it writes the rewrite as a debt ---")
check(purge.compaction_pending() == "", "at the start nothing waits")
result = purge.forget_user("u-mirek", to_bin=True)
check(result["deleted"] == 200, f"200 rows were deleted ({result['deleted']})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE user_id = ?",
                     ("u-mirek",)) == 0, "they are gone from the database at once")
check(REWRITES["count"] == 0, "but the file was not rewritten (compact was not called)")
check(purge.compaction_pending() != "", "and the debt is written")

print()
print("--- the deadline: the first time of the tidying after the request ---")
db.set_setting("task_purge_time", "04:45")
db.forget_settings()


def deadline_for(local_request: datetime) -> datetime | None:
    """Slips in the request's time (it is written in UTC) and returns the computed deadline."""
    utc = (local_request.astimezone() if local_request.tzinfo else
           local_request.replace(tzinfo=datetime.now().astimezone().tzinfo))
    db.set_setting(purge.DEFERRED_KEY,
                   utc.astimezone(__import__("datetime").timezone.utc)
                   .strftime(db.TIME_FORMAT))
    db.forget_settings()
    return tasks.deferred_compaction_time(now=local_request)


today = datetime(2026, 9, 10, 0, 0)
target = deadline_for(today.replace(hour=15, minute=0))
check(target == today.replace(day=11, hour=4, minute=45),
      f"a request at 15:00 -> tomorrow 04:45 ({target})")
target = deadline_for(today.replace(hour=4, minute=50))
check(target == today.replace(day=11, hour=4, minute=45),
      f"a request at 04:50 (five minutes after) -> tomorrow, not in five minutes ({target})")
target = deadline_for(today.replace(hour=2, minute=0))
check(target == today.replace(hour=4, minute=45),
      f"a request at 2:00 -> still today 04:45 ({target})")

db.set_setting(purge.DEFERRED_KEY, "")
db.forget_settings()
check(tasks.deferred_compaction_time() is None, "without a debt no deadline")

print()
print("--- the scheduler pays only at the deadline ---")
# A fresh request: the deadline is only at night, so nothing may happen now.
purge.defer_compaction()
asyncio.run(tasks._finish_deferred_compaction())
check(purge.compaction_pending() != "", "before the deadline the debt stays")
check(REWRITES["count"] == 0, "and the file was not rewritten")

# A request "from the day before yesterday": the deadline passed long ago.
old = (datetime.now().astimezone().astimezone(
    __import__("datetime").timezone.utc) - timedelta(days=2))
db.set_setting(purge.DEFERRED_KEY, old.strftime(db.TIME_FORMAT))
db.forget_settings()
asyncio.run(tasks._finish_deferred_compaction())
check(purge.compaction_pending() == "", "after the deadline the debt is paid")
check(REWRITES["count"] == 1, "the rewrite ran exactly once")
after = traces(MARK)
# Two traces may stay and both are on purpose: the account in `users` (that is
# in Jellyfin, it is not deleted from here) and the row in `forgettings`, by which
# the page offers restoring from a backup. They disappear with it.
check(after <= 2, f"and the trace of the plays is gone ({after}: the account and the record of the forgetting)")

print()
print("--- the night's tidying rewrites by itself, the debt goes with it ---")
fill("u-jana", "JanaNeobvykleJmeno", 50)
purge.forget_user("u-jana", to_bin=True)
check(purge.compaction_pending() != "", "after forgetting there is a debt")
fill("u-karel", "KarelNeobvykleJmeno", 30)          # the year 2020 = old
tidied = purge.delete_old(purge.MIN_DAYS)
check(tidied["deleted"] == 30, f"the tidying deleted 30 old ones ({tidied['deleted']})")
check(purge.compaction_pending() != "", "the debt stayed (Jana is in the bin)")
# Jana is in the bin, so the tidying must not delete her rows - and must not cancel the order
# for the rewrite, otherwise the bin would have no time to disappear.
check(purge.bin_has_rows(), "Jana stays in the bin")
check(purge.compaction_pending() != "",
      "and the order for the rewrite stayed - the bin must have a time to be emptied")

print()
print("--- outside the bin (to_bin=False) it deletes straight away ---")
purge.empty_bin()                      # so we tidy up after Jana
purge.finish_deferred_compaction()
fill("u-petr", "PetrNeobvykleJmeno", 40)
purge.forget_user("u-petr")
check(traces("PetrNeobvykleJmeno") <= 1, "the trace disappeared at once")
check(purge.compaction_pending() == "", "and no debt came about")

print()
print("--- a failed rewrite leaves the debt ---")
db.compact = lambda: False
try:
    purge.defer_compaction()
    check(purge.finish_deferred_compaction() is False, "it reports a failure")
    check(purge.compaction_pending() != "", "and the debt stayed for the next night")
finally:
    db.compact = _counted_compact
purge.finish_deferred_compaction()
check(purge.compaction_pending() == "", "paid the second time")

print()
print("--- the route and the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword"})
fill("u-eva", "EvaNeobvykleJmeno", 20)
before_click = REWRITES["count"]
response = client.post("/settings/history/forget", data={"user_id": "u-eva"},
                       follow_redirects=True)
text = re.sub(r"<[^>]+>", " ", response.text)
text = re.sub(r"\s+", " ", text)
check("(20 přehrávání)" in text, "the message says how many went")
check("jde vrátit zpět" in text,
      "and that it can be taken back until the night")
check("Nejbližší úklid" in text,
      "the Restore dialog says until when it can be taken back")
check(REWRITES["count"] == before_click, "the click did not rewrite the file")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
