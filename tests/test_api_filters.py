# -*- coding: utf-8 -*-
r"""Filters, sorting and the strict parameters of the API through the real app.

`test_api_query.py` watches the reading of the query itself; here it is checked
that the addresses really use it - over the database, with keys and their rights:

* nonsense in the query returns 400, never the default data;
* the API's errors have one body (`detail` + `error`), 401/403/404 too;
* a filter runs over the whole list, so it finds what is deep in today's order
  too (the 5 least played from a path, oldest first);
* a filter sees only what the key does - a hidden name cannot be found by it.

Run:
    .venv/bin/python tests/test_api_filters.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "filtry.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
GB = 1024 ** 3
ago = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime(db.TIME_FORMAT)  # noqa: E731

# 27 movies in /media/jine/ played 0-2x and 3 in /media/xy/ played 5-6x:
# in today's order of the least played those come only after 27 others, so outside
# the default limit of 25. A filter must find them anyway.
XY = (("xa", 5, "2024-03-01 10:00:00"), ("xb", 5, "2024-01-01 10:00:00"),
      ("xc", 6, "2023-06-01 10:00:00"))
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES"
                 " ('lib1', 'Filmy', 'movies'), ('lib2', 'Seriály', 'tvshows')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    plays = []
    for i in range(27):
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, width, height, video_codec,"
            " library_id, date_created, path, tmdb_id, tech_source, is_missing)"
            " VALUES (?, ?, 'Movie', ?, 1920, 1080, 'h264', 'lib1', ?, ?, ?, 'jellyfin', 0)",
            (f"i{i}", f"Film {i}", (i + 1) * GB, ago(400 + i),
             f"/media/jine/film{i}.mkv", str(1000 + i)))
        plays += [(f"i{i}", f"Film {i}")] * (i % 3)
    for name, times, added in XY:
        conn.execute(
            "INSERT INTO items (id, name, type, size_bytes, width, height, video_codec,"
            " library_id, date_created, path, tech_source, is_missing)"
            " VALUES (?, ?, 'Movie', ?, 3840, 2160, 'hevc', 'lib1', ?, ?, 'jellyfin', 0)",
            (name, name.upper(), 2 * GB, added, f"/media/xy/{name}.mkv"))
        plays += [(name, name.upper())] * times
    conn.execute(
        "INSERT INTO items (id, name, type, series_id, series_name, parent_index_number,"
        " index_number, size_bytes, library_id, date_created, path, tmdb_id, tech_source,"
        " is_missing) VALUES ('e1', 'Pilot', 'Episode', 's1', 'Kancl', 1, 2, ?, 'lib2', ?,"
        " '/media/serialy/Kancl/S01E02.mkv', '2316', 'jellyfin', 0)", (GB, ago(30)))
    plays += [("e1", "Pilot")] * 2
    for k, (item, title) in enumerate(plays):
        who = "u1" if k % 2 else "u2"
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, remote_address, bitrate)"
            " VALUES (?, ?, ?, ?, ?, 'Movie', ?, ?, 3600, 0, 0, 0, ?, ?,"
            " '203.0.113.77', 8000000)",
            (f"s{k}", who, "Jana" if who == "u1" else "Petr", item, title,
             ago(1 + k % 20), ago(1 + k % 20),
             "Transcode" if k % 5 == 0 else "DirectPlay",
             "Jellyfin Web" if k % 3 else "Infuse"))
    conn.commit()

client = TestClient(app)
SERVER = api.create_token("Grafana")
H = {"Authorization": f"Bearer {SERVER['token']}"}
READER_ID = accounts.create("reader", "readerpass")
READER = api.create_token("Homepage", READER_ID)
HC = {"Authorization": f"Bearer {READER['token']}"}


def get(address: str, headers: dict | None = None):
    return client.get(address, headers=H if headers is None else headers)


def code(response) -> str | None:
    try:
        return response.json().get("error")
    except ValueError:
        return None


print("--- errors have one body ---")
response = get("/api/v1/summary", {})
check(response.status_code == 401 and code(response) == "missing_token"
      and response.headers.get("www-authenticate") == "Bearer"
      and response.json().get("detail"),
      f"without a token: 401 missing_token with WWW-Authenticate ({response.status_code}, {code(response)})")
response = get("/api/v1/summary", {"Authorization": "Bearer js_nesmysl"})
check(response.status_code == 401 and code(response) == "invalid_token"
      and response.headers.get("www-authenticate") == "Bearer",
      f"an invalid token: 401 invalid_token ({code(response)})")

api.disable_token(SERVER["id"])
response = get("/api/v1/summary")
check(response.status_code == 403 and code(response) == "key_disabled",
      f"a disabled key: 403 key_disabled ({code(response)})")
api.enable_token(SERVER["id"])

response = get("/api/v1/summary", HC)
check(response.status_code == 403 and code(response) == "key_not_allowed",
      f"a reader without the key permission: 403 key_not_allowed ({code(response)})")
db.set_setting("reader_may_have_key", "1")
check(get("/api/v1/summary", HC).status_code == 200, "with the permission it passes")
db.set_setting("reader_sees_network", "0")
response = get("/api/v1/bandwidth", HC)
check(response.status_code == 403 and code(response) == "area_not_allowed",
      f"a reader outside the area: 403 area_not_allowed ({code(response)})")

response = get("/api/v1/item/neexistuje")
check(response.status_code == 404 and code(response) == "not_found",
      f"a title that does not exist: 404 not_found ({code(response)})")
response = get("/api/v1/item/i;DROP")
check(response.status_code == 404 and code(response) == "not_found",
      "a nonsense id: 404 not_found too")
check(all(set(get(a, h).json()) >= {"detail", "error"} for a, h in (
          ("/api/v1/summary", {}), ("/api/v1/bandwidth", HC), ("/api/v1/item/x", H))),
      "every error body has detail and error")

print()
print("--- lists of titles ---")
from jellyscope import insights, stats  # noqa: E402

default = get("/api/v1/least-played").json()["items"]
check(not any(p["path"].startswith("/media/xy") for p in default),
      "the files from /media/xy are past the default limit in today's order")
response = get("/api/v1/least-played?path[starts_with]=/media/xy&sort=plays,added_at&limit=5")
check(response.status_code == 200
      and [p["id"] for p in response.json().get("items", [])] == ["xb", "xa", "xc"],
      "the 5 least played from /media/xy, oldest first on a tie: all 3 "
      f"({response.status_code}, {[p['id'] for p in response.json().get('items', [])]})")
response = get("/api/v1/least-played?order=oldest&sort=plays")
check(response.status_code == 400 and code(response) == "conflicting_parameters",
      f"order and sort at once: 400 conflicting_parameters ({code(response)})")
check(get("/api/v1/least-played?order=size").status_code == 200, "order=size still passes")

THREE = "/media/jine/film0.mkv,/media/jine/film3.mkv,/media/jine/film6.mkv"
dead = insights.dead_storage(25, 365)
whole = get("/api/v1/unwatched").json()
check(whole.get("count") == dead["item_count"] and whole.get("size_bytes") == dead["size_bytes"]
      # float(): PostgreSQL returns the sum as a Decimal, and Decimal('30.4') != 30.4
      and whole.get("share_percent") == round(float(dead["share"]), 1)
      and [p["id"] for p in whole.get("items", [])] == [r["id"] for r in dead["rows"]],
      "unwatched without a filter: the same numbers and items as today "
      f"({whole.get('count')}/{dead['item_count']}, {whole.get('size_bytes')}/{dead['size_bytes']!r}, "
      f"{whole.get('share_percent')}/{dead['share']!r})")
three = get(f"/api/v1/unwatched?path[in]={THREE}").json()
check(three.get("count") == 3 and three.get("size_bytes") == 12 * GB
      and three.get("share_percent") == round(12 * GB / whole["library_bytes"] * 100, 1)
      and three.get("library_bytes") == whole["library_bytes"],
      f"unwatched with a filter: the sums only for the chosen ({three.get('count')}, {three.get('size_bytes')})")
nothing = get("/api/v1/unwatched?path[starts_with]=/nikde").json()
check(nothing.get("items") == [] and nothing.get("count") == 0 and nothing.get("share_percent") == 0,
      "a filter that finds nothing: an empty list and zeros")
check(api._unused_summary([], 0)["share_percent"] == 0,
      "an empty library: share 0, no division by zero")

series = get("/api/v1/top-items?is_series[equals]=true").json().get("items", [])
check(series and all(p["is_series"] for p in series), "top-items: series only")
check([p["title"] for p in get("/api/v1/top-items").json()["items"]]
      == [r["label"] for r in stats.top_items(30, 10, "both")],
      "top-items without a filter as today")
movies = get("/api/v1/recently-added?library[equals]=Filmy&limit=3").json().get("items", [])
check(0 < len(movies) <= 3 and all(p["library"] == "Filmy" for p in movies),
      "recently-added: Filmy only, 3 at most")
check([p["id"] for p in get("/api/v1/recently-added").json()["items"]]
      == [r["id"] for r in stats.recently_added(18)], "recently-added without a filter as today")
check([p["id"] for p in default]
      == [r["id"] for r in insights.least_played(365, 25, 0, "size")],
      "least-played without a filter as today")

for address, expected in (
        ("/api/v1/least-played?days=abc", "invalid_value"),
        ("/api/v1/least-played?days=0", "invalid_value"),
        ("/api/v1/least-played?days=3651", "invalid_value"),
        ("/api/v1/least-played?limit=0", "invalid_value"),
        ("/api/v1/least-played?limit=501", "invalid_value"),
        ("/api/v1/unwatched?limit=abc", "invalid_value"),
        ("/api/v1/top-items?limit=201", "invalid_value"),
        ("/api/v1/top-items?kind=DROP", "invalid_value"),
        ("/api/v1/least-played?min_size_mb=-5", "invalid_value"),
        ("/api/v1/least-played?order=bogus", "invalid_value"),
        ("/api/v1/least-played?days=7&days=30", "duplicate_parameter"),
        ("/api/v1/least-played?foo=1", "unknown_parameter"),
        ("/api/v1/recently-added?days=7", "unknown_parameter"),
        ("/api/v1/top-items?plays[contains]=1", "operator_not_allowed")):
    response = get(address)
    check(response.status_code == 400 and code(response) == expected,
          f"{address} -> 400 {expected} ({response.status_code}, {code(response)})")
check(get("/api/v1/top-items?kind=MOVIES").status_code == 200, "kind in capitals passes")

print()
print("--- people and traffic ---")
from jellyscope import access  # noqa: E402

history = get("/api/v1/history?is_transcode[equals]=false&sort=-started_at&limit=500").json()
rows = history.get("playbacks", [])
check(rows and all(not p["is_transcode"] for p in rows)
      and [p["started_at"] for p in rows] == sorted((p["started_at"] for p in rows), reverse=True),
      f"history: direct plays only, newest first ({len(rows)})")
petr = get("/api/v1/history?user=u2&limit=500").json().get("playbacks", [])
check(petr and all(p["user_id"] == "u2" for p in petr), "history?user=u2 Petr only")
for wrong in ("a;b", "a" * 65):
    response = get(f"/api/v1/history?user={wrong}")
    check(response.status_code == 400 and code(response) == "invalid_value",
          f"history?user={wrong[:8]!r} -> 400 invalid_value ({code(response)})")
check(get("/api/v1/history?user=").status_code == 200,
      "an empty user= (as the guide showed it) = not limited to a viewer")

# A viewer from Jellyfin (Petr, u2) with their own key.
db.set_setting(access.LOGIN_KEY, "1")
db.set_setting("viewer_may_have_key", "1")
viewer = accounts.from_jellyfin({"id": "u2", "name": "Petr", "is_admin": False, "is_disabled": False})
HD = {"Authorization": f"Bearer {api.create_token('Mobil', viewer['id'])['token']}"}
response = get("/api/v1/history?user=u1", HD)
check(response.status_code == 403 and code(response) == "own_history_only",
      f"a viewer with somebody else's user: 403 own_history_only ({response.status_code}, {code(response)})")
for address in ("/api/v1/history?user=u2", "/api/v1/history"):
    response = get(address, HD)
    own = response.json().get("playbacks", []) if response.status_code == 200 else []
    check(response.status_code == 200 and own and all(p["user_id"] == "u2" for p in own),
          f"a viewer {address}: 200 and only their own ({response.status_code})")
response = get("/api/v1/history?user=a;b", HD)
check(response.status_code == 400, "a nonsense user is 400 before it is decided whose it is")

# A reader with hidden names: does not find a real name by a filter, not even letter by letter.
db.set_setting("reader_anonymise", "1")
check(get("/api/v1/users?name[equals]=Petr").json().get("users"),
      "the server's key finds Petr (the filter works)")
for address, listing in (("/api/v1/users?name[equals]=Petr", "users"),
                         ("/api/v1/history?user[contains]=Pe", "playbacks"),
                         ("/api/v1/history?user[starts_with]=P", "playbacks")):
    response = get(address, HC)
    check(response.status_code == 200 and response.json().get(listing) == [],
          f"a reader with anonymising: {address} finds nothing")
db.set_setting("reader_anonymise", "0")

viewers = get("/api/v1/users?plays[gte]=1&sort=-watched_hours").json().get("users", [])
check(viewers and all(u["plays"] >= 1 for u in viewers)
      and [u["watched_hours"] for u in viewers] == sorted((u["watched_hours"] for u in viewers),
                                                          reverse=True),
      "users: filter and sorting")
check(len(get("/api/v1/users?limit=1").json().get("users", [])) == 1, "users?limit=1")
response = get("/api/v1/users?limit=501")
check(response.status_code == 400 and code(response) == "invalid_value", "users?limit=501 -> 400")

with db.connect() as conn:
    for k, paused in enumerate((0, 1)):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client)"
            " VALUES (?, 'u1', 'Jana', 'i1', 'Film 1', 'Movie', ?, ?, 60, 0, ?, 1,"
            " 'DirectPlay', 'Jellyfin Web')", (f"zive{k}", ago(0), ago(0), paused))
    conn.commit()
everything = get("/api/v1/now-playing").json()
running = get("/api/v1/now-playing?is_paused[equals]=false").json()
check(everything.get("count") == 2 and running.get("count") == 1
      and all(not s["is_paused"] for s in running.get("sessions", [])),
      f"now-playing: only those not paused ({everything.get('count')}, {running.get('count')})")
response = get("/api/v1/now-playing?days=7")
check(response.status_code == 400 and code(response) == "unknown_parameter",
      "now-playing does not know days -> unknown_parameter")

libraries = get("/api/v1/libraries?type[equals]=movies").json()
check(libraries.get("count") == 1 and libraries["libraries"][0]["name"] == "Filmy",
      "libraries: movies only")

# Without a GeoIP database there are no countries - they are slipped in, so there is something to filter.
original_countries = stats.viewer_countries
stats.viewer_countries = lambda days: [
    {"label": "Česko", "code": "CZ", "plays": 3, "seconds": 100, "byte_count": 500},
    {"label": "Německo", "code": "DE", "plays": 1, "seconds": 10, "byte_count": 50},
    {"label": "Slovensko", "code": "SK", "plays": 2, "seconds": 60, "byte_count": 900}]
whole_traffic = get("/api/v1/bandwidth").json()
cz_sk = get("/api/v1/bandwidth?code[in]=CZ,SK&sort=-bytes").json()
stats.viewer_countries = original_countries
check([z["code"] for z in cz_sk.get("countries", [])] == ["SK", "CZ"],
      f"bandwidth: CZ and SK only, biggest first ({[z['code'] for z in cz_sk.get('countries', [])]})")
check(cz_sk.get("total_bytes") == whole_traffic.get("total_bytes") and len(whole_traffic.get("countries", [])) == 3,
      "a filter does not change the traffic summary")

print()
print("--- more lists: insights and play-methods ---")
whole = get("/api/v1/insights?days=30&limit=15").json()
check([r["title"] for r in whole.get("transcode_offenders", [])]
      == [r["label"] for r in insights.transcode_offenders(30, 15)]
      and [r["title"] for r in whole.get("upgrade_candidates", [])]
      == [r["label"] for r in insights.upgrade_candidates(30, 15)]
      and [r["title"] for r in whole.get("oversized_rarely_watched", [])]
      == [r["label"] for r in insights.oversized_rarely_watched(30, 15)]
      and [r["title"] for r in whole.get("never_finished", [])]
      == [r["label"] for r in insights.never_finished(30, 15)]
      and "transcode_speed" in whole,
      "insights without a filter as today (all four lists and the speed)")
methods = get("/api/v1/play-methods?days=30").json()
check([m["method"] for m in methods.get("methods", [])]
      == [m["method"] for m in stats.play_method_breakdown(30)]
      and [c["client"] for c in methods.get("clients", [])]
      == [c["label"] for c in stats.client_breakdown(30, 25)],
      "play-methods without a filter as today")

# The Insights lists are empty on small data - they are slipped in.
original_unfinished = insights.never_finished
original_big = insights.oversized_rarely_watched
insights.never_finished = lambda days, limit=15: [
    {"label": "A", "attempts": 3, "best_percent": 20, "runtime_minutes": 100},
    {"label": "B", "attempts": 5, "best_percent": 40, "runtime_minutes": 90},
    {"label": "C", "attempts": 9, "best_percent": 80, "runtime_minutes": 80}][:limit]
insights.oversized_rarely_watched = lambda days, limit=15: [
    {"id": i, "label": i.upper(), "type": "Movie", "path": f"/media/{folder}/{i}.mkv",
     "tmdb_id": None, "plays": 1, "hours": 1, "size_bytes": 50 * GB, "gb_per_hour": 50,
     "width": 3840, "height": 2160}
    for i, folder in (("v1", "other"), ("v2", "xy"), ("v3", "xy"))][:limit]
narrowed = get("/api/v1/insights?never_finished.best_percent[lt]=50"
               "&sort=-never_finished.attempts").json()
check([r["title"] for r in narrowed.get("never_finished", [])] == ["B", "A"],
      f"never_finished.best_percent[lt]=50 + sort ({[r['title'] for r in narrowed.get('never_finished', [])]})")
check(narrowed.get("transcode_offenders") == whole.get("transcode_offenders")
      and narrowed.get("upgrade_candidates") == whole.get("upgrade_candidates")
      and narrowed.get("transcode_speed") == whole.get("transcode_speed")
      and len(narrowed.get("oversized_rarely_watched", [])) == 3,
      "the other lists unchanged")
xy = get("/api/v1/insights?oversized_rarely_watched.path[starts_with]=/media/xy").json()
check([r["id"] for r in xy.get("oversized_rarely_watched", [])] == ["v2", "v3"],
      "oversized: only files from /media/xy")
insights.never_finished = original_unfinished
insights.oversized_rarely_watched = original_big

response = get("/api/v1/insights?title[contains]=x")
check(response.status_code == 400 and code(response) == "unknown_list"
      and response.json().get("allowed") == ["transcode_offenders", "upgrade_candidates",
                                             "oversized_rarely_watched", "never_finished"],
      f"insights: a field without a list -> 400 unknown_list with the choices ({code(response)})")
response = get("/api/v1/insights?foo.title[contains]=x")
check(response.status_code == 400 and code(response) == "unknown_list", "an unknown list")
clients = get("/api/v1/play-methods?clients.client[equals]=infuse&sort=-clients.plays").json()
check([c["client"] for c in clients.get("clients", [])] == ["Infuse"]
      and clients.get("methods") == methods.get("methods"),
      "play-methods: narrows only clients")
for address in ("/api/v1/insights?limit=101", "/api/v1/play-methods?limit=501",
                "/api/v1/insights?days=-3", "/api/v1/play-methods?days=NaN"):
    response = get(address)
    check(response.status_code == 400 and code(response) == "invalid_value",
          f"{address} -> 400 invalid_value ({code(response)})")

print()
print("--- every address ---")
from jellyscope import applog  # noqa: E402
from jellyscope.api_query import OPERATORS  # noqa: E402

check(set(api.KNOWN_PARAMS) == set(api.ADDRESS_AREAS),
      f"every address has its parameters described ({sorted(set(api.KNOWN_PARAMS) ^ set(api.ADDRESS_AREAS))})")
check(set(api.LISTS) <= set(api.ADDRESS_AREAS), "lists only for existing addresses")


def has_guard(dependency) -> bool:
    return any(d.call is api.may_open_path or has_guard(d) for d in dependency.dependencies)


paths = sorted(r.path for r in api.router.routes if "GET" in getattr(r, "methods", ()))
for path in paths:
    address = path.replace("{item_id}", "i1")
    route = next(r for r in api.router.routes if r.path == path)
    without = get(address + "?days=abc&nesmysl=1&plays[gt]=x", {})
    with_key = get(address + ("&" if "?" in address else "?") + "nesmysl=1")
    check(without.status_code == 401 and with_key.status_code == 400
          and code(with_key) == "unknown_parameter" and has_guard(route.dependant),
          f"{path}: without a key 401 even with nonsense, with a key 400, has a guard "
          f"({without.status_code}, {with_key.status_code}, {code(with_key)})")

for address, expected in (("/api/v1/summary?plays[gt]=1", "not_filterable"),
                          ("/api/v1/library?sort=x", "not_filterable"),
                          ("/api/v1/item/i1?path[contains]=x", "not_filterable"),
                          ("/api/v1/summary?limit=5", "unknown_parameter"),
                          ("/api/v1/?days=7", "unknown_parameter")):
    response = get(address)
    check(response.status_code == 400 and code(response) == expected,
          f"{address} -> 400 {expected} ({response.status_code}, {code(response)})")
check(get("/api/v1/summary?days=7").status_code == 200
      and get("/api/v1/library?days=7").status_code == 200, "summary and library pass with days")

print()
print("--- the description of the fields matches the answer ---")
# Empty lists get one slipped-in row: the shape of a row is made by the address
# (`r.get(...)`), so an empty dict is enough.
slipped_in = {(insights, "never_finished"), (insights, "oversized_rarely_watched"),
              (insights, "upgrade_candidates"), (insights, "transcode_offenders"),
              (stats, "viewer_countries")}
original = {(m, n): getattr(m, n) for m, n in slipped_in}
for module, name in slipped_in:
    setattr(module, name, lambda *a, **k: [{}])
for path, listings in api.LISTS.items():
    response = get(path).json()
    for listing, fields in listings.items():
        rows = response.get(listing) or []
        check(bool(rows) and all(set(r) == set(fields) for r in rows),
              f"{path} {listing}: the row's keys = the description "
              f"({sorted(set(rows[0]) ^ set(fields)) if rows else 'an empty list'})")
for (module, name), function in original.items():
    setattr(module, name, function)

print()
print("--- the index ---")
index = get("/api/v1/").json()
check(index.get("filtering", {}).get("operators") == {k: list(v) for k, v in OPERATORS.items()},
      "the index: operators by type")
check(index.get("lists", {}).get("/api/v1/least-played") == api.LISTS["/api/v1/least-played"]
      and "/api/v1/insights" in index.get("lists", {}),
      "the index: the list fields for the addresses")
reader_lists = get("/api/v1/", HC).json().get("lists", {})
check("/api/v1/bandwidth" not in reader_lists and "/api/v1/least-played" in reader_lists,
      "a reader without Network does not see the fields of /api/v1/bandwidth")
check(api.path_fields(["/api/v1/least-played", "/api/v1/summary"])
      == {"/api/v1/least-played": api.LISTS["/api/v1/least-played"]},
      "path_fields: only addresses with a list")

print()
print("--- covering indexes for the summaries of plays ---")
# The summaries "how many times and when last" (least-played, unused space,
# Insights) went through an index only halfway: for each of 150,000 plays
# SQLite then jumped into the table for watched_seconds and started_at. Covering
# indexes have everything in them - on 40,000 files 0.5 s -> 0.05-0.09 s.
if db.current_dialect() == "postgres":
    indexes = {r["indexname"] for r in db.query_all(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'playback'")}
else:
    indexes = {r["name"] for r in db.query_all(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'playback'")}
check({"idx_playback_item_time", "idx_playback_time_item"} <= indexes,
      f"playback has both covering indexes ({sorted(i for i in indexes if 'time' in i)})")
check("idx_playback_item" not in indexes,
      "the superfluous idx_playback_item (a prefix of the covering one) is gone - it would only slow writing")

print()
print("--- without a filter the whole list is not read ---")
# The whole list is expensive (on a library of 40,000 files tenths of a second
# up to seconds). Without a filter and sorting, what was before is enough: the limit
# straight in SQL. With a filter or sorting everything has to be read.
limits = []
original_least = insights.least_played


def spy(days, limit=25, *args, **kwargs):
    limits.append(limit)
    return original_least(days, limit, *args, **kwargs)


insights.least_played = spy
get("/api/v1/least-played?limit=7")
get("/api/v1/least-played?sort=plays&limit=7")
get("/api/v1/least-played?path[contains]=x&limit=7")
insights.least_played = original_least
check(limits == [7, stats.NO_LIMIT, stats.NO_LIMIT],
      f"without a filter the limit in SQL, with sort or a filter the whole list ({limits})")

get("/api/v1/least-played?foo=1")
last = applog.read_api(5)["rows"][0]
check(last["result"] == 400 and last["path"] == "/api/v1/least-played?foo=1",
      f"a refused query is in the API log with the status 400 ({last['result']})")

print()
print("--- history with a filter only thin rows ---")
# stats.history() returns p.* with everything and a transcode analysis for each row:
# the whole history on 150,000 plays thus took over 400 MB. With a filter a thin
# history is therefore read in batches (stats.history_for_api).
import json  # noqa: E402

# A server error as 500, not an exception - so the test gets to its end.
tolerant = TestClient(app, raise_server_exceptions=False)


class _Response:
    """A response that can always be read as JSON (500 is {})."""

    def __init__(self, response):
        self.status_code = response.status_code
        try:
            self._json = response.json()
        except ValueError:
            self._json = {}

    def json(self):
        return self._json


def get(address: str, headers: dict | None = None):  # noqa: F811
    return _Response(tolerant.get(address, headers=H if headers is None else headers))


original_history = stats.history


def only_with_limit(*args, **kwargs):
    if kwargs.get("limit") == stats.NO_LIMIT:
        raise AssertionError("the whole fat history")
    return original_history(*args, **kwargs)


stats.history = only_with_limit
response = get("/api/v1/history?is_transcode[equals]=false&sort=-started_at&limit=500")
all_by_filter = get("/api/v1/history?watched_seconds[gte]=0&limit=500").json().get("playbacks", [])
stats.history = original_history
check(response.status_code == 200, f"a filter does not read the whole fat history ({response.status_code})")
without_filter = get("/api/v1/history?limit=500").json().get("playbacks", [])
check(len(all_by_filter) == len(without_filter) > 0
      and sorted(json.dumps(p, sort_keys=True) for p in all_by_filter)
      == sorted(json.dumps(p, sort_keys=True) for p in without_filter),
      "the thin way returns the same rows as before (a filter that lets everything through)")

print()
print("--- history: a prefilter in SQL gives the same result ---")
# History with a filter over the whole period carried all plays out of the database
# (150,000 rows ~ 1 s) and filtered only in Python. Simple fields are now prefiltered
# in SQL. It is only a SUPERSET - Python still decides exactly - so the result must be
# the same with the prefilter and without it.
with db.connect() as conn:
    for k, (client_name, device, method) in enumerate((
            ("Straße TV", "Obývák", "DirectPlay"), ("INFUSE", "iPad", "Transcode (v:h264)"),
            ("100%_klient", "Telefon", "DirectStream"), (None, None, None),
            ("a\\b TV", "Ložnice", "DirectPlay"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, device_name)"
            " VALUES (?, 'u1', 'Jana', 'e1', 'Pilot', 'Episode', ?, ?, ?, 0, 0, 0, ?, ?, ?)",
            (f"pf{k}", ago(2 + k), ago(2 + k), 600 * (k + 1), method, client_name, device))
    conn.commit()

CONDITIONS: list = []
original_thin = stats.history_for_api


def history_spy(*args, **kwargs):
    CONDITIONS.append(list(kwargs.get("conditions") or []))
    return original_thin(*args, **kwargs)


stats.history_for_api = history_spy
original_prefilter = api._history_prefilter
try:
  for query, goes_to_sql in (
        ("client[equals]=infuse", True), ("client[contains]=strasse", True),
        ("client[contains]=%25_", True), ("client[starts_with]=jelly", True),
        ("type[in]=movie,EPISODE", True), ("is_transcode[equals]=true", True),
        ("is_transcode[not]=true", True), ("watched_seconds[gte]=1800&sort=-started_at", True),
        # A device as a name: it is anonymised only in Python; in SQL the filter would
        # run over the real name (see test_anonymise_devices).
        ("title[ends_with]=1", True), ("device[contains]=ob", False),
        ("client[contains]=a%5Cb", True), ("client[starts_with]=100%25_", True),
        ("watched_seconds[in]=600,1200", True), ("client[contains]=!", True),
        ("user[contains]=Pe", False), ("client[equals]=Straße TV", False),
        ("client[not]=Infuse", False)):
    address = f"/api/v1/history?days=30&limit=500&{query}"
    CONDITIONS.clear()
    with_prefilter = get(address).json()
    api._history_prefilter = lambda filters: []
    without = get(address).json()
    api._history_prefilter = original_prefilter
    check(with_prefilter == without and "playbacks" in without,
          f"{query}: the same result with the prefilter and without ({len(without.get('playbacks', []))} rows)")
    check(bool(CONDITIONS and CONDITIONS[0]) == goes_to_sql,
          f"{query}: {'goes' if goes_to_sql else 'does not go'} to SQL ({CONDITIONS[:1]})")
finally:
    stats.history_for_api = original_thin
    api._history_prefilter = original_prefilter
check([p["client"] for p in get("/api/v1/history?days=30&limit=500&client[contains]=a%5Cb")
       .json().get("playbacks", [])] == ["a\\b TV"], "a backslash in a value is an ordinary character")
response = get("/api/v1/history?days=30&client[equals]=%00")
check(response.status_code == 400 and code(response) == "invalid_value",
      f"NUL in a value: 400, not a database crash ({response.status_code})")

# The prefilter filters the raw column. If _plays derived the field differently
# (COALESCE, a translation...), SQL would quietly drop rows - so it is watched here
# that every prefiltered field is that column unchanged.
for field, column in api._HISTORY_COLUMNS.items():
    raw = {column.split(".", 1)[1]: 4242 if field == "watched_seconds" else "hodnota-" + field}
    check(api._plays(raw).get(field) == next(iter(raw.values())),
          f"the field {field} is the column {column} unchanged in the answer")
check([p["client"] for p in get("/api/v1/history?days=30&limit=500&client[contains]=strasse")
       .json().get("playbacks", [])] == ["Straße TV"],
      "„strasse“ finds „Straße TV“ with the prefilter too (SQL lets non-ASCII rows through)")
check([p["client"] for p in get("/api/v1/history?days=30&limit=500&client[contains]=%25_")
       .json().get("playbacks", [])] == ["100%_klient"],
      "% and _ in a value are ordinary characters in SQL, not wildcards")

print()
print("--- broken data and a big library ---")
# Jellyfin can send DateCreated 0001-01-01 (MinValue). With a filter the whole library
# is now walked through, and the shift by a day back at the year 0001 overflowed.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, date_created,"
                 " tech_source, is_missing) VALUES ('nula', 'Nula', 'Movie', 'lib1',"
                 " '0001-01-01T00:00:00.0000000Z', 'jellyfin', 0)")
    conn.commit()
response = get("/api/v1/recently-added?name[contains]=Film&limit=5")
check(response.status_code == 200, f"a date added from the year 0001 brings nothing down ({response.status_code})")

# More groups than SQLite takes parameters (32,766): the keys of all groups used to be
# put into IN (?, ?, ...) and the query fell over.
with db.connect() as conn:
    conn.executemany(
        "INSERT INTO items (id, name, type, library_id, date_created, tech_source,"
        " is_missing) VALUES (?, ?, 'Movie', 'lib1', ?, 'jellyfin', 0)",
        [(f"h{i}", f"Hromadny {i}", ago(500 + i % 300)) for i in range(33000)])
    conn.commit()
response = get("/api/v1/recently-added?name[contains]=Hromadny%201&limit=5")
check(response.status_code == 200 and len(response.json().get("items", [])) == 5,
      f"33,000 groups: recently-added with a filter passes ({response.status_code})")
check(get("/api/v1/recently-added?sort=name&limit=5").status_code == 200,
      "with sort too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
