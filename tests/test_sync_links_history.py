# -*- coding: utf-8 -*-
"""Synchronizace knihovny musí sama srovnat převzatou historii.

Předchozí test (test_import_then_connect) ověřuje, že *srovnání funguje*.
Tenhle ověřuje něco jiného a stejně důležitého: že se **opravdu spustí**,
když uživatel poprvé připojí Jellyfin a nechá stáhnout knihovnu. Kdyby se
volání ztratilo, všechno ostatní by bylo správně a naimportovaná data by
přesto zůstala s otazníky.

Jellyfin tu není — nahradí ho podvržený klient, který vrací připravená
data. Testujeme tak skutečný `scanner.sync_library()`, ne jeho popis.
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sync.db")

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


UZIVATEL = "aa11bb22cc33dd44ee55ff6600112233"
FILM = "0011223344556677889900aabbccddee"


class FalesnyKlient:
    """Nejmenší možná náhrada za JellyfinClient — jen co scanner potřebuje."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": UZIVATEL, "Name": "Karel", "Policy": {"IsAdministrator": False}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib-filmy", "Name": "Filmy", "CollectionType": "movies"}]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        return 1

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        yield {
            "Id": FILM,
            "Name": "Matrix",
            "Type": "Movie",
            "DateCreated": "2026-01-01T00:00:00.0000000Z",
            "ProviderIds": {"Tmdb": "603"},
            "RunTimeTicks": 60_000_000_000,
        }


db.init_db()

# Historie, jaká zůstane po importu bez připojeného Jellyfinu: jméno
# neznáme, typ neznáme a id uživatele je zapsané s pomlčkami.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 item_name, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('import:jst:1:x', 'aa11bb22-cc33-dd44-ee55-ff6600112233', '?',
                   ?, 'Matrix', ?, ?, 3600, 0)""",
        (FILM, db.utcnow(), db.utcnow()),
    )

pred = db.query_one("SELECT user_name, item_type, library_id FROM playback")
check(pred["user_name"] == "?", "před synchronizací je místo jména otazník")
check(pred["item_type"] is None, "před synchronizací není znám typ položky")


print()
print("--- synchronizace knihovny ---")
scanner.JellyfinClient = FalesnyKlient          # type: ignore[assignment]
vysledek = asyncio.run(scanner.sync_library())
check(vysledek.get("status") == "ok", f"synchronizace prošla: {vysledek}")
check(vysledek.get("users") == 1 and vysledek.get("items") == 1,
      f"stáhl se uživatel i titul: {vysledek}")


print()
print("--- historie se srovnala, aniž o to kdo žádal ---")
po = db.query_one(
    "SELECT user_id, user_name, item_type, library_id FROM playback")
check(po["user_id"] == UZIVATEL, "id uživatele je ve tvaru z Jellyfinu")
check(po["user_name"] == "Karel", "doplnilo se jméno uživatele")
check(po["item_type"] == "Movie", "doplnil se typ položky")
check(po["library_id"] == "lib-filmy", "doplnila se knihovna")

polozka = db.query_one("SELECT tmdb_id FROM items WHERE id = ?", (FILM,))
check(polozka["tmdb_id"] == "603", "titul má z Jellyfinu tmdb ID")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
