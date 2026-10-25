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

# Všechny tabulky ze schématu, v pořadí podle cizích klíčů - tentýž seznam
# jako u zálohy (db.TABULKY). Dřív tu byl vlastní, ručně psaný seznam a
# převod s ním tiše vynechával API klíče, přehledy, verze souborů...
TABLES: list[str] = list(db.TABULKY)

BATCH_SIZE = 500


def _columns(conn: db.Connection, table: str) -> list[str]:
    return sorted(conn.table_columns(table))


def copy_all(source: dialect.DatabaseConfig,
             target: dialect.DatabaseConfig) -> dict[str, Any]:
    """Překopíruje všechna data ze zdrojové databáze do cílové."""
    if source.kind == target.kind and source.to_dict() == target.to_dict():
        return {"status": "error", "message": "Zdroj a cíl jsou tatáž databáze."}

    copied: dict[str, int] = {}
    # U ktere tabulky se zrovna pracuje - at hlaska rekne, kde hledat.
    # Samotne "FOREIGN KEY constraint failed" to nerekne.
    tabulka: str | None = None

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
            # Koš zapomenutých diváků (db.KOS) není ve schématu - vzniká,
            # až je ho potřeba. Když ve zdroji je, přenese se taky: jinak
            # by po převodu nešlo zapomenutého diváka vrátit.
            tabulky = list(TABLES)
            if db.sloupce(src, db.KOS):
                db._zajisti_kos(dst)
                tabulky.append(db.KOS)

            # Mažeme v opačném pořadí než plníme - ze stejného důvodu,
            # z jakého se plní odshora: cizí klíče.
            for table in reversed(tabulky):
                tabulka = table
                if db.sloupce(dst, table):
                    dst.execute(f"DELETE FROM {table}")

            for table in tabulky:
                tabulka = table
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

            tabulka = None
            if dst.is_postgres:
                _fix_sequences(dst, tabulky)

    except Exception as exc:  # noqa: BLE001 - hlášku ukazujeme uživateli
        log.exception("prenos dat selhal (tabulka %s)", tabulka)
        chyba = f"{type(exc).__name__}: {exc}"
        if tabulka is None:
            return {"status": "error", "message": chyba}
        return {"status": "error", "message": f"{tabulka}: {chyba}",
                "tabulka": tabulka, "chyba": chyba}

    return {"status": "ok", "copied": copied, "total": sum(copied.values())}


def _fix_sequences(conn: db.Connection, tabulky: list[str]) -> None:
    """Posune čítače id za nejvyšší zkopírovanou hodnotu.

    PostgreSQL přiděluje id z posloupnosti, která o vložených hodnotách
    neví. Bez tohohle kroku by první nově vložený řádek dostal id 1
    a narazil na už existující záznam.

    Které tabulky čítač mají, se zjistí z databáze (`pg_get_serial_sequence`),
    ne z ručního seznamu - ten by se s novou tabulkou zase zapomněl.
    """
    for table in tabulky:
        if "id" not in db.sloupce(conn, table):
            continue
        posloupnost = dict(conn.execute(
            "SELECT pg_get_serial_sequence(?, 'id') AS s", (table,)).fetchone())["s"]
        if posloupnost:
            conn.execute(
                f"SELECT setval('{posloupnost}', COALESCE((SELECT MAX(id) FROM {table}), 1))")


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
