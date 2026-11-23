# -*- coding: utf-8 -*-
r"""The books library in the Library: tabs, a book grid, a book page built like an album, the Library page's section.

Spec: docs/superpowers/specs/2026-11-23-books-design.md.

* /library/<id of a books library> has three tabs - Overview (numbers, formats, decades, genres, the largest books),
  Books (a grid with covers, search by title, author or series - case and diacritics do not matter, % and _ are
  plain characters -, sorted by added, name, author, year or size, 60 to a page) and Activity (reading and
  listening of the period, readers and the last rows only with "...including who read and listened").
* /book/<id> belongs to the Library: tiles, "About the book", the files of the book, "Who read and listened" only with
  the books_who area. A multi-file audiobook is one book in the grid and on its page. An unknown id is 404, a
  vanished book keeps its page with a sentence.
* The Library page has the libraries' tiles (leading to the tabs) and a Books section next to the music one.
* A library of e-books alone and one with no listening open every tab.

The two books libraries of tests/books_responses/: "Knihy" (e-books) and "Audioknihy" (audiobooks).

Run:
    .venv/bin/python tests/test_books_library.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "books-library.db")
os.environ["SECRET_KEY"] = "test-key-books-library"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login, tile  # noqa: E402
from _books import BOOK_READERS, fill_books  # noqa: E402
from jellyscope import access, accounts, db, formatting, stats  # noqa: E402
from jellyscope.stats.books_library import library_book_works  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


EBOOKS = "9df755f2b7a9da9df80891c79b9a8a4d"
AUDIO = "5e2022368c574c08ee6f709de93555dc"
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin = login("admin")


def titles(result: dict) -> list[str]:
    return [work["title"] for work in result["works"]]


def names_in(text: str) -> list[str]:
    return [name for name in BOOK_READERS.values() if name in text]


print("--- without books ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, missing_runs) VALUES (?, 'Knihy', 'books', '[]', 0)", (EBOOKS,))
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, missing_runs) VALUES (?, 'Audioknihy', 'books', '[]', 0)", (AUDIO,))
    conn.commit()
index = admin.get("/library").text
check('id="books-in-library"' not in index and f'href="/library/{EBOOKS}"' in index,
      "no book yet: the libraries have their tiles (with a link), the Books section of the Library page is not there")
check(all(admin.get(f"/library/{EBOOKS}?tab={tab}").status_code == 200 for tab in ("overview", "books", "activity")),
      "an empty books library opens every tab")

ids = fill_books()
print()
print("--- the Library page ---")
index = admin.get("/library").text
check(f'href="/library/{EBOOKS}"' in index and f'href="/library/{AUDIO}"' in index
      and "Obsah tohoto typu Jellyscope zatím nesbírá." not in index,
      "the two libraries are tiles leading to their page, not tiles of an uncollected type")
tile_ebooks = index.split(f'href="/library/{EBOOKS}"')[1].split("</a>")[0]
tile_audio = index.split(f'href="/library/{AUDIO}"')[1].split("</a>")[0]
# e-books 1 850 000 + 12 400 000 + 48 000 000 + 900 000 = 63 150 000 B = 60,2 MB
check(">4<" in tile_ebooks and "60,2 MB" in tile_ebooks and "Délka audioknih" not in tile_ebooks,
      "the e-books tile: 4 e-books, 60,2 MB, no audio length")
check(">0<" in tile_audio and "Délka audioknih" in tile_audio and f"/image/{ids['Kapitola 1']}?kind=Primary" in tile_audio,
      "the audiobook tile: 0 e-books, the audio length, the cover of the newest book with one")
section = index.split('id="books-in-library"')[1] if 'id="books-in-library"' in index else ""
check(all(f"<!-- card: {key} -->" in section for key in ("books_formats", "books_years", "books_library_genres", "books_largest")),
      "the Library page's Books section has the library's cards of Books")
numbers = stats.books_library_summary()
check(numbers["ebook_count"] == 4 and numbers["audiobook_count"] == 4 and numbers["author_count"] == 5
      and all(f">{value}<" in section for value in ("4", "5")),
      f"with the counts of every books library ({numbers})")
check(index.count("<!-- card: books_formats -->") == 1, "once")
defaults = [o.key for o in access.AREAS if o.default]
reader = accounts.get_by_name("reader")
accounts.save_permissions(reader["id"], access.pack_permissions([a for a in defaults if a != "books"], False))
db.forget_settings()
check('id="books-in-library"' not in login("reader").get("/library").text, "without the Books area no Books section")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()

print()
print("--- the book grid ---")
check(sorted(titles(library_book_works(AUDIO))) == ["Anonymní nahrávka", "Cesta na jih", "Hobit", "Kniha bez autora"],
      "the audiobook library: four books - the three files of Cesta na jih are one")
page = admin.get(f"/library/{AUDIO}?tab=books")
check(page.status_code == 200 and page.text.count("media-card") >= 4 and f'href="/book/{ids["Kapitola 1"]}"' in page.text
      and f"/image/{ids['Kapitola 1']}?kind=Primary" in page.text
      and f'href="/book/{ids["Kapitola 2"]}"' not in page.text and "Cesta na jih" in page.text,
      "the grid: one card for Cesta na jih, leading to the page of its first file, with the cover")
for search in ("duna", "DUNA", "Herbert"):
    check(titles(library_book_works(EBOOKS, search=search)) == ["Duna"], f"search {search!r} finds Duna")
check(titles(library_book_works(EBOOKS, search="jirasek")) == ["Staré pověsti"], "search without diacritics finds the author Jirásek")
check(titles(library_book_works(EBOOKS, search="Kroniky")) == ["Duna"], "search by the series")
check(library_book_works(EBOOKS, search="%")["total"] == 0 and library_book_works(EBOOKS, search="_")["total"] == 0,
      "% and _ are plain characters, not wildcards")
check(f'href="/book/{ids["Duna"]}"' in admin.get(f"/library/{EBOOKS}?tab=books&search=DUNA").text, "the page searches too")
by_year = [w["year"] for w in library_book_works(EBOOKS, sort="year")["works"]]
check(by_year == [2019, 1965, 1894, None], f"sorted by year, newest first, unknown last ({by_year})")
by_size = [w["size_bytes"] for w in library_book_works(EBOOKS, sort="size")["works"]]
check(by_size == [48_000_000, 12_400_000, 1_850_000, 900_000], f"sorted by size, largest first ({by_size})")
check(titles(library_book_works(EBOOKS, sort="name")) == ["Algoritmy bez vzorců", "Duna", "Příběhy z Trollí hory", "Staré pověsti"],
      "sorted by name")
# the author as written: "Alois Jirasek" (Stare povesti) before "Frank Herbert" (Duna); the two without an author last, by title
check(titles(library_book_works(EBOOKS, sort="author")) == ["Staré pověsti", "Duna", "Algoritmy bez vzorců", "Příběhy z Trollí hory"],
      "sorted by author, the books without one last")
check(titles(library_book_works(EBOOKS, sort="nonsense")) == titles(library_book_works(EBOOKS, sort="added"))
      == ["Duna", "Algoritmy bez vzorců", "Příběhy z Trollí hory", "Staré pověsti"], "an unknown sort is by added (newest first)")
paged = library_book_works(EBOOKS, sort="name", page=2, per_page=3)
check(titles(paged) == ["Staré pověsti"] and paged["pages"] == 2 and paged["total"] == 4 and paged["page"] == 2,
      "paging: 3 per page, the second page has the fourth")
check(library_book_works(EBOOKS, page=99, per_page=3)["page"] == 2, "a page beyond the end is the last one")

print()
print("--- the book page ---")
detail = admin.get(f"/book/{ids['Kapitola 2']}")
html = detail.text
check(detail.status_code == 200 and all(label in html for label in ("Souborů", "Délka", "Velikost", "Poslech audioknih")),
      "tiles: files, length, size, listening (an audiobook)")
check(html.count('id="file-') == 3 and html.index("Kapitola 1") < html.index("Kapitola 2") < html.index("Kapitola 3"),
      "the three files of the audiobook in order")
check("O knize" in html and "Karel Vypravěč" in html and "MP3" in html and "Audiokniha" in html,
      "About the book: the author, the format")
check(f'href="/library/{AUDIO}?tab=books"' in html, "the book leads back to the grid of its library")
check(any(name in html for name in BOOK_READERS.values()) and "Kdo četl a poslouchal" in html, "the administrator sees who read")
ebook_html = admin.get(f"/book/{ids['Duna']}").text
check("Čas čtení" in ebook_html and "Poslech audioknih" not in ebook_html and "Délka" not in ebook_html.split('<div class="item-layout"')[0],
      "an e-book: reading time, no length")
check("days=" not in re.sub(r'href="/(?:books|book)[^"]*"', "", html.split('<div class="kpi-row">')[0]),
      "no period switch: the book page is about the book, not a period")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()
reader_client = login("reader")
reader_page = reader_client.get(f"/book/{ids['Kapitola 2']}").text
check("Kdo četl a poslouchal" not in reader_page and not names_in(reader_page), "without ...including who read: no readers")
accounts.save_permissions(reader["id"], access.pack_permissions([a for a in defaults if a != "library"], False))
db.forget_settings()
check(login("reader").get(f"/book/{ids['Duna']}", follow_redirects=False).status_code == 403, "without the Library: 403")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()
check(admin.get("/book/neni-takove").status_code == 404 and admin.get("/book/a.b").status_code == 404, "an unknown or wrong id is 404")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 1 WHERE id = ?", (ids["Duna"],))
    conn.commit()
vanished = admin.get(f"/book/{ids['Duna']}")
check(vanished.status_code == 200 and "Už není v Jellyfinu" in vanished.text, "a vanished book keeps its page with a sentence")
check("Duna" not in titles(library_book_works(EBOOKS)), "and is not in the grid")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 0 WHERE id = ?", (ids["Duna"],))
    conn.commit()

print()
print("--- tabs of a books library: Overview, Books, Activity ---")
page = admin.get(f"/library/{EBOOKS}").text
tabs = re.findall(rf'href="/library/{EBOOKS}\?tab=(\w+)"', page)
check(tabs[:3] == ["overview", "books", "activity"] and 'class="active"' in page, f"three tabs, Overview open by default ({tabs[:3]})")
check(all(f"<!-- card: {key} -->" in page for key in ("books_formats", "books_years", "books_library_genres", "books_largest")),
      "the overview has formats, decades, genres and the largest books")
check("Hobit" not in page and "Karel" not in page, "nothing of the other library on the e-book overview")
check("Délka audioknih</p>" in page and re.search(r"Délka audioknih</p>\s*<div class=\"tile-value\">–<", page) is not None,
      "e-books alone: the audiobook length is a dash, not a zero")
audio_overview = admin.get(f"/library/{AUDIO}?tab=overview").text
check("Hobit" in audio_overview and "Duna" not in audio_overview, "the audiobook library's overview is its own")
activity = admin.get(f"/library/{AUDIO}?tab=activity&days=30").text
# audiobooks 30 days: 1800+1800+3600+2400 = 9600 s, two works (Cesta na jih, Hobit), 3 readers
check(all(label in activity for label in ("Poslech audioknih", "Čas čtení", "Čtenáři a posluchači", "Nejčtenější knihy"))
      and "Hobit" in activity and "Cesta na jih" in activity, "activity: tiles and the most read books")
check("Poslední čtení a poslech" in activity and len(names_in(activity)) == 3 and f'href="/book/{ids["Kapitola 1"]}"' in activity,
      "the administrator sees readers and the last rows, a row leads to the book")
check(f'href="/library/{AUDIO}?tab=activity&amp;days=7"' in activity, "the period switch keeps the tab")
whole = admin.get(f"/library/{AUDIO}?tab=activity&days=0").text
check("Co se z knihovny četlo a poslouchalo za celou historii." in whole, "the whole history says so")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()
reader_client = login("reader")
reader_activity = reader_client.get(f"/library/{AUDIO}?tab=activity&days=30").text
check("Nejčtenější knihy" in reader_activity and "Poslední čtení a poslech" not in reader_activity and not names_in(reader_activity),
      "without ...including who read: numbers and books, no names")
ebook_activity = admin.get(f"/library/{EBOOKS}?tab=activity&days=30").text
# e-books 30 days: Duna 900 + 1200 = 2100 s from 2 readers
check("Duna" in ebook_activity and tile(ebook_activity, "Čas čtení") == formatting.seconds_human(2100)
      and tile(ebook_activity, "Poslech audioknih") == formatting.seconds_human(0),
      "e-books alone: the activity shows the reading and no listening")
accounts.save_permissions(reader["id"], access.pack_permissions([a for a in defaults if a != "books"], False))
db.forget_settings()
reader_client = login("reader")
reader_page = reader_client.get(f"/library/{EBOOKS}").text
reader_tabs = re.findall(rf'href="/library/{EBOOKS}\?tab=(\w+)"', reader_page)
check(reader_tabs == ["books"] and f'href="/book/{ids["Duna"]}"' in reader_page and "<!-- card: books_" not in reader_page,
      f"without Books: only the Books tab, no books cards ({reader_tabs})")
check("Čas čtení" not in reader_client.get(f"/library/{EBOOKS}?tab=activity&days=30").text, "and no Activity even through the address")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()

print()
print("--- a work in two libraries, the cards' list, reading never reported ---")
# The same e-book (title and author) in both libraries: one work for the section, one in each library for its own views.
with db.connect() as conn:
    conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, format, size_bytes, library_id, added_at, missing)"
                 " SELECT 'copy-duna', kind, name, title, author, title_key, 'pdf', 1000, ?, added_at, 0 FROM book WHERE id = ?",
                 (AUDIO, ids["Duna"]))
    conn.commit()
check(len(library_book_works(EBOOKS)["works"]) == 4 and "Duna" in titles(library_book_works(AUDIO))
      and library_book_works(AUDIO)["total"] == 5,
      "the copy of Duna counts in each library it is in (e-books 4, audiobooks 4 + the copy)")
check(stats.books_library_summary(stats.books_library_works())["ebook_count"] == 4,
      "while the section-wide numbers count the work once")
tile_text = admin.get("/library").text.split(f'href="/library/{AUDIO}"')[1].split("</a>")[0]
check(re.search(r"E-knih</span><span>1<", tile_text) is not None, "the library's tile counts the copy too (1 e-book in the audiobook library)")
check(admin.get(f"/library/{AUDIO}?tab=overview").text.count("<!-- card: books_formats -->") == 1
      and re.search(r"E-knih</p>\s*<div class=\"tile-value\">1<", admin.get(f"/library/{AUDIO}?tab=overview").text),
      "and so does the tab's Overview")
with db.connect() as conn:
    conn.execute("DELETE FROM book WHERE id = 'copy-duna'")
    conn.commit()
from jellyscope.web import books_library as books_library_page  # noqa: E402
from jellyscope import cards_books  # noqa: E402

check(books_library_page.LIBRARY_CARDS == tuple(f"books_{n}" for n in cards_books.PAGES["library"] if n != "library")
      and f"<!-- card: {books_library_page.LIBRARY_CARDS[0]} -->" in admin.get(f"/library/{EBOOKS}").text,
      "the library's cards are the registry's list of the library page, one list for the tab and the section")
# Jellyfin never reported reading (no row of the kind Book): the tile says so instead of a zero
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type = 'Book'")
    conn.commit()
note = "Jellyfin čtení e-knih nehlásí."
check(note in admin.get(f"/library/{EBOOKS}?tab=activity&days=30").text and note in admin.get(f"/book/{ids['Duna']}").text,
      "never reported: the Activity tab and an e-book's page say so instead of a zero")
check(note not in admin.get(f"/book/{ids['Hobit']}").text, "an audiobook's page has no such note")

print()
print("--- a library with no listening ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type IN ('Book', 'AudioBook')")
    conn.commit()
check(all(admin.get(f"/library/{lib}?tab={tab}&days=30").status_code == 200
          for lib in (EBOOKS, AUDIO) for tab in ("overview", "books", "activity")), "every tab of both libraries opens without listening")
empty_activity = admin.get(f"/library/{AUDIO}?tab=activity&days=30").text
check("V tomhle období se nic nečetlo ani neposlouchalo." in empty_activity
      and "Z téhle knihovny se zatím nic nečetlo ani neposlouchalo." in empty_activity and "<td" not in empty_activity.split("</nav>")[-1],
      "nothing yet is said in words, no empty table")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
