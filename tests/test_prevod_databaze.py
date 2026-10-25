# -*- coding: utf-8 -*-
r"""Převod databáze (Nastavení → Databáze): přenese se všechno.

Seznam tabulek k převodu byl psaný ručně a s novými tabulkami ho nikdo
nedoplnil - převod SQLite -> PostgreSQL tiše vynechal API klíče, přehledy,
verze souborů, denní snímky knihovny, blokace adres i zapomenuté diváky.
Hlídá se tu:

* převod zná každou tabulku ze schématu, v pořadí, v jakém se dají plnit;
* skutečný převod přenese řádky ze všech tabulek (i z koše zapomenutých);
* po převodu do PostgreSQL jdou zakládat nové řádky (čítače id posunuté).

Bez PostgreSQL se převádí do druhé SQLite; přes scratchpad/pg_nove.py
do PostgreSQL, tedy směr, o který jde.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_prevod_databaze.py
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
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cil.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import api, db, dbmigrate, dialect  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


print("--- seznam tabulek ---")
for nazev in ("schema.sql", "schema_postgres.sql"):
    text = (PROJECT / "jellyscope" / nazev).read_text(encoding="utf-8")
    ve_schematu = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", text))
    check(set(dbmigrate.TABLES) == ve_schematu,
          f"{nazev}: převod zná všechny tabulky (chybí {sorted(ve_schematu - set(dbmigrate.TABLES))})")
schema = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")
spatne_poradi = []
for tabulka, telo in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S):
    for cil in re.findall(r"REFERENCES (\w+)", telo):
        if dbmigrate.TABLES.index(cil) > dbmigrate.TABLES.index(tabulka):
            spatne_poradi.append(f"{tabulka} -> {cil}")
check(not spatne_poradi, f"tabulka s cizím klíčem až po té, na kterou odkazuje ({spatne_poradi})")

print("--- skutečný převod ---")
zdroj = dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(Path(_tmp) / "zdroj.db"))
db.init_db(zdroj)
T = "2026-10-20 10:00:00"
with db.connect(zdroj) as conn:
    for sql, hodnoty in (
        ("INSERT INTO settings (key, value) VALUES (?, ?)", ("prevod_test", "1")),
        ("INSERT INTO accounts (id, username, password_hash, is_admin, created_at)"
         " VALUES (7, 'spravce', 'x', 1, ?)", (T,)),
        ("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')", ()),
        ("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u1', 'Jana', 0, 0)", ()),
        ("INSERT INTO items (id, name, type, library_id, tech_source, is_missing)"
         " VALUES ('i1', 'Duna', 'Movie', 'lib1', 'jellyfin', 0)", ()),
        ("INSERT INTO item_versions (item_id, source_id, poradi) VALUES ('i1', 's1', 0)", ()),
        ("INSERT INTO item_streams (item_id, stream_index, type) VALUES ('i1', 0, 'Video')", ()),
        ("INSERT INTO playback (id, session_key, user_id, item_id, started_at, last_seen_at)"
         " VALUES (40, 's1', 'u1', 'i1', ?, ?)", (T, T)),
        ("INSERT INTO scan_log (id, kind, started_at) VALUES (9, 'library', ?)", (T,)),
        ("INSERT INTO dashboard_layout (account_id, sekce, poradi) VALUES (7, 'mapa', 0)", ()),
        ("INSERT INTO library_snapshot (den, polozek, filmu, epizod, velikost, uhd, hdr,"
         " bez_technik, zapsano_v) VALUES ('2026-10-20', 1, 1, 0, 5, 0, 0, 0, ?)", (T,)),
        ("INSERT INTO api_tokens (id, name, token_hash, ukazka, created_at, account_id)"
         " VALUES (12, 'Grafana', 'otisk', 'js_abc', ?, 7)", (T,)),
        ("INSERT INTO login_blocks (ip, level) VALUES ('203.0.113.9', 1)", ()),
        ("INSERT INTO zapomenuti (id, user_id, jmeno, relaci, zapomenuto_v)"
         " VALUES (3, 'u9', 'Petr', 1, ?)", (T,)),
        ("INSERT INTO mapa_dlazdice (podrobnost, x, y, obsah) VALUES (1, 2, 3, '{}')", ()),
    ):
        conn.execute(sql, hodnoty)
    db._zajisti_kos(conn)
    conn.execute(f"INSERT INTO {db.KOS} (id, session_key, user_id, item_id, started_at,"
                 " last_seen_at, udalost) VALUES (41, 's2', 'u9', 'i1', ?, ?, 3)", (T, T))
    conn.commit()

cil = db.database_config()
print("cíl:", cil.kind)
vysledek = dbmigrate.copy_all(zdroj, cil)
check(vysledek.get("status") == "ok", f"převod proběhne ({vysledek.get('message', '')[:120]})")


def pocet(config, tabulka: str) -> int:
    with db.connect(config) as conn:
        if not db.sloupce(conn, tabulka):
            return -1
        return int(dict(conn.execute(f"SELECT COUNT(*) AS n FROM {tabulka}").fetchone())["n"])


chybi = [t for t in list(dbmigrate.TABLES) + [db.KOS]
         if pocet(zdroj, t) != pocet(cil, t) or pocet(cil, t) < 1]
check(not chybi, f"všechny tabulky přenesené i s řádky (nesedí: {chybi})")
check(db.query_value("SELECT account_id FROM api_tokens WHERE id = 12", default=None) == 7,
      "API klíč zůstal svému účtu")

novy = api.vytvor("Po prevodu")
check(novy["id"] > 12, f"nový API klíč po převodu dostane volné id ({novy['id']})")
with db.connect() as conn:
    conn.execute("INSERT INTO zapomenuti (user_id, jmeno, relaci, zapomenuto_v)"
                 " VALUES ('u8', 'Ota', 1, ?)", (T,))
    conn.commit()
check(db.query_value("SELECT MAX(id) FROM zapomenuti") > 3, "i ostatní čítače id jsou posunuté")

print("--- převod, který se nepovede ---")
# API klic uctu, ktery uz neexistuje (stara SQLite bez kontroly cizich
# klicu to dovolila). Hlaska musi rict, u ktere tabulky to spadlo - samotne
# "FOREIGN KEY constraint failed" nerika, kde hledat.
with db.connect(zdroj) as conn:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO api_tokens (id, name, token_hash, ukazka, created_at, account_id)"
                 " VALUES (13, 'Sirotek', 'otisk2', 'js_def', ?, 999)", (T,))
    conn.commit()
vysledek = dbmigrate.copy_all(zdroj, cil)
check(vysledek.get("status") == "error" and vysledek.get("tabulka") == "api_tokens"
      and "api_tokens" in vysledek.get("message", ""),
      f"chyba jmenuje tabulku ({vysledek.get('tabulka')}: {vysledek.get('message', '')[:120]})")
check(db.query_value("SELECT COUNT(*) FROM api_tokens WHERE id = 12") == 1,
      "nepovedený převod v cíli nic nesmaže")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
