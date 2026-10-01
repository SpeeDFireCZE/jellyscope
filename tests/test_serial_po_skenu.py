# -*- coding: utf-8 -*-
r"""Díly nového seriálu se v Nedávno přidaných nesmí rozpadnout.

Během skenu se položky v Jellyfinu mění pod rukama. Rychlá synchronizace
si díl uloží v okamžiku, kdy ho Jellyfin:

1. ještě **nezařadil k seriálu** (SeriesId chybí) - a pak ho zařadí
   pod stejným id, nebo
2. **založí znovu pod novým id** - starý záznam (bez seriálu) zmizí,
   nový je zařazený správně, oba se stejnou cestou a datem přidání.

Dřív se rychlá synchronizace vracela jen pět minut před poslední známý
titul, takže se k takovému dílu už nikdy nevrátila. V Nedávno přidaných
stál mimo svůj seriál jako samostatný díl, dokud ho neopravila plná
synchronizace. Teď se každý běh vrací o den a co Jellyfin neposlal,
ověří podle id.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_serial_po_skenu.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "serial.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, scanner, stats  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

ted = datetime.now(timezone.utc)


def cas(hodin_zpet: float) -> str:
    return (ted - timedelta(hours=hodin_zpet)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def dil(item_id: str, cislo: int, serial: str | None, hodin_zpet: float) -> dict[str, Any]:
    return {"Id": item_id, "Name": f"Díl {cislo}", "Type": "Episode",
            "SeriesId": serial, "SeriesName": "Kocour Mikeš" if serial else None,
            "IndexNumber": cislo, "ParentIndexNumber": 1,
            "DateCreated": cas(hodin_zpet),
            "Path": f"/media/serialy/Mikes/S01E{cislo:02d}.mkv"}


# Co Jellyfin zrovna zna. Test ho mezi behy meni - tak, jak se meni
# behem skenu.
KNIHOVNA: list[dict[str, Any]] = []


class FalesnyKlient:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FalesnyKlient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return []

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib", "Name": "Seriály", "CollectionType": "tvshows"}]

    async def items_page(self, start: int, limit: int, *a: Any, **k: Any) -> dict[str, Any]:
        serazene = sorted(KNIHOVNA, key=lambda i: i["DateCreated"], reverse=True)
        return {"Items": serazene[start:start + limit], "TotalRecordCount": len(serazene)}

    async def items_by_ids(self, ids: Any) -> list[dict[str, Any]]:
        return [i for i in KNIHOVNA if i["Id"] in set(ids)]

    async def _first_admin_id(self) -> str | None:
        return None

    recent_items = JellyfinClient.recent_items


scanner.JellyfinClient = FalesnyKlient          # type: ignore[assignment]


def rychla() -> dict[str, Any]:
    return asyncio.run(scanner.sync_recent())


def karty_serialu() -> list[dict[str, Any]]:
    return [k for k in stats.recently_added(20)
            if "Mikeš" in str(k.get("title") or "") or k["id"].startswith("e")]


# --- 1. beh: sken prave probiha ---------------------------------------
# Dily 1-3 uz jsou zarazene, dil 4 jeste ne, dil 5 Jellyfin zalozil pod
# docasnym id a bez serialu. Dil 6 je o dve hodiny novejsi - posune
# hranici, za kterou se puvodni rychla synchronizace uz nevracela.
KNIHOVNA[:] = [dil("e1", 1, "mikes", 3), dil("e2", 2, "mikes", 3),
               dil("e3", 3, "mikes", 3), dil("e4", 4, None, 3),
               dil("e5-docasny", 5, None, 3), dil("e6", 6, "mikes", 1)]
prvni = rychla()
check(prvni["status"] == "ok", f"první běh ({prvni.get('message')})")
# Historie na docasnem zaznamu - nekdo zacal koukat hned.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, is_active)"
        " VALUES ('k', 'u1', 'Jana', 'e5-docasny', 'Díl 5', 'Episode', ?, ?, 1200, 0)",
        (db.utcnow(), db.utcnow()))
    conn.commit()
pred = karty_serialu()
print(f"       po prvním běhu: {len(pred)} karet {[k['title'] for k in pred]}")
check(len(pred) > 1, "uprostřed skenu se seriál opravdu rozpadne (výchozí stav testu)")

# --- 2. beh: sken dobehl ------------------------------------------------
# Dil 4 je zarazeny (tote id), dil 5 ma nove id a docasne Jellyfin nezna.
KNIHOVNA[:] = [dil("e1", 1, "mikes", 3), dil("e2", 2, "mikes", 3),
               dil("e3", 3, "mikes", 3), dil("e4", 4, "mikes", 3),
               dil("e5", 5, "mikes", 3), dil("e6", 6, "mikes", 1)]
druhy = rychla()
check(druhy["status"] == "ok", f"druhý běh ({druhy.get('message')})")

e4 = db.query_one("SELECT series_id FROM items WHERE id = 'e4'")
check(e4 and e4["series_id"] == "mikes", "díl 4 je dodatečně zařazený k seriálu")
docasny = db.query_one("SELECT is_missing FROM items WHERE id = 'e5-docasny'")
check(docasny and docasny["is_missing"] == 1, "dočasný záznam dílu 5 je v archivu")
check(db.query_one("SELECT series_id FROM items WHERE id = 'e5'")["series_id"] == "mikes",
      "a nový díl 5 je pod seriálem")
historie = db.query_one("SELECT item_id FROM playback WHERE session_key = 'k'")
check(historie["item_id"] == "e5", "historie přešla na nový díl 5 (stejná cesta k souboru)")

po = karty_serialu()
check(len(po) == 1 and po[0]["title"] == "Kocour Mikeš",
      f"v Nedávno přidaných je jedna karta ({[k['title'] for k in po]})")
check(len(po[0]["episodes"]) == 6 if po else False,
      f"se všemi šesti díly ({len(po[0]['episodes']) if po else 0})")
zprava = str((scanner.last_scan("recent") or {}).get("message") or "")
check("znovu" in zprava or "again" in zprava, f"log úlohy to říká ({zprava})")

# --- 3. pojistky --------------------------------------------------------
# Starsi nez den se neoveruje - na to je plna synchronizace.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, date_created, is_missing, path)"
                 " VALUES ('stary', 'Starý film', 'Movie', ?, 0, '/m/stary.mkv')",
                 (cas(24 * 10),))
    conn.commit()
rychla()
check(db.query_one("SELECT is_missing FROM items WHERE id = 'stary'")["is_missing"] == 0,
      "položka starší než den, kterou Jellyfin neposlal, zůstává (není to práce rychlé synchronizace)")

# Kdyz Jellyfin polozku zna, ale neposlal ji (strop), nesmi se archivovat.
KNIHOVNA_ZALOHA = list(KNIHOVNA)
puvodni_recent = FalesnyKlient.recent_items


async def bez_dilu_2(self, od, strop=2000, item_types="Movie,Episode", parent_id=None):
    vse = await puvodni_recent(self, od, strop, item_types, parent_id)
    return [i for i in vse if i["Id"] != "e2"]

FalesnyKlient.recent_items = bez_dilu_2        # type: ignore[assignment]
rychla()
FalesnyKlient.recent_items = puvodni_recent    # type: ignore[assignment]
check(db.query_one("SELECT is_missing FROM items WHERE id = 'e2'")["is_missing"] == 0,
      "díl, který Jellyfin zná, ale v běhu neposlal, se neztratí")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
