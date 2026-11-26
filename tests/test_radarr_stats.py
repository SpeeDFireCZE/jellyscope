# -*- coding: utf-8 -*-
r"""Radarr's statistics (stats/radarr.py) over a hand-made history with a known result.

The data (the period Sat 31. 10. - Mon 2. 11. 2026, the app's zone UTC):

    g1 grab      10-31 20:00  A  movie 11  WEBDL-720p   2 GB  RSS, NZBgeek, NTb, SAB, usenet
    i1 import    10-31 20:30  A  movie 11  WEBDL-720p   2 GB  the movie came out 10-31 18:00
    g2 grab      11-01 10:00  B  movie 11  WEBDL-1080p  4 GB  manual pick, DrunkenSlug, FLUX
    d1 delete    11-01 11:00     movie 11  WEBDL-720p         reason Upgrade
    i2 import    11-01 11:02  B  movie 11  WEBDL-1080p  4 GB  the movie came out 10-31 18:00
    g3 grab      11-01 12:00  C  movie 12  WEBDL-1080p  3 GB  search, NZBgeek, NTb, qBit, torrent
    f1 failure   11-01 13:00  C  movie 12
    i3 import    11-02 08:00  D  movie 13  Bluray-1080p 6 GB  the movie comes out only 11-03 (before the release)

What follows from it and the test guards:
- additions by day: three days with one import each, 1. 11. is an upgrade (i2 replaced the file deleted by d1 two
  minutes earlier), not a new movie;
- the download speed (grab -> import by download_id): 30 min and 62 min, the median 46 min (2760 s), p90 by linear
  interpolation 3528 s; i3 has no grab;
- since the release: only the movie's first import - i1 after 2.5 h (9000 s); i2 is an upgrade of the same movie
  (17 h after the release) and does not belong to "how soon the movie was added", i3 is before the release,
  separately (a finding of the final review: upgrades moved the median and the buckets later);
- sources: an import's indexer is from its grab, i3 without a grab is "–";
- upgrades: one, 720p -> 1080p, a manual pick brought it; all grabs in the period: two automatic (RSS, search), one
  manual;
- failures: one;
- indexer reliability: NZBgeek grabbed A (imported) and C (failed), DrunkenSlug B (imported); further hand-made grabs
  add a grab still downloading at the period's end, a Blackhole grab without download_id, a grab before the period
  and a failure of its own - each counts where the card says; a release grabbed again before it finished and a
  release written for two movies (several grabs, one download_id) are one download each.
Finally Review Focus 5: midnight in the app's zone - an import at 10-31 23:30 UTC belongs to Sunday 1. 11. in Prague
(UTC+1), hour 0.

Run:
    .venv/bin/python tests/test_radarr_stats.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "radarr-test.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402

GB = 1024 ** 3
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


def insert(arr_id: int, kind: str, date: str, download_id: str | None, item: int, quality: str = "",
           size: int | None = None, released: str | None = None, **columns: str) -> None:
    """One event of Radarr's history; `columns` are further columns of the table by their names."""
    row = {"arr_id": arr_id, "event_type": kind, "happened_at": date, "title": "Movie",
           "item_id": item, "title_id": 1, "quality": quality, "resolution": 1080 if "1080" in quality else 720,
           "size_bytes": size, "indexer": "", "release_group": "", "client": "", "protocol": "", "source": "",
           "reason": "", "download_id": download_id, "released_at": released, "cf_score": 0, **columns}
    with db.connect() as conn:
        conn.execute(f"INSERT INTO radarr_event ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                     tuple(row.values()))
        conn.commit()


set_zone("UTC")
insert(1, "grabbed", "2026-10-31 20:00:00", "A", 11, "WEBDL-720p", 2 * GB, source="Rss", indexer="NZBgeek",
       release_group="NTb", client="SAB", protocol="usenet")
insert(2, "downloadFolderImported", "2026-10-31 20:30:00", "A", 11, "WEBDL-720p", 2 * GB, released="2026-10-31 18:00:00")
insert(3, "grabbed", "2026-11-01 10:00:00", "B", 11, "WEBDL-1080p", 4 * GB, source="InteractiveSearch",
       indexer="DrunkenSlug", release_group="FLUX", client="SAB", protocol="usenet")
insert(4, "movieFileDeleted", "2026-11-01 11:00:00", None, 11, "WEBDL-720p", reason="Upgrade")
insert(5, "downloadFolderImported", "2026-11-01 11:02:00", "B", 11, "WEBDL-1080p", 4 * GB, released="2026-10-31 18:00:00")
insert(6, "grabbed", "2026-11-01 12:00:00", "C", 12, "WEBDL-1080p", 3 * GB, source="Search", indexer="NZBgeek",
       release_group="NTb", client="qBit", protocol="torrent")
insert(7, "downloadFailed", "2026-11-01 13:00:00", "C", 12)
insert(8, "downloadFolderImported", "2026-11-02 08:00:00", "D", 13, "Bluray-1080p", 6 * GB, released="2026-11-03 00:00:00")

PERIOD = stats.period_between("2026-10-31", "2026-11-02")

print("--- When Radarr imported a movie's file (for Bazarr's subtitle speed) ---")
check(stats.radarr_imports({11, 12, 13, 99}) == {11: ["2026-10-31 20:30:00", "2026-11-01 11:02:00"], 13: ["2026-11-02 08:00:00"]},
      "import times from the oldest; a movie without an import (12) and an unknown movie (99) are not in the result")
check(stats.radarr_imports([]) == {} and stats.radarr_imports(set()) == {}, "nothing without items")

print("--- What was added and when ---")
p = stats.radarr_additions(PERIOD)
check([(r["day"], r["count"], r["new_count"], r["upgrade_count"]) for r in p] ==
      [("2026-10-31", 1, 1, 0), ("2026-11-01", 1, 0, 1), ("2026-11-02", 1, 1, 0)],
      f"additions by day: new, upgrade, new ({[(r['day'], r['count'], r['new_count'], r['upgrade_count']) for r in p]})")
check([r["size_bytes"] for r in p] == [2 * GB, 4 * GB, 6 * GB], "the size imported per day")
h = stats.radarr_heatmap(PERIOD)
check(len(h) == 7 and all(len(r) == 24 for r in h), "heatmap 7 × 24 (as charts.heatmap)")
check(h[5][20] == 1 and h[6][11] == 1 and h[0][8] == 1 and sum(map(sum, h)) == 3,
      "heatmap: Sat 20 h, Sun 11 h, Mon 8 h (row 0 = Monday)")
n = stats.radarr_largest(PERIOD, 1)
check(len(n) == 1 and n[0]["size_bytes"] == 6 * GB and n[0]["quality"] == "Bluray-1080p" and "episode" not in n[0],
      "the largest imported file (a movie has no episode)")
check(len(stats.radarr_largest(PERIOD)) == 3, "the largest: only imports (three), the default limit 10")

print()
print("--- Speed ---")


def add_title(title_id: int, added: str | None) -> None:
    """A movie in Radarr's list with the time it was added to it (the column `added_at`)."""
    with db.connect() as conn:
        conn.execute("INSERT INTO radarr_title (title_id, name, added_at) VALUES (?, ?, ?)", (title_id, f"Title {title_id}", added))
        conn.commit()


# The movie (title 1) is in Radarr since January - it was waited for.
add_title(1, "2026-01-01 00:00:00")
r = stats.radarr_speed(PERIOD)
check(r["downloads"] == {"median": 2760, "p90": 3528, "count": 2}, f"grab -> import: the median and p90 ({r['downloads']})")
since = r["since_release"]
check(since["median"] == 9000 and since["count"] == 1 and since["before_release"] == 1 and since["not_counted"] == 0
      and since["unknown"] == 0,
      f"since the release: only the first import (the upgrade i2 does not count), before the release separately ({since})")
check(since["buckets"] == {"1h": 0, "6h": 1, "1d": 0, "7d": 0, "longer": 0}, f"since the release by bucket ({since['buckets']})")
# "Since the release" measures waiting - and one can wait only for what was in the app already when it came out.
# A movie or series added years after the release would otherwise drag the numbers to years (a 30 year old movie = 30
# years).
add_title(2, "2026-11-01 14:00:00")          # a thirty year old title added today
add_title(3, "2026-10-01 00:00:00")          # a new title, added before its release
add_title(4, None)                           # a title the app did not say when it was added
insert(20, "downloadFolderImported", "2026-11-01 15:00:00", "S", 21, "Bluray-1080p", 8 * GB, released="1996-05-01 00:00:00", title_id=2)
insert(21, "downloadFolderImported", "2026-11-02 12:00:00", "N", 31, "WEBDL-1080p", 3 * GB, released="2026-10-31 12:00:00", title_id=3)
insert(22, "downloadFolderImported", "2026-11-02 13:00:00", "U", 41, "WEBDL-1080p", 3 * GB, released="2026-11-02 12:00:00", title_id=4)
insert(23, "downloadFolderImported", "2026-11-02 14:00:00", "Z", 51, "WEBDL-1080p", 3 * GB, released="2026-11-02 12:00:00", title_id=99)
since = stats.radarr_speed(PERIOD)["since_release"]
check(since["count"] == 2 and since["buckets"] == {"1h": 0, "6h": 1, "1d": 0, "7d": 1, "longer": 0}
      and since["median"] == (9000 + 2 * 86400) / 2 and since["before_release"] == 1,
      f"since the release: only titles that were in the app when the movie came out ({since})")
# Review: "added after the release" and "we do not know when it was added" are two different things - and the card says
# them so.
check(since["not_counted"] == 1 and since["unknown"] == 2,
      f"added only after the release separately, titles without a time added (or no longer in the app) separately ({since['not_counted']}, {since['unknown']})")
with db.connect() as conn:
    conn.execute("DELETE FROM radarr_event WHERE arr_id >= 20")
    conn.commit()
empty = stats.radarr_speed(stats.period_between("2026-01-01", "2026-01-02"))
check(empty["downloads"] == {"median": None, "p90": None, "count": 0} and empty["since_release"]["median"] is None,
      "an empty period: None, not zero")

print()
print("--- Sources and quality ---")
sources = stats.radarr_sources(PERIOD)
check(set(sources) == {"indexers", "groups", "clients", "protocols", "qualities", "resolutions"}, "all groups of sources")
check([(x["name"], x["count"]) for x in sources["indexers"]] == [("–", 1), ("DrunkenSlug", 1), ("NZBgeek", 1)],
      f"an import's indexer = its grab's indexer, without a grab \"–\"; on a tie the larger first ({sources['indexers']})")
check([x["size_bytes"] for x in sources["indexers"]] == [6 * GB, 4 * GB, 2 * GB], "the size at a source = the imported files")
check([(x["name"], x["count"]) for x in sources["qualities"]] == [("Bluray-1080p", 1), ("WEBDL-1080p", 1), ("WEBDL-720p", 1)],
      f"the qualities of the imported files ({sources['qualities']})")
check({x["name"]: x["count"] for x in sources["protocols"]} == {"–": 1, "usenet": 2}, f"protocols ({sources['protocols']})")

print()
print("--- Upgrades and activity ---")
u = stats.radarr_upgrades(PERIOD)
check(u["count"] == 1 and u["item_count"] == 1 and u["per_item"] == 1, f"one upgrade of one movie ({u['count']}, {u['item_count']})")
check(u["transitions"] == [{"z": "WEBDL-720p", "to": "WEBDL-1080p", "count": 1}], f"the quality transition ({u['transitions']})")
# `titul_id` = the title's id in the app: by it the card finds the title in the library (stats.radarr_detail_links).
check(u["titles"] == [{"title": "Movie", "count": 1, "title_id": 1}], f"titles with upgrades ({u['titles']})")
check(u["how"] == {"manual": 1}, f"a manual pick brought the upgrade ({u['how']})")
check(u["how_all"] == {"automatic": 2, "manual": 1}, f"all grabs: 2 automatic, 1 manual ({u['how_all']})")
check(stats.radarr_failures(PERIOD) == 1, "failures: one")

print()
print("--- Indexer reliability: downloads of the period, their imports and failures by download_id ---")


def reliability(period) -> list[tuple]:
    """(name, downloads, imported, failed, unmatched, success) per indexer, in the function's order."""
    return [(x["name"], x["downloads"], x["imported"], x["failed"], x["unmatched"], x["success"])
            for x in stats.radarr_indexer_reliability(period)]


# From the data above: NZBgeek grabbed A (imported) and C (failed), DrunkenSlug grabbed B (imported).
check(reliability(PERIOD) == [("NZBgeek", 2, 1, 1, 0, 0.5), ("DrunkenSlug", 1, 1, 0, 0, 1.0)],
      f"a grab is imported or failed by its download_id; the most failures first ({reliability(PERIOD)})")
insert(40, "grabbed", "2026-11-02 23:50:00", "L", 16, "WEBDL-720p", GB, source="Rss", indexer="NZBgeek")
insert(41, "downloadFolderImported", "2026-11-03 00:05:00", "L", 16, "WEBDL-720p", GB)
insert(42, "grabbed", "2026-11-02 09:00:00", None, 17, "WEBDL-720p", GB, source="ReleasePush", indexer="Other")
insert(43, "downloadFolderImported", "2026-11-02 09:10:00", "Blackhole_y", 17, "WEBDL-720p", GB)
insert(44, "grabbed", "2026-10-30 10:00:00", "P", 18, "WEBDL-720p", GB, source="Rss", indexer="DrunkenSlug")
insert(45, "downloadFailed", "2026-10-31 01:00:00", "P", 18)
insert(46, "grabbed", "2026-11-02 11:00:00", "Q", 19, "WEBDL-720p", GB, source="Rss", indexer="Tracker")
insert(47, "downloadFailed", "2026-11-02 12:00:00", "Q", 19)
rows = reliability(PERIOD)
check(rows[1] == ("NZBgeek", 3, 1, 1, 0, 0.5),
      f"a grab still downloading at the period's end (imported only after it) is neither a success nor a failure ({rows[1]})")
check(("Other", 1, 0, 0, 1, None) in rows,
      f"a grab without download_id (Blackhole) cannot be matched and lowers no success rate ({rows})")
check(("DrunkenSlug", 1, 1, 0, 0, 1.0) in rows, "a grab before the period does not count, not even with its failure in the period")
check([r[0] for r in rows] == ["Tracker", "NZBgeek", "DrunkenSlug", "Other"],
      f"sorted by failures, then the lowest success rate; nothing finished comes last ({[r[0] for r in rows]})")
analysis, before = stats.radarr_events(PERIOD), stats.radarr_indexer_reliability(PERIOD)
insert(48, "grabbed", "2026-11-02 15:00:00", "S", 22, "WEBDL-720p", GB, source="Rss", indexer="Later")
from_page = stats.radarr_indexer_reliability(PERIOD, analysis=analysis)
check(from_page == before and from_page != stats.radarr_indexer_reliability(PERIOD),
      "it computes from the page's paired events (analysis=), not from the table again: a grab stored after them is not in it")
# The same download_id grabbed again after its failure is a download of its own (the outcome pairs with the last grab
# before it, as the sources pair it). A download is imported when it was imported - a failure written after the import
# (Radarr's "mark as failed" in the history) does not make it failed: failed = a failure and no import.
insert(60, "grabbed", "2026-12-01 10:00:00", "R", 20, "WEBDL-720p", GB, source="Rss", indexer="Again")
insert(61, "downloadFailed", "2026-12-01 11:00:00", "R", 20)
insert(62, "grabbed", "2026-12-02 10:00:00", "R", 20, "WEBDL-720p", GB, source="Search", indexer="Again")
insert(63, "downloadFolderImported", "2026-12-02 10:30:00", "R", 20, "WEBDL-720p", GB)
insert(64, "grabbed", "2026-12-03 10:00:00", "M", 21, "WEBDL-720p", GB, source="Rss", indexer="Marked")
insert(65, "downloadFolderImported", "2026-12-03 10:20:00", "M", 21, "WEBDL-720p", GB)
insert(66, "downloadFailed", "2026-12-03 12:00:00", "M", 21)
december = reliability(stats.period_between("2026-12-01", "2026-12-03"))
check(december == [("Again", 2, 1, 1, 0, 0.5), ("Marked", 1, 1, 0, 0, 1.0)],
      f"a repeated download_id after its failure is another download; a failure after the import leaves it imported ({december})")
check(stats.radarr_indexer_reliability(stats.period_between("2026-01-01", "2026-01-02")) == [], "an empty period: no rows")
# One release, several grabs with its download_id before any outcome: grabbed again (a manual pick of the same release
# while it still downloads) and then imported, grabbed twice and then failed, and one release written for two movies
# with an import of each - every one of them is ONE download.
insert(70, "grabbed", "2026-12-10 10:00:00", "TWICE", 30, "WEBDL-720p", GB, source="Rss", indexer="Twice")
insert(71, "grabbed", "2026-12-10 12:00:00", "TWICE", 30, "WEBDL-720p", GB, source="InteractiveSearch", indexer="Twice")
insert(72, "downloadFolderImported", "2026-12-10 13:00:00", "TWICE", 30, "WEBDL-720p", GB)
insert(73, "grabbed", "2026-12-11 10:00:00", "BAD", 31, "WEBDL-720p", GB, source="Rss", indexer="Twice")
insert(74, "grabbed", "2026-12-11 10:30:00", "BAD", 31, "WEBDL-720p", GB, source="Search", indexer="Twice")
insert(75, "downloadFailed", "2026-12-11 11:00:00", "BAD", 31)
insert(76, "grabbed", "2026-12-12 10:00:00", "DUO", 32, "WEBDL-720p", GB, source="Rss", indexer="Duo")
insert(77, "grabbed", "2026-12-12 10:00:00", "DUO", 33, "WEBDL-720p", GB, source="Rss", indexer="Duo")
insert(78, "downloadFolderImported", "2026-12-12 10:30:00", "DUO", 32, "WEBDL-720p", GB)
insert(79, "downloadFolderImported", "2026-12-12 10:30:00", "DUO", 33, "WEBDL-720p", GB)
several = reliability(stats.period_between("2026-12-10", "2026-12-12"))
check(several == [("Twice", 2, 1, 1, 0, 0.5), ("Duo", 1, 1, 0, 0, 1.0)],
      f"a release grabbed again before it finished and a release for two movies are one download each ({several})")
with db.connect() as conn:
    conn.execute("DELETE FROM radarr_event WHERE arr_id BETWEEN 40 AND 79")
    conn.commit()

print()
print("--- Blackhole: a grab without download_id ---")
insert(20, "grabbed", "2026-11-02 10:00:00", None, 14, "WEBDL-720p", GB, source="ReleasePush", indexer="Other")
insert(21, "downloadFolderImported", "2026-11-02 10:10:00", "Blackhole_x", 14, "WEBDL-720p", GB)
sources = stats.radarr_sources(PERIOD)
check(("Other", 1) in [(x["name"], x["count"]) for x in sources["indexers"]],
      "an import without a grab of the same download_id pairs with the last grab of the same item without download_id")
check(stats.radarr_speed(PERIOD)["downloads"]["count"] == 3, "... and counts into the download speed (10 min)")
with db.connect() as conn:
    conn.execute("DELETE FROM radarr_event WHERE arr_id IN (20, 21)")
    conn.commit()

print()
print("--- Code review: the same download_id twice ---")
# The same release (the same torrent hash = the same download_id) grabbed again: the import must pair with the last
# grab before it, not with the first in the window - otherwise "downloading" takes weeks.
insert(30, "grabbed", "2026-12-01 10:00:00", "R", 20, "WEBDL-720p", GB, source="Rss")
insert(31, "downloadFolderImported", "2026-12-01 10:20:00", "R", 20, "WEBDL-720p", GB)
insert(32, "grabbed", "2026-12-10 10:00:00", "R", 20, "WEBDL-720p", GB, source="Search")
insert(33, "downloadFolderImported", "2026-12-10 10:30:00", "R", 20, "WEBDL-720p", GB)
december = stats.radarr_speed(stats.period_between("2026-12-10", "2026-12-11"))
check(december["downloads"] == {"median": 1800, "p90": 1800, "count": 1},
      f"the import pairs with the last grab before it ({december['downloads']})")
with db.connect() as conn:
    conn.execute("DELETE FROM radarr_event WHERE arr_id BETWEEN 30 AND 33")
    conn.commit()

print()
print("--- Review Focus 5: midnight in the app's zone ---")
insert(9, "downloadFolderImported", "2026-10-31 23:30:00", "E", 15, "WEBDL-720p", GB)
set_zone("Europe/Prague")
PERIOD = stats.period_between("2026-10-31", "2026-11-02")
p = {r["day"]: r["count"] for r in stats.radarr_additions(PERIOD)}
check(p.get("2026-11-01") == 2 and p.get("2026-10-31") == 1, f"an import at 23:30 UTC is on Sunday 1. 11. in Prague ({p})")
h = stats.radarr_heatmap(PERIOD)
check(h[6][0] == 1 and h[5][21] == 1, "the heatmap in the app's zone: Sunday 0 h; i1 Saturday 21 h (20:30 UTC + 1)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
