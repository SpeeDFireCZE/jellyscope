# -*- coding: utf-8 -*-
r"""Deleting deletes exactly what it is to - and nothing more.

This is the test one sleeps at night because of. Both deleting paths get a
value from outside (a viewer's id from a form, a number of days from the
settings) and both reach for `DELETE`. A bug in either means everything gone.

What is guarded:

* **Forgetting a viewer** deletes only them. The other viewers must have
  exactly as many records after the intervention as before - each is checked
  apart, not only the sum, because the sum would lie were one to lose and
  another to gain.
* **A slipped-in value deletes nothing.** `' OR '1'='1`, `%`, empty, spaces.
  The query is parametrised, so it should not pass - but exactly this is the
  bug that shows only on an empty database.
* **Clearing out by age** leaves what is fresh and **never** touches a session
  running right now.
* **Nothing but the history.** The tables of titles, libraries and accounts
  must stay unchanged.

Run:
    .venv/bin/python tests/test_delete_only_own.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "mazani.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, purge  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def when(days_ago: int) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def record(user_id: str, name: str, key: str, age_days: int,
           active: int = 0) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " started_at, last_seen_at, watched_seconds, is_active)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (key, user_id, name, "i-1", when(age_days), when(age_days),
             1200, active))
        conn.commit()


def counts() -> dict[str, int]:
    """How many records which viewer has. It is compared after every intervention."""
    return {r["user_id"]: r["n"] for r in db.query_all(
        "SELECT user_id, COUNT(*) AS n FROM playback GROUP BY user_id")}


db.init_db()
with db.connect() as conn:
    for uid, name in (("u-1", "Petr"), ("u-2", "Jana"), ("u-3", "Mirek")):
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (uid, name, db.utcnow()))
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
        " VALUES ('lib','Filmy','movies','[]',?)", (db.utcnow(),))
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, synced_at)"
        " VALUES ('i-1','Film','Movie','lib',?)", (db.utcnow(),))
    conn.commit()

for index in range(5):
    record("u-1", "Petr", f"p-{index}", 400)
for index in range(4):
    record("u-2", "Jana", f"j-{index}", 400)
for index in range(3):
    record("u-3", "Mirek", f"m-{index}", 400)
record("u-2", "Jana", "j-ziva", 0, active=1)

print("--- a slipped-in value deletes nothing ---")
SLIPPED_IN = ["' OR '1'='1", "%", "_", "u-1' OR user_id LIKE '%",
              "", "   ", "'; DELETE FROM playback; --", "*"]
for naughty in SLIPPED_IN:
    before = counts()
    result = purge.forget_user(naughty)
    check(result["deleted"] == 0 and counts() == before,
          f"{naughty[:26]!r} deleted nothing")

print()
print("--- forgetting deletes only that one ---")
before = counts()
result = purge.forget_user("u-2")
after = counts()
check(result["deleted"] == 5, f"Jana had 5 records ({result['deleted']})")
check("u-2" not in after, "and none remained to her")
check(after.get("u-1") == before["u-1"], f"Petr kept {after.get('u-1')} of {before['u-1']}")
check(after.get("u-3") == before["u-3"], f"Mirek kept {after.get('u-3')} of {before['u-3']}")

print()
print("--- the history yes, the rest no ---")
check(db.query_value("SELECT COUNT(*) FROM users") == 3, "the accounts stayed")
check(db.query_value("SELECT COUNT(*) FROM items") == 1, "the titles stayed")
check(db.query_value("SELECT COUNT(*) FROM libraries") == 1, "the libraries stayed")

print()
print("--- clearing out leaves the fresh and the running ---")
for index in range(3):
    record("u-2", "Jana", f"j2-{index}", 5)        # fresh
record("u-1", "Petr", "p-ziva", 500, active=1)     # old, but running

before = counts()
result = purge.delete_old(30)
after = counts()
# The old ones are Petr's (5) and Mirek's (3) - both groups are to go.
check(result["deleted"] == 8, f"8 old ones went ({result['deleted']})")
check(after.get("u-2") == 3, f"Jana's fresh ones stayed ({after.get('u-2')})")
check(db.query_value(
    "SELECT COUNT(*) FROM playback WHERE session_key = 'p-ziva'") == 1,
    "the session running right now stayed, even though it is old")
check("u-3" not in after, "Mirek's old ones went too")

print()
print("--- the number of days stays within bounds ---")
# Neither zero nor a negative number may mean "delete everything" and a big
# number must not fail. Jana's 3 fresh ones + Petr's running one remain.
# Zero and a negative number move the boundary to "now" or into the future,
# a giant number used to fail on OverflowError and a text is no number at all.
# None of these values may change a single row.
for nonsense in (0, -5, 10 ** 9, 10 ** 12, "nesmysl", None, ""):
    before_attempt = db.query_value("SELECT COUNT(*) FROM playback")
    try:
        purge.delete_old(nonsense)
        failed = ""
    except Exception as error:      # noqa: BLE001
        failed = type(error).__name__
    left = db.query_value("SELECT COUNT(*) FROM playback")
    check(not failed, f"days={nonsense!r}: did not fail {failed}")
    check(left == before_attempt,
          f"days={nonsense!r}: kept {left} of {before_attempt}")

# A valid number on the contrary MAY delete - even when it is smaller than the
# form allows. A decimal one is evened out to whole days.
print()
print("--- a valid boundary may delete ---")
before_real = db.query_value("SELECT COUNT(*) FROM playback")
result = purge.delete_old(1.5)
left = db.query_value("SELECT COUNT(*) FROM playback")
check(result["deleted"] == before_real - left,
      f"a one-day boundary cleared out {result['deleted']}")
check(db.query_value(
    "SELECT COUNT(*) FROM playback WHERE is_active = 1") >= 1,
    "and the running session survived it too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
