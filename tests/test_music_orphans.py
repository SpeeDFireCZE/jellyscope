# -*- coding: utf-8 -*-
r"""A music listen is no orphan record: the tidy and the import do not relink it to a movie.

The final review of music (15. 11. 2026): a track is not in `items` (only movies and episodes are there), so
every listen looked like an orphan history record. The daily tidy (and every import) then relinked a listen
of the track "Interstellar" by name to the movie Interstellar - the listen got lost in the charts under an
unknown artist and counted as a play of the movie. Guarded:

A) the tidy leaves a track's listen on the track, even when it is called like a movie (also a row without a
   kind whose id is a known track);
B) the count and the analysis of orphan records in Settings do not count listens;
C) looking up orphans in Jellyfin does not ask about tracks;
D) a really orphaned record of a movie is still relinked by name as before.

Run:
    .venv/bin/python tests/test_music_orphans.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "music-orphans.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _package import substitute  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.importers import orphans  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


WHEN = "2026-11-10 20:00:00"
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for item_id, name in (("film-interstellar", "Interstellar"), ("film-duna", "Duna")):
        conn.execute("INSERT INTO items (id, name, type, library_id) VALUES (?, ?, 'Movie', 'filmy')", (item_id, name))
    conn.execute("INSERT INTO music_track (id, name, artist, runtime_ticks) VALUES ('skladba-interstellar', 'Interstellar',"
                 " 'Hans Zimmer', 2400000000)")
    conn.execute("INSERT INTO music_track (id, name, artist, runtime_ticks) VALUES ('skladba-duna', 'Duna', 'Hans Zimmer', 2400000000)")
    for key, item_id, name, kind in (("poslech", "skladba-interstellar", "Interstellar", "Audio"),
                                       ("poslech-bez-druhu", "skladba-duna", "Duna", None),
                                       ("stary-film", "smazany-film-duna", "Duna", "Movie")):
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                     " watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, 'u1', 'Jana', ?, ?, ?, ?, ?, 240, 0, 0, 0)",
                     (key, item_id, name, kind, WHEN, WHEN))
    conn.commit()

print("--- B) the count and the analysis of orphans ---")
check(orphans.orphan_playback_count() == 1 and orphans.orphan_items_count() == 1,
      f"only the old movie is an orphan, the listen is not ({orphans.orphan_playback_count()}, {orphans.orphan_items_count()})")
analysis = [r["item_id"] for r in orphans.analyse_orphans()]
check(analysis == ["smazany-film-duna"], f"the analysis of orphans without the listen ({analysis})")

print()
print("--- C) looking up in Jellyfin ---")
asked: list[list[str]] = []


class _Client:
    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> None:
        return None

    async def items_by_ids(self, ids):
        asked.append(list(ids))
        return []


restore = substitute(orphans, "JellyfinClient", _Client)
try:
    asyncio.run(orphans.look_up_orphans_in_jellyfin())
finally:
    restore()
check(asked == [["smazany-film-duna"]], f"Jellyfin is not asked about tracks ({asked})")

print()
print("--- A) + D) the tidy ---")
orphans.relink_orphans()
rows = {r["session_key"]: r["item_id"] for r in db.query_all("SELECT session_key, item_id FROM playback")}
check(rows["poslech"] == "skladba-interstellar" and rows["poslech-bez-druhu"] == "skladba-duna",
      f"the listen stayed on the track, although it is called like a movie ({rows})")
check(rows["stary-film"] == "film-duna", f"a really orphaned movie was relinked by name as before ({rows['stary-film']})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
