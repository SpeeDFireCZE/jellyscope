# -*- coding: utf-8 -*-
r"""Small API matters deferred from the review of the filters (1.8.1).

* a time in a filter can be given in the shape the API itself returns it in too;
* `min_size_mb` with a huge number is 400, not 500, and decimal MB are not cut off;
* 404 and 405 under /api/ carry the code in `error` like the other errors;
* numbers only from ASCII digits and without a newline at the end;
* the same order with the limit in SQL and without it (ties);
* text is compared in another way of writing the same character too (NFC / NFD);
* `/least-played` with `sort` does not report `order: size`;
* the Spanish name of the type "čas" in a filter.

Run:
    .venv/bin/python tests/test_api_details.py
"""
from __future__ import annotations

import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "drobnosti.db")
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
ago = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime(db.TIME_FORMAT)  # noqa: E731
SAME_TIME = ago(5)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u1', 'Jana', 0, 0)")
    # Five titles with the same date added, name and size - ties.
    for k in range(5):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing) VALUES (?, 'Kopie', 'Movie', 1258291, 'lib1', ?, ?, 'jellyfin', 0)",
            (f"k{k}", SAME_TIME, f"/m/kopie{k}.mkv"))
    # Four old unplayed files of the same size - ties in the unused space.
    for k in range(4):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing) VALUES (?, 'Stary', 'Movie', 5000000, 'lib1', ?, ?,"
            " 'jellyfin', 0)", (f"s{k}", ago(500), f"/m/stary{k}.mkv"))
    # A series and a movie added at the same moment (a tie between the groups).
    NEW = ago(2)
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
        " tech_source, is_missing) VALUES ('b-film', 'Film B', 'Movie', 1000, 'lib1', ?,"
        " '/m/b.mkv', 'jellyfin', 0)", (NEW,))
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
        " tech_source, is_missing, series_id, series_name, parent_index_number, index_number)"
        " VALUES ('a-dil', 'Dil 1', 'Episode', 1000, 'lib1', ?, '/m/s/d1.mkv', 'jellyfin', 0,"
        " 'z-serial', 'Serial Z', 1, 1)", (NEW,))
    # Three big unplayed files of the same size (insights: oversized).
    # Inserted backwards, so the order of the rows in the table does not decide for the test.
    for k in (2, 1, 0):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing) VALUES (?, 'Velky', 'Movie', 5000000000, 'lib1', ?, ?,"
            " 'jellyfin', 0)", (f"v{k}", ago(600), f"/m/velky{k}.mkv"))
    # Three titles started and left unfinished the same way (insights: never_finished).
    for k in (2, 1, 0):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
            " tech_source, is_missing, runtime_ticks) VALUES (?, ?, 'Movie', 1000, 'lib1', ?, ?,"
            " 'jellyfin', 0, 72000000000)", (f"n{k}", f"Nedokonceny {k}", ago(600), f"/m/n{k}.mkv"))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
            " started_at, last_seen_at, watched_seconds, paused_seconds, is_paused, is_active,"
            " play_method, client, position_ticks) VALUES (?, 'u1', 'Jana', ?, ?, 'Movie', ?, ?,"
            " 300, 0, 0, 0, 'DirectPlay', 'Web', 3000000000)",
            (f"ns{k}", f"n{k}", f"Nedokonceny {k}", ago(1), ago(1)))
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, library_id, date_created, path,"
        " tech_source, is_missing) VALUES ('am', 'Amélie', 'Movie', 2000000000, 'lib1', ?,"
        " '/m/amelie.mkv', 'jellyfin', 0)", (ago(400),))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " started_at, last_seen_at, watched_seconds, paused_seconds, is_paused, is_active,"
        " play_method, client) VALUES ('s1', 'u1', 'Jana', 'am', 'Amélie', 'Movie', ?, ?,"
        " 3600, 0, 0, 0, 'DirectPlay', 'Web')", (ago(1), ago(1)))
    conn.commit()

client = TestClient(app)
H = {"Authorization": f"Bearer {api.create_token('Testy')['token']}"}


def get(address: str):
    return client.get(address, headers=H)


print("--- a time in the shape of the answer ---")
response = get("/api/v1/history?started_at[gte]=2026-01-01T12:00:00.0000000Z")
check(response.status_code == 200, f"the shape 2026-01-01T12:00:00.0000000Z passes ({response.status_code})")
check(get("/api/v1/history?started_at[gte]=2026-01-01T12:00:00Z").status_code == 200, "without fractions too")
check(get("/api/v1/history?started_at[gte]=2026-01-01T12:00").status_code == 400, "an incomplete time is 400")

print("--- min_size_mb ---")
response = get("/api/v1/least-played?min_size_mb=1e20")
check(response.status_code == 400 and response.json().get("error") == "invalid_value",
      f"a huge number is 400 invalid_value ({response.status_code})")
copies = [t for t in get("/api/v1/least-played?min_size_mb=1.5&limit=500").json().get("items", [])
          if t["name"] == "Kopie"]
check(not copies, f"1.5 MB is not cut down to 1 MB - a 1.2 MB file does not pass ({len(copies)})")

print("--- 404 and 405 under /api/ ---")
response = get("/api/v1/neexistuje")
check(response.status_code == 404 and response.json().get("error") == "not_found",
      f"an unknown address: 404 not_found ({response.json()})")
response = client.post("/api/v1/history", headers=H)
check(response.status_code == 405 and response.json().get("error") == "method_not_allowed",
      f"a method other than GET: 405 method_not_allowed ({response.status_code} {response.text[:80]})")

print("--- numbers only from ASCII ---")
for query in ("days=30%0A", "days=%D9%A3%D9%A0", "limit=5%0A", "limit=%EF%BC%95",
              "min_size_mb=%D9%A3", "min_size_mb=5%0A", "min_size_mb=1_0", "min_size_mb=1e3"):
    response = get(f"/api/v1/least-played?{query}")
    check(response.status_code == 400, f"{query}: 400 ({response.status_code})")
for query in ("watched_seconds[gte]=%EF%BC%95", "watched_seconds[gte]=1_800",
              "watched_seconds[gte]=1800%0A"):
    response = get(f"/api/v1/history?{query}")
    check(response.status_code == 400, f"{query}: 400 ({response.status_code})")
check(get("/api/v1/least-played?min_size_mb=0.5").status_code == 200
      and get("/api/v1/history?watched_seconds[gte]=-1.5").status_code == 200,
      "an ordinary decimal and a negative number pass")

print("--- ties: with the limit and without ---")
for address, listing in (("/api/v1/recently-added", "items"), ("/api/v1/least-played", "items"),
                         ("/api/v1/unwatched", "items")):
    fast = [t.get("id") or t.get("item_id") for t in get(f"{address}?limit=3").json().get(listing, [])]
    full = [t.get("id") or t.get("item_id")
            for t in get(f"{address}?limit=3&name[not]=nic").json().get(listing, [])]
    check(fast and fast == full, f"{address}: the same order with the limit in SQL and without ({fast} / {full})")

check([t.get("id") for t in get("/api/v1/recently-added?limit=1").json().get("items", [])]
      == [t.get("id") for t in get("/api/v1/recently-added?limit=1&name[not]=nic").json().get("items", [])],
      "a series and a movie added at once: the same first title with the limit and without")
# A tie is decided by the id (files), or the name (titles) - the same in SQLite and
# PostgreSQL, not by however it happens to come out of the database.
for listing, key, expected in (("oversized_rarely_watched", "id", ["v0", "v1"]),
                               ("never_finished", "title", ["Nedokonceny 0", "Nedokonceny 1"])):
    fast = [t.get(key) for t in get("/api/v1/insights?limit=2").json().get(listing, [])]
    full = [t.get(key) for t in get(f"/api/v1/insights?limit=2&{listing}.{key}[not]=nic").json().get(listing, [])]
    check(fast == full == expected,
          f"insights {listing}: a tie is decided by {key}, with the limit and without ({fast} / {full})")

print("--- another way of writing the same character ---")
nfd = "Amélie"
response = get(f"/api/v1/history?title[contains]={nfd}")
check(len(response.json().get("playbacks", [])) == 1, "„Amélie“ written decomposed (NFD) is found")

print("--- order with sort ---")
response = get("/api/v1/least-played?sort=-size_bytes")
check(response.status_code == 200 and response.json().get("order") is None,
      f"with sort there is no order: size ({response.json().get('order')})")
check(get("/api/v1/least-played").json().get("order") == "size", "without sort still size")

print("--- Spanish ---")
es = json.loads((PROJECT / "jellyscope" / "translations" / "es.json").read_text(encoding="utf-8"))
check(es.get("time") == "fecha y hora", f"the field type „time“ is date and time in Spanish ({es.get('time')})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
