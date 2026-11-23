# -*- coding: utf-8 -*-
r"""Library growth with music: old snapshots completed when read, no jump.

Spec: docs/superpowers/specs/2026-11-16-content-kinds-size-design.md, parts 6 and 7.
Snapshots written before 16. 11. 2026 hold video only (`kinds` NULL); the
reader adds the music computed from the days tracks were added, seated on
the first snapshot that carries music - so the curve does not jump on the
day of the upgrade. Before the first snapshot the backfill counts music too,
and the chart's bubble says how much of a day is music.

Run:
    .venv/bin/python tests/test_content_kinds_growth.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "content-kinds-growth.db")
os.environ["SECRET_KEY"] = "test-key-content-kinds-growth"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login  # noqa: E402
from jellyscope import accounts, content_kinds, db, demodata, formatting, scanner, stats  # noqa: E402

failures = 0
GB = 1024 ** 3
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
demodata.seed()
# These tests are about music (and video); the demo's books have their own checks in test_content_kinds.py.
with db.connect() as _conn:
    _conn.execute("DELETE FROM book")
    _conn.commit()
TODAY = datetime.now(formatting.zone()).date()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def day(back: int) -> str:
    return (TODAY - timedelta(days=back)).isoformat()


def sql(text: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(text, params)
        conn.commit()


def snapshot(back: int, size: int, kinds: dict | None) -> None:
    sql("INSERT INTO library_snapshot (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr, without_tech, written_at, kinds)"
        " VALUES (?, 10, 5, 5, ?, 0, 0, 0, ?, ?)", (day(back), size, db.utcnow(), None if kinds is None else json.dumps(kinds)))


def by_day(rows: list[dict]) -> dict[str, dict]:
    return {r["day"]: r for r in rows}


music_now = content_kinds.totals()["music"]
VIDEO = 500 * GB

print("--- (a) the day of the upgrade: no jump ---")
sql("DELETE FROM library_snapshot")
for back in range(10, 0, -1):                       # written by the old version: video only
    snapshot(back, VIDEO + (10 - back) * GB, None)
snapshot(0, VIDEO + 10 * GB + music_now["size"], {"music": {"items": music_now["items"], "size": music_now["size"]}})
rows = by_day(stats.snapshots(30))
check(rows[day(0)]["size_bytes"] - rows[day(1)]["size_bytes"] == 1 * GB,
      f"from the last old snapshot to the first new one only the video grew ({(rows[day(0)]['size_bytes'] - rows[day(1)]['size_bytes']) / GB} GB)")
check(all(rows[day(b)].get("kinds", {}).get("music") for b in range(10, 0, -1)),
      "every old snapshot carries the music computed for its day")
added_5 = int(db.query_value("SELECT COALESCE(SUM(size_bytes), 0) FROM music_track WHERE album_id = 'demo-alb-3'") or 0)
check(rows[day(5)]["kinds"]["music"] - rows[day(6)]["kinds"]["music"] == added_5 > 0,
      "an album added 5 days ago shows in the old snapshots from that day")
check(rows[day(0)]["kinds"] == {"music": music_now["size"]}, "the new snapshot keeps its measured part")

print()
print("--- (b) before the first snapshot: the backfill counts music ---")
sql("DELETE FROM library_snapshot")
curve = stats.snapshots(30)
last = curve[-1] if curve else {}
check(last.get("day") == day(0) and last.get("size_bytes") == stats.library_state()["size_bytes"]
      and last.get("kinds", {}).get("music") == music_now["size"],
      f"the last computed day is the library with music, its part said ({last.get('kinds')})")

print()
print("--- (c) a track gone from Jellyfin ---")
gone_id, gone_size = (lambda r: (r["id"], int(r["size_bytes"])))(
    db.query_one("SELECT id, size_bytes FROM music_track WHERE album_id = 'demo-alb-6' ORDER BY id LIMIT 1"))
unknown_id = db.query_value("SELECT id FROM music_track WHERE album_id = 'demo-alb-6' ORDER BY id LIMIT 1 OFFSET 1")
sql("UPDATE music_track SET missing = 1, synced_at = ? WHERE id = ?", (day(20) + " 09:00:00", gone_id))
sql("UPDATE music_track SET missing = 1, synced_at = '' WHERE id = ?", (unknown_id,))
rows = by_day(stats.snapshots(30))
check(rows[day(21)]["kinds"]["music"] - rows[day(19)]["kinds"]["music"] == gone_size,
      "it counts until the last sync that saw it, not after")
present_then = int(db.query_value("SELECT COALESCE(SUM(size_bytes), 0) FROM music_track WHERE missing = 0 AND added_at <= ?",
                                  (day(21) + " 23:59:59",)) or 0)
check(rows[day(21)]["kinds"]["music"] == present_then + gone_size
      and rows[day(0)]["kinds"]["music"] == content_kinds.totals()["music"]["size"],
      "a track gone on an unknown day counts nowhere")

print()
print("--- (d) a period all after the upgrade ---")
sql("DELETE FROM library_snapshot")
for back in range(40, -1, -1):
    snapshot(back, VIDEO + 3 * GB, {"music": {"items": 3, "size": 3 * GB}})
rows = stats.snapshots(30)
check(rows and all(r["size_bytes"] == VIDEO + 3 * GB and r["kinds"] == {"music": 3 * GB} for r in rows),
      "measured parts stay as measured, nothing added")

print()
print("--- (e) the chart's bubble ---")
accounts.create("admin", "longpassword", is_admin=True)
page = login("admin").get("/library").text
bubbles = re.findall(r'<g class="chart-hit"[^>]*>', page)
check(any(f"z toho hudba {formatting.bytes_human(3 * GB)}" in b for b in bubbles),
      f"a day of the growth chart says how much of it is music ({len(bubbles)} bubbles)")

print()
print("--- (f) disks: old snapshots completed, forecasts without a jump ---")
# Music lies on disk A with the movies; series on disk B. Snapshots D-10..D-1 are turned into what
# the old version wrote (knihovna video only, the music in "outside Jellyfin", kinds NULL), D is new.
sql("DELETE FROM library_disk_snapshot")
sql("UPDATE music_track SET missing = 0")
db.set_setting(scanner.CONVERTED_KEY, "1")


def jellyfin_folders() -> None:
    def folder(path: str, device: str) -> dict:
        return {"Path": path, "FreeSpace": 500 * GB, "UsedSpace": 1500 * GB, "StorageType": "Fixed", "DeviceId": device}
    scanner.remember_folders({"Libraries": [
        {"Name": "Filmy", "Id": "filmy", "Folders": [folder("D:/media/filmy", "dev-a")]},
        {"Name": "Hudba", "Id": "hudba", "Folders": [folder("/media/music", "dev-a")]},
        {"Name": "Serialy", "Id": "serialy", "Folders": [folder("D:/media/serialy", "dev-b")]}]})
    db.forget_settings()


def old_and_new_snapshots() -> int:
    """Snapshots D-10..D (D new); returns the music bytes on disk A today."""
    sql("DELETE FROM library_disk_snapshot")
    for back in range(10, -1, -1):
        scanner.write_disk_snapshot(day(back))
    music_a = json.loads(db.query_value("SELECT kinds FROM library_disk_snapshot WHERE day = ? AND disk = 'dev-a'",
                                        (day(0),)) or "{}").get("music", 0)
    sql("UPDATE library_disk_snapshot SET library = library - ?, outside = outside + ?,"
        " free = CASE WHEN source = 'manual' THEN free + ? ELSE free END, kinds = NULL"
        " WHERE disk = 'dev-a' AND day < ?", (music_a, music_a, music_a, day(0)))
    sql("UPDATE library_disk_snapshot SET kinds = NULL WHERE disk = 'dev-b' AND day < ?", (day(0),))
    return music_a


jellyfin_folders()
music_a = old_and_new_snapshots()
check(music_a == content_kinds.totals()["music"]["size"] > 0, "all demo music lies on disk A")
series_a = by_day(stats.disk_growth_series(30, "dev-a"))
check(series_a[day(0)]["size_bytes"] == series_a[day(1)]["size_bytes"],
      f"disk A: no jump from the last old snapshot to the new one ({(series_a[day(0)]['size_bytes'] - series_a[day(1)]['size_bytes']) / GB} GB)")
check(series_a[day(3)]["outside"] == series_a[day(0)]["outside"],
      "outside Jellyfin of an old snapshot is smaller by the music on the disk")
series_b = stats.disk_growth_series(30, "dev-b")
stored_b = {r["day"]: r["library"] for r in db.query_all("SELECT day, library FROM library_disk_snapshot WHERE disk = 'dev-b'")}
check(all(r["size_bytes"] == stored_b[r["day"]] for r in series_b if not r["backfilled"]), "disk B without music as stored")

# A manual capacity: the forecast's tempo comes from the growth of Jellyfin data on the disk.
check(scanner.set_capacity("dev-a", "manual", 4000 * GB) == "", "a manual capacity on disk A")
db.forget_settings()
old_and_new_snapshots()
album_3 = int(db.query_value("SELECT COALESCE(SUM(size_bytes), 0) FROM music_track WHERE album_id = 'demo-alb-3'") or 0)
card = {r["key"]: r for r in stats.disks_overview(30)["disks"]}
check(abs((card["dev-a"]["daily"] or 0) - album_3 / 10) < 1,
      f"the tempo is what really came in 10 days (an album 5 days ago), not the whole music at once "
      f"({(card['dev-a']['daily'] or 0) / GB:.3f} vs {album_3 / 10 / GB:.3f} GB a day)")

print()
print("--- (g) a disk snapshot written before the first full music sync ---")
# Final review 16. 11. 2026: right after the upgrade tracks have no path. A disk snapshot written
# then (a library sync, a capacity change) must not claim the disk's music measured as 0 - that
# would seat the old snapshots on 0 and leave a step for good.
paths = {r["id"]: r["path"] for r in db.query_all("SELECT id, path FROM music_track")}
sql("DELETE FROM library_disk_snapshot")
sql("UPDATE music_track SET path = NULL")
check(scanner.disk_state()["disks"][0]["kinds"] == {}, "the Disks card: music stays outside Jellyfin until the paths are known")
for back in range(10, 0, -1):
    scanner.write_disk_snapshot(day(back))
written = json.loads(db.query_value("SELECT kinds FROM library_disk_snapshot WHERE disk = 'dev-a' AND day = ?",
                                    (day(1),)) or "null")
check(written is not None and "music" not in written, f"the snapshot leaves the music unmeasured ({written})")
with db.connect() as conn:
    conn.executemany("UPDATE music_track SET path = ? WHERE id = ?", [(path, track) for track, path in paths.items()])
    conn.commit()
scanner.write_disk_snapshot(day(0))
series_a = by_day(stats.disk_growth_series(30, "dev-a"))
check(series_a[day(0)]["size_bytes"] == series_a[day(1)]["size_bytes"],
      f"no step once the paths are known ({(series_a[day(0)]['size_bytes'] - series_a[day(1)]['size_bytes']) / GB} GB)")
card = {r["key"]: r for r in stats.disks_overview(30)["disks"]}
check(abs((card["dev-a"]["daily"] or 0) - album_3 / 10) < 1,
      f"and the tempo is what really came in ({(card['dev-a']['daily'] or 0) / GB:.3f} GB a day)")

print()
print("--- (h) one disk's growth: the bubble and the shape of `kinds` ---")
# Deferred minors of the final review, fixed 16. 11. 2026.
choice = next(d["choice"] for d in stats.disks_to_choose() if d["key"] == "dev-a")
page = login("admin").get(f"/library?growth_disk={choice}").text
check(any("z toho hudba" in b for b in re.findall(r'<g class="chart-hit"[^>]*>', page)),
      "a disk's growth chart says how much of a day is music too")
sql("DELETE FROM library_disk_snapshot")
for back in range(3, -1, -1):
    scanner.write_disk_snapshot(day(back))
rows_a = stats.disk_snapshots(30)["dev-a"]
check(all(isinstance(r["kinds"], dict) for r in rows_a) and rows_a[-1]["kinds"] == {"music": music_a},
      f"snapshots of a disk carry `kinds` as {{kind: bytes}}, measured or completed ({type(rows_a[-1]['kinds']).__name__})")

print()
print("--- (i) Jellyfin 10.x: tracks without a path are computed too ---")
# Without a disk list the one "Library" row holds every file, a path or not - the computed past
# of its old snapshots must count tracks Jellyfin gave no path for.
db.set_setting(scanner.FOLDERS_KEY, "[]")
db.forget_settings()
sql("UPDATE music_track SET path = '' WHERE album_id = 'demo-alb-3'")
sql("DELETE FROM library_disk_snapshot")
for back in range(10, -1, -1):
    scanner.write_disk_snapshot(day(back))
music_all = content_kinds.totals()["music"]["size"]
sql("UPDATE library_disk_snapshot SET library = library - ?, kinds = NULL WHERE day < ?", (music_all, day(0)))
library_row = by_day(stats.disk_snapshots(30)["library"])
check(library_row[day(4)]["kinds"]["music"] - library_row[day(6)]["kinds"]["music"] == album_3,
      f"the album without paths shows on the day it came ({(library_row[day(4)]['kinds']['music'] - library_row[day(6)]['kinds']['music']) / GB:.3f} GB)")

print()
print("--- (j) the files' history is read once, until the files change ---")
# Speed (16. 11. 2026): with tens of thousands of tracks reading their history took ~0.16 s, and a
# page with disks read it several times - per call and per disk. Now once, kept until tracks change.
reads: list[int] = []
original_history = content_kinds.history


def counted_history() -> list[dict]:
    reads.append(1)
    return original_history()


content_kinds.history = counted_history
try:
    before = stats.disk_snapshots(30)["library"]      # remembered from the sections above, or read now
    reads.clear()
    # A track gone 5 days ago: the old snapshot 10 days ago (seated on today's measurement, which
    # still had it) now holds it on top - the computed past changed shape.
    gone = db.query_one("SELECT id, size_bytes FROM music_track WHERE album_id = 'demo-alb-6' AND missing = 0 ORDER BY id LIMIT 1")
    sql("UPDATE music_track SET missing = 1, synced_at = ? WHERE id = ?", (day(5) + " 09:00:00", gone["id"]))
    after = stats.disk_snapshots(30)["library"]
    stats.disk_snapshots(30)
    stats.disk_snapshots(90)
    check(len(reads) == 1, f"after the tracks changed three views read the history once ({len(reads)})")
    check(after[0]["library"] - before[0]["library"] == int(gone["size_bytes"]),
          "and count the change")
    stats.disk_snapshots(30)
    check(len(reads) == 1, "while nothing changes it is not read again")
finally:
    content_kinds.history = original_history

print()
print("--- (k) books in the bubble ---")
# The growth tooltip names the books too ("of which books {size}") when the snapshot measured some.
sql("DELETE FROM library_snapshot")
for back in range(10, -1, -1):
    snapshot(back, VIDEO + 5 * GB, {"music": {"items": 3, "size": 3 * GB}, "books": {"items": 2, "size": 2 * GB}})
page = login("admin").get("/library").text
bubbles = re.findall(r'<g class="chart-hit"[^>]*>', page)
check(any(f"z toho knihy {formatting.bytes_human(2 * GB)}" in b for b in bubbles),
      f"a day of the growth chart says how much of it is books ({len(bubbles)} bubbles)")

print()
print("--- (l) books are not measured before the first books sync ---")
# An empty `book` table before the first books sync is "not measured yet", not "0 bytes": a disk snapshot written
# then would store books=0 and the history would later jump. Music is protected the same way (paths NULL).
sql("DELETE FROM library_disk_snapshot")
sql("DELETE FROM settings WHERE key = 'books_first_sync'")
db.forget_settings()
check("books" not in content_kinds.kinds_with_paths(), "no books and no sync yet: books are not measured")
sql("INSERT INTO book (id, kind, name, title, author, title_key, library_id, path, size_bytes, missing)"
    " VALUES ('kk1', 'book', 'K', 'K', 'A', 'k|a', 'lib', '/media/books/k.epub', 100, 0)")
check("books" in content_kinds.kinds_with_paths(), "a synced book: measured")
sql("DELETE FROM book")
db.set_setting("books_first_sync", "1")
db.forget_settings()
check("books" in content_kinds.kinds_with_paths(), "the first sync ran and found none: measured (0 is true)")
sql("DELETE FROM settings WHERE key = 'books_first_sync'")
db.forget_settings()
jellyfin_folders()      # real disks again (section (i) left Jellyfin 10.x's single row, which measures every kind)
scanner.write_disk_snapshot(day(0))
written = json.loads(db.query_value("SELECT kinds FROM library_disk_snapshot WHERE disk = 'dev-a' AND day = ?", (day(0),)) or "null")
check(written is not None and "books" not in written, f"a disk snapshot before the first sync leaves the books out ({written})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
