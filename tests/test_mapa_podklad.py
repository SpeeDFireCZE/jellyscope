# -*- coding: utf-8 -*-
r"""Podklad mapy z OpenStreetMap - volitelný, výchozí vypnutý.

Když ho správce zapne, prohlížeč každého, kdo mapu otevře, stahuje
dlaždice z tile.openstreetmap.org. Hlídá se tu:

* výchozí je kreslená mapa a bezpečnostní hlavička (CSP) OSM nepouští;
* po zapnutí CSP povolí právě OSM, stránka má podpis s odkazem
  a mapa dostane data-podklad="osm";
* nesmyslná hodnota spadne na kreslenou, čtenář volbu nezmění.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_mapa_podklad.py
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

from jellyscope import accounts, db, geoip, web_zaklad  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


geoip.je_k_dispozici = lambda: True
geoip.najdi = lambda adresa: {"lat": 50.08, "lon": 14.41, "mesto": "Prague",
                              "zeme": "Czechia", "kod": "CZ"}
db.init_db()
kdy = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method, client, remote_address, bitrate)"
        " VALUES ('s1', 'u1', 'Jana', 'i1', 'Duna', 'Movie', ?, ?, 600, 0, 0, 0,"
        " 'DirectPlay', 'Web', '203.0.113.1', 8000000)", (kdy, kdy))
    conn.commit()
accounts.create("spravce", "dlouheheslo", is_admin=True)
accounts.create("ctenar", "ctenarheslo")


def prihlas(jmeno: str, heslo: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


def uloz(k: TestClient, hodnota: str):
    return k.post("/settings/interface",
                  data={"ui_max_streams": "10", "ui_max_viewers": "10",
                        "ui_map_zoom": "click", "ui_map_podklad": hodnota},
                  follow_redirects=False)


spravce = prihlas("spravce", "dlouheheslo")
ctenar = prihlas("ctenar", "ctenarheslo")
OSM = "https://tile.openstreetmap.org"
PODPIS = "https://www.openstreetmap.org/copyright"

print("--- výchozí stav ---")
check(web_zaklad._podklad_mapy() == "kresleny", "výchozí podklad je kreslená mapa")
sit = spravce.get("/network")
check(sit.status_code == 200 and 'data-podklad="kresleny"' in sit.text,
      "stránka Síť: mapa s kresleným podkladem")
check(OSM not in sit.headers.get("content-security-policy", ""),
      "CSP bez OpenStreetMap, dokud není zapnutý")
check(PODPIS not in sit.text, "bez OSM ani podpis OSM")
check(sit.headers.get("referrer-policy") == "same-origin", "bez OSM Referer ven nejde (same-origin)")

print("--- zapnutí ---")
check(uloz(spravce, "osm").status_code == 303 and web_zaklad._podklad_mapy() == "osm",
      "správce zapne OSM")
sit = spravce.get("/network")
check(f"img-src 'self' {OSM}" in sit.headers.get("content-security-policy", ""),
      f"CSP pustí obrázky z OSM ({sit.headers.get('content-security-policy', '')[:80]})")
check('data-podklad="osm"' in sit.text and PODPIS in sit.text,
      "mapa má podklad osm a podpis s odkazem")
# Pojistka pro prohlížeče, které referrerpolicy u SVG <image> neznají:
# ven jde jen adresa serveru (cesty s id ne), a to UI u volby přiznává.
check(sit.headers.get("referrer-policy") == "strict-origin-when-cross-origin",
      f"s OSM jde ven jen adresa serveru ({sit.headers.get('referrer-policy')})")

print("--- nesmysl a čtenář ---")
uloz(spravce, "vymysl")
check(web_zaklad._podklad_mapy() == "kresleny", "nesmysl spadne na kreslenou mapu")
uloz(spravce, "osm")
check(uloz(ctenar, "kresleny").status_code == 403 and web_zaklad._podklad_mapy() == "osm",
      "čtenář volbu nezmění (403)")
nastaveni = spravce.get("/settings?section=interface").text
check('name="ui_map_podklad"' in nastaveni and "OpenStreetMap" in nastaveni,
      "volba je v Nastavení → Rozhraní")

print("--- výpadek databáze ---")
from jellyscope import web  # noqa: E402

puvodni = web._podklad_mapy


def rozbita() -> str:
    raise RuntimeError("databáze neodpovídá")


web._podklad_mapy = rozbita
zdravi = TestClient(app).get("/health")
web._podklad_mapy = puvodni
check(zdravi.status_code == 200 and OSM not in zdravi.headers.get("content-security-policy", "x"),
      f"když nastavení nejde přečíst, /health běží s přísnou CSP ({zdravi.status_code})")

mapa_js = (PROJECT / "jellyscope" / "static" / "mapa.js").read_text(encoding="utf-8")
check("strict-origin" in mapa_js and "tile.openstreetmap.org" in mapa_js,
      "mapa.js stahuje dlaždice OSM s referrerpolicy strict-origin")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
