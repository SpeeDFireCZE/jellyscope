"""Choosing a range by dragging in a chart.

The chart must say where on its axis which moment lies, the server must accept those
moments and convert them to its zone and the page is then to look
only at the chosen section.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vyber.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import charts, db, stats, web  # noqa: E402

failures = 0


def check(condition, label, detail=""):
    global failures
    if condition:
        print(f"OK     {label}")
    else:
        failures += 1
        print(f"FAIL   {label} {detail}")


db.init_db()
db.set_setting("app_timezone", "Europe/Prague")

print("The chart gives away the axis' bounds")
base = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc).timestamp()
points = [{"label": f"20.08. {18 + i}:00", "time": base + i * 3600, "mbit": 10.0 + i}
          for i in range(5)]
svg = charts.area_chart_multi(points, "popisek", [{"key": "mbit", "label": "Mbit/s"}],
                              unit="", selectable=True)
check('data-selection="time"' in svg, "the chart is marked as selectable in time")
check(f'data-time-from="{int(base)}"' in svg
      and f'data-time-to="{int(base + 4 * 3600)}"' in svg,
      "it carries the edge moments")
check('data-x-from=' in svg and 'data-x-to=' in svg, "it carries the bounds of the x axis")
check('class="chart-selection"' in svg, "it has the selection rectangle ready")

without = charts.area_chart_multi(points, "popisek", [{"key": "mbit", "label": "Mbit/s"}],
                                  unit="")
check("data-selection" not in without and "chart-selection" not in without,
      "without selectable=True nothing is added")

print("A chart by day is rounded to days")
days_rows = [{"day": f"2026-08-{day:02d}", "hours": day} for day in range(10, 18)]
svg_days = charts.area_chart_multi(days_rows, "day", [{"key": "hours", "label": "Hodiny"}],
                                   selectable=True)
check('data-selection="days"' in svg_days, "it is marked as daily")
check('data-days="2026-08-10,2026-08-11' in svg_days and "2026-08-17" in svg_days,
      "it carries the list of days")
check("data-time-from" not in svg_days, "it does not send the time")

# A day without a date on the axis (only a label) cannot be selected - there would be nothing
# to tell which day it is.
without_days = charts.area_chart_multi(
    [{"label": f"{d}.08.", "mbit": d} for d in range(10, 18)], "popisek",
    [{"key": "mbit", "label": "Mbit/s"}], selectable=True)
check("data-selection" not in without_days, "without days and time nothing is selected")

print("Moments are converted to the application's zone")
since = datetime(2026, 8, 20, 19, 30, tzinfo=timezone.utc).timestamp()
until = datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc).timestamp()
o = stats.period_from_instants(since, until)
check(o.since == "2026-08-20 19:30:00" and o.until == "2026-08-20 21:45:00", "the query is in UTC",
      f"({o.since} -> {o.until})")
check(o.since_local == "2026-08-20 21:30:00" and o.until_local == "2026-08-20 23:45:00",
      "for a person the local time",
      f"({o.since_local} -> {o.until_local})")
check(o.relative is False, "it is not relative")
check(stats.period_from_instants(until, since) is None, "reversed bounds are refused")
check(stats.period_from_instants("x", "y") is None, "nonsense is refused")

# The moments come from the address, so slipping in a link is enough. The calendar has its
# range and a conversion outside it throws OverflowError or OSError - the page
# fell over with 500 on it. A nonsensical period is no server error.
for desc, since, until in (("huge", 1e308, 1.5e308),
                           ("negative huge", -1e308, 0.0),
                           ("zero (the conversion falls over on Windows)", 0.0, 1.0),
                           ("outside the calendar", 10 ** 20, 10 ** 20 + 1),
                           ("not a number", float("nan"), 1.0),
                           ("infinity", float("inf"), float("inf"))):
    try:
        result = stats.period_from_instants(since, until)
        crashed = False
    except Exception:  # noqa: BLE001 - that is what it is about here
        result, crashed = "exception", True
    check(not crashed, f"{desc} does not fall over", f"({result})")

print("A whole day in the local zone, not in UTC")
day = stats.period_between("2026-08-20", "2026-08-20")
check(day.since == "2026-08-19 22:00:00" and day.until == "2026-08-20 22:00:00",
      "summer: the day starts at 22:00 UTC of the previous day",
      f"({day.since} -> {day.until})")
winter = stats.period_between("2026-01-15", "2026-01-15")
check(winter.since == "2026-01-14 23:00:00" and winter.until == "2026-01-15 23:00:00",
      "winter: a shift by an hour",
      f"({winter.since} -> {winter.until})")

print("The switch at the top shows the time only when it matters")
desc = web._period_for_template(o)
check(desc["since_label"] == "20.8.2026 21:30" and desc["until_label"] == "20.8.2026 23:45",
      "selecting part of a day shows the time",
      f"({desc['since_label']} – {desc['until_label']})")
desc_day = web._period_for_template(day)
check(desc_day["since_label"] == "20.8.2026" and desc_day["until_label"] == "20.8.2026",
      "a whole day stays without the time",
      f"({desc_day['since_label']} – {desc_day['until_label']})")
check(desc["since_text"] == "20.8.2026" and desc["until_text"] == "20.8.2026",
      "the form still gets only the date")

print("The chosen section really narrows the data")
now = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
with db.connect() as conn:
    for index, offset in enumerate([-3, -1, 3]):   # hours from `now`
        start = now + timedelta(hours=offset)
        end = start + timedelta(hours=1)
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
            " is_paused, is_active) VALUES (?,?,?,?,?,?,?,?,?,0,0,0)",
            (f"s{index}", f"u{index}", "Pepa", f"i{index}", "Film",
             start.strftime(db.TIME_FORMAT), end.strftime(db.TIME_FORMAT),
             end.strftime(db.TIME_FORMAT), 3600))
    conn.commit()

section = stats.period_from_instants(
    (now - timedelta(hours=2)).timestamp(), (now + timedelta(hours=1)).timestamp())
summary = stats.overview(section)
check(summary["watched_seconds"] == 3600,
      "the section holds only what belongs in it",
      f"(seconds: {summary['watched_seconds']})")

print("A period shorter than two days still has something to show")
# Selecting by dragging can end at one single day. A point without a neighbour has no
# length, so a line of zero width came of it - the chart looked empty,
# even though there was watching that day.
single = charts.area_chart_multi([{"day": "2026-08-31", "movie_hours": 3.65}], "day",
                                 [{"key": "movie_hours", "label": "Filmy"}])
paths = re.findall(r'<path d="([^"]+)"', single)
check(len(paths) == 2, "the chart is drawn from one day", f"(paths: {len(paths)})")
# The edges are not read hard-coded: the left one widened, so larger axis labels
# fit into it on a phone. What matters is that the line goes from edge to edge
# of the area, not how much that edge measures right now.
def horizontal_range(path: str) -> tuple[float, float]:
    """The smallest and the biggest x in a path - the pairs "x,y" control points included."""
    xs = [float(x) for x, _ in re.findall(r"([\d.]+),([\d.]+)", path)]
    return min(xs), max(xs)


edges = [horizontal_range(c) for c in paths]
check(all(since < 120 and until > 700 and until - since > 600 for since, until in edges),
      "and the line goes across the whole width",
      f"({edges})")
# The line (the second path) starts at the height of the value. Zero lies on the baseline,
# so the higher, the smaller the coordinate - and right on the baseline it would
# mean there was no watching that day.
base = 240 - 30      # the chart's height minus the bottom margin
line_height = float(re.match(r"M[\d.]+,([\d.]+)", paths[1]).group(1))
check(line_height < base - 20, "the line is at the height of the value, not at zero",
      f"(y={line_height}, baseline={base})")

# The mini chart under the main number is for a trend - for one day there is none,
# so it is not to take up space with an empty area.
check(charts.sparkline([{"day": "2026-08-31", "hours": 3.65}]) == "",
      "a mini chart from one day is not drawn")
check(charts.sparkline([{"day": "2026-08-30", "hours": 1.0},
                        {"day": "2026-08-31", "hours": 3.65}]) != "",
      "from two days it is")

print("The comparison with the previous period measures a section of the same length")
# Selecting by dragging can be shorter than a day. If the previous window were derived
# from the number of days (a two-hour selection has `days == 1`), two
# hours would be compared with the whole previous day - and every arrow "against the previous
# period" would report a drop that did not happen.
def length(o):
    return (datetime.strptime(o.until, db.TIME_FORMAT)
            - datetime.strptime(o.since, db.TIME_FORMAT))

for desc, hours in (("two hours", 2), ("six hours", 6), ("a day and a half", 36)):
    selection = stats.period_from_instants((now - timedelta(hours=hours)).timestamp(),
                                           now.timestamp())
    previous = stats.previous_period(selection)
    check(length(previous) == length(selection),
          f"{desc}: the previous window is of the same length", f"({length(previous)} vs {length(selection)})")
    check(previous.until == selection.since, f"{desc}: and it ends where the chosen one starts")

# Whole days and "the last N days" must stay as they were.
day = stats.period_between("2026-08-20", "2026-08-20")
check(length(stats.previous_period(day)).days == 1, "a whole day: the previous one is a day too")
# "The last 30 days" hold only the part of today that has passed, so their previous window is the same span thirty
# days back (decided 25. 11. 2026): from the local midnight thirty days before their first day to this clock time
# thirty days ago - not thirty whole days, which made every morning look like a drop.
from jellyscope import formatting  # noqa: E402


def local(utc_text: str) -> datetime:
    return (datetime.strptime(utc_text, db.TIME_FORMAT).replace(tzinfo=timezone.utc)
            .astimezone(formatting.zone()).replace(tzinfo=None))


chosen, before = stats._as_period(30), stats.previous_period(30)
check(local(before.since) == local(chosen.since) - timedelta(days=30),
      f"the last 30 days: the previous one starts thirty days before their first midnight ({local(before.since)})")
passed = datetime.now(formatting.zone()).replace(tzinfo=None) - local(chosen.since)
check(abs((local(before.until) - local(before.since)) - passed) < timedelta(minutes=1),
      f"and is as long as the part of the thirty days that has passed ({local(before.until) - local(before.since)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
