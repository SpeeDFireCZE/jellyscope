# -*- coding: utf-8 -*-
"""Import napřed, připojení Jellyfinu až potom.

Přesně tenhle postup je běžný: nejdřív si člověk přenese historii
z Jellystatu nebo Playback Reportingu, teprve pak nastaví spojení
s Jellyfinem. V okamžiku importu tedy Jellyscope **nezná ani jednoho
uživatele, ani jediný titul** — v historii zůstanou otazníky, žádný typ
položky a ItemId, které nemusí odpovídat ničemu v knihovně.

Test hlídá, že po první synchronizaci knihovny se to všechno srovná samo:

  * jména uživatelů se doplní z tabulky `users`,
  * typ, knihovna a název seriálu se převezmou z `items`,
  * historie se přepne na titul, který má stejné tmdb ID (soubor se mezitím
    překódoval a Jellyfin mu dal nové ItemId),
  * a to i tehdy, když jeden zdroj píše identifikátory s pomlčkami
    a Jellyfin bez nich.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "napojeni.db")

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def radek(key: str) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            """SELECT user_id, user_name, item_id, item_type, library_id, series_name
                 FROM playback WHERE session_key LIKE ?""",
            (f"%{key}%",),
        ).fetchone()
    return dict(row) if row else {}


db.init_db()

# Identifikátory schválně ve dvou tvarech. Jellyfin je v API posílá bez
# pomlček, Jellystat i plugin Playback Reporting je běžně ukládají
# s pomlčkami — je to totéž číslo, jen jinak zapsané.
UZIVATEL_JELLYFIN = "aa11bb22cc33dd44ee55ff6600112233"
UZIVATEL_IMPORT = "aa11bb22-cc33-dd44-ee55-ff6600112233"

FILM_STARY = "0011223344556677889900aabbccddee"    # id před překódováním
FILM_NOVY = "ffeeddccbbaa00998877665544332211"     # id, které má Jellyfin teď
EPIZODA = "1122334455667788990011223344aabb"

zaloha = {
    "jf_playback_activity": [
        {"Id": 1, "ActivityDateInserted": "2026-07-01 20:00:00",
         "PlaybackDuration": "3600", "UserId": UZIVATEL_IMPORT,
         "UserName": "Karel", "NowPlayingItemId": FILM_STARY,
         "NowPlayingItemName": "Matrix"},
        {"Id": 2, "ActivityDateInserted": "2026-07-02 21:00:00",
         "PlaybackDuration": "1800", "UserId": UZIVATEL_IMPORT,
         "NowPlayingItemId": EPIZODA, "NowPlayingItemName": "Kancelář",
         "EpisodeId": EPIZODA, "SeriesName": "Kancelář"},
    ]
}


print("--- import bez připojeného Jellyfinu ---")
vysledek = asyncio.run(
    importers.import_jellystat_json(json.dumps(zaloha).encode("utf-8")))
check(vysledek.get("status") == "ok", f"import proběhl: {vysledek.get('status')}")
check(vysledek.get("imported") == 2, f"převzaly se 2 záznamy ({vysledek.get('imported')})")

film = radek(":1:")
# Záloha u prvního záznamu jméno nese, u druhého ne. Kde ho zdroj nepošle,
# musí zůstat otazník - vymýšlet si ho nebudeme.
check(film.get("user_name") == "Karel", "jméno, které záloha nesla, se použilo")
check(radek(":2:").get("user_name") == "?", "jinde je zatím otazník")
check(film.get("library_id") is None, "knihovna zatím není známá")
check(importers._orphan_item_ids() != [], "položky historie zatím nikam nevedou")


print()
print("--- teď se připojí Jellyfin: uživatelé, knihovna, tituly ---")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
        " VALUES (?,?,0,0,?)",
        (UZIVATEL_JELLYFIN, "Karel", db.utcnow()),
    )
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, synced_at) VALUES (?,?,?,?)",
        ("lib-filmy", "Filmy", "movies", db.utcnow()),
    )
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, synced_at) VALUES (?,?,?,?)",
        ("lib-serialy", "Seriály", "tvshows", db.utcnow()),
    )
    # Film má po překódování nové ItemId, ale stejné tmdb ID.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, tmdb_id, is_missing, synced_at)
           VALUES (?,?,?,?,?,0,?)""",
        (FILM_NOVY, "Matrix", "Movie", "lib-filmy", "603", db.utcnow()),
    )
    # Epizoda si id nechala - jen ho Jellyfin píše bez pomlček.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_name, is_missing, synced_at)
           VALUES (?,?,?,?,?,0,?)""",
        (EPIZODA, "Kancelář", "Episode", "lib-serialy", "Kancelář", db.utcnow()),
    )

# Tohle volá scanner.sync_library() hned po stažení knihovny. Jellyfin tu
# nakonfigurovaný není, takže krok "zeptej se na stará ItemId" selže
# a použije se shoda podle názvu - přesně jako u nedostupného serveru.
souhrn = asyncio.run(importers.link_imported_history())
check(isinstance(souhrn, dict), f"srovnání proběhlo: {souhrn}")


print()
print("--- uživatelé se spárovali navzdory jinému zápisu id ---")
for klic, popis in ((":1:", "film"), (":2:", "epizoda")):
    zaznam = radek(klic)
    check(zaznam.get("user_id") == UZIVATEL_JELLYFIN,
          f"{popis}: user_id je ve tvaru z Jellyfinu")
    check(zaznam.get("user_name") == "Karel", f"{popis}: doplnilo se jméno uživatele")


print()
print("--- historie našla tituly v knihovně ---")
film = radek(":1:")
check(film.get("item_id") == FILM_NOVY,
      "film se přepnul na nové ItemId (shoda názvu)")
check(film.get("item_type") == "Movie", "doplnil se typ Movie")
check(film.get("library_id") == "lib-filmy", "doplnila se knihovna")

epizoda = radek(":2:")
check(epizoda.get("item_id") == EPIZODA, "epizoda ukazuje na správný díl")
check(epizoda.get("item_type") == "Episode", "doplnil se typ Episode")
check(epizoda.get("series_name") == "Kancelář", "doplnil se název seriálu")
check(importers._orphan_item_ids() == [], "žádný záznam už nevisí ve vzduchu")


print()
print("--- párování podle tmdb, když Jellyfin staré id ještě zná ---")
# Druhá cesta: server odpoví, že staré ItemId mělo tmdb 603. Tvar odpovědi
# je stejný jako z API.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('import:jst:9:x', ?, ?, 'Matrix', ?, ?, 900, 0)""",
        (UZIVATEL_IMPORT, "jeste-starsi-id", db.utcnow(), db.utcnow()),
    )
polozek, radku = importers._link_by_tmdb(
    [{"Id": "jeste-starsi-id", "ProviderIds": {"Tmdb": "603"}}])
check(polozek == 1 and radku == 1, "tmdb spárovalo záznam s titulem v knihovně")
check(radek(":9:").get("item_id") == FILM_NOVY, "historie ukazuje na titul z knihovny")


print()
print("--- opakované spuštění už nic nemění ---")
# Ten záznam z minulé části je čerstvý, takže první průchod ještě má co
# dělat. Zajímá nás až ten druhý: srovnání musí být idempotentní, jinak by
# se při každé synchronizaci knihovny zbytečně přepisovala celá historie.
importers.refresh_playback_metadata()

zmeny = importers._sjednot_identifikatory()
check(zmeny == {"user_id": 0, "item_id": 0}, f"není co sjednocovat: {zmeny}")
zmeny = importers.refresh_playback_metadata()
check(all(v == 0 for v in zmeny.values()), f"není co doplňovat: {zmeny}")


print()
print("--- neznámý uživatel se nepřiřadí k nikomu ---")
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('import:jst:99:x', 'ffffffff-0000-0000-0000-000000000000', '?', ?, ?, ?, 60, 0)""",
        (FILM_NOVY, db.utcnow(), db.utcnow()),
    )
importers.refresh_playback_metadata()
cizi = radek(":99:")
check(cizi.get("user_id") == "ffffffff-0000-0000-0000-000000000000",
      "cizí id zůstalo nezměněné")
check(cizi.get("user_name") == "?", "raději otazník než smyšlené jméno")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
