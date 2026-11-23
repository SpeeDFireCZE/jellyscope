# -*- coding: utf-8 -*-
r"""Radarr: the live downloads with progress (the card "Stahuje se").

The card shows what Radarr downloads right now and how far it is, and refreshes itself. Radarr
refreshes the progress from the download client only once a minute, so while something downloads
Jellyscope sends it the command RefreshMonitoredDownloads - the only thing it sends to Radarr. Guarded:

A) the client: the whole queue in one page and only with the fields used (the app's messages and
   paths are not read); the refresh command as a POST with the same protection of the key as a GET;
B) grouping: queue records into downloads, downloads into blocks by movie; progress, state and the
   order of blocks; a movie has no episodes;
C) liveness: the rhythm 5 s / 60 s, the command only while downloading and at most once per read of
   the queue, the cache, cancelling, an unavailable app;
D) the library and the posters of movies in the queue;
E) the card, its body and the slice;
F) fixes from a review.

Radarr does not run: the queue is recorded from a real Radarr with a fake download client
(tests/radarr_responses/queue_stahuje.json, see ~/projects/arr-test/stahovani.py).

Spec: docs/superpowers/specs/2026-11-13-zive-stahovani-design.md

Run:
    .venv/bin/python tests/test_radarr_progress.py
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "radarr-progress.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import RADARR_RESPONSES, no_network, card_html, recorded_response, login, connect_radarr, radarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, cards_base, cards_radarr, db, images, services, stats  # noqa: E402
from jellyscope.arr import radarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no app other than Radarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# A dict, so the recorded queue can be swapped at run time (an empty queue, a queue of another shape).
RECORDED = {"radarr": recorded_response(RADARR_RESPONSES, "queue_stahuje")}
KEY = "tajny-klic"
# Every request: (method, path in the API, params, body, key from the header, host).
requests: list[tuple[str, str, dict[str, str], Any, str, str]] = []
answers: dict[str, httpx.Response | None] = {"instead": None}


def handler():
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v3/", 1)[-1]
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, path, dict(request.url.params), body,
                         request.headers.get("X-Api-Key", ""), request.url.host))
        if answers["instead"] is not None:
            return answers["instead"]
        if request.method == "POST" and path == "command":
            return httpx.Response(201, json={"id": 7, "name": (body or {}).get("name"), "status": "queued"})
        if request.method == "GET" and path == "queue":
            return httpx.Response(200, json=RECORDED["radarr"])
        return httpx.Response(404)
    return answer


def client() -> Any:
    return radarr.RadarrClient("http://radarr.test", KEY, transport=httpx.MockTransport(handler()))


async def whole() -> dict[str, Any]:
    async with client() as c:
        return await c.whole_queue()


async def refresh() -> None:
    async with client() as c:
        await c.refresh_downloads()


def command_error(answer: httpx.Response) -> str:
    """The text of the error the refresh command ends with on the given answer of the app ("" = no error)."""
    answers["instead"] = answer
    try:
        asyncio.run(refresh())
    except radarr.RadarrError as error:
        return str(error)
    finally:
        answers["instead"] = None
    return ""


print("--- A) the client: the whole queue and the refresh command ---")
queue = asyncio.run(whole())
check(queue["total"] == 4 and len(queue["records"]) == len(RECORDED["radarr"]["records"]) == 4,
      f"Radarr's whole queue: the records and the count ({queue['total']}, {len(queue['records'])})")
method, path, params = requests[-1][:3]
check((method, path) == ("GET", "queue") and params.get("pageSize") == "200" and params.get("page") == "1"
      and params.get("includeMovie") == "true", f"one page of up to 200 records with the movie ({params})")
# A download Radarr did not pair with a movie does not come in the queue at all without the flag
# (checked on a real Radarr) - the client downloads and the card would claim nothing.
check(params.get("includeUnknownMovieItems") == "true" and "includeUnknownSeriesItems" not in params
      and "includeSeries" not in params and "includeEpisode" not in params,
      f"the queue with unpaired downloads too; Radarr is not asked about series and episodes ({sorted(params)})")
check(all(not ({"statusMessages", "errorMessage", "outputPath"} & set(r)) for r in queue["records"]),
      "the app's messages and paths are never read from the answer")
first = next(r for r in queue["records"] if r.get("status") == "downloading")
check({"downloadId", "size", "sizeleft", "status", "trackedDownloadState", "timeleft", "movieId", "downloadClient",
       "protocol", "title"} <= set(first) and first["movie"]["title"] == "Inception" and first["movie"]["tmdbId"] == 27205
      and not {"images", "overview", "alternateTitles"} & set(first["movie"]) and "episode" not in first and "series" not in first,
      f"a record carries what the card draws, and only a few fields of the embedded movie ({sorted(first['movie'])})")
asyncio.run(refresh())
check(requests[-1][:2] == ("POST", "command") and requests[-1][3] == {"name": "RefreshMonitoredDownloads"}
      and requests[-1][4] == KEY and KEY not in str(requests[-1][2]),
      f"the refresh command: POST command with the key only in the header ({requests[-1][:4]})")
# The command is only a nudge: when the app hangs, reading the queue is not to wait on it long (its own short limit).
timeouts: list[tuple[str, Any]] = []


def with_timeout(request: httpx.Request) -> httpx.Response:
    timeouts.append((request.method, request.extensions.get("timeout")))
    return httpx.Response(201, json={})


async def command_and_queue() -> None:
    async with radarr.RadarrClient("http://radarr.test", KEY, transport=httpx.MockTransport(with_timeout)) as c:
        await c.refresh_downloads()
        await c.whole_queue()


asyncio.run(command_and_queue())
check([m for m, _ in timeouts] == ["POST", "GET"] and timeouts[0][1]["read"] <= 3 and timeouts[0][1]["connect"] <= 3
      and timeouts[1][1]["read"] > timeouts[0][1]["read"],
      f"the refresh command has a short limit of its own, reading the queue the usual one ({timeouts[0][1]}, {timeouts[1][1]})")
requests.clear()
text = command_error(httpx.Response(302, headers={"Location": "http://jinde.test/api/v3/command"}))
check(bool(text) and KEY not in text and {r[5] for r in requests} == {"radarr.test"},
      f"a POST does not follow a redirect (the key would go elsewhere) and ends with an error without the key ({text})")
text = command_error(httpx.Response(500, text=f"chyba, klíč {KEY}"))
check("500" in text and KEY not in text, f"the error body goes out without the key ({text})")
check("401" in command_error(httpx.Response(401)), "a refused key is an understandable error")

print()
print("--- B) grouping: downloads and blocks by movie ---")
progress = radarr.progress
RADARR_QUEUE = queue["records"]          # the recorded queue as the client returns it
TEMPLATE = next(r for r in RADARR_QUEUE if r.get("status") == "downloading")      # Inception


def record(**changes: Any) -> dict[str, Any]:
    """A queue record after a real one (Inception), with values of its own."""
    return {**TEMPLATE, **changes}


def movie(number: int, name: str, **changes: Any) -> dict[str, Any]:
    """A queue record of another movie (its own id, name and download)."""
    return record(**{"movieId": number, "movie": {"id": number, "title": name, "tmdbId": 1000 + number}, "downloadId": f"film-{number}", **changes})


def blocks_of(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return progress.blocks(records)


b = blocks_of(RADARR_QUEUE)
by_title = {x["title"]: x for x in b}
check(set(by_title) == {"Inception", "Dune", "Arrival", "Elephants Dream"} and len(b) == 4, f"one block per movie ({[x['title'] for x in b]})")
check([(x["title"], x["state"]) for x in b]
      == [("Inception", "downloading"), ("Dune", "waiting_client"), ("Arrival", "paused"), ("Elephants Dream", "stuck")],
      f"order: what downloads, then waiting and paused, then stuck ({[(x['title'], x['state']) for x in b]})")
check(not any({"description", "episode_count", "_episodes", "_episode_numbers", "_title_key"} & (set(x) | {k for d in x["downloads"] for k in d}) for x in b),
      "a movie has no episodes: neither the block nor a download carries an episode description or count (nor any working key)")
inception = by_title["Inception"]
check((inception["count"], inception["size_bytes"], inception["grabbed"], inception["percent"], inception["seconds_left"])
      == (1, 12884901888, 12884901888 - 12514754560, 2, 3 * 3600 + 18 * 60 + 55) and len(inception["downloads"]) == 1,
      f"a movie's block: the size, the downloaded bytes, the percent and the time left ({inception['percent']} %, {inception['seconds_left']} s)")
check(inception["client"] == "Klient s průběhem" and inception["protocol"] == "usenet" and inception["tmdb_id"] == "27205"
      and inception["title_id"] == 6, f"a block carries the client, the protocol and the movie's id ({inception['client']}, {inception['tmdb_id']})")
check(not any({"message", "statusMessages", "errorMessage", "outputPath"} & (set(x) | {k for d in x["downloads"] for k in d}) for x in b),
      "the app's messages do not get into the blocks")
# `importBlocked` exists only in newer versions of the apps. Older ones report a stuck import as "completed
# + importPending" with `trackedDownloadStatus` warning/error. Without a warning it is ordinary waiting for the import.
finished = {s: blocks_of([record(status="completed", trackedDownloadState="importPending", trackedDownloadStatus=s, sizeleft=0)])[0]["state"]
            for s in ("ok", "warning", "error", None)}
check(finished == {"ok": "waiting_import", "warning": "stuck", "error": "stuck", None: "waiting_import"}
      and blocks_of([record(trackedDownloadStatus="warning")])[0]["state"] == "downloading",
      f"finished with a warning or an error at the import is stuck, without them it waits; what downloads keeps downloading ({finished})")
# Two records of the same download (the same `downloadId`) are one download; two releases of the same movie are two.
same = blocks_of([record(downloadId="A", size=1000, sizeleft=400), record(downloadId="A", size=1000, sizeleft=400)])[0]
check((same["count"], same["size_bytes"], same["grabbed"], same["percent"]) == (1, 1000, 600, 60),
      f"two records of one download: the size and the progress once ({same['count']}, {same['size_bytes']}, {same['percent']})")
two = blocks_of([record(downloadId="A", size=1000, sizeleft=500, status="downloading", timeleft="00:10:00"),
                 record(downloadId="B", size=3000, sizeleft=3000, status="paused", timeleft=None)])[0]
check((two["percent"], two["state"], two["seconds_left"], two["count"], len(two["downloads"])) == (12, "downloading", 600, 2, 2),
      f"a movie downloaded twice: one block, the progress from the sums, the state by the order ({two['percent']}, {two['state']}, {two['count']})")
check(services.integer(float("inf")) == 0 and blocks_of([record(size=float("inf"), sizeleft=float("-inf"))])[0]["percent"] is None,
      "infinity instead of a number (JSON allows it) is nonsense like any other, it does not bring the page down")
VARIOUS_STATES = [movie(11, "E", status="completed", trackedDownloadState="importBlocked"),
                  movie(12, "D", status="completed", trackedDownloadState="importPending"),
                  movie(13, "C", status="paused"), movie(14, "B", status="queued"), movie(15, "A", status="downloading")]
check([x["state"] for x in blocks_of(VARIOUS_STATES)] == ["downloading", "waiting_client", "paused", "waiting_import", "stuck"],
      f"the order of blocks by state ({[x['state'] for x in blocks_of(VARIOUS_STATES)]})")
ties = blocks_of([movie(21, "Zebra", timeleft="00:20:00"), movie(22, "alfa", timeleft="00:20:00"), movie(23, "Beta", timeleft="00:05:00")])
check([x["title"] for x in ties] == ["Beta", "alfa", "Zebra"],
      f"the same state: the shorter time left first, on a tie by the name regardless of case ({[x['title'] for x in ties]})")
other_states = {(s, t): blocks_of([record(status=s, trackedDownloadState=t)])[0]["state"]
                for s, t in (("delay", "downloading"), ("downloadClientUnavailable", "downloading"), ("completed", "importing"),
                             ("completed", "failedPending"), ("failed", "failed"), ("warning", "downloading"), ("divny", "x"))}
check(list(other_states.values()) == ["postponed", "client_not_responding", "importing", "stuck", "failed", "warnings", "divny"],
      f"the other states; an unknown one as the app sent it ({list(other_states.values())})")
without_title = blocks_of([record(movie=None, movieId=None, title="Vydani.2021.1080p")])[0]
check((without_title["title"], without_title["title_id"], without_title["tmdb_id"]) == ("Vydani.2021.1080p", None, ""),
      "a record without a movie: a block with the release's name, without an id")
check(len(blocks_of([record(movie=None, movieId=None, title="X", downloadId="N"), record(movie=None, movieId=None, title="Y", downloadId="N")])) == 2,
      "two unpaired downloads are two blocks, even if they had the same download id")
for size, left in ((0, 0), (100, 500), (100, -5), (None, None), ("x", "y")):
    one_block = blocks_of([record(size=size, sizeleft=left)])[0]
    check(one_block["percent"] is None or 0 <= one_block["percent"] <= 100, f"size {size!r}, left {left!r}: the progress within bounds ({one_block['percent']})")
check([progress.seconds(x) for x in ("00:12:34", "1.02:03:04", "00:00:07.5000000", "", None, "x", 5)] == [754, 93784, 7, None, None, None, None],
      "the time left in every shape")
check(len(blocks_of([record(downloadId="M"), movie(2, "Dva", downloadId="M")])) == 2, "one download for two movies: two blocks")

print()
print("--- C) liveness: rhythm, the command only while downloading, the cache ---")
clock = {"now": 1000.0}
radarr.progress._now = lambda: clock["now"]
live_queues: dict[str, Any] = {"radarr": RECORDED["radarr"], "command_fails": False, "down": False}


def live_radarr(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v3/", 1)[-1]
    requests.append((request.method, path, dict(request.url.params), None, request.headers.get("X-Api-Key", ""), request.url.host))
    if live_queues["down"]:
        raise httpx.ConnectError("Connection refused", request=request)
    if request.method == "POST":
        return httpx.Response(500, text="nejde") if live_queues["command_fails"] else httpx.Response(201, json={"id": 1})
    return httpx.Response(200, json=live_queues["radarr"])


def move(seconds: float) -> None:
    clock["now"] += seconds


def commands() -> int:
    return sum(r[0] == "POST" and r[1] == "command" for r in requests)


def queue_reads() -> int:
    return sum(r[0] == "GET" and r[1] == "queue" for r in requests)


def read() -> dict[str, Any]:
    return asyncio.run(radarr.download_state())


async def in_one_loop(how_many: int) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*(radarr.download_state() for _ in range(how_many))))


def cards_in_threads(how_many: int) -> None:
    """Cards of several viewers at once: each in its own thread through live(), as when a page is drawn."""
    threads = [threading.Thread(target=lambda: cards_base.live(radarr.download_state)) for _ in range(how_many)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)


restore_client = substitute(radarr, "RadarrClient", radarr_client(live_radarr))
connect_radarr()
requests.clear()
s1 = read()
check(s1["available"] and s1["live"] and s1["refresh"] == 5 and commands() == 0 and len(s1["blocks"]) == 4 and s1["more_count"] == 0,
      f"the first read: something downloads, rhythm 5 s, no command is sent ({s1['refresh']}, {commands()})")
move(5)
read()
# Since 23. 11. 2026 the command goes beside the read (a hung app must not cost two time limits): both in this read.
check(commands() == 1 and queue_reads() == 2 and sorted(method for method, *_rest in requests[-2:]) == ["GET", "POST"],
      "downloading: the next read sends the refresh command beside reading the queue")
# The browser asks every 5 s measured on its side; the request arrives at the server a few ms earlier or later.
# So the command goes with every real read of the queue - and thanks to the cache those are at least 4 s apart.
move(4.99)
read()
check(commands() == 2 and queue_reads() == 3, f"a request a hair under 5 s: the queue is read and the command goes with it ({commands()})")
move(1)
read()
read()
check(commands() == 2 and queue_reads() == 3, "within 4 s it reads from the cache and sends no command")
move(5)
cards_in_threads(3)
check(commands() == 3 and queue_reads() == 4, f"three viewers' cards at once: one request and one command ({queue_reads()}, {commands()})")
move(5)
asyncio.run(in_one_loop(3))
check(commands() == 4 and queue_reads() == 5, f"three slices at once in one loop: one request and one command too ({queue_reads()}, {commands()})")
before_pair = commands()
for _ in range(4):
    move(4.5)
    read()               # the second viewer: a real read
    move(0.5)
    read()               # the first viewer: from the cache
check(commands() - before_pair == 4, f"two viewers 4.5 s apart: the command with every read, not every other ({commands() - before_pair} in 20 s)")
before_end = commands()
live_queues["radarr"] = {**RECORDED["radarr"], "records": [], "totalRecords": 0}
move(5)
s = read()
check(s["blocks"] == [] and not s["live"] and s["refresh"] == 60 and commands() == before_end + 1, "a cancelled download disappears at the next refresh, rhythm a minute")
move(60)
read()
check(commands() == before_end + 1, "after a calm (an empty queue) no command is sent")
live_queues.update(radarr=RECORDED["radarr"], command_fails=True)
move(60)
read()
move(5)
s = read()
check(s["available"] and len(s["blocks"]) == 4 and commands() == before_end + 2, f"an error of the command does not stop reading the queue ({commands()})")
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
      and commands() == attempts, f"the app does not answer: unavailable, a minute's rhythm; the next read sends no command any more ({s['error']})")
live_queues["down"] = False
db.set_setting("arr_radarr_url", "http://jinde.test")
db.forget_settings()
read()
check(requests[-1][5] == "jinde.test", f"a change of the address in Settings: it reads from the new one at once, not from the cache ({requests[-1][5]})")
db.set_setting("arr_radarr_url", "http://radarr.test")
db.forget_settings()
live_queues["radarr"] = {"records": [movie(100 + i, f"Film {i:02d}") for i in range(70)], "totalRecords": 250}
move(10)
s = read()
check(len(s["blocks"]) == 60 and s["more_count"] == 10 and s["incomplete"], f"at most 60 blocks, the rest as a count; a queue over one page admits it ({len(s['blocks'])}, {s['more_count']}, {s['incomplete']})")
live_queues["radarr"] = RECORDED["radarr"]
move(10)
read()
check(radarr.progress.fresh_tmdb(6) == "27205" and radarr.progress.fresh_tmdb(999) == "",
      "the movie's TMDB id from the queue read last (for the poster of a movie still downloading)")

print()
print("--- D) the library and the posters of movies in the queue ---")
JPEG = b"\xff\xd8\xff\xe0" + b"jellyscope-test" * 8
image: dict[str, Any] = {"body": JPEG, "code": 200}


def with_posters():
    """A Radarr that serves the movies' posters besides the queue (mediacover)."""
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v3/", 1)[-1]
        requests.append((request.method, path, dict(request.url.params), None, request.headers.get("X-Api-Key", ""), request.url.host))
        if image.get("down"):
            raise httpx.ConnectError("Connection refused", request=request)
        if path.startswith("mediacover/"):
            return httpx.Response(image["code"], content=image["body"], headers={"Content-Type": "image/jpeg"})
        if path == "queue":
            return httpx.Response(200, json=RECORDED["radarr"])
        return httpx.Response(201, json={"id": 1}) if request.method == "POST" else httpx.Response(404)
    return answer


def fetched_posters() -> list[str]:
    return [r[1] for r in requests if r[1].startswith("mediacover/")]


def db_exec(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)
        conn.commit()


restore_client()
substitute(radarr, "RadarrClient", radarr_client(with_posters()))
now = db.utcnow()
# Inception (6) is not in the library, The Matrix (5) is. Arrival (8) and Dune (9) are not in the list of
# movies yet - only the queue just read knows them.
for movie_id, name, tmdb in ((6, "Inception", "27205"), (5, "The Matrix", "603"), (77, "Bez TMDB id", "")):
    db_exec("INSERT INTO radarr_title (title_id, name, tmdb_id) VALUES (?, ?, ?)", (movie_id, name, tmdb))
db_exec("INSERT INTO items (id, name, type, tmdb_id, image_tag, date_created, synced_at) VALUES ('m603', 'The Matrix', 'Movie', '603', 'otisk-m', ?, ?)", (now, now))
check(stats.radarr_library({5, 6}) == {5: {"detail_url": "/item/m603", "poster_id": "m603", "poster_tag": "otisk-m"}},
      f"a movie from the library: its detail and its poster ({stats.radarr_library({5, 6})})")
check(stats.radarr_detail_links({5, 6}) == {5: "/item/m603"}, "the details give the same addresses")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.save_permissions(accounts.get_by_name("reader")["id"], access.pack_permissions(["library"], False))
db.forget_settings()
admin, reader_without_arr = login("admin"), login("reader")
requests.clear()
answer = admin.get("/arr/radarr/poster/6")
check(answer.status_code == 200 and answer.content == JPEG and answer.headers["content-type"] == "image/jpeg"
      and fetched_posters() == ["mediacover/6/poster-250.jpg"] and images.title_file("movie", "27205").exists(),
      f"a movie outside the library: the poster from Radarr, stored as the title's provisional poster ({answer.status_code}, {fetched_posters()})")
check(admin.get("/arr/radarr/poster/6").status_code == 200 and len(fetched_posters()) == 1, "the second time from the disk, the app is not asked")
check(admin.get("/arr/radarr/poster/8").status_code == 200 and fetched_posters()[-1] == "mediacover/8/poster-250.jpg"
      and images.title_file("movie", "329865").exists(), "a movie only the queue just read knows gets a poster too")
requests.clear()
check([admin.get(f"/arr/radarr/poster/{title}").status_code for title in (999, 77)] == [404, 404] and fetched_posters() == []
      and admin.get("/arr/radarr/poster/999").headers.get("cache-control") == "no-store",
      "an unknown movie and a movie without a TMDB id: 404 without asking the app and without keeping it in the browser")
# Dune (9): its poster is not downloaded yet.
image["body"] = b"<html>Sign in</html>"
check(admin.get("/arr/radarr/poster/9").status_code == 404 and not images.title_file("movie", "438631").exists()
      and fetched_posters() == ["mediacover/9/poster-250.jpg"], "the app sends a page instead of an image: not stored, 404")
image["body"] = JPEG
check(admin.get("/arr/radarr/poster/9").status_code == 404 and len(fetched_posters()) == 1, "that there is no poster is remembered for five minutes")
radarr.posters._MISSING.clear()
check(admin.get("/arr/radarr/poster/9").status_code == 200, "and then it is tried again")
check(reader_without_arr.get("/arr/radarr/poster/6").status_code == 403 and admin.get("/arr/nothing/poster/6").status_code == 404
      and admin.get("/arr/sonarr/poster/6").status_code == 404,
      "without the *Arr area 403; an app Jellyscope does not know or that is not connected 404")
# The database and the disk belong in a thread: the poster route runs in the server's loop and the loop would wait for them as a whole.
where_it_runs: list[bool] = []
original_tmdb, original_load = radarr.posters._tmdb_id, images.load_title
radarr.posters._tmdb_id = lambda i: (where_it_runs.append(threading.current_thread() is threading.main_thread()), original_tmdb(i))[1]
images.load_title = lambda kind, tmdb: (where_it_runs.append(threading.current_thread() is threading.main_thread()), original_load(kind, tmdb))[1]
from_thread = asyncio.run(radarr.provisional_poster(6))
radarr.posters._tmdb_id, images.load_title = original_tmdb, original_load
check(from_thread is not None and where_it_runs and not any(where_it_runs),
      f"looking up the movie and reading the poster from the disk run in a thread, not in the loop ({where_it_runs})")
db_exec("INSERT INTO items (id, name, type, tmdb_id, date_created, synced_at) VALUES ('m27205', 'Počátek', 'Movie', '27205', ?, ?)", (now, now))
check(radarr.clean_up_posters() == 1 and not images.title_file("movie", "27205").exists() and images.title_file("movie", "329865").exists()
      and images.title_file("movie", "438631").exists(), "the movie arrived in the library: the clean-up deletes its provisional poster and leaves the others")

print()
print("--- E) the card Stahuje se, its body and the slice ---")
check(len(cards_radarr.CARDS) == 23 and len(cards.ALL_CARDS) == 206,
      f"23 Radarr cards, 206 in the registry ({len(cards_radarr.CARDS)}, {len(cards.ALL_CARDS)})")
check(cards_radarr.SECTIONS[0][1][:2] == ("live_numbers", "downloading"), "Stahuje se is the second card of the section Teď v aplikaci (now in the app)")
accounts.create("viewer", "longpassword")                 # default permissions: it has the *Arr area, it is no admin
viewer = login("viewer")


def fresh() -> None:
    """The next read goes to the app, not to the cache (not even after an error, which is kept a minute)."""
    move(61)


def whole_card(html: str, key: str) -> str:
    """The card with its dialogs: from its mark to the next card's mark (card_html ends at the first <dialog>)."""
    start = html.find(f"<!-- card: {key} -->")
    end = html.find("<!-- card: ", start + 1)
    return html_lib.unescape(html[start:end if end > 0 else len(html)]) if start >= 0 else ""


def plain(part: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", part).split())


# The queue for the card: the recorded four, a second release of Inception (a movie downloaded twice) and The Matrix from the library.
full = RECORDED["radarr"]
RECORDED["radarr"] = {"totalRecords": 6, "records": [
    *full["records"], record(downloadId="druhe-vydani", size=4000, sizeleft=1000, timeleft="00:01:00"),
    record(movieId=5, movie={"id": 5, "title": "The Matrix", "tmdbId": 603}, downloadId="matrix", size=2147483648, sizeleft=2147483648,
           status="queued", timeleft=None)]}
fresh()
page = admin.get("/arr/radarr").text
card = whole_card(page, "arr_radarr_downloading")
check('data-partial="/partials/arr/radarr/downloading"' in card and 'data-refresh="5"' in card and "<h2>Stahuje se</h2>" in card,
      "the card has its heading and its body as a self-refreshing slice in a 5 s rhythm")
check(card.count("<dialog") == 1 and 'id="modal-downloading-radarr-6"' in card and card.count('data-modal="modal-downloading-radarr-6"') == 2,
      f"a dialog with the breakdown only for a movie with several downloads; the poster and the link in the block open it ({card.count('<dialog')})")
card_blocks = {plain(part)[:40]: part for part in card.split("<dialog")[0].split('<div class="session">')[1:]}
inception_block = next(part for name, part in card_blocks.items() if "Inception" in name)
dune_block = next(part for name, part in card_blocks.items() if "Dune" in name)
inception_dialog = card.split('id="modal-downloading-radarr-6"')[1].split("</dialog>")[0]
check('role="progressbar"' not in inception_block and "%" not in plain(inception_block) and "zbývá" not in inception_block
      and "zobrazit průběh" in inception_block and "2 stahování" in inception_block and "stahuje se" in inception_block,
      f"a movie with several downloads: no overall progress in the block, only the state, the count of downloads and the way to the dialog ({plain(inception_block)[:90]})")
check(inception_dialog.count('role="progressbar"') == 2 and inception_dialog.count("<strong>Inception</strong>") == 2 and "zbývá" in inception_dialog,
      "the progress of every download is in the dialog, under the movie's name")
check('role="progressbar"' in dune_block and 'aria-valuenow="0"' in dune_block and "zobrazit průběh" not in dune_block
      and "session-facts" not in dune_block, "a movie with one download has its progress right in the block and no extra line")
check(not re.search(r"\bdíl", plain(card)) and "S0" not in plain(card), "Radarr's card says not a word about episodes")
check(all(text in card for text in ("stahuje se", "pozastaveno", "čeká ve frontě klienta", "import se nepovedl", "Klient s průběhem",
                                    "usenet", "zbývá")) and "stuck" not in plain(card),
      "the states in Czech, the client and the protocol, the time left")
check('src="/image/m603?kind=Primary&w=300&tag=otisk-m"' in card and 'href="/item/m603"' in card
      and 'src="/arr/radarr/poster/9"' in card and 'href="/item/m27205"' in card,
      "a movie from the library has its poster from Jellyfin and a link, a movie outside the library a provisional poster without a link")
card_text = plain(card)
check("- z " not in card_text and "0 % · 10,0 GB" in card_text, "a download that has not started yet writes only the size (not \"- z 10,0 GB\")")
check(not any(text in card for text in ("No files found", "eligible", "/client/hotove", "No audio tracks")),
      "the app's messages and paths are not on the card, not even for the admin")
slice_answer = admin.get("/partials/arr/radarr/downloading")
check(slice_answer.status_code == 200 and slice_answer.headers.get("X-Jellyscope-Refresh") == "5" and "<h2>Stahuje se</h2>" not in slice_answer.text
      and 'class="sessions"' in slice_answer.text and slice_answer.text.count("<dialog") == 1 and slice_answer.headers.get("cache-control") == "no-store",
      f"the slice: only the card's body, the rhythm in a header, not kept ({slice_answer.status_code}, {slice_answer.headers.get('X-Jellyscope-Refresh')})")
check(reader_without_arr.get("/partials/arr/radarr/downloading").status_code == 403
      and admin.get("/partials/arr/nothing/downloading").status_code == 404
      and admin.get("/partials/arr/sonarr/downloading").status_code == 404, "the slice: without the *Arr area 403, an app not connected 404")
RECORDED["radarr"] = {"records": [], "totalRecords": 0}
fresh()
empty = admin.get("/partials/arr/radarr/downloading")
check("Teď se nic nestahuje." in empty.text and empty.headers.get("X-Jellyscope-Refresh") == "60" and "<dialog" not in empty.text,
      "an empty queue: one sentence and a minute's rhythm")
fresh()
check('data-refresh="60"' in card_html(admin.get("/arr/radarr").text, "arr_radarr_downloading"), "the card on the page then carries the minute's rhythm too")
RECORDED["radarr"] = full
image["down"] = True
fresh()
for_viewer = viewer.get("/partials/arr/radarr/downloading")
check("Aplikace neodpovídá." in for_viewer.text and "Connection refused" not in for_viewer.text and for_viewer.headers.get("X-Jellyscope-Refresh") == "60",
      "the app does not answer: a sentence without details, a minute's rhythm")
check("Connection refused" in admin.get("/partials/arr/radarr/downloading").text and KEY not in admin.get("/partials/arr/radarr/downloading").text,
      "only the admin sees the error's detail, nobody the key")
image["down"] = False
fresh()
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
cards.save_layout(["arr_radarr_downloading"])
dashboard = html_lib.unescape(admin.get("/dashboard").text)
check("Radarr · Stahuje se" in dashboard and dashboard.count('data-partial="/partials/arr/radarr/downloading"') == 1 and "Inception" in dashboard,
      "on the dashboard the card with the app's prefix and with its slice")

print()
print("--- F) fixes from a review ---")
# 1) An app that does not answer is not tried again within a minute. A success holds 4 s, but an error
#    used to be kept just as briefly: a stuck app then cost a wait on every page load.
image["down"] = True
fresh()
requests.clear()
for _ in range(3):
    bad = read()
    move(15)
check(queue_reads() == 1 and not bad["available"] and bad["refresh"] == 60,
      f"the app does not answer: not tried again within a minute ({queue_reads()} requests in 45 s)")
move(20)
read()
check(queue_reads() == 2, f"after a minute it is tried again ({queue_reads()})")
image["down"] = False
fresh()
check(read()["available"], "and when it answers again, the card comes back")

# 2) A queue of an unexpected shape is the app's error, not a crash of the page.
real_blocks = progress.blocks


def broken(*_a: Any) -> list[dict[str, Any]]:
    raise TypeError("fronta v necekanem tvaru")


progress.blocks = broken
fresh()
try:
    odd = read()
except Exception as exception:  # noqa: BLE001 - exactly what is guarded
    odd = {"vyjimka": repr(exception)}
progress.blocks = real_blocks
check(odd.get("available") is False and odd.get("blocks") == [] and odd.get("refresh") == 60 and KEY not in str(odd.get("error")),
      f"a queue of an unexpected shape: the card says the app does not answer, the page does not crash ({str(odd)[:90]})")
fresh()

# 3) A poster known to be missing is not offered again by the card: the body changes every 5 s and the
#    browser would ask for it at every swap (and the placeholder would flash in place of the poster every time).
images.forget_title("movie", "438631")
radarr.posters._MISSING.clear()
image["code"] = 404
with_poster = admin.get("/partials/arr/radarr/downloading").text
none = admin.get("/arr/radarr/poster/9").status_code
fresh()
without_poster = admin.get("/partials/arr/radarr/downloading").text
radarr.posters._MISSING.clear()
fresh()
check('src="/arr/radarr/poster/9"' in with_poster and none == 404 and 'src="/arr/radarr/poster/9"' not in without_poster
      and "Dune" in without_poster and 'src="/arr/radarr/poster/9"' in admin.get("/partials/arr/radarr/downloading").text,
      "a poster the app does not have is not offered by the card for five minutes (a placeholder right away), then it is tried again")
image["code"] = 200

# 4) A finished download without a warning waits for the import; the line with the progress does not
#    start with a separator when the size is unknown (a torrent still fetching its metadata).
RECORDED["radarr"] = {"totalRecords": 2, "records": [
    record(downloadId="ok1", status="completed", trackedDownloadState="importPending", trackedDownloadStatus="ok", sizeleft=0),
    movie(50, "Bez velikosti", size=0, sizeleft=0)]}
fresh()
slice_f = admin.get("/partials/arr/radarr/downloading").text
lines_f = [" ".join(re.sub(r"<[^>]+>", " ", html_lib.unescape(line)).split())
           for line in re.findall(r'<span class="session-time">(.*?)</span>\s*</div>', slice_f, re.S)]
check("staženo, čeká na převzetí" in slice_f, "finished without a warning: downloaded, waiting for the import")
check(len(lines_f) == 2 and not any(line.startswith("·") for line in lines_f) and any(line.startswith("zbývá") for line in lines_f),
      f"the line under the bar does not start with a separator, not even when the size is unknown ({lines_f})")

# 5) The sentence about movies over the limit has the right form.
RECORDED["radarr"] = {"totalRecords": 61, "records": [movie(100 + i, f"Film {i:02d}") for i in range(61)]}
fresh()
long_text = plain(html_lib.unescape(admin.get("/partials/arr/radarr/downloading").text))
check("A 1 další titul ve frontě." in long_text and "dalších 1" not in long_text, f"one movie over the limit: \"A 1 další titul\" ({long_text[-60:]})")
RECORDED["radarr"] = full
fresh()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
