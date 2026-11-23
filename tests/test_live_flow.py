# -*- coding: utf-8 -*-
r"""The live flow on the Network page and the peaks by days.

The Network page used to have a single chart: the concurrent flow over the
whole period, split into a hundred and twenty stretches. For a month that
meant one point per five hours - and the peak was looked for in it. Whoever
wanted to see what flows **now** never learned it; and the curve looked
random, because one peak could be a single hour out of six.

Now these are two different questions and two different cards:

* "Právě teče" (**flowing right now**) - the sum of the flows of running plays
  and the last hour by minutes. It refreshes itself, by the same mechanism as
  "právě se hraje".
* "Špička po dnech" (**the peak by days**) - one number a day, the highest.

Run:
    .venv/bin/python tests/test_live_flow.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "tok.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
NOW = datetime.now(timezone.utc)


def playback(key: str, minutes_ago: int, length_minutes: int, mbit: int,
             active: int = 0, paused: int = 0, seen_minutes_ago: int | None = None) -> None:
    start = NOW - timedelta(minutes=minutes_ago)
    end = start + timedelta(minutes=length_minutes)
    seen = NOW - timedelta(minutes=seen_minutes_ago) if seen_minutes_ago is not None else end
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
            " bitrate, is_paused, is_active, play_method, remote_address, client, device_name)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, "u1", "Pepa", "film", "Duna",
             start.strftime(db.TIME_FORMAT), seen.strftime(db.TIME_FORMAT),
             None if active else end.strftime(db.TIME_FORMAT),
             length_minutes * 60, 0, mbit * 1_000_000, paused, active,
             "Transcode" if mbit > 15 else "DirectPlay", "10.0.0.5", "Web", "Chrome"))


print("--- what flows right now ---")
playback("bezi-1", minutes_ago=30, length_minutes=30, mbit=20, active=1)
playback("bezi-2", minutes_ago=10, length_minutes=10, mbit=8, active=1)
playback("pauza", minutes_ago=40, length_minutes=5, mbit=12, active=1, paused=1)
playback("davno", minutes_ago=600, length_minutes=60, mbit=30)

now_flow = stats.flow_now()
check(now_flow["mbit"] == 28.0, f"adds up only the running ones ({now_flow['mbit']} Mbit/s)")
check(now_flow["stream_count"] == 2, f"and counts them ({now_flow['stream_count']})")
check(now_flow["paused_count"] == 1, "gives the paused ones apart")
check(now_flow["recounted"] == 1, "and how many of them are a transcode")

print()
print("--- the live curve ---")
live = stats.bandwidth_live(None, points=144)
check(len(live) == 144, f"as many points as was said ({len(live)})")
check(live[-1]["mbit"] == 28.0, f"the last point matches what flows ({live[-1]['mbit']})")
check(live[5]["mbit"] == 0.0, f"nothing hours ago ({live[5]['mbit']})")

print()
print("--- a paused one does not count in the curve ---")
# Nothing flows during a pause. It can be told only from what the collector
# reports about running plays - and exactly that drives it here: a paused
# stream ends where it was last playing, and from then on it does not add
# to the curve.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
playback("pauza-ted", minutes_ago=90, length_minutes=30, mbit=25, active=1, paused=1)
live = stats.bandwidth_live(None, points=144)
check(live[-1]["mbit"] == 0.0, f"nothing flows from it now ({live[-1]['mbit']})")
check(max(b["mbit"] for b in live) == 25.0,
      "but the time when it played stayed in the curve")
check(stats.flow_now()["mbit"] == 0.0, "and it does not count in the number next to the curve")
check(stats.flow_now()["paused_count"] == 1, "it is only said that it is there")

print()
print("--- the window follows the filter, at least one day ---")
# An hour window looked broken: in an evening when something played all the
# time, it said "nothing flows", because the last episode had just ended.
def hours(period) -> float:
    points = stats.bandwidth_live(period, points=180)
    return (points[-1]["time"] - points[0]["time"]) / 3600

check(23 <= hours(None) <= 25, f"without a filter a day ({hours(None):.1f} h)")
check(160 <= hours(7) <= 170, f"seven days ({hours(7):.1f} h)")
check(700 <= hours(30) <= 725, f"thirty days ({hours(30):.1f} h)")
# A short own period must not shrink below a day.
short = stats.period_between("2026-08-20", "2026-08-20")
check(23 <= hours(short) <= 25, f"one day stays a day ({hours(short):.1f} h)")

print()
print("--- the label by the window's length ---")
# Up to two days the hour and the minute are enough; over two days the date
# must be visible, otherwise Wednesday and Thursday cannot be told apart on the axis.
check(":" in stats.bandwidth_live(None, points=24)[0]["label"], "a short window: the time")
check("." in stats.bandwidth_live(30, points=24)[0]["label"], "a long window: the date too")

print()
print("--- a running play always belongs in the window ---")
# The collector need not have reported for a whole hour (Jellyfin did not
# answer for a while), but the stream still runs. Were it to drop out of the
# window, the curve would fall to zero and the number next to it would show
# the full flow.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
playback("dlouho-bezi", minutes_ago=300, length_minutes=300, mbit=15,
         active=1, seen_minutes_ago=120)
live = stats.bandwidth_live(None, points=144)
check(live[-1]["mbit"] == 15.0, f"the flow counts on ({live[-1]['mbit']})")

print()
print("--- the peak by days ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
# The day is decided in the application's zone. The plays sit at fixed local times, so the result is the same
# whenever the test runs (counted back from "now", a play 26 hours ago crossed midnight shortly after it):
#  * from 23:00 four days ago to 01:00 three days ago one stream (10) - it flows in both days, so both have a peak;
#  * the day before yesterday at noon two streams at once (10 + 20) - concurrent flows add up;
#  * yesterday at noon one stream (12);
#  * today one stream inside the part of the day that has passed (8) - the current, partial day counts too.
import time as clock  # noqa: E402
from datetime import time  # noqa: E402

from jellyscope import formatting  # noqa: E402

zone = formatting.zone() or NOW.astimezone().tzinfo      # None = the system's local time
now_local = datetime.now(zone)
midnight = datetime.combine(now_local.date(), time(0), tzinfo=zone)
if (now_local - midnight).total_seconds() < 120:        # the first two minutes of a day: give today room for a play
    clock.sleep(120 - (now_local - midnight).total_seconds())
    now_local = datetime.now(zone)
    midnight = datetime.combine(now_local.date(), time(0), tzinfo=zone)
today = now_local.date()


def local(days_back: int, hour: int) -> datetime:
    return datetime.combine(today - timedelta(days=days_back), time(hour), tzinfo=zone)


def playback_between(key: str, start: datetime, end: datetime, mbit: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at, last_seen_at,"
            " ended_at, watched_seconds, paused_seconds, bitrate, is_paused, is_active, play_method)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?,0,0,'DirectPlay')",
            (key, "u1", "Pepa", "film", "Duna", start.astimezone(timezone.utc).strftime(db.TIME_FORMAT),
             end.astimezone(timezone.utc).strftime(db.TIME_FORMAT), end.astimezone(timezone.utc).strftime(db.TIME_FORMAT),
             int((end - start).total_seconds()), mbit * 1_000_000))


elapsed = now_local - midnight
playback_between("pres-pulnoc", local(4, 23), local(3, 1), 10)
playback_between("souběh-a", local(2, 12), local(2, 13), 10)
playback_between("souběh-b", local(2, 12), local(2, 13), 20)
playback_between("vcera", local(1, 12), local(1, 12) + timedelta(minutes=30), 12)
playback_between("dnes", midnight + elapsed / 4, midnight + elapsed / 2, 8)

peaks = {d["day"]: d["mbit"] for d in stats.bandwidth_daily_peaks(7)}
expected = {(today - timedelta(days=n)).isoformat(): mbit for n, mbit in ((4, 10.0), (3, 10.0), (2, 30.0), (1, 12.0), (0, 8.0))}
check(peaks == expected, f"the peak of every day, a play across midnight in both of its days ({peaks})")

print()
print("--- the transcode variants have colours one can tell apart ---")
# Shading one colour was here before and blended in a narrow bar.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    for i, method in enumerate(("DirectPlay", "Transcode",
                                "Transcode (v:h264 a:direct)",
                                "Transcode (v:direct a:aac)")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, watched_seconds, paused_seconds, play_method,"
            " bitrate, is_paused, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)",
            (f"m{i}", "u1", "Pepa", "film", "Duna",
             (NOW - timedelta(hours=2)).strftime(db.TIME_FORMAT),
             (NOW - timedelta(hours=1)).strftime(db.TIME_FORMAT),
             3600, 0, method, 10_000_000))

methods = stats.play_method_breakdown(7)
roles = {r["method"]: r["role"] for r in methods}
check(roles.get("Transcode (v:h264 a:direct)") == "serious",
      "the detailed variant is told as a transcode")
colours = [r.get("color") or r["role"] for r in methods]
check(len(set(colours)) == len(colours), f"and each has its colour ({colours})")

print()
print("--- the peak can be read from the chart ---")
# The curve over a week is dense and a peak tends to be a few pixels wide.
# Without a mark the mouse had to hit it - and the tooltip moreover reported
# the value of the first point in the band, not of the highest, so even after
# a hit the neighbour's number could come up.
from jellyscope import charts  # noqa: E402

with db.connect() as conn:
    conn.execute("DELETE FROM playback")
for i in range(3):
    playback(f"soubezne-{i}", minutes_ago=2 * 24 * 60, length_minutes=40, mbit=15)
playback("maly", minutes_ago=24 * 60, length_minutes=30, mbit=5)

points = stats.bandwidth_live(7)
peak_point = max(points, key=lambda b: b["mbit"])
check(peak_point["mbit"] == 45.0, f"the peak is the sum of three streams ({peak_point['mbit']})")
check(peak_point["stream_count"] == 3, f"and it is known how many there were ({peak_point['stream_count']})")

svg = charts.area_chart_multi(
    points, "label", [{"key": "mbit", "label": "Mbit/s", "color": "var(--accent)"}],
    unit="", steps=True)
# The peak's value belongs in the tooltip, not hard into the chart: a permanent
# label covered the curve and was visible even when nobody wanted it.
check(peak_point["label"] in svg, f"the peak's time is in the tooltip ({peak_point['label']})")

# The tooltip must report the band's peak, not the first point in it.
check(svg.count("chart-hit") <= charts.MAX_AREAS,
      f"at most {charts.MAX_AREAS} areas ({svg.count('chart-hit')})")
check("45" in svg, "and the peak's value can be found in the tooltips")

print()
print("--- the curve's density ---")
# A five-minute step over a week made a hair of a peak. A ceiling of points
# makes a column of it that can be hit.
check(len(points) <= stats.MAX_LIVE_POINTS,
      f"at most {stats.MAX_LIVE_POINTS} points ({len(points)})")
check(points[0]["step_minutes"] >= 10, f"for a week the step is bigger ({points[0]['step_minutes']} min)")
day = stats.bandwidth_live(None)
check(day[0]["step_minutes"] <= 5, f"for a day it stays fine ({day[0]['step_minutes']} min)")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
