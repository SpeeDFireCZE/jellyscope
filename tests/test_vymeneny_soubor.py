# -*- coding: utf-8 -*-
r"""Vyměněný soubor nenechá v knihovně dvojníka - a druhou kopii nesloučí.

Když se v Jellyfinu vymění soubor (lepší rip, překódování), není to
změna: je to **nová položka s novým ItemId**. Rychlá synchronizace ji
přidá, jenže ta stará v knihovně zůstane - slučování podle TMDB id na ni
nedosáhne, dokud titul žádné nemá. Do plné synchronizace a narovnání dat
(tedy přes noc) pak v knihovně stojí tentýž film dvakrát.

Teď se to srovná hned po přidání: u každé novinky se hledá starší
dvojník a **Jellyfinu se ukáže přímo na jeho ItemId**. Ten jediný dotaz
je celé rozhodnutí:

Dvojník se hledá **nejdřív podle TMDB id** - to přežije překódování
i to, že si Jellyfin doplní jiný název. Teprve když ho titul nemá, jde se
podle názvu a roku (u dílu podle seriálu a čísel). U epizody tmdb id samo
nestačí: Jellyfin u dílu hlásí id celého seriálu.

* Jellyfin ho **nezná** -> soubor byl nahrazen. Starý jde do archivu
  a historie se přepíše na nový; „kolikrát jsem to viděl" patří filmu,
  ne souboru.
* Jellyfin ho **zná** -> je to druhá kopie (4K vedle 1080p) a slučovat
  se nesmí. Půlka historie by jinak zmizela.

Ověřeno i naostro proti Jellyfinu 10.11.11: výměna souboru nechá jeden
živý titul s historií, druhá kopie ve vlastní složce zůstane jako dva.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_vymeneny_soubor.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vymena.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


class PodvrzenyKlient:
    """Jellyfin, který zná jen to, co mu řekneme."""

    def __init__(self, zname: set[str]) -> None:
        self.zname = zname
        self.dotazy: list[list[str]] = []

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, str]]:
        self.dotazy.append(list(ids))
        return [{"Id": i} for i in ids if i in self.zname]


def polozka(item_id: str, nazev: str, typ: str = "Movie", rok: int = 2019,
            serial: str = "", rada: int | None = None,
            dil: int | None = None, tmdb: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, production_year, series_id,"
            " parent_index_number, index_number, tmdb_id, is_missing)"
            " VALUES (?,?,?,?,?,?,?,?,0)",
            (item_id, nazev, typ, rok, serial or None, rada, dil,
             tmdb or None))
        conn.commit()


def prehravani(session: str, item_id: str, kolik: int = 1) -> None:
    with db.connect() as conn:
        for poradi in range(kolik):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, is_active) VALUES (?,?,?,?,?,?,?,?,0)",
                # Kazdy zaznam jiny den: stejny titul ve stejnou minutu
                # je pro narovnani dat duplicita a slouci se - spravne,
                # jen by to tenhle test meril mimo.
                (f"{session}-{poradi}", "u-1", "Divák", item_id, "Film",
                 f"2026-09-0{poradi + 1} 10:00:00",
                 f"2026-09-0{poradi + 1} 10:20:00", 1200))
        conn.commit()


def stav(item_id: str) -> tuple[int, int]:
    """(je v archivu, kolik má přehrávání)"""
    archiv = db.query_value("SELECT is_missing FROM items WHERE id = ?",
                            (item_id,), default=-1)
    hraní = db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = ?",
                           (item_id,), default=0)
    return (int(archiv), int(hraní))


print("--- bez tmdb id se dvojník pozná podle názvu a roku ---")
polozka("film-stary", "Zkouška", rok=2019)
polozka("film-novy", "Zkouška", rok=2019)
polozka("film-jiny", "Něco jiného", rok=2019)
nalezeno = scanner.dvojnici_novych(["film-novy"])
check(nalezeno.get("film-novy") == ["film-stary"],
      f"našel se starší se stejným názvem a rokem ({nalezeno})")
check("film-jiny" not in str(nalezeno), "jiný film se do toho neplete")

polozka("dil-stary", "1. díl", typ="Episode", serial="ser-1", rada=1, dil=1)
polozka("dil-novy", "1. díl", typ="Episode", serial="ser-1", rada=1, dil=1)
polozka("dil-jiny", "2. díl", typ="Episode", serial="ser-1", rada=1, dil=2)
nalezeno = scanner.dvojnici_novych(["dil-novy"])
check(nalezeno.get("dil-novy") == ["dil-stary"],
      f"u dílu rozhoduje seriál a čísla řady a dílu ({nalezeno})")

print()
print("--- rozhoduje TMDB id, ne název ---")
# Jellyfin si k prekodovanemu souboru casto doplni jiny nazev (jiny
# jazyk, jina edice). Podle nazvu by se dvojnik nenasel; tmdb id prezije.
polozka("tmdb-stary", "Zkouška jinak", rok=2001, tmdb="12345")
polozka("tmdb-novy", "Zkouška úplně jinak", rok=2002, tmdb="12345")
nalezeno = scanner.dvojnici_novych(["tmdb-novy"])
check(nalezeno.get("tmdb-novy") == ["tmdb-stary"],
      f"stejné tmdb id stačí, i když název a rok nesedí ({nalezeno})")

polozka("tmdb-jiny", "Něco", rok=2001, tmdb="99999")
nalezeno = scanner.dvojnici_novych(["tmdb-jiny"])
check(not nalezeno, "jiné tmdb id se nespáruje")

# U dilu je tmdb id SERIALU - samo o sobe by slilo celou radu.
polozka("ep-1", "1. díl", typ="Episode", serial="s-2", rada=1, dil=1,
        tmdb="777")
polozka("ep-2", "2. díl", typ="Episode", serial="s-2", rada=1, dil=2,
        tmdb="777")
polozka("ep-1-novy", "1. díl", typ="Episode", serial="s-2", rada=1, dil=1,
        tmdb="777")
nalezeno = scanner.dvojnici_novych(["ep-1-novy"])
check(nalezeno.get("ep-1-novy") == ["ep-1"],
      f"u dílu se k tmdb přidají čísla řady a dílu ({nalezeno})")
check("ep-2" not in str(nalezeno), "druhý díl se nesloučí")

polozka("ep-bez-cisla", "Speciál", typ="Episode", serial="s-2", rada=None,
        dil=None, tmdb="777")
nalezeno = scanner.dvojnici_novych(["ep-bez-cisla"])
check(not nalezeno,
      f"díl bez čísla se podle tmdb nepáruje vůbec ({nalezeno})")

print()
print("--- co Jellyfin nezná, je nahrazený soubor ---")
prehravani("stary", "film-stary", 3)
klient = PodvrzenyKlient(zname={"film-novy"})      # stary uz Jellyfin nezna
vysledek = asyncio.run(scanner.srovnej_po_novych(klient, ["film-novy"]))
check(klient.dotazy and "film-stary" in klient.dotazy[0],
      "Jellyfinu se ukázalo přímo na staré ItemId")
check(vysledek["archivovano"] == 1, f"starý šel do archivu ({vysledek})")
check(stav("film-stary") == (1, 0), f"a je prázdný ({stav('film-stary')})")
check(stav("film-novy") == (0, 3),
      f"historie je na novém ({stav('film-novy')})")

print()
print("--- co Jellyfin zná, je druhá kopie a nesahá se na ni ---")
polozka("kopie-stara", "Dvakrát", rok=2020)
polozka("kopie-nova", "Dvakrát", rok=2020)
prehravani("kopie", "kopie-stara", 2)
klient = PodvrzenyKlient(zname={"kopie-stara", "kopie-nova"})
vysledek = asyncio.run(scanner.srovnej_po_novych(klient, ["kopie-nova"]))
check(vysledek["archivovano"] == 0, f"nic se nearchivovalo ({vysledek})")
check(stav("kopie-stara") == (0, 2),
      f"stará kopie si nechala historii ({stav('kopie-stara')})")
check(stav("kopie-nova") == (0, 0), "a nová zůstala prázdná")

print()
print("--- bez novinek se Jellyfina nikdo neptá ---")
klient = PodvrzenyKlient(zname=set())
vysledek = asyncio.run(scanner.srovnej_po_novych(klient, []))
check(not klient.dotazy, "žádný dotaz")
check(vysledek["archivovano"] == 0, "a nic se nezměnilo")

polozka("samotny", "Sám doma", rok=1990)
klient = PodvrzenyKlient(zname={"samotny"})
vysledek = asyncio.run(scanner.srovnej_po_novych(klient, ["samotny"]))
check(not klient.dotazy and vysledek["dvojniku"] == 0,
      "titul bez dvojníka se neřeší vůbec")

print()
print("--- rychlá synchronizace to volá sama ---")
zdroj = (PROJECT / "jellyscope" / "scanner.py").read_text(encoding="utf-8")
telo = zdroj[zdroj.index("async def sync_recent"):zdroj.index("async def _sync_libraries")]
check("srovnej_po_novych(client, nova_id)" in telo,
      "sync_recent po nových titulech srovnává knihovnu")
i_volani = telo.index("srovnej_po_novych")
check("async with JellyfinClient" in telo[:i_volani]
      and telo[:i_volani].count("async with JellyfinClient") == 1,
      "a dělá to, dokud je spojení s Jellyfinem otevřené")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
