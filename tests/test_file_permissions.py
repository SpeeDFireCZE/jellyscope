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
