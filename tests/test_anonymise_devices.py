# -*- coding: utf-8 -*-
r"""Anonymising the names of devices.

Devices in Jellyfin are often named after people ("Petr's Shield",
"Jana's iPhone"). With hiding names and addresses switched on, the names
of other people's devices must go too - otherwise the pseudonym "Divák 3"
would be given away by the next column. The player (Jellyfin Web,
Android TV...) stays. Watched here:

* somebody else's device = a stable nickname "Zařízení N", one's own stays;
* the pages (history) and the API (history, the filter `device[...]`);
* without hiding names nothing changes.

Run:
    .venv/bin/python tests/test_anonymise_devices.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zarizeni.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    conn.execute("INSERT INTO items (id, name, type, library_id, tech_source, is_missing)"
                 " VALUES ('i1', 'Duna', 'Movie', 'lib1', 'jellyfin', 0)")
    for k, (who, name, device, player) in enumerate((
            ("u1", "Jana", "Janin iPhone", "Jellyfin iOS"),
            ("u2", "Petr", "Petruv Shield", "Android TV"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client, device_name, remote_address)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, 3600, 0, 0, 0, 'DirectPlay',"
            " ?, ?, '203.0.113.7')", (f"s{k}", who, name, when, when, player, device))
    conn.commit()

print("--- the rule ---")
rows = access.anonymise([
    {"user_id": "u1", "user_name": "Jana", "device_name": "Janin iPhone", "client": "Jellyfin iOS"},
    {"user_id": "u2", "user_name": "Petr", "device_name": "Petruv Shield", "client": "Android TV"},
], "u1")
check(rows[0]["device_name"] == "Janin iPhone", "one's own device stays")
check(rows[1]["device_name"].startswith("Zařízení ") and rows[1]["client"] == "Android TV",
      f"somebody else's device has a nickname, the player stays ({rows[1]['device_name']})")
again = access.anonymise([{"user_id": "u2", "device_name": "Petruv Shield"}], "u1")
check(again[0]["device_name"] == rows[1]["device_name"], "the nickname is stable")
summary = access.anonymise([{"label": "Petruv Shield", "device_name": "Petruv Shield",
                             "user_id": "u2", "hours": 1.0}], "u1")
check(summary[0]["label"] == summary[0]["device_name"] != "Petruv Shield"
      and summary[0]["label"].startswith("Zařízení "),
      f"a summary by device (a viewer's detail): the label is the device's nickname, not „Divák“ ({summary[0]['label']})")

# Device numbers NOT alphabetical: whoever knows the devices' names (the family) would
# match them back by the order. And a new device must not shift the numbers of the
# older ones - they are counted by the first appearance.
before_new = access.anonymise([{"user_id": "u2", "device_name": "Petruv Shield"},
                               {"user_id": "u2", "device_name": "Janin iPhone"}], "u9")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method, client, device_name)"
        " VALUES ('s9', 'u2', 'Petr', 'i1', 'Duna', 'Movie', ?, ?, 60, 0, 0, 0,"
        " 'DirectPlay', 'Web', 'Aaa novy')", (when, when))
    conn.commit()
access.forget_devices()
after_new = access.anonymise([{"user_id": "u2", "device_name": "Petruv Shield"},
                              {"user_id": "u2", "device_name": "Janin iPhone"},
                              {"user_id": "u2", "device_name": "Aaa novy"}], "u9")
check([r["device_name"] for r in after_new[:2]] == [r["device_name"] for r in before_new]
      and after_new[2]["device_name"].endswith(" 3"),
      f"a new device gets the next number, the older ones do not move ({[r['device_name'] for r in after_new]})")

# The API's history is anonymised in batches - the order of the devices must not be
# read again for each one (it searches the whole table of plays).
calls = []
original = access._device_order
access._device_order = lambda: calls.append(1) or original()
access.forget_devices()
for _ in range(5):
    access.anonymise([{"user_id": "u2", "device_name": "Petruv Shield"}], "u9")
access._device_order = original
check(len(calls) == 1, f"the order of the devices is read once, not for each batch ({len(calls)}×)")

print("--- the pages ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass")
db.set_setting("reader_anonymise", "1")
db.forget_settings()


def sign_in(name: str, password: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


reader = sign_in("reader", "readerpass")
history = reader.get("/history").text
check("Petruv Shield" not in history and "Janin iPhone" not in history
      and "Zařízení " in history, "a reader with hidden names does not see the devices' names")
check("Android TV" in history, "the player is still seen")
detail = reader.get("/users/" + access.anonymise([{"user_id": "u2"}], "")[0]["user_id"])
check("Petruv Shield" not in detail.text, "not in a viewer's detail either")
# The API guide on My account describes the fields (device: text) - that is documentation,
# not data about viewers; anonymising must not touch it.
db.set_setting("reader_may_have_key", "1")
db.forget_settings()
account = reader.get("/account").text
check('<code>device</code>&nbsp;<span class="api-type">text</span>' in account
      and '<code>user_id</code>&nbsp;<span class="api-type">text</span>' in account,
      "the API guide on My account shows the fields' types with hidden names too")

# The internal JSON /api/now-playing (outside the API with keys): anonymised only too.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method, client, device_name, remote_address)"
        " VALUES ('zive', 'u2', 'Petr', 'i1', 'Duna', 'Movie', ?, ?, 60, 0, 0, 1,"
        " 'DirectPlay', 'Android TV', 'Petruv Shield', '203.0.113.7')",
        (datetime.now(timezone.utc).strftime(db.TIME_FORMAT),) * 2)
    conn.commit()
live = reader.get("/api/now-playing").text
check("Petruv Shield" not in live and "203.0.113.7" not in live and '"Petr"' not in live,
      "the internal /api/now-playing for a reader with hidden names: no real name, device or IP")
admin = sign_in("admin", "longpassword")
check("Petruv Shield" in admin.get("/history").text, "the administrator without hiding sees the real names")

print("--- API ---")
key = api.create_token("Widget", None, api.pack_key_permissions([], True, True))["token"]
H = {"Authorization": f"Bearer {key}"}
anonymous = TestClient(app)
records = anonymous.get("/api/v1/history", headers=H).json().get("playbacks", [])
devices = {z["device"] for z in records}
check(records and not devices & {"Petruv Shield", "Janin iPhone"}
      and all(z.startswith("Zařízení ") for z in devices),
      f"API history: the devices' nicknames ({devices})")
check(anonymous.get("/api/v1/history?device[contains]=Petr", headers=H).json().get("playbacks") == [],
      "the filter does not find a device's real name")
one = sorted(devices)[0] if devices else "x"
check(len(anonymous.get(f"/api/v1/history?device[equals]={one}", headers=H).json().get("playbacks", [])) == 1,
      "the filter by the nickname works")
open_key = api.create_token("Grafana")["token"]
check({z["device"] for z in anonymous.get("/api/v1/history", headers={
      "Authorization": f"Bearer {open_key}"}).json()["playbacks"]} == {"Petruv Shield", "Janin iPhone",
                                                                     "Aaa novy"},
      "a key without hiding names sees the real names")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
