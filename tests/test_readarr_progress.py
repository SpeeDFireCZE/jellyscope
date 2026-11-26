# -*- coding: utf-8 -*-
r"""Readarr: the live state, the live downloads (the card "Stahuje se") and the provisional covers.

Readarr does not run here: the answers are put together by hand after Readarr's API v1
(tests/readarr_responses/*.json). Guarded:

A) the client: the whole queue in one page with only the fields used, the refresh command, the cover;
B) grouping: queue records into downloads, downloads into blocks by book; progress, state,
   the order of blocks;
C) liveness: the rhythm 5 s / 60 s, the command only while downloading, the cache, an unavailable
   app, the live state (queue, missing, cutoff, the calendar, health);
D) the library and the covers of books in the queue: a cover is stored and deleted once the book
   is in the library, a book in the library uses Jellyfin's cover.

Run:
    .venv/bin/python tests/test_readarr_progress.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "readarr-progress.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import READARR_RESPONSES, connect_readarr, readarr_client, readarr_handler, no_network, recorded_response  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db, images  # noqa: E402
from jellyscope.arr import readarr  # noqa: E402

failures = 0
db.init_db()
no_network()      # no app other than Readarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


RECORDED = recorded_response(READARR_RESPONSES, "queue_downloading")
KEY = "tajny-klic"
# Every request: (method, path in the API, params, body, key from the header, host).
requests: list[tuple[str, str, dict[str, str], Any, str, str]] = []
answers: dict[str, httpx.Response | None] = {"instead": None}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v1/", 1)[-1]
    body = json.loads(request.content) if request.content else None
    requests.append((request.method, path, dict(request.url.params), body,
                     request.headers.get("X-Api-Key", ""), request.url.host))
    if answers["instead"] is not None:
        return answers["instead"]
    if request.method == "POST" and path == "command":
        return httpx.Response(201, json={"id": 7, "name": (body or {}).get("name"), "status": "queued"})
    if request.method == "GET" and path == "queue":
        return httpx.Response(200, json=RECORDED)
    return httpx.Response(404)


def client() -> Any:
    return readarr.ReadarrClient("http://readarr.test", KEY, transport=httpx.MockTransport(handler))


async def whole() -> dict[str, Any]:
    async with client() as c:
        return await c.whole_queue()


async def refresh() -> None:
    async with client() as c:
        await c.refresh_downloads()


print("--- A) the client: the whole queue, the refresh command, the cover ---")
queue = asyncio.run(whole())
check(queue["total"] == 8 and len(queue["records"]) == len(RECORDED["records"]) == 8,
      f"Readarr's whole queue: the records and the count ({queue['total']}, {len(queue['records'])})")
method, path, params = requests[-1][:3]
check((method, path) == ("GET", "queue") and params.get("pageSize") == "200" and params.get("page") == "1"
      and params.get("includeBook") == "true" and params.get("includeAuthor") == "true",
      f"one page of up to 200 records with the book and the author ({params})")
check(params.get("includeUnknownAuthorItems") == "true" and not {"includeSeries", "includeEpisode", "includeUnknownSeriesItems"} & set(params),
      f"the queue with downloads not paired with an author too, and no question about series ({sorted(params)})")
check(all(not ({"statusMessages", "errorMessage", "outputPath"} & set(r)) for r in queue["records"]),
      "the app's messages and paths are never read from the answer")
first = next(r for r in queue["records"] if r.get("status") == "downloading" and r.get("bookId") == 31)
check({"downloadId", "size", "sizeleft", "status", "trackedDownloadState", "timeleft", "bookId", "authorId", "downloadClient",
       "protocol", "title"} <= set(first)
      and set(first["book"]) <= {"id", "title", "authorId"} and first["book"]["title"] == "Nikola Šuhaj"
      and set(first["author"]) <= {"id", "authorName"} and first["author"]["authorName"] == "Ivan Olbracht",
      f"a record carries what the card draws, and only a few fields of the embedded book and author ({first['book']}, {first['author']})")
asyncio.run(refresh())
check(requests[-1][:2] == ("POST", "command") and requests[-1][3] == {"name": "RefreshMonitoredDownloads"}
      and requests[-1][4] == KEY and KEY not in str(requests[-1][2]),
      f"the refresh command: POST command with the key only in the header ({requests[-1][:4]})")
timeouts: list[tuple[str, Any]] = []


def with_timeout(request: httpx.Request) -> httpx.Response:
    timeouts.append((request.method, request.extensions.get("timeout")))
    return httpx.Response(201, json={})


async def command_and_queue() -> None:
    async with readarr.ReadarrClient("http://readarr.test", KEY, transport=httpx.MockTransport(with_timeout)) as c:
        await c.refresh_downloads()
        await c.whole_queue()


asyncio.run(command_and_queue())
check([m for m, _ in timeouts] == ["POST", "GET"] and timeouts[0][1]["read"] <= 3 and timeouts[1][1]["read"] > timeouts[0][1]["read"],
      "the refresh command has a short limit of its own, reading the queue the usual one")
JPEG = b"\xff\xd8\xff\xe0" + b"jellyscope-test" * 8


async def cover_of(book_id: int, answer: httpx.Response) -> bytes | None:
    seen.append("")

    def one(request: httpx.Request) -> httpx.Response:
        seen[-1] = request.url.path
        return answer
    async with readarr.ReadarrClient("http://readarr.test", KEY, transport=httpx.MockTransport(one)) as c:
        return await c.cover(book_id)


seen: list[str] = []
check(asyncio.run(cover_of(31, httpx.Response(200, content=JPEG))) == JPEG and seen[-1] == "/api/v1/mediacover/book/31/cover.jpg",
      f"the book's cover is read from mediacover/book/<id>/cover.jpg ({seen[-1]})")
check(asyncio.run(cover_of(31, httpx.Response(404))) is None, "a book without a cover: None")
try:
    asyncio.run(cover_of(31, httpx.Response(500, text=f"chyba, klíč {KEY}")))
    check(False, "an error of the app is raised")
except readarr.ReadarrError as error:
    check("500" in str(error) and KEY not in str(error), f"the error body goes out without the key ({error})")

print()
print("--- B) grouping: downloads and blocks by book ---")
progress = readarr.progress
records = queue["records"]
b = progress.blocks(records)
by_title = {x["title"]: x for x in b}
check([x["title"] for x in b] == ["Unknown.Author-Mystery-EPUB", "Terry Pratchett - Mort", "Terry Pratchett - Stráže! Stráže!",
                                  "Ivan Olbracht - Nikola Šuhaj", "Božena Němcová - Babička", "Karel Čapek - Bílá nemoc", "Karel Čapek - Válka s mloky"],
      f"one block per book, in the order downloading (by the time left), waiting, paused, stuck ({[x['title'] for x in b]})")
blue, silver, dawn, tapes, stuck = (by_title.get(n, {}) for n in ("Terry Pratchett - Stráže! Stráže!", "Terry Pratchett - Mort",
                                                                   "Ivan Olbracht - Nikola Šuhaj", "Božena Němcová - Babička",
                                                                   "Karel Čapek - Válka s mloky"))
check((blue.get("count"), blue.get("size_bytes"), blue.get("percent"), blue.get("seconds_left"), blue.get("title_id"))
      == (2, 3 * 1024**3, 50, 360, 22),
      f"a book in two downloads is one block: the sums, the longest time left ({blue.get('count')}, {blue.get('percent')}, {blue.get('seconds_left')})")
check(blue.get("author") == "Terry Pratchett" and blue.get("book") == "Stráže! Stráže!"
      and {d["state"] for d in blue["downloads"]} == {"downloading"},
      "a block carries the author and the book apart too")
check((dawn.get("percent"), dawn.get("seconds_left"), dawn.get("state"), dawn.get("client"), dawn.get("protocol"))
      == (25, 750, "downloading", "Klient s průběhem", "usenet"),
      f"progress, time left, client and protocol of a block ({dawn.get('percent')}, {dawn.get('seconds_left')}, {dawn.get('client')})")
check(stuck.get("state") == "stuck" and not any("track" in k for x in b for k in x),
      "a finished download with a warning is stuck; a book has no track counts")
check(tapes.get("state") == "waiting_client" and by_title["Karel Čapek - Bílá nemoc"]["state"] == "paused",
      "waiting in the client's queue and paused")
pack = [x for x in b if any(d["title_id"] in (21, 22) for d in x["downloads"])]
check(len(pack) == 2, "one release carrying two books is a download in two blocks, one per book")
unknown = by_title["Unknown.Author-Mystery-EPUB"]
check((unknown["title_id"], unknown["author"], unknown["book"], unknown["protocol"]) == (None, "", "Unknown.Author-Mystery-EPUB", "torrent"),
      "a record Readarr did not pair: a block with the release's name, without an id")
check(not any({"message", "statusMessages", "errorMessage", "outputPath"} & (set(x) | {k for d in x["downloads"] for k in d}) for x in b),
      "the app's messages do not get into the blocks")
TEMPLATE = next(r for r in records if r.get("bookId") == 31)


def record(**changes: Any) -> dict[str, Any]:
    return {**TEMPLATE, **changes}


finished = {s: progress.blocks([record(status="completed", trackedDownloadState="importPending", trackedDownloadStatus=s, sizeleft=0)])[0]["state"]
            for s in ("ok", "warning", "error", None)}
check(finished == {"ok": "waiting_import", "warning": "stuck", "error": "stuck", None: "waiting_import"}
      and progress.blocks([record(trackedDownloadStatus="warning")])[0]["state"] == "downloading",
      f"finished with a warning or an error at the import is stuck, without them it waits ({finished})")
other = {(s, t): progress.blocks([record(status=s, trackedDownloadState=t)])[0]["state"]
         for s, t in (("delay", "downloading"), ("downloadClientUnavailable", "downloading"), ("completed", "importing"),
                      ("completed", "failedPending"), ("failed", "failed"), ("divny", "x"))}
check(list(other.values()) == ["postponed", "client_not_responding", "importing", "stuck", "failed", "divny"],
      f"the other states; an unknown one as the app sent it ({list(other.values())})")
for size, left in ((0, 0), (100, 500), (100, -5), (None, None), ("x", "y"), (float("inf"), float("-inf"))):
    percent = progress.blocks([record(size=size, sizeleft=left)])[0]["percent"]
    check(percent is None or 0 <= percent <= 100, f"size {size!r}, left {left!r}: the progress within bounds ({percent})")
# Readarr has ONE queue record per book per download: a discography of three books is three records with the same
# downloadId (and the same size of the whole release). They are three blocks (one per book, nothing to merge), and each
# knows it is a part of a download of three books - so the size shown is understood. The same book twice in one
# download is merged into one download (a duplicate record).
shared = progress.blocks([record(downloadId="D", bookId=a, book={"id": a, "title": f"Book {a}"}) for a in (71, 72, 73)]
                         + [record(downloadId="E", bookId=74, book={"id": 74, "title": "Book 74"}),
                            record(downloadId="E", bookId=74, book={"id": 74, "title": "Book 74"})])
by_id = {b["title_id"]: b for b in shared}
check(len(shared) == 4 and [by_id[a]["download_books"] for a in (71, 72, 73, 74)] == [3, 3, 3, 1],
      f"a download of three books: three blocks, each says 3; a duplicate record of one book is merged, says 1 ({[b['download_books'] for b in shared]})")
check(by_id[74]["count"] == 1, "the duplicate record of one book in one download is one download, not two")
check(progress.blocks([record(downloadId="")])[0]["download_books"] == 1 and progress.blocks([record(downloadId="")] * 2)[0]["download_books"] == 1,
      "records without a downloadId share nothing")
check([progress.seconds(x) for x in ("00:12:34", "1.02:03:04", "", None, "x", 5)] == [754, 93784, None, None, None, None],
      "the time left in every shape")
check(len(progress.blocks([record(downloadId="M", bookId=1), record(downloadId="M", bookId=2)])) == 2, "one download for two books: two blocks")

print()
print("--- C) liveness: rhythm, the command only while downloading, the cache; the live state ---")
from jellyscope import cards_base  # noqa: E402

clock = {"now": 1000.0}
progress._now = lambda: clock["now"]
live_queues: dict[str, Any] = {"queue": RECORDED, "command_fails": False, "down": False}


def live_readarr(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v1/", 1)[-1]
    requests.append((request.method, path, dict(request.url.params), None, request.headers.get("X-Api-Key", ""), request.url.host))
    if live_queues["down"]:
        raise httpx.ConnectError("Connection refused", request=request)
    if request.method == "POST":
        return httpx.Response(500, text="nejde") if live_queues["command_fails"] else httpx.Response(201, json={"id": 1})
    return httpx.Response(200, json=live_queues["queue"])


def move(seconds: float) -> None:
    clock["now"] += seconds


def commands() -> int:
    return sum(r[0] == "POST" and r[1] == "command" for r in requests)


def queue_reads() -> int:
    return sum(r[0] == "GET" and r[1] == "queue" for r in requests)


def read() -> dict[str, Any]:
    return asyncio.run(readarr.download_state())


restore_client = substitute(readarr, "ReadarrClient", readarr_client(live_readarr))
connect_readarr()
requests.clear()
s1 = read()
check(s1["available"] and s1["live"] and s1["refresh"] == 5 and commands() == 0 and len(s1["blocks"]) == 7 and s1["more_count"] == 0
      and s1["error"] == "", f"the first read: something downloads, rhythm 5 s, no command is sent ({s1['refresh']}, {commands()})")
move(5)
read()
# Since 23. 11. 2026 the command goes beside the read (a hung app must not cost two time limits): both in this read.
check(commands() == 1 and queue_reads() == 2 and sorted(method for method, *_rest in requests[-2:]) == ["GET", "POST"],
      "downloading: the next read sends the refresh command beside reading the queue")
move(4.99)
read()
check(commands() == 2 and queue_reads() == 3, f"a request a hair under 5 s: the queue is read and the command goes with it ({commands()})")
move(1)
read()
read()
check(commands() == 2 and queue_reads() == 3, "within 4 s it reads from the cache and sends no command")
move(5)
import threading  # noqa: E402

threads = [threading.Thread(target=lambda: cards_base.live(readarr.download_state)) for _ in range(3)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(10)
check(commands() == 3 and queue_reads() == 4, f"three viewers' cards at once: one request and one command ({queue_reads()}, {commands()})")
before_end = commands()
live_queues["queue"] = {**RECORDED, "records": [], "totalRecords": 0}
move(5)
s = read()
check(s["blocks"] == [] and not s["live"] and s["refresh"] == 60 and commands() == before_end + 1,
      "a cancelled download disappears at the next refresh, rhythm a minute")
move(60)
read()
check(commands() == before_end + 1, "after a calm (an empty queue) no command is sent")
live_queues.update(queue=RECORDED, command_fails=True)
move(60)
read()
move(5)
s = read()
check(s["available"] and len(s["blocks"]) == 7 and commands() == before_end + 2, f"an error of the command does not stop reading the queue ({commands()})")
live_queues.update(command_fails=False, down=True)
move(5)
s = read()
# Since 23. 11. 2026 the first error during a download is retried at the fast rhythm: a single answer slower than the
# short limit must not stop the live card for a minute. A second error in a row is calm.
check(not s["available"] and s["refresh"] == progress.FAST_REFRESH,
      f"the first error during a download: one more try at the fast rhythm ({s['refresh']})")
move(progress.FAST_REFRESH)
s = read()
attempts = commands()
move(60)
read()
check(not s["available"] and s["refresh"] == 60 and s["blocks"] == [] and "Connection refused" in s["error"] and KEY not in s["error"]
      and commands() == attempts, f"the app does not answer: unavailable, a minute's rhythm, an error without the key ({s['error']})")
live_queues["down"] = False
db.set_setting("arr_readarr_url", "http://jinde.test")
db.forget_settings()
read()
check(requests[-1][5] == "jinde.test", f"a change of the address in Settings: it reads from the new one at once ({requests[-1][5]})")
db.set_setting("arr_readarr_url", "http://readarr.test")
db.forget_settings()
many = [record(downloadId=str(i), bookId=100 + i, book={"id": 100 + i, "title": f"Book {i:02d}"}) for i in range(70)]
live_queues["queue"] = {"records": many, "totalRecords": 250}
move(10)
s = read()
check(len(s["blocks"]) == 60 and s["more_count"] == 10 and s["incomplete"], f"at most 60 blocks, the rest as a count ({len(s['blocks'])}, {s['more_count']})")
live_queues["queue"] = {"records": [record(bookId=4, book="not-a-dict", author=["x"], size="big")], "totalRecords": 1}
move(10)
s = read()
check(s["available"] and s["blocks"][0]["percent"] is None, "a record of an odd shape does not bring the page down")
live_queues["queue"] = {"records": 5, "totalRecords": "x"}
move(10)
s = read()
check(s["available"] and s["blocks"] == [], "a queue of an unexpected shape is an empty queue, not an exception")
live_queues["queue"] = RECORDED
move(10)
read()
check(progress.in_queue(31) and progress.in_queue(22) and not progress.in_queue(999), "the books of the queue read last are known")
restore_client()

# The live state: the queue with a problem, missing, cutoff, the calendar, health.
live_requests: list[str] = []
live_instead: dict[str, httpx.Response] = {}
restore_client = substitute(readarr, "ReadarrClient", readarr_client(readarr_handler(live_requests, live_instead)))
readarr.live.CACHE.clear()
state = asyncio.run(readarr.live_state())
# Missing and cutoff come from the sync since 23. 11. 2026 (none ran here): not part of the live state.
check(state["available"] and state["error"] == "" and state["queue"]["count"] == 3 and state["missing"] is None and state["cutoff"] is None,
      f"the live state: the queue; missing and cutoff wait for the sync ({state['queue']['count']}, {state['missing']}, {state['cutoff']})")
errors = state["queue"]["with_error"]
check([(e["title"], e["title_id"], e["state"]) for e in errors] == [("Karel Čapek - Válka s mloky", 11, "importPending"), ("Karel Čapek - Bílá nemoc", 12, "failedPending")]
      and errors[0]["message"] == "Has missing files" and errors[1]["message"] == "Download failed",
      f"the queue items with an error or a warning, by book, with the reason ({[(e['title'], e['message']) for e in errors]})")
calendar = state["calendar"]
check([(c["title"], c["title_id"]) for c in calendar] == [("Terry Pratchett - Stráže! Stráže!", 22), ("Ivan Olbracht - Další týden", 51)]
      and calendar[0]["has_file"] is True and calendar[1]["has_file"] is False and calendar[0]["at"] < calendar[1]["at"],
      f"the calendar: books by the release date, whether the book is on the disk ({[(c['title'], c['at']) for c in calendar]})")
check(state["health"] == [{"type": "warning", "message": "Indexers unavailable due to failures: Test"}], "health checks")
reads = len(live_requests)
asyncio.run(readarr.live_state())
check(len(live_requests) == reads, "the second read within a minute comes from the cache")
readarr.live.CACHE.clear()
live_instead["queue"] = httpx.Response(500, text=f"chyba {KEY}")
state = asyncio.run(readarr.live_state())
check(state["available"] and state["queue"] is None and state["calendar"] is not None and KEY not in state["error"] and "500" in state["error"],
      f"what fails is unknown (None), the rest loads, the error is without the key ({state['error']})")
# An odd record (a number or a list where an object belongs) does not break the whole state: it is named by what is there.
readarr.live.CACHE.clear()
live_instead.clear()
live_instead["queue"] = httpx.Response(200, json={"totalRecords": 3, "records": [
    {"title": "Odd release", "bookId": 7, "trackedDownloadStatus": "warning", "book": "text", "author": ["x"],
     "statusMessages": ["a text", 5, None, {"title": "Odd", "messages": "not a list"}, {"title": "Fine", "messages": ["m1", "m2", "m3"]}]},
    {"title": "Another", "bookId": 8, "status": "failed", "book": 5, "author": 7, "statusMessages": "oops"},
    "not a record"]})
live_instead["calendar"] = httpx.Response(200, json=[
    {"id": 9, "title": "Calendar odd", "book": [1], "author": "x", "statistics": "none", "releaseDate": "2026-11-25T00:00:00Z"}, 3])
state = asyncio.run(readarr.live_state())
odd = state["queue"]["with_error"] if state["queue"] else None
check(state["available"] and odd is not None and [(e["title"], e["title_id"]) for e in odd] == [("Odd release", 7), ("Another", 8)]
      and odd[0]["message"] == "m1; m2" and odd[1]["message"] == "",
      f"an odd queue record (book, author, statusMessages of the wrong kind) is named by its release and does not break the state ({odd})")
check(state["calendar"] is not None and [(c["title"], c["title_id"], c["has_file"]) for c in state["calendar"]] == [("Calendar odd", 9, False)],
      f"an odd calendar record is named by its title ({state['calendar']})")
live_instead.clear()
# Audit stats-apps-forms-3: the queue answer itself of the wrong kind (a text for the count, a number or an object for
# the list) is no error of the whole state either - it ran after the requests, outside any try, and every view failed.
odd_answers = []
for answer in ({"totalRecords": "n/a", "records": []}, {"records": 5}, {"totalRecords": [1], "records": {"a": 1}},
               {"totalRecords": None, "records": [{"title": "A"}, "x"]}):
    try:
        odd_answers.append(readarr.live._queue(answer)["count"])
    except Exception as error:      # noqa: BLE001 - the check below reports it
        odd_answers.append(repr(error))
check(odd_answers == [0, 0, 0, 1], f"a queue answer of the wrong kind counts what is there ({odd_answers})")

# Audit QA-6: the minute queue (the tile "Ve frontě", the card of queue items with an error) left out a download
# Readarr did not pair with any title - the API leaves it out without `includeUnknownAuthorItems` - while the card "Stahuje se"
# (whole_queue) showed it. Such an item is the one that needs a hand (a manual import).
UNKNOWN_ITEM = {"title": "Unknown.Release.1080p", "status": "completed", "trackedDownloadStatus": "warning",
                "trackedDownloadState": "importPending", "statusMessages": [{"title": "x", "messages": ["Manual import required"]}]}


def with_unknown_item(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/api/v1/queue") and request.url.params.get("pageSize") != "200":
        records = [UNKNOWN_ITEM] if request.url.params.get("includeUnknownAuthorItems") == "true" else []
        return httpx.Response(200, json={"totalRecords": len(records), "records": records})
    return recorded_readarr(request)


restore_client()
recorded_readarr = readarr_handler(live_requests, live_instead)
restore_client = substitute(readarr, "ReadarrClient", readarr_client(with_unknown_item))
readarr.live.CACHE.clear()
unknown_state = asyncio.run(readarr.live_state())
check(unknown_state["queue"] is not None and unknown_state["queue"]["count"] == 1
      and [e["title"] for e in unknown_state["queue"]["with_error"]] == ["Unknown.Release.1080p"],
      f"a download Readarr did not pair is in the queue's count and among the items with an error ({unknown_state['queue']})")
restore_client()
restore_client = substitute(readarr, "ReadarrClient", readarr_client(readarr_handler(live_requests, live_instead)))
readarr.live.CACHE.clear()
live_instead.update({p: httpx.Response(500) for p in ("queue", "wanted/missing", "wanted/cutoff", "calendar", "health")})
state = asyncio.run(readarr.live_state())
check(not state["available"] and state["queue"] is None and state["error"] != "", "everything fails: Readarr is unavailable, the page stays up")
readarr.live.CACHE.clear()
restore_client()

print()
print("--- D) the library and the covers of books in the queue ---")
posters = readarr.posters
image: dict[str, Any] = {"body": JPEG, "code": 200}
cover_requests: list[str] = []


def with_covers(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v1/", 1)[-1]
    if path.startswith("mediacover/"):
        cover_requests.append(path)
        return httpx.Response(image["code"], content=image["body"], headers={"Content-Type": "image/jpeg"})
    if path == "queue":
        return httpx.Response(200, json=RECORDED)
    return httpx.Response(404)


def db_exec(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)
        conn.commit()


substitute(readarr, "ReadarrClient", readarr_client(with_covers))
move(10)
progress._memory = None
now = db.utcnow()
# The book 31 is not in the library, 11 is, 21 is too but has no cover tag in Jellyfin.
for book_id, name, jellyfin_id in ((31, "Nikola Šuhaj", ""), (11, "Válka s mloky", "book-war"), (21, "Mort", "book-mort")):
    db_exec("INSERT INTO readarr_title (title_id, name, jellyfin_id) VALUES (?, ?, ?)", (book_id, name, jellyfin_id))
for jellyfin_id, tag in (("book-war", "otisk-obalu"), ("book-mort", "")):
    db_exec("INSERT INTO book (id, name, cover_tag, missing, synced_at) VALUES (?, ?, ?, 0, ?)", (jellyfin_id, jellyfin_id, tag, now))
read()
stored = asyncio.run(posters.provisional_poster(31))
check(stored == (JPEG, "image/jpeg") and cover_requests == ["mediacover/book/31/cover.jpg"] and images.title_file("book", "31").exists(),
      f"a book outside the library: the cover from Readarr, stored as the book's provisional cover ({cover_requests})")
check(asyncio.run(posters.provisional_poster(31)) == (JPEG, "image/jpeg") and len(cover_requests) == 1, "the second time from the disk, the app is not asked")
check(images.titles_on_disk() == [("book", "31")], f"the store lists the cover under the kind book ({images.titles_on_disk()})")
check(asyncio.run(posters.provisional_poster(41)) == (JPEG, "image/jpeg") and cover_requests[-1] == "mediacover/book/41/cover.jpg",
      "a book only the queue just read knows gets a cover too")
cover_requests.clear()
check([asyncio.run(posters.provisional_poster(a)) for a in (11, 21, 999)] == [None, None, None] and cover_requests == [],
      "a book in the library and an unknown book: nothing, and Readarr is not asked")
image["body"] = b"<html>Sign in</html>"
check(asyncio.run(posters.provisional_poster(12)) is None and not images.title_file("book", "12").exists()
      and cover_requests == ["mediacover/book/12/cover.jpg"] and posters.poster_missing(12),
      "the app sends a page instead of an image: not stored, remembered as without a cover")
image["body"] = JPEG
check(asyncio.run(posters.provisional_poster(12)) is None and len(cover_requests) == 1, "that there is no cover is remembered for five minutes")
posters._MISSING[12] = time.monotonic() - posters.MISSING_SECONDS - 1
check(not posters.poster_missing(12) and asyncio.run(posters.provisional_poster(12)) == (JPEG, "image/jpeg"), "and then it is tried again")
check(not posters.poster_missing(None) and not posters.poster_missing(31), "a book that has a cover is not marked missing")

# Once the book arrives in the library, the next sync deletes its provisional cover.
check(posters.clean_up_posters() == 0 and {a for _, a in images.titles_on_disk()} == {"31", "41", "12"}, "books outside the library keep their covers")
db_exec("UPDATE readarr_title SET jellyfin_id = 'book-suhaj' WHERE title_id = 31")
check(posters.clean_up_posters() == 1 and images.titles_on_disk() == [("book", "12"), ("book", "41")] and images.load_title("book", "31") is None,
      f"the book came into the library: its provisional cover is deleted, the others stay ({images.titles_on_disk()})")
check(asyncio.run(posters.provisional_poster(31)) is None, "and the book no longer asks Readarr for a cover")
images.save_title("movie", "31", JPEG)
db_exec("UPDATE readarr_title SET jellyfin_id = 'book-suhaj' WHERE title_id = 41 OR title_id = 12")
check(posters.clean_up_posters() == 0 and ("movie", "31") in images.titles_on_disk(), "a movie's cover with the same number is never touched")
images.forget_titles()
check(images.titles_on_disk() == [], "the provisional covers can all be forgotten")

# Deleting the downloaded data forgets that app's provisional covers (and only its kind): once the app is
# disconnected nothing else would ever delete them.
images.save_title("book", "31", JPEG)
images.save_title("book", "41", JPEG)
images.save_title("movie", "31", JPEG)
readarr.delete_data()
check(images.titles_on_disk() == [("movie", "31")],
      f"deleting the downloaded data deletes the book covers and leaves a movie's with the same number ({images.titles_on_disk()})")
images.forget_titles()

readarr.save_settings(False, "http://readarr.test", KEY)
db.forget_settings()
cover_requests.clear()
check(asyncio.run(posters.provisional_poster(41)) is None and cover_requests == [], "Readarr switched off: no request outside")
try:
    images.title_file("book", "²")
    check(False, "a non-ASCII digit is refused")
except ValueError as error:
    check("TMDB" not in str(error), f"the id is checked as digits and the message no longer claims TMDB only ({error})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
