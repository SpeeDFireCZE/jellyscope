# -*- coding: utf-8 -*-
r"""Imported history: fifteen hours at a twenty-eight-minute episode.

Playback Reporting and Jellystat measure the time **on the clock** - from the start to the end of the
session. When somebody falls asleep at an episode and the player stays open till the morning, the source
reports fifteen hours of watching. Jellyscope took it over as it came, so one episode beat a whole month in
the statistics.

From the real data that revealed it:

    an episode of 28 min  17:25:58 → 08:58:09  =  55 931 s
    Zoo                   06:22:39 → 07:36:40  =  90 841 s

In both cases `watched_seconds` equalled the difference of the times to the second - nobody watched
anything, only the player ran.

Run:
    .venv/bin/python tests/test_import_durations.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "durations.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
TICK = 10_000_000


def write(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def title(item_id: str, name: str, minutes: int | None) -> None:
    write(
        """INSERT INTO items (id, name, type, library_id, runtime_ticks, is_missing)
           VALUES (?,?,'Episode','lib',?,0)""",
        (item_id, name, minutes * 60 * TICK if minutes else None),
    )


def play(key: str, item_id: str, seconds: int, paused: int = 0) -> int:
    write(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 item_name, item_type, started_at, last_seen_at,
                                 watched_seconds, paused_seconds, is_active)
           VALUES (?,'u1','se',?,'x','Episode',?,?,?,?,0)""",
        (key, item_id, db.utcnow(), db.utcnow(), seconds, paused),
    )
    return int(db.query_value(
        "SELECT id FROM playback WHERE session_key = ?", (key,)))


title("dil-28", "Záplava kostlivců", 28)
title("dil-45", "Zoo", 45)
title("bez-delky", "Neznámá délka", None)

# Exactly the two records that revealed it.
skeletons = play("import:pbr:1", "dil-28", 55931)
zoo = play("import:jst:1", "dil-45", 90841)
# Honest watching of the same episode - that must stay untouched.
honest = play("import:pbr:2", "dil-28", 1500)
# A little over the length (rewinding, a scene again) neither.
almost = play("import:pbr:3", "dil-28", 28 * 60 + 200)
# Our own collection has its increment limited already when measured - we do not touch it.
own = play("demo-sess-9::dil-28", "dil-28", 55931)
# And for a title without a known length there is nothing to judge by.
unknown = play("import:pbr:4", "bez-delky", 55931)

print("--- what is capped ---")
result = importers.cap_nonsense_durations()
check(result["rows"] == 2, f"two records were capped ({result['rows']})")


def seconds_of(row_id: int) -> tuple[int, int]:
    row = db.query_one(
        "SELECT watched_seconds, paused_seconds FROM playback WHERE id = ?",
        (row_id,))
    return int(row["watched_seconds"]), int(row["paused_seconds"])


watched, paused = seconds_of(skeletons)
check(watched == int(28 * 60 * 1.5),
      f"of fifteen hours 1.5 times the episode is left ({watched} s)")
check(watched + paused == 55931,
      f"and the surplus was poured into the pause, the sum fits ({watched} + {paused})")

watched, _ = seconds_of(zoo)
check(watched == int(45 * 60 * 1.5), f"the same with a longer episode ({watched} s)")

print()
print("--- what it does not touch ---")
check(seconds_of(honest)[0] == 1500, "honest watching stays")
check(seconds_of(almost)[0] == 28 * 60 + 200, "a slight overrun does not matter either")
check(seconds_of(own)[0] == 55931, "our own collection is not adjusted")
check(seconds_of(unknown)[0] == 55931, "without a known length there is nothing to judge by")

print()
print("--- a second run has nothing to do ---")
check(importers.cap_nonsense_durations()["rows"] == 0, "a repetition changes nothing")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
