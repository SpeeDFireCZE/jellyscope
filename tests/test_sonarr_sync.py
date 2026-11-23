# -*- coding: utf-8 -*-
r"""The sync of Sonarr's history into the table sonarr_event.

Guarded:

A) a history record as a row: the time in UTC in the database's format, the series' name, the episode
   S01E02, when the episode aired, the grab's source, the protocol, the size - for a record whose
   series is no longer in Sonarr too (the release's name stays instead of the name);
B) the first sync fetches the whole history, the next ones ask since the last minus an hour and
   double nothing (upsert by the record's id);
C) 5000 records at once are written within a few seconds;
D) an error (a wrong key) is written into the state without the key and does not move the sync mark;
F) the web: "Sync now" and "Delete downloaded data" (only the admin, a backup first, the next sync
   starts from the beginning again), the sync state on the card and the confirmation dialog.

The task "*Arr sync" and that one app's error does not stop the other are guarded by
tests/test_arr_registry.py.

Sonarr does not run: the history is recorded from a real Sonarr (tests/sonarr_responses/) and
httpx.MockTransport returns it.

Run:
    .venv/bin/python tests/test_sonarr_sync.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sonarr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import no_network, not_responding, sonarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, services, tasks, web  # noqa: E402
from jellyscope.arr import sonarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "sonarr_responses"
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
    return int(db.query_value("SELECT COUNT(*) FROM sonarr_event"))


def app_with_history(history: list[dict], requests: list[str] | None = None,
                     code: int = 200) -> httpx.MockTransport:
    """An app with the given history: `history/since` (the whole of it since a date) and the paged `history`.

    It notes `since <date>` or `page <number> of <size>` into `requests`. It orders the pages by date
    ascending like a real Sonarr.
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


SONARR = recorded("history_since")
BEGINNING = "2000-01-01T00:00:00Z"

print("--- A) a history record -> a row ---")
grab = next(r for r in SONARR if r["eventType"] == "grabbed" and r["data"].get("releaseSource") == "InteractiveSearch")
r = sonarr.event_from_history(grab)
check(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", r["happened_at"] or "") is not None, f"the date in the database's format ({r['happened_at']})")
check(r["title"] == grab["series"]["title"] and r["episode"] == "S%02dE%02d" % (
    grab["episode"]["seasonNumber"], grab["episode"]["episodeNumber"]), f"the series and the episode ({r['title']} {r['episode']})")
check(r["item_id"] == grab["episodeId"] and r["title_id"] == grab["seriesId"], "the item = the episode, title_id = the series")
check(r["source"] == "InteractiveSearch" and r["indexer"] == "arr-test" and r["protocol"] == "usenet",
      f"the source, the indexer and the protocol (protocol \"1\" = usenet) ({r['source']}, {r['indexer']}, {r['protocol']})")
check(r["size_bytes"] == int(grab["data"]["size"]) and r["client"] == "Blackhole", "the size (a number from a string) and the client")
check(r["released_at"] == grab["episode"]["airDateUtc"].replace("T", " ").replace("Z", ""), f"the episode aired ({r['released_at']})")
check(r["quality"] == grab["quality"]["quality"]["name"] and r["resolution"] == grab["quality"]["quality"]["resolution"],
      f"the quality and the resolution ({r['quality']}, {r['resolution']})")
shifted = {**grab, "date": "2026-11-12T22:50:18.1234567+02:00"}
check(sonarr.event_from_history(shifted)["happened_at"] == "2026-11-12 20:50:18",
      "a time with an offset and seven decimal places (.NET) converts to UTC")
# Review Focus 2: a series deleted in the app - the history stays, includeSeries returns null.
without = {**SONARR[0], "series": None, "episode": None, "id": 999999}
r = sonarr.event_from_history(without)
check(r["title"] == without["sourceTitle"] and r["episode"] == "" and r["released_at"] is None,
      "a series deleted in the app: the release's name instead of the name, no episode")
foreign = {**grab, "data": {**grab["data"], "protocol": "2", "releaseSource": "4", "size": "neni-cislo"}}
r = sonarr.event_from_history(foreign)
check(r["protocol"] == "torrent" and r["source"] == "InteractiveSearch" and r["size_bytes"] is None,
      "a numeric protocol and source translate into names, a size that is no number = unknown")
check("app" not in r and set(r) == set(sonarr.sync.COLUMNS), "the row has exactly the columns of Sonarr's table")
deletion = next(r for r in SONARR if r["eventType"] == "episodeFileDeleted")
check(sonarr.event_from_history(deletion)["reason"] == "Upgrade", "the reason the file was deleted (Upgrade)")

print()
print("--- B) sync ---")
sonarr.save_settings(True, "http://nas:8989/sonarr", "tajny-klic")
requests: list[str] = []
result = asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR, requests)))
n = count()
check(result["new_count"] == n == len(SONARR) and result["total"] == len(SONARR),
      f"the first sync fetches the whole history ({result}, in the database {n})")
# Review finding 3: the first sync of a big history must not come in one answer (50 000 records =
# hundreds of MB in memory) - it goes page by page, not through history/since.
check(bool(requests) and all(q.startswith("page ") for q in requests), f"the first time page by page, not all at once ({requests})")
check(bool(requests) and all(q.startswith("page ") and int(q.split()[-1]) <= 1000 for q in requests), "a page of at most 1000 records")
requests.clear()
mark = db.get_setting("arr_sonarr_last_sync", "")
result = asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR, requests)))
check(count() == n and result["new_count"] == 0, f"a repeated sync doubles nothing ({result})")
expected = (datetime.strptime(mark, db.TIME_FORMAT) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if mark else "?"
check(requests == [f"since {expected}"], f"the second time it asks since the last sync minus an hour ({requests} vs {expected})")
state = sonarr.sync_state()
check(set(state) == {"at", "result", "new_count", "error"} and state["result"] == "ok" and state["error"] == ""
      and state["new_count"] == 0 and state["at"], f"the sync state ({state})")
row = db.query_one("SELECT * FROM sonarr_event WHERE event_type = 'grabbed' AND source = 'InteractiveSearch'")
check(bool(row and row["indexer"] and row["episode"].startswith("S")), "a grab by an interactive pick is in the database")
row = db.query_one("SELECT * FROM sonarr_event WHERE event_type = 'downloadFolderImported'")
check(bool(row and row["download_id"]), "an import has a download_id (pairing with the grab)")
check(db.query_value("SELECT COUNT(*) FROM sonarr_event WHERE reason = 'Upgrade'") >= 1,
      "a file deleted because of an upgrade is in the database")
check(db.query_value("SELECT COUNT(DISTINCT source) FROM sonarr_event WHERE event_type = 'grabbed'") == 5,
      "all five grab sources (RSS, search, user search, interactive pick, pushed from outside)")
changed = [{**r, "data": {**r["data"], "reason": "Manual"}} if r["eventType"] == "episodeFileDeleted" else r for r in SONARR]
asyncio.run(sonarr.synchronize(transport=app_with_history(changed)))
check(db.query_value("SELECT COUNT(*) FROM sonarr_event WHERE reason = 'Manual'") >= 1,
      "the upsert rewrites a changed record (it does not just skip it)")

# A minor from a review: the state and the mark change with every sync - the log would get four
# lines "nastaveni … zmeneno" an hour from them.
import logging  # noqa: E402
db.set_setting("arr_sonarr_state", "{}")                                # so the state and the mark
db.set_setting("arr_sonarr_last_sync", "2026-01-01 00:00:00")      # really change
caught: list[str] = []
catcher = logging.Handler()
catcher.emit = lambda record: caught.append(record.getMessage())
logging.getLogger("jellyscope.db").addHandler(catcher)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)
asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR)))
logging.getLogger("jellyscope.db").removeHandler(catcher)
check(not [c for c in caught if "arr_sonarr_state" in c or "arr_sonarr_last_sync" in c],
      f"the sync does not log the change of the state and the mark ({caught[:3]})")

print()
print("--- C) 5000 records (Review Focus 3) ---")
sonarr.delete_data()
big = [{**SONARR[0], "id": 100000 + i} for i in range(5000)]
big_requests: list[str] = []
start = time.perf_counter()
result = asyncio.run(sonarr.synchronize(transport=app_with_history(big, big_requests)))
duration = time.perf_counter() - start
check(count() == 5000 and result["new_count"] == 5000 and result["total"] == 5000 and duration < 10,
      f"5000 records in {duration:.1f} s")
check(len(big_requests) >= 5 and not any(q.startswith("since") for q in big_requests),
      f"5000 records page by page ({len(big_requests)} requests)")
check(sonarr.delete_data() == 5000 and count() == 0 and db.get_setting("arr_sonarr_last_sync", "") == "",
      "deleting the downloaded data returns the count and clears the sync mark")
asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR)))
check(count() == n, "and the next sync fetches the whole history again")

print()
print("--- D) a wrong key (Review Focus 4) ---")
mark_before = db.get_setting("arr_sonarr_last_sync", "")
try:
    asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR, code=401)))
    refused = ""
except sonarr.SonarrError as error:
    refused = str(error)
state = sonarr.sync_state()
check(bool(refused) and state["result"] == "error" and "401" in state["error"] and "tajny-klic" not in state["error"],
      f"Sonarr's error is in the state, without the key ({state['error']})")
check(db.get_setting("arr_sonarr_last_sync", "") == mark_before and count() == n, "the error did not move the sync mark and deleted nothing")
asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR)))
check(sonarr.sync_state()["result"] == "ok", "the next successful sync fixes the state again")

print()
print("--- F) web ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
check(reader.post("/settings/arr/sonarr/delete", follow_redirects=False).status_code in (303, 403)
      and count() == n, "a reader deletes nothing")
check(reader.post("/settings/arr/sonarr/sync", follow_redirects=False).status_code in (303, 403), "a reader does not sync")
check(admin.post("/settings/arr/nothing/sync", follow_redirects=False).status_code == 404
      and admin.post("/settings/arr/nothing/delete", follow_redirects=False).status_code == 404, "an unknown app = 404")
# Sonarr does not run: the connection is refused (the client is faked, no real network).
restore = substitute(sonarr, "SonarrClient", sonarr_client(not_responding()))
answer = admin.post("/settings/arr/sonarr/sync")
db.forget_settings()
check("flash error" in answer.text and sonarr.sync_state()["result"] == "error" and count() == n,
      "Sync now: an unavailable app = an error in the message and in the state, the data stays")
restore()
page = admin.get("/settings?section=arr").text
check('action="/settings/arr/sonarr/sync"' in page and 'action="/settings/arr/sonarr/delete"' in page,
      "the card has Sync now and Delete downloaded data")
check('data-modal="modal-arr-delete-sonarr"' in page and re.search(r'<dialog[^>]+id="modal-arr-delete-sonarr"', page),
      "the deletion is confirmed in a dialog")
check(f"{count()}" in page, "the card shows how many records are downloaded")
db.set_setting("backup_path", str(Path(_tmp) / "backups"))   # so list_backups() sees the backup
db.forget_settings()
# Review finding 5: another address for an app with downloaded data (another instance, a reinstall)
# would quietly mix two histories (the record ids start from 1 again). A warning, nothing is deleted.
answer = admin.post("/settings/arr/sonarr", data={"enabled": "1", "url": "http://jiny-sonarr:8989", "api_key": "",
                                                  "action": "save"})
check("flash warning" in answer.text and "Smazat stažená data" in answer.text and count() == n,
      "a change of the address for an app with data: a warning with the button's name, the data stays")
answer = admin.post("/settings/arr/sonarr", data={"enabled": "1", "url": "http://jiny-sonarr:8989", "api_key": "",
                                                  "action": "save"})
check("flash warning" not in answer.text, "a save without a change of the address does not warn")
admin.post("/settings/arr/sonarr", data={"enabled": "1", "url": "http://nas:8989/sonarr", "api_key": "", "action": "save"})
# A minor from a review: deleting during a sync would leave the last hour with a new mark and the
# whole history would never come back - now it is not deleted.
sonarr.sync._RUNNING.add(sonarr.NAME)
answer = admin.post("/settings/arr/sonarr/delete")
sonarr.sync._RUNNING.discard(sonarr.NAME)
check(count() == n and "flash error" in answer.text, "downloaded data is not deleted during a sync")


# Code review: the deletion runs in a thread, so a sync started between the check "not running" and
# the DELETE would fetch only the increment and write a fresh mark after the deletion - the whole
# history would never come back. So the deletion locks the app itself, still in the loop.
async def delete_and_sync() -> str:
    deletion = asyncio.create_task(sonarr.delete_downloaded_data())
    await asyncio.sleep(0)                      # the deletion already holds the app and runs in a thread
    try:
        await sonarr.synchronize(transport=app_with_history(SONARR))
        return "the sync went through"
    except sonarr.SonarrError as error:
        return str(error)
    finally:
        await deletion


concurrent_result = asyncio.run(delete_and_sync())
check("already running" in concurrent_result and count() == 0, f"a sync started during the deletion is refused ({concurrent_result})")
check(not sonarr.sync_running(), "after the deletion the app is free again")
asyncio.run(sonarr.synchronize(transport=app_with_history(SONARR)))
backups_before = len(tasks.list_backups())
answer = admin.post("/settings/arr/sonarr/delete", follow_redirects=False)
db.forget_settings()
check(answer.status_code == 303 and count() == 0, "Delete downloaded data deletes Sonarr's history")
check(len(tasks.list_backups()) == backups_before + 1, "a backup was made before the deletion")
check(db.get_setting("arr_sonarr_last_sync", "") == "" and sonarr.sync_state()["at"] == "",
      "after the deletion the next sync starts from the beginning")

print()
print("--- the counts of missing and cutoff (refresh_counts, the *Arr task after the sync, 23. 11. 2026) ---")
counted: list[tuple[str, object]] = []


def with_counts(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v3/", 1)[-1]
    if path.startswith("wanted/"):
        counted.append((path, request.extensions.get("timeout", {}).get("read")))
        return httpx.Response(200, json={"totalRecords": {"wanted/missing": 41, "wanted/cutoff": 7}[path], "records": []})
    if path == "history":
        return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": 0, "records": []})
    return httpx.Response(200, json=[])


asyncio.run(sonarr.synchronize(transport=httpx.MockTransport(with_counts)))
check(counted == [], f"the history sync itself counts nothing (Settings' button waits for it) ({counted})")
asyncio.run(sonarr.refresh_counts(transport=httpx.MockTransport(with_counts)))
stored = services.stored_counts("arr_sonarr", services.settings_fingerprint(
    db.get_setting("arr_sonarr_url", ""), db.get_setting("arr_sonarr_api_key", "")))
check(sorted(counted) == [("wanted/cutoff", 300.0), ("wanted/missing", 300.0)],
      f"both counts, with the long limit ({counted})")
check(stored["missing"] == 41 and stored["cutoff"] == 7 and stored["missing_at"], f"stored with their time ({stored})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
