# -*- coding: utf-8 -*-
"""Stopping a running task must not leave the database in a mess.

A task is **not interrupted in the middle of work**. Only a flag is set and the loop
notices it when it finishes the item in progress - only then it ends. A hard
interruption (`task.cancel()`) could come in the middle of a write and leave
half a batch behind.

The most dangerous place is `_mark_missing()`: after a sync it marks
as gone everything that was not seen in this run. But when the run stops
halfway, "not seen" is most of the library - and without protection titles that
are normally in Jellyfin would vanish into the archive. This test is devoted to exactly that
the most.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a stand-in in all submodules of the scanner package

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice
# of the database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "stop.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# How many items get through before somebody presses Stop.
STOP_AFTER = 250
TOTAL = 1000


class FakeClient:
    """A Jellyfin that returns 1000 items - and after the 250th Stop is "pressed"."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": "u1", "Name": "Karel", "Policy": {}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib-filmy", "Name": "Filmy", "CollectionType": "movies"}]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        # This we ask Jellyfin in advance, so the progress can be shown.
        return TOTAL

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        for i in range(TOTAL):
            if i == STOP_AFTER:
                # This the user does by clicking Stop.
                scanner.request_stop()
            yield {
                "Id": f"film-{i:04d}",
                "Name": f"Film {i}",
                "Type": "Movie",
                "DateCreated": "2026-01-01T00:00:00.0000000Z",
                "RunTimeTicks": 60_000_000_000,
            }


db.init_db()

# The library as it looks after an earlier successful sync. These items
# are still in Jellyfin - the stopped run must not mark them as gone.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO libraries (id, name, collection_type, synced_at) VALUES (?,?,?,?)",
        ("lib-filmy", "Filmy", "movies", db.utcnow()),
    )
    for i in range(TOTAL):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, date_created,
                                  is_missing, synced_at)
               VALUES (?,?,'Movie','lib-filmy',?,0,?)""",
            (f"film-{i:04d}", f"Film {i}", "2026-01-01 00:00:00",
             "2020-01-01 00:00:00"),
        )

check(db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 1") == 0,
      "at the start nothing is marked as gone")


print()
print("--- a sync we stop in the middle ---")
substitute(scanner, "JellyfinClient", FakeClient)
result = asyncio.run(scanner.sync_library())

check(result.get("status") == "stopped", f"the task reports stopping: {result}")
check(result.get("items") is not None and result["items"] < TOTAL,
      f"not the whole library went through ({result.get('items')} of {TOTAL})")
check(result.get("items", 0) >= STOP_AFTER,
      f"but the batch in progress was finished ({result.get('items')} >= {STOP_AFTER})")


print()
print("--- and above all: the library stayed in order ---")
gone = db.query_value("SELECT COUNT(*) FROM items WHERE is_missing = 1")
check(gone == 0, f"no title was marked as gone ({gone})")
check(db.query_value("SELECT COUNT(*) FROM items") == TOTAL,
      "the number of titles did not change")
# What got done is stored - with the new sync time too.
updated = db.query_value(
    "SELECT COUNT(*) FROM items WHERE synced_at > '2021-01-01'")
check(updated >= STOP_AFTER,
      f"what got done is stored ({updated} items)")


print()
print("--- the record in the log says it out loud ---")
record = scanner.last_scan("library")
check(record is not None and record["status"] == "stopped",
      f"the task's state is 'stopped' ({record['status'] if record else '-'})")
check(record is not None and "Stopped at your request" in (record["message"] or ""),
      f"and the message explains it: {record['message'] if record else '-'}")


print()
print("--- the flag leaves nothing behind ---")
check(not scanner.stop_requested(),
      "the flag was cleared after the task ended (otherwise the next task would end at once)")
check(not scanner.is_scan_running(), "the lock is released")

# The second run - nothing stops it any more - must go through whole.
class ClientWithoutStop(FakeClient):
    async def iter_items(self, parent_id: str):       # noqa: ANN201
        for i in range(TOTAL):
            yield {
                "Id": f"film-{i:04d}", "Name": f"Film {i}", "Type": "Movie",
                "DateCreated": "2026-01-01T00:00:00.0000000Z",
                "RunTimeTicks": 60_000_000_000,
            }


substitute(scanner, "JellyfinClient", ClientWithoutStop)
result = asyncio.run(scanner.sync_library())
check(result.get("status") == "ok", f"the second run went through whole: {result.get('status')}")
check(result.get("items") == TOTAL, f"all {TOTAL} items went through ({result.get('items')})")


print()
print("--- stopping does not work when nothing runs ---")
check(scanner.request_stop() is False, "request_stop() returns False")
check(not scanner.stop_requested(), "and does not set the flag")
# If it set it, the next task started would end straight away.
substitute(scanner, "JellyfinClient", ClientWithoutStop)
result = asyncio.run(scanner.sync_library())
check(result.get("status") == "ok", "the next task did not end because of it")


print()
print("--- the same with the technical analysis ---")
# Here the files are processed several at once (a semaphore). Stopping
# must let the ones in progress finish and only skip the ones not reached -
# otherwise a file analysed halfway would stay in the database.
from jellyscope import probe  # noqa: E402

with db.connect() as conn:
    for i in range(TOTAL):
        conn.execute("UPDATE items SET path = ? WHERE id = ?",
                     (f"/media/film-{i:04d}.mkv", f"film-{i:04d}"))

running = {"current": 0, "max": 0, "done": 0}


async def fake_probe(path: str, ffprobe_bin: str) -> dict[str, Any]:
    running["current"] += 1
    running["max"] = max(running["max"], running["current"])
    await asyncio.sleep(0.002)
    running["done"] += 1
    if running["done"] == 40:
        scanner.request_stop()
    running["current"] -= 1
    return {"container": "mkv", "video_codec": "h264", "size_bytes": 1}


probe.find_ffprobe = lambda p="": "/usr/bin/ffprobe"     # type: ignore[assignment]
probe.probe_file = fake_probe                             # type: ignore[assignment]
db.set_setting("tech_source", "ffprobe")

result = asyncio.run(scanner.run_tech_scan(only_missing=True))
check(result.get("status") == "stopped", f"the analysis reports stopping: {result.get('status')}")
check(result.get("skipped", 0) > 0, f"something was skipped ({result.get('skipped')})")
check(running["current"] == 0, "no file stayed in progress")

saved = db.query_value("SELECT COUNT(*) FROM items WHERE tech_source = 'ffprobe'")
check(saved == result["ok"],
      f"exactly as many were stored as were finished ({saved} = {result['ok']})")
check(saved + result["skipped"] + result["failed"] == TOTAL,
      "done + skipped + failed = all the files")

record = scanner.last_scan("tech")
check(record is not None and record["status"] == "stopped", "the log says 'stopped'")
check(record is not None and "the next analysis goes on" in (record["message"] or ""),
      "and mentions that the next analysis will continue")


print()
print("--- the page waits and refreshes itself ---")
# After a restart and after a task was stopped it formerly had to be refreshed by hand - and
# worse, it could not be told when it would work. `?wait=...` tells the page what to
# wait for; the handler is in static/js/app.js and asks /health.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(app)
client.post("/login", data={"username": "admin", "password": "longpassword"})

for address, expected in (
    ("/settings?section=tasks&wait=task", 'data-wait-for="task"'),
    ("/settings?section=general&wait=restart", 'data-wait-for="restart"'),
):
    html = client.get(address).text
    check(expected in html, f"{address} sets the waiting")
    check("wait-note" in html, "and explains what is waited for")

# Without the parameter nothing is waited for. This is also a safeguard against
# an endless loop: after the refresh `wait` is thrown away from the address, so
# the waiting does not start again.
check("data-wait-for" not in client.get("/settings?section=tasks").text,
      "without the parameter nothing is waited for")
# Nothing other than those two values may get from the address into the page.
for forged in ("cokoliv", "<script>", "restart; drop"):
    html = client.get("/settings?section=tasks", params={"wait": forged}).text
    check("data-wait-for" not in html, f"an unknown value is ignored: {forged!r}")

# /health must carry what is waited for.
state = client.get("/health").json()
for key in ("started_at", "task_running", "stop_pending"):
    check(key in state, f"/health reports {key}")
check(state["task_running"] is False, "and nothing runs now")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
