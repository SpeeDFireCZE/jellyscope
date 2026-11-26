# -*- coding: utf-8 -*-
r"""Before every deletion a database backup is made - and without it nothing is deleted.

Two ways Jellyscope deletes on purpose: the nightly history purge
and forgetting a viewer by hand. Both start with a backup, so that a wrongly set
bound or a click on the wrong name have a way back.

What is verified here:

* forgetting a viewer first makes a backup - and it **contains** what was
  deleted right after (otherwise it would be useless),
* without a set folder the backup goes to `data/backups` next to the database (`data/zalohy` while it exists),
* when the backup fails, the viewer's history **stays** and the message says why,
* a viewer without history is not backed up (there is nothing to protect),
* the nightly purge backs up only when it has something to delete, and stops without a backup,
* the backup's name from the two ways differs, but cleaning up and restoring take it like
  any other (it starts with `jellyscope-`, ends with `.db`).

Run:
    .venv/bin/python tests/test_backup_before_delete.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "zaloha.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, purge, tasks, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword"})


def fill(user_id: str, name: str, count: int, when="2020-01-01") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, name, is_administrator,"
            " is_disabled, synced_at) VALUES (?,?,0,0,?)",
            (user_id, name, db.utcnow()))
        for index in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,0)",
                (f"{user_id}-{when}-{index}", user_id, name, f"i-{index}",
                 f"{when} 10:00:00", f"{when} 10:20:00", 1200))
        conn.commit()


def backups(folder: Path) -> list[Path]:
    return sorted(folder.glob("jellyscope-*.db")) if folder.exists() else []


def in_backup(file: Path, user_id: str) -> int:
    connection = sqlite3.connect(file)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM playback WHERE user_id = ?",
            (user_id,)).fetchone()[0]
    finally:
        connection.close()


def forget(user_id: str) -> str:
    response = client.post("/settings/history/forget",
                           data={"user_id": user_id}, follow_redirects=True)
    text = re.sub(r"<[^>]+>", " ", response.text)
    return re.sub(r"\s+", " ", text)


print("--- forgetting: a backup into data/backups when the folder is not set ---")
FALLBACK = Path(_tmp) / "data" / "backups"
fill("u-mirek", "Mirek", 120)
check(not backups(FALLBACK), "before the click there is no backup")
text = forget("u-mirek")
files = backups(FALLBACK)
check(len(files) == 1, f"after the click there is one backup in data/backups ({len(files)})")
check(files and files[0].name.endswith("-before-delete.db") and tasks.BACKUP_BEFORE_DELETE == "before-delete",
      f"and its name says why it was made, in English ({files[0].name if files else '-'})")
check(files and in_backup(files[0], "u-mirek") == 120,
      "the backup contains all 120 rows that were deleted right after")
check(purge.session_count("u-mirek") == 0, "and they are gone from the database")
# The file name is no longer in the message - it is in the list of the forgotten on the card,
# and only at the moment it is needed (after the bin is emptied). The message has what
# a person needs at once: that it can be taken back.
check("jde vrátit zpět" in text, "the message says the intervention can be taken back")
check(purge.forgettings()[0]["backup_file"] == files[0].name,
      "and the list of the forgotten knows the backup's file")

print()
hint = client.get("/settings?section=tasks").text
check("<code>data/backups</code>" in hint and "data/zalohy" not in hint,
      "Settings -> Tasks names the folder the backup before deleting goes to (data/backups)")

print("--- an installation from before 1.9.0 keeps its data/zalohy ---")
OLD_FALLBACK = Path(_tmp) / "data" / "zalohy"
OLD_FALLBACK.mkdir(parents=True)
check("<code>data/zalohy</code>" in client.get("/settings?section=tasks").text, "and the hint names data/zalohy then")
fill("u-olda", "Olda", 5)
forget("u-olda")
check(len(backups(OLD_FALLBACK)) == 1 and len(backups(FALLBACK)) == 1,
      "while data/zalohy exists the backup goes there, not into data/backups")
for leftover in backups(OLD_FALLBACK):
    leftover.unlink()
OLD_FALLBACK.rmdir()

print("--- forgetting: a set folder takes precedence ---")
OWN = Path(_tmp) / "moje-zalohy"
db.set_setting("backup_path", str(OWN))
db.forget_settings()
fill("u-jana", "Jana", 30)
forget("u-jana")
check(len(backups(OWN)) == 1, "the backup went into the set folder")
check(len(backups(FALLBACK)) == 1, "and not into the fallback one any more")

print()
print("--- without a backup nothing is deleted ---")
# A path that cannot be created: a file instead of a folder.
block = Path(_tmp) / "blok"
block.write_text("tady slozka byt nemuze", encoding="utf-8")
db.set_setting("backup_path", str(block / "zalohy"))
db.forget_settings()
fill("u-petr", "Petr", 40)
text = forget("u-petr")
check(purge.session_count("u-petr") == 40, "the viewer's history stayed whole")
check("Historie se nesmazala" in text, "and the message says nothing was deleted")
check("nepovedla záloha" in text, "…because the backup failed")
db.set_setting("backup_path", str(OWN))
db.forget_settings()
# Petr's old history would otherwise be left for the nightly purge below.
forget("u-petr")

print()
print("--- nothing is backed up for nothing ---")
before = len(backups(OWN))
text = forget("u-neexistuje")
check(len(backups(OWN)) == before, "a viewer without history = no backup")
check("Nebylo co zapomenout" in text, "and the message says so")

print()
print("--- the nightly purge ---")
db.set_setting("history_retention_enabled", "1")
db.set_setting(purge.RETENTION_KEY, str(purge.MIN_DAYS))
db.set_setting("task_purge_enabled", "1")
db.forget_settings()
# Only fresh records by themselves: there is nothing to purge, nothing is to be backed up.
fill("u-sam", "Sam", 10, when="2099-01-01")
before = len(backups(OWN))
result = asyncio.run(tasks._run_purge())
check(result["deleted"] == 0 and len(backups(OWN)) == before,
      "nothing old = nothing deleted and no backup")

fill("u-stary", "Stary", 25)                     # 2020 = to be purged
result = asyncio.run(tasks._run_purge())
check(result["deleted"] == 25, f"it purged 25 old ones ({result['deleted']})")
check(len(backups(OWN)) == before + 1, "and made a backup beforehand")
latest = max(backups(OWN), key=lambda p: p.stat().st_mtime)
check(in_backup(latest, "u-stary") == 25, "in which those 25 rows are")

db.set_setting("backup_path", str(block / "zalohy"))
db.forget_settings()
fill("u-stary2", "Stary2", 15)
result = asyncio.run(tasks._run_purge())
check(result["status"] == "error", "without a backup the purge ends with an error")
check(purge.session_count("u-stary2") == 15, "and deletes nothing")
check("Bez zálohy se nemaže" in str(result.get("message")),
      "with a message that says so")

print()
print("--- two backups in the same second do not overwrite each other ---")
db.set_setting("backup_path", str(OWN))
db.forget_settings()
fill("u-dvojce1", "Dvojce1", 5)
fill("u-dvojce2", "Dvojce2", 5)
before = len(backups(OWN))
forget("u-dvojce1")
forget("u-dvojce2")
check(len(backups(OWN)) == before + 2,
      f"two quick clicks = two backups ({len(backups(OWN)) - before})")

print()
print("--- cleaning up and restoring take a backup before deletion like any other ---")
db.set_setting("backup_path", str(OWN))
db.set_setting("backup_keep", "1")
db.forget_settings()
deleted = tasks._prune_backups(OWN)
check(len(backups(OWN)) == 1, f"the cleanup left one ({deleted} deleted)")
left = backups(OWN)[0]
check(left.name.startswith("jellyscope-") and left.name.endswith(".db"),
      f"the name fits the restore's pattern ({left.name})")

print()
print("--- backups before deletion have their own queue ---")
# A shared queue would mean that three forgettings a day push out the nightly
# backups - that is, exactly what the backing up is for.
import time  # noqa: E402

db.set_setting("backup_keep", "2")
db.forget_settings()
for index in range(4):
    (OWN / f"jellyscope-2026010{index}-000000.db").write_text("nocni", encoding="utf-8")
    (OWN / f"jellyscope-2026010{index}-000000-{tasks.BACKUP_BEFORE_DELETE}.db").write_text("pred mazanim", encoding="utf-8")
    time.sleep(0.02)
tasks._prune_backups(OWN)
nightly = [p for p in backups(OWN) if tasks.BACKUP_BEFORE_DELETE not in p.name]
before_delete = [p for p in backups(OWN) if tasks.BACKUP_BEFORE_DELETE in p.name]
check(len(nightly) == 2, f"two regular ones stayed ({len(nightly)})")
check(len(before_delete) == 2, f"and two before deletion too ({len(before_delete)})")
check(all("20260103" in p.name or "20260102" in p.name for p in nightly + before_delete),
      f"and they are the newest ones ({sorted(p.name for p in nightly + before_delete)})")

print()
print("--- the backups before deletion of 1.8.x (-pred-mazanim) are in the same queue ---")
for file in backups(OWN):
    file.unlink()
db.set_setting("backup_keep", "7")
db.forget_settings()
for index in range(8):
    (OWN / f"jellyscope-202602{index + 10}-000000.db").write_text("night", encoding="utf-8")
    time.sleep(0.02)
for index in range(3):
    (OWN / f"jellyscope-2026030{index}-000000-pred-mazanim.db").write_text("old before delete", encoding="utf-8")
    time.sleep(0.02)
    (OWN / f"jellyscope-2026030{index}-000001-before-delete.db").write_text("before delete", encoding="utf-8")
    time.sleep(0.02)
tasks._prune_backups(OWN)
names = sorted(p.name for p in backups(OWN))
night_left = [n for n in names if "delete" not in n and "mazanim" not in n]
check(len(night_left) == 7, f"the night ones keep their seven - the old backups before deletion push none out ({len(night_left)})")
check(len([n for n in names if "pred-mazanim" in n or "before-delete" in n]) == 6,
      f"and the six before deletion, old and new, share their own queue of seven ({names})")
listed = {b["name"] for b in tasks.list_backups()} if db.set_setting("backup_path", str(OWN)) is None else set()
check(any("pred-mazanim" in n for n in listed) and any("before-delete" in n for n in listed),
      "the list of backups shows both")

print()
print("--- a backup file that vanishes while the old ones are pruned ---")
# A file can vanish between listing the folder and reading its time (another cleanup, a person deleting it).
# Reading its time used to end the backup with an exception after the copy was written, and the task log
# stayed "running" for good. A link to nothing is such a file: the folder lists it, its time cannot be read.
from jellyscope import scanner  # noqa: E402

vanishing = OWN / "jellyscope-20250101-000000.db"
try:
    vanishing.symlink_to(OWN / "gone-meanwhile.db")
except OSError:
    vanishing = None
if vanishing is None:
    check(True, "(this system makes no links; skipped)")
else:
    import logging  # noqa: E402

    warnings: list[str] = []
    catcher = logging.Handler(logging.WARNING)
    catcher.emit = lambda record: warnings.append(record.getMessage())  # type: ignore[method-assign]
    logging.getLogger("jellyscope.tasks").addHandler(catcher)
    try:
        outcome = asyncio.run(tasks.backup_database())
    except Exception as error:  # noqa: BLE001 - the old behaviour, measured
        outcome = {"raised": repr(error)}
    finally:
        logging.getLogger("jellyscope.tasks").removeHandler(catcher)
    record = scanner.last_scan("backup") or {}
    check(outcome.get("status") == "ok", f"the backup is written and reported ({outcome})")
    check(record.get("status") == "done" and bool(record.get("finished_at")),
          f"and its task log is closed ({record.get('status')})")
    # Skipping the whole queue for one unreadable file left it unpruned for good (a link that stays, ESTALE on NFS):
    # keep=7 stopped holding, the disk filled, and the log said nothing.
    night = [p for p in backups(OWN) if p != vanishing and "delete" not in p.name and "mazanim" not in p.name]
    check(len(night) == 7, f"the rest of the queue is pruned to its seven all the same ({len(night)})")
    check(any(vanishing.name in line for line in warnings), f"the file that was skipped is in the log ({warnings})")
    listed = [b["name"] for b in tasks.list_backups()]
    check(len(listed) == 10 and vanishing.name not in listed,
          f"Settings lists the backups without the unreadable one ({len(listed)})")
    vanishing.unlink()
print()
print("--- the backup before a restore ---")
made = tasks._backup_before_restore(db.database_config())
check(made.name.endswith("-before-restore.db") and made.exists() and tasks.BACKUP_BEFORE_RESTORE == "before-restore",
      f"is named before-restore ({made.name})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
