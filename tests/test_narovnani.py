# -*- coding: utf-8 -*-
r"""Narovnání dat: jedna akce, která záznam opravdu zařadí.

Co to hlídá:

  1. **Záznam visící na id SERIÁLU se zařadí ke konkrétnímu dílu.**
     Dřív se u něj jen doplnilo jméno seriálu - a když už tam bylo,
     neudělalo se nic. Tlačítko pak napsalo „Jellyfin zná 20 z 20“
     a víc nic, což vypadalo jako porouchaná funkce.

     Trik je v tom, že id seriálu **zužuje hledání**: název „5. díl“ má
     každý seriál, ale mezi díly jednoho seriálu už je jednoznačný.

  2. **Jedna akce místo dvou tlačítek.** Pořadí není na výběr - dohledání
     v Jellyfinu vyrábí vazby, se kterými pracuje všechno ostatní.

  3. **Když Jellyfin neodpoví**, zbytek stejně proběhne.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_narovnani.py
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
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "narovnani.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, importers, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

SERIAL_ID = "6a6152eddb1c02ccb5c3887497e3e64d"

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type)"
                 " VALUES ('lib', 'Seriály', 'tvshows')")
    for rada, dil, jmeno in ((1, 5, "Nákup"), (1, 6, "Výlet"), (2, 1, "Návrat")):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, series_id,"
            " series_name, parent_index_number, index_number, is_missing,"
            " synced_at) VALUES (?,?,'Episode','lib',?,?,?,?,0,?)",
            (f"ep-{rada}-{dil}", jmeno, SERIAL_ID, "Kancelář", rada, dil,
             db.utcnow()),
        )

    # Tři záznamy historie, všechny visí na id SERIÁLU - přesně jak to
    # chodí z převzatého importu.
    for i, nazev in enumerate(("Kancelář - S01E05 - Nákup",   # podle čísel
                               "Výlet",                        # podle názvu
                               "Něco, co v knihovně není")):   # nezařaditelné
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, series_name, item_type, started_at, last_seen_at,"
            " ended_at, watched_seconds) VALUES (?, 'u1', 'Jana', ?, ?,"
            " 'Kancelář', 'Episode', datetime('now','-3 hours'),"
            " datetime('now','-2 hours'), datetime('now','-2 hours'), 1500)",
            (f"s{i}", SERIAL_ID, nazev),
        )

serial_z_jellyfinu = {
    "Id": SERIAL_ID,
    "Name": "Kancelář",
    "Type": "Series",
    "Path": "/media/serialy/Kancelar",
    "ProviderIds": {"Tmdb": "2316"},
}


print("--- záznam na id seriálu se zařadí ke konkrétnímu dílu ---")
# Jméno seriálu už v záznamech JE - dřív to znamenalo, že se neudělá nic.
vysledek = importers.zaloz_z_jellyfinu([serial_z_jellyfinu])
check(vysledek["navazano"] == 2,
      f"dva ze tří záznamů se zařadily ({vysledek})")
check(vysledek["zalozeno"] == 0, "a nic se nezaložilo do knihovny")

radky = {r["item_name"]: r["item_id"] for r in db.query_all(
    "SELECT item_name, item_id FROM playback")}
check(radky.get("Nákup") == "ep-1-5", f"podle čísla dílu ({radky})")
check(radky.get("Výlet") == "ep-1-6", "podle názvu dílu")
check(radky.get("Něco, co v knihovně není") == SERIAL_ID,
      "co určit nejde, zůstane viset na seriálu - hádat nesmíme")

# Dvakrát po sobě nesmí nadělat nic navíc.
znovu = importers.zaloz_z_jellyfinu([serial_z_jellyfinu])
check(znovu["navazano"] == 0, f"podruhé už nemá co zařadit ({znovu})")


print()
print("--- narovnání dat je jedna akce ---")
vysledek = asyncio.run(importers.narovnej_data())
check(vysledek["jellyfin"]["status"] == "error",
      "Jellyfin v testu neodpovídá (adresa je schválně mrtvá)")
check("nazvy" in vysledek and "duplicity" in vysledek,
      f"a zbytek proběhl i bez něj ({sorted(vysledek)})")
check(isinstance(vysledek["zbyva"], int), "hlásí, kolik záznamů zbývá")
check(isinstance(vysledek["casti"], list), "a vrací věty do hlášky")


print()
print("--- úloha pouští totéž ---")
check("tidy" in tasks.TASKS, "úloha existuje")
uloha = tasks.TASKS["tidy"]
check(uloha.je_denni and tasks.denni_cas(uloha) == "04:00",
      f"je denní a má výchozí čas ({tasks.denni_cas(uloha)})")
check(tasks.is_enabled(uloha), "a je ve výchozím stavu zapnutá")
# Pořadí není náhodné: narovnání pracuje s tím, co synchronizace stáhla,
# a záloha už má ukládat srovnaná data.
casy = {k: tasks.denni_cas(t) for k, t in tasks.TASKS.items() if t.je_denni}
check(casy["sync"] < casy["tidy"] < casy["backup"],
      f"běží mezi synchronizací a zálohou ({casy})")

vysledek = asyncio.run(uloha.runner())
check(vysledek["status"] == "ok", f"úloha doběhne ({vysledek})")
check(vysledek["message"], "a řekne, co udělala")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
