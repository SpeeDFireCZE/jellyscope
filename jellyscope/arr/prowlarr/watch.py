"""Measuring the indexers' status in the background: a loop that runs as long as the app does.

The *Arr sync task samples once an hour and a page at most every ten minutes, and only when somebody looks: an outage
shorter than that is missed and the end of a longer one is known only within the hour. So the app runs this loop beside
the playback collector (web/start.py, not in the demo mode): every N seconds (Settings → *Arr, Prowlarr's form; 60 by
default) it asks Prowlarr only what a sample needs - the indexers and who it leaves out (live.status_sample, two
requests with the live time limit) - and keeps the answer (samples.py):

* a change of the state (an indexer starts or stops being left out, an indexer added, removed, switched on or off) is
  stored at the round that sees it, as a new sample, and moves the marker of the cards that refresh by themselves;
* while nothing changes, a round only moves the last sample's last round (`measured_until`) - the table grows by one
  sample every ten minutes (`samples.EVERY`), which also keeps the times of the indexers left out fresh;
* a round that measures nothing (Prowlarr does not answer, it is not connected, the switch is off, its write met
  another writer's lock) breaks the chain: the next answer starts a new sample, and the time between is not measured
  (stats.prowlarr_availability). So does a round that comes late - the process was frozen (a host suspend, docker
  pause) longer than `ROUND_GAP_TIMES` intervals: the time nobody measured is not stretched over.

While the loop measures, neither the *Arr sync task nor a page samples (samples.watching); switched off, both work as
before. It never dies: an unexpected error is logged and the loop waits a little longer, as the collector does - but
never shorter than the interval; it ends only when it is cancelled (the app shuts down). It waits in pieces of at most
`SLICE` seconds and reads the interval again after each: a changed one takes effect within a piece, and a piece asks
Prowlarr nothing. With Prowlarr not connected it idles quietly - no request, no log line.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from ... import db
from .live import status_sample
from .samples import EVERY, FAILED, LOOP, MOVED, extend_watched, store_watched
from .settings import WATCH_DEFAULT, is_connected, watch_settings

log = logging.getLogger("jellyscope.arr")

SECOND = 1.0         # how long a second of the loop's pauses is (tests shorten it)
MAX_BACKOFF = 60     # seconds: the longest pause after an unexpected error (2, 4, 8 ... 60, as the collector)
SLICE = 15           # seconds: the longest piece of a pause - a changed interval takes effect within one
# A round later than this many intervals after the last one does not move the sample over the time between: the
# statistics do not measure a longer step between two of the loop's samples either (stats/prowlarr.LOOP_GAP_TIMES -
# the statistics do not import the app's package; tests/test_prowlarr_watch.py compares them).
ROUND_GAP_TIMES = 1.5


@dataclass
class Chain:
    """What the loop knows of its last stored sample: when it was taken (the sample a round that sees the same moves;
    "" = none - the next round stores a new one), the state it saw (the indexers left out, the indexers enabled), the
    interval it was stored with and the time of the last round that stored or moved it. The state stays when the
    chain breaks: the next sample is a change only when it differs."""
    sampled_at: str = ""
    state: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    interval: int = 0
    last_round: str = ""


def _since(earlier: str, now: str) -> timedelta:
    return datetime.strptime(now, db.TIME_FORMAT) - datetime.strptime(earlier, db.TIME_FORMAT)


def _continues(chain: Chain, now: str, interval: int) -> bool:
    """Whether the round at `now` may move the chain's sample: it is younger than ten minutes (`EVERY` - then a new
    one) and the last round was at most `ROUND_GAP_TIMES` intervals ago (the process was not frozen in between)."""
    return (bool(chain.sampled_at and chain.last_round) and _since(chain.sampled_at, now) < EVERY
            and _since(chain.last_round, now) <= timedelta(seconds=ROUND_GAP_TIMES * interval))


async def watch_once(chain: Chain, transport: httpx.AsyncBaseTransport | None = None, *, now: str | None = None) -> int:
    """One round of the loop. Returns how many seconds to wait for the next one (the interval from Settings).

    `now` (UTC) is the round's time for tests; otherwise the time of Prowlarr's answer.
    """
    settings = watch_settings()
    interval = settings["interval"]
    if not settings["enabled"] or not is_connected():
        chain.sampled_at, LOOP["answered"] = "", None
        return interval
    sample = await status_sample(transport, now=now)
    LOOP["answered"] = sample is not None
    if sample is None:                   # Prowlarr did not answer: not measured until it does
        chain.sampled_at = ""
        return interval
    when, rows, enabled = sample
    state = (tuple(row[0] for row in rows), tuple(enabled))
    if (state, interval) == (chain.state, chain.interval) and _continues(chain, when, interval):
        extended = await asyncio.to_thread(extend_watched, chain.sampled_at, when)
        if extended == MOVED:
            chain.last_round = when
            return interval
        if extended == FAILED:           # another writer's lock, a broken table: this round is not measured
            chain.sampled_at = ""
            return interval
    stored = await asyncio.to_thread(store_watched, when, rows, enabled, interval, state != chain.state)
    chain.sampled_at = chain.last_round = when if stored else ""
    if stored:
        chain.state, chain.interval = state, interval
    return interval


def _interval(known: int) -> int:
    """The interval from Settings now; `known` when they cannot be read (the next round logs what is wrong)."""
    try:
        return int(watch_settings()["interval"])
    except Exception:   # noqa: BLE001 - the loop must not die of a pause
        return known


async def _pause(interval: int, at_least: int) -> None:
    """Wait for the next round: the interval from Settings (`interval` the round's), but never less than `at_least`
    seconds (the backoff after an unexpected error). In pieces of at most `SLICE` seconds, the interval read again
    after each: a changed one takes effect within a piece. A piece reads only the settings - no request to Prowlarr."""
    waited = 0
    while (left := max(interval, at_least) - waited) > 0:
        piece = min(SLICE, left)
        await asyncio.sleep(piece * SECOND)
        waited += piece
        interval = _interval(interval)


async def watch_indexers_forever(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """The loop in the background for the whole time the app runs (web/start.py). It must never fall: an error is
    logged, the loop waits gradually longer and tries again. It ends only when cancelled."""
    chain, backoff, interval = Chain(), 0, WATCH_DEFAULT
    LOOP["running"] += 1
    try:
        while True:
            try:
                interval = await watch_once(chain, transport)
                backoff = 0
            except Exception as error:   # noqa: BLE001 - the loop must not die of anything
                log.warning("prowlarr: measuring the indexers' status failed: %s", error)
                chain.sampled_at = ""
                backoff = min(MAX_BACKOFF, (backoff * 2) or 2)
                interval = _interval(interval)
            await _pause(interval, backoff)
    finally:
        LOOP["running"] -= 1
