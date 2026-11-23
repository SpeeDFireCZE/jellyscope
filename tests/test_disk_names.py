# -*- coding: utf-8 -*-
r"""A custom name for a physical disk (the user, 23. 11. 2026).

A physical disk was named after the libraries on it ("Movies, Shows") and could not be renamed. Now the disk's
window on the Disks card has a Name field. The test watches:

A) saving a name: trimmed, cut to 60 characters, empty = the default name after the libraries, an unknown disk
   = an error; renaming does not change the disk's fingerprint (the pace does not start over);
B) a name stored at a device that a merge joined to another disk still holds;
C) the route: the administrator saves the name with the capacity form, a form without the field (an old tab)
   keeps the name, a reader changes nothing; the snapshot and the Library growth chips get the new name at once;
D) the card: the window has the field with the custom name and the default name as a placeholder; a reader sees
   no field.

Spec: docs/superpowers/specs/2026-11-23-disks-and-collections-design.md

Run:
    .venv/bin/python tests/test_disk_names.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "disk-names.db")
os.environ["SECRET_KEY"] = "test-key-disk-names"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, formatting, scanner, stats, web  # noqa: E402

failures = 0
GB = 1024 ** 3
db.init_db()


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}


def rows() -> dict[str, dict[str, Any]]:
    db.forget_settings()
    return {row["key"]: row for row in scanner.disk_state()["disks"]}


with db.connect() as conn:
    for item_id, path, size in (("movie", "/media/movies/a.mkv", 30 * GB),
                                ("episode", "/media/shows/s01e01.mkv", 50 * GB)):
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)", (item_id, item_id, path, size, db.utcnow()))
    conn.commit()
scanner.remember_folders({"Libraries": [
    {"Name": "Filmy", "Folders": [folder("/media/movies", "/mnt/a", 500 * GB, 500 * GB)]},
    {"Name": "Serialy", "Folders": [folder("/media/shows", "/mnt/b", 300 * GB, 700 * GB)]}]})
db.set_setting(scanner.CONVERTED_KEY, "1")


print("--- A) saving a name ---")
fingerprint_before = rows()["/mnt/a"]["fingerprint"]
check(rows()["/mnt/a"]["name"] == "Filmy" and rows()["/mnt/a"]["custom_name"] == "",
      "without a name the disk is named after its libraries")
check(scanner.set_disk_name("/mnt/a", "  Velký disk  ") == "", "a name is saved")
row = rows()["/mnt/a"]
check(row["name"] == "Velký disk" and row["custom_name"] == "Velký disk" and row["default_name"] == "Filmy",
      f"the card shows the custom name, the default stays known ({row['name']!r}, {row['default_name']!r})")
check(row["fingerprint"] == fingerprint_before, "renaming does not change the fingerprint")
check(rows()["/mnt/b"]["name"] == "Serialy", "the other disk keeps its name")
check(scanner.set_disk_name("/mnt/a", "x" * 70) == "" and rows()["/mnt/a"]["name"] == "x" * 60, "cut to 60")
check(scanner.set_disk_name("/mnt/a", "   ") == "" and rows()["/mnt/a"]["name"] == "Filmy",
      "empty = default name")
check(scanner.set_disk_name("/mnt/zzz", "X") == "No such disk here.", "an unknown disk")


print()
print("--- B) a name at a device joined by a merge ---")
check(scanner.set_disk_name("/mnt/b", "Seriálový disk") == "", "the second disk gets a name")
# Only a suspected pair can be merged: two Docker volumes of one disk report the same numbers.
scanner.remember_folders({"Libraries": [
    {"Name": "Filmy", "Folders": [folder("/media/movies", "/mnt/a", 500 * GB, 500 * GB)]},
    {"Name": "Serialy", "Folders": [folder("/media/shows", "/mnt/b", 500 * GB, 500 * GB)]}]})
check(scanner.decide_same("/mnt/a", "/mnt/b", True), "(the admin confirms one disk)")
merged = rows()
check(list(merged) == ["/mnt/a"] and merged["/mnt/a"]["name"] == "Seriálový disk",
      f"after the merge the name stored at the joined device holds ({[r['name'] for r in merged.values()]})")
scanner.undo_decision("/mnt/a")
check(sorted(rows()) == ["/mnt/a", "/mnt/b"], "(the merge taken back)")
scanner.set_disk_name("/mnt/b", "")


print()
print("--- C) the route ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")


def window_form(disk: str) -> dict[str, str]:
    """The hidden fields of a physical disk's window as the Library page draws them - what the browser sends."""
    page = admin.get("/library").text
    for form_html in re.findall(r'<form method="post" action="/library/disks/capacity">(.*?)</form>', page, re.S):
        hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form_html))
        if hidden.get("disk") == disk:
            return hidden
    return {}


form = {**window_form("/mnt/a"), "source": "jellyfin", "capacity": "", "unit": "GB", "name": "Cloud"}
r = reader.post("/library/disks/capacity", data=form, follow_redirects=False)
check(r.status_code == 403 and rows()["/mnt/a"]["name"] == "Filmy", f"a reader changes nothing ({r.status_code})")
r = admin.post("/library/disks/capacity", data=form, follow_redirects=True)
check(rows()["/mnt/a"]["name"] == "Cloud" and "Disk saved." in r.text,
      "the administrator saves the name with the capacity form")
# A tab opened before the field existed sends neither the field nor anything the window adds with it.
old_tab = {"disk": "/mnt/a", "source": "jellyfin", "capacity": "", "unit": "GB"}
admin.post("/library/disks/capacity", data=old_tab, follow_redirects=True)
check(rows()["/mnt/a"]["name"] == "Cloud", "a form without the name field (an old tab) keeps the name")
admin.post("/library/disks/capacity", data={**form, "name": ""}, follow_redirects=True)
check(rows()["/mnt/a"]["name"] == "Filmy", "a cleared Name field gives the disk its default name back")
admin.post("/library/disks/capacity", data=form, follow_redirects=True)
today = datetime.now(formatting.zone()).strftime("%Y-%m-%d")
check(db.query_value("SELECT name FROM library_disk_snapshot WHERE disk = '/mnt/a' AND day = ?", (today,)) == "Cloud",
      "today's disk snapshot has the new name")
check("Cloud" in [choice["name"] for choice in stats.disks_to_choose()], "the Library growth chip has the new name")


print()
print("--- D) the card ---")
page = admin.get("/library").text
check(re.search(r'<input[^>]*name="name"[^>]*value="Cloud"[^>]*placeholder="Filmy"', page) is not None,
      "the disk's window: the Name field with the custom name, the default as a placeholder")
check(re.search(r'<input[^>]*name="name"[^>]*value=""[^>]*placeholder="Serialy"', page) is not None,
      "a disk without a custom name: an empty field with its default name")
check('name="name"' not in reader.get("/library").text, "a reader sees no field")



print()
print("--- E) a rename keeps a manual capacity (the window rounds it to four places) ---")


def capacity_in_window(action: str, hidden_name: str, hidden_value: str) -> dict[str, str]:
    """The capacity and unit a disk's window shows, plus its hidden fields - what saving it sends back."""
    page = admin.get("/library").text
    for form_html in re.findall(rf'<form method="post" action="{action}">(.*?)</form>', page, re.S):
        hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form_html))
        if hidden.get(hidden_name) == hidden_value:
            value = re.search(r'<input[^>]*name="capacity"[^>]*value="([^"]*)"', form_html, re.S)
            unit = re.search(r'<option value="(GB|TB)" selected>', form_html)
            paths = re.findall(r'<input type="text" name="path"[^>]*value="([^"]*)"', form_html)
            return {**hidden, "capacity": value.group(1) if value else "", "unit": unit.group(1) if unit else "",
                    "path": paths}
    return {}


check(scanner.set_capacity("/mnt/b", "manual", 1500 * GB) == "", "(the series disk gets a manual 1500 GB)")
scanner.write_disk_snapshot()
before = rows()["/mnt/b"]
shown = capacity_in_window("/library/disks/capacity", "disk", "/mnt/b")
check(shown.get("capacity") == "1.4648" and shown.get("unit") == "TB", f"(the window shows it rounded: {shown})")
admin.post("/library/disks/capacity", data={**{k: v for k, v in shown.items() if k != "path"},
                                            "source": "manual", "name": "Seriály"}, follow_redirects=True)
after = rows()["/mnt/b"]
check(after["name"] == "Seriály" and after["total"] == 1500 * GB and after["fingerprint"] == before["fingerprint"],
      f"renamed, the capacity and the fingerprint stay ({after['total']}, {after['fingerprint'] == before['fingerprint']})")
check(scanner.add_virtual("Cloud", "/media/movies/cloud", 1500 * GB)[0] == "", "(a virtual disk of 1500 GB)")
virtual = next(v for v in scanner.virtual_disks() if v["name"] == "Cloud")
shown = capacity_in_window("/library/disks/virtual/edit", "id", virtual["id"])
admin.post("/library/disks/virtual/edit", data={**shown, "name": "Cloud Drive"}, follow_redirects=True)
db.forget_settings()
renamed = next(v for v in scanner.virtual_disks() if v["id"] == virtual["id"])
check(renamed["name"] == "Cloud Drive" and renamed["capacity"] == 1500 * GB,
      f"a virtual disk renamed in its window keeps its capacity ({renamed['capacity']})")
check(scanner.set_capacity("/mnt/b", "manual", 1600 * GB) == "" and rows()["/mnt/b"]["total"] == 1600 * GB,
      "a real change of the capacity is still saved")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
