# -*- coding: utf-8 -*-
r"""Bazarr: subtitle statistics over the table `bazarr_event` (stats/bazarr.py).

The fourteen cards of the Bazarr tab compute from one pick of the period's events. Exact numbers are guarded over a
small history of its own:

A) the period's numbers, by day, day × hour, how the subtitles were acquired;
B) languages and providers (and how many of a provider's subtitles were replaced later);
C) the match score, upgrades paired with the previous subtitles, subtitles acquired repeatedly;
D) from the file's import to the first subtitles (the import is in Sonarr's and Radarr's history);
E) the whole history and records without a date, an empty period, the app's zone;
F) a file that was in the library before Bazarr started does not distort the speed;
G) an upgrade's improvement is measured against the last subtitles that had a score;
H) events that brought no subtitles are only counted - they are not loaded into memory;
I) the speed is measured after every import of a file - after a replacement by a better version too.

"Acquired subtitles" are the actions 1 (downloaded), 2 (manual pick), 3 (upgrade), 4 (uploaded), 6 (translated) and
7 (extracted from the file); syncing (5) and deleting (0) do not belong among them.

Run:
    .venv/bin/python tests/test_bazarr_stats.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bazarr-test.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402
from jellyscope.arr import bazarr  # noqa: E402
from jellyscope.stats import bazarr as bazarr_stats  # noqa: E402

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


counter = 0


def insert(date: str | None, action: int, item: int, language: str = "cs", provider: str = "", score: float | None = None,
           kind: str = "episode", hi: int = 0, forced: int = 0) -> None:
    """A subtitle event. Episodes 101-104 are "Series A" S01E01-E04, the movie is "Movie B" (id 7)."""
    global counter
    counter += 1
    row = {"fingerprint": f"{counter:040d}", "kind": kind, "title": "Series A" if kind == "episode" else "Movie B",
           "episode": f"S01E{item - 100:02d}" if kind == "episode" else "", "title_id": 1 if kind == "episode" else item,
           "item_id": item, "action": action, "language": language, "hi": hi, "forced": forced, "provider": provider,
           "score": score, "happened_at": date, "time_exact": 1 if date else 0, "synced_at": "2026-11-14 00:00:00"}
    with db.connect() as conn:
        conn.execute(f"INSERT INTO bazarr_event ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        conn.commit()


def add_import(app: str, item: int, date: str) -> None:
    """A file's import into the library in Sonarr's or Radarr's history."""
    global counter
    counter += 1
    with db.connect() as conn:
        conn.execute(f"INSERT INTO {app}_event (arr_id, event_type, happened_at, title, item_id, title_id) VALUES (?, ?, ?, 'x', ?, 1)",
                     (counter, "downloadFolderImported", date, item))
        conn.commit()


set_zone("UTC")
# Series A, S01E01: Czech subtitles downloaded and replaced by better ones the next day; English ones; a sync.
insert("2026-11-02 10:00:00", 1, 101, "cs", "gestdown", 80.0)
insert("2026-11-03 10:00:00", 3, 101, "cs", "supersubtitles", 95.0)
insert("2026-11-02 10:05:00", 1, 101, "en", "gestdown", 90.0)
insert("2026-11-04 09:00:00", 5, 101, "cs")
# S01E02: a manual pick, a delete, one's own file.
insert("2026-11-05 20:30:00", 2, 102, "cs", "yifysubtitles", 69.99)
insert("2026-11-05 21:00:00", 0, 102, "cs")
insert("2026-11-06 08:00:00", 4, 102, "cs", "manual", 100.0)
# Movie B: downloaded for the hearing impaired, translated, forced extracted from the file; and an unknown action.
insert("2026-11-06 23:30:00", 1, 7, "en", "yifysubtitles", 70.0, kind="movie", hi=1)
insert("2026-11-07 00:15:00", 6, 7, "cs", kind="movie")
insert("2026-11-07 00:20:00", 7, 7, "en", "embeddedsubtitles", kind="movie", forced=1)
insert("2026-11-08 12:00:00", 9, 7, "cs", kind="movie")
# S01E03: subtitles from October (before the period) replaced in the period. S01E04: downloaded, without an import in
# Sonarr's history.
insert("2026-10-20 08:00:00", 1, 103, "cs", "gestdown", 60.0)
insert("2026-11-09 08:00:00", 3, 103, "cs", "supersubtitles", 85.0)
insert("2026-11-08 10:00:00", 1, 104, "cs", "gestdown", 88.0)
# After the period and without a date (the time format could not be read).
insert("2026-11-12 08:00:00", 1, 104, "en", "gestdown", 75.0)
insert(None, 1, 105, "fr", "gestdown", 50.0)
add_import("sonarr", 101, "2026-11-02 09:00:00")
add_import("sonarr", 102, "2026-11-05 20:00:00")
add_import("sonarr", 102, "2026-11-05 20:20:00")          # the file replaced by a better one: the subtitles belong to this
add_import("sonarr", 7, "2026-11-06 23:00:00")            # Sonarr's episode with the same number as the movie - not to mix up
add_import("radarr", 7, "2026-11-01 00:00:00")
add_import("sonarr", 103, "2026-10-20 07:00:00")

PERIOD = stats.period_between("2026-11-01", "2026-11-10")

print("--- A) what was acquired ---")
c = stats.bazarr_numbers(PERIOD)
check(c == {"obtained_count": 10, "automatic": 4, "manual": 2, "upgrade_count": 2, "sync_count": 1, "deleted": 1},
      f"the period's numbers: acquired subtitles and of them automatic, manual and by upgrade; syncs and deletes separately ({c})")
d = {r["day"]: (r["episodes"], r["movies"]) for r in stats.bazarr_by_day(PERIOD)}
check(d == {"2026-11-02": (2, 0), "2026-11-03": (1, 0), "2026-11-05": (1, 0), "2026-11-06": (1, 1), "2026-11-07": (0, 2),
            "2026-11-08": (1, 0), "2026-11-09": (1, 0)}, f"by day: episodes and movies separately, only days with acquired subtitles ({d})")
h = stats.bazarr_heatmap(PERIOD)
check(len(h) == 7 and all(len(r) == 24 for r in h) and sum(map(sum, h)) == 10 and h[0][10] == 2 and h[4][23] == 1 and h[5][0] == 2
      and h[6][10] == 1, "day × hour: Monday 10 h twice, Friday 23 h, Saturday 0 h twice, Sunday 10 h")
actions = {r["action"]: r["count"] for r in stats.bazarr_actions(PERIOD)}
check(actions == {"grabbed": 4, "manual": 1, "upgrade": 2, "uploaded": 1, "translated": 1, "extracted": 1, "synced": 1,
                  "deleted": 1, "other": 1} and [r["action"] for r in stats.bazarr_actions(PERIOD)][:2] == ["grabbed", "manual"],
      f"how the subtitles were acquired: all actions, an unknown code as \"other\" ({actions})")

check(bazarr_stats.ACTIONS == bazarr.ACTIONS and bazarr_stats.ACQUIRED == bazarr.ACQUIRED,
      "the statistics know the same actions as the client (the statistics package does not import the app, so it has them copied)")

print()
print("--- B) languages and providers ---")
j = stats.bazarr_languages(PERIOD)
check([(r["language"], r["count"], r["share"], r["hi"], r["forced"], r["score"]) for r in j]
      == [("cs", 7, 70.0, 0, 0, 86.3), ("en", 3, 30.0, 1, 1, 80.0)],
      f"languages: count, share, hearing impaired, forced, the mean score ({j})")
p = stats.bazarr_providers(PERIOD)
check([(r["provider"], r["count"], r["score"], r["replaced"]) for r in p]
      == [("gestdown", 3, 86.0, 2), ("supersubtitles", 2, 90.0, 0), ("yifysubtitles", 2, 70.0, 0), ("", 1, None, 0),
          ("embeddedsubtitles", 1, None, 0), ("manual", 1, 100.0, 0)],
      f"providers: count, the mean score and how many of their subtitles were replaced later ({[(r['provider'], r['count'], r['score'], r['replaced']) for r in p]})")
check(sum(r["share"] for r in p) == 100.0 and p[0]["share"] == 30.0, "the providers' shares make a hundred percent")

print()
print("--- C) how good they are ---")
s = stats.bazarr_scores(PERIOD)
check(s == {"median": 86.5, "count": 8, "buckets": {"under_70": 1, "70": 1, "80": 3, "90": 3}},
      f"score: the median and the distribution; the bounds 70, 80 and 90 belong to the higher bucket, a record without a score does not count ({s})")
u = stats.bazarr_upgrades(PERIOD)
check(u == {"count": 2, "paired": 2, "improvements": 20.0},
      f"upgrades: paired with the previous subtitles of the same item and language (from before the period too), the median improvement ({u})")
m = stats.bazarr_changed(PERIOD, 10)
check([(r["kind"], r["title"], r["episode"], r["title_id"], r["language"], r["count"]) for r in m]
      == [("episode", "Series A", "S01E01", 1, "cs", 2), ("episode", "Series A", "S01E02", 1, "cs", 2)]
      and len(stats.bazarr_changed(PERIOD, 1)) == 1,
      f"subtitles of the same language acquired repeatedly; another language or flag (hearing impaired, forced) is not summed ({m})")

print()
print("--- D) from the file to the subtitles ---")
r = stats.bazarr_speed(PERIOD)
check((r["count"], r["not_imported"], r["before_bazarr"], r["median"], r["p90"]) == (5, 1, 0, 3900, 518220),
      f"the first subtitles in a language after the file's import minus that import; without an import separately ({r})")
check(r["buckets"] == {"10m": 1, "1h": 1, "1d": 1, "7d": 2, "longer": 0}, f"buckets up to 10 minutes, an hour, a day, a week ({r['buckets']})")

print()
print("--- E) the whole history, an empty period, the zone ---")
check(stats.bazarr_first() == "2026-10-20 08:00:00", f"the first event ({stats.bazarr_first()})")
WHOLE = stats.whole_history_period(first=stats.bazarr_first())
whole = stats.bazarr_numbers(WHOLE)
check(whole["obtained_count"] == 13 and sum(x["episodes"] + x["movies"] for x in stats.bazarr_by_day(WHOLE)) == 12
      and "fr" in {x["language"] for x in stats.bazarr_languages(WHOLE)} and sum(map(sum, stats.bazarr_heatmap(WHOLE))) == 12,
      f"a record without a date counts only into the whole history and is not in the charts by day and hour ({whole})")
check("fr" not in {x["language"] for x in stats.bazarr_languages(PERIOD)}
      and stats.bazarr_numbers(stats.period_between("2026-10-01", "2026-11-30"))["obtained_count"] == 12,
      "a record without a date does not belong to a fixed period")
EMPTY = stats.period_between("2026-01-01", "2026-01-02")
check(stats.bazarr_numbers(EMPTY)["obtained_count"] == 0 and stats.bazarr_scores(EMPTY)["median"] is None
      and stats.bazarr_upgrades(EMPTY)["improvements"] is None and stats.bazarr_speed(EMPTY)["median"] is None
      and stats.bazarr_by_day(EMPTY) == [] and stats.bazarr_languages(EMPTY) == [] and stats.bazarr_changed(EMPTY, 10) == [],
      "an empty period: None, not zero; empty lists")
analysis = stats.bazarr_analysis(PERIOD)
check(stats.bazarr_numbers(PERIOD, analysis=analysis) == c and stats.bazarr_languages(PERIOD, analysis=analysis) == j
      and stats.bazarr_speed(PERIOD, analysis=analysis) == r and stats.bazarr_upgrades(PERIOD, analysis=analysis) == u,
      "with a shared analysis the same comes out (the page picks the events once)")
set_zone("Europe/Prague")
prague = {x["day"]: (x["episodes"], x["movies"]) for x in stats.bazarr_by_day(stats.period_between("2026-11-01", "2026-11-10"))}
check(prague.get("2026-11-07") == (0, 3) and "2026-11-06" in prague and prague["2026-11-06"] == (1, 0),
      f"days are counted in the app's zone: half past eleven at night UTC is the next day in Prague already ({prague.get('2026-11-06')}, {prague.get('2026-11-07')})")


print()
print("--- F) a file older than Bazarr ---")
set_zone("UTC")
# Bazarr's first event is from 20 October. Episode 201 was in the library already on 1 October: it got subtitles at the
# first bulk search, not "two months after the file". Episode 202 came while Bazarr was running.
add_import("sonarr", 201, "2026-10-01 00:00:00")
insert("2026-12-02 10:00:00", 1, 201, "cs", "gestdown", 80.0)
add_import("sonarr", 202, "2026-12-02 09:00:00")
insert("2026-12-02 09:30:00", 1, 202, "cs", "gestdown", 80.0)
# Episode 203: an old file replaced by a better one while Bazarr was already running - it counts from the replacement.
add_import("sonarr", 203, "2026-09-01 00:00:00")
add_import("sonarr", 203, "2026-12-03 08:00:00")
insert("2026-12-03 08:10:00", 1, 203, "cs", "gestdown", 80.0)
f = stats.bazarr_speed(stats.period_between("2026-12-01", "2026-12-05"))
check((f["count"], f["before_bazarr"], f["not_imported"], f["median"]) == (2, 1, 0, 1200) and f["buckets"]["10m"] == 1
      and f["buckets"]["1h"] == 1,
      f"a file imported before Bazarr's first event does not count into the speed and the card knows how many ({f})")

print()
print("--- G) an upgrade after subtitles without a score ---")
# Downloaded subtitles (60 %), then translated ones (they have no score), then an upgrade (85 %). The upgrade replaced the
# translated ones - but how much the match improved can be said only against the last ones that had a score.
insert("2026-12-10 08:00:00", 1, 301, "cs", "gestdown", 60.0)
insert("2026-12-11 08:00:00", 6, 301, "cs")
insert("2026-12-12 08:00:00", 3, 301, "cs", "supersubtitles", 85.0)
G = stats.period_between("2026-12-10", "2026-12-15")
check(stats.bazarr_upgrades(G) == {"count": 1, "paired": 1, "improvements": 25.0},
      f"the improvement against the last subtitles with a score, not against the translated ones without it ({stats.bazarr_upgrades(G)})")
check({r["provider"]: r["replaced"] for r in stats.bazarr_providers(G)} == {"gestdown": 0, "supersubtitles": 0, "": 1},
      f"\"replaced later\" is still credited to the one whose subtitles the upgrade really replaced ({[(r['provider'], r['replaced']) for r in stats.bazarr_providers(G)]})")

print()
print("--- H) what brought no subtitles is only counted ---")
# Syncing the timing tends to come with every downloaded subtitles - as many rows as acquired ones. The cards need only
# their count, so they are not loaded into memory (the analysis is half the size then).
for i in range(40):
    insert(f"2026-12-20 10:{i:02d}:00", 5, 400 + i, "cs")
insert("2026-12-20 12:00:00", 1, 400, "cs", "gestdown", 80.0)
insert("2026-12-20 13:00:00", 0, 400, "cs")
insert("2026-12-20 14:00:00", 9, 400, "cs")
H = stats.period_between("2026-12-20", "2026-12-21")
analysis_h = stats.bazarr_analysis(H)
loaded = [u for value in analysis_h.values() if isinstance(value, list) for u in value if isinstance(u, dict) and "action" in u]
check(len(analysis_h["obtained"]) == 1 and loaded and all(u["action"] in bazarr.ACQUIRED for u in loaded),
      f"the analysis holds only rows of acquired subtitles ({len(analysis_h['obtained'])} acquired of 43 events; other actions are not in memory)")
check(stats.bazarr_numbers(H) == {"obtained_count": 1, "automatic": 1, "manual": 0, "upgrade_count": 0, "sync_count": 40, "deleted": 1}
      and {r["action"]: r["count"] for r in stats.bazarr_actions(H)} == {"grabbed": 1, "synced": 40, "deleted": 1, "other": 1},
      f"the counts of syncs, deletes and unknown actions still fit ({stats.bazarr_numbers(H)})")
undated_before = stats.bazarr_numbers(stats.whole_history_period(first=stats.bazarr_first()))["sync_count"]
insert(None, 5, 500, "cs")
check(stats.bazarr_numbers(stats.whole_history_period(first=stats.bazarr_first()))["sync_count"] == undated_before + 1
      and stats.bazarr_numbers(H)["sync_count"] == 40, "a record without a date counts only into the whole history here too")

print()
print("--- I) the speed after every import of a file ---")
# Episode 601: a file and subtitles five minutes after it. Two days later Sonarr replaces the file by a better version and
# Bazarr downloads subtitles for it again, within half an hour. Both are "how long after the file".
add_import("sonarr", 601, "2026-12-25 10:00:00")
insert("2026-12-25 10:05:00", 1, 601, "cs", "gestdown", 80.0)
add_import("sonarr", 601, "2026-12-27 10:00:00")
insert("2026-12-27 10:30:00", 1, 601, "cs", "gestdown", 85.0)
# Further subtitles for the same file (an upgrade, hearing impaired) are not first any more; English after the
# replacement is first.
insert("2026-12-27 12:00:00", 3, 601, "cs", "supersubtitles", 95.0)
insert("2026-12-27 12:30:00", 1, 601, "cs", "gestdown", 70.0, hi=1)
insert("2026-12-27 11:00:00", 1, 601, "en", "gestdown", 90.0)
# Episode 602: the file replaced, but Bazarr did not find new subtitles - the second import has nothing to pair with.
add_import("sonarr", 602, "2026-12-25 08:00:00")
insert("2026-12-25 08:10:00", 1, 602, "cs", "gestdown", 80.0)
add_import("sonarr", 602, "2026-12-26 08:00:00")
# Episode 603: subtitles from the first sync without a date - nobody knows whether they were before the import or after.
insert(None, 1, 603, "cs", "gestdown", 80.0)
add_import("sonarr", 603, "2026-12-26 09:00:00")
insert("2026-12-26 09:10:00", 1, 603, "cs", "gestdown", 80.0)
# Episode 604: the file imported again (a fresh library scan), the old subtitles stayed and Bazarr searched for nothing.
# An upgrade of the subtitles a day and a half later is not "the first subtitles after the import" - it would drag the
# numbers to days otherwise.
add_import("sonarr", 604, "2026-12-25 09:00:00")
insert("2026-12-25 09:05:00", 1, 604, "cs", "gestdown", 70.0)
add_import("sonarr", 604, "2026-12-26 09:00:00")
insert("2026-12-27 15:00:00", 3, 604, "cs", "supersubtitles", 90.0)
# Movie 77 through Radarr: 20 minutes after the first file, 40 minutes after the replaced one. Sonarr has an episode with
# the same number imported right before the second subtitles - not to mix up.
add_import("radarr", 77, "2026-12-25 12:00:00")
insert("2026-12-25 12:20:00", 1, 77, "en", "gestdown", 80.0, kind="movie")
add_import("radarr", 77, "2026-12-26 12:00:00")
add_import("sonarr", 77, "2026-12-26 12:30:00")
insert("2026-12-26 12:40:00", 1, 77, "en", "gestdown", 85.0, kind="movie")
I = stats.period_between("2026-12-25", "2026-12-28")
i = stats.bazarr_speed(I)
check((i["count"], i["median"], i["not_imported"], i["before_bazarr"]) == (7, 1200, 0, 0)
      and i["buckets"] == {"10m": 3, "1h": 4, "1d": 0, "7d": 0, "longer": 0},
      f"after a file's replacement the new subtitles count too (episode and movie); an upgrade after a repeated import not ({i})")
whole_speed = stats.bazarr_speed(stats.whole_history_period(first=stats.bazarr_first()))
by_periods = sum(stats.bazarr_speed(o)["count"] for o in (PERIOD, stats.period_between("2026-12-01", "2026-12-05"), I))
check(whole_speed["count"] == by_periods == 14, f"the whole history gives the same as the sum of the periods ({whole_speed['count']}, {by_periods})")
# Only the speed card needs the files' imports: the shared analysis (which all cards read) does not ask Sonarr's and
# Radarr's history.
sql_queries: list[str] = []
original_query = db.query_all


def _listen(sql, *a, **k):
    sql_queries.append(sql)
    return original_query(sql, *a, **k)


db.query_all = _listen
try:
    analysis_i = stats.bazarr_analysis(I)
    stats.bazarr_numbers(I, analysis=analysis_i)
    stats.bazarr_upgrades(I, analysis=analysis_i)
    imports_in_analysis = sum("_event WHERE event_type LIKE" in q for q in sql_queries)
    stats.bazarr_speed(I, analysis=analysis_i)
    imports_total = sum("_event WHERE event_type LIKE" in q for q in sql_queries)
finally:
    db.query_all = original_query
check(imports_in_analysis == 0 and imports_total >= 1,
      f"only the speed card asks for the files' imports, not the shared analysis ({imports_in_analysis}, {imports_total})")
only_second = stats.bazarr_speed(stats.period_between("2026-12-27", "2026-12-28"))
check((only_second["count"], only_second["median"]) == (2, 2700),
      f"a period with only the replacement: subtitles are measured from the replacement, not from the first file ({only_second['count']}, {only_second['median']})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
