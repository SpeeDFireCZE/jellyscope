# -*- coding: utf-8 -*-
r"""Library growth "All" = the whole storage, plus the disk that fills first (the user, 23. 11. 2026).

"All" showed the free space of one disk only (the one the largest file lies on). The user chose variant B: the
free space is the sum of the disks, "runs out in" is the sum / the growth of the whole library, and a tile beside
it names the disk that fills first. The test watches:

A) the sum of the disks (physical and virtual, a disk with unknown free space left out), B's forecast, the tile
   "Fills first", one free-space number (scanner.free_space_now: API, the daily snapshot) = the sum;
B) the notifications still watch the disk that fills first (space_forecast) and name it;
C) the API and the Library page: free_bytes and days_until_full, the tile under "All" and not under a disk's chip;
D) no disk filling = no tile; a confirmed pair of devices is one disk in the sum;
E) a single disk = "All" is that disk (its measured forecast, no tile); Jellyfin without the disks broken down
   keeps its free space.

Spec: docs/superpowers/specs/2026-11-23-disks-and-collections-design.md

Run:
    .venv/bin/python tests/test_growth_all_disks.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "growth-all-disks.db")
os.environ["SECRET_KEY"] = "test-key-growth-all-disks"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, api, db, formatting, notifications, scanner, stats, web  # noqa: E402
from jellyscope.scanner.free_space import _library_free_space  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
DROP = TB // 50                    # the movies' disk loses this much free space a day: full in 50 days
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


def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}


def set_jellyfin(free_a: int, extra: list[dict[str, Any]] | None = None, only_a: bool = False) -> None:
    libraries = [{"Name": "Filmy", "Folders": [folder("/media/movies", "/mnt/a", free_a, 2 * TB)]}]
    if not only_a:
        libraries += [{"Name": "Serialy", "Folders": [folder("/media/shows", "/mnt/b", 4 * TB, 3 * TB)]},
                      {"Name": "Nezname", "Folders": [folder("/media/unknown", "/mnt/d", -1, -1)]}]
    scanner.remember_folders({"Libraries": libraries + (extra or [])})
    db.forget_settings()


def two_days(free_a_yesterday: int, free_a_today: int, **options: Any) -> None:
    """Yesterday's and today's disk snapshots (and today's library snapshot) with the movies' disk free space."""
    set_jellyfin(free_a_yesterday, **options)
    scanner.write_disk_snapshot(day(1))
    set_jellyfin(free_a_today, **options)
    scanner.write_daily_snapshots()
    db.forget_settings()


db.set_setting(scanner.CONVERTED_KEY, "1")
add_item("movie-old", "/media/movies/old.mkv", TB, 100)
for number in range(1, 11):
    add_item(f"movie-{number}", f"/media/movies/m{number}.mkv", 30 * GB, number)
add_item("episode-old", "/media/shows/old.mkv", 500 * GB, 100)
add_item("cloud-old", "/media/movies/cloud/c.mkv", 100 * GB, 200)
set_jellyfin(TB)
scanner.add_virtual("Cloud", "/media/movies/cloud", 2 * TB + 100 * GB)
two_days(TB + DROP, TB)


print("--- A) All = the whole storage ---")
g = stats.library_growth(30)
check(g["enough_data"] and g["daily"] > 0, f"(the library grows: {g.get('daily', 0) / GB:.1f} GB a day)")
check(g["free_space"] == 7 * TB,
      f"All: the free space is the sum of the disks, the unknown one left out ({g['free_space'] / TB:.2f} TB)")
check(g["days_left"] == int(7 * TB / g["daily"]), f"All: runs out = the sum / the library's growth (B) ({g['days_left']})")
check((g.get("fills_first") or {}).get("name") == "Filmy" and g["fills_first"]["days_left"] == 50,
      f"the disk that fills first ({g.get('fills_first')})")
check(not g.get("disk_name"), "All's own forecast names no disk")
check(scanner.free_space_now() == 7 * TB, "one free-space number (API, daily snapshot) is the sum")
check(db.query_value("SELECT free_space FROM library_snapshot WHERE day = ?", (day(0),)) == 7 * TB,
      "today's library snapshot carries the sum")


print()
print("--- B) the notifications watch the disk that fills first ---")
s = stats.space_forecast(90)
check(s["days_left"] == 50 and s["disk_name"] == "Filmy" and s["free_space"] == TB,
      f"space_forecast: the disk that fills first ({s.get('days_left')}, {s.get('disk_name')})")
db.set_setting("notify_space_days", "60")
db.forget_settings()
state = notifications._space_state()
running_out, text = notifications._space_running_out(state)
check(running_out and "Filmy" in text and state["values"]["days"] == "50",
      f"the alert names the disk and its days ({text!r})")


print()
print("--- C) the API and the Library page ---")
client = TestClient(web.app)
token = api.create_token("growth")["token"]
answer = client.get("/api/v1/library?days=30", headers={"Authorization": f"Bearer {token}"}).json()
check(answer.get("free_bytes") == 7 * TB and answer["growth"]["days_until_full"] == g["days_left"],
      f"the API: the sum and B's forecast ({answer.get('free_bytes')}, {answer.get('growth')})")
accounts.create("admin", "longpassword", is_admin=True)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
page = client.get("/library").text
tile = re.search(r"Fills first.{0,600}", page, re.S)
check(tile is not None and "Filmy" in tile.group(0) and "50" in tile.group(0),
      "the Library growth card under All has the tile with the disk and its days")
choice = next(c["choice"] for c in stats.disks_to_choose() if c["name"] == "Filmy")
check("Fills first" not in client.get(f"/library?growth_disk={choice}").text, "a disk's chip has no such tile")


print()
print("--- D) nothing filling; a confirmed pair is one disk ---")
two_days(TB, TB)
check(stats.library_growth(30).get("fills_first") is None, "no disk filling = no tile")
same = [{"Name": "Docker 1", "Folders": [folder("/media/docker1", "/mnt/e", 500 * GB, 500 * GB)]},
        {"Name": "Docker 2", "Folders": [folder("/media/docker2", "/mnt/f", 500 * GB, 500 * GB)]}]
two_days(TB, TB, extra=same)
# Two volumes that look like one disk (Docker) count once until the admin answers - the sum must not show a
# reader twice the space (the user, 23. 11. 2026, minor M1 of the disks review).
check(scanner.free_space_now() == 7 * TB + 500 * GB, "two suspected volumes count once until the admin answers")
check(scanner.decide_same("/mnt/e", "/mnt/f", False), "(the admin says: different disks)")
two_days(TB, TB, extra=same)
check(scanner.free_space_now() == 7 * TB + 1000 * GB, "different disks count both")
check(scanner.undo_decision("/mnt/e") and scanner.decide_same("/mnt/e", "/mnt/f", True), "(the admin confirms one disk)")
two_days(TB, TB, extra=same)
check(scanner.free_space_now() == 7 * TB + 500 * GB, "a confirmed pair is one disk in the sum")


print()
print("--- D2) files on a disk with unknown free space ---")
# The sum leaves the disk out (its free space is unknown), so its files' growth must not shorten the forecast
# either: the known free space takes only the growth of the files on the known disks (minor M2 of the review).
add_item("unknown-old", "/media/unknown/old.mkv", 100 * GB, 100)     # the disk's curve starts with its first file
add_item("unknown-new", "/media/unknown/new.mkv", 300 * GB, 2)
two_days(TB + DROP, TB, extra=same)
g = stats.library_growth(30)
unknown = stats.disk_growth(30, "/mnt/d")
check(unknown["enough_data"] and unknown["daily"] > 0, f"(the unknown disk grows: {unknown.get('daily', 0) / GB:.1f} GB a day)")
check(g["days_left"] == int(g["free_space"] / (g["daily"] - unknown["daily"])),
      f"All: the sum / the growth of the files on the known disks ({g['days_left']})")


print()
print("--- E) a single disk; Jellyfin without the disks broken down ---")
for disk in scanner.virtual_disks():
    scanner.delete_virtual(disk["id"])
two_days(TB + DROP, TB, only_a=True)
g = stats.library_growth(30)
check(g["days_left"] == 50 and g["free_space"] == TB and g.get("fills_first") is None,
      f"a single disk: All is that disk, its measured forecast, no tile ({g['days_left']}, {g['free_space']})")
db.set_setting(scanner.FOLDERS_KEY, "[]")
db.forget_settings()
check(scanner.free_space_now() == _library_free_space(), "without the disks broken down the free space is as before")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
