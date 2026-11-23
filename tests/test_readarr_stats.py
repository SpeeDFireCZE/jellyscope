# -*- coding: utf-8 -*-
r"""Readarr's statistics (stats/readarr.py) over the recorded history with a known result.

The unit is the book: Readarr writes one import per book file (`bookFileImported`), so the files of one
download (the same `download_id`) are one import of a book; imports without a `download_id` (a manual
import) are grouped by the book and the day (UTC). The data is the recorded history
(tests/readarr_responses/history_since.json) synced by `fill_readarr()`; the period is November 2026, the
app's zone UTC. The numbers below are counted by hand from the recording:

    grab 101   11-12 20:50:18  book 11  EPUB  4 MB  Rss               arr-test  WRE     SABnzbd  usenet  (aaa111)
    import 102 11-12 21:00:05  1 file of 4 MB (aaa111)                         = import B1: 1 file, 4 MB
    grab 103   11-14 08:10:00  book 11  AZW3  6 MB  InteractiveSearch arr-test  AZWGRP  SABnzbd  usenet  (bbb222)
    delete 104 11-14 09:00:00  the EPUB file, reason Upgrade (no file id: a deletion carries none)
    import 105 11-14 09:00:10  1 file (a NEW file id, 1108) of 6 MB (bbb222)   = import B2: 1 file, 6 MB, an upgrade
    imports    11-15 10:00:00..01  book 12, 2 PDF files of 1 MB, NO download_id = import B3: 2 files, 2 MB (manual)
    grab 108   11-16 12:00     book 22  MP3  (tor_hash_ccc333), failed 12:30 - no import
    grab 110   11-17 12:00     book 22  MP3  98 MB  UserInvokedSearch arr-test WRE SABnzbd usenet (ddd444)
    imports    11-17 13:00:00..01   2 files of 49 MB                      = import B4: 2 files, 98 MB
    (downloadImported, bookImportIncomplete, downloadIgnored, renamed, retagged, a Manual deletion: not imports)

What follows from it and the test guards:
- imports: four books (not six files), 6 files, 110 MB (4 + 6 + 2 + 98); new 3, upgrades 1 (Review Focus 1: B2 has a
  file id of its own - no id of the deleted file - and still is the upgrade of B1, paired by the book);
- the manual import of book 12 is ONE import of two files (Review Focus 2), not two;
- by day: 11-12 (1, 1 file, 4 MB), 11-14 (1, 1, 6 MB, upgrade), 11-15 (1, 2, 2 MB), 11-17 (1, 2, 98 MB);
- since the release: the books were added to Readarr on 11-12, after their release (1936...1989): nothing was waited for
  (B1, B3, B4 not counted, B2 an upgrade);
- download time: B1 20:50:18 -> 21:00:05 = 587 s, B2 08:10:00 -> 09:00:10 = 3010 s, B4 12:00:00 -> 13:00:00 = 3600 s;
  B3 has no grab. Median 3010, p90 at rank 0.9 * (3 - 1) = 1.8: 3010 + 0.8 * (3600 - 3010) = 3482;
- sources over the four imports: indexer arr-test x3 (4 + 6 + 98 = 108 MB) and "-" x1 (2 MB); groups WRE x2 (4 + 98 = 102 MB),
  AZWGRP x1 (6 MB), "-" x1 (2 MB); clients SABnzbd main x3 (108 MB; the import's name), "-" x1; protocols usenet x3, "-" x1;
  qualities one each, by size: MP3 98, AZW3 6, EPUB 4, PDF 2 MB;
- upgrades: one (book 11, EPUB -> AZW3); the grab that brought it was a manual pick; all four grabs of the period:
  Rss + Search = 2 automatic, 1 manual, 1 user's search;
- failures: one (event 109).

Run:
    .venv/bin/python tests/test_readarr_stats.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "readarr-statistics.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import connect_readarr, fill_readarr, no_network, readarr_client, readarr_handler  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db, stats  # noqa: E402
from jellyscope.arr import readarr  # noqa: E402

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


def insert(arr_id: int, kind: str, date: str, download_id: str | None, file: int | None, book: int = 40, quality: str = "EPUB",
           size: int | None = None, released: str | None = None, **columns: object) -> None:
    """One more event of Readarr's history; `columns` are further columns of the table by their names."""
    row = {"arr_id": arr_id, "event_type": kind, "happened_at": date, "title": f"Book {book}", "author": "Author", "item_id": file,
           "title_id": book, "author_id": 9, "quality": quality, "size_bytes": size, "indexer": "", "release_group": "", "client": "",
           "protocol": "", "source": "", "reason": "", "download_id": download_id, "released_at": released, "cf_score": 0, **columns}
    with db.connect() as conn:
        conn.execute(f"INSERT INTO readarr_event ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        conn.commit()


def remove(first: int, last: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM readarr_event WHERE arr_id BETWEEN ? AND ?", (first, last))
        conn.commit()


def add_title(book: int, added: str | None) -> None:
    """A book in Readarr's list with the time it was added (the column `added_at`)."""
    with db.connect() as conn:
        conn.execute("INSERT INTO readarr_title (title_id, name, added_at) VALUES (?, ?, ?)", (book, f"Book {book}", added))
        conn.commit()


set_zone("UTC")
substitute(readarr, "ReadarrClient", readarr_client(readarr_handler()))
connect_readarr()
fill_readarr()
PERIOD = stats.period_between("2026-11-01", "2026-11-30")

print("--- What was added and when ---")
p = stats.readarr_additions(PERIOD)
check([(r["day"], r["count"], r["file_count"], r["new_count"], r["upgrade_count"]) for r in p]
      == [("2026-11-12", 1, 1, 1, 0), ("2026-11-14", 1, 1, 0, 1), ("2026-11-15", 1, 2, 1, 0), ("2026-11-17", 1, 2, 1, 0)],
      f"imports of books by day, with the files: a download is one book, not its files ({[(r['day'], r['count'], r['file_count']) for r in p]})")
check([r["size_bytes"] for r in p] == [4 * MB, 6 * MB, 2 * MB, 98 * MB], "the size of a book = the sum of its files")
check(sum(r["count"] for r in p) == 4, "four imports of books (downloadImported is no import of its own)")
h = stats.readarr_heatmap(PERIOD)
check(len(h) == 7 and all(len(r) == 24 for r in h), "heatmap 7 x 24 (as charts.heatmap)")
check(h[3][21] == 1 and h[5][9] == 1 and h[6][10] == 1 and h[1][13] == 1 and sum(map(sum, h)) == 4,
      "heatmap by the first file of a book: Thu 21 h, Sat 9 h, Sun 10 h, Tue 13 h (row 0 = Monday)")
n = stats.readarr_largest(PERIOD, 2)
check([(r["title"], r["author"], r["size_bytes"], r["file_count"], r["quality"]) for r in n]
      == [("Stráže! Stráže!", "Terry Pratchett", 98 * MB, 2, "MP3"), ("Válka s mloky", "Karel Čapek", 6 * MB, 1, "AZW3")],
      f"the largest imports are books, with their file count ({n})")
check([r["size_bytes"] for r in stats.readarr_largest(PERIOD)] == [98 * MB, 6 * MB, 4 * MB, 2 * MB],
      "the largest: all four books, the larger first")
check(stats.readarr_largest(PERIOD)[0]["title_id"] == 22, "a row carries the book's id in Readarr (for the link)")
# Review Focus 2: the same book imported by hand on another day is another import; the same day = the same one.
insert(500, "bookFileImported", "2026-11-15 18:00:00", None, 1203, book=12, size=MB, title="Bílá nemoc")
insert(501, "bookFileImported", "2026-11-16 18:00:00", None, 1204, book=12, size=MB, title="Bílá nemoc")
check([(r["day"], r["count"], r["file_count"]) for r in stats.readarr_additions(PERIOD)]
      == [("2026-11-12", 1, 1), ("2026-11-14", 1, 1), ("2026-11-15", 1, 3), ("2026-11-16", 1, 1), ("2026-11-17", 1, 2)],
      "a manual import: the files of the same book and day are one import (3 files on 11-15), another day is another one")
remove(500, 501)

print()
print("--- Speed ---")
r = stats.readarr_speed(PERIOD)
check(r["downloads"] == {"median": 3010, "p90": 3482, "count": 3}, f"grab -> the first file: 587, 3010 and 3600 s; the median and p90 ({r['downloads']})")
since = r["since_release"]
# The books were added to Readarr on 2026-11-12, years AFTER they came out (1936-1989): nothing was waited for.
# B1, B3, B4 are therefore "not counted" (3), B2 is an upgrade and does not take part at all.
check(since["count"] == 0 and since["median"] is None and since["not_counted"] == 3 and since["unknown"] == 0 and since["before_release"] == 0,
      f"since the release: the books came to Readarr after they came out (B1, B3, B4 not counted, B2 is an upgrade) ({since})")
add_title(41, "2026-01-01 00:00:00")       # in Readarr since January: it was waited for
add_title(42, "2026-01-01 00:00:00")
add_title(43, "2026-01-01 00:00:00")
add_title(44, "2026-11-21 00:00:00")       # the book came into Readarr a day after its release
# book 45 has no row in readarr_title at all: when it was added is unknown
insert(600, "bookFileImported", "2026-11-20 03:00:00", "Z1", 6001, book=41, size=MB, released="2026-11-20 00:00:00")           # 3 h after the release
insert(601, "bookFileImported", "2026-11-21 11:00:00", "Z2", 6002, book=42, size=MB, released="2026-11-21 12:00:00")           # an hour before it
insert(602, "bookFileImported", "2026-11-22 12:00:00", "Z3", 6003, book=43, size=MB, released="2026-05-23 00:00:00")          # 183 days and 12 h
insert(603, "bookFileImported", "2026-11-22 12:00:00", "Z4", 6004, book=44, size=MB, released="2026-11-20 00:00:00")          # added after
insert(604, "bookFileImported", "2026-11-22 12:00:00", "Z5", 6005, book=45, size=MB, released="2026-11-20 00:00:00")          # added: unknown
since = stats.readarr_speed(PERIOD)["since_release"]
# Counted: book 41 (10800 s = 3 h, bucket 6h) and book 43 (183 * 86400 + 43200 = 15854400 s, bucket longer); the median is
# (10800 + 15854400) / 2 = 7932600. Book 42 is before its release (1), book 44 was added after it (3 + 1 = 4 not counted), 45 is unknown (1).
check(since["count"] == 2 and since["median"] == 7932600 and since["before_release"] == 1 and since["not_counted"] == 4 and since["unknown"] == 1
      and since["buckets"] == {"1h": 0, "6h": 1, "1d": 0, "7d": 0, "longer": 1},
      f"since the release: only books that were in Readarr when they came out; an import before the release, a book added later and an unknown time separately ({since})")
remove(600, 604)
with db.connect() as conn:
    conn.execute("DELETE FROM readarr_title WHERE title_id BETWEEN 41 AND 45")
    conn.commit()
empty = stats.readarr_speed(stats.period_between("2026-01-01", "2026-01-02"))
check(empty["downloads"] == {"median": None, "p90": None, "count": 0} and empty["since_release"]["median"] is None,
      "an empty period: None, not zero")

print()
print("--- Sources and quality ---")
sources = stats.readarr_sources(PERIOD)
check(set(sources) == {"indexers", "groups", "clients", "protocols", "qualities"}, f"the groups of sources (no resolutions for books) ({sorted(sources)})")
check([(x["name"], x["count"], x["size_bytes"]) for x in sources["indexers"]] == [("arr-test", 3, 108 * MB), ("–", 1, 2 * MB)],
      f"indexers: from the grab of the import; the manual import has none ({sources['indexers']})")
check([(x["name"], x["count"], x["size_bytes"]) for x in sources["groups"]] == [("WRE", 2, 102 * MB), ("AZWGRP", 1, 6 * MB), ("–", 1, 2 * MB)],
      f"release groups ({sources['groups']})")
check([(x["name"], x["count"]) for x in sources["clients"]] == [("SABnzbd main", 3), ("–", 1)],
      f"clients: the import's own client name (an instance in Readarr), not the grab's type name ({sources['clients']})")
check([(x["name"], x["count"]) for x in sources["protocols"]] == [("usenet", 3), ("–", 1)], f"protocols ({sources['protocols']})")
check([(x["name"], x["count"], x["size_bytes"]) for x in sources["qualities"]]
      == [("MP3", 1, 98 * MB), ("AZW3", 1, 6 * MB), ("EPUB", 1, 4 * MB), ("PDF", 1, 2 * MB)], f"qualities of the imported books ({sources['qualities']})")

print()
print("--- Upgrades and activity ---")
u = stats.readarr_upgrades(PERIOD)
check(u["count"] == 1 and u["item_count"] == 1 and u["per_item"] == 1,
      f"Review Focus 1: an import with a new file id after a deletion is ONE upgrade of one book ({u['count']}, {u['item_count']})")
check(u["transitions"] == [{"old": "EPUB", "new": "AZW3", "count": 1}], f"the quality transition, once ({u['transitions']})")
check(u["titles"] == [{"title": "Válka s mloky", "author": "Karel Čapek", "count": 1, "title_id": 11}], f"the most upgraded books have it once ({u['titles']})")
check(u["how"] == {"manual": 1}, f"a manual pick brought the upgrade ({u['how']})")
check(u["how_all"] == {"automatic": 2, "manual": 1, "user": 1}, f"all grabs of the period ({u['how_all']})")
check(stats.readarr_failures(PERIOD) == 1, "failures: one")
check(stats.readarr_first() == "2026-11-12 20:50:18", f"the first event of the history ({stats.readarr_first()})")

print()
print("--- An upgrade to ANOTHER edition of the book, and a slow import ---")
# A deletion carries no file id and a new edition's files have new ids, so nothing pairs by the file: book 80 as EPUB (file 8101),
# then AZW3 (files 8201-8202). The deletion comes first (12-02 10:00:00), the import only 40 minutes later (12-02 10:40:00..01).
insert(1000, "grabbed", "2026-12-01 09:00:00", "RA", None, book=80, quality="EPUB", source="Rss")
insert(1001, "bookFileImported", "2026-12-01 09:10:00", "RA", 8101, book=80, quality="EPUB", size=3 * MB)
insert(1010, "grabbed", "2026-12-02 09:00:00", "RB", None, book=80, quality="AZW3", source="InteractiveSearch")
insert(1011, "bookFileDeleted", "2026-12-02 10:00:00", None, None, book=80, quality="EPUB", reason="Upgrade")
for n in range(2):
    insert(1020 + n, "bookFileImported", f"2026-12-02 10:40:0{n}", "RB", 8201 + n, book=80, quality="AZW3", size=5 * MB)
december = stats.period_between("2026-12-01", "2026-12-31")
a = stats.readarr_additions(december)
# Two imports of the book (12-01 EPUB: 1 file 3 MB new; 12-02 AZW3: 2 files 10 MB) and the second is an upgrade: 1 new + 1 upgrade, not 2 new.
check([(r["day"], r["count"], r["new_count"], r["upgrade_count"], r["size_bytes"]) for r in a]
      == [("2026-12-01", 1, 1, 0, 3 * MB), ("2026-12-02", 1, 0, 1, 10 * MB)],
      f"a new edition + an import 40 min after the deletion: the second import is an upgrade ({a})")
u = stats.readarr_upgrades(december)
check(u["count"] == 1 and u["transitions"] == [{"old": "EPUB", "new": "AZW3", "count": 1}] and u["how"] == {"manual": 1},
      f"the upgrade carries the old and the new quality and the grab that brought it ({u['count']}, {u['transitions']}, {u['how']})")
# "How soon after the release": only the first import counts (the upgrade does not), once.
with db.connect() as conn:
    conn.execute("UPDATE readarr_event SET released_at = '2026-12-01 00:00:00' WHERE title_id = 80")
    conn.commit()
add_title(80, "2026-01-01 00:00:00")
since = stats.readarr_speed(december)["since_release"]
check(since["count"] == 1, f"'how soon after the release' counts the book once, not again for the upgrade ({since['count']})")
# Another deletion with no import of the book near: not an upgrade of anything, and no import is changed.
insert(1040, "bookFileDeleted", "2026-12-05 10:00:00", None, None, book=80, reason="Upgrade")
check(stats.readarr_upgrades(december)["count"] == 1, "a deletion with no import after it pairs with nothing")
# A deletion of another reason is no upgrade even next to an import.
insert(1041, "bookFileDeleted", "2026-12-01 09:10:00", None, None, book=80, reason="Manual")
check(stats.readarr_upgrades(december)["count"] == 1, "a Manual deletion next to an import is no upgrade")
remove(1000, 1041)
with db.connect() as conn:
    conn.execute("DELETE FROM readarr_title WHERE title_id = 80")
    conn.commit()

print()
print("--- A grab without download_id (a client that does not report it) ---")
insert(700, "grabbed", "2026-11-25 10:00:00", None, None, book=50, size=MB, source="ReleasePush", indexer="Other")
insert(701, "bookFileImported", "2026-11-25 10:10:00", "Blackhole_x", 7001, book=50, size=MB)
check(("Other", 1) in [(x["name"], x["count"]) for x in stats.readarr_sources(PERIOD)["indexers"]],
      "an import pairs with the last grab of the same BOOK without a download_id")
remove(700, 701)

print()
print("--- The same download_id twice: the import pairs with the last grab before it ---")
insert(800, "grabbed", "2026-12-01 10:00:00", "R", None, book=60, source="Rss")
insert(801, "bookFileImported", "2026-12-01 10:20:00", "R", 8001, book=60, size=MB)
insert(802, "grabbed", "2026-12-10 10:00:00", "R", None, book=60, source="Search")
insert(803, "bookFileImported", "2026-12-10 10:30:00", "R", 8002, book=60, size=MB)
december = stats.readarr_speed(stats.period_between("2026-12-10", "2026-12-11"))
check(december["downloads"] == {"median": 1800, "p90": 1800, "count": 1}, f"30 min, not nine days ({december['downloads']})")
remove(800, 803)

print()
print("--- Links into the books library ---")
check(stats.readarr_library([11, 12, 22]) == {}, "without books in the library no book is linked")
with db.connect() as conn:
    conn.execute("INSERT INTO book (id, name, title, author, cover_tag, missing, synced_at) VALUES ('jf-mloci', 'Valka s mloky', 'Valka s mloky', 'Capek', 'tag1', 0, ?)", (db.utcnow(),))
    conn.execute("UPDATE readarr_title SET jellyfin_id = 'jf-mloci' WHERE title_id = 11")
    conn.execute("UPDATE readarr_title SET jellyfin_id = 'jf-gone' WHERE title_id = 12")
    conn.commit()
check(stats.readarr_library([11, 12, 22]) == {11: {"book_id": "jf-mloci", "cover_tag": "tag1"}},
      f"a paired book; a book outside the library (or whose pair is not in the books table) has none ({stats.readarr_library([11, 12, 22])})")
check(stats.readarr_library() == {11: {"book_id": "jf-mloci", "cover_tag": "tag1"}}, "without ids: all paired books")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 1")
    conn.commit()
check(stats.readarr_library([11]) == {}, "a book that went missing from the library is not linked (no dead link)")
check(stats.readarr_library([None, "x"]) == {} and stats.readarr_library([]) == {}, "nothing to look up, nothing found")

print()
print("--- A server without books ---")
with db.connect() as conn:
    conn.execute("DELETE FROM readarr_event")
    conn.execute("DELETE FROM readarr_title")
    conn.commit()
check(stats.readarr_additions(PERIOD) == [] and stats.readarr_largest(PERIOD) == [] and stats.readarr_first() == ""
      and stats.readarr_failures(PERIOD) == 0 and stats.readarr_upgrades(PERIOD)["count"] == 0 and stats.readarr_library([11]) == {},
      "no history: empty lists and zeros, no error")
insert(900, "bookFileImported", "2026-11-05 23:30:00", "E", 9001, book=70, size=MB)

print()
print("--- Midnight in the app's zone ---")
set_zone("Europe/Prague")
PERIOD = stats.period_between("2026-11-01", "2026-11-30")
days = {r["day"]: r["count"] for r in stats.readarr_additions(PERIOD)}
check(days.get("2026-11-06") == 1 and "2026-11-05" not in days, f"an import at 23:30 UTC is on Friday 6. 11. in Prague ({days})")
check(stats.readarr_heatmap(PERIOD)[4][0] == 1, "the heatmap in the app's zone: Friday 0 h")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
