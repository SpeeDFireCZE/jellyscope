# -*- coding: utf-8 -*-
r"""Podrobná offline mapa: Natural Earth 1:10M rozřezaná na dlaždice.

Správce ji stáhne na kliknutí, aplikace ji rozřeže do dlaždic a uloží do
hlavní databáze (zálohuje se s ní). Hlídá se tu:

* zpracování: dlaždice obou podrobností, jen konečná čísla (i u polygonu
  do -90°), hrubší podrobnost zjednodušená;
* stahování: jen pevné adresy zamčené verze, strop velikosti, a když
  stahování spadne uprostřed, stará mapa zůstane celá;
* tabulka je v zálohách i v převodu SQLite -> PostgreSQL.

Internet není potřeba - server se podvrhuje přes httpx.MockTransport.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_mapa_podrobna.py
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

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "podrobna.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402

from jellyscope import db, dbmigrate, mapa_podrobna, tasks  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


db.init_db()


def polygon(*prstence):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [list(p) for p in prstence]}}


def cara(body):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString", "coordinates": body}}


def sbirka(*prvky):
    return {"type": "FeatureCollection", "features": list(prvky)}


# Ctverec pres hranu dlazdic (delka 0-40 = x 180-220, hrana podrobnosti 1
# je na 202,5), kus Antarktidy az k jiznimu polu a vlnita cara.
PEVNINA = sbirka(
    polygon([[0, 0], [40, 0], [40, 30], [0, 30], [0, 0]]),
    polygon([[10, -90], [20, -90], [20, -80], [10, -80], [10, -90]]),
)
VLNY = sbirka(cara([[i * 0.05, 45 + 0.001 * (i % 2)] for i in range(400)]))
JEZERO = sbirka(polygon([[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]))

print("--- zpracování ---")
dlazdice: dict = {}
mapa_podrobna.zpracuj(PEVNINA, "pevnina", "plocha", dlazdice)
mapa_podrobna.zpracuj(JEZERO, "jezera", "plocha", dlazdice)
mapa_podrobna.zpracuj(VLNY, "reky", "cara", dlazdice)
podrobnosti = {klic[0] for klic in dlazdice}
check(podrobnosti == {1, 2}, f"dlaždice obou podrobností ({podrobnosti})")
check(mapa_podrobna.PODROBNOSTI[1]["n"] == 32 and mapa_podrobna.PODROBNOSTI[2]["n"] == 128,
      "síť 32 × 32 pro hrubší podrobnost (menší dlaždice), 128 × 128 pro plnou")
velikost1 = 360 / mapa_podrobna.PODROBNOSTI[1]["n"]
sloupce = {klic[1] for klic in dlazdice if klic[0] == 1 and klic[2] == int(170 / velikost1)
           and "pevnina" in dlazdice[klic]}
check(sloupce == set(range(int(180 / velikost1), int(220 / velikost1) + 1)),
      f"čtverec přes hranu dlaždic je ve všech dlaždicích, přes které vede ({sorted(sloupce)})")
drobny: dict = {}
# 0,05 jednotky: zjednodušení (tolerance 0,04) ho nesmaže, při 8-127× je to
# ale pár pixelů - v hrubší podrobnosti jen zbytečně nafukuje dlaždici.
mapa_podrobna.zpracuj(sbirka(polygon([[50, 50], [50.05, 50], [50.05, 50.032], [50, 50.032],
                                      [50, 50]])), "mesta", "plocha", drobny)
check({klic[0] for klic in drobny} == {2},
      "útvar menší než pár pixelů není v hrubší podrobnosti, jen v plné")
kratka: dict = {}
mapa_podrobna.zpracuj(sbirka(cara([[60, 10], [60.05, 10]])), "reky", "cara", kratka)
check({klic[0] for klic in kratka} == {1, 2},
      "krátký úsek čáry v hrubší podrobnosti zůstane - řeky a hranice bez mezer")
cisla = [c for vrstvy in dlazdice.values() for cesty in vrstvy.values()
         for cesta in cesty for c in re.findall(r"[-\d.eEinfaINF]+", cesta)]
check(cisla and all(re.fullmatch(r"-?\d+(\.\d+)?", c) for c in cisla),
      "v cestách jsou jen konečná čísla (i u polygonu k jižnímu pólu)")


def pocet_bodu(podrobnost: int) -> int:
    return sum(cesta.count("L") + 1 for (p, _, _), vrstvy in dlazdice.items() if p == podrobnost
               for cesta in vrstvy.get("reky", []))


check(0 < pocet_bodu(1) < pocet_bodu(2), f"podrobnost 1 je zjednodušená ({pocet_bodu(1)} < {pocet_bodu(2)})")

print("--- stahování ---")
DATA = {"ne_10m_land": PEVNINA, "ne_10m_lakes": JEZERO, "ne_10m_urban_areas": JEZERO,
        "ne_10m_rivers_lake_centerlines": VLNY, "ne_10m_admin_0_boundary_lines_land": VLNY,
        "ne_10m_admin_1_states_provinces_lines": VLNY}
adresy: list[str] = []


def server(chyba_u: int | None = None):
    def odpoved(pozadavek: httpx.Request) -> httpx.Response:
        adresy.append(str(pozadavek.url))
        if chyba_u is not None and len(adresy) == chyba_u:
            raise httpx.ConnectError("spojení spadlo", request=pozadavek)
        jmeno = str(pozadavek.url).rsplit("/", 1)[-1].removesuffix(".geojson")
        return httpx.Response(200, content=json.dumps(DATA[jmeno]).encode())
    return httpx.AsyncClient(transport=httpx.MockTransport(odpoved))


def stahni(klient) -> dict:
    return asyncio.run(mapa_podrobna.stahni_a_zpracuj(klient))


vysledek = stahni(server())
ocekavane = [mapa_podrobna.ZDROJ.format(verze=mapa_podrobna.VERZE, soubor=s)
             for s, _, _ in mapa_podrobna.SOUBORY]
check(vysledek.get("status") == "ok", f"stažení proběhne ({vysledek})")
check(adresy == ocekavane and all("/v5.1.2/" in a for a in adresy),
      "stahuje se jen z pevných adres zamčené verze v5.1.2")
stav = mapa_podrobna.stav()
check(stav.get("verze") == "v5.1.2" and stav.get("kdy") and stav.get("dlazdic", 0) > 0,
      f"stav po stažení ({stav})")
klic = db.query_one("SELECT podrobnost, x, y FROM mapa_dlazdice ORDER BY podrobnost, x, y")
obsah = mapa_podrobna.dlazdice(klic["podrobnost"], klic["x"], klic["y"]) if klic else None
check(obsah is not None and isinstance(json.loads(obsah), dict), "dlaždice je JSON s vrstvami")
check(mapa_podrobna.dlazdice(3, 0, 0) is None and mapa_podrobna.dlazdice(1, 999, 0) is None,
      "neznámá dlaždice = None")

check(db.get_setting("mapa_podrobna_format") == mapa_podrobna.FORMAT and stav.get("platna")
      and not stav.get("zastarala"), "uloží se i tvar dlaždic (síť), mapa je platná")

print("--- zastavení ---")
pred = db.query_all("SELECT podrobnost, x, y, obsah FROM mapa_dlazdice ORDER BY podrobnost, x, y")
adresy.clear()
zastavit = iter([False, True])
vysledek = asyncio.run(mapa_podrobna.stahni_a_zpracuj(server(), zastavit=lambda: next(zastavit, True)))
po = db.query_all("SELECT podrobnost, x, y, obsah FROM mapa_dlazdice ORDER BY podrobnost, x, y")
check(vysledek.get("status") == "stopped" and len(adresy) == 1
      and [dict(r) for r in po] == [dict(r) for r in pred],
      f"zastavení mezi soubory: konec, stará mapa beze změny ({vysledek}, {len(adresy)} souborů)")

pred = db.query_all("SELECT podrobnost, x, y, obsah FROM mapa_dlazdice ORDER BY podrobnost, x, y")
puvodni_strop = mapa_podrobna.STROP_BAJTU
mapa_podrobna.STROP_BAJTU = 100
adresy.clear()
vysledek = stahni(server())
mapa_podrobna.STROP_BAJTU = puvodni_strop
po = db.query_all("SELECT podrobnost, x, y, obsah FROM mapa_dlazdice ORDER BY podrobnost, x, y")
check(vysledek.get("status") == "error" and [dict(r) for r in po] == [dict(r) for r in pred],
      f"soubor nad stropem: chyba a mapa beze změny ({vysledek.get('message', '')[:60]})")

adresy.clear()
vysledek = stahni(server(chyba_u=3))
po = db.query_all("SELECT podrobnost, x, y, obsah FROM mapa_dlazdice ORDER BY podrobnost, x, y")
check(vysledek.get("status") == "error" and [dict(r) for r in po] == [dict(r) for r in pred],
      "výpadek u třetího souboru: stará mapa zůstane celá")
zbytky = list(Path(_tmp).rglob("*.stahuje"))
check(not zbytky, f"žádný rozdělaný soubor v data/ ({zbytky})")

print("--- zálohy a převod ---")
check("mapa_dlazdice" in tasks.ZALOHOVANE_TABULKY and "mapa_dlazdice" in dbmigrate.TABLES,
      "tabulka je v zálohách i v převodu SQLite -> PostgreSQL")
if not db.database_config().is_postgres:
    zaloha = Path(_tmp) / "zaloha.db"
    tasks._backup_sqlite(db.database_config(), zaloha)
    with sqlite3.connect(zaloha) as spojeni:
        v_zaloze = spojeni.execute("SELECT COUNT(*) FROM mapa_dlazdice").fetchone()[0]
    check(v_zaloze == len(pred) > 0, f"záloha SQLite dlaždice obsahuje ({v_zaloze})")

mapa_podrobna.smaz()
check(db.query_value("SELECT COUNT(*) FROM mapa_dlazdice") == 0
      and not mapa_podrobna.stav().get("verze"), "Smazat vyprázdní tabulku i stav")

print("--- web ---")
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, geoip, scanner  # noqa: E402
from jellyscope.web import app  # noqa: E402

geoip.je_k_dispozici = lambda: True
geoip.najdi = lambda adresa: {"lat": 50.08, "lon": 14.41, "mesto": "Prague",
                              "zeme": "Czechia", "kod": "CZ"}
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
adresy.clear()
stahni(server())
radek = db.query_one("SELECT podrobnost, x, y FROM mapa_dlazdice ORDER BY podrobnost, x, y")
ADRESA = f"/network/mapa/dlazdice/{radek['podrobnost']}/{radek['x']}/{radek['y']}"


def prihlas(k: TestClient, jmeno: str, heslo: str) -> TestClient:
    k.post("/login", data={"username": jmeno, "password": heslo, "zpusob": "mistni"},
           follow_redirects=False)
    return k


odpoved = TestClient(app).get(ADRESA, follow_redirects=False)
check(odpoved.status_code in (303, 307) and "/login" in odpoved.headers.get("location", ""),
      "bez přihlášení přesměrování na přihlášení, žádná data")
ctenar = prihlas(TestClient(app), "ctenar", "ctenarheslo")
odpoved = ctenar.get(ADRESA)
check(odpoved.status_code == 200 and isinstance(odpoved.json(), dict) and odpoved.json(),
      "přihlášený dostane dlaždici")
check(odpoved.headers.get("cache-control") == "private, max-age=604800"
      and odpoved.headers.get("etag"), "hlavičky mezipaměti")
check(ctenar.get(ADRESA, headers={"If-None-Match": odpoved.headers.get("etag", "")}).status_code == 304,
      "stejný ETag = 304")
prazdna = ctenar.get("/network/mapa/dlazdice/1/0/0")
check(prazdna.status_code == 200 and prazdna.json() == {}, "platná dlaždice bez dat = prázdný objekt")
spatne = [a for a in ("3/0/0", "1/-1/0", "1/99999/0", "2/0/128", "x/0/0", "1/0/1e3")
          if ctenar.get(f"/network/mapa/dlazdice/{a}").status_code != 404]
check(not spatne, f"nesmyslné souřadnice = 404 ({spatne})")
pocet = db.query_value("SELECT COUNT(*) FROM mapa_dlazdice")
check(ctenar.post("/network/mapa-podrobna", follow_redirects=False).status_code == 403
      and ctenar.post("/network/mapa-podrobna/smazat", follow_redirects=False).status_code == 403
      and db.query_value("SELECT COUNT(*) FROM mapa_dlazdice") == pocet,
      "čtenář nestáhne ani nesmaže (403)")

volani = []


async def podvrzene_stazeni(klient=None, **_) -> dict:
    volani.append(1)
    return {"status": "ok", "dlazdic": 1, "bajtu": 1}


puvodni_stazeni = mapa_podrobna.stahni_a_zpracuj
mapa_podrobna.stahni_a_zpracuj = podvrzene_stazeni
with TestClient(app) as spravce:
    prihlas(spravce, "spravce", "dlouheheslo")
    odpoved = spravce.post("/network/mapa-podrobna", follow_redirects=False)
    check(odpoved.status_code == 303 and "wait=task" in odpoved.headers.get("location", ""),
          "správce spustí stahování, stránka počká na úlohu")
    for _ in range(50):
        if db.query_value("SELECT status FROM scan_log WHERE kind = 'mapa'"
                          " ORDER BY id DESC LIMIT 1", default="") not in ("", "running"):
            break
        time.sleep(0.1)
    check(volani == [1] and db.query_value(
        "SELECT status FROM scan_log WHERE kind = 'mapa' ORDER BY id DESC LIMIT 1") == "done",
        "úloha „mapa“ proběhla a je v záznamu úloh")
    puvodni_bezi = scanner.is_scan_running
    scanner.is_scan_running = lambda: True
    spravce.post("/network/mapa-podrobna", follow_redirects=False)
    time.sleep(0.3)
    scanner.is_scan_running = puvodni_bezi
    check(volani == [1] and "už běží" in spravce.get("/network").text,
          "druhé spuštění během běžící úlohy se odmítne")
    sit = spravce.get("/network").text
    check("Stáhnout znovu" in sit and "Smazat" in sit and 'data-podrobna="1"' in sit,
          "karta Mapa: stažená podrobná mapa, Stáhnout znovu, Smazat")
    check(re.search(r'data-podrobna-verze="[0-9a-f]{8,}"', sit) is not None,
          "mapa nese verzi podrobné mapy (prohlížeč pak nebere dlaždice z mezipaměti po novém stažení)")
    puvodni_bezi = scanner.is_scan_running
    scanner.is_scan_running = lambda: True
    spravce.post("/network/mapa-podrobna/smazat", follow_redirects=False)
    scanner.is_scan_running = puvodni_bezi
    check(mapa_podrobna.stav()["verze"] and "už běží" in spravce.get("/network").text,
          "Smazat během běžící úlohy se odmítne (úloha by mapu zase zapsala)")
    db.set_setting("mapa_podrobna_format", "0:16:128")
    db.forget_settings()
    sit = spravce.get("/network").text
    check('data-podrobna="0"' in sit and "starším tvaru" in sit
          and spravce.get(ADRESA).status_code == 404,
          "dlaždice ve starším tvaru (jiná síť) se neukazují a karta řekne, ať se mapa stáhne znovu")
    db.set_setting("mapa_podrobna_format", mapa_podrobna.FORMAT)
    db.forget_settings()
    check(spravce.post("/network/mapa-podrobna/smazat", follow_redirects=False).status_code == 303
          and not mapa_podrobna.stav()["verze"], "správce podrobnou mapu smaže")
    sit = spravce.get("/network").text
    check("Stáhnout podrobnou mapu" in sit and 'data-podrobna="0"' in sit,
          "bez podrobné mapy tlačítko ke stažení")

    async def nepovedene_stazeni(klient=None, **_) -> dict:
        return {"status": "error", "message": "spojení spadlo"}

    mapa_podrobna.stahni_a_zpracuj = nepovedene_stazeni
    spravce.post("/network/mapa-podrobna", follow_redirects=False)
    for _ in range(50):
        if db.query_value("SELECT status FROM scan_log WHERE kind = 'mapa'"
                          " ORDER BY id DESC LIMIT 1", default="") == "error":
            break
        time.sleep(0.1)
    sit = spravce.get("/network").text
    check("Poslední stažení podrobné mapy se nepovedlo" in sit and "spojení spadlo" in sit,
          "nepovedené stažení správce na kartě uvidí i s důvodem")
    db.set_setting("mapa_podrobna_kdy", "2999-01-01 00:00:00")
    db.forget_settings()
    check("se nepovedlo" not in spravce.get("/network").text,
          "po pozdějším úspěšném stažení stará chyba zmizí")
    mapa_podrobna.smaz()
mapa_podrobna.stahni_a_zpracuj = puvodni_stazeni
sablona = (PROJECT / "jellyscope" / "templates" / "_sekce_mapa.html").read_text(encoding="utf-8")
mapa_js = (PROJECT / "jellyscope" / "static" / "mapa.js").read_text(encoding="utf-8")
check("?v=" in mapa_js and "dataset.podrobnaVerze" in mapa_js, "dlaždice se stahují s verzí v adrese")
check("ResizeObserver" in mapa_js, "mapa se přepočítá při změně velikosti a když se ukáže skrytá karta")
check("jediná chvíle, kdy aplikace sáhne jinam" not in sablona,
      "karta netvrdí, že GeoLite2 je jediné spojení mimo Jellyfin")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
