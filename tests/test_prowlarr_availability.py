# -*- coding: utf-8 -*-
r"""Prowlarr: the indexers' availability computed from the stored samples of their status.

Every sample (`prowlarr_sample`) has a row of its own and a row for every indexer Prowlarr left out at that moment.
The statistics (`stats.prowlarr_availability`) turn them into, for the period and every indexer: the share of the
MEASURED time it was not left out, how many outages, the longest one, the last failure, the share of failed queries
from the hourly history (`prowlarr_hour`) and one state per day for the card's strip. Guarded on tiny hand-made data
sets where the right answer is obvious (hourly samples, the usual interval one hour):

A) before the first sample: only the failed queries from the history, every day "not measured" or "failed";
B) an outage starts at "failing since", but not before the last clean sample, and ends at the first later sample
   without it; a day with an outage is "outage" even with failed queries, a day with only failed queries "failed";
   an indexer known only from the samples; the most troublesome first;
C) a gap between two samples longer than twice the usual interval is not measured time (twice exactly still is),
   and time nobody measured is not counted as down;
D) an outage crossing the period's start and its end, an outage still running (the next sample is not late yet,
   so the time up to now is measured), and the same when the last sample is too old;
E) a period with no sample in it, but samples later: nothing measured, "measured since" the first sample;
F) the days the clocks change (Europe/Prague): a 25-hour and a 23-hour day, an outage in the right local day;
G) every sample keeps which indexers were present and enabled then and the usual interval in force: an indexer's time
   is measured only between two samples it was present and enabled in - an indexer added in the period, one removed
   from Prowlarr, one switched off by hand - and a gap is judged by the interval stored with its samples, never by
   today's setting.

The rows are inserted straight into the tables - tests/test_prowlarr_samples.py guards how samples are taken.

Spec: docs/superpowers/specs/2026-11-26-live-cards-design.md (B1)

Run:
    .venv/bin/python tests/test_prowlarr_availability.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-availability.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402

failures = 0
db.init_db()
HOUR = timedelta(hours=1)
FORMAT = "%Y-%m-%d %H:%M:%S"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def set_zone(name: str) -> None:
    db.set_setting("app_timezone", name)
    db.forget_settings()


def add_indexer(number: int, name: str, enabled: int = 1, removed: int = 0) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at) VALUES (?, ?, 'usenet', 'private', ?, ?, ?)",
                     (number, name, enabled, removed, "2026-11-01 00:00:00"))


def add_hour(when: str, indexer: int, kind: str, count: int, failed: int = 0) -> None:
    """The sum of one hour (UTC) of one indexer's queries."""
    with db.connect() as conn:
        conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                     " VALUES (?, ?, 'Sonarr', ?, ?, ?, 0, 0, 0)", (when, indexer, kind, count, failed))


# The indexers present and enabled in Prowlarr at a sample, unless a test says otherwise (9 is known only from samples).
ENABLED = (1, 2, 3, 9)


def sample(when: str, *left_out: tuple[int, str | None, str | None], enabled: tuple[int, ...] = ENABLED,
           interval: timedelta = HOUR) -> None:
    """A sample taken at `when` (UTC): its own row - with the indexers present and enabled then (as samples.py stores
    them, ",1,2,") and the usual interval in force - and a row per indexer left out as (number, failing since, most
    recent failure)."""
    with db.connect() as conn:
        conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id, enabled_ids, interval_seconds) VALUES (?, 0, ?, ?)",
                     (when, "".join(f",{n}" for n in enabled) + ",", int(interval.total_seconds())))
        for number, since, last in left_out:
            conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id, failing_since, skipped_until, last_failure) VALUES (?, ?, ?, ?, ?)",
                         (when, number, since, when, last))


def hours(first: str, last: str) -> list[str]:
    """Every whole hour from `first` to `last` (UTC), both included."""
    moment, end, out = datetime.strptime(first, FORMAT), datetime.strptime(last, FORMAT), []
    while moment <= end:
        out.append(moment.strftime(FORMAT))
        moment += HOUR
    return out


def samples(moments: list[str], left_out: dict[str, list[tuple[int, str | None, str | None]]] | None = None,
            interval: timedelta = HOUR) -> None:
    """A sample at every moment; `left_out` says who was left out at which of them."""
    for moment in moments:
        sample(moment, *(left_out or {}).get(moment, []), interval=interval)


def clear() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM prowlarr_sample")
        conn.execute("DELETE FROM prowlarr_hour")


def rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["name"] or f"#{r['indexer_id']}": r for r in result["indexers"]}


def close(a: float | None, b: float) -> bool:
    return a is not None and abs(a - b) < 1e-9


set_zone("UTC")
add_indexer(1, "Alfa")
add_indexer(2, "Beta")
add_indexer(3, "Gama")
add_indexer(4, "Delta", enabled=0)           # switched off by hand and without data: not in the table
add_indexer(5, "Epsilon", removed=1)         # gone from Prowlarr: only while it has data in the period

print("--- A) before the first sample ---")
add_hour("2026-11-02 10:00:00", 1, "query", 10, 2)
add_hour("2026-11-03 08:00:00", 2, "rss", 5, 0)
add_hour("2026-11-02 12:00:00", 5, "query", 4, 4)
add_hour("2026-11-02 12:00:00", 0, "query", 3, 3)      # an event without an indexer has no availability
THREE_DAYS = stats.period_between("2026-11-01", "2026-11-03")
a = stats.prowlarr_availability(THREE_DAYS, now="2026-11-10 12:00:00")
check(a["first_sample"] == "" and a["measured_since"] == "" and a["days"] == ["2026-11-01", "2026-11-02", "2026-11-03"],
      f"no sample yet: no first sample, the period's days ({a['first_sample']!r}, {a['days']})")
check([r["name"] for r in a["indexers"]] == ["Epsilon", "Alfa", "Beta", "Gama"],
      f"the enabled indexers and those with queries, from the highest share of failed queries; not the one switched off by hand ({[r['name'] for r in a['indexers']]})")
r = rows(a)
check(all(x["availability"] is None and x["outages"] == 0 and x["longest"] is None and x["last_failure"] is None and x["measured"] == 0
          and x["running"] is False for x in a["indexers"]),
      "nothing measured: the availability, the longest outage and the last failure are None, not zero")
check((r["Alfa"]["query_count"], r["Alfa"]["failed_count"]) == (10, 2) and close(r["Alfa"]["failure_share"], 0.2)
      and close(r["Epsilon"]["failure_share"], 1.0) and r["Epsilon"]["removed"] is True and r["Gama"]["failure_share"] is None,
      "the share of failed queries from the hourly history; a removed indexer is marked")
check(r["Alfa"]["days"] == ["unmeasured", "failed", "unmeasured"] and r["Beta"]["days"] == ["unmeasured"] * 3,
      f"the strip: a day with failed queries is “failed”, the others “not measured” ({r['Alfa']['days']}, {r['Beta']['days']})")

print()
print("--- B) outages over hourly samples ---")
clear()
samples(hours("2026-11-01 23:00:00", "2026-11-03 01:00:00"), {
    "2026-11-02 10:00:00": [(1, "2026-11-02 09:30:00", "2026-11-02 09:55:00")],
    "2026-11-02 11:00:00": [(1, "2026-11-02 09:30:00", "2026-11-02 10:50:00")],
    "2026-11-02 12:00:00": [(9, "2026-11-02 11:45:00", "2026-11-02 11:50:00")],
    # Prowlarr's "failing since" is from a streak long before - the last clean sample (14:00) is the earliest start.
    "2026-11-02 15:00:00": [(2, "2026-11-01 08:00:00", "2026-11-02 14:58:00")],
})
add_hour("2026-11-02 10:00:00", 1, "query", 2, 2)
add_hour("2026-11-02 08:00:00", 3, "query", 4, 1)
ONE_DAY = stats.period_between("2026-11-02", "2026-11-02")
b = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
r = rows(b)
check([x["name"] or x["indexer_id"] for x in b["indexers"]] == ["Alfa", "Beta", 9, "Gama"],
      f"the most troublesome first: the lowest availability; an indexer known only from the samples has no name ({[x['name'] or x['indexer_id'] for x in b['indexers']]})")
check(all(x["measured"] == 86400 for x in b["indexers"]) and b["first_sample"] == "2026-11-01 23:00:00" and b["measured_since"] == "",
      "the samples cover the whole day: 24 measured hours, no “measured since”")
alfa = r["Alfa"]
check((alfa["outages"], alfa["longest"], alfa["down"], alfa["last_failure"], alfa["running"]) == (1, 9000, 9000, "2026-11-02 10:50:00", False)
      and close(alfa["availability"], 1 - 9000 / 86400),
      f"an outage from “failing since” 9:30 to the first clean sample 12:00: 2.5 h, the last failure is the newest ({alfa})")
beta = r["Beta"]
check((beta["outages"], beta["longest"], beta["last_failure"]) == (1, 7200, "2026-11-02 14:58:00") and close(beta["availability"], 1 - 7200 / 86400),
      f"“failing since” the day before, but not before the last clean sample: 14:00-16:00 = 2 h ({beta['longest']})")
check((r["Gama"]["outages"], r["Gama"]["longest"], r["Gama"]["last_failure"]) == (0, None, None) and close(r["Gama"]["availability"], 1.0)
      and close(r["Gama"]["failure_share"], 0.25),
      "never left out: available all the measured time, no outage; its failed queries stand apart")
check((alfa["days"], r["Gama"]["days"], r["#9"]["days"]) == (["outage"], ["failed"], ["outage"]),
      f"the strip: an outage beats failed queries, a day with only failed queries is “failed” ({alfa['days']}, {r['Gama']['days']})")
page_analysis = stats.prowlarr_analysis(ONE_DAY)
add_hour("2026-11-02 09:00:00", 3, "query", 4, 4)       # stored after the page computed its analysis
shared = stats.prowlarr_availability(ONE_DAY, analysis=page_analysis, now="2026-11-10 00:00:00")
fresh = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(shared == b and rows(fresh)["Gama"]["failed_count"] == 5,
      f"the page's shared analysis is used, not the hourly history again ({rows(shared)['Gama']['failed_count']}, {rows(fresh)['Gama']['failed_count']})")

print()
print("--- C) gaps between samples ---")
clear()
GAPS = [h for h in hours("2026-11-01 23:00:00", "2026-11-03 01:00:00")
        if h not in ("2026-11-02 06:00:00", "2026-11-02 18:00:00", "2026-11-02 19:00:00", "2026-11-02 20:00:00", "2026-11-02 21:00:00")]
samples(GAPS, {"2026-11-02 17:00:00": [(1, "2026-11-02 16:30:00", "2026-11-02 16:59:00")],
               "2026-11-02 22:00:00": [(1, "2026-11-02 16:30:00", "2026-11-02 21:59:00")]})
c = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
r = rows(c)
check(all(x["measured"] == 86400 - 5 * 3600 for x in c["indexers"]),
      f"5:00 → 7:00 is twice the interval and still measured, 17:00 → 22:00 is not: 19 measured hours ({r['Gama']['measured']})")
check((r["Alfa"]["outages"], r["Alfa"]["longest"], r["Alfa"]["down"]) == (1, 5400, 5400) and close(r["Alfa"]["availability"], 1 - 5400 / 68400),
      f"an outage across the gap is one, and only its measured half hour before and hour after count ({r['Alfa']})")
clear()
samples(GAPS, interval=timedelta(minutes=10))
ten_minutes = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(all(x["availability"] is None and x["measured"] == 0 and x["days"] == ["unmeasured"] for x in ten_minutes["indexers"]),
      "hourly samples stored with a usual interval of ten minutes are only gaps: nothing measured")

print()
print("--- D) the period's edges, an outage still running ---")
clear()
LATE = hours("2026-11-04 20:00:00", "2026-11-05 03:00:00")
samples(LATE, {
    **{h: [(1, "2026-11-04 21:30:00", (datetime.strptime(h, FORMAT) - timedelta(minutes=1)).strftime(FORMAT))]
       for h in hours("2026-11-04 22:00:00", "2026-11-05 02:00:00")},
    "2026-11-05 02:00:00": [(1, "2026-11-04 21:30:00", "2026-11-05 01:59:00"), (2, "2026-11-05 01:40:00", "2026-11-05 01:59:00")],
    "2026-11-05 03:00:00": [(2, "2026-11-05 01:40:00", "2026-11-05 02:59:00")],
})
FIFTH = stats.period_between("2026-11-05", "2026-11-05")
d = stats.prowlarr_availability(FIFTH, now="2026-11-05 03:30:00")
r = rows(d)
check(all(x["measured"] == 12600 for x in d["indexers"]) and [x["name"] for x in d["indexers"]] == ["Alfa", "Beta", "Gama"],
      f"the last sample is half an hour old - the next one is not late yet: measured up to now, 3.5 h ({r['Gama']['measured']})")
check((r["Alfa"]["outages"], r["Alfa"]["longest"], r["Alfa"]["running"], r["Alfa"]["last_failure"]) == (1, 10800, False, "2026-11-05 01:59:00")
      and close(r["Alfa"]["availability"], 1 - 10800 / 12600),
      f"an outage that began the day before counts from the period's start: 0:00-3:00 ({r['Alfa']['longest']})")
check((r["Beta"]["outages"], r["Beta"]["longest"], r["Beta"]["running"], r["Beta"]["last_failure"]) == (1, 6600, True, "2026-11-05 02:59:00")
      and close(r["Beta"]["availability"], 1 - 6600 / 12600) and r["Beta"]["days"] == ["outage"] and r["Gama"]["days"] == ["fine"],
      f"an outage still running: from 1:40 up to now, marked as running ({r['Beta']['longest']}, {r['Beta']['running']})")
old = rows(stats.prowlarr_availability(FIFTH, now="2026-11-05 06:00:00"))
check(old["Gama"]["measured"] == 10800 and (old["Beta"]["longest"], old["Beta"]["running"]) == (4800, False) and close(old["Alfa"]["availability"], 0.0),
      f"the last sample three hours old: measured only up to it, the outage ends at it, nothing is “running” ({old['Beta']['longest']})")
FOURTH = stats.period_between("2026-11-04", "2026-11-04")
e = stats.prowlarr_availability(FOURTH, now="2026-11-05 03:30:00")
r = rows(e)
check(all(x["measured"] == 4 * 3600 for x in e["indexers"]) and e["measured_since"] == "2026-11-04 20:00:00",
      f"the samples start at 20:00: 4 measured hours and “measured since” the first sample ({e['measured_since']!r})")
check((r["Alfa"]["outages"], r["Alfa"]["longest"], r["Alfa"]["running"], r["Alfa"]["last_failure"]) == (1, 9000, False, "2026-11-04 23:59:00")
      and close(r["Alfa"]["availability"], 1 - 9000 / 14400) and [x["name"] for x in e["indexers"]] == ["Alfa", "Beta", "Gama"],
      f"an outage crossing the period's end counts up to the end, the last failure within the period ({r['Alfa']['longest']}, {r['Alfa']['last_failure']})")

print()
print("--- E) a period with no sample ---")
OCTOBER = stats.period_between("2026-10-01", "2026-10-02")
f = stats.prowlarr_availability(OCTOBER, now="2026-11-10 00:00:00")
check(f["measured_since"] == "2026-11-04 20:00:00" and [x["name"] for x in f["indexers"]] == ["Alfa", "Beta", "Gama"]
      and all(x["availability"] is None and x["outages"] == 0 and x["days"] == ["unmeasured", "unmeasured"] for x in f["indexers"]),
      f"samples only after the period: nothing measured, every day “not measured”, measured since the first sample ({f['measured_since']!r})")

print()
print("--- F) the days the clocks change (Europe/Prague) ---")
clear()
set_zone("Europe/Prague")
# Autumn: on 25. 10. 2026 the clocks go back at 1:00 UTC - the day has 25 hours. 22:10-22:40 UTC is 23:10-23:40 of 25. 10.
samples(sorted([*hours("2026-10-23 21:00:00", "2026-10-27 00:00:00"), "2026-10-25 22:20:00", "2026-10-25 22:40:00"]), {
    "2026-10-25 05:00:00": [(2, "2026-10-25 04:00:00", "2026-10-25 04:30:00")],
    "2026-10-25 22:20:00": [(1, "2026-10-25 22:10:00", "2026-10-25 22:15:00")],
})
g = rows(stats.prowlarr_availability(stats.period_between("2026-10-24", "2026-10-26"), now="2026-11-10 00:00:00"))
check(g["Alfa"]["days"] == ["fine", "outage", "fine"] and g["Beta"]["days"] == ["fine", "outage", "fine"],
      f"autumn: the outage 22:10 UTC belongs to 25. 10. in the winter time, not to the next day ({g['Alfa']['days']})")
g = rows(stats.prowlarr_availability(stats.period_between("2026-10-25", "2026-10-25"), now="2026-11-10 00:00:00"))
check(g["Gama"]["measured"] == 25 * 3600 and close(g["Beta"]["availability"], 1 - 7200 / 90000) and close(g["Alfa"]["availability"], 1 - 1800 / 90000),
      f"the 25-hour day: two hours out of 25 ({g['Gama']['measured']}, {g['Beta']['availability']})")
# Spring: on 29. 3. 2026 the clocks go forward at 1:00 UTC - the day has 23 hours. 22:10-22:40 UTC is 0:10-0:40 of 30. 3.
clear()
samples(sorted([*hours("2026-03-27 22:00:00", "2026-03-30 23:00:00"), "2026-03-29 22:20:00", "2026-03-29 22:40:00"]), {
    "2026-03-29 22:20:00": [(2, "2026-03-29 22:10:00", "2026-03-29 22:15:00")],
})
g = rows(stats.prowlarr_availability(stats.period_between("2026-03-28", "2026-03-30"), now="2026-11-10 00:00:00"))
check(g["Beta"]["days"] == ["fine", "fine", "outage"] and g["Gama"]["days"] == ["fine"] * 3,
      f"spring: the outage 22:10 UTC belongs to 30. 3. in the summer time ({g['Beta']['days']})")
g = rows(stats.prowlarr_availability(stats.period_between("2026-03-29", "2026-03-29"), now="2026-11-10 00:00:00"))
check(g["Gama"]["measured"] == 23 * 3600 and close(g["Gama"]["availability"], 1.0) and g["Beta"]["outages"] == 0,
      f"the 23-hour day: 23 measured hours, the outage after its midnight is not in it ({g['Gama']['measured']})")
set_zone("UTC")

print()
print("--- G) indexers that come and go; the interval stored with the samples ---")
clear()
add_indexer(6, "Zeta")
TEN_DAYS = stats.period_between("2026-11-01", "2026-11-10")
ADDED, REMOVED, OFF_FROM, OFF_UNTIL = "2026-11-09 00:00:00", "2026-11-03 00:00:00", "2026-11-05 00:00:00", "2026-11-07 00:00:00"
for moment in hours("2026-11-01 00:00:00", "2026-11-11 00:00:00"):
    present, left = [1], []                              # Alfa: present and fine all the time
    if moment >= ADDED:                                  # Zeta: added on day 9, left out at every sample since
        present.append(6)
        left.append((6, ADDED, moment))
    if moment < REMOVED:                                 # Epsilon: left out on days 1-2, then removed from Prowlarr
        present.append(5)
        left.append((5, "2026-11-01 00:00:00", moment))
    if not OFF_FROM <= moment < OFF_UNTIL:               # Beta: switched off by hand on days 5 and 6
        present.append(2)
    sample(moment, *left, enabled=tuple(present))
g = rows(stats.prowlarr_availability(TEN_DAYS, now="2026-11-20 00:00:00"))
check(close(g["Zeta"]["availability"], 0.0) and g["Zeta"]["measured"] == 48 * 3600 and g["Zeta"]["days"] == ["unmeasured"] * 8 + ["outage"] * 2,
      f"an indexer added on day 9 and out ever since: 0 %, measured only since it is there ({g['Zeta']['availability']}, {g['Zeta']['days']})")
check(close(g["Epsilon"]["availability"], 0.0) and g["Epsilon"]["measured"] == 47 * 3600 and g["Epsilon"]["longest"] == 47 * 3600
      and g["Epsilon"]["days"] == ["outage"] * 2 + ["unmeasured"] * 8,
      f"an indexer out on days 1-2 and then removed: 0 %, nothing measured after its removal ({g['Epsilon']['availability']}, {g['Epsilon']['days']})")
check(close(g["Beta"]["availability"], 1.0) and g["Beta"]["measured"] == (240 - 49) * 3600
      and g["Beta"]["days"] == ["fine"] * 4 + ["unmeasured"] * 2 + ["fine"] * 4,
      f"an indexer switched off by hand: its days off are “not measured” ({g['Beta']['measured'] / 3600} h, {g['Beta']['days']})")
check(g["Alfa"]["measured"] == 240 * 3600 and close(g["Alfa"]["availability"], 1.0)
      and g["Gama"]["availability"] is None and g["Gama"]["measured"] == 0 and g["Gama"]["days"] == ["unmeasured"] * 10,
      f"an indexer present all the time is measured all the time; one enabled now but in no sample, never ({g['Gama']['measured']})")

# The reviewer's case: hourly samples of the task (stored with its hour) for ten days, Jellyscope down 20 hours
# (11-05 06:00 → 11-06 02:00), Alfa left out at the two samples before (failing since 4:30).
clear()
DOWN = ("2026-11-05 06:00:00", "2026-11-06 02:00:00")
for moment in hours("2026-11-01 00:00:00", "2026-11-10 23:00:00"):
    if not DOWN[0] < moment < DOWN[1]:
        sample(moment, *([(1, "2026-11-05 04:30:00", moment)] if "2026-11-05 05:00:00" <= moment <= DOWN[0] else []), enabled=(1,))


def alfa() -> tuple[Any, ...]:
    r = rows(stats.prowlarr_availability(TEN_DAYS, now="2026-11-20 00:00:00"))["Alfa"]
    return r["availability"], r["measured"], r["longest"]


hourly = alfa()
check(close(hourly[0], 1 - 5400 / (219 * 3600)) and hourly[1:] == (219 * 3600, 5400),
      f"20 hours without a sample are not measured, the outage counts its measured hour and a half ({hourly})")
# Samples every 12 hours stored with that interval are measured time; then hourly ones: a gap of five hours between
# two of them is not, and the gap from the last 12-hour sample to the first hourly one is judged by the larger interval.
clear()
for moment in ("2026-11-01 00:00:00", "2026-11-01 12:00:00", "2026-11-02 00:00:00"):
    sample(moment, enabled=(1,), interval=timedelta(hours=12))
for moment in [*hours("2026-11-02 10:00:00", "2026-11-02 12:00:00"), *hours("2026-11-02 17:00:00", "2026-11-03 00:00:00")]:
    sample(moment, enabled=(1,))
mixed = rows(stats.prowlarr_availability(stats.period_between("2026-11-01", "2026-11-02"), now="2026-11-20 00:00:00"))["Alfa"]
check(mixed["measured"] == (48 - 5) * 3600 and mixed["days"] == ["fine", "fine"],
      f"each gap by the interval stored with its two samples: 12-hour ones measured, 5 hours between hourly ones not ({mixed['measured'] / 3600} h)")

print()
print("--- H) the background loop's samples: measured while it ran, not measured while it did not ---")
MINUTE = timedelta(minutes=1)


def watched(when: str, until: str, *left_out: tuple[int, str | None, str | None], interval: timedelta = MINUTE) -> None:
    """A sample of the background loop (arr/prowlarr/watch.py): taken at `when` and seen the same at every round up
    to `until` (measured_until), with the loop's interval."""
    sample(when, *left_out, interval=interval)
    with db.connect() as conn:
        conn.execute("UPDATE prowlarr_sample SET measured_until = ? WHERE sampled_at = ? AND indexer_id = 0", (until, when))


def at(minutes: float) -> str:
    """A moment `minutes` after 10:00 of 2. 11. (UTC)."""
    return (datetime(2026, 11, 2, 10) + timedelta(minutes=minutes)).strftime(FORMAT)


clear()
# The loop every minute from 10:00 to 12:00, a new sample every ten minutes (nothing changed): each measured to the
# round before the next one - two hours, the steps of one round between them included.
for start in range(0, 120, 10):
    watched(at(start), at(start + 9 if start < 110 else 120))
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(all(x["measured"] == 7200 for x in h["indexers"] if x["name"] in ("Alfa", "Beta", "Gama")) and h.get("intervals") == [60],
      f"a loop sample is measured up to the last round that saw it; the next one a round later: 2 h, measured every minute"
      f" ({[x['measured'] for x in h['indexers']]}, {h.get('intervals')})")
clear()
watched(at(0), at(60))
watched(at(90), at(120))
h = rows(stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00"))
check(h["Gama"]["measured"] == 5400 and h["Gama"]["days"] == ["fine"],
      f"the app down from 11:00 to 11:30: the half hour without a round is not measured ({h['Gama']['measured']})")
clear()
watched(at(0), at(5))
watched(at(7), at(30))      # the round at 10:06 got no answer from Prowlarr
h = rows(stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00"))
check(h["Gama"]["measured"] == (5 + 23) * 60,
      f"one round Prowlarr did not answer: the two intervals around it are not measured - unlike the task's samples, where"
      f" twice the interval still is ({h['Gama']['measured']})")
clear()
# Alfa left out from the round at 10:20 (failing since 10:19:30) to the round at 10:40: the outage starts exactly at its
# failing since (the last clean round was 10:19) and ends at the first round without it.
watched(at(0), at(19))
watched(at(20), at(39), (1, at(19.5), at(38)))
watched(at(40), at(60))
h = rows(stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00"))
check((h["Alfa"]["outages"], h["Alfa"]["longest"], h["Alfa"]["measured"], h["Alfa"]["last_failure"]) == (1, 1230, 3600, at(38))
      and close(h["Alfa"]["availability"], 1 - 1230 / 3600) and close(h["Beta"]["availability"], 1.0),
      f"an outage measured by the loop: from its failing since to the round that saw it fine, 20.5 min ({h['Alfa']})")
clear()
watched(at(0), at(29.5), (2, at(-5), at(29)))
running = rows(stats.prowlarr_availability(ONE_DAY, now=at(30.5)))
stopped = rows(stats.prowlarr_availability(ONE_DAY, now=at(32)))
check(running["Gama"]["measured"] == 1830 and running["Beta"]["running"] is True and running["Beta"]["longest"] == 1830,
      f"the last round half a minute ago: the loop runs - measured up to now, the outage still running ({running['Gama']['measured']})")
check(stopped["Gama"]["measured"] == 1770 and stopped["Beta"]["running"] is False and stopped["Beta"]["longest"] == 1770,
      f"the last round two and a half minutes ago (more than one and a half intervals): measured only up to it, nothing"
      f" running ({stopped['Gama']['measured']})")
clear()
# The *Arr task's hourly samples, then the loop switched on: the step from the task's last sample to the loop's first is
# judged as today (twice the larger interval); the card says both intervals.
for moment in ("2026-11-02 08:00:00", "2026-11-02 09:00:00"):
    sample(moment)
watched(at(-30), at(0))
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(rows(h)["Gama"]["measured"] == 7200 and h.get("intervals") == [60, 3600],
      f"the task's samples, then the loop's: measured as before, both intervals told ({rows(h)['Gama']['measured']}, {h.get('intervals')})")
clear()
check(stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00").get("intervals") == [], "nothing measured: no interval")

print()
print("--- I) samples inside a loop sample's stretch: two app processes on one database, a stray sample ---")


def a_loop(shift: float) -> None:
    """One loop's samples `shift` minutes after 10:00: fine to 10:09, Alfa left out (failing since 10:09:30) from the
    round at 10:10 to the one at 11:09 - a new sample every ten minutes - and fine again from 11:10 to 11:20."""
    watched(at(shift), at(shift + 9))
    for start in range(10, 70, 10):
        watched(at(start + shift), at(start + shift + 9), (1, at(9.5), at(start + shift)))
    watched(at(70 + shift), at(80 + shift))


clear()
a_loop(0)
a_loop(20 / 60)        # the same app started twice on one database: its loop 20 s later
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check((rows(h)["Alfa"]["outages"], rows(h)["Alfa"]["longest"], rows(h)["Alfa"]["measured"], h["intervals"]) == (1, 3630, 4820, [60]),
      f"two loops 20 s apart: each sample is left out up to its own last round - one outage of an hour and half a minute,"
      f" the time measured once ({rows(h)['Alfa']['outages']}, {rows(h)['Alfa']['longest']}, {rows(h)['Alfa']['measured']})")
clear()
watched(at(0), at(9))
watched(at(10), at(39), (1, at(9.5), at(30)))
sample(at(20), (1, at(9.5), at(19)))            # the task's sample (an hour) inside the loop's stretch
watched(at(40), at(50))
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check((rows(h)["Alfa"]["outages"], rows(h)["Alfa"]["longest"], rows(h)["Alfa"]["measured"], h["intervals"]) == (1, 1830, 3000, [60]),
      f"a task's sample inside the loop's stretch: the outage to the round that saw it fine (30.5 min), measured every"
      f" minute ({rows(h)['Alfa']['outages']}, {rows(h)['Alfa']['longest']}, {rows(h)['Alfa']['measured']}, {h['intervals']})")
clear()
watched(at(0), at(50))
sample(at(12), interval=10 * MINUTE)            # a page's sample (ten minutes) inside the loop's stretch
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(rows(h)["Gama"]["measured"] == 3000 and h["intervals"] == [60],
      f"a page's sample inside the loop's stretch does not cut it: the whole stretch measured, every minute"
      f" ({rows(h)['Gama']['measured']}, {h['intervals']})")
clear()
with db.connect() as conn:          # a sample of an early build: no interval stored
    conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id, enabled_ids) VALUES (?, 0, ',1,2,3,')", (at(0),))
sample(at(60))
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(rows(h)["Gama"]["measured"] == 3600 and h["intervals"] == [3600],
      f"a sample without an interval measures by the other one and tells no interval of 0 s ({h['intervals']})")
clear()
sample("2026-11-02 08:00:00", enabled=(), interval=10 * MINUTE)     # nobody there: nothing measured
sample("2026-11-02 08:10:00", enabled=(), interval=10 * MINUTE)
sample("2026-11-02 09:00:00")
sample("2026-11-02 10:00:00")
h = stats.prowlarr_availability(ONE_DAY, now="2026-11-10 00:00:00")
check(rows(h)["Gama"]["measured"] == 3600 and h["intervals"] == [3600],
      f"the interval of samples nobody was measured between is not told ({h['intervals']})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
