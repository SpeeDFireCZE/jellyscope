# -*- coding: utf-8 -*-
r"""Readarr's books and their counterparts in the books library (links from Readarr's cards).

Readarr knows a book by its own id and title; the history Jellyscope fetches has nothing to find it
by in the library. So the sync also fetches Readarr's list of books and authors (the table
`readarr_title`, one row per book) and pairs it with the books library Jellyscope already has (the
table `book`). Guarded:

A) the list of books is stored (the author's name from the list of authors, the ISBN-13 of every
   edition joined by a bar, the ASINs, whether an edition is an e-book, a broken record is skipped);
B) a book pairs by an ISBN-13 of any of its editions (the library's ISBN may carry hyphens or be an
   ISBN-10), else by its title and its author folded the way the books section folds them
   (`book.title_key`: case and diacritics do not matter); a book outside the library has an empty
   counterpart;
C) a library without books, a book that went missing from the library, a book of the same title by
   another author: no pairing, no error; the pairing is recomputed on every sync; a server without
   books is no error either;
D) the list is fetched the first time, after a day, after an upgrade and when the history brings an
   unknown book - not on every sync; a failed list brings neither the history sync nor the stored
   books down;
E) deleting the downloaded data deletes the books too;
F) odd ids (digits outside ASCII, an ISBN that is no ISBN).

Readarr does not run: the answers are built by hand in the shape of the API v1 (tests/readarr_responses/).

Run:
    .venv/bin/python tests/test_readarr_titles.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "readarr-titles.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import READARR_RESPONSES, connect_readarr, fill_readarr, no_network, readarr_client, readarr_handler, recorded_response  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import books, db  # noqa: E402
from jellyscope.arr import readarr  # noqa: E402

failures = 0
db.init_db()
no_network()      # no app other than Readarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def make_stale() -> None:
    """The list of books is "old" (fetched long ago) - the next sync fetches it again."""
    db.set_setting("arr_readarr_titles", "2026-01-01 00:00:00")
    db.forget_settings()


def titles() -> dict[int, dict]:
    return {r["title_id"]: dict(r) for r in db.query_all("SELECT * FROM readarr_title ORDER BY title_id")}


BOOKS = recorded_response(READARR_RESPONSES, "book")      # 11 and 12 by the first author, 21 and 22 by the second
AUTHORS = recorded_response(READARR_RESPONSES, "author")
EDITIONS = recorded_response(READARR_RESPONSES, "edition")      # every edition of every book (the list of books carries the monitored one only)
ISBNS_11 = [e["isbn13"] for e in EDITIONS if e["bookId"] == 11]
NOW = db.utcnow()


def library(rows: list[tuple]) -> None:
    """The books library: (id, title, author, isbn, missing); the title_key is made the way the books sync makes it."""
    with db.connect() as conn:
        conn.execute("DELETE FROM book")
        for item_id, title, author, isbn, missing in rows:
            conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, isbn, missing, synced_at) VALUES (?, 'book', ?, ?, ?, ?, ?, ?, ?)",
                         (item_id, title, title, author, books.book_title_key(title, author), isbn, missing, NOW))
        conn.commit()


requests: list[str] = []
handlers = {"readarr": readarr_handler(requests)}
substitute(readarr, "ReadarrClient", readarr_client(lambda request: handlers["readarr"](request)))

print("--- A) the list of books is stored on a sync ---")
connect_readarr()
fill_readarr()
rows = titles()
check(len(rows) == len(BOOKS) == 4, f"every book of Readarr ({len(rows)})")
check(rows[11]["name"] == "Válka s mloky" and rows[11]["author"] == "Karel Čapek" and rows[11]["author_id"] == 1,
      f"a book: the title, the author's name from the list of authors and its id ({rows[11]})")
check(rows[11]["isbns"] == "|".join(ISBNS_11) and rows[11]["asins"] == "B00A000001" and rows[11]["foreign_book_id"] == "200011",
      f"the ISBN-13 of every edition joined by a bar, the ASINs, the foreign id ({rows[11]['isbns']}, {rows[11]['asins']}, {rows[11]['foreign_book_id']})")
check(rows[11]["is_ebook"] == 1 and rows[21]["is_ebook"] == 0 and rows[12]["isbns"] == "",
      f"an e-book edition marks the book, a book without an ISBN has none ({rows[11]['is_ebook']}, {rows[21]['is_ebook']}, {rows[12]['isbns']!r})")
check(rows[11]["released_at"] == "1936-01-01 00:00:00", f"the release date in UTC ({rows[11]['released_at']})")
check(rows[11]["added_at"] == "2026-11-12 20:50:10" and rows[21]["added_at"] == "2026-11-12 20:51:31", f"the time the book came into Readarr ({rows[11]['added_at']}, {rows[21]['added_at']})")
check(requests.count("book") == 1 and requests.count("author") == 1 and requests.count("edition") == 1,
      f"one request for the list of books, one for the authors and one for the editions ({requests})")
check(len(BOOKS[0]["editions"]) == 1 and len(ISBNS_11) == 2 and rows[11]["isbns"].count("|") == 1,
      "the list of books carries one edition, yet both ISBNs of the book are stored: the other edition comes from the edition request")
check(all(r["synced_at"] for r in rows.values()), "the time of the sync is stored")
parsed = readarr.title_rows(
    [{"id": 5, "title": "A", "authorId": 7, "foreignBookId": "9", "releaseDate": "2020-01-02T00:00:00Z", "added": "0001-01-01T00:00:00Z",
      "editions": [{"isbn13": "978-80-7432-255-6", "asin": "b00a000001", "isEbook": True}, {"isbn13": "nesmysl"}, {"x": 1}, "x", {"isbn13": 9788074322556},
                   {"isbn13": "9788020417411", "asin": "B00A000001"}],
      "author": {"authorName": "Embedded"}},
     {"id": "x", "title": "B"}, "nesmysl", {"title": "bez id"}, {"id": 6, "title": None, "editions": None, "releaseDate": "0001-01-01T00:00:00Z"}],
    [{"id": 7, "authorName": "From list"}])
check([(r["title_id"], r["name"], r["author"], r["isbns"], r["asins"], r["is_ebook"], r["released_at"], r["added_at"]) for r in parsed]
      == [(5, "A", "From list", "9788074322556|9788020417411", "B00A000001", 1, "2020-01-02 00:00:00", None), (6, "", "", "", "", 0, None, None)],
      f"a broken record is skipped, the ISBN loses its hyphens and is stored once, the ASIN is upper case, an empty .NET date is unknown, the list of authors wins over the embedded author ({parsed})")
check(readarr.title_rows([{"id": 8, "title": "E", "author": {"authorName": "Embedded"}}])[0]["author"] == "Embedded",
      "without the list of authors the author embedded in the book is used")

print()
print("--- B) a book pairs with the library ---")
library([("jf-valka", "A title the library has otherwise", "Karel Čapek (CZ)", "978-80-204-1741-1", 0),    # by the 2nd edition's ISBN, with hyphens
         ("jf-nemoc", "BILA NEMOC", "KAREL CAPEK", "", 0),                                                  # by the title and the author, without case and diacritics
         ("jf-mort", "Mort", "Other Author", "", 0),                                                       # the same title, another author
         ("jf-straze", "Stráže! Stráže!", "Terry Pratchett", "", 1)])                                      # the right title but gone from the library
asyncio.run(readarr.synchronize())
rows = titles()
check(rows[11]["jellyfin_id"] == "jf-valka", f"a book by an ISBN-13 of any of its editions, though its title and author differ ({rows[11]['jellyfin_id']})")
check(rows[12]["jellyfin_id"] == "jf-nemoc", f"a book without a shared ISBN by its title and its author, case and diacritics do not matter ({rows[12]['jellyfin_id']})")
check(rows[21]["jellyfin_id"] == "", "a book of the same title by another author is not its counterpart")
check(rows[22]["jellyfin_id"] == "", "a book that went missing from the library is not paired")
check(books.book_title_key("Bílá nemoc", "Karel Čapek") == books.book_title_key("BILA NEMOC", "KAREL CAPEK"), "the key folds the way the books section does")
# An ISBN-10 of the library is the same book as the ISBN-13 of the edition (978 + the same nine digits).
library([("jf-mort10", "Elsewhere", "Nobody", "0552131067", 0), ("jf-mort", "Mort", "Terry Pratchett", "", 0)])
asyncio.run(readarr.synchronize())
check(titles()[21]["jellyfin_id"] == "jf-mort10", f"an ISBN-10 pairs with the ISBN-13, and the ISBN wins over a title match ({titles()[21]['jellyfin_id']})")
check(titles()[22]["jellyfin_id"] == "", "a library book is no counterpart of a book it has nothing in common with")
# A wrong ISBN in the library but the right title and author still pairs by the name.
library([("jf-x", "Valka s mloky", "Karel Capek", "9780000000002", 0)])
asyncio.run(readarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-x", "an ISBN that matches nothing does not stop the pairing by the title")
# Of several library books with one key (a multi-file audiobook) the first by id holds.
library([("jf-b", "Valka s mloky", "Karel Capek", "", 0), ("jf-a", "Valka s mloky", "Karel Capek", "", 0)])
asyncio.run(readarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-a", "where several library books fit, the first by id holds (the same choice on every run)")

print()
print("--- C) no books, a book that left the library ---")
library([])
result = asyncio.run(readarr.synchronize())
check("new_count" in result and all(r["jellyfin_id"] == "" for r in titles().values()) and len(titles()) == 4,
      "a library without books: no pairing, no error, the books stay")
library([("jf-valka", "Válka s mloky", "Karel Čapek", "", 0)])
asyncio.run(readarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-valka", "the book comes into the library: paired on the next sync")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 1")
    conn.commit()
asyncio.run(readarr.synchronize())
check(titles()[11]["jellyfin_id"] == "", "the book left the library: it loses its pairing (a link would lead nowhere)")
handlers["readarr"] = readarr_handler(requests, instead={"book": httpx.Response(200, json=[]), "author": httpx.Response(200, json=[]),
                                                         "history/since": httpx.Response(200, json=[])})
make_stale()
result = asyncio.run(readarr.synchronize())
check("new_count" in result and readarr.sync_state()["result"] == "ok" and len(titles()) == 4,
      "a Readarr without books answers with empty lists: no error, the stored books stay")
handlers["readarr"] = readarr_handler(requests)

print()
print("--- D) when the list is fetched ---")
library([("jf-valka", "Válka s mloky", "Karel Čapek", "", 0)])
before = requests.count("book")
asyncio.run(readarr.synchronize())
asyncio.run(readarr.synchronize())
check(requests.count("book") == before, f"further syncs the same day do not fetch the list ({requests.count('book') - before}x)")
check(titles()[11]["jellyfin_id"] == "jf-valka", "but the books still pair with the library on every sync")
with db.connect() as conn:      # the history brings a book Jellyscope does not know
    conn.execute("DELETE FROM readarr_title WHERE title_id = 22")
    conn.commit()
asyncio.run(readarr.synchronize())
check(requests.count("book") == before + 1 and 22 in titles(), "an unknown book in the history: the list is fetched at once")
make_stale()
asyncio.run(readarr.synchronize())
check(requests.count("book") == before + 2, "after a day the list is refreshed")
db.set_setting("arr_readarr_titles_version", "")
db.forget_settings()
asyncio.run(readarr.synchronize())
check(requests.count("book") == before + 3 and db.get_setting("arr_readarr_titles_version", "") != "", "a list fetched by an older version is fetched once again")
check("arr_readarr_titles" in db.UNLOGGED_SETTINGS and "arr_readarr_titles_version" in db.UNLOGGED_SETTINGS, "the list's marks do not flood the settings log")

handlers["readarr"] = readarr_handler(requests, instead={"book": httpx.Response(500, text="error")})
make_stale()
before = requests.count("book")
result = asyncio.run(readarr.synchronize())
check("new_count" in result and readarr.sync_state()["result"] == "ok" and len(titles()) == 4 and requests.count("book") == before + 1,
      "a failed list of books does not bring the history sync down and leaves the stored books")
asyncio.run(readarr.synchronize())
check(requests.count("book") == before + 2, "after a failed fetch the list is tried at the very next sync")
handlers["readarr"] = readarr_handler(requests, instead={"author": httpx.Response(500, text="error")})
make_stale()
asyncio.run(readarr.synchronize())
check(len(titles()) == 4 and readarr.sync_state()["result"] == "ok" and titles()[11]["author"] == "Karel Čapek",
      "a failed list of authors changes nothing either (the books keep the authors embedded in them)")
handlers["readarr"] = readarr_handler(requests, instead={"book": httpx.Response(200, json=BOOKS[:1])})
make_stale()
asyncio.run(readarr.synchronize())
check(len(titles()) == 4, "a book the app no longer has stays in the table (the history still points at it)")
handlers["readarr"] = readarr_handler(requests)
original = readarr.titles._pair_books
readarr.titles._pair_books = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected"))      # type: ignore[assignment]
try:
    result = asyncio.run(readarr.synchronize())
except Exception as error:      # noqa: BLE001
    result = {"exception": repr(error)}
finally:
    readarr.titles._pair_books = original
db.forget_settings()
check("new_count" in result and readarr.sync_state()["result"] == "ok", f"an unexpected error in the title step does not bring the history sync down ({result})")

print()
print("--- D2) the editions are asked for apart ---")
handlers["readarr"] = readarr_handler(requests, instead={"edition": httpx.Response(500, text="boom")})
make_stale()
library([("jf-valka", "Elsewhere", "Nobody", "978-80-204-1741-1", 0)])
asyncio.run(readarr.synchronize())
check(titles()[11]["isbns"] == ISBNS_11[0] and titles()[11]["jellyfin_id"] == "" and readarr.sync_state()["result"] == "ok",
      "a failed edition request: the book keeps the monitored edition's ISBN from the list, no pairing by the second one, the sync is fine")
handlers["readarr"] = readarr_handler(requests)
make_stale()
asyncio.run(readarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-valka", "the second edition's ISBN pairs only when it comes from the edition request")
seen_params: list[list[str]] = []
inner = readarr_handler(requests)


def spy(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/api/v1/edition"):
        seen_params.append(request.url.params.get_list("bookId"))
    return inner(request)


handlers["readarr"] = spy
many = [{"id": i, "title": f"B{i}", "authorId": 1, "editions": []} for i in range(1, 251)]
handlers["readarr"] = lambda request: (httpx.Response(200, json=many) if request.url.path.endswith("/api/v1/book") else spy(request))
make_stale()
asyncio.run(readarr.synchronize())
check([len(p) for p in seen_params] == [100, 100, 50] and seen_params[0][0] == "1" and seen_params[2][-1] == "250",
      f"the ids go in chunks of 100 ({[len(p) for p in seen_params]})")


async def editions_error() -> str:
    async with readarr.ReadarrClient("http://readarr.test", "tajny-klic",
                                     transport=httpx.MockTransport(lambda r: httpx.Response(500, text="chyba, klíč tajny-klic"))) as c:
        try:
            await c.editions([11])
        except readarr.ReadarrError as error:
            return str(error)
    return ""


message = asyncio.run(editions_error())
check("500" in message and "tajny-klic" not in message, f"an error of the edition request goes out without the key ({message})")
handlers["readarr"] = readarr_handler(requests)

print()
print("--- E) deleting the downloaded data ---")
check(readarr.delete_data() > 0 and titles() == {} and (db.forget_settings() or True)
      and db.get_setting("arr_readarr_titles", "") == "" and db.get_setting("arr_readarr_titles_version", "") == "",
      "deleting Readarr's data deletes its books and the list's fetch mark")
check(db.query_value("SELECT COUNT(*) FROM book") == 1, "the books library itself is not touched")

print()
print("--- F) odd ids ---")
try:
    odd: object = readarr.title_rows([{"id": "²", "title": "x"}, {"id": 1, "title": "Divný", "authorId": "²", "foreignBookId": "٣",
                                                                        "editions": [{"isbn13": "٣" * 13}, {"isbn13": "978807432255"}, {"isbn13": "97880743225567"}]},
                                     {"id": 2, "title": "Dobrý", "editions": [{"isbn13": "9788074322556 "}]}])
except ValueError as error:
    odd = f"exception {error}"
check(isinstance(odd, list) and [(r["title_id"], r["author_id"], r["isbns"]) for r in odd] == [(1, None, ""), (2, None, "9788074322556")],
      f"ids and ISBNs that are none are stored empty and the list does not crash ({odd})")
check(readarr.titles._isbn13("0552131067") == "9780552131063" and readarr.titles._isbn13("080442957X") == "9780804429573"
      and readarr.titles._isbn13("978-80-7432-255-6") == "9788074322556" and readarr.titles._isbn13("12345") == "" and readarr.titles._isbn13(None) == "",
      "an ISBN-10 (also with X) converts to an ISBN-13, hyphens go, nonsense is empty")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
