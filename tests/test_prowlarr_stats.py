# -*- coding: utf-8 -*-
r"""Prowlarr: statistics over the hourly sums and the list of grabbed releases.

Queries to the indexers are stored as sums by hour (`prowlarr_hour`), grabbed releases one by one
(`prowlarr_grab`). Guarded:

A) the period's numbers, by day, day of the week × hour, who asks;
B) the indexers' performance: failed queries (a share of the count of queries standing beside it), failed sign-ins
   separately, the mean response as a share of sums (not a mean of means), how many searches found something, an
   indexer without a name;
C) grabbed releases: failures and the time only for those Prowlarr downloaded itself;
D) the app's zone (the hour before midnight UTC falls into the next day), an empty period, the whole history, the
   page's shared analysis.

The rows are inserted straight into the tables - tests/test_prowlarr_sync.py guards the sync.

Spec: docs/superpowers/specs/2026-11-15-prowlarr-design.md

Run:
    .venv/bin/python tests/test_prowlarr_stats.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-test.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def set_zone(name: str) -> None:
    db.set_setting("app_timezone", name)
    db.forget_settings()


def add_hour(when: str, indexer: int, app: str, kind: str, count: int, failed: int = 0, response_sum: int = 0,
             response_count: int = 0, with_result: int = 0) -> None:
    """The sum of one hour (UTC) of one indexer, app and kind."""
    with db.connect() as conn:
        conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (when, indexer, app, kind, count, failed, response_sum, response_count, with_result))


def add_grab(number: int, when: str, indexer: int, app: str, title: str, method: str = "redirect", succeeded: int = 1,
             response: int | None = None) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO prowlarr_grab (id, happened_at, indexer_id, app, title, method, succeeded, response, client)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')", (number, when, indexer, app, title, method, succeeded, response))


def add_indexer(number: int, name: str, protocol: str = "usenet", removed: int = 0) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at) VALUES (?, ?, ?, 'private', 1, ?, ?)",
                     (number, name, protocol, removed, "2026-11-10 00:00:00"))


def close(a: float | None, b: float) -> bool:
    return a is not None and abs(a - b) < 1e-9


set_zone("UTC")
add_indexer(1, "Alfa")
add_indexer(2, "Beta", "torrent")
add_indexer(3, "Gama", removed=1)
# Indexer 1 (Alfa): searches and RSS from Sonarr, searches from Radarr an hour before midnight UTC.
add_hour("2026-11-02 10:00:00", 1, "Sonarr", "query", 10, 2, 800, 8, 6)
add_hour("2026-11-02 10:00:00", 1, "Sonarr", "rss", 4, 0, 400, 4, 4)
add_hour("2026-11-02 23:00:00", 1, "Radarr", "query", 5, 1, 1200, 4, 2)
# Indexer 2 (Beta): searches (one from the cache - five responses of six), RSS without a known app (all failed), failed
# sign-ins.
add_hour("2026-11-03 08:00:00", 2, "Radarr", "query", 6, 0, 3000, 5, 3)
add_hour("2026-11-03 08:00:00", 2, "", "rss", 3, 3, 0, 0, 0)
add_hour("2026-11-03 08:00:00", 2, "Sonarr", "auth", 2, 2, 0, 0, 0)
# Indexer 7 has no name (it is not in the indexers' table): a manual search in Prowlarr.
add_hour("2026-11-04 12:00:00", 7, "Prowlarr", "query", 1, 0, 50, 1, 1)
# Outside the period.
add_hour("2026-10-20 10:00:00", 1, "Sonarr", "query", 100, 50, 5000, 50, 10)
add_hour("2026-11-12 10:00:00", 1, "Sonarr", "query", 7, 7, 0, 0, 0)
# Grabbed releases: from Alfa only redirected (usenet), from Beta downloaded by Prowlarr (one failed), one from an unknown
# indexer.
add_grab(1, "2026-11-02 10:30:00", 1, "Sonarr", "Series.S01E01")
add_grab(2, "2026-11-02 23:30:00", 1, "Radarr", "Movie.A")
add_grab(3, "2026-11-03 08:10:00", 2, "Radarr", "Movie.B", "proxy", 1, 40)
add_grab(4, "2026-11-03 08:20:00", 2, "Radarr", "Movie.C", "proxy", 0)
add_grab(5, "2026-11-03 08:30:00", 2, "Sonarr", "Series.S01E02", "proxy", 1, 60)
add_grab(6, "2026-11-05 09:00:00", 9, "", "Unknown", "")
add_grab(7, "2026-10-01 08:00:00", 1, "Sonarr", "Old")
add_grab(8, "2026-11-11 00:30:00", 1, "Sonarr", "After.period")
PERIOD = stats.period_between("2026-11-01", "2026-11-10")

print("--- A) numbers, days, hours, who asks ---")
c = stats.prowlarr_numbers(PERIOD)
check((c["searches"], c["rss"], c["failed_count"]) == (22, 7, 6) and close(c["failure_share"], 6 / 29) and c["response"] == 248,
      f"the period's numbers: searches, RSS, failures and the mean response (a share of sums, not a mean of means = 230) ({c})")
by_day = {x["day"]: (x["searches"], x["rss"], x["failed_count"]) for x in stats.prowlarr_by_day(PERIOD)}
check(by_day == {"2026-11-02": (15, 4, 3), "2026-11-03": (6, 3, 3), "2026-11-04": (1, 0, 0)},
      f"by day, only days when something was ({by_day})")
h = stats.prowlarr_heatmap(PERIOD)
check(len(h) == 7 and all(len(r) == 24 for r in h) and (h[0][10], h[0][23], h[1][8], h[2][12]) == (10, 5, 6, 1) and sum(map(sum, h)) == 22,
      "day of the week × hour: only searches, row 0 = Monday")
who = [(x["app"], x["searches"], x["rss"], x["grabs"]) for x in stats.prowlarr_who(PERIOD)]
check(who == [("Sonarr", 10, 4, 2), ("Radarr", 11, 0, 3), ("", 0, 3, 1), ("Prowlarr", 1, 0, 0)],
      f"who asks: an unknown app is a row of its own, a sign-in does not count among the queries ({who})")

print()
print("--- B) the indexers' performance ---")
ix = {x["indexer_id"]: x for x in stats.prowlarr_indexers(PERIOD)}
check([x["indexer_id"] for x in stats.prowlarr_indexers(PERIOD)] == [1, 2, 7, 9],
      f"indexers from the most queries; also the one with only a grabbed release in the period ({list(ix)})")
a = ix[1]
check((a["name"], a["query_count"], a["failed_count"], a["response"], a["grabs"], a["login_failed"]) == ("Alfa", 19, 3, 150, 2, 0)
      and close(a["failure_share"], 3 / 19) and close(a["found_share"], 8 / 12),
      f"Alfa: queries (searches and RSS), the response 2400 / 16, found something in 8 of 12 answered searches ({a})")
b = ix[2]
check((b["name"], b["query_count"], b["failed_count"], b["response"], b["grabs"], b["login_failed"]) == ("Beta", 9, 3, 600, 3, 2)
      and close(b["failure_share"], 3 / 9) and close(b["found_share"], 0.5),
      f"Beta: 3 of 9 queries failed - the share belongs to the count of queries beside it; failed sign-ins (2) stand apart ({b})")
check((ix[7]["name"], ix[7]["query_count"], ix[7]["response"]) == (None, 1, 50) and close(ix[7]["failure_share"], 0.0)
      and close(ix[7]["found_share"], 1.0)
      and (ix[9]["name"], ix[9]["query_count"], ix[9]["failure_share"], ix[9]["response"], ix[9]["found_share"], ix[9]["grabs"])
      == (None, 0, None, None, None, 1),
      "an indexer without a name has name None; where there is nothing to compute from, it is None (not zero)")
names = stats.prowlarr_indexer_names()
check(names == {1: {"name": "Alfa", "protocol": "usenet", "removed": False}, 2: {"name": "Beta", "protocol": "torrent", "removed": False},
                3: {"name": "Gama", "protocol": "usenet", "removed": True}}, f"the indexers' names, the removed ones too ({names})")

print()
print("--- C) grabbed releases ---")
v = stats.prowlarr_grabbed(PERIOD)
check(v == {"grab_count": 6, "being_grabbed": 3, "redirected": 2, "failed_count": 1, "response": 50},
      f"grabbed: failures and the time only from those Prowlarr downloaded itself ({v})")
vi = [(x["indexer_id"], x["name"], x["grab_count"], x["being_grabbed"], x["failed_count"]) for x in stats.prowlarr_grabbed_indexers(PERIOD)]
shares = [x["share"] for x in stats.prowlarr_grabbed_indexers(PERIOD)]
check(vi == [(2, "Beta", 3, 3, 1), (1, "Alfa", 2, 0, None), (9, None, 1, 0, None)] and close(shares[0], 0.5) and close(shares[1], 1 / 3),
      f"by indexer: for an indexer where Prowlarr only redirected the failures are None (unknown) ({vi})")
latest = stats.prowlarr_grabbed_latest(PERIOD, limit=3)
check([(x["title"], x["indexer_id"], x["name"], x["app"], x["succeeded"], x["method"]) for x in latest]
      == [("Unknown", 9, None, "", True, ""), ("Series.S01E02", 2, "Beta", "Sonarr", True, "proxy"), ("Movie.C", 2, "Beta", "Radarr", False, "proxy")]
      and latest[0]["happened_at"] == "2026-11-05 09:00:00", f"the latest grabbed from the newest, with a limit ({[x['title'] for x in latest]})")
check(len(stats.prowlarr_grabbed_latest(PERIOD, limit=None)) == 6, "without a limit all of the period")

print()
print("--- D) the zone, an empty period, the whole history, the shared analysis ---")
set_zone("Europe/Prague")
PRAGUE = stats.period_between("2026-11-01", "2026-11-10")
by_day = {x["day"]: (x["searches"], x["rss"], x["failed_count"]) for x in stats.prowlarr_by_day(PRAGUE)}
h = stats.prowlarr_heatmap(PRAGUE)
check(by_day == {"2026-11-02": (10, 4, 2), "2026-11-03": (11, 3, 4), "2026-11-04": (1, 0, 0)}
      and (h[0][11], h[1][0], h[1][9], h[2][13]) == (10, 5, 6, 1),
      f"in the zone Europe/Prague the hour 23:00 UTC falls into the next day and the hours shift ({by_day})")
check(stats.prowlarr_numbers(PRAGUE)["searches"] == 22 and stats.prowlarr_grabbed(PRAGUE)["grab_count"] == 6
      and stats.prowlarr_grabbed(stats.period_between("2026-11-01", "2026-11-11"))["grab_count"] == 7,
      "the period's bounds are in the app's zone: a release of 11. 11. 00:30 UTC is 11. 11. 01:30 in Prague")
set_zone("UTC")
EMPTY = stats.period_between("2026-01-01", "2026-01-02")
check(stats.prowlarr_numbers(EMPTY) == {"searches": 0, "rss": 0, "failed_count": 0, "failure_share": None, "response": None}
      and stats.prowlarr_by_day(EMPTY) == [] and sum(map(sum, stats.prowlarr_heatmap(EMPTY))) == 0 and stats.prowlarr_who(EMPTY) == []
      and stats.prowlarr_indexers(EMPTY) == []
      and stats.prowlarr_grabbed(EMPTY) == {"grab_count": 0, "being_grabbed": 0, "redirected": 0, "failed_count": 0, "response": None}
      and stats.prowlarr_grabbed_indexers(EMPTY) == [] and stats.prowlarr_grabbed_latest(EMPTY, limit=5) == [],
      "an empty period: zeros, None and empty lists")
check(stats.prowlarr_first() == "2026-10-01 08:00:00", f"the first event: the oldest of both tables ({stats.prowlarr_first()})")
WHOLE = stats.whole_history_period(first=stats.prowlarr_first())
check(stats.prowlarr_numbers(WHOLE)["searches"] == 22 + 100 + 7 and stats.prowlarr_grabbed(WHOLE)["grab_count"] == 8, "the whole history")
r = stats.prowlarr_analysis(PERIOD)
with db.connect() as conn:
    conn.execute("DELETE FROM prowlarr_hour")
    conn.execute("DELETE FROM prowlarr_grab")
check(stats.prowlarr_numbers(PERIOD, analysis=r) == c and [x["indexer_id"] for x in stats.prowlarr_indexers(PERIOD, analysis=r)] == [1, 2, 7, 9]
      and stats.prowlarr_grabbed(PERIOD, analysis=r) == v and len(stats.prowlarr_who(PERIOD, analysis=r)) == 4
      and len(stats.prowlarr_by_day(PERIOD, analysis=r)) == 3
      and sum(map(sum, stats.prowlarr_heatmap(PERIOD, analysis=r))) == 22 and len(stats.prowlarr_grabbed_indexers(PERIOD, analysis=r)) == 3,
      "the shared analysis: the page asks the database once and the cards compute from it")
check(stats.prowlarr_first() == "" and stats.prowlarr_numbers(PERIOD)["searches"] == 0, "without data: the first event empty")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
