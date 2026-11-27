# -*- coding: utf-8 -*-
r"""The demo's ids have the shape the real services give them (demo_world).

The demo's made-up *Arr apps and Seerr go through the real pairing code. That code compares Jellyfin's
ids without dashes (jellyfin.id_without_dashes) and takes a TMDB or TVDB id only when it is digits
(jellyfin.digits_only) - the demo's old ids ("demo-movie-3" became "demomovie3", "demo-tmdb-3" was no
number) paired with nothing. So the demo writes ids of 32 lower-case hex characters, as Jellyfin's API
sends them, and numeric TMDB ids, all made from readable keys ("demo-u1", "demo-movie-3") the same way
every time: a refresh or another machine gives the same ids, so links into the demo hold.

The made-up apps tell one story of the flaky indexer (demo_world.FLAKY_INDEXER): its outages are demo_world's
schedule, and at every moment Sonarr's and Radarr's health warn about it exactly while Prowlarr leaves it out
(indexerstatus, health) - outside an outage they show only an update; Lidarr and Readarr never name it.

Run:
    .venv/bin/python tests/test_demo_world.py
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "test-key-demo-world"
os.environ["JELLYSCOPE_DEMO"] = "1"

from jellyscope import config, db, demodata, languages  # noqa: E402
from jellyscope.jellyfin import digits_only, id_without_dashes  # noqa: E402

try:
    from jellyscope import demo_world
except ImportError:                     # before the module exists: the shape checks below still say what is wrong
    demo_world = None

config.load_config(reload=True)

HEX = re.compile(r"[0-9a-f]{32}")
# The columns that hold an id Jellyfin gives: a title's, a series', a viewer's, a library's, music's and books'.
ID_COLUMNS = {
    "items": ("id", "series_id", "library_id"),
    "item_streams": ("item_id",),
    "users": ("id",),
    "libraries": ("id",),
    "music_artist": ("id",),
    "music_album": ("id", "artist_id", "library_id"),
    "music_track": ("id", "album_id", "artist_id", "library_id"),
    "book": ("id", "library_id"),
    "playback": ("user_id", "item_id", "library_id"),
}
IDS_MARK = "IDS "


def stored_ids() -> dict[str, list[str]]:
    """Every stored id by table and column, and the titles' TMDB ids - sorted, to compare two seeds."""
    found = {f"{table}.{column}": sorted({str(r["v"]) for r in db.query_all(
                 f"SELECT {column} AS v FROM {table} WHERE {column} IS NOT NULL")})
             for table, columns in ID_COLUMNS.items() for column in columns}
    found["items.tmdb_id"] = sorted(f"{r['id']}={r['tmdb_id']}" for r in db.query_all("SELECT id, tmdb_id FROM items"))
    return found


db.init_db()
demodata.prepare(quiet=True)

if "--ids" in sys.argv:                 # the second, independent seed (another process, another database)
    print(IDS_MARK + json.dumps(stored_ids()))
    sys.exit(0)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def one(sql: str, params: tuple = ()) -> dict:
    return db.query_one(sql, params) or {}


print("--- every stored id has Jellyfin's shape ---")
first = stored_ids()
for column, values in first.items():
    if column == "items.tmdb_id":
        continue
    wrong = [v for v in values if not HEX.fullmatch(v) or id_without_dashes(v) != v]
    check(bool(values) and not wrong, f"{column}: {len(values)} ids of 32 lower-case hex characters ({wrong[:3]})")

titles = set(first["items.id"]) | set(first["music_track.id"]) | set(first["book.id"])
check(set(first["playback.item_id"]) <= titles, "every playback points at a title, a track or a book of the library")
check(set(first["playback.user_id"]) <= set(first["users.id"]), "every playback points at a viewer")
check(set(first["items.library_id"]) | set(first["music_track.library_id"]) | set(first["book.library_id"])
      <= set(first["libraries.id"]), "every title, track and book is in a library of the demo")
check(set(first["music_track.album_id"]) <= set(first["music_album.id"])
      and set(first["music_album.artist_id"]) <= set(first["music_artist.id"]), "every track has its album, every album its artist")

print()
print("--- TMDB ids are numbers: a movie its own, an episode its series' (as Jellyfin gives them) ---")
rows = db.query_all("SELECT id, type, series_id, tmdb_id FROM items")
check(all(digits_only(str(r["tmdb_id"] or "")) for r in rows), "every title has a TMDB id of digits")
series_tmdb: dict[str, set[str]] = {}
for r in rows:
    if r["type"] == "Episode":
        series_tmdb.setdefault(str(r["series_id"]), set()).add(str(r["tmdb_id"]))
check(series_tmdb and all(len(ids) == 1 for ids in series_tmdb.values()),
      f"all episodes of a series carry one TMDB id ({len(series_tmdb)} series)")
movie_tmdb = [str(r["tmdb_id"]) for r in rows if r["type"] == "Movie"]
every_tmdb = movie_tmdb + [next(iter(ids)) for ids in series_tmdb.values()]
check(len(set(every_tmdb)) == len(every_tmdb), "and no two movies or series share one")

print()
print("--- the readable keys ---")
check(demo_world is not None, "jellyscope.demo_world exists")
if demo_world is not None:
    jid = demo_world.jellyfin_id
    check(HEX.fullmatch(jid("demo-u1") or "") is not None and jid("demo-u1") == jid("demo-u1") != jid("demo-u2"),
          "jellyfin_id: 32 hex characters, the same for one key, another for another key")
    keys = [f"demo-movie-{n}" for n in range(300)]
    for name, make in (("tmdb_id", demo_world.tmdb_id), ("tvdb_id", demo_world.tvdb_id)):
        numbers = [make(key) for key in keys]
        check(all(digits_only(n) and str(int(n)) == n and int(n) > 0 for n in numbers),
              f"{name}: a positive number without a leading zero ({numbers[:3]})")
        check(len(set(numbers)) == len(keys) and numbers == [make(key) for key in keys],
              f"{name}: the same for one key, another for each of {len(keys)} keys")
    check(one("SELECT name FROM users WHERE id = ?", (jid("demo-u1"),)).get("name") == "Petr", "demo-u1 is Petr")
    check(one("SELECT name FROM libraries WHERE id = ?", (jid("demo-tv"),)).get("name") == "Serialy"
          and one("SELECT name FROM libraries WHERE id = ?", (jid("demo-music"),)).get("name") == "Hudba",
          "demo-tv and demo-music are the libraries Serialy and Hudba")
    duna = one("SELECT name, tmdb_id FROM items WHERE id = ?", (jid("demo-movie-0"),))
    check(duna.get("name") == "Duna" and duna.get("tmdb_id") == demo_world.tmdb_id("demo-movie-0"),
          f"demo-movie-0 is Duna with the TMDB id of its key ({duna})")
    episode = one("SELECT series_id, series_name, tmdb_id FROM items WHERE id = ?", (jid("demo-ep-0-1"),))
    check(episode.get("series_id") == jid("demo-series-0") and episode.get("series_name") == "Chernobyl"
          and episode.get("tmdb_id") == demo_world.tmdb_id("demo-series-0"),
          f"demo-ep-0-1 is an episode of demo-series-0 (Chernobyl) with the series' TMDB id ({episode})")
    check(one("SELECT name FROM music_artist WHERE id = ?", (jid("demo-int-1"),)).get("name") == "Severní světla"
          and one("SELECT artist_id FROM music_album WHERE id = ?", (jid("demo-alb-1"),)).get("artist_id") == jid("demo-int-1")
          and one("SELECT album_id FROM music_track WHERE id = ?", (jid("demo-alb-1-1"),)).get("album_id") == jid("demo-alb-1"),
          "demo-int-1, its album demo-alb-1 and its first track demo-alb-1-1")
    check(one("SELECT title FROM book WHERE id = ?", (jid("demo-kn-1"),)).get("title") == "Pisek a vitr"
          and one("SELECT title FROM book WHERE id = ?", (jid("demo-ab-1-1"),)).get("title") == "Cesta na jih",
          "demo-kn-1 is an e-book, demo-ab-1-1 the first part of an audiobook")
    live = one("SELECT user_id, item_id, library_id FROM playback WHERE session_key = ?", (demodata.LIVE_KEY,))
    check(live == {"user_id": jid("demo-u2"), "item_id": jid("demo-movie-0"), "library_id": jid("demo-movies")},
          f"the made-up stream: Jana watches Duna ({live})")
named = one("SELECT audio_languages FROM items WHERE path LIKE '%Sedmikrasky.1966%'")
check(named and named.get("audio_languages") not in (None, "", languages.UNKNOWN) and "cs" in str(named["audio_languages"]),
      f"the title with its language only in the file's name got it from the name ({named})")

print()
print("--- two seeds give the same ids ---")
demodata.refresh()
check(stored_ids() == first, "a refresh (the data made again) keeps every id")
child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--ids"], capture_output=True, text=True,
                       env={**os.environ, "PYTHONHASHSEED": "1"}, timeout=300)
lines = [line for line in child.stdout.splitlines() if line.startswith(IDS_MARK)]
other = json.loads(lines[0][len(IDS_MARK):]) if lines else {}
check(child.returncode == 0 and other == first,
      f"a seed in another process and another database gives the same ids ({child.returncode}, {child.stderr[-300:]!r})")

print()
print("--- the demo's pages open with them ---")
from _apps import login  # noqa: E402

demo = login(demodata.DEMO_USERNAME, demodata.DEMO_PASSWORD)
movie = demo_world.jellyfin_id("demo-movie-0") if demo_world else "demo-movie-0"
for path in ("/video/overview", "/library", "/history", "/users", f"/item/{movie}"):
    response = demo.get(path)
    leftover = re.findall(r'href="[^"]*demo-[^"]*"', response.text)
    check(response.status_code == 200 and not leftover, f"{path}: {response.status_code}, no link with an old id ({leftover[:2]})")
check("Duna" in demo.get(f"/item/{movie}").text, "the movie's detail shows the movie")

print()
print("--- the flaky indexer's outages: one story in every made-up app ---")
MADE_UP = {name: importlib.import_module(f"jellyscope.arr.{name}.demo") for name in ("sonarr", "radarr", "lidarr", "readarr", "prowlarr")}
UNAVAILABLE = f"Indexers unavailable due to failures: {demo_world.FLAKY_INDEXER}"
FLAKY_NUMBER = [name for name, _protocol in demo_world.INDEXERS].index(demo_world.FLAKY_INDEXER) + 1


def asked(name: str, path: str, at: datetime):
    """The made-up app's answer to a GET at the moment `at` (demo_world.now substituted)."""
    real_now = demo_world.now
    demo_world.now = lambda: at
    try:
        response = MADE_UP[name].handler(httpx.Request("GET", f"http://{name}.demo{MADE_UP[name].API}{path}"))
    finally:
        demo_world.now = real_now
    return response.json() if response.status_code == 200 else None


SINCE = datetime(2026, 11, 23, tzinfo=UTC)
found = MADE_UP["prowlarr"].outages(SINCE, SINCE + timedelta(days=14))
moments = [(at, True) for start, end in found for at in (start, start + (end - start) / 2, end - timedelta(seconds=1))]
moments += [(at, False) for start, end in found for at in (start - timedelta(seconds=1), end, end + timedelta(hours=2))]
check(len(found) >= 6, f"the schedule has outages in two weeks ({len(found)})")
wrong: dict[str, list[str]] = {}
for at, out in moments:
    for name in ("sonarr", "radarr"):
        messages = [h.get("message", "") for h in asked(name, "health", at) or []]
        if (UNAVAILABLE in messages) != out or not any(m.startswith("New update is available") for m in messages):
            wrong.setdefault(name, []).append(f"{at:%d. %H:%M:%S} {'in' if out else 'out'}: {messages}")
    for name in ("lidarr", "readarr"):
        if any(demo_world.FLAKY_INDEXER in h.get("message", "") for h in asked(name, "health", at) or []):
            wrong.setdefault(name, []).append(f"{at:%d. %H:%M:%S}")
    status = asked("prowlarr", "indexerstatus", at)
    messages = [h.get("message", "") for h in asked("prowlarr", "health", at) or []]
    left_out = [s.get("indexerId") for s in status] if isinstance(status, list) else None
    if left_out != ([FLAKY_NUMBER] if out else []) or (UNAVAILABLE in messages) != out:
        wrong.setdefault("prowlarr", []).append(f"{at:%d. %H:%M:%S} {'in' if out else 'out'}: {status} {messages}")
for name in ("sonarr", "radarr"):
    check(name not in wrong, f"{name}'s health warns about {demo_world.FLAKY_INDEXER} exactly in its outages, an update"
          f" always ({len(moments)} moments; {wrong.get(name, [])[:2]})")
check(not {"lidarr", "readarr"} & set(wrong), f"Lidarr's and Readarr's health never name it ({wrong.get('lidarr', [])[:1]}"
      f" {wrong.get('readarr', [])[:1]})")
check("prowlarr" not in wrong, f"Prowlarr leaves it out exactly then (indexerstatus, health) ({wrong.get('prowlarr', [])[:2]})")
schedule = demo_world.flaky_outages(int(SINCE.timestamp()), int((SINCE + timedelta(days=14)).timestamp()))
check([(datetime.fromtimestamp(a, UTC), datetime.fromtimestamp(b, UTC)) for a, b in schedule] == found
      and all(demo_world.flaky_down(at) == out for at, out in moments),
      "the schedule is demo_world's: Prowlarr's outages are flaky_outages, flaky_down says the same at every moment")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
