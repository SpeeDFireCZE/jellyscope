# -*- coding: utf-8 -*-
r"""Díl, který v knihovně existuje znovu, nesmí zůstat viset v archivu.

Co se dělo: v Jellyfinu se nahradil soubor jednoho dílu (jiná kvalita,
nový rip). Jellyfin to nebere jako změnu - založí **novou položku s novým
ItemId** a ta stará zmizí. Jellyscope ji tedy při synchronizaci označí
"chybí" a v detailu seriálu se objeví hláška "v archivu je navíc 3 dílů",
přestože ty díly v knihovně normálně jsou.

Slučování při synchronizaci to nechytlo: porovnává uložené `tmdb_id`
a starší záznamy (z importu historie nebo z doby před tím sloupcem)
žádné nemají. `scanner.slouc_archiv_do_zivych()` proto identitu bere
odjinud - seriál plus číslo řady a dílu.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_archiv_serialu.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "archiv.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def zapis(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def polozka(item_id: str, rada, dil, chybi: int, jmeno: str,
            tmdb: str | None = None, serial: str | None = "serial-1") -> None:
    zapis(
        """INSERT INTO items (id, name, type, series_id, series_name,
                              parent_index_number, index_number, library_id,
                              is_missing, tmdb_id)
           VALUES (?,?,'Episode',?,'Kancelar',?,?,'lib-tv',?,?)""",
        (item_id, jmeno, serial, rada, dil, chybi, tmdb),
    )


def prehrani(item_id: str, sekundy: int, klic: str) -> None:
    zapis(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 item_name, item_type, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES (?,'u1','Petr',?,'Karamboly','Episode',?,?,?,0)""",
        (klic, item_id, db.utcnow(), db.utcnow(), sekundy),
    )


zapis("INSERT INTO libraries (id, name, collection_type)"
      " VALUES ('lib-tv','Serialy','tvshows')")

# Živý díl (nový soubor) a dvě staré položky téhož dílu v archivu -
# přesně to, co bylo vidět na stránce seriálu: S04E04 dvakrát.
polozka("novy-s4e4", 4, 4, 0, "Karamboly s karambity")
polozka("stary-s4e4-a", 4, 4, 1, "Karamboly s karambity")
polozka("stary-s4e4-b", 4, 4, 1, "Karamboly s karambity")
# Díl, který v Jellyfinu opravdu už není - ten v archivu zůstat MÁ.
polozka("stary-s4e9", 4, 9, 1, "Posledni dil")
# A díl z jiného seriálu se stejnými čísly. Nesmí se do toho připlést.
polozka("cizi-s4e4", 4, 4, 1, "Uplne jiny serial", serial="serial-2")

prehrani("stary-s4e4-a", 1200, "sess-a")
prehrani("stary-s4e4-b", 300, "sess-b")

print("--- výchozí stav ---")
serial = stats.series_detail("serial-1")
check(serial["archived_count"] == 3,
      f"v archivu visí tři díly ({serial['archived_count']})")

print()
print("--- sloučení archivu se živými díly ---")
slouceno = scanner.slouc_archiv_do_zivych()
check(slouceno == 2, f"sloučily se dvě staré položky ({slouceno})")

serial = stats.series_detail("serial-1")
check(serial["archived_count"] == 1,
      f"v archivu zbyl jen díl, který v Jellyfinu opravdu není "
      f"({[d['name'] for d in serial['archived']]})")

check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'cizi-s4e4'") == 1,
      "cizí seriál se stejnými čísly zůstal nedotčený")

print()
print("--- historie přešla na živý díl ---")
sekundy = db.query_value(
    "SELECT SUM(watched_seconds) FROM playback WHERE item_id = 'novy-s4e4'")
check(sekundy == 1500, f"obě přehrávání sedí na novém dílu ({sekundy})")
check(db.query_value(
    "SELECT COUNT(*) FROM playback WHERE item_id IN ('stary-s4e4-a','stary-s4e4-b')") == 0,
    "a na starých položkách už nic nevisí")

print()
print("--- druhé spuštění nemá co dělat ---")
check(scanner.slouc_archiv_do_zivych() == 0, "opakování nic nemění")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
