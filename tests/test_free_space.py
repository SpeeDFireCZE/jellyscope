# -*- coding: utf-8 -*-
r"""Where the free space on the disk comes from.

Originally `shutil.disk_usage` was asked about the disk where **Jellyscope** runs.
But the data tends to be elsewhere - on a NAS, on another machine, in another container -
and then a completely foreign disk was measured and the estimate "the space runs out in X days" spoke
about something else.

Three sources, in this order:

1. **A capacity entered by hand.** For a library in the cloud not even Jellyfin knows it;
   the administrator knows it. Their number beats everything.
2. **Jellyfin.** It sits by those files, so we ask it.
3. **The disk under the application.** The original way; it holds only when the data lies here.

Run:
    .venv/bin/python tests/test_free_space.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "misto.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, scanner  # noqa: E402

failures = 0
GB = 1024 ** 3


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


db.init_db()

print("--- reading Jellyfin's answer ---")
# The library's folder is found by the path.
response = {"Folders": [
    {"Path": "/data/serialy", "FreeSpace": 100 * GB, "TotalSpace": 500 * GB},
    {"Path": "/data/filmy", "FreeSpace": 700 * GB, "TotalSpace": 4000 * GB},
]}
space = scanner.space_from_jellyfin(response, "/data/filmy/Duna/Duna.mkv")
check(space.get("free") == 700 * GB,
      f"the folder the library lies in is chosen ({space})")
check(space.get("total") == 4000 * GB, "with the total size too")

# When none fits, the tightest holds - the space runs out on it.
space = scanner.space_from_jellyfin(response, "/uplne/jinde/film.mkv")
check(space.get("free") == 100 * GB,
      f"otherwise the one with the least free space ({space})")

# Windows and Linux write paths differently; the case of letters is not to decide
# whether the disk is found either.
space = scanner.space_from_jellyfin(
    {"Folders": [{"Path": "D:\\Media\\Filmy", "FreeSpace": 42 * GB}]},
    "d:/media/filmy/a.mkv")
check(space.get("free") == 42 * GB, f"neither slashes nor the case of letters matter ({space})")

# What is not a number is not taken. Guessing a value from which "runs out in
# X days" is computed would be worse than admitting we do not know it.
# The form from Jellyfin 12 (`/System/Info/Storage`, copied from 12.1.0): the server's folders
# as named keys and `Libraries` with the libraries' folders.
# The total space is not sent - it is the sum of the free and the used; -1 is
# "cannot measure" and must not be counted.
twelve = {
    "ProgramDataFolder": {"Path": "C:\\jellyfin", "FreeSpace": 10 * GB,
                          "UsedSpace": 90 * GB, "StorageType": "Fixed"},
    "ImageCacheFolder": {"Path": "C:\\jellyfin\\cache\\images",
                         "FreeSpace": -1, "UsedSpace": -1},
    "Libraries": [
        {"Id": "a", "Name": "Filmy", "Folders": [
            {"Path": "D:\\media\\Filmy", "FreeSpace": 700 * GB,
             "UsedSpace": 3300 * GB, "StorageType": "Fixed"}]},
        {"Id": "b", "Name": "Serialy", "Folders": [
            {"Path": "E:\\media\\Serialy", "FreeSpace": 100 * GB,
             "UsedSpace": 400 * GB, "StorageType": "Fixed"}]},
    ],
}
space = scanner.space_from_jellyfin(twelve, "D:/media/Filmy/Duna/Duna.mkv")
check(space.get("free") == 700 * GB and space.get("total") == 4000 * GB,
      f"Jellyfin 12: the library's folder, total = free + used ({space})")
space = scanner.space_from_jellyfin(twelve, "/jinde/film.mkv")
check(space.get("free") == 100 * GB,
      f"Jellyfin 12: without a match the tightest library, not a server folder ({space})")
space = scanner.space_from_jellyfin({"ProgramDataFolder": twelve["ProgramDataFolder"],
                                     "ImageCacheFolder": twelve["ImageCacheFolder"]},
                                    "/jinde/film.mkv")
check(space.get("free") == 10 * GB and space.get("total") == 100 * GB,
      f"Jellyfin 12 without libraries: a server folder, -1 is skipped ({space})")

for nonsense in ({}, None, {"Folders": []}, {"Folders": [{"Path": "/x"}]},
                 {"Folders": [{"Path": "/x", "FreeSpace": "hodně"}]},
                 {"Folders": [{"Path": "/x", "FreeSpace": 0}]}):
    check(scanner.space_from_jellyfin(nonsense, "/x") == {},
          f"from {str(nonsense)[:34]} nothing is made up")

print()
print("--- the order of the sources ---")


def add_item(number: int, size: int, path: str = "/data/filmy/f.mkv") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, path, size_bytes, is_missing)"
            " VALUES (?,?, 'Movie', ?, ?, 0)",
            (f"i{number}", f"Film {number}", path, size))
        conn.commit()


for number in range(3):
    add_item(number, 100 * GB)          # the library has 300 GB

# 1) Without anything the disk under the application is used.
free = scanner._library_free_space()
check(db.get_setting(scanner.SOURCE_KEY, "") in ("disk", ""),
      f"without another source the disk under the application is read ({db.get_setting(scanner.SOURCE_KEY, '')})")

# 2) The figure from Jellyfin takes precedence over it.
db.set_setting(scanner.JF_FREE_KEY, str(900 * GB))
db.forget_settings()
check(scanner._library_free_space() == 900 * GB,
      "the figure from Jellyfin beats the disk under the application")
check(db.get_setting(scanner.SOURCE_KEY, "") == "jellyfin", "and it is seen in the source")

# 3) A capacity entered by hand beats even Jellyfin.
db.set_setting(scanner.CAPACITY_KEY, str(1000 * GB))
db.forget_settings()
free = scanner._library_free_space()
check(free == 700 * GB,
      f"the capacity minus the library = 1000 - 300 GB ({free / GB:.0f} GB)")
check(db.get_setting(scanner.SOURCE_KEY, "") == "manual", "and the source says so")

# A library bigger than the entered capacity must not give a negative space.
db.set_setting(scanner.CAPACITY_KEY, str(100 * GB))
db.forget_settings()
check(scanner._library_free_space() == 0,
      "a library bigger than the capacity means zero, not a negative number")

# Zero means "find it out yourself", not "zero bytes free".
db.set_setting(scanner.CAPACITY_KEY, "0")
db.forget_settings()
check(scanner._library_free_space() == 900 * GB,
      "zero switches the manual value off and Jellyfin holds again")

print()
print("--- what is stored after a sync ---")
db.set_setting(scanner.JF_FREE_KEY, "")
db.set_setting(scanner.JF_TOTAL_KEY, "")
db.forget_settings()
scanner.remember_space_from_jellyfin(
    {"Folders": [{"Path": "/data/filmy", "FreeSpace": 55 * GB,
                  "TotalSpace": 900 * GB}]})
db.forget_settings()
check(db.get_setting(scanner.JF_FREE_KEY, "") == str(55 * GB),
      "the free space is stored for writing the snapshot later")
check(db.get_setting(scanner.JF_TOTAL_KEY, "") == str(900 * GB),
      "and the total size too")

# The library's path is taken from the BIGGEST item - that is where the disk the library
# really lies on is measured.
add_item(99, 500 * GB, "/jiny/disk/velky.mkv")
scanner.remember_space_from_jellyfin(
    {"Folders": [{"Path": "/data/filmy", "FreeSpace": 10 * GB},
                 {"Path": "/jiny/disk", "FreeSpace": 20 * GB}]})
db.forget_settings()
check(db.get_setting(scanner.JF_FREE_KEY, "") == str(20 * GB),
      "the disk of the biggest file is looked for, not the first folder in the answer")

print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/settings?section=data")
    check(page.status_code == 200, "Data collection loads")
    check('name="library_capacity_gb"' in page.text, "and has a field for the capacity")

    client.post("/settings", follow_redirects=False, data={
        "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "4000"})
    db.forget_settings()
    check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 4000 * GB,
          "the entered GB are stored as bytes")
    html = client.get("/settings?section=data").text
    check('value="4000"' in html, "and come back into the form in GB again")

    # Nonsense is straightened, not stored.
    client.post("/settings", follow_redirects=False, data={
        "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "-9"})
    db.forget_settings()
    check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 0,
          "a negative capacity is straightened to zero")

    # Terabytes. Whoever has a thirty-terabyte array is not to write "30000".
    client.post("/settings", follow_redirects=False, data={
        "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "30", "library_capacity_unit": "TB"})
    db.forget_settings()
    check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 30 * 1024 ** 4,
          "the entered TB are stored as bytes")
    html = client.get("/settings?section=data").text
    check('value="30"' in html, "and come back into the form in TB again, not as 30720")
    check('<option value="TB" selected>' in html, "with the unit TB chosen")

    # A made-up unit is input from a form like any other - GB holds.
    client.post("/settings", follow_redirects=False, data={
        "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "8", "library_capacity_unit": "PB"})
    db.forget_settings()
    check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 8 * GB,
          "an unknown unit is taken as GB")

    # Settings from before did not know the unit. The stored bytes stay
    # stored bytes though - they must not become another capacity.
    db.set_setting(scanner.CAPACITY_KEY, str(20 * 1024 ** 4))
    db.set_setting("library_capacity_unit", "")
    db.forget_settings()
    html = client.get("/settings?section=data").text
    check('value="20"' in html and '<option value="TB" selected>' in html,
          "older settings without a unit show in TB")
    check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 20 * 1024 ** 4,
          "and the value itself does not change meanwhile")

    # Clearing the number is the way back to "find out yourself". FastAPI makes an empty field its default
    # (None), the same as a form without the field - so the hidden fields are sent as the page renders them.
    import re  # noqa: E402

    html = client.get("/settings?section=data").text
    form = html[html.index('method="post" action="/settings"'):]
    form = form[:form.index("</form>")]
    hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
    response = client.post("/settings", follow_redirects=False, data={
        **hidden, "tech_source": "jellyfin", "poll_interval": "10",
        "ffprobe_concurrency": "3", "path_mappings": "[]",
        "library_capacity_gb": "", "library_capacity_unit": "TB"})
    db.forget_settings()
    check(response.status_code == 303 and db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 0,
          f"a cleared capacity field goes back to automatic ({db.get_setting(scanner.CAPACITY_KEY, '')})")

print()
print("--- an older Jellyfin does not have the endpoint ---")
import asyncio  # noqa: E402

from jellyscope.jellyfin import JellyfinClient, JellyfinError  # noqa: E402


async def _try() -> dict:
    jf_client = JellyfinClient("http://127.0.0.1:1", "k")

    async def _fail(path, params=None):
        raise JellyfinError("404")

    jf_client._get = _fail                       # type: ignore[assignment]
    try:
        return await jf_client.storage()
    finally:
        await jf_client.close()


check(asyncio.run(_try()) == {},
      "a missing endpoint returns empty, not an exception - the sync goes on")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
