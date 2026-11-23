# -*- coding: utf-8 -*-
"""An import into an empty library - the state a person is in right after installing.

This test came from a specific bug: after an import from Jellystat into a Jellyfin not connected yet, the watching
chart stayed empty, the users had question marks and the library was empty - although the summary numbers fit.

Run:
    .venv/bin/python tests/test_import_empty.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would override the stored choice of the database in data/database.json
# and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "empty.db")

from jellyscope import db, importers, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

# --- a Jellystat backup as it really looks ---------------------------------
# The table jf_playback_activity has no column with the item's type; a movie is told from an episode by a filled
# SeriesName / EpisodeId.
now = datetime.now(timezone.utc)


def record(index: int, name: str, series: str | None, user: str,
           user_id: str, hours: float, days_ago: int) -> dict:
    return {
        "Id": f"jst-{index}",
        "NowPlayingItemId": f"item-{index}",
        "NowPlayingItemName": name,
        "SeriesName": series,
        "EpisodeId": f"ep-{index}" if series else None,
        "UserId": user_id,
        "UserName": user,
        "Client": "Jellyfin Web",
        "DeviceName": "Chrome",
        "PlayMethod": "DirectPlay",
        "PlaybackDuration": int(hours * 3600),
        "ActivityDateInserted": (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S"),
    }


backup = {"jf_playback_activity": [
    record(1, "Dune", None, "Tomáš", "u-1", 2.0, 1),
    record(2, "Matrix", None, "Tomáš", "u-1", 2.5, 2),
    record(3, "Episode 1", "The Office", "Jana", "u-2", 0.5, 1),
    record(4, "Episode 2", "The Office", "Jana", "u-2", 0.5, 3),
]}

result = asyncio.run(importers.import_jellystat_json(
    json.dumps(backup).encode("utf-8"), min_seconds=60
))
check(result["status"] == "ok", f"the import ran: {result.get('message', '')}")
check(result["imported"] == 4, f"4 records were imported ({result['imported']})")


print()
print("--- the user's name from the backup, not a question mark ---")
names = {row["user_name"] for row in db.query_all("SELECT DISTINCT user_name FROM playback")}
check("?" not in names, f"the history has no question marks: {sorted(names)}")
check(names == {"Tomáš", "Jana"}, f"the names fit: {sorted(names)}")


print()
print("--- the item's type: movie vs episode ---")
types = {row["item_name"]: row["item_type"]
         for row in db.query_all("SELECT item_name, item_type FROM playback")}
check(types.get("Dune") == "Movie", f"Dune is a movie ({types.get('Dune')!r})")
check(types.get("Matrix") == "Movie", f"Matrix is a movie ({types.get('Matrix')!r})")
check(types.get("Episode 1") == "Episode", f"an episode is Episode ({types.get('Episode 1')!r})")
# This was the bug: the title's name was stored in item_type.
check(not any(t in ("Dune", "Matrix", "Episode 1") for t in types.values()),
      f"item_type holds no titles' names: {sorted(set(types.values()))}")


print()
print("--- the watching chart by day ---")
daily = stats.daily_activity_split(30)
total = sum(row["hours"] for row in daily)
check(abs(total - 5.5) < 0.05, f"the hours' sum fits: {total:.2f} (expected 5.5)")

movies = sum(row["movie_hours"] for row in daily)
series = sum(row["series_hours"] for row in daily)
other = sum(row["other_hours"] for row in daily)
check(abs(movies - 4.5) < 0.05, f"movies: {movies:.2f} (expected 4.5)")
check(abs(series - 1.0) < 0.05, f"series: {series:.2f} (expected 1.0)")
check(other == 0, f"nothing fell into 'other': {other:.2f}")
check(abs((movies + series + other) - total) < 0.05,
      "the split adds up to the whole")


print()
print("--- an unknown type does not get lost ---")
# If a record came whose type we do not recognise, it must not vanish from the chart.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, item_id, item_name, item_type,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('x-1', 'mystery', 'Concert', 'MusicVideo', ?, ?, 3600, 0)""",
        (db.utcnow(), db.utcnow()),
    )
daily = stats.daily_activity_split(30)
check(abs(sum(row["other_hours"] for row in daily) - 1.0) < 0.05,
      "the unknown type fell into 'other'")
check(abs(sum(row["hours"] for row in daily) - 6.5) < 0.05,
      f"and counted into the whole: {sum(row['hours'] for row in daily):.2f} (expected 6.5)")


print()
print("--- filling in after Jellyfin is connected ---")
# We simulate that the library was synced only now.
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u-3', 'Petr')")
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, series_name, is_missing, synced_at)"
        " VALUES ('item-9', 'Late movie', 'Movie', 'lib-1', NULL, 0, ?)",
        (db.utcnow(),),
    )
    conn.execute("INSERT INTO libraries (id, name) VALUES ('lib-1', 'Movies')")
    conn.execute(
        """INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,
                                 item_type, started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('late', 'u-3', '?', 'item-9', 'Late movie', 'Late movie', ?, ?, 3600, 0)""",
        (db.utcnow(), db.utcnow()),
    )

changes = importers.refresh_playback_metadata()
check(changes["user_names"] == 1, f"the user's name was filled in ({changes['user_names']})")
check(changes["item_types"] >= 1, f"the item's type was fixed ({changes['item_types']})")
check(changes["libraries"] == 1, f"the library was filled in ({changes['libraries']})")

row = db.query_one("SELECT * FROM playback WHERE session_key = 'late'")
check(row["user_name"] == "Petr", f"the name is Petr ({row['user_name']!r})")
check(row["item_type"] == "Movie", f"the type is Movie ({row['item_type']!r})")
check(row["library_id"] == "lib-1", f"the library is lib-1 ({row['library_id']!r})")

# A repeated run has nothing left to do - and must not overwrite names that fit.
changes = importers.refresh_playback_metadata()
check(changes["user_names"] == 0, "the second pass does not overwrite the names")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
