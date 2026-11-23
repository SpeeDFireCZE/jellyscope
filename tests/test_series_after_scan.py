# -*- coding: utf-8 -*-
r"""The episodes of a new series must not fall apart in Recently added.

During a scan the items in Jellyfin change under one's hands. The quick sync
stores an episode at the moment when Jellyfin:

1. has **not sorted it to the series** yet (SeriesId is missing) - and then
   sorts it under the same id, or
2. **creates it again under a new id** - the old record (without a series)
   vanishes, the new one is sorted right, both with the same path and date
   added.

The quick sync used to go back only five minutes before the last known title,
so it never came back to such an episode. In Recently added it stood outside
its series as a standalone episode until the full sync fixed it. Now every run
goes back a day and checks by id what Jellyfin did not send.

Run:
    .venv/bin/python tests/test_series_after_scan.py
"""
from __future__ import annotations

import asyncio
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "serial.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, i18n, scanner, stats  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

now = datetime.now(timezone.utc)


def time(hours_back: float) -> str:
    return (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def episode(item_id: str, number: int, series: str | None, hours_back: float) -> dict[str, Any]:
    return {"Id": item_id, "Name": f"Díl {number}", "Type": "Episode",
            "SeriesId": series, "SeriesName": "Kocour Mikeš" if series else None,
            "IndexNumber": number, "ParentIndexNumber": 1,
            "DateCreated": time(hours_back),
            "Path": f"/media/serialy/Mikes/S01E{number:02d}.mkv"}


# What Jellyfin knows right now. The test changes it between runs - the way
# it changes during a scan.
LIBRARY: list[dict[str, Any]] = []


class FakeClient:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return []

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib", "Name": "Seriály", "CollectionType": "tvshows"}]

    async def items_page(self, start: int, limit: int, *a: Any, **k: Any) -> dict[str, Any]:
        ordered = sorted(LIBRARY, key=lambda i: i["DateCreated"], reverse=True)
        return {"Items": ordered[start:start + limit], "TotalRecordCount": len(ordered)}

    async def items_by_ids(self, ids: Any) -> list[dict[str, Any]]:
        return [i for i in LIBRARY if i["Id"] in set(ids)]

    async def _first_admin_id(self) -> str | None:
        return None

    recent_items = JellyfinClient.recent_items


substitute(scanner, "JellyfinClient", FakeClient)


def quick() -> dict[str, Any]:
    return asyncio.run(scanner.sync_recent())


def series_cards() -> list[dict[str, Any]]:
    return [k for k in stats.recently_added(20)
            if "Mikeš" in str(k.get("title") or "") or k["id"].startswith("e")]


# --- 1st run: the scan is in progress ----------------------------------
# Episodes 1-3 are sorted already, episode 4 not yet, episode 5 Jellyfin
# created under a temporary id and without a series. Episode 6 is two hours
# newer - it moves the boundary the original quick sync did not go back past.
LIBRARY[:] = [episode("e1", 1, "mikes", 3), episode("e2", 2, "mikes", 3),
              episode("e3", 3, "mikes", 3), episode("e4", 4, None, 3),
              episode("e5-docasny", 5, None, 3), episode("e6", 6, "mikes", 1)]
first = quick()
check(first["status"] == "ok", f"the first run ({first.get('message')})")
# History on the temporary record - somebody started watching right away.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " item_type, started_at, last_seen_at, watched_seconds, is_active)"
        " VALUES ('k', 'u1', 'Jana', 'e5-docasny', 'Díl 5', 'Episode', ?, ?, 1200, 0)",
        (db.utcnow(), db.utcnow()))
    conn.commit()
before = series_cards()
print(f"       after the first run: {len(before)} cards {[k['title'] for k in before]}")
check(len(before) > 1, "in the middle of the scan the series really falls apart (the test's starting state)")

# --- 2nd run: the scan finished -----------------------------------------
# Episode 4 is sorted (the same id), episode 5 has a new id and Jellyfin does
# not know the temporary one.
LIBRARY[:] = [episode("e1", 1, "mikes", 3), episode("e2", 2, "mikes", 3),
              episode("e3", 3, "mikes", 3), episode("e4", 4, "mikes", 3),
              episode("e5", 5, "mikes", 3), episode("e6", 6, "mikes", 1)]
second = quick()
check(second["status"] == "ok", f"the second run ({second.get('message')})")

e4 = db.query_one("SELECT series_id FROM items WHERE id = 'e4'")
check(e4 and e4["series_id"] == "mikes", "episode 4 is sorted to the series afterwards")
temporary = db.query_one("SELECT is_missing FROM items WHERE id = 'e5-docasny'")
check(temporary and temporary["is_missing"] == 1, "the temporary record of episode 5 is in the archive")
check(db.query_one("SELECT series_id FROM items WHERE id = 'e5'")["series_id"] == "mikes",
      "and the new episode 5 is under the series")
history = db.query_one("SELECT item_id FROM playback WHERE session_key = 'k'")
check(history["item_id"] == "e5", "the history moved to the new episode 5 (the same file path)")

after = series_cards()
check(len(after) == 1 and after[0]["title"] == "Kocour Mikeš",
      f"Recently added has one card ({[k['title'] for k in after]})")
check(len(after[0]["episodes"]) == 6 if after else False,
      f"with all six episodes ({len(after[0]['episodes']) if after else 0})")
_row = scanner.last_scan("recent") or {}
message = i18n.stored_text(_row.get("message"), _row.get("message_values"))   # a stored key and its values
check("znovu" in message or "again" in message, f"the task's log says so ({message})")

# --- 3. safeguards ------------------------------------------------------
# Older than a day is not checked - the full sync is for that.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, date_created, is_missing, path)"
                 " VALUES ('stary', 'Starý film', 'Movie', ?, 0, '/m/stary.mkv')",
                 (time(24 * 10),))
    conn.commit()
quick()
check(db.query_one("SELECT is_missing FROM items WHERE id = 'stary'")["is_missing"] == 0,
      "an item older than a day that Jellyfin did not send stays (that is not the quick sync's job)")

# When Jellyfin knows an item but did not send it (the cap), it must not be archived.
LIBRARY_BACKUP = list(LIBRARY)
original_recent = FakeClient.recent_items


async def without_episode_2(self, since, cap=2000, item_types="Movie,Episode", parent_id=None):
    everything = await original_recent(self, since, cap, item_types, parent_id)
    return [i for i in everything if i["Id"] != "e2"]

FakeClient.recent_items = without_episode_2        # type: ignore[assignment]
quick()
FakeClient.recent_items = original_recent    # type: ignore[assignment]
check(db.query_one("SELECT is_missing FROM items WHERE id = 'e2'")["is_missing"] == 0,
      "an episode Jellyfin knows but did not send in the run is not lost")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
