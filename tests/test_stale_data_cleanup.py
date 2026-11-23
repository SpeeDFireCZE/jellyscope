# -*- coding: utf-8 -*-
"""Cleanup: what is not to stay lying in the database.

Two things that by themselves only grow or stay behind from old versions - and nobody notices, because they break
nothing:

1. **scan_log.** The quick sync runs every fifteen minutes, so over ninety rows are added a day. Nothing was ever
   deleted, yet every call of /health asks the table - and that comes from every open tab every ten seconds.

2. **Dropped settings.** When some option disappears from the app, its row in the `settings` table stays. Whoever
   looks into the database searches in vain for why nothing follows it.

Run:
    .venv/bin/python tests/test_stale_data_cleanup.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cleanup.db")
os.environ["SECRET_KEY"] = "test-signing-key"

from jellyscope import db, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


def record_count(kind: str) -> int:
    return int(db.query_value(
        "SELECT COUNT(*) FROM scan_log WHERE kind = ?", (kind,), default=0))


print("--- scan_log does not grow forever ---")
for _ in range(scanner.RECORDS_PER_KIND + 40):
    scanner.finish_task_log(scanner.start_task_log("recent"), "done")

check(record_count("recent") == scanner.RECORDS_PER_KIND,
      f"of {scanner.RECORDS_PER_KIND + 40} runs {record_count('recent')} stayed "
      f"(cap {scanner.RECORDS_PER_KIND})")

# Every kind is trimmed separately - otherwise the frequent quick sync would push out the records of backups, of
# which there are a few a month.
scanner.finish_task_log(scanner.start_task_log("backup"), "done")
scanner.finish_task_log(scanner.start_task_log("library"), "done")
for _ in range(50):
    scanner.finish_task_log(scanner.start_task_log("recent"), "done")
check(record_count("backup") == 1, "the backup kept its record")
check(record_count("library") == 1, "the sync too")

# And above all: the last run must stay the last one, not get lost with the cleanup.
last = scanner.last_scan("recent")
highest = db.query_value("SELECT MAX(id) FROM scan_log WHERE kind = 'recent'")
check(last is not None and last["id"] == highest,
      "the last record is still available")


print()
print("--- dropped settings are deleted from an old database ---")
with db.connect() as conn:
    for key in db.DROPPED_SETTINGS:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')"
                     " ON CONFLICT (key) DO UPDATE SET value = '1'", (key,))
    conn.execute("INSERT INTO settings (key, value) VALUES ('unknown_key', 'x')"
                 " ON CONFLICT (key) DO NOTHING")
db.forget_settings()
check(all(k in db.get_settings() for k in db.DROPPED_SETTINGS),
      "starting state: the old settings are in the database")

db.init_db()
db.forget_settings()
stored = db.get_settings()
check(not any(k in stored for k in db.DROPPED_SETTINGS),
      f"after the start they are gone ({[k for k in db.DROPPED_SETTINGS if k in stored]})")
check(stored.get("unknown_key") == "x",
      "an unknown key stayed - only the named list is deleted")
check(stored.get("library_sync_time") == "03:30",
      "and the live settings are in their place")


print()
print("--- the count of plays is not found out through the whole list ---")
# /health asks from every open tab every ten seconds. For one number three tables used to be joined and every column
# pulled out.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at)"
                 " VALUES ('i1', 'Movie', 'Movie', 0, ?)", (db.utcnow(),))
    for i in range(3):
        conn.execute(
            """INSERT INTO playback (session_key, item_id, item_name, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'i1', 'Movie', ?, ?, 600, ?)""",
            (f"s{i}", db.utcnow(), db.utcnow(), 1 if i < 2 else 0),
        )

check(stats.active_session_count() == 2,
      f"only running sessions count ({stats.active_session_count()})")
check(stats.active_session_count() == len(stats.active_sessions()),
      "the number matches what the full list returns")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
