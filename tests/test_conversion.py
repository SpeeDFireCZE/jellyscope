# -*- coding: utf-8 -*-
r"""Converting the database to English names at start (jellyscope/conversion.py).

The converter knows the old shape from the catalog, not from a version number, so it converts a
restored old backup too: a table, a column, an index, a setting's key and a stored value are
renamed by the map (jellyscope/schema_names.py). A second start does nothing. When a name exists
in both shapes, nothing is guessed: an empty new one is dropped and the old one renamed; data in
both stops the conversion (db.ConversionFailed - the outage page) with nothing changed.

The map here is a small one of the test's own, so the test does not depend on what the steps of
the database spec have converted so far.

Run:
    .venv/bin/python tests/test_conversion.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion.db")
os.environ["SECRET_KEY"] = "conversion-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import conversion, db, schema_names  # noqa: E402
from jellyscope.schema_names import TableRename  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


NAMES = SimpleNamespace(
    TABLES=(TableRename("stara_tab", "new_tab", columns={"nazev": "name", "rok": "year"},
                        indexes={"idx_stara_nazev": "idx_new_name"}),),
    SETTING_KEYS={"stary_klic": "new_key", "druhy_klic": "second_key"},
    VALUES={("scan_log", "kind"): {"stary": "new"}},
)

db.init_db()
config = db.database_config()


def run(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def rows(sql: str) -> list[dict]:
    return [dict(r) for r in db.query_all(sql)]


def setting(key: str) -> str:
    """Straight from the table: db.get_setting keeps a copy in memory, and the test writes past it."""
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return "" if row is None else str(dict(row)["value"])


def tables() -> set[str]:
    with db.connect() as conn:
        if conn.is_postgres:
            found = conn.execute("SELECT table_name AS name FROM information_schema.tables"
                                 " WHERE table_schema = current_schema()").fetchall()
        else:
            found = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {dict(r)["name"] for r in found}


def indexes() -> set[str]:
    with db.connect() as conn:
        if conn.is_postgres:
            found = conn.execute("SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()").fetchall()
        else:
            found = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {dict(r)["name"] for r in found}


def old_shape() -> None:
    """The table, its index, two settings and a stored value as a database before the conversion has them."""
    for table in ("stara_tab", "new_tab"):
        run(f"DROP TABLE IF EXISTS {table}")
    run("CREATE TABLE stara_tab (id TEXT PRIMARY KEY, nazev TEXT NOT NULL DEFAULT '', rok INTEGER)")
    run("CREATE INDEX idx_stara_nazev ON stara_tab (nazev)")
    run("INSERT INTO stara_tab (id, nazev, rok) VALUES ('a', 'Abbey Road', 1969), ('b', 'Kind of Blue', 1959)")
    for key in ("stary_klic", "new_key", "druhy_klic", "second_key"):
        run("DELETE FROM settings WHERE key = ?", (key,))
    run("INSERT INTO settings (key, value) VALUES ('stary_klic', '42'), ('druhy_klic', 'x')")
    run("DELETE FROM scan_log")
    run("INSERT INTO scan_log (kind, started_at, status) VALUES ('stary', '2026-11-21 10:00:00', 'done'),"
        " ('library', '2026-11-21 10:00:00', 'done')")


print("--- the old shape is converted ---")
old_shape()
with db.connect() as conn:
    plan = conversion.plan(conn, NAMES)
check(bool(plan), "the old shape needs converting")
summary = conversion.convert(config, NAMES)
check(summary is not None and summary["tables"] == 1 and summary["columns"] == 2 and summary["indexes"] == 1
      and summary["settings"] == 2 and summary["values"] == 1, f"the summary counts each kind ({summary})")
check("new_tab" in tables() and "stara_tab" not in tables(), "the table has its new name")
check(rows("SELECT id, name, year FROM new_tab ORDER BY id")
      == [{"id": "a", "name": "Abbey Road", "year": 1969}, {"id": "b", "name": "Kind of Blue", "year": 1959}],
      "and its rows under the new column names")
check("idx_stara_nazev" not in indexes(), "the old index is gone (the schema makes the new one)")
check(setting("new_key") == "42" and setting("stary_klic") == "", "a setting moved to its new key")
check(sorted(r["kind"] for r in rows("SELECT kind FROM scan_log")) == ["library", "new"], "a stored value is renamed, others stay")
check(conversion.convert(config, NAMES) is None, "a second start does nothing")

print()
print("--- both names: the new one empty ---")
old_shape()
run("CREATE TABLE new_tab (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', year INTEGER)")
conversion.convert(config, NAMES)
check(rows("SELECT COUNT(*) AS n FROM new_tab")[0]["n"] == 2 and "stara_tab" not in tables(),
      "an empty new table is dropped and the old one takes its place")

print()
print("--- both names with data ---")
old_shape()
run("CREATE TABLE new_tab (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', year INTEGER)")
run("INSERT INTO new_tab (id, name) VALUES ('c', 'Blue Train')")
try:
    conversion.convert(config, NAMES)
    check(False, "data in both tables must stop the conversion")
except db.ConversionFailed as error:
    check("stara_tab" in str(error) and "new_tab" in str(error) and conversion.ADVICE in str(error),
          f"the conversion stops, names both and says to keep one ({str(error)[:80]})")
check({"stara_tab", "new_tab"} <= tables() and rows("SELECT COUNT(*) AS n FROM stara_tab")[0]["n"] == 2
      and setting("stary_klic") == "42", "and nothing was changed")

print()
print("--- a setting under both names ---")
old_shape()
run("INSERT INTO settings (key, value) VALUES ('new_key', '42')")       # the same value: nothing to decide
conversion.convert(config, NAMES)
check(setting("new_key") == "42" and setting("stary_klic") == "", "the same value: the old key goes")

old_shape()
# 'ui_language' plays the new key whose stored value is its default ('en'): the old custom value wins.
names_default = SimpleNamespace(TABLES=NAMES.TABLES, SETTING_KEYS={"stary_klic": "ui_language"}, VALUES={})
run("DELETE FROM settings WHERE key = 'ui_language'")
run("INSERT INTO settings (key, value) VALUES ('ui_language', ?)", (db.DEFAULT_SETTINGS["ui_language"],))
run("UPDATE settings SET value = 'cs' WHERE key = 'stary_klic'")
conversion.convert(config, names_default)
check(setting("ui_language") == "cs" and setting("stary_klic") == "",
      "the new key at its default: the old custom value wins")

old_shape()
# The old key holds the default, the new one a custom value (older code wrote the default under the old name
# again): the custom new value stays, the old key goes.
run("DELETE FROM settings WHERE key = 'ui_language'")
run("INSERT INTO settings (key, value) VALUES ('ui_language', 'cs')")
run("UPDATE settings SET value = ? WHERE key = 'stary_klic'", (db.DEFAULT_SETTINGS["ui_language"],))
default_error = None
try:
    conversion.convert(config, names_default)
except db.ConversionFailed as error:
    default_error = error
check(default_error is None and setting("ui_language") == "cs" and setting("stary_klic") == "",
      f"the old key at its default: the new custom value stays ({str(default_error)[:80] if default_error else ''})")
run("UPDATE settings SET value = ? WHERE key = 'ui_language'", (db.DEFAULT_SETTINGS["ui_language"],))

old_shape()
run("INSERT INTO settings (key, value) VALUES ('new_key', '7')")        # both custom and different
try:
    conversion.convert(config, NAMES)
    check(False, "two different custom values must stop the conversion")
except db.ConversionFailed as error:
    check("stary_klic" in str(error) and "new_key" in str(error), "the conversion stops and names both keys")
check(setting("stary_klic") == "42" and setting("new_key") == "7" and "stara_tab" in tables(),
      "and nothing was changed")

print()
print("--- an error in the middle: everything goes back ---")
import logging  # noqa: E402
import subprocess  # noqa: E402

original_apply = conversion.apply


def state() -> tuple:
    names = tables()
    content = rows("SELECT * FROM stara_tab ORDER BY id") if "stara_tab" in names else []
    return ("stara_tab" in names, "new_tab" in names, content, setting("stary_klic"),
            sorted(r["kind"] for r in rows("SELECT kind FROM scan_log")))


old_shape()
before = state()


def broken_apply(conn, found):
    original_apply(conn, found)
    raise RuntimeError("disk gone")       # after every rename, before the commit

from jellyscope import tasks  # noqa: E402

backups = Path(tasks.backup_folder(tasks.BACKUP_BEFORE_CONVERSION))


def made_backups() -> list[str]:
    return sorted(p.name for p in backups.glob("*before-conversion*")) if backups.is_dir() else []


made_before = made_backups()
caught: BaseException | None = None
conversion.apply = broken_apply
try:
    conversion.convert(config, NAMES)
except Exception as error:  # noqa: BLE001 - what comes out is the point
    caught = error
finally:
    conversion.apply = original_apply
check(state() == before, "an error after the renames leaves the database as it was")
# An error the conversion does not know (a driver's, a bug) must still end in the outage page, not in a crash:
# a service restarted every few seconds would otherwise make a new full backup each time until the disk is full.
check(isinstance(caught, db.ConversionFailed) and "disk gone" in str(caught),
      f"an unexpected error comes out as the conversion's failure ({type(caught).__name__}: {str(caught)[:80]})")
check(made_backups() == made_before, f"the backup of the failed attempt is gone, the database is as it was ({made_backups()})")

print()
print("--- a row lost in the conversion is caught ---")


def losing_apply(conn, found):
    summary = original_apply(conn, found)
    conn.execute("DELETE FROM new_tab WHERE id = 'a'")
    return summary

conversion.apply = losing_apply
try:
    conversion.convert(config, NAMES)
    check(False, "a lost row must stop the conversion")
except db.ConversionFailed as error:
    check("new_tab" in str(error) and conversion.ADVICE not in str(error),
          f"the check names the table, without the advice for two names ({str(error)[:90]})")
finally:
    conversion.apply = original_apply
check(state() == before, "and nothing was changed")


run("CREATE TABLE ostatni_tab (id TEXT PRIMARY KEY)")
run("INSERT INTO ostatni_tab (id) VALUES ('x')")


def vanishing_apply(conn, found):
    summary = original_apply(conn, found)
    conn.execute("DROP TABLE ostatni_tab")          # a table the conversion does not touch at all
    return summary

conversion.apply = vanishing_apply
try:
    conversion.convert(config, NAMES)
    check(False, "a table that vanished must stop the conversion")
except db.ConversionFailed as error:
    check("ostatni_tab" in str(error), f"a table the conversion does not touch is counted too ({str(error)[:90]})")
finally:
    conversion.apply = original_apply
check(state() == before and "ostatni_tab" in tables(), "and nothing was changed")
run("DROP TABLE IF EXISTS ostatni_tab")


def changing_apply(conn, found):
    summary = original_apply(conn, found)
    conn.execute("UPDATE new_tab SET year = 2000 WHERE id = 'a'")
    return summary

conversion.apply = changing_apply
try:
    conversion.convert(config, NAMES)
    check(False, "a changed value must stop the conversion")
except db.ConversionFailed as error:
    check("new_tab" in str(error), "a changed value is caught too")
finally:
    conversion.apply = original_apply
check(state() == before, "and nothing was changed")



def setting_losing_apply(conn, found):
    summary = original_apply(conn, found)
    conn.execute("DELETE FROM settings WHERE key = 'new_key'")
    return summary

conversion.apply = setting_losing_apply
try:
    conversion.convert(config, NAMES)
    check(False, "a lost setting must stop the conversion")
except db.ConversionFailed as error:
    check("settings" in str(error), "a lost setting is caught")
finally:
    conversion.apply = original_apply
check(state() == before, "and nothing was changed")

print()
print("--- the process killed in the middle ---")
script = (
    "import sys, time; sys.path.insert(0, %r)\n"
    "from types import SimpleNamespace\n"
    "from jellyscope import conversion, db\n"
    "from jellyscope.schema_names import TableRename\n"
    "names = SimpleNamespace(TABLES=(TableRename('stara_tab', 'new_tab', columns={'nazev': 'name', 'rok': 'year'},"
    " indexes={'idx_stara_nazev': 'idx_new_name'}),), SETTING_KEYS={'stary_klic': 'new_key', 'druhy_klic': 'second_key'},"
    " VALUES={('scan_log', 'kind'): {'stary': 'new'}})\n"
    "original = conversion.apply\n"
    "def slow(conn, found):\n"
    "    summary = original(conn, found)\n"
    "    print('renamed', flush=True); time.sleep(60)\n"
    "    return summary\n"
    "conversion.apply = slow\n"
    "conversion.convert(db.database_config(), names)\n") % str(PROJECT)
child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, env=dict(os.environ))
line = child.stdout.readline()
child.kill()
child.wait()
check(line.strip() == "renamed", "the child renamed everything before it was killed")
check(state() == before, "a killed conversion leaves the database as it was")
check(conversion.convert(config, NAMES) is not None and "new_tab" in tables(), "and the next start converts it")

print()
print("--- the log says what happened ---")
old_shape()
heard: list[str] = []


class _Catch(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        heard.append(record.getMessage())


catch = _Catch()
logging.getLogger("jellyscope.conversion").addHandler(catch)
logging.getLogger("jellyscope.conversion").setLevel(logging.INFO)
conversion.convert(config, NAMES)
logging.getLogger("jellyscope.conversion").removeHandler(catch)
check(any(m.startswith("database converted to English names: 1 tables, 2 columns, 1 indexes, 2 settings, 1 values")
          for m in heard), f"a summary line ({heard[-1:] if heard else None})")

print()
print("--- a mapped value in the primary key changes the order of the rows ---")
# The print before reads the old values in their order and maps them; after, the new values come in their own
# order (zebra after query, where akce came before dotaz). The same rows must give the same print.
ORDER_NAMES = SimpleNamespace(TABLES=(TableRename("stara_hodina", "new_hour", columns={"druh": "kind"}),),
                              SETTING_KEYS={}, VALUES={("new_hour", "kind"): {"akce": "zebra", "dotaz": "query"}})
run("CREATE TABLE stara_hodina (druh TEXT NOT NULL, n INTEGER NOT NULL, pocet INTEGER, PRIMARY KEY (druh, n))")
run("INSERT INTO stara_hodina (druh, n, pocet) VALUES ('akce', 1, 5), ('dotaz', 1, 7), ('rss', 2, 9)")
order_error = None
try:
    conversion.convert(config, ORDER_NAMES)
except db.ConversionFailed as error:
    order_error = error
hour_rows = sorted((r["kind"], r["n"], r["pocet"]) for r in rows("SELECT kind, n, pocet FROM new_hour")) \
    if "new_hour" in tables() else []
check(order_error is None and hour_rows == [("query", 1, 7), ("rss", 2, 9), ("zebra", 1, 5)],
      f"converted, the rows the same ({str(order_error)[:90] if order_error else hour_rows})")
run("DROP TABLE IF EXISTS new_hour")
run("DROP TABLE IF EXISTS stara_hodina")

print()
print("--- the primary key's columns in their own order ---")
run("CREATE TABLE pk_order_tab (a TEXT NOT NULL, b TEXT NOT NULL, PRIMARY KEY (b, a))")
with db.connect() as conn:
    key = conversion._order(conn, "pk_order_tab", ["a", "b"])
check(key == ["b", "a"], f"(b, a), not the columns' order ({key})")
run("DROP TABLE pk_order_tab")

print()
print("--- stored values: one look per column, not per value ---")
MANY = SimpleNamespace(TABLES=(), SETTING_KEYS={},
                       VALUES={("scan_log", "kind"): {f"stary_{n}": f"new_{n}" for n in range(40)}})
run("DELETE FROM scan_log")
run("INSERT INTO scan_log (kind, started_at, status) VALUES ('stary_3', '2026-11-21 10:00:00', 'done'),"
    " ('stary_17', '2026-11-21 10:00:00', 'done'), ('library', '2026-11-21 10:00:00', 'done')")
asked: list[str] = []
with db.connect() as conn:
    plain_execute = conn.execute
    conn.execute = lambda sql, *args, **kwargs: (asked.append(sql), plain_execute(sql, *args, **kwargs))[1]
    many = conversion.plan(conn, MANY)
looks = [sql for sql in asked if "FROM scan_log" in sql]
check(sorted(old for _, _, old, _ in many.values) == ["stary_17", "stary_3"] and len(looks) == 1,
      f"the two stored values found with {len(looks)} look(s) at scan_log")
run("DELETE FROM scan_log")


def chains(names) -> list[str]:
    """A name that is both a new and an old one would be renamed twice (a -> b, then b -> c)."""
    renamed = [t for t in names.TABLES if t.old != t.new]        # a table that keeps its name renames nothing
    found = [t.new for t in renamed if t.new in {u.old for u in renamed}]
    found += [f"{t.new}.{new}" for t in names.TABLES for new in t.columns.values() if new in t.columns]
    found += [new for new in names.SETTING_KEYS.values() if new in names.SETTING_KEYS]
    found += [f"{table}.{column}={new}" for (table, column), mapping in names.VALUES.items()
              for new in mapping.values() if new in mapping]
    return found


check(not chains(schema_names), f"the real map renames nothing twice ({chains(schema_names)})")
chained = SimpleNamespace(TABLES=(TableRename("a_tab", "b_tab"), TableRename("b_tab", "c_tab"), TableRename("d_tab", "d_tab")),
                          SETTING_KEYS={"k1": "k2", "k2": "k3"}, VALUES={("t", "c"): {"x": "y", "y": "z"}})
check(chains(chained) == ["b_tab", "k2", "t.c=y"], f"and the guard sees a chain ({chains(chained)})")

print()
print("--- two starts at once ---")
if not config.is_postgres:
    print("SKIP   on SQLite BEGIN IMMEDIATE lets one start convert at a time")
else:
    import threading  # noqa: E402
    import time  # noqa: E402

    old_shape()
    inside, results = threading.Event(), {}

    def slow_apply(conn, found):
        inside.set()
        time.sleep(2)                       # the first start holds its transaction open for a while
        return original_apply(conn, found)

    def first() -> None:
        try:
            results["first"] = conversion.convert(config, NAMES)
        except Exception as error:  # noqa: BLE001
            results["first"] = error

    conversion.apply = slow_apply
    made_before_both = made_backups()
    thread = threading.Thread(target=first)
    thread.start()
    inside.wait(30)
    conversion.apply = original_apply
    try:
        results["second"] = conversion.convert(config, NAMES)
    except Exception as error:  # noqa: BLE001
        results["second"] = error
    thread.join(60)
    check(isinstance(results.get("first"), dict) and results.get("second") is None,
          f"the second waits for the first and finds nothing to do ({results})")
    check(len(made_backups()) == len(made_before_both) + 1, "and only one backup is made")

print()
print("--- init_db converts with the real map ---")
check(conversion.plan.__module__ == "jellyscope.conversion", "the converter is its own module")
with db.connect() as conn:
    check(not conversion.plan(conn), "a database made by today's schema has nothing to convert")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
