# -*- coding: utf-8 -*-
r"""The Disks card: each disk's settings in a dialog, a virtual disk can be edited.

Under every disk there used to hang a row with the paths and the capacity form.
Now the table has only the disk's row and at its end an “Edit” button,
which opens a dialog with that disk's settings (the user, 12. 11. 2026):

A) a virtual disk can be edited after it is created (name, path, capacity) with
   the same rules as when adding and with the history kept;
B) the route for editing is for the administrator only;
C) the card: a button at the end of every row, a dialog for each; the forms
   in the dialogs, not in the table; adding a virtual disk in a dialog too;
   neither a reader nor the custom dashboard has the buttons, the dialogs or the forms.

Run:
    .venv/bin/python tests/test_disk_dialogs.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "okna.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, cards, db, scanner, web  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}


def put_setting(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


def virtual_by_id() -> dict[str, dict[str, Any]]:
    db.forget_settings()
    return {v["id"]: v for v in scanner.virtual_disks()}


with db.connect() as conn:
    for item_id, path, size in (("film", "/media/filmy/a.mkv", 30 * GB),
                                ("dil", "/media/serialy/s01e01.mkv", 50 * GB),
                                ("cloud", "/media/serialy/cloud/s01e02.mkv", 7 * GB)):
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)", (item_id, item_id, path, size, db.utcnow()))
    conn.commit()
scanner.remember_folders({"Libraries": [
    {"Name": "Filmy", "Folders": [folder("/media/filmy", "dev-a", 500 * GB, 500 * GB)]},
    {"Name": "Serialy", "Folders": [folder("/media/serialy", "dev-b", 300 * GB, 700 * GB)]}]})
put_setting(scanner.CONVERTED_KEY, "1")
put_setting(scanner.VIRTUAL_KEY, [{"id": "v1", "name": "Cloud", "path": "/media/serialy/cloud", "capacity": 2 * TB},
                                  {"id": "v2", "name": "Archiv", "path": "/media/filmy/archiv", "capacity": TB}])


print("--- A) editing a virtual disk ---")
scanner.write_disk_snapshot("2026-11-01")
error, _ = scanner.edit_virtual("v1", "Cloud Drive", "/media/serialy/cloud/", 3 * TB)
check(error == "", f"the edit passes ({error})")
v = virtual_by_id()["v1"]
check(v["name"] == "Cloud Drive" and v["paths"] == ["/media/serialy/cloud"] and v["capacity"] == 3 * TB,
      f"a new name, path (without the trailing slash) and capacity ({v})")
check(sorted(virtual_by_id()) == ["v1", "v2"], "the id stays and the second disk does not change")
scanner.write_disk_snapshot("2026-11-02")
check(db.query_value("SELECT COUNT(*) FROM library_disk_snapshot WHERE disk = 'virt:v1'") == 2,
      "the history of snapshots goes on under the same disk")
check(scanner.edit_virtual("v1", "Cloud Drive", "/media/serialy/cloud", 3 * TB)[0] == "",
      "its own path does not count as an overlap")
for arguments, label in (
        (("neni", "X", "/media/serialy/x", TB), "an unknown disk"),
        (("v1", "", "/media/serialy/cloud", TB), "an empty name"),
        (("v1", "Cloud", "", TB), "an empty path"),
        (("v1", "Cloud", "/media/serialy/cloud", 0), "a zero capacity"),
        (("v1", "Cloud", "/jinde/cloud", TB), "a path outside the libraries' folders"),
        (("v1", "Cloud", "/media/filmy/archiv/x", TB), "a path in another virtual disk"),
        (("v1", "Cloud", "/media/filmy", TB), "a path above another virtual disk")):
    error, _ = scanner.edit_virtual(*arguments)
    check(error != "" and virtual_by_id()["v1"]["name"] == "Cloud Drive", f"{label}: an error and nothing changes ({error})")
check(scanner.add_virtual("Třetí", "/media/filmy/archiv/x", TB)[0] != "",
      "adding still has the same rules (an overlap)")


print()
print("--- B) the route for the administrator only ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
fields = {"id": "v1", "name": "Cloud", "path": "/media/serialy/cloud", "capacity": "1,5", "unit": "TB"}
r = reader.post("/library/disks/virtual/edit", data=fields, follow_redirects=False)
check(r.status_code == 403 and virtual_by_id()["v1"]["name"] == "Cloud Drive", f"a reader changes nothing ({r.status_code})")
r = admin.post("/library/disks/virtual/edit", data=fields, follow_redirects=False)
check(r.status_code == 303 and r.headers.get("location") == "/library#disks", "the administrator: saved, back to the card")
check(virtual_by_id()["v1"]["name"] == "Cloud" and virtual_by_id()["v1"]["capacity"] == int(1.5 * TB),
      "the new details hold (1,5 TB with a decimal comma)")
r = admin.post("/library/disks/virtual/edit", data={**fields, "path": "/jinde"}, follow_redirects=True)
check(virtual_by_id()["v1"]["paths"] == ["/media/serialy/cloud"] and "/jinde" in r.text,
      "a wrong path: nothing changes and the message names it")


print()
print("--- C) the card: a button at the end of the row, the settings in a dialog ---")


def card(html: str) -> str:
    return html[html.index('id="disks"'):html.index("Celkem za všechny knihovny")]


page = admin.get("/library").text
k = card(page)
table = k[k.index("<table"):k.index("</table>")]
row_count = len(scanner.disk_state()["disks"])
buttons = re.findall(r'data-modal="(modal-disk-\d+)"', table)
check(len(buttons) == row_count == 4, f"a button on every disk's row ({len(buttons)} of {row_count})")
check(all(f'<dialog class="modal" id="{dialog}"' in k for dialog in buttons), "each button has its dialog")
check("<form" not in table and "colspan" not in table, "the table has no forms and no rows under a disk")
dialogs = dict(re.findall(r'<dialog class="modal" id="(modal-disk-[\w-]+)"(.*?)</dialog>', k, flags=re.S))
physical = [content for content in dialogs.values() if 'action="/library/disks/capacity"' in content]
check(len(physical) == 2 and any("/media/filmy" in o and 'value="dev-a"' in o for o in physical),
      "the dialog of a disk from Jellyfin: the paths and the capacity (reported by Jellyfin / manual)")
cloud = next((o for o in dialogs.values() if 'value="v1"' in o), "")
check('action="/library/disks/virtual/edit"' in cloud and 'value="Cloud"' in cloud
      and 'value="/media/serialy/cloud"' in cloud and 'value="1.5"' in cloud,
      "the virtual disk's dialog: the name, the path and the capacity filled in")
check('action="/library/disks/virtual/delete"' in cloud, "and deleting in it")
check('data-modal="modal-disk-new"' in k and 'action="/library/disks/virtual"' in dialogs.get("modal-disk-new", "")
      and "<details" not in k, "adding a virtual disk is a dialog too")
ids = re.findall(r'\bid="([^"]+)"', page)
check(len(ids) == len(set(ids)), f"the ids on the page do not repeat ({[i for i in ids if ids.count(i) > 1][:3]})")

k = card(reader.get("/library").text)
check("modal-disk" not in k and "<form" not in k and "<dialog" not in k and "/media/" not in k,
      "a reader: without buttons, dialogs, forms and paths")
db.set_setting(cards.ENABLED_KEY, "1")
db.forget_settings()
cards.save_layout([("disks", "full")])
overview = admin.get("/dashboard").text
check('id="disks"' in overview and "modal-disk" not in overview and "<dialog class=\"modal\" id=\"modal-disk" not in overview,
      "in the custom dashboard the card is without buttons and dialogs")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
