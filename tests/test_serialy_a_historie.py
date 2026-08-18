# -*- coding: utf-8 -*-
"""Identita epizody, sjednocování seriálů ve statistikách a úklid historie.

Jedna chyba stála za velkou částí toho, co se v aplikaci tvářilo špatně,
a stojí za to ji popsat pořádně:

    `tmdb_id_of()` vrací u epizody id **seriálu** - epizoda sama v TMDB
    obvykle žádné nemá. Slučování překódovaných souborů ale bralo tohle
    id jako identitu položky, takže považovalo každé dva díly téhož
    seriálu za tentýž soubor. Při každém skenu proto slilo historii všech
    dílů na jediný.

Projevilo se to na místech, která spolu na první pohled nesouvisejí:
ve statistikách vypadalo, že divák viděl jednu epizodu dvacetkrát;
"sledovaných titulů" bylo mnohonásobně méně; a prokliky mířily na položky,
které slučování mezitím smazalo, takže končily na 404.

Dál se tu testuje sjednocování seriálů v přehledech (nejsledovanější
tituly, překódované soubory), počítání využití knihoven a úklid historie
po dvou souběžně běžících sběračích.

Spusteni:
    .\\.venv\\Scripts\\python.exe tests\\test_serialy_a_historie.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "serialy.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import (collector, db, importers, insights, langstats,
                        scanner, stats)  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def epizoda(cislo: int, ident: str | None = None) -> dict[str, Any]:
    """Epizoda tak, jak ji posílá Jellyfin - tedy s TMDB id seriálu."""
    return {
        "Id": ident or f"ep-{cislo}", "Name": f"{cislo}. díl", "Type": "Episode",
        "SeriesId": "ser-1", "SeriesName": "Kancelář",
        "ParentIndexNumber": 1, "IndexNumber": cislo,
        "RunTimeTicks": 12_000_000_000,
        # Epizoda vlastní tmdb nemá - Jellyfin posílá id SERIÁLU.
        "SeriesProviderIds": {"Tmdb": "2316"},
    }


def film(ident: str, tmdb: str) -> dict[str, Any]:
    return {"Id": ident, "Name": "Duna", "Type": "Movie",
            "RunTimeTicks": 72_000_000_000, "ProviderIds": {"Tmdb": tmdb}}


print("--- epizodu neidentifikuje tmdb, ale tmdb + řada + díl ---")
dily = [epizoda(i) for i in range(1, 6)]
check(len({scanner.tmdb_id_of(d) for d in dily}) == 1,
      "všechny díly mají stejné tmdb (je to id seriálu)")
identity = [scanner.identita_polozky(d) for d in dily]
check(len(set(identity)) == 5, f"ale identitu má každý vlastní ({identity[:2]}…)")
check(scanner.identita_polozky(film("f1", "999")) == ("999", -1, -1),
      "u filmu je řada i díl -1")

# Díl bez čísla nejde jednoznačně určit - takový se raději neslučuje.
bez_cisla = epizoda(9)
bez_cisla["IndexNumber"] = None
check(scanner.identita_polozky(bez_cisla) is None,
      "díl bez čísla se neslučuje vůbec")


print()
print("--- historie dílů se při skenu neslévá ---")
now = db.utcnow()
radky = [scanner._radek_polozky(d, "lib", {}, now) for d in dily]
dvojice = [(scanner.identita_polozky(d), d["Id"]) for d in dily]
scanner._write_batch(list(radky), [], list(dvojice), True)

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lib','Seriály',?)",
                 (now,))
    for i in range(1, 6):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     series_name, library_id, item_type,
                                     started_at, last_seen_at, ended_at,
                                     watched_seconds, play_method, is_active)
               VALUES (?, 'u1', ?, ?, 'Kancelář', 'lib', 'Episode', ?, ?,
                       datetime(?, '+30 minutes'), 1800, 'Transcode', 0)""",
            (f"s-{i}", f"ep-{i}", f"{i}. díl", now, now, now))


def rozlozeni() -> dict[str, int]:
    return {r["item_id"]: r["n"] for r in db.query_all(
        "SELECT item_id, COUNT(*) AS n FROM playback GROUP BY item_id")}


pred = rozlozeni()
scanner._write_batch(list(radky), [], list(dvojice), True)
scanner._write_batch(list(radky), [], list(dvojice), True)
check(rozlozeni() == pred,
      f"dva další skeny historií nehnuly ({rozlozeni()})")
check(len(rozlozeni()) == 5, "každý díl má pořád svoje přehrání")


print()
print("--- ale překódovaný soubor se sloučit MUSÍ ---")
# Kvůli tomuhle slučování vzniklo: nový ItemId, tentýž díl.
novy = epizoda(3, ident="ep-3-hevc")
scanner._write_batch([scanner._radek_polozky(novy, "lib", {}, now)], [],
                     [(scanner.identita_polozky(novy), "ep-3-hevc")], True)
po = rozlozeni()
check("ep-3-hevc" in po and "ep-3" not in po,
      f"historie 3. dílu přešla na nový soubor ({sorted(po)})")
check(len(po) == 5, "a ostatní díly zůstaly na svém")


print()
print("--- v přehledech se seriál ukazuje jako jeden titul ---")
tituly = stats.top_items(365, limit=10)
serialy = [r for r in tituly if r["is_series"]]
check(len(serialy) == 1, f"seriál je jeden řádek, ne pět ({len(serialy)})")
check(serialy[0]["label"] == "Kancelář", f"a jmenuje se seriálem ({serialy[0]['label']})")
check(serialy[0]["detail_url"] == "/series/ser-1",
      f"proklik vede na seriál ({serialy[0]['detail_url']})")

prekodovane = insights.transcode_offenders(365)
check(len(prekodovane) == 1,
      f"i mezi překódovanými je seriál jeden řádek ({len(prekodovane)})")
check(prekodovane[0]["detail_url"] == "/series/ser-1",
      f"a taky se dá prokliknout ({prekodovane[0]['detail_url']})")

# Filtr filmy/seriály - stejné tři možnosti jako u sledovanosti po dnech.
check(all(r["is_series"] for r in stats.top_items(365, kind="series")),
      "filtr 'seriály' pustí jen seriály")
check(not stats.top_items(365, kind="movies"),
      "filtr 'filmy' zatím nic nemá, jsou tu jen epizody")


print()
print("--- seriál se pozná i z názvu, když nic jiného není ---")
# Playback Reporting ukládá název jako "Seriál - s01e01 - Název dílu"
# a o seriálu neřekne nic jiného. K položce už navíc nemusí být v knihovně
# nic. Bez rozboru názvu se takový seriál rozpadl na jednotlivé díly -
# v přehledu bylo pět řádků "Blue - s01e17 - Calypso" místo jednoho "Blue".
check(stats.serial_z_nazvu("Blue - s03e03 - Opičí dráha") == "Blue",
      "z 'Blue - s03e03 - Opičí dráha' se vyčte 'Blue'")
check(stats.serial_z_nazvu("Seal Team 6 - s02e07 - 7. epizoda") == "Seal Team 6",
      "číslo v názvu seriálu nevadí")
check(stats.serial_z_nazvu("Hanebný pancharti") is None,
      "u filmu se nic nevymýšlí")
check(stats.serial_z_nazvu("Něco - s01 - jiného") is None,
      "musí tam být i číslo dílu, ne jen řada")

with db.connect() as conn:
    for klic, nazev in (
        ("imp-1", "Blue - s03e03 - Opičí dráha"),
        ("imp-2", "Blue - s01e17 - Calypso"),
        ("imp-3", "Blue - s01e37 - Dobrodružství"),
    ):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', ?, ?, ?, ?, 3600, 0)""",
            (klic, f"pryc-{klic}", nazev, now, now))

# Samotné pravidlo pro skupinu: tři různé díly musí dát jeden klíč.
# Ověřujeme ho přímo, ne přes žebříček - dokud "Blue" v knihovně není,
# žebříček ho (správně) skryje jako titul bez protějšku.
klice = {stats.klic_titulu({"item_name": nazev})[0] for nazev in (
    "Blue - s03e03 - Opičí dráha", "Blue - s01e17 - Calypso",
    "Blue - s01e37 - Dobrodružství")}
check(klice == {"nazev:blue"}, f"tři díly dají jeden klíč ({klice})")
check(stats.klic_titulu({"item_name": "Blue - s01e17 - Calypso"})[1:] == ("Blue", True),
      "s popiskem 'Blue' a označením seriál")

# A když tentýž seriál v knihovně máme, musí se to slít dohromady -
# ne rozpadnout na "z importu" a "z vlastního sběru".
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, is_missing, synced_at)
           VALUES ('blue-ep1', '1. díl', 'Episode', 'lib', 'ser-blue', 'Blue',
                   1, 1, 0, ?)""", (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 series_name, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('blue-nove', 'u1', 'blue-ep1', '1. díl', 'Blue', ?, ?, 7200, 0)""",
        (now, now))

modre = [r for r in stats.top_items(365, limit=30) if r["label"] == "Blue"]
check(len(modre) == 1,
      f"import i vlastní sběr jsou pořád jeden řádek ({len(modre)})")
check(modre[0]["plays"] == 4, f"a sečtou se ({modre[0]['plays']} spuštění)")
check(modre[0]["detail_url"] == "/series/ser-blue",
      f"proklik vede na seriál z knihovny ({modre[0]['detail_url']})")


print()
print("--- díl se pozná i podle jména z knihovny ---")
# Tvrdší případ než "Blue - s01e17": záznam nese JEN jméno dílu ("Zvony")
# a k položce už v knihovně nic nevede. Z názvu se seriál poznat nedá,
# ale knihovna ten díl zná - a ví, do kterého seriálu patří. Bez toho
# z něj v přehledu vznikl samostatný titul, tedy neexistující film.
with db.connect() as conn:
    for ident, jmeno, serial, jmeno_serialu, cislo in (
        ("got-5", "Zvony", "ser-got", "Hra o trůny", 5),
        ("got-6", "Zimní vichry", "ser-got", "Hra o trůny", 6),
        ("hod-3", "Zelená rada", "ser-hod", "Rod Draka", 3),
        # Název, který mají dva různé seriály - u takového se nehádá.
        ("got-1p", "Pilot", "ser-got", "Hra o trůny", 1),
        ("hod-1p", "Pilot", "ser-hod", "Rod Draka", 1),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing, synced_at)
               VALUES (?, ?, 'Episode', 'lib', ?, ?, 1, ?, 0, ?)""",
            (ident, jmeno, serial, jmeno_serialu, cislo, now))

    for klic, jmeno in (("sir-1", "Zvony"), ("sir-2", "Zelená rada"),
                        ("sir-3", "Hra o trůny"), ("sir-4", "Pilot"),
                        ("sir-5", "Úplně neznámý titul")):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u9', ?, ?, ?, ?, 3600, 0)""",
            (klic, f"osirely-{klic}", jmeno, now, now))

podle_popisku = {r["label"]: r for r in stats.top_items(365, limit=40)}
check("Zvony" not in podle_popisku,
      f"díl 'Zvony' už není samostatný titul ({list(podle_popisku)[:6]})")
check("Zelená rada" not in podle_popisku, "ani 'Zelená rada'")
check("Hra o trůny" in podle_popisku and podle_popisku["Hra o trůny"]["is_series"],
      "místo toho je tam seriál 'Hra o trůny'")
check(podle_popisku.get("Rod Draka", {}).get("detail_url") == "/series/ser-hod",
      f"a dá se prokliknout, i když k dílu položka nevede "
      f"({podle_popisku.get('Rod Draka', {}).get('detail_url')})")

# Dvojznačný název se přiřadit nesmí - špatně zařazená historie je horší
# než ta, o které víme, že je stranou. V žebříčku se takový záznam
# neukáže (nemá protějšek), ale hlavní je, že se **nepřipsal cizímu
# seriálu** - to by byla tichá lež v číslech.
check("Pilot" not in podle_popisku,
      "'Pilot' mají dva seriály, takže se nehádá a mezi tituly nepatří")
klic_pilota = stats.klic_titulu({"item_name": "Pilot"},
                                stats._dily_podle_nazvu())[0]
check(klic_pilota.startswith("polozka:"),
      f"a nezařadí se pod žádný seriál ({klic_pilota})")

# Odsledovaný čas se nikde neztrácí - jen tenhle žebříček ho neukazuje.
sekundy = db.query_value(
    "SELECT watched_seconds FROM playback WHERE session_key = 'sir-5'")
check(sekundy == 3600,
      f"titul, který knihovna nezná, zůstává v historii ({sekundy} s)")


print()
print("--- a úklid to spraví i v datech, ne jen v přehledu ---")
# V přehledu to sedí i bez úklidu, ale záznam pořád ukazuje na položku,
# která neexistuje - a to se nese dál (jazyk, knihovna, proklik z historie).
osirelych = importers.orphan_playback_count()
check(osirelych >= 3, f"úklid osiřelé záznamy vidí ({osirelych})")
navazano = importers.relink_orphans()
check(navazano["rows"] >= 2, f"a naváže je na položky ({navazano})")

zvony = db.query_one("SELECT item_id FROM playback WHERE session_key = 'sir-1'")
check(zvony["item_id"] == "got-5",
      f"'Zvony' teď ukazují na skutečný díl ({zvony['item_id']})")
pilot = db.query_one("SELECT item_id FROM playback WHERE session_key = 'sir-4'")
check(str(pilot["item_id"]).startswith("osirely-"),
      f"dvojznačný 'Pilot' zůstal nenavázaný ({pilot['item_id']})")
check(importers.relink_orphans()["rows"] == 0, "podruhé už nemá co navazovat")


print()
print("--- převzatá historie se naváže podle čísla dílu ---")
# Skutečná data z ostré databáze: **všech** 1035 osiřelých záznamů bylo
# z importu a ani jeden se nenavázal. Důvod byl prostý - párování podle
# názvu hledalo v knihovně celý řetězec
#
#     "Seal Team 6 - s02e07 - 7. epizoda"
#
# jenže tam se ten díl jmenuje prostě "7. epizoda". Přitom v tom řetězci
# je všechno, co určuje jeden konkrétní díl: seriál, řada, číslo.
rozbor = importers.rozbor_nazvu("Seal Team 6 - s02e07 - 7. epizoda")
check(rozbor == {"serial": "Seal Team 6", "rada": 2, "dil": 7,
                 "nazev": "7. epizoda"},
      f"název se rozebere na součástky ({rozbor})")
check(importers.rozbor_nazvu("Nadace - s02e10 - Mýty o stvoření")["dil"] == 10,
      "dvouciferné číslo dílu")
check(importers.rozbor_nazvu("48. díl") is None,
      "holý název dílu se rozebrat nedá - a nic se nevymýšlí")
check(importers.rozbor_nazvu("Mýtus") is None, "ani název filmu")

with db.connect() as conn:
    # Knihovna: díly se jmenují tak, jak je pojmenoval Jellyfin -
    # BEZ předpony se seriálem.
    for ident, jmeno, serial, jmeno_serialu, rada, cislo in (
        ("st-207", "7. epizoda", "ser-st", "Seal Team 6", 2, 7),
        ("st-101", "Pilot", "ser-st", "Seal Team 6", 1, 1),
        ("nad-210", "Mýty o stvoření", "ser-nad", "Nadace", 2, 10),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing, synced_at)
               VALUES (?, ?, 'Episode', 'lib', ?, ?, ?, ?, 0, ?)""",
            (ident, jmeno, serial, jmeno_serialu, rada, cislo, now))

    for klic, stare_id, nazev in (
        ("import:jst:2235", "064b516426e8aaaa", "Seal Team 6 - s02e07 - 7. epizoda"),
        ("import:jst:1942", "064b516426e8bbbb", "Seal Team 6 - s01e01 - Pilot"),
        ("import:pbr:2026", "a9e42f4f7498dddd", "Nadace - s02e10 - Mýty o stvoření"),
        ("import:jst:790c", "c6e1882cea33eeee", "48. díl"),
    ):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u7', ?, ?, 'Episode', ?, ?, 3600, 0)""",
            (klic, stare_id, nazev, now, now))

navazano = importers.relink_orphans()
check(navazano["items"] >= 3, f"tři složené názvy se navázaly ({navazano})")
for klic, ceka in (("import:jst:2235", "st-207"), ("import:jst:1942", "st-101"),
                   ("import:pbr:2026", "nad-210")):
    kam = db.query_one("SELECT item_id FROM playback WHERE session_key = ?",
                       (klic,))["item_id"]
    check(kam == ceka, f"{klic} -> {kam} (čeká se {ceka})")

zbytek = db.query_one("SELECT item_id FROM playback WHERE session_key = 'import:jst:790c'")
check(str(zbytek["item_id"]).startswith("c6e1882"),
      f"'48. díl' se nemá čeho chytit a zůstane ({zbytek['item_id']})")
check(importers.relink_orphans()["items"] == 0, "podruhé už nemá co navazovat")

podle_popisku = {r["label"] for r in stats.top_items(365, limit=40)}
check("Seal Team 6" in podle_popisku,
      f"a v žebříčku je seriál, ne díly ({sorted(podle_popisku)[:8]})")


print()
print("--- co se nedá zařadit, se v žebříčku neukáže ---")
# Zbytky, u kterých se nedá zjistit, o co šlo. "6. díl" má v knihovně
# každý seriál, takže jednoznačná shoda neexistuje a přiřadit je naslepo
# by znamenalo připsat sledování cizímu seriálu.
prehled = stats.top_items(365, limit=40)
popisky = {r["label"] for r in prehled}
check("48. díl" not in popisky, "nezařaditelný zbytek se nezobrazí")
check(all(r["detail_url"] for r in prehled), "co zůstalo, jde prokliknout")
check(prehled[0].get("_skryto", 0) >= 1,
      f"a je vidět, kolik jich bylo skryto ({prehled[0].get('_skryto')})")

# Archiv se skrývat NESMÍ. Titul, který v knihovně byl a v Jellyfinu už
# není, je platná statistika - ne odpad.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, is_missing, synced_at)
           VALUES ('archiv-1', 'Smazaný film', 'Movie', 'lib', 1, ?)""", (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('arch', 'u8', 'archiv-1', 'Smazaný film', ?, ?, 9999, 0)""",
        (now, now))
archiv = [r for r in stats.top_items(365, limit=40) if r["label"] == "Smazaný film"]
check(len(archiv) == 1, "archivovaný titul zůstane v žebříčku")
check(archiv[0]["detail_url"] == "/item/archiv-1",
      f"a dá se otevřít ({archiv[0]['detail_url']})")


print()
print("--- tatáž podívaná ze dvou zdrojů importu ---")
# Jellystat a Playback Reporting si zapisují jiný okamžik (začátek
# přehrávání vs. zápis do tabulky), takže se týž film objeví dvakrát
# s posunem - někdy o půl hodiny, u nočního koukání až druhý den ráno.
# Překryv v čase je proto nechytí; pozná se to podle stejně dlouhého
# přehrávání téhož titulu na témže zařízení v rámci jednoho dne.
def dvojnik(klic, nazev, od, minut, jazyk=None, zarizeni="MIBOX4", uziv="dvoj"):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     device_name, audio_language, started_at,
                                     last_seen_at, ended_at, watched_seconds, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (klic, uziv, nazev, nazev, zarizeni, jazyk, od, od, od, minut * 60))


# Přesně dvojice z nahlášeného výpisu.
dvojnik("import:jst:d1", "Zvony HBO", "2026-08-01 21:38:00", 109, "cs")
dvojnik("import:pbr:d1", "Zvony HBO", "2026-08-02 10:00:00", 108)
dvojnik("import:jst:d2", "Poslední ze Starků", "2026-08-01 20:16:00", 75, "cs")
dvojnik("import:pbr:d2", "Poslední ze Starků", "2026-08-01 19:38:00", 74)
# A co se slučovat NESMÍ:
dvojnik("import:jst:d3", "Duna", "2026-07-01 20:00:00", 120, "cs")
dvojnik("import:jst:d4", "Duna", "2026-07-05 20:00:00", 120, "cs")   # jiný den
dvojnik("import:jst:d5", "Matrix", "2026-07-10 20:00:00", 120, "cs")
dvojnik("import:jst:d6", "Matrix", "2026-07-10 22:10:00", 60, "cs")  # jiná délka
dvojnik("import:jst:d7", "Sedm", "2026-07-11 20:00:00", 90)
dvojnik("import:jst:d8", "Sedm", "2026-07-11 21:00:00", 90, zarizeni="telefon")
dvojnik("vlastni:d9", "Vetřelec", "2026-07-12 20:00:00", 90, "cs")
dvojnik("vlastni:d10", "Vetřelec", "2026-07-12 23:00:00", 90, "cs")  # oba vlastní
# (schválně bez překryvu v čase - jinak by to byla duplicita pro to
#  druhé slučování a testovalo by se něco jiného, než se má)

check(importers.import_duplicate_count() == 2,
      f"najdou se právě dvě dvojice ({importers.import_duplicate_count()})")
vysledek = importers.merge_import_duplicates()
check(vysledek["removed"] == 2, f"a smažou se dva řádky ({vysledek})")

zbylo = {r["item_name"]: r for r in db.query_all(
    "SELECT item_name, started_at, watched_seconds, audio_language"
    "  FROM playback WHERE user_id = 'dvoj'")}
check(list(zbylo).count("Zvony HBO") == 1, "'Zvony' zůstaly jednou")
check(zbylo["Zvony HBO"]["audio_language"] == "cs",
      "zůstal ten bohatší záznam - ten, co zná jazyk")
check(zbylo["Zvony HBO"]["watched_seconds"] == 109 * 60,
      f"a delší z obou dob ({zbylo['Zvony HBO']['watched_seconds']} s)")
check(zbylo["Poslední ze Starků"]["started_at"].endswith("19:38:00"),
      f"začátek se bere nejčasnější ({zbylo['Poslední ze Starků']['started_at']})")

zustaly = [r["item_name"] for r in db.query_all(
    "SELECT item_name FROM playback WHERE user_id = 'dvoj'")]
for titul, kolik, proc in (("Duna", 2, "jiný den"), ("Matrix", 2, "jiná délka"),
                           ("Sedm", 2, "jiné zařízení"),
                           ("Vetřelec", 2, "oba z vlastního sběru")):
    check(zustaly.count(titul) == kolik, f"{titul}: nesloučeno ({proc})")
check(importers.merge_import_duplicates()["removed"] == 0, "podruhé už nic")


print()
print("--- jazyk z importu se do statistik započítá ---")
# Filtr na importy tu byl kvůli tomu, že jazyk NEZNÁME - ne kvůli tomu,
# odkud záznam je. Když ho zdroj pošle, je stejně dobrý jako z vlastního
# sběru. Naopak záznam bez jazyka se do statistik dostat nesmí, jinak
# by je zaplavil "Neuvedeno".
check(importers._jazyk_ze_zdroje({"audiolanguage": "ces"}) == "cs",
      "jazyk se přečte ze sloupce zálohy")
check(importers._jazyk_ze_zdroje({"AudioLanguage": "eng"}) == "en",
      "na zápisu názvu sloupce nezáleží")
check(importers._jazyk_ze_zdroje({"itemname": "Duna"}) is None,
      "a nic se nevymýšlí, když sloupec chybí")

# Jazykové statistiky se počítají přes celou historii, takže si na chvíli
# necháme jen tyhle tři řádky - a hned je zase vrátíme zpátky.
with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = -watched_seconds")
    for klic, jazyk, sekund in (("import:pbr:j1", "cs", 3600),
                                ("import:pbr:j2", "en", 3600),
                                ("import:pbr:j3", None, 3600)):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     audio_language, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'uj', ?, 'Film', ?, ?, ?, ?, 0)""",
            (klic, klic, jazyk, now, now, sekund))

jazyky = {r["code"]: r for r in
          langstats.watched_languages(365, langstats.colour_map())["rows"]}
check(set(jazyky) == {"cs", "en"},
      f"započítaly se oba jazyky z importu ({sorted(jazyky)})")
check("und" not in jazyky,
      "ale záznam bez jazyka statistiku nezaplavil 'Neuvedeno'")

with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = -watched_seconds"
                 " WHERE watched_seconds < 0")


print()
print("--- využití knihoven nemůže přesáhnout 100 % ---")
# Sledované položky se počítaly včetně archivu, celkový počet jen ze
# živých - jablko ku hrušce. U seriálu, ze kterého část dílů zmizela,
# vycházelo využití přes sto procent.
# Vlastní knihovna, ať se nesahá na to, co používají sekce níž.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lu','Vyuziti',?)",
                 (now,))
    for i in range(1, 4):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, is_missing, synced_at,
                                  size_bytes)
               VALUES (?, ?, 'Movie', 'lu', 0, ?, 1000000000)""",
            (f"zivy-{i}", f"Film {i}", now))
    for i in range(1, 3):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, is_missing, synced_at,
                                  size_bytes)
               VALUES (?, ?, 'Movie', 'lu', 1, ?, 1000000000)""",
            (f"pryc-{i}", f"Smazaný {i}", now))
    for polozka in ("zivy-1", "zivy-2", "zivy-3", "pryc-1", "pryc-2"):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     library_id, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'uv', ?, 'x', 'lu', ?, ?, 3600, 0)""",
            (f"v-{polozka}", polozka, now, now))

vyuziti = next(r for r in insights.storage_efficiency(365)
               if r["label"] == "Vyuziti")
check(vyuziti["item_count"] == 3, f"celkem se počítají živé ({vyuziti['item_count']})")
check(vyuziti["watched_items"] == 3,
      f"a sledované taky - ne včetně archivu ({vyuziti['watched_items']})")
check(vyuziti["watched_items"] <= vyuziti["item_count"],
      "využití tak nemůže přesáhnout 100 %")


print()
print("--- 've slabé kvalitě' se pozná z obou stran obrazu ---")
# Dřív rozhodovala jen výška (`i.height < 1000`) a šířka se do šablony
# nedostávala vůbec. Odnesla to všechna širokoúhlá vydání: běžný 1080p
# film v poměru scope má 1920x800, takže výška je pod hranicí, i když
# je to plnohodnotné 1080p. A ve sloupci "Rozlišení" pak svítilo něco
# jiného, než co soubor doopravdy je.
# Vlastní knihovna: tyhle položky jsou velké a v knihovně seriálů by
# přebily obrázek, který se bere z největšího titulu.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lkv','Kvalita',?)",
                 (now,))
    for ident, nazev, sirka, vyska, bitrate in (
        ("kv-sd", "Starý film", 720, 576, 1_500_000),
        # Tenhle případ stará podmínka označila špatně - výška 800 je
        # pod hranicí, ale 1920 na šířku je plnohodnotné 1080p.
        ("kv-scope1080", "1080p v poměru scope", 1920, 800, 8_000_000),
        ("kv-ultra", "Širokoúhlý 1080p", 2560, 1080, 8_000_000),
        ("kv-scope", "4K scope", 3840, 1608, 25_000_000),
        ("kv-bitrate", "Málo datového toku", 1920, 1080, 1_200_000),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, width, height,
                                  bitrate, size_bytes, is_missing, synced_at)
               VALUES (?, ?, 'Movie', 'lkv', ?, ?, ?, 5000000000, 0, ?)""",
            (ident, nazev, sirka, vyska, bitrate, now))
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, watched_seconds, is_active)
               VALUES (?, 'ukvalita', ?, ?, ?, ?, 7200, 0)""",
            (f"kv-{ident}", ident, nazev, now, now))

kandidati = {r["label"]: r for r in insights.upgrade_candidates(365, limit=50)}
check("Starý film" in kandidati, "opravdu nízké rozlišení se najde")
check("Málo datového toku" in kandidati, "i nízký datový tok")
check("1080p v poměru scope" not in kandidati,
      f"1080p v poměru scope slabá kvalita NENÍ ({sorted(kandidati)})")
check("Širokoúhlý 1080p" not in kandidati, "ani širokoúhlé vydání")
check("4K scope" not in kandidati, "a 4K v poměru scope už vůbec ne")

stary = kandidati["Starý film"]
check(stary["width"] == 720 and stary["height"] == 576,
      f"oba rozměry dorazí do šablony ({stary['width']}x{stary['height']})")
check(stary["detail_url"] == "/item/kv-sd",
      f"a řádek se dá prokliknout ({stary['detail_url']})")


print()
print("--- historie bere rozlišení z relace, ne z knihovny ---")
# 4K film v poměru scope má výšku 1608 - pod hranicí pro 4K. Rozlišení
# se pozná podle šířky, jenže ta se do šablony vůbec nedostávala:
# vybírala se jen `i.height`. Takový film pak v historii vyšel jako 1080p,
# zatímco karta "Právě se hraje" ho správně hlásila jako 4K.
from jellyscope.formatting import resolution_human  # noqa: E402

with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, width, height, is_missing, synced_at)
           VALUES ('scope-4k', 'Hanebný pancharti', 'Movie', 3840, 1608, 0, ?)""",
        (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 video_width, video_height, started_at,
                                 last_seen_at, watched_seconds, is_active)
           VALUES ('scope-1', 'u1', 'scope-4k', 'Hanebný pancharti', 3840, 1608,
                   ?, ?, 3600, 0)""", (now, now))
    # Starší záznam, který rozměry ještě neukládal - musí se dohledat.
    # Schválně v jiný čas: kdyby se s předchozím překrýval, byla by to
    # podle pravidel úklidu duplicita (stejný divák, stejný film, překryv).
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('scope-2', 'u1', 'scope-4k', 'Hanebný pancharti',
                   datetime(?, '-3 hours'), datetime(?, '-3 hours'), 3600, 0)""",
        (now, now))

radky = {r["session_key"]: r for r in stats.history(200, 0)}
for klic, popis in (("scope-1", "se zapsanými rozměry"),
                    ("scope-2", "bez nich (dohledá se z knihovny)")):
    radek = radky[klic]
    # `.get()` schválně: kdyby se šířka z dotazu zase vytratila, má test
    # spadnout na čitelné hlášce, ne na KeyError.
    sirka, vyska = radek.get("width"), radek.get("height")
    check(resolution_human(vyska, sirka) == "4K",
          f"4K scope film je 4K i v historii – {popis} "
          f"({sirka}x{vyska} -> {resolution_human(vyska, sirka)})")


print()
print("--- knihovna seriálů má obrázek ze seriálu, ne z dílu ---")
# Epizoda v Jellyfinu backdrop nemá, ten patří seriálu. Dlaždice seriálové
# knihovny proto zůstávala šedá - obrázek se nenačetl.
karty = {k["id"]: k for k in stats.library_cards()}
check("lib" in karty, "knihovna se vypsala")
check(karty["lib"]["poster_id"] == "ser-1" or karty["lib"]["poster_id"] == "ser-blue",
      f"obrázek se bere ze seriálu, ne z epizody ({karty['lib']['poster_id']})")


print()
print("--- proklik nesmí vést na 404 ---")
# Titul, ke kterému už v knihovně nic není (smazaný soubor, import).
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('osirely', 'u1', 'uz-neexistuje', 'Zmizelý film', ?, ?, 9999, 0)""",
        (now, now))
osirely = [r for r in stats.top_items(365, limit=20) if r["label"] == "Zmizelý film"]
check(not osirely,
      "titul, ke kterému v knihovně ani v archivu nic nevede, se neukáže")
zbyl = db.query_one("SELECT watched_seconds FROM playback WHERE session_key = 'osirely'")
check(zbyl["watched_seconds"] == 9999,
      f"ale z historie nezmizí ({zbyl['watched_seconds']} s)")


print()
print("--- využití knihoven počítá i historii bez library_id ---")
# Importovaná historie knihovnu nezná a starší sběr ji nevyplnil
# u položky, kterou jsme ještě nesynchronizovali.
s_udajem = insights.storage_efficiency(365)
with db.connect() as conn:
    conn.execute("UPDATE playback SET library_id = NULL")
bez_udaje = insights.storage_efficiency(365)

check([r["hours"] for r in s_udajem] == [r["hours"] for r in bez_udaje],
      "odsledovaný čas se nezměnil - knihovna se dohledá přes položku")
check([r["watched_items"] for r in s_udajem] == [r["watched_items"] for r in bez_udaje],
      "a počet sledovaných titulů taky ne")
check(any(r["hours"] for r in bez_udaje), "a nejsou to nuly")


print()
print("--- dva sběrače naráz nesmí vyrobit duplicitu ---")
STOPY = [{"Type": "Video", "Index": 0, "Codec": "h264", "Width": 1920, "Height": 1080}]


def snimek(klic: str) -> dict[str, Any]:
    return {"Id": klic, "UserId": "u9", "UserName": "Karel",
            "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False},
            "NowPlayingItem": {"Id": "film-x", "Name": "Duna", "Type": "Movie",
                               "RunTimeTicks": 72_000_000_000,
                               "MediaStreams": STOPY}}


pred_sberem = db.query_value("SELECT COUNT(*) FROM playback")
# Stará verze aplikace si klíč relace skládá jinak než nová.
collector._store_sessions([snimek("relace-STARA")], max_gap_seconds=600)
collector._store_sessions([snimek("relace-NOVA")], max_gap_seconds=600)
collector._store_sessions([snimek("relace-NOVA")], max_gap_seconds=600)
pribylo = db.query_value("SELECT COUNT(*) FROM playback") - pred_sberem
check(pribylo == 1, f"vznikl jediný záznam, ne dva ({pribylo})")


print()
print("--- a co už v databázi leží, jde uklidit ---")
with db.connect() as conn:
    # Přesně situace uživatele: dvě verze zapsaly totéž přehrávání zvlášť.
    for klic, posun, doba in (("stara::x", "+0 minutes", 2400),
                              ("nova::x", "+1 minutes", 2700)):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, ended_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u5', 'film-y', 'Duna', datetime(?, ?), ?,
                       datetime(?, '+45 minutes'), ?, 0)""",
            (klic, now, posun, now, now, doba))

check(importers.duplicate_playback_count() == 1,
      f"úklid ví, že je tam jedna duplicita ({importers.duplicate_playback_count()})")
vysledek = importers.merge_duplicate_playback()
check(vysledek["removed"] == 1, f"a smaže právě jeden řádek ({vysledek})")
zbyly = db.query_one("SELECT watched_seconds FROM playback WHERE item_id = 'film-y'")
check(int(zbyly["watched_seconds"]) == 2700,
      f"zůstal ten úplnější, nesečtený ({zbyly['watched_seconds']} s)")
check(importers.merge_duplicate_playback()["removed"] == 0,
      "podruhé už nenajde nic")

print()
print("--- co se sloučit NESMÍ ---")
# Tohle je ta část, kde se dá nadělat největší škoda: smazaný řádek
# historie se už nevrátí. Každý případ je situace, která v domácnosti
# opravdu nastane.
pripady = [
    ("film začatý včera a dokoukaný dnes",
     [("2026-08-15 22:00:00", "2026-08-15 23:00:00", 3600, "n-1", "TV", "u1"),
      ("2026-08-16 20:00:00", "2026-08-16 21:30:00", 5400, "n-1", "TV", "u1")]),
    ("dokoukal a hned pustil znovu",
     [("2026-08-14 10:00:00", "2026-08-14 12:00:00", 7200, "n-2", "TV", "u1"),
      ("2026-08-14 12:00:00", "2026-08-14 14:00:00", 7200, "n-2", "TV", "u1")]),
    ("tentýž film na televizi a na telefonu zároveň",
     [("2026-08-12 20:00:00", "2026-08-12 22:00:00", 7200, "n-3", "TV", "u1"),
      ("2026-08-12 20:30:00", "2026-08-12 21:00:00", 1800, "n-3", "telefon", "u1")]),
    ("dva uživatelé u téhož filmu ve stejnou chvíli",
     [("2026-08-11 20:00:00", "2026-08-11 22:00:00", 7200, "n-4", "TV", "u1"),
      ("2026-08-11 20:00:00", "2026-08-11 22:00:00", 7200, "n-4", "TV", "u2")]),
]
for popis, zaznamy in pripady:
    pred = importers.duplicate_playback_count()
    with db.connect() as conn:
        for poradi, (od, do, sekund, polozka, zarizeni, uzivatel) in enumerate(zaznamy):
            conn.execute(
                """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                         device_id, started_at, last_seen_at,
                                         ended_at, watched_seconds, is_active)
                   VALUES (?,?,?,'Film',?,?,?,?,?,0)""",
                (f"{polozka}-{poradi}", uzivatel, polozka, zarizeni, od, do, do, sekund))
    pribylo = importers.duplicate_playback_count() - pred
    check(pribylo == 0, f"{popis} ({pribylo} k sloučení)")

# A naopak: záznam ze staré verze, která zařízení nehlásila, se sloučit má.
# Neznámé zařízení nikomu neodporuje.
pred = importers.duplicate_playback_count()
with db.connect() as conn:
    for klic, od, do, sekund, zarizeni in (
        ("bez-zarizeni", "2026-08-10 20:00:00", "2026-08-10 20:40:00", 2400, None),
        ("se-zarizenim", "2026-08-10 20:00:30", "2026-08-10 20:45:00", 2700, "TV"),
    ):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     device_id, started_at, last_seen_at,
                                     ended_at, watched_seconds, is_active)
               VALUES (?, 'u1', 'n-5', 'Film', ?, ?, ?, ?, ?, 0)""",
            (klic, zarizeni, od, do, do, sekund))
check(importers.duplicate_playback_count() - pred == 1,
      "neznámé zařízení slučování nebrání - jinak by stará verze utekla")


print()
print("--- slitou historii epizod jde vrátit ke správným dílům ---")
# Následek chyby popsané nahoře: všechno visí na jednom dílu, ale název
# dílu v záznamu zůstal správný.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_name LIKE '%díl%'")
    for i in range(1, 5):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     series_name, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u1', 'ep-1', ?, 'Kancelář', ?, ?, 1800, 0)""",
            (f"slite-{i}", f"{i}. díl", now, now))

check(len(importers.misplaced_episode_rows()) == 3,
      f"tři ze čtyř visí na cizím dílu ({len(importers.misplaced_episode_rows())})")
oprava = importers.repair_episode_links()
check(oprava["moved"] == 3, f"a vrátí se ({oprava})")

kde = {r["item_id"] for r in db.query_all(
    "SELECT DISTINCT item_id FROM playback WHERE item_name LIKE '%díl%'")}
check(len(kde) == 4, f"každý díl má zase svoje přehrání ({sorted(kde)})")
check(importers.repair_episode_links()["moved"] == 0, "podruhé už nemá co vracet")


print()
print("--- import ze zálohy pluginu (TSV) ---")
tsv = "\n".join("\t".join(r) for r in [
    ["2026-03-01 20:00:00", "u1", "tsv-1", "Movie", "Duna", "DirectPlay",
     "Jellyfin Web", "Chrome", "3600"],
    ["2026-03-02 21:00:00", "u2", "tsv-2", "Movie", "Něco", "Transcode",
     "Findroid", "Pixel", "1800"],
    ["2026-03-03 22:00:00", "u1", "tsv-3", "Episode", "1. díl", "DirectPlay",
     "Kodi", "TV", "30"],
]).encode("utf-8")

vysledek = asyncio.run(importers.import_playback_reporting_tsv(tsv, min_seconds=60))
check(vysledek["status"] == "ok", f"soubor bez hlavičky se přečte ({vysledek.get('message')})")
check(vysledek["imported"] == 2, f"dva záznamy, krátký se přeskočil ({vysledek['imported']})")

# Některé verze pluginu hlavičku píšou, jiné ne - poznat to musíme samo.
s_hlavickou = (b"DateCreated\tUserId\tItemId\tItemType\tItemName\t"
               b"PlaybackMethod\tClientName\tDeviceName\tPlayDuration\n" + tsv)
znovu = asyncio.run(importers.import_playback_reporting_tsv(s_hlavickou, min_seconds=60))
check(znovu["imported"] == 0 and znovu["duplicate"] == 2,
      f"tentýž obsah s hlavičkou se nezdvojí ({znovu['imported']}/{znovu['duplicate']})")

# Nahraný nesmysl musí něco poradit, ne spadnout.
spatny = asyncio.run(importers.import_playback_reporting_tsv(b'{"neco": "jineho"}'))
check(spatny["status"] == "error" and "Backup" in spatny["message"],
      f"jiný soubor poradí, kde zálohu vyrobit ({spatny['message'][:60]}…)")
prazdny = asyncio.run(importers.import_playback_reporting_tsv(b""))
check(prazdny["status"] == "error", "prázdný soubor nespadne")


print()
print("--- archivované díly se nepočítají mezi živé ---")
# Tohle byl ten rozdíl "Jellyfin ukazuje 100, Jellyscope 120": seznam
# knihovny archivované díly vynechával, detail seriálu je vypisoval mezi
# ostatními. Z rozdílu přitom nešlo poznat proč.
with db.connect() as conn:
    for cislo in (11, 12, 13):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing,
                                  synced_at, size_bytes)
               VALUES (?, ?, 'Episode', 'lib', 'ser-1', 'Kancelář', 1, ?, 1, ?, 1000000000)""",
            (f"ep-smazany-{cislo}", f"{cislo}. díl", cislo, now))

detail = stats.series_detail("ser-1")
v_knihovne = [r for r in stats.library_rows(50, 0) if r["id"] == "ser-1"]
check(detail["episode_count"] == v_knihovne[0]["episode_count"],
      f"detail i seznam knihovny hlásí stejný počet "
      f"({detail['episode_count']} vs {v_knihovne[0]['episode_count']})")
check(detail["archived_count"] == 3,
      f"a archivované jsou vypsané zvlášť ({detail['archived_count']})")
check(all(d["is_missing"] for d in detail["archived"]),
      "v archivu je jen to, co v Jellyfinu opravdu není")
check(all(not d["is_missing"] for rada in detail["seasons"] for d in rada["episodes"]),
      "mezi řadami archivované nejsou")

# Seriál, ze kterého zbyl jen archiv, musí jít pořád otevřít - jinak by
# odkaz z historie skončil na 404.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE series_id = 'ser-1'")
cely_archiv = stats.series_detail("ser-1")
check(bool(cely_archiv) and cely_archiv["episode_count"] > 0,
      "seriál jen v archivu se pořád otevře")
check(cely_archiv["missing"] is True, "a je označený jako zmizelý")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 0 WHERE id LIKE 'ep-%'"
                 " AND id NOT LIKE 'ep-smazany-%'")


print()
print("--- nedávno přidané: dávka dílů se dá rozkliknout ---")
# Když seriálu přibyde celá sezóna, karta má napřed vypadat jako každá
# jiná - jen název. Teprve po najetí se plakát zvětší a ukáže odkazy
# na jednotlivé díly. Sem patří jen ta data; zvětšení řeší CSS.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lr','Seriály',?)",
                 (now,))
    # Celá sezóna přišla během několika minut.
    for cislo in range(1, 6):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, date_created,
                                  is_missing, synced_at)
               VALUES (?, ?, 'Episode', 'lr', 'ser-nova', 'Nová sezóna', 2, ?, ?, 0, ?)""",
            (f"nova-{cislo}", f"{cislo}. díl", cislo,
             f"2026-08-17 10:0{cislo}:00", now))
    # Jiný seriál - jediný díl.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              is_missing, synced_at)
           VALUES ('sam-1', 'Poslední díl', 'Episode', 'lr', 'ser-sam', 'Jiný seriál',
                   1, 9, '2026-08-17 09:00:00', 0, ?)""", (now,))
    # A díl téhož seriálu z minulého měsíce - do dávky nepatří.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              is_missing, synced_at)
           VALUES ('sam-0', 'Starý díl', 'Episode', 'lr', 'ser-sam', 'Jiný seriál',
                   1, 8, '2026-07-01 09:00:00', 0, ?)""", (now,))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, date_created,
                              is_missing, synced_at)
           VALUES ('film-r', 'Nějaký film', 'Movie', 'lr', '2026-08-17 08:00:00', 0, ?)""",
        (now,))

pridane = {r["title"]: r for r in stats.recently_added(10)}
sezona = pridane["Nová sezóna"]
check(len(sezona["episodes"]) == 5,
      f"pět dílů v dávce ({len(sezona['episodes'])})")
# Odshora dolů jako seznam dílů, ne podle data přidání: Jellyfin načte
# soubory v pořadí, v jakém je najde na disku, takže dávka přijde
# zamíchaná - a čtení "E1, E2, E3..." je to jediné, co dává smysl.
check([d["id"] for d in sezona["episodes"]] ==
      ["nova-1", "nova-2", "nova-3", "nova-4", "nova-5"],
      f"seřazené od prvního dílu ({[d['id'] for d in sezona['episodes']]})")
check(sezona["series_url"] == "/series/ser-nova",
      f"a název vede na seriál ({sezona['series_url']})")

jeden = pridane["Jiný seriál"]
check(jeden["episodes"] == [],
      f"u jednoho dílu se seznam nedělá ({len(jeden['episodes'])})")
check(jeden["id"] == "sam-1", "karta míří rovnou na ten díl")

check(pridane["Nějaký film"]["episodes"] == [], "u filmu taky ne")

# Díl z minulého měsíce se do dávky počítat nesmí - jinak by se ve
# výpisu objevilo něco, co s tou novinkou nemá nic společného.
check(all(d["id"] != "sam-0" for d in jeden["episodes"]),
      "starý díl do dávky nepatří")

# Tentýž díl ve dvou souborech (zbyla stará verze vedle nové) jsou
# v Jellyfinu dvě položky. Ve výpisu novinek by z toho byly dva stejné
# řádky - a jeden z nich ukazuje na tu položku, kterou příští scan
# sloučí pryč, takže odkaz přestane platit.
with db.connect() as conn:
    conn.execute("UPDATE items SET height = 1080 WHERE id = 'nova-3'")
    # Kopie je novější, ale technická data ještě nemá.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              height, is_missing, synced_at)
           VALUES ('nova-3-kopie', '3. díl', 'Episode', 'lr', 'ser-nova',
                   'Nová sezóna', 2, 3, '2026-08-17 10:04:30', NULL, 0, ?)""",
        (now,))

sezona = {r["title"]: r for r in stats.recently_added(10)}["Nová sezóna"]
cisla = [(d["parent_index_number"], d["index_number"]) for d in sezona["episodes"]]
check(cisla == [(2, 1), (2, 2), (2, 3), (2, 4), (2, 5)],
      f"stejný díl podruhé se ve výpisu neopakuje ({cisla})")
check([d["id"] for d in sezona["episodes"]][2] == "nova-3",
      "zůstane ten, který už má změřená technická data")


print()
print("--- rozlišení v Zjištění se počítá z obou stran obrazu ---")
# Dva dotazy tam vybíraly jen `i.height` a šířka se do šablony vůbec
# nedostala, takže 4K film v poměru scope (3840x1608) vyšel jako 1080p -
# výška je pod hranicí. Popisek varianty u duplicit se navíc skládal
# přímo z výšky ("hevc / 1608p").
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM playback")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lz','Filmy',?)",
                 (now,))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, width, height, video_codec,
                              bitrate, size_bytes, date_created, path,
                              production_year, is_missing, synced_at)
           VALUES ('scope4k', 'Hanebný pancharti', 'Movie', 'lz', 3840, 1608, 'hevc',
                   25000000, 60000000000, '2020-01-01 00:00:00', '/m/x.mkv',
                   2009, 0, ?)""", (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 play_method, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('z1', 'uz', 'scope4k', 'Hanebný pancharti', 'Transcode',
                   ?, ?, 7200, 0)""", (now, now))

# Do "nesledovaného obsahu" patří jen to, na co se nikdo nedíval -
# proto vlastní položka, kterou nikdo nespustil.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, width, height, video_codec,
                              bitrate, size_bytes, date_created, production_year,
                              is_missing, synced_at)
           VALUES ('scope-mrtvy', 'Nikdo to nevidel', 'Movie', 'lz', 3840, 1608,
                   'hevc', 25000000, 60000000000, '2020-01-01 00:00:00', 2009, 0, ?)""",
        (now,))

for nazev, radky in (
    ("nesledovaný obsah", insights.dead_storage(days=1)["rows"]),
    ("velké a málo sledované", insights.oversized_rarely_watched(365)),
    ("překódované soubory", insights.transcode_offenders(365)),
):
    radek = radky[0] if radky else {}
    check(radek.get("width") == 3840 and radek.get("height") == 1608,
          f"{nazev}: oba rozměry dorazí ({radek.get('width')}x{radek.get('height')})")

# Popisek varianty u duplicit musí použít stejné pravidlo.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, width, height, video_codec,
                              size_bytes, production_year, is_missing, synced_at)
           VALUES ('scope-kopie', 'Hanebný pancharti', 'Movie', 'lz', 1920, 800,
                   'h264', 8000000000, 2009, 0, ?)""", (now,))
duplicity = insights.duplicate_candidates()
check(duplicity and "4K" in duplicity[0]["variants"],
      f"varianta se popisuje stejným pravidlem ({duplicity[0]['variants'] if duplicity else '-'})")
check(duplicity and "1608p" not in duplicity[0]["variants"],
      "a ne holou výškou")


print()
print("--- proklik z nejsledovanějších vede tam, kam popisek ---")
# Nahlášeno z provozu: řádek se jmenoval správně a byl ve správné sekci,
# ale proklik vedl na docela jinou položku.
#
# Vzniklo to takhle: film se jmenoval stejně jako díl jiného seriálu,
# takže se podle názvu přiřadil do jeho skupiny. Sám `series_id` nenesl
# (proto se hádalo z názvu), takže z něj vyšel odkaz na jeho vlastní
# položku - a protože byl odsledovanější, stal se zástupcem skupiny.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM playback")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lp','Vše',?)",
                 (now,))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, is_missing, synced_at)
           VALUES ('dil-x','Skandál v Belgravii','Episode','lp','ser-sh','Sherlock',
                   2,1,0,?)""", (now,))
    # Film stejného jména. Vlastní seriál nemá - a nemá ho ani mít.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, is_missing, synced_at)
           VALUES ('film-x','Skandál v Belgravii','Movie','lp',0,?)""", (now,))
    for klic, ident, serial, typ, sekund in [
            ("sh-1", "dil-x", "Sherlock", "Episode", 3600),
            # Film je odsledovanější, takže by se stal zástupcem skupiny.
            ("sh-2", "film-x", None, "Movie", 7200)]:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, series_name, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?,'u1',?,'Skandál v Belgravii',?,?,?,?,?,0)""",
            (klic, ident, typ, serial, now, now, sekund))

radky = {r["label"]: r for r in stats.top_items(365, limit=10)}
check(set(radky) == {"Sherlock", "Skandál v Belgravii"},
      f"film a seriál zůstanou dva řádky ({sorted(radky)})")
check(radky["Sherlock"]["detail_url"] == "/series/ser-sh",
      f"seriál vede na seriál ({radky['Sherlock']['detail_url']})")
check(radky["Sherlock"]["is_series"] is True, "a je ve správné sekci")
check(radky["Skandál v Belgravii"]["detail_url"] == "/item/film-x",
      f"film na svou položku ({radky['Skandál v Belgravii']['detail_url']})")
check(round(radky["Sherlock"]["hours"], 1) == 1.0,
      f"a hodiny se nesčítají přes dva různé tituly ({radky['Sherlock']['hours']})")

# Druhá pojistka pro případ, kdy se do skupiny seriálu záznam dostat
# MÁ: díl, u kterého Jellyfin neposlal id seriálu, se k němu přiřadí
# podle názvu. Odkaz se pak nesmí vzít z něj (nemá co nabídnout), ale
# z klíče skupiny - tedy z id seriálu.
skupina = stats._slouc_tituly(
    [{"item_id": "dil-bez-serialu", "item_name": "Velká hra",
      "series_name": "Sherlock", "existujici_id": "dil-bez-serialu",
      "series_id": None, "i_series_name": None, "i_name": "Velká hra",
      "i_type": "Episode", "is_missing": 0, "seconds": 9999},
     {"item_id": "dil-x", "item_name": "Skandál v Belgravii",
      "series_name": "Sherlock", "existujici_id": "dil-x",
      "series_id": "ser-sh", "i_series_name": "Sherlock",
      "i_name": "Skandál v Belgravii", "i_type": "Episode",
      "is_missing": 0, "seconds": 10}],
    stats.KIND_BOTH, 10, {"hours": "seconds"})
check(len(skupina) == 1, f"oba díly jsou jeden seriál ({len(skupina)})")
check(skupina[0]["detail_url"] == "/series/ser-sh",
      f"odkaz se bere z klíče skupiny, ne od nejsilnějšího záznamu "
      f"({skupina[0]['detail_url']})")


print()
print("--- srovnání názvů podle knihovny ---")
# Název se u přehrávání ukládá spolu se záznamem. Když se titul později
# přejmenuje (nebo se v Jellyfinu spraví špatně určená metadata), nese
# starý záznam původní jméno - a ve statistikách patří k jednomu titulu
# název druhého.
with db.connect() as conn:
    conn.execute("UPDATE items SET name = 'Skandál v Belgravii (2012)'"
                 " WHERE id = 'film-x'")

check(importers.stale_name_rows() == 1,
      f"nesedící záznam se najde ({importers.stale_name_rows()})")
vysledek = importers.sjednot_nazvy()
check(vysledek == {"items": 1, "rows": 1}, f"a srovná se ({vysledek})")
check(db.query_value("SELECT item_name FROM playback WHERE item_id = 'film-x'")
      == "Skandál v Belgravii (2012)", "název v historii sedí s knihovnou")
check(importers.stale_name_rows() == 0, "podruhé už není co srovnávat")

# Osiřelý záznam se nechává být: jeho název je jediné, co o něm víme.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 item_type, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('osirely','u1','uz-neexistuje','Něco dávného','Movie',
                   ?,?,1800,0)""", (now, now))
importers.sjednot_nazvy()
check(db.query_value("SELECT item_name FROM playback WHERE session_key = 'osirely'")
      == "Něco dávného", "u záznamu bez položky se název nemaže")


print()
print("--- otisk doběhlých úloh ---")
# Podle změny tohohle čísla stránka pozná, že úloha skončila - i když
# celá proběhla mezi dvěma dotazy.
pred_ulohou = scanner.tasks_version()
scan_id = scanner.start_task_log("recent")
check(scanner.tasks_version() == pred_ulohou,
      "rozběhnutá úloha otisk nemění - ještě není co ukázat")
scanner.finish_task_log(scan_id, "done", total=1, ok=1, message="hotovo")
check(scanner.tasks_version() != pred_ulohou,
      f"doběhlá už ano ({pred_ulohou} -> {scanner.tasks_version()})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
