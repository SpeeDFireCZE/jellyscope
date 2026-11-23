# -*- coding: utf-8 -*-
r"""Restoring a backup after the conversion to English names: either names land alone and convert.

Before any restore every table Jellyscope has had - today's names, the old ones of the map, the bin
and the older shared *Arr tables - is dropped, so the restored content is the only one in the
database; the restart after a restore converts it. Here the old backup is the one the conversion
made before it renamed (on PostgreSQL an own export from the catalog with the header 1.8.x knows).

Run:
    .venv/bin/python tests/test_conversion_restore.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_restore.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-restore.db")
os.environ["SECRET_KEY"] = "conversion-restore-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

import asyncio  # noqa: E402

from jellyscope import conversion, db, schema_names, tasks  # noqa: E402

config = db.database_config()

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# The map of this test: one table, renamed with a column (the real map fills group by group).
schema_names.TABLES = (schema_names.TableRename("stara_tab", "new_tab", columns={"nazev": "name"}),)
db.init_db()
folder = Path(_tmp) / "backups-test"
db.set_setting("backup_path", str(folder))


def run(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def tables() -> set[str]:
    with db.connect() as conn:
        return conversion._tables(conn)


def content() -> list[dict]:
    return [dict(r) for r in db.query_all("SELECT id, name FROM new_tab ORDER BY id")]


run("CREATE TABLE stara_tab (id TEXT PRIMARY KEY, nazev TEXT NOT NULL DEFAULT '')")
run("INSERT INTO stara_tab (id, nazev) VALUES ('a', 'Abbey Road'), ('b', 'Kind of Blue')")
db.set_setting("ui_language", "cs")

print("--- the start converts, the backup before it keeps the old shape ---")
db.init_db()
old_backup = sorted(folder.glob("jellyscope-*-before-conversion.*"))
check(len(old_backup) == 1 and "new_tab" in tables() and "stara_tab" not in tables(), "converted, with a backup before")
run("INSERT INTO new_tab (id, name) VALUES ('c', 'Written after the conversion')")

print()
print("--- restoring the old backup: the old content alone, converted at the restart ---")
result = tasks.restore_backup(old_backup[0].name)
check(result["status"] == "ok", f"the restore ran ({result})")
check("new_tab" not in tables() or not content(), "the tables of the new names are gone before the old ones come back")
db.init_db()                              # the restart after a restore
check("stara_tab" not in tables() and content() == [{"id": "a", "name": "Abbey Road"}, {"id": "b", "name": "Kind of Blue"}],
      f"the old rows under the new names, the one written after the conversion gone ({content()})")
check(db.get_setting("ui_language") == "cs", "the settings came back too")

print()
print("--- a backup made after the conversion restores the same ---")
# A table of the application (the own export of PostgreSQL holds the application's tables, not this test's).
run("INSERT INTO libraries (id, name) VALUES ('lib-1', 'Filmy')")
made = asyncio.run(tasks.backup_database())
check(made["status"] == "ok", f"a backup after the conversion ({made.get('message', '')})")
run("DELETE FROM libraries")
result = tasks.restore_backup(Path(made["file"]).name)
db.init_db()
check(result["status"] == "ok" and [dict(r)["id"] for r in db.query_all("SELECT id FROM libraries")] == ["lib-1"],
      "and restoring it gives the same rows")

print()
print("--- a pg_dump backup that psql cannot finish changes nothing ---")
# The output of pg_dump is replayed by psql. The tables are dropped in the same transaction as the replay:
# when psql stops, the database is as it was - not empty until somebody notices.
import shutil  # noqa: E402

made_by = Path(made.get("file", ""))
by_pg_dump = config.is_postgres and made_by.is_file() and not tasks._is_own_export(made_by.read_text(encoding="utf-8")[:200])
if not (by_pg_dump and shutil.which("psql")):
    print("SKIP   needs PostgreSQL with pg_dump and psql")
else:
    broken = made_by.parent / "jellyscope-20261121-130000-broken.sql"
    broken.write_text("SELECT jellyscope_no_such_function();\n" + made_by.read_text(encoding="utf-8"), encoding="utf-8")
    result = tasks.restore_backup(broken.name)
    db.forget_settings()
    check(result["status"] == "error" and [dict(r)["id"] for r in db.query_all("SELECT id FROM libraries")] == ["lib-1"]
          and db.get_setting("ui_language") == "cs", f"psql stopped, the database is as it was ({result.get('message', '')[:120]})")
    broken.unlink()

print()
print("--- the tables dropped before a restore ---")
names = tasks._jellyscope_tables()
check({"new_tab", "stara_tab", db.PLAYBACK_BIN, "arr_event", "arr_titul", "api_pouziti"} <= names
      and set(db.TABLES) <= names, "every name Jellyscope has had, and only those")

if db.database_config().is_postgres:
    # A restore of a backup of 1.8.x brings a TABLE named like the lock view; a second restore before the restart
    # (which converts it and makes the view) must still drop everything.
    from jellyscope import schema_names  # noqa: E402
    with db.connect() as conn:
        conn.execute(f"DROP VIEW IF EXISTS {schema_names.LOCK_VIEW}")
        conn.execute(f"CREATE TABLE {schema_names.LOCK_VIEW} (id INTEGER)")
        conn.commit()
    with db.connect() as conn:
        planned = tasks._jellyscope_drops(conn)
    try:
        with db.connect() as conn:
            tasks._drop_jellyscope_tables(conn)
            conn.commit()
        dropped = None
    except Exception as error:  # noqa: BLE001
        dropped = error
    with db.connect() as conn:
        left = conn.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = current_schema()"
                            " AND table_name = ?", (schema_names.LOCK_VIEW,)).fetchone()
    check(dropped is None and int(dict(left)["n"]) == 0,
          f"with {schema_names.LOCK_VIEW} a table (a restore of 1.8.x before the restart) the drops go through ({dropped!r}, {int(dict(left)['n'])} left, {[d for d in planned if 'kos' in d]})")
    db.init_db()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
