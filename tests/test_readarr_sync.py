# -*- coding: utf-8 -*-
r"""The sync of Readarr's history into the table readarr_event.

Guarded:

A) a history record as a row: the time in UTC in the database's format, the book's title, the
   author, the book file's id of an import, the book's release date, the grab's source, the protocol
   ("1" usenet, "2" torrent), the size, the download client (an import carries the client instance's
   name `downloadClientName`, a grab the client's type `downloadClient`) - for a record whose book is
   no longer in Readarr too (the release's name stays instead of the title); every event type of the
   recording is one row;
B) the first sync fetches the whole history page by page, the next ones ask since the last minus an
   hour and double nothing (upsert by the record's id); an import without a download id (a manual
   one) is stored with an empty one; the name of the app (Readarr, Chaptarr, Bookshelf) is stored on
   a sync;
C) 5000 records at once are written within a few seconds;
D) an error (a wrong key) is written into the state without the key and does not move the sync mark;
E) deleting the downloaded data returns the count and clears the mark, the books and the authors
   too; a deletion during a sync is refused and a sync during the deletion too.

Readarr does not run: the history is built by hand in the shape of the API v1 (tests/readarr_responses/;
the keys of `data` are exactly those Readarr's HistoryService writes for each event type) and
httpx.MockTransport returns it.

Run:
    .venv/bin/python tests/test_readarr_sync.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "readarr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import no_network  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import readarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "readarr_responses"
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
    return int(db.query_value("SELECT COUNT(*) FROM readarr_event"))


def app_with_history(history: list[dict], requests: list[str] | None = None, code: int = 200,
                     status: dict | None = None) -> httpx.MockTransport:
    """An app with the given history: `history/since` (the whole of it since a date), the paged `history`, the lists of books and authors.

    It notes `since <date>` or `page <number> of <size>` into `requests`. It orders the pages by date
    ascending like a real Readarr.
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
        if path.endswith("/api/v1/book"):
            return httpx.Response(200, json=recorded("book"))
        if path.endswith("/api/v1/author"):
            return httpx.Response(200, json=recorded("author"))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


HISTORY = recorded("history_since")
TYPES = {r["eventType"] for r in HISTORY}

print("--- A) a history record -> a row ---")
grab = next(r for r in HISTORY if r["eventType"] == "grabbed" and r["data"].get("releaseSource") == "InteractiveSearch")
r = readarr.event_from_history(grab)
check(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", r["happened_at"] or "") is not None, f"the date in the database's format ({r['happened_at']})")
check(r["title"] == "Válka s mloky" and r["author"] == "Karel Čapek", f"the book and the author ({r['title']}, {r['author']})")
check(r["title_id"] == 11 and r["author_id"] == 1 and r["item_id"] is None, f"title_id = the book, author_id = the author, a grab has no file ({r['title_id']}, {r['author_id']}, {r['item_id']})")
check(r["source"] == "InteractiveSearch" and r["indexer"] == "test-indexer" and r["protocol"] == "usenet" and r["client"] == "SABnzbd",
      f"the source, the indexer, the protocol (\"1\" = usenet) and the client's type from a grab ({r['source']}, {r['indexer']}, {r['protocol']}, {r['client']})")
check(r["size_bytes"] == int(grab["data"]["size"]) and r["release_group"] == "AZWGRP", "the size (a number from a string) and the release group")
check(r["released_at"] == "1936-01-01 00:00:00", f"the book's release date in UTC ({r['released_at']})")
check(r["quality"] == "AZW3" and r["cf_score"] == 0 and r["download_id"] == "SABnzbd_nzo_bbb222", f"the quality, the score, the download id ({r['quality']}, {r['cf_score']}, {r['download_id']})")
imported = next(x for x in HISTORY if x["eventType"] == "bookFileImported" and x["data"].get("fileId") == "1108")
r = readarr.event_from_history(imported)
check(r["item_id"] == 1108 and r["size_bytes"] == int(imported["data"]["size"]) and r["release_group"] == "AZWGRP",
      f"a book file's import: the file's id (`fileId`, not the history record's), the size and the group from data ({r['item_id']}, {r['size_bytes']}, {r['release_group']})")
check(r["client"] == "SABnzbd main" and imported["data"]["downloadClient"] == "SABnzbd",
      f"an import's client is the instance's name (`downloadClientName`), the type is only `downloadClient` ({r['client']})")
manual = next(x for x in HISTORY if x["eventType"] == "bookFileImported" and x["downloadId"] is None)
check(readarr.event_from_history(manual)["client"] == "" and readarr.event_from_history(manual)["release_group"] == "",
      "a manual import has no client and no group (null in data = empty)")
shifted = {**grab, "date": "2026-11-12T22:50:18.1234567+02:00"}
check(readarr.event_from_history(shifted)["happened_at"] == "2026-11-12 20:50:18", "a time with an offset and seven decimal places (.NET) converts to UTC")
without = next(x for x in HISTORY if x["book"] is None)
r = readarr.event_from_history(without)
check(r["title"] == without["sourceTitle"] and r["released_at"] is None and r["title_id"] == 21 and r["author"] == "Terry Pratchett",
      "a book deleted in the app: the release's name instead of the title, no release date (the author stays: the record has it)")
foreign = {**grab, "data": {**grab["data"], "protocol": "2", "releaseSource": "4", "size": "neni-cislo"}}
r = readarr.event_from_history(foreign)
check(r["protocol"] == "torrent" and r["source"] == "InteractiveSearch" and r["size_bytes"] is None,
      "a numeric protocol and source translate into names, a size that is no number = unknown")
check(set(r) == set(readarr.sync.COLUMNS), "the row has exactly the columns of Readarr's table")
deletion = next(x for x in HISTORY if x["eventType"] == "bookFileDeleted" and x["data"].get("reason") == "Upgrade")
check(readarr.event_from_history(deletion)["reason"] == "Upgrade" and readarr.event_from_history(deletion)["size_bytes"] is None,
      "the reason the file was deleted (Upgrade); a deletion carries no size")
failed = next(x for x in HISTORY if x["eventType"] == "downloadFailed")
r = readarr.event_from_history(failed)
check(r["event_type"] == "downloadFailed" and r["client"] == "qBittorrent" and r["indexer"] == "tracker" and r["size_bytes"] == 60000000
      and r["release_group"] == "TGRP" and r["protocol"] == "", f"a failed download: the client's type, the indexer, the size; no protocol in the record ({r})")
torrent = next(x for x in HISTORY if x["eventType"] == "grabbed" and x["data"].get("protocol") == "2")
check(readarr.event_from_history(torrent)["protocol"] == "torrent" and readarr.event_from_history(torrent)["client"] == "qBittorrent",
      "a torrent grab: protocol \"2\" = torrent")
ignored = next(x for x in HISTORY if x["eventType"] == "downloadIgnored")
check(readarr.event_from_history(ignored)["client"] == "SABnzbd main", "an ignored download keeps the client's name it came with")
every = {t: [x for x in HISTORY if x["eventType"] == t] for t in TYPES}
check(all(readarr.event_from_history(x)["event_type"] == t for t, xs in every.items() for x in xs), "every event type of the recording converts")
check(readarr.event_from_history({"id": "x"})["arr_id"] is None and readarr.event_from_history({})["arr_id"] is None, "a record without a numeric id has no id (it is dropped by the sync)")

print()
print("--- B) sync ---")
readarr.save_settings(True, "http://nas:8787/readarr", "tajny-klic")
requests: list[str] = []
result = asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY, requests)))
n = count()
check(result["new_count"] == n == len(HISTORY) and result["total"] == len(HISTORY),
      f"the first sync fetches the whole history ({result}, in the database {n})")
paged = [q for q in requests if q.startswith(("page ", "since "))]
check(bool(paged) and all(q.startswith("page ") and int(q.split()[-1]) <= 1000 for q in paged), f"the first time page by page, a page of at most 1000 records ({paged})")
check({r["event_type"] for r in db.query_all("SELECT DISTINCT event_type FROM readarr_event")} == TYPES and len(TYPES) >= 9,
      f"every event type of the recording is stored ({sorted(TYPES)})")
requests.clear()
mark = db.get_setting("arr_readarr_last_sync", "")
result = asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY, requests)))
check(count() == n and result["new_count"] == 0, f"a repeated sync doubles nothing ({result})")
expected = (datetime.strptime(mark, db.TIME_FORMAT) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if mark else "?"
check(f"since {expected}" in requests and not any(q.startswith("page ") for q in requests), f"the second time it asks since the last sync minus an hour ({requests} vs {expected})")
state = readarr.sync_state()
check(set(state) == {"at", "result", "new_count", "error"} and state["result"] == "ok" and state["error"] == "" and state["new_count"] == 0 and state["at"],
      f"the sync state ({state})")
check(readarr.event_count() == n, "event_count counts the stored records")
row = db.query_one("SELECT * FROM readarr_event WHERE event_type = 'grabbed' AND source = 'InteractiveSearch'")
check(bool(row and row["indexer"] and row["author"] == "Karel Čapek"), "a grab by an interactive pick is in the database")
check(db.query_value("SELECT COUNT(*) FROM readarr_event WHERE event_type = 'bookFileImported' AND download_id IS NULL") == 2,
      "a manual import (two files of one book) has no download id")
check(db.query_value("SELECT COUNT(*) FROM readarr_event WHERE event_type = 'bookFileImported' AND download_id IS NOT NULL") == 4,
      "the other imports have the download id that pairs them with the grab")
check(db.query_value("SELECT COUNT(*) FROM readarr_event WHERE reason = 'Upgrade'") == 1, "the file deleted because of the upgrade has the reason")
check(sorted(r["item_id"] for r in db.query_all("SELECT item_id FROM readarr_event WHERE title_id = 11 AND event_type = 'bookFileImported'")) == [1102, 1108],
      "the upgrade's import has another file id than the first one (the upgrade is paired by the book, not by the file)")
check(db.query_value("SELECT COUNT(*) FROM readarr_event WHERE event_type = 'grabbed' AND item_id IS NOT NULL") == 0, "a grab has no file id")
check(db.query_value("SELECT COUNT(*) FROM readarr_event WHERE event_type = 'bookFileDeleted' AND item_id IS NOT NULL") == 0, "a deletion carries no file id (Readarr does not write it)")
changed = [{**r, "data": {**r["data"], "reason": "MissingFromDisk"}} if r["eventType"] == "bookFileDeleted" else r for r in HISTORY]
asyncio.run(readarr.synchronize(transport=app_with_history(changed)))
check(db.query_value("SELECT COUNT(*) FROM readarr_event WHERE reason = 'MissingFromDisk'") == 2, "the upsert rewrites a changed record (it does not just skip it)")
asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY)))

# The state and the mark change with every sync - they must not flood the settings log.
db.set_setting("arr_readarr_state", "{}")
db.set_setting("arr_readarr_last_sync", "2026-01-01 00:00:00")
caught: list[str] = []
catcher = logging.Handler()
catcher.emit = lambda record: caught.append(record.getMessage())
logging.getLogger("jellyscope.db").addHandler(catcher)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)
asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY)))
logging.getLogger("jellyscope.db").removeHandler(catcher)
check(not [c for c in caught if "arr_readarr_state" in c or "arr_readarr_last_sync" in c], f"the sync does not log the change of the state and the mark ({caught[:3]})")
check(db.get_setting("arr_readarr_app_name", "") == "Readarr", "the sync stores the name the app gave (arr_readarr_app_name)")
for fork in ("Chaptarr", "Bookshelf"):
    asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY, status={"appName": fork, "version": "0.4.1"})))
    check(db.get_setting("arr_readarr_app_name", "") == fork, f"a sync against {fork} stores its name")
mark_before = db.get_setting("arr_readarr_last_sync", "")
with db.connect() as conn:
    conn.execute("DELETE FROM readarr_event")
    conn.commit()
db.set_setting("arr_readarr_last_sync", "")
db.forget_settings()
try:
    asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY, status={"appName": "Lidarr", "version": "3"})))
    other = ""
except readarr.ReadarrError as error:
    other = str(error)
check("Lidarr answers at this address, not Readarr" in other and readarr.sync_state()["result"] == "error" and count() == 0
      and db.get_setting("arr_readarr_last_sync", "") == "" and db.get_setting("arr_readarr_app_name", "") == "Bookshelf",
      f"another app at the address: the connection test's error, no row written, the mark unmoved, the stored name kept ({other})")
db.set_setting("arr_readarr_last_sync", mark_before)
db.forget_settings()
asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY)))
check(db.get_setting("arr_readarr_app_name", "") == "Readarr", "and Readarr's own name is stored again")
check("arr_readarr_api_key" in db.SECRET_SETTINGS and "arr_readarr_titles" in db.UNLOGGED_SETTINGS and "arr_readarr_titles_version" in db.UNLOGGED_SETTINGS
      and "arr_readarr_state" in db.UNLOGGED_SETTINGS and "arr_readarr_last_sync" in db.UNLOGGED_SETTINGS
      and "arr_readarr_app_name" in db.UNLOGGED_SETTINGS, "the key is secret, the marks are not logged")

print()
print("--- C) 5000 records ---")
readarr.delete_data()
big = [{**HISTORY[0], "id": 100000 + i} for i in range(5000)]
big_requests: list[str] = []
start = time.perf_counter()
result = asyncio.run(readarr.synchronize(transport=app_with_history(big, big_requests)))
duration = time.perf_counter() - start
check(count() == 5000 and result["new_count"] == 5000 and result["total"] == 5000 and duration < 10, f"5000 records in {duration:.1f} s")
check(len([q for q in big_requests if q.startswith("page ")]) >= 5 and not any(q.startswith("since") for q in big_requests), "5000 records page by page")
check(readarr.delete_data() == 5000 and count() == 0 and db.get_setting("arr_readarr_last_sync", "") == "", "deleting the downloaded data returns the count and clears the sync mark")
asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY)))
check(count() == n, "and the next sync fetches the whole history again")

print()
print("--- D) a wrong key ---")
mark_before = db.get_setting("arr_readarr_last_sync", "")
try:
    asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY, code=401)))
    refused = ""
except readarr.ReadarrError as error:
    refused = str(error)
state = readarr.sync_state()
check(bool(refused) and state["result"] == "error" and "401" in state["error"] and "tajny-klic" not in state["error"] and "tajny-klic" not in refused,
      f"Readarr's error is in the state, without the key ({state['error']})")
check(db.get_setting("arr_readarr_last_sync", "") == mark_before and count() == n, "the error did not move the sync mark and deleted nothing")
asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY)))
check(readarr.sync_state()["result"] == "ok", "the next successful sync fixes the state again")
readarr.save_settings(False, "", "")
try:
    asyncio.run(readarr.synchronize())
    missing = ""
except readarr.ReadarrError as error:
    missing = str(error)
check("tajny-klic" not in missing and readarr.sync_state()["result"] == "error", "a switched-off app with no address: an error without the key")
# The saved key goes only to the server it was saved for: after the address was cleared it is typed again.
readarr.save_settings(True, "http://nas:8787/readarr", "tajny-klic")

print()
print("--- E) deleting the downloaded data ---")
asyncio.run(readarr.synchronize(transport=app_with_history(HISTORY)))
check(db.query_value("SELECT COUNT(*) FROM readarr_title") == 4, "the books are stored with the history")
readarr.sync._RUNNING.add(readarr.NAME)
try:
    readarr.delete_data()
    refused_during_sync = ""
except readarr.ReadarrError as error:
    refused_during_sync = str(error)
readarr.sync._RUNNING.discard(readarr.NAME)
check("running" in refused_during_sync and count() == n, f"downloaded data is not deleted during a sync ({refused_during_sync})")


async def delete_and_sync() -> str:
    deletion = asyncio.create_task(readarr.delete_downloaded_data())
    await asyncio.sleep(0)                      # the deletion already holds the app and runs in a thread
    try:
        await readarr.synchronize(transport=app_with_history(HISTORY))
        return "the sync went through"
    except readarr.ReadarrError as error:
        return str(error)
    finally:
        await deletion


concurrent_result = asyncio.run(delete_and_sync())
check("already running" in concurrent_result and count() == 0, f"a sync started during the deletion is refused ({concurrent_result})")
check(not readarr.sync_running(), "after the deletion the app is free again")
check(db.query_value("SELECT COUNT(*) FROM readarr_title") == 0 and db.get_setting("arr_readarr_last_sync", "") == "" and readarr.sync_state()["at"] == "",
      "the deletion took the books and the marks too")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
