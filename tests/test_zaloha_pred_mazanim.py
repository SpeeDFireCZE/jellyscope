# -*- coding: utf-8 -*-
r"""Před každým mazáním se udělá záloha databáze - a bez ní se nemaže.

Dvě cesty, kudy Jellyscope maže schválně: noční Odklízení historie
a ruční zapomenutí diváka. Obě začínají zálohou, aby špatně nastavená
hranice nebo kliknutí na špatné jméno měly cestu zpět.

Co se tu ověřuje:

* zapomenutí diváka nejdřív udělá zálohu - a ta **obsahuje** to, co se
  vzápětí smazalo (jinak by byla k ničemu),
* bez nastavené složky jde záloha do `data/zalohy` vedle databáze,
* když se záloha nepovede, historie diváka **zůstane** a hláška řekne proč,
* diváka bez historie se nezálohuje (není co chránit),
* noční odklízení zálohuje jen když má co mazat, a bez zálohy zastaví,
* název zálohy ze dvou cest se liší, ale úklid i obnova ho berou jako
  každou jinou (začíná `jellyscope-`, končí `.db`).

Spuštění:
    .\.venv\Scripts\python.exe tests\test_zaloha_pred_mazanim.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "zaloha.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, odklizeni, tasks, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo"})


def naplnit(user_id: str, jmeno: str, kolik: int, kdy="2020-01-01") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, name, is_administrator,"
            " is_disabled, synced_at) VALUES (?,?,0,0,?)",
            (user_id, jmeno, db.utcnow()))
        for poradi in range(kolik):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,0)",
                (f"{user_id}-{kdy}-{poradi}", user_id, jmeno, f"i-{poradi}",
                 f"{kdy} 10:00:00", f"{kdy} 10:20:00", 1200))
        conn.commit()


def zalohy(slozka: Path) -> list[Path]:
    return sorted(slozka.glob("jellyscope-*.db")) if slozka.exists() else []


def v_zaloze(soubor: Path, user_id: str) -> int:
    spojeni = sqlite3.connect(soubor)
    try:
        return spojeni.execute(
            "SELECT COUNT(*) FROM playback WHERE user_id = ?",
            (user_id,)).fetchone()[0]
    finally:
        spojeni.close()


def zapomen(user_id: str) -> str:
    odpoved = klient.post("/settings/historie/zapomen",
                          data={"user_id": user_id}, follow_redirects=True)
    text = re.sub(r"<[^>]+>", " ", odpoved.text)
    return re.sub(r"\s+", " ", text)


print("--- zapomenutí: záloha do data/zalohy, když složka není nastavená ---")
NAHRADNI = Path(_tmp) / "data" / "zalohy"
naplnit("u-mirek", "Mirek", 120)
check(not zalohy(NAHRADNI), "před kliknutím žádná záloha není")
text = zapomen("u-mirek")
soubory = zalohy(NAHRADNI)
check(len(soubory) == 1, f"po kliknutí je v data/zalohy jedna záloha ({len(soubory)})")
check(soubory and tasks.ZALOHA_PRED_MAZANIM in soubory[0].name,
      f"a v názvu je, proč vznikla ({soubory[0].name if soubory else '-'})")
check(soubory and v_zaloze(soubory[0], "u-mirek") == 120,
      "záloha obsahuje všech 120 řádků, které se vzápětí smazaly")
check(odklizeni.pocet_relaci("u-mirek") == 0, "a z databáze jsou pryč")
check("Záloha před smazáním:" in text and "pred-mazanim" in text,
      "hláška jmenuje soubor zálohy")

print()
print("--- zapomenutí: nastavená složka má přednost ---")
VLASTNI = Path(_tmp) / "moje-zalohy"
db.set_setting("backup_path", str(VLASTNI))
db.forget_settings()
naplnit("u-jana", "Jana", 30)
zapomen("u-jana")
check(len(zalohy(VLASTNI)) == 1, "záloha šla do nastavené složky")
check(len(zalohy(NAHRADNI)) == 1, "a do náhradní už ne")

print()
print("--- bez zálohy se nemaže ---")
# Cesta, kterou nejde vytvorit: soubor misto slozky.
blok = Path(_tmp) / "blok"
blok.write_text("tady slozka byt nemuze", encoding="utf-8")
db.set_setting("backup_path", str(blok / "zalohy"))
db.forget_settings()
naplnit("u-petr", "Petr", 40)
text = zapomen("u-petr")
check(odklizeni.pocet_relaci("u-petr") == 40, "historie diváka zůstala celá")
check("Historie se nesmazala" in text, "a hláška říká, že se nesmazalo")
check("nepovedla záloha" in text, "…protože se nepovedla záloha")
db.set_setting("backup_path", str(VLASTNI))
db.forget_settings()
# Petrova stara historie by jinak zustala nocnimu odklizeni nize.
zapomen("u-petr")

print()
print("--- naprázdno se nezálohuje ---")
pred = len(zalohy(VLASTNI))
text = zapomen("u-neexistuje")
check(len(zalohy(VLASTNI)) == pred, "divák bez historie = žádná záloha")
check("Nebylo co zapomenout" in text, "a hláška to řekne")

print()
print("--- noční odklízení ---")
db.set_setting("history_retention_enabled", "1")
db.set_setting(odklizeni.RETENCE_KLIC, str(odklizeni.MIN_DNU))
db.set_setting("task_purge_enabled", "1")
db.forget_settings()
# Sam jen cerstve zaznamy: neni co odklidit, nema se zalohovat.
naplnit("u-sam", "Sam", 10, kdy="2099-01-01")
pred = len(zalohy(VLASTNI))
vysledek = asyncio.run(tasks._run_purge())
check(vysledek["smazano"] == 0 and len(zalohy(VLASTNI)) == pred,
      "nic starého = nic smazaného a žádná záloha")

naplnit("u-stary", "Stary", 25)                     # 2020 = k odklizeni
vysledek = asyncio.run(tasks._run_purge())
check(vysledek["smazano"] == 25, f"odklidilo 25 starých ({vysledek['smazano']})")
check(len(zalohy(VLASTNI)) == pred + 1, "a předtím udělalo zálohu")
posledni = max(zalohy(VLASTNI), key=lambda p: p.stat().st_mtime)
check(v_zaloze(posledni, "u-stary") == 25, "v níž těch 25 řádků je")

db.set_setting("backup_path", str(blok / "zalohy"))
db.forget_settings()
naplnit("u-stary2", "Stary2", 15)
vysledek = asyncio.run(tasks._run_purge())
check(vysledek["status"] == "error", "bez zálohy odklízení skončí chybou")
check(odklizeni.pocet_relaci("u-stary2") == 15, "a nic nesmaže")
check("Bez zálohy se nemaže" in str(vysledek.get("message")),
      "s hláškou, která to říká")

print()
print("--- dvě zálohy v téže sekundě se nepřepíšou ---")
db.set_setting("backup_path", str(VLASTNI))
db.forget_settings()
naplnit("u-dvojce1", "Dvojce1", 5)
naplnit("u-dvojce2", "Dvojce2", 5)
pred = len(zalohy(VLASTNI))
zapomen("u-dvojce1")
zapomen("u-dvojce2")
check(len(zalohy(VLASTNI)) == pred + 2,
      f"dvě rychlá kliknutí = dvě zálohy ({len(zalohy(VLASTNI)) - pred})")

print()
print("--- úklid a obnova berou zálohu před mazáním jako každou jinou ---")
db.set_setting("backup_path", str(VLASTNI))
db.set_setting("backup_keep", "1")
db.forget_settings()
smazano = tasks._prune_backups(VLASTNI)
check(len(zalohy(VLASTNI)) == 1, f"úklid nechal jednu ({smazano} smazal)")
zbyla = zalohy(VLASTNI)[0]
check(zbyla.name.startswith("jellyscope-") and zbyla.name.endswith(".db"),
      f"název sedí na vzor obnovy ({zbyla.name})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
