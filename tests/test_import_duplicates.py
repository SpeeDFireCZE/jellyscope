# -*- coding: utf-8 -*-
"""Import nesmí zdvojit záznamy — ani po opakování, ani mezi zdroji.

Tři situace, které v praxi nastanou snadno:

  1. Tentýž soubor se nahraje dvakrát (klik navíc, nejistota, jestli to
     vyšlo).
  2. Táž historie se přenese z Jellystatu **i** z Playback Reportingu —
     obojí čte stejná data ze stejného Jellyfinu.
  3. Naimportuje se období, které už Jellyscope sám nasbíral.

Proti prvnímu stačí klíč záznamu (`import:jst:<id>:<položka>`). Druhý ani
třetí ale klíčem nepokryjeme: jiný zdroj má jiný tvar klíče a vlastní sběr
klíč z importu nemá vůbec. Proto se porovnává i obsah — stejný uživatel,
stejná položka a **překrývající se čas**.
"""
from __future__ import annotations

import asyncio
import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dup.db")

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def pocet() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM playback"))


UZIVATEL = "aa11bb22cc33dd44ee55ff6600112233"
FILM = "0011223344556677889900aabbccddee"

# Táž dvě přehrávání, jak je vidí každý ze zdrojů. Časy se schválně liší
# o pár desítek sekund — každý nástroj si zapisuje trochu jiný okamžik
# (začátek přehrávání vs. chvíli, kdy si to poznamenal).
JELLYSTAT = {
    "jf_playback_activity": [
        {"Id": 1, "ActivityDateInserted": "2026-07-01 20:00:00",
         "PlaybackDuration": "3600", "UserId": UZIVATEL,
         "NowPlayingItemId": FILM, "NowPlayingItemName": "Matrix"},
        {"Id": 2, "ActivityDateInserted": "2026-07-03 18:00:00",
         "PlaybackDuration": "1800", "UserId": UZIVATEL,
         "NowPlayingItemId": FILM, "NowPlayingItemName": "Matrix"},
    ]
}

PBR_SLOUPCE = ["rowid", "DateCreated", "UserId", "ItemId", "ItemType",
               "ItemName", "PlaybackMethod", "ClientName", "DeviceName",
               "PlayDuration"]
PBR_RADKY = [
    [10, "2026-07-01 20:00:40", UZIVATEL, FILM, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600],
    [11, "2026-07-03 18:00:25", UZIVATEL, FILM, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 1800],
]


def naimportuj_pbr(min_seconds: int = 60) -> dict[str, Any]:
    """Spustí import z Playback Reportingu s podvrženou odpovědí serveru."""
    class FalesnaOdpoved:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"colums": PBR_SLOUPCE, "results": PBR_RADKY}

    class FalesneHttp:
        """Nahrazuje httpx klienta uvnitř JellyfinClient."""

        async def post(self, cesta: str, json: Any = None) -> FalesnaOdpoved:
            # Kontrola u příležitosti: i tady musí jít dovnitř jen SELECT.
            dotaz = (json or {}).get("CustomQueryString", "")
            assert dotaz.upper().startswith("SELECT"), dotaz
            return FalesnaOdpoved()

    class FalesnyKlient:
        def __init__(self, *a: Any, **k: Any) -> None:
            self._client = FalesneHttp()

        async def __aenter__(self) -> "FalesnyKlient":
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

    puvodni = importers.JellyfinClient
    importers.JellyfinClient = FalesnyKlient          # type: ignore[assignment]
    try:
        return asyncio.run(importers.import_playback_reporting(min_seconds))
    finally:
        importers.JellyfinClient = puvodni            # type: ignore[assignment]


db.init_db()

print("--- 1) první import z Jellystatu ---")
v = asyncio.run(importers.import_jellystat_json(json.dumps(JELLYSTAT).encode()))
check(v.get("imported") == 2, f"naimportovaly se 2 záznamy ({v.get('imported')})")
check(pocet() == 2, f"v databázi jsou 2 řádky ({pocet()})")


print()
print("--- 2) týž soubor podruhé ---")
v = asyncio.run(importers.import_jellystat_json(json.dumps(JELLYSTAT).encode()))
check(v.get("imported") == 0, "nic nového se nenaimportovalo")
check(v.get("duplicate") == 2, f"oba záznamy poznal podle klíče ({v.get('duplicate')})")
check(pocet() == 2, f"počet řádků se nezměnil ({pocet()})")


print()
print("--- 3) táž data z Playback Reportingu ---")
# Klíče mají jiný tvar (pbr vs jst), takže je klíč nezachytí. Musí je
# poznat porovnání obsahu.
v = naimportuj_pbr()
check(v.get("status") == "ok", f"import proběhl: {v.get('status')}")
check(v.get("imported") == 0, f"nic se nezdvojilo ({v.get('imported')} nových)")
check(v.get("known_elsewhere") == 2,
      f"oba záznamy poznal jako už známé ({v.get('known_elsewhere')})")
check(pocet() == 2, f"v databázi jsou pořád 2 řádky ({pocet()})")


print()
print("--- 4) skutečně nové přehrávání projde ---")
PBR_RADKY.append(
    [12, "2026-07-20 21:00:00", UZIVATEL, FILM, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 2400])
v = naimportuj_pbr()
check(v.get("imported") == 1, f"přibyl jeden nový záznam ({v.get('imported')})")
check(pocet() == 3, f"v databázi jsou 3 řádky ({pocet()})")


print()
print("--- 5) záznam ze sběrače se taky nezdvojí ---")
# Vlastní sběr nemá s importem společný klíč vůbec.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, ended_at,
                                 watched_seconds, is_active)
           VALUES ('relace-abc:polozka', ?, ?, 'Matrix',
                   '2026-07-25 19:00:00', '2026-07-25 20:00:00',
                   '2026-07-25 20:00:00', 3600, 0)""",
        (UZIVATEL, FILM),
    )
PBR_RADKY.append(
    [13, "2026-07-25 19:00:50", UZIVATEL, FILM, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600])
v = naimportuj_pbr()
check(v.get("imported") == 0, f"sběrem pokryté období se nenaimportovalo ({v.get('imported')})")
# Tři: dva záznamy z Jellystatu (řádky 10 a 11, seznam je průběžný)
# a nově i ten, který mezitím zachytil sběrač.
check(v.get("known_elsewhere") == 3, f"poznal je jako už známé ({v.get('known_elsewhere')})")
check(pocet() == 4, f"počet řádků se nezměnil ({pocet()})")


print()
print("--- 6) překryv se posuzuje na uživatele a položku, ne globálně ---")
JINY_UZIVATEL = "bb22cc33dd44ee55ff6600112233aa11"
JINY_FILM = "ffeeddccbbaa00998877665544332211"
PBR_RADKY.append(
    # Stejný čas jako existující záznam, ale jiný divák - musí projít.
    [14, "2026-07-25 19:00:00", JINY_UZIVATEL, FILM, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600])
PBR_RADKY.append(
    # Stejný divák i čas, ale jiný film - taky musí projít.
    [15, "2026-07-25 19:00:00", UZIVATEL, JINY_FILM, "Movie", "Duna",
     "DirectPlay", "Web", "Chrome", 3600])
v = naimportuj_pbr()
check(v.get("imported") == 2, f"obě odlišná přehrávání prošla ({v.get('imported')})")
check(pocet() == 6, f"v databázi je 6 řádků ({pocet()})")


print()
print("--- 7) navazující díly se nepovažují za duplicitu ---")
# Konec jednoho a začátek druhého ve stejnou vteřinu není překryv:
# divák si mohl pustit další díl hned, jak dokoukal předchozí.
PBR_RADKY.append(
    [16, "2026-07-25 20:00:00", UZIVATEL, FILM, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 1800])
v = naimportuj_pbr()
check(v.get("imported") == 1, f"navazující přehrávání prošlo ({v.get('imported')})")


print()
print("--- 8) identifikátor s pomlčkami je tentýž uživatel ---")
S_POMLCKAMI = "aa11bb22-cc33-dd44-ee55-ff6600112233"
pred = pocet()
JELLYSTAT["jf_playback_activity"].append(
    {"Id": 99, "ActivityDateInserted": "2026-07-20 21:00:30",
     "PlaybackDuration": "2400", "UserId": S_POMLCKAMI,
     "NowPlayingItemId": FILM, "NowPlayingItemName": "Matrix"})
v = asyncio.run(importers.import_jellystat_json(json.dumps(JELLYSTAT).encode()))
check(v.get("imported") == 0,
      f"jiný zápis téhož id duplicitu neschová ({v.get('imported')} nových)")
check(pocet() == pred, f"počet řádků se nezměnil ({pocet()})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
