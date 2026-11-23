# -*- coding: utf-8 -*-
r"""The library's disks: the minor findings from the final review (12. 11. 2026).

Each part watches one of them:

 1) today's row of a deleted virtual disk does not stay in the snapshot until midnight;
 2) “space runs out in” in Library growth does not contradict the card, and Jellyfin 10.x
    counts the forecast as before (from the library's growth);
 3) an unresolved suspicion of one and the same disk is admitted by the card: the data outside Jellyfin
    and the forecast may be overstated;
 4) the card and the chart say that the data outside Jellyfin includes the file system's
    reserve too;
 5) a virtual disk can be entered above several libraries' folders too;
 6) Windows network paths (\\NAS\...) are compared regardless of letter case;
 7) old details about disks (Jellyfin has not sent them for long) are not passed off as today's;
 8) the answer “the same / different disks” can be taken back;
 9) the “network” label only for network storage;
10) the card does not hold things up: assigning files to disks is fast even for a large library;
11) an old manual capacity is neither lost nor left hidden, and two concurrent
    writes of the disks' settings do not overwrite each other.

Run:
    .venv/bin/python tests/test_disk_details.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "drobnosti.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from _package import substitute  # noqa: E402
from jellyscope import accounts, db, formatting, scanner, stats, web  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
TODAY = datetime.now(formatting.zone()).date()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def day(ago: int) -> str:
    return (TODAY - timedelta(days=ago)).isoformat()


def folder(path: str, device: str, free: int, used: int, kind: str = "Fixed") -> dict[str, Any]:
    return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": kind, "DeviceId": device}


def jellyfin(**libraries: list[dict[str, Any]]) -> None:
    scanner.remember_folders({"Libraries": [{"Name": name, "Folders": folders}
                                            for name, folders in libraries.items()]})
    db.forget_settings()


def put_setting(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


def put_items(*rows: tuple[str, str, int]) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM items")
        for item_id, path, size in rows:
            conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                         " VALUES (?, ?, 'Movie', ?, ?, 0, ?)", (item_id, item_id, path, size, db.utcnow()))
        conn.commit()


def write_library_snapshot(day_: str, size: int) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr, without_tech,"
                     " free_space, written_at) VALUES (?, 1, 1, 0, ?, 0, 0, 0, NULL, ?)"
                     " ON CONFLICT (day) DO UPDATE SET size_bytes = excluded.size_bytes", (day_, size, db.utcnow()))
        conn.commit()


def clean_state() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM library_disk_snapshot")
        conn.execute("DELETE FROM library_snapshot")
        conn.commit()
    for key in (scanner.FOLDERS_KEY, scanner.FOLDERS_TIME_KEY, scanner.DISK_CAPACITY_KEY, scanner.SAME_KEY,
                scanner.DIFFERENT_KEY, scanner.VIRTUAL_KEY, scanner.free_space.JF_FREE_KEY):
        put_setting(key, "")
    put_setting(scanner.CONVERTED_KEY, "1")
    put_setting(scanner.CAPACITY_KEY, "0")


def rows() -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in scanner.disk_state()["disks"]}


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")


def card(client: TestClient) -> str:
    page = client.get("/library").text
    return page[page.index('id="disks"'):page.index("Celkem za všechny knihovny")]


MOVIES = [folder("/media/filmy", "dev-a", 500 * GB, 500 * GB)]
SERIES = [folder("/media/serialy", "dev-b", 300 * GB, 700 * GB)]
put_items(("film", "/media/filmy/a.mkv", 30 * GB), ("dil", "/media/serialy/s01e01.mkv", 50 * GB))


print("--- 1) a deleted virtual disk disappears from today's snapshot too ---")
clean_state()
jellyfin(Filmy=MOVIES, Serialy=SERIES)
scanner.add_virtual("Cloud", "/media/serialy/cloud", TB)
scanner.write_disk_snapshot(day(3))
scanner.write_disk_snapshot(day(0))
virt = scanner.virtual_disks()[0]["id"]
scanner.delete_virtual(virt)
scanner.write_disk_snapshot(day(0))
today_disks = {r["disk"] for r in db.query_all("SELECT disk FROM library_disk_snapshot WHERE day = ?", (day(0),))}
check(f"virt:{virt}" not in today_disks and today_disks == {"dev-a", "dev-b"},
      f"today's snapshot has only the disks that exist ({today_disks})")
check(db.query_value("SELECT COUNT(*) FROM library_disk_snapshot WHERE disk = ?", (f"virt:{virt}",)) == 1,
      "an older snapshot of the deleted disk stays in the history")


print()
print("--- 2) Library growth does not contradict the card; Jellyfin 10.x as before ---")
clean_state()
put_items(("film", "/media/filmy/a.mkv", 30 * GB))
jellyfin(Filmy=MOVIES)
scanner.write_disk_snapshot(day(10))
jellyfin(Filmy=[folder("/media/filmy", "dev-a", 600 * GB, 400 * GB)])    # space was added: the disk is not filling
scanner.write_disk_snapshot(day(0))
write_library_snapshot(day(10), 60 * GB)
write_library_snapshot(day(0), 80 * GB)                                    # but the library grows
check(stats.disks_overview(90)["disks"][0]["days_left"] is None, "the card: the disk is not filling, it has no forecast")
growth = stats.library_growth(90)
check(growth.get("days_left") is None,
      f"Library growth does not add a forecast of its own that would contradict the card ({growth.get('days_left')})")

clean_state()                                                              # Jellyfin 10.x: without folders
put_items(("film", "/media/filmy/a.mkv", 30 * GB), ("dil", "/media/serialy/s01e01.mkv", 50 * GB))
put_setting(scanner.CAPACITY_KEY, str(TB))
for day_, free in ((day(10), 900 * GB), (day(0), 500 * GB)):               # the free space falls fast
    with db.connect() as conn:
        conn.execute("INSERT INTO library_disk_snapshot (day, disk, name, kind, source, total, free, library,"
                     " outside, fingerprint, written_at) VALUES (?, 'library', 'Knihovna', 'single', 'jellyfin', ?, ?, ?,"
                     " NULL, 'x', ?)", (day_, TB, free, 80 * GB, db.utcnow()))
        conn.commit()
write_library_snapshot(day(10), 60 * GB)
write_library_snapshot(day(0), 80 * GB)
growth = stats.library_growth(90)
expected = int((TB - 80 * GB) / (2 * GB))                                  # from the library's growth: 20 GB in 10 days
check(growth.get("days_left") == expected,
      f"10.x: the forecast from the library's growth as before ({growth.get('days_left')}, expected {expected})")
single = stats.disks_overview(90, stats.library_growth)["disks"][0]
check(single["days_left"] == growth.get("days_left"), f"and the card shows the same ({single['days_left']})")


print()
print("--- 3) an unresolved suspicion is admitted by the card ---")
clean_state()
jellyfin(Filmy=MOVIES, Hudba=[folder("/media/hudba", "dev-c", 500 * GB, 500 * GB)])
k = card(reader)
check("můžou být nadsazené" in k, "a reader sees that the data outside Jellyfin and the forecast may be overstated")
scanner.decide_same("dev-a", "dev-c", False)
check("můžou být nadsazené" not in card(reader), "after the administrator's answer the warning goes away")


print()
print("--- 4) the file system's reserve ---")
scanner.write_disk_snapshot(day(0))                                        # the chart draws from the snapshots
write_library_snapshot(day(1), 70 * GB)
write_library_snapshot(day(0), 80 * GB)
check("rezerv" in card(reader), "the card explains that outside Jellyfin is the file system's reserve too")
check("rezerv" in reader.get("/library").text.split("Celkem za všechny knihovny", 1)[1],
      "the Library growth chart too")


print()
print("--- 5) a virtual disk above several libraries' folders ---")
clean_state()
CLOUD = [folder("/media/cloud/filmy", "dev-x", 10 * GB, 90 * GB)]
jellyfin(Filmy=CLOUD, Serialy=[folder("/media/cloud/serialy", "dev-x", 10 * GB, 90 * GB)])
put_items(("f", "/media/cloud/filmy/a.mkv", 3 * GB), ("s", "/media/cloud/serialy/b.mkv", 4 * GB))
error, _ = scanner.add_virtual("Cloud", "/media/cloud", 2 * TB)
check(error == "", f"a path above both folders passes ({error})")
virt = f"virt:{scanner.virtual_disks()[0]['id']}" if scanner.virtual_disks() else ""
check(virt and rows()[virt]["library"] == 7 * GB, "and the files from both belong to it")
error, _ = scanner.add_virtual("Jinde", "/media/jinde", TB)
check(error != "", "a path outside all folders still does not pass")
put_items(("film", "/media/filmy/a.mkv", 30 * GB), ("dil", "/media/serialy/s01e01.mkv", 50 * GB))


print()
print("--- 6) Windows network paths ---")
check(scanner.normalize_path("\\\\NAS\\Media\\Filmy\\") == "//nas/media/filmy", "a UNC path in lower case")
check(scanner.is_inside("\\\\NAS\\Media\\Filmy\\a.mkv", "\\\\nas\\media\\filmy"),
      "a file on \\\\NAS in the folder \\\\nas - Windows does not tell letter case apart")


print()
print("--- 7) old details about disks ---")
clean_state()
jellyfin(Filmy=MOVIES, Serialy=SERIES)
check(rows()["dev-a"]["free"] == 500 * GB and not scanner.disk_state().get("outdated_list"),
      "fresh details hold")
old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(timespec="seconds")
put_setting(scanner.FOLDERS_TIME_KEY, old)
scanner.set_capacity("dev-b", "manual", 2 * TB)
r = rows()
check(r["dev-a"]["free"] is None and r["dev-a"]["source"] == "",
      f"the space Jellyfin sent 5 days ago is not passed off as today's ({r['dev-a']['free']})")
check(r["dev-b"]["free"] == 2 * TB - 50 * GB, "a manual capacity still holds")
check(scanner.disk_state().get("outdated_list"), "the state says since when the details are")
check("naposledy" in card(reader), "and the card too")
put_setting(scanner.FOLDERS_TIME_KEY, "")
check(rows()["dev-a"]["free"] == 500 * GB, "details without a time (from an earlier version) are taken as fresh")
jellyfin(Filmy=MOVIES, Serialy=SERIES)
check(db.get_setting(scanner.FOLDERS_TIME_KEY, ""), "storing the folders writes the time")


print()
print("--- 8) the answer “the same / different” can be taken back ---")
clean_state()
MUSIC = [folder("/media/hudba", "dev-c", 500 * GB, 500 * GB)]
jellyfin(Filmy=MOVIES, Hudba=MUSIC)
scanner.decide_same("dev-a", "dev-c", True)
check("dev-c" not in rows(), "merged")
check("/library/disks/decision/undo" in card(admin), "the administrator has a back button by the merged disk")
check("/library/disks/decision/undo" not in card(reader), "a reader not")
r = reader.post("/library/disks/decision/undo", data={"disk": "dev-a"}, follow_redirects=False)
check(r.status_code == 403, f"a reader does not cancel the answer ({r.status_code})")
r = admin.post("/library/disks/decision/undo", data={"disk": "dev-a"}, follow_redirects=False)
check(r.status_code == 303 and "dev-c" in rows() and scanner.disk_state()["suspicions"] == [("dev-a", "dev-c")],
      "after cancelling there are two disks again and the suspicion comes back")
scanner.decide_same("dev-a", "dev-c", False)
check("/library/disks/decision/undo" in card(admin), "the answer “different disks” can be taken back too")
check(scanner.undo_decision("dev-c") and scanner.disk_state()["suspicions"] == [("dev-a", "dev-c")],
      "and the question then shows again")
check(not scanner.undo_decision("neni"), "for a disk without an answer there is nothing to cancel")

clean_state()                                                              # “different” holds for the whole group
THREE = {name: [folder(f"/media/{name}", device, 500 * GB, 500 * GB)]
         for name, device in (("b", "dev-b"), ("c", "dev-c"), ("d", "dev-d"))}
jellyfin(**THREE)
scanner.decide_same("dev-c", "dev-d", False)
scanner.decide_same("dev-b", "dev-c", True)
check(scanner.disk_state()["suspicions"] == [],
      f"c ≠ d and b = c, so b ≠ d - it does not ask again ({scanner.disk_state()['suspicions']})")


print()
print("--- 9) the “network” label ---")
clean_state()
jellyfin(Usb=[folder("/media/usb", "dev-u", 10 * GB, 90 * GB, kind="Removable")],
         Nas=[folder("/media/nas", "dev-n", 20 * GB, 80 * GB, kind="Network")])
k = card(reader)
usb = k[k.index("<strong>Usb</strong>"):].split("</td>")[0]
nas = k[k.index("<strong>Nas</strong>"):].split("</td>")[0]
check("síťové" not in usb, "a USB disk is not a network one")
check("síťové" in nas, "a NAS is")


print()
print("--- 10) speed with a large library ---")
clean_state()
FOLDERS = [f"/media/lib{i}" for i in range(20)]
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.executemany("INSERT INTO items (id, name, type, path, size_bytes, is_missing, synced_at)"
                     " VALUES (?, ?, 'Episode', ?, ?, 0, ?)",
                     [(f"i{n}", f"i{n}", f"{FOLDERS[n % 20]}/Show {n // 400}/Season 1/e{n}.mkv", GB, db.utcnow())
                      for n in range(150_000)])
    conn.commit()
jellyfin(**{f"L{i}": [folder(FOLDERS[i], f"dev{i % 3}", 100 * GB, 900 * GB)] for i in range(20)})
put_setting(scanner.VIRTUAL_KEY, [{"id": f"v{i}", "name": f"V{i}", "path": f"{FOLDERS[i]}/Show {i}", "capacity": TB}
                                  for i in range(5)])


def fastest(function: Any) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        function()
        times.append(time.perf_counter() - start)
    return min(times)


query = fastest(lambda: db.query_all("SELECT path, size_bytes FROM items WHERE is_missing = 0"))
state = fastest(scanner.disk_state)
# A ratio, not a fixed time - the machine is often busy. The original pairing (every
# file against every folder) had 6.8× here, the new one 1.5× on SQLite and 2× on
# PostgreSQL (it reads rows more cheaply, the pairing takes just as long).
check(state < query * 3,
      f"the card takes at most 3× as much as loading the files alone ({state:.2f} s against {query:.2f} s)")
check(sum(r["library"] for r in scanner.disk_state()["disks"]) == 150_000 * GB, "and no file gets lost")
put_items(("film", "/media/filmy/a.mkv", 30 * GB), ("dil", "/media/serialy/s01e01.mkv", 50 * GB))


print()
print("--- 11) an old manual capacity and concurrent writes ---")
clean_state()
put_items(("velky", "/jinde/velky.mkv", 90 * GB), ("dil", "/media/serialy/s01e01.mkv", 50 * GB))
jellyfin(Filmy=MOVIES, Serialy=SERIES)
put_setting(scanner.CONVERTED_KEY, "")
put_setting(scanner.CAPACITY_KEY, str(4 * TB))
check(scanner.convert_old_capacity(), "the conversion runs even when the largest file lies outside the libraries' folders")
check(json.loads(db.get_setting(scanner.DISK_CAPACITY_KEY, "") or "{}") == {"dev-b": {"source": "manual", "capacity": 4 * TB}}
      and db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 0,
      "the capacity is with the disk on which most of the library lies")

clean_state()
put_items(("velky", "/jinde/velky.mkv", 90 * GB))
jellyfin(Filmy=MOVIES)
put_setting(scanner.CONVERTED_KEY, "")
put_setting(scanner.CAPACITY_KEY, str(4 * TB))
check(not scanner.convert_old_capacity() and db.get_setting(scanner.CONVERTED_KEY, "") != "1"
      and db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 4 * TB,
      "nothing lies on any disk: the capacity stays where it was and the conversion is tried next time")
put_items(("film", "/media/filmy/a.mkv", 30 * GB), ("dil", "/media/serialy/s01e01.mkv", 50 * GB))

clean_state()
jellyfin(Filmy=MOVIES, Serialy=SERIES)
admin.post("/settings", data={"section": "data", "tech_source": "jellyfin", "library_capacity_gb": "4",
                              "library_capacity_unit": "TB", "path_mappings": "[]"}, follow_redirects=False)
db.forget_settings()
check(db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0) == 0,
      "a form opened before the disks were found does not bring back the hidden old capacity")

clean_state()
jellyfin(Filmy=MOVIES, Serialy=SERIES)
original_json = scanner.disks._json


def slow_json(key: str, default: Any) -> Any:
    value = original_json(key, default)
    if key == scanner.VIRTUAL_KEY:
        time.sleep(0.2)              # the second write manages to squeeze in between the read and the save
    return value


restore = substitute(scanner, "_json", slow_json)
threads = [threading.Thread(target=scanner.add_virtual, args=(disk_name, path, TB))
           for disk_name, path in (("A", "/media/filmy/a"), ("B", "/media/serialy/b"))]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
restore()
db.forget_settings()
check(sorted(v["name"] for v in scanner.virtual_disks()) == ["A", "B"],
      f"two virtual disks added concurrently are both saved ({[v['name'] for v in scanner.virtual_disks()]})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
