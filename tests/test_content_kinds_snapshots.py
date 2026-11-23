# -*- coding: utf-8 -*-
r"""Daily snapshots carry the whole library and its parts by content kind.

Spec: docs/superpowers/specs/2026-11-16-content-kinds-size-design.md, part 5.
`size` of the library snapshot and `library` of a disk snapshot are the
whole (video and music) from now on; `kinds` holds the parts by kind as JSON.
NULL `kinds` means a snapshot written before - video only (completed when
read, part 6); every kind is written, a disk without music has {"music": 0}.

Run:
    .venv/bin/python tests/test_content_kinds_snapshots.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "content-kinds-snapshots.db")
os.environ["SECRET_KEY"] = "test-key-content-kinds-snapshots"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"

from jellyscope import content_kinds, db, demodata, scanner, stats  # noqa: E402

failures = 0
GB = 1024 ** 3
db.init_db()
demodata.seed()
# These tests are about music (and video): the demo's books are taken out and a kind without files shows
# up as measured zero, like music on a disk without music (the books have checks in test_content_kinds.py).
with db.connect() as _conn:
    _conn.execute("DELETE FROM book")
    _conn.commit()
# The first books sync ran and found none: books are measured, 0 (before it they are left out, see the end).
db.set_setting("books_first_sync", "1")
db.forget_settings()
NO_BOOKS = {"books": 0}


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def folder(path: str, device: str) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": 500 * GB, "UsedSpace": 1500 * GB, "StorageType": "Fixed", "DeviceId": device}


def latest(table: str, **where: str) -> list[dict[str, Any]]:
    extra = "".join(f" AND {column} = ?" for column in where)
    return db.query_all(f"SELECT * FROM {table} WHERE day = (SELECT MAX(day) FROM {table}){extra}", tuple(where.values()))


video = db.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(size_bytes, 0)), 0) AS b FROM items WHERE is_missing = 0")
music = content_kinds.totals()["music"]
check(music["size"] > 0 and int(video["b"]) > 0, "demo data have video and music")

print("--- Jellyfin 10.x: the Library row ---")
scanner.write_disk_snapshot()
row = latest("library_disk_snapshot", disk="library")
check(len(row) == 1 and row[0]["library"] == int(video["b"]) + music["size"]
      and json.loads(row[0]["kinds"] or "null") == {"music": music["size"], **NO_BOOKS},
      f"knihovna is the whole, kinds the music ({row[0]['kinds'] if row else None})")

print()
print("--- disks ---")
scanner.remember_folders({"ProgramDataFolder": folder("/config", "/"), "Libraries": [
    {"Name": "Filmy", "Id": "filmy", "Folders": [folder("D:/media/filmy", "dev-a")]},
    {"Name": "Hudba", "Id": "hudba", "Folders": [folder("/media/music", "dev-a")]},
    {"Name": "Serialy", "Id": "serialy", "Folders": [folder("D:/media/serialy", "dev-b")]}]})
db.forget_settings()
scanner.write_daily_snapshots()
library = latest("library_snapshot")
check(len(library) == 1 and library[0]["size_bytes"] == int(video["b"]) + music["size"]
      and library[0]["item_count"] == int(video["n"]),
      f"library: the whole size, items still video ({library[0]['size_bytes'] if library else None})")
parts = json.loads(library[0]["kinds"] or "null") if library else None
check(parts == {"music": {"items": music["items"], "size": music["size"]}, "books": {"items": 0, "size": 0}}, f"library kinds: music items and size ({parts})")
disks = {r["disk"]: r for r in latest("library_disk_snapshot")}
films_a = int(db.query_value("SELECT COALESCE(SUM(size_bytes), 0) FROM items WHERE is_missing = 0 AND path LIKE 'D:/media/filmy/%'") or 0)
check(disks.get("dev-a", {}).get("library") == films_a + music["size"]
      and json.loads(disks["dev-a"]["kinds"] or "null") == {"music": music["size"], **NO_BOOKS},
      f"disk A: movies and music, kinds the music ({disks.get('dev-a', {}).get('kinds')})")
check(json.loads(disks.get("dev-b", {}).get("kinds") or "null") == {"music": 0, **NO_BOOKS},
      f"disk B without music: music measured as 0 - not left out, not NULL ({disks.get('dev-b', {}).get('kinds')!r})")

print()
print("--- the tile and the curve agree ---")
curve = stats.snapshots(30)
check(curve and curve[-1]["size_bytes"] == stats.tech_coverage()["size_bytes"] == stats.library_state()["size_bytes"],
      "Velikost celkem, the library state and the last point of the curve are one number")
check(stats.library_state()["kinds"] == {"music": {"items": music["items"], "size": music["size"]}},
      "library_state carries the parts by kind")

print()
print("--- before the first books sync the books are left out ---")
db.set_setting("books_first_sync", "")
db.forget_settings()
scanner.write_disk_snapshot()
disks = {r["disk"]: r for r in latest("library_disk_snapshot")}
check(json.loads(disks["dev-a"]["kinds"] or "null") == {"music": music["size"]}
      and json.loads(disks["dev-b"]["kinds"] or "null") == {"music": 0},
      f"disks: music stored, books not ({disks['dev-a']['kinds']}, {disks['dev-b']['kinds']})")
db.set_setting("books_first_sync", "1")
db.forget_settings()
scanner.write_disk_snapshot()
disks = {r["disk"]: r for r in latest("library_disk_snapshot")}
check(json.loads(disks["dev-b"]["kinds"] or "null") == {"music": 0, **NO_BOOKS}, "after the first sync books are stored, 0 when there are none")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
