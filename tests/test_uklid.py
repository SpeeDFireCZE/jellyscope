# -*- coding: utf-8 -*-
"""Úklid: co v databázi nemá zůstávat ležet.

Dvě věci, které samy od sebe jen rostou nebo zůstávají po starých
verzích — a nikdo si toho nevšimne, protože nic nerozbijí:

1. **scan_log.** Rychlá synchronizace běží každých patnáct minut, takže
   denně přibude přes devadesát řádků. Nikdy se nic nemazalo, přitom se
   na tabulku ptá každé volání /health — a to chodí z každé otevřené
   karty každých deset vteřin.

2. **Zrušená nastavení.** Když nějaká volba z aplikace zmizí, řádek
   v tabulce `settings` zůstane. Kdo se do databáze podívá, marně hledá,
   proč se podle něj nic neřídí.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "uklid.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def pocet_zaznamu(kind: str) -> int:
    return int(db.query_value(
        "SELECT COUNT(*) FROM scan_log WHERE kind = ?", (kind,), default=0))


print("--- scan_log neroste donekonečna ---")
for _ in range(scanner.ZAZNAMU_NA_DRUH + 40):
    scanner.finish_task_log(scanner.start_task_log("recent"), "done")

check(pocet_zaznamu("recent") == scanner.ZAZNAMU_NA_DRUH,
      f"z {scanner.ZAZNAMU_NA_DRUH + 40} běhů zůstalo "
      f"{pocet_zaznamu('recent')} (strop {scanner.ZAZNAMU_NA_DRUH})")

# Prořezává se každý druh zvlášť - jinak by častá rychlá synchronizace
# vytlačila záznamy o zálohách, kterých je pár za měsíc.
scanner.finish_task_log(scanner.start_task_log("backup"), "done")
scanner.finish_task_log(scanner.start_task_log("library"), "done")
for _ in range(50):
    scanner.finish_task_log(scanner.start_task_log("recent"), "done")
check(pocet_zaznamu("backup") == 1, "záloha si svůj záznam podržela")
check(pocet_zaznamu("library") == 1, "synchronizace taky")

# A hlavně: poslední běh musí zůstat ten poslední, ne se ztratit s úklidem.
posledni = scanner.last_scan("recent")
nejvyssi = db.query_value("SELECT MAX(id) FROM scan_log WHERE kind = 'recent'")
check(posledni is not None and posledni["id"] == nejvyssi,
      "poslední záznam je pořád k dispozici")


print()
print("--- zrušená nastavení se ze staré databáze smažou ---")
with db.connect() as conn:
    for klic in db.ZRUSENA_NASTAVENI:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')"
                     " ON CONFLICT (key) DO UPDATE SET value = '1'", (klic,))
    conn.execute("INSERT INTO settings (key, value) VALUES ('vlastni_klic', 'x')"
                 " ON CONFLICT (key) DO NOTHING")
db.forget_settings()
check(all(k in db.get_settings() for k in db.ZRUSENA_NASTAVENI),
      "výchozí stav: stará nastavení v databázi jsou")

db.init_db()
db.forget_settings()
ulozena = db.get_settings()
check(not any(k in ulozena for k in db.ZRUSENA_NASTAVENI),
      f"po startu jsou pryč ({[k for k in db.ZRUSENA_NASTAVENI if k in ulozena]})")
check(ulozena.get("vlastni_klic") == "x",
      "cizí klíč zůstal - maže se jen jmenovitý seznam")
check(ulozena.get("library_sync_time") == "03:30",
      "a živá nastavení jsou na svém místě")


print()
print("--- počet přehrávání se nezjišťuje přes celý seznam ---")
# /health se ptá z každé otevřené karty každých deset vteřin. Dřív se
# kvůli jednomu číslu spojovaly tři tabulky a tahaly všechny sloupce.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at)"
                 " VALUES ('i1', 'Film', 'Movie', 0, ?)", (db.utcnow(),))
    for i in range(3):
        conn.execute(
            """INSERT INTO playback (session_key, item_id, item_name, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'i1', 'Film', ?, ?, 600, ?)""",
            (f"s{i}", db.utcnow(), db.utcnow(), 1 if i < 2 else 0),
        )

check(stats.active_session_count() == 2,
      f"počítají se jen běžící relace ({stats.active_session_count()})")
check(stats.active_session_count() == len(stats.active_sessions()),
      "číslo sedí s tím, co vrací plný seznam")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
