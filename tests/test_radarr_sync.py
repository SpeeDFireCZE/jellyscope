# -*- coding: utf-8 -*-
r"""The sync of Radarr's history into the table radarr_event.

Guarded:

A) a history record as a row: the time in UTC in the database's format, the movie's name, when the
   movie came out (the earlier of the digital and the physical release), the grab's source, the
   protocol, the size - for a record whose movie is no longer in Radarr too (the release's name stays
   instead of the name);
B) the first sync fetches the whole history, the next ones ask since the last minus an hour and
   double nothing (upsert by the record's id);
C) 5000 records at once are written within a few seconds;
D) an error (a wrong key) is written into the state without the key and does not move the sync mark;
F) the web: "Sync now" and "Delete downloaded data" (only the admin, a backup first, the next sync
   starts from the beginning again), the sync state on the card and the confirmation dialog.

The task "*Arr sync" and that one app's error does not stop the other are guarded by
tests/test_arr_registry.py.

Radarr does not run: the history is recorded from a real Radarr (tests/radarr_responses/) and
httpx.MockTransport returns it.

Run:
    .venv/bin/python tests/test_radarr_sync.py
"""
from __future__ import annotations

import asyncio
import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "radarr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import no_network, not_responding, radarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, tasks, web  # noqa: E402
from jellyscope.arr import radarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "radarr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # what has no transport of its own here must not reach the network


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> list[dict]:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM radarr_event"))


def app_with_history(history: list[dict], requests: list[str] | None = None,
                     code: int = 200) -> httpx.MockTransport:
    """An app with the given history: `history/since` (the whole of it since a date) and the paged `history`.

    It notes `since <date>` or `page <number> of <size>` into `requests`. It orders the pages by date
    ascending like a real Radarr.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if code != 200:
            return httpx.Response(code)
        params = request.url.params
        if request.url.path.endswith("/api/v3/history/since"):
            if requests is not None:
                requests.append(f"since {params.get('date', '')}")
            return httpx.Response(200, json=history)
        if request.url.path.endswith("/api/v3/history"):
            page, size = int(params.get("page", "1")), int(params.get("pageSize", "10"))
            if requests is not None:
                requests.append(f"page {page} of {size}")
            ordered = sorted(history, key=lambda r: (r["date"], r["id"]))
            return httpx.Response(200, json={"page": page, "pageSize": size, "totalRecords": len(history),
                                             "records": ordered[(page - 1) * size:page * size]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


RADARR = recorded("history_since")
BEGINNING = "2000-01-01T00:00:00Z"

print("--- A) a history record -> a row ---")
movie = next(r for r in RADARR if r["eventType"] == "grabbed")
r = radarr.event_from_history(movie)
check(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", r["happened_at"] or "") is not None, f"the date in the database's format ({r['happened_at']})")
check(r["title"] == movie["movie"]["title"] and "episode" not in r and r["item_id"] == r["title_id"] == movie["movieId"],
      f"a movie: the name, no episode, the item and title_id = the movie ({r['title']})")
check(r["indexer"] == "arr-test" and r["protocol"] in ("usenet", "torrent") and r["source"] in ("Rss", "Search", "UserInvokedSearch",
                                                                                              "InteractiveSearch", "ReleasePush"),
      f"the source, the indexer and the protocol ({r['source']}, {r['indexer']}, {r['protocol']})")
check(r["size_bytes"] == int(movie["data"]["size"]) and bool(r["client"]), "the size (a number from a string) and the client")
check(r["released_at"] == "2008-04-10 00:00:00", f"the movie came out: the digital release ({r['released_at']})")
check(r["quality"] == movie["quality"]["quality"]["name"] and r["resolution"] == movie["quality"]["quality"]["resolution"],
      f"the quality and the resolution ({r['quality']}, {r['resolution']})")
shifted = {**movie, "date": "2026-11-12T22:50:18.1234567+02:00"}
check(radarr.event_from_history(shifted)["happened_at"] == "2026-11-12 20:50:18",
      "a time with an offset and seven decimal places (.NET) converts to UTC")
physical_earlier = {**movie, "movie": {**movie["movie"], "digitalRelease": "2009-01-01T00:00:00Z",
                                       "physicalRelease": "2008-06-01T00:00:00Z"}}
check(radarr.event_from_history(physical_earlier)["released_at"] == "2008-06-01 00:00:00", "came out = the earlier of the two releases")
without_release = {**movie, "movie": {**movie["movie"], "digitalRelease": None, "physicalRelease": None}}
check(radarr.event_from_history(without_release)["released_at"] is None,
      "a movie without a digital and a physical release: unknown (a cinema premiere does not count)")
# Review Focus 2: a movie deleted in Radarr - the history stays, includeMovie returns null.
deleted = radarr.event_from_history({**movie, "movie": None, "id": 999999})
check(deleted["title"] == movie["sourceTitle"] and deleted["released_at"] is None, "a movie deleted in the app: the release's name instead of the name")
foreign = {**movie, "data": {**movie["data"], "protocol": "2", "releaseSource": "4", "size": "neni-cislo"}}
r = radarr.event_from_history(foreign)
check(r["protocol"] == "torrent" and r["source"] == "InteractiveSearch" and r["size_bytes"] is None,
      "a numeric protocol and source translate into names, a size that is no number = unknown")
check("app" not in r and set(r) == set(radarr.sync.COLUMNS), "the row has exactly the columns of Radarr's table")
deletion = next(r for r in RADARR if r["eventType"] == "movieFileDeleted")
check(radarr.event_from_history(deletion)["reason"] == "Upgrade", "the reason the file was deleted (Upgrade)")

print()
print("--- B) sync ---")
radarr.save_settings(True, "http://nas:7878/radarr", "tajny-klic")
requests: list[str] = []
result = asyncio.run(radarr.synchronize(transport=app_with_history(RADARR, requests)))
n = count()
check(result["new_count"] == n == len(RADARR) and result["total"] == len(RADARR),
      f"the first sync fetches the whole history ({result}, in the database {n})")
# Review finding 3: the first sync of a big history must not come in one answer (50 000 records =
# hundreds of MB in memory) - it goes page by page, not through history/since.
check(bool(requests) and all(q.startswith("page ") for q in requests), f"the first time page by page, not all at once ({requests})")
check(bool(requests) and all(q.startswith("page ") and int(q.split()[-1]) <= 1000 for q in requests), "a page of at most 1000 records")
requests.clear()
mark = db.get_setting("arr_radarr_last_sync", "")
result = asyncio.run(radarr.synchronize(transport=app_with_history(RADARR, requests)))
check(count() == n and result["new_count"] == 0, f"a repeated sync doubles nothing ({result})")
expected = (datetime.strptime(mark, db.TIME_FORMAT) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if mark else "?"
check(requests == [f"since {expected}"], f"the second time it asks since the last sync minus an hour ({requests} vs {expected})")
state = radarr.sync_state()
check(set(state) == {"at", "result", "new_count", "error"} and state["result"] == "ok" and state["error"] == ""
      and state["new_count"] == 0 and state["at"], f"the sync state ({state})")
row = db.query_one("SELECT * FROM radarr_event WHERE event_type = 'grabbed' AND source = 'InteractiveSearch'")
check(bool(row and row["indexer"] and row["title"]), "a grab by an interactive pick is in the database")
row = db.query_one("SELECT * FROM radarr_event WHERE event_type = 'downloadFolderImported'")
check(bool(row and row["download_id"]), "an import has a download_id (pairing with the grab)")
check(db.query_value("SELECT COUNT(*) FROM radarr_event WHERE reason = 'Upgrade'") >= 1,
      "a file deleted because of an upgrade is in the database")
check(db.query_value("SELECT COUNT(DISTINCT source) FROM radarr_event WHERE event_type = 'grabbed'") == 5,
      "all five grab sources (RSS, search, user search, interactive pick, pushed from outside)")
changed = [{**r, "data": {**r["data"], "reason": "Manual"}} if r["eventType"] == "movieFileDeleted" else r for r in RADARR]
asyncio.run(radarr.synchronize(transport=app_with_history(changed)))
check(db.query_value("SELECT COUNT(*) FROM radarr_event WHERE reason = 'Manual'") >= 1,
      "the upsert rewrites a changed record (it does not just skip it)")

# A minor from a review: the state and the mark change with every sync - the log would get four
# lines "nastaveni … zmeneno" an hour from them.
import logging  # noqa: E402
db.set_setting("arr_radarr_state", "{}")                                # so the state and the mark
db.set_setting("arr_radarr_last_sync", "2026-01-01 00:00:00")      # really change
caught: list[str] = []
catcher = logging.Handler()
catcher.emit = lambda record: caught.append(record.getMessage())
logging.getLogger("jellyscope.db").addHandler(catcher)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)
asyncio.run(radarr.synchronize(transport=app_with_history(RADARR)))
logging.getLogger("jellyscope.db").removeHandler(catcher)
check(not [c for c in caught if "arr_radarr_state" in c or "arr_radarr_last_sync" in c],
      f"the sync does not log the change of the state and the mark ({caught[:3]})")

print()
print("--- C) 5000 records (Review Focus 3) ---")
radarr.delete_data()
big = [{**RADARR[0], "id": 100000 + i} for i in range(5000)]
big_requests: list[str] = []
start = time.perf_counter()
result = asyncio.run(radarr.synchronize(transport=app_with_history(big, big_requests)))
duration = time.perf_counter() - start
check(count() == 5000 and result["new_count"] == 5000 and result["total"] == 5000 and duration < 10,
      f"5000 records in {duration:.1f} s")
check(len(big_requests) >= 5 and not any(q.startswith("since") for q in big_requests),
      f"5000 records page by page ({len(big_requests)} requests)")
check(radarr.delete_data() == 5000 and count() == 0 and db.get_setting("arr_radarr_last_sync", "") == "",
      "deleting the downloaded data returns the count and clears the sync mark")
asyncio.run(radarr.synchronize(transport=app_with_history(RADARR)))
check(count() == n, "and the next sync fetches the whole history again")

print()
print("--- D) a wrong key (Review Focus 4) ---")
mark_before = db.get_setting("arr_radarr_last_sync", "")
try:
    asyncio.run(radarr.synchronize(transport=app_with_history(RADARR, code=401)))
    refused = ""
except radarr.RadarrError as error:
    refused = str(error)
state = radarr.sync_state()
check(bool(refused) and state["result"] == "error" and "401" in state["error"] and "tajny-klic" not in state["error"],
      f"Radarr's error is in the state, without the key ({state['error']})")
check(db.get_setting("arr_radarr_last_sync", "") == mark_before and count() == n, "the error did not move the sync mark and deleted nothing")
asyncio.run(radarr.synchronize(transport=app_with_history(RADARR)))
check(radarr.sync_state()["result"] == "ok", "the next successful sync fixes the state again")

print()
print("--- F) web ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
check(reader.post("/settings/arr/radarr/delete", follow_redirects=False).status_code in (303, 403)
      and count() == n, "a reader deletes nothing")
check(reader.post("/settings/arr/radarr/sync", follow_redirects=False).status_code in (303, 403), "a reader does not sync")
check(admin.post("/settings/arr/nothing/sync", follow_redirects=False).status_code == 404
      and admin.post("/settings/arr/nothing/delete", follow_redirects=False).status_code == 404, "an unknown app = 404")
# Radarr does not run: the connection is refused (the client is faked, no real network).
restore = substitute(radarr, "RadarrClient", radarr_client(not_responding()))
answer = admin.post("/settings/arr/radarr/sync")
db.forget_settings()
check("flash error" in answer.text and radarr.sync_state()["result"] == "error" and count() == n,
      "Sync now: an unavailable app = an error in the message and in the state, the data stays")
restore()
page = admin.get("/settings?section=arr").text
check('action="/settings/arr/radarr/sync"' in page and 'action="/settings/arr/radarr/delete"' in page,
      "the card has Sync now and Delete downloaded data")
check('data-modal="modal-arr-delete-radarr"' in page and re.search(r'<dialog[^>]+id="modal-arr-delete-radarr"', page),
      "the deletion is confirmed in a dialog")
check(f"{count()}" in page, "the card shows how many records are downloaded")
db.set_setting("backup_path", str(Path(_tmp) / "backups"))   # so list_backups() sees the backup
db.forget_settings()
# Review finding 5: another address for an app with downloaded data (another instance, a reinstall)
# would quietly mix two histories (the record ids start from 1 again). A warning, nothing is deleted.
answer = admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "http://jiny-radarr:7878", "api_key": "",
                                                  "action": "save"})
check("flash warning" in answer.text and "Smazat stažená data" in answer.text and count() == n,
      "a change of the address for an app with data: a warning with the button's name, the data stays")
answer = admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "http://jiny-radarr:7878", "api_key": "",
                                                  "action": "save"})
check("flash warning" not in answer.text, "a save without a change of the address does not warn")
admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "http://nas:7878/radarr", "api_key": "", "action": "save"})
# A minor from a review: deleting during a sync would leave the last hour with a new mark and the
# whole history would never come back - now it is not deleted.
radarr.sync._RUNNING.add(radarr.NAME)
answer = admin.post("/settings/arr/radarr/delete")
radarr.sync._RUNNING.discard(radarr.NAME)
check(count() == n and "flash error" in answer.text, "downloaded data is not deleted during a sync")


# Code review: the deletion runs in a thread, so a sync started between the check "not running" and
# the DELETE would fetch only the increment and write a fresh mark after the deletion - the whole
# history would never come back. So the deletion locks the app itself, still in the loop.
async def delete_and_sync() -> str:
    deletion = asyncio.create_task(radarr.delete_downloaded_data())
    await asyncio.sleep(0)                      # the deletion already holds the app and runs in a thread
    try:
        await radarr.synchronize(transport=app_with_history(RADARR))
        return "the sync went through"
    except radarr.RadarrError as error:
        return str(error)
    finally:
        await deletion


concurrent_result = asyncio.run(delete_and_sync())
check("already running" in concurrent_result and count() == 0, f"a sync started during the deletion is refused ({concurrent_result})")
check(not radarr.sync_running(), "after the deletion the app is free again")
asyncio.run(radarr.synchronize(transport=app_with_history(RADARR)))
backups_before = len(tasks.list_backups())
answer = admin.post("/settings/arr/radarr/delete", follow_redirects=False)
db.forget_settings()
check(answer.status_code == 303 and count() == 0, "Delete downloaded data deletes Radarr's history")
check(len(tasks.list_backups()) == backups_before + 1, "a backup was made before the deletion")
check(db.get_setting("arr_radarr_last_sync", "") == "" and radarr.sync_state()["at"] == "",
      "after the deletion the next sync starts from the beginning")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
