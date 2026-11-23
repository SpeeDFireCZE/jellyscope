# -*- coding: utf-8 -*-
r"""Sonarr's series and their counterparts in the library (links from Sonarr's cards).

Sonarr knows a series by its own id and name; the history Jellyscope fetches has nothing to find it by
in the library. So the sync also fetches Sonarr's list of series (the table `sonarr_title`) and pairs
**by id, not by name** - Jellyfin has the name in the language of the metadata. Guarded:

A) the list of series is stored (the ids from the app as a text, a broken record is skipped);
B) a series pairs with Jellyfin through the series' Tvdb, else Tmdb, else Imdb id; the id from
   Jellyfin is stored without dashes;
C) an unavailable Jellyfin does not delete an earlier pairing; a failed list of series does not bring
   the history sync down;
D) details: a series leads to /series/<series id> - only when it really is in the library;
E) deleting the downloaded data deletes the series too;
F) ids of digits outside ASCII.

Sonarr does not run: the answers are recorded (tests/sonarr_responses/), Jellyfin is a faked list of
series (tests/_apps.py).

Run:
    .venv/bin/python tests/test_sonarr_titles.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sonarr-titles.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    SONARR_RESPONSES, no_network, fake_jellyfin_series, recorded_response, fill_sonarr, connect_sonarr, sonarr_client, sonarr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import db, stats  # noqa: E402
from jellyscope.arr import sonarr  # noqa: E402
from jellyscope.jellyfin import JellyfinError  # noqa: E402

failures = 0
db.init_db()
no_network()      # no app other than Sonarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def make_stale() -> None:
    """The list of series is "old" (fetched long ago) - the next sync fetches it again."""
    db.set_setting("arr_sonarr_titles", "2026-01-01 00:00:00")
    db.forget_settings()


def titles() -> dict[int, dict]:
    return {r["title_id"]: dict(r) for r in db.query_all("SELECT * FROM sonarr_title ORDER BY title_id")}


SERIES = recorded_response(SONARR_RESPONSES, "series")          # Game of Thrones (1), Chernobyl (2), Dark (3), The Expanse (4)
GOT = "feca7a7c96c254bb6ed6a3c87574ce63"
CHERNOBYL = "287bce17ef706956df43daa0e47c24a7"
DARK = "a427aea771c45a7d71be634b62b36e34"


def with_dashes(bare: str) -> str:
    return f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}"


# Jellyfin: GoT is known by Tvdb, Chernobyl has only Tmdb, Dark only Imdb (and an id with dashes),
# The Expanse is not in Jellyfin at all; plus a series Sonarr does not know.
jellyfin_series = [
    {"Id": GOT, "ProviderIds": {"Tvdb": "121361", "Tmdb": "1399", "Imdb": "tt0944947"}},
    {"Id": CHERNOBYL, "ProviderIds": {"Tmdb": "87108"}},
    {"Id": with_dashes(DARK), "ProviderIds": {"Imdb": "tt5753856"}},
    {"Id": "0" * 32, "ProviderIds": {"Tvdb": "999"}},
    {"Id": "1" * 32},                                    # a series without provider ids (unidentified)
    "nesmysl",
]
requests: list[str] = []
jellyfin_calls: list[str] = []
# The handler can be swapped at run time (an error of the list, another list): the client asks the one
# currently in the dict.
handlers = {"sonarr": sonarr_handler(requests)}
substitute(sonarr, "SonarrClient", sonarr_client(lambda request: handlers["sonarr"](request)))
restore_jellyfin = substitute(sonarr, "JellyfinClient", fake_jellyfin_series(jellyfin_series, jellyfin_calls))

print("--- A) the list of series is stored on a sync ---")
connect_sonarr()
fill_sonarr()
series = titles()
check(len(series) == len(SERIES) == 4, f"every series of Sonarr ({len(series)})")
check(series[1]["name"] == "Game of Thrones" and (series[1]["tvdb_id"], series[1]["tmdb_id"], series[1]["imdb_id"]) == ("121361", "1399", "tt0944947"),
      f"a series: the name and the ids as a text ({series[1]})")
check(requests.count("series") == 1 and jellyfin_calls == ["Series"],
      f"one request for the list of series and one for Jellyfin's series ({jellyfin_calls})")
# A Jellyfin that accepts the connection and does not answer must not hold the *Arr sync for nine
# minutes (the default limit 180 s × 3 tries): the list of series is a small request, a short limit is enough.
from jellyscope.jellyfin import QUICK_TIMEOUT  # noqa: E402

check(sonarr.titles.JellyfinClient.last_timeout is QUICK_TIMEOUT,
      f"Jellyfin is asked for series with a short timeout ({sonarr.titles.JellyfinClient.last_timeout})")
rows = sonarr.title_rows([{"id": 5, "title": "A", "tmdbId": 603, "tvdbId": 121361, "imdbId": "tt0133093"}, {"id": "x", "title": "B"},
                          "nesmysl", {"title": "bez id"}, {"id": 6, "title": None, "tmdbId": "abc", "tvdbId": 0, "imdbId": "<script>"}])
check([(r["title_id"], r["name"], r["tmdb_id"], r["tvdb_id"], r["imdb_id"]) for r in rows]
      == [(5, "A", "603", "121361", "tt0133093"), (6, "", "", "", "")] and "app" not in rows[0],
      f"a broken record is skipped, an id that is no id is not stored ({rows})")

print()
print("--- B) a series pairs through the ids from Jellyfin ---")
check(series[1]["jellyfin_id"] == GOT, "Game of Thrones: by the Tvdb id")
check(series[2]["jellyfin_id"] == CHERNOBYL, "Chernobyl: Jellyfin has only the Tmdb id")
check(series[3]["jellyfin_id"] == DARK, f"Dark: only the Imdb id; the id from Jellyfin stored without dashes ({series[3]['jellyfin_id']})")
check(series[4]["jellyfin_id"] == "", "The Expanse: not in Jellyfin, stays unpaired")

print()
print("--- C) an unavailable Jellyfin, a failed list of titles ---")
restore_jellyfin()
restore_jellyfin = substitute(sonarr, "JellyfinClient", fake_jellyfin_series([], error=JellyfinError("Nepodarilo se spojit s Jellyfinem")))
result = asyncio.run(sonarr.synchronize())
check(titles()[1]["jellyfin_id"] == GOT and "new_count" in result, "an unavailable Jellyfin: the sync goes through and the earlier pairing stays")
restore_jellyfin()
restore_jellyfin = substitute(sonarr, "JellyfinClient", fake_jellyfin_series([{"Id": GOT, "ProviderIds": {"Tvdb": "121361"}}]))
asyncio.run(sonarr.synchronize())
after = titles()
check(after[1]["jellyfin_id"] == GOT and after[2]["jellyfin_id"] == "", "a series that left Jellyfin loses its pairing")
handlers["sonarr"] = sonarr_handler(requests, instead={"series": httpx.Response(500, text="error")})
make_stale()
before = requests.count("series")
result = asyncio.run(sonarr.synchronize())
check("new_count" in result and sonarr.sync_state()["result"] == "ok" and len(titles()) == len(SERIES)
      and requests.count("series") == before + 1,
      "a failed list of titles does not bring the history sync down and leaves the stored titles")
asyncio.run(sonarr.synchronize())
check(requests.count("series") == before + 2, "after a failed fetch the list is tried at the very next sync")
handlers["sonarr"] = sonarr_handler(requests, instead={"series": httpx.Response(200, json=SERIES[:1])})
asyncio.run(sonarr.synchronize())
check(len(titles()) == len(SERIES), "a title the app no longer has stays in the table (the history still points at it)")
handlers["sonarr"] = sonarr_handler(requests)
restore_jellyfin()
restore_jellyfin = substitute(sonarr, "JellyfinClient", fake_jellyfin_series(jellyfin_series))
make_stale()
asyncio.run(sonarr.synchronize())

print()
print("--- C2) findings of the final review: when the list is fetched, where it is parsed, an unexpected error ---")
# The app cannot page the list of titles (tens of MB with a big library). So it is fetched only when
# needed: the first time, when the history brought a title Jellyscope does not know, and otherwise once a day.
before = requests.count("series")
asyncio.run(sonarr.synchronize())
asyncio.run(sonarr.synchronize())
check(requests.count("series") == before, f"further syncs the same day do not fetch the list ({requests.count('series') - before}×)")
check(titles()[3]["jellyfin_id"] == DARK, "series still pair with Jellyfin on every sync")
with db.connect() as conn:      # the history brings a title Jellyscope does not know (a new series in Sonarr)
    conn.execute("DELETE FROM sonarr_title WHERE title_id = 2")
    conn.commit()
asyncio.run(sonarr.synchronize())
check(requests.count("series") == before + 1 and 2 in titles(), "an unknown title in the history: the list is fetched at once")
make_stale()
asyncio.run(sonarr.synchronize())
check(requests.count("series") == before + 2, "after a day the list is refreshed (corrected ids, titles without a history)")

# Parsing the list must not run in the asyncio loop (the whole web would stand for that time) and only a
# few fields of every title are kept, not the whole record with the cast and images.
import json  # noqa: E402
import threading  # noqa: E402

big = [{"id": 1, "title": "A", "tmdbId": 5, "tvdbId": 7, "imdbId": "tt0000001", "overview": "x" * 500,
        "images": [{"coverType": "poster", "url": "/a.jpg", "id": 9}], "seasons": [{"seasonNumber": 1, "statistics": {"episodeCount": 10}}],
        "alternateTitles": [{"title": "B", "id": 3}]}, "nesmysl", {"id": 2, "title": "C", "tmdbId": 6}]
where_parsed: list[bool] = []
original_loads = json.loads


def _loads(data, *a, **k):
    if isinstance(data, (bytes, bytearray)) and b'"overview"' in data:
        where_parsed.append(threading.current_thread() is threading.main_thread())
    return original_loads(data, *a, **k)


async def _fetch_big() -> list:
    # The package's client is faked in this test (it talks to `handlers`), so the answer is given through them.
    async with sonarr.SonarrClient("http://sonarr.test", "key") as client:
        return await client.titles()


handlers["sonarr"] = lambda request: httpx.Response(200, json=big)
json.loads = _loads
try:
    fetched = asyncio.run(_fetch_big())
finally:
    json.loads = original_loads
    handlers["sonarr"] = sonarr_handler(requests)
check(fetched == [{"id": 1, "title": "A", "tmdbId": 5, "tvdbId": 7, "imdbId": "tt0000001"}, {"id": 2, "title": "C", "tmdbId": 6}],
      f"only a few fields of every series stay from the list of series ({fetched})")
check(where_parsed == [False], f"and it is parsed outside the asyncio loop, in a thread ({where_parsed})")

# An unexpected error in the step with titles (anything other than an error of the app or Jellyfin)
# must not bring the history sync down: the mark would not move and the state would report an error for ever.
restore_jellyfin()
restore_jellyfin = substitute(sonarr, "JellyfinClient", fake_jellyfin_series([], error=RuntimeError("něco nečekaného")))
mark_before = db.get_setting("arr_sonarr_last_sync", "")
try:
    result = asyncio.run(sonarr.synchronize())
except Exception as error:      # noqa: BLE001
    result = {"vyjimka": repr(error)}
db.forget_settings()
check("new_count" in result and sonarr.sync_state()["result"] == "ok"
      and db.get_setting("arr_sonarr_last_sync", "") >= mark_before and titles()[1]["jellyfin_id"] == GOT,
      f"an unexpected error with titles: the history is fetched, the state is ok, the pairing stays ({result})")
restore_jellyfin()
restore_jellyfin = substitute(sonarr, "JellyfinClient", fake_jellyfin_series(jellyfin_series))
asyncio.run(sonarr.synchronize())
check("arr_sonarr_titles" in db.UNLOGGED_SETTINGS, "the mark of the list's fetch does not flood the settings log")
# When the title arrived in the app (`added`): by it the card "Jak brzy po vydání" (how soon after
# release) tells what was waited for.
import re  # noqa: E402

added = {i: r["added_at"] for i, r in titles().items()}
check(len(added) == 4 and all(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", a or "") for a in added.values())
      and added[1] == SERIES[0]["added"].replace("T", " ").replace("Z", ""),
      f"the list of titles carries the time added to the app too, in UTC ({added})")
odd = sonarr.title_rows([{"id": 1, "title": "A", "added": "0001-01-01T00:00:00Z"}, {"id": 2, "title": "B", "added": "nesmysl"},
                         {"id": 3, "title": "C"}, {"id": 4, "title": "D", "added": "2024-05-06T07:08:09Z"}])
check([r["added_at"] for r in odd] == [None, None, None, "2024-05-06 07:08:09"],
      f"an added time that is no time (not even the default year 1 from .NET) is unknown ({[r['added_at'] for r in odd]})")
try:
    with_offset = [r["added_at"] for r in sonarr.title_rows([{"id": 1, "title": "A", "added": "0001-01-01T00:00:00+01:00"},
                                                            {"id": 2, "title": "B", "added": "0002-03-04T00:00:00Z"},
                                                            {"id": 3, "title": "C", "added": "9999-12-31T23:59:59-01:00"}])]
except Exception as exception:      # noqa: BLE001 - exactly what is guarded
    with_offset = repr(exception)
check(with_offset == [None, None, None], f"a default date with an offset (out of range) does not bring the list of titles down ({with_offset})")
# After an upgrade the list of titles is fetched (the mark is fresh), but without the added time: it is fetched once again.
with db.connect() as conn:
    conn.execute("UPDATE sonarr_title SET added_at = NULL")
    conn.commit()
db.set_setting("arr_sonarr_titles_version", "")
db.forget_settings()
before = requests.count("series")
asyncio.run(sonarr.synchronize())
db.forget_settings()
after_first = requests.count("series")
asyncio.run(sonarr.synchronize())
check(after_first == before + 1 and requests.count("series") == after_first and titles()[1]["added_at"] == added[1]
      and db.get_setting("arr_sonarr_titles_version", "") != "" and "arr_sonarr_titles_version" in db.UNLOGGED_SETTINGS,
      f"a list fetched by an older version is fetched once again, because of the added time ({before} -> {after_first} -> {requests.count('series')})")

print()
print("--- D) stats.sonarr_detail_links: where a link leads ---")
check(stats.sonarr_detail_links() == {} and stats.sonarr_library() == {}, "an empty library: no series has a link")
now = db.utcnow()
with db.connect() as conn:
    # Series: GoT has an id with dashes in the library, Chernobyl without; Dark has only an episode that left Jellyfin.
    for item_id, series_id, name, missing in (("d-got", with_dashes(GOT), "Hra o trůny", 0), ("d-cher", CHERNOBYL, "Černobyl", 0),
                                              ("d-dark", DARK, "Dark", 1)):
        conn.execute("INSERT INTO items (id, name, type, series_id, series_name, is_missing, synced_at) VALUES (?, 'Pilot', 'Episode', ?, ?, ?, ?)",
                     (item_id, series_id, name, missing, now))
    # A movie with the same name as a series has no say in it: a series is looked up through its episodes.
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, is_missing, synced_at) VALUES ('f-got', 'Game of Thrones', 'Movie', '1399', 0, ?)", (now,))
    conn.commit()
check(stats.sonarr_detail_links() == {1: f"/series/{with_dashes(GOT)}", 2: f"/series/{CHERNOBYL}"},
      f"a series: /series/<id as the library has it>; not a series with only episodes that left ({stats.sonarr_detail_links()})")
check(stats.sonarr_library([1])[1]["poster_id"] == with_dashes(GOT), "the poster is the series' poster")
# The card asks only about the series it draws - not about Sonarr's whole library (thousands of series
# would mean thousands of ids in the query on every page load).
in_queries: list[tuple[str, list]] = []
original_in = db.query_all_in


def _eavesdrop_in(sql, values, *a, **k):
    values = list(values)
    in_queries.append((sql, values))
    return original_in(sql, values, *a, **k)


db.query_all_in = _eavesdrop_in
try:
    only_got = stats.sonarr_detail_links([1, 4])
    nothing = (stats.sonarr_detail_links([]), stats.sonarr_detail_links([999]), stats.sonarr_detail_links([None]))
finally:
    db.query_all_in = original_in
check(only_got == {1: f"/series/{with_dashes(GOT)}"} and nothing == ({}, {}, {}),
      f"with a list of ids only the given series ({only_got}, {nothing})")
to_library = [v for sql, v in in_queries if "FROM items" in sql and v]
check(bool(to_library) and all(len(v) <= 2 for v in to_library), f"and only they are asked about in the library ({to_library})")
largest = stats.sonarr_largest(365)
check(bool(largest) and all("title_id" in r for r in largest) and {r["title_id"] for r in largest} <= {1, 2},
      "the largest files carry the series' id in Sonarr")
upgraded = stats.sonarr_upgrades(365)["titles"]
check(bool(upgraded) and upgraded[0]["title"] == "Game of Thrones" and upgraded[0]["title_id"] == 1,
      f"upgraded titles too ({upgraded[:1]})")
state = asyncio.run(sonarr.live_state())
check(state["queue"]["with_error"][0]["title_id"] == 1 and {k["title_id"] for k in state["calendar"]} == {1}, "Sonarr's live queue and calendar: the series' id")

print()
print("--- E) deleting the downloaded data ---")
before_deletion = len(titles())
check(before_deletion == len(SERIES) and sonarr.delete_data() > 0 and titles() == {}
      and (db.forget_settings() or True) and db.get_setting("arr_sonarr_titles", "") == "" and db.get_setting("arr_sonarr_titles_version", "") == "",
      "deleting Sonarr's data deletes its series and the list's fetch mark")

print()
print("--- F) digits outside ASCII ---")
# A security pass (13. 11. 2026): str.isdigit() lets "²" or Arabic digits through too. int("²") then
# fails with ValueError - one such id in the list of titles brought the whole step down.
try:
    odd_ids: object = sonarr.title_rows([{"id": 1, "title": "Divný", "tmdbId": "²", "tvdbId": "٣"},
                                         {"id": 2, "title": "Dobrý", "tmdbId": "603"}])
except ValueError as error:
    odd_ids = f"exception {error}"
check(isinstance(odd_ids, list) and [(r["tmdb_id"], r["tvdb_id"]) for r in odd_ids] == [("", ""), ("603", "")],
      f"ids of digits outside ASCII are stored empty and the list of titles does not crash ({odd_ids})")
try:
    source: object = sonarr.sync._source("²")
except ValueError as error:
    source = f"exception {error}"
check(source == "²", f"an event's source of a digit outside ASCII stays a text and the history's conversion does not crash ({source})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
