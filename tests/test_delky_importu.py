# -*- coding: utf-8 -*-
r"""Převzatá historie: patnáct hodin u dvacetiosmiminutového dílu.

Playback Reporting i Jellystat měří čas **na hodinách** - od spuštění po
konec relace. Když někdo usne u dílu a přehrávač zůstane otevřený do
rána, hlásí zdroj patnáct hodin sledování. Jellyscope to přebíral tak,
jak to přišlo, takže jeden díl přebil ve statistikách celý měsíc.

Ze skutečných dat, která to odhalila:

    Záplava kostlivců  17:25:58 → 08:58:09  =  55 931 s  (díl má 28 min)
    Zoo                06:22:39 → 07:36:40  =  90 841 s

V obou případech se `watched_seconds` rovnalo rozdílu časů na vteřinu
přesně - nikdo nic nesledoval, jen běžel přehrávač.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_delky_importu.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "delky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
TIK = 10_000_000


def zapis(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def titul(item_id: str, jmeno: str, minut: int | None) -> None:
    zapis(
        """INSERT INTO items (id, name, type, library_id, runtime_ticks, is_missing)
           VALUES (?,?,'Episode','lib',?,0)""",
        (item_id, jmeno, minut * 60 * TIK if minut else None),
    )


def prehrani(klic: str, item_id: str, sekund: int, pauza: int = 0) -> int:
    zapis(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 item_name, item_type, started_at, last_seen_at,
                                 watched_seconds, paused_seconds, is_active)
           VALUES (?,'u1','se',?,'x','Episode',?,?,?,?,0)""",
        (klic, item_id, db.utcnow(), db.utcnow(), sekund, pauza),
    )
    return int(db.query_value(
        "SELECT id FROM playback WHERE session_key = ?", (klic,)))


titul("dil-28", "Záplava kostlivců", 28)
titul("dil-45", "Zoo", 45)
titul("bez-delky", "Neznámá délka", None)

# Přesně ty dva záznamy, které to odhalily.
kostlivci = prehrani("import:pbr:1", "dil-28", 55931)
zoo = prehrani("import:jst:1", "dil-45", 90841)
# Poctivé sledování téhož dílu - toho se to dotknout nesmí.
poctive = prehrani("import:pbr:2", "dil-28", 1500)
# Trochu přes délku (převíjení, opakovaná scéna) taky ne.
skoro = prehrani("import:pbr:3", "dil-28", 28 * 60 + 200)
# Vlastní sběr má přírůstek omezený už při měření - nesaháme na něj.
vlastni = prehrani("demo-sess-9::dil-28", "dil-28", 55931)
# A u titulu bez známé délky není podle čeho soudit.
nezname = prehrani("import:pbr:4", "bez-delky", 55931)

print("--- co se zkrátí ---")
vysledek = importers.zkrat_nesmyslne_delky()
check(vysledek["rows"] == 2, f"zkrátily se dva záznamy ({vysledek['rows']})")


def sekundy(radek_id: int) -> tuple[int, int]:
    radek = db.query_one(
        "SELECT watched_seconds, paused_seconds FROM playback WHERE id = ?",
        (radek_id,))
    return int(radek["watched_seconds"]), int(radek["paused_seconds"])


videno, pauza = sekundy(kostlivci)
check(videno == int(28 * 60 * 1.5),
      f"z patnácti hodin zbylo 1,5násobku dílu ({videno} s)")
check(videno + pauza == 55931,
      f"a přebytek se přelil do pauzy, součet sedí ({videno} + {pauza})")

videno, _ = sekundy(zoo)
check(videno == int(45 * 60 * 1.5), f"totéž u delšího dílu ({videno} s)")

print()
print("--- čeho se to nedotkne ---")
check(sekundy(poctive)[0] == 1500, "poctivé sledování zůstane")
check(sekundy(skoro)[0] == 28 * 60 + 200, "ani mírné přetažení nevadí")
check(sekundy(vlastni)[0] == 55931, "vlastní sběr se neupravuje")
check(sekundy(nezname)[0] == 55931, "bez známé délky není podle čeho soudit")

print()
print("--- druhé spuštění nemá co dělat ---")
check(importers.zkrat_nesmyslne_delky()["rows"] == 0, "opakování nic nemění")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
