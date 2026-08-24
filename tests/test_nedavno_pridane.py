# -*- coding: utf-8 -*-
r"""Jeden velký seriál nesmí vytlačit všechno ostatní z „Nedávno přidané".

Co se stalo: v Jellyfinu se špatně určil seriál a každý díl se přidal
jako samostatný titul. Po opravě metadat se všechny díly zapsaly znovu,
takže dostaly dnešní datum přidání.

Výpis přitom bral pevný počet nejnovějších ŘÁDKŮ a teprve ty seskupoval
podle seriálu. Dvě stě dílů s dnešním datem tenhle strop zaplnilo samo,
takže v „Nedávno přidané" zbyl jediný seriál a nic dalšího - a už se to
nevrátilo, protože ta data zůstala nejnovější.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_nedavno_pridane.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "nedavno.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
TED = datetime.now(timezone.utc).replace(tzinfo=None)


def cas(pred_hodinami: float) -> str:
    return (TED - timedelta(hours=pred_hodinami)).strftime(db.TIME_FORMAT)


def zapis(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


zapis("INSERT INTO libraries (id, name, collection_type)"
      " VALUES ('lib','Knihovna','movies')")


def film(item_id: str, jmeno: str, pridano: str) -> None:
    zapis(
        """INSERT INTO items (id, name, type, library_id, date_created, is_missing)
           VALUES (?,?,'Movie','lib',?,0)""",
        (item_id, jmeno, pridano),
    )


def dil(item_id: str, cislo: int, pridano: str, serial: str = "serial-1") -> None:
    zapis(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              is_missing)
           VALUES (?,?,'Episode','lib',?,'Velky serial',1,?,?,0)""",
        (item_id, f"{cislo}. dil", serial, cislo, pridano),
    )


print("--- seriál o dvou stech dílech, přidaný celý najednou ---")
# Přesně to, co udělá Jellyfin po opravě špatně určeného seriálu.
for cislo in range(200):
    dil(f"dil-{cislo}", cislo + 1, cas(1))

# A pět filmů, které přišly dřív. Ty ve výpisu zůstat MAJÍ - jsou to
# poslední přírůstky, které tomu seriálu předcházely.
for cislo in range(5):
    film(f"film-{cislo}", f"Film {cislo}", cas(24 + cislo))

vypis = stats.recently_added(limit=18)
tituly = [radek["title"] for radek in vypis]
check(len(vypis) > 1, f"ve výpisu není jen ten jeden seriál ({len(vypis)} položek)")
check("Velky serial" in tituly, "seriál tam je - přišel poslední")
check(all(f"Film {i}" in tituly for i in range(5)),
      f"a filmy z něj nezmizely ({tituly})")

print()
print("--- seriál je jeden řádek, ne dvě stě ---")
check(tituly.count("Velky serial") == 1, "seriál se vypíše jednou")
serial = next(r for r in vypis if r["title"] == "Velky serial")
check(len(serial["episodes"]) == 200,
      f"a nese celou dávku dílů ({len(serial['episodes'])})")

print()
print("--- pořadí zůstává podle data přidání ---")
check(tituly[0] == "Velky serial", "nejnovější je první")
check(tituly[1] == "Film 0", f"pak nejnovější film ({tituly[1]})")

print()
print("--- limit platí na skupiny, ne na řádky ---")
for cislo in range(30):
    film(f"dalsi-{cislo}", f"Další {cislo}", cas(48 + cislo))
check(len(stats.recently_added(limit=18)) == 18,
      "vypíše se přesně tolik skupin, kolik se řeklo")
check(len(stats.recently_added(limit=3)) == 3, "a menší limit taky sedí")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
