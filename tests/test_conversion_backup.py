# -*- coding: utf-8 -*-
r"""A backup before the conversion: made first, kept 30 days, the administrator told.

The conversion makes a full copy of the database as it is - before anything is renamed, without
the application's tables (the task log has the old shape then) - so the way back to 1.8.x is a
restore of it. Without the copy nothing is converted (the outage page says why). The copy stays out
of the usual pruning and goes after 30 days; the administrator sees a stripe with its file and date
until they close it or the file is gone.

Run:
    .venv/bin/python tests/test_conversion_backup.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_backup.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-backup.db")
os.environ["SECRET_KEY"] = "conversion-backup-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, conversion, db, tasks, web  # noqa: E402
from jellyscope.schema_names import TableRename  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


NAMES = SimpleNamespace(TABLES=(TableRename("stara_tab", "new_tab", columns={"nazev": "name"}),),
                        SETTING_KEYS={}, VALUES={})
db.init_db()
config = db.database_config()
folder = Path(_tmp) / "zalohy-test"
db.set_setting("backup_path", str(folder))


def run(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def tables() -> set[str]:
    with db.connect() as conn:
        return conversion._tables(conn)


# A carriage return in a text (an error page of nginx in the task log ends its lines with \r\n - found 22. 11. 2026
# in a database of 1.8.4): the restore of 1.8.x reads the export as text, which turns a bare \r into \n.
WITH_CR = "502 Bad Gateway\r\n<html>\r\nend\r"
OLD_ROWS = [("a", "Abbey Road"), ("b", "O'Brien's 50% Mix"), ("c", WITH_CR)]


def old_shape() -> None:
    for table in ("stara_tab", "new_tab"):
        run(f"DROP TABLE IF EXISTS {table}")
    # A unique and a foreign key too: a restore into an empty database must give them back (found 22. 11. 2026 -
    # the export wrote only the columns and the primary key, so API keys of a deleted account stayed behind).
    run("CREATE TABLE stara_tab (id TEXT PRIMARY KEY, nazev TEXT NOT NULL DEFAULT '', kod TEXT UNIQUE,"
        " rodic TEXT REFERENCES stara_tab(id) ON DELETE CASCADE)")
    run("INSERT INTO stara_tab (id, nazev) VALUES ('a', 'Abbey Road'), ('b', 'O''Brien''s 50% Mix')")
    run("INSERT INTO stara_tab (id, nazev, kod, rodic) VALUES ('c', ?, 'k', 'a')", (WITH_CR,))


print("--- the backup comes first ---")
old_shape()
seen_at_apply: list[list[str]] = []
original_apply = conversion.apply


def watching_apply(conn, found):
    seen_at_apply.append(sorted(p.name for p in folder.glob("*before-conversion*")))
    return original_apply(conn, found)

conversion.apply = watching_apply
try:
    conversion.convert(config, NAMES)
finally:
    conversion.apply = original_apply
made = sorted(folder.glob("jellyscope-*-before-conversion.*"))
check(len(made) == 1, f"one backup before the conversion ({[p.name for p in made]})")
check(bool(seen_at_apply) and seen_at_apply[0] == [made[0].name] if made else False,
      "and it existed before anything was renamed")
check(made and made[0].suffix == (".sql" if config.is_postgres else ".db"), "a .db for SQLite, a .sql for PostgreSQL")
if config.is_postgres:
    # 1.8.x restores an own export into its own tables; the output of pg_dump it cannot (psql stops at
    # "already exists"). So on PostgreSQL this backup is the own export even where pg_dump is at hand.
    first = made[0].read_text(encoding="utf-8").splitlines()[0] if made else ""
    check(first == conversion.OLD_EXPORT_MARK, f"on PostgreSQL the own export with the header 1.8.x knows ({first[:60]!r})")

print()
print("--- the backup holds the database before the conversion ---")
if made and not config.is_postgres:
    import sqlite3  # noqa: E402
    copy = sqlite3.connect(made[0])
    try:
        names = {r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        content = copy.execute("SELECT id, nazev FROM stara_tab ORDER BY id").fetchall()
    finally:
        copy.close()
    check("stara_tab" in names and "new_tab" not in names, "the copy has the old names")
    check(content == OLD_ROWS, "and the old rows")
elif made:
    text = made[0].read_text(encoding="utf-8")
    check(text.startswith("-- Záloha Jellyscope") or "PostgreSQL database dump" in text[:300],
          "an own export with the header 1.8.x knows, or pg_dump's")
    if text.startswith("-- Záloha Jellyscope"):
        with db.connect() as conn:
            for table in sorted(conversion._tables(conn)):
                conn.execute(f"DROP TABLE {table} CASCADE")
        raw = db._open_raw(config)
        try:
            with raw.cursor() as cursor:
                cursor.execute(text)
            raw.commit()
        finally:
            raw.close()
        check("stara_tab" in tables() and [tuple(dict(r).values()) for r in db.query_all(
                  "SELECT id, nazev FROM stara_tab ORDER BY id")] == OLD_ROWS,
              "restored into an empty database (read as text, as 1.8.x does) it gives the old tables and rows,"
              " a carriage return too")
        constraints = sorted(str(dict(r)["c"]) for r in db.query_all(
            "SELECT contype::text || ' ' || pg_get_constraintdef(oid) AS c FROM pg_constraint"
            " WHERE conrelid = 'stara_tab'::regclass AND contype <> 'p'"))
        check(constraints == ["f FOREIGN KEY (rodic) REFERENCES stara_tab(id) ON DELETE CASCADE", "u UNIQUE (kod)"],
              f"and its unique and foreign key (a restore drops the tables first) ({constraints})")
        db.init_db()

print()
print("--- no backup, no conversion ---")
old_shape()
for leftover in folder.glob("*"):
    leftover.unlink()
folder.chmod(0o500)
try:
    conversion.convert(config, NAMES)
    check(False, "a folder that cannot be written to must stop the conversion")
except db.ConversionFailed as error:
    check("backup" in str(error).lower(), f"the conversion stops and says why ({str(error)[:90]})")
    # The outage page is all a person sees: the folder by name, advice they can follow from there (Settings is
    # out of reach then), and no word about two names to choose from.
    check(str(folder) in str(error) and "Settings" not in str(error) and conversion.ADVICE not in str(error),
          f"it names the folder and gives advice that works from the outage page ({str(error)[:200]})")
finally:
    folder.chmod(0o700)
check("stara_tab" in tables() and "new_tab" not in tables(), "and nothing was renamed")

print()
print("--- a backup that fails half way leaves no file ---")
# A full disk in the middle of the copy: a file with the right name and a valid header but half the data
# would be offered as the way back (README) - and a restore of it would commit only a part.
for leftover in folder.glob("*"):
    leftover.unlink()


def half_written(*args):
    Path(args[-1]).write_text(conversion.OLD_EXPORT_MARK + "\nINSERT INTO libraries VALUES ('x');\n", encoding="utf-8")
    raise OSError(28, "No space left on device")


saved = (tasks._backup_sqlite, tasks._pg_dump, conversion._export_as_found)
tasks._backup_sqlite = tasks._pg_dump = conversion._export_as_found = half_written
try:
    conversion.convert(config, NAMES)
    check(False, "a backup that fails must stop the conversion")
except db.ConversionFailed as error:
    check("space" in str(error), f"the conversion stops and says why ({str(error)[:90]})")
finally:
    tasks._backup_sqlite, tasks._pg_dump, conversion._export_as_found = saved
check(not list(folder.glob("*")), f"no file is left behind ({[p.name for p in folder.glob('*')]})")
check("stara_tab" in tables() and "new_tab" not in tables(), "and nothing was renamed")

print()
print("--- 30 days, out of the usual pruning ---")
db.set_setting("backup_keep", "1")
for leftover in folder.glob("*"):
    leftover.unlink()
for day in range(10):
    (folder / f"jellyscope-202611{day + 10:02d}-030000.db").write_bytes(b"x")
recent = folder / f"jellyscope-{(date.today() - timedelta(days=2)).strftime('%Y%m%d')}-120000-before-conversion.db"
recent.write_bytes(b"x")
tasks._prune_backups(folder)
check(recent.exists(), "a backup before the conversion is not pruned with the others")
check(len(list(folder.glob("jellyscope-202611*-030000.db"))) == 1, "the usual ones keep backup_keep")
old = folder / f"jellyscope-{(date.today() - timedelta(days=31)).strftime('%Y%m%d')}-120000-before-conversion.db"
old.write_bytes(b"x")
tasks._prune_backups(folder)
check(not old.exists() and recent.exists(), "after 30 days it goes, a younger one stays")

print()
print("--- the administrator is told ---")
for leftover in folder.glob("*"):
    leftover.unlink()
old_shape()
conversion.convert(config, NAMES)
made = sorted(folder.glob("jellyscope-*-before-conversion.*"))
stored = json.loads(db.query_one("SELECT value FROM settings WHERE key = 'conversion_backup'")["value"])
check(made and stored["file"] == made[0].name and stored["until"] == (date.today() + timedelta(days=30)).isoformat(),
      f"the conversion notes its backup and the day it goes ({stored})")
accounts.create("conv_admin", "conversion-backup-pw", is_admin=True)
accounts.create("conv_reader", "conversion-backup-pw", is_admin=False)


def page(user: str) -> str:
    client = TestClient(web.app)
    client.post("/login", data={"username": user, "password": "conversion-backup-pw", "method": "local"})
    return client.get("/settings?section=general").text


admin_page = page("conv_admin")
check(made and made[0].name in admin_page and 'data-conversion-notice' in admin_page, "the administrator sees the stripe with the file")
check("data-conversion-notice" not in page("conv_reader"), "a reader does not")
client = TestClient(web.app)
client.post("/login", data={"username": "conv_admin", "password": "conversion-backup-pw", "method": "local"})
answer = client.post("/settings/conversion-notice/close", follow_redirects=False)
check(answer.status_code == 303 and "data-conversion-notice" not in page("conv_admin"), "closed, it is gone")
run("DELETE FROM settings WHERE key = 'conversion_notice_closed'")
db.forget_settings()
for leftover in folder.glob("*before-conversion*"):
    leftover.unlink()
check("data-conversion-notice" not in page("conv_admin"), "and when the backup file is gone, so is the stripe")

print()
print("--- the way back: the code of 1.8.4 restores the backup ---")
# The way back to 1.8.x is a restore of this backup in 1.8.x (README, "Going back to 1.8.x"): a database made
# by the code of the released 1.8.4, its backup made by this code the way the conversion makes it, then
# 1.8.4 on an empty database restores it in
# Settings → Backups (restore_backup) and starts again. Every table of 1.8.4 has the same rows as before.
# The backup is made by backup_before itself - with pg_dump at hand, too.
# The code of 1.8.4 comes from the tag in the prod repository beside this one (read only, git archive);
# without it the part is skipped. (A database of 1.8.4 has nothing to convert until the core tables are
# converted - step 4 - so the backup is made straight by the conversion's function.)
import subprocess  # noqa: E402

PROD = PROJECT.parent / "jellyscope-prod"
PRINTS = r'''
import hashlib, json
def prints(db):
    found = {}
    with db.connect() as conn:
        for table in list(db.TABULKY) + [db.KOS]:
            rows = sorted(repr(tuple(str(v) for v in dict(r).values())) for r in conn.execute(f"SELECT * FROM {table}").fetchall())
            found[table] = [len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest()]
    return found
'''
MAKE_184 = PRINTS + r'''
import sys
sys.path.insert(0, ".")
from jellyscope import db, demodata
db.init_db()
demodata.seed()
with db.connect() as conn:   # an error page of nginx in the task log: lines end with \r\n (found 22. 11. 2026)
    conn.execute("INSERT INTO scan_log (kind, started_at, finished_at, status, message) VALUES (?, ?, ?, ?, ?)",
                 ("recent", "2026-11-01 10:00:00", "2026-11-01 10:00:01", "error", "502 Bad Gateway\r\n<html>\r\nend\r"))
    conn.commit()
db.set_setting("backup_path", sys.argv[1])
print(json.dumps(prints(db)))
'''
BACKUP_NOW = r'''
import sys
sys.path.insert(0, ".")
from jellyscope import conversion, db
print(conversion.backup_before(db.database_config()).name)
'''
RESTORE_184 = r'''
import json, sys
sys.path.insert(0, ".")
from jellyscope import db, tasks
db.init_db()
db.set_setting("backup_path", sys.argv[1])
result = tasks.restore_backup(sys.argv[2])
print(json.dumps({"status": result.get("status"), "message": str(result.get("message", ""))[:300]}))
'''
START_184 = PRINTS + r'''
import sys
sys.path.insert(0, ".")
from jellyscope import db
db.init_db()
print(json.dumps(prints(db)))
'''

# This code: the rows of every table (and the settings) and the shape of the database (tables with their columns,
# indexes, views); SHAPE_19 only reads, START_19 starts first (init_db: the conversion, the lock).
SHAPE_19 = r'''
import hashlib, json, sys
sys.path.insert(0, ".")
from jellyscope import conversion, db
def shape():
    with db.connect() as conn:
        found = {"rows": {}, "tables": {}}
        for table in sorted(conversion._tables(conn)):
            query = (f"SELECT * FROM {table} WHERE key NOT IN ('{conversion.BACKUP_SETTING}', '{conversion.NOTICE_CLOSED_SETTING}')"
                     if table == "settings" else f"SELECT * FROM {table}")   # the note of a conversion is no data
            rows = sorted(repr(tuple(str(v) for v in dict(r).values())) for r in conn.execute(query).fetchall())
            found["rows"][table] = [len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest()]
            found["tables"][table] = sorted(conn.table_columns(table))
        found["indexes"] = sorted(conversion._indexes(conn))
        if conn.is_postgres:
            views = conn.execute("SELECT table_name AS name FROM information_schema.views WHERE table_schema = current_schema()")
        else:
            views = conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
        found["views"] = sorted(str(dict(r)["name"]) for r in views.fetchall())
        note = conn.execute("SELECT value FROM settings WHERE key = ?", (conversion.BACKUP_SETTING,)).fetchone()
        found["note"] = str(dict(note)["value"]) if note else None    # which backup the stripe offers as the way back
        # Unique and foreign keys: a restore into an empty database creates the tables from the backup itself.
        found["constraints"] = sorted(str(dict(r)["c"]) for r in conn.execute(
            "SELECT conrelid::regclass::text || ' ' || pg_get_constraintdef(oid) AS c FROM pg_constraint"
            " WHERE connamespace = current_schema()::regnamespace AND contype <> 'p'").fetchall()) if conn.is_postgres else []
    return found
'''
START_19 = SHAPE_19 + r'''
db.init_db()
print(json.dumps(shape()))
'''
READ_19 = SHAPE_19 + r'''
print(json.dumps(shape()))
'''
# A backup of 1.8.4 restored in 1.9.0 (Settings → Backups) and the restart after it (init_db converts it).
RESTORE_19 = SHAPE_19 + r'''
from jellyscope import tasks
db.init_db()
db.set_setting("backup_path", sys.argv[1])
result = tasks.restore_backup(sys.argv[2])
db.forget_settings()
db.init_db()
print(json.dumps({"status": result.get("status"), "message": str(result.get("message", ""))[:300], "shape": shape()}))
'''


def differences(a: dict, b: dict) -> list[str]:
    """What differs between two shapes (SHAPE_19): tables, their rows and columns, indexes, views."""
    found = [f"rows {t}" for t in sorted(set(a.get("rows", {})) | set(b.get("rows", {})))
             if a.get("rows", {}).get(t) != b.get("rows", {}).get(t)]
    found += [f"columns {t}: {sorted(set(a.get('tables', {}).get(t, [])) ^ set(b.get('tables', {}).get(t, [])))}"
              for t in sorted(set(a.get("tables", {})) | set(b.get("tables", {})))
              if a.get("tables", {}).get(t) != b.get("tables", {}).get(t)]
    found += [f"indexes {sorted(set(a.get('indexes', [])) ^ set(b.get('indexes', [])))}"] if a.get("indexes") != b.get("indexes") else []
    found += [f"views {a.get('views')} / {b.get('views')}"] if a.get("views") != b.get("views") else []
    found += ([f"constraints {sorted(set(a.get('constraints', [])) ^ set(b.get('constraints', [])))}"]
              if a.get("constraints") != b.get("constraints") else [])
    return found


def answer_of(done: subprocess.CompletedProcess) -> dict:
    return json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 and done.stdout.strip() else {}


def child(code_root: Path, script: str, database: str, *args: str) -> subprocess.CompletedProcess:
    """A process of the code in `code_root` over `database` (a SQLite file, or a PostgreSQL database by name)."""
    home = Path(tempfile.mkdtemp(prefix="way-back-"))
    (home / "data").mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_PATH", "JELLYSCOPE_HOME")}
    env.update(JELLYSCOPE_HOME=str(home), SECRET_KEY="way-back-secret-key-long-enough-123", JELLYSCOPE_DEMO="0",
               JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="x")
    if config.is_postgres:
        (home / "data" / "database.json").write_text(json.dumps({
            "kind": "postgres", "host": config.host, "port": config.port, "database": database, "user": config.user,
            "password": config.password, "use_pool": False}), encoding="utf-8")
    else:
        env["DATABASE_PATH"] = database
    return subprocess.run([sys.executable, "-c", script, *args], cwd=code_root, env=env, capture_output=True, text=True,
                          timeout=900)


def admin_sql(sql: str) -> None:
    import psycopg
    with psycopg.connect(host=config.host, port=config.port, user=config.user, password=config.password or None,
                         dbname="postgres", autocommit=True) as connection:
        connection.execute(sql)


archive = subprocess.run(["git", "-C", str(PROD), "archive", "1.8.4"], capture_output=True) if PROD.is_dir() else None
if archive is None or archive.returncode:
    print("SKIP   the code of 1.8.4 is not at hand (the tag 1.8.4 in ../jellyscope-prod)")
else:
    v184 = Path(tempfile.mkdtemp(prefix="v184-code-"))
    subprocess.run(["tar", "-x", "-C", str(v184)], input=archive.stdout, check=True)
    way_back = Path(_tmp) / "way-back-backups"
    tag = f"{os.getpid()}"
    if config.is_postgres:
        old_db, new_db, fresh_db, restore_db = (f"jellyscope_wayback_old_{tag}", f"jellyscope_wayback_new_{tag}",
                                                f"jellyscope_wayback_fresh_{tag}", f"jellyscope_wayback_restore_{tag}")
        for name in (old_db, new_db, fresh_db, restore_db):
            admin_sql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            admin_sql(f"CREATE DATABASE {name}")
    else:
        old_db, new_db, fresh_db, restore_db = (str(Path(_tmp) / "v184-old.db"), str(Path(_tmp) / "v184-new.db"),
                                                str(Path(_tmp) / "v190-fresh.db"), str(Path(_tmp) / "v190-restore.db"))
    try:
        made = child(v184, MAKE_184, old_db, str(way_back))
        before = json.loads(made.stdout.strip().splitlines()[-1]) if made.returncode == 0 else {}
        check(made.returncode == 0 and len(before) >= 15 and before.get("playback", [0])[0] > 0,
              f"1.8.4 made its database ({len(before)} tables, {before.get('playback', [0])[0]} plays) {made.stderr[-300:]}")
        backed = child(PROJECT, BACKUP_NOW, old_db)
        name = backed.stdout.strip().splitlines()[-1] if backed.returncode == 0 and backed.stdout.strip() else ""
        check(name.startswith("jellyscope-") and (way_back / name).is_file(),
              f"this code backed it up the way the conversion does ({name}) {backed.stderr[-300:]}")
        restored = child(v184, RESTORE_184, new_db, str(way_back), name)
        answer = json.loads(restored.stdout.strip().splitlines()[-1]) if restored.returncode == 0 else {}
        check(answer.get("status") == "ok", f"1.8.4 restored it on an empty database ({answer}) {restored.stderr[-300:]}")
        started = child(v184, START_184, new_db)
        after = json.loads(started.stdout.strip().splitlines()[-1]) if started.returncode == 0 else {}
        differ = sorted(t for t in before if before[t] != after.get(t))
        check(started.returncode == 0 and before and not differ,
              f"1.8.4 starts on it and every table has the same rows ({differ}) {started.stderr[-300:]}")

        print("--- the lock: 1.8.4 does not start on a database of 1.9.0 ---")
        # The spec: a database of 1.9.0 (converted or new) is for 1.9.0 only - 1.8.x would quietly open the
        # restricted API keys and the readers' rights. The view playback_kos stops its start in its bin. On
        # PostgreSQL its start is one transaction and leaves nothing; on SQLite its tables script and
        # migrations commit at once and leave empty old tables and old columns, which the next start of
        # 1.9.0 drops. No row and no setting is written either way.
        converted: dict = {}
        for label, database in (("converted from 1.8.4", old_db), ("new", fresh_db)):
            shape19 = answer_of(child(PROJECT, START_19, database))
            converted = converted or shape19
            refused = child(v184, START_184, database)
            right_after = answer_of(child(PROJECT, READ_19, database))
            again = answer_of(child(PROJECT, START_19, database))
            check(bool(shape19.get("rows")) and "playback_kos" in shape19.get("views", []),
                  f"{label}: 1.9.0 starts on it, the view is there ({len(shape19.get('rows', {}))} tables)")
            cause = next((line for line in refused.stderr.splitlines() if "view" in line.lower()), "")
            check(refused.returncode != 0 and bool(cause),
                  f"{label}: the start of 1.8.4 ends with an error on the view ({refused.returncode}: {cause.strip()[:160]})")
            # On SQLite 1.8.4 adds its old columns to the tables (SELECT * reads them too): the counts are compared.
            changed_rows = sorted(t for t, n in shape19.get("rows", {}).items()
                                  if (right_after.get("rows", {}).get(t) or [None])[:1 if not config.is_postgres else 2]
                                  != n[:1 if not config.is_postgres else 2])
            check(bool(right_after) and not changed_rows and (not config.is_postgres or right_after == shape19),
                  f"{label}: 1.8.4 wrote no row and no setting{' and nothing at all' if config.is_postgres else ''} "
                  f"({changed_rows}, {differences(shape19, right_after)[:4]})")
            check(again == shape19, f"{label}: the next start of 1.9.0 cleans up and runs: the same rows and shape "
                                    f"({differences(shape19, again)[:6]})")
            check(again.get("note") == shape19.get("note") and (label == "new") == (shape19.get("note") is None),
                  f"{label}: the stripe still offers the backup made before the conversion (not one of the cleanup)"
                  f" ({shape19.get('note')} -> {again.get('note')})")

        print("--- a backup of 1.8.4 restored in 1.9.0 ---")
        restored19 = answer_of(child(PROJECT, RESTORE_19, restore_db, str(way_back), name))
        shape = restored19.get("shape", {})
        rows_of = {t: n[0] for t, n in shape.get("rows", {}).items()}
        check(restored19.get("status") == "ok" and "playback_kos" in shape.get("views", [])
              and not {"playback_kos", "zapomenuti", "mapa_dlazdice"} & set(shape.get("tables", {}))
              and rows_of.get("playback") == before.get("playback", [None])[0]
              and rows_of.get("forgettings") == before.get("zapomenuti", [None])[0]
              and rows_of.get("playback_bin") == before.get("playback_kos", [None])[0],
              f"restored and converted at the restart: the view again, the old names gone, every play there "
              f"({restored19.get('status')}, {rows_of.get('playback')} plays, {restored19.get('message')})")
        # The same database as the one converted in place: every row (a carriage return too) and, on PostgreSQL,
        # every unique and foreign key - the restore made the tables from the backup's own CREATE TABLE.
        apart = [d for d in differences(converted, shape) if not d.startswith("rows settings")]
        check(bool(converted) and not apart, f"and it is the database converted in place: rows, columns, indexes,"
                                             f" keys ({apart[:6]})")
    finally:
        if config.is_postgres:
            for name in (old_db, new_db, fresh_db, restore_db):
                admin_sql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
