# -*- coding: utf-8 -*-
r"""The behaviour of the four longest functions where the other tests do not reach.

Rebuild 3f split `_store_sessions` (the collector), `run_tech_scan` (the file
analysis) and `import_jellystat_json` into named steps - and `settings_page`
too, but the other tests go through that one whole. Before the split it was
measured which lines today's suite does not go through; this test pins them,
so it can be told that after the split they behave the same:

* collector: a session that plays nothing; one snapshot = one transaction;
* analysis: busy, ffprobe missing, a broken path mapping, limiting to a
  library, `limit`, an empty list of items, nothing to analyse, filling in
  languages (even when Jellyfin fails), the task message's suffixes;
* Jellystat import: the playback table is missing; a record that is not a
  dict, has no time, has an unreadable length or is in the file twice.

The test passed over the code BEFORE the split and must pass after it too.

Spec: docs/superpowers/specs/2026-11-06-slozite-funkce-design.md

Run:
    .venv/bin/python tests/test_complex_functions.py
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
from _package import substitute  # noqa: E402 - a fake in all the package's submodules

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "slozite.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import collector, db, i18n, importers, languages, probe, scanner  # noqa: E402
from jellyscope.jellyfin import JellyfinError  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def task_count(kind: str) -> int:
    return db.query_value("SELECT COUNT(*) FROM scan_log WHERE kind = ?", (kind,)) or 0


print("--- collector: a session that plays nothing ---")
PLAYING = {"Id": "s1", "UserId": "u1", "UserName": "Jana", "Client": "Web",
           "DeviceName": "Chrome", "DeviceId": "d1", "PlayState": {"PositionTicks": 0},
           "NowPlayingItem": {"Id": "film-1", "Name": "Matrix", "Type": "Movie"}}
IDLE = {"Id": "s2", "UserId": "u2", "UserName": "Karel", "Client": "Web",
        "DeviceName": "Firefox", "DeviceId": "d2", "PlayState": {}}
result = collector._store_sessions([IDLE, PLAYING], max_gap_seconds=60)
check(result == {"active": 1, "started": 1, "ended": 0},
      f"an idle session does not count among the active ones ({result})")
result = collector._store_sessions([IDLE], max_gap_seconds=60)
check(result == {"active": 0, "started": 0, "ended": 1},
      f"when only the idle one plays, everything active is ended ({result})")

print()
print("--- collector: one snapshot = one transaction ---")
# A helper that opened its own connection would wait for the lock on SQLite
# and on an error leave half a snapshot written.
original_connect = db.connect
opened = {"n": 0}


def counted_connect(*a: Any, **k: Any) -> Any:
    opened["n"] += 1
    return original_connect(*a, **k)


db.connect = counted_connect  # type: ignore[assignment]
try:
    another = dict(PLAYING, Id="s3", UserId="u3", DeviceId="d3",
                   NowPlayingItem={"Id": "film-2", "Name": "Alien", "Type": "Movie"})
    collector._store_sessions([PLAYING, another], max_gap_seconds=60)
    collector._store_sessions([PLAYING, another], max_gap_seconds=60)
finally:
    db.connect = original_connect  # type: ignore[assignment]
check(opened["n"] == 2, f"two calls with two sessions = two connections ({opened['n']})")


print()
print("--- file analysis ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    for lib in ("lib-a", "lib-b"):
        conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at)"
                     " VALUES (?, ?, 'movies', ?)", (lib, lib, db.utcnow()))
    for i, lib in enumerate(("lib-a", "lib-a", "lib-b")):
        conn.execute("INSERT INTO items (id, name, type, library_id, path, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?)",
                     (f"pol-{i}", f"Film {i}", lib, f"/media/film-{i}.mkv", db.utcnow()))

paths: list[str] = []


async def probe_ok(path: str, binary: str) -> dict[str, Any]:
    paths.append(path)
    return {"container": "mkv", "video_codec": "h264", "size_bytes": 1, "streams": []}


probe.find_ffprobe = lambda p="": "/usr/bin/ffprobe"  # type: ignore[assignment]
probe.probe_file = probe_ok  # type: ignore[assignment]
# Jellyfin is not in the test - the fillers are faked so they connect nowhere.
restore = [substitute(scanner, "candidates_for_language_from_name", lambda *a, **k: []),
           substitute(scanner, "candidates_for_range_from_jellyfin", lambda *a, **k: [])]


async def holding_lock() -> dict[str, Any]:
    async with scanner.task_state._scan_lock:
        return await scanner.run_tech_scan()

check(asyncio.run(holding_lock()) == {"status": "busy", "message": "Another task is already running."},
      "when a task already runs, the analysis answers 'busy'")

probe.find_ffprobe = lambda p="": None  # type: ignore[assignment]
result = asyncio.run(scanner.run_tech_scan())
check(result.get("status") == "error"
      and result.get("message", "").startswith("ffprobe could not be found"),
      f"without ffprobe an error with instructions ({result.get('status')})")
probe.find_ffprobe = lambda p="": "/usr/bin/ffprobe"  # type: ignore[assignment]

for broken in ("{", '{"a": 1}'):
    db.set_setting("path_mappings", broken)
    paths.clear()
    result = asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"]))
    check(result.get("status") == "ok" and paths == ["/media/film-0.mkv"],
          f"a broken path mapping {broken!r} is skipped, the path stays ({paths})")
# A mapping stored before the save checked its shape: an entry that is not an object of texts is skipped, the
# others still apply - the analysis does not end with an error for every file.
for broken, expected in (('["/media", "/mnt/media"]', "/media/film-0.mkv"),
                         ('[{"from": "/media", "to": 5}, {"from": "/media", "to": "/mnt/media"}]',
                          "/mnt/media/film-0.mkv")):
    db.set_setting("path_mappings", broken)
    paths.clear()
    result = asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"]))
    check(result.get("status") == "ok" and paths == [expected],
          f"a mapping entry of the wrong shape is skipped ({broken}: {result.get('status')}, {paths})")
db.set_setting("path_mappings", "[]")

paths.clear()
asyncio.run(scanner.run_tech_scan(only_missing=False, library_id="lib-b"))
check(paths == ["/media/film-2.mkv"], f"library_id analyses only that library ({paths})")

paths.clear()
asyncio.run(scanner.run_tech_scan(only_missing=False, limit=1))
check(len(paths) == 1, f"limit=1 analyses one item ({len(paths)})")

before = task_count("tech")
result = asyncio.run(scanner.run_tech_scan(item_ids=[]))
check(result == {"status": "ok", "total": 0, "ok": 0, "failed": 0},
      f"an empty list of items: nothing ({result})")
check(task_count("tech") == before, "and the task is not even created")

asyncio.run(scanner.run_tech_scan(only_missing=False))     # now all of them have ffprobe
before = task_count("tech")
result = asyncio.run(scanner.run_tech_scan(only_missing=True))
check(result == {"status": "ok", "total": 0, "ok": 0, "failed": 0},
      f"nothing to analyse: nothing ({result})")
record = scanner.last_scan("tech")
check(task_count("tech") == before + 1 and record is not None and record["status"] == "done"
      and record["message"] == "Nothing to analyse.",
      "and the task is written with the key 'Nothing to analyse.' (translated when shown)")

print()
print("--- analysis: streams without a language ask Jellyfin all at once ---")


async def probe_without_language(path: str, binary: str) -> dict[str, Any]:
    paths.append(path)
    return {"container": "mkv", "video_codec": "h264", "size_bytes": 1,
            "streams": [{"stream_index": 0, "type": "Video", "codec": "h264", "language": languages.UNKNOWN},
                        {"stream_index": 1, "type": "Audio", "codec": "aac", "language": languages.UNKNOWN}]}


probe.probe_file = probe_without_language  # type: ignore[assignment]
# From before: the item pol-1 (the same library) has subtitles without a
# language and without a source mark, pol-2 (another library) too - that one must not ask.
with db.connect() as conn:
    for item in ("pol-1", "pol-2"):
        conn.execute("INSERT INTO item_streams (item_id, stream_index, type, language)"
                     " VALUES (?, 5, 'Subtitle', ?)", (item, languages.UNKNOWN))
requests: list[list[str]] = []


async def fill_ok(ids: list[str]) -> int:
    requests.append(ids)
    return 4


restore.append(substitute(scanner, "fill_languages_from_jellyfin", fill_ok))
result = asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"],
                                           library_id="lib-a"))
check(requests == [["pol-0"]], f"only the analysed item, nothing from before outside the list ({requests})")
requests.clear()
# Only pol-0 is analysed (the others have ffprobe already); pol-1 comes from before.
with db.connect() as conn:
    conn.execute("UPDATE items SET tech_source = NULL WHERE id = 'pol-0'")
paths.clear()
result = asyncio.run(scanner.run_tech_scan(only_missing=True, library_id="lib-a"))
check(paths == ["/media/film-0.mkv"] and requests == [["pol-0", "pol-1"]],
      f"from the analysis and from before with one request, sorted, only that library ({paths}, {requests})")
check(result.get("filled") == 4, f"the number filled in is returned ({result.get('filled')})")

# The sorting shows only on more items: two (and even five) can be in the
# right order by chance too - the order of a set depends on PYTHONHASHSEED.
# With twelve it did not happen for any of 200 seeds. The names are on purpose
# in a different order than the ids - the analysis goes by the name.
SORTED = [f"pol-c{i:02d}" for i in range(12)]
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, synced_at)"
                 " VALUES ('lib-c', 'lib-c', 'movies', ?)", (db.utcnow(),))
    for name, item in zip("ABCDEFGHIJKL", [SORTED[(i * 5) % 12] for i in range(12)]):
        conn.execute("INSERT INTO items (id, name, type, library_id, path, is_missing, synced_at)"
                     " VALUES (?, ?, 'Movie', 'lib-c', ?, 0, ?)",
                     (item, f"Film {name}", f"/media/{item}.mkv", db.utcnow()))
requests.clear()
asyncio.run(scanner.run_tech_scan(only_missing=False, library_id="lib-c"))
check(requests == [SORTED], f"twelve items with one request, sorted - not in the order of the analysis ({requests})")


async def fill_error(ids: list[str]) -> int:
    raise JellyfinError("Jellyfin neodpovídá")


restore.append(substitute(scanner, "fill_languages_from_jellyfin", fill_error))
result = asyncio.run(scanner.run_tech_scan(only_missing=False, library_id="lib-a"))
check(result.get("status") == "ok" and result.get("filled") == 0,
      f"an unreachable Jellyfin does not spoil the analysis ({result.get('status')}, {result.get('filled')})")

print()
print("--- analysis: the task message's suffixes ---")
probe.probe_file = probe_ok  # type: ignore[assignment]
restore += [substitute(scanner, "candidates_for_language_from_name", lambda *a, **k: ["pol-0"]),
            substitute(scanner, "fill_languages_from_name", lambda ids: 2),
            substitute(scanner, "candidates_for_range_from_jellyfin", lambda *a, **k: ["pol-0"])]


async def range_ok(ids: list[str]) -> int:
    return 3


restore.append(substitute(scanner, "fill_range_from_jellyfin", range_ok))
asyncio.run(scanner.run_tech_scan(only_missing=False, item_ids=["pol-0"]))
row = scanner.last_scan("tech") or {}
message = i18n.stored_text(row.get("message"), row.get("message_values"), "cs")   # a key and its values, read in Czech
check(", odhadnut z názvu u 2 titulů" in message, f"the suffix about the guess from the name ({message})")
check(", rozsah z Jellyfinu u 3 titulů" in message, "the suffix about the range from Jellyfin")
for v in reversed(restore):
    v()


print()
print("--- Jellystat import: the playback table is missing ---")
result = asyncio.run(importers.import_jellystat_json(json.dumps({"neco": []}).encode()))
check(result.get("status") == "error", f"a file without the table is an error ({result.get('status')})")
record = scanner.last_scan("import")
check(record is not None and record["status"] == "error"
      and record["message"] == "There is no playback table (jf_playback_activity) in the file. Is it really a Jellystat backup?",
      "and the task writes it")

print()
print("--- Jellystat import: broken records and a duplicate in the file ---")
VALID = {"Id": "r1", "UserId": "u1", "UserName": "Jana", "NowPlayingItemId": "film-x",
         "NowPlayingItemName": "Matrix", "ActivityDateInserted": "2026-08-01 20:00:00",
         "PlaybackDuration": "1800", "Client": "Jellyfin Web", "DeviceName": "Chrome"}
backup = [VALID, dict(VALID), "neni slovnik",
          {"Id": "r2", "UserId": "u1", "NowPlayingItemId": "film-x", "PlaybackDuration": "900"},
          dict(VALID, Id="r3", PlaybackDuration="abc")]
result = asyncio.run(importers.import_jellystat_json(json.dumps(backup).encode(), min_seconds=60))
check(result.get("found") == 5, f"five records were found ({result.get('found')})")
check(result.get("imported") == 1, f"one was imported ({result.get('imported')})")
check(result.get("duplicate") == 1, f"the second same one is a duplicate ({result.get('duplicate')})")
check(result.get("too_short") == 1,
      f"an unreadable length = 0 s = too short ({result.get('too_short')})")
check(result.get("known_elsewhere") == 0 and result.get("repaired") == 0,
      "a non-dict and a record without a time are skipped quietly")

print()
print("--- Jellystat import: fixing an old import, a record twice in the file ---")
# The old import took the series' id instead of the episode's. The same row of
# the backup is now twice in the file, with the episode's id: the first is fixed
# in place, the second is a duplicate already. Were the fix to change only a copy
# of the known keys, it would be fixed twice and the second would pass as new.
with db.connect() as conn:
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at,"
                 " last_seen_at, ended_at, watched_seconds, paused_seconds, is_paused, is_active)"
                 " VALUES ('import:jst:r9:serial-z', 'u1', 'Jana', 'serial-z', 'Seriál',"
                 " '2026-07-02 20:00:00', '2026-07-02 20:00:00', '2026-07-02 20:00:00', 2400, 0, 0, 0)")
FIX = dict(VALID, Id="r9", NowPlayingItemId="serial-z", NowPlayingItemName="Seriál",
           SeriesName="Seriál", EpisodeId="dil-z", EpisodeName="Pilot",
           ActivityDateInserted="2026-07-02 20:00:00", PlaybackDuration="2400")
result = asyncio.run(importers.import_jellystat_json(json.dumps([FIX, dict(FIX)]).encode(),
                                                     min_seconds=60))
check(result.get("repaired") == 1 and result.get("duplicate") == 1 and result.get("imported") == 0,
      f"fixed once, a duplicate the second time, nothing new ({result.get('repaired')}, "
      f"{result.get('duplicate')}, {result.get('imported')})")
keys = [r["session_key"] for r in db.query_all(
    "SELECT session_key FROM playback WHERE session_key LIKE 'import:jst:r9:%'")]
check(keys == ["import:jst:r9:dil-z"], f"the history has one row and it points at the episode ({keys})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
