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
print("--- imported transcodes: 'Transcode' means every transcode, a variant itself ---")
# Playback Reporting stores the method with details, "Transcode (v:h264 a:direct)". The transcode share on the
# Overview counts those by the start of the name; the history's filter has to agree with it, and the variants the
# offer lists must really filter.
with db.connect() as conn:
    for key, method in (("e", "Transcode (v:h264 a:direct)"), ("f", "Transcode (v:direct a:aac)")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, play_method, started_at, last_seen_at, ended_at, watched_seconds)"
            " VALUES (?, 'u1', 'Jana', ?, ?, 'Movie', ?, datetime('now','-1 day'),"
            " datetime('now','-1 day'), datetime('now','-1 day'), 600)",
            (key, f"i-{key}", f"Titul {key}", method),
        )
transcodes = db.query_value(
    f"SELECT COUNT(*) FROM playback WHERE watched_seconds > 0 AND {stats.transcode_condition()}")
check(transcodes == 4 and stats.history_count(method="Transcode") == 4,
      f"'Transcode' finds the imported variants too, as the transcode share counts them "
      f"({stats.history_count(method='Transcode')} of {transcodes})")
check(stats.history_count(method="Transcode (v:h264 a:direct)") == 1,
      f"a variant from the offer filters exactly ({stats.history_count(method='Transcode (v:h264 a:direct)')})")
check(len(stats.history(limit=500, method="Transcode")) == stats.history_count(method="Transcode"),
      "the count matches the list for the transcodes")
check(stats.history_count(method="Remux") == 0, "a method nobody played finds nothing")

with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE play_method = 'Transcode'")
offer = stats.filter_values()
check("Transcode" in offer["methods"] and "Transcode (v:h264 a:direct)" in offer["methods"],
      f"with imported transcodes only, 'Transcode' is offered beside the variants ({offer['methods']})")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("tester", "testingpass", is_admin=True)
with TestClient(app) as browser:
    browser.post("/login", data={"username": "tester", "password": "testingpass"})
    page = browser.get("/history", params={"method": "Transcode (v:h264 a:direct)"})
    check(page.status_code == 200 and 'value="Transcode (v:h264 a:direct)" selected' in page.text
          and "Titul e" in page.text and "Titul f" not in page.text,
          "the page keeps a chosen variant and lists only its plays")
    page = browser.get("/history", params={"method": "nonsense"})
    check(page.status_code == 200 and "Titul e" in page.text and "Titul f" in page.text,
          "a method that is not in the offer is ignored")

print()
print("--- the offer of the filters is not counted again on every page ---")
# The offer counted the whole history (three GROUP BYs) on every load and every page click: with a hundred
# thousand plays most of the page's time. The page keeps it until a new play arrives or a minute passes.
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402

from jellyscope.web import history as history_page  # noqa: E402

counted: list[int] = []
real_filter_values = stats.filter_values


def counting_filter_values() -> dict:
    counted.append(1)
    return real_filter_values()


restore = substitute(stats, "filter_values", counting_filter_values)
history_page._FILTER_OPTIONS["at"] = None       # what the pages above kept
try:
    with TestClient(app) as browser:
        browser.post("/login", data={"username": "tester", "password": "testingpass"})
        for address in ("/history", "/history?page=2", "/history?method=Transcode"):
            browser.get(address)
        check(len(counted) == 1, f"three pages count the offer once ({len(counted)} times)")
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, play_method,"
                " client, started_at, last_seen_at, ended_at, watched_seconds) VALUES ('h', 'u1', 'Jana', 'i-h',"
                " 'Novinka', 'Movie', 'DirectPlay', 'Nový přehrávač', datetime('now','-1 day'),"
                " datetime('now','-1 day'), datetime('now','-1 day'), 600)")
        before = len(counted)
        page = browser.get("/history", params={"client": "Nový přehrávač"})
        check(len(counted) == before + 1 and "Novinka" in page.text and "Titul e" not in page.text,
              "a new play brings its player into the offer straight away")
        before = len(counted)
        history_page._FILTER_OPTIONS["at"] -= history_page.FILTER_OPTIONS_TTL + 1
        browser.get("/history")
        check(len(counted) == before + 1, "after a minute the offer is counted again (a deleted play leaves it)")
finally:
    restore()

print()
print("--- the search takes % and _ as they are typed ---")
# In LIKE "_" is any one character and "%" anything: unescaped, searching for "_" listed the whole history.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, play_method,"
        " started_at, last_seen_at, ended_at, watched_seconds) VALUES ('g', 'u1', 'Jana', 'i-g', 'Up_Down 100%',"
        " 'Movie', 'DirectPlay', datetime('now','-1 day'), datetime('now','-1 day'), datetime('now','-1 day'), 600)")
found = {text: stats.history_count(search=text) for text in ("_", "%", "p_D", "100%", "pxD", "!")}
check(found == {"_": 1, "%": 1, "p_D": 1, "100%": 1, "pxD": 0, "!": 0},
      f"only the title that has the characters is found ({found})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
