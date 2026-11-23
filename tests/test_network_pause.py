# -*- coding: utf-8 -*-
r"""A paused playback pulls no data - and the network chart must know it.

A pause transfers no data: the server sends nothing and the player asks for
nothing. The concurrent flow chart however did not take it into account - it
took the bitrate and spread it over the whole time from the start to the end of
the session. A movie paused overnight thus held the full flow until the
morning, and a playback paused **right now** too, because `last_seen_at` runs
on even during a pause.

It showed also in two numbers on one page contradicting each other: the area
under the curve came out many times bigger than the volume of data next to it,
which has always been computed from `watched_seconds`.

When exactly it was paused the database does not know - it keeps only sums. So
the flow lasts as long as there really was watching; with a pause in the
middle it is moved forward, but the height and the amount match.

Run:
    .venv/bin/python tests/test_network_pause.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sit.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import collector, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def playback(key: str, minutes_ago: int, watched_s: int,
             paused_s: int = 0, mbit: int = 20, active: int = 1) -> None:
    start = NOW - timedelta(minutes=minutes_ago)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " bitrate, is_paused, is_active, play_method, remote_address, client,"
            " device_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, "u1", "Pepa", "film", "Duna",
             start.strftime(db.TIME_FORMAT), NOW.strftime(db.TIME_FORMAT),
             watched_s, paused_s, mbit * 1_000_000,
             1 if paused_s else 0, active, "DirectPlay", "10.0.0.5", "Web", "Chrome"))


def minutes_in_chart(points: int = 600) -> float:
    curve = stats.bandwidth_timeline(7, points=points)
    if not curve:
        return 0.0
    return (curve[-1]["time"] - curve[0]["time"]) / 60


print("--- a movie paused for almost two hours ---")
# Ten minutes were watched, the rest is a standing pause. And it stands RIGHT NOW.
playback("s1", minutes_ago=120, watched_s=600, paused_s=6600)
minutes = minutes_in_chart()
check(9 <= minutes <= 11, f"the chart covers about ten minutes, not two hours ({minutes:.1f})")

summary = stats.bandwidth_summary(7)
check(summary["peak_mbit"] == 20.0, f"the peak stays 20 Mbit/s ({summary['peak_mbit']})")
check(abs(summary["byte_count"] - 20e6 * 600 / 8) < 1e6,
      f"the volume matches ten minutes ({summary['byte_count'] / 1e9:.2f} GB)")
# This is the sentence the whole thing came about for: the area under the
# curve and the volume of data next to it must say the same.
area_gb = summary["peak_mbit"] * 1e6 * (minutes * 60) / 8 / 1e9
check(abs(area_gb - summary["byte_count"] / 1e9) < 0.2,
      f"the area under the curve matches the volume ({area_gb:.2f} GB vs "
      f"{summary['byte_count'] / 1e9:.2f} GB)")

print()
print("--- whoever watches without a pause loses nothing ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
playback("s2", minutes_ago=60, watched_s=3600)
minutes = minutes_in_chart()
check(59 <= minutes <= 61, f"the chart covers the whole hour ({minutes:.1f})")

print()
print("--- concurrency still adds up ---")
# Two people at once = the sum of the flows. Were the fix to break going
# through the events, exactly this would fail.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
playback("s3", minutes_ago=30, watched_s=1800, mbit=20)
playback("s4", minutes_ago=30, watched_s=1800, mbit=12)
check(stats.bandwidth_summary(7)["peak_mbit"] == 32.0,
      f"the peak is the sum of both ({stats.bandwidth_summary(7)['peak_mbit']})")

print()
print("--- and the language statistics: a pause does not count in the watched time ---")
# The answer to the worry whether a pause skews the languages and subtitles too.
# It does not: the collector adds to `watched_seconds` only when it really plays.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")

TICK = 10_000_000


class FakeClient:
    def __init__(self) -> None:
        self.sessions_now: list[dict] = []

    async def sessions(self) -> list[dict]:
        return self.sessions_now


def session(position_s: int, paused: bool) -> dict:
    return {
        "Id": "s5", "UserId": "u1", "UserName": "Pepa",
        "Client": "Jellyfin Web", "DeviceName": "Chrome",
        "PlayState": {"PositionTicks": position_s * TICK, "IsPaused": paused,
                      "PlayMethod": "DirectPlay"},
        "NowPlayingItem": {"Id": "film-1", "Name": "Duna", "Type": "Movie",
                           "RunTimeTicks": 7200 * TICK},
    }


client = FakeClient()


def snapshot() -> None:
    asyncio.run(collector.poll_once(client, max_gap_seconds=30))


def move_clock(by_seconds: int) -> None:
    """Pretends time passed between snapshots.

    The collector computes the increment from the difference against
    `last_seen_at`. Two snapshots right after each other have a difference of
    zero, so the test would measure nothing.
    """
    with db.connect() as conn:
        conn.execute("UPDATE playback SET last_seen_at = ?",
                     ((datetime.now(timezone.utc).replace(tzinfo=None)
                       - timedelta(seconds=by_seconds)).strftime(db.TIME_FORMAT),))


def state() -> tuple[int, int]:
    r = db.query_one("SELECT watched_seconds, paused_seconds FROM playback")
    return int(r["watched_seconds"] or 0), int(r["paused_seconds"] or 0)


client.sessions_now = [session(60, paused=False)]
snapshot()

move_clock(20)
client.sessions_now = [session(70, paused=True)]
snapshot()
watched, paused = state()
check(paused >= 20, f"the pause counts apart ({paused} s)")
check(watched == 0, f"and is not added to the watched time ({watched} s)")

move_clock(20)
snapshot()
watched2, paused2 = state()
check(watched2 == watched, f"not at the second snapshot either ({watched2} s)")
check(paused2 > paused, f"the pause grows meanwhile ({paused2} s)")

# And because a standing number can mean broken counting too, we start once
# more: the watched time must move.
move_clock(20)
client.sessions_now = [session(90, paused=False)]
snapshot()
watched3, _ = state()
check(watched3 >= 20, f"after starting again it grows again ({watched3} s)")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
