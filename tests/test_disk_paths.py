# -*- coding: utf-8 -*-
r"""A virtual disk with several paths (the user, 12. 11. 2026).

A cloud mount is often seen through several libraries' folders (/media/filmy/cloud
and /media/serialy/cloud) while the quota is one. The test watches:

A) the list of paths: assigning files under any of them, every file
   in exactly one disk, empty and doubled fields, the rules for paths (into a library's
   folder, without an overlap with another virtual disk), the fingerprint and the older entry
   with one path;
B) routes with a repeated `path` field (the administrator only);
C) the dialog: a field with a cross for every path and the “Add a path” button.

Spec: docs/superpowers/specs/2026-11-12-disky-cesty-a-rust-design.md

Run:
    .venv/bin/python tests/test_disk_paths.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cesty.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, formatting, scanner, web  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
TODAY = datetime.now(formatting.zone()).date()


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def day(ago: int) -> str:
    return (TODAY - timedelta(days=ago)).isoformat()


def folder(path: str, device: str) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": 100 * GB, "UsedSpace": 400 * GB, "StorageType": "Fixed", "DeviceId": device}


def put_setting(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


def rows() -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in scanner.disk_state()["disks"]}


def fingerprint(day_: str, disk: str) -> str:
    return str(db.query_value("SELECT fingerprint FROM library_disk_snapshot WHERE day = ? AND disk = ?",
                              (day_, disk), default=""))


with db.connect() as conn:
    for item_id, path, size in (("a", "/media/filmy/cloud/a.mkv", 3 * GB),
                                ("b", "/media/serialy/cloud/b.mkv", 4 * GB),
                                ("x", "/media/filmy/x.mkv", 10 * GB)):
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)", (item_id, item_id, path, size, db.utcnow()))
    conn.commit()
scanner.remember_folders({"Libraries": [{"Name": "Filmy", "Folders": [folder("/media/filmy", "dev-a")]},
                                        {"Name": "Serialy", "Folders": [folder("/media/serialy", "dev-b")]}]})
put_setting(scanner.CONVERTED_KEY, "1")


print("--- A) the list of paths ---")
check(scanner.add_virtual("Cloud", ["/media/filmy/cloud", "/media/serialy/cloud/"], 2 * TB)[0] == "",
      "a disk with two paths can be added")
v = scanner.virtual_disks()[0]
check(v["paths"] == ["/media/filmy/cloud", "/media/serialy/cloud"] and "path" not in v,
      f"the paths normalised, in the order entered ({v})")
r = rows()
virt = f"virt:{v['id']}"
check(r[virt]["library"] == 7 * GB and r["dev-a"]["library"] == 10 * GB and r["dev-b"]["library"] == 0,
      f"the files under both paths belong to the virtual disk, the physical ones do not count them "
      f"({r[virt]['library'] // GB}, {r['dev-a']['library'] // GB}, {r['dev-b']['library'] // GB} GB)")
check(r[virt]["paths"] == v["paths"], "the disk's row carries all the paths")
# The user, 12. 11.: a virtual disk's file must not be counted for the physical disk too.
# Every file in the libraries' folders is in exactly one disk - the sum = the library.
check(sum(x["library"] for x in r.values()) == 17 * GB,
      f"every file in exactly one disk ({sum(x['library'] for x in r.values()) // GB} GB of 17)")

error, _ = scanner.edit_virtual(v["id"], "Cloud", ["/media/filmy/cloud/", "", "  ", "/media/filmy/cloud"], 2 * TB)
check(error == "" and scanner.virtual_disks()[0]["paths"] == ["/media/filmy/cloud"],
      f"empty and doubled fields are thrown away ({error}, {scanner.virtual_disks()[0]['paths']})")
check(scanner.edit_virtual(v["id"], "Cloud", ["", " "], 2 * TB)[0] == "Enter at least one path."
      and scanner.virtual_disks()[0]["paths"] == ["/media/filmy/cloud"],
      "only empty fields: an error and nothing changes")
check(scanner.add_virtual("X", [], TB)[0] == "Enter at least one path.", "without paths nothing is added")
error, values = scanner.edit_virtual(v["id"], "Cloud", ["/media/filmy/cloud", "/jinde"], 2 * TB)
check(error != "" and values == {"path": "/jinde"} and scanner.virtual_disks()[0]["paths"] == ["/media/filmy/cloud"],
      f"one wrong path: the error names it and nothing is saved ({values})")
scanner.add_virtual("Archiv", ["/media/serialy/archiv"], TB)
check(scanner.edit_virtual(v["id"], "Cloud", ["/media/filmy/cloud", "/media/serialy/archiv/x"], TB)[0] != "",
      "a path in another virtual disk does not pass")
check(scanner.edit_virtual(v["id"], "Cloud", ["/media/filmy/cloud", "/media/filmy/cloud/a"], TB)[0] == "",
      "the paths of one disk may overlap")
scanner.write_disk_snapshot(day(1))
scanner.edit_virtual(v["id"], "Cloud", ["/media/filmy/cloud", "/media/serialy/cloud"], TB)
scanner.write_disk_snapshot(day(0))
check(fingerprint(day(1), virt) and fingerprint(day(1), virt) != fingerprint(day(0), virt),
      "the fingerprint changes after the paths change")

put_setting(scanner.VIRTUAL_KEY, [{"id": "old", "name": "Stary", "path": "/media/filmy/cloud", "capacity": TB}])
check(scanner.virtual_disks()[0]["paths"] == ["/media/filmy/cloud"], "an older entry with one path is loaded")
check(scanner.edit_virtual("old", "Stary", "/media/filmy/cloud", 2 * TB)[0] == "", "a string = one path")
stored = json.loads(db.get_setting(scanner.VIRTUAL_KEY, "[]"))[0]
check(stored.get("paths") == ["/media/filmy/cloud"] and "path" not in stored,
      f"after saving only a list of paths ({stored})")


print()
print("--- B) routes with a repeated path field ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
admin.post("/library/disks/virtual/edit", follow_redirects=False,
           data={"id": "old", "name": "Stary", "path": ["/media/filmy/cloud", "/media/serialy/cloud"],
                 "capacity": "2", "unit": "TB"})
db.forget_settings()
check(scanner.virtual_disks()[0]["paths"] == ["/media/filmy/cloud", "/media/serialy/cloud"],
      "editing saves all the paths from the form")
r = admin.post("/library/disks/virtual", follow_redirects=False,
               data={"name": "Dva", "path": ["/media/filmy/a", "/media/serialy/a"], "capacity": "1", "unit": "TB"})
db.forget_settings()
two = next((x for x in scanner.virtual_disks() if x["name"] == "Dva"), {})
check(r.status_code == 303 and two.get("paths") == ["/media/filmy/a", "/media/serialy/a"],
      f"adding with two paths ({two.get('paths')})")
r = reader.post("/library/disks/virtual/edit", follow_redirects=False,
                data={"id": "old", "name": "X", "path": ["/media/filmy/cloud"], "capacity": "1", "unit": "TB"})
check(r.status_code == 403, f"a reader changes nothing ({r.status_code})")


print()
print("--- C) the dialog: a field for every path ---")


def disk_dialog(client: TestClient, disk_id: str | None) -> str:
    """The content of a virtual disk's dialog (by id), or of the dialog for adding (None)."""
    page = client.get("/library").text
    dialogs = re.findall(r'<dialog class="modal" id="(modal-disk-[\w-]+)"(.*?)</dialog>', page, flags=re.S)
    if disk_id is None:
        return next((content for dialog, content in dialogs if dialog == "modal-disk-new"), "")
    return next((content for _dialog, content in dialogs if f'name="id" value="{disk_id}"' in content), "")


dialog = disk_dialog(admin, "old")
check(dialog.count('name="path"') == 2 and 'value="/media/serialy/cloud"' in dialog
      and dialog.count("data-remove-path") == 2 and "data-add-path" in dialog and "data-paths" in dialog,
      "the disk's dialog: a field with a cross for every path and the Add a path button")
# A screen reader: the first field has the label “Cesty” (label for), the other fields
# and all the crosses are numbered, so it can be told which is which.
check(re.search(r'name="path"[^>]*aria-label="Cesta 2"', dialog)
      and 'aria-label="Odebrat cestu 1"' in dialog and 'aria-label="Odebrat cestu 2"' in dialog,
      "the other fields and the crosses have numbered labels")
check(re.search(r'data-paths="[\w-]+-path"', dialog) and 'data-label="Cesta"' in dialog
      and 'data-label-remove="Odebrat cestu"' in dialog,
      "the list of paths carries the first field's id and the labels for the script")
new = disk_dialog(admin, None)
check(new.count('name="path"') == 1 and re.search(r'name="path"[^>]*value=""', new),
      "the dialog for adding: one empty field")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
