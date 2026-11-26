# -*- coding: utf-8 -*-
r"""The library's growth by disks (the user, 12. 11. 2026).

The Library growth chart showed only the sum of all disks. Now it can be switched to
one disk. The test watches:

A) the choice of disks (names from the Disks card) and the series of one disk: measurements from
   the disk's snapshots, the past filled in only from files that lie on the disk,
   joining to the first measurement, data outside Jellyfin; a virtual disk's file
   does not count for the physical disk, neither in the measurement nor in the fill-in;
B) the disk's summary (tiles): the increase, and "the space runs out in" the same as
   in the Disks card; a disk with a single snapshot or an unknown disk = too little data;
C) chips in the Library growth card: "Vše" and the disks with names from the card, the choice
   in the address and in the session, on the Library and in the custom dashboard; an unknown or
   deleted disk = "Vše"; a key with a slash and a space; one disk = without chips;
D) Jellyfin 10.x without a breakdown of disks: the choice empty.

Spec: docs/superpowers/specs/2026-11-12-disky-cesty-a-rust-design.md

Run:
    .venv/bin/python tests/test_disk_growth.py
"""
from __future__ import annotations

import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rust_disku.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, formatting, scanner, stats  # noqa: E402

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


def set_jellyfin(free_movies: int, used_movies: int) -> None:
    def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
        return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}
    scanner.remember_folders({"Libraries": [
        {"Name": "Filmy", "Folders": [folder("/media/filmy", "dev-a", free_movies, used_movies)]},
        {"Name": "Serialy", "Folders": [folder("/media/serialy", "dev-b", 300 * GB, 700 * GB)]}]})
    db.forget_settings()


def set_value(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


set_value(scanner.CONVERTED_KEY, "1")
add_item("a", "/media/filmy/a.mkv", 30 * GB, 30)
add_item("s", "/media/serialy/s.mkv", 50 * GB, 10)
set_jellyfin(500 * GB, 500 * GB)
scanner.write_disk_snapshot(day(4))
add_item("b", "/media/filmy/b.mkv", 10 * GB, 2)
set_jellyfin(480 * GB, 520 * GB)
scanner.write_disk_snapshot(day(0))


print("--- A) the choice and the series of one disk ---")
check([(d["key"], d["name"]) for d in stats.disks_to_choose()] == [("dev-a", "Filmy"), ("dev-b", "Serialy")],
      f"the choice of disks with names from the Disks card ({stats.disks_to_choose()})")
series = {r["day"]: r for r in stats.disk_growth_series(30, "dev-a")}
check(day(4) in series and series[day(4)]["size_bytes"] == 30 * GB and not series[day(4)]["backfilled"]
      and series[day(0)]["size_bytes"] == 40 * GB,
      "measured days from the disk's snapshots (30 and 40 GB)")
check(series.get(day(4), {}).get("outside") == 470 * GB and series.get(day(0), {}).get("outside") == 480 * GB,
      "data outside Jellyfin from its snapshots")
check(day(20) in series and series[day(20)]["size_bytes"] == 30 * GB and series[day(20)]["backfilled"]
      and series[day(10)]["size_bytes"] == 30 * GB,
      "the fill-in only from the disk's files: an episode on the second disk (D-10) does not belong in the movies' curve")


print()
print("--- B) the disk's summary (tiles) ---")
growth = stats.disk_growth(30, "dev-a")
card = {r["key"]: r for r in stats.disks_overview(30)["disks"]}
check(growth["enough_data"] and growth["increase"] == 10 * GB and growth["disk_name"] == "Filmy" and growth["item_count"] is None,
      f"10 GB added, the disk's name, without the number of items ({growth.get('increase')}, {growth.get('disk_name')})")
check(growth["days_left"] == 96 == card["dev-a"]["days_left"] and growth["free_space"] == 480 * GB,
      f"the space runs out in the same as in the Disks card ({growth['days_left']}, {card['dev-a']['days_left']})")
check(stats.disk_growth(30, "dev-b")["days_left"] is None, "a standing disk has no prediction")

# A virtual disk's file does not belong in the physical disk's curve - neither in
# the measurement nor in the fill-in. Every file is in exactly one disk.
add_item("c", "/media/filmy/cloud/c.mkv", 5 * GB, 20)
scanner.add_virtual("Cloud", "/media/filmy/cloud", TB)
scanner.write_disk_snapshot(day(0))
series = {r["day"]: r for r in stats.disk_growth_series(30, "dev-a")}
check(series[day(0)]["size_bytes"] == 40 * GB and series[day(20)]["size_bytes"] == 30 * GB,
      f"a virtual disk's file is not counted by the physical disk ({series[day(0)]['size_bytes'] // GB}, "
      f"{series[day(20)]['size_bytes'] // GB} GB)")
latest = [stats.disk_growth_series(30, d["key"])[-1]["size_bytes"] for d in stats.disks_to_choose()]
check(sum(latest) == stats.library_state()["size_bytes"],
      f"the sum of the disks = the whole library ({sum(latest) // GB} of {stats.library_state()['size_bytes'] // GB} GB)")

scanner.add_virtual("Novy", "/media/serialy/novy", TB)
scanner.write_disk_snapshot(day(0))
key = next(d["key"] for d in stats.disks_to_choose() if d["name"] == "Novy")
check(stats.disk_growth(30, key)["enough_data"] is False, "a disk with a single snapshot: too little data")
check(stats.disk_growth(30, "neni")["enough_data"] is False and stats.disk_growth_series(30, "neni") == [],
      "an unknown disk: too little data, an empty series")
check(stats.library_growth(30)["size"] == stats.library_state()["size_bytes"], "“Vše” unchanged")


print()
print("--- C) chips in the Library growth card ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, cards, web  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


def card(html: str) -> str:
    """The Library growth card: from its id to the start of the next card."""
    if 'id="library-growth"' not in html:
        return ""
    start = html.index('id="library-growth"')
    following = html.find('<div class="card"', start)
    return html[start:following if following > 0 else len(html)]


def active(k: str) -> str:
    """The key of the active chip (from its link), or ""."""
    m = re.search(r'class="chip active"[^>]*href="\?growth_disk=([^"#]*)', k)
    return m.group(1) if m else ""


def choice(key: str) -> str:
    """What the chip's address carries: a fingerprint of the disk's key (the key is the device's id = a path)."""
    return next(d["choice"] for d in stats.disks_to_choose() if d["key"] == key)


admin, reader = sign_in("admin"), sign_in("reader")
A, B = choice("dev-a"), choice("dev-b")
k = card(admin.get("/library?days=30").text)
check('href="?growth_disk=all"' in k and f'href="?growth_disk={A}"' in k and f'data-growth-disk="{A}"' in k
      and ">Filmy<" in k and ">Serialy<" in k and ">Cloud<" in k,
      "chips: Vše and the disks with names from the Disks card")
check(active(k) == "all", f"without a choice Vše is chosen ({active(k)!r})")
k = card(admin.get(f"/library?days=30&growth_disk={A}").text)
increase = formatting.bytes_human(stats.disk_growth(30, "dev-a")["increase"])
check(active(k) == A and "Filmy" in k and increase in k,
      f"the chosen disk: its curve and tiles ({increase} added)")
check(active(card(admin.get("/library").text)) == A, "the choice is remembered in the session")
check(active(card(admin.get("/library?growth_disk=neni").text)) == "all", "an unknown disk = Vše")
# A session from before part 10d2 remembers the whole library as "all": still the whole library, shown as all.
check(cards.growth_data(30, "vse") == cards.growth_data(30, "all") and cards.growth_data(30, "vse")["growth_disk"] == "all",
      "an old remembered value (vse) means the whole library, shown as all")
# The card's partial: a chip swaps only the card, the page stays where it is (the user, 12. 11.).
check("#library-growth" not in k, "the chip's link has no anchor - the page does not jump to the chart after the click")
r = admin.get(f"/library/partials/growth?growth_disk={A}")
check(r.status_code == 200 and 'id="library-growth"' in r.text and "<html" not in r.text and active(r.text) == A,
      f"the card's partial: only the card, with the chosen disk ({r.status_code})")
check(active(card(admin.get("/library").text)) == A, "the partial remembers the choice in the session like the page")
check(reader.get(f"/library/partials/growth?growth_disk={B}").status_code == 200, "a reader may have the partial too (the Library area)")
admin.get("/library?growth_disk=all")
check(">Filmy<" in card(reader.get("/library").text), "a reader sees the chips too")
db.set_setting(cards.ENABLED_KEY, "1")
db.forget_settings()
cards.save_layout([("library_growth", "full")])
check(active(card(admin.get(f"/dashboard?growth_disk={B}").text)) == B,
      "the choice holds in the custom dashboard too")

# A disk key with slashes and a space (the device's id is a mount point).
scanner.remember_folders({"Libraries": [
    {"Name": "Filmy", "Folders": [{"Path": "/media/filmy", "FreeSpace": 480 * GB, "UsedSpace": 520 * GB,
                                   "DeviceId": "dev-a"}]},
    {"Name": "Hudba", "Folders": [{"Path": "/media/hudba", "FreeSpace": 50 * GB, "UsedSpace": 50 * GB,
                                   "DeviceId": "/mnt/data disk"}]}]})
db.forget_settings()
scanner.write_disk_snapshot(day(0))
k = card(admin.get("/library").text)
check(f'href="?growth_disk={choice("/mnt/data disk")}"' in k and "/mnt/data" not in k,
      "a key with slashes and a space: only its fingerprint in the link")
r = admin.get(f"/library?growth_disk={choice('/mnt/data disk')}")
check(r.status_code == 200 and active(card(r.text)) == choice("/mnt/data disk"), "and it chooses the right disk")

# The remembered disk deleted in the meantime.
cloud = next(d for d in stats.disks_to_choose() if d["name"] == "Cloud")
check(active(card(admin.get(f"/library?growth_disk={cloud['choice']}").text)) == cloud["choice"],
      "a virtual disk can be chosen")
scanner.delete_virtual(cloud["key"][5:])
scanner.write_disk_snapshot(day(0))
r = admin.get("/library")
check(r.status_code == 200 and active(card(r.text)) == "all", "a deleted disk in the session = Vše, nothing falls over")

# One disk = without chips.
scanner.remember_folders({"Libraries": [{"Name": "Filmy", "Folders": [
    {"Path": "/media/filmy", "FreeSpace": 480 * GB, "UsedSpace": 520 * GB, "DeviceId": "dev-a"}]}]})
set_value(scanner.VIRTUAL_KEY, [])
with db.connect() as conn:
    conn.execute("DELETE FROM library_disk_snapshot")
    conn.commit()
scanner.write_disk_snapshot(day(0))
k = card(admin.get("/library").text)
check(k and "growth_disk=" not in k, "one disk: the chart without chips")


print()
print("--- D) Jellyfin 10.x without a breakdown of disks ---")
set_value(scanner.FOLDERS_KEY, "")
with db.connect() as conn:
    conn.execute("DELETE FROM library_disk_snapshot")
    conn.commit()
scanner.write_disk_snapshot(day(0))
check(stats.disks_to_choose() == [], "the choice of disks is empty")

print()
print("--- E) 'the last N days' grow over all N days on a disk too ---")
# The disk's curve started at the first of the seven days, whose snapshot already holds that day's additions: one
# gigabyte a day came out as 6 GB in a week. The base is the end of the day before; the chart keeps seven points.
from jellyscope import content_kinds  # noqa: E402

with db.connect() as conn:
    conn.execute("DELETE FROM library_disk_snapshot")
    for ago in range(12, -1, -1):
        conn.execute("INSERT INTO library_disk_snapshot (day, disk, name, kind, source, total, free, library, outside,"
                     " fingerprint, written_at, kinds) VALUES (?, 'dev-n', 'Nove', 'physical', 'jellyfin', ?, ?, ?, 0,"
                     " 'same', ?, ?)", (day(ago), TB, TB - (12 - ago) * GB, (12 - ago) * GB, db.utcnow(),
                                        json.dumps({kind.key: 0 for kind in content_kinds.KINDS})))
    conn.commit()
week = stats.disk_growth(7, "dev-n")
check(week["enough_data"] and week["increase"] == 7 * GB and week["days"] == 7,
      f"a gigabyte a day: 7 GB in the last 7 days ({week.get('increase', 0) / GB:g} GB, {week.get('days')} days)")
check([r["day"] for r in stats.disk_growth_series(7, "dev-n")] == [day(ago) for ago in range(6, -1, -1)],
      "and the chart draws the seven days")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
