# -*- coding: utf-8 -*-
"""Aktualizace staré databáze na nové schéma.

Kdo Jellyscope spustil dřív, má v databázi tabulky bez sloupců, které
přibyly později (`items.tmdb_id`, `playback.is_paused`). Start aplikace
je musí umět doplnit — a hlavně nesmí u toho spadnout.

Konkrétní past, kterou tenhle test hlídá: index `idx_items_tmdb` se
odkazuje na sloupec, který ve staré tabulce ještě není. Když se schéma
spustí celé najednou, index se zakládá dřív, než migrace stihne sloupec
přidat, a aplikace skončí na "no such column: tmdb_id". Proto init_db()
nejdřív založí tabulky, pak doplní sloupce a teprve nakonec indexy.

Stará databáze se tu nesimuluje ručně psaným SQL, ale **skutečným
schématem, ze kterého se vyškrtnou novější řádky** — díky tomu test
nezestárne spolu s ním.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = Path(tempfile.mkdtemp())
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(_tmp / "stara.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# ---------------------------------------------------------------------------
# Obě schémata musí popisovat tytéž tabulky
# ---------------------------------------------------------------------------
print("--- SQLite a PostgreSQL schéma vedle sebe ---")
#
# Schémata jsou dva soubory a sloupec se snadno přidá jen do jednoho.
# Na SQLite by se nic nestalo, na PostgreSQL by chyběl - a protože migrace
# ho pak stejně doplní, projevilo by se to až nekonzistencí mezi tím, co
# je v souboru, a tím, co je v databázi.


def _sloupce_tabulek(text: str) -> dict[str, set[str]]:
    """Z CREATE TABLE vytáhne názvy sloupců. Klíčová slova a omezení ne."""
    KLICOVA = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "REFERENCES"}
    tabulky: dict[str, set[str]] = {}
    jmeno = None
    for radek in text.splitlines():
        holy = radek.strip()
        if not holy or holy.startswith("--"):
            continue
        velky = holy.upper()
        if velky.startswith("CREATE TABLE"):
            # "CREATE TABLE IF NOT EXISTS jmeno (" -> jmeno
            slova = holy.replace("(", " ").split()
            jmeno = slova[-1].lower() if slova else None
            if jmeno:
                tabulky[jmeno] = set()
            continue
        if jmeno is None:
            continue
        if holy.startswith(")"):
            jmeno = None
            continue
        prvni = holy.split()[0].strip('",')
        if prvni.upper() not in KLICOVA:
            tabulky[jmeno].add(prvni.lower())
    return tabulky


sqlite_tabulky = _sloupce_tabulek(
    (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8"))
pg_tabulky = _sloupce_tabulek(
    (PROJECT / "jellyscope" / "schema_postgres.sql").read_text(encoding="utf-8"))

check(sqlite_tabulky and pg_tabulky, "z obou schémat se dají přečíst tabulky")
check(set(sqlite_tabulky) == set(pg_tabulky),
      "obě schémata mají tytéž tabulky "
      f"({set(sqlite_tabulky) ^ set(pg_tabulky) or 'shodné'})")
for jmeno in sorted(set(sqlite_tabulky) & set(pg_tabulky)):
    rozdil = sqlite_tabulky[jmeno] ^ pg_tabulky[jmeno]
    check(not rozdil, f"tabulka {jmeno} má v obou stejné sloupce"
                      + (f" (liší se: {sorted(rozdil)})" if rozdil else ""))

print()
# ---------------------------------------------------------------------------
# Rozdělení schématu na tabulky a indexy
# ---------------------------------------------------------------------------
print("--- rozdělení schématu ---")

for jmeno in ("schema.sql", "schema_postgres.sql"):
    text = (PROJECT / "jellyscope" / jmeno).read_text(encoding="utf-8")
    tabulky, indexy = db._oddel_indexy(text)

    # Nic se nesmí ztratit ani vymyslet - jen přeskládat.
    check(sorted((tabulky + indexy).split()) == sorted(text.split()),
          f"{jmeno}: rozdělení nic neztratilo")

    vsechny = len(re.findall(r"(?mi)^CREATE (?:UNIQUE )?INDEX", text))
    check(len(re.findall(r"(?mi)^CREATE (?:UNIQUE )?INDEX", indexy)) == vsechny,
          f"{jmeno}: všech {vsechny} indexů je ve druhé části")
    check("CREATE INDEX" not in tabulky.upper(),
          f"{jmeno}: v části s tabulkami žádný index nezůstal")
    check("CREATE TABLE" in tabulky.upper() and "CREATE TABLE" not in indexy.upper(),
          f"{jmeno}: tabulky zůstaly v první části")


# ---------------------------------------------------------------------------
# Skutečná stará databáze
# ---------------------------------------------------------------------------
print()
print("--- start nad starou databází ---")

NOVE_SLOUPCE = {"items": "tmdb_id", "playback": "is_paused"}

schema = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")
stare = schema
for sloupec in NOVE_SLOUPCE.values():
    # Vyhodíme definici sloupce i každý index, který se na něj odkazuje -
    # přesně tak vypadala databáze, než sloupec přibyl.
    stare = re.sub(rf"(?mi)^\s*{sloupec}\s+[A-Z].*\n", "", stare)
    stare = re.sub(rf"(?mis)^CREATE (?:UNIQUE )?INDEX[^;]*\({sloupec}\);\n", "", stare)

for sloupec in NOVE_SLOUPCE.values():
    check(f" {sloupec} " not in stare and f"({sloupec})" not in stare,
          f"zkušební staré schéma opravdu nezná {sloupec}")

cesta = _tmp / "stara.db"
spojeni = sqlite3.connect(cesta)
spojeni.executescript(stare)
spojeni.commit()

for tabulka, sloupec in NOVE_SLOUPCE.items():
    sloupce = [r[1] for r in spojeni.execute(f"PRAGMA table_info({tabulka})")]
    check(sloupec not in sloupce, f"stará databáze nemá {tabulka}.{sloupec}")

# Ať je co ztratit: řádek, který musí aktualizaci přežít.
spojeni.execute("INSERT INTO libraries (id, name) VALUES ('lib', 'Filmy')")
spojeni.execute(
    """INSERT INTO items (id, name, type, library_id, date_created,
                          is_missing, synced_at)
       VALUES ('film-1', 'Starý film', 'Movie', 'lib', ?, 0, ?)""",
    ("2020-01-01T00:00:00", "2020-01-01T00:00:00"),
)
spojeni.commit()
spojeni.close()

try:
    pridane = db.init_db()
    check(True, "init_db() nad starou databází projde")
except Exception as chyba:                      # noqa: BLE001
    check(False, f"init_db() spadlo: {chyba}")
    pridane = []

for tabulka, sloupec in NOVE_SLOUPCE.items():
    check(f"{tabulka}.{sloupec}" in pridane, f"init_db() hlásí doplnění {tabulka}.{sloupec}")

with db.connect() as conn:
    for tabulka, sloupec in NOVE_SLOUPCE.items():
        check(sloupec in conn.table_columns(tabulka), f"{tabulka}.{sloupec} v databázi je")

    indexy = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    check("idx_items_tmdb" in indexy, "index idx_items_tmdb se založil až po migraci")

    radek = conn.execute("SELECT name, tmdb_id FROM items WHERE id = 'film-1'").fetchone()
    check(radek is not None and radek["name"] == "Starý film", "původní data zůstala")
    check(radek is not None and radek["tmdb_id"] is None, "nový sloupec je zatím prázdný")

# Druhý start nesmí dělat nic navíc - migrace se nesmí opakovat.
check(db.init_db() == [], "druhý start už nic nedoplňuje")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
