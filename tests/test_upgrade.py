# -*- coding: utf-8 -*-
"""Upgrading an old database to the new schema.

Whoever ran Jellyscope before has tables in the database without the columns
added later (`items.tmdb_id`, `playback.is_paused`). The start of the
application has to be able to add them - and above all must not fail doing it.

The particular trap this test guards: the index `idx_items_tmdb` refers to a
column the old table does not have yet. When the schema runs all at once, the
index is created before the migration manages to add the column, and the
application ends on "no such column: tmdb_id". So init_db() creates the tables
first, then adds the columns and only at the end the indexes.

The old database is not simulated here with hand-written SQL but with **the
real schema with the newer lines struck out** - thanks to that the test does
not grow old along with it.
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
# An own home: without it DATABASE_PATH would be overridden by the stored
# choice of database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(_tmp / "old.db")
os.environ["SECRET_KEY"] = "test-signing-key"

from jellyscope import db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# ---------------------------------------------------------------------------
# Both schemas have to describe the same tables
# ---------------------------------------------------------------------------
print("--- the SQLite and PostgreSQL schemas side by side ---")
#
# The schemas are two files, and a column is easily added to one only.
# Nothing would happen on SQLite, on PostgreSQL it would be missing - and since
# the migration adds it anyway, it would show only as an inconsistency between
# what is in the file and what is in the database.


def _table_columns(text: str) -> dict[str, set[str]]:
    """Takes the names of the columns out of CREATE TABLE. Not keywords and constraints."""
    KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "REFERENCES"}
    tables: dict[str, set[str]] = {}
    name = None
    for line in text.splitlines():
        bare = line.strip()
        if not bare or bare.startswith("--"):
            continue
        upper = bare.upper()
        if upper.startswith("CREATE TABLE"):
            # "CREATE TABLE IF NOT EXISTS name (" -> name
            words = bare.replace("(", " ").split()
            name = words[-1].lower() if words else None
            if name:
                tables[name] = set()
            continue
        if name is None:
            continue
        if bare.startswith(")"):
            name = None
            continue
        first = bare.split()[0].strip('",')
        if first.upper() not in KEYWORDS:
            tables[name].add(first.lower())
    return tables


sqlite_tables = _table_columns(
    (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8"))
pg_tables = _table_columns(
    (PROJECT / "jellyscope" / "schema_postgres.sql").read_text(encoding="utf-8"))

check(sqlite_tables and pg_tables, "tables can be read from both schemas")
check(set(sqlite_tables) == set(pg_tables),
      "both schemas have the same tables "
      f"({set(sqlite_tables) ^ set(pg_tables) or 'the same'})")
for name in sorted(set(sqlite_tables) & set(pg_tables)):
    difference = sqlite_tables[name] ^ pg_tables[name]
    check(not difference, f"the table {name} has the same columns in both"
                          + (f" (they differ: {sorted(difference)})" if difference else ""))

print()
# ---------------------------------------------------------------------------
# Splitting the schema into tables and indexes
# ---------------------------------------------------------------------------
print("--- splitting the schema ---")

for name in ("schema.sql", "schema_postgres.sql"):
    text = (PROJECT / "jellyscope" / name).read_text(encoding="utf-8")
    tables, indexes = db._split_indexes(text)

    # Nothing may get lost or made up - only rearranged.
    check(sorted((tables + indexes).split()) == sorted(text.split()),
          f"{name}: the split lost nothing")

    total = len(re.findall(r"(?mi)^CREATE (?:UNIQUE )?INDEX", text))
    check(len(re.findall(r"(?mi)^CREATE (?:UNIQUE )?INDEX", indexes)) == total,
          f"{name}: all {total} indexes are in the second part")
    check("CREATE INDEX" not in tables.upper(),
          f"{name}: no index stayed in the part with the tables")
    check("CREATE TABLE" in tables.upper() and "CREATE TABLE" not in indexes.upper(),
          f"{name}: the tables stayed in the first part")


# ---------------------------------------------------------------------------
# A real old database
# ---------------------------------------------------------------------------
print()
print("--- the start over an old database ---")

# seerr_request.jellyfin_id (seerr_zadost before 1.9.0): the table came into being on 13. 11. 2026 without it and the column was added the same day.
NEW_COLUMNS = {"items": "tmdb_id", "playback": "is_paused", "seerr_request": "jellyfin_id"}
# More columns of the same table, added later (posters of requests, 13. 11. 2026).
# Separately, because NEW_COLUMNS holds one column per table.
MORE_COLUMNS = (("seerr_request", "tvdb_id"), ("seerr_request", "poster"))

schema = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")
old_schema = schema

def table_block(text: str, table: str) -> str:
    return re.search(rf"(?is)CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);", text).group(0)


for table, column in NEW_COLUMNS.items():
    # We throw out the column's definition and every index referring to it -
    # exactly what the database looked like before the column was added. In
    # that one table only: newer tables have a column of the same name too
    # (seerr_request.tmdb_id), and those are not our concern.
    block = table_block(old_schema, table)
    old_schema = old_schema.replace(block, re.sub(rf"(?mi)^\s*{column}\s+[A-Z].*\n", "", block))
    old_schema = re.sub(rf"(?mis)^CREATE (?:UNIQUE )?INDEX[^;]*ON {table}\s*\([^)]*\b{column}\b[^)]*\);\n", "", old_schema)

for table, column in MORE_COLUMNS:
    block = table_block(old_schema, table)
    without = re.sub(rf"(?mi)^\s*{column}\s+[A-Z].*\n", "", block)
    # The last column of a table has no comma after it; once it is removed,
    # the one that stayed last must not have one either.
    without = re.sub(r",(\s*(?:--[^\n]*)?\n\);)$", r"\1", without)
    old_schema = old_schema.replace(block, without)

for table, column in [*NEW_COLUMNS.items(), *MORE_COLUMNS]:
    check(f" {column} " not in table_block(old_schema, table)
          and not re.search(rf"ON {table}\s*\([^)]*\b{column}\b", old_schema),
          f"the trial old schema really does not know {table}.{column}")

path = _tmp / "old.db"
raw = sqlite3.connect(path)
raw.executescript(old_schema)
raw.commit()

for table, column in [*NEW_COLUMNS.items(), *MORE_COLUMNS]:
    columns = [r[1] for r in raw.execute(f"PRAGMA table_info({table})")]
    check(column not in columns, f"the old database does not have {table}.{column}")

# A request stored before the posters: after the upgrade it has to stay, with the new columns empty.
raw.execute("INSERT INTO seerr_request (id, media_type, tmdb_id, title, status, requester_name, created_at)"
            " VALUES (7, 'movie', '603', 'The Matrix', 2, 'jana', '2026-11-13 06:28:00')")

# So there is something to lose: a row that has to survive the upgrade.
raw.execute("INSERT INTO libraries (id, name) VALUES ('lib', 'Filmy')")
raw.execute(
    """INSERT INTO items (id, name, type, library_id, date_created,
                          is_missing, synced_at)
       VALUES ('film-1', 'Starý film', 'Movie', 'lib', ?, 0, ?)""",
    ("2020-01-01T00:00:00", "2020-01-01T00:00:00"),
)
raw.commit()
raw.close()

try:
    added = db.init_db()
    check(True, "init_db() over an old database passes")
except Exception as error:                      # noqa: BLE001
    check(False, f"init_db() failed: {error}")
    added = []

for table, column in [*NEW_COLUMNS.items(), *MORE_COLUMNS]:
    check(f"{table}.{column}" in added, f"init_db() reports adding {table}.{column}")

with db.connect() as conn:
    for table, column in [*NEW_COLUMNS.items(), *MORE_COLUMNS]:
        check(column in conn.table_columns(table), f"{table}.{column} is in the database")
    request = conn.execute("SELECT title, tvdb_id, poster, jellyfin_id FROM seerr_request WHERE id = 7").fetchone()
    check(request is not None and (request["title"], request["tvdb_id"], request["poster"], request["jellyfin_id"]) == ("The Matrix", "", "", ""),
          "the request stored before the posters stayed; its new columns are empty (the sync fills the poster in)")

    indexes = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    check("idx_items_tmdb" in indexes, "the index idx_items_tmdb was created only after the migration")

    row = conn.execute("SELECT name, tmdb_id FROM items WHERE id = 'film-1'").fetchone()
    check(row is not None and row["name"] == "Starý film", "the original data stayed")
    check(row is not None and row["tmdb_id"] is None, "the new column is empty so far")

# A second start must do nothing extra - the migration must not repeat.
check(db.init_db() == [], "a second start adds nothing any more")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
