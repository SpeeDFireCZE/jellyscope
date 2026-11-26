# -*- coding: utf-8 -*-
r"""What is sensitive should be readable by the owner only.

Three files on the disk carry something that not just anybody who gets onto
the machine should read:

* `data/secret_key` - whoever knows it signs a cookie as an administrator,
* `data/database.json` - with PostgreSQL it holds **the database password**,
  and readably; otherwise the application could not sign in with it,
* backups - the whole database, that is the accounts' password fingerprints
  and the watching history too.

The first one was guarded from the start, the other two were not. CodeQL
found it (`py/clear-text-storage-sensitive-data`) and was right: the care was
in one place and missing a bit further on.

On Windows `chmod` sets no rights - so the test watches that the application
**asks** for them, and on Linux it also checks how the file really ended up.
Live use runs on Linux, development was on Windows; the test must hold on
both, otherwise someone turns it off.

Run:
    .venv/bin/python tests/test_file_permissions.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "data" / "prava.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, dialect, tasks  # noqa: E402

failures = 0
POSIX = os.name != "nt"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# On Windows chmod sets nothing, so the rights alone would not prove the test.
# So we watch the calls - and on Linux we then ask the file itself too.
calls: list[tuple[str, int]] = []
original_chmod = Path.chmod


def watched_chmod(self, mode, *a, **kw):  # noqa: ANN001
    calls.append((self.name, mode))
    return original_chmod(self, mode, *a, **kw)


Path.chmod = watched_chmod  # type: ignore[method-assign]

db.init_db()

print("--- database.json: it holds the database password ---")
config = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="127.0.0.1", port=5432,
                                database="jellyscope", user="js",
                                password="tajne-heslo")
dialect.save_config(Path(_tmp), config)
file = dialect.config_path(Path(_tmp))
check(file.is_file(), "the file came about")
check("tajne-heslo" in file.read_text(encoding="utf-8"),
      "the password really is in it (hence the rights)")
check(("database.json", 0o600) in calls, "the application asked for the rights 600")
if POSIX:
    check(oct(file.stat().st_mode)[-3:] == "600",
          f"and the file has them ({oct(file.stat().st_mode)[-3:]})")

print()
print("--- a backup: the whole database in one file ---")
backups = Path(_tmp) / "backups"
db.set_setting("backup_path", str(backups))
calls.clear()
result = asyncio.run(tasks._run_backup())
check(result.get("status") == "ok", f"the backup ran ({result.get('message', '')})")

if result.get("status") == "ok":
    backup = Path(result["file"])
    check(backup.is_file(), f"the file is on the disk ({backup.name})")
    check(any(name == backup.name and mode == 0o600 for name, mode in calls),
          "the application asked for the rights 600")
    if POSIX:
        check(oct(backup.stat().st_mode)[-3:] == "600",
              f"and the file has them ({oct(backup.stat().st_mode)[-3:]})")

print()
print("--- a backup is private while it is being written, the one before a restore too ---")
# With the usual umask 022 a backup was readable to everyone while it was copied (chmod came after it), and the
# backup made before a restore stayed so for ever (it had no chmod at all).
os.umask(0o022)
modes_while_copying: list[str] = []
original_connect = tasks.sqlite3.connect


def watched_connect(target, *a, **kw):  # noqa: ANN001
    """The rights of a backup's file at the moment SQLite opens it to copy into it."""
    if Path(str(target)).name.startswith("jellyscope-"):
        modes_while_copying.append(oct(Path(target).stat().st_mode)[-3:] if Path(target).exists() else "none")
    return original_connect(target, *a, **kw)


tasks.sqlite3.connect = watched_connect
try:
    result = asyncio.run(tasks._run_backup())
    safety = tasks._backup_before_restore(db.database_config())
finally:
    tasks.sqlite3.connect = original_connect
if POSIX:
    check(modes_while_copying[:1] == ["600"],
          f"a backup is born with the rights 600, before the copy starts ({modes_while_copying[:1]})")
    check(oct(safety.stat().st_mode)[-3:] == "600",
          f"the backup before a restore has the rights 600 ({oct(safety.stat().st_mode)[-3:]})")
    check(modes_while_copying[1:2] == ["600"],
          f"and it is born with them too ({modes_while_copying[1:2]})")

print()
print("--- the database and the log are born private ---")
# The database (the API keys and passwords of the apps readable, the accounts' password hashes) and the log were
# created with the default umask, so 644. The launchers set the umask for everything the app creates.
if POSIX:
    import subprocess  # noqa: E402

    for launcher, code in (
            ("run.py", "import run, uvicorn; uvicorn.run = lambda *a, **k: None; run.main()"),
            ("manage.py", "import manage; manage.main(['accounts'])")):
        home = Path(tempfile.mkdtemp())
        environment = {**os.environ, "JELLYSCOPE_HOME": str(home), "DATABASE_PATH": str(home / "data" / "new.db"),
                       "SECRET_KEY": "x" * 40, "JELLYFIN_URL": "http://127.0.0.1:1", "JELLYFIN_API_KEY": "test-key",
                       "JELLYSCOPE_DEMO": "0"}
        finished = subprocess.run([sys.executable, "-c", f"import os; os.umask(0o022); {code}"], cwd=PROJECT,
                                  env=environment, capture_output=True, text=True, timeout=120)
        created = [home / "data" / "new.db"] + ([home / "data" / "logs" / "jellyscope.log"] if launcher == "run.py"
                                                else [])
        modes = {p.name: oct(p.stat().st_mode)[-3:] if p.exists() else "none" for p in created}
        check(finished.returncode == 0 and set(modes.values()) == {"600"},
              f"{launcher}: what it creates is readable by its owner only ({modes}, {finished.stderr[-200:]!r})")

print()
print("--- what an older installation left readable is narrowed at the start, its content untouched ---")
# The umask protects only what is created from now on: an upgraded installation kept its database (with -wal and
# -shm), logs and backups readable to every account on the machine (644). The start narrows them - by chmod only.
if POSIX:
    import hashlib  # noqa: E402
    import logging  # noqa: E402
    import sqlite3  # noqa: E402

    from fastapi.testclient import TestClient  # noqa: E402

    from jellyscope import applog, web  # noqa: E402

    data = Path(_tmp) / "data"
    database = Path(os.environ["DATABASE_PATH"])
    dialect.save_config(Path(_tmp), dialect.DatabaseConfig(kind=dialect.SQLITE, path=str(database)))
    holder = sqlite3.connect(database)          # keeps -wal and -shm on the disk, as a running app has them
    holder.execute("PRAGMA journal_mode = WAL")
    holder.execute("UPDATE settings SET value = value WHERE key = 'backup_path'")
    holder.commit()
    (data / "secret_key").write_text("k" * 64, encoding="utf-8")
    applog.log_dir().mkdir(parents=True, exist_ok=True)
    (applog.log_dir() / "jellyscope.log").write_text("a line\n", encoding="utf-8")
    (applog.log_dir() / "jellyscope.log.1").write_text("an older line\n", encoding="utf-8")
    update_backups = data / "before-update"
    update_backups.mkdir(exist_ok=True)
    (update_backups / "jellyscope-20261101-000000.db").write_bytes(b"a copy before an update")
    elsewhere = Path(tempfile.mkdtemp()) / "elsewhere.log"
    elsewhere.write_text("not the app's", encoding="utf-8")
    (applog.log_dir() / "linked.log").symlink_to(elsewhere)
    files = [database, Path(f"{database}-wal"), Path(f"{database}-shm"), data / "secret_key",
             dialect.config_path(Path(_tmp)), applog.log_dir() / "jellyscope.log", applog.log_dir() / "jellyscope.log.1",
             update_backups / "jellyscope-20261101-000000.db", *sorted(backups.glob("jellyscope-*.db"))]
    folders = [data, applog.log_dir(), update_backups]

    def mode(path: Path) -> str:
        return oct(path.stat().st_mode)[-3:]

    def widen() -> None:
        for path in files + [elsewhere]:
            if path.exists():                   # -wal and -shm go with the last connection
                os.chmod(path, 0o644)
        for path in folders + [backups]:
            os.chmod(path, 0o755)

    def fingerprints() -> dict[str, str]:
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

    db.get_setting("backup_path")               # settings in the cache: the narrowing opens no connection
    widen()
    before = fingerprints()
    tasks.narrow_private_files()
    check({p.name: mode(p) for p in files} == {p.name: "600" for p in files},
          f"the database, its -wal and -shm, the key, database.json, the logs and the backups: 600 "
          f"({ {p.name: mode(p) for p in files if mode(p) != '600'} })")
    check({p.name: mode(p) for p in folders} == {p.name: "700" for p in folders},
          f"the data folder, the logs' and the update backups' folders: 700 ({[mode(p) for p in folders]})")
    check(fingerprints() == before, "not one byte of them changed")
    check(mode(elsewhere) == "644", "a link out of the data folder is not followed")
    check(mode(backups) == "755", "a backup folder of the administrator's own outside data keeps its rights")

    real_geteuid = os.geteuid
    widen()
    os.geteuid = lambda: real_geteuid() + 1     # type: ignore[assignment] - every file is somebody else's now
    try:
        tasks.narrow_private_files()
    finally:
        os.geteuid = real_geteuid               # type: ignore[assignment]
    check(mode(database) == "644" and mode(data) == "755", "a file the process does not own is left as it is")

    warnings: list[str] = []
    catcher = logging.Handler(logging.WARNING)
    catcher.emit = lambda record: warnings.append(record.getMessage())  # type: ignore[method-assign]
    logging.getLogger("jellyscope.tasks").addHandler(catcher)
    narrowing = Path.chmod

    def refusing(self, mode_bits, *a, **kw):  # noqa: ANN001
        if self.name == "secret_key":
            raise PermissionError("Operation not permitted")
        return narrowing(self, mode_bits, *a, **kw)

    Path.chmod = refusing  # type: ignore[method-assign]
    (applog.log_dir() / "jellyscope.log.1").unlink()
    files.remove(applog.log_dir() / "jellyscope.log.1")
    try:
        with TestClient(web.app):
            started = True
    except Exception as error:  # noqa: BLE001 - the old behaviour would be a start that fails
        started, warnings = False, warnings + [repr(error)]
    finally:
        Path.chmod = narrowing  # type: ignore[method-assign]
        logging.getLogger("jellyscope.tasks").removeHandler(catcher)
    check(started and mode(database) == "600" and mode(data) == "700",
          f"the start of the app narrows them, a missing file stops nothing ({mode(database)}, {mode(data)})")
    check(mode(data / "secret_key") == "644" and any("secret_key" in line for line in warnings),
          f"a chmod that fails is logged and skipped ({warnings})")
    holder.close()

    print()
    print("--- a folder in data/ that is a link out of it is not followed ---")
    # Only a path that is itself a link was checked: the files in data/before-update (or data/logs) linked to a
    # folder elsewhere got 600 there, and a log collector of another account could not read them any more.
    outside = Path(tempfile.mkdtemp())
    os.chmod(outside, 0o755)
    foreign_copy = outside / "jellyscope-20261102-000000.db"
    foreign_copy.write_bytes(b"a copy kept elsewhere")
    os.chmod(foreign_copy, 0o644)
    kept_aside = data / "before-update.kept"
    update_backups.rename(kept_aside)
    update_backups.symlink_to(outside, target_is_directory=True)
    try:
        tasks.narrow_private_files()
    finally:
        update_backups.unlink()
        kept_aside.rename(update_backups)
    check(mode(foreign_copy) == "644" and mode(outside) == "755",
          f"what lies behind the link keeps its rights ({mode(foreign_copy)}, {mode(outside)})")
    check(mode(backups) == "755" and {mode(p) for p in backups.glob("jellyscope-*.db")} == {"600"},
          "the backup folder set in Settings stays the exception: its backups are narrowed outside data/ too")

    print()
    print("--- a file system that ignores the rights gets a warning, not a success ---")
    # CIFS without unix extensions, drvfs: chmod succeeds and changes nothing. Every start logged that N files were
    # now their owner's only, while `ls -l` showed 777 on.
    widen()
    ignored: list[str] = []
    ignoring = logging.Handler(logging.WARNING)
    ignoring.emit = lambda record: ignored.append(record.getMessage())  # type: ignore[method-assign]
    logging.getLogger("jellyscope.tasks").addHandler(ignoring)
    before_ignoring = Path.chmod
    Path.chmod = lambda self, mode_bits, *a, **kw: None  # type: ignore[method-assign,assignment]
    try:
        counted = tasks.narrow_private_files()
    finally:
        Path.chmod = before_ignoring  # type: ignore[method-assign]
        logging.getLogger("jellyscope.tasks").removeHandler(ignoring)
    check(counted == 0, f"nothing is counted as narrowed when the rights stayed ({counted})")
    check(len(ignored) == 1 and "ignores" in ignored[0], f"one warning that the file system ignores them ({ignored})")
    check(mode(database) == "644", "and the rights really stayed")


print()
print("--- a backup folder that does not answer does not stop the start ---")
# The start deleted half-written backups and narrowed the rights on the event loop, before the port opened: a backup
# folder on a network share that does not answer (a NAS asleep, a hard NFS mount) kept the app from starting at all.
import logging  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402
from jellyscope.web import start as web_start  # noqa: E402

answer = threading.Event()
work_done: list[str] = []
real_remove, real_narrow = tasks.remove_partial_backups, tasks.narrow_private_files
real_limit = getattr(web_start, "FILE_WORK_SECONDS", None)


def stalled_remove() -> int:
    answer.wait(6)                              # the share answers only when the test lets it
    work_done.append("removed")
    return 0


def counted_narrow() -> int:
    work_done.append("narrowed")
    return 0


start_warnings: list[str] = []
start_catcher = logging.Handler(logging.WARNING)
start_catcher.emit = lambda record: start_warnings.append(record.getMessage())  # type: ignore[method-assign]
logging.getLogger("jellyscope.web").addHandler(start_catcher)
tasks.remove_partial_backups, tasks.narrow_private_files = stalled_remove, counted_narrow  # type: ignore[assignment]
web_start.FILE_WORK_SECONDS = 0.5               # type: ignore[attr-defined]
try:
    began = time.monotonic()
    with TestClient(web.app) as client:
        took = time.monotonic() - began
        answered = client.get("/health").status_code
        answer.set()
        for _ in range(50):
            if "narrowed" in work_done:
                break
            time.sleep(0.05)
finally:
    answer.set()
    tasks.remove_partial_backups, tasks.narrow_private_files = real_remove, real_narrow  # type: ignore[assignment]
    if real_limit is not None:
        web_start.FILE_WORK_SECONDS = real_limit  # type: ignore[attr-defined]
    logging.getLogger("jellyscope.web").removeHandler(start_catcher)
check(took < 3 and answered == 200, f"the app starts and answers past the time limit ({took:.1f} s, {answered})")
check(any("did not answer" in line for line in start_warnings), f"with one warning in the log ({start_warnings})")
check(work_done == ["removed", "narrowed"], f"the work goes on in its thread and finishes when the share answers ({work_done})")

print()
print("--- on Windows it must not fail ---")
# `chmod` sets no rights there and for some files it can even raise an error.
# Were it not caught, the database settings would not be stored at all.
def exploding_chmod(self, mode, *a, **kw):  # noqa: ANN001
    raise OSError("chmod tu není")


Path.chmod = exploding_chmod  # type: ignore[method-assign]
try:
    dialect.save_config(Path(_tmp), config)
    check(True, "storing survives a chmod that fails")
except OSError:
    check(False, "storing survives a chmod that fails")
finally:
    Path.chmod = original_chmod  # type: ignore[method-assign]

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
