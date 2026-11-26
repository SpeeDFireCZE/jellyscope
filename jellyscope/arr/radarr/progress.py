"""The live downloads: what Radarr downloads right now and how far it is.

The card "Stahuje se" (downloading) shows Radarr's queue by movies and refreshes itself. The queue is
read and put together here:

* **A queue record is not always one download.** Records with the same `downloadId` of one movie
  are merged into downloads, and those are grouped by movie into **blocks** (`blocks`): a movie
  downloaded twice (two releases) is one block with two downloads.
* **Liveness.** Radarr refreshes the progress from the download client only once a minute. While
  something downloads and somebody watches, Jellyscope sends it a refresh command before every real
  read of the queue (`RadarrClient.refresh_downloads`). Thanks to the cache the reads are at least TTL
  seconds apart, however many people watch - so the command never goes more often; with one viewer
  it goes with every refresh of the card (every FAST_REFRESH seconds). Without downloads nothing is
  sent and the card asks only once a minute (`refresh`).
* **A cache** of a few seconds (`_memory`): several open cards and windows share one request. Its key
  carries Radarr's address and the key's fingerprint, so a change in Settings shows at once.

The app's messages (paths on the disk, the clients' addresses) are never read from the queue (see
client.QUEUE_FIELDS) - the card shows them to nobody.

Called from two worlds: from the card's slice (the app's loop) and from drawing the page (a thread
with its own loop, cards_base.live). Concurrent reads in one loop share one task; threads queue up
in live() and the next one reads from the cache. The command is guarded by a short lock with no
waiting inside.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from typing import Any

import httpx

from ... import db
from ...jellyfin import digits_only
from ...services import LIVE_TIMEOUT, integer
from .client import SETTINGS_PREFIX, RadarrClient, RadarrError

log = logging.getLogger("jellyscope.arr")

FAST_REFRESH = 5             # seconds between refreshes of the card while something downloads
CALM_REFRESH = 60            # seconds between refreshes when nothing downloads or the app does not answer
TTL = 4.0                    # seconds in the cache (and the shortest time between commands); an error is kept CALM_REFRESH seconds
MAX_BLOCKS = 60              # how many blocks the card gets at most; of the rest it gives the count

# What the module remembers between reads of the queue. Only functions here change it (through
# `global`), nothing outside reads it.
Remembered = tuple[float, str, dict[str, Any], RadarrError | None]      # (when loaded, the address and the key's fingerprint, state, error)
_memory: Remembered | None = None                        # the cache of the last read
_last: tuple[float, bool] = (float("-inf"), False)       # (when it was read last, whether something downloaded)
_last_command: float = float("-inf")                     # when the last refresh command went
_titles: dict[int, str] = {}                             # {movie id: TMDB id} from the last queue
_RUNNING: dict[str, tuple[Any, asyncio.Task[Remembered]]] = {}    # reads running right now by settings: (loop, task)
_LOCK = threading.Lock()
_now = time.monotonic                                    # the clock (tests substitute it)

# The order of blocks in the card: what downloads, then waiting and paused, then finished and stuck.
BLOCK_ORDER = ("downloading", "waiting_client", "paused", "postponed", "client_not_responding",
               "importing", "waiting_import", "stuck", "failed", "warnings")
# The state of a block with several downloads: the first of these that at least one of them has.
STATE_PRIORITY = ("downloading", "stuck", "failed", "warnings", "client_not_responding",
                  "importing", "waiting_import", "waiting_client", "paused", "postponed")
_BY_STATUS = {"downloading": "downloading", "queued": "waiting_client", "paused": "paused", "delay": "postponed",
              "downloadclientunavailable": "client_not_responding", "warning": "warnings"}
_TIME = re.compile(r"(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?")


def seconds(text: Any) -> int | None:
    """The time left from the queue (`00:12:34`, with days `1.02:03:04`) in seconds. What is no time is None."""
    match = _TIME.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    days, hours, minutes, secs = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + secs


def _state(record: dict[str, Any]) -> str:
    """The state of a queue record in one word (the keys see BLOCK_ORDER). An unknown one as the app sent it."""
    status = str(record.get("status") or "")
    tracked = str(record.get("trackedDownloadState") or "").lower()
    if tracked in ("importblocked", "failedpending"):
        return "stuck"
    if tracked == "importing":
        return "importing"
    if "failed" in (status.lower(), tracked):
        return "failed"
    if status.lower() == "completed":
        # A finished download the app reports a warning or an error for will not be imported by itself
        # (nothing to import in the folder, it cannot be paired). `importBlocked` exists only in newer
        # versions of the apps; older ones report it only this way. The card of the queue with an error
        # or a warning ("Queue items with an error or warning") shows the same record.
        return "stuck" if str(record.get("trackedDownloadStatus") or "").lower() in ("warning", "error") else "waiting_import"
    return _BY_STATUS.get(status.lower()) or status or "?"


def _title(record: dict[str, Any]) -> tuple[int | None, str, str]:
    """(the movie's id in Radarr, the name, the TMDB id). A record Radarr did not pair has only the release's name."""
    own = record.get("movie")
    own = own if isinstance(own, dict) else {}
    movie_id = integer(record.get("movieId"), None)
    tmdb = str(own.get("tmdbId") or "")
    return movie_id, str(own.get("title") or record.get("title") or "?"), tmdb if digits_only(tmdb) and tmdb != "0" else ""


def _downloads(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Queue records merged into downloads: one `downloadId` of one movie = one download."""
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for order, record in enumerate(r for r in records if isinstance(r, dict)):
        movie_id, title, tmdb = _title(record)
        download_id = str(record.get("downloadId") or "")
        # Without `downloadId` every record is a download of its own; an unpaired record a title of its own.
        key = (movie_id if movie_id is not None else ("release", order), download_id or ("record", order))
        download = by_key.get(key)
        if download is None:
            size = max(0, integer(record.get("size"), 0) or 0)
            left = integer(record.get("sizeleft"), 0) or 0
            state = _state(record)
            download = by_key[key] = {
                "title_id": movie_id, "title": title, "tmdb_id": tmdb, "_title_key": key[0],
                "state": state, "size_bytes": size, "grabbed": min(max(size - left, 0), size),
                "seconds_left": seconds(record.get("timeleft")) if state == "downloading" else None,
                "client": str(record.get("downloadClient") or ""), "protocol": str(record.get("protocol") or ""),
            }
    for download in by_key.values():
        download["percent"] = int(download["grabbed"] * 100 / download["size_bytes"]) if download["size_bytes"] else None
    return list(by_key.values())


def _sort_key(block: dict[str, Any]) -> tuple[int, float, str]:
    """The order of blocks in the card and of downloads in the dialog: state, time left and name."""
    order = BLOCK_ORDER.index(block["state"]) if block["state"] in BLOCK_ORDER else len(BLOCK_ORDER)
    return (order, block["seconds_left"] if block["seconds_left"] is not None else float("inf"), block["title"].lower())


def blocks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The queue by movies, sorted for the card: one block per movie with the overall progress and a breakdown.

    A block's progress = the downloaded bytes of all its downloads / their size. The time left is the
    longest of those downloading (when everything will be done). The state is the first of
    STATE_PRIORITY that at least one download has.
    """
    by_title: dict[Any, list[dict[str, Any]]] = {}
    for download in _downloads(records):
        by_title.setdefault(download.pop("_title_key"), []).append(download)
    result = []
    for part in by_title.values():
        first, states = part[0], [d["state"] for d in part]
        size, downloaded = sum(d["size_bytes"] for d in part), sum(d["grabbed"] for d in part)
        times = [d["seconds_left"] for d in part if d["seconds_left"] is not None]
        clients, protocols = {d["client"] for d in part}, {d["protocol"] for d in part}
        result.append({
            "title_id": first["title_id"], "title": first["title"], "tmdb_id": first["tmdb_id"],
            "count": len(part),
            "state": next((state for state in STATE_PRIORITY if state in states), states[0]),
            "size_bytes": size, "grabbed": downloaded, "percent": int(downloaded * 100 / size) if size else None,
            "seconds_left": max(times) if times else None,
            "client": clients.pop() if len(clients) == 1 else "", "protocol": protocols.pop() if len(protocols) == 1 else "",
            "downloads": sorted(part, key=_sort_key),
        })
    return sorted(result, key=_sort_key)


async def _load(url: str, key: str, fingerprint: str, transport: httpx.AsyncBaseTransport | None) -> Remembered:
    """Read the queue (after sending the refresh command, if due), store the result in the cache and return it."""
    global _memory, _last, _last_command, _titles
    now = _now()
    with _LOCK:
        when, downloading = _last
        previous_error = _memory is not None and _memory[3] is not None
        # Only when something downloaded at the last read (and that was not long ago) - the first read
        # and reads after a calm go without the command. Otherwise with every read: thanks to the cache
        # they are at least TTL seconds apart, and the limit here only guards two reads at once (the
        # page's thread and the slice's loop). A FAST_REFRESH limit would not do: the browser asks every
        # FAST_REFRESH seconds measured on its side, the request arrives a few milliseconds earlier or
        # later - and every second command would drop out.
        downloading_lately = downloading and now - when <= CALM_REFRESH
        send = downloading_lately and now - _last_command >= TTL
        if send:
            _last_command = now
    state: dict[str, Any] = {"available": False, "blocks": [], "more_count": 0, "incomplete": False, "live": False}
    error: RadarrError | None = None
    try:
        client = RadarrClient(url, key, transport=transport, timeout=LIVE_TIMEOUT)
    except RadarrError as exception:               # an invalid stored address or key
        error = exception
    else:
        async with client as c:
            # The command beside the read, not before it: the app refreshes in the background anyway (this read
            # does not wait for it), and in a row a hung app would cost the card two time limits.
            answers = await asyncio.gather(c.whole_queue(), *([c.refresh_downloads()] if send else []),
                                           return_exceptions=True)
            if len(answers) > 1 and isinstance(answers[1], BaseException):
                try:
                    raise answers[1]       # an error of the program goes on; the app's error is only logged
                except RadarrError as exception:   # the command is only a nudge - it does not stop reading the queue
                    log.debug("the command to refresh downloads in %s failed: %s", "radarr", exception)
            try:
                if isinstance(answers[0], BaseException):
                    raise answers[0]
                queue = answers[0]
                every = blocks(queue["records"])
            except RadarrError as exception:
                error = exception
            except (ValueError, TypeError, AttributeError, ArithmeticError) as exception:
                # A queue of an unexpected shape is the app's error like any other (the live state in
                # live.py takes it the same way): the card says the app does not answer, the page does
                # not go down.
                error = RadarrError("{reason}", reason=str(exception))
            else:
                state = {"available": True, "blocks": every[:MAX_BLOCKS], "more_count": max(0, len(every) - MAX_BLOCKS),
                         "incomplete": queue["total"] > len(queue["records"]),
                         "live": any(block["state"] == "downloading" for block in every)}
                _titles = {block["title_id"]: block["tmdb_id"] for block in every
                           if block["title_id"] is not None and block["tmdb_id"]}
    with _LOCK:
        # The first error after a read that saw a download keeps the fast rhythm: one answer slower than the
        # short limit must not stop the live card for a minute. A second error in a row is calm again.
        _last = (_now(), state["live"] or (error is not None and downloading_lately and not previous_error))
    _memory = (_now(), fingerprint, state, error)
    return _memory


async def download_state(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """What Radarr downloads now: `{"available", "error", "blocks", "more_count", "incomplete", "live", "refresh"}`.

    `live` = at least one download runs; `refresh` = in how many seconds the card is to ask again
    (FAST_REFRESH while downloading, else CALM_REFRESH). An unavailable Radarr does not bring the page
    down: `dostupna: False` and the error (without the key, translated on every read - the cache is
    shared by every language).
    """
    url, key = db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")
    fingerprint = url + "|" + hashlib.sha256(key.encode()).hexdigest()
    cached = _memory
    # A success holds TTL seconds. An error CALM_REFRESH: the card asks an unavailable app only once a
    # minute anyway, and without it a stuck app would cost a wait on every page load - except the first error
    # during a download, which holds FAST_REFRESH (a single slow answer, see _load). A change of the
    # address or the key in Settings bypasses the cache at once (`fingerprint`).
    hold = TTL if cached is None or cached[3] is None else (FAST_REFRESH if _last[1] else CALM_REFRESH)
    if not (cached and cached[1] == fingerprint and _now() - cached[0] < hold):
        loop = asyncio.get_running_loop()
        running = _RUNNING.get(fingerprint)
        if running is None or running[0] is not loop or running[1].done():
            task = loop.create_task(_load(url, key, fingerprint, transport))
            _RUNNING[fingerprint] = running = (loop, task)
            task.add_done_callback(
                lambda done: _RUNNING.pop(fingerprint, None) if _RUNNING.get(fingerprint, (None, None))[1] is done else None)
        cached = await asyncio.shield(running[1])
    _, _, state, error = cached
    fast = state["live"] or (error is not None and _last[1])      # downloading, or the first error during it
    return {**state, "error": error.translated() if error else "", "refresh": FAST_REFRESH if fast else CALM_REFRESH}


def fresh_tmdb(movie_id: int) -> str:
    """The movie's TMDB id from the queue read last, else "".

    For the poster of a movie that is still downloading and not in the list of movies yet (the list is
    fetched once a day or with the history).
    """
    return _titles.get(movie_id, "")
