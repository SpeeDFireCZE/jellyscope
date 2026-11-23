"""Moving data between SQLite and PostgreSQL.

When you switch the database in Settings, the new one is empty. This module
can copy the data from the old one into it, so you do not lose the history.

## How it works

Both databases have **the same names of tables and columns** (see
`schema.sql` and `schema_postgres.sql`). So copying is straightforward:
read the rows from here, write them there. No conversion of types is
needed, because times are stored as text in both as well.

## What to watch out for

* It copies **in batches**, not everything at once. With hundreds of
  thousands of rows the memory would run out otherwise.
* The tables are filled **in the order of their dependencies** - `items`
  has to come before `item_streams`, otherwise a foreign key would refuse
  the write.
* The target is **emptied** first. Were rows only added, a second run
  would end on a uniqueness violation.
* On PostgreSQL the counters of the `BIGSERIAL` columns have to be
  **moved on** after copying. Without it the first new record would get
  id 1 - which is already there - and the write would fail.
"""

from __future__ import annotations

import logging
from typing import Any

from . import db, dialect, i18n

log = logging.getLogger("jellyscope.dbmigrate")

# Every table of the schema, in the order of the foreign keys - the same list
# as the backup's (db.TABLES). There used to be an own, hand-written list
# here, and the conversion silently left out API keys, dashboards, file
# versions...
TABLES: list[str] = list(db.TABLES)

BATCH_SIZE = 500


def _columns(conn: db.Connection, table: str) -> list[str]:
    return sorted(conn.table_columns(table))


def copy_all(source: dialect.DatabaseConfig,
             target: dialect.DatabaseConfig) -> dict[str, Any]:
    """Copies all the data from the source database to the target one."""
    if source.kind == target.kind and source.to_dict() == target.to_dict():
        return {"status": "error", "message": "The source and the target are the same database."}

    copied: dict[str, int] = {}
    # The table being worked on - so the message says where to look.
    # A bare "FOREIGN KEY constraint failed" does not say it.
    current_table: str | None = None

    # The target has to have the schema, otherwise there is nowhere to write.
    #
    # This used to be **outside** the try, and it was a nasty bug: when
    # creating the tables failed, the exception went all the way to the web
    # and the user got a bare "Internal Server Error" without a single clue.
    # The most common cause is easy to fix, though - see the message below.
    try:
        db.init_db(target)
    except Exception as exc:  # noqa: BLE001 - the message is shown to the user
        log.exception("creating the schema in the target database failed")
        template = ("The schema could not be created in the target database. The account may not create "
                    "tables. On PostgreSQL 15+ it also needs:  GRANT CREATE ON SCHEMA public TO {user}; ({kind}: {error})"
                    if "permission denied" in str(exc).lower() else
                    "The schema could not be created in the target database. ({kind}: {error})")
        return {"status": "error",
                "message": i18n.translate_with(template, None, user=target.user, kind=type(exc).__name__,
                                               error=i18n.error_text(exc))}

    try:
        with db.connect(source) as src, db.connect(target) as dst:
            # The bin of forgotten viewers (db.PLAYBACK_BIN) is not in the schema - it
            # is created when it is needed. When the source has it, it is
            # moved too: otherwise a forgotten viewer could not be brought
            # back after the conversion.
            tables = list(TABLES)
            if db.columns_of(src, db.PLAYBACK_BIN):
                db._ensure_playback_bin(dst)
                tables.append(db.PLAYBACK_BIN)

            # We delete in the opposite order to filling - for the same reason
            # we fill from the top: the foreign keys.
            for table in reversed(tables):
                current_table = table
                if db.columns_of(dst, table):
                    dst.execute(f"DELETE FROM {table}")

            for table in tables:
                current_table = table
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

            current_table = None
            if dst.is_postgres:
                _fix_sequences(dst, tables)

    except Exception as exc:  # noqa: BLE001 - the message is shown to the user
        log.exception("the data transfer failed (table %s)", current_table)
        error = f"{type(exc).__name__}: {exc}"
        if current_table is None:
            return {"status": "error", "message": error}
        return {"status": "error", "message": f"{current_table}: {error}",
                "table": current_table, "error": error}

    return {"status": "ok", "copied": copied, "total": sum(copied.values())}


def _fix_sequences(conn: db.Connection, tables: list[str]) -> None:
    """Moves the id counters past the highest copied value.

    PostgreSQL hands out ids from a sequence that knows nothing about the
    inserted values. Without this step the first newly inserted row would
    get id 1 and run into a record that already exists.

    Which tables have a counter is found out from the database
    (`pg_get_serial_sequence`), not from a hand-written list - that one
    would forget a new table again.
    """
    for table in tables:
        if "id" not in db.columns_of(conn, table):
            continue
        sequence = dict(conn.execute(
            "SELECT pg_get_serial_sequence(?, 'id') AS s", (table,)).fetchone())["s"]
        if sequence:
            conn.execute(
                f"SELECT setval('{sequence}', COALESCE((SELECT MAX(id) FROM {table}), 1))")


def summarise(config: dialect.DatabaseConfig) -> dict[str, int]:
    """How many rows each table has - for a check before and after the move."""
    counts: dict[str, int] = {}
    try:
        with db.connect(config) as conn:
            for table in TABLES:
                if not conn.table_columns(table):
                    continue
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                counts[table] = int(dict(row)["n"]) if row else 0
    except Exception:  # noqa: BLE001 - an unreachable database is not a bug of the program
        return {}
    return counts
