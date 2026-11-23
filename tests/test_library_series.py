# -*- coding: utf-8 -*-
"""A series is one row in the library, not a hundred.

In the `items` table every episode is a row of its own — that is how
Jellyfin sends them and how we need them for the statistics. In the library's list it is
useless though: a series of five seasons takes a hundred tiles and everything else
gets lost in them. On top, the pages then go by episodes, so "page 1 of 20" says
nothing about how many titles a person has.

The rows are therefore grouped by series. The breakdown into seasons and episodes is only
in the series' detail, where a person looks for it.

The group's key is `COALESCE(series_id, id)`: a movie has no `series_id`,
so it forms a group by itself and **one query serves both**. Were
it two queries, they would drift apart over time.

Run:
    .venv/bin/python tests/test_library_series.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice of the
# database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "knihovna.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, stats  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at)"
                 " VALUES ('lib', 'Seriály', 'tvshows', ?)", (db.utcnow(),))

    # Two movies.
    for ident, name, size in (("film-1", "Matrix", 4_000_000_000),
                              ("film-2", "Duna", 12_000_000_000)):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, size_bytes,
                                  width, height, video_codec, is_missing, synced_at)
               VALUES (?,?,'Movie','lib',?,1920,1080,'h264',0,?)""",
            (ident, name, size, db.utcnow()),
        )

    # A series: two seasons of three episodes each.
    for season in (1, 2):
        for episode_no in (1, 2, 3):
            conn.execute(
                """INSERT INTO items (id, name, type, library_id, series_id,
                                      series_name, season_name, index_number,
                                      parent_index_number, size_bytes, width, height,
                                      video_codec, is_missing, synced_at)
                   VALUES (?,?,'Episode','lib','serial-1','Kancelář',?,?,?,
                           1000000000,1920,1080,'hevc',0,?)""",
                (f"ep-{season}-{episode_no}", f"{season}x{episode_no:02d} Díl", f"Řada {season}",
                 episode_no, season, db.utcnow()),
            )

    # A few plays, so they add up into the series' row.
    for i, ident in enumerate(("ep-1-1", "ep-1-2", "ep-2-1", "film-1")):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', ?, ?, ?, 1800, 0)""",
            (f"relace-{i}", ident, db.utcnow(), db.utcnow()),
        )


print("--- the library's list ---")
check(db.query_value("SELECT COUNT(*) FROM items") == 8, "the database has 8 items")
check(stats.library_rows_count() == 3,
      f"but the list has three rows: two movies and one series "
      f"({stats.library_rows_count()})")

rows = {r["name"]: r for r in stats.library_rows(50, 0)}
check(set(rows) == {"Matrix", "Duna", "Kancelář"}, f"and they are the right ones: {set(rows)}")

series = rows["Kancelář"]
check(series["is_series"] == 1, "the series is marked as a series")
check(series["id"] == "serial-1", f"and its id is the series' id ({series['id']})")
check(series["episode_count"] == 6, f"six episodes ({series['episode_count']})")
check(series["season_count"] == 2, f"two seasons ({series['season_count']})")
check(series["size_bytes"] == 6_000_000_000,
      f"the size is the sum of the episodes ({series['size_bytes']})")
check(series["plays"] == 3, f"and the plays too ({series['plays']})")

movie = rows["Duna"]
check(movie["is_series"] == 0, "a movie is not a series")
check(movie["episode_count"] == 1, "and it stands by itself")
check(movie["size_bytes"] == 12_000_000_000, "with its own size")


print()
print("--- sorting and paging work with groups ---")
# Duna 12 GB, the series 6x1 GB = 6 GB, Matrix 4 GB. Were the series sorted
# by one episode (1 GB), it would fall to the end - this way it is in the middle.
by_size = [r["name"] for r in stats.library_rows(50, 0, sort="size")]
check(by_size == ["Duna", "Kancelář", "Matrix"],
      f"a series is sorted by the sum of its episodes, not by one ({by_size})")

by_name = [r["name"] for r in stats.library_rows(50, 0, sort="name")]
check(by_name == sorted(by_name), f"by name: {by_name}")

first_page = stats.library_rows(2, 0, sort="name")
second_page = stats.library_rows(2, 2, sort="name")
check(len(first_page) == 2 and len(second_page) == 1, "the pages go by groups")
check(not ({r["id"] for r in first_page} & {r["id"] for r in second_page}),
      "and the pages do not overlap")

check(stats.library_rows_count(search="Kancel") == 1, "the search finds the series")
check(stats.library_rows_count(search="Matr") == 1, "and the movie")


print()
print("--- the series' detail ---")
detail = stats.series_detail("serial-1")
check(detail["name"] == "Kancelář", "the name matches")
check(detail["season_count"] == 2 and detail["episode_count"] == 6, "two seasons, six episodes")
check([s["name"] for s in detail["seasons"]] == ["Řada 1", "Řada 2"],
      f"the seasons are sorted: {[s['name'] for s in detail['seasons']]}")
check([e["index_number"] for e in detail["seasons"][0]["episodes"]] == [1, 2, 3],
      "the episodes inside a season too")
check(detail["seasons"][0]["plays"] == 2, "the plays add up by seasons")
check(stats.series_detail("neexistuje") == {}, "an unknown series returns emptiness")


print()
print("--- through the interface ---")
client = TestClient(app)
client.post("/login", data={"username": "admin", "password": "longpassword"})

html = client.get("/library/lib?tab=media").text
tiles = re.findall(r'<a class="media-card[^"]*"\s+href="([^"]+)"', html)
check(len(tiles) == 3, f"the page has three tiles ({len(tiles)})")
check("/series/serial-1" in tiles, "the series leads to its detail")
check("/item/film-1" in tiles and "/item/film-2" in tiles, "the movies to the item's detail")
check(not any(k.startswith("/item/ep-") for k in tiles),
      "single episodes are not in the list")

response = client.get("/series/serial-1")
check(response.status_code == 200, "the series' detail opens")
check(response.text.count("<details") == 2, "it has two seasons that unfold")
check(response.text.count('href="/item/ep-') == 6, "and six episodes with click-throughs")
check("Kancelář" in response.text, "with the series' name")

check(client.get("/series/neexistuje").status_code == 404, "an unknown series is 404")

# From an episode one can go back to the series.
episode_detail = client.get("/item/ep-1-1").text
check('href="/series/serial-1"' in episode_detail, "an episode has a link back to the series")


print()
print("--- missing technical data are worked out right from here ---")
# The message by the library used to only point to Settings and the analysis had to be
# run for the whole library. The button runs only the missing files
# and only in this library.
check(stats.library_overview("lib")["without_tech"] == 8,
      "the starting state: no file has technical data")

db.set_setting("tech_source", "jellyfin")
overview = client.get("/library/lib").text
check('action="/settings/scan"' not in overview,
      "with the Jellyfin source the button is not offered - the data are taken at the sync")
check("Spusť analýzu v Nastavení" in overview, "the original sentence stays")

db.set_setting("tech_source", "ffprobe")
overview = client.get("/library/lib").text
check('action="/settings/scan"' in overview, "with ffprobe the button is on the page")
check('name="library_id" value="lib"' in overview, "and carries this library's id")
check('name="mode" value="missing"' in overview, "it runs only the missing files")

# The redirect back to the library. We leave the source on Jellyfin, so the
# analysis does not really start in the test - we care only where it returns to.
db.set_setting("tech_source", "jellyfin")
response = client.post("/settings/scan", data={"mode": "missing", "library_id": "lib"},
                       follow_redirects=False)
check(response.headers["location"].startswith("/library/lib"),
      f"after starting it returns to the library ({response.headers['location']})")

# A foreign value from the form must not get into the redirect's address.
response = client.post("/settings/scan",
                       data={"mode": "missing", "library_id": "https://cizi.example"},
                       follow_redirects=False)
check(response.headers["location"] == "/settings?section=tasks",
      f"an unknown library leads back to Settings ({response.headers['location']})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
