# -*- coding: utf-8 -*-
r"""The library's disks: edge cases the review found (7. 11. 2026).

1) Jellyfin sends -1 for a folder ("cannot measure"): that must not become
   "0 B free" and the alarm "space runs out in 0 days".
2) Merging two devices into one disk must not throw away the manual capacity
   that was set on the other one.
3) The pace must not be computed across a change of the disk's settings
   (manual -> Jellyfin, a virtual disk added): the numbers before the change
   and after it cannot be compared.
4) After converting the old manual capacity to a disk, the "one number" of
   free space too (the API, the library's daily snapshot, Library growth) must
   come from the disk, not from what Jellyfin reports about the mount.

Run:
    .venv/bin/python tests/test_disk_edge_cases.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "okraje.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import api, api_query, db, formatting, notifications, scanner, stats  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
db.init_db()
TODAY = datetime.now(formatting.zone()).date()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def day(ago: int) -> str:
    return (TODAY - timedelta(days=ago)).isoformat()


def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}


def jellyfin(**libraries: list[dict[str, Any]]) -> None:
    scanner.remember_folders({"Libraries": [{"Name": name, "Folders": folders}
                                            for name, folders in libraries.items()]})
    db.forget_settings()


def set_value(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


def clean_state() -> None:
    """No snapshots, no disk settings - each part starts from zero."""
    with db.connect() as conn:
        conn.execute("DELETE FROM library_disk_snapshot")
        conn.execute("DELETE FROM library_snapshot")
        conn.commit()
    for key in (scanner.FOLDERS_KEY, scanner.DISK_CAPACITY_KEY, scanner.SAME_KEY,
                scanner.DIFFERENT_KEY, scanner.VIRTUAL_KEY):
        set_value(key, "")
    set_value(scanner.CONVERTED_KEY, "1")
    set_value(scanner.CAPACITY_KEY, "0")


def rows() -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in scanner.disk_state()["disks"]}


with db.connect() as conn:
    for item_id, path, size in (("film", "/media/filmy/a.mkv", 30 * GB),
                                ("dil", "/media/serialy/s01e01.mkv", 50 * GB)):   # the biggest file
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)", (item_id, item_id, path, size, db.utcnow()))
    conn.commit()

MOVIES = [folder("/media/filmy", "dev-a", 500 * GB, 500 * GB)]
SERIES = [folder("/media/serialy", "dev-b", 300 * GB, 700 * GB)]


print("--- 1) Jellyfin sends -1 (cannot measure) ---")
clean_state()
jellyfin(Filmy=MOVIES, Serialy=SERIES)
scanner.write_disk_snapshot(day(1))
jellyfin(Filmy=[folder("/media/filmy", "dev-a", -1, -1)], Serialy=SERIES)
scanner.write_disk_snapshot(day(0))
r = rows()["dev-a"]
check(r["free"] is None and r["total"] is None and r["outside"] is None,
      f"unknown space is unknown, not zero ({r['free']}, {r['total']}, {r['outside']})")
check(r["source"] == "", f"and the card says the space could not be found out ({r['source']!r})")
first = stats.runs_out_first(90)
check(first is None or first["days_left"] is None, f"no forecast \"runs out in 0 days\" ({first})")
db.set_setting("notify_misto_dnu", "100")
db.forget_settings()
check(not notifications._space_running_out()[0], f"and no alarm ({notifications._space_running_out()})")
check(not scanner.disk_state()["suspicions"], "unknown numbers are no suspicion of the same disk")
check(scanner.set_capacity("dev-a", "manual", TB) == "" and rows()["dev-a"]["free"] == TB - 30 * GB,
      "a manual capacity holds even when Jellyfin does not know the space")


print()
print("--- 2) merging does not throw away the manual capacity ---")
clean_state()
MUSIC = [folder("/media/hudba", "dev-c", 500 * GB, 500 * GB)]            # the same numbers as dev-a
jellyfin(Filmy=MOVIES, Hudba=MUSIC, Serialy=SERIES)
check(scanner.set_capacity("dev-c", "manual", 4 * TB) == "", "the capacity set on the second device")
check(scanner.decide_same("dev-a", "dev-c", True), "the administrator confirms: it is one disk")
r = rows()
check("dev-c" not in r and r["dev-a"]["source"] == "manual" and r["dev-a"]["total"] == 4 * TB,
      f"the merged disk carries the manual capacity on ({r['dev-a']['source']}, {(r['dev-a']['total'] or 0) / TB} TB)")
check(scanner.set_capacity("dev-a", "jellyfin", 0) == "" and rows()["dev-a"]["source"] == "jellyfin",
      "and switching to Jellyfin removes it from all of the disk's devices")


print()
print("--- 3) the pace is not computed across a change of the settings ---")
clean_state()
jellyfin(Filmy=[folder("/media/filmy", "dev-a", 2 * TB + 50 * GB, 6 * TB)])
scanner.set_capacity("dev-a", "manual", 10 * TB)
scanner.write_disk_snapshot(day(30))                                       # manual: almost 10 TB free
scanner.set_capacity("dev-a", "jellyfin", 0)
scanner.write_disk_snapshot(day(5))                                        # Jellyfin: 2 TB + 50 GB free
check(stats.runs_out_first(90) is None,
      f"a jump from the manual capacity to Jellyfin is no loss of space ({stats.runs_out_first(90)})")
jellyfin(Filmy=[folder("/media/filmy", "dev-a", 2 * TB, 6 * TB + 50 * GB)])
scanner.write_disk_snapshot(day(0))
first = stats.runs_out_first(90)
check(first is not None and first["daily"] == 10 * GB and first["days_left"] == int(2 * TB / (10 * GB)),
      f"the pace only from days with the same settings ({first})")
row = stats.disks_overview(90)["disks"][0]
check(row["days_left"] == int(2 * TB / (10 * GB)), f"the card shows the same ({row['days_left']})")
mark_before = db.query_value("SELECT fingerprint FROM library_disk_snapshot WHERE day = ?", (day(0),))
scanner.add_virtual("Cloud", "/media/filmy/cloud", TB)
scanner.write_disk_snapshot(day(0))
mark_after = db.query_value("SELECT fingerprint FROM library_disk_snapshot WHERE day = ? AND disk = 'dev-a'", (day(0),))
check(mark_before and mark_after and mark_before != mark_after,
      "an added virtual disk changes the physical disk's mark (it takes files from it)")


print()
print("--- 4) after converting the old capacity the one number comes from the disk too ---")
clean_state()
set_value(scanner.CONVERTED_KEY, "")
set_value(scanner.CAPACITY_KEY, str(4 * TB))                                # before: one manual capacity
MOUNT = [folder("/media/serialy", "dev-b", 1000 * TB, 1 * TB)]             # the cloud's mount reports nonsense
jellyfin(Filmy=MOVIES, Serialy=MOUNT)
scanner.remember_space_from_jellyfin({"Libraries": [{"Name": "Serialy", "Folders": MOUNT}]})
db.forget_settings()
expected = 4 * TB - 50 * GB
check(rows()["dev-b"]["free"] == expected, "the card: the manual capacity is at the disk with the series")
check(scanner.free_space_now() == expected,
      f"the one number of free space is from the disk, not from Jellyfin ({(scanner.free_space_now() or 0) / TB} TB)")
scanner.write_daily_snapshots()
check(db.query_value("SELECT free_space FROM library_snapshot ORDER BY day DESC") == expected,
      "the library's daily snapshot writes the same")
check(db.query_value("SELECT COUNT(*) FROM library_disk_snapshot") == 2, "and the disks' snapshot was written too")
answer = api.library(api_query.Query())
check(answer.get("free_bytes") == expected, f"API /library: free_bytes ({answer.get('free_bytes')})")
with db.connect() as conn:
    conn.execute("INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr, without_tech,"
                 " free_space, written_at) VALUES (?, 1, 1, 0, ?, 0, 0, 0, NULL, ?)", (day(10), 70 * GB, db.utcnow()))
    conn.commit()
growth = stats.library_growth(90)
check(growth.get("free_space") == expected, f"Library growth: free space ({(growth.get('free_space') or 0) / TB} TB)")

clean_state()
set_value(scanner.CAPACITY_KEY, str(2 * TB))
check(scanner.free_space_now() == 2 * TB - 80 * GB, "without a breakdown of disks (Jellyfin 10.x) the old manual capacity holds")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
