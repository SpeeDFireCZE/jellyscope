# -*- coding: utf-8 -*-
r"""The new overviews in the API: the least played, the unwatched, viewers, history...

`test_api.py` watches the gate - the token, the fingerprint, GETs only. This one watches
what is behind the gate:

* every address of the index answers, and the other way round every route under `/api/`
  is in the index - the API documents itself and must not lie,
* nonsense in the parameters (`days`, `limit`, `kind`, `min_size_mb`, a title's
  id) brings nothing down and gets nowhere,
* the answers hold **no IP addresses** - neither the history nor the network traffic
  gives them away, although the database has them,
* the least played really go from zero up, and the unwatched match how much
  space they take; the optional `order=oldest|newest` sorts the same number of plays
  by the date added, without it the order is as before,
* every item carries its path and tmdb id - an episode the series' id with season and episode.

Run:
    .venv/bin/python tests/test_api_overviews.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api2.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

ADDRESS = "203.0.113.77"      # a public address that must NOT show up in the API
GB = 1024 ** 3
ago = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime(db.TIME_FORMAT)  # noqa: E731
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES"
                 " ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    for number, (name, size, added) in enumerate((
            ("Duna", 30 * GB, 400), ("Amelie", 4 * GB, 400),
            ("Nikdo", 12 * GB, 400), ("Novinka", 20 * GB, 1)), start=1):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, width, height,"
            " video_codec, library_id, date_created, tech_source, is_missing)"
            " VALUES (?, ?, 'Movie', ?, 1920, 1080, 'h264', 'lib1', ?,"
            " 'jellyfin', 0)",
            (f"i{number}", name, size, ago(added)))
        conn.execute("UPDATE items SET path = ?, tmdb_id = ? WHERE id = ?",
                     (f"/media/filmy/{name}.mkv", str(1000 + number), f"i{number}"))
    # An episode: never played, small and added the day before yesterday - among the zeros it is
    # the last by size, in the middle by date. That tells the sorting by date from the
    # default one. tmdb_id is the SERIES' id (that is how Jellyscope stores it for an
    # episode), season and episode say which one it is.
    conn.execute(
        "INSERT INTO items (id, name, type, series_id, series_name,"
        " parent_index_number, index_number, size_bytes, library_id,"
        " date_created, path, tmdb_id, tech_source, is_missing)"
        " VALUES ('e1', 'Pilot', 'Episode', 's1', 'Kancl', 1, 2, ?, 'lib1', ?,"
        " '/media/serialy/Kancl/S01E02.mkv', '2316', 'jellyfin', 0)",
        (2 * GB, ago(2)))
    # Duna 3x, Amelie 1x, Nikdo and Novinka never.
    for k, (item, who, when) in enumerate((
            ("i1", "u1", 1), ("i1", "u2", 3), ("i1", "u1", 10), ("i2", "u2", 5))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, watched_seconds,"
            " paused_seconds, is_paused, is_active, play_method, client,"
            " remote_address, bitrate)"
            " VALUES (?, ?, ?, ?, ?, 'Movie', ?, ?, 5400, 0, 0, 0,"
            " 'DirectPlay', 'Jellyfin Web', ?, 8000000)",
            (f"s{k}", who, "Jana" if who == "u1" else "Petr", item,
             {"i1": "Duna", "i2": "Amelie"}[item], ago(when), ago(when), ADDRESS))
    conn.commit()

client = TestClient(app)
TOKEN = api.create_token("Grafana")["token"]
H = {"Authorization": f"Bearer {TOKEN}"}

print("--- the index does not lie: every address answers and every route is in it ---")
index = client.get("/api/v1/", headers=H).json()["endpoints"]
for path in index:
    real = path.replace("{id}", "i1")
    without = client.get(real)
    s = client.get(real, headers=H)
    check(without.status_code == 401 and s.status_code == 200,
          f"{path}: without a token {without.status_code}, with a token {s.status_code}")
    check(client.post(real, headers=H).status_code == 405,
          f"{path}: POST is 405")

routes = {r.path for r in api.router.routes if r.path.rstrip("/")}
documented = {c.replace("{id}", "{item_id}") for c in index}
missing = sorted(r for r in routes if r not in documented
                 and r.rstrip("/") != "/api/v1")
check(not missing, f"every route under /api/ is in the index (missing: {missing})")

print()
print("--- the least played: from zero up, at zero the biggest first ---")
least = client.get("/api/v1/least-played?days=365", headers=H).json()
names = [p["name"] for p in least["items"]]
plays = [p["plays"] for p in least["items"]]
check(plays == sorted(plays), f"sorted by plays ({plays})")
check(names[:2] == ["Novinka", "Nikdo"],
      f"at zero the bigger file is first ({names[:2]})")
check(names[-1] == "Duna", f"the most played is last ({names[-1]})")
duna = least["items"][-1]
check(duna["plays"] == 3 and duna["last_played"], "with the count and the last play")
check(all("size_human" in p and "library" in p for p in least["items"]),
      "every row has the size and the library")
filtered = client.get("/api/v1/least-played?min_size_mb=10000", headers=H).json()
check([p["name"] for p in filtered["items"]] == ["Novinka", "Nikdo", "Duna"],
      f"min_size_mb filters out the small ones ({[p['name'] for p in filtered['items']]})")

print()
print("--- the least played by the date added (order) ---")


def order_of(address: str) -> list[str]:
    return [p["name"] for p in client.get(address, headers=H).json()["items"]]


check(least["order"] == "size"
      and names == ["Novinka", "Nikdo", "Pilot", "Amelie", "Duna"],
      f"without order the order is as before - at zero by size ({names})")
oldest = order_of("/api/v1/least-played?days=365&order=oldest")
check(oldest == ["Nikdo", "Pilot", "Novinka", "Amelie", "Duna"],
      f"oldest: at zero oldest first, then the played ones as before ({oldest})")
newest = order_of("/api/v1/least-played?days=365&order=newest")
check(newest == ["Novinka", "Pilot", "Nikdo", "Amelie", "Duna"],
      f"newest: at zero newest first ({newest})")
check(order_of("/api/v1/least-played?days=365&order=OLDEST") == oldest,
      "the letter case does not matter")
response = client.get("/api/v1/least-played?days=365&order=size", headers=H).json()
check(response["order"] == "size" and [p["name"] for p in response["items"]] == names,
      "order=size -> the default order")
# An unknown sorting used to fall back to the default quietly. Now it is 400: an answer
# sorted differently than the tool asked for looks all right, and is not.
for wrong in ("bogus", "", "date_created; DROP TABLE items"):
    response = client.get(f"/api/v1/least-played?days=365&order={wrong}", headers=H)
    check(response.status_code == 400 and response.json().get("error") == "invalid_value",
          f"order={wrong[:20]!r} -> 400 invalid_value")
# A title without a date added: in both directions after the titles with a date - SQLite
# and PostgreSQL would otherwise each sort NULL somewhere else.
with db.connect() as conn:
    conn.execute("UPDATE items SET date_created = NULL WHERE id = 'e1'")
check(order_of("/api/v1/least-played?days=365&order=oldest")[:3] == ["Nikdo", "Novinka", "Pilot"]
      and order_of("/api/v1/least-played?days=365&order=newest")[:3] == ["Novinka", "Nikdo", "Pilot"],
      "a title without a date added is last at zero in both directions")
with db.connect() as conn:
    conn.execute("UPDATE items SET date_created = ? WHERE id = 'e1'", (ago(2),))
check(order_of("/api/v1/least-played?min_size_mb=10000&order=oldest") == ["Nikdo", "Novinka", "Duna"],
      "order goes together with min_size_mb")

print()
print("--- every item has a path and a tmdb id ---")
FIELDS = ("path", "tmdb_id", "season", "episode")
for address, key in (("/api/v1/least-played?days=365", "items"),
                     ("/api/v1/unwatched?days=365", "items"),
                     ("/api/v1/recently-added", "items")):
    items = client.get(address, headers=H).json()[key]
    check(items and all(all(k in p for k in FIELDS) for p in items),
          f"{address.split('?')[0]}: {len(items)} items, all with {', '.join(FIELDS)}")
insight_items = client.get("/api/v1/insights?days=30", headers=H).json()["oversized_rarely_watched"]
check(insight_items and all(all(k in p for k in FIELDS) for p in insight_items),
      f"insights.oversized_rarely_watched: {len(insight_items)} items, all with a path and a tmdb id")
rows = {p["name"]: p for p in least["items"]}
check(rows["Duna"]["path"] == "/media/filmy/Duna.mkv" and rows["Duna"]["tmdb_id"] == "1001"
      and rows["Duna"]["season"] is None and rows["Duna"]["episode"] is None,
      f"a movie: path and tmdb id, no season and episode ({rows['Duna']['path']}, {rows['Duna']['tmdb_id']})")
episode = rows["Pilot"]
check(episode["tmdb_id"] == "2316" and episode["season"] == 1 and episode["episode"] == 2
      and episode["path"] == "/media/serialy/Kancl/S01E02.mkv" and episode["series"] == "Kancl",
      f"an episode: the series' tmdb id, season {episode['season']}, episode {episode['episode']}")
recent = {p["name"]: p for p in client.get("/api/v1/recently-added", headers=H).json()["items"]}
check(recent.get("Pilot", {}).get("tmdb_id") == "2316"
      and recent.get("Novinka", {}).get("path") == "/media/filmy/Novinka.mkv",
      "the recently added too")
check(all(p["path"] for p in insight_items), "oversized: the path filled in")
# A music track has the track number in index_number - it must not pass for an episode.
track = api._file({"type": "Audio", "parent_index_number": 1, "index_number": 7,
                   "path": "/media/hudba/07.flac", "tmdb_id": None})
check(track["season"] is None and track["episode"] is None and track["path"],
      "season and episode only for an episode, not for a music track")

print()
print("--- the unwatched: what was not played and how much it takes ---")
dead = client.get("/api/v1/unwatched?days=365", headers=H).json()
check([p["name"] for p in dead["items"]] == ["Nikdo"],
      f"only Nikdo is unwatched - Novinka is fresh ({[p['name'] for p in dead['items']]})")
check(dead["count"] == 1 and dead["size_bytes"] == 12 * GB,
      f"and the numbers match ({dead['count']}, {dead['size_human']})")
check(0 < dead["share_percent"] < 100, f"the share of the library ({dead['share_percent']} %)")

print()
print("--- the chart, viewers, history ---")
top = client.get("/api/v1/top-items?days=30&limit=5", headers=H).json()
check(top["items"] and top["items"][0]["title"] == "Duna",
      f"the most watched is Duna ({[t['title'] for t in top['items']]})")
check(top["items"][0]["plays"] == 3, "with three plays")
viewers = client.get("/api/v1/users?days=30", headers=H).json()["users"]
check([d["name"] for d in viewers] == ["Jana", "Petr"],
      f"viewers from the most active ({[d['name'] for d in viewers]})")
check(viewers[0]["plays"] == 2 and viewers[0]["watched_hours"] == 3.0,
      "with counts and hours")
history = client.get("/api/v1/history?days=30&limit=10", headers=H).json()
check(history["count"] == 4, f"the history has four records ({history['count']})")
check(history["playbacks"][0]["title"] == "Duna"
      and history["playbacks"][0]["user"] == "Jana", "newest first")
only_petr = client.get("/api/v1/history?days=30&user=u2", headers=H).json()
check(all(p["user"] == "Petr" for p in only_petr["playbacks"])
      and only_petr["count"] == 2, "the filter by viewer")

print()
print("--- addresses go nowhere ---")
for path in ("/api/v1/history?days=30", "/api/v1/bandwidth?days=30",
             "/api/v1/users", "/api/v1/item/i1", "/api/v1/now-playing"):
    check(ADDRESS not in client.get(path, headers=H).text,
          f"{path.split('?')[0]} does not give away the IP address")
traffic = client.get("/api/v1/bandwidth?days=30", headers=H).json()
check(traffic["total_bytes"] > 0 and traffic["origin"]["internet"]["plays"] == 4,
      f"the traffic is summed by origin ({traffic['origin']['internet']['plays']} from the internet)")

print()
print("--- one title ---")
title = client.get("/api/v1/item/i1", headers=H).json()
check(title["name"] == "Duna" and title["plays"] == 3 and title["viewers"] == 2,
      f"detail: {title['name']}, {title['plays']} plays, {title['viewers']} viewers")
check(title["versions"] == [], "the versions empty when there is one file")
with db.connect() as conn:
    for number, (source, label) in enumerate((("s-4k", "4K HDR"), ("s-hd", "1080p"))):
        conn.execute("INSERT INTO item_versions (item_id, source_id, position, name, path, width, height, size_bytes)"
                     " VALUES ('i1', ?, ?, ?, ?, 1920, 1080, ?)", (source, number, label, f"/media/filmy/Duna-{source}.mkv", 10 ** 9))
    conn.commit()
versions = client.get("/api/v1/item/i1", headers=H).json()["versions"]
check([v["name"] for v in versions] == ["4K HDR", "1080p"] and [v["source_id"] for v in versions] == ["s-4k", "s-hd"],
      f"two files: the versions with their names, in order ({versions})")
with db.connect() as conn:
    conn.execute("DELETE FROM item_versions WHERE item_id = 'i1'")
    conn.commit()
check(title["path"] == "/media/filmy/Duna.mkv" and title["tmdb_id"] == "1001",
      "the detail has the path and the tmdb id")
for wrong in ("neexistuje", "i1;DROP TABLE items", "../../etc/passwd",
              "x" * 200, "%00"):
    response = client.get(f"/api/v1/item/{wrong}", headers=H)
    check(response.status_code == 404, f"id {wrong[:20]!r} -> 404 ({response.status_code})")
check(db.query_value("SELECT COUNT(*) FROM items") == 5, "the items table survived")

print()
print("--- nonsense in the parameters returns 400 and brings nothing down ---")
# Nonsense used to be quietly evened out to the default value and the data came back. Now
# it is 400 - and still nothing falls over and nothing goes into SQL.
for path in ("/api/v1/least-played?days=abc&limit=-5&min_size_mb=x",
             "/api/v1/least-played?limit=999999999",
             "/api/v1/least-played?order=" + "x" * 5000,
             "/api/v1/top-items?kind=DROP&limit=abc",
             "/api/v1/unwatched?days=0&limit=0",
             "/api/v1/recently-added?limit=1%20OR%201=1",
             "/api/v1/history?days=1e400&limit=&user=" + "a" * 500,
             "/api/v1/insights?days=-3&limit=1;--",
             "/api/v1/play-methods?days=NaN"):
    response = client.get(path, headers=H)
    check(response.status_code == 400 and response.json().get("error"),
          f"{path[:60]} -> {response.status_code}")
check(db.query_value("SELECT COUNT(*) FROM items") == 5, "the items table survived")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
