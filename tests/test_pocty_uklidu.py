# -*- coding: utf-8 -*-
r"""Počty pro „Narovnání dat" jsou rychlé – a pořád stejné.

Ta čísla se ukazují na dvou místech v Nastavení a počítají se při každém
otevření stránky. Na 150 000 přehrávání to dělalo **6,2 sekundy**, takže
se Nastavení otevíralo sedm sekund. Zrychlilo se to dvěma zásahy:

1. **Převod času z databáze nejde přes `strptime`.** Volá se dvakrát na
   každý řádek, tedy 300 000× při jednom otevření, a strptime rozebírá
   formátovací řetězec znovu při každém volání. Tvar je pevný, takže se
   počítá z pozic znaků – 4,8× rychleji.

2. **Bez importovaných záznamů se duplicity mezi zdroji nehledají.**
   Skupina se započítá jen tehdy, když je v ní aspoň jeden řádek
   z importu; kdo nikdy nic neimportoval, dostane vždycky prázdný
   výsledek – dosud až po průchodu celou historií.

Obojí je zrychlení, ne změna pravidel, a přesně to tenhle test hlídá:
**čísla musí zůstat stejná**. Kdyby se rychlá cesta chovala jinak než ta
původní, změnilo by to, které záznamy se považují za duplicitu – a podle
těch čísel se pak doopravdy maže.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_pocty_uklidu.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "pocty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def kdy(pred_minutami: int) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=pred_minutami)).strftime(db.TIME_FORMAT)


db.init_db()

print("--- převod času vrací totéž co dřív ---")
# Rychla cesta smi platit jen pro presne ten tvar, ktery v databazi je.
# Cokoliv jineho musi propadnout na strptime - vcetne veci, ktere strptime
# odmita. Kdyby rychla cesta prijala vic, zmenilo by to slucovani.
VZORKY = [
    "2026-09-09 10:20:30", "2020-01-01 00:00:00", "1999-12-31 23:59:59",
    "2024-02-29 12:00:00",              # přestupný rok
    "2026-13-45 99:99:99",              # tvar sedí, hodnoty ne
    "2026-09-09 10:20:30.123",          # milisekundy - strptime odmítá
    "2026-09-09T10:20:30",              # ISO s T
    "2026-09-09", "", "nesmysl", None, 12345,
]
for vzorek in VZORKY:
    rychle = importers._epocha(vzorek)
    poctive = importers._epocha_pomalu(vzorek) if vzorek else None
    check(rychle == poctive,
          f"{str(vzorek)[:24]!r}: {rychle!r} == {poctive!r}")

print()
print("--- bez importu se duplicity mezi zdroji nehledají ---")
with db.connect() as conn:
    for poradi in range(6):
        conn.execute(
            "INSERT INTO playback (session_key,user_id,user_name,item_id,"
            "item_name,started_at,ended_at,last_seen_at,watched_seconds,"
            "is_active) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (f"vlastni-{poradi}", "u1", "Petr", "i1", "Film",
             kdy(100 + poradi * 60), kdy(70 + poradi * 60),
             kdy(70 + poradi * 60), 1800))
    conn.commit()

check(importers._existuje_import() is False, "žádný import tu není")
check(importers.import_duplicate_groups() == [],
      "a duplicity mezi zdroji se nehledají")

print()
print("--- s importem se počítá dál a najde se totéž ---")
# Dva zaznamy o teze podivane: jeden vlastni, jeden z importu. Stejne
# dlouhe prehravani tehoz titulu tymz divakem v ramci jednoho dne.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key,user_id,user_name,item_id,"
        "item_name,started_at,ended_at,last_seen_at,watched_seconds,"
        "is_active) VALUES (?,?,?,?,?,?,?,?,?,0)",
        ("import:pbr:1:i9", "u9", "Jana", "i9", "Jiný film",
         kdy(300), kdy(270), kdy(270), 1800))
    conn.execute(
        "INSERT INTO playback (session_key,user_id,user_name,item_id,"
        "item_name,started_at,ended_at,last_seen_at,watched_seconds,"
        "is_active) VALUES (?,?,?,?,?,?,?,?,?,0)",
        ("vlastni-9", "u9", "Jana", "i9", "Jiný film",
         kdy(280), kdy(250), kdy(250), 1800))
    conn.commit()

check(importers._existuje_import() is True, "teď už tu import je")
skupiny = importers.import_duplicate_groups()
check(len(skupiny) == 1, f"našla se jedna skupina ({len(skupiny)})")
if skupiny:
    check(any(str(r["session_key"]).startswith("import:") for r in skupiny[0]),
          "a je v ní ten importovaný záznam")
check(importers.import_duplicate_count() == 1,
      f"počet k odstranění je 1 ({importers.import_duplicate_count()})")

print()
print("--- zkratka nesmí zakrýt skutečnou duplicitu ---")
# Past: kdyby se zkratka ptala spatne (treba na jiny prefix), tenhle
# pripad by tise vratil nulu a nikdo by si toho nevsiml.
check(importers.import_duplicate_count() > 0,
      "s importem v databázi vrací nenulový počet")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
