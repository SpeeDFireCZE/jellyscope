# -*- coding: utf-8 -*-
r"""The library's disks: physical ones by device id, virtual ones by path.

Jellyscope used to know one free space. When the movies lie on one disk
and the series on another, it was blind; a cloud mount that reports a nonsense
capacity could only be dealt with by a manual capacity for everything at once. The test
watches:

A) finding the disks from Jellyfin 12's answer, assigning the files (a virtual
   disk by path, a physical one by the library's folder), the data outside Jellyfin,
   the suspicion of one and the same disk (Docker volumes) and the administrator's decision,
   converting today's manual capacity, and Jellyfin 10.x without a breakdown of disks.

Spec: docs/superpowers/specs/2026-11-07-disky-knihovny-design.md

Run:
    .venv/bin/python tests/test_disks.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "disky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, scanner  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def item(item_id: str, path: str, size: int, missing: int = 0) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, ?, ?)", (item_id, item_id, path, size, missing, db.utcnow()))


def folder(path: str, device: str, free: int, used: int, kind: str = "Fixed") -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": kind, "DeviceId": device}


def storage(**libraries: list[dict[str, Any]]) -> dict[str, Any]:
    """Jellyfin 12's /System/Info/Storage answer (the shape checked on 12.1.0)."""
    return {"ProgramDataFolder": folder("/config", "/", 5 * GB, 20 * GB),
            "Libraries": [{"Name": name, "Id": name, "Folders": folders} for name, folders in libraries.items()]}


def put_setting(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


def rows() -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in scanner.disk_state()["disks"]}


item("film-a", "/media/filmy/a.mkv", 10 * GB)
item("film-b", "/media/filmy/b.mkv", 20 * GB)
item("film-stary", "/media/filmy/stary.mkv", 100 * GB, missing=1)      # in the archive: does not count
item("ep-1", "/media/serialy/show/s01e01.mkv", 5 * GB)
item("ep-cloud", "/media/serialy/cloud/show/s01e02.mkv", 7 * GB)
item("jinde", "/jinde/z.mkv", 1 * GB)                                # outside all folders
item("podobny", "/media/filmy2/d.mkv", 2 * GB)                      # /media/filmy2 is not /media/filmy

MOVIES = [folder("/media/filmy", "dev-a", 100 * GB, 400 * GB)]
SERIES = [folder("/media/serialy/", "dev-b", 50 * GB, 950 * GB)]

print("--- paths are compared folder by folder ---")
check(scanner.normalize_path("C:\\Media\\Filmy\\") == "c:/media/filmy", "Windows: slashes and letter case")
check(scanner.normalize_path("/media/filmy/") == "/media/filmy", "without the trailing slash")
check(scanner.is_inside("/media/filmy/a.mkv", "/media/filmy"), "a file in the folder")
check(scanner.is_inside("/media/filmy", "/media/filmy/"), "a folder inside itself")
check(not scanner.is_inside("/media/filmy2/d.mkv", "/media/filmy"), "/media/filmy2 is not inside /media/filmy")
check(scanner.is_inside("C:\\Media\\Filmy\\x.mkv", "c:/media/filmy"), "a Windows path in the folder")
check(scanner.is_inside("/cokoliv", "/"), "the root contains everything")

print()
print("--- Jellyfin 10.x: without a breakdown of disks one row as before ---")
state = scanner.disk_state()
check(state["mode"] == "single" and [r["name"] for r in state["disks"]] == ["Library"],
      f"one row “Library” - a key, the card translates it ({state['mode']}, {[r['name'] for r in state['disks']]})")
check(state["disks"][0]["library"] == 45 * GB, f"the library's size as before ({state['disks'][0]['library']})")

print()
print("--- storing the folders from Jellyfin's answer ---")
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES))
stored = json.loads(db.get_setting(scanner.FOLDERS_KEY, "[]"))
check(len(stored) == 2 and {s["device"] for s in stored} == {"dev-a", "dev-b"},
      f"both libraries' folders are stored ({stored})")
scanner.remember_folders({})
scanner.remember_folders({"Libraries": []})
check(len(json.loads(db.get_setting(scanner.FOLDERS_KEY, "[]"))) == 2,
      "an empty answer (an older Jellyfin, an outage) does not delete the previous folders")

print()
print("--- two physical disks ---")
r = rows()
check(sorted(r) == ["dev-a", "dev-b"], f"two disks ({sorted(r)})")
check(r["dev-a"]["libraries"] == ["Filmy"] and r["dev-a"]["library"] == 30 * GB,
      f"movies: 30 GB of files, neither the archive nor a similar folder counts ({r['dev-a']['library'] / GB})")
check(r["dev-a"]["free"] == 100 * GB and r["dev-a"]["total"] == 500 * GB and r["dev-a"]["source"] == "jellyfin",
      "the free space and the capacity by Jellyfin")
check(r["dev-a"]["outside"] == 370 * GB, f"the data outside Jellyfin = used - files ({r['dev-a']['outside'] / GB})")
check(r["dev-b"]["library"] == 12 * GB, f"series: 12 GB ({r['dev-b']['library'] / GB})")
check(scanner.disk_state()["mode"] == "disks", "the by-disk mode")

print()
print("--- a virtual disk (a cloud mount) ---")
put_setting(scanner.VIRTUAL_KEY, [{"id": "v1", "name": "Cloud", "path": "/media/serialy/cloud", "capacity": 2 * TB}])
r = rows()
check(r["virt:v1"]["library"] == 7 * GB and r["virt:v1"]["kind"] == "virtual",
      f"the cloud gets only its own files ({r['virt:v1']['library'] / GB})")
check(r["virt:v1"]["free"] == 2 * TB - 7 * GB and r["virt:v1"]["outside"] is None and r["virt:v1"]["total"] == 2 * TB,
      "free = capacity - files, the data outside Jellyfin is not measured")
check(r["dev-b"]["library"] == 5 * GB, f"the physical disk does not count the cloud's files ({r['dev-b']['library'] / GB})")
check(r["dev-b"]["outside"] == 945 * GB, "and its data outside Jellyfin is not skewed by them")
item("ep-hd", "/media/serialy/cloud/hd/s01e03.mkv", 1 * GB)
put_setting(scanner.VIRTUAL_KEY, [{"id": "v1", "name": "Cloud", "path": "/media/serialy/cloud", "capacity": 2 * TB},
                                  {"id": "v2", "name": "HD", "path": "/media/serialy/cloud/hd/", "capacity": TB}])
r = rows()
check(r["virt:v2"]["library"] == 1 * GB and r["virt:v1"]["library"] == 7 * GB,
      "with nested paths the longer one wins")
put_setting(scanner.VIRTUAL_KEY, [{"id": "v3", "name": "Překlep", "path": "/media/serialy/clodu", "capacity": TB}])
r = rows()
check(r["virt:v3"]["without_file"] and not r["dev-b"]["without_file"], "a virtual disk without files is recognised")
put_setting(scanner.VIRTUAL_KEY, [])

print()
print("--- a physical disk's manual capacity ---")
put_setting(scanner.DISK_CAPACITY_KEY, {"dev-a": {"source": "manual", "capacity": TB}})
r = rows()
check(r["dev-a"]["source"] == "manual" and r["dev-a"]["total"] == TB and r["dev-a"]["free"] == TB - 30 * GB,
      "free = the capacity entered - files")
check(r["dev-a"]["outside"] is None, "the data outside Jellyfin is not measured with a manual capacity")
check(r["dev-b"]["source"] == "jellyfin", "the second disk still by Jellyfin")
put_setting(scanner.DISK_CAPACITY_KEY, {})

print()
print("--- the data outside Jellyfin does not go below zero ---")
scanner.remember_folders(storage(Filmy=[folder("/media/filmy", "dev-a", 100 * GB, 10 * GB)], Serialy=SERIES))
check(rows()["dev-a"]["outside"] == 0, "used smaller than the files: 0, not a negative number")

print()
print("--- Docker volumes: a suspicion, not a merge ---")
MUSIC = [folder("/media/hudba", "dev-c", 100 * GB, 400 * GB)]          # the same numbers as dev-a
OTHER = [folder("/media/jiny", "dev-d", 120 * GB, 380 * GB)]            # the same capacity, other free space
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES, Hudba=MUSIC, Jiny=OTHER))
state = scanner.disk_state()
check(len(state["disks"]) == 4, f"without a decision the disks stay apart ({len(state['disks'])})")
check(state["suspicions"] == [("dev-a", "dev-c")], f"one suspicion: dev-a and dev-c ({state['suspicions']})")
r = rows()
check(r["dev-a"]["suspicions"] == ["dev-c"] and r["dev-c"]["suspicions"] == ["dev-a"] and not r["dev-d"]["suspicions"],
      "both disks show whom they may match; other free space has no suspicion")
put_setting(scanner.SAME_KEY, [["dev-a", "dev-c"]])
state = scanner.disk_state()
r = rows()
check(len(state["disks"]) == 3 and r["dev-a"]["libraries"] == ["Filmy", "Hudba"] and not state["suspicions"],
      f"after “Yes” one disk with both libraries ({[x['libraries'] for x in state['disks']]})")
put_setting(scanner.SAME_KEY, [])
put_setting(scanner.DIFFERENT_KEY, [["dev-c", "dev-a"]])
state = scanner.disk_state()
check(len(state["disks"]) == 4 and not state["suspicions"], "after “No” two disks and the suspicion goes away")
put_setting(scanner.DIFFERENT_KEY, [])
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES))

print()
print("--- a bad setting brings nothing down ---")
for key in (scanner.DISK_CAPACITY_KEY, scanner.SAME_KEY, scanner.DIFFERENT_KEY, scanner.VIRTUAL_KEY):
    put_setting(key, "{nesmysl")
    check(len(scanner.disk_state()["disks"]) == 2, f"bad JSON in {key} is taken as empty")
    put_setting(key, "")

print()
print("--- converting today's manual capacity ---")
put_setting(scanner.CONVERTED_KEY, "")
put_setting(scanner.DISK_CAPACITY_KEY, {})
db.set_setting(scanner.CAPACITY_KEY, str(2 * TB))
db.forget_settings()
check(scanner.convert_old_capacity() is True, "the conversion ran")
check(json.loads(db.get_setting(scanner.DISK_CAPACITY_KEY, "{}")) == {"dev-a": {"source": "manual", "capacity": 2 * TB}},
      f"the capacity is with the disk with the largest file ({db.get_setting(scanner.DISK_CAPACITY_KEY, '')})")
check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 0, "and the old value was moved over, not doubled")
check(scanner.convert_old_capacity() is False, "the second time nothing")
check(json.loads(db.get_setting(scanner.DISK_CAPACITY_KEY, "{}"))["dev-a"]["capacity"] == 2 * TB, "and changes nothing")
put_setting(scanner.FOLDERS_KEY, "")
put_setting(scanner.CONVERTED_KEY, "")
db.set_setting(scanner.CAPACITY_KEY, str(TB))
db.forget_settings()
check(scanner.convert_old_capacity() is False and db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == TB,
      "without a breakdown of disks (Jellyfin 10.x) nothing is converted and the manual capacity stays")


print()
print("--- B) disk snapshots, the pace and the forecast ---")
from datetime import datetime, timedelta  # noqa: E402

from jellyscope import formatting, notifications, stats  # noqa: E402

TODAY = datetime.now(formatting.zone()).date()
TEN_DAYS_AGO = (TODAY - timedelta(days=10)).isoformat()


def write_library_snapshot(day: str, size: int) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr, without_tech,"
                     " free_space, written_at) VALUES (?, 1, 1, 0, ?, 0, 0, 0, NULL, ?)"
                     " ON CONFLICT (day) DO UPDATE SET size_bytes = excluded.size_bytes", (day, size, db.utcnow()))


put_setting(scanner.FOLDERS_KEY, "")
put_setting(scanner.DISK_CAPACITY_KEY, {})
put_setting(scanner.CONVERTED_KEY, "1")
db.set_setting(scanner.CAPACITY_KEY, "0")
put_setting(scanner.VIRTUAL_KEY, [{"id": "v1", "name": "Cloud", "path": "/media/serialy/cloud", "capacity": 2 * TB}])
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES))
check(scanner.write_disk_snapshot(TEN_DAYS_AGO) == 3, "a snapshot: a row for every disk (two physical, one virtual)")
scanner.write_disk_snapshot(TEN_DAYS_AGO)
check(db.query_value("SELECT COUNT(*) FROM library_disk_snapshot WHERE day = ?", (TEN_DAYS_AGO,)) == 3,
      "the same day is overwritten, not added")
check("library_disk_snapshot" in db.TABLES, "the table is in the export and the database conversion")

# Ten days later: the movies lost 10 GB of free space, the cloud gained 10 GB, the series stand still.
scanner.remember_folders(storage(Filmy=[folder("/media/filmy", "dev-a", 90 * GB, 410 * GB)], Serialy=SERIES))
item("ep-cloud-2", "/media/serialy/cloud/show/s01e04.mkv", 10 * GB)
scanner.write_disk_snapshot(TODAY.isoformat())
write_library_snapshot(TEN_DAYS_AGO, 45 * GB)
write_library_snapshot(TODAY.isoformat(), 55 * GB)

overview = stats.disks_overview(90)
r = {x["key"]: x for x in overview["disks"]}
check(r["dev-a"]["daily"] == GB and r["dev-a"]["days_left"] == 90,
      f"Jellyfin: the pace from the shrinking free space ({r['dev-a']['daily'] / GB} GB/day, {r['dev-a']['days_left']} days)")
check(r["virt:v1"]["daily"] == GB and r["virt:v1"]["days_left"] == int((2 * TB - 18 * GB) / GB),
      f"a virtual disk: the pace from the files' growth ({r['virt:v1']['days_left']} days)")
check(r["dev-b"]["days_left"] is None, "a disk standing still has no forecast")
check(overview["earliest"]["key"] == "dev-a", "the disk with the movies runs out first")
outside = {x["day"]: x for x in stats.outside_series(90)}
check(outside[TEN_DAYS_AGO]["outside"] == (370 + 945) * GB and outside[TEN_DAYS_AGO]["library"] == 35 * GB,
      f"the chart's series: the data outside Jellyfin and the measured disks' files ({outside[TEN_DAYS_AGO]})")

first = stats.runs_out_first(90)
check(first is not None and first["key"] == "dev-a" and first["days_left"] == 90 and first["name"] == "Filmy",
      f"the forecast only from the snapshots (without walking the files) ({first})")
# Since 23. 11. 2026 Library growth "All" counts the whole storage and names the disk that runs out first in a
# tile of its own; the space alert (space_forecast) still takes that disk.
growth = stats.space_forecast(90)
check(growth["days_left"] == 90 and growth["free_space"] == 90 * GB and growth.get("disk_name") == "Filmy",
      f"the space alert takes the disk that runs out first ({growth.get('days_left')}, {growth.get('disk_name')})")
all_disks = stats.library_growth(90)
check((all_disks.get("fills_first") or {}).get("name") == "Filmy" and all_disks["fills_first"]["days_left"] == 90,
      f"Library growth “All” names it in the tile “Fills first” ({all_disks.get('fills_first')})")

db.set_setting("notify_space_days", "100")
db.forget_settings()
report, text = notifications._space_running_out()
check(report and "Filmy" in text and "90" in text, f"the notification names the disk ({report}, {text})")

# A custom wording of the message with variables (in English: the text holds in every
# interface language). Sending is caught instead of the network.
import asyncio  # noqa: E402

sent: list[tuple[str, str]] = []


async def capture(subject: str, message_text: str) -> None:
    sent.append((subject, message_text))


original_send, original_channels = notifications.send, notifications.enabled_channels
notifications.send, notifications.enabled_channels = capture, lambda: ["ntfy"]
db.set_setting(notifications.event_key("collector"), "0")
db.set_setting(notifications.event_key("space"), "1")
db.set_setting(notifications.text_key("space_low", "subject"), "Disk {disk}: {days} days")
db.set_setting(notifications.text_key("space_low", "body"),
               "Free {free}, {per_day} a day, threshold {threshold}. {detail}")
db.set_setting(notifications.text_key("space_ok", "body"), "OK {disk}: {days} days, free {free}")
db.forget_settings()
asyncio.run(notifications.check())
check(sent == [("Disk Filmy: 90 days",
                f"Free {formatting.bytes_human(90 * GB)}, {formatting.bytes_human(GB)} a day, threshold 100. {text}")],
      f"the variables {{disk}} {{days}} {{free}} {{per_day}} {{threshold}} {{detail}} are filled in ({sent})")
sent.clear()
db.set_setting("notify_space_days", "10")
db.forget_settings()
asyncio.run(notifications.check())
check(sent and sent[0][1] == f"OK Filmy: 90 days, free {formatting.bytes_human(90 * GB)}",
      f"the message that there is enough space again too ({sent})")
notifications.send, notifications.enabled_channels = original_send, original_channels
for part in ("predmet", "text"):
    for message in ("space_low", "space_ok"):
        db.set_setting(notifications.text_key(message, part), "")
db.set_setting("notify_space_days", "100")
db.set_setting(notifications.event_key("space"), "")
db.forget_settings()

put_setting(scanner.VIRTUAL_KEY, [])
overview = stats.disks_overview(90)
check(all(x["key"] != "virt:v1" for x in overview["disks"]) and stats.runs_out_first(90)["key"] == "dev-a",
      "a deleted virtual disk disappears from the overview and the old snapshots bring nothing down")
check(db.query_value("SELECT COUNT(*) FROM library_disk_snapshot WHERE disk = 'virt:v1'") == 2,
      "its snapshots stay in the history")


print()
print("--- C) setting the disks up from the card (the administrator only) ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, web  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
put_setting(scanner.VIRTUAL_KEY, [])
put_setting(scanner.DISK_CAPACITY_KEY, {})


def send(client: TestClient, address: str, **fields: str) -> Any:
    return client.post(address, data=fields, follow_redirects=False)


def setting(key: str) -> Any:
    db.forget_settings()
    return json.loads(db.get_setting(key, "") or "null")


r = send(admin, "/library/disks/capacity", disk="dev-a", source="manual", capacity="1,5", unit="TB")
check(r.status_code == 303 and r.headers.get("location") == "/library#disks", "after saving back to the card")
check(setting(scanner.DISK_CAPACITY_KEY) == {"dev-a": {"source": "manual", "capacity": int(1.5 * TB)}},
      "a manual capacity is saved (1,5 TB, a decimal comma)")
today_disk = db.query_one("SELECT source, total FROM library_disk_snapshot WHERE day = ? AND disk = 'dev-a'",
                          (TODAY.isoformat(),))
check(today_disk and today_disk["source"] == "manual" and int(today_disk["total"]) == int(1.5 * TB),
      "and today's snapshot is overwritten at once - the forecast holds without waiting for a sync")
# A tab left open across the update to 1.9.0 sends the old word of a manual capacity: nothing changes (the
# capacity is not switched back to Jellyfin quietly), the page says to reload.
stale = send(admin, "/library/disks/capacity", disk="dev-a", source="rucne", capacity="3", unit="TB")
check(setting(scanner.DISK_CAPACITY_KEY) == {"dev-a": {"source": "manual", "capacity": int(1.5 * TB)}}
      and "F5" in admin.get(stale.headers.get("location", "/library"), follow_redirects=True).text,
      "an old word of the source from an older page saves nothing and says to reload")
send(admin, "/library/disks/capacity", disk="dev-a", source="jellyfin")
check(setting(scanner.DISK_CAPACITY_KEY) == {}, "switching back to Jellyfin removes the manual capacity")
for fields, label in (({"disk": "neni", "source": "manual", "capacity": "1", "unit": "TB"}, "an unknown disk"),
                      ({"disk": "dev-a", "source": "manual", "capacity": "0", "unit": "TB"}, "a zero capacity"),
                      ({"disk": "dev-a", "source": "manual", "capacity": "hodně", "unit": "TB"}, "a non-numeric capacity")):
    send(admin, "/library/disks/capacity", **fields)
    check(setting(scanner.DISK_CAPACITY_KEY) == {}, f"{label}: nothing is saved")

send(admin, "/library/disks/virtual", name="Cloud", path="/media/serialy/cloud/", capacity="2", unit="TB")
virtual = scanner.virtual_disks()
check(len(virtual) == 1 and virtual[0]["paths"] == ["/media/serialy/cloud"] and virtual[0]["capacity"] == 2 * TB,
      f"a virtual disk is added ({virtual})")
for fields, label in (({"name": "Mimo", "path": "/jinde", "capacity": "1", "unit": "TB"}, "a path outside the libraries' folders"),
                      ({"name": "Uvnitř", "path": "/media/serialy/cloud/hd", "capacity": "1", "unit": "TB"},
                       "a path inside another virtual disk"),
                      ({"name": "Nad", "path": "/media/serialy", "capacity": "1", "unit": "TB"},
                       "a path under which another virtual disk lies"),
                      ({"name": "Nula", "path": "/media/filmy", "capacity": "0", "unit": "GB"}, "a zero capacity"),
                      ({"name": "  ", "path": "/media/filmy", "capacity": "1", "unit": "TB"}, "an empty name")):
    send(admin, "/library/disks/virtual", **fields)
    check(len(scanner.virtual_disks()) == 1, f"{label}: refused")
send(admin, "/library/disks/virtual", name="Filmy v cloudu", path="/media/filmy/cloud", capacity="500",
     unit="GB")
check(len(scanner.virtual_disks()) == 2, "a second virtual disk that does not overlap passes")
send(admin, "/library/disks/virtual/delete", id=virtual[0]["id"])
check([v["name"] for v in scanner.virtual_disks()] == ["Filmy v cloudu"], "only that one is deleted")
put_setting(scanner.VIRTUAL_KEY, [])

scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES, Hudba=MUSIC, Jiny=OTHER))
send(admin, "/library/disks/same", a="dev-a", b="dev-d", answer="yes")
check(not setting(scanner.SAME_KEY), "a pair that is not under suspicion cannot be merged")
send(admin, "/library/disks/same", a="dev-c", b="dev-a", answer="yes")
check(setting(scanner.SAME_KEY) == [["dev-a", "dev-c"]] and len(scanner.disk_state()["disks"]) == 3,
      "“Yes” merges the disks")
put_setting(scanner.SAME_KEY, [])
send(admin, "/library/disks/same", a="dev-a", b="dev-c", answer="no")
check(setting(scanner.DIFFERENT_KEY) == [["dev-a", "dev-c"]] and not scanner.disk_state()["suspicions"],
      "“No” is remembered and the suspicion goes away")
put_setting(scanner.DIFFERENT_KEY, [])
put_setting(scanner.SAME_KEY, [["dev-a", "dev-c"], ["dev-c", "dev-e"]])
check(scanner.physical_disks()[0]["device"] == ["dev-a", "dev-c"] and
      {d["key"] for d in scanner.physical_disks()} == {"dev-a", "dev-b", "dev-d"},
      "groups with a shared disk join into one")
put_setting(scanner.SAME_KEY, [])
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES))

for address, fields in (("/library/disks/capacity", {"disk": "dev-a", "source": "manual", "capacity": "1", "unit": "TB"}),
                        ("/library/disks/virtual", {"name": "X", "path": "/media/filmy", "capacity": "1", "unit": "TB"}),
                        ("/library/disks/virtual/delete", {"id": "x"}),
                        ("/library/disks/same", {"a": "dev-a", "b": "dev-c", "answer": "yes"})):
    check(send(reader, address, **fields).status_code == 403, f"reader: {address} → 403")
check(setting(scanner.DISK_CAPACITY_KEY) == {} and not scanner.virtual_disks(), "and nothing changed")


print()
print("--- D) the Disks card, the chart and Settings ---")
from jellyscope import cards  # noqa: E402

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at) VALUES ('lib-f', 'Filmy', 'movies', ?)",
                 (db.utcnow(),))
put_setting(scanner.VIRTUAL_KEY, [{"id": "v9", "name": "Prázdný cloud", "path": "/media/filmy/nic", "capacity": TB}])
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES, Hudba=MUSIC))
scanner.write_disk_snapshot()


def card(html: str) -> str:
    return html[html.index('id="disks"'):html.index("Celkem za všechny knihovny")]


page = admin.get("/library").text
check('id="disks"' in page and page.index("Souborů") < page.index('id="disks"')
      < page.index("Celkem za všechny knihovny"),
      "the card is under the libraries' cards and above their statistics")
k = card(page)
check("Filmy" in k and "Serialy" in k, "a row per disk with the libraries")
check("/media/filmy" in k and 'action="/library/disks/capacity"' in k and 'action="/library/disks/virtual"' in k,
      "the administrator sees the paths and the forms")
check("Prázdný cloud" in k and "nesedí žádný soubor" in k, "a virtual disk without files has a warning")
check('data-modal="modal-same-disk-1"' in k and '<dialog class="modal" id="modal-same-disk-1"' in page
      and re.search(r'<form[^>]*action="/library/disks/same"(?:(?!</form>).)*name="answer" value="yes"'
                    r'(?:(?!</form>).)*name="answer" value="no"', page, re.S) is not None,
      "a suspicion opens a dialog with the choice Yes / No")
check("mimo Jellyfin" in page[page.index("Celkem za všechny knihovny"):],
      "the Library growth chart has a series of the data outside Jellyfin")
k = card(reader.get("/library").text)
check("Filmy" in k and "/media/" not in k and "<form" not in k and "<dialog" not in k,
      "a reader sees the numbers and the names, not the paths or the forms")

data = admin.get("/settings?section=data").text
check('name="library_capacity_gb"' not in data and 'href="/library#disks"' in data,
      "Settings → Data: a link to the Disks card instead of the capacity field")
put_setting(scanner.DISK_CAPACITY_KEY, {"dev-a": {"source": "manual", "capacity": TB}})
r = admin.post("/settings", data={"tech_source": "jellyfin", "poll_interval": "10", "ffprobe_path": "",
                                  "ffprobe_concurrency": "3", "path_mappings": "[]"}, follow_redirects=False)
check(r.status_code == 303 and setting(scanner.DISK_CAPACITY_KEY) == {"dev-a": {"source": "manual", "capacity": TB}},
      "saving Settings → Data does not delete a disk's manual capacity")
put_setting(scanner.DISK_CAPACITY_KEY, {})

put_setting(scanner.FOLDERS_KEY, "")
db.set_setting(scanner.CAPACITY_KEY, str(3 * TB))
db.forget_settings()
check("Knihovna" in card(admin.get("/library").text), "Jellyfin 10.x: one row “Knihovna” in the card")
data = admin.get("/settings?section=data").text
check('name="library_capacity_gb"' in data, "and the manual capacity stays in Settings → Data as before")
admin.post("/settings", data={"tech_source": "jellyfin", "poll_interval": "10", "ffprobe_path": "",
                              "ffprobe_concurrency": "3", "path_mappings": "[]"})
check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 3 * TB,
      "saving without the capacity field does not delete it")
admin.post("/settings", data={"tech_source": "jellyfin", "poll_interval": "10", "ffprobe_path": "",
                              "ffprobe_concurrency": "3", "path_mappings": "[]",
                              "library_capacity_gb": "4", "library_capacity_unit": "TB"})
check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 4 * TB, "with the field it saves it as before")
scanner.remember_folders(storage(Filmy=MOVIES, Serialy=SERIES, Hudba=MUSIC))

notifications_page = admin.get("/settings?section=notifications").text
import re  # noqa: E402


def variables_dialog(message: str) -> str:
    """The content of one message's dialog with variables ("" when there is no dialog)."""
    m = re.search(r'<dialog class="modal[^"]*" id="modal-variables-' + message + r'"(.*?)</dialog>', notifications_page, flags=re.S)
    return m.group(1) if m else ""


space, collector = variables_dialog("space_low"), variables_dialog("collector_down")
check('data-modal="modal-variables-space_low"' in notifications_page and 'data-modal="modal-variables-collector_down"' in notifications_page,
      "a Variables button by every message that has some")
check(all("{" + p + "}" in space for p in ("detail", "disk", "days", "free", "per_day", "threshold"))
      and "{minutes}" not in space, "the dialog of the message Space running out: only its variables")
check(all("{" + p + "}" in collector for p in ("detail", "minutes", "threshold", "error"))
      and "{disk}" not in collector and "{free}" not in collector, "the dialog of the message Collector not collecting: only its variables")
check('class="modal-body"' in space and 'class="modal-body"' in collector, "the dialog's content can scroll")
fields = notifications_page[notifications_page.index('name="text_space_low_text"'):][:1500]
check("<code>{days}</code>" in fields and "<code>{per_day}</code>" in fields,
      "the message Space running out shows its variables")
label = notifications_page[notifications_page.index("Dochází místo na disku"):][:900]
check("dojde nejdřív" in label and "Knihovna → Disky" in label and "dopočítá z ní" not in label,
      "Settings → Notifications: the description of watching the space speaks of disks and the Disks card, not of the former single capacity")

check(any(s.key == "disks" and s.area == "library" for s in cards.ALL_CARDS), "the card is in the registry of cards")
db.set_setting(cards.ENABLED_KEY, "1")
db.forget_settings()
cards.save_layout([("disks", "full")])
overview = admin.get("/dashboard").text
check('id="disks"' in overview and 'action="/library/disks/capacity"' not in overview,
      "in the custom dashboard the card shows without the forms")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
