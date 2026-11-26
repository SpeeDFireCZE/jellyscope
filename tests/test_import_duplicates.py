# -*- coding: utf-8 -*-
"""The import must not double records - neither after a repetition, nor between sources.

Three situations that happen easily in practice:

  1. The same file is uploaded twice (an extra click, unsure whether it worked).
  2. The same history is taken over from Jellystat **and** from Playback Reporting - both read the same
     data from the same Jellyfin.
  3. A period Jellyscope collected by itself is imported.

Against the first one the record's key is enough (`import:jst:<id>:<item>`). The second and the third the
key does not cover: another source has another shape of the key and its own collection has no import key
at all. So the content is compared too - the same user, the same item and **an overlapping time**.

Run:
    .venv/bin/python tests/test_import_duplicates.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in every submodule of the package importers

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would be overridden by the stored choice of database in
# data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dup.db")

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM playback"))


USER = "aa11bb22cc33dd44ee55ff6600112233"
MOVIE = "0011223344556677889900aabbccddee"

# The same two plays as each source sees them. The times differ by a few tens of seconds on purpose - every
# tool writes a slightly different moment (the start of the play vs. the moment it noted it).
JELLYSTAT = {
    "jf_playback_activity": [
        {"Id": 1, "ActivityDateInserted": "2026-07-01 20:00:00",
         "PlaybackDuration": "3600", "UserId": USER,
         "NowPlayingItemId": MOVIE, "NowPlayingItemName": "Matrix"},
        {"Id": 2, "ActivityDateInserted": "2026-07-03 18:00:00",
         "PlaybackDuration": "1800", "UserId": USER,
         "NowPlayingItemId": MOVIE, "NowPlayingItemName": "Matrix"},
    ]
}

PBR_COLUMNS = ["rowid", "DateCreated", "UserId", "ItemId", "ItemType",
               "ItemName", "PlaybackMethod", "ClientName", "DeviceName",
               "PlayDuration"]
PBR_ROWS = [
    [10, "2026-07-01 20:00:40", USER, MOVIE, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600],
    [11, "2026-07-03 18:00:25", USER, MOVIE, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 1800],
]


def import_pbr(min_seconds: int = 60) -> dict[str, Any]:
    """Run the import from Playback Reporting with a substituted answer of the server."""
    class FakeAnswer:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"colums": PBR_COLUMNS, "results": PBR_ROWS}

    class FakeHttp:
        """Stands in for the httpx client inside JellyfinClient."""

        async def post(self, path: str, json: Any = None) -> FakeAnswer:
            # A check on the occasion: only SELECT may go in here too.
            query = (json or {}).get("CustomQueryString", "")
            assert query.upper().startswith("SELECT"), query
            return FakeAnswer()

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            self._client = FakeHttp()

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

    original = importers.JellyfinClient
    substitute(importers, "JellyfinClient", FakeClient)
    try:
        return asyncio.run(importers.import_playback_reporting(min_seconds))
    finally:
        substitute(importers, "JellyfinClient", original)


db.init_db()

print("--- 1) the first import from Jellystat ---")
v = asyncio.run(importers.import_jellystat_json(json.dumps(JELLYSTAT).encode()))
check(v.get("imported") == 2, f"2 records were imported ({v.get('imported')})")
check(count() == 2, f"the database holds 2 rows ({count()})")


print()
print("--- 2) the same file a second time ---")
v = asyncio.run(importers.import_jellystat_json(json.dumps(JELLYSTAT).encode()))
check(v.get("imported") == 0, "nothing new was imported")
check(v.get("duplicate") == 2, f"it recognised both records by the key ({v.get('duplicate')})")
check(count() == 2, f"the count of rows did not change ({count()})")


print()
print("--- 3) the same data from Playback Reporting ---")
# The keys have another shape (pbr vs jst), so the key does not catch them. The comparison of content must
# recognise them.
v = import_pbr()
check(v.get("status") == "ok", f"the import went through: {v.get('status')}")
check(v.get("imported") == 0, f"nothing doubled ({v.get('imported')} new)")
check(v.get("known_elsewhere") == 2,
      f"it recognised both records as known already ({v.get('known_elsewhere')})")
check(count() == 2, f"the database still holds 2 rows ({count()})")


print()
print("--- 4) a really new play goes through ---")
PBR_ROWS.append(
    [12, "2026-07-20 21:00:00", USER, MOVIE, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 2400])
v = import_pbr()
check(v.get("imported") == 1, f"one new record was added ({v.get('imported')})")
check(count() == 3, f"the database holds 3 rows ({count()})")


print()
print("--- 5) a record from the collector is not doubled either ---")
# Its own collection has no key in common with the import at all.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, ended_at,
                                 watched_seconds, is_active)
           VALUES ('relace-abc:polozka', ?, ?, 'Matrix',
                   '2026-07-25 19:00:00', '2026-07-25 20:00:00',
                   '2026-07-25 20:00:00', 3600, 0)""",
        (USER, MOVIE),
    )
PBR_ROWS.append(
    [13, "2026-07-25 19:00:50", USER, MOVIE, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600])
v = import_pbr()
check(v.get("imported") == 0, f"a period covered by the collection was not imported ({v.get('imported')})")
# Three: two records from Jellystat (rows 10 and 11, the list runs on) and newly also the one the collector
# caught meanwhile.
check(v.get("known_elsewhere") == 3, f"it recognised them as known already ({v.get('known_elsewhere')})")
check(count() == 4, f"the count of rows did not change ({count()})")


print()
print("--- 6) the overlap is judged per user and item, not globally ---")
OTHER_USER = "bb22cc33dd44ee55ff6600112233aa11"
OTHER_MOVIE = "ffeeddccbbaa00998877665544332211"
PBR_ROWS.append(
    # The same time as an existing record, but another viewer - it must go through.
    [14, "2026-07-25 19:00:00", OTHER_USER, MOVIE, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 3600])
PBR_ROWS.append(
    # The same viewer and time, but another movie - it must go through too.
    [15, "2026-07-25 19:00:00", USER, OTHER_MOVIE, "Movie", "Duna",
     "DirectPlay", "Web", "Chrome", 3600])
v = import_pbr()
check(v.get("imported") == 2, f"both different plays went through ({v.get('imported')})")
check(count() == 6, f"the database holds 6 rows ({count()})")


print()
print("--- 7) consecutive episodes do not count as a duplicate ---")
# The end of one and the start of the other in the same second is no overlap: the viewer may have started the
# next episode right after finishing the previous one.
PBR_ROWS.append(
    [16, "2026-07-25 20:00:00", USER, MOVIE, "Movie", "Matrix",
     "DirectPlay", "Web", "Chrome", 1800])
v = import_pbr()
check(v.get("imported") == 1, f"the consecutive play went through ({v.get('imported')})")


print()
print("--- 8) an identifier with dashes is the same user ---")
WITH_DASHES = "aa11bb22-cc33-dd44-ee55-ff6600112233"
before = count()
JELLYSTAT["jf_playback_activity"].append(
    {"Id": 99, "ActivityDateInserted": "2026-07-20 21:00:30",
     "PlaybackDuration": "2400", "UserId": WITH_DASHES,
     "NowPlayingItemId": MOVIE, "NowPlayingItemName": "Matrix"})
v = asyncio.run(importers.import_jellystat_json(json.dumps(JELLYSTAT).encode()))
check(v.get("imported") == 0,
      f"another notation of the same id does not hide a duplicate ({v.get('imported')} new)")
check(count() == before, f"the count of rows did not change ({count()})")


print()
print("--- 9) the library's name joins two sources that name the title their own way ---")
# The collector wrote what Jellyfin named the episode, the import what Jellystat did: the item is the same, so
# its name in the library is the same too (review of the database step 6: a renamed alias took the playback's
# name instead - SQLite the first column of a name, PostgreSQL the last).
from jellyscope import importers as _importers  # noqa: E402

EPISODE = "0e1d2c3b4a5968778695a4b3c2d1e0f9"
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at) VALUES (?, 'Zvony', 'Episode', 0, ?)",
                 (EPISODE, db.utcnow()))
    for key, name, start in (("relace-xyz:polozka", "Zvony", "2026-08-01 20:00:00"),
                             ("import:jellystat:99", "Hra o truny - s08e05 - Zvony", "2026-08-01 20:00:30")):
        conn.execute("INSERT INTO playback (session_key, user_id, item_id, item_name, started_at, last_seen_at, ended_at,"
                     " watched_seconds, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, 3000, 0)",
                     (key, USER, EPISODE, name, start, start, start))
    conn.commit()
pair = [g for g in _importers.import_duplicate_groups() if {r["item_id"] for r in g} == {EPISODE}]
check(len(pair) == 1 and {r["session_key"] for r in pair[0]} == {"relace-xyz:polozka", "import:jellystat:99"},
      f"the collected and the imported record of one episode are one viewing ({[len(g) for g in pair]})")

print()
print("--- an unexpected error while writing leaves no import hanging as running ---")
# Only the reading of the file was guarded: a database locked while the rows were written ended the import with the
# exception, and its run stayed "running" in Settings -> Tasks until the next restart.
import sqlite3  # noqa: E402

from jellyscope import scanner  # noqa: E402


def locked_insert(rows):
    raise sqlite3.OperationalError("database is locked")


fresh = {"jf_playback_activity": [dict(JELLYSTAT["jf_playback_activity"][0], Id=500,
                                       ActivityDateInserted="2026-10-01 20:00:00")]}
tsv = "\t".join(["DateCreated", "UserId", "ItemId", "ItemType", "ItemName", "PlaybackMethod", "ClientName",
                 "DeviceName", "PlayDuration"]) + "\n" + "\t".join(
    ["2026-10-02 20:00:00", USER, MOVIE, "Movie", "Matrix", "DirectPlay", "Web", "Chrome", "3600"])
restore_insert = substitute(importers, "_insert", locked_insert)
try:
    for label, run in (("Jellystat", lambda: importers.import_jellystat_json(json.dumps(fresh).encode())),
                       ("Playback Reporting (file)", lambda: importers.import_playback_reporting_tsv(tsv.encode()))):
        try:
            asyncio.run(run())
            raised = ""
        except sqlite3.OperationalError as error:
            raised = str(error)
        record = scanner.last_scan("import") or {}
        check(record.get("status") == "error" and bool(record.get("finished_at")),
              f"{label}: the run is closed as failed ({record.get('status')}, {raised!r})")
finally:
    restore_insert()

# The try began only after the file was searched for its table: a list with a number in it (TypeError) or a JSON
# nested too deep (RecursionError) left the run "running" just the same.
for label, raw in (("a list with a number after a record", b'[{"x": 1}, 5]'),
                   ("JSON nested too deep", b"[" * 100000 + b"]" * 100000)):
    try:
        asyncio.run(importers.import_jellystat_json(raw))
        raised = ""
    except Exception as error:  # noqa: BLE001 - what the test looks at
        raised = type(error).__name__
    record = scanner.last_scan("import") or {}
    check(record.get("status") == "error" and bool(record.get("finished_at")),
          f"Jellystat, {label}: the run is closed as failed ({record.get('status')}, {raised!r})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
