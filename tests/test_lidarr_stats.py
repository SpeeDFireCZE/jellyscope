# -*- coding: utf-8 -*-
r"""Lidarr's statistics (stats/lidarr.py) over the recorded history with a known result.

The unit is the album: Lidarr writes one import per track (`trackFileImported`), so the tracks of one
download (the same `download_id`) are one import of an album; imports without a `download_id` (a manual
import) are grouped by the album and the day (UTC). The data is the recorded history
(tests/lidarr_responses/history_since.json) synced by `fill_lidarr()`; the period is November 2026, the
app's zone UTC. The numbers below are counted by hand from the recording:

    grab 101   11-12 20:50:18  album 11  MP3-320       45 MB  Rss                  test-indexer  WRE      SABnzbd  usenet  (aaa111)
    imports    11-12 21:00:05..21:02:05   3 tracks of 15 MB (aaa111)           = import A1: 3 tracks, 45 MB
    grab 106   11-14 08:10:00  album 11  FLAC          210 MB InteractiveSearch    test-indexer  FLACGRP  SABnzbd  usenet  (bbb222)
    deletes    11-14 09:00:00..02  3 tracks, reason Upgrade (MP3-320)
    imports    11-14 09:00:10..12  3 tracks of 70 MB (bbb222)                  = import A2: 3 tracks, 210 MB, an upgrade
    imports    11-15 10:00:00..01  album 12, 2 tracks of 90 MB, FLAC 24bit, NO download_id
                                                                                = import A3: 2 tracks, 180 MB (manual)
    grab 116   11-16 12:00     album 22  AAC-256 (tor_hash_ccc333), failed 12:30 - no import
    grab 118   11-17 12:00     album 22  MP3-320 UserInvokedSearch test-indexer WRE SABnzbd usenet (ddd444)
    imports    11-17 13:00:00..01   2 tracks of 20 MB                         = import A4: 2 tracks, 40 MB
    (downloadImported, albumImportIncomplete, downloadIgnored, renamed, retagged: not imports)

What follows from it and the test guards:
- imports: four albums (not ten tracks, not eight with `downloadImported`), 10 tracks, 475 MB (45 + 210 + 180 + 40);
  new 3, upgrades 1 (A2 replaced the three tracks of A1: Review Focus 1 - album 11 was downloaded twice = two imports
  of the album, one upgrade, found by tracks);
- the manual import of album 12 is ONE import of two tracks (Review Focus 2), not two;
- by day: 11-12 (1, 3 tracks, 45 MB), 11-14 (1, 3, 210 MB, upgrade), 11-15 (1, 2, 180 MB), 11-17 (1, 2, 40 MB);
- since the release: the artists were added to Lidarr on 11-12, after every album came out, so nothing was waited for
  (A1, A3, A4 not counted, A2 an upgrade) - extra albums with known times of adding are inserted in the test, see there;
- download time (grab -> the first imported track of the same download_id): A1 20:50:18 -> 21:00:05 = 587 s,
  A2 08:10:00 -> 09:00:10 = 3010 s, A4 12:00:00 -> 13:00:00 = 3600 s; A3 has no grab. Median 3010, p90 at rank
  0.9 * (3 - 1) = 1.8: 3010 + 0.8 * (3600 - 3010) = 3482;
- sources over the four imports: indexer test-indexer x3 (45 + 210 + 40 = 295 MB) and "-" x1 (180 MB); groups WRE x2
  (85 MB), FLACGRP x1 (210 MB), "-" x1 (180 MB); clients SABnzbd main x3 (the import's name), "-" x1; protocols usenet x3, "-" x1; qualities
  MP3-320 x2 (85 MB), FLAC x1 (210 MB), FLAC 24bit x1 (180 MB);
- upgrades: one (album 11, MP3-320 -> FLAC), counted once though three tracks were replaced; the grab that brought it
  was a manual pick; all four grabs of the period: Rss + Search = 2 automatic, 1 manual, 1 user's search;
- failures: one (grab 116);
- indexer reliability: test-indexer 3 grabs, 3 imported; tracker 1 grab, 1 failed - further hand-made grabs add a grab
  still downloading at the period's end, a Blackhole grab without download_id and a grab before the period; a
  release of two albums (a grab per album, one download_id) is one download.

Run:
    .venv/bin/python tests/test_lidarr_stats.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lidarr-statistics.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import connect_lidarr, fill_lidarr, lidarr_client, lidarr_handler, no_network  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db, stats  # noqa: E402
from jellyscope.arr import lidarr  # noqa: E402

MB = 1_000_000
failures = 0
db.init_db()
no_network()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def set_zone(name: str) -> None:
    db.set_setting("app_timezone", name)
    db.forget_settings()


def insert(arr_id: int, kind: str, date: str, download_id: str | None, track: int | None, album: int = 40, quality: str = "FLAC",
           size: int | None = None, released: str | None = None, **columns: object) -> None:
    """One more event of Lidarr's history; `columns` are further columns of the table by their names."""
    row = {"arr_id": arr_id, "event_type": kind, "happened_at": date, "title": f"Album {album}", "artist": "Artist", "item_id": track,
           "title_id": album, "artist_id": 9, "quality": quality, "size_bytes": size, "indexer": "", "release_group": "", "client": "",
           "protocol": "", "source": "", "reason": "", "download_id": download_id, "released_at": released, "cf_score": 0, **columns}
    with db.connect() as conn:
        conn.execute(f"INSERT INTO lidarr_event ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        conn.commit()


def remove(first: int, last: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM lidarr_event WHERE arr_id BETWEEN ? AND ?", (first, last))
        conn.commit()


set_zone("UTC")
substitute(lidarr, "LidarrClient", lidarr_client(lidarr_handler()))
connect_lidarr()
fill_lidarr()
PERIOD = stats.period_between("2026-11-01", "2026-11-30")

print("--- What was added and when ---")
p = stats.lidarr_additions(PERIOD)
check([(r["day"], r["count"], r["track_count"], r["new_count"], r["upgrade_count"]) for r in p]
      == [("2026-11-12", 1, 3, 1, 0), ("2026-11-14", 1, 3, 0, 1), ("2026-11-15", 1, 2, 1, 0), ("2026-11-17", 1, 2, 1, 0)],
      f"imports of albums by day, with the tracks: a download is one album, not its tracks ({[(r['day'], r['count'], r['track_count']) for r in p]})")
check([r["size_bytes"] for r in p] == [45 * MB, 210 * MB, 180 * MB, 40 * MB], "the size of an album = the sum of its tracks")
check(sum(r["count"] for r in p) == 4, "four imports of albums (downloadImported is no import of its own)")
h = stats.lidarr_heatmap(PERIOD)
check(len(h) == 7 and all(len(r) == 24 for r in h), "heatmap 7 x 24 (as charts.heatmap)")
check(h[3][21] == 1 and h[5][9] == 1 and h[6][10] == 1 and h[1][13] == 1 and sum(map(sum, h)) == 4,
      "heatmap by the first track of an album: Thu 21 h, Sat 9 h, Sun 10 h, Tue 13 h (row 0 = Monday)")
n = stats.lidarr_largest(PERIOD, 2)
check([(r["title"], r["artist"], r["size_bytes"], r["track_count"], r["quality"]) for r in n]
      == [("Hvězdy", "Kryštof", 210 * MB, 3, "FLAC"), ("Šípková Růžena", "Kryštof", 180 * MB, 2, "FLAC 24bit")],
      f"the largest imports are albums, with their track count ({n})")
check(len(stats.lidarr_largest(PERIOD)) == 4 and [r["size_bytes"] for r in stats.lidarr_largest(PERIOD)] == [210 * MB, 180 * MB, 45 * MB, 40 * MB],
      "the largest: all four albums, the larger first")
check(stats.lidarr_largest(PERIOD)[0]["title_id"] == 11, "a row carries the album's id in Lidarr (for the link)")
# Review Focus 2: the same album imported by hand on another day is another import; the same day = the same one.
insert(500, "trackFileImported", "2026-11-15 18:00:00", None, 1203, album=12, size=90 * MB, title="Šípková Růžena")
insert(501, "trackFileImported", "2026-11-16 18:00:00", None, 1204, album=12, size=90 * MB, title="Šípková Růžena")
check([(r["day"], r["count"], r["track_count"]) for r in stats.lidarr_additions(PERIOD)]
      == [("2026-11-12", 1, 3), ("2026-11-14", 1, 3), ("2026-11-15", 1, 3), ("2026-11-16", 1, 1), ("2026-11-17", 1, 2)],
      "a manual import: the tracks of the same album and day are one import (3 tracks on 11-15), another day is another one")
remove(500, 501)

print()
print("--- Speed ---")
r = stats.lidarr_speed(PERIOD)
check(r["downloads"] == {"median": 3010, "p90": 3482, "count": 3}, f"grab -> the first track: 587, 3010 and 3600 s; the median and p90 ({r['downloads']})")
since = r["since_release"]
# The artists (Krystof 1, Nightingale Quartet 2) were added to Lidarr on 2026-11-12, years AFTER the albums came out (2019-2024):
# nothing was waited for. A1, A3, A4 are therefore "not counted" (3), A2 is an upgrade and does not take part at all.
check(since["count"] == 0 and since["median"] is None and since["not_counted"] == 3 and since["unknown"] == 0 and since["before_release"] == 0,
      f"since the release: the artists came to Lidarr after the albums (A1, A3, A4 not counted, A2 is an upgrade) ({since})")


def add_title(album: int, added: str | None) -> None:
    """An album in Lidarr's list with the time its artist was added (the column `added_at`)."""
    with db.connect() as conn:
        conn.execute("INSERT INTO lidarr_title (title_id, name, added_at) VALUES (?, ?, ?)", (album, f"Album {album}", added))
        conn.commit()


add_title(41, "2026-01-01 00:00:00")       # in Lidarr since January: its albums were waited for
add_title(42, "2026-01-01 00:00:00")
add_title(43, "2026-01-01 00:00:00")
add_title(44, "2026-11-21 00:00:00")       # the artist came a day after the album's release
# album 45 has no row in lidarr_title at all: when the artist was added is unknown
insert(600, "trackFileImported", "2026-11-20 03:00:00", "Z1", 6001, album=41, size=MB, released="2026-11-20 00:00:00")           # 3 h after the release
insert(601, "trackFileImported", "2026-11-21 11:00:00", "Z2", 6002, album=42, size=MB, released="2026-11-21 12:00:00")           # an hour before it
insert(602, "trackFileImported", "2026-11-22 12:00:00", "Z3", 6003, album=43, size=MB, released="2026-05-23 00:00:00")          # 183 days and 12 h
insert(603, "trackFileImported", "2026-11-22 12:00:00", "Z4", 6004, album=44, size=MB, released="2026-11-20 00:00:00")          # artist added after
insert(604, "trackFileImported", "2026-11-22 12:00:00", "Z5", 6005, album=45, size=MB, released="2026-11-20 00:00:00")          # added: unknown
since = stats.lidarr_speed(PERIOD)["since_release"]
# Counted: album 41 (10800 s = 3 h, bucket 6h) and album 43 (183 * 86400 + 43200 = 15854400 s, bucket longer); the median is
# (10800 + 15854400) / 2 = 7932600. Album 42 is before its release (1), album 44 was added after it (3 + 1 = 4 not counted), 45 is unknown (1).
check(since["count"] == 2 and since["median"] == 7932600 and since["before_release"] == 1 and since["not_counted"] == 4 and since["unknown"] == 1
      and since["buckets"] == {"1h": 0, "6h": 1, "1d": 0, "7d": 0, "longer": 1},
      f"since the release: only albums whose artist was in Lidarr when they came out; an import before the release, an artist added later and an unknown time separately ({since})")
remove(600, 604)
with db.connect() as conn:
    conn.execute("DELETE FROM lidarr_title WHERE title_id BETWEEN 41 AND 45")
    conn.commit()
empty = stats.lidarr_speed(stats.period_between("2026-01-01", "2026-01-02"))
check(empty["downloads"] == {"median": None, "p90": None, "count": 0} and empty["since_release"]["median"] is None,
      "an empty period: None, not zero")

print()
print("--- Sources and quality ---")
sources = stats.lidarr_sources(PERIOD)
check(set(sources) == {"indexers", "groups", "clients", "protocols", "qualities"}, f"the groups of sources (no resolutions for music) ({sorted(sources)})")
check([(x["name"], x["count"], x["size_bytes"]) for x in sources["indexers"]] == [("test-indexer", 3, 295 * MB), ("–", 1, 180 * MB)],
      f"indexers: from the grab of the import; the manual import has none ({sources['indexers']})")
check([(x["name"], x["count"], x["size_bytes"]) for x in sources["groups"]] == [("WRE", 2, 85 * MB), ("FLACGRP", 1, 210 * MB), ("–", 1, 180 * MB)],
      f"release groups ({sources['groups']})")
check([(x["name"], x["count"]) for x in sources["clients"]] == [("SABnzbd main", 3), ("–", 1)],
      f"clients: the import's own client name (an instance in Lidarr), not the grab's type name ({sources['clients']})")
check([(x["name"], x["count"]) for x in sources["protocols"]] == [("usenet", 3), ("–", 1)], f"protocols ({sources['protocols']})")
check([(x["name"], x["count"], x["size_bytes"]) for x in sources["qualities"]]
      == [("MP3-320", 2, 85 * MB), ("FLAC", 1, 210 * MB), ("FLAC 24bit", 1, 180 * MB)], f"qualities of the imported albums ({sources['qualities']})")

print()
print("--- Upgrades and activity ---")
u = stats.lidarr_upgrades(PERIOD)
check(u["count"] == 1 and u["item_count"] == 1 and u["per_item"] == 1,
      f"Review Focus 1: three replaced tracks are ONE upgrade of one album ({u['count']}, {u['item_count']})")
check(u["transitions"] == [{"old": "MP3-320", "new": "FLAC", "count": 1}], f"the quality transition, once ({u['transitions']})")
check(u["titles"] == [{"title": "Hvězdy", "artist": "Kryštof", "count": 1, "title_id": 11}], f"the most upgraded albums have it once ({u['titles']})")
check(u["how"] == {"manual": 1}, f"a manual pick brought the upgrade ({u['how']})")
check(u["how_all"] == {"automatic": 2, "manual": 1, "user": 1}, f"all grabs of the period ({u['how_all']})")
check(stats.lidarr_failures(PERIOD) == 1, "failures: one")
check(stats.lidarr_first() == "2026-11-12 20:50:18", f"the first event of the history ({stats.lidarr_first()})")

print()
print("--- Indexer reliability: downloads of the period, their album imports and failures by download_id ---")


def reliability(period) -> list[tuple]:
    """(name, downloads, imported, failed, unmatched, success) per indexer, in the function's order."""
    return [(x["name"], x["downloads"], x["imported"], x["failed"], x["unmatched"], x["success"])
            for x in stats.lidarr_indexer_reliability(period)]


# From the recording: test-indexer grabbed aaa111, bbb222 and ddd444 (all three imported as albums), tracker ccc333 (failed).
check(reliability(PERIOD) == [("tracker", 1, 0, 1, 0, 0.0), ("test-indexer", 3, 3, 0, 0, 1.0)],
      f"an album's grab is imported or failed by its download_id; the most failures first ({reliability(PERIOD)})")
insert(1100, "grabbed", "2026-11-30 23:50:00", "L", None, album=90, source="Rss", indexer="test-indexer")
insert(1101, "trackFileImported", "2026-12-01 00:10:00", "L", 9001, album=90, size=MB)
insert(1102, "grabbed", "2026-11-26 10:00:00", None, None, album=91, source="ReleasePush", indexer="Other")
insert(1103, "trackFileImported", "2026-11-26 10:10:00", "Blackhole_z", 9101, album=91, size=MB)
insert(1104, "grabbed", "2026-10-30 10:00:00", "P", None, album=92, source="Rss", indexer="tracker")
insert(1105, "downloadFailed", "2026-11-01 01:00:00", "P", None, album=92)
rows = reliability(PERIOD)
check(("test-indexer", 4, 3, 0, 0, 1.0) in rows,
      f"a grab still downloading at the period's end (imported only after it) is neither a success nor a failure ({rows})")
check(("Other", 1, 0, 0, 1, None) in rows,
      f"a grab without download_id (Blackhole) cannot be matched and lowers no success rate ({rows})")
check(("tracker", 1, 0, 1, 0, 0.0) in rows, "a grab before the period does not count, not even with its failure in the period")
check([r[0] for r in rows] == ["tracker", "test-indexer", "Other"],
      f"sorted by failures, then the lowest success rate; nothing finished comes last ({[r[0] for r in rows]})")
analysis, before = stats.lidarr_events(PERIOD), stats.lidarr_indexer_reliability(PERIOD)
insert(1106, "grabbed", "2026-11-27 15:00:00", "S", None, album=95, source="Rss", indexer="Later")
from_page = stats.lidarr_indexer_reliability(PERIOD, analysis=analysis)
check(from_page == before and from_page != stats.lidarr_indexer_reliability(PERIOD),
      "it computes from the page's paired events (analysis=), not from the table again: a grab stored after them is not in it")
remove(1100, 1106)
# The same download_id grabbed again after its failure is a download of its own (the outcome pairs with the last grab
# before it, as the sources pair it). A download is imported when it was imported - a failure written after the import
# does not make it failed: failed = a failure and no import.
insert(1110, "grabbed", "2026-12-01 10:00:00", "R", None, album=93, source="Rss", indexer="Again")
insert(1111, "downloadFailed", "2026-12-01 11:00:00", "R", None, album=93)
insert(1112, "grabbed", "2026-12-02 10:00:00", "R", None, album=93, source="Search", indexer="Again")
insert(1113, "trackFileImported", "2026-12-02 10:30:00", "R", 9301, album=93, size=MB)
insert(1114, "grabbed", "2026-12-03 10:00:00", "M", None, album=94, source="Rss", indexer="Marked")
insert(1115, "trackFileImported", "2026-12-03 10:20:00", "M", 9401, album=94, size=MB)
insert(1116, "downloadFailed", "2026-12-03 12:00:00", "M", None, album=94)
december = reliability(stats.period_between("2026-12-01", "2026-12-03"))
check(december == [("Again", 2, 1, 1, 0, 0.5), ("Marked", 1, 1, 0, 0, 1.0)],
      f"a repeated download_id after its failure is another download; a failure after the import leaves it imported ({december})")
check(stats.lidarr_indexer_reliability(stats.period_between("2026-01-01", "2026-01-02")) == [], "an empty period: no rows")
remove(1110, 1116)
# A release of several albums (a discography): Lidarr writes a grab and a failure per ALBUM, all with one download_id,
# and imports the tracks of each. Two albums imported, two albums that failed - one download each.
insert(1120, "grabbed", "2026-12-10 10:00:00", "DISCO", None, album=96, source="Rss", indexer="Discs")
insert(1121, "grabbed", "2026-12-10 10:00:00", "DISCO", None, album=97, source="Rss", indexer="Discs")
for n, album in enumerate((96, 96, 97, 97)):
    insert(1122 + n, "trackFileImported", f"2026-12-10 11:00:0{n}", "DISCO", 9600 + n, album=album, size=MB)
for n, album in enumerate((98, 99)):
    insert(1130 + n, "grabbed", "2026-12-11 10:00:00", "BAD", None, album=album, source="Rss", indexer="Discs")
    insert(1132 + n, "downloadFailed", "2026-12-11 11:00:00", "BAD", None, album=album)
several = reliability(stats.period_between("2026-12-10", "2026-12-11"))
check(several == [("Discs", 2, 1, 1, 0, 0.5)],
      f"a release of two albums imported and one that failed are one download each, not one per album ({several})")
remove(1120, 1133)

print()
print("--- An upgrade to ANOTHER release of the album, and a slow import ---")
# Lidarr's tracks belong to a release: an upgrade to another edition deletes the old files with the OLD release's track ids
# and imports the new ones with the NEW release's ids. Album 80: MP3 on release A (tracks 8101-8103), then FLAC on release B
# (tracks 8201-8204). The deletions come first (12-02 10:00:00..02), the track-imported events only after the whole album was
# moved - here 40 minutes later (4 tracks, 12-02 10:40:00..03), beyond the ten minutes of the track-level pairing.
insert(1000, "grabbed", "2026-12-01 09:00:00", "RA", None, album=80, quality="MP3-320", source="Rss")
for n in range(3):
    insert(1001 + n, "trackFileImported", f"2026-12-01 09:10:0{n}", "RA", 8101 + n, album=80, quality="MP3-320", size=10 * MB)
insert(1010, "grabbed", "2026-12-02 09:00:00", "RB", None, album=80, quality="FLAC", source="InteractiveSearch")
for n in range(3):
    insert(1011 + n, "trackFileDeleted", f"2026-12-02 10:00:0{n}", None, 8101 + n, album=80, quality="MP3-320", size=10 * MB, reason="Upgrade")
for n in range(4):
    insert(1020 + n, "trackFileImported", f"2026-12-02 10:40:0{n}", "RB", 8201 + n, album=80, quality="FLAC", size=30 * MB)
december = stats.period_between("2026-12-01", "2026-12-31")
a = stats.lidarr_additions(december)
# Two imports of the album (12-01 MP3: 3 tracks 30 MB new; 12-02 FLAC: 4 tracks 120 MB) and the second is an upgrade:
# 1 new + 1 upgrade, not 2 new.
check([(r["day"], r["count"], r["new_count"], r["upgrade_count"]) for r in a] == [("2026-12-01", 1, 1, 0), ("2026-12-02", 1, 0, 1)],
      f"another release + an import 40 min after the deletion: the second import is an upgrade ({a})")
u = stats.lidarr_upgrades(december)
check(u["count"] == 1 and u["transitions"] == [{"old": "MP3-320", "new": "FLAC", "count": 1}] and u["how"] == {"manual": 1},
      f"the upgrade carries the old and the new quality and the grab that brought it ({u['count']}, {u['transitions']}, {u['how']})")
# "How soon after the release": only the first import counts (the upgrade does not), once.
with db.connect() as conn:
    conn.execute("UPDATE lidarr_event SET released_at = '2026-12-01 00:00:00' WHERE title_id = 80")
    conn.commit()
add_title(80, "2026-01-01 00:00:00")
since = stats.lidarr_speed(december)["since_release"]
check(since["count"] == 1, f"'how soon after the release' counts the album once, not again for the upgrade ({since['count']})")
# Another deletion with no import of the album near: not an upgrade of anything, and no import is changed.
insert(1040, "trackFileDeleted", "2026-12-05 10:00:00", None, 8201, album=80, reason="Upgrade")
check(stats.lidarr_upgrades(december)["count"] == 1, "a deletion with no import after it pairs with nothing")
remove(1000, 1040)
with db.connect() as conn:
    conn.execute("DELETE FROM lidarr_title WHERE title_id = 80")
    conn.commit()

print()
print("--- A grab without download_id (a client that does not report it) ---")
insert(700, "grabbed", "2026-11-25 10:00:00", None, None, album=50, size=MB, source="ReleasePush", indexer="Other")
insert(701, "trackFileImported", "2026-11-25 10:10:00", "Blackhole_x", 7001, album=50, size=MB)
check(("Other", 1) in [(x["name"], x["count"]) for x in stats.lidarr_sources(PERIOD)["indexers"]],
      "an import pairs with the last grab of the same ALBUM without a download_id")
remove(700, 701)

print()
print("--- The same download_id twice: the import pairs with the last grab before it ---")
insert(800, "grabbed", "2026-12-01 10:00:00", "R", None, album=60, source="Rss")
insert(801, "trackFileImported", "2026-12-01 10:20:00", "R", 8001, album=60, size=MB)
insert(802, "grabbed", "2026-12-10 10:00:00", "R", None, album=60, source="Search")
insert(803, "trackFileImported", "2026-12-10 10:30:00", "R", 8002, album=60, size=MB)
december = stats.lidarr_speed(stats.period_between("2026-12-10", "2026-12-11"))
check(december["downloads"] == {"median": 1800, "p90": 1800, "count": 1}, f"30 min, not nine days ({december['downloads']})")
remove(800, 803)

print()
print("--- Links into the music library ---")
check(stats.lidarr_library([11, 12, 22]) == {}, "without music in the library no album and no artist is linked")
with db.connect() as conn:
    conn.execute("INSERT INTO music_album (id, name, artist_id, artist, cover_tag, missing, synced_at) VALUES ('jf-stars', 'Hvezdy', 'jf-kryst', 'Krystof', 'tag1', 0, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO music_artist (id, name, synced_at, missing) VALUES ('jf-kryst', 'Krystof', ?, 0)", (db.utcnow(),))
    conn.execute("UPDATE lidarr_title SET jellyfin_id = 'jf-stars', jellyfin_artist_id = 'jf-kryst' WHERE title_id = 11")
    conn.execute("UPDATE lidarr_title SET jellyfin_artist_id = 'jf-kryst' WHERE title_id = 12")
    conn.commit()
check(stats.lidarr_library([11, 12, 22]) == {11: {"album_id": "jf-stars", "cover_tag": "tag1", "artist_id": "jf-kryst"},
                                             12: {"album_id": "", "cover_tag": "", "artist_id": "jf-kryst"}},
      f"a paired album and its artist; an album outside the library has none; an artist alone is still linked ({stats.lidarr_library([11, 12, 22])})")
with db.connect() as conn:
    conn.execute("UPDATE music_album SET missing = 1")
    conn.commit()
check(stats.lidarr_library([11])[11]["album_id"] == "", "an album that went missing from the library is not linked (no dead link)")
check(stats.lidarr_library([None, "x"]) == {} and stats.lidarr_library([]) == {}, "nothing to look up, nothing found")

print()
print("--- Midnight in the app's zone ---")
insert(900, "trackFileImported", "2026-11-05 23:30:00", "E", 9001, album=70, size=MB)
set_zone("Europe/Prague")
PERIOD = stats.period_between("2026-11-01", "2026-11-30")
days = {r["day"]: r["count"] for r in stats.lidarr_additions(PERIOD)}
check(days.get("2026-11-06") == 1 and "2026-11-05" not in days, f"an import at 23:30 UTC is on Friday 6. 11. in Prague ({days})")
check(stats.lidarr_heatmap(PERIOD)[4][0] == 1, "the heatmap in the app's zone: Friday 0 h")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
