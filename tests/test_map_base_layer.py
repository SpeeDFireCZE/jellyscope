# -*- coding: utf-8 -*-
r"""The map's base layer from OpenStreetMap - optional, off by default.

When the administrator switches it on, the browser of everyone who opens the map downloads
tiles from tile.openstreetmap.org. Watched here:

* the default is the drawn map and the security header (CSP) does not let OSM in;
* after switching it on the CSP allows exactly OSM, the page has the attribution with a link
  and the map gets data-basemap="osm";
* a nonsense value falls back to the drawn one, a reader does not change the choice.

Run:
    .venv/bin/python tests/test_map_base_layer.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "podklad.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, geoip  # noqa: E402
from jellyscope.web import base as web_base  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


geoip.is_available = lambda: True
geoip.lookup = lambda address: {"lat": 50.08, "lon": 14.41, "city": "Prague",
                                "country": "Czechia", "code": "CZ"}
db.init_db()
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


def sign_in(name: str, password: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


def save(k: TestClient, value: str):
    return k.post("/settings/interface",
                  data={"ui_max_streams": "10", "ui_max_viewers": "10",
                        "ui_map_zoom": "click", "ui_map_base": value},
                  follow_redirects=False)


admin = sign_in("admin", "longpassword")
reader = sign_in("reader", "readerpass")
OSM = "https://tile.openstreetmap.org"
ATTRIBUTION = "https://www.openstreetmap.org/copyright"

print("--- the default state ---")
check(web_base._map_basemap() == "drawn", "the default base layer is the drawn map")
network = admin.get("/network")
check(network.status_code == 200 and 'data-basemap="drawn"' in network.text,
      "the Network page: a map with the drawn base layer")
check(OSM not in network.headers.get("content-security-policy", ""),
      "a CSP without OpenStreetMap while it is not switched on")
check(ATTRIBUTION not in network.text, "without OSM no OSM attribution either")
check(network.headers.get("referrer-policy") == "same-origin", "without OSM no Referer goes out (same-origin)")

print("--- switching on ---")
check(save(admin, "osm").status_code == 303 and web_base._map_basemap() == "osm",
      "the administrator switches OSM on")
network = admin.get("/network")
check(f"img-src 'self' {OSM}" in network.headers.get("content-security-policy", ""),
      f"the CSP lets images from OSM in ({network.headers.get('content-security-policy', '')[:80]})")
check('data-basemap="osm"' in network.text and ATTRIBUTION in network.text,
      "the map has the osm base layer and the attribution with a link")
# A safeguard for browsers that do not know referrerpolicy on an SVG <image>:
# only the server's address goes out (paths with ids not), and the UI admits it by the choice.
check(network.headers.get("referrer-policy") == "strict-origin-when-cross-origin",
      f"with OSM only the server's address goes out ({network.headers.get('referrer-policy')})")

print("--- nonsense and a reader ---")
save(admin, "vymysl")
check(web_base._map_basemap() == "drawn", "nonsense falls back to the drawn map")
save(admin, "osm")
check(save(reader, "drawn").status_code == 403 and web_base._map_basemap() == "osm",
      "a reader does not change the choice (403)")
settings_page = admin.get("/settings?section=interface").text
check('name="ui_map_base"' in settings_page and "OpenStreetMap" in settings_page,
      "the choice is in Settings → Interface")

print("--- a database outage ---")
from jellyscope import web  # noqa: E402

original = web._map_basemap


def broken() -> str:
    raise RuntimeError("databáze neodpovídá")


web._map_basemap = broken
health = TestClient(app).get("/health")
web._map_basemap = original
check(health.status_code == 200 and OSM not in health.headers.get("content-security-policy", "x"),
      f"when the setting cannot be read, /health runs with the strict CSP ({health.status_code})")

map_js = (PROJECT / "jellyscope" / "static" / "map.js").read_text(encoding="utf-8")
# The whole piece of code that puts a tile's address together - not just the server's name
# (CodeQL would take that as an incomplete check of the address, py/incomplete-url-substring-sanitization).
check("strict-origin" in map_js and '"https://tile.openstreetmap.org/" + key + ".png"' in map_js,
      "map.js downloads OSM tiles with referrerpolicy strict-origin")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
