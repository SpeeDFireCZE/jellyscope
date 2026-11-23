# -*- coding: utf-8 -*-
r"""Books: the statistics (stats/books_listening.py and stats/books_library.py) over hand-counted data.

Listening to an audiobook is a history row of the kind "AudioBook", reading an e-book one of the kind "Book"; the book
comes from the `book` table. The data are tests/_books.py:BOOK_LISTENS over the prepared books (tests/books_responses/).
Guarded:

A) the period's analysis: books only, the period only, one work per multi-file audiobook;
B) numbers: audiobook listening, reading, readers, works, authors;
C) by days and day of the week × hour;
D) readers and the work they spent the most time with;
E) rankings of works, authors and genres; a book the sync does not know yet counts under its name;
F) technique: direct × transcoded (audiobooks only) and the reasons, apps;
G) the library: numbers, formats, decades, genres, the newest, the largest, what nobody reads;
H) a library of e-books alone: no length, no listening - nothing falls over;
I) the book page's data: a multi-file audiobook is one book; reading reported or not.

Hand-counted (the listens of tests/_books.py, 30 days = all but Eva's "Stare povesti" 40 days ago):
  jana: Kapitola 1 1800 s + Kapitola 2 1800 s (audiobook "Cesta na jih"), Duna 900 s (e-book)
  petr: Hobit 3600 s (audiobook, transcoded), Duna 1200 s (e-book)
  eva:  Hobit 2400 s (audiobook, DirectStream), [Stare povesti 600 s, 40 days ago]

Run:
    .venv/bin/python tests/test_books_listening_stats.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "books-statistics.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _books import fill_books  # noqa: E402
from jellyscope import db, stats  # noqa: E402
from jellyscope.stats import books_library, books_listening  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


EBOOKS = "9df755f2b7a9da9df80891c79b9a8a4d"
AUDIO = "5e2022368c574c08ee6f709de93555dc"
print("--- without books ---")
check(stats.books_first() is None and not stats.reading_reported() and stats.books_library_works() == [],
      "an empty database: no first row, no reading reported, no works")
check(stats.books_numbers(stats.books_analysis(30)) == {"listening": 0, "reading": 0, "reader_count": 0, "work_count": 0,
                                                       "author_count": 0}, "numbers of nothing are zeros")

ids = fill_books()
# A history row of a book the sync does not know (added and played before the next run).
NOW = datetime.now(UTC)
HOUR_AGO, TWO_HOURS_AGO = ((NOW - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S") for h in (1, 2))
LAST_SEEN = NOW.strftime("%Y-%m-%d %H:%M:%S")
INSERT_ROW = ("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name, play_method,"
              " media_runtime_ticks, started_at, last_seen_at, watched_seconds, paused_seconds, is_paused, is_active)"
              " VALUES (?, 'u-eva', 'Eva', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, 0, 0)")
with db.connect() as conn:
    # A history row of a book the sync does not know (added and played before the next run); times computed in Python.
    conn.execute(INSERT_ROW, ("unknown-1", "neznama-kniha", "Nová kniha", "AudioBook", "Finamp", "Tablet", "DirectPlay",
                              HOUR_AGO, LAST_SEEN, 300))
    # Movie watching is not books.
    conn.execute(INSERT_ROW, ("film-1", "film", "Film", "Movie", "Web", "PC", "DirectPlay", TWO_HOURS_AGO, LAST_SEEN, 7000))
    conn.commit()

print()
print("--- A) the analysis ---")
analysis = stats.books_analysis(30)
check(len(analysis["rows"]) == 7 and all(x["item_type"] in ("Book", "AudioBook") for x in analysis["rows"]),
      f"7 rows in 30 days: six of the fixture and the unknown book, no movie ({len(analysis['rows'])})")
# 6 listens of the fixture within 30 days + 1 unknown book = 7; the 40 days old one is out
check(len(stats.books_analysis(365)["rows"]) == 8, "365 days bring Eva's 40 days old reading too")
journey = [x for x in analysis["rows"] if x["title"] == "Cesta na jih"]
check(len(journey) == 2 and len({x["item_id"] for x in journey}) == 2 and len({x["work"] for x in journey}) == 1,
      "two files of one audiobook are one work")
# audiobook library: Kapitola 1, Kapitola 2, Hobit (petr), Hobit (eva) = 4 rows (the unknown book has no library); e-books: Duna x 2
check(len(stats.books_analysis(30, library_id=AUDIO)["rows"]) == 4 and len(stats.books_analysis(30, library_id=EBOOKS)["rows"]) == 2,
      "a library asks for its own rows")

print()
print("--- B) numbers ---")
numbers = stats.books_numbers(analysis)
# listening 1800+1800+3600+2400+300 = 9900; reading 900+1200 = 2100; readers 3; works Cesta, Duna, Hobit, Nova kniha = 4;
# authors Karel Vypravec, Frank Herbert, J. R. R. Tolkien = 3 (the unknown book has none)
check(numbers == {"listening": 9900, "reading": 2100, "reader_count": 3, "work_count": 4, "author_count": 3}, f"numbers {numbers}")
check(stats.reading_reported(), "reading is reported (rows of the kind Book exist)")

print()
print("--- C) by days and by hours ---")
by_day = stats.books_by_day(analysis)
check(sum(d["listening"] for d in by_day) == 9900 and sum(d["reading"] for d in by_day) == 2100 and len(by_day) >= 1,
      "the days add up to listening 9900 and reading 2100")
grid = stats.books_heatmap(analysis)
check(len(grid) == 7 and all(len(row) == 24 for row in grid) and abs(sum(map(sum, grid)) - 12000 / 3600) < 1e-9,
      "the grid is 7 x 24 and sums to 12000 s = 3.33 h")

print()
print("--- D) readers ---")
readers = stats.books_readers(analysis)
# petr 3600+1200 = 4800; jana 1800+1800+900 = 4500; eva 2400+300 = 2700
check([(r["user_name"], r["seconds"]) for r in readers] == [("Petr Čtenář", 4800), ("Janička Čtenářová", 4500), ("Eva Čtenářka", 2700)],
      f"readers by time: {[(r['user_name'], r['seconds']) for r in readers]}")
by_name = {r["user_name"]: r for r in readers}
# petr: Hobit 3600 > Duna 1200; jana: Cesta na jih 3600 (two files) > Duna 900; eva: Hobit 2400 > Nova kniha 300
check([by_name[n]["work"] for n in ("Petr Čtenář", "Janička Čtenářová", "Eva Čtenářka")] == ["Hobit", "Cesta na jih", "Hobit"]
      and by_name["Janička Čtenářová"]["work_id"] in {ids["Kapitola 1"], ids["Kapitola 2"], ids["Kapitola 3"]},
      "the work each spent the most time with; the two files of Cesta na jih sum up")

print()
print("--- E) rankings ---")
works = stats.books_works(analysis)
# Hobit 3600+2400 = 6000 (2 readers); Cesta na jih 1800+1800 = 3600 (1); Duna 900+1200 = 2100 (2); Nova kniha 300 (1)
check([(w["title"], w["seconds"], w["reader_count"]) for w in works]
      == [("Hobit", 6000, 2), ("Cesta na jih", 3600, 1), ("Duna", 2100, 2), ("Nová kniha", 300, 1)],
      f"works: {[(w['title'], w['seconds'], w['reader_count']) for w in works]}")
journey_work = next(w for w in works if w["title"] == "Cesta na jih")
# the representative file of the work: the one with the cover (Kapitola 1)
check(journey_work["id"] == ids["Kapitola 1"] and journey_work["cover_tag"] == "cover-cesta" and journey_work["author"] == "Karel Vypravěč",
      "a multi-file audiobook has the file with the cover as its page and cover")
unknown = next(w for w in works if w["title"] == "Nová kniha")
check(unknown["id"] is None and unknown["author"] == "" and unknown["kind"] == "audiobook",
      "a book the sync does not know counts under its name from the history, without a page")
check(len(stats.books_works(analysis, 2)) == 2, "the limit cuts the list")
authors = stats.books_authors(analysis)
# Tolkien 6000; Karel 3600; Herbert 2100; unknown "" 300
check([(a["name"], a["seconds"], a["work_count"]) for a in authors]
      == [("J. R. R. Tolkien", 6000, 1), ("Karel Vypravěč", 3600, 1), ("Frank Herbert", 2100, 1), ("", 300, 1)],
      f"authors: {[(a['name'], a['seconds']) for a in authors]}")
genres = stats.books_genres(analysis)
# Fantasy 6000; Dobrodruzstvi 3600; Science Fiction|Klasika 2100 each (the tie by the name: Klasika first)
check([(g["genre"], g["seconds"]) for g in genres] == [("Fantasy", 6000), ("Dobrodružství", 3600), ("Klasika", 2100), ("Science Fiction", 2100)],
      f"genres: {[(g['genre'], g['seconds']) for g in genres]}")

print()
print("--- F) technique ---")
method = stats.books_play_method(analysis)
# audiobooks only: direct 1800+1800+2400 (DirectStream) +300 = 6300; transcoded Hobit 3600 (1 reason); the e-books do not count
check(method["direct"] == 6300 and method["transcoded"] == 3600
      and method["reasons"] == [{"reason": "AudioCodecNotSupported", "count": 1}], f"method {method['direct']}/{method['transcoded']}")
apps = stats.books_apps(analysis)
# Jellyfin Web 900+3600+1200 = 5700 (device Telefon); Finamp 1800+1800+300 = 3900 (Telefon, Tablet = 2 devices); Symfonium 2400
check([(a["app"], a["seconds"], a["device"]) for a in apps] == [("Jellyfin Web", 5700, 1), ("Finamp", 3900, 2), ("Symfonium", 2400, 1)],
      f"apps {[(a['app'], a['seconds'], a['device']) for a in apps]}")
check(stats.books_first() is not None, "the history of books has a start")

print()
print("--- G) the library ---")
all_works = stats.books_library_works()
check(len(all_works) == 8, f"8 works in the library (10 files; the three files of Cesta na jih are one) ({len(all_works)})")
summary = stats.books_library_summary(all_works)
# e-books Duna, Algoritmy, Pribehy, Stare povesti = 4; audiobook works Cesta, Hobit, Kniha bez autora, Anonymni = 4;
# authors Herbert, Karel, Tolkien, the one of the Interpret file, Jirasek = 5; size 553 150 000; length 9000+39600+600+300 = 49500 s
check(summary == {"ebook_count": 4, "audiobook_count": 4, "author_count": 5, "size_bytes": 553_150_000, "seconds": 49_500},
      f"library numbers {summary}")
formats = stats.books_formats(all_works)
# files: mp3 5 (3 chapters + Kniha bez autora + Anonymni), then one each: CBZ, EPUB, M4B, MOBI, PDF; 10 files
check([(f["format"], f["file_count"]) for f in formats] == [("MP3", 5), ("CBZ", 1), ("EPUB", 1), ("M4B", 1), ("MOBI", 1), ("PDF", 1)]
      and abs(formats[0]["share"] - 50.0) < 1e-9 and formats[0]["size_bytes"] == 28_800_000 + 24_000_000 + 19_200_000 + 5_000_000 + 3_000_000,
      f"formats {[(f['format'], f['file_count']) for f in formats]}")
decades = stats.books_decades(all_works)
# years: Stare 1894 -> 1890, Hobit 1937 -> 1930, Duna 1965 -> 1960, Algoritmy 2019 -> 2010, Cesta 2020 -> 2020; no year: 3
check([(d["decades"], d["work_count"]) for d in decades] == [(1890, 1), (1930, 1), (1960, 1), (2010, 1), (2020, 1), (None, 3)],
      f"decades {[(d['decades'], d['work_count']) for d in decades]}")
library_genres = stats.books_library_genres(all_works)
check([(g["genre"], g["work_count"]) for g in library_genres]
      == [("Dobrodružství", 1), ("Fantasy", 1), ("Informatika", 1), ("Klasika", 1), ("Komiks", 1), ("Science Fiction", 1)],
      "genres count works, not files (Dobrodružství is one although Cesta na jih has three files)")
newest = stats.books_newest(all_works, 3)
# Duna 11-20, Cesta na jih 11-19 12:00 (its last file), Algoritmy 11-18
check([w["title"] for w in newest] == ["Duna", "Cesta na jih", "Algoritmy bez vzorců"], f"newest {[w['title'] for w in newest]}")
largest = stats.books_largest(all_works, 3)
# Hobit 410 000 000; Cesta na jih 28.8 + 24 + 19.2 = 72 000 000; Pribehy z Trolli hory 48 000 000
check([(w["title"], w["size_bytes"]) for w in largest]
      == [("Hobit", 410_000_000), ("Cesta na jih", 72_000_000), ("Příběhy z Trollí hory", 48_000_000)], "the largest")
unread = stats.books_unread(all_works)
# opened for 30 s or more: Cesta, Duna, Hobit, Stare povesti (600 s); not: Anonymni nahravka (2026-09-15), Kniha bez autora (10-15),
# Pribehy (11-10), Algoritmy (11-18) - the longest in the library first
check([w["title"] for w in unread] == ["Anonymní nahrávka", "Kniha bez autora", "Příběhy z Trollí hory", "Algoritmy bez vzorců"],
      f"what nobody reads {[w['title'] for w in unread]}")

print()
print("--- G2) unknown formats, per library, the book page's queries ---")
# Six files without a format are more than the five MP3s - and still the last row of the formats.
with db.connect() as conn:
    for n in range(6):
        conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, format, size_bytes, library_id, missing)"
                     " VALUES (?, 'book', ?, ?, '', ?, '', 1, ?, 0)", (f"nofmt-{n}", f"Bez formátu {n}", f"Bez formátu {n}", f"bez formatu {n}|", EBOOKS))
    conn.commit()
formats = stats.books_formats(stats.books_library_works())
check(formats[0]["format"] == "MP3" and formats[-1]["format"] == "" and formats[-1]["file_count"] == 6,
      f"the unknown format is the last row although it has the most files ({[(f['format'], f['file_count']) for f in formats]})")
with db.connect() as conn:
    conn.execute("DELETE FROM book WHERE id LIKE 'nofmt-%'")
    conn.commit()
# A work in two libraries: one for the whole library, one in each library's own view.
with db.connect() as conn:
    conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, format, size_bytes, library_id, added_at, missing)"
                 " SELECT 'copy-duna', kind, name, title, author, title_key, 'pdf', 1000, ?, added_at, 0 FROM book WHERE id = ?",
                 (AUDIO, ids["Duna"]))
    conn.commit()
check(len(books_listening.books_works_index()) == 8 and len(books_listening.books_works_index(EBOOKS)) == 4 and len(books_listening.books_works_index(AUDIO)) == 5
      and len(stats.books_library_works()) == 8 and len(stats.books_library_works(AUDIO)) == 5,
      "the section counts Duna once (8 works); the audiobook library's own view has it too (4 audiobooks + the copy = 5)")
with db.connect() as conn:
    conn.execute("DELETE FROM book WHERE id = 'copy-duna'")
    conn.commit()
# The book page asks the database only for the work's own rows and reads the table of books once.
seen: list[str] = []
original_all, original_iter = db.query_all, db.query_iter


def _record(function):
    def wrapper(sql, *args, **kwargs):
        seen.append(sql)
        return function(sql, *args, **kwargs)
    return wrapper


db.query_all, db.query_iter = _record(original_all), _record(original_iter)
try:
    stats.book_detail(ids["Kapitola 2"])
finally:
    db.query_all, db.query_iter = original_all, original_iter
history = [q for q in seen if "FROM playback p" in q]
index_reads = [q for q in seen if "cover_tag, added_at, library_id, missing, title_key FROM book" in q]
check(len(history) == 1 and "b.title_key = ?" in history[0] and len(index_reads) == 1,
      f"the book page: one history query limited to the work, one read of the books' table ({len(history)}, {len(index_reads)})")

print()
print("--- H) a library of e-books alone ---")
only_ebooks = books_library.books_library_overview(EBOOKS)
check(only_ebooks["numbers"] == {"ebook_count": 4, "audiobook_count": 0, "author_count": 2, "size_bytes": 63_150_000, "seconds": 0},
      f"e-books alone: {only_ebooks['numbers']}")
check(only_ebooks["books_formats"]["rows"][0]["format"] in ("CBZ", "EPUB", "MOBI", "PDF") and len(only_ebooks["books_formats"]["rows"]) == 4,
      "formats of the e-book library")
activity = books_library.books_library_listening(EBOOKS, 30)
check(activity["totals"] == {"listening": 0, "reading": 2100, "reader_count": 2, "work_count": 1}
      and [r["label"] for r in activity["top_works"]] == ["Duna"] and len(activity["recent"]) == 3,
      f"activity of the e-book library: reading 900 + 1200, nothing listened {activity['totals']}")
audio_activity = books_library.books_library_listening(AUDIO, 30)
check(audio_activity["totals"]["listening"] == 9600 and audio_activity["totals"]["reading"] == 0
      and audio_activity["totals"]["work_count"] == 2,
      f"activity of the audiobook library: 1800+1800+3600+2400 = 9600 s, two works {audio_activity['totals']}")
ebooks_only_analysis = stats.books_analysis(30, library_id=EBOOKS)
check(stats.books_play_method(ebooks_only_analysis) == {"direct": 0, "transcoded": 0, "reasons": []},
      "no audiobook: direct and transcoded are zero, not an error")

print()
print("--- I) the book page ---")
detail = stats.book_detail(ids["Kapitola 2"])
# any file leads to the work: 3 files, length (24+30+36) * 1e9 ticks = 9000 s, size 72 000 000, listening 3600 s, one reader
check(detail is not None and len(detail["files"]) == 3 and detail["seconds"] == 9000 and detail["size"] == 72_000_000
      and detail["listening"] == 3600 and detail["reading"] == 0 and [r["seconds"] for r in detail["readers"]] == [3600]
      and [f["name"] for f in detail["files"]] == ["Kapitola 1", "Kapitola 2", "Kapitola 3"],
      f"the page of an audiobook of three files: {detail and (len(detail['files']), detail['seconds'], detail['size'])}")
check(stats.book_detail(ids["Kapitola 1"])["work"]["id"] == stats.book_detail(ids["Kapitola 3"])["work"]["id"],
      "every file leads to the same work")
ebook = stats.book_detail(ids["Duna"])
check(ebook["reading"] == 2100 and ebook["listening"] == 0 and ebook["seconds"] == 0 and len(ebook["readers"]) == 2,
      f"an e-book: reading 2100 s from two readers, no length ({ebook['reading']})")
check(stats.book_detail("neexistuje") is None, "an unknown id is None")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 1 WHERE id = ?", (ids["Duna"],))
    conn.commit()
check(stats.book_detail(ids["Duna"])["work"]["missing"] and len(stats.books_library_works()) == 7,
      "a vanished book keeps its page but leaves the library")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type = 'Book'")
    conn.commit()
check(not stats.reading_reported() and stats.books_numbers(stats.books_analysis(30))["reading"] == 0,
      "without a row of the kind Book reading is not reported")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
