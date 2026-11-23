# -*- coding: utf-8 -*-
r"""Radarr's movies and their counterparts in the library (links from Radarr's cards).

Radarr knows a movie by its own id and name; the history Jellyscope fetches has nothing by which it
could be found in the library. So during the sync Jellyscope also fetches Radarr's list of movies (the
table `radarr_title`) and pairs **by the TMDB id, not by name** - Jellyfin has the name in the
language of the metadata ("Matrix" against "The Matrix"). Guarded:

A) the list of movies is stored (ids from the app as text, a broken record is skipped); a movie has no
   TVDB id and no id from Jellyfin, and Jellyfin is asked nothing during the sync;
C) a failed list of movies does not bring the history sync down; the list is fetched only when
   needed (the first time, an unknown movie in the history, after a day, after an upgrade) and is
   parsed outside the loop;
D) details: a movie leads to /item/<id> by its TMDB id - only when it really is in the library;
E) deleting the downloaded data deletes the movies too;
F) ids from digits outside ASCII.

Radarr does not run: its answers are recorded (tests/radarr_responses/).

Run:
    .venv/bin/python tests/test_radarr_titles.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "radarr-titles.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    RADARR_RESPONSES, no_network, recorded_response, fill_radarr, connect_radarr, radarr_client, radarr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import db, stats  # noqa: E402
from jellyscope.arr import radarr  # noqa: E402

failures = 0
db.init_db()
no_network()      # no app other than Radarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def make_stale() -> None:
    """The list of movies is "old" (fetched long ago) - the next sync fetches it again."""
    db.set_setting("arr_radarr_titles", "2026-01-01 00:00:00")
    db.forget_settings()


def titles() -> dict[int, dict]:
    return {r["title_id"]: dict(r) for r in db.query_all("SELECT * FROM radarr_title ORDER BY title_id")}


MOVIES = recorded_response(RADARR_RESPONSES, "movie")       # Big Buck Bunny (1), Sintel (2), Tears of Steel (3), Elephants Dream (4), …
requests: list[str] = []
# The handler can be swapped at run time (an error of the list, another list): the client asks the one currently in the dict.
handlers = {"radarr": radarr_handler(requests)}
substitute(radarr, "RadarrClient", radarr_client(lambda request: handlers["radarr"](request)))

print("--- A) the list of movies is stored during the sync ---")
connect_radarr()
fill_radarr()
movies = titles()
check(len(movies) == len(MOVIES) == 9, f"every movie of Radarr ({len(movies)})")
check((movies[2]["name"], movies[2]["tmdb_id"], movies[2]["imdb_id"]) == ("Sintel", "45745", MOVIES[1].get("imdbId") or ""),
      f"a movie: the name and the ids as text ({movies[2]})")
check(not {"tvdb_id", "jellyfin_id", "app"} & set(movies[2]),
      "a movie has no TVDB id and no id from Jellyfin is stored with movies (they pair only when reading) - the table has no such columns")
check(requests.count("movie") == 1, f"one request for the list of movies ({requests.count('movie')})")
check(not hasattr(radarr.titles, "JellyfinClient") and not hasattr(radarr, "JellyfinClient"),
      "Radarr does not ask Jellyfin during the sync (it has no Jellyfin client at all)")
rows = radarr.title_rows([{"id": 5, "title": "A", "tmdbId": 603, "imdbId": "tt0133093"}, {"id": "x", "title": "B"}, "nesmysl",
                          {"title": "bez id"}, {"id": 6, "title": None, "tmdbId": "abc", "imdbId": "<script>"}])
check([(r["title_id"], r["name"], r["tmdb_id"], r["imdb_id"]) for r in rows] == [(5, "A", "603", "tt0133093"), (6, "", "", "")]
      and set(rows[0]) == {"title_id", "name", "tmdb_id", "imdb_id", "added_at"},
      f"a broken record is skipped, an id that is no id is not stored ({rows})")

print()
print("--- C) a failed list of movies ---")
handlers["radarr"] = radarr_handler(requests, instead={"movie": httpx.Response(500, text="error")})
make_stale()
before = requests.count("movie")
result = asyncio.run(radarr.synchronize())
check("new_count" in result and radarr.sync_state()["result"] == "ok" and len(titles()) == len(MOVIES)
      and requests.count("movie") == before + 1,
      "a failed list of movies does not bring the history sync down and leaves the stored movies")
asyncio.run(radarr.synchronize())
check(requests.count("movie") == before + 2, "after a failed fetch the list is tried right at the next sync")
handlers["radarr"] = radarr_handler(requests, instead={"movie": httpx.Response(200, json=MOVIES[:1])})
asyncio.run(radarr.synchronize())
check(len(titles()) == len(MOVIES), "a movie Radarr no longer has stays in the table (the history still points at it)")
handlers["radarr"] = radarr_handler(requests)
make_stale()
asyncio.run(radarr.synchronize())

print()
print("--- C2) when the list is fetched, where it is parsed, an unexpected error ---")
# Radarr cannot page the list of movies (tens of MB with a big library). So it is fetched only when
# needed: the first time, when the history brought a movie Jellyscope does not know, and otherwise once a day.
before = requests.count("movie")
asyncio.run(radarr.synchronize())
asyncio.run(radarr.synchronize())
check(requests.count("movie") == before, f"further syncs the same day do not fetch the list ({requests.count('movie') - before}×)")
with db.connect() as conn:      # the history brings a movie Jellyscope does not know (a new movie in Radarr)
    conn.execute("DELETE FROM radarr_title WHERE title_id = 2")
    conn.commit()
asyncio.run(radarr.synchronize())
check(requests.count("movie") == before + 1 and 2 in titles(), "an unknown movie in the history: the list is fetched at once")
make_stale()
asyncio.run(radarr.synchronize())
check(requests.count("movie") == before + 2, "after a day the list is refreshed (corrected ids, movies without history)")

# Parsing the list must not run in the asyncio loop (the whole web would stand for that time) and of
# every movie only a few fields are kept, not the whole record with the cast and the images.
big = [{"id": 1, "title": "A", "tmdbId": 5, "imdbId": "tt0000001", "overview": "x" * 500,
        "images": [{"coverType": "poster", "url": "/a.jpg", "id": 9}], "movieFile": {"id": 7, "mediaInfo": {"audioCodec": "AAC"}},
        "alternateTitles": [{"title": "B", "id": 3}]}, "nesmysl", {"id": 2, "title": "C", "tmdbId": 6}]
where_parsed: list[bool] = []
original_loads = json.loads


def _loads(data, *a, **k):
    if isinstance(data, (bytes, bytearray)) and b'"overview"' in data:
        where_parsed.append(threading.current_thread() is threading.main_thread())
    return original_loads(data, *a, **k)


async def _fetch_big() -> list:
    async with radarr.RadarrClient("http://radarr.test", "key") as c:
        return await c.titles()


handlers["radarr"] = lambda request: httpx.Response(200, json=big)
json.loads = _loads
try:
    fetched = asyncio.run(_fetch_big())
finally:
    json.loads = original_loads
    handlers["radarr"] = radarr_handler(requests)
check(fetched == [{"id": 1, "title": "A", "tmdbId": 5, "imdbId": "tt0000001"}, {"id": 2, "title": "C", "tmdbId": 6}],
      f"only a few fields of every movie stay from the list of movies ({fetched})")
check(where_parsed == [False], f"and it is parsed outside the asyncio loop, in a thread ({where_parsed})")


# An unexpected error in the step with titles (anything other than Radarr's error) must not bring the
# history sync down: the mark would not move and the state would report an error for ever.
async def _fails(*_a: object, **_k: object) -> int:
    raise RuntimeError("something unexpected")


restore_titles = substitute(radarr, "sync_titles", _fails)
mark_before = db.get_setting("arr_radarr_last_sync", "")
try:
    result = asyncio.run(radarr.synchronize())
except Exception as error:      # noqa: BLE001
    result = {"vyjimka": repr(error)}
restore_titles()
db.forget_settings()
check("new_count" in result and radarr.sync_state()["result"] == "ok"
      and db.get_setting("arr_radarr_last_sync", "") >= mark_before and len(titles()) == len(MOVIES),
      f"an unexpected error with the titles: the history is fetched, the state is ok, the movies stay ({result})")
check("arr_radarr_titles" in db.UNLOGGED_SETTINGS and "arr_radarr_titles_version" in db.UNLOGGED_SETTINGS,
      "the mark of the list's fetch does not flood the settings log")
# When the movie arrived in Radarr (`added`): by it the card "How soon after the release" tells what was waited for.
added = {i: r["added_at"] for i, r in titles().items()}
check(len(added) == len(MOVIES) and all(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", p or "") for p in added.values())
      and added[1] == MOVIES[0]["added"].replace("T", " ").replace("Z", ""),
      f"the list of movies carries the time it was added to Radarr too, in UTC ({added[1]})")
odd = radarr.title_rows([{"id": 1, "title": "A", "added": "0001-01-01T00:00:00Z"}, {"id": 2, "title": "B", "added": "nesmysl"},
                         {"id": 3, "title": "C"}, {"id": 4, "title": "D", "added": "2024-05-06T07:08:09Z"}])
check([r["added_at"] for r in odd] == [None, None, None, "2024-05-06 07:08:09"],
      f"an added time that is no time (not even the default year 1 from .NET) is unknown ({[r['added_at'] for r in odd]})")
try:
    with_offset = [r["added_at"] for r in radarr.title_rows([{"id": 1, "title": "A", "added": "0001-01-01T00:00:00+01:00"},
                                                            {"id": 2, "title": "B", "added": "0002-03-04T00:00:00Z"},
                                                            {"id": 3, "title": "C", "added": "9999-12-31T23:59:59-01:00"}])]
except Exception as exception:      # noqa: BLE001 - exactly what is guarded
    with_offset = repr(exception)
check(with_offset == [None, None, None], f"a default date with a time offset (out of range) does not bring the list of movies down ({with_offset})")
# After an upgrade the list of movies is fetched (the mark is fresh), but without the added time: it is fetched once again.
with db.connect() as conn:
    conn.execute("UPDATE radarr_title SET added_at = NULL")
    conn.commit()
db.set_setting("arr_radarr_titles_version", "")
db.forget_settings()
before = requests.count("movie")
asyncio.run(radarr.synchronize())
db.forget_settings()
after_first = requests.count("movie")
asyncio.run(radarr.synchronize())
check(after_first == before + 1 and requests.count("movie") == after_first and titles()[1]["added_at"] == added[1]
      and db.get_setting("arr_radarr_titles_version", "") != "",
      f"a list fetched by an older version is fetched once again, because of the added time ({before} -> {after_first} -> {requests.count('movie')})")

print()
print("--- D) stats.radarr_detail_links: where a link leads ---")
check(stats.radarr_detail_links() == {} and stats.radarr_library() == {}, "an empty library: no movie has a link")
now = db.utcnow()
with db.connect() as conn:
    # The movie Sintel (TMDB 45745) is in the library under a Czech name; Big Buck Bunny (10378) twice,
    # once as an item that disappeared from Jellyfin; The Matrix (603) only as a disappeared one.
    for item_id, name, tmdb, missing in (("f-sintel", "Sintel (česky)", "45745", 0), ("f-bbb-old", "Big Buck Bunny", "10378", 1),
                                         ("f-bbb", "Big Buck Bunny", "10378", 0), ("f-matrix", "Matrix", "603", 1),
                                         ("f-other", "Other movie", "", 0)):
        conn.execute("INSERT INTO items (id, name, type, tmdb_id, is_missing, synced_at) VALUES (?, ?, 'Movie', ?, ?, ?)",
                     (item_id, name, tmdb, missing, now))
    # An episode with the same TMDB id as a movie has no say: a movie is looked for only among movies.
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, series_id, is_missing, synced_at) VALUES"
                 " ('d-sintel', 'Pilot', 'Episode', '45745', 'serial-x', 0, ?)", (now,))
    conn.commit()
check(stats.radarr_detail_links() == {2: "/item/f-sintel", 1: "/item/f-bbb"},
      f"a movie: by its TMDB id, with another name too; an item that disappeared from Jellyfin is not taken ({stats.radarr_detail_links()})")
check(stats.radarr_library([2])[2]["poster_id"] == "f-sintel", "the poster is the movie's poster")
# The card asks only for the movies it draws - not for Radarr's whole library (thousands of movies would
# mean thousands of ids in a query on every page load).
in_queries: list[tuple[str, list]] = []
original_in = db.query_all_in


def _listen_in(sql, values, *a, **k):
    values = list(values)
    in_queries.append((sql, values))
    return original_in(sql, values, *a, **k)


db.query_all_in = _listen_in
try:
    only_sintel = stats.radarr_detail_links([2])
    nothing = (stats.radarr_detail_links([]), stats.radarr_detail_links([5, 999]), stats.radarr_detail_links([None]))
finally:
    db.query_all_in = original_in
check(only_sintel == {2: "/item/f-sintel"} and nothing == ({}, {}, {}), f"with a list of ids only the given movies ({only_sintel}, {nothing})")
into_library = [v for sql, v in in_queries if "FROM items" in sql and v]
check(["45745"] in into_library and all(len(v) <= 2 for v in into_library), f"and it asks the library only about them ({into_library})")
largest = stats.radarr_largest(365)
check(bool(largest) and all("title_id" in r and "episode" not in r for r in largest) and {r["title_id"] for r in largest} <= set(movies),
      "the largest files carry the movie's id in Radarr (and no episode)")
upgraded = stats.radarr_upgrades(365)["titles"]
check(bool(upgraded) and upgraded[0]["title_id"] in movies and upgraded[0]["title"] == movies[upgraded[0]["title_id"]]["name"],
      f"upgraded titles too ({upgraded[:1]})")
state = asyncio.run(radarr.live_state())
check(state["queue"]["with_error"][0]["title_id"] == 4 and {k["title_id"] for k in state["calendar"]} == {1, 2, 3, 4},
      "Radarr's live queue and calendar: the movie's id")

print()
print("--- E) deleting the downloaded data ---")
check(len(titles()) == len(MOVIES) and radarr.delete_data() > 0 and titles() == {}
      and (db.forget_settings() or True) and db.get_setting("arr_radarr_titles", "") == "" and db.get_setting("arr_radarr_titles_version", "") == "",
      "deleting Radarr's data deletes its movies and the mark of the list's fetch")

print()
print("--- F) digits outside ASCII ---")
# A security pass (13. 11. 2026): str.isdigit() lets "²" or Arabic digits through too. int("²") then
# fails with ValueError - one such id in the list of movies brought the whole step down.
try:
    odd_id: object = radarr.title_rows([{"id": 1, "title": "Odd", "tmdbId": "²"}, {"id": 2, "title": "Good", "tmdbId": "603"}])
except ValueError as error:
    odd_id = f"exception {error}"
check(isinstance(odd_id, list) and [r["tmdb_id"] for r in odd_id] == ["", "603"],
      f"an id from digits outside ASCII is stored empty and the list of movies does not fail ({odd_id})")
try:
    source: object = radarr.sync._source("²")
except ValueError as error:
    source = f"exception {error}"
check(source == "²", f"an event's source from a digit outside ASCII stays text and the history conversion does not fail ({source})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
