# -*- coding: utf-8 -*-
r"""For an episode Jellystat sends two different ids - the right one must be taken.

The root of a problem that long showed elsewhere: in a Jellystat backup a record of an episode has

    NowPlayingItemId  = the SERIES' id     ("Tým SEAL")
    EpisodeId         = the EPISODE's id   (what we want)
    NowPlayingItemName = the SERIES' name
    EpisodeName        = the episode's name

The import took the first one. The whole history of a series so ended up on a single identifier nothing in
the library leads to, and was called by the series' name - so **it could not be worked out which episode it
was**. The user saw it as "looking up orphans does nothing": there really was nothing to work from.

The second half of the test guards the repair of data imported already: when the same backup is uploaded
again, the old records are to be **repaired in place**, not doubled. The key
`import:jst:<row id>:<item id>` changes with the repair, so without it a second copy of every play would
come to be.

Run:
    .venv/bin/python tests/test_jellystat_episodes.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "jst.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

SERIES_ID = "b31ca2733b47b9f87736375722ef85a0"
EPISODE_ID = "aa11bb22cc33dd44ee55ff6677889900"

# An episode in the library - so it shows that the record links to something.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, series_id, series_name,"
        " parent_index_number, index_number, is_missing, synced_at)"
        " VALUES (?, 'Nasazení', 'Episode', ?, 'Tým SEAL', 2, 5, 0, ?)",
        (EPISODE_ID, SERIES_ID, db.utcnow()),
    )


def backup(row_id: str, with_episode: bool) -> bytes:
    """One row of a Jellystat backup - with the episode's id, or without it."""
    record = {
        "Id": row_id,
        "UserId": "u1",
        "UserName": "Jana",
        "NowPlayingItemId": SERIES_ID,
        "NowPlayingItemName": "Tým SEAL",
        "SeriesName": "Tým SEAL",
        "ActivityDateInserted": "2026-08-01 20:00:00",
        "PlaybackDuration": "1800",
        "Client": "Jellyfin Web",
        "DeviceName": "Chrome",
    }
    if with_episode:
        record["EpisodeId"] = EPISODE_ID
        record["EpisodeName"] = "Nasazení"
    return json.dumps([record]).encode("utf-8")


print("--- a record with EpisodeId links to the episode, not to the series ---")
result = asyncio.run(importers.import_jellystat_json(backup("r1", True), min_seconds=60))
check(result["status"] == "ok" and result["imported"] == 1,
      f"imported ({result['status']}, {result.get('imported')})")

row = db.query_one("SELECT item_id, item_name, series_name, item_type"
                   " FROM playback WHERE user_id = 'u1'")
check(row["item_id"] == EPISODE_ID,
      f"it hangs on the EPISODE's id, not the series' ({row['item_id']})")
check(row["item_name"] == "Nasazení",
      f"and it is called by the episode's name ({row['item_name']})")
check(row["series_name"] == "Tým SEAL",
      f"the series' name is not lost ({row['series_name']})")
# This is the point: such a record is no orphan any more.
check(importers.orphan_playback_count() == 0, "and it is no orphan")


print()
print("--- an old import (without EpisodeId) ends at the series ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")

asyncio.run(importers.import_jellystat_json(backup("r2", False), min_seconds=60))
row = db.query_one("SELECT item_id, session_key FROM playback WHERE user_id = 'u1'")
check(row["item_id"] == SERIES_ID,
      f"without EpisodeId only the series' id is left ({row['item_id']})")
check(importers.orphan_playback_count() == 1, "such a record is an orphan")
old_key = row["session_key"]


print()
print("--- a repeated import of the same backup REPAIRS, does not double ---")
result = asyncio.run(importers.import_jellystat_json(backup("r2", True), min_seconds=60))
check(result.get("repaired") == 1, f"it reports a repair ({result.get('repaired')})")
check(result["imported"] == 0, "and inserts nothing new")
check(db.query_value("SELECT COUNT(*) FROM playback") == 1,
      f"the history still holds one record "
      f"({db.query_value('SELECT COUNT(*) FROM playback')})")

row = db.query_one("SELECT item_id, item_name, session_key FROM playback")
check(row["item_id"] == EPISODE_ID, f"and it points at the episode ({row['item_id']})")
check(row["session_key"] != old_key, "the key changed with the repair")
check(importers.orphan_playback_count() == 0, "it is no orphan any more")

# The third time there is nothing to do.
result = asyncio.run(importers.import_jellystat_json(backup("r2", True), min_seconds=60))
check(result.get("repaired") == 0 and result["imported"] == 0,
      f"the third time it does nothing ({result.get('repaired')}, {result['imported']})")
check(db.query_value("SELECT COUNT(*) FROM playback") == 1, "and does not double it")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
