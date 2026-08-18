# -*- coding: utf-8 -*-
"""Dohledání položek k importované historii.

Playback Reporting ani Jellystat neposílají tmdb ID — posílají ItemId
z Jellyfinu, který se ale mění při každém překódování souboru. Import
proto často odkazuje na položky, které v knihovně už pod tím id nejsou.
Tenhle test hlídá, že se historie k titulům zase najde.
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
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "link.db")

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def polozka(item_id: str, name: str, tmdb: str | None = None,
            series: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, tmdb_id, series_name, is_missing, synced_at)"
            " VALUES (?,?,?,?,?,0,?)",
            (item_id, name, "Episode" if series else "Movie", tmdb, series, db.utcnow()),
        )


def historie(key: str, item_id: str, name: str, series: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback
               (session_key, item_id, item_name, series_name, started_at,
                last_seen_at, watched_seconds, is_active)
               VALUES (?,?,?,?,?,?,600,0)""",
            (key, item_id, name, series, db.utcnow(), db.utcnow()),
        )


def kde_je(key: str) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT item_id FROM playback WHERE session_key = ?", (key,)
        ).fetchone()
    return str(row["item_id"])


print("--- osiřelé záznamy se najdou ---")
polozka("v-knihovne", "Matrix", tmdb="603")
historie("import:1", "stare-id", "Matrix")          # osiřelý
historie("import:2", "v-knihovne", "Matrix")        # v pořádku

orphans = importers._orphan_item_ids()
check(orphans == ["stare-id"], f"nalezen jen osiřelý záznam: {orphans}")


print()
print("--- párování podle tmdb ID ---")
# Tohle by normálně přišlo z Jellyfinu; tvar odpovědi je stejný.
odpoved = [{"Id": "stare-id", "ProviderIds": {"Tmdb": "603"}}]
polozek, radku = importers._link_by_tmdb(odpoved)
check(polozek == 1, "spárovala se jedna položka")
check(radku == 1, "přepsal se jeden záznam historie")
check(kde_je("import:1") == "v-knihovne", "historie ukazuje na titul v knihovně")
check(kde_je("import:2") == "v-knihovne", "původně správný záznam se nezměnil")
check(importers._orphan_item_ids() == [], "žádný osiřelý záznam nezbyl")


print()
print("--- neznámé tmdb nespáruje nic ---")
historie("import:3", "cizi-id", "Neznámý film")
polozek, _ = importers._link_by_tmdb([{"Id": "cizi-id", "ProviderIds": {"Tmdb": "9999"}}])
check(polozek == 0, "tmdb, které v knihovně není, nespáruje nic")
check(kde_je("import:3") == "cizi-id", "záznam zůstal, kde byl")


print()
print("--- párování podle názvu, když Jellyfin id nezná ---")
polozek, radku = importers._link_by_name()
check(polozek == 0, "název 'Neznámý film' nemá v knihovně protějšek")

historie("import:4", "davno-pryc", "Matrix")
polozek, radku = importers._link_by_name()
check(polozek == 1, "shoda názvu spárovala jednu položku")
check(kde_je("import:4") == "v-knihovne", "historie našla titul podle názvu")


print()
print("--- nejednoznačná shoda se raději nespáruje ---")
polozka("duna-1080", "Duna")
polozka("duna-2160", "Duna")
historie("import:5", "duna-stare", "Duna")
polozek, _ = importers._link_by_name()
check(polozek == 0, "dva stejně pojmenované tituly = nepárujeme")
check(kde_je("import:5") == "duna-stare",
      "raději nespárováno než špatně přiřazeno")


print()
print("--- epizody se párují včetně seriálu ---")
polozka("ep-nova", "1. díl", series="Kancelář")
historie("import:6", "ep-stara", "1. díl", series="Kancelář")
polozek, _ = importers._link_by_name()
check(polozek == 1, "epizoda se spárovala")
check(kde_je("import:6") == "ep-nova", "historie epizody ukazuje na správný díl")


print()
print("--- celý průchod nespadne, i když Jellyfin neodpovídá ---")
# Jellyfin tu není nakonfigurovaný, takže první krok musí selhat
# a funkce má tiše pokračovat na shodu podle názvu.
vysledek = asyncio.run(importers.link_imported_history())
check(vysledek["status"] != "error" if "status" in vysledek else True,
      "funkce vrátila výsledek místo výjimky")
check("by_tmdb" in vysledek and "by_name" in vysledek,
      f"výsledek obsahuje oba způsoby: {vysledek}")

# Bez osiřelých záznamů se nemá dělat vůbec nic.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_id NOT IN (SELECT id FROM items)")
vysledek = asyncio.run(importers.link_imported_history())
check(vysledek["orphans"] == 0, "bez osiřelých záznamů se nic nedohledává")


print()
print("--- osiřelé se dohledají přímo v Jellyfinu ---")
# Nejsilnější stopa, kterou převzatá historie nese, je samotné ItemId -
# je pravé, jen k němu u nás nic nevede. Jellystat totiž ukládá jen název
# dílu ("7. epizoda", "Pilot") a o seriálu neřekne nic, takže párování
# podle jména takový záznam odmítá zařadit - a dělá dobře, ten název má
# každý seriál. Jellyfin ale to id zná a seriál i číslo dílu řekne.
now = db.utcnow()
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    conn.execute("DELETE FROM items")
    # Díl v knihovně - pod JINÝM id, než jaké nese historie.
    conn.execute(
        """INSERT INTO items (id, name, type, series_id, series_name, tmdb_id,
                              parent_index_number, index_number, is_missing, synced_at)
           VALUES ('nove-id','7. epizoda','Episode','ser-1','Kancelář','2316',
                   2,7,0,?)""", (now,))
    for i in range(3):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?,'u1','stare-id','7. epizoda','Episode',?,?,1800,0)""",
            (f"import:jst:u:{i}", now, now))
    # A díl seriálu, který v knihovně už není. Jellyfin ho zná, takže
    # aspoň doplní seriál - v přehledech se pak nezařadí jako film.
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 item_type, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('import:jst:u:9','u1','smazane-id','Pilot','Episode',?,?,1800,0)""",
        (now, now))


class JellyfinSPameti:
    """Jellyfin, který obě id zná - stejně jako ten skutečný."""

    dotazy: list[list[str]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "JellyfinSPameti":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        JellyfinSPameti.dotazy.append(sorted(ids))
        return [
            {"Id": "stare-id", "Name": "7. epizoda", "Type": "Episode",
             "SeriesId": "ser-1", "SeriesName": "Kancelář",
             "ParentIndexNumber": 2, "IndexNumber": 7,
             "SeriesProviderIds": {"Tmdb": "2316"}},
            {"Id": "smazane-id", "Name": "Pilot", "Type": "Episode",
             "SeriesId": "ser-x", "SeriesName": "Odpadlík",
             "ParentIndexNumber": 1, "IndexNumber": 1,
             "SeriesProviderIds": {"Tmdb": "999"}},
        ]


importers.JellyfinClient = JellyfinSPameti  # type: ignore[assignment]

check(importers.orphan_playback_count() == 4, "výchozí stav: čtyři osiřelé řádky")
check(importers.orphan_items_count() == 2, "ale jen dva tituly")

vysledek = asyncio.run(importers.dohledej_osirele_v_jellyfinu())
check(vysledek["nalezeno"] == 2, f"Jellyfin zná obě id ({vysledek})")
check(vysledek["navazano"] == 1,
      f"díl, který v knihovně je pod jiným id, se naváže ({vysledek['navazano']})")
check(vysledek["zalozeno"] == 1,
      f"a ten, který v knihovně chybí, se založí ({vysledek['zalozeno']})")

# To podstatné: id v převzaté historii JE Jellyfin id, takže jakmile
# položka existuje, záznamy na ni ukazují samy od sebe - a přijde s ní
# tmdb_id, seriál i čísla dílu, takže funguje slučování i zařazení.
zalozena = db.query_one("SELECT * FROM items WHERE id = 'smazane-id'")
check(zalozena is not None, "položka se opravdu založila")
check(zalozena["series_name"] == "Odpadlík" and zalozena["tmdb_id"] == "999",
      f"i se seriálem a tmdb ({zalozena['series_name']}, {zalozena['tmdb_id']})")
check(zalozena["parent_index_number"] == 1 and zalozena["index_number"] == 1,
      "a s čísly řady a dílu")
check(importers.orphan_playback_count() == 0,
      f"osiřelý nezůstal žádný ({importers.orphan_playback_count()})")
check(JellyfinSPameti.dotazy == [["smazane-id", "stare-id"]],
      f"ptáme se jedním dotazem na obě id ({JellyfinSPameti.dotazy})")

navazane = db.query_all("SELECT * FROM playback WHERE item_id = 'nove-id'")
check(len(navazane) == 3, f"všechny tři řádky přešly na položku z knihovny ({len(navazane)})")
check(navazane[0]["series_name"] == "Kancelář", "a vědí o seriálu")

# Podruhé už není co dohledávat - a položka, kterou jsme právě založili,
# se nesmí zakládat znovu.
druhy = asyncio.run(importers.dohledej_osirele_v_jellyfinu())
check(druhy["navazano"] == 0 and druhy["zalozeno"] == 0,
      f"opakované spuštění už nic nemění ({druhy})")
check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'smazane-id'") == 1,
      "a položka je pořád jen jedna")


print()
print("--- co se nepovedlo zařadit: seznam i s důvodem ---")
# Seznam se nikam neukládá a počítá se při každém otevření znovu.
# Uložený by ukazoval stav po posledním úklidu - tedy něco, co už
# nemusí platit.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    conn.execute("DELETE FROM items")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lb','Vše',?)",
                 (now,))
    # Dva seriály a v obou "7. epizoda" - proto je název nejednoznačný.
    for serial, jmeno in (("ser-1", "Kancelář"), ("ser-2", "Sherlock")):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing, synced_at)
               VALUES (?,'7. epizoda','Episode','lb',?,?,2,7,0,?)""",
            (f"{serial}-e7", serial, jmeno, now))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, is_missing, synced_at)
           VALUES ('film-duna','Duna','Movie','lb',0,?)""", (now,))

    for cislo, (nazev, serial, kolik) in enumerate([
            ("7. epizoda", None, 25),                      # víc shod
            ("Duna", None, 3),                             # jedna shoda
            ("Sherlock - s02e09 - Velká hra", None, 4),    # seriál je, díl ne
            ("Neznámý - s01e01 - Něco", None, 29),         # seriál není
            ("Longlegs", None, 2)]):                       # nic
        for n in range(kolik):
            conn.execute(
                """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                         series_name, item_type, started_at,
                                         last_seen_at, watched_seconds, is_active)
                   VALUES (?,'u1',?,?,?,'Episode',?,?,1800,0)""",
                (f"import:jst:{cislo}:{n}", f"osirele-{cislo}", nazev, serial, now, now))

podle_duvodu = {r["duvod"]: r for r in importers.rozbor_osirelych()}
check(podle_duvodu[importers.DUVOD_VIC_SHOD]["item_name"] == "7. epizoda",
      "díl jménem 7. epizoda spadne mezi nejednoznačné")
check(podle_duvodu[importers.DUVOD_VIC_SHOD]["shod"] == 2,
      f"a ví se, kolik shod to má ({podle_duvodu[importers.DUVOD_VIC_SHOD]['shod']})")
check(podle_duvodu[importers.DUVOD_JEDNA_SHODA]["item_name"] == "Duna",
      "titul s jedinou shodou se pozná")
check(podle_duvodu[importers.DUVOD_SERIAL_JE]["serial_z_nazvu"] == "Sherlock",
      "u složeného názvu se vyčte seriál")
check(importers.DUVOD_SERIAL_NENI in podle_duvodu, "seriál mimo knihovnu má svůj důvod")
check(importers.DUVOD_NIC in podle_duvodu, "a co nemá stopu vůbec, taky")
check(all(d in importers.DUVODY_POPIS for d in podle_duvodu),
      "každý důvod má svůj popis pro stránku")

# Jeden řádek na titul, ne na záznam - jinak by v seznamu, kde má člověk
# něco poznat podle názvu, byla stokrát tatáž věta.
check(podle_duvodu[importers.DUVOD_VIC_SHOD]["radku"] == 25,
      "u titulu je vidět, kolika záznamů se to týká")
check(len(importers.rozbor_osirelych()) == 5, "pět titulů, ne 63 záznamů")


print()
print("--- ruční přiřazení ---")
kandidati = importers.kandidati_pro_osireleho("Sherlock")
check(len(kandidati) == 1 and kandidati[0]["id"] == "ser-2-e7",
      f"hledání v knihovně najde díl podle seriálu ({kandidati})")
check(importers.kandidati_pro_osireleho("S") == [],
      "jedno písmeno se nehledá - vrátilo by celou knihovnu")

vysledek = importers.prirad_rucne("osirele-0", "ser-2-e7")
check(vysledek["status"] == "ok" and vysledek["rows"] == 25,
      f"přiřadí se všech 25 záznamů ({vysledek})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'ser-2-e7'") == 25,
      "a visí na vybraném dílu")
check(db.query_value("SELECT series_name FROM playback WHERE item_id = 'ser-2-e7'")
      == "Sherlock", "včetně seriálu")
check(len(importers.rozbor_osirelych()) == 4, "v seznamu o titul míň")

check(importers.prirad_rucne("osirele-1", "neexistuje")["status"] == "error",
      "cíl mimo knihovnu se odmítne")
check(importers.prirad_rucne("uz-neni", "film-duna")["status"] == "error",
      "a přiřazovat něco, co nikde není, taky nejde")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
