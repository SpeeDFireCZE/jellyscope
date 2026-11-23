# -*- coding: utf-8 -*-
r"""The own PostgreSQL export (a backup without pg_dump) must be restorable.

Found on 10. 9. 2026 on a real PostgreSQL 16: the export wrote the schema from
`schema.sql` for SQLite (`AUTOINCREMENT`), so Postgres refused it - through
the application and through `psql -f` alike. And it backed up 8 tables of 12:
without the library snapshots, API keys, blocks and the dashboard's layout.
Since 1.6.7 it is the safeguard before every deletion, so a backup that
cannot be restored is worse than none - it looks like it protects.

The suite runs without Postgres, so what can be told from the file and the
code is guarded:

* the list of backed-up tables = the tables in `schema_postgres.sql`,
* the export takes the schema for PostgreSQL and does not translate it,
* every column from `db.MIGRATIONS` is in the export too (otherwise the
  INSERTs into a clean database do not go through),
* the restore through the application runs the file directly through the
  driver, not through the wrapper that translates question marks and per
  cents - the data hold both,
* the export reads all tables with one connection in one transaction (one
  snapshot of the database). Tables read each on its own could part ways: a
  backup with Prowlarr's old synchronisation mark and already new sums would
  count the same events a second time after a restore. That the snapshot
  really holds is checked only against PostgreSQL (.superpowers/tools/test_on_pg.py)
  - a write by another connection during the export must not get into the file.

Live (the restore through the application and `psql -f`) checked by hand
against PostgreSQL 16.

Run:
    .venv/bin/python tests/test_own_export_pg.py
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
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- the list of tables ---")
schema_pg = db.SCHEMA_POSTGRES.read_text(encoding="utf-8")
in_schema = re.findall(r"^CREATE TABLE (?:IF NOT EXISTS )?(\w+)", schema_pg, re.M)
check(set(in_schema) == set(tasks.BACKED_UP_TABLES),
      f"every table of the schema is backed up (missing: "
      f"{sorted(set(in_schema) - set(tasks.BACKED_UP_TABLES))})")
order = {t: i for i, t in enumerate(tasks.BACKED_UP_TABLES)}
check(order["items"] < order["item_streams"] and order["accounts"] < order["dashboard_layout"],
      "the order respects the foreign keys (items before item_streams, accounts before dashboard_layout)")

print()
print("--- the export ---")
db.init_db()
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
                 " VALUES ('u-a','Ada',0,0,?)", (db.utcnow(),))
    conn.execute("INSERT INTO items (id, name, type) VALUES ('i-x', 'Kdo? 100%', 'Movie')")
    conn.execute("INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr,"
                 " without_tech, written_at) VALUES ('2026-09-01',5,3,2,1000,1,1,0,'2026-09-01 00:00:00')")
    conn.commit()

file = Path(_tmp) / "vlastni.sql"
tasks._dump_own(file)
text = file.read_text(encoding="utf-8")
sql_lines = [r for r in text.splitlines() if not r.lstrip().startswith("--")]
check(not any("AUTOINCREMENT" in r for r in sql_lines),
      "the schema in the export is for PostgreSQL (no AUTOINCREMENT)")
check("BIGSERIAL" in text or "GENERATED" in text or "SERIAL" in text,
      "…and it really is schema_postgres.sql")
missing = [f"{t}.{s}" for t, sl in db.MIGRATIONS.items() for s in sl
           if f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {s} " not in text]
check(not missing, f"every migration column has an ALTER TABLE in the export (missing: {missing})")
check("INSERT INTO library_snapshot" in text, "the data of the library snapshots are in the export")
check("'Kdo? 100%'" in text, "a question mark and a per cent in the data stay literal")
check("INSERT INTO items" in text and "INSERT INTO users" in text, "the tables' data are in the export")

print()
print("--- a carriage return survives reading the file ---")
# Found on 22. 11. 2026 in a database of 1.8.4: error pages of nginx in the task log (502, 504) end their lines
# with \r\n. A text-mode read - the restore of 1.8.x reads the export with Path.read_text - turns \r\n and a lone
# \r into \n, so a bare carriage return came back as a line feed. The export writes it as an escape (E'\r'),
# which no reading changes; today's restore reads without translating line ends, so an older export keeps them.
with_cr = "502 Bad Gateway\r\n<html>\r\nend\rlast \\ back'slash"
written = tasks._sql_value(with_cr)
check("\r" not in written, f"the export writes no bare carriage return ({written!r})")
cr_file = Path(_tmp) / "carriage-return.sql"
cr_file.write_text(written, encoding="utf-8")
check(cr_file.read_text(encoding="utf-8") == written, "a text-mode read (the restore of 1.8.x) gets the same text back")
check(tasks._sql_value("plain 'text' \\ 50%") == "'plain ''text'' \\ 50%'",
      "a text without a carriage return stays in the standard notation (a backslash is a character)")
cr_file.write_bytes("-- Jellyscope backup (own export)\nINSERT INTO t VALUES ('a\r\nb\rc');\n".encode("utf-8"))
check(tasks._read_export(cr_file) == "-- Jellyscope backup (own export)\nINSERT INTO t VALUES ('a\r\nb\rc');\n",
      "today's restore reads an older export without changing its line ends")
# On Windows text mode wrote every \n as \r\n - inside a text too. Our writers write \n on every system now; an
# older export made on Windows (its header line ends with \r\n) is read as before, with universal newlines -
# kept as it is, every line feed inside a text would come back as \r\n (found by the review, 22. 11. 2026).
cr_file.write_bytes("-- Jellyscope backup (own export)\r\nINSERT INTO t VALUES ('a\r\nb');\r\n".encode("utf-8"))
check(tasks._read_export(cr_file) == "-- Jellyscope backup (own export)\nINSERT INTO t VALUES ('a\nb');\n",
      "an older export made on Windows is read with universal newlines")
import inspect  # noqa: E402

from jellyscope import conversion  # noqa: E402

writers = {"tasks._dump_own": inspect.getsource(tasks._dump_own),
           "conversion._export_as_found": inspect.getsource(conversion._export_as_found)}
check(all(re.search(r'\.open\("w", encoding="utf-8", newline="\\n"\)', source) for source in writers.values()),
      f"both writers write \\n on every system ({[n for n, s in writers.items() if 'newline=' not in s]})")
if db.database_config().is_postgres:
    raw = db._open_raw(db.database_config())
    try:
        with raw.cursor() as cursor:
            cursor.execute(f"SELECT {written} AS v")
            row = cursor.fetchone()
            back = row["v"] if isinstance(row, dict) else row[0]
    finally:
        raw.close()
    check(back == with_cr, f"PostgreSQL reads the escape back as the original text ({back!r})")

print()
print("--- one snapshot ---")
connections = {"count": 0}
original_connect = db.connect


def counted_connect(*args, **kwargs):
    connections["count"] += 1
    return original_connect(*args, **kwargs)


db.connect = counted_connect
try:
    tasks._dump_own(Path(_tmp) / "jednim-spojenim.sql")
finally:
    db.connect = original_connect
check(connections["count"] == 1, f"the export reads all tables with one connection, not each on its own ({connections['count']})")
if db.database_config().is_postgres:
    with db.connect() as conn:
        conn.execute("INSERT INTO libraries (id, name) VALUES ('snimek', 'Knihovna snímku')")
    original_value = tasks._sql_value
    happened = {"uz": False}

    def value_with_write(value):
        """At writing the first value (the libraries table) another connection writes a row into settings - that one is read later."""
        if not happened["uz"]:
            happened["uz"] = True
            with original_connect() as other:
                other.execute("INSERT INTO settings (key, value) VALUES ('zapsano_behem_zalohy', 'ano')"
                              " ON CONFLICT (key) DO UPDATE SET value = excluded.value")
        return original_value(value)

    tasks._sql_value = value_with_write
    try:
        tasks._dump_own(Path(_tmp) / "snimek.sql")
    finally:
        tasks._sql_value = original_value
    db.forget_settings()
    check(happened["uz"] and db.get_setting("zapsano_behem_zalohy") == "ano"
          and "zapsano_behem_zalohy" not in (Path(_tmp) / "snimek.sql").read_text(encoding="utf-8"),
          "PostgreSQL: what another connection writes during the export is not in the backup (all tables from one moment)")
else:
    print("      (that the snapshot holds against a concurrent write too is checked only on PostgreSQL)")

print()
print("--- the restore through the application does not translate ---")
source = (PROJECT / "jellyscope" / "tasks.py").read_text(encoding="utf-8")
body = source[source.index("def _restore_postgres"):source.index("def _find_psql")]
check("_raw.cursor()" in body and "cursor.execute(content)" in body,
      "the own export runs directly through the driver")
check("conn.execute(content)" not in body,
      "not through the wrapper, which would double the per cents and rewrite the question marks in the data")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
