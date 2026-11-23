# -*- coding: utf-8 -*-
r"""Lidarr: the live state, the live downloads (the card "Stahuje se") and the provisional covers.

Lidarr does not run here: the answers are put together by hand after Lidarr's API v1
(tests/lidarr_responses/*.json). Guarded:

A) the client: the whole queue in one page with only the fields used, the refresh command, the cover;
B) grouping: queue records into downloads, downloads into blocks by album; progress, tracks, state,
   the order of blocks;
C) liveness: the rhythm 5 s / 60 s, the command only while downloading, the cache, an unavailable
   app, the live state (queue, missing, cutoff, the calendar, health);
D) the library and the covers of albums in the queue: a cover is stored and deleted once the album
   is in the library, an album in the library uses Jellyfin's cover.

Run:
    .venv/bin/python tests/test_lidarr_progress.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lidarr-progress.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import LIDARR_RESPONSES, connect_lidarr, lidarr_client, lidarr_handler, no_network, recorded_response  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db, images, stats  # noqa: E402
from jellyscope.arr import lidarr  # noqa: E402

failures = 0
db.init_db()
no_network()      # no app other than Lidarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


RECORDED = recorded_response(LIDARR_RESPONSES, "queue_downloading")
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
    return lidarr.LidarrClient("http://lidarr.test", KEY, transport=httpx.MockTransport(handler))


async def whole() -> dict[str, Any]:
    async with client() as c:
        return await c.whole_queue()


async def refresh() -> None:
    async with client() as c:
        await c.refresh_downloads()


print("--- A) the client: the whole queue, the refresh command, the cover ---")
queue = asyncio.run(whole())
check(queue["total"] == 8 and len(queue["records"]) == len(RECORDED["records"]) == 8,
      f"Lidarr's whole queue: the records and the count ({queue['total']}, {len(queue['records'])})")
method, path, params = requests[-1][:3]
check((method, path) == ("GET", "queue") and params.get("pageSize") == "200" and params.get("page") == "1"
      and params.get("includeAlbum") == "true" and params.get("includeArtist") == "true",
      f"one page of up to 200 records with the album and the artist ({params})")
check(params.get("includeUnknownArtistItems") == "true" and not {"includeSeries", "includeEpisode", "includeUnknownSeriesItems"} & set(params),
      f"the queue with downloads not paired with an artist too, and no question about series ({sorted(params)})")
check(all(not ({"statusMessages", "errorMessage", "outputPath"} & set(r)) for r in queue["records"]),
      "the app's messages and paths are never read from the answer")
first = next(r for r in queue["records"] if r.get("status") == "downloading" and r.get("albumId") == 31)
check({"downloadId", "size", "sizeleft", "status", "trackedDownloadState", "timeleft", "albumId", "artistId", "downloadClient",
       "protocol", "title", "trackFileCount", "trackHasFileCount"} <= set(first)
      and set(first["album"]) <= {"id", "title", "artistId"} and first["album"]["title"] == "Dawn Chorus"
      and set(first["artist"]) <= {"id", "artistName"} and first["artist"]["artistName"] == "Paper Lanterns",
      f"a record carries what the card draws, and only a few fields of the embedded album and artist ({first['album']}, {first['artist']})")
asyncio.run(refresh())
check(requests[-1][:2] == ("POST", "command") and requests[-1][3] == {"name": "RefreshMonitoredDownloads"}
      and requests[-1][4] == KEY and KEY not in str(requests[-1][2]),
      f"the refresh command: POST command with the key only in the header ({requests[-1][:4]})")
timeouts: list[tuple[str, Any]] = []


def with_timeout(request: httpx.Request) -> httpx.Response:
    timeouts.append((request.method, request.extensions.get("timeout")))
    return httpx.Response(201, json={})


async def command_and_queue() -> None:
    async with lidarr.LidarrClient("http://lidarr.test", KEY, transport=httpx.MockTransport(with_timeout)) as c:
        await c.refresh_downloads()
        await c.whole_queue()


asyncio.run(command_and_queue())
check([m for m, _ in timeouts] == ["POST", "GET"] and timeouts[0][1]["read"] <= 3 and timeouts[1][1]["read"] > timeouts[0][1]["read"],
      "the refresh command has a short limit of its own, reading the queue the usual one")
JPEG = b"\xff\xd8\xff\xe0" + b"jellyscope-test" * 8


async def cover_of(album_id: int, answer: httpx.Response) -> bytes | None:
    seen.append("")

    def one(request: httpx.Request) -> httpx.Response:
        seen[-1] = request.url.path
        return answer
    async with lidarr.LidarrClient("http://lidarr.test", KEY, transport=httpx.MockTransport(one)) as c:
        return await c.cover(album_id)


seen: list[str] = []
check(asyncio.run(cover_of(31, httpx.Response(200, content=JPEG))) == JPEG and seen[-1] == "/api/v1/mediacover/album/31/cover.jpg",
      f"the album's cover is read from mediacover/album/<id>/cover.jpg ({seen[-1]})")
check(asyncio.run(cover_of(31, httpx.Response(404))) is None, "an album without a cover: None")
try:
    asyncio.run(cover_of(31, httpx.Response(500, text=f"chyba, klíč {KEY}")))
    check(False, "an error of the app is raised")
except lidarr.LidarrError as error:
    check("500" in str(error) and KEY not in str(error), f"the error body goes out without the key ({error})")

print()
print("--- B) grouping: downloads and blocks by album ---")
progress = lidarr.progress
records = queue["records"]
b = progress.blocks(records)
by_title = {x["title"]: x for x in b}
check([x["title"] for x in b] == ["Unknown.Artist-Mystery-FLAC-2020", "Nightingale Quartet - Blue Hour", "Nightingale Quartet - Silver Lining",
                                  "Paper Lanterns - Dawn Chorus", "Marlow - Lost Tapes", "Kryštof - Šípková Růžena", "Kryštof - Hvězdy"],
      f"one block per album, in the order downloading (by the time left), waiting, paused, stuck ({[x['title'] for x in b]})")
blue, silver, dawn, tapes, stuck = (by_title.get(n, {}) for n in ("Nightingale Quartet - Blue Hour", "Nightingale Quartet - Silver Lining",
                                                                   "Paper Lanterns - Dawn Chorus", "Marlow - Lost Tapes",
                                                                   "Kryštof - Hvězdy"))
check((blue.get("count"), blue.get("size_bytes"), blue.get("percent"), blue.get("seconds_left"), blue.get("title_id"))
      == (2, 3 * 1024**3, 50, 360, 22),
      f"an album in two downloads is one block: the sums, the longest time left ({blue.get('count')}, {blue.get('percent')}, {blue.get('seconds_left')})")
check(blue.get("artist") == "Nightingale Quartet" and blue.get("album") == "Blue Hour"
      and {d["state"] for d in blue["downloads"]} == {"downloading"},
      "a block carries the artist and the album apart too")
check((dawn.get("percent"), dawn.get("seconds_left"), dawn.get("state"), dawn.get("client"), dawn.get("protocol"))
      == (25, 750, "downloading", "Klient s průběhem", "usenet"),
      f"progress, time left, client and protocol of a block ({dawn.get('percent')}, {dawn.get('seconds_left')}, {dawn.get('client')})")
check((blue.get("track_has_file_count"), blue.get("track_file_count")) == (0, 8)
      and (stuck.get("track_has_file_count"), stuck.get("track_file_count")) == (2, 3) and stuck.get("state") == "stuck",
      f"the tracks: the biggest numbers among the downloads, not their sum; a finished download with a warning is stuck "
      f"({blue.get('track_has_file_count')}/{blue.get('track_file_count')}, {stuck.get('track_has_file_count')}/{stuck.get('track_file_count')})")
check(tapes.get("state") == "waiting_client" and by_title["Kryštof - Šípková Růžena"]["state"] == "paused",
      "waiting in the client's queue and paused")
pack = [x for x in b if any(d["title_id"] in (21, 22) for d in x["downloads"])]
check(len(pack) == 2, "one release carrying two albums is a download in two blocks, one per album")
unknown = by_title["Unknown.Artist-Mystery-FLAC-2020"]
check((unknown["title_id"], unknown["artist"], unknown["album"], unknown["protocol"]) == (None, "", "Unknown.Artist-Mystery-FLAC-2020", "torrent"),
      "a record Lidarr did not pair: a block with the release's name, without an id")
check(not any({"message", "statusMessages", "errorMessage", "outputPath"} & (set(x) | {k for d in x["downloads"] for k in d}) for x in b),
      "the app's messages do not get into the blocks")
TEMPLATE = next(r for r in records if r.get("albumId") == 31)


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
# Lidarr has ONE queue record per album per download: a discography of three albums is three records with the same
# downloadId (and the same size of the whole release). They are three blocks (one per album, nothing to merge), and each
# knows it is a part of a download of three albums - so the size shown is understood. The same album twice in one
# download is merged into one download (a duplicate record).
shared = progress.blocks([record(downloadId="D", albumId=a, album={"id": a, "title": f"Album {a}"}) for a in (71, 72, 73)]
                         + [record(downloadId="E", albumId=74, album={"id": 74, "title": "Album 74"}),
                            record(downloadId="E", albumId=74, album={"id": 74, "title": "Album 74"})])
by_id = {b["title_id"]: b for b in shared}
check(len(shared) == 4 and [by_id[a]["download_albums"] for a in (71, 72, 73, 74)] == [3, 3, 3, 1],
      f"a download of three albums: three blocks, each says 3; a duplicate record of one album is merged, says 1 ({[b['download_albums'] for b in shared]})")
check(by_id[74]["count"] == 1, "the duplicate record of one album in one download is one download, not two")
check(progress.blocks([record(downloadId="")])[0]["download_albums"] == 1 and progress.blocks([record(downloadId="")] * 2)[0]["download_albums"] == 1,
      "records without a downloadId share nothing")
old = progress.blocks([{k: v for k, v in TEMPLATE.items() if k not in ("trackFileCount", "trackHasFileCount")}])[0]
check((old["track_has_file_count"], old["track_file_count"]) == (None, None), "a record without the track counts (an older Lidarr): unknown, not zero")
check([progress.seconds(x) for x in ("00:12:34", "1.02:03:04", "", None, "x", 5)] == [754, 93784, None, None, None, None],
      "the time left in every shape")
check(len(progress.blocks([record(downloadId="M", albumId=1), record(downloadId="M", albumId=2)])) == 2, "one download for two albums: two blocks")

print()
print("--- C) liveness: rhythm, the command only while downloading, the cache; the live state ---")
from jellyscope import cards_base  # noqa: E402

clock = {"now": 1000.0}
progress._now = lambda: clock["now"]
live_queues: dict[str, Any] = {"queue": RECORDED, "command_fails": False, "down": False}


def live_lidarr(request: httpx.Request) -> httpx.Response:
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
    return asyncio.run(lidarr.download_state())


restore_client = substitute(lidarr, "LidarrClient", lidarr_client(live_lidarr))
connect_lidarr()
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

threads = [threading.Thread(target=lambda: cards_base.live(lidarr.download_state)) for _ in range(3)]
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
db.set_setting("arr_lidarr_url", "http://jinde.test")
db.forget_settings()
read()
check(requests[-1][5] == "jinde.test", f"a change of the address in Settings: it reads from the new one at once ({requests[-1][5]})")
db.set_setting("arr_lidarr_url", "http://lidarr.test")
db.forget_settings()
many = [record(downloadId=str(i), albumId=100 + i, album={"id": 100 + i, "title": f"Album {i:02d}"}) for i in range(70)]
live_queues["queue"] = {"records": many, "totalRecords": 250}
move(10)
s = read()
check(len(s["blocks"]) == 60 and s["more_count"] == 10 and s["incomplete"], f"at most 60 blocks, the rest as a count ({len(s['blocks'])}, {s['more_count']})")
live_queues["queue"] = {"records": [record(albumId=4, album="not-a-dict", artist=["x"], size="big")], "totalRecords": 1}
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
check(progress.in_queue(31) and progress.in_queue(22) and not progress.in_queue(999), "the albums of the queue read last are known")
restore_client()

# The live state: the queue with a problem, missing, cutoff, the calendar, health.
live_requests: list[str] = []
live_instead: dict[str, httpx.Response] = {}
restore_client = substitute(lidarr, "LidarrClient", lidarr_client(lidarr_handler(live_requests, live_instead)))
lidarr.live.CACHE.clear()
state = asyncio.run(lidarr.live_state())
# Missing and cutoff come from the sync since 23. 11. 2026 (none ran here): not part of the live state.
check(state["available"] and state["error"] == "" and state["queue"]["count"] == 3 and state["missing"] is None and state["cutoff"] is None,
      f"the live state: the queue; missing and cutoff wait for the sync ({state['queue']['count']}, {state['missing']}, {state['cutoff']})")
errors = state["queue"]["with_error"]
check([(e["title"], e["title_id"], e["state"]) for e in errors] == [("Kryštof - Hvězdy", 11, "importPending"), ("Kryštof - Šípková Růžena", 12, "failedPending")]
      and errors[0]["message"] == "Has missing tracks" and errors[1]["message"] == "Download failed",
      f"the queue items with an error or a warning, by album, with the reason ({[(e['title'], e['message']) for e in errors]})")
calendar = state["calendar"]
check([(c["title"], c["title_id"]) for c in calendar] == [("Nightingale Quartet - Blue Hour", 22), ("Paper Lanterns - Next Week", 51)]
      and calendar[0]["has_file"] is True and calendar[1]["has_file"] is False and calendar[0]["at"] < calendar[1]["at"],
      f"the calendar: albums by the release date, whether the album is on the disk ({[(c['title'], c['at']) for c in calendar]})")
check(state["health"] == [{"type": "warning", "message": "Indexers unavailable due to failures: Test"}], "health checks")
reads = len(live_requests)
asyncio.run(lidarr.live_state())
check(len(live_requests) == reads, "the second read within a minute comes from the cache")
lidarr.live.CACHE.clear()
live_instead["queue"] = httpx.Response(500, text=f"chyba {KEY}")
state = asyncio.run(lidarr.live_state())
check(state["available"] and state["queue"] is None and state["calendar"] is not None and KEY not in state["error"] and "500" in state["error"],
      f"what fails is unknown (None), the rest loads, the error is without the key ({state['error']})")
lidarr.live.CACHE.clear()
live_instead.update({p: httpx.Response(500) for p in ("queue", "wanted/missing", "wanted/cutoff", "calendar", "health")})
state = asyncio.run(lidarr.live_state())
check(not state["available"] and state["queue"] is None and state["error"] != "", "everything fails: Lidarr is unavailable, the page stays up")
lidarr.live.CACHE.clear()
restore_client()

print()
print("--- D) the library and the covers of albums in the queue ---")
posters = lidarr.posters
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


substitute(lidarr, "LidarrClient", lidarr_client(with_covers))
move(10)
progress._memory = None
now = db.utcnow()
# Dawn Chorus (31) is not in the library, Hvezdy (11) is, Silver Lining (21) is too but has no cover in Jellyfin.
for album_id, name, jellyfin_id in ((31, "Dawn Chorus", ""), (11, "Hvězdy", "alb-hvezdy"), (21, "Silver Lining", "alb-silver")):
    db_exec("INSERT INTO lidarr_title (title_id, name, jellyfin_id) VALUES (?, ?, ?)", (album_id, name, jellyfin_id))
for jellyfin_id, tag in (("alb-hvezdy", "otisk-obalu"), ("alb-silver", "")):
    db_exec("INSERT INTO music_album (id, name, cover_tag, missing, synced_at) VALUES (?, ?, ?, 0, ?)", (jellyfin_id, jellyfin_id, tag, now))
read()
# The cards pair an album with the library through stats.lidarr_library (one query for all of them).
check(stats.lidarr_library([11, 21, 31, 999]) == {11: {"album_id": "alb-hvezdy", "cover_tag": "otisk-obalu", "artist_id": ""},
                                                   21: {"album_id": "alb-silver", "cover_tag": "", "artist_id": ""}},
      f"an album in the library has its Jellyfin id and cover tag, one outside it or unknown none ({stats.lidarr_library([11, 21, 31, 999])})")
db_exec("UPDATE music_album SET missing = 1 WHERE id = 'alb-hvezdy'")
check(11 not in stats.lidarr_library([11]), "an album gone from the music library is not linked")
db_exec("UPDATE music_album SET missing = 0 WHERE id = 'alb-hvezdy'")

stored = asyncio.run(posters.provisional_poster(31))
check(stored == (JPEG, "image/jpeg") and cover_requests == ["mediacover/album/31/cover.jpg"] and images.title_file("album", "31").exists(),
      f"an album outside the library: the cover from Lidarr, stored as the album's provisional cover ({cover_requests})")
check(asyncio.run(posters.provisional_poster(31)) == (JPEG, "image/jpeg") and len(cover_requests) == 1, "the second time from the disk, the app is not asked")
check(images.titles_on_disk() == [("album", "31")], f"the store lists the cover under the kind album ({images.titles_on_disk()})")
check(asyncio.run(posters.provisional_poster(41)) == (JPEG, "image/jpeg") and cover_requests[-1] == "mediacover/album/41/cover.jpg",
      "an album only the queue just read knows gets a cover too")
cover_requests.clear()
check([asyncio.run(posters.provisional_poster(a)) for a in (11, 21, 999)] == [None, None, None] and cover_requests == [],
      "an album in the library and an unknown album: nothing, and Lidarr is not asked")
image["body"] = b"<html>Sign in</html>"
check(asyncio.run(posters.provisional_poster(12)) is None and not images.title_file("album", "12").exists()
      and cover_requests == ["mediacover/album/12/cover.jpg"] and posters.poster_missing(12),
      "the app sends a page instead of an image: not stored, remembered as without a cover")
image["body"] = JPEG
check(asyncio.run(posters.provisional_poster(12)) is None and len(cover_requests) == 1, "that there is no cover is remembered for five minutes")
posters._MISSING[12] = time.monotonic() - posters.MISSING_SECONDS - 1
check(not posters.poster_missing(12) and asyncio.run(posters.provisional_poster(12)) == (JPEG, "image/jpeg"), "and then it is tried again")
check(not posters.poster_missing(None) and not posters.poster_missing(31), "an album that has a cover is not marked missing")

# Once the album arrives in the library, the next sync deletes its provisional cover.
check(posters.clean_up_posters() == 0 and {a for _, a in images.titles_on_disk()} == {"31", "41", "12"}, "albums outside the library keep their covers")
db_exec("UPDATE lidarr_title SET jellyfin_id = 'alb-dawn' WHERE title_id = 31")
check(posters.clean_up_posters() == 1 and images.titles_on_disk() == [("album", "12"), ("album", "41")] and images.load_title("album", "31") is None,
      f"the album came into the library: its provisional cover is deleted, the others stay ({images.titles_on_disk()})")
check(asyncio.run(posters.provisional_poster(31)) is None, "and the album no longer asks Lidarr for a cover")
images.save_title("movie", "31", JPEG)
db_exec("UPDATE lidarr_title SET jellyfin_id = 'alb-dawn' WHERE title_id = 41 OR title_id = 12")
check(posters.clean_up_posters() == 0 and ("movie", "31") in images.titles_on_disk(), "a movie's cover with the same number is never touched")
images.forget_titles()
check(images.titles_on_disk() == [], "the provisional covers can all be forgotten")

# Deleting the downloaded data forgets that app's provisional covers (and only its kind): once the app is
# disconnected nothing else would ever delete them.
images.save_title("album", "31", JPEG)
images.save_title("album", "41", JPEG)
images.save_title("movie", "31", JPEG)
lidarr.delete_data()
check(images.titles_on_disk() == [("movie", "31")],
      f"deleting the downloaded data deletes the album covers and leaves a movie's with the same number ({images.titles_on_disk()})")
images.forget_titles()

lidarr.save_settings(False, "http://lidarr.test", KEY)
db.forget_settings()
cover_requests.clear()
check(asyncio.run(posters.provisional_poster(41)) is None and cover_requests == [], "Lidarr switched off: no request outside")
try:
    images.title_file("album", "²")
    check(False, "a non-ASCII digit is refused")
except ValueError as error:
    check("TMDB" not in str(error), f"the id is checked as digits and the message no longer claims TMDB only ({error})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
