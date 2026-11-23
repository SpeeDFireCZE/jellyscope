# -*- coding: utf-8 -*-
r"""Music in the Disks card and in the total size (content_kinds.py).

Spec: docs/superpowers/specs/2026-11-16-content-kinds-size-design.md, parts 3 and 4.
Tracks used to fall into "outside Jellyfin" (no path) and the total size did
not know them. Now a track with a path counts as Jellyfin data on its disk,
and every present track counts in the total size and in Jellyfin 10.x's
single "Library" row.

Run:
    .venv/bin/python tests/test_content_kinds_disks.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "content-kinds-disks.db")
os.environ["SECRET_KEY"] = "test-key-content-kinds-disks"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, content_kinds, db, formatting, scanner, stats, web  # noqa: E402

failures = 0
GB = 1024 ** 3
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def item(item_id: str, path: str, size: int) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)", (item_id, item_id, path, size, db.utcnow()))
        conn.commit()


def track(track_id: str, path: str | None, size: int | None, missing: int = 0) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO music_track (id, name, path, size_bytes, added_at, synced_at, missing)"
                     " VALUES (?, ?, ?, ?, '2026-10-01 10:00:00', ?, ?)",
                     (track_id, track_id, path, size, db.utcnow(), missing))
        conn.commit()


def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}


def rows() -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in scanner.disk_state()["disks"]}


item("film", "/media/filmy/a.mkv", 10 * GB)
item("ep", "/media/serialy/s01e01.mkv", 5 * GB)
track("t1", "/media/hudba/artist/album/01.flac", 2 * GB)
track("t2", "/media/hudba/artist/album/02.flac", 1 * GB)
track("t-no-path", "", 2 * GB)                                     # Jellyfin gave no path for it
track("t-gone", "/media/hudba/old/01.flac", 4 * GB, missing=1)    # no longer in Jellyfin

print("--- Jellyfin 10.x: one row, music included ---")
state = scanner.disk_state()
check(state["mode"] == "single" and state["disks"][0]["library"] == 15 * GB + 5 * GB,
      f"the Library row counts every present track, with or without a path ({state['disks'][0]['library'] / GB})")

# Final review 16. 11. 2026: with a manual capacity the free space is the capacity minus every
# file Jellyfin has, music included - like the Library row's Jellyfin data.
db.set_setting(scanner.CAPACITY_KEY, str(100 * GB))
db.forget_settings()
row = scanner.disk_state()["disks"][0]
check(row["library"] + row["free"] == 100 * GB,
      f"10.x manual capacity: Jellyfin data and free space add up to it ({(row['library'] + row['free']) / GB} GB)")
db.set_setting(scanner.CAPACITY_KEY, "0")
db.forget_settings()

print()
print("--- disks: music on its disk ---")
scanner.remember_folders({"ProgramDataFolder": folder("/config", "/", 5 * GB, 20 * GB), "Libraries": [
    {"Name": "Filmy", "Id": "filmy", "Folders": [folder("/media/filmy", "dev-a", 100 * GB, 400 * GB)]},
    {"Name": "Hudba", "Id": "hudba", "Folders": [folder("/media/hudba", "dev-a", 100 * GB, 400 * GB)]},
    {"Name": "Serialy", "Id": "serialy", "Folders": [folder("/media/serialy", "dev-b", 50 * GB, 950 * GB)]}]})
by_disk = rows()
a, b = by_disk.get("dev-a", {}), by_disk.get("dev-b", {})
check(a.get("library") == 13 * GB, f"disk A: the movie and the tracks with a path ({(a.get('library') or 0) / GB} GB)")
check(a.get("outside") == 400 * GB - 13 * GB, f"outside Jellyfin smaller by the music ({(a.get('outside') or 0) / GB} GB)")
check(b.get("library") == 5 * GB and b.get("outside") == 945 * GB, "disk B without music unchanged")
files = scanner.files_by_disk(scanner.physical_disks(), scanner.virtual_disks())
check(files.get("dev-a", {}).get("kinds") == {"music": 3 * GB} and files.get("dev-b", {}).get("kinds") == {},
      f"files by disk carry the music part ({files.get('dev-a', {}).get('kinds')}, {files.get('dev-b', {}).get('kinds')})")
check(files.get("", {}).get("bytes", 0) == 0, "a track without a path is on no disk (and not 'outside everything' either)")

print()
print("--- music by disk is counted once, until the tracks change ---")
# Speed (16. 11. 2026): the Disks card went through every track's path on every view.
calls: list[int] = []
original_files = content_kinds.current_files


def counted_files():
    calls.append(1)
    return original_files()


content_kinds.current_files = counted_files
try:
    track("t3", "/media/hudba/artist/album/03.flac", 1 * GB)      # a change: counted afresh
    first = rows()["dev-a"]["library"]
    rows()
    rows()
    check(len(calls) == 1 and first == 14 * GB, f"three views after a change go through the tracks once ({len(calls)})")
    with db.connect() as conn:
        conn.execute("DELETE FROM music_track WHERE id = 't3'")
        conn.commit()
    check(rows()["dev-a"]["library"] == 13 * GB and len(calls) == 2, "and again when the tracks change")
finally:
    content_kinds.current_files = original_files

print()
print("--- the total size ---")
coverage = stats.tech_coverage()
check(coverage["size_bytes"] == 15 * GB + 5 * GB and coverage["kinds"] == {"music": 5 * GB}
      and coverage["total"] == 2, f"video and music together, music apart, items still video ({coverage['size_bytes'] / GB})")
accounts.create("admin", "longpassword", is_admin=True)
admin = login("admin")
page = admin.get("/library").text
note = f"z toho hudba {formatting.bytes_human(5 * GB)}"
check(note in page and "všechny knihovny dohromady" not in page, f"the tile's note: {note}")
with db.connect() as conn:
    conn.execute("UPDATE music_track SET size_bytes = NULL WHERE id = 't2'")
    conn.commit()
page = admin.get("/library").text
tile = re.search(r'Velikost celkem</p>\s*<div class="tile-value">[^<]*</div>\s*<div class="delta[^"]*">([^<]*)<', page)
check(stats.tech_coverage()["without_size"] == 1 and tile is not None and "1 položek bez velikosti" in tile.group(1),
      f"a track without a size: the note about items without a size wins and counts it ({tile and tile.group(1)})")

print()
print("--- the API mirrors the page ---")
# /api/v1/library gives "the same numbers as the Library page" - with music since 16. 11. 2026;
# the number of items stays movies and episodes.
token = api.create_token("test", None, None)["token"]
library = TestClient(web.app).get("/api/v1/library", headers={"Authorization": f"Bearer {token}"}).json()
check(library.get("size_bytes") == stats.tech_coverage()["size_bytes"] and library.get("items") == 2,
      f"size with music, items video ({library.get('size_bytes')}, {library.get('items')})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
