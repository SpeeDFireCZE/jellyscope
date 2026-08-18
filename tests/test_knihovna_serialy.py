# -*- coding: utf-8 -*-
"""Seriál je v knihovně jeden řádek, ne sto.

V tabulce `items` je každá epizoda samostatný řádek — tak je posílá
Jellyfin a tak je potřebujeme pro statistiky. V seznamu knihovny je to ale
k ničemu: seriál o pěti řadách zabere sto dlaždic a všechno ostatní v nich
zanikne. Navíc se pak stránkuje po dílech, takže „strana 1 z 20" nic
neříká o tom, kolik titulů člověk má.

Řádky se proto seskupují podle seriálu. Rozpad na řady a díly je až
v detailu seriálu, kde ho člověk hledá.

Klíč skupiny je `COALESCE(series_id, id)`: film žádné `series_id` nemá,
takže tvoří skupinu sám za sebe a **jeden dotaz obslouží obojí**. Kdyby
to byly dva dotazy, časem by se rozešly.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "knihovna.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, stats  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at)"
                 " VALUES ('lib', 'Seriály', 'tvshows', ?)", (db.utcnow(),))

    # Dva filmy.
    for ident, nazev, velikost in (("film-1", "Matrix", 4_000_000_000),
                                   ("film-2", "Duna", 12_000_000_000)):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, size_bytes,
                                  width, height, video_codec, is_missing, synced_at)
               VALUES (?,?,'Movie','lib',?,1920,1080,'h264',0,?)""",
            (ident, nazev, velikost, db.utcnow()),
        )

    # Seriál: dvě řady po třech dílech.
    for rada in (1, 2):
        for dil in (1, 2, 3):
            conn.execute(
                """INSERT INTO items (id, name, type, library_id, series_id,
                                      series_name, season_name, index_number,
                                      parent_index_number, size_bytes, width, height,
                                      video_codec, is_missing, synced_at)
                   VALUES (?,?,'Episode','lib','serial-1','Kancelář',?,?,?,
                           1000000000,1920,1080,'hevc',0,?)""",
                (f"ep-{rada}-{dil}", f"{rada}x{dil:02d} Díl", f"Řada {rada}",
                 dil, rada, db.utcnow()),
            )

    # Pár přehrání, ať se sčítají do řádku seriálu.
    for i, ident in enumerate(("ep-1-1", "ep-1-2", "ep-2-1", "film-1")):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', ?, ?, ?, 1800, 0)""",
            (f"relace-{i}", ident, db.utcnow(), db.utcnow()),
        )


print("--- seznam knihovny ---")
check(db.query_value("SELECT COUNT(*) FROM items") == 8, "v databázi je 8 položek")
check(stats.library_rows_count() == 3,
      f"ale seznam má tři řádky: dva filmy a jeden seriál "
      f"({stats.library_rows_count()})")

radky = {r["name"]: r for r in stats.library_rows(50, 0)}
check(set(radky) == {"Matrix", "Duna", "Kancelář"}, f"a jsou to ty správné: {set(radky)}")

serial = radky["Kancelář"]
check(serial["is_series"] == 1, "seriál je označený jako seriál")
check(serial["id"] == "serial-1", f"a jeho id je id seriálu ({serial['id']})")
check(serial["episode_count"] == 6, f"šest dílů ({serial['episode_count']})")
check(serial["season_count"] == 2, f"dvě řady ({serial['season_count']})")
check(serial["size_bytes"] == 6_000_000_000,
      f"velikost je součet dílů ({serial['size_bytes']})")
check(serial["plays"] == 3, f"a přehrání taky ({serial['plays']})")

film = radky["Duna"]
check(film["is_series"] == 0, "film není seriál")
check(film["episode_count"] == 1, "a je sám za sebe")
check(film["size_bytes"] == 12_000_000_000, "s vlastní velikostí")


print()
print("--- řazení a stránkování pracují se skupinami ---")
# Duna 12 GB, seriál 6x1 GB = 6 GB, Matrix 4 GB. Kdyby se seriál řadil
# podle jednoho dílu (1 GB), spadl by na konec - takhle je uprostřed.
podle_velikosti = [r["name"] for r in stats.library_rows(50, 0, sort="size")]
check(podle_velikosti == ["Duna", "Kancelář", "Matrix"],
      f"seriál se řadí podle součtu dílů, ne podle jednoho ({podle_velikosti})")

podle_jmena = [r["name"] for r in stats.library_rows(50, 0, sort="name")]
check(podle_jmena == sorted(podle_jmena), f"podle názvu: {podle_jmena}")

prvni = stats.library_rows(2, 0, sort="name")
druha = stats.library_rows(2, 2, sort="name")
check(len(prvni) == 2 and len(druha) == 1, "stránkuje se po skupinách")
check(not ({r["id"] for r in prvni} & {r["id"] for r in druha}),
      "a stránky se nepřekrývají")

check(stats.library_rows_count(search="Kancel") == 1, "hledání najde seriál")
check(stats.library_rows_count(search="Matr") == 1, "i film")


print()
print("--- detail seriálu ---")
detail = stats.series_detail("serial-1")
check(detail["name"] == "Kancelář", "název sedí")
check(detail["season_count"] == 2 and detail["episode_count"] == 6, "dvě řady, šest dílů")
check([s["name"] for s in detail["seasons"]] == ["Řada 1", "Řada 2"],
      f"řady jsou seřazené: {[s['name'] for s in detail['seasons']]}")
check([e["index_number"] for e in detail["seasons"][0]["episodes"]] == [1, 2, 3],
      "díly uvnitř řady taky")
check(detail["seasons"][0]["plays"] == 2, "přehrání se sčítají po řadách")
check(stats.series_detail("neexistuje") == {}, "neznámý seriál vrátí prázdno")


print()
print("--- přes rozhraní ---")
klient = TestClient(app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})

html = klient.get("/library/lib?tab=media").text
karty = re.findall(r'<a class="media-card[^"]*"\s+href="([^"]+)"', html)
check(len(karty) == 3, f"na stránce jsou tři dlaždice ({len(karty)})")
check("/series/serial-1" in karty, "seriál vede na svůj detail")
check("/item/film-1" in karty and "/item/film-2" in karty, "filmy na detail položky")
check(not any(k.startswith("/item/ep-") for k in karty),
      "jednotlivé díly v seznamu nejsou")

odpoved = klient.get("/series/serial-1")
check(odpoved.status_code == 200, "detail seriálu se otevře")
check(odpoved.text.count("<details") == 2, "má dvě rozbalovací řady")
check(odpoved.text.count('href="/item/ep-') == 6, "a šest dílů s prokliky")
check("Kancelář" in odpoved.text, "s názvem seriálu")

check(klient.get("/series/neexistuje").status_code == 404, "neznámý seriál je 404")

# Z dílu se dá vrátit na seriál.
detail_dilu = klient.get("/item/ep-1-1").text
check('href="/series/serial-1"' in detail_dilu, "z dílu vede odkaz zpět na seriál")


print()
print("--- chybějící technická data se dopočítají rovnou odsud ---")
# Hláška u knihovny dřív jen odkazovala do Nastavení a analýzu bylo nutné
# spustit pro celou knihovnu. Tlačítko spustí jen chybějící soubory
# a jen v téhle knihovně.
check(stats.library_overview("lib")["without_tech"] == 8,
      "výchozí stav: technická data nemá žádný soubor")

db.set_setting("tech_source", "jellyfin")
prehled = klient.get("/library/lib").text
check('action="/settings/scan"' not in prehled,
      "při zdroji Jellyfin se tlačítko nenabízí - data se berou při synchronizaci")
check("Spusť analýzu v Nastavení" in prehled, "zůstane původní věta")

db.set_setting("tech_source", "ffprobe")
prehled = klient.get("/library/lib").text
check('action="/settings/scan"' in prehled, "při ffprobe je tlačítko na stránce")
check('name="library_id" value="lib"' in prehled, "a nese id téhle knihovny")
check('name="mode" value="missing"' in prehled, "spouští jen chybějící soubory")

# Přesměrování zpět na knihovnu. Zdroj necháme na Jellyfinu, ať se
# analýza v testu doopravdy nerozjede - zajímá nás jen, kam to vrací.
db.set_setting("tech_source", "jellyfin")
odpoved = klient.post("/settings/scan", data={"mode": "missing", "library_id": "lib"},
                      follow_redirects=False)
check(odpoved.headers["location"].startswith("/library/lib"),
      f"po spuštění se vrátí na knihovnu ({odpoved.headers['location']})")

# Cizí hodnota z formuláře se do adresy přesměrování dostat nesmí.
odpoved = klient.post("/settings/scan",
                      data={"mode": "missing", "library_id": "https://cizi.example"},
                      follow_redirects=False)
check(odpoved.headers["location"] == "/settings?section=tasks",
      f"neznámá knihovna vede zpátky do Nastavení ({odpoved.headers['location']})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
