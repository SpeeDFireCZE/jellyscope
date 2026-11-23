# -*- coding: utf-8 -*-
r"""When a disk runs out of space - from its file growth until the snapshots are enough (the user, 23. 11. 2026).

The forecast of one disk came only from the drop of free space between the disk's daily snapshots, so a disk
with a single snapshot (the first day of the disks, or the day after its settings changed) had none. Now it
falls back to the disk's free space / the daily growth of the files on it over the period. The test watches:

A) one snapshot: the chart's disk, the Disks card and runs_out_first (with disk_growth) give the same number
   from the file growth; without disk_growth nothing changes;
B) a disk that does not grow, and a disk whose free space is unknown, get no forecast;
C) with two snapshots the measured pace holds, not the fallback - also when it says "not filling";
D) the day after a settings change (a virtual disk added: a new fingerprint for every disk) the card and the
   chart agree for every disk.

Spec: docs/superpowers/specs/2026-11-23-disks-and-collections-design.md

Run:
    .venv/bin/python tests/test_disk_forecast_fallback.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "disk-forecast.db")
os.environ["SECRET_KEY"] = "test-key-disk-forecast"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from _package import substitute  # noqa: E402
from jellyscope import accounts, db, formatting, scanner, stats, web  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
db.init_db()
TODAY = datetime.now(formatting.zone()).date()


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def day(ago: int) -> str:
    return (TODAY - timedelta(days=ago)).isoformat()


def add_item(item_id: str, path: str, size: int, created_ago: int) -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=created_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    with db.connect() as conn:
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, date_created, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?, ?)", (item_id, item_id, path, size, created, db.utcnow()))
        conn.commit()


def set_jellyfin(free_a: int, free_b: int) -> None:
    def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
        return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}
    scanner.remember_folders({"Libraries": [
        {"Name": "Filmy", "Folders": [folder("/media/movies", "/mnt/a", free_a, 2 * TB)]},
        {"Name": "Serialy", "Folders": [folder("/media/shows", "/mnt/b", free_b, 3 * TB)]},
        {"Name": "Archiv", "Folders": [folder("/media/archive", "/mnt/c", 2 * TB, TB)]},
        # Jellyfin sends -1 for a folder it cannot measure.
        {"Name": "Nezname", "Folders": [folder("/media/unknown", "/mnt/d", -1, -1)]}]})
    db.forget_settings()


def card() -> dict[str, dict[str, Any]]:
    db.forget_settings()
    return {row["key"]: row for row in stats.disks_overview(30, stats.library_growth, stats.disk_growth)["disks"]}


db.set_setting(scanner.CONVERTED_KEY, "1")
for number, ago in ((1, 25), (2, 15), (3, 5)):
    add_item(f"movie-{number}", f"/media/movies/m{number}.mkv", 100 * GB, ago)
add_item("movie-old", "/media/movies/old.mkv", TB, 100)
add_item("movie-past", "/media/movies/past.mkv", 200 * GB, 60)       # grows a past period without snapshots
add_item("episode", "/media/shows/e.mkv", 30 * GB, 10)
add_item("episode-old", "/media/shows/old.mkv", 500 * GB, 100)
add_item("archive-old", "/media/archive/old.mkv", 200 * GB, 200)
add_item("unknown", "/media/unknown/u.mkv", 40 * GB, 8)
set_jellyfin(3 * TB, TB)
scanner.write_disk_snapshot(day(0))


print("--- A) one snapshot: the forecast from the file growth ---")
g = stats.disk_growth(30, "/mnt/a")
check(g["enough_data"] and g["daily"] > 0, f"(the disk's curve grows: {g.get('daily', 0) / GB:.1f} GB a day)")
check(g["days_left"] == int(3 * TB / g["daily"]),
      f"a disk with one snapshot gets a forecast from its file growth ({g['days_left']})")
check(card()["/mnt/a"]["days_left"] == g["days_left"], "the Disks card says the same as the chart")
first = stats.runs_out_first(30, stats.disk_growth)
check(first is not None and first["key"] == "/mnt/a" and first["days_left"] == g["days_left"],
      f"runs_out_first with the fallback names the disk that fills first ({first})")
check(stats.runs_out_first(30) is None, "without disk_growth nothing changes (one snapshot = no forecast)")
gb = stats.disk_growth(30, "/mnt/b")
check(gb["days_left"] == int(TB / gb["daily"]) == card()["/mnt/b"]["days_left"],
      "the second disk too, the same in the chart and the card")

# Every disk is without a pace here: the Library page under "All" asks for each disk's forecast in the Disks card
# and again for the tile "Fills first" - the disk's file curve is computed once per load.
computed: list[str] = []
original_series = stats.growth._disk_growth_series     # where a curve is computed, not answered from memory


def counting_series(days: Any, key: str) -> list[dict[str, Any]]:
    computed.append(key)
    return original_series(days, key)


restore = substitute(stats, "_disk_growth_series", counting_series)
accounts.create("admin", "longpassword", is_admin=True)
admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
admin.get("/library")
restore()
check(computed and len(computed) == len(set(computed)),
      f"the Library under All computes each disk's curve once ({sorted(computed)})")


# A past period ends before the disk's first snapshot: the chart has no free space there and no forecast - the
# card says the same, it does not take today's free space for a period it did not measure (minor M3).
past = stats.period_between(day(80), day(40))
past_card = {r["key"]: r for r in stats.disks_overview(past, stats.library_growth, stats.disk_growth)["disks"]}
check(stats.disk_growth(past, "/mnt/a")["days_left"] is None and past_card["/mnt/a"]["days_left"] is None,
      f"a past period without snapshots: no forecast in the chart nor the card ({past_card['/mnt/a']['days_left']})")

print()
print("--- B) no growth, unknown free space ---")
check(stats.disk_growth(30, "/mnt/c")["days_left"] is None and card()["/mnt/c"]["days_left"] is None,
      "a disk with no files added in the period has no forecast")
check(stats.disk_growth(30, "/mnt/d")["days_left"] is None and card()["/mnt/d"]["days_left"] is None,
      "a disk whose free space Jellyfin cannot measure has no forecast")


print()
print("--- C) two snapshots: the measured pace holds ---")
set_jellyfin(3 * TB + 50 * GB, TB)
scanner.write_disk_snapshot(day(1))
set_jellyfin(3 * TB, TB)
scanner.write_disk_snapshot(day(0))
measured = int(3 * TB / (50 * GB))
check(stats.disk_growth(30, "/mnt/a")["days_left"] == measured == card()["/mnt/a"]["days_left"],
      f"the drop of free space (50 GB a day) holds, not the file growth ({measured})")
check(stats.disk_growth(30, "/mnt/b")["days_left"] is None and card()["/mnt/b"]["days_left"] is None,
      "free space standing still = not filling, no fallback")
first = stats.runs_out_first(30, stats.disk_growth)
check(first["key"] == "/mnt/a" and first["days_left"] == measured, "runs_out_first takes the measured pace")


print()
print("--- D) the day after a settings change ---")
add_item("cloud", "/media/movies/cloud/c.mkv", 20 * GB, 3)
scanner.add_virtual("Cloud", "/media/movies/cloud", TB)
scanner.write_disk_snapshot(day(0))
db.forget_settings()
rows = card()
for choice in stats.disks_to_choose():
    chart = stats.disk_growth(30, choice["key"])["days_left"]
    check(chart == rows[choice["key"]]["days_left"],
          f"{choice['name']}: the card and the chart agree ({chart}, {rows[choice['key']]['days_left']})")
check(rows["/mnt/a"]["days_left"] is not None, "the physical disk has a forecast again (from its files)")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
