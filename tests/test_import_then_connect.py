# -*- coding: utf-8 -*-
"""The import first, connecting Jellyfin only afterwards.

Exactly this order is common: one first moves the history over from Jellystat or Playback Reporting, and
only then sets up the connection to Jellyfin. At the moment of the import Jellyscope therefore **knows not a
single user, not a single title** - question marks stay in the history, no item type, and ItemIds that need
not match anything in the library.

The test guards that after the first library synchronization all of it straightens out by itself:

  * the users' names are filled in from the table `users`,
  * the type, the library and the series' name are taken from `items`,
  * the history switches to the title with the same tmdb ID (the file was transcoded meanwhile and Jellyfin
    gave it a new ItemId),
  * even when one source writes the identifiers with dashes and Jellyfin without them.
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
# Its own home: without it DATABASE_PATH would override the database chosen in data/database.json and the
# test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "connect.db")

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def record(key: str) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            """SELECT user_id, user_name, item_id, item_type, library_id, series_name
                 FROM playback WHERE session_key LIKE ?""",
            (f"%{key}%",),
        ).fetchone()
    return dict(row) if row else {}


db.init_db()

# The identifiers in two shapes on purpose. Jellyfin sends them without dashes in its API, Jellystat and the
# Playback Reporting plugin commonly store them with dashes - it is the same number, only written differently.
USER_JELLYFIN = "aa11bb22cc33dd44ee55ff6600112233"
USER_IMPORT = "aa11bb22-cc33-dd44-ee55-ff6600112233"

MOVIE_OLD = "0011223344556677889900aabbccddee"    # the id before transcoding
MOVIE_NEW = "ffeeddccbbaa00998877665544332211"    # the id Jellyfin has now
EPISODE = "1122334455667788990011223344aabb"

backup = {
    "jf_playback_activity": [
        {"Id": 1, "ActivityDateInserted": "2026-07-01 20:00:00",
         "PlaybackDuration": "3600", "UserId": USER_IMPORT,
         "UserName": "Karel", "NowPlayingItemId": MOVIE_OLD,
         "NowPlayingItemName": "Matrix"},
        {"Id": 2, "ActivityDateInserted": "2026-07-02 21:00:00",
         "PlaybackDuration": "1800", "UserId": USER_IMPORT,
         "NowPlayingItemId": EPISODE, "NowPlayingItemName": "Kancelář",
         "EpisodeId": EPISODE, "SeriesName": "Kancelář"},
    ]
}


print("--- the import without a connected Jellyfin ---")
result = asyncio.run(
    importers.import_jellystat_json(json.dumps(backup).encode("utf-8")))
check(result.get("status") == "ok", f"the import ran: {result.get('status')}")
check(result.get("imported") == 2, f"2 records were taken over ({result.get('imported')})")

movie = record(":1:")
# The backup carries the name with the first record, not with the second. Where the source does not send it,
# a question mark must stay - we will not make it up.
check(movie.get("user_name") == "Karel", "the name the backup carried was used")
check(record(":2:").get("user_name") == "?", "elsewhere there is a question mark for now")
check(movie.get("library_id") is None, "the library is not known yet")
check(importers._orphan_item_ids() != [], "the history's items lead nowhere yet")


print()
print("--- now Jellyfin connects: users, the library, titles ---")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
        " VALUES (?,?,0,0,?)",
        (USER_JELLYFIN, "Karel", db.utcnow()),
    )
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, synced_at) VALUES (?,?,?,?)",
        ("lib-filmy", "Filmy", "movies", db.utcnow()),
    )
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, synced_at) VALUES (?,?,?,?)",
        ("lib-serialy", "Seriály", "tvshows", db.utcnow()),
    )
    # After transcoding the movie has a new ItemId but the same tmdb ID.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, tmdb_id, is_missing, synced_at)
           VALUES (?,?,?,?,?,0,?)""",
        (MOVIE_NEW, "Matrix", "Movie", "lib-filmy", "603", db.utcnow()),
    )
    # The episode kept its id - only Jellyfin writes it without dashes.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_name, is_missing, synced_at)
           VALUES (?,?,?,?,?,0,?)""",
        (EPISODE, "Kancelář", "Episode", "lib-serialy", "Kancelář", db.utcnow()),
    )

# scanner.sync_library() calls this right after downloading the library. Jellyfin is not configured here, so
# the step "ask about the old ItemIds" fails and the match by name is used - exactly as with an unreachable
# server.
summary = asyncio.run(importers.link_imported_history())
check(isinstance(summary, dict), f"the straightening ran: {summary}")


print()
print("--- the users paired despite another way of writing the id ---")
for key, label in ((":1:", "film"), (":2:", "epizoda")):
    entry = record(key)
    check(entry.get("user_id") == USER_JELLYFIN,
          f"{label}: user_id is in Jellyfin's shape")
    check(entry.get("user_name") == "Karel", f"{label}: the user's name was filled in")


print()
print("--- the history found the titles in the library ---")
movie = record(":1:")
check(movie.get("item_id") == MOVIE_NEW,
      "the movie switched to the new ItemId (a match of names)")
check(movie.get("item_type") == "Movie", "the type Movie was filled in")
check(movie.get("library_id") == "lib-filmy", "the library was filled in")

episode = record(":2:")
check(episode.get("item_id") == EPISODE, "the episode points at the right episode")
check(episode.get("item_type") == "Episode", "the type Episode was filled in")
check(episode.get("series_name") == "Kancelář", "the series' name was filled in")
check(importers._orphan_item_ids() == [], "no record hangs in the air any more")


print()
print("--- pairing by tmdb when Jellyfin still knows the old id ---")
# The second way: the server answers that the old ItemId had tmdb 603. The answer's shape is the same as from
# the API.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('import:jst:9:x', ?, ?, 'Matrix', ?, ?, 900, 0)""",
        (USER_IMPORT, "jeste-starsi-id", db.utcnow(), db.utcnow()),
    )
items, rows = importers._link_by_tmdb(
    [{"Id": "jeste-starsi-id", "ProviderIds": {"Tmdb": "603"}}])
check(items == 1 and rows == 1, "tmdb paired the record with the title in the library")
check(record(":9:").get("item_id") == MOVIE_NEW, "the history points at the title from the library")


print()
print("--- a repeated run changes nothing any more ---")
# The record from the previous part is fresh, so the first pass still has something to do. Only the second
# one interests us: the straightening must be idempotent, otherwise every library synchronization would
# rewrite the whole history needlessly.
importers.refresh_playback_metadata()

changes = importers._unify_ids()
check(changes == {"user_id": 0, "item_id": 0}, f"nothing to unify: {changes}")
changes = importers.refresh_playback_metadata()
check(all(v == 0 for v in changes.values()), f"nothing to fill in: {changes}")


print()
print("--- an unknown user is assigned to nobody ---")
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('import:jst:99:x', 'ffffffff-0000-0000-0000-000000000000', '?', ?, ?, ?, 60, 0)""",
        (MOVIE_NEW, db.utcnow(), db.utcnow()),
    )
importers.refresh_playback_metadata()
stranger = record(":99:")
check(stranger.get("user_id") == "ffffffff-0000-0000-0000-000000000000",
      "the foreign id stayed unchanged")
check(stranger.get("user_name") == "?", "rather a question mark than a made-up name")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
