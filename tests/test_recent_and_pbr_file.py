# -*- coding: utf-8 -*-
"""The quick sync of newly added items + the import from the plugin's backup.

Two things that are unrelated, but both came about for the same reason —
the full path is sometimes not enough:

  * **The quick sync** goes through only what was added in Jellyfin a short
    while ago. The window follows from the task's interval and has a reserve:
    with a run once an hour it looks an hour and a half back, so nothing
    falls through when a run is a few minutes late.

  * **The import from the backup** goes around the broken API of the Playback
    Reporting plugin. When the plugin is compiled against another Jellyfin,
    it fails before it gets to its data — but it can make a backup itself
    and stores it as an ordinary TSV file.

Both share one trap the test guards above all: the quick sync **must not**
call `_mark_missing()`. That marks as vanished everything the run did not
see — and this run sees only a handful of titles, so the rest of the library
would end in the archive.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in every submodule of the scanner package

_tmp = tempfile.mkdtemp()
# An own home: without it the stored choice of the database in
# data/database.json would override DATABASE_PATH and the test would run
# against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "recent.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, importers, scanner  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def ago(minutes: int) -> str:
    """A time in the shape Jellyfin sends it in."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.0000000Z")


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

# The library: three fresh titles and five hundred old ones.
NEW = [{"Id": f"novy-{i}", "Name": f"Nový {i}", "Type": "Movie",
        "DateCreated": ago(i * 10), "RunTimeTicks": 6_000_000_000}
       for i in range(3)]
OLD = [{"Id": f"stary-{i}", "Name": f"Starý {i}", "Type": "Movie",
        "DateCreated": ago(5000 + i), "RunTimeTicks": 6_000_000_000}
       for i in range(500)]
LIBRARY = NEW + OLD

counter = {"stranek": 0}


class FakeClient:
    """A Jellyfin that can page a sorted list."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def items_page(self, start: int, limit: int, *a: Any, **k: Any) -> dict[str, Any]:
        counter["stranek"] += 1
        return {"Items": LIBRARY[start:start + limit],
                "TotalRecordCount": len(LIBRARY)}

    async def items_by_ids(self, ids: Any) -> list[dict[str, Any]]:
        """Asking for concrete items - the analysis fills in languages and the
        dynamic range with it. This test does not aim at them, so it returns nothing."""
        return []

    async def _first_admin_id(self) -> str | None:
        return None

    async def virtual_folders(self) -> list[dict[str, Any]]:
        # The quick sync goes through the libraries one by one - only so it
        # knows where a newly added title belongs.
        return [{"ItemId": "lib", "Name": "Filmy", "CollectionType": "movies"}]

    async def users(self) -> list[dict[str, Any]]:
        # Since 1.7.1 the quick sync takes the users too - for the accounts of
        # people the admin removed in Jellyfin. Nobody here.
        return []

    # We test the real logic of the window, not its copy.
    recent_items = JellyfinClient.recent_items


substitute(scanner, "JellyfinClient", FakeClient)

# An old title that is still in Jellyfin - it must not vanish into the archive.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, date_created, is_missing, synced_at)
           VALUES ('stary-0', 'Starý 0', 'Movie', '2020-01-01 00:00:00', 0, ?)""",
        (db.utcnow(),),
    )


print("--- the boundary is set by the last known title, not by the clock ---")
# Originally a time window was computed from the task's interval. But then it
# mattered whether the task ran as planned: when the application stood still
# for half a day, the window was shorter than the outage and the titles from
# that time fell through. The database holds only that old title from 2020 so
# far, so the boundary comes out just before it - and everything newer is gone
# through, that is the whole library.
result = asyncio.run(scanner.sync_recent())
check(result["status"] == "ok", f"the first run ran: {result}")
check(result["since"].startswith("2019-12-31"),
      f"the boundary is five minutes before the last known title ({result['since']})")
check(result["checked"] == len(LIBRARY),
      f"everything newer is gone through ({result['checked']})")
# `stary-0` was in the database already (we put it there above), so it is not
# new - and it must not count as new.
check(result["items"] == len(LIBRARY) - 1,
      f"only those we did not have yet count as new ({result['items']})")


print()
print("--- a second run right after the first must report nothing new ---")
# This was a reported bug: the boundary is moved back by five minutes on
# purpose (so a title added in the same second is not skipped), so the newest
# already known title is downloaded again. It used to count as new - the
# result therefore never showed zero and a person looked in vain for what was added.
result = asyncio.run(scanner.sync_recent())
check(result["items"] == 0,
      f"nothing was added, so nothing is reported ({result['items']})")
check(result["checked"] > 0,
      f"a few had to be checked meanwhile ({result['checked']})")

# On a completely empty database there is nothing to push off from - then the
# newest items are taken, as many as fit under the cap.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
check(scanner._last_added() is None,
      "an empty library has no boundary")
result = asyncio.run(scanner.sync_recent())
check(result["since"] is None and result["items"] == len(LIBRARY),
      f"and everything is taken ({result['items']})")

# We put the old title back - we need it below.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, date_created, is_missing, synced_at)
           VALUES ('stary-0', 'Starý 0', 'Movie', '2020-01-01 00:00:00', 0, ?)
           ON CONFLICT(id) DO UPDATE SET is_missing = 0""",
        (db.utcnow(),))

counter["stranek"] = 0
result = asyncio.run(scanner.sync_recent())
check(result["since"] is not None, f"the second time the boundary is there ({result['since']})")
check(counter["stranek"] <= 2,
      f"and a few calls are enough instead of going through the library ({counter['stranek']})")


print()
print("--- a new title is found even when the task did not run for long ---")
LIBRARY.insert(0, {"Id": "uplne-novy", "Name": "Úplně nový", "Type": "Movie",
                   "DateCreated": ago(0), "RunTimeTicks": 6_000_000_000})
result = asyncio.run(scanner.sync_recent())
check(db.query_one("SELECT name FROM items WHERE id = 'uplne-novy'") is not None,
      "the new title was stored")


print()
print("--- and above all: nothing vanished into the archive ---")
vanished = db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 1")
check(vanished == 0, f"no title was marked as vanished ({vanished})")
check(db.query_one("SELECT is_missing FROM items WHERE id = 'stary-0'")["is_missing"] == 0,
      "the old title we did not see now stayed in the library")

# Were somebody to add _mark_missing() to the quick sync, this test would
# still pass - hence one more check of the source.
# Only the body of sync_recent - not a slice of the package's joined files,
# which would depend on the order of the files.
body = inspect.getsource(scanner.sync_recent)
# We look for a real call, not a mention in a comment - that one is to be there.
statements = [r for r in body.splitlines()
              if r.strip() and not r.strip().startswith(("#", '"', "*", "-"))]
check(not any("_mark_missing(" in r for r in statements),
      "sync_recent() does not call _mark_missing() (otherwise the library would vanish)")


print()
print("--- a title sorted with a delay fixes itself ---")
# This happened in practice: Jellyfin adds a file before it sorts it into the
# library. It is stored under the file name and without a series. It used to
# stay that way forever - the time window moved on meanwhile and the next run
# did not look at that title any more. Now the boundary follows from the last
# known title, so the newest is checked again until an even newer one comes.
LIBRARY.insert(0, {"Id": "nezarazeny", "Name": "nazev.souboru.1080p.mkv",
                   "Type": "Video", "DateCreated": ago(0),
                   "RunTimeTicks": 6_000_000_000})
asyncio.run(scanner.sync_recent())
r = db.query_one("SELECT name, type, series_name, library_id FROM items"
                 " WHERE id = 'nezarazeny'")
check(r["name"].endswith(".mkv"), "first it is stored under the file name")
check(r["library_id"] == "lib",
      f"but we know the library right away - they are gone through one by one ({r['library_id']})")

LIBRARY[0] = {"Id": "nezarazeny", "Name": "1. díl", "Type": "Episode",
              "SeriesName": "Kancelář", "SeriesId": "ser-1",
              "ParentIndexNumber": 1, "IndexNumber": 1,
              "DateCreated": ago(0), "RunTimeTicks": 6_000_000_000}
asyncio.run(scanner.sync_recent())
r = db.query_one("SELECT name, type, series_name FROM items WHERE id = 'nezarazeny'")
check(r["name"] == "1. díl", f"after sorting the name is fixed ({r['name']})")
check(r["type"] == "Episode" and r["series_name"] == "Kancelář",
      f"and the type and the series ({r['type']}, {r['series_name']})")


print()
print("--- a repeated run doubles nothing ---")
count_before = db.query_value("SELECT COUNT(*) FROM items")
asyncio.run(scanner.sync_recent())
check(db.query_value("SELECT COUNT(*) FROM items") == count_before,
      "the number of titles did not change (written through ON CONFLICT)")


print("--- with the ffprobe source a new title is measured right away ---")
# With the technical data source "ffprobe" the details from Jellyfin are on
# purpose not taken. A newly added title thus stayed completely empty -
# without a container, resolution and size - and waited for the daily task,
# that is up to a day.
#
# **Only the newly added ones** are measured, not the whole library: it is a few files.
from jellyscope import probe  # noqa: E402

db.set_setting("tech_source", "ffprobe")
measured: list[str] = []


async def _fake_probe(path: str, tool: str) -> dict[str, Any]:
    measured.append(path)
    return {"container": "mkv", "video_codec": "hevc", "width": 3840,
            "height": 2160, "bitrate": 25_000_000, "size_bytes": 40_000_000_000}


probe.probe_file = _fake_probe                        # type: ignore[assignment]
probe.find_ffprobe = lambda path="": "/usr/bin/ffprobe"  # type: ignore[assignment]

# Let all titles so far have a path - otherwise they would not get into the
# analysis and the test would not show they are left out on purpose.
with db.connect() as conn:
    conn.execute("UPDATE items SET path = '/media/' || id || '.mkv'")
before_analysis = db.query_value("SELECT COUNT(*) FROM items")

LIBRARY.insert(0, {"Id": "novy-ffprobe", "Name": "Zbrusu nový", "Type": "Movie",
                   "Path": "/media/novy.mkv", "DateCreated": ago(0),
                   "RunTimeTicks": 6_000_000_000})
measured.clear()
result = asyncio.run(scanner.sync_recent())

check(result["items"] == 1, f"one title was added ({result['items']})")
check(result.get("tech", {}).get("ok") == 1,
      f"and it was measured right away ({result.get('tech')})")
check(measured == ["/media/novy.mkv"],
      f"ONLY the new one was measured, not the whole library ({len(measured)} files)")

new_row = db.query_one(
    "SELECT container, width, height, bitrate, tech_source FROM items"
    "  WHERE id = 'novy-ffprobe'")
check(new_row["container"] == "mkv" and new_row["width"] == 3840,
      f"the technical data are in the database ({dict(new_row)})")
check(new_row["tech_source"] == "ffprobe", "and next to them where they come from")

# When nothing is added, nothing is measured at all.
measured.clear()
result = asyncio.run(scanner.sync_recent())
check(result["items"] == 0 and not measured,
      f"without new titles ffprobe does not run ({len(measured)})")
check(db.query_value("SELECT COUNT(*) FROM items") == before_analysis + 1,
      "and the number of titles matches")

db.set_setting("tech_source", "jellyfin")


print()
print("--- the import from the plugin's backup (a TSV file) ---")
# The plugin makes the backup itself: Dashboard → Playback Reporting →
# Backup → Save backup. It is ordinary text, values separated by a tab.
#
# `playback_reporting.db` used to be uploaded here directly. But a person has
# to get to it over SSH and know where it lies on the server - whereas the
# plugin makes the backup with one click.
backup = "\n".join("\t".join(str(x) for x in row) for row in [
    ("2026-07-01 20:00:00", "u1", "film-1", "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600),
    ("2026-07-02 21:00:00", "u1", "film-2", "Movie", "Duna",
     "DirectPlay", "Web", "Chrome", 1800),
    ("2026-07-03 10:00:00", "u1", "film-3", "Movie", "Krátký",
     "DirectPlay", "Web", "Chrome", 10),        # below the boundary, skipped
]).encode("utf-8")

result = asyncio.run(
    importers.import_playback_reporting_tsv(backup, min_seconds=60))
check(result["status"] == "ok", f"the import ran: {result.get('message', '')}")
check(result["imported"] == 2, f"two records were taken over ({result['imported']})")
check(result["too_short"] == 1, "the short one was skipped")

# The key is the same as with the import through the API, so it is not doubled even later.
keys = [r["session_key"] for r in db.query_all(
    "SELECT session_key FROM playback ORDER BY session_key")]
check(all(k.startswith("import:pbr:") for k in keys),
      f"the keys have the shape as from the API: {keys}")

result = asyncio.run(
    importers.import_playback_reporting_tsv(backup, min_seconds=60))
check(result["imported"] == 0, "uploading again adds nothing")
check(db.query_value("SELECT COUNT(*) FROM playback") == 2, "still two records")

# Some versions of the plugin write a header too - we have to tell that ourselves.
with_header = (b"DateCreated\tUserId\tItemId\tItemType\tItemName\t"
               b"PlaybackMethod\tClientName\tDeviceName\tPlayDuration\n" + backup)
result = asyncio.run(
    importers.import_playback_reporting_tsv(with_header, min_seconds=60))
check(result["imported"] == 0 and result["found"] == 3,
      f"a file with a header is read the same ({result['found']} found)")

# Only a line feed ends a record (\r\n of Windows too). str.splitlines() also ends a line at U+2028, NEL, a form
# feed and a few more - a title from the internet's metadata with one of them fell apart into two halves of five
# fields, and the play was lost.
odd_titles = "\r\n".join("\t".join(str(x) for x in row) for row in [
    ("2026-07-04 20:00:00", "u1", "film-4", "Movie", "Line\u2028separator", "DirectPlay", "Web", "Chrome", 3600),
    ("2026-07-05 20:00:00", "u1", "film-5", "Movie", "Next\x85line", "DirectPlay", "Web", "Chrome", 3600),
    ("2026-07-06 20:00:00", "u1", "film-6", "Movie", "Form\x0cfeed", "DirectPlay", "Web", "Chrome", 3600),
]).encode("utf-8")
result = asyncio.run(importers.import_playback_reporting_tsv(odd_titles, min_seconds=60))
check(result.get("found") == 3 and result.get("imported") == 3,
      f"a title with U+2028, NEL or a form feed is one record, Windows line ends too "
      f"({result.get('found')} found, {result.get('imported')} imported)")


print()
print("--- what if the file is not what it should be ---")
result = asyncio.run(importers.import_playback_reporting_tsv(b"tohle neni zaloha"))
check(result["status"] == "error", "a nonsense file is refused")
check("Backup" in result["message"],
      f"and it advises where to make the backup: {result['message'][:110]}")

result = asyncio.run(importers.import_playback_reporting_tsv(b""))
check(result["status"] == "error", "an empty file is refused")


print()
print("--- a library in which nothing was added ---")
# The function returns a list of new ids. An empty answer used to return zero
# from it and the caller added it to a list (`nova_id.extend(...)`), which ends
# in "'int' object is not iterable". It failed only when some library had not a
# single new item - that is on almost every run.
empty = asyncio.run(scanner._save_new_items([], "lib", False))
check(empty == [], f"an empty list is returned, not zero ({empty!r})")
new: list[str] = []
new.extend(empty)
check(new == [], "and the caller can add it to the others right away")


print()
print("--- the task's message: the archive sentences without a double dot ---")
# Both archive sentences ended with a dot and were glued with ". ". When
# something was archived for both reasons at once, the task's log said
# "...pripojena.. 2 drive ulozenych polozek...".
# A stored message now (a key and its values), read in Czech as the test's checks are.
message = scanner._recent_message(since="2026-01-01", added=3, seen=40,
                                  replaced=2, recreated=1).translated("cs")
check(".." not in message, f"no two dots in a row ({message!r})")
check(message.startswith("3 ") and "2 z nich" in message and "1 dříve" in message,
      "and the message has everything: new, replaced and created again")
check(scanner._recent_message(since="2026-01-01", added=0, seen=40,
                              replaced=0, recreated=0).translated("cs") == "Nic nového (zkontrolováno 40)",
      "without the archive the message stays as it was")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
