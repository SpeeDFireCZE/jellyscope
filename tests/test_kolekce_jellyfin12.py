# -*- coding: utf-8 -*-
r"""Jellyfin 12 schovává filmy za kolekce - Jellyscope si řekne, že nechce.

Reprodukováno 10. 9. 2026 na Jellyfinu 12.0.0: dotaz
`/Items?ParentId=<knihovna>&Recursive=true&IncludeItemTypes=Movie,Episode`
vrátil místo tří filmů z kolekce jednu položku typu `BoxSet` - i přes
filtr typu. V knihovně filmů pak stála kolekce a filmy z ní chyběly,
včetně sledovanosti. Parametr `CollapseBoxSetItems=false` to vypíná
(ověřeno naostro: 5 filmů, žádná kolekce); na 10.x je bez účinku.

Co se tu hlídá (bez Jellyfinu, přes podvržený server):

* každý dotaz na položky posílá `CollapseBoxSetItems=false`,
* když server přesto pošle obal (kolekci, seriál, složku), plná ani
  rychlá synchronizace ho nezapíše - ale nezařazený soubor (`Video`) ano,
* kolekce, která se do databáze dostala dřív, zmizí úklidem.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_kolekce_jellyfin12.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kolekce.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from jellyscope import db, scanner  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

FILM = {"Id": "f1", "Name": "Matrix", "Type": "Movie", "ProductionYear": 1999,
        "DateCreated": "2026-09-01T10:00:00Z"}
KOLEKCE = {"Id": "k1", "Name": "Harry Potter kolekce", "Type": "BoxSet",
           "IsFolder": True, "DateCreated": "2026-09-02T10:00:00Z"}
# Cerstve pridany soubor, ktery Jellyfin jeste nezaradil: posila ho jako
# `Video`. Ten se ulozit MA - pri dalsim behu se opravi na film nebo dil.
NEZARAZENY = {"Id": "v1", "Name": "nazev.souboru.mkv", "Type": "Video",
              "IsFolder": False, "DateCreated": "2026-09-03T10:00:00Z"}
DOTAZY: list[dict[str, list[str]]] = []


def server(zadost: httpx.Request) -> httpx.Response:
    """Podvržený Jellyfin 12: na položky vrátí film a kolekci."""
    DOTAZY.append(parse_qs(zadost.url.query.decode()))
    if zadost.url.path.endswith("/Items"):
        return httpx.Response(200, json={"Items": [FILM, KOLEKCE, NEZARAZENY],
                                         "TotalRecordCount": 3})
    return httpx.Response(200, json={})


def klient() -> JellyfinClient:
    k = JellyfinClient("http://jellyfin.test", "test-key", verze="12.0.0")
    k._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                  base_url="http://jellyfin.test")
    return k


print("--- dotazy říkají, že kolekce nechtějí ---")


async def dotazy():
    async with klient() as jf:
        await jf.items_page(0, 100, "Movie,Episode", None, "lib1")
        await jf.items_by_ids(["f1"])
        [i async for i in jf.iter_items(parent_id="lib1")]
        await jf.recent_items(None, parent_id="lib1")
        await jf.item_count(parent_id="lib1")


asyncio.run(dotazy())
bez = [d for d in DOTAZY if d.get("CollapseBoxSetItems") != ["false"]]
check(len(DOTAZY) >= 5 and not bez,
      f"všech {len(DOTAZY)} dotazů na položky nese CollapseBoxSetItems=false")

print()
print("--- co server přesto pošle, se nezapíše ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
                 " VALUES ('lib1', 'Filmy', 'movies', '[]', ?)", (db.utcnow(),))
    conn.commit()


async def plna():
    async with klient() as jf:
        return await scanner._sync_items_of_library(
            jf, {"id": "lib1", "name": "Filmy"}, use_jellyfin_tech=True)


pocet = asyncio.run(plna())
typy = sorted(r["type"] for r in db.query_all("SELECT type FROM items"))
check(typy == ["Movie", "Video"],
      f"plná synchronizace zapsala film i nezařazený soubor, ne kolekci ({typy})")
check(pocet == 2, f"a počítá jen zapsané ({pocet})")

db.query_all("DELETE FROM items")
nova = asyncio.run(scanner._uloz_nove_polozky([FILM, KOLEKCE, NEZARAZENY], "lib1", True))
typy = sorted(r["type"] for r in db.query_all("SELECT type FROM items"))
check(typy == ["Movie", "Video"] and sorted(nova) == ["f1", "v1"],
      f"rychlá synchronizace totéž ({typy}, nové {sorted(nova)})")
check(scanner.je_obal({"Type": "Movie", "IsFolder": True}), "obal se pozná i podle IsFolder")

print()
print("--- kolekce z dřívějška zmizí úklidem ---")
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, synced_at)"
                 " VALUES ('k-stara', 'Stará kolekce', 'BoxSet', 'lib1', ?)",
                 (db.utcnow(),))
    conn.commit()
uklizeno = scanner.uklid_fantomu()
check(uklizeno == 1 and db.query_value(
    "SELECT COUNT(*) FROM items WHERE type = 'BoxSet'") == 0,
    f"úklid odstranil kolekci ({uklizeno})")
check(db.query_value("SELECT COUNT(*) FROM items WHERE type = 'Video'") == 1,
      "a nezařazený soubor nechal být")
# Cizi druh, na ktery se v seznamu obalu nemyslelo (davny import), ma
# taky zmizet - jinak by ho `_mark_missing` kazdou noc znovu archivoval.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, synced_at)"
                 " VALUES ('mv1', 'Klip', 'MusicVideo', 'lib1', ?)", (db.utcnow(),))
    conn.commit()
check(scanner.uklid_fantomu() == 1 and db.query_value(
    "SELECT COUNT(*) FROM items WHERE type = 'MusicVideo'") == 0,
    "úklid se zbaví i druhu, na který se nemyslelo")
check(db.query_value("SELECT COUNT(*) FROM items WHERE type = 'Video'") == 1,
      "a `Video` pořád nechává být")
zdroj = (PROJECT / "jellyscope" / "scanner.py").read_text(encoding="utf-8")
telo = zdroj[zdroj.index("async def sync_library"):zdroj.index("async def _sync_users")]
check("uklid_fantomu" in telo, "a úklid běží i na konci plné synchronizace, ne jen při startu")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
