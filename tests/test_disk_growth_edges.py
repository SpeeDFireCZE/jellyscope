# -*- coding: utf-8 -*-
r"""The library's growth by disks: edge cases from the final review (12. 11. 2026).

1) A remembered disk and a past period that ends before the disk's first
   snapshot: the Library must not fall over (formerly 500 at every load).
2) A virtual disk added only to files that were already in the library:
   the chart's history must not count them for both the physical disk and the virtual one
   (formerly a step and a negative increase, the sum of the disks above the library).
3) A disk's chip must not give away the device's id in its link - that is a path on
   the server (a mount point) and a reader does not see paths.
4) The virtual disk's dialog must not round the capacity: saving because of
   a rename would quietly change it.

Run:
    .venv/bin/python tests/test_disk_growth_edges.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rust_okraje.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, cards, db, formatting, scanner, stats, web  # noqa: E402

failures = 0
GB = 1024 ** 3
TB = 1024 ** 4
db.init_db()
TODAY = datetime.now(formatting.zone()).date()
DEVICE = "/mnt/tajna cesta"          # the device's id = a mount point on the server


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


def set_value(key: str, value: Any) -> None:
    db.set_setting(key, value if isinstance(value, str) else json.dumps(value))
    db.forget_settings()


set_value(scanner.CONVERTED_KEY, "1")
scanner.remember_folders({"Libraries": [
    {"Name": "Filmy", "Folders": [{"Path": "/media/filmy", "FreeSpace": 500 * GB, "UsedSpace": 500 * GB,
                                   "StorageType": "Fixed", "DeviceId": DEVICE}]},
    {"Name": "Serialy", "Folders": [{"Path": "/media/serialy", "FreeSpace": 300 * GB, "UsedSpace": 700 * GB,
                                     "StorageType": "Fixed", "DeviceId": "dev-b"}]}]})
db.forget_settings()
# A file of the movies' own and a file of the cloud - both in the library long before the first snapshot.
add_item("a", "/media/filmy/a.mkv", 30 * GB, 60)
add_item("c", "/media/filmy/cloud/c.mkv", 40 * GB, 60)
scanner.write_disk_snapshot(day(20))
scanner.write_disk_snapshot(day(10))
# Only now the administrator adds a virtual disk for the cloud.
scanner.add_virtual("Cloud", "/media/filmy/cloud", 2 * TB)
scanner.write_disk_snapshot(day(5))
scanner.write_disk_snapshot(day(0))
cloud = f"virt:{scanner.virtual_disks()[0]['id']}"


print("--- 2) a virtual disk added to files that were already there ---")
movies = {r["day"]: r for r in stats.disk_growth_series(90, DEVICE)}
cloud_rows = {r["day"]: r for r in stats.disk_growth_series(90, cloud)}
check(max(r["size_bytes"] for r in movies.values()) == 30 * GB,
      f"the physical disk never has the cloud's files (at most {max(r['size_bytes'] for r in movies.values()) // GB} GB)")
check(stats.disk_growth(90, DEVICE)["increase"] == 0, f"without a step: the increase 0 "
      f"({stats.disk_growth(90, DEVICE)['increase'] // GB} GB)")
days = sorted(set(movies) & set(cloud_rows))
wrong = [d for d in days if movies[d]["size_bytes"] + cloud_rows[d]["size_bytes"] != 70 * GB]
check(days and not wrong, f"on every day the disks together = the library 70 GB (wrong: {wrong[:3]})")


print()
print("--- 1) a remembered disk and a past period before the first snapshot ---")
past = stats.period_between(day(80), day(40))
try:
    growth = stats.disk_growth(past, DEVICE)
    check(growth["days_left"] is None, f"the disk's summary for a past period does not fall over ({growth.get('enough_data')})")
except Exception as exc:     # noqa: BLE001 - the test shows what fell over
    check(False, f"the disk's summary for a past period does not fall over ({type(exc).__name__}: {exc})")

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
page = admin.get("/library").text
choice = re.findall(r'href="\?growth_disk=([^"#]+)"[^>]*>Filmy<', page)
check(choice, "the Filmy disk chip is there")
if choice:
    admin.get(f"/library?growth_disk={choice[0]}")
r = admin.get(f"/?since={day(60)}&until={day(30)}")
r = admin.get("/library")
check(r.status_code == 200, f"the Library with a past period and a remembered disk loads ({r.status_code})")
db.set_setting(cards.ENABLED_KEY, "1")
db.forget_settings()
cards.save_layout([("rust_knihovny", "cela")])
check(admin.get("/dashboard").status_code == 200, "the custom dashboard too")
admin.get("/library?days=30")


print()
print("--- 3) the chip does not give away the device's id ---")
for who, client in (("reader", reader), ("administrator", admin)):
    html = client.get("/library").text
    growth_card = html[html.index('id="library-growth"'):]
    growth_card = growth_card[:growth_card.find('<div class="card"') if '<div class="card"' in growth_card else None]
    check(">Filmy<" in growth_card and "tajna" not in growth_card and "%2Fmnt" not in growth_card,
          f"{who}: the Filmy chip without the device's path in the link")
check("tajna" not in reader.get("/library").text, "a reader does not find the device's path anywhere on the page")
choice = re.findall(r'href="\?growth_disk=([^"#]+)"[^>]*>Filmy<', reader.get("/library").text)
html = reader.get(f"/library?growth_disk={choice[0]}").text if choice else ""
check(re.search(r'class="chip active"[^>]*>Filmy<', html), "the opaque choice chooses the right disk")


print()
print("--- 4) the capacity in the dialog is not rounded ---")
capacity = int(1.234 * TB)
virt_id = cloud[5:]
scanner.edit_virtual(virt_id, "Cloud", ["/media/filmy/cloud"], capacity)
html = admin.get("/library").text
dialog = next((o for o in re.findall(r"<dialog[^>]*>(.*?)</dialog>", html, flags=re.S)
               if f'name="id" value="{virt_id}"' in o), "")
value = re.search(r'name="capacity"[^>]*value="([^"]*)"', dialog)
check(value and value.group(1) == "1.234", f"the dialog shows 1.234 TB ({value.group(1) if value else None})")
admin.post("/library/disks/virtual/edit", follow_redirects=False,
           data={"id": virt_id, "name": "Cloud 2", "path": ["/media/filmy/cloud"],
                 "capacity": value.group(1) if value else "", "unit": "TB"})
db.forget_settings()
saved = scanner.virtual_disks()[0]
check(saved["name"] == "Cloud 2" and saved["capacity"] == capacity,
      f"a rename does not change the capacity ({saved['capacity']} against {capacity})")

print()
print("--- 5) no extra work ---")
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402

calls: dict[str, int] = {}


def counting(name: str) -> Any:
    original = getattr(stats, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls[name] = calls.get(name, 0) + 1
        return original(*args, **kwargs)
    return wrapper


restorers = [substitute(stats, name, counting(name)) for name in ("disk_growth_series", "disks_to_choose")]
choice = re.findall(r'href="\?growth_disk=([^"#]+)"[^>]*>Filmy<', admin.get("/library").text)[0]
calls.clear()
admin.get(f"/library?growth_disk={choice}")
library_calls = dict(calls)
calls.clear()
admin.get("/video/overview")       # the overview of watching (/ opens the default page since 16. 11. 2026)
overview_calls = dict(calls)
for restore in restorers:
    restore()
check(library_calls.get("disk_growth_series") == 1 and library_calls.get("disks_to_choose", 0) <= 2,
      f"the Library with a chosen disk computes the disk's series once ({library_calls})")
check(not overview_calls.get("disks_to_choose"),
      f"the Overview without the Library growth card does not ask for disks ({overview_calls})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
