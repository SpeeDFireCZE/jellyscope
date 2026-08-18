"""Přenos dat mezi SQLite a PostgreSQL.

Když v Nastavení přepneš databázi, nová je prázdná. Tenhle modul umí data
z té staré do ní překopírovat, aby ses o historii nepřipravil.

## Jak to funguje

Obě databáze mají **stejné názvy tabulek i sloupců** (viz `schema.sql`
a `schema_postgres.sql`). Kopírování je proto přímočaré: přečti řádky
odsud, zapiš je tam. Žádný převod typů není potřeba, protože i časy
ukládáme v obou jako text.

## Na co si dát pozor

* Kopíruje se **po dávkách**, ne všechno najednou. U statisíců řádků
  by jinak došla paměť.
* Tabulky se plní **v pořadí podle závislostí** – `items` musí být dřív
  než `item_streams`, jinak by cizí klíč zápis odmítl.
* Cíl se nejdřív **vyprázdní**. Kdyby se jen přidávalo, druhý běh by
  skončil na porušení jedinečnosti.
* U PostgreSQL se po kopírování musí **posunout čítače** sloupců
  `BIGSERIAL`. Bez toho by první nový záznam dostal id 1 – které už tam
  je – a zápis by spadl.
"""

from __future__ import annotations

import logging
from typing import Any

from . import db, dialect

log = logging.getLogger("jellyscope.dbmigrate")

# Pořadí je závazné: tabulky s cizími klíči až po těch, na které odkazují.
TABLES: list[str] = [
    "settings",
    "accounts",
    "libraries",
    "users",
    "items",
    "item_streams",
    "playback",
    "scan_log",
]

# Tabulky s automaticky přidělovaným id - u nich se musí posunout čítač.
SEQUENCE_TABLES = ["accounts", "playback", "scan_log"]

BATCH_SIZE = 500


def _columns(conn: db.Connection, table: str) -> list[str]:
    return sorted(conn.table_columns(table))


def copy_all(source: dialect.DatabaseConfig,
             target: dialect.DatabaseConfig) -> dict[str, Any]:
    """Překopíruje všechna data ze zdrojové databáze do cílové."""
    if source.kind == target.kind and source.to_dict() == target.to_dict():
        return {"status": "error", "message": "Zdroj a cíl jsou tatáž databáze."}

    copied: dict[str, int] = {}

    # Cíl musí mít schéma, jinak není kam zapisovat.
    #
    # Tohle bylo dřív **mimo** try a byla to nepříjemná chyba: když
    # zakládání tabulek selhalo, výjimka prošla až do webu a uživatel
    # dostal holé "Internal Server Error" bez jediného vodítka.
    # Nejčastější příčina je přitom snadno opravitelná - viz hláška níže.
    try:
        db.init_db(target)
    except Exception as exc:  # noqa: BLE001 - hlášku ukazujeme uživateli
        log.exception("zalozeni schematu v cilove databazi selhalo")
        rada = ""
        if "permission denied" in str(exc).lower():
            rada = (" Účet nemá právo zakládat tabulky. Na PostgreSQL 15+"
                    " je potřeba i:  GRANT CREATE ON SCHEMA public TO"
                    f" {target.user};")
        return {
            "status": "error",
            "message": f"Schéma v cílové databázi se nepodařilo založit."
                       f"{rada} ({type(exc).__name__}: {exc})",
        }

    try:
        with db.connect(source) as src, db.connect(target) as dst:
            # Mažeme v opačném pořadí než plníme - ze stejného důvodu,
            # z jakého se plní odshora: cizí klíče.
            for table in reversed(TABLES):
                dst.execute(f"DELETE FROM {table}")

            for table in TABLES:
                shared = [c for c in _columns(src, table) if c in dst.table_columns(table)]
                if not shared:
                    continue

                column_list = ", ".join(shared)
                placeholders = ", ".join("?" for _ in shared)
                insert = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"

                cursor = src.execute(f"SELECT {column_list} FROM {table}")
                total = 0
                while True:
                    rows = cursor.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    dst.executemany(
                        insert, [[dict(row)[c] for c in shared] for row in rows]
                    )
                    total += len(rows)

                copied[table] = total

            if dst.is_postgres:
                _fix_sequences(dst)

    except Exception as exc:  # noqa: BLE001 - hlášku ukazujeme uživateli
        log.exception("prenos dat selhal")
        return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

    return {"status": "ok", "copied": copied, "total": sum(copied.values())}


def _fix_sequences(conn: db.Connection) -> None:
    """Posune čítače id za nejvyšší zkopírovanou hodnotu.

    PostgreSQL přiděluje id z posloupnosti, která o vložených hodnotách
    neví. Bez tohohle kroku by první nově vložený řádek dostal id 1
    a narazil na už existující záznam.
    """
    for table in SEQUENCE_TABLES:
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
            f" COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )


def summarise(config: dialect.DatabaseConfig) -> dict[str, int]:
    """Kolik řádků je v které tabulce - pro kontrolu před i po přenosu."""
    counts: dict[str, int] = {}
    try:
        with db.connect(config) as conn:
            for table in TABLES:
                if not conn.table_columns(table):
                    continue
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                counts[table] = int(dict(row)["n"]) if row else 0
    except Exception:  # noqa: BLE001 - nedostupná databáze není chyba programu
        return {}
    return counts
