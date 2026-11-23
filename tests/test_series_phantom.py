# -*- coding: utf-8 -*-
r"""Only what has a file belongs to the library - a movie and an episode.

The bug this guards: looking up orphaned records in Jellyfin
(`importers.create_from_jellyfin`) created an item out of anything Jellyfin
returned. When a history record hung on the id of a **series** - and that
happens with imported history - an item of the kind "Series" was created.

But the sync asks Jellyfin only for `Movie,Episode`. So it never saw such an
item and `_mark_missing()` marked it vanished on every run. The library then
held an "archived" series that is in Jellyfin as normal - and next to it the
right one, put together from episodes. The user sees it as "series fall
into the archive by themselves and are detected again".

Movies were not affected: a movie's id in the history returns an item of the
kind "Movie", which the sync knows.

Run:
    .venv/bin/python tests/test_series_phantom.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "fantom.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers, jellyfin, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

SERIES_ID = "6a6152eddb1c02ccb5c3887497e3e64d"
EPISODE_ID = "aa11bb22cc33dd44ee55ff6677889900"


def record(item_id: str, name: str, series: str | None = None) -> None:
    """One history record that leads to nothing in the library."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, series_name, item_type, started_at, last_seen_at,"
            " ended_at, watched_seconds) VALUES (?, 'u1', 'Jana', ?, ?, ?,"
            " 'Episode', datetime('now','-16 days'), datetime('now','-16 days'),"
            " datetime('now','-16 days'), 1800)",
            (f"s-{item_id}", item_id, name, series),
        )


print("--- what we ask Jellyfin for and what we keep in the library must match ---")
# Were these two lists to drift apart, the bug would come back in another
# shape: an item of a kind we do not ask for falls into the archive on every run.
import inspect  # noqa: E402

signature = inspect.signature(jellyfin.JellyfinClient.items_page)
asked_for = str(signature.parameters["item_types"].default)
check(set(asked_for.split(",")) == set(scanner.MANAGED_TYPES),
      f"IncludeItemTypes ({asked_for}) = MANAGED_TYPES {scanner.MANAGED_TYPES}")


print()
print("--- a record hanging on the id of a SERIES ---")
record(SERIES_ID, "Ve službě: Za mřížemi")

series = {
    "Id": SERIES_ID,
    "Name": "Ve službě: Za mřížemi",
    "Type": "Series",
    "Path": "/media/serialy/Ve sluzbe za mrizemi",
    "ProviderIds": {"Tmdb": "123456"},
}
result = importers.create_from_jellyfin([series])

check(db.query_one("SELECT id FROM items WHERE id = ?", (SERIES_ID,)) is None,
      "the series is NOT created in the library")
check(result["created_count"] == 0, f"and is not reported as created ({result})")

# What can be got out of it though: the series' name. With it the episode
# stops looking like a standalone movie in the overviews.
row = db.query_one("SELECT series_name FROM playback WHERE item_id = ?",
                   (SERIES_ID,))
check(row["series_name"] == "Ve službě: Za mřížemi",
      f"the series' name is filled into the record ({row['series_name']!r})")
check(result["filled"] == 1, "and it is reported apart")


print()
print("--- a record hanging on the id of an EPISODE (this must keep working) ---")
record(EPISODE_ID, "7. epizoda")

episode = {
    "Id": EPISODE_ID,
    "Name": "7. epizoda",
    "Type": "Episode",
    "SeriesName": "Ve službě: Za mřížemi",
    "ParentIndexNumber": 1,
    "IndexNumber": 7,
    "Path": "/media/serialy/Ve sluzbe za mrizemi/S01E07.mkv",
    "ProviderIds": {"Tmdb": "123456"},
}
result = importers.create_from_jellyfin([episode])
stored = db.query_one("SELECT id, type FROM items WHERE id = ?", (EPISODE_ID,))
check(stored is not None and stored["type"] == "Episode",
      "the episode is created in the library as it should")
check(result["created_count"] == 1, f"and is reported as created ({result})")


print()
print("--- the sync has nothing to archive any more ---")
# A stamp from the future = "this run saw none of it". That is exactly what
# the full sync does at its end.
scanner._mark_missing("2099-01-01 00:00:00")
archived = db.query_value(
    "SELECT COUNT(*) FROM items WHERE is_missing = 1", default=0)
# The episode will be archived - the sync really did not see it, because this
# test runs none. The point is that the series is not in the table.
check(db.query_one("SELECT id FROM items WHERE id = ?", (SERIES_ID,)) is None,
      f"there is nothing extra to archive in the library (archived: {archived})")


print()
print("--- cleaning up items created before this was fixed ---")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, is_missing, synced_at)"
        " VALUES ('stary-serial', 'Starý fantom', 'Series', 1, '2026-01-01 00:00:00')")
    conn.execute(
        "INSERT INTO items (id, name, type, is_missing, synced_at)"
        " VALUES ('stara-rada', 'Řada 1', 'Season', 1, '2026-01-01 00:00:00')")
    conn.execute(
        "INSERT INTO item_streams (item_id, stream_index, type)"
        " VALUES ('stary-serial', 0, 'Video')")

deleted = scanner.clean_up_phantoms()
check(deleted == 2, f"the cleanup finds and deletes both ({deleted})")
check(db.query_one("SELECT id FROM items WHERE id = 'stary-serial'") is None,
      "the series is gone from the library")
check(db.query_one("SELECT id FROM items WHERE id = 'stara-rada'") is None,
      "the season too")
check(db.query_value("SELECT COUNT(*) FROM item_streams"
                     " WHERE item_id = 'stary-serial'", default=0) == 0,
      "and no streams of it stay in item_streams either")
check(db.query_one("SELECT id FROM items WHERE id = ?", (EPISODE_ID,)) is not None,
      "the cleanup does not touch an honest episode")
check(scanner.clean_up_phantoms() == 0, "the second time it has nothing to do")

# The history stays. It is a valid record of what somebody watched - only
# nothing in the library leads to it, which was true before as well.
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = ?",
                     (SERIES_ID,), default=0) == 1,
      "the history record stayed, only it is orphaned again")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
