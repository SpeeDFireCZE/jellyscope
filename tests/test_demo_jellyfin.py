# -*- coding: utf-8 -*-
r"""The demo's library as catalogs, and the made-up Jellyfin Sonarr's sync pairs with (demo_world, demo_jellyfin).

The demo's made-up apps answer from the demo's library: Sonarr its series, Radarr its movies, Lidarr its
albums, Readarr its books, Seerr requests of its viewers. demo_world reads them from the library's tables as
catalogs, every title with a number an app knows it by (Sonarr's seriesId, Radarr's movie id...) - the same at
every seed, because it comes from the title's readable key, not from the order the rows were written in.

Sonarr's sync pairs its series with Jellyfin's by their TVDB and TMDB ids (arr/sonarr/titles.py) and the demo
is to go through that real code: the seed connects the made-up Jellyfin (demo_apps.seed_apps), which answers
the paged list of series from the same catalog and the server's info, a sign-in through Jellyfin with 401 (a
wrong name or password: the sign-in page says so, nothing about Jellyfin in the log) - and 404 to everything else.

The test runs in the demo mode with every socket call refused: nothing of it may go to the network.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 3)

Run:
    .venv/bin/python tests/test_demo_jellyfin.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
# An address from .env the seed is to replace with the made-up Jellyfin's (db.seed_from_env stores it first).
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "test-key-demo-jellyfin"
os.environ["JELLYSCOPE_DEMO"] = "1"

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_apps, demo_switch, demo_world, demodata  # noqa: E402
from jellyscope.arr import sonarr  # noqa: E402
from jellyscope.jellyfin import DEMO_NAME, JellyfinClient, JellyfinError  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Every way out of the process is recorded and refused - but the test's own database on PostgreSQL (tests/_network.py).
NETWORK = refuse_network()

HEX = re.compile(r"[0-9a-f]{32}")
DIGITS = re.compile(r"[1-9][0-9]*")
FIELDS = {
    "series": {"number", "title", "jellyfin_id", "tmdb_id", "tvdb_id", "year", "seasons"},
    "movies": {"number", "title", "jellyfin_id", "tmdb_id", "year"},
    "albums": {"number", "artist", "title", "jellyfin_id", "year"},
    "books": {"number", "author", "title", "jellyfin_id", "audiobook"},
    "viewers": {"jellyfin_id", "name", "is_admin"},
}


def catalogs() -> dict[str, list[dict]]:
    return {"series": demo_world.series(), "movies": demo_world.movies(), "albums": demo_world.albums(),
            "books": demo_world.books(), "viewers": demo_world.viewers()}


def numbers(found: dict[str, list[dict]]) -> dict[str, dict[str, int]]:
    """Every catalog's {jellyfin id: number}, the episodes' too - to compare two seeds."""
    by = {kind: {entry["jellyfin_id"]: entry["number"] for entry in entries}
          for kind, entries in found.items() if kind != "viewers"}
    by["episodes"] = {episode["jellyfin_id"]: episode["number"] for entry in found["series"]
                      for episodes in entry["seasons"].values() for episode in episodes}
    return by


def request(method: str, path: str, **params) -> httpx.Response:
    """One request straight to the made-up Jellyfin's handler (at the address the seed stores)."""
    from jellyscope import demo_jellyfin

    return demo_jellyfin.handler(httpx.Request(method, f"http://jellyfin.demo{path}", params=params or None))


async def _all_series(page_size: int) -> list[dict]:
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        return [series async for series in client.iter_items(page_size=page_size, item_types="Series")]


async def _users() -> list:
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        return await client.users()


async def _system_info() -> dict:
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        return await client.system_info()


async def _verify(name: str, password: str):
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        return await client.verify_login(name, password)


# What the log gets from INFO up (the application's log file).
RECORDS: list[logging.LogRecord] = []


class _Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        RECORDS.append(record)


logging.getLogger().addHandler(_Collect(level=logging.INFO))
logging.getLogger("jellyscope").setLevel(logging.INFO)


db.init_db()
demodata.prepare(quiet=True)
demo_apps.load_fakes()
found = catalogs()

print("--- the catalogs of the demo's library ---")
check(len(found["series"]) >= 5 and len(found["movies"]) >= 15 and len(found["albums"]) >= 10 and len(found["books"]) >= 10,
      "at least 5 series, 15 movies, 10 albums and 10 books "
      f"({len(found['series'])}, {len(found['movies'])}, {len(found['albums'])}, {len(found['books'])})")
for kind, entries in found.items():
    check(all(FIELDS[kind] <= set(entry) for entry in entries), f"{kind}: every entry has {sorted(FIELDS[kind])}")
    check(all(HEX.fullmatch(entry["jellyfin_id"]) for entry in entries), f"{kind}: Jellyfin's ids in its shape")
    if kind != "viewers":
        own = [entry["number"] for entry in entries]
        check(all(isinstance(n, int) and n > 0 for n in own) and len(set(own)) == len(own) and own == sorted(own),
              f"{kind}: every number a positive whole number of its own, the catalog sorted by it ({own})")

print()
print("--- series: the TV library's episodes by series and season ---")
episodes = db.query_all("SELECT id, name, series_id, series_name, parent_index_number AS season, index_number AS episode,"
                        " production_year, tmdb_id FROM items WHERE type = 'Episode'")
listed = {episode["jellyfin_id"]: (entry, season, episode) for entry in found["series"]
          for season, season_episodes in entry["seasons"].items() for episode in season_episodes}
check(len(listed) == len(episodes) and all(row["id"] in listed for row in episodes),
      f"every episode of the library is in its series once ({len(listed)} of {len(episodes)})")
check(all(listed[row["id"]][0]["jellyfin_id"] == row["series_id"] and listed[row["id"]][0]["title"] == row["series_name"]
          and listed[row["id"]][1] == row["season"] == listed[row["id"]][2]["season"]
          and listed[row["id"]][2]["episode"] == row["episode"] and listed[row["id"]][2]["title"] == row["name"]
          for row in episodes),
      "an episode under its series and season, with its number in the season and its name")
episode_numbers = [episode["number"] for _, _, episode in listed.values()]
check(all(isinstance(n, int) and n > 0 for n in episode_numbers) and len(set(episode_numbers)) == len(episode_numbers),
      "every episode has a number of its own across all series (Sonarr's episodeId)")
check(all(entry["tmdb_id"] == {str(row["tmdb_id"]) for row in episodes if row["series_id"] == entry["jellyfin_id"]}.pop()
          and DIGITS.fullmatch(entry["tvdb_id"]) and entry["tvdb_id"] != entry["tmdb_id"] for entry in found["series"]),
      "a series' TMDB id is its episodes' one, its TVDB id digits of its own")
check(all(entry["year"] == min(row["production_year"] for row in episodes if row["series_id"] == entry["jellyfin_id"])
          for entry in found["series"]), "a series' year is that of its first episode")
first = found["series"][0]
check((first["number"], first["title"], first["jellyfin_id"], first["tvdb_id"])
      == (1, "Chernobyl", demo_world.jellyfin_id("demo-series-0"), demo_world.tvdb_id("demo-series-0")),
      f"the number comes from the key: Chernobyl (demo-series-0) is series 1 ({first['number']}, {first['title']})")

print()
print("--- movies, albums, books, viewers ---")
movies = {r["id"]: r for r in db.query_all("SELECT id, name, production_year, tmdb_id FROM items WHERE type = 'Movie'")}
check(sorted(entry["jellyfin_id"] for entry in found["movies"]) == sorted(movies),
      f"every movie of the library ({len(found['movies'])} of {len(movies)})")
check(all((entry["title"], entry["year"], entry["tmdb_id"]) == (movies[entry["jellyfin_id"]]["name"],
          movies[entry["jellyfin_id"]]["production_year"], str(movies[entry["jellyfin_id"]]["tmdb_id"]))
          for entry in found["movies"]), "a movie with its name, year and TMDB id")
check((found["movies"][0]["number"], found["movies"][0]["title"]) == (1, "Duna"), "Duna (demo-movie-0) is movie 1")
albums = {r["id"]: r for r in db.query_all("SELECT id, name, artist, year FROM music_album")}
check(sorted(entry["jellyfin_id"] for entry in found["albums"]) == sorted(albums),
      f"every album of the library ({len(found['albums'])} of {len(albums)})")
check(all((entry["title"], entry["artist"], entry["year"]) == (albums[entry["jellyfin_id"]]["name"],
          albums[entry["jellyfin_id"]]["artist"], albums[entry["jellyfin_id"]]["year"]) for entry in found["albums"]),
      "an album with its name, artist and year")
rows = db.query_all("SELECT id, kind, title, author FROM book")
works = {(r["title"], r["author"]) for r in rows}
check(sorted((entry["title"], entry["author"]) for entry in found["books"]) == sorted(works),
      f"every book once, an audiobook in parts too ({len(found['books'])} books, {len(rows)} rows)")
by_work = {(entry["title"], entry["author"]): entry for entry in found["books"]}
check(all(by_work[(r["title"], r["author"])]["audiobook"] is (r["kind"] == "audiobook") for r in rows),
      "an audiobook is told from an e-book")
parts = sorted(r["id"] for r in rows if r["title"] == "Cesta na jih")
check(len(parts) == 4 and by_work[("Cesta na jih", "Karel Vypravec")]["jellyfin_id"] == parts[0],
      "an audiobook in four parts: its id is the first part's, the one Readarr's pairing takes (the first by id)")
check((found["books"][0]["number"], found["books"][0]["title"]) == (1, "Pisek a vitr"), "the first book is number 1")
users = {r["id"]: r for r in db.query_all("SELECT id, name, is_administrator FROM users")}
check(sorted(entry["jellyfin_id"] for entry in found["viewers"]) == sorted(users) and len(users) == 11,
      f"every viewer ({len(found['viewers'])})")
check(all(entry["name"] == users[entry["jellyfin_id"]]["name"] and entry["is_admin"] is bool(users[entry["jellyfin_id"]]["is_administrator"])
          for entry in found["viewers"]) and [e["name"] for e in found["viewers"] if e["is_admin"]] == ["Petr"],
      "a viewer with the name and whether an administrator (Petr)")
moment = demo_world.now()
check(moment.tzinfo is not None and moment.utcoffset() == timedelta(0) and abs(moment - datetime.now(UTC)) < timedelta(seconds=5),
      f"now() is the present in UTC ({moment})")

print()
print("--- the made-up Jellyfin: Sonarr's pairing through its real code ---")
check(demo_switch.registered(DEMO_NAME), "load_fakes registers the made-up Jellyfin")
check(db.jellyfin_connection() == (demo_apps.ADDRESS.format(key="jellyfin"), demo_apps.api_key("jellyfin")),
      f"the seed connects it in place of the address from .env ({db.jellyfin_connection()[0]})")
# A demo without JELLYFIN_URL in .env stores no address - and Jellyfin's client cannot even ask without one.
db.set_setting("jellyfin_url", "")
db.set_setting("jellyfin_api_key", "")
demo_apps.seed_apps()
check(db.jellyfin_connection() == (demo_apps.ADDRESS.format(key="jellyfin"), demo_apps.api_key("jellyfin")),
      "and where none is stored")
try:
    by = asyncio.run(sonarr.titles._jellyfin_series())
except Exception as error:          # noqa: BLE001 - the check below says what went wrong
    print(f"       {error!r}")
    by = None
check(by is not None and by["Tvdb"] == {entry["tvdb_id"]: entry["jellyfin_id"] for entry in found["series"]},
      f"Sonarr's Jellyfin series: every series' TVDB id leads to its Jellyfin id ({len((by or {}).get('Tvdb', {}))})")
check(by is not None and by["Tmdb"] == {entry["tmdb_id"]: entry["jellyfin_id"] for entry in found["series"]} and not by["Imdb"],
      "and its TMDB id too; no IMDb id")
page = request("GET", "/Items", IncludeItemTypes="Series", StartIndex=1, Limit=2, Recursive="true")
body = page.json() if page.status_code == 200 else {}
check(page.status_code == 200 and [item["Id"] for item in body.get("Items", [])] == [e["jellyfin_id"] for e in found["series"][1:3]]
      and body.get("TotalRecordCount") == len(found["series"]) and body.get("StartIndex") == 1,
      f"a page of series: StartIndex and Limit, TotalRecordCount of all ({page.status_code}, {body.get('TotalRecordCount')})")
item = body.get("Items", [{}])[0]
check(item.get("Type") == "Series" and item.get("Name") == found["series"][1]["title"]
      and item.get("ProviderIds") == {"Tvdb": found["series"][1]["tvdb_id"], "Tmdb": found["series"][1]["tmdb_id"]},
      f"a series of the page: its type, name and ids ({item})")
paged = asyncio.run(_all_series(2))
check([s["Id"] for s in paged] == [e["jellyfin_id"] for e in found["series"]],
      f"the client pages through all of them, two at a time ({len(paged)})")
info = asyncio.run(_system_info())
public = request("GET", "/System/Info/Public")
check(str(info.get("Version", "")).startswith("10.11.") and public.status_code == 200
      and str(public.json().get("Version", "")).startswith("10.11."),
      f"the server's info and its public part: Jellyfin 10.11 ({info.get('Version')})")
others = {f"{method} {path} {params}": request(method, path, **params).status_code for method, path, params in (
    ("GET", "/Items", {"IncludeItemTypes": "Movie,Episode"}), ("GET", "/Items", {}),
    ("GET", "/Items", {"IncludeItemTypes": "MusicAlbum"}), ("GET", "/Users", {}), ("GET", "/Sessions", {}),
    ("GET", "/Library/VirtualFolders", {}), ("GET", f"/Users/{found['viewers'][0]['jellyfin_id']}/Items", {"IncludeItemTypes": "Series"}),
    ("POST", "/Items", {"IncludeItemTypes": "Series"}))}
check(set(others.values()) == {404}, f"everything else is 404 - a library sync gets no empty library ({others})")
try:
    asyncio.run(_users())
    refused = False
except JellyfinError as error:
    refused = "404" in str(error)
check(refused, "Jellyfin's client gets 404 as its error, not an answer")
check(request("POST", "/Users/AuthenticateByName").status_code == 401,
      "a sign-in through Jellyfin gets 401, Jellyfin's answer to a wrong name or password")
try:
    verified = asyncio.run(_verify(found["viewers"][0]["name"], "a password"))
except JellyfinError as error:
    verified = error
check(verified is None, f"Jellyfin's client: the name and the password do not match, no error ({verified!r})")
# The sign-in page with sign-in through Jellyfin on (a visitor cannot switch it on: the demo saves nothing).
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, web  # noqa: E402

db.set_setting(access.LOGIN_KEY, "1")
first = len(RECORDS)
page = TestClient(web.app).post("/login", data={"username": found["viewers"][0]["name"], "password": "a password",
                                                "method": "jellyfin"}, follow_redirects=False)
about_jellyfin = [r.getMessage() for r in RECORDS[first:] if r.levelno >= logging.WARNING and "Jellyfin" in r.getMessage()]
check(page.status_code == 401 and "Wrong username or password." in page.text and not about_jellyfin,
      f"the sign-in page says the name or the password is wrong, the log nothing about Jellyfin ({page.status_code},"
      f" {about_jellyfin[:2]})")
db.set_setting(access.LOGIN_KEY, "0")

print()
print("--- the numbers hold after a refresh ---")
before = numbers(found)
db.set_setting(demodata.SEED_KEY, (datetime.now(UTC) - timedelta(days=2)).strftime(db.TIME_FORMAT))
counts = demodata.prepare(quiet=True)
again = catalogs()
check(counts["items"] > 0 and numbers(again) == before, "the second prepare() made the data again with the same numbers")
by_again = asyncio.run(sonarr.titles._jellyfin_series())
check(by_again == by, "and the pairing gives the same")
duna = demo_world.jellyfin_id("demo-movie-0")
with db.connect() as conn:
    conn.execute("DELETE FROM items WHERE id = ?", (duna,))
    conn.commit()
without = {entry["jellyfin_id"]: entry["number"] for entry in demo_world.movies()}
check(duna not in without and without == {k: v for k, v in before["movies"].items() if k != duna},
      "a title gone keeps nobody else's number (from the key, not the place)")

print()
print("--- a real installation: the seed leaves Jellyfin's connection alone ---")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
db.set_setting("jellyfin_url", "http://192.0.2.7:8096")
demo_switch.clear()
demo_apps.load_fakes()
check(not demo_switch.registered(DEMO_NAME) and demo_apps.seed_apps() == {}
      and db.jellyfin_connection()[0] == "http://192.0.2.7:8096",
      "outside the demo mode no made-up Jellyfin is registered nor connected")
check(not NETWORK, f"nothing touched a socket ({NETWORK[:2]})")

print(f"\nDONE - failures: {failures}")
sys.exit(1 if failures else 0)
