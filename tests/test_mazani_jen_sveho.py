# -*- coding: utf-8 -*-
r"""Mazání smaže přesně to, co má - a nic navíc.

Tohle je test, kvůli kterému se v noci spí. Obě mazací cesty dostávají
hodnotu zvenku (id diváka z formuláře, počet dní z nastavení) a obě
sahají na `DELETE`. Chyba v kterékoliv z nich znamená pryč všechno.

Co se hlídá:

* **Zapomenutí diváka** smaže jen jeho. Ostatní diváci musí mít po zásahu
  přesně tolik záznamů co předtím - kontroluje se každý zvlášť, ne jen
  součet, protože součet by lhal, kdyby se smazalo jednomu a přibylo
  jinému.
* **Podstrčená hodnota nesmaže nic.** `' OR '1'='1`, `%`, prázdno,
  mezery. Dotaz je parametrizovaný, takže by projít neměla - ale právě
  tohle je chyba, která se pozná až na prázdné databázi.
* **Odklízení podle stáří** nechá to, co je čerstvé, a **nikdy** nesahá
  na právě běžící relaci.
* **Nic jiného než historie.** Tabulky s tituly, knihovnami a účty musí
  zůstat beze změny.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_mazani_jen_sveho.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "mazani.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, odklizeni  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def kdy(pred_dny: int) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=pred_dny)).strftime("%Y-%m-%d %H:%M:%S")


def zaznam(user_id: str, jmeno: str, klic: str, stari_dnu: int,
           aktivni: int = 0) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " started_at, last_seen_at, watched_seconds, is_active)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (klic, user_id, jmeno, "i-1", kdy(stari_dnu), kdy(stari_dnu),
             1200, aktivni))
        conn.commit()


def pocty() -> dict[str, int]:
    """Kolik záznamů má který divák. Po každém zásahu se to porovnává."""
    return {r["user_id"]: r["n"] for r in db.query_all(
        "SELECT user_id, COUNT(*) AS n FROM playback GROUP BY user_id")}


db.init_db()
with db.connect() as conn:
    for uid, jmeno in (("u-1", "Petr"), ("u-2", "Jana"), ("u-3", "Mirek")):
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (uid, jmeno, db.utcnow()))
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
        " VALUES ('lib','Filmy','movies','[]',?)", (db.utcnow(),))
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, synced_at)"
        " VALUES ('i-1','Film','Movie','lib',?)", (db.utcnow(),))
    conn.commit()

for poradi in range(5):
    zaznam("u-1", "Petr", f"p-{poradi}", 400)
for poradi in range(4):
    zaznam("u-2", "Jana", f"j-{poradi}", 400)
for poradi in range(3):
    zaznam("u-3", "Mirek", f"m-{poradi}", 400)
zaznam("u-2", "Jana", "j-ziva", 0, aktivni=1)

print("--- podstrčená hodnota nesmaže nic ---")
PODSTRCENE = ["' OR '1'='1", "%", "_", "u-1' OR user_id LIKE '%",
              "", "   ", "'; DELETE FROM playback; --", "*"]
for zlobive in PODSTRCENE:
    pred = pocty()
    vysledek = odklizeni.zapomen_uzivatele(zlobive)
    check(vysledek["smazano"] == 0 and pocty() == pred,
          f"{zlobive[:26]!r} nesmazalo nic")

print()
print("--- zapomenutí smaže jen toho jednoho ---")
pred = pocty()
vysledek = odklizeni.zapomen_uzivatele("u-2")
po = pocty()
check(vysledek["smazano"] == 5, f"Jana měla 5 záznamů ({vysledek['smazano']})")
check("u-2" not in po, "a nezbyl jí žádný")
check(po.get("u-1") == pred["u-1"], f"Petrovi zůstalo {po.get('u-1')} z {pred['u-1']}")
check(po.get("u-3") == pred["u-3"], f"Mirkovi zůstalo {po.get('u-3')} z {pred['u-3']}")

print()
print("--- historie ano, zbytek ne ---")
check(db.query_value("SELECT COUNT(*) FROM users") == 3, "účty zůstaly")
check(db.query_value("SELECT COUNT(*) FROM items") == 1, "tituly zůstaly")
check(db.query_value("SELECT COUNT(*) FROM libraries") == 1, "knihovny zůstaly")

print()
print("--- odklízení nechá čerstvé i právě běžící ---")
for poradi in range(3):
    zaznam("u-2", "Jana", f"j2-{poradi}", 5)        # cerstve
zaznam("u-1", "Petr", "p-ziva", 500, aktivni=1)     # stara, ale bezi

pred = pocty()
vysledek = odklizeni.smaz_stare(30)
po = pocty()
# Stare jsou Petrovy (5) i Mirkovy (3) - odejit maji obe skupiny.
check(vysledek["smazano"] == 8, f"odešlo 8 starých ({vysledek['smazano']})")
check(po.get("u-2") == 3, f"Janiny čerstvé zůstaly ({po.get('u-2')})")
check(db.query_value(
    "SELECT COUNT(*) FROM playback WHERE session_key = 'p-ziva'") == 1,
    "právě běžící relace zůstala, i když je stará")
check("u-3" not in po, "Mirkovy staré odešly taky")

print()
print("--- počet dní se drží v mezích ---")
# Nula ani zaporne cislo nesmi znamenat "smaz vsechno" a velke cislo
# nesmi spadnout. Zbyva Janiny 3 cerstve + Petrova beziici.
# Nula a zaporne cislo posunou hranici na "ted" nebo do budoucnosti,
# obri cislo drive spadlo na OverflowError a text neni cislo vubec.
# Zadna z techhle hodnot nesmi zmenit ani radek.
for nesmysl in (0, -5, 10 ** 9, 10 ** 12, "nesmysl", None, ""):
    pred_pokusem = db.query_value("SELECT COUNT(*) FROM playback")
    try:
        odklizeni.smaz_stare(nesmysl)
        spadlo = ""
    except Exception as chyba:      # noqa: BLE001
        spadlo = type(chyba).__name__
    zbylo = db.query_value("SELECT COUNT(*) FROM playback")
    check(not spadlo, f"dnů={nesmysl!r}: nespadlo {spadlo}")
    check(zbylo == pred_pokusem,
          f"dnů={nesmysl!r}: zůstalo {zbylo} z {pred_pokusem}")

# Platne cislo naopak mazat SMI - i kdyz je mensi, nez dovoli formular.
# Desetinne se srovna na cele dny.
print()
print("--- platná hranice mazat smí ---")
pred_ostrym = db.query_value("SELECT COUNT(*) FROM playback")
vysledek = odklizeni.smaz_stare(1.5)
zbylo = db.query_value("SELECT COUNT(*) FROM playback")
check(vysledek["smazano"] == pred_ostrym - zbylo,
      f"jednodenní hranice odklidila {vysledek['smazano']}")
check(db.query_value(
    "SELECT COUNT(*) FROM playback WHERE is_active = 1") >= 1,
    "a běžící relace přežila i ji")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
