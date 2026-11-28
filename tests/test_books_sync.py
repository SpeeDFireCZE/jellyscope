# -*- coding: utf-8 -*-
r"""Books: the sync of e-books and audiobooks from Jellyfin, its task, the content kind, the demo.

Jellyfin does not run: the answers are built by hand (tests/books_responses/, see tests/_books.py). It
guards:

G) no books library: nothing is fetched, there is no section; two runs at once - the second is refused;
A) the first run: a full pass, the columns (author from People, AlbumArtist or Artists, the title of a
   multi-file audiobook, format, size, ISBN, runtime), a second run adds nothing;
B) incremental: reading stops at the first page where everything is known;
C) a full pass after 24 hours: what vanished gets `missing`, what came back loses it;
D) an error in the middle of a full pass: nothing is marked missing;
E) incomplete metadata;
F) pairing: imported history without a type whose item is a book;
H) the task `books_sync`;
K) the content kind (disks, totals) and the demo data;
L) a library of the type "books" is stored by the library sync, video items stay as they were.

Spec: docs/superpowers/specs/2026-11-23-books-design.md

Run:
    .venv/bin/python tests/test_books_sync.py
"""
from __future__ import annotations

import asyncio
import copy
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "books-sync.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "tajny-klic-jellyfinu"

from _package import substitute  # noqa: E402
from _books import all_items, fake_jellyfin_books, item, recorded  # noqa: E402
from jellyscope import books, content_kinds, db, demodata, scanner, tasks  # noqa: E402
from jellyscope.books import sync  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def count(where: str = "1 = 1") -> int:
    return int(db.query_value(f"SELECT COUNT(*) FROM book WHERE {where}") or 0)


def row(name: str) -> dict:
    return db.query_one("SELECT * FROM book WHERE name = ?", (name,)) or {}


def run_sync(client: type, **kwargs) -> dict:
    restore = substitute(sync, "JellyfinClient", client)
    try:
        return asyncio.run(books.synchronize(**kwargs))
    finally:
        restore()


def clean() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM book")
        conn.execute("DELETE FROM settings WHERE key LIKE 'books_%'")
        conn.commit()


LIBRARIES = recorded("libraries")
BOOKS_LIB, AUDIO_LIB = LIBRARIES[0]["ItemId"], LIBRARIES[1]["ItemId"]

print("--- G) no books library, two at once ---")
film_libraries = [lib for lib in LIBRARIES if lib.get("CollectionType") != "books"]
check(run_sync(fake_jellyfin_books(libraries=film_libraries)) == {"library_count": 0} and count() == 0
      and not books.has_books(), "Jellyfin without a books library: nothing is fetched and there are no books")
check(db.get_setting("books_first_sync", "") == "1", "a finished run marks the first sync (content kinds: from then on books are measured)")


async def two_at_once() -> list:
    return await asyncio.gather(books.synchronize(), books.synchronize(), return_exceptions=True)


restore = substitute(sync, "JellyfinClient", fake_jellyfin_books(libraries=film_libraries))
results = asyncio.run(two_at_once())
restore()
check(sum(isinstance(r, books.BooksError) and "already running" in str(r) for r in results) == 1,
      f"two runs at once: the second is refused ({results})")
check(not books.sync_running(), "and nothing runs afterwards")

print()
print("--- A) the first run: everything ---")
calls: list = []
result = run_sync(fake_jellyfin_books(calls=calls))
check(result.get("full") is True and result.get("item_count") == 10 and result.get("library_count") == 2, f"the first run is a full pass ({result})")
check(count() == 10 and count("kind = 'book'") == 4 and count("kind = 'audiobook'") == 6,
      f"4 e-books and 6 audiobook items ({count()}, {count('kind = ' + repr('book'))})")
check(books.has_books(), "after the sync there are books")
dune = row("Duna")
check((dune.get("kind"), dune.get("title"), dune.get("author"), dune.get("series"), dune.get("year"), dune.get("genres"))
      == ("book", "Duna", "Frank Herbert", "Kroniky Duny", 1965, "Science Fiction|Klasika"),
      f"an e-book: author from People (Type Author, not the translator), series, year, genres ({dune.get('author')!r})")
check((dune.get("format"), dune.get("size_bytes"), dune.get("runtime_ticks"), dune.get("isbn"), dune.get("cover_tag"))
      == ("epub", 1_850_000, None, "9788025702345", "cover-duna") and '"GoogleBooks"' in dune.get("provider_ids", "")
      and dune.get("path", "").endswith("Duna.epub") and dune.get("library_id") == BOOKS_LIB,
      f"format by the file extension, size, no runtime, ISBN and provider ids, cover, path, library ({dune})")
check(dune.get("added_at", "").startswith("2026-11-20") and dune.get("missing") == 0 and dune.get("synced_at"),
      "added time in UTC, not missing")
check(row("Algoritmy bez vzorců").get("format") == "pdf" and row("Algoritmy bez vzorců").get("author") == ""
      and row("Algoritmy bez vzorců").get("isbn") == "", "an e-book without an author or an ISBN has empty ones")
check(row("Příběhy z Trollí hory").get("author") == "" and row("Příběhy z Trollí hory").get("format") == "cbz",
      "an illustrator is not an author")
parts = db.query_all("SELECT * FROM book WHERE title = 'Cesta na jih' ORDER BY name")
check(len(parts) == 3 and {p["author"] for p in parts} == {"Karel Vypravěč"} and {p["kind"] for p in parts} == {"audiobook"}
      and [p["name"] for p in parts] == ["Kapitola 1", "Kapitola 2", "Kapitola 3"]
      and all(p["library_id"] == AUDIO_LIB for p in parts) and {p["format"] for p in parts} == {"mp3"},
      "a multi-file audiobook: three items, one title (the Album) and one author (AlbumArtist)")
check(sum(p["runtime_ticks"] for p in parts) == 90_000_000_000, "its runtime is the sum of the parts")
check(books.book_title_key("Cesta na jih", "Karel Vypravěč") == books.book_title_key(" cesta na JIH ", "karel vypravěč")
      and books.book_title_key("Cesta na jih", "A") != books.book_title_key("Cesta na jih", "B"),
      "book_title_key: the title and the author, case and spaces do not matter")
distinct = db.query_value("SELECT COUNT(DISTINCT title_key) FROM book WHERE kind = 'audiobook'")
check(distinct == 4, f"the stored key counts the multi-file audiobook once ({distinct} audiobooks of 6 items)")
check(books.book_title_key("Rur", "Čapek") == books.book_title_key(" RUR ", "ČAPEK") == books.book_title_key("Rúr", "Capek"),
      "the key folds case and diacritics the same way on every database")
check(row("Duna").get("title_key") == books.book_title_key("Duna", "Frank Herbert"), "the row stores its key")
capek = [item("Kapitola 1", Id=f"capek-{n}", Name=f"Díl {n}", Album="R.U.R.", AlbumArtist=name,
              DateCreated=f"2026-11-2{n}T08:00:00.0000000Z") for n, name in ((1, "Čapek"), (2, "ČAPEK"))]
run_sync(fake_jellyfin_books(items=[*capek, *all_items()]))
check(db.query_value("SELECT COUNT(DISTINCT title_key) FROM book WHERE title = 'R.U.R.'") == 1
      and db.query_value("SELECT COUNT(*) FROM book WHERE title = 'R.U.R.'") == 2,
      "parts tagged Čapek and ČAPEK are one work (decided in Python, not by LOWER() of the database)")
with db.connect() as conn:
    conn.execute("DELETE FROM book WHERE id LIKE 'capek-%'")
    conn.commit()
hobit = row("Hobit")
check((hobit.get("title"), hobit.get("author"), hobit.get("format"), hobit.get("runtime_ticks"), hobit.get("series"))
      == ("Hobit", "J. R. R. Tolkien", "m4b", 396_000_000_000, "Středozem") and hobit.get("provider_ids") and not hobit.get("isbn"),
      f"an m4b audiobook: AlbumArtist wins over Artists, the format from the file, no Album = the name ({hobit.get('author')!r})")
check(row("Kniha bez autora").get("author") == "Jen Interpret", "only Artists: the first of them is the author")
anonymous = row("Anonymní nahrávka")
check(anonymous.get("author") == "" and anonymous.get("title") == "Anonymní nahrávka", "no author at all: empty, not a crash")
check(row("Staré pověsti").get("author") == "Alois Jirásek" and row("Staré pověsti").get("format") == "mobi", "another e-book")
types = {t for t, *_ in calls}
check(types == {"Book,AudioBook"}, f"it asks for both types at once ({types})")
calls.clear()
second = run_sync(fake_jellyfin_books(calls=calls))
check(second.get("new_count") == 0 and second.get("full") is False and count() == 10 and {s for _, s, _, _ in calls} == {0},
      f"a second run adds nothing and reads only the first page ({second}, {calls})")

print()
print("--- B) incremental ---")
new_book = item("Duna", Id="nova-kniha-1", Name="Nová kniha", DateCreated="2026-11-22T19:00:00.0000000Z")
others = all_items()
calls.clear()
restore = substitute(sync, "PAGE_SIZE", 3)
result = run_sync(fake_jellyfin_books(items=[new_book, *others], calls=calls))
check(result.get("new_count") == 1 and row("Nová kniha").get("title") == "Nová kniha", f"a new book comes ({result})")
starts = sorted({s for _, s, _, lib in calls if lib == BOOKS_LIB})
check(starts == [0, 3], f"reading stops at the first page where everything is known ({starts})")
restore()

print()
print("--- C) a full pass after 24 hours ---")
now = datetime.now(UTC)
without_one = [new_book, *others]
gone = next(i for i in without_one if i["Name"] == "Staré pověsti")
without_one.remove(gone)
# 22, not 23: a full pass is due an hour early (FULL_PASS_SLACK), a daily task starts a minute earlier or later.
db.set_setting("books_full_pass", (now - timedelta(hours=22)).strftime("%Y-%m-%d %H:%M:%S"))
result = run_sync(fake_jellyfin_books(items=without_one))
check(result.get("full") is False and count("missing = 1") == 0, "22 hours after a full pass: incremental only, nothing missing")
db.set_setting("books_full_pass", (now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S"))
result = run_sync(fake_jellyfin_books(items=without_one))
missing = {r["name"] for r in db.query_all("SELECT name FROM book WHERE missing = 1")}
check(result.get("full") is True and missing == {"Staré pověsti"} and result.get("missing") == 1,
      f"after 24 hours a full pass: the vanished book has `missing` ({missing})")
check(count() == 11 and books.has_books(), "its row stays (history and charts keep the name)")
run_sync(fake_jellyfin_books(items=[new_book, *others]), full=True)
check(count("missing = 1") == 0, "a book that came back loses `missing`")
renamed = copy.deepcopy([new_book, *others])
next(i for i in renamed if i["Name"] == "Hobit")["Name"] = "Hobit (nové vydání)"
run_sync(fake_jellyfin_books(items=renamed), full=True)
check(row("Hobit (nové vydání)").get("title") == "Hobit (nové vydání)" and count("missing = 1") == 0, "a full pass fixes the name as Jellyfin has it")
run_sync(fake_jellyfin_books(items=[new_book, *others]), full=True)

print()
print("--- D) an error in the middle of a full pass ---")
last_full = db.get_setting("books_full_pass", "")
before_missing = count("missing = 1")
restore = substitute(sync, "PAGE_SIZE", 2)
try:
    run_sync(fake_jellyfin_books(items=[i for i in [new_book, *others] if i["Name"] != "Duna"], fail_at=4), full=True)
    raised = False
except books.BooksError as error:
    raised = "tajny-klic-jellyfinu" not in str(error)
restore()
check(raised and count("missing = 1") == before_missing and db.get_setting("books_full_pass", "") == last_full,
      "an error in a full pass: nothing marked missing, the time of the last full pass stays")
check(books.sync_state().get("result") == "error", f"the state says the error ({books.sync_state()})")

print()
print("--- E) incomplete metadata ---")
bare = {"Name": "Holá kniha", "Id": "bare-1", "Type": "Book", "DateCreated": "2026-11-21T10:00:00.0000000Z"}
run_sync(fake_jellyfin_books(items=[bare, *all_items()]), full=True)
bare_row = row("Holá kniha")
check((bare_row.get("kind"), bare_row.get("author"), bare_row.get("format"), bare_row.get("size_bytes"), bare_row.get("runtime_ticks"),
       bare_row.get("genres"), bare_row.get("year"), bare_row.get("path")) == ("book", "", "", None, None, "", None, ""),
      f"a book without sources, genres or a year: empty values, no error ({bare_row})")
check(bare_row.get("library_id") in (BOOKS_LIB, AUDIO_LIB, None) and bare_row.get("missing") == 0, "and it is stored")
db.set_setting("books_full_pass", "")

print()
print("--- F) pairing of imported history ---")
now_text = now.strftime("%Y-%m-%d %H:%M:%S")
hobit_id, dune_id = row("Hobit").get("id"), row("Duna").get("id")


def dashed(value: str) -> str:
    return "-".join((value[:8], value[8:12], value[12:16], value[16:20], value[20:]))


with db.connect() as conn:
    for key, item_id, item_type in (("imp-1", hobit_id, None), ("imp-2", dashed(dune_id), ""), ("imp-3", hobit_id, "Movie")):
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                     " watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, 'u1', 'Jana', ?, 'Převzaté', ?, ?, ?, 300, 0, 0, 0)",
                     (key, item_id, item_type, now_text, now_text))
    conn.commit()
result = run_sync(fake_jellyfin_books(items=[bare, *all_items()]), full=True)
paired = {r["session_key"]: (r["item_id"], r["item_type"]) for r in db.query_all(
    "SELECT session_key, item_id, item_type FROM playback WHERE session_key LIKE 'imp-%'")}
check(paired == {"imp-1": (hobit_id, "AudioBook"), "imp-2": (dune_id, "Book"), "imp-3": (hobit_id, "AudioBook")} and result.get("paired") == 3,
      f"history without a type becomes AudioBook / Book (also an id with dashes); a row typed Movie (not in items) too ({paired})")

print()
print("--- H) the task ---")
task = tasks.TASKS.get("books_sync")
check(task is not None and task.name == "Books sync" and task.default_minutes == 60 and task.interval_setting == "books_sync_minutes"
      and task.log_kind == "books" and tasks.is_enabled(task) and not tasks.is_daily(task),
      "the task books_sync: 60 minutes (books_sync_minutes), on")
restore = substitute(sync, "JellyfinClient", fake_jellyfin_books())
outcome = asyncio.run(tasks.run_now("books_sync"))
restore()
check(outcome["status"] == "ok" and "Knihy" in outcome["message"] and (scanner.last_scan("books") or {}).get("status") == "ok",
      f"the task's run is in the task log ({outcome})")
restore = substitute(sync, "JellyfinClient", fake_jellyfin_books(libraries=film_libraries))
outcome = asyncio.run(tasks.run_now("books_sync"))
restore()
check(outcome == {"status": "ok", "message": "Jellyfin has no books library."}, f"without a books library - the key ({outcome})")
restore = substitute(sync, "JellyfinClient", fake_jellyfin_books(fail_at=0))
outcome = asyncio.run(tasks.run_now("books_sync"))
restore()
check(outcome["status"] == "error" and outcome["message"] and "tajny-klic-jellyfinu" not in outcome["message"],
      f"a Jellyfin error: the task ends with an error and a text ({outcome})")

print()
print("--- libraries gone from Jellyfin ---")
run_sync(fake_jellyfin_books(items=[new_book, bare, *all_items()]), full=True)
db.set_setting("books_vanished_libraries", "")
check(count("missing = 1") == 0, "a full pass with everything: nothing is missing")
run_sync(fake_jellyfin_books(libraries=film_libraries))
check(count("missing = 1") == 0, "a library missing from one run only is not gone yet")
result = run_sync(fake_jellyfin_books(libraries=film_libraries))
check(count("missing = 0") == 0 and result.get("missing", 0) > 0 and not books.has_books(),
      f"missing from two runs in a row: its books are missing, there is no section ({result})")
run_sync(fake_jellyfin_books(items=[new_book, bare, *all_items()]))
check(count("missing = 1") == 0 and books.has_books(), "the library back: the books are back")

# The daily full pass in a run Jellyfin left one of the books libraries out (once - it may be starting): that
# library was not read, so its books are not marked missing. Only _gone_libraries decides, after two runs.
db.set_setting("books_vanished_libraries", "")
only_books = [lib for lib in LIBRARIES if lib.get("ItemId") != AUDIO_LIB]
everything = [new_book, bare, *all_items()]
in_audio = count(f"library_id = '{AUDIO_LIB}' AND missing = 0")
run_sync(fake_jellyfin_books(libraries=only_books, items=everything), full=True)
check(in_audio > 0 and count(f"library_id = '{AUDIO_LIB}' AND missing = 1") == 0,
      f"a full pass without the audiobooks library: its {in_audio} books are not missing after one run")
run_sync(fake_jellyfin_books(libraries=only_books, items=everything), full=True)
check(count(f"library_id = '{AUDIO_LIB}' AND missing = 0") == 0 and count(f"library_id = '{BOOKS_LIB}' AND missing = 0") > 0,
      "left out of two runs in a row: they are missing, the other library stays")
run_sync(fake_jellyfin_books(items=everything), full=True)

print()
print("--- K) the content kind and the demo ---")
kind = content_kinds.BY_KEY.get("books")
check(kind is not None and (kind.table, kind.path, kind.size, kind.added, kind.last_seen, kind.missing)
      == ("book", "path", "size_bytes", "added_at", "synced_at", "missing")
      and kind.label == "Books" and kind.share == "of which books {size}", "the content kind books -> the table book")
totals = content_kinds.totals()["books"]
check(totals["items"] == count("missing = 0") and totals["size"] == int(db.query_value("SELECT SUM(size_bytes) FROM book WHERE missing = 0")),
      f"totals count the books ({totals})")
files = [f for f in content_kinds.current_files() if f[0] == "books"]
check(len(files) == count("missing = 0 AND path <> ''") and all(f[1].startswith("/media/") for f in files),
      f"current files come with their paths ({len(files)})")
check(any(r["kind"] == "books" and r["size"] > 0 for r in content_kinds.history()), "history knows the books")

demodata.wipe()
demodata.seed()
check(books.has_books() and count("kind = 'book'") >= 5 and count("kind = 'audiobook'") >= 5,
      f"the demo has e-books and audiobooks ({count('kind = ' + repr('book'))}, {count('kind = ' + repr('audiobook'))})")
check(db.query_value("SELECT COUNT(*) FROM libraries WHERE collection_type = 'books'") >= 1
      and db.query_value("SELECT COUNT(DISTINCT library_id) FROM book") >= 2, "in libraries of the type books (two of them)")
listening = {r["item_type"]: r["n"] for r in db.query_all(
    "SELECT item_type, COUNT(*) AS n FROM playback WHERE item_type IN ('AudioBook', 'Book') GROUP BY item_type")}
check(listening.get("AudioBook", 0) > 50 and listening.get("Book", 0) > 20, f"audiobook listening and reading in playback ({listening})")
orphans = db.query_value("SELECT COUNT(*) FROM playback p WHERE p.item_type IN ('AudioBook', 'Book')"
                         " AND NOT EXISTS (SELECT 1 FROM book b WHERE b.id = p.item_id)")
check(orphans == 0, "every listening row has its book in the library")
multi = db.query_value("SELECT COUNT(*) FROM (SELECT title, author FROM book WHERE kind = 'audiobook' GROUP BY title, author HAVING COUNT(*) > 1) t")
check(multi >= 1, "the demo has a multi-file audiobook")
demodata.wipe()
check(count() == 0, "the demo's wipe clears the books table too")

print()
print("--- L) the library sync stores a books library ---")
client = fake_jellyfin_books()()
returned = asyncio.run(scanner._sync_libraries(client))
stored = {r["id"]: r for r in db.query_all("SELECT * FROM libraries")}
check(stored[BOOKS_LIB]["collection_type"] == "books" and stored[AUDIO_LIB]["name"] == "Audioknihy"
      and [lib["id"] for lib in returned] == [LIBRARIES[2]["ItemId"]],
      "both libraries are stored; only the video library goes on to the items")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
