# -*- coding: utf-8 -*-
r"""What is deleted vanishes from the file too - not only from the application's view.

`DELETE` in SQLite only drops a row from the list on the page. Its bytes stay
lying until something overwrites them, so "deleted" history can still be read
from the file. Once that saved data deleted by mistake - and it is at the same
time the reason why "deleted" could not be relied on.

So after the interventions that delete on purpose, the database is repacked
(`db.compact()`). The test does not ask the application whether the data are
there - it would say "they are not" in both cases. It asks **the file**: it
looks for the viewer's name in its bytes.

Run:
    .venv/bin/python tests/test_deleted_is_gone.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
FILE = str(Path(_tmp) / "stopa.db")
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = FILE
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, purge  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def traces(mark: str) -> int:
    """How many times the text is in the database files - the WAL journal included."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = Path(FILE + suffix)
        if path.exists():
            total += path.read_bytes().count(mark.encode())
    return total


def fill(user_id: str, mark: str, count: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (user_id, mark, db.utcnow()))
        for index in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,0)",
                (f"{user_id}-{index}", user_id, mark, f"i-{index}",
                 "2020-01-01 10:00:00", "2020-01-01 10:20:00", 1200))
        conn.commit()


db.init_db()

# An unusual name on purpose - it cannot appear in the file by chance.
MARK = "MirekNeobvykleJmeno"
fill("u-mirek", MARK, 200)

print("--- forgetting a viewer ---")
before = traces(MARK)
check(before > 100, f"before deleting the name is in the file {before}x")

result = purge.forget_user("u-mirek")
check(result["deleted"] == 200, f"200 records were deleted ({result['deleted']})")

after = traces(MARK)
# One trace stays: the name in the `users` table. The account is in Jellyfin,
# it is not deleted from here - see forget_user().
check(after <= 1, f"after forgetting {after} traces remained in the file (the account in users)")
check(db.query_value("SELECT COUNT(*) FROM users WHERE id = ?",
                     ("u-mirek",)) == 1, "and the account stayed in the list")

print()
print("--- clearing out by age ---")
OLD = "JanaNeobvykleJmeno"
fill("u-jana", OLD, 150)
check(traces(OLD) > 100, "before deleting the name is in the file")

# The records are from 2020, so even the shortest retention clears them out.
cleared = purge.delete_old(purge.MIN_DAYS)
check(cleared["deleted"] == 150, f"150 records were deleted ({cleared['deleted']})")
left = traces(OLD)
check(left <= 1, f"and {left} traces of them remained in the file")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
