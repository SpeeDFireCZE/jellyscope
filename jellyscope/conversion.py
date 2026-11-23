"""Converting an old database to the English names, at start (spec 2026-11-21-database-english-design).

`db.init_db` calls `convert()` before it creates the tables: the schema's `CREATE TABLE IF NOT
EXISTS` would otherwise make the new names empty next to the old ones. The old shape is told from
the catalog, not from a version number, so a restored old backup is converted too, and a second
start does nothing.

Everything happens in one transaction (on SQLite an explicit one - the `sqlite3` module would
commit DDL at once): tables, columns, indexes (PostgreSQL: their primary keys and sequences too),
setting keys and stored values by `schema_names`. A name in both shapes is never guessed at: an
empty new table is dropped and the old one takes its place; data under both names stops the
conversion before anything changes (`db.ConversionFailed` - the outage page says what to do).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import db, dialect, schema_names

log = logging.getLogger("jellyscope.conversion")

FAILED = "The database could not be converted to English names. Nothing was changed."
BOTH_TABLES = "Both {old} and {new} hold data."
BOTH_COLUMNS = "Both columns {table}.{old} and {table}.{new} exist."
BOTH_SETTINGS = "The setting {old} and its new name {new} hold different values."
ADVICE = "Keep the one you want - rename or delete the other - and start the application again."
MISMATCH = "The table {table} does not match after the conversion ({before} rows before, {after} after)."
MISMATCH_SETTINGS = "The settings do not match after the conversion."
UNEXPECTED = "The conversion stopped on an error: {error}"
NO_BACKUP = "The backup before the conversion could not be made in {folder}: {error}"
NO_BACKUP_ADVICE = ("Make room on the disk, or let the user Jellyscope runs as write into that folder, then start the "
                    "application again.")
# The setting that tells the administrator about the backup (the stripe at the top, web.base).
BACKUP_SETTING = "conversion_backup"
NOTICE_CLOSED_SETTING = "conversion_notice_closed"
# The header of an own export that 1.8.x knows (tasks.OWN_BACKUP_MARKS) - this backup is for going back there.
OLD_EXPORT_MARK = "-- Záloha Jellyscope (vlastní export)"
# The key of PostgreSQL's advisory lock that lets one conversion run at a time ("JSCO").
CONVERSION_LOCK = 0x4A53434F


@dataclass
class Plan:
    """What a database needs to reach the new names. Empty = nothing (a converted or a new one)."""

    drop_tables: list[str] = field(default_factory=list)            # empty tables in the way
    tables: list[tuple[str, str]] = field(default_factory=list)       # (old, new)
    columns: list[tuple[str, str, str]] = field(default_factory=list)  # (table after the renames, old, new)
    # Old columns beside their new ones holding only NULL or their default: what a start of 1.8.x leaves
    # (its migrations add the old columns before the lock view stops it). (table after the renames, old)
    drop_columns: list[tuple[str, str]] = field(default_factory=list)
    indexes: list[tuple[str, str]] = field(default_factory=list)      # (old, new)
    constraints: list[tuple[str, str, str]] = field(default_factory=list)  # PostgreSQL: (table, old, new)
    sequences: list[tuple[str, str]] = field(default_factory=list)    # PostgreSQL: (old, new)
    settings: list[tuple[str, str, str]] = field(default_factory=list)  # (old key, new key, action)
    values: list[tuple[str, str, str, str]] = field(default_factory=list)  # (table, column, old, new)
    # JSON to rewrite: (table, column, {old text: new text}) by their names after the renames; a setting is
    # ("settings", its new key, ...).
    json_cells: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    drops_data: bool = False        # a dropped table holds rows (an orphaned one): the backup comes first
    # Cells of a JSON target that are no JSON: (table, column, how many), kept as they are and logged. No work
    # by itself (they stay no JSON - every start would convert again).
    unreadable_json: list[tuple[str, str, int]] = field(default_factory=list)
    conflicts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def only_leftovers(self) -> bool:
        """Nothing to rename, only what a start of 1.8.x left behind (empty old tables, old columns with NULL
        or their default): no data to save first, and the backup made before the real conversion stays the
        way back the stripe offers."""
        return not (self.tables or self.columns or self.indexes or self.constraints or self.sequences
                    or self.settings or self.values or self.json_cells or self.drops_data or self.conflicts)

    def __bool__(self) -> bool:
        return any((self.drop_tables, self.tables, self.columns, self.drop_columns, self.indexes, self.constraints,
                    self.sequences, self.settings, self.values, self.json_cells, self.conflicts))


def _tables(conn: db.Connection) -> set[str]:
    if conn.is_postgres:
        rows = conn.execute("SELECT table_name AS name FROM information_schema.tables"
                            " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'").fetchall()
    else:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(dict(row)["name"]) for row in rows}


def _indexes(conn: db.Connection) -> set[str]:
    if conn.is_postgres:
        rows = conn.execute("SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()").fetchall()
    else:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL").fetchall()
    return {str(dict(row)["name"]) for row in rows}


def _has_rows(conn: db.Connection, table: str) -> bool:
    return conn.execute(f"SELECT 1 AS x FROM {table} LIMIT 1").fetchone() is not None


def _primary_key(conn: db.Connection, table: str) -> str | None:
    """PostgreSQL: the name of the table's primary key constraint."""
    row = conn.execute("SELECT c.conname AS name FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid"
                       " JOIN pg_namespace n ON n.oid = t.relnamespace"
                       " WHERE c.contype = 'p' AND t.relname = ? AND n.nspname = current_schema()", (table,)).fetchone()
    return str(dict(row)["name"]) if row else None


def _sequences(conn: db.Connection, table: str) -> list[tuple[str, str]]:
    """PostgreSQL: (sequence, column) of the sequences the table's columns own (SERIAL ids)."""
    rows = conn.execute(
        "SELECT s.relname AS sequence, a.attname AS col FROM pg_class s"
        " JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'"
        " JOIN pg_class t ON t.oid = d.refobjid JOIN pg_namespace n ON n.oid = t.relnamespace"
        " JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid"
        " WHERE s.relkind = 'S' AND t.relname = ? AND n.nspname = current_schema()", (table,)).fetchall()
    return [(str(dict(r)["sequence"]), str(dict(r)["col"])) for r in rows]


def _holds_only_default(conn: db.Connection, table: str, column: str) -> bool:
    """The column holds nothing but NULL or its declared default - what ALTER TABLE ADD COLUMN leaves.

    The default is read from the database itself (SQLite's table_info, PostgreSQL's catalog), so it is the
    one the column was added with.
    """
    if conn.is_postgres:
        row = conn.execute("SELECT column_default AS d FROM information_schema.columns"
                           " WHERE table_schema = current_schema() AND table_name = ? AND column_name = ?",
                           (table, column)).fetchone()
        default = dict(row)["d"] if row else None
    else:
        default = next((r["dflt_value"] for r in conn._raw.execute(f"PRAGMA table_info({table})").fetchall()
                        if r["name"] == column), None)
    # SQLite writes a declared DEFAULT NULL as the text NULL: that is no default to compare with.
    if default is not None and str(default).strip().upper() == "NULL":
        default = None
    condition = f"{column} IS NOT NULL" + (f" AND {column} <> {default}" if default is not None else "")
    return conn.execute(f"SELECT 1 FROM {table} WHERE {condition} LIMIT 1").fetchone() is None


def _plan_tables(conn: db.Connection, names: Any, result: Plan) -> dict[str, str]:
    """Tables, columns, indexes, keys and sequences. Returns {table's new name: its name now}."""
    existing, indexes = _tables(conn), _indexes(conn)
    physical: dict[str, str] = {}
    for table in names.TABLES:
        if table.old != table.new and table.old in existing:
            old_rows = _has_rows(conn, table.old)
            if table.new in existing and (_has_rows(conn, table.new) or not old_rows):
                if old_rows:
                    result.conflicts.append((BOTH_TABLES, {"old": table.old, "new": table.new}))
                    continue
                # The old one is empty: the new one stays - with its data, or empty with its shape, indexes and
                # counter of ids (an empty old table beside it is what a start of 1.8.x leaves).
                result.drop_tables.append(table.old)
                physical[table.new] = table.new
                indexes -= set(table.indexes)              # they go with the old table
            else:
                if table.new in existing:
                    result.drop_tables.append(table.new)   # an empty new table in the way of the old data
                result.tables.append((table.old, table.new))
                physical[table.new] = table.old
        elif table.new in existing:
            physical[table.new] = table.new
        else:
            continue
        now = physical[table.new]
        columns = conn.table_columns(now)
        renamed = {}
        for old, new in table.columns.items():
            if old not in columns:
                continue
            if new in columns:
                if _holds_only_default(conn, now, old):
                    result.drop_columns.append((table.new, old))
                else:
                    result.conflicts.append((BOTH_COLUMNS, {"table": table.new, "old": old, "new": new}))
                continue
            result.columns.append((table.new, old, new))
            renamed[old] = new
        result.indexes += [(old, new) for old, new in table.indexes.items() if old in indexes]
        if conn.is_postgres and now != table.new:
            key = _primary_key(conn, now)
            if key == f"{table.old}_pkey":
                result.constraints.append((table.new, key, f"{table.new}_pkey"))
            for sequence, column in _sequences(conn, now):
                if sequence == f"{table.old}_{column}_seq":
                    result.sequences.append((sequence, f"{table.new}_{renamed.get(column, column)}_seq"))
    for orphan in getattr(names, "ORPHANED_TABLES", ()):
        if orphan in existing:              # read by nothing; its rows go into the backup first
            result.drop_tables.append(orphan)
            result.drops_data = result.drops_data or _has_rows(conn, orphan)
    return physical


def _plan_settings(conn: db.Connection, names: Any, result: Plan) -> None:
    if "settings" not in _tables(conn):
        return
    stored = {str(dict(r)["key"]): dict(r)["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}
    for old, new in names.SETTING_KEYS.items():
        if old not in stored:
            continue
        if new not in stored:
            result.settings.append((old, new, "move"))
            continue
        # Both keys (a manual restore into a live database): the old value is compared as the map makes it.
        old_value = _setting_in_new_words(names, new, stored[old])
        default = db.DEFAULT_SETTINGS.get(new)
        if _same_setting(names, new, stored[new], old_value):
            result.settings.append((old, new, "drop_old"))
        elif stored[new] == default:
            result.settings.append((old, new, "replace"))      # the new key only holds its default
        elif _same_setting(names, new, old_value, default):
            result.settings.append((old, new, "keep_new"))     # the old key only holds the default
        else:
            result.conflicts.append((BOTH_SETTINGS, {"old": old, "new": new}))


def _setting_in_new_words(names: Any, key: str, value: Any) -> Any:
    """A setting's stored value as the conversion makes it under its new key: its plain value by SETTING_VALUES,
    its JSON by JSON_KEYS / JSON_VALUES."""
    if not isinstance(value, str):
        return value
    value = getattr(names, "SETTING_VALUES", {}).get(key, {}).get(value, value)
    target = ("settings", key)
    keys = getattr(names, "JSON_KEYS", {}).get(target, {})
    values = {k: m for (t, k), m in getattr(names, "JSON_VALUES", {}).items() if t == target}
    return (json_converted(value, keys, values) or value) if keys or values else value


def _same_setting(names: Any, key: str, first: Any, second: Any) -> bool:
    """The same value - a JSON setting by its content, not by how the text is formatted."""
    if first == second:
        return True
    target = ("settings", key)
    if not (isinstance(first, str) and isinstance(second, str) and (
            target in getattr(names, "JSON_KEYS", {}) or any(t == target for t, _ in getattr(names, "JSON_VALUES", {})))):
        return False
    try:
        return json.loads(first) == json.loads(second)
    except ValueError:
        return False


def _plan_values(conn: db.Connection, names: Any, result: Plan, physical: dict[str, str]) -> None:
    existing = _tables(conn)
    for (table, column), mapping in names.VALUES.items():
        now = physical.get(table, table if table in existing else None)
        if now is None:
            continue
        columns = conn.table_columns(now)
        renames = next((t.columns for t in names.TABLES if t.new == table), {})
        column_now = column if column in columns else next((o for o, n in renames.items() if n == column and o in columns), None)
        if column_now is None:
            continue
        olds = list(mapping)
        marks = ", ".join("?" for _ in olds)
        present = {str(dict(r)["v"]) for r in conn.execute(
            f"SELECT DISTINCT {column_now} AS v FROM {now} WHERE {column_now} IN ({marks})", tuple(olds)).fetchall()}
        result.values.extend((table, column, old, new) for old, new in mapping.items() if old in present)


def json_converted(text: str, keys: dict[str, str], values: dict[str, dict[str, str]]) -> str | None:
    """`text` with the keys of its objects renamed by `keys` and the values under a key mapped by
    `values[key]` (a string, or the strings of a list), at any depth; None when nothing changes or it is no
    JSON. An object having the old and the new key already keeps the old one (no value is lost). The text
    keeps its shape: compact stays compact, letters stay letters (or escapes, if it had them)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    changed = False

    def walk(node: Any, under: str | None) -> Any:
        nonlocal changed
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                new_key = keys.get(key, key)
                if new_key != key and new_key in node:
                    new_key = key
                changed = changed or new_key != key
                out[new_key] = walk(value, new_key)
            return out
        if isinstance(node, list):
            return [walk(value, under) for value in node]
        if isinstance(node, str) and under is not None and node in values.get(under, {}):
            changed = True
            return values[under][node]
        return node

    converted = walk(data, None)
    if not changed:
        return None
    compact = ", " not in text and ": " not in text
    return json.dumps(converted, ensure_ascii="\\u" in text, separators=(",", ":") if compact else None)


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _plan_json(conn: db.Connection, names: Any, result: Plan, physical: dict[str, str]) -> None:
    keys_by_target = getattr(names, "JSON_KEYS", {})
    values_by_target = getattr(names, "JSON_VALUES", {})
    existing = _tables(conn)
    for target in sorted(set(keys_by_target) | {target for target, _ in values_by_target}):
        table, column = target
        keys = keys_by_target.get(target, {})
        values = {key: mapping for (t, key), mapping in values_by_target.items() if t == target}
        if table == "settings":
            if "settings" not in existing:
                continue
            sources = [column, *(old for old, new in names.SETTING_KEYS.items() if new == column)]
            marks = ", ".join("?" for _ in sources)
            counted = [(dict(r)["value"], 1) for r in conn.execute(
                f"SELECT value FROM settings WHERE key IN ({marks})", tuple(sources)).fetchall()]
        else:
            now = physical.get(table, table if table in existing else None)
            if now is None:
                continue
            columns = conn.table_columns(now)
            renames = next((t.columns for t in names.TABLES if t.new == table), {})
            column_now = column if column in columns else next((o for o, n in renames.items() if n == column and o in columns), None)
            if column_now is None:
                continue
            counted = [(dict(r)["v"], int(dict(r)["n"])) for r in conn.execute(
                f"SELECT {column_now} AS v, COUNT(*) AS n FROM {now} WHERE {column_now} IS NOT NULL"
                f" GROUP BY {column_now}").fetchall()]
        changes = {str(text): new for text, _ in counted
                   if text is not None and (new := json_converted(str(text), keys, values)) is not None}
        if changes:
            result.json_cells.append((table, column, changes))
        unreadable = sum(n for text, n in counted if text is not None and str(text).strip() and not _is_json(str(text)))
        if unreadable:
            result.unreadable_json.append((table, column, unreadable))
    # A plain value of one setting: the same rewrite of the cell's text, by the setting's own map.
    if "settings" in existing:
        for key, mapping in getattr(names, "SETTING_VALUES", {}).items():
            sources = [key, *(old for old, new in names.SETTING_KEYS.items() if new == key)]
            marks = ", ".join("?" for _ in sources)
            texts = {str(dict(r)["value"]) for r in conn.execute(
                f"SELECT value FROM settings WHERE key IN ({marks})", tuple(sources)).fetchall()}
            changes = {text: mapping[text] for text in texts if text in mapping}
            if changes:
                result.json_cells.append(("settings", key, changes))


def plan(conn: db.Connection, names: Any = schema_names) -> Plan:
    """What the database needs to reach the names of `names` (the map). Reads only."""
    result = Plan()
    physical = _plan_tables(conn, names, result)
    _plan_settings(conn, names, result)
    _plan_values(conn, names, result, physical)
    _plan_json(conn, names, result, physical)
    return result


def apply(conn: db.Connection, found: Plan) -> dict[str, int]:
    """Carries the plan out inside the caller's transaction. Returns how much of each kind it changed."""
    for table in found.drop_tables:
        conn.execute(f"DROP TABLE {table}")
    for old, new in found.tables:
        conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
    for table, column in found.drop_columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    for table, old, new in found.columns:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    for old, new in found.indexes:
        # SQLite cannot rename an index: the old one goes, the schema makes the new one right after.
        conn.execute(f"ALTER INDEX {old} RENAME TO {new}" if conn.is_postgres else f"DROP INDEX IF EXISTS {old}")
    for table, old, new in found.constraints:
        conn.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")
    for old, new in found.sequences:
        conn.execute(f"ALTER SEQUENCE {old} RENAME TO {new}")
    for old, new, action in found.settings:
        if action == "move":
            conn.execute("UPDATE settings SET key = ? WHERE key = ?", (new, old))
        elif action == "replace":
            conn.execute("UPDATE settings SET value = (SELECT value FROM settings WHERE key = ?) WHERE key = ?", (old, new))
            conn.execute("DELETE FROM settings WHERE key = ?", (old,))
        else:
            conn.execute("DELETE FROM settings WHERE key = ?", (old,))
    for table, column, old, new in found.values:
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old))
    cells = 0
    for table, column, changes in found.json_cells:
        if table == "settings":
            for old_text, new_text in changes.items():
                conn.execute("UPDATE settings SET value = ? WHERE key = ? AND value = ?", (new_text, column, old_text))
                cells += 1
        else:
            cells += _rewrite_cells(conn, table, column, changes)
    return {"tables": len(found.tables), "columns": len(found.columns), "indexes": len(found.indexes),
            "settings": len(found.settings), "values": len(found.values), "json_cells": cells,
            "dropped": len(found.drop_tables)}


def _rewrite_cells(conn: db.Connection, table: str, column: str, changes: dict[str, str]) -> int:
    """Every row of `table` whose `column` holds a text of `changes` gets its new text, by the row's key (SQLite: rowid,
    PostgreSQL: the primary key) - one pass over the table. A statement per text over the whole table read every
    cell for every text: the detailed map's thousands of tiles took minutes."""
    keys = _order(conn, table, []) if conn.is_postgres else ["rowid"]
    if not keys:                                   # PostgreSQL without a primary key: text by text
        for old_text, new_text in changes.items():
            conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new_text, old_text))
        return sum(int(dict(r)["n"]) for r in conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {column} IN ({', '.join('?' for _ in changes)})",
            tuple(changes.values())).fetchall())
    named = ", ".join(f"{key} AS k{number}" for number, key in enumerate(keys))
    where = " AND ".join(f"{key} = ?" for key in keys)
    rewritten = 0
    for row in conn.execute(f"SELECT {named}, {column} AS v FROM {table} WHERE {column} IS NOT NULL").fetchall():
        data = dict(row)
        new_text = changes.get(str(data["v"]))
        if new_text is not None:
            conn.execute(f"UPDATE {table} SET {column} = ? WHERE {where}",
                         (new_text, *(data[f"k{number}"] for number in range(len(keys)))))
            rewritten += 1
    return rewritten


def _order(conn: db.Connection, table: str, columns: list[str]) -> list[str]:
    """The primary key's columns (rows in a stable order without sorting by everything), else all."""
    if conn.is_postgres:
        rows = conn.execute("SELECT a.attname AS name FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid"
                            " JOIN pg_namespace n ON n.oid = t.relnamespace"
                            " JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey)"
                            " WHERE i.indisprimary AND t.relname = ? AND n.nspname = current_schema()"
                            " ORDER BY array_position(i.indkey::int2[], a.attnum)", (table,)).fetchall()
        keys = [str(dict(r)["name"]) for r in rows]
    else:
        info = conn._raw.execute(f"PRAGMA table_info({table})").fetchall()
        keys = [str(r["name"]) for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
    return keys or columns


def fingerprint(conn: db.Connection, table: str, columns: list[str],
                values: dict[str, dict[str, str]] | None = None) -> tuple[int, str]:
    """(rows, sha256 of the rows) of `table` with `columns` in this order; `values` maps a column's
    old values to the new ones first, so the old shape and the converted one give the same print.

    The print does not depend on the order of the rows: a mapped value in the primary key orders the rows
    differently after the conversion (akce < dotaz, but zebra > query). Each row is hashed alone; the sorted
    hashes make the print."""
    mapped = [(values or {}).get(column, {}) for column in columns]
    hashes = []
    for row in conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall():
        data = dict(row)
        hashes.append(hashlib.sha256(repr(tuple(m.get(data[c], data[c]) if isinstance(data[c], str) else data[c]
                                                for c, m in zip(columns, mapped))).encode("utf-8")).hexdigest())
    hashes.sort()
    return len(hashes), hashlib.sha256("".join(hashes).encode("ascii")).hexdigest()


def _prints(conn: db.Connection, found: Plan, after: bool) -> dict[str, tuple[int, str]]:
    """The fingerprints of every table the plan touches, keyed by its new name. Before the conversion the
    table is read under its old names (columns in the order of their new names, values through the map),
    after it under the new ones - the same data gives the same print."""
    renamed_from = {new: old for old, new in found.tables}
    touched = (set(renamed_from) | {t for t, _, _ in found.columns} | {t for t, _, _, _ in found.values}
               | {t for t, _, _ in found.json_cells})
    json_maps = {(t, c): changes for t, c, changes in found.json_cells}
    prints = {}
    for table in sorted(touched - {"settings"}):
        now = table if after else renamed_from.get(table, table)
        to_new = {} if after else {old: new for t, old, new in found.columns if t == table}
        pairs = sorted((to_new.get(column, column), column) for column in conn.table_columns(now))
        values = {} if after else {column: {**{old: new for t, c, old, new in found.values if t == table and c == final},
                                            **json_maps.get((table, final), {})}
                                   for final, column in pairs}
        prints[table] = fingerprint(conn, now, [column for _, column in pairs], values)
    return prints


def _settings(conn: db.Connection) -> dict[str, Any]:
    if "settings" not in _tables(conn):
        return {}
    return {str(dict(r)["key"]): dict(r)["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}


def _expected_settings(before: dict[str, Any], found: Plan) -> dict[str, Any]:
    expected = dict(before)
    for old, new, action in found.settings:
        value = expected.pop(old)
        if action in ("move", "replace"):
            expected[new] = value                   # the old value; drop_old and keep_new: the new key's own
    for table, column, old, new in found.values:
        if (table, column) == ("settings", "value"):
            expected = {k: (new if v == old else v) for k, v in expected.items()}
    for table, key, changes in found.json_cells:
        if table == "settings" and key in expected and str(expected[key]) in changes:
            expected[key] = changes[str(expected[key])]
    return expected


def _counts(conn: db.Connection, skip: set[str]) -> dict[str, int]:
    """The rows of every table but `skip` - and but SQLite's own tables (sqlite_sequence, which `_autoincrement_rows`
    checks by name; sqlite_stat1/4 of ANALYZE, whose rows go with a dropped table or index)."""
    return {t: int(dict(conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone())["n"])
            for t in sorted(_tables(conn) - skip) if not t.startswith("sqlite_")}


SQLITE_SEQUENCE = "sqlite_sequence"


def _autoincrement_rows(conn: db.Connection) -> dict[str, int]:
    """SQLite: the last id of every AUTOINCREMENT table, by its name (sqlite_sequence; {} on PostgreSQL or before the
    first AUTOINCREMENT table). A renamed table keeps its row under the new name, a dropped one takes it along."""
    if conn.is_postgres or SQLITE_SEQUENCE not in _tables(conn):
        return {}
    return {str(dict(r)["name"]): int(dict(r)["seq"]) for r in conn.execute(f"SELECT name, seq FROM {SQLITE_SEQUENCE}").fetchall()}


def _parents_first(conn: db.Connection, tables: set[str]) -> list[str]:
    """The tables in an order a restore can insert them in: a table another one points at with a foreign key
    comes before it (items before item_streams), otherwise by name. 1.8.x restores into its own tables, which
    have the foreign keys."""
    parents: dict[str, set[str]] = {table: set() for table in tables}
    for row in conn.execute("SELECT conrelid::regclass::text AS child, confrelid::regclass::text AS parent"
                            " FROM pg_constraint WHERE contype = 'f' AND connamespace = current_schema()::regnamespace"
                            ).fetchall():
        child, parent = str(row["child"]), str(row["parent"])
        if child in parents and parent in parents and child != parent:
            parents[child].add(parent)
    ordered: list[str] = []
    while parents:
        ready = sorted(t for t, waiting in parents.items() if not waiting - set(ordered)) or sorted(parents)[:1]
        for table in ready:
            ordered.append(table)
            del parents[table]
    return ordered


def _table_constraints(conn: db.Connection, table: str, exported: set[str]) -> list[str]:
    """The unique, foreign, check and exclusion keys of a table as parts of its CREATE TABLE. A foreign key to
    a table outside the backup (another schema) is left out - the restore would stop on it."""
    found = []
    for row in conn.execute(
            "SELECT conname, contype::text AS kind, confrelid::regclass::text AS parent,"
            " pg_get_constraintdef(oid) AS definition FROM pg_constraint"
            " WHERE conrelid = (SELECT oid FROM pg_class WHERE relname = ?"
            " AND relnamespace = current_schema()::regnamespace) AND contype IN ('u', 'f', 'c', 'x')"
            " ORDER BY conname", (table,)).fetchall():
        constraint = dict(row)
        if constraint["kind"] == "f" and constraint["parent"] not in exported:
            continue
        name = str(constraint["conname"]).replace('"', '""')
        found.append(f'CONSTRAINT "{name}" {constraint["definition"]}')
    return found


def _export_as_found(config: dialect.DatabaseConfig, destination: Path) -> int:
    """PostgreSQL without pg_dump: every table of the schema as the catalog has it - its columns, types,
    defaults, primary key, unique and foreign keys - then the rows, with the header 1.8.x restores. Not the
    schema file: that one has the new names already. Returns the size.

    The keys are in the CREATE TABLE: a restore of 1.8.x makes its own tables first (IF NOT EXISTS skips
    ours), a restore of 1.9.0 drops the tables and takes ours - without the keys an API key of a deleted
    account or the streams of a deleted item would stay behind for good."""
    from psycopg.rows import dict_row

    from .tasks import _sql_value

    written: list[str] = []
    raw = db._open_raw(config)
    try:
        conn = db.Connection(raw, config.kind)
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # newline="\n": on Windows text mode would write a \n inside a text as \r\n (tasks._read_export).
        with destination.open("w", encoding="utf-8", newline="\n") as out:
            out.write(f"{OLD_EXPORT_MARK}\n-- created: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC, "
                      "before the conversion to English names\n--\n"
                      "SET standard_conforming_strings = on;\nBEGIN;\n\n")
            exported = _tables(conn)
            for table in _parents_first(conn, exported):
                columns = [dict(r) for r in conn.execute(
                    "SELECT column_name, data_type, column_default, is_nullable FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position",
                    (table,)).fetchall()]
                keys = _order(conn, table, [])
                parts = []
                for column in columns:
                    default = str(column["column_default"] or "")
                    if default.startswith("nextval("):
                        kind = "BIGSERIAL" if column["data_type"] == "bigint" else "SERIAL"
                        parts.append(f"{column['column_name']} {kind}")
                        continue
                    part = f"{column['column_name']} {column['data_type']}"
                    if default:
                        part += f" DEFAULT {default}"
                    if column["is_nullable"] == "NO":
                        part += " NOT NULL"
                    parts.append(part)
                if keys:
                    parts.append(f"PRIMARY KEY ({', '.join(keys)})")
                parts += _table_constraints(conn, table, exported)
                out.write(f"CREATE TABLE IF NOT EXISTS {table} (\n    " + ",\n    ".join(parts) + "\n);\n")
                names = [c["column_name"] for c in columns]
                # A server-side cursor: the rows come in batches, a big playback table is never in memory whole.
                with raw.cursor(name=f"jellyscope_export_{len(written)}", row_factory=dict_row) as cursor:
                    cursor.itersize = 2000
                    cursor.execute(f"SELECT {', '.join(names)} FROM {table}")
                    for data in cursor:
                        out.write(f"INSERT INTO {table} ({', '.join(names)}) VALUES "
                                  f"({', '.join(_sql_value(data[n]) for n in names)}) ON CONFLICT DO NOTHING;\n")
                written.append(table)
                for column in columns:
                    if str(column["column_default"] or "").startswith("nextval("):
                        name = column["column_name"]
                        out.write(f"SELECT setval(pg_get_serial_sequence('{table}', '{name}'), "
                                  f"COALESCE((SELECT MAX({name}) FROM {table}), 0) + 1, false);\n")
                out.write("\n")
            out.write("COMMIT;\n")
        raw.rollback()
    finally:
        raw.close()
    return destination.stat().st_size


def backup_before(config: dialect.DatabaseConfig) -> Path:
    """A full copy of the database as it is, before anything is renamed - the way back to 1.8.x.

    SQLite: the built-in backup into a .db file. PostgreSQL: the own export from the catalog with the header
    1.8.x knows - also where pg_dump is at hand, because 1.8.x restores an own export into its own tables,
    while the output of pg_dump stops its psql at "already exists" (README, "Updating to 1.9.0"). It touches
    none of the application's tables (the task log has the old shape now). The copy is written under a
    temporary name and gets its own only when it is whole: a half-written file with the right name and a
    valid header would be offered as the way back. It fails with `db.ConversionFailed` - without the copy
    nothing is converted.
    """
    from . import tasks

    partial: Path | None = None
    folder: Path | str = "?"
    try:
        folder = Path(tasks.backup_folder(tasks.BACKUP_BEFORE_CONVERSION))
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        destination = tasks._free_name(folder, f"jellyscope-{stamp}-{tasks.BACKUP_BEFORE_CONVERSION}",
                                       ".sql" if config.is_postgres else ".db")
        partial = destination.with_name(destination.name + ".partial")
        partial.unlink(missing_ok=True)
        partial.touch(mode=0o600)               # hashes of passwords and API keys: never readable to others
        if not config.is_postgres:
            tasks._backup_sqlite(config, partial)
        else:
            _export_as_found(config, partial)
        partial.chmod(0o600)
        partial.replace(destination)
    except BaseException as error:
        if partial is not None:
            partial.unlink(missing_ok=True)
        if isinstance(error, db.ConversionFailed) or not isinstance(error, Exception):
            raise
        raise _failed([(NO_BACKUP, {"folder": str(folder), "error": error}), (NO_BACKUP_ADVICE, {})]) from error
    return destination


def _note_backup(conn: db.Connection, backup: Path) -> None:
    """Tells the administrator (web.base's stripe) which file it is and until when it is kept."""
    from . import tasks

    until = (date.today() + timedelta(days=tasks.CONVERSION_BACKUP_DAYS)).isoformat()
    for key, value in ((BACKUP_SETTING, json.dumps({"file": backup.name, "until": until})),
                       (NOTICE_CLOSED_SETTING, "")):
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def noted_backup() -> dict[str, str] | None:
    """The backup before the conversion the administrator is to be told about: {"file", "until"}, or None
    (no conversion, the stripe closed, the file gone - pruned after its days or deleted by hand)."""
    from . import tasks

    if db.get_setting(NOTICE_CLOSED_SETTING, "") == "1":
        return None
    try:
        noted = json.loads(db.get_setting(BACKUP_SETTING, "") or "{}")
    except ValueError:
        return None
    name = str(noted.get("file") or "") if isinstance(noted, dict) else ""
    if not name or name != Path(name).name or not name.startswith("jellyscope-"):
        return None
    if not (Path(tasks.backup_folder(tasks.BACKUP_BEFORE_CONVERSION)) / name).is_file():
        return None
    return {"file": name, "until": str(noted.get("until") or "")}


def _failed(reasons: list[tuple[str, dict[str, Any]]], advice: bool = False) -> db.ConversionFailed:
    """The outage page's text: what failed and why; `advice` adds the word for names that are both there."""
    return db.ConversionFailed([(FAILED, {}), *reasons, *([(ADVICE, {})] if advice else [])])


def convert(config: dialect.DatabaseConfig | None = None, names: Any = schema_names) -> dict[str, int] | None:
    """Converts the database to the names of the map, in one transaction. None = nothing to convert.

    Data under both names raises `db.ConversionFailed` before anything is changed.
    """
    config = config or db.database_config()
    raw = db._open_raw(config)
    try:
        if not config.is_postgres:
            raw.isolation_level = None          # the transaction is ours: BEGIN ... COMMIT below
        conn = db.Connection(raw, config.kind)
        if not config.is_postgres:
            conn.execute("BEGIN IMMEDIATE")
        else:
            # Two starts at once (two processes, a restart racing the old one): the second waits here until
            # the first commits, then finds nothing to convert. (SQLite: BEGIN IMMEDIATE does the same.)
            conn.execute("SELECT pg_advisory_xact_lock(?)", (CONVERSION_LOCK,))
        backup: Path | None = None
        committing = committed = False
        try:
            found = plan(conn, names)
            if not found:
                _end(conn, commit=False)
                return None
            if found.conflicts:
                raise _failed(found.conflicts, advice=True)
            backup = None if found.only_leftovers else backup_before(config)
            touched_now = {old for old, _ in found.tables} | set(found.drop_tables)
            before = _prints(conn, found, after=False)
            # settings are checked key by key (two keys of one setting become one row), not by their count
            settings_before, counts_before = _settings(conn), _counts(conn, touched_now | {"settings"})
            renamed = dict(found.tables)
            sequences_expected = {renamed.get(name, name): seq for name, seq in _autoincrement_rows(conn).items()
                                  if name not in found.drop_tables}
            summary = apply(conn, found)
            after = _prints(conn, found, after=True)
            for table in sorted(before):
                if before[table] != after.get(table):
                    raise _failed([(MISMATCH, {"table": table, "before": before[table][0],
                                               "after": after.get(table, (0, ""))[0]})])
            if _settings(conn) != _expected_settings(settings_before, found):
                raise _failed([(MISMATCH_SETTINGS, {})])
            # Every table the conversion does not rename keeps its rows - and is still there.
            counts_after = _counts(conn, {new for _, new in found.tables} | {"settings"})
            for table, count in counts_before.items():
                if counts_after.get(table, -1) != count:
                    raise _failed([(MISMATCH, {"table": table, "before": count, "after": counts_after.get(table, 0)})])
            sequences_after = _autoincrement_rows(conn)
            if sequences_after != sequences_expected:
                raise _failed([(MISMATCH, {"table": SQLITE_SEQUENCE, "before": len(sequences_expected),
                                           "after": len(sequences_after)})])
            if backup is not None:
                _note_backup(conn, backup)
            committing = True
            _end(conn, commit=True)
            committed = True
            db.forget_settings()
            rows = sum(count for count, _ in after.values())
            for table, column, count in found.unreadable_json:
                log.warning("conversion to English names: %s.%s has %s cells that are no JSON - kept as they are",
                            table, column, count)
            log.info("database converted to English names: %s tables, %s columns, %s indexes, %s settings, "
                     "%s values, %s JSON cells, %s tables dropped, %s rows checked", summary["tables"],
                     summary["columns"], summary["indexes"], summary["settings"], summary["values"],
                     summary["json_cells"], summary["dropped"], rows)
        except BaseException as error:
            if committed:
                raise
            try:
                _end(conn, commit=False)
            except Exception:  # noqa: BLE001 - a dead connection: the server rolls the transaction back itself
                log.exception("rolling back the conversion failed")
            if backup is not None and not committing:
                # Nothing was changed, so the copy of this attempt is not needed - and a start that fails again
                # and again (a service restarted every few seconds) would fill the disk with copies. (An error
                # while committing leaves it: whether the commit went through is not certain then.)
                backup.unlink(missing_ok=True)
            if isinstance(error, Exception) and not isinstance(error, db.DatabaseUnavailable):
                # Whatever else stopped it - a driver's error, a bug - ends in the outage page too, not in a
                # crash of the start.
                raise db.ConversionFailed([(FAILED, {}), (UNEXPECTED, {"error": error})]) from error
            raise
        return summary
    finally:
        raw.close()


def _end(conn: db.Connection, commit: bool) -> None:
    if conn.is_postgres:
        conn.commit() if commit else conn.rollback()
    else:
        raw = conn._raw
        if raw.in_transaction:
            raw.execute("COMMIT" if commit else "ROLLBACK")
