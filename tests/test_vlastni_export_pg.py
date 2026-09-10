# -*- coding: utf-8 -*-
r"""Vlastní export PostgreSQL (záloha bez pg_dump) musí jít obnovit.

Nalezeno 10. 9. 2026 na skutečném PostgreSQL 16: export psal schéma ze
`schema.sql` pro SQLite (`AUTOINCREMENT`), takže ho Postgres odmítl -
aplikací i `psql -f`. A zálohoval 8 tabulek z 12: bez snímků knihovny,
API klíčů, blokací a rozvržení nástěnky. Od 1.6.7 je to pojistka před
každým mazáním, takže záloha, která nejde obnovit, je horší než žádná -
vypadá, že chrání.

Sada běží bez Postgresu, tak se hlídá to, co se dá poznat ze souboru
a z kódu:

* seznam zálohovaných tabulek = tabulky ve `schema_postgres.sql`,
* export bere schéma pro PostgreSQL a nepřekládá ho,
* v exportu je i každý sloupec z `db.MIGRATIONS` (jinak INSERTy do čisté
  databáze neprojdou),
* obnova aplikací pouští soubor přímo přes ovladač, ne přes obal, který
  překládá otazníky a procenta - v datech je obojí.

Naostro (obnova aplikací i `psql -f`) ověřeno ručně proti PostgreSQL 16.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_vlastni_export_pg.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "export.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


print("--- seznam tabulek ---")
schema_pg = db.SCHEMA_POSTGRES.read_text(encoding="utf-8")
ve_schematu = re.findall(r"^CREATE TABLE (?:IF NOT EXISTS )?(\w+)", schema_pg, re.M)
check(set(ve_schematu) == set(tasks.ZALOHOVANE_TABULKY),
      f"zálohuje se každá tabulka ze schématu (chybí: "
      f"{sorted(set(ve_schematu) - set(tasks.ZALOHOVANE_TABULKY))})")
poradi = {t: i for i, t in enumerate(tasks.ZALOHOVANE_TABULKY)}
check(poradi["items"] < poradi["item_streams"] and poradi["accounts"] < poradi["dashboard_layout"],
      "pořadí respektuje cizí klíče (items před item_streams, accounts před dashboard_layout)")

print()
print("--- export ---")
db.init_db()
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
                 " VALUES ('u-a','Ada',0,0,?)", (db.utcnow(),))
    conn.execute("INSERT INTO items (id, name, type) VALUES ('i-x', 'Kdo? 100%', 'Movie')")
    conn.execute("INSERT INTO library_snapshot (den, polozek, filmu, epizod, velikost, uhd, hdr,"
                 " bez_technik, zapsano_v) VALUES ('2026-09-01',5,3,2,1000,1,1,0,'2026-09-01 00:00:00')")
    conn.commit()

soubor = Path(_tmp) / "vlastni.sql"
tasks._dump_vlastni(soubor)
text = soubor.read_text(encoding="utf-8")
sql_radky = [r for r in text.splitlines() if not r.lstrip().startswith("--")]
check(not any("AUTOINCREMENT" in r for r in sql_radky),
      "schéma v exportu je pro PostgreSQL (žádný AUTOINCREMENT)")
check("BIGSERIAL" in text or "GENERATED" in text or "SERIAL" in text,
      "…a je to skutečně schema_postgres.sql")
chybi = [f"{t}.{s}" for t, sl in db.MIGRATIONS.items() for s in sl
         if f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {s} " not in text]
check(not chybi, f"každý migrační sloupec má v exportu ALTER TABLE (chybí: {chybi})")
check("INSERT INTO library_snapshot" in text, "data snímků knihovny jsou v exportu")
check("'Kdo? 100%'" in text, "otazník a procento v datech zůstávají doslova")
check("INSERT INTO items" in text and "INSERT INTO users" in text, "data tabulek jsou v exportu")

print()
print("--- obnova aplikací nepřekládá ---")
zdroj = (PROJECT / "jellyscope" / "tasks.py").read_text(encoding="utf-8")
telo = zdroj[zdroj.index("def _obnov_postgres"):zdroj.index("def _najdi_psql")]
check("_raw.cursor()" in telo and "kurzor.execute(obsah)" in telo,
      "vlastní export se pouští přímo přes ovladač")
check("conn.execute(obsah)" not in telo,
      "ne přes obal, který by v datech zdvojil procenta a přepsal otazníky")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
