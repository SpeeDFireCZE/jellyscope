# -*- coding: utf-8 -*-
r"""The counts for "tidying the data" are fast - and still the same.

Those numbers show in two places in Settings and are computed every time the page opens. On 150,000 plays
that took **6.2 seconds**, so Settings opened for seven seconds. Two changes sped it up:

1. **Converting the database's time does not go through `strptime`.** It is called twice per row, i.e.
   300,000 times when the page opens once, and strptime parses the format string again on every call. The
   shape is fixed, so it is computed from the characters' positions - 4.8 times faster.

2. **Without imported records the duplicates between sources are not searched for.** A group counts only
   when it holds at least one row from an import; who never imported anything always gets an empty result -
   until now only after going through the whole history.

Both are a speed-up, not a change of rules, and that is exactly what this test guards: **the numbers must
stay the same**. If the fast path behaved differently from the original one, it would change which records
count as duplicates - and by those numbers rows are really deleted.

Run:
    .venv/bin/python tests/test_cleanup_counts.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cleanup-counts.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes)).strftime(db.TIME_FORMAT)


db.init_db()

print("--- the time conversion returns the same as before ---")
# The fast path may hold only for exactly the shape that is in the database. Anything else must fall through
# to strptime - including things strptime refuses. If the fast path accepted more, it would change the merging.
SAMPLES = [
    "2026-09-09 10:20:30", "2020-01-01 00:00:00", "1999-12-31 23:59:59",
    "2024-02-29 12:00:00",              # a leap year
    "2026-13-45 99:99:99",              # the shape fits, the values do not
    "2026-09-09 10:20:30.123",          # milliseconds - strptime refuses
    "2026-09-09T10:20:30",              # ISO with T
    "2026-09-09", "", "nesmysl", None, 12345,
]
for sample in SAMPLES:
    fast = importers._epoch(sample)
    honest = importers._epoch_slow(sample) if sample else None
    check(fast == honest,
          f"{str(sample)[:24]!r}: {fast!r} == {honest!r}")

print()
print("--- without an import the duplicates between sources are not searched for ---")
with db.connect() as conn:
    for number in range(6):
        conn.execute(
            "INSERT INTO playback (session_key,user_id,user_name,item_id,"
            "item_name,started_at,ended_at,last_seen_at,watched_seconds,"
            "is_active) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (f"vlastni-{number}", "u1", "Petr", "i1", "Film",
             minutes_ago(100 + number * 60), minutes_ago(70 + number * 60),
             minutes_ago(70 + number * 60), 1800))
    conn.commit()

check(importers._import_exists() is False, "there is no import here")
check(importers.import_duplicate_groups() == [],
      "and the duplicates between sources are not searched for")

print()
print("--- with an import the counting goes on and finds the same ---")
# Two records of the same viewing: one our own, one from an import. An equally long play of the same title by
# the same viewer within one day.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key,user_id,user_name,item_id,"
        "item_name,started_at,ended_at,last_seen_at,watched_seconds,"
        "is_active) VALUES (?,?,?,?,?,?,?,?,?,0)",
        ("import:pbr:1:i9", "u9", "Jana", "i9", "Jiný film",
         minutes_ago(300), minutes_ago(270), minutes_ago(270), 1800))
    conn.execute(
        "INSERT INTO playback (session_key,user_id,user_name,item_id,"
        "item_name,started_at,ended_at,last_seen_at,watched_seconds,"
        "is_active) VALUES (?,?,?,?,?,?,?,?,?,0)",
        ("vlastni-9", "u9", "Jana", "i9", "Jiný film",
         minutes_ago(280), minutes_ago(250), minutes_ago(250), 1800))
    conn.commit()

check(importers._import_exists() is True, "now there is an import")
groups = importers.import_duplicate_groups()
check(len(groups) == 1, f"one group was found ({len(groups)})")
if groups:
    check(any(str(r["session_key"]).startswith("import:") for r in groups[0]),
          "and the imported record is in it")
check(importers.import_duplicate_count() == 1,
      f"the count to remove is 1 ({importers.import_duplicate_count()})")

print()
print("--- the shortcut must not hide a real duplicate ---")
# The trap: if the shortcut asked wrongly (say for another prefix), this case would quietly return zero and
# nobody would notice.
check(importers.import_duplicate_count() > 0,
      "with an import in the database it returns a non-zero count")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
