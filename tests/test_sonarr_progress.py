# -*- coding: utf-8 -*-
r"""Sonarr: the live downloads with progress (the card "Stahuje se").

The card shows what Sonarr downloads right now and how far it is, and refreshes itself. Sonarr
refreshes the progress from the download client only once a minute, so while something downloads
Jellyscope sends it the command RefreshMonitoredDownloads - the only thing it sends to Sonarr. Guarded:

A) the client: the whole queue in one page and only with the fields used (the app's messages and
   paths are not read); the refresh command as a POST with the same protection of the key as a GET;
B) grouping: queue records into downloads (a season pack is one), downloads into blocks by series;
   progress, state and the order of blocks;
C) liveness: the rhythm 5 s / 60 s, the command only while downloading and at most once per 5 s per
   app, the cache, cancelling, an unavailable app;
D) the library and the posters of titles in the queue;
E) the card, its body and the slice.

Sonarr does not run: the queue is recorded from a real Sonarr with a fake download client
(tests/sonarr_responses/queue_stahuje.json).

Spec: docs/superpowers/specs/2026-11-13-zive-stahovani-design.md

Run:
    .venv/bin/python tests/test_sonarr_progress.py
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

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sonarr-progress.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import SONARR_RESPONSES, no_network, recorded_response, sonarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import sonarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no app other than Sonarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# A dict, so the recorded queue can be swapped at run time (an empty queue, a queue of another shape).
RECORDED = {"sonarr": recorded_response(SONARR_RESPONSES, "queue_stahuje")}
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
            return httpx.Response(200, json=RECORDED["sonarr"])
        return httpx.Response(404)
    return answer


def client() -> Any:
    return sonarr.SonarrClient("http://sonarr.test", KEY, transport=httpx.MockTransport(handler()))


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
    except sonarr.SonarrError as error:
        return str(error)
    finally:
        answers["instead"] = None
    return ""


print("--- A) the client: the whole queue and the refresh command ---")
queue = asyncio.run(whole())
check(queue["total"] == 11 and len(queue["records"]) == len(RECORDED["sonarr"]["records"]) == 11,
      f"Sonarr's whole queue: the records and the count ({queue['total']}, {len(queue['records'])})")
method, path, params = requests[-1][:3]
check((method, path) == ("GET", "queue") and params.get("pageSize") == "200" and params.get("page") == "1"
      and params.get("includeSeries") == "true" and params.get("includeEpisode") == "true",
      f"one page of up to 200 records with the series and the episode ({params})")
# The final review: a download the app did not pair with a title does not come in the queue at all
# without the flag (checked on a real Sonarr and Radarr) - the client downloads and the card would claim nothing.
check(params.get("includeUnknownSeriesItems") == "true" and "includeUnknownMovieItems" not in params,
      f"Sonarr: the queue with unpaired downloads too ({sorted(params)})")
check(all(not ({"statusMessages", "errorMessage", "outputPath"} & set(r)) for r in queue["records"]),
      "the app's messages and paths are never read from the answer")
first = next(r for r in queue["records"] if r.get("status") == "downloading")
check({"downloadId", "size", "sizeleft", "status", "trackedDownloadState", "timeleft", "seriesId", "downloadClient",
       "protocol", "title"} <= set(first) and first["series"]["title"] and "tmdbId" in first["series"]
      and {"seasonNumber", "episodeNumber", "title"} <= set(first["episode"]) and "images" not in first["series"],
      f"a record carries what the card draws, and only a few fields of the embedded series and episode ({sorted(first['series'])})")
check("includeMovie" not in params and all("movie" not in r for r in queue["records"]), "Sonarr is not asked about movies")
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
    async with sonarr.SonarrClient("http://sonarr.test", KEY, transport=httpx.MockTransport(with_timeout)) as c:
        await c.refresh_downloads()
        await c.whole_queue()


asyncio.run(command_and_queue())
check([m for m, _ in timeouts] == ["POST", "GET"] and timeouts[0][1]["read"] <= 3 and timeouts[0][1]["connect"] <= 3
      and timeouts[1][1]["read"] > timeouts[0][1]["read"],
      f"the refresh command has a short limit of its own, reading the queue the usual one ({timeouts[0][1]}, {timeouts[1][1]})")
requests.clear()
text = command_error(httpx.Response(302, headers={"Location": "http://jinde.test/api/v3/command"}))
check(bool(text) and KEY not in text and {r[5] for r in requests} == {"sonarr.test"},
      f"a POST does not follow a redirect (the key would go elsewhere) and ends with an error without the key ({text})")
text = command_error(httpx.Response(500, text=f"chyba, klíč {KEY}"))
check("500" in text and KEY not in text, f"the error body goes out without the key ({text})")
check("401" in command_error(httpx.Response(401)), "a refused key is an understandable error")

print()
print("--- B) grouping: downloads and blocks by title ---")
progress = sonarr.progress

SONARR_QUEUE = queue["records"]          # the recorded queue as the client returns it
TEMPLATE = next(r for r in SONARR_QUEUE if r.get("status") == "downloading" and r["series"]["title"] == "The Expanse")


def record(**changes: Any) -> dict[str, Any]:
    """A queue record after a real one (The Expanse S01E01), with values of its own. `episode_no=` sets the episode's number."""
    episode_no = changes.pop("episode_no", None)
    new = {**TEMPLATE, **changes}
    if episode_no is not None:
        new["episode"] = {**TEMPLATE["episode"], "episodeNumber": episode_no, "title": f"Díl {episode_no}"}
        new["episodeId"] = 1000 + episode_no
    return new


def blocks_of(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return progress.blocks(records)


b = blocks_of(SONARR_QUEUE)
by_title = {x["title"]: x for x in b}
check(set(by_title) == {"The Expanse", "Chernobyl", "Dark", "Game of Thrones"} and len(b) == 4,
      f"one block per series ({[x['title'] for x in b]})")
expanse, chernobyl, dark, got = (by_title.get(n, {}) for n in ("The Expanse", "Chernobyl", "Dark", "Game of Thrones"))
check(expanse.get("count") == 3 and len(expanse.get("downloads", [])) == 3 and expanse.get("episode_count") == 3
      and expanse.get("state") == "downloading" and {s["state"] for s in expanse["downloads"]} == {"downloading", "paused"},
      f"three downloads of one series are one block with a breakdown ({expanse.get('count')}, {expanse.get('state')})")
check((chernobyl.get("count"), chernobyl.get("episode_count"), chernobyl.get("size_bytes")) == (1, 5, 21474836480)
      and chernobyl.get("description") == "S01E01-E05" and chernobyl.get("percent") == 1,
      f"a season pack is one download: the size once, the description as a range ({chernobyl.get('count')}, {chernobyl.get('size_bytes')}, "
      f"{chernobyl.get('description')!r}, {chernobyl.get('percent')})")
check(dark.get("state") == "stuck" and {s["state"] for s in dark.get("downloads", [])} == {"stuck", "waiting_client"}
      and got.get("state") == "stuck" and got.get("description", "").startswith("S01E07 · "),
      f"states: a finished download the app cannot import (a warning) is stuck; waiting in the client's queue ({dark.get('state')}, {got.get('state')}, {got.get('description')!r})")
# A review: `importBlocked` exists only in newer versions of the apps. Older ones report a stuck import
# as "completed + importPending" with `trackedDownloadStatus` warning/error - and so do new ones when
# there is nothing to import in the folder. Without a warning it is ordinary waiting for the import.
finished = {s: blocks_of([record(status="completed", trackedDownloadState="importPending", trackedDownloadStatus=s, sizeleft=0)])[0]["state"]
            for s in ("ok", "warning", "error", None)}
check(finished == {"ok": "waiting_import", "warning": "stuck", "error": "stuck", None: "waiting_import"}
      and blocks_of([record(trackedDownloadStatus="warning")])[0]["state"] == "downloading",
      f"finished with a warning or an error at the import is stuck, without them it waits; what downloads keeps downloading ({finished})")
# A review: two releases of the same episode in the queue are two downloads, but still one episode.
same = blocks_of([record(downloadId="A", episode_no=5), record(downloadId="B", episode_no=5), record(downloadId="C", episode_no=6)])[0]
check((same["count"], same["episode_count"]) == (3, 2), f"an episode in two downloads counts once in the block ({same['count']} downloads, {same['episode_count']} episodes)")
from jellyscope import services  # noqa: E402

check(services.integer(float("inf")) == 0 and services.integer(float("nan"), None) is None
      and blocks_of([record(size=float("inf"), sizeleft=float("-inf"))])[0]["percent"] is None,
      "infinity instead of a number (JSON allows it) is nonsense like any other, it does not bring the page down")
check([x["title"] for x in b] == ["The Expanse", "Chernobyl", "Dark", "Game of Thrones"],
      f"order: downloading by the time left, then waiting, then stuck ({[(x['title'], x['seconds_left']) for x in b]})")
# Downloads with the same state and the same time left go in the dialog by episodes, not as the queue happens to send them.
equal = blocks_of([record(downloadId=f"s{e}", timeleft="00:20:00", episode_no=e) for e in (3, 1, 2)])[0]
check([d["description"][:6] for d in equal["downloads"]] == ["S01E01", "S01E02", "S01E03"],
      f"downloads with the same state and time are sorted by episodes ({[d['description'][:6] for d in equal['downloads']]})")
check(expanse.get("client") == "Klient s průběhem" and expanse.get("protocol") == "usenet" and expanse.get("tmdb_id") == "63639"
      and expanse.get("title_id") == 4, f"a block carries the client, the protocol and the title's id ({expanse.get('client')}, {expanse.get('tmdb_id')})")
check(not any({"message", "statusMessages", "errorMessage", "outputPath"} & (set(x) | {k for d in x["downloads"] for k in d}) for x in b),
      "the app's messages do not get into the blocks")
pack = blocks_of([record(downloadId="X", size=1000, sizeleft=400, episode_no=e) for e in (1, 2, 3)])[0]
check((pack["count"], pack["episode_count"], pack["size_bytes"], pack["grabbed"], pack["percent"], pack["description"]) == (1, 3, 1000, 600, 60, "S01E01-E03"),
      f"a pack: the size and the progress once, not per episode ({pack['size_bytes']}, {pack['grabbed']}, {pack['percent']})")
def season_episode(season: int, number: int, **changes: Any) -> dict[str, Any]:
    return record(episode={"seasonNumber": season, "episodeNumber": number, "title": f"Díl {number}"}, episodeId=season * 1000 + number, **changes)


descriptions = {name: blocks_of([season_episode(s, n, downloadId="P") for s, n in episodes])[0]
                for name, episodes in (("contiguous", [(2, 1), (2, 2), (2, 3)]), ("gapped", [(2, 1), (2, 10)]),
                                       ("many_gapped", [(2, n) for n in (1, 3, 5, 7, 9)]),
                                       ("several_seasons", [(1, 1), (1, 2), (2, 1), (3, 1)]), ("every_other_season", [(1, 10), (3, 1)]))}
check({name: (d["description"], d["episode_count"]) for name, d in descriptions.items()}
      == {"contiguous": ("S02E01-E03", 3), "gapped": ("S02E01, E10", 2), "many_gapped": ("S02", 5), "several_seasons": ("S01-S03", 4),
          "every_other_season": ("S01, S03", 2)},
      f"a download's description: a range only for contiguous episodes, else a list or only the season; a pack over several seasons as a range of seasons ({ {name: d['description'] for name, d in descriptions.items()} })")
two = blocks_of([record(downloadId="A", size=1000, sizeleft=500, status="downloading", timeleft="00:10:00", episode_no=1),
                 record(downloadId="B", size=3000, sizeleft=3000, status="paused", timeleft=None, episode_no=2)])[0]
check((two["percent"], two["state"], two["seconds_left"], two["count"]) == (12, "downloading", 600, 2),
      f"a block's progress from the sums, the state by the order, the time left the longest of those downloading ({two['percent']}, {two['state']}, {two['seconds_left']})")
VARIOUS_STATES = [record(downloadId="1", seriesId=11, series={"id": 11, "title": "E"}, status="completed", trackedDownloadState="importBlocked"),
                  record(downloadId="2", seriesId=12, series={"id": 12, "title": "D"}, status="completed", trackedDownloadState="importPending"),
                  record(downloadId="3", seriesId=13, series={"id": 13, "title": "C"}, status="paused"),
                  record(downloadId="4", seriesId=14, series={"id": 14, "title": "B"}, status="queued"),
                  record(downloadId="5", seriesId=15, series={"id": 15, "title": "A"}, status="downloading")]
check([x["state"] for x in blocks_of(VARIOUS_STATES)] == ["downloading", "waiting_client", "paused", "waiting_import", "stuck"],
      f"the order of blocks by state ({[x['state'] for x in blocks_of(VARIOUS_STATES)]})")
other_states = {(s, t): blocks_of([record(status=s, trackedDownloadState=t)])[0]["state"]
                for s, t in (("delay", "downloading"), ("downloadClientUnavailable", "downloading"), ("completed", "importing"),
                             ("completed", "failedPending"), ("failed", "failed"), ("warning", "downloading"), ("divny", "x"))}
check(list(other_states.values()) == ["postponed", "client_not_responding", "importing", "stuck", "failed", "warnings", "divny"],
      f"the other states; an unknown one as the app sent it ({list(other_states.values())})")
without_title = blocks_of([record(series=None, seriesId=None, episode=None, episodeId=None, title="Vydani.S01E01.720p")])[0]
check((without_title["title"], without_title["title_id"], without_title["tmdb_id"]) == ("Vydani.S01E01.720p", None, ""),
      "a record without a title: a block with the release's name, without an id")
for size, left in ((0, 0), (100, 500), (100, -5), (None, None), ("x", "y")):
    one_block = blocks_of([record(size=size, sizeleft=left)])[0]
    check(one_block["percent"] is None or 0 <= one_block["percent"] <= 100, f"size {size!r}, left {left!r}: the progress within bounds ({one_block['percent']})")
check([progress.seconds(x) for x in ("00:12:34", "1.02:03:04", "00:00:07.5000000", "", None, "x", 5)] == [754, 93784, 7, None, None, None, None],
      f"the time left in every shape ({[progress.seconds(x) for x in ('00:12:34', '1.02:03:04', '00:00:07.5000000', '', None, 'x', 5)]})")
check(len(blocks_of([record(downloadId="M", seriesId=1, series={"id": 1, "title": "Jedna"}),
                     record(downloadId="M", seriesId=2, series={"id": 2, "title": "Dva"})])) == 2, "one download for two titles: two blocks")

print()
print("--- C) liveness: rhythm, the command only while downloading, the cache ---")
import threading  # noqa: E402

from _apps import connect_sonarr  # noqa: E402
from jellyscope import cards_base  # noqa: E402

clock = {"now": 1000.0}
sonarr.progress._now = lambda: clock["now"]
live_queues: dict[str, Any] = {"sonarr": RECORDED["sonarr"], "command_fails": False, "down": False}


def live_sonarr(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v3/", 1)[-1]
    requests.append((request.method, path, dict(request.url.params), None, request.headers.get("X-Api-Key", ""), request.url.host))
    if live_queues["down"]:
        raise httpx.ConnectError("Connection refused", request=request)
    if request.method == "POST":
        return httpx.Response(500, text="nejde") if live_queues["command_fails"] else httpx.Response(201, json={"id": 1})
    return httpx.Response(200, json=live_queues["sonarr"])


def move(seconds: float) -> None:
    clock["now"] += seconds


def commands() -> int:
    return sum(r[0] == "POST" and r[1] == "command" for r in requests)


def queue_reads() -> int:
    return sum(r[0] == "GET" and r[1] == "queue" for r in requests)


def read() -> dict[str, Any]:
    return asyncio.run(sonarr.download_state())


async def in_one_loop(how_many: int) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*(sonarr.download_state() for _ in range(how_many))))


def cards_in_threads(how_many: int) -> None:
    """Cards of several viewers at once: each in its own thread through live(), as when a page is drawn."""
    threads = [threading.Thread(target=lambda: cards_base.live(sonarr.download_state)) for _ in range(how_many)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)


restore_client = substitute(sonarr, "SonarrClient", sonarr_client(live_sonarr))
connect_sonarr()
requests.clear()
s1 = read()
check(s1["available"] and s1["live"] and s1["refresh"] == 5 and commands() == 0 and len(s1["blocks"]) == 4 and s1["more_count"] == 0,
      f"the first read: something downloads, rhythm 5 s, no command is sent ({s1['refresh']}, {commands()})")
move(5)
read()
# Since 23. 11. 2026 the command goes beside the read (a hung app must not cost two time limits): both in this read.
check(commands() == 1 and queue_reads() == 2 and sorted(method for method, *_rest in requests[-2:]) == ["GET", "POST"],
      "downloading: the next read sends the refresh command beside reading the queue")
# The final review: the browser asks every 5 s measured on its side; the request arrives at the server
# a few ms earlier or later. With a limit of exactly 5 s every second command dropped out and the
# progress moved every 10 s. So the command goes with every real read of the queue - and thanks to the
# cache those are at least 4 s apart.
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
# Two viewers 4.5 s apart (the second reads just as the first one's cache expired): the command still every 5 s.
before_pair = commands()
for _ in range(4):
    move(4.5)
    read()               # the second viewer: a real read
    move(0.5)
    read()               # the first viewer: from the cache
check(commands() - before_pair == 4, f"two viewers 4.5 s apart: the command with every read, not every other ({commands() - before_pair} in 20 s)")
before_end = commands()
live_queues["sonarr"] = {**RECORDED["sonarr"], "records": [], "totalRecords": 0}
move(5)
s = read()
check(s["blocks"] == [] and not s["live"] and s["refresh"] == 60 and commands() == before_end + 1, "a cancelled download disappears at the next refresh, rhythm a minute")
move(60)
read()
check(commands() == before_end + 1, "after a calm (an empty queue) no command is sent")
live_queues.update(sonarr=RECORDED["sonarr"], command_fails=True)
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
db.set_setting("arr_sonarr_url", "http://jinde.test")
db.forget_settings()
read()
check(requests[-1][5] == "jinde.test", f"a change of the address in Settings: it reads from the new one at once, not from the cache ({requests[-1][5]})")
db.set_setting("arr_sonarr_url", "http://sonarr.test")
db.forget_settings()
many = [record(downloadId=str(i), seriesId=100 + i, series={"id": 100 + i, "title": f"Seriál {i:02d}"}) for i in range(70)]
live_queues["sonarr"] = {"records": many, "totalRecords": 250}
move(10)
s = read()
check(len(s["blocks"]) == 60 and s["more_count"] == 10 and s["incomplete"], f"at most 60 blocks, the rest as a count; a queue over one page admits it ({len(s['blocks'])}, {s['more_count']}, {s['incomplete']})")
live_queues["sonarr"] = RECORDED["sonarr"]
move(10)
read()
check(sonarr.progress.fresh_tmdb(4) == "63639" and sonarr.progress.fresh_tmdb(999) == "",
      "the title's TMDB id from the queue read last (for the poster of a title still downloading)")

print()
print("--- D) the library and the posters of titles in the queue ---")
from _apps import login  # noqa: E402
from jellyscope import access, accounts, images, stats  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"jellyscope-test" * 8
image: dict[str, Any] = {"body": JPEG, "code": 200}


def with_posters():
    """A Sonarr that serves the series' posters besides the queue (mediacover)."""
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v3/", 1)[-1]
        requests.append((request.method, path, dict(request.url.params), None, request.headers.get("X-Api-Key", ""), request.url.host))
        if image.get("down"):
            raise httpx.ConnectError("Connection refused", request=request)
        if path.startswith("mediacover/"):
            return httpx.Response(image["code"], content=image["body"], headers={"Content-Type": "image/jpeg"})
        if path == "queue":
            return httpx.Response(200, json=RECORDED["sonarr"])
        return httpx.Response(201, json={"id": 1}) if request.method == "POST" else httpx.Response(404)
    return answer


def fetched_posters() -> list[str]:
    return [r[1] for r in requests if r[1].startswith("mediacover/")]


def db_exec(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)
        conn.commit()


restore_client()
substitute(sonarr, "SonarrClient", sonarr_client(with_posters()))
now = db.utcnow()
# The Expanse (4) is not in the library, Chernobyl (2) is. Dark (3) and Game of Thrones (1) are not in
# the list of series yet - only the queue just read knows them.
for series_id, name, tmdb, jellyfin_id in ((4, "The Expanse", "63639", ""), (2, "Chernobyl", "87108", "serchernobyl"),
                                           (77, "Bez TMDB id", "", "")):
    db_exec("INSERT INTO sonarr_title (title_id, name, tmdb_id, jellyfin_id) VALUES (?, ?, ?, ?)", (series_id, name, tmdb, jellyfin_id))
db_exec("INSERT INTO items (id, name, type, series_id, series_name, series_image_tag, date_created, synced_at) VALUES"
        " ('dil-ch', 'Díl', 'Episode', 'serchernobyl', 'Chernobyl', 'otisk-s', ?, ?)", (now, now))
check(stats.sonarr_library({2, 4}) == {2: {"detail_url": "/series/serchernobyl", "poster_id": "serchernobyl", "poster_tag": "otisk-s"}},
      f"a series from the library: the series' detail and poster ({stats.sonarr_library({2, 4})})")
check(stats.sonarr_detail_links({2, 4}) == {2: "/series/serchernobyl"}, "the details give the same addresses")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.save_permissions(accounts.get_by_name("reader")["id"], access.pack_permissions(["library"], False))
db.forget_settings()
admin, reader_without_arr = login("admin"), login("reader")
requests.clear()
answer = admin.get("/arr/sonarr/poster/4")
check(answer.status_code == 200 and answer.content == JPEG and answer.headers["content-type"] == "image/jpeg"
      and fetched_posters() == ["mediacover/4/poster-250.jpg"] and images.title_file("tv", "63639").exists(),
      f"a title outside the library: the poster from the app, stored as the title's provisional poster ({answer.status_code}, {fetched_posters()})")
check(admin.get("/arr/sonarr/poster/4").status_code == 200 and len(fetched_posters()) == 1, "the second time from the disk, the app is not asked")
check(admin.get("/arr/sonarr/poster/3").status_code == 200 and fetched_posters()[-1] == "mediacover/3/poster-250.jpg",
      "a title only the queue just read knows gets a poster too")
requests.clear()
check([admin.get(f"/arr/sonarr/poster/{title}").status_code for title in (999, 77)] == [404, 404] and fetched_posters() == []
      and admin.get("/arr/sonarr/poster/999").headers.get("cache-control") == "no-store",
      "an unknown title and a title without a TMDB id: 404 without asking the app and without keeping it in the browser")
# Game of Thrones (1): its poster is not downloaded yet and the series is not in the library either.
image["body"] = b"<html>Sign in</html>"
check(admin.get("/arr/sonarr/poster/1").status_code == 404 and not images.title_file("tv", "1399").exists()
      and fetched_posters() == ["mediacover/1/poster-250.jpg"], "the app sends a page instead of an image: not stored, 404")
image["body"] = JPEG
check(admin.get("/arr/sonarr/poster/1").status_code == 404 and len(fetched_posters()) == 1, "that there is no poster is remembered for five minutes")
sonarr.posters._MISSING.clear()
check(admin.get("/arr/sonarr/poster/1").status_code == 200, "and then it is tried again")
check(reader_without_arr.get("/arr/sonarr/poster/4").status_code == 403 and admin.get("/arr/nothing/poster/4").status_code == 404
      and admin.get("/arr/radarr/poster/4").status_code == 404,
      "without the *Arr area 403; an app Jellyscope does not know or that is not connected 404")
# The database and the disk belong in a thread: the poster route runs in the server's loop and the loop would wait for them as a whole.
where_it_runs: list[bool] = []
original_tmdb, original_load = sonarr.posters._tmdb_id, images.load_title
sonarr.posters._tmdb_id = lambda i: (where_it_runs.append(threading.current_thread() is threading.main_thread()), original_tmdb(i))[1]
images.load_title = lambda kind, tmdb: (where_it_runs.append(threading.current_thread() is threading.main_thread()), original_load(kind, tmdb))[1]
from_thread = asyncio.run(sonarr.provisional_poster(1))
sonarr.posters._tmdb_id, images.load_title = original_tmdb, original_load
check(from_thread is not None and where_it_runs and not any(where_it_runs),
      f"looking up the title and reading the poster from the disk run in a thread, not in the loop ({where_it_runs})")
db_exec("INSERT INTO items (id, name, type, series_id, series_name, date_created, synced_at) VALUES ('dil-ex', 'Díl', 'Episode', 'serexpanse', 'The Expanse', ?, ?)", (now, now))
db_exec("UPDATE sonarr_title SET jellyfin_id = 'serexpanse' WHERE title_id = 4")
check(sonarr.clean_up_posters() == 1 and not images.title_file("tv", "63639").exists() and images.title_file("tv", "70523").exists()
      and images.title_file("tv", "1399").exists(), "the series arrived in the library: the clean-up deletes its provisional poster and leaves the others")

print()
print("--- E) the card Stahuje se, its body and the slice ---")
import html as html_lib  # noqa: E402

from _apps import card_html  # noqa: E402
from jellyscope import cards, cards_sonarr  # noqa: E402

check(len(cards_sonarr.CARDS) == 24 and len(cards.ALL_CARDS) == 211,
      f"24 Sonarr cards, 211 in the registry ({len(cards_sonarr.CARDS)}, {len(cards.ALL_CARDS)})")
check(cards_sonarr.SECTIONS[0][1][:2] == ("live_numbers", "downloading"), "Stahuje se is the second card of the section Teď v aplikaci (now in the app)")
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


fresh()
page = admin.get("/arr/sonarr").text
card = whole_card(page, "arr_sonarr_downloading")
check('data-partial="/partials/arr/sonarr/downloading"' in card and 'data-refresh="5"' in card and "<h2>Stahuje se</h2>" in card,
      "the card has its heading and its body as a self-refreshing slice in a 5 s rhythm")
check(card.count("<dialog") == 2 and 'id="modal-downloading-sonarr-4"' in card and 'id="modal-downloading-sonarr-3"' in card
      and card.count('data-modal="modal-downloading-sonarr-') == 4 and card.count('<button') >= 4,
      f"a dialog with the breakdown only for series with several downloads; the poster and the link in the block open it ({card.count('<dialog')})")
import re  # noqa: E402


def plain(part: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", part).split())


# The card's blocks (without dialogs) and the dialogs apart: the overall progress of a series with several
# downloads is not drawn at all - an average over episodes says nothing. The progress is only in the
# dialog, for every download separately.
card_blocks = {plain(part)[:40]: part for part in card.split("<dialog")[0].split('<div class="session">')[1:]}
expanse_block = next(part for name, part in card_blocks.items() if "The Expanse" in name)
chernobyl_block = next(part for name, part in card_blocks.items() if "Chernobyl" in name)
expanse_dialog = card.split('id="modal-downloading-sonarr-4"')[1].split("</dialog>")[0]
check('role="progressbar"' not in expanse_block and "%" not in plain(expanse_block) and "zbývá" not in expanse_block
      and " GB" not in plain(expanse_block) and "zobrazit průběh" in expanse_block and "3 díly" in expanse_block and "stahuje se" in expanse_block,
      f"a series with several downloads: no overall progress in the block, only the state and the way to the dialog ({plain(expanse_block)[:90]})")
check(expanse_dialog.count('role="progressbar"') == 3 and expanse_dialog.count("%") >= 3 and "zbývá" in expanse_dialog,
      f"the progress of every download is in the dialog ({expanse_dialog.count('progressbar')})")
check('role="progressbar"' in chernobyl_block and 'aria-valuenow="1"' in chernobyl_block and "S01E01-E05" in chernobyl_block
      and "zbývá" in chernobyl_block and "zobrazit průběh" not in chernobyl_block,
      "a title with one download has its progress right in the block")
# The link to the dialog looks like the other muted texts of the block (and like "zobrazit jako tabulku"
# elsewhere), not like a coloured link - by colour and style it did not fit the block for the user.
check('class="session-link"' in expanse_block and "link-button" not in expanse_block and "zobrazit průběh →" in plain(html_lib.unescape(expanse_block)),
      "the link to the dialog with the progress is a muted text with an arrow, not a coloured link")
check(all(text in card for text in ("stahuje se", "pozastaveno", "čeká ve frontě klienta",
                                    "import se nepovedl", "Klient s průběhem", "usenet", "zbývá"))
      and "stuck" not in plain(card),
      "the states in Czech, the client and the protocol, the time left")
check('src="/image/serchernobyl?kind=Primary&w=300&tag=otisk-s"' in card and 'href="/series/serchernobyl"' in card
      and 'src="/arr/sonarr/poster/3"' in card and 'href="/series/serexpanse"' in card,
      "a title from the library has its poster from Jellyfin and a link, a title outside the library a provisional poster without a link")
card_text = plain(card)
check("- z " not in card_text and "0 % · 2,0 GB" in card_text,
      "a download that has not started yet writes only the size (not \"- z 2,0 GB\")")
check(not any(text in card for text in ("No files found", "eligible", "/client/hotove", "No audio tracks")),
      "the app's messages and paths are not on the card, not even for the admin")
slice_answer = admin.get("/partials/arr/sonarr/downloading")
check(slice_answer.status_code == 200 and slice_answer.headers.get("X-Jellyscope-Refresh") == "5" and "<h2>Stahuje se</h2>" not in slice_answer.text
      and 'class="sessions"' in slice_answer.text and slice_answer.text.count("<dialog") == 2 and slice_answer.headers.get("cache-control") == "no-store",
      f"the slice: only the card's body, the rhythm in a header, not kept ({slice_answer.status_code}, {slice_answer.headers.get('X-Jellyscope-Refresh')})")
check(reader_without_arr.get("/partials/arr/sonarr/downloading").status_code == 403
      and admin.get("/partials/arr/nothing/downloading").status_code == 404, "the slice: without the *Arr area 403, an app not connected 404")
full = RECORDED["sonarr"]
RECORDED["sonarr"] = {"records": [], "totalRecords": 0}
fresh()
empty = admin.get("/partials/arr/sonarr/downloading")
check("Teď se nic nestahuje." in empty.text and empty.headers.get("X-Jellyscope-Refresh") == "60" and "<dialog" not in empty.text,
      "an empty queue: one sentence and a minute's rhythm")
fresh()
check('data-refresh="60"' in card_html(admin.get("/arr/sonarr").text, "arr_sonarr_downloading"), "the card on the page then carries the minute's rhythm too")
RECORDED["sonarr"] = full
image["down"] = True
fresh()
for_viewer = viewer.get("/partials/arr/sonarr/downloading")
check("Aplikace neodpovídá." in for_viewer.text and "Connection refused" not in for_viewer.text and for_viewer.headers.get("X-Jellyscope-Refresh") == "60",
      "the app does not answer: a sentence without details, a minute's rhythm")
check("Connection refused" in admin.get("/partials/arr/sonarr/downloading").text and KEY not in admin.get("/partials/arr/sonarr/downloading").text,
      "only the admin sees the error's detail, nobody the key")
image["down"] = False
fresh()
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
cards.save_layout(["arr_sonarr_downloading"])
dashboard = html_lib.unescape(admin.get("/dashboard").text)
check("Sonarr · Stahuje se" in dashboard and dashboard.count('data-partial="/partials/arr/sonarr/downloading"') == 1
      and 'id="modal-downloading-sonarr-4"' in dashboard, "on the dashboard the card with the app's prefix and with its slice")

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
images.forget_title("tv", "70523")
sonarr.posters._MISSING.clear()
image["code"] = 404
with_poster = admin.get("/partials/arr/sonarr/downloading").text
none = admin.get("/arr/sonarr/poster/3").status_code
fresh()
without_poster = admin.get("/partials/arr/sonarr/downloading").text
sonarr.posters._MISSING.clear()
fresh()
check('src="/arr/sonarr/poster/3"' in with_poster and none == 404 and 'src="/arr/sonarr/poster/3"' not in without_poster
      and "Dark" in without_poster and 'src="/arr/sonarr/poster/3"' in admin.get("/partials/arr/sonarr/downloading").text,
      "a poster the app does not have is not offered by the card for five minutes (a placeholder right away), then it is tried again")
image["code"] = 200

# 4) A finished download without a warning waits for the import; the line with the progress does not
#    start with a separator when the size is unknown (a torrent still fetching its metadata).
RECORDED["sonarr"] = {"totalRecords": 2, "records": [
    record(downloadId="ok1", status="completed", trackedDownloadState="importPending", trackedDownloadStatus="ok", sizeleft=0),
    record(downloadId="nul", seriesId=50, series={"id": 50, "title": "Bez velikosti"}, size=0, sizeleft=0)]}
fresh()
slice_f = admin.get("/partials/arr/sonarr/downloading").text
lines_f = [" ".join(re.sub(r"<[^>]+>", " ", html_lib.unescape(line)).split())
           for line in re.findall(r'<span class="session-time">(.*?)</span>\s*</div>', slice_f, re.S)]
check("staženo, čeká na převzetí" in slice_f, "finished without a warning: downloaded, waiting for the import")
check(len(lines_f) == 2 and not any(line.startswith("·") for line in lines_f) and any(line.startswith("zbývá") for line in lines_f),
      f"the line under the bar does not start with a separator, not even when the size is unknown ({lines_f})")

# 5) A pack over several seasons has a description and a count of episodes in the block; the sentence
#    about titles over the limit has the right form.
RECORDED["sonarr"] = {"totalRecords": 62, "records": [season_episode(s, n, downloadId="full", seriesId=60, series={"id": 60, "title": "Celý seriál"})
                                                      for s, n in ((1, 1), (1, 2), (2, 1), (3, 1))]
                      + [record(downloadId=f"d{i}", seriesId=100 + i, series={"id": 100 + i, "title": f"Seriál {i:02d}"}) for i in range(60)]}
fresh()
long_text = plain(html_lib.unescape(admin.get("/partials/arr/sonarr/downloading").text))
check("A 1 další titul ve frontě." in long_text and "dalších 1" not in long_text, f"one title over the limit: \"A 1 další titul\" ({long_text[-60:]})")
RECORDED["sonarr"] = {"totalRecords": 4, "records": RECORDED["sonarr"]["records"][:4]}
fresh()
whole_series = plain(html_lib.unescape(admin.get("/partials/arr/sonarr/downloading").text))
check("Celý seriál S01-S03 · 4 díly" in whole_series, f"a pack over several seasons: a range of seasons and the count of episodes ({whole_series[:80]})")
RECORDED["sonarr"] = full
fresh()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
