# -*- coding: utf-8 -*-
r"""A pause is no new play; and the filter in the history can be combined.

**The pause.** Some players vanish from `/Sessions` on a pause. The collector
closes such a record - and when the movie went on, it created a new one. One
viewing was then in the history twice and in the statistics as two plays. Now
an interrupted playback is resumed, if it is the same title, a while passed
and **it did not start from the beginning**: whoever plays a movie again from
zero watches it a second time.

**The filter.** The user, the kind, the period, the play method, the player and
the language must be combinable - and the count at the top must match what is
listed, otherwise the paging lies.

Run:
    .venv/bin/python tests/test_pause_and_filter.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "pauza.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import collector, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

TICK = 10_000_000  # one second in Jellyfin's ticks


class FakeClient:
    """Returns what we would get from /Sessions."""

    def __init__(self) -> None:
        self.sessions_now: list[dict] = []

    async def sessions(self) -> list[dict]:
        return self.sessions_now


def session(session_id: str, position_s: int, paused: bool = False) -> dict:
    return {
        "Id": session_id,
        "UserId": "u1",
        "UserName": "Jana",
        "Client": "Jellyfin Web",
        "DeviceName": "Chrome",
        "PlayState": {"PositionTicks": position_s * TICK, "IsPaused": paused,
                      "PlayMethod": "DirectPlay"},
        "NowPlayingItem": {"Id": "film-1", "Name": "Duna", "Type": "Movie",
                           "RunTimeTicks": 7200 * TICK},
    }


client = FakeClient()


def snapshot() -> None:
    asyncio.run(collector.poll_once(client, max_gap_seconds=30))


print("--- a pause and playing again is ONE playback ---")
client.sessions_now = [session("s1", 600)]
snapshot()
check(db.query_value("SELECT COUNT(*) FROM playback") == 1, "watching started")

# The player vanishes from /Sessions - a pause, a TV gone to sleep, a closed tab.
client.sessions_now = []
snapshot()
check(db.query_value("SELECT COUNT(*) FROM playback WHERE is_active = 1") == 0,
      "the record closed")

# And in a while it is back - another session id, because the client connected again.
client.sessions_now = [session("s2", 1200)]
snapshot()
count = db.query_value("SELECT COUNT(*) FROM playback")
check(count == 1, f"still one playback, not two ({count})")
row = db.query_one("SELECT is_active, ended_at, session_key FROM playback")
check(row["is_active"] == 1, "and it is active again")
check(row["ended_at"] is None, "the end was cancelled")
check(row["session_key"].startswith("s2::"), "the key was rewritten to the new session")


print()
print("--- but playing from the beginning is a new playback ---")
client.sessions_now = []
snapshot()
# The same movie, but the player is at zero - somebody played it again.
client.sessions_now = [session("s3", 5)]
snapshot()
count = db.query_value("SELECT COUNT(*) FROM playback")
check(count == 2, f"this is a second viewing ({count})")


print()
print("--- and after half an hour it is not resumed ---")
with db.connect() as conn:
    conn.execute("UPDATE playback SET is_active = 0,"
                 " last_seen_at = datetime('now', '-2 hours'),"
                 " ended_at = datetime('now', '-2 hours')")
client.sessions_now = [session("s4", 900)]
snapshot()
check(db.query_value("SELECT COUNT(*) FROM playback") == 3,
      "after two hours it is a new viewing")


print()
print("--- the filter in the history can be combined ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    samples = [
        ("a", "u1", "Jana", "Movie", "Transcode", "Jellyfin Web", "cs", 3600),
        ("b", "u1", "Jana", "Episode", "DirectPlay", "Infuse", "en", 1800),
        ("c", "u2", "Petr", "Movie", "DirectPlay", "Jellyfin Web", "cs", 2400),
        ("d", "u2", "Petr", "Movie", "Transcode", "Infuse", None, 1200),
    ]
    for key, user, name, kind, method, client_, language, seconds in samples:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, play_method, client, audio_language,"
            " started_at, last_seen_at, ended_at, watched_seconds)"
            " VALUES (?,?,?,?,?,?,?,?,?, datetime('now','-1 day'),"
            " datetime('now','-1 day'), datetime('now','-1 day'), ?)",
            (key, user, name, f"i-{key}", f"Titul {key}", kind,
             method, client_, language, seconds),
        )

check(stats.history_count() == 4, "without a filter they are all there")
check(stats.history_count(user_id="u1") == 2, "by the user")
check(stats.history_count(method="Transcode") == 2, "by the method")
check(stats.history_count(client="Infuse") == 2, "by the player")
check(stats.history_count(language="cs") == 2, "by the language")
check(stats.history_count(language="und") == 1, "\"neuvedený\" catches an empty value too")
check(stats.history_count(kind=stats.KIND_MOVIE) == 3, "by the kind")

# This is the point: a combination, not just one choice.
check(stats.history_count(user_id="u2", kind=stats.KIND_MOVIE,
                          method="Transcode") == 1,
      "the user + the kind + the method at once")
check(stats.history_count(user_id="u1", method="DirectPlay",
                          client="Infuse") == 1, "and another combination")
check(stats.history_count(since="2000-01-01", until="2000-01-02") == 0,
      "a period out of range returns nothing")

# The count above the list must match the list, otherwise the paging lies.
for filters in ({}, {"user_id": "u1"}, {"method": "Transcode"},
                {"kind": stats.KIND_MOVIE, "client": "Jellyfin Web"}):
    check(len(stats.history(limit=500, **filters)) == stats.history_count(**filters),
          f"the count matches the list {filters or '(without a filter)'}")

offer = stats.filter_values()
check(set(offer["methods"]) == {"Transcode", "DirectPlay"},
      f"the offer of methods by the data ({offer['methods']})")
check("Infuse" in offer["clients"], "and the offer of players too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
