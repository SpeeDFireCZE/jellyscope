# -*- coding: utf-8 -*-
r"""Restoring a SQLite backup while the application keeps working.

The collector, the scheduler's tasks and the web requests hold their own connections and do not stop for a
restore. The restore must still leave exactly the chosen backup behind, whole:

* a read that stays open across the restore (a page load, a long stats query) must not bring the old state
  back when it ends as the last connection,
* changes another connection left in the journal (WAL) must not be mixed into the restored file,
* a write that is under way when the restore starts is waited for, not cut into.

Run:
    .venv/bin/python tests/test_backup_restore.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "backup-restore.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "backup-restore-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


FOLDER = Path(_tmp) / "backups"


def fill(tag: str, count: int) -> None:
    with db.connect() as conn:
        for index in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at,"
                " last_seen_at, watched_seconds, paused_seconds, is_active) VALUES (?,?,?,?,?,?,?,?,0,0)",
                (f"{tag}:{index}", "u1", "User", f"item{index}", tag * 20,
                 "2026-01-01 00:00:00", "2026-01-01 00:10:00", 600))


def state(path: Path) -> tuple[str, int, set[str]]:
    """The integrity of a database file and what its history holds (rows, tags)."""
    conn = sqlite3.connect(path)
    try:
        integrity = "; ".join(r[0] for r in conn.execute("PRAGMA integrity_check").fetchall())[:120]
        try:
            rows = conn.execute("SELECT COUNT(*) FROM playback").fetchone()[0]
            tags = {r[0].split(":")[0] for r in conn.execute("SELECT session_key FROM playback")}
        except sqlite3.DatabaseError as error:
            rows, tags = -1, {f"unreadable: {error}"}
        return integrity, rows, tags
    finally:
        conn.close()


def make_backup() -> str:
    """A backup of the state now, by the regular task."""
    made = asyncio.run(tasks.backup_database())
    assert made["status"] == "ok", made
    return Path(made["file"]).name


def wal_connection() -> sqlite3.Connection:
    """A connection opened the way the application opens its own (db._open_raw)."""
    conn = sqlite3.connect(DB, timeout=15.0)
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def fresh_start(grow: bool = True) -> str:
    """Every case on a database of its own (a damaged one must not spoil the next): 3000 rows, backed up."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB}{suffix}").unlink(missing_ok=True)
    db.forget_settings()
    db.init_db()
    db.set_setting("backup_path", str(FOLDER))
    fill("BACKED-UP", 3000)
    chosen = make_backup()
    if grow:
        # Life goes on after the backup: the history grows.
        fill("AFTER", 3000)
    return chosen


CHOSEN = fresh_start(grow=False)
check(state(FOLDER / CHOSEN)[1] == 3000, "the chosen backup holds 3000 rows")

print("--- a read open across the restore ---")
# A page load is still reading while the application goes on writing as usual and the admin restores; the
# read ends as the last connection, and its old journal must not be checkpointed over the restored file.
reader = wal_connection()
cursor = reader.execute("SELECT * FROM playback")
cursor.fetchmany(10)
fill("AFTER", 3000)
result = tasks.restore_backup(CHOSEN)
try:
    cursor.fetchall()
    read_error = None
except sqlite3.DatabaseError as error:
    read_error = error
reader.close()
integrity, rows, tags = state(DB)
check(result["status"] == "ok", f"the restore reports success ({result.get('message', '')[:100]})")
check(read_error is None, f"the read open meanwhile ends on its own snapshot ({read_error})")
check(integrity == "ok" and rows == 3000 and tags == {"BACKED-UP"},
      f"the database is the chosen backup, whole ({integrity}, {rows} rows, {sorted(tags)})")

print()
print("--- changes another connection left in the journal ---")
# The database grew and was checkpointed (pages reorganised), then a running thread wrote frames into the
# journal that nothing checkpointed yet; it closes after the restore.
CHOSEN = fresh_start()
with sqlite3.connect(DB) as squash:
    squash.execute("PRAGMA wal_checkpoint(TRUNCATE)")
other = wal_connection()
other.execute("PRAGMA wal_autocheckpoint = 0")
other.execute("UPDATE playback SET item_name = COALESCE(item_name, '') || ' ' WHERE id % 3 = 0")
other.commit()
result = tasks.restore_backup(CHOSEN)
other.close()
integrity, rows, tags = state(DB)
check(result["status"] == "ok", f"the restore reports success ({result.get('message', '')[:100]})")
check(integrity == "ok" and rows == 3000 and tags == {"BACKED-UP"},
      f"no page of the old state is mixed in ({integrity}, {rows} rows, {sorted(tags)})")

print()
print("--- a write under way when the restore starts ---")
# A sync, an import or the collector is in the middle of its transaction. The restore waits for it to end
# and then puts the backup back - a copy cut into it would be a damaged file.
CHOSEN = fresh_start()
started = threading.Event()


def running_task() -> None:
    conn = wal_connection()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE playback SET item_name = 'WRITTEN-BY-A-RUNNING-TASK' WHERE id % 3 = 0")
    started.set()
    time.sleep(1.0)
    conn.commit()
    conn.close()


worker = threading.Thread(target=running_task)
worker.start()
started.wait()
result = tasks.restore_backup(CHOSEN)
worker.join()
integrity, rows, tags = state(DB)
check(result["status"] == "ok", f"the restore reports success ({result.get('message', '')[:100]})")
check(integrity == "ok" and rows == 3000 and tags == {"BACKED-UP"},
      f"the restored database is whole and holds the backup ({integrity}, {rows} rows, {sorted(tags)})")
with db.connect() as conn:
    renamed = conn.execute("SELECT COUNT(*) AS n FROM playback WHERE item_name = 'WRITTEN-BY-A-RUNNING-TASK'").fetchone()
check(int(dict(renamed)["n"]) == 0, "the write that ended before the restore is not in it")

print()
print("--- a backup folder with # or ? in its path ---")
# The restore opens the backup read-only by a file: URI. A folder named like a shared one ("backups #1",
# "what?") must not cut the path short (an error saying it is not a backup of Jellyscope), and reading
# the backup must leave no journal files beside it, which no cleanup would ever remove.
for name in ("backups #1", "what?", "50% off"):
    fresh_start(grow=False)
    folder = Path(_tmp) / name
    db.set_setting("backup_path", str(folder))
    chosen = make_backup()
    fill("AFTER", 100)
    result = tasks.restore_backup(chosen)
    left = sorted(p.name for p in folder.iterdir() if not p.name.endswith(".db"))
    check(result["status"] == "ok" and state(DB)[1] == 3000,
          f"{name!r}: the restore goes through ({result.get('message', '')[:90]})")
    check(not left, f"{name!r}: nothing but backups is left in the folder ({left})")

print()
print("--- a backup that fails half-way leaves no file behind ---")
# The disk fills during the copy (or PostgreSQL goes away in the middle of the own export). A file left
# with a backup's name would be listed in Settings as a backup, offered for a restore, and counted by the
# pruning - a week of such nights and the first good backup deletes every older good one.
fresh_start(grow=False)
real_backup_sqlite = tasks._backup_sqlite


def failing_copy(config, destination: Path) -> int:
    destination.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)
    raise OSError("disk I/O error")


def backup_files() -> list[str]:
    return sorted(p.name for p in FOLDER.iterdir())


before = backup_files()
tasks._backup_sqlite = failing_copy
try:
    failed = asyncio.run(tasks.backup_database())
    refused = tasks.restore_backup(CHOSEN)
finally:
    tasks._backup_sqlite = real_backup_sqlite
check(failed["status"] == "error" and backup_files() == before,
      f"the failed backup is reported, and nothing new is in the folder ({sorted(set(backup_files()) - set(before))})")
check(refused["status"] == "error" and backup_files() == before and state(DB)[1] == 3000,
      f"a failed backup before a restore leaves no file either, and nothing is restored ({refused.get('message', '')[:70]})")
made = asyncio.run(tasks.backup_database())
check(made["status"] == "ok" and Path(made["file"]).is_file() and Path(made["file"]).suffix == ".db"
      and not [n for n in backup_files() if n.endswith(".partial")],
      f"a backup that goes through gets its name, with no temporary file left ({Path(made['file']).name})")

print()
print("--- a backup cut off by the end of the process is cleaned at the next start ---")
# A restart through execv (an update, a restore), a stopped container or a crash in the middle of a backup gets no
# exception, so the .partial file stayed - 5 GB a time, which neither Settings nor the pruning ever sees.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

fallback = tasks.fallback_backup_folder()
fallback.mkdir(parents=True, exist_ok=True)
cut_off = [FOLDER / "jellyscope-20261125-033000.db.partial", FOLDER / "jellyscope-20261125-033000-2.sql.partial",
           fallback / "jellyscope-20261125-040000-before-delete.db.partial"]
kept = [FOLDER / CHOSEN, FOLDER / "notes.partial", fallback / "jellyscope-20261125-040000-before-delete.db"]
for path in cut_off + kept[1:]:
    path.write_bytes(b"SQLite format 3\x00")
with TestClient(web.app):
    pass
check(not [p.name for p in cut_off if p.exists()], f"the half-written backups are gone ({[p.name for p in cut_off if p.exists()]})")
check(all(p.exists() for p in kept), f"whole backups and other files stay ({[p.name for p in kept if not p.exists()]})")
for path in kept[1:]:
    path.unlink()

print()
print("--- an own PostgreSQL export that ends half-way is not restored ---")
# An export cut off (an older version wrote straight into the final name) has the header and BEGIN but no
# COMMIT and only part of the tables. Replaying it would drop every table and commit what is there.
partial = FOLDER / "jellyscope-20261124-040000.sql"
partial.write_text(f"{tasks.OWN_BACKUP_MARKS[0]} (own export)\nSET standard_conforming_strings = on;\nBEGIN;\n\n"
                   "INSERT INTO settings (key, value) VALUES ('ui_language', 'cs') ON CONFLICT DO NOTHING;\n",
                   encoding="utf-8")
opened: list[bool] = []
real_connect = db.connect


def watched_connect(*args, **kwargs):
    opened.append(True)
    return real_connect(*args, **kwargs)


db.connect = watched_connect
try:
    tasks._restore_postgres(db.database_config(), partial)
    outcome: Exception | None = None
except Exception as error:  # noqa: BLE001
    outcome = error
finally:
    db.connect = real_connect
check(isinstance(outcome, tasks.BackupError) and not opened,
      f"refused before the database is touched ({type(outcome).__name__}: {outcome}, connections: {len(opened)})")
partial.unlink()
# And the other way: a guard that refused every export would pass the check above. A whole own export - written by
# _dump_own itself, as one made on Windows (CRLF) and as one of 1.8.x (the Czech header, CRLF) - goes past the guard
# up to the database (the database itself is not touched here: the test runs on SQLite).
whole = FOLDER / "jellyscope-20261124-050000.sql"
tasks._dump_own(whole)
exported = whole.read_text(encoding="utf-8")


class ReachedTheDatabase(Exception):
    """The guard let the export through: the restore asks for a connection."""


def reached(*args, **kwargs):
    raise ReachedTheDatabase()


for label, text in (("an own export", exported), ("an export made on Windows (CRLF)", exported.replace("\n", "\r\n")),
                    ("an export of 1.8.x (its header, CRLF)",
                     exported.replace(tasks.OWN_BACKUP_MARKS[0], tasks.OWN_BACKUP_MARKS[1], 1).replace("\n", "\r\n"))):
    whole.write_bytes(text.encode("utf-8"))
    db.connect = reached
    try:
        tasks._restore_postgres(db.database_config(), whole)
        outcome = None
    except Exception as error:  # noqa: BLE001
        outcome = error
    finally:
        db.connect = real_connect
    check(text.count("INSERT INTO") > 3 and isinstance(outcome, ReachedTheDatabase),
          f"{label}: the guard lets it through to the database ({type(outcome).__name__}: {outcome})")
whole.unlink()

print()
print("--- a restore waits for no running task: it is refused ---")
# A sync whose batch waited for the lock during the restore wrote into the restored database after it, and its
# _mark_missing marked nearly the whole restored library as vanished. The other task buttons refuse to start
# while a task runs; the restore does the same.
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402

from jellyscope import accounts, scanner  # noqa: E402
from jellyscope.web import settings as web_settings  # noqa: E402

CHOSEN = fresh_start()
accounts.create("admin", "longpassword", is_admin=True)
admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword"})
restarts: list[bool] = []
restore_restart = substitute(web_settings, "_schedule_restart", lambda: restarts.append(True))
asyncio.run(scanner.task_lock().acquire())
try:
    answer = admin.post("/settings/backup/restore", data={"name": CHOSEN})
finally:
    scanner.task_lock().release()
integrity, rows, tags = state(DB)
check(rows == 6000 and tags == {"BACKED-UP", "AFTER"} and not restarts,
      f"nothing is restored while a task runs, nothing restarts ({rows} rows, {sorted(tags)}, restarts: {len(restarts)})")
check("Another task is already running, wait for it to finish." in answer.text,
      "and the page says why, as the other task buttons do")

# An import, a backup and the clean-up take no task lock (a sync may run beside them), yet a restore under one of
# them let its writes, waiting for the database, land in the restored one. Each job is held in its middle while
# the restore is asked for; the jobs then end with an error, and the restore at the end must still go through.
from jellyscope import importers, purge  # noqa: E402


def refused_during(label: str, job: Any) -> None:
    started, go = threading.Event(), threading.Event()

    def held(*args: object, **kwargs: object) -> None:
        started.set()
        go.wait(10)
        raise RuntimeError("the job ends badly")

    async def held_async(*args: object, **kwargs: object) -> None:
        await asyncio.to_thread(held)

    def run() -> None:
        try:
            job(held, held_async)
        except Exception:  # noqa: BLE001 - the job is meant to fail; only the restore beside it is checked
            pass

    thread = threading.Thread(target=run)
    thread.start()
    started.wait(10)
    try:
        answer = admin.post("/settings/backup/restore", data={"name": CHOSEN})
    finally:
        go.set()
        thread.join(30)
    integrity, rows, tags = state(DB)
    check(started.is_set() and rows == 6000 and tags == {"BACKED-UP", "AFTER"} and not restarts
          and "Another task is already running, wait for it to finish." in answer.text,
          f"{label}: the restore is refused while it runs ({rows} rows, restarts: {len(restarts)})")


def signed_in_admin() -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": "admin", "password": "longpassword"})
    return client


def jellystat_import(held: Any, held_async: Any) -> None:
    put_back = substitute(importers, "import_jellystat_json", held_async)
    try:
        signed_in_admin().post("/settings/import/jellystat", data={"min_seconds": "60"},
                               files={"backup": ("jellystat.json", b'{"jf_playback_activity": []}',
                                                 "application/json")})
    finally:
        put_back()


def playback_reporting_import(held: Any, held_async: Any) -> None:
    put_back = substitute(importers, "import_playback_reporting", held_async)
    try:
        signed_in_admin().post("/settings/import/playback-reporting", data={"min_seconds": "60"})
    finally:
        put_back()


def backup(held: Any, held_async: Any) -> None:
    real = tasks._write_whole
    tasks._write_whole = held
    try:
        asyncio.run(tasks.backup_database())
    finally:
        tasks._write_whole = real


def clean_up(held: Any, held_async: Any) -> None:
    real = purge.enabled, purge.overview, purge.delete_old
    purge.enabled, purge.overview, purge.delete_old = (lambda: True), (lambda *a, **k: {"leaving": 0}), held
    try:
        asyncio.run(tasks._run_purge())
    finally:
        purge.enabled, purge.overview, purge.delete_old = real


for label, job in (("a Jellystat import", jellystat_import), ("a Playback Reporting import", playback_reporting_import),
                   ("a backup", backup), ("the clean-up", clean_up)):
    refused_during(label, job)

lock_during_restore: list[bool] = []
real_restore = tasks.restore_backup


def watched_restore(name: str) -> dict[str, Any]:
    # A task the scheduler starts during a long restore would write into the restored database until the restart.
    lock_during_restore.append(scanner.is_scan_running())
    return real_restore(name)


tasks.restore_backup = watched_restore
try:
    admin.post("/settings/backup/restore", data={"name": CHOSEN}, follow_redirects=False)
finally:
    tasks.restore_backup = real_restore
    restore_restart()
integrity, rows, tags = state(DB)
check(rows == 3000 and tags == {"BACKED-UP"} and restarts == [True],
      f"with the task over the same button restores and restarts ({rows} rows, restarts: {len(restarts)})")
check(lock_during_restore == [True] and not scanner.is_scan_running(),
      f"the restore holds the task lock while it runs, and gives it back ({lock_during_restore})")

print()
print("--- after a restore everybody signs in again, and nothing fails before the restart ---")
# The backup's session records came back with it: a copy of a cookie signed out after the backup was made opened
# the account again. And a backup from before the sessions had no table for them: until the restart every request
# with a session cookie and every sign-in ended with 500 - the waiting page after the restore too.
COOKIE = "jellyscope_session"
fresh_start(grow=False)
accounts.create("admin", "longpassword", is_admin=True)
signed_in = TestClient(web.app)
signed_in.post("/login", data={"username": "admin", "password": "longpassword"})
copy = TestClient(web.app)
copy.cookies.set(COOKIE, signed_in.cookies.get(COOKIE) or "")
with_session = make_backup()
signed_in.post("/logout", follow_redirects=False)
check(copy.get("/account", follow_redirects=False).status_code == 303, "signed out after the backup: the copy is refused")
result = tasks.restore_backup(with_session)
check(result["status"] == "ok" and copy.get("/account", follow_redirects=False).status_code == 303,
      f"the backup restored: the copy signed out since still opens nothing ({result.get('status')})")
check(not db.query_all("SELECT token_hash FROM web_sessions"), "no session of the backup is left")

older = FOLDER / "jellyscope-20261120-030000.db"
older.write_bytes((FOLDER / with_session).read_bytes())
stripped = sqlite3.connect(older)
stripped.execute("DROP TABLE web_sessions")
stripped.commit()
stripped.close()
signed_in = TestClient(web.app)
signed_in.post("/login", data={"username": "admin", "password": "longpassword"})
result = tasks.restore_backup(older.name)


def answers(client: TestClient, method: str, path: str, **kwargs: object) -> int:
    try:
        return client.request(method, path, follow_redirects=False, **kwargs).status_code
    except Exception:  # noqa: BLE001 - the test client raises what the server answers with 500
        return 500


waiting = answers(signed_in, "GET", "/settings?section=tasks&wait=restart")
sign_in = answers(TestClient(web.app), "POST", "/login", data={"username": "admin", "password": "longpassword"})
check(result["status"] == "ok" and waiting == 303 and sign_in == 303,
      f"a backup from before the sessions: the page after the restore asks to sign in, signing in works "
      f"({result.get('status')}, {waiting}, {sign_in})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
