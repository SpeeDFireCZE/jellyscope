# -*- coding: utf-8 -*-
r"""The detailed offline map: Natural Earth 1:10M cut into tiles.

The administrator downloads it with a click, the app cuts it into tiles and stores them in the
main database (it is backed up with it). Watched here:

* processing: tiles of both details, only finite numbers (even with a polygon
  down to -90°), the coarser detail simplified;
* downloading: only the fixed addresses of the locked version, a size ceiling, and when
  the download falls over in the middle, the old map stays whole;
* the table is in the backups and in the SQLite -> PostgreSQL conversion.

No internet is needed - the server is substituted through httpx.MockTransport.

Run:
    .venv/bin/python tests/test_detailed_map.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in all the submodules of the scanner package

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "podrobna.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402

from jellyscope import db, dbmigrate, detailed_map, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def polygon(*rings):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [list(p) for p in rings]}}


def line(points):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString", "coordinates": points}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


# A square across the tiles' edge (longitude 0-40 = x 180-220, the edge of detail 1
# is at 202.5), a piece of Antarctica down to the south pole and a wavy line.
LAND = collection(
    polygon([[0, 0], [40, 0], [40, 30], [0, 30], [0, 0]]),
    polygon([[10, -90], [20, -90], [20, -80], [10, -80], [10, -90]]),
)
WAVES = collection(line([[i * 0.05, 45 + 0.001 * (i % 2)] for i in range(400)]))
LAKE = collection(polygon([[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]))

print("--- processing ---")
tiles: dict = {}
detailed_map.process(LAND, "land", "plocha", tiles)
detailed_map.process(LAKE, "lakes", "plocha", tiles)
detailed_map.process(WAVES, "reky", "cara", tiles)
details = {key[0] for key in tiles}
check(details == {1, 2}, f"tiles of both details ({details})")
check(detailed_map.DETAIL_LEVELS[1]["n"] == 32 and detailed_map.DETAIL_LEVELS[2]["n"] == 128,
      "a 32 × 32 grid for the coarser detail (smaller tiles), 128 × 128 for the full one")
size1 = 360 / detailed_map.DETAIL_LEVELS[1]["n"]
columns = {key[1] for key in tiles if key[0] == 1 and key[2] == int(170 / size1)
           and "land" in tiles[key]}
check(columns == set(range(int(180 / size1), int(220 / size1) + 1)),
      f"a square across the tiles' edge is in all the tiles it goes through ({sorted(columns)})")
tiny: dict = {}
# 0.05 units: simplifying (tolerance 0.04) does not delete it, but at 8-127× it is
# a few pixels - in the coarser detail it only needlessly inflates the tile.
detailed_map.process(collection(polygon([[50, 50], [50.05, 50], [50.05, 50.032], [50, 50.032],
                                         [50, 50]])), "mesta", "plocha", tiny)
check({key[0] for key in tiny} == {2},
      "a shape smaller than a few pixels is not in the coarser detail, only in the full one")
short: dict = {}
detailed_map.process(collection(line([[60, 10], [60.05, 10]])), "reky", "cara", short)
check({key[0] for key in short} == {1, 2},
      "a short piece of a line stays in the coarser detail - rivers and borders without gaps")
numbers = [c for layers in tiles.values() for paths in layers.values()
           for path in paths for c in re.findall(r"[-\d.eEinfaINF]+", path)]
check(numbers and all(re.fullmatch(r"-?\d+(\.\d+)?", c) for c in numbers),
      "the paths have only finite numbers (even with a polygon to the south pole)")


def point_count(detail: int) -> int:
    return sum(path.count("L") + 1 for (p, _, _), layers in tiles.items() if p == detail
               for path in layers.get("reky", []))


check(0 < point_count(1) < point_count(2), f"detail 1 is simplified ({point_count(1)} < {point_count(2)})")

print("--- downloading ---")
DATA = {"ne_10m_land": LAND, "ne_10m_lakes": LAKE, "ne_10m_urban_areas": LAKE,
        "ne_10m_rivers_lake_centerlines": WAVES, "ne_10m_admin_0_boundary_lines_land": WAVES,
        "ne_10m_admin_1_states_provinces_lines": WAVES}
urls: list[str] = []


def server(fail_at: int | None = None):
    def response(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if fail_at is not None and len(urls) == fail_at:
            raise httpx.ConnectError("spojení spadlo", request=request)
        name = str(request.url).rsplit("/", 1)[-1].removesuffix(".geojson")
        return httpx.Response(200, content=json.dumps(DATA[name]).encode())
    return httpx.AsyncClient(transport=httpx.MockTransport(response))


def download(client) -> dict:
    return asyncio.run(detailed_map.download_and_process(client))


result = download(server())
expected = [detailed_map.SOURCE.format(version=detailed_map.VERSION, file=s)
            for s, _, _ in detailed_map.FILES]
check(result.get("status") == "ok", f"the download runs ({result})")
check(urls == expected and all("/v5.1.2/" in a for a in urls),
      "it downloads only from the fixed addresses of the locked version v5.1.2")
state = detailed_map.state()
check(state.get("version") == "v5.1.2" and state.get("at") and state.get("tile_count", 0) > 0,
      f"the state after the download ({state})")
key = db.query_one("SELECT detail_level, x, y FROM detailed_map_tile ORDER BY detail_level, x, y")
content = detailed_map.tile(key["detail_level"], key["x"], key["y"]) if key else None
check(content is not None and isinstance(json.loads(content), dict), "a tile is JSON with layers")
check(detailed_map.tile(3, 0, 0) is None and detailed_map.tile(1, 999, 0) is None,
      "an unknown tile = None")

check(db.get_setting("detailed_map_format") == detailed_map.FORMAT and state.get("valid")
      and not state.get("outdated"), "the tiles' shape (the grid) is stored too, the map is valid")

print("--- stopping ---")
before = db.query_all("SELECT detail_level, x, y, content FROM detailed_map_tile ORDER BY detail_level, x, y")
urls.clear()
stops = iter([False, True])
result = asyncio.run(detailed_map.download_and_process(server(), stop=lambda: next(stops, True)))
after = db.query_all("SELECT detail_level, x, y, content FROM detailed_map_tile ORDER BY detail_level, x, y")
check(result.get("status") == "stopped" and len(urls) == 1
      and [dict(r) for r in after] == [dict(r) for r in before],
      f"stopping between files: the end, the old map unchanged ({result}, {len(urls)} files)")

before = db.query_all("SELECT detail_level, x, y, content FROM detailed_map_tile ORDER BY detail_level, x, y")
original_ceiling = detailed_map.MAX_BYTES
detailed_map.MAX_BYTES = 100
urls.clear()
result = download(server())
detailed_map.MAX_BYTES = original_ceiling
after = db.query_all("SELECT detail_level, x, y, content FROM detailed_map_tile ORDER BY detail_level, x, y")
check(result.get("status") == "error" and [dict(r) for r in after] == [dict(r) for r in before],
      f"a file above the ceiling: an error and the map unchanged ({result.get('message', '')[:60]})")
# The message names the file and the ceiling (the translation's placeholders {soubor} and {mb}). The review of step 8:
# a renamed format key would give a KeyError instead of the sentence and only "'file'" in the message.
check(".geojson" in result.get("message", "") and "MB" in result.get("message", ""),
      f"the message above the ceiling names the file and the ceiling ({result.get('message', '')[:80]})")

urls.clear()
result = download(server(fail_at=3))
after = db.query_all("SELECT detail_level, x, y, content FROM detailed_map_tile ORDER BY detail_level, x, y")
check(result.get("status") == "error" and [dict(r) for r in after] == [dict(r) for r in before],
      "an outage at the third file: the old map stays whole")
leftovers = list(Path(_tmp).rglob("*.stahuje"))
check(not leftovers, f"no unfinished file in data/ ({leftovers})")

print("--- backups and the conversion ---")
check("detailed_map_tile" in tasks.BACKED_UP_TABLES and "detailed_map_tile" in dbmigrate.TABLES,
      "the table is in the backups and in the SQLite -> PostgreSQL conversion")
if not db.database_config().is_postgres:
    backup = Path(_tmp) / "zaloha.db"
    tasks._backup_sqlite(db.database_config(), backup)
    with sqlite3.connect(backup) as connection:
        in_backup = connection.execute("SELECT COUNT(*) FROM detailed_map_tile").fetchone()[0]
    check(in_backup == len(before) > 0, f"the SQLite backup contains the tiles ({in_backup})")

detailed_map.delete()
check(db.query_value("SELECT COUNT(*) FROM detailed_map_tile") == 0
      and not detailed_map.state().get("version"), "Delete empties the table and the state")

print("--- the web ---")
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, geoip, scanner  # noqa: E402
from jellyscope.web import app  # noqa: E402

geoip.is_available = lambda: True
geoip.lookup = lambda address: {"lat": 50.08, "lon": 14.41, "city": "Prague",
                                "country": "Czechia", "code": "CZ"}
when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method, client, remote_address, bitrate)"
        " VALUES ('s1', 'u1', 'Jana', 'i1', 'Duna', 'Movie', ?, ?, 600, 0, 0, 0,"
        " 'DirectPlay', 'Web', '203.0.113.1', 8000000)", (when, when))
    conn.commit()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass")
urls.clear()
download(server())
row = db.query_one("SELECT detail_level, x, y FROM detailed_map_tile ORDER BY detail_level, x, y")
TILE_URL = f"/network/map/tiles/{row['detail_level']}/{row['x']}/{row['y']}"


def sign_in(k: TestClient, name: str, password: str) -> TestClient:
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


response = TestClient(app).get(TILE_URL, follow_redirects=False)
check(response.status_code in (303, 307) and "/login" in response.headers.get("location", ""),
      "without signing in a redirect to signing in, no data")
reader = sign_in(TestClient(app), "reader", "readerpass")
response = reader.get(TILE_URL)
check(response.status_code == 200 and isinstance(response.json(), dict) and response.json(),
      "a signed-in person gets the tile")
check(response.headers.get("cache-control") == "private, max-age=604800"
      and response.headers.get("etag"), "the cache headers")
check(reader.get(TILE_URL, headers={"If-None-Match": response.headers.get("etag", "")}).status_code == 304,
      "the same ETag = 304")
# A browser keeping a tile from before the database step 5 has it with the Czech layer names (pevnina, jezera, ...) -
# under the ETag and the address stamp of then (made of the download's time only): it gets the tile anew.
import hashlib  # noqa: E402

map_state = detailed_map.state()
old_tag = hashlib.sha256(f"{map_state['at']}|{row['detail_level']}|{row['x']}|{row['y']}".encode()).hexdigest()[:20]
again = reader.get(TILE_URL, headers={"If-None-Match": f'"{old_tag}"'})
check(again.status_code == 200 and again.json() and response.headers.get("etag") != f'"{old_tag}"',
      "the ETag of a tile from before the layers' English names = the tile again, not 304")
check(map_state["fingerprint"] != hashlib.sha256(f"{map_state['at']}|{detailed_map.FORMAT}".encode()).hexdigest()[:12]
      and map_state["valid"], "the tiles' address changes too, the map stays valid (no download)")
empty = reader.get("/network/map/tiles/1/0/0")
check(empty.status_code == 200 and empty.json() == {}, "a valid tile without data = an empty object")
bad = [a for a in ("3/0/0", "1/-1/0", "1/99999/0", "2/0/128", "x/0/0", "1/0/1e3")
       if reader.get(f"/network/map/tiles/{a}").status_code != 404]
check(not bad, f"nonsense coordinates = 404 ({bad})")
count = db.query_value("SELECT COUNT(*) FROM detailed_map_tile")
check(reader.post("/network/detailed-map", follow_redirects=False).status_code == 403
      and reader.post("/network/detailed-map/delete", follow_redirects=False).status_code == 403
      and db.query_value("SELECT COUNT(*) FROM detailed_map_tile") == count,
      "a reader neither downloads nor deletes (403)")

calls = []


async def fake_download(client=None, **_) -> dict:
    calls.append(1)
    return {"status": "ok", "tile_count": 1, "byte_count": 1}


original_download = detailed_map.download_and_process
detailed_map.download_and_process = fake_download
with TestClient(app) as admin:
    sign_in(admin, "admin", "longpassword")
    response = admin.post("/network/detailed-map", follow_redirects=False)
    check(response.status_code == 303 and "wait=task" in response.headers.get("location", ""),
          "the administrator starts the download, the page waits for the task")
    for _ in range(50):
        if db.query_value("SELECT status FROM scan_log WHERE kind = 'map'"
                          " ORDER BY id DESC LIMIT 1", default="") not in ("", "running"):
            break
        time.sleep(0.1)
    check(calls == [1] and db.query_value(
        "SELECT status FROM scan_log WHERE kind = 'map' ORDER BY id DESC LIMIT 1") == "done",
        "the task “map” ran and is in the tasks' record")
    original_running = scanner.is_scan_running
    substitute(scanner, "is_scan_running", lambda: True)
    admin.post("/network/detailed-map", follow_redirects=False)
    time.sleep(0.3)
    substitute(scanner, "is_scan_running", original_running)
    check(calls == [1] and "už běží" in admin.get("/network").text,
          "a second start during a running task is refused")
    network = admin.get("/network").text
    check("Stáhnout znovu" in network and "Smazat" in network and 'data-detailed="1"' in network,
          "the Map card: a downloaded detailed map, Stáhnout znovu, Smazat")
    check(re.search(r'data-detailed-version="[0-9a-f]{8,}"', network) is not None,
          "the map carries the detailed map's version (the browser then does not take tiles from the cache after a new download)")
    original_running = scanner.is_scan_running
    substitute(scanner, "is_scan_running", lambda: True)
    admin.post("/network/detailed-map/delete", follow_redirects=False)
    substitute(scanner, "is_scan_running", original_running)
    check(detailed_map.state()["version"] and "už běží" in admin.get("/network").text,
          "Delete during a running task is refused (the task would write the map again)")
    db.set_setting("detailed_map_format", "0:16:128")
    db.forget_settings()
    network = admin.get("/network").text
    check('data-detailed="0"' in network and "starším tvaru" in network
          and admin.get(TILE_URL).status_code == 404,
          "tiles in an older shape (another grid) are not shown and the card says to download the map again")
    db.set_setting("detailed_map_format", detailed_map.FORMAT)
    db.forget_settings()
    check(admin.post("/network/detailed-map/delete", follow_redirects=False).status_code == 303
          and not detailed_map.state()["version"], "the administrator deletes the detailed map")
    network = admin.get("/network").text
    check("Stáhnout podrobnou mapu" in network and 'data-detailed="0"' in network,
          "without the detailed map a button to download it")

    async def failed_download(client=None, **_) -> dict:
        return {"status": "error", "message": "spojení spadlo"}

    detailed_map.download_and_process = failed_download
    admin.post("/network/detailed-map", follow_redirects=False)
    for _ in range(50):
        if db.query_value("SELECT status FROM scan_log WHERE kind = 'map'"
                          " ORDER BY id DESC LIMIT 1", default="") == "error":
            break
        time.sleep(0.1)
    network = admin.get("/network").text
    check("Poslední stažení podrobné mapy se nepovedlo" in network and "spojení spadlo" in network,
          "the administrator sees a failed download on the card with the reason")
    db.set_setting("detailed_map_at", "2999-01-01 00:00:00")
    db.forget_settings()
    check("se nepovedlo" not in admin.get("/network").text,
          "after a later successful download the old error goes away")
    detailed_map.delete()
detailed_map.download_and_process = original_download
template = (PROJECT / "jellyscope" / "templates" / "_card_map.html").read_text(encoding="utf-8")
map_js = (PROJECT / "jellyscope" / "static" / "map.js").read_text(encoding="utf-8")
check("?v=" in map_js and "dataset.detailedVersion" in map_js, "the tiles are downloaded with the version in the address")
check("ResizeObserver" in map_js, "the map recomputes when its size changes and when a hidden card shows")
check("jediná chvíle, kdy aplikace sáhne jinam" not in template,
      "the card does not claim GeoLite2 is the only connection outside Jellyfin")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
