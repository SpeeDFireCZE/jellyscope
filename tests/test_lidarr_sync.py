# -*- coding: utf-8 -*-
r"""The sync of Lidarr's history into the table lidarr_event.

Guarded:

A) a history record as a row: the time in UTC in the database's format, the album's name, the
   artist, the track's id (it pairs the upgrades), the album's release date, the grab's source, the
   protocol, the size - for a record whose album is no longer in Lidarr too (the release's name
   stays instead of the name); every event type of the recording is one row;
B) the first sync fetches the whole history page by page, the next ones ask since the last minus an
   hour and double nothing (upsert by the record's id); an import without a download id (a manual
   one) is stored with an empty one;
C) 5000 records at once are written within a few seconds;
D) an error (a wrong key) is written into the state without the key and does not move the sync mark;
E) deleting the downloaded data returns the count and clears the mark, the albums and the artists
   too; a deletion during a sync is refused and a sync during the deletion too.

Lidarr does not run: the history is built by hand in the shape of the API v1 (tests/lidarr_responses/)
and httpx.MockTransport returns it.

Run:
    .venv/bin/python tests/test_lidarr_sync.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lidarr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import no_network  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import lidarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "lidarr_responses"
failures = 0
db.init_db()
no_network()      # what has no transport of its own here must not reach the network


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> list[dict]:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM lidarr_event"))


def app_with_history(history: list[dict], requests: list[str] | None = None, code: int = 200, status: dict | None = None) -> httpx.MockTransport:
    """An app with the given history: `history/since` (the whole of it since a date), the paged `history`, the lists of albums and artists.

    It notes `since <date>` or `page <number> of <size>` into `requests`. It orders the pages by date
    ascending like a real Lidarr.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if code != 200:
            return httpx.Response(code)
        params = request.url.params
        path = request.url.path
        if path.endswith("/api/v1/history/since"):
            if requests is not None:
                requests.append(f"since {params.get('date', '')}")
            return httpx.Response(200, json=history)
        if path.endswith("/api/v1/history"):
            page, size = int(params.get("page", "1")), int(params.get("pageSize", "10"))
            if requests is not None:
                requests.append(f"page {page} of {size}")
            ordered = sorted(history, key=lambda r: (r["date"], r["id"]))
            return httpx.Response(200, json={"page": page, "pageSize": size, "totalRecords": len(history),
                                             "records": ordered[(page - 1) * size:page * size]})
        if path.endswith("/api/v1/system/status"):
            return httpx.Response(200, json=status if status is not None else recorded("system_status"))
        if path.endswith("/api/v1/album"):
            return httpx.Response(200, json=recorded("album"))
        if path.endswith("/api/v1/artist"):
            return httpx.Response(200, json=recorded("artist"))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


HISTORY = recorded("history_since")
TYPES = {r["eventType"] for r in HISTORY}

print("--- A) a history record -> a row ---")
grab = next(r for r in HISTORY if r["eventType"] == "grabbed" and r["data"].get("releaseSource") == "InteractiveSearch")
r = lidarr.event_from_history(grab)
check(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", r["happened_at"] or "") is not None, f"the date in the database's format ({r['happened_at']})")
check(r["title"] == "Hvězdy" and r["artist"] == "Kryštof", f"the album and the artist ({r['title']}, {r['artist']})")
check(r["title_id"] == 11 and r["artist_id"] == 1 and r["item_id"] is None, f"title_id = the album, artist_id = the artist, a grab has no track ({r['title_id']}, {r['artist_id']}, {r['item_id']})")
check(r["source"] == "InteractiveSearch" and r["indexer"] == "test-indexer" and r["protocol"] == "usenet" and r["client"] == "SABnzbd",
      f"the source, the indexer, the protocol (\"1\" = usenet) and the client ({r['source']}, {r['indexer']}, {r['protocol']}, {r['client']})")
check(r["size_bytes"] == int(grab["data"]["size"]) and r["release_group"] == "FLACGRP", "the size (a number from a string) and the release group")
check(r["released_at"] == "2019-05-17 00:00:00", f"the album's release date in UTC ({r['released_at']})")
check(r["quality"] == "FLAC" and r["cf_score"] == 0 and r["download_id"] == "SABnzbd_nzo_bbb222", f"the quality, the score, the download id ({r['quality']}, {r['cf_score']}, {r['download_id']})")
imported = next(x for x in HISTORY if x["eventType"] == "trackFileImported")
r = lidarr.event_from_history(imported)
check(r["item_id"] == imported["trackId"] and r["size_bytes"] == int(imported["data"]["size"]) and r["client"] == "SABnzbd main" and r["release_group"] == "WRE",
      f"a track's import: the track, the size, the client and the group from data ({r['item_id']}, {r['size_bytes']}, {r['client']})")
shifted = {**grab, "date": "2026-11-12T22:50:18.1234567+02:00"}
check(lidarr.event_from_history(shifted)["happened_at"] == "2026-11-12 20:50:18", "a time with an offset and seven decimal places (.NET) converts to UTC")
without = next(x for x in HISTORY if x["album"] is None)
r = lidarr.event_from_history(without)
check(r["title"] == without["sourceTitle"] and r["artist"] == "" and r["released_at"] is None and r["title_id"] == 21,
      "an album deleted in the app: the release's name instead of the name, no artist, no release date")
foreign = {**grab, "data": {**grab["data"], "protocol": "2", "releaseSource": "4", "size": "neni-cislo"}}
r = lidarr.event_from_history(foreign)
check(r["protocol"] == "torrent" and r["source"] == "InteractiveSearch" and r["size_bytes"] is None,
      "a numeric protocol and source translate into names, a size that is no number = unknown")
check(set(r) == set(lidarr.sync.COLUMNS), "the row has exactly the columns of Lidarr's table")
deletion = next(x for x in HISTORY if x["eventType"] == "trackFileDeleted" and x["data"].get("reason") == "Upgrade")
check(lidarr.event_from_history(deletion)["reason"] == "Upgrade", "the reason the file was deleted (Upgrade)")
failed = next(x for x in HISTORY if x["eventType"] == "downloadFailed")
check(lidarr.event_from_history(failed)["event_type"] == "downloadFailed" and lidarr.event_from_history(failed)["protocol"] == "torrent", "a failed download")
check(lidarr.event_from_history({"id": "x"})["arr_id"] is None and lidarr.event_from_history({})["arr_id"] is None, "a record without a numeric id has no id (it is dropped by the sync)")

print()
print("--- B) sync ---")
lidarr.save_settings(True, "http://nas:8686/lidarr", "tajny-klic")
requests: list[str] = []
result = asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY, requests)))
n = count()
check(result["new_count"] == n == len(HISTORY) and result["total"] == len(HISTORY),
      f"the first sync fetches the whole history ({result}, in the database {n})")
paged = [q for q in requests if q.startswith(("page ", "since "))]
check(bool(paged) and all(q.startswith("page ") and int(q.split()[-1]) <= 1000 for q in paged), f"the first time page by page, a page of at most 1000 records ({paged})")
check({r["event_type"] for r in db.query_all("SELECT DISTINCT event_type FROM lidarr_event")} == TYPES and len(TYPES) >= 9,
      f"every event type of the recording is stored ({sorted(TYPES)})")
requests.clear()
mark = db.get_setting("arr_lidarr_last_sync", "")
result = asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY, requests)))
check(count() == n and result["new_count"] == 0, f"a repeated sync doubles nothing ({result})")
expected = (datetime.strptime(mark, db.TIME_FORMAT) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if mark else "?"
check(f"since {expected}" in requests and not any(q.startswith("page ") for q in requests), f"the second time it asks since the last sync minus an hour ({requests} vs {expected})")
state = lidarr.sync_state()
check(set(state) == {"at", "result", "new_count", "error"} and state["result"] == "ok" and state["error"] == "" and state["new_count"] == 0 and state["at"],
      f"the sync state ({state})")
check(lidarr.event_count() == n, "event_count counts the stored records")
row = db.query_one("SELECT * FROM lidarr_event WHERE event_type = 'grabbed' AND source = 'InteractiveSearch'")
check(bool(row and row["indexer"] and row["artist"] == "Kryštof"), "a grab by an interactive pick is in the database")
check(db.query_value("SELECT COUNT(*) FROM lidarr_event WHERE event_type = 'trackFileImported' AND download_id IS NULL") == 2,
      "a manual import (two tracks of one album) has no download id")
check(db.query_value("SELECT COUNT(*) FROM lidarr_event WHERE event_type = 'trackFileImported' AND download_id IS NOT NULL") == 8,
      "the other imports have the download id that pairs them with the grab")
check(db.query_value("SELECT COUNT(*) FROM lidarr_event WHERE reason = 'Upgrade'") == 3, "the three tracks deleted because of the upgrade have the reason")
check(db.query_value("SELECT COUNT(DISTINCT item_id) FROM lidarr_event WHERE item_id IS NOT NULL AND event_type IN ('trackFileDeleted', 'trackFileImported') AND title_id = 11") == 3,
      "an upgrade pairs by the track: the same three track ids on the deletion and on both imports")
check(db.query_value("SELECT COUNT(*) FROM lidarr_event WHERE event_type = 'grabbed' AND item_id IS NOT NULL") == 0, "a grab has no track id (0 in the answer = none)")
changed = [{**r, "data": {**r["data"], "reason": "MissingFromDisk"}} if r["eventType"] == "trackFileDeleted" else r for r in HISTORY]
asyncio.run(lidarr.synchronize(transport=app_with_history(changed)))
check(db.query_value("SELECT COUNT(*) FROM lidarr_event WHERE reason = 'MissingFromDisk'") == 4, "the upsert rewrites a changed record (it does not just skip it)")
asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY)))

# The state and the mark change with every sync - they must not flood the settings log.
db.set_setting("arr_lidarr_state", "{}")
db.set_setting("arr_lidarr_last_sync", "2026-01-01 00:00:00")
caught: list[str] = []
catcher = logging.Handler()
catcher.emit = lambda record: caught.append(record.getMessage())
logging.getLogger("jellyscope.db").addHandler(catcher)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)
asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY)))
logging.getLogger("jellyscope.db").removeHandler(catcher)
check(not [c for c in caught if "arr_lidarr_state" in c or "arr_lidarr_last_sync" in c], f"the sync does not log the change of the state and the mark ({caught[:3]})")
check("arr_lidarr_api_key" in db.SECRET_SETTINGS and "arr_lidarr_titles" in db.UNLOGGED_SETTINGS and "arr_lidarr_titles_version" in db.UNLOGGED_SETTINGS
      and "arr_lidarr_state" in db.UNLOGGED_SETTINGS and "arr_lidarr_last_sync" in db.UNLOGGED_SETTINGS, "the key is secret, the marks are not logged")

print()
print("--- C) 5000 records ---")
lidarr.delete_data()
big = [{**HISTORY[0], "id": 100000 + i} for i in range(5000)]
big_requests: list[str] = []
start = time.perf_counter()
result = asyncio.run(lidarr.synchronize(transport=app_with_history(big, big_requests)))
duration = time.perf_counter() - start
check(count() == 5000 and result["new_count"] == 5000 and result["total"] == 5000 and duration < 10, f"5000 records in {duration:.1f} s")
check(len([q for q in big_requests if q.startswith("page ")]) >= 5 and not any(q.startswith("since") for q in big_requests), "5000 records page by page")
check(lidarr.delete_data() == 5000 and count() == 0 and db.get_setting("arr_lidarr_last_sync", "") == "", "deleting the downloaded data returns the count and clears the sync mark")
asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY)))
check(count() == n, "and the next sync fetches the whole history again")

print()
print("--- D) a wrong key ---")
mark_before = db.get_setting("arr_lidarr_last_sync", "")
try:
    asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY, code=401)))
    refused = ""
except lidarr.LidarrError as error:
    refused = str(error)
state = lidarr.sync_state()
check(bool(refused) and state["result"] == "error" and "401" in state["error"] and "tajny-klic" not in state["error"] and "tajny-klic" not in refused,
      f"Lidarr's error is in the state, without the key ({state['error']})")
check(db.get_setting("arr_lidarr_last_sync", "") == mark_before and count() == n, "the error did not move the sync mark and deleted nothing")
asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY)))
check(lidarr.sync_state()["result"] == "ok", "the next successful sync fixes the state again")
lidarr.save_settings(False, "", "")
try:
    asyncio.run(lidarr.synchronize())
    missing = ""
except lidarr.LidarrError as error:
    missing = str(error)
check("tajny-klic" not in missing and lidarr.sync_state()["result"] == "error", "a switched-off app with no address: an error without the key")
# The saved key goes only to the server it was saved for: after the address was cleared it is typed again.
lidarr.save_settings(True, "http://nas:8686/lidarr", "tajny-klic")

mark_before = db.get_setting("arr_lidarr_last_sync", "")
with db.connect() as conn:
    conn.execute("DELETE FROM lidarr_event")
    conn.commit()
db.set_setting("arr_lidarr_last_sync", "")
db.forget_settings()
try:
    asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY, status={"appName": "Readarr", "version": "0.4"})))
    other = ""
except lidarr.LidarrError as error:
    other = str(error)
check("Readarr answers at this address, not Lidarr" in other and lidarr.sync_state()["result"] == "error" and count() == 0
      and db.get_setting("arr_lidarr_last_sync", "") == "", f"another app at the address: the connection test's error, no row written, the mark unmoved ({other})")
db.set_setting("arr_lidarr_last_sync", mark_before)
db.forget_settings()

print()
print("--- E) deleting the downloaded data ---")
asyncio.run(lidarr.synchronize(transport=app_with_history(HISTORY)))
check(db.query_value("SELECT COUNT(*) FROM lidarr_title") == 4, "the albums are stored with the history")
lidarr.sync._RUNNING.add(lidarr.NAME)
try:
    lidarr.delete_data()
    refused_during_sync = ""
except lidarr.LidarrError as error:
    refused_during_sync = str(error)
lidarr.sync._RUNNING.discard(lidarr.NAME)
check("running" in refused_during_sync and count() == n, f"downloaded data is not deleted during a sync ({refused_during_sync})")


async def delete_and_sync() -> str:
    deletion = asyncio.create_task(lidarr.delete_downloaded_data())
    await asyncio.sleep(0)                      # the deletion already holds the app and runs in a thread
    try:
        await lidarr.synchronize(transport=app_with_history(HISTORY))
        return "the sync went through"
    except lidarr.LidarrError as error:
        return str(error)
    finally:
        await deletion


concurrent_result = asyncio.run(delete_and_sync())
check("already running" in concurrent_result and count() == 0, f"a sync started during the deletion is refused ({concurrent_result})")
check(not lidarr.sync_running(), "after the deletion the app is free again")
check(db.query_value("SELECT COUNT(*) FROM lidarr_title") == 0 and db.get_setting("arr_lidarr_last_sync", "") == "" and lidarr.sync_state()["at"] == "",
      "the deletion took the albums and the marks too")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
