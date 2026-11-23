# -*- coding: utf-8 -*-
r"""The sync of Seerr's requests into the table seerr_request.

Guarded:

A) a request as a row: times in UTC in the database's format, the TMDB id as a text (like
   items.tmdb_id), the requester with a normalised Jellyfin id, 4K, "available" only when the title
   really is in the library (Seerr otherwise fills mediaAddedAt with the title's creation time) - for
   a request without a requester or without a title too (Review Focus 1 and 2);
B) the whole list page by page, upsert without doubling;
C) times of state changes: approval and decline only for requests Jellyscope saw pending;
D) a request deleted in Seerr is marked, one that comes back is unmarked; an unfinished walk marks
   nothing; hundreds of pages and a request on two pages (Review Focus 5);
E) names: from the Jellyscope library without a request, the others from Seerr all at once, at most
   5 at a time, each only once; a failed look-up does not bring the sync down and is tried next time
   (Review Focus 3);
F) an error without the key, the mark unchanged, a concurrent run "already running", a log without
   noise;
G) the task "Seerr sync"; the web: Sync now, Delete downloaded data (a backup, refused during a
   sync), the warning on a change of the address.

Seerr does not run: the requests are recorded from a real Seerr (tests/seerr_responses/request.json),
httpx.MockTransport pages them.

Run:
    .venv/bin/python tests/test_seerr_sync.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "seerr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, scanner, seerr, tasks, web  # noqa: E402

RESPONSES = PROJECT / "tests" / "seerr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


RECORDED = json.loads((RESPONSES / "request.json").read_text(encoding="utf-8"))["results"]


class Seerr:
    """A fake Seerr: it pages `requests`, answers the look-up of names and counts the requests."""

    def __init__(self, requests: list[dict], code: int = 200, fail_on_page: int | None = None,
                 failing_names: set[str] | None = None, off_list: list[dict] | None = None,
                 detail_500: set[int] | None = None, newest_first: bool = False) -> None:
        self.requests, self.code, self.fail_on_page = requests, code, fail_on_page
        self.failing_names = failing_names or set()
        # Requests that are in Seerr but the paged list did not return (a shift while paging).
        self.off_list = {r["id"]: r for r in off_list or []}
        self.detail_500 = detail_500 or set()
        self.newest_first = newest_first          # Overseerr: ignores sortDirection, sends the newest first
        self.checked: list[int] = []              # requests for a request's detail (request/{id})
        self.timeouts: list[tuple[str, float]] = []   # (filter, read timeout in seconds)
        self.pages: list[tuple[int, int]] = []
        self.names: list[str] = []
        self.concurrent = self.max_concurrent = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.code != 200:
            return httpx.Response(self.code)
        path = request.url.path.split("/api/v1/", 1)[-1]
        if path == "request":
            take, skip = int(request.url.params["take"]), int(request.url.params["skip"])
            self.pages.append((skip, take))
            self.timeouts.append((request.url.params.get("filter", ""),
                                  (request.extensions.get("timeout") or {}).get("read")))
            if self.fail_on_page is not None and skip // take + 1 == self.fail_on_page:
                return httpx.Response(500)
            listed = self.requests[::-1] if self.newest_first else self.requests
            return httpx.Response(200, json={"pageInfo": {"pages": -(-len(listed) // take), "pageSize": take,
                                                          "results": len(listed), "page": skip // take + 1},
                                             "results": listed[skip:skip + take]})
        if path.startswith("request/"):
            request_id = int(path.split("/")[1])
            self.checked.append(request_id)
            if request_id in self.detail_500:
                return httpx.Response(500)
            record = self.off_list.get(request_id) or next((r for r in self.requests if r["id"] == request_id), None)
            return httpx.Response(200, json=record) if record else httpx.Response(404)
        if path.startswith(("movie/", "tv/")):
            self.names.append(path)
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            await asyncio.sleep(0.01)
            self.concurrent -= 1
            if path in self.failing_names:
                return httpx.Response(500)
            media_type, tmdb = path.split("/")
            return httpx.Response(200, json={"name": f"Seriál {tmdb}"} if media_type == "tv" else {"title": f"Film {tmdb}"})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def sync(s: Seerr) -> dict:
    return asyncio.run(seerr.synchronize(transport=s.transport()))


def row(request_id: int) -> dict:
    return db.query_one("SELECT * FROM seerr_request WHERE id = ?", (request_id,)) or {}


def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM seerr_request"))


def sample(**changes) -> dict:
    """A copy of a recorded request (Matrix, id 1) with changes."""
    r = copy.deepcopy(RECORDED[0])
    for key, value in changes.items():
        r[key] = value
    return r


print("--- A) a request as a row ---")
check(seerr.normalize_id("34D3F8D3-AB12-cd34") == "34d3f8d3ab12cd34" and seerr.normalize_id(None) == "",
      "a Jellyfin id: lower case, without dashes")
matrix = seerr.request_row(RECORDED[0])
check(matrix["id"] == 1 and matrix["media_type"] == "movie" and matrix["tmdb_id"] == "603" and matrix["seasons"] is None,
      f"a movie: id, type, TMDB id as a text ({matrix['tmdb_id']!r})")
check(matrix["status"] == 5 and matrix["media_status"] == 5 and matrix["is_4k"] == 0 and matrix["auto_request"] == 0,
      "the request's and the title's state, 4K and automatic")
check(matrix["created_at"] == "2026-11-13 06:28:48" and matrix["updated_at"] == "2026-11-13 06:40:00",
      f"times in UTC in the database's format ({matrix['created_at']})")
check(matrix["available_at"] == "2026-11-13 06:31:12", f"available = when the title arrived in the library ({matrix['available_at']})")
check(matrix["jellyfin_id"] == RECORDED[0]["media"]["jellyfinMediaId"].lower(),
      "the title's id in Jellyfin (media.jellyfinMediaId) - pairing with the Jellyscope library")
check(matrix["requester_name"] == "jana" and matrix["requester_jellyfin_id"] == seerr.normalize_id(
    RECORDED[0]["requestedBy"]["jellyfinUserId"]) and matrix["handled_by_name"] == "admin", "the requester and who handled it")
dark = seerr.request_row(next(r for r in RECORDED if r["id"] == 2))
check(dark["media_type"] == "tv" and dark["seasons"] == 1 and dark["media_status"] == 4 and dark["available_at"] is None,
      "a series partly available: mediaAddedAt is the default (= the title's creation), available unknown")
pending = seerr.request_row(next(r for r in RECORDED if r["id"] == 10))
check(pending["status"] == 1 and pending["available_at"] is None and pending["handled_by_name"] == "",
      "pending: the title is not in the library (available unknown), nobody handled it")
four_k = seerr.request_row(sample(is4k=True, media={**RECORDED[0]["media"], "status4k": 3}))
check(four_k["is_4k"] == 1 and four_k["media_status"] == 3 and four_k["available_at"] is None and four_k["jellyfin_id"] == "",
      "4K: the title's state from status4k, the title's id from jellyfinMediaId4k")
four_k_done = seerr.request_row(sample(is4k=True, media={**RECORDED[0]["media"], "status4k": 5}))
check(four_k_done["media_status"] == 5 and four_k_done["available_at"] is None,
      "4K available: mediaAddedAt belongs to the normal version, when 4K arrived is unknown")
without_requester = seerr.request_row(sample(id=901, requestedBy=None, modifiedBy=None))
check(without_requester["requester_name"] == "" and without_requester["requester_jellyfin_id"] == ""
      and without_requester["requester_seerr_id"] is None,
      "Review Focus 1: a requester deleted in Seerr - an empty name, nothing crashes")
without_title = seerr.request_row(sample(id=902, media=None))
check(without_title["tmdb_id"] == "" and without_title["media_status"] is None and without_title["available_at"] is None,
      "Review Focus 2: a request without a title - an empty TMDB id, nothing crashes")
# The TMDB id goes into the path of the request for a name (movie/{id}): Seerr's answer is input
# like any other, so only a number is taken - nothing like "../settings/main" gets into the path.
forged = seerr.request_row(sample(media={**RECORDED[0]["media"], "tmdbId": "../settings/main"}))
check(forged["tmdb_id"] == "" and seerr.request_row(sample(media={**RECORDED[0]["media"], "tmdbId": "603"}))["tmdb_id"] == "603",
      "the TMDB id only as a number (a number in a string too); anything else is dropped")
check(seerr.sync._page_rows([sample(id="nesmysl"), sample(id=971, status="nesmysl"), sample(id=972)])
      == [seerr.request_row(sample(id=972))],
      "a record with an id or a state that is no number is skipped, the others on the page go through")
upper = seerr.request_row(sample(requestedBy={**RECORDED[0]["requestedBy"], "jellyfinUserId": "34D3F8D3-00AA"}))
check(upper["requester_jellyfin_id"] == "34d3f8d300aa", "Review Focus 4: a Jellyfin id with upper case and dashes is normalised")

print()
print("--- B) sync ---")
seerr.save_settings(True, "http://nas:5055/seerr", "tajny-klic")
DARK = next(r for r in RECORDED if r["id"] == 2)["media"]["jellyfinMediaId"]
with db.connect() as conn:       # Matrix and Dark are in the Jellyscope library: names without asking Seerr
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, synced_at) VALUES ('m603', 'The Matrix', 'Movie', '603', ?)",
                 (db.utcnow(),))
    # An episode has no TMDB id (Jellyfin 12) - the name by the series' id in Jellyfin.
    conn.execute("INSERT INTO items (id, name, type, series_name, series_id, synced_at)"
                 " VALUES ('e1', 'Secrets', 'Episode', 'Dark', ?, ?)", (DARK, db.utcnow()))
    conn.commit()
s = Seerr(copy.deepcopy(RECORDED))
v = sync(s)
check(v == {"new_count": len(RECORDED), "total": len(RECORDED), "deleted_count": 0} and count() == len(RECORDED),
      f"the first sync stores every request ({v})")
check(s.pages == [(0, 100)], f"pages of 100 ({s.pages})")
check(s.timeouts == [("all", 180.0)], f"a page of the sync has a long read timeout ({s.timeouts})")
v = sync(Seerr(copy.deepcopy(RECORDED)))
check(count() == len(RECORDED) and v["new_count"] == 0, f"a repeated sync doubles nothing ({v})")
check(row(10)["seen_pending"] == 1 and row(1)["seen_pending"] == 0, "a pending request has the mark \"seen pending\"")

print()
print("--- E) names ---")
check(row(1)["title"] == "The Matrix" and "movie/603" not in s.names, "Matrix from the Jellyscope library, without a request")
others = {f"{r['type']}/{r['media']['tmdbId']}" for r in RECORDED} - {"movie/603", "tv/70523"}
check(set(s.names) == others and len(s.names) == len(others), f"the other names from Seerr, each once ({len(s.names)})")
check(1 < s.max_concurrent <= 5, f"look-ups at once, at most 5 ({s.max_concurrent})")
check(row(2)["title"] == "Dark" and "tv/70523" not in s.names, "a series from the library by the series' id (an episode without a TMDB id)")
check(row(10)["title"] == "Film 872585", "names from Seerr stored")
s = Seerr(copy.deepcopy(RECORDED))
sync(s)
check(s.names == [], "stored names are not looked up again")
with db.connect() as conn:
    conn.execute("UPDATE seerr_request SET title = '' WHERE id IN (3, 4)")
    conn.commit()
s = Seerr(copy.deepcopy(RECORDED), failing_names={"movie/27205"})
v = sync(s)
check(seerr.sync_state()["result"] == "ok" and row(3)["title"] == "" and row(4)["title"] == "Film 157336",
      "Review Focus 3: a failed look-up does not bring the sync down, the name stays empty")
s = Seerr(copy.deepcopy(RECORDED))
sync(s)
check(s.names == ["movie/27205"] and row(3)["title"] == "Film 27205", "the next time it is tried again (only that one)")

# A review finding: `REPLACE(LOWER(items.id), '-', '')` in the query rules the index out and with a
# big library the pairing takes tens of seconds. The search goes by the stored shapes of the id
# (without dashes and with them).
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, series_name, series_id, synced_at) VALUES ('e-pomlcky', 'Díl',"
                 " 'Episode', 'Seriál s pomlčkami', 'abcdef01-2345-6789-abcd-ef0123456789', ?)", (db.utcnow(),))
    conn.commit()
with_dashes = sample(id=950, type="tv", media={**RECORDED[0]["media"], "mediaType": "tv", "tmdbId": 9950,
                                               "jellyfinMediaId": "ABCDEF0123456789ABCDEF0123456789"})
sql_queries: list[str] = []
original_query_all = db.query_all
db.query_all = lambda sql, *a, **k: (sql_queries.append(sql), original_query_all(sql, *a, **k))[1]
s = Seerr(copy.deepcopy(RECORDED) + [with_dashes])
sync(s)
db.query_all = original_query_all
check(row(950)["title"] == "Seriál s pomlčkami" and "tv/9950" not in s.names,
      "a series id stored with dashes pairs with the id from Seerr without dashes")
check(not [q for q in sql_queries if "REPLACE(" in q.upper() or "LOWER(" in q.upper()],
      "pairing with the library wraps no column in a function (it uses the index)")
# Code review: an episode may carry its own TMDB id (the episode's id, another number space than the
# series'). A request for a series with the same number would get another series' name - for good.
foreign = sample(id=951, type="tv", media={**RECORDED[0]["media"], "mediaType": "tv", "tmdbId": 9990,
                                           "jellyfinMediaId": None})
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, series_name, series_id, tmdb_id, synced_at) VALUES"
                 " ('e-cizi', 'Díl', 'Episode', 'Cizí seriál', 'serie-cizi', '9990', ?)", (db.utcnow(),))
    conn.commit()
s = Seerr(copy.deepcopy(RECORDED) + [foreign])
sync(s)
check(row(951)["title"] == "Seriál 9990" and "tv/9990" in s.names,
      f"a series is not named by an episode's TMDB id - the name comes from Seerr ({row(951)['title']})")
with db.connect() as conn:
    conn.execute("DELETE FROM seerr_request WHERE id IN (950, 951)")
    conn.commit()

print()
print("--- C) times of state changes ---")
changed = copy.deepcopy(RECORDED)
for r in changed:
    if r["id"] == 10:
        r["status"], r["updatedAt"] = 2, "2026-11-14T10:00:00.000Z"
    if r["id"] == 12:
        r["status"], r["updatedAt"] = 3, "2026-11-14T11:00:00.000Z"
sync(Seerr(changed))
check(row(10)["approved_at"] == "2026-11-14 10:00:00", f"approval after \"pending\": the time of the change ({row(10)['approved_at']})")
check(row(12)["declined_at"] == "2026-11-14 11:00:00", "decline after \"pending\": the time of the change")
check(row(1)["approved_at"] is None and row(14)["declined_at"] is None,
      "first seen already approved / declined: the time unknown (NULL)")
sync(Seerr(changed))
check(row(10)["approved_at"] == "2026-11-14 10:00:00", "the approval time does not change with further syncs")
# Code review: a request that managed both the approval and the download between two syncs is first
# seen already done - and its updatedAt is the time it finished, not the approval. The approval time
# is unknown.
sync(Seerr(changed + [sample(id=960, status=1)]))
sync(Seerr(changed + [sample(id=960, status=5, updatedAt="2026-11-14T12:30:00.000Z")]))
check(row(960)["seen_pending"] == 1 and row(960)["approved_at"] is None,
      f"pending, done the next time: the approval time unknown, not the time it finished ({row(960)['approved_at']})")
with db.connect() as conn:
    conn.execute("DELETE FROM seerr_request WHERE id = 960")
    conn.commit()

print()
print("--- D) deleted in Seerr ---")
without_6 = [r for r in changed if r["id"] != 6]
v = sync(Seerr(without_6))
check(row(6)["deleted_at"] is not None and v["deleted_count"] == 1 and count() == len(RECORDED),
      f"a request missing in Seerr stays, marked ({v})")
# A review finding: deleting an earlier request during the paging moves the next request to a page
# already fetched and it is missing from the list although it exists. Before a request is marked it
# is checked by itself.
shifted = next(r for r in changed if r["id"] == 5)
shifted = {**shifted, "status": 3, "updatedAt": "2026-11-14T12:00:00.000Z"}
s = Seerr([r for r in changed if r["id"] != 5], off_list=[shifted])
v = sync(s)
check(5 in s.checked and row(5)["deleted_at"] is None and v["deleted_count"] == 0,
      f"a request missing from the list but present in Seerr is not marked deleted ({v}, checked {s.checked})")
check(row(5)["status"] == 3, "... and its current state is stored")
s = Seerr([r for r in changed if r["id"] != 4], detail_500={4})
v = sync(s)
check(row(4)["deleted_at"] is None and v["deleted_count"] == 0 and seerr.sync_state()["result"] == "ok",
      "a check that fails (500) does not mark the request and does not bring the sync down")
sync(Seerr(changed))
check(row(6)["deleted_at"] is None, "a request that comes back is unmarked")
many = [sample(id=1000 + i, createdAt="2026-11-15T10:00:00.000Z") for i in range(350)]
sync(Seerr(changed + many))
before = count()
shifted_list = changed + many
shifted_pages = shifted_list[:100] + shifted_list[99:]       # request no. 100 is on page 1 and 2 (a shift while paging)
s = Seerr(shifted_pages)
sync(s)
check(count() == before == len(changed) + 350 and len(s.pages) == 4,
      f"Review Focus 5: {len(changed) + 350} requests on {len(s.pages)} pages, nothing doubled")
check(db.query_value("SELECT COUNT(*) FROM seerr_request WHERE deleted_at IS NOT NULL") == 0, "... and nothing marked deleted")
try:       # 200 requests would be missing on pages 2+ - but page 2 fails, so nothing may be marked
    sync(Seerr(changed + many[:150], fail_on_page=2))
except seerr.SeerrError:
    pass
check(seerr.sync_state()["result"] == "error"
      and db.query_value("SELECT COUNT(*) FROM seerr_request WHERE deleted_at IS NOT NULL") == 0,
      "an unfinished walk (an error on page 2) marks nothing deleted")
sync(Seerr(changed + many))

print()
print("--- F) errors, concurrency, log ---")
mark = db.get_setting("seerr_last_sync", "")
try:
    sync(Seerr(changed, code=401))
except seerr.SeerrError:
    pass
state = seerr.sync_state()
check(state["result"] == "error" and "401" in state["error"] and "tajny-klic" not in state["error"],
      f"an error without the key in the state ({state['error']})")
check(db.get_setting("seerr_last_sync", "") == mark, "an error does not move the mark of the last sync")
seerr.sync._RUNNING.add("seerr")
try:
    sync(Seerr(changed))
    check(False, "a concurrent run is refused")
except seerr.SeerrError as error:
    check("already running" in str(error), "a concurrent run: \"already running\"")
seerr.sync._RUNNING.discard("seerr")
db.set_setting("seerr_state", "{}")
caught: list[str] = []
catcher = logging.Handler()
catcher.emit = lambda record: caught.append(record.getMessage())
logging.getLogger("jellyscope.db").addHandler(catcher)
logging.getLogger("jellyscope.db").setLevel(logging.INFO)
sync(Seerr(changed + many))
logging.getLogger("jellyscope.db").removeHandler(catcher)
check(not [c for c in caught if "seerr_state" in c or "seerr_last_sync" in c],
      f"the sync does not log the change of the state and the mark ({caught[:2]})")

print()
print("--- The live card \"Waiting for approval\" ---")
waiting = [sample(id=2000 + i, status=1, createdAt=f"2026-11-15T10:{i // 60:02d}:{i % 60:02d}.000Z") for i in range(120)]
s = Seerr(waiting, newest_first=True)
seerr.live.CACHE.clear()
live = asyncio.run(seerr.pending(transport=s.transport()))
check(bool(s.timeouts) and all(limit is not None and limit <= 20 for _f, limit in s.timeouts),
      f"the live request has a short timeout, so a slow Seerr does not hold the page up ({s.timeouts[:2]})")
check(live["available"] and live["count"] == 120 and len(live["requests"]) == 120,
      f"every pending request loads, over several pages too ({live['count']}, {len(live['requests'])})")
check([r["request_id"] for r in live["requests"][:2]] == [2000, 2001],
      "the longest waiting first, even when the app sends the newest first (Overseerr)")
check(all("id" not in r for r in live["requests"]),
      "a row carries no key `id` (anonymising would take it for a viewer's id and make a dead link)")
# Code review: a fresh pending request (newer than the last sync) had no name on the card, only
# "TMDB 603" - and the live card exists for the fresh requests. The name is looked up at once (the
# library, then Seerr) and remembered, so Seerr is not asked on every load.
fresh = [sample(id=3000 + i, status=1, createdAt=f"2026-11-16T10:00:{i:02d}.000Z",
                media={**RECORDED[0]["media"], "tmdbId": 4200 + i, "jellyfinMediaId": None}) for i in range(30)]
s = Seerr(fresh)
seerr.live.CACHE.clear()
live = asyncio.run(seerr.pending(transport=s.transport()))
check(live["requests"][0]["title"] == "Film 4200" and "movie/4200" in s.names,
      f"a fresh pending request has its name from Seerr ({live['requests'][0]['title']})")
check(0 < len(s.names) <= 20 and live["requests"][-1]["title"] == "TMDB 4229",
      f"at most 20 look-ups per page load, the oldest first ({len(s.names)})")
looked_up = len(s.names)
seerr.live.CACHE.clear()
asyncio.run(seerr.pending(transport=s.transport()))
check(len(s.names) - looked_up <= 10 and "movie/4200" not in s.names[looked_up:],
      "looked-up names are remembered - the next load fills in only the rest")
seerr.live.CACHE.clear()

print()
print("--- G) the task and the web ---")
task = tasks.TASKS.get("seerr")
check(task is not None and task.default_minutes == 60 and task.interval_setting == "seerr_sync_minutes"
      and task.log_kind == "seerr" and tasks.is_enabled(task), "the task seerr: an hour, on by default")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
n = count()
page = admin.get("/settings?section=seerr").text
check('action="/settings/seerr/sync"' in page and 'data-modal="modal-seerr-delete"' in page
      and f"{n}" in page, "the card: Sync now, Delete downloaded data (a dialog) and the count of requests")
check(reader.post("/settings/seerr/delete", follow_redirects=False).status_code in (303, 403) and count() == n,
      "a reader deletes nothing")
answer = admin.post("/settings/seerr", data={"enabled": "1", "url": "http://jiny-seerr:5055", "api_key": "",
                                             "action": "save"})
check("flash warning" in answer.text and "Smazat stažená data" in answer.text and count() == n,
      "a change of the address with downloaded data: a warning, nothing is deleted")
admin.post("/settings/seerr", data={"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "", "action": "save"})
answer = admin.post("/settings/seerr/sync")
db.forget_settings()
check("flash error" in answer.text and seerr.sync_state()["result"] == "error",
      "Sync now: an unavailable Seerr = an error in the message and in the state")


# Code review: the deletion in a thread and a sync must not meet (see test_sonarr_sync).
async def delete_and_sync() -> str:
    deletion = asyncio.create_task(seerr.delete_downloaded_data())
    await asyncio.sleep(0)
    try:
        await seerr.synchronize(transport=Seerr(changed).transport())
        return "the sync went through"
    except seerr.SeerrError as error:
        return str(error)
    finally:
        await deletion


concurrent_result = asyncio.run(delete_and_sync())
check("already running" in concurrent_result and count() == 0 and not seerr.sync_running(),
      f"a sync started during the deletion is refused ({concurrent_result})")
seerr.save_settings(True, "http://nas:5055/seerr", "tajny-klic")
sync(Seerr(changed + many))
n = count()
seerr.save_settings(True, "http://127.0.0.1:1", "")


# Code review: the task caught only SeerrError - after another exception (a locked database, an
# error in the conversion) the run stayed "running" in the task log for ever.
async def crashes(transport=None):
    raise RuntimeError("database is locked")
original_synchronize = seerr.synchronize
seerr.synchronize = crashes
result = asyncio.run(tasks.run_now("seerr"))
seerr.synchronize = original_synchronize
last = scanner.last_scan("seerr")
check(result.get("status") == "error" and last is not None and last.get("status") == "error",
      f"an unexpected error ends the task with an error, the run does not hang ({result}, {last and last.get('status')})")

seerr.sync._RUNNING.add("seerr")
answer = admin.post("/settings/seerr/delete")
seerr.sync._RUNNING.discard("seerr")
check(count() == n and "flash error" in answer.text, "downloaded data is not deleted during a sync")
db.set_setting("backup_path", str(Path(_tmp) / "backups"))
db.forget_settings()
backups = len(tasks.list_backups())
check(admin.post("/settings/seerr/delete", follow_redirects=False).status_code == 303 and count() == 0
      and len(tasks.list_backups()) == backups + 1, "deleting: a backup first, then an empty table")
check(seerr.sync_state()["at"] == "", "after deleting the state is empty")
seerr.save_settings(False, "", "")
result = asyncio.run(tasks.run_now("seerr"))
check(result == {"status": "ok", "message": "Seerr is not connected."}, f"the task without Seerr - the key ({result})")
last = scanner.last_scan("seerr")
check(last is not None and last.get("status") == "ok", "the task's run is in the task log")

print()
print("--- after the conversion to English names (database step 3) ---")
# What Seerr no longer sends - the time of an approval or a decline seen after "pending", a request deleted in
# Seerr, the poster's path - lives only in the table. After the conversion the next sync keeps it and doubles
# nothing. The old shape is made from today's by the map turned around.
from jellyscope import schema_names  # noqa: E402

seerr.save_settings(True, "http://nas:5055/seerr", "tajny-klic")
with db.connect() as conn:
    conn.execute(f"DELETE FROM {next(t.new for t in schema_names.TABLES if t.new.startswith('seerr_'))}")
    conn.commit()
sync(Seerr(RECORDED))
sync(Seerr(changed))
sync(Seerr(without_6))
table = next(t for t in schema_names.TABLES if t.new.startswith("seerr_"))
KEPT = ("id", "status", "approved_at", "declined_at", "deleted_at", "seen_pending", "poster", "title", "is_4k", "requester_name")
before = {r["id"]: {k: r[k] for k in KEPT} for r in db.query_all(f"SELECT * FROM {table.new}")}
with db.connect() as conn:                 # the table and the keys as the dev before step 3 had them
    for index in table.indexes.values():
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute(f"ALTER TABLE {table.new} RENAME TO {table.old}")
    for old, new in table.columns.items():
        conn.execute(f"ALTER TABLE {table.old} RENAME COLUMN {new} TO {old}")
    old_keys = {old: new for old, new in schema_names.SETTING_KEYS.items() if new.startswith("seerr_")}
    for old, new in old_keys.items():
        conn.execute("UPDATE settings SET key = ? WHERE key = ?", (old, new))
    moved = conn.execute("SELECT COUNT(*) FROM settings WHERE key IN (%s)" % ", ".join("?" * len(old_keys)),
                         tuple(old_keys)).fetchone()
    conn.commit()
check(next(iter(moved.values())) if isinstance(moved, dict) else moved[0], "the old shape has the Seerr keys under the old names")
db.forget_settings()
db.init_db()
db.forget_settings()
asked = Seerr(without_6)
again = sync(asked)
after = {r["id"]: {k: r[k] for k in KEPT} for r in db.query_all(f"SELECT * FROM {table.new}")}
check(bool(before) and after == before and again.get("deleted_count", 0) == 0,
      f"the sync right after the conversion keeps the states Seerr no longer sends and doubles nothing ({len(before)} requests)")
check(after[10]["approved_at"] == "2026-11-14 10:00:00" and after[12]["declined_at"] == "2026-11-14 11:00:00"
      and after[6]["deleted_at"] is not None, "the approval, the decline and the deletion are still there")
check(bool(asked.pages) and again.get("total") == len(without_6) and asked.names == [] and asked.checked == [],
      f"after the conversion the sync walks all of Seerr's requests and asks for no title and no request again "
      f"({again}, {asked.names}, {asked.checked})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
