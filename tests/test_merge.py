# -*- coding: utf-8 -*-
"""Slučování položek podle tmdb_id a archiv.

Situace, kterou to řeší: překóduješ film a nahradíš původní soubor.
Jellyfin to nepozná jako změnu — založí novou položku s novým ItemId.
Bez slučování by se historie přehrávání rozpadla na dva tituly.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# Databáze musí být dočasná, ať test nesahá na skutečná data.
_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "merge.db")

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def vloz_polozku(item_id: str, tmdb: str | None, name: str = "Film") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, tmdb_id, is_missing, synced_at)"
            " VALUES (?,?,?,?,0,?)",
            (item_id, name, "Movie", tmdb, db.utcnow()),
        )


def vloz_prehravani(item_id: str, seconds: int) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback
               (session_key, item_id, item_name, started_at, last_seen_at,
                watched_seconds, is_active)
               VALUES (?,?,?,?,?,?,0)""",
            (f"s-{item_id}-{seconds}", item_id, "Film",
             db.utcnow(), db.utcnow(), seconds),
        )


def odsledovano(item_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(watched_seconds), 0) AS s"
            "  FROM playback WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    return int(row["s"])


def polozka(item_id: str):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


print("--- tmdb_id z odpovědi Jellyfinu ---")
check(scanner.tmdb_id_of({"ProviderIds": {"Tmdb": "603"}}) == "603",
      "přečte klíč Tmdb")
check(scanner.tmdb_id_of({"ProviderIds": {"TmdbId": "603"}}) == "603",
      "přečte i klíč TmdbId")
check(scanner.tmdb_id_of({"ProviderIds": {"tmdb": "603"}}) == "603",
      "na velikosti písmen nezáleží")
check(scanner.tmdb_id_of({"ProviderIds": {"Imdb": "tt0133093"}}) is None,
      "jiný poskytovatel se nepoplete s TMDB")
check(scanner.tmdb_id_of({"SeriesProviderIds": {"Tmdb": "1396"}}) == "1396",
      "u epizody vezme id seriálu")
# A právě proto se tmdb_id samo o sobě nesmí použít jako identita položky:
# všechny díly seriálu ho mají stejné. Podrobně v test_serialy_a_historie.py.
check(scanner.identita_polozky({"Type": "Episode", "ParentIndexNumber": 1,
                                "IndexNumber": 3,
                                "SeriesProviderIds": {"Tmdb": "1396"}})
      == ("1396", 1, 3),
      "identita epizody je tmdb + řada + díl")
check(scanner.tmdb_id_of({}) is None, "bez ProviderIds vrátí None")
check(scanner.tmdb_id_of({"ProviderIds": {"Tmdb": ""}}) is None,
      "prázdná hodnota se nepočítá")
check(scanner.tmdb_id_of({"ProviderIds": "nesmysl"}) is None,
      "poškozená odpověď nespadne")


print()
print("--- překódovaný soubor: nové ItemId, stejné tmdb ---")
vloz_polozku("stare-id", "603", "Matrix")
vloz_prehravani("stare-id", 5400)
check(odsledovano("stare-id") == 5400, "výchozí stav: 5400 s na starém id")

merged = scanner._merge_by_tmdb([(("603", -1, -1), "nove-id")])
check(merged == 1, "proběhlo jedno sloučení")
check(polozka("stare-id") is None, "stará položka zmizela")
check(polozka("nove-id") is not None, "položka je pod novým id")
check(odsledovano("nove-id") == 5400, "historie se přenesla beze ztráty")
check(odsledovano("stare-id") == 0, "na starém id už nic nezůstalo")
check(polozka("nove-id")["name"] == "Matrix", "název a ostatní data zůstala")


print()
print("--- nové id už v databázi je (sync předběhl slučování) ---")
vloz_polozku("stara-2", "550", "Klub rváčů")
vloz_prehravani("stara-2", 1200)
vloz_polozku("nova-2", "550", "Klub rváčů")
vloz_prehravani("nova-2", 300)

merged = scanner._merge_by_tmdb([(("550", -1, -1), "nova-2")])
check(merged == 1, "proběhlo sloučení")
check(polozka("stara-2") is None, "duplicitní položka je pryč")
check(odsledovano("nova-2") == 1500, "historie obou se sečetla (1200 + 300)")


print()
print("--- co se slučovat nesmí ---")
vloz_polozku("jiny-film", "999", "Jiný film")
vloz_prehravani("jiny-film", 600)
merged = scanner._merge_by_tmdb([(("111", -1, -1), "neexistuje")])
check(merged == 0, "neznámé tmdb nic nesloučí")
check(odsledovano("jiny-film") == 600, "cizí položky se nedotkne")

merged = scanner._merge_by_tmdb([(("999", -1, -1), "jiny-film")])
check(merged == 0, "stejné id na obou stranách se neslučuje samo se sebou")
check(polozka("jiny-film") is not None, "položka nezmizela")

check(scanner._merge_by_tmdb([]) == 0, "prázdný seznam projde bez chyby")


print()
print("--- víc kandidátů: vyhraje ten s největší historií ---")
vloz_polozku("maly", "777", "Duna")
vloz_prehravani("maly", 100)
vloz_polozku("velky", "777", "Duna")
vloz_prehravani("velky", 9000)

scanner._merge_by_tmdb([(("777", -1, -1), "duna-nova")])
check(odsledovano("duna-nova") == 9000,
      "přenesla se historie té položky, kde bylo odsledováno nejvíc")
check(polozka("velky") is None, "vybraná položka se přejmenovala")
check(polozka("maly") is not None, "druhá zůstala nedotčená")


print()
print("--- archiv místo mazání ---")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = ?", ("jiny-film",))
check(polozka("jiny-film")["is_missing"] == 1, "položka jde označit jako archivovaná")
check(odsledovano("jiny-film") == 600, "archivace historii nemaže")

# Sloučení musí archivovanou položku zase probudit - soubor se vrátil.
scanner._merge_by_tmdb([(("999", -1, -1), "jiny-film-nove")])
check(polozka("jiny-film-nove")["is_missing"] == 0,
      "sloučení vrátí položku z archivu zpátky mezi živé")


print()
print("--- druhá kopie téhož dílu se slučovat nesmí ---")
# Rozdíl mezi "vyměněný soubor" a "dvě kopie": u výměny stará položka
# z Jellyfinu zmizela, u dvou kopií tam obě dál jsou. Slučování to samo
# nepozná - obě mají stejné tmdb + řadu + díl -, takže by jednu smazalo.
# Příští scan by ji vrátil a smazal tu druhou; položky by se střídaly
# a odkaz na tu právě sežranou házel 404.


def vloz_epizodu(item_id: str, tmdb: str, rada: int, dil: int,
                 synced_at: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (id, name, type, tmdb_id, parent_index_number,
                                  index_number, is_missing, synced_at)
               VALUES (?,?,'Episode',?,?,?,0,?)""",
            (item_id, f"S{rada:02d}E{dil:02d}", tmdb, rada, dil,
             synced_at or db.utcnow()),
        )


vloz_epizodu("kopie-A", "1396", 3, 3)
vloz_prehravani("kopie-A", 900)

merged = scanner._merge_by_tmdb([(("1396", 3, 3), "kopie-B")],
                                chranena={"kopie-A"})
check(merged == 0, "položka, o které víme, že v Jellyfinu je, se nesloučí")
check(polozka("kopie-A") is not None, "a nezmizela")
check(odsledovano("kopie-A") == 900, "historie zůstala u ní")

# Druhý způsob, jak se to pozná: razítko `synced_at`. Co Jellyfin poslal
# v tomhle běhu, existuje - a existující položka se neslučuje. Plná
# synchronizace jiné ochranu nepotřebuje.
beh = db.utcnow()
vloz_epizodu("kopie-C", "1396", 4, 1, synced_at=beh)
merged = scanner._merge_by_tmdb([(("1396", 4, 1), "kopie-D")], videno_od=beh)
check(merged == 0, "položka viděná v tomhle běhu se nesloučí")
check(polozka("kopie-C") is not None, "zůstala")

# A protipól: opravdu vyměněný soubor. Stará položka má razítko z minula,
# protože ji Jellyfin už neposlal - tam sloučit musíme, jinak se historie
# rozpadne na dva tituly.
vloz_epizodu("vymeneny-stary", "1396", 5, 2, synced_at="2020-01-01 00:00:00")
vloz_prehravani("vymeneny-stary", 1800)
merged = scanner._merge_by_tmdb([(("1396", 5, 2), "vymeneny-novy")],
                                videno_od=beh)
check(merged == 1, "vyměněný soubor se sloučí dál")
check(odsledovano("vymeneny-novy") == 1800, "a historie se přenesla")

# Dávka, která se zrovna zapisuje, je živá z definice - ochrana se z ní
# vezme sama, aniž by ji volající musel vypisovat.
vloz_epizodu("davka-A", "1396", 6, 6)
vloz_prehravani("davka-A", 300)
radek = scanner._radek_polozky(
    {"Id": "davka-A", "Name": "S06E06", "Type": "Episode",
     "ParentIndexNumber": 6, "IndexNumber": 6,
     "SeriesProviderIds": {"Tmdb": "1396"}},
    "lib", {}, db.utcnow())
scanner._write_batch([radek], [], [(("1396", 6, 6), "davka-B")], True)
check(polozka("davka-A") is not None,
      "položku z právě zapisované dávky slučování nesebere")
check(odsledovano("davka-A") == 300, "a historie zůstala u ní")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
