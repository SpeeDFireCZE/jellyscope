# -*- coding: utf-8 -*-
r"""Tidying the data: one action that really files a record.

What it guards:

  1. **A record hanging on the id of a SERIES is filed to a specific episode.**
     It used to only get the series' name filled in - and when it was there
     already, nothing was done. The button then wrote "Jellyfin zná 20 z 20"
     and nothing more, which looked like a broken function.

     The trick is that the series' id **narrows the search**: every series has
     a name "5. díl", but among the episodes of one series it is unambiguous.

  2. **One action instead of two buttons.** The order is not a choice - looking
     things up in Jellyfin makes the links everything else works with.

  3. **When Jellyfin does not answer**, the rest runs anyway.

Run:
    .venv/bin/python tests/test_tidy.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "narovnani.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

SERIES_ID = "6a6152eddb1c02ccb5c3887497e3e64d"

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type)"
                 " VALUES ('lib', 'Seriály', 'tvshows')")
    for season, episode, name in ((1, 5, "Nákup"), (1, 6, "Výlet"), (2, 1, "Návrat")):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, series_id,"
            " series_name, parent_index_number, index_number, is_missing,"
            " synced_at) VALUES (?,?,'Episode','lib',?,?,?,?,0,?)",
            (f"ep-{season}-{episode}", name, SERIES_ID, "Kancelář", season, episode,
             db.utcnow()),
        )

    # Three history records, all hanging on the id of the SERIES - exactly as
    # they come from an imported history.
    for i, title in enumerate(("Kancelář - S01E05 - Nákup",   # by the numbers
                               "Výlet",                        # by the name
                               "Něco, co v knihovně není")):   # unfileable
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, series_name, item_type, started_at, last_seen_at,"
            " ended_at, watched_seconds) VALUES (?, 'u1', 'Jana', ?, ?,"
            " 'Kancelář', 'Episode', datetime('now','-3 hours'),"
            " datetime('now','-2 hours'), datetime('now','-2 hours'), 1500)",
            (f"s{i}", SERIES_ID, title),
        )

series_from_jellyfin = {
    "Id": SERIES_ID,
    "Name": "Kancelář",
    "Type": "Series",
    "Path": "/media/serialy/Kancelar",
    "ProviderIds": {"Tmdb": "2316"},
}


print("--- a record on the series' id is filed to a specific episode ---")
# The series' name IS in the records already - it used to mean nothing is done.
result = importers.create_from_jellyfin([series_from_jellyfin])
check(result["linked"] == 2,
      f"two of three records were filed ({result})")
check(result["created_count"] == 0, "and nothing was created in the library")

rows = {r["item_name"]: r["item_id"] for r in db.query_all(
    "SELECT item_name, item_id FROM playback")}
check(rows.get("Nákup") == "ep-1-5", f"by the episode number ({rows})")
check(rows.get("Výlet") == "ep-1-6", "by the episode name")
check(rows.get("Něco, co v knihovně není") == SERIES_ID,
      "what cannot be determined stays hanging on the series - we must not guess")

# Twice in a row must not do anything extra.
again = importers.create_from_jellyfin([series_from_jellyfin])
check(again["linked"] == 0, f"the second time there is nothing to file ({again})")


print()
print("--- tidying the data is one action ---")
result = asyncio.run(importers.tidy_data())
check(result["jellyfin"]["status"] == "error",
      "Jellyfin does not answer in the test (the address is dead on purpose)")
check("unified_names" in result and "duplicates" in result,
      f"and the rest ran without it ({sorted(result)})")
check(isinstance(result["left"], int), "it reports how many records are left")
check(isinstance(result["parts"], list), "and returns sentences for the message")


print()
print("--- the task runs the same ---")
check("tidy" in tasks.TASKS, "the task exists")
task = tasks.TASKS["tidy"]
check(tasks.is_daily(task) and tasks.daily_time(task) == "04:00",
      f"it is daily and has the default time ({tasks.daily_time(task)})")
check(tasks.is_enabled(task), "and it is on by default")
# The order is not random: tidying works with what the synchronisation pulled,
# and the backup should already store tidied data.
times = {k: tasks.daily_time(t) for k, t in tasks.TASKS.items() if tasks.is_daily(t)}
check(times["sync"] < times["tidy"] < times["backup"],
      f"it runs between the synchronisation and the backup ({times})")

result = asyncio.run(task.runner())
check(result["status"] == "ok", f"the task finishes ({result})")
check(result["message"], "and says what it did")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
