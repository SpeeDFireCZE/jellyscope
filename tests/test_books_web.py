# -*- coding: utf-8 -*-
r"""The Books pages: cards from the registry, the menu, the permission areas, a viewer from Jellyfin, anonymising.

Watched:

A) without books the pages do not exist (404) and the menu has no Books;
B) with books: the overview's cards in the order of the sections, the tiles' numbers as in the statistics, the short
   chart leads to the book pages and (with the insights area) to the whole charts, Insights has its cards;
C) an account without the Books area does not have the page (403); without the insights area there is no Insights and
   no link to them, and a chart card of a dashboard is not drawn but kept in the layout;
D) without "...including who read and listened" the Readers card is not there and a reader's name is nowhere on the page
   or in the cards' data; the same for a viewer signed in from Jellyfin; with the right and anonymising the card has no names;
E) books without any listening (synced, nothing read) say "nothing" and have no empty table;
F) a library of e-books alone (no audiobook listened to): the cards about audiobooks say nothing instead of failing;
G) the books vanished from Jellyfin: the page and the menu item are gone.

The readers' names (tests/_books.py:BOOK_READERS) match nothing in the books, so even the page's bare text gives them away.

Spec: docs/superpowers/specs/2026-11-23-books-design.md

Run:
    .venv/bin/python tests/test_books_web.py
"""
from __future__ import annotations

import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "books-page.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from _apps import card_html, login, tile  # noqa: E402
from _books import BOOK_READERS, fill_books  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope import access, accounts, cards, cards_books, db, formatting, jellyfin, stats, web  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def menu(html: str) -> list[str]:
    """The addresses of the menu's items in order."""
    found = re.search(r"<nav[^>]*>.*?</nav>", html, re.S)
    return re.findall(r'href="(/[^"]*)"', found.group(0)) if found else []


def names_in(text: str) -> list[str]:
    return [name for name in BOOK_READERS.values() if name in text]


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
DEFAULTS = [o.key for o in access.AREAS if o.default]


def reader_rights(areas: list[str] | None, anonymise: bool = False) -> None:
    accounts.save_permissions(reader_account["id"], None if areas is None else access.pack_permissions(areas, anonymise))
    db.forget_settings()


admin = login("admin")

print("--- A) without books ---")
check(all(admin.get(f"/books/{page}", follow_redirects=False).status_code == 404 for page in ("overview", "compare", "insights")),
      "without books every page of the section is 404")
check(not any(a.startswith("/books") for a in menu(admin.get("/library").text)), "the menu has no Books")

print()
print("--- B) with books ---")
ids = fill_books()
r = admin.get("/books/overview?days=30")
s = r.text
overview_keys = [cards_books.card_key(n) for n in cards_books.PAGES["overview"]]
check(r.status_code == 200 and re.findall(r"<!-- card: (\w+) -->", s) == overview_keys
      and s.index("<!-- card: books_new -->") < s.index("<h2>Statistiky</h2>"),
      "the overview's cards in the order of the pages, the newly added books above the statistics")
check(not any(f"<!-- card: books_{n} -->" in s for n in cards_books.PAGES["library"] if n != "new"),
      "no library card on the overview (they are in the Library)")
most_read = card_html(s, "books_most_read")
check('href="/books/insights"' in most_read and f'href="/book/{ids["Hobit"]}"' in most_read,
      "the short chart: books leading to their page and a link to the whole charts on Insights")
ri = admin.get("/books/insights?days=30")
si = ri.text
CARD_MARK = r"<!-- card: (\w+) -->"   # outside the f-strings: a backslash in one needs Python 3.12
check(ri.status_code == 200 and re.findall(r"<!-- card: (\w+) -->", si) == [cards_books.card_key(n) for n in cards_books.PAGES["insights"]],
      f"Insights: the charts and what nobody reads ({re.findall(CARD_MARK, si)})")
numbers = stats.books_numbers(stats.books_analysis(30))
# listening 1800+1800+3600+2400 = 9600 s, reading 900+1200 = 2100 s, 3 readers, 3 works (Cesta na jih, Duna, Hobit), 3 authors
check(numbers == {"listening": 9600, "reading": 2100, "reader_count": 3, "work_count": 3, "author_count": 3}
      and tile(s, "Poslech audioknih") == formatting.seconds_human(9600) and tile(s, "Čas čtení") == formatting.seconds_human(2100)
      and tile(s, "Čtenáři a posluchači") == "3" and tile(s, "Knihy") == "3" and tile(s, "Autorů") == "3",
      f"the tiles as the statistics ({numbers}; {tile(s, 'Poslech audioknih')}, {tile(s, 'Čas čtení')})")
works_card = card_html(si, "books_works")
check(f'href="/book/{ids["Hobit"]}"' in works_card and works_card.count(f"/book/{ids['Kapitola 1']}") >= 1
      and f"/image/{ids['Kapitola 1']}?kind=Primary" in works_card
      and not re.search(rf"/book/({ids['Kapitola 2']}|{ids['Kapitola 3']})", works_card),
      "the books are links; a multi-file audiobook is one book with the cover and the page of its first file")
check(card_html(si, "books_unread").count("Anonymní nahrávka") >= 1 and "Algoritmy bez vzorců" in card_html(si, "books_unread"),
      "what nobody reads lists the unread books")
addresses = menu(s)
check("/books/overview" in addresses and "/library" in addresses
      and addresses.index("/books/overview") < addresses.index("/books/insights") < addresses.index("/library"),
      f"the Books section is in the menu before the Library, Insights after its overview ({addresses})")
check(len(names_in(card_html(s, "books_readers"))) == 3, "the administrator sees the readers by name")

print()
print("--- C) without the Books area ---")
check("books" in DEFAULTS and "books_insights" in DEFAULTS and "books_who" not in DEFAULTS,
      "the Books area and the insights are on by default, “…who read” off")
cards.save_layout(["books_numbers", "books_readers", "books_authors"])
reader_rights([o for o in DEFAULTS if o not in ("books", "books_insights")] + ["overview", "dashboard"])
reader = login("reader")
check(reader.get("/books/overview", follow_redirects=False).status_code == 403, "an account without Books: /books/overview = 403")
reader_rights([o for o in DEFAULTS if o != "books_insights"] + ["overview", "dashboard"])
cards.save_layout(["books_numbers", "books_works"])
check(reader.get("/books/insights", follow_redirects=False).status_code == 403
      and reader.get("/books/overview", follow_redirects=False).status_code == 200,
      "without the insights: /books/insights = 403, the overview opens")
check('href="/books/insights"' not in reader.get("/books/overview?days=30").text,
      "and the short chart has no link to the insights it may not open")
p = reader.get("/dashboard?days=30").text
check("<!-- card: books_works -->" not in p and "<!-- card: books_numbers -->" in p
      and "books_works" in [card.key for card in cards.load_layout()],
      "a chart in the dashboard: not drawn without the insights, kept in the layout")
reader_rights([o for o in DEFAULTS if o not in ("books", "books_insights")] + ["overview", "dashboard"])
cards.save_layout(["books_numbers", "books_readers", "books_authors"])
p = reader.get("/dashboard?days=30").text
check("<!-- card: books_" not in p and "/books/overview" not in menu(p),
      "in the dashboard for everybody it does not see the books cards, the menu does not have it")

print()
print("--- D) the readers' names ---")
reader_rights([*DEFAULTS, "overview", "dashboard"])
reader_account = accounts.get_by_name("reader")
s_reader = reader.get("/books/overview?days=30").text
check("<!-- card: books_readers -->" not in s_reader
      and len(re.findall(r"<!-- card: (\w+) -->", s_reader)) == len(cards_books.PAGES["overview"]) - 1,
      "with Books without “…who read”: the page without the Readers card")
check(not names_in(s_reader) and "/users/" not in s_reader, f"and without a single reader's name ({names_in(s_reader)})")
si_reader = reader.get("/books/insights?days=30").text
check(not names_in(si_reader) and "/users/" not in si_reader, "nor on Insights")
data = cards.data_for(cards.allowed(list(cards_books.CARDS), reader_account), 30, account=reader_account)
data_text = json.dumps(data, default=str, ensure_ascii=False)
check(not names_in(data_text) and "user_name" not in data_text and "user_id" not in data_text and "books_readers" not in data,
      "not in the cards' data either (the Readers card is not counted)")
p = reader.get("/dashboard?days=30").text
check("<!-- card: books_numbers -->" in p and "<!-- card: books_readers -->" not in p and not names_in(p),
      "the same in the dashboard for everybody")
reader_rights([*DEFAULTS, "books_who", "users"], anonymise=True)
s_anonymised = reader.get("/books/overview?days=30").text
check("<!-- card: books_readers -->" in s_anonymised and not names_in(s_anonymised) and 'href="/users/skryto-?' not in s_anonymised,
      f"with the right and anonymising: the card is there, the names hidden, no dead link ({names_in(s_anonymised)})")
reader_rights(None)

# A viewer from Jellyfin (Jana): a viewer's default areas, so Books without "...who read".
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
accounts.from_jellyfin({"id": "u-jana", "name": "jana", "is_admin": False, "is_disabled": False})


def server(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/Users/AuthenticateByName":
        return httpx.Response(200, json={"AccessToken": "t", "User": {"Id": "u-jana", "Name": "jana", "Policy": {}}})
    return httpx.Response(204)


async def substituted_aenter(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server), base_url="http://jellyfin.test")
    return self


original_aenter = jellyfin.JellyfinClient.__aenter__
jellyfin.JellyfinClient.__aenter__ = substituted_aenter
try:
    viewer = TestClient(web.app)
    sign_in_response = viewer.post("/login", data={"username": "jana", "password": "x", "method": "jellyfin"}, follow_redirects=False)
finally:
    jellyfin.JellyfinClient.__aenter__ = original_aenter
s_viewer = viewer.get("/books/overview?days=30").text
check(sign_in_response.status_code == 303 and "<!-- card: books_numbers -->" in s_viewer
      and "<!-- card: books_readers -->" not in s_viewer and not names_in(s_viewer),
      f"a viewer from Jellyfin: has the page, neither the Readers card nor the names ({sign_in_response.status_code}, {names_in(s_viewer)})")

print()
print("--- F) a library of e-books alone ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type = 'AudioBook'")
    conn.execute("UPDATE book SET missing = 1 WHERE kind = 'audiobook'")
    conn.commit()
r = admin.get("/books/overview?days=30")
ri = admin.get("/books/insights?days=30")
check(r.status_code == 200 and ri.status_code == 200, "e-books alone: the pages open")
check("V tomhle období se žádná audiokniha neposlouchala." in card_html(r.text, "books_method")
      and tile(r.text, "Poslech audioknih") == formatting.seconds_human(0) and tile(r.text, "Čas čtení") == formatting.seconds_human(2100),
      f"no audiobook: the method card says so, the listening tile is zero, reading 2100 s ({tile(r.text, 'Poslech audioknih')})")
check("Duna" in card_html(ri.text, "books_works") and "Hobit" not in card_html(ri.text, "books_works"),
      "the e-book is in the charts, the vanished audiobooks are not")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 0")
    conn.commit()
check(admin.get("/books/overview?days=30").status_code == 200, "back with all the books")

print()
print("--- E) books without listening ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type IN ('Book', 'AudioBook')")
    conn.commit()
r = admin.get("/books/overview?days=30")
ri = admin.get("/books/insights?days=30")
both = r.text + ri.text
empty_tables = [k.key for k in cards_books.CARDS if "<table" in card_html(both, k.key) and "<td" not in card_html(both, k.key)]
check(r.status_code == 200 and ri.status_code == 200 and not empty_tables,
      f"books without listening: the pages are there, no empty table ({empty_tables})")
check("V tomhle období se nic nečetlo ani neposlouchalo." in card_html(ri.text, "books_authors")
      and "V tomhle období se nic nečetlo ani neposlouchalo." in card_html(ri.text, "books_works")
      and "Jellyfin čtení e-knih nehlásí." in card_html(r.text, "books_numbers"),
      "the cards say “nothing yet” and that Jellyfin reports no reading")
check("Algoritmy bez vzorců" in card_html(ri.text, "books_unread") and "Anonymní nahrávka" in card_html(ri.text, "books_unread"),
      "everything is unread")

print()
print("--- G) the books disappeared from Jellyfin ---")
with db.connect() as conn:
    conn.execute("UPDATE book SET missing = 1")
    conn.commit()
check(admin.get("/books/overview", follow_redirects=False).status_code == 404
      and not any(a.startswith("/books") for a in menu(admin.get("/library").text)),
      "all the books disappeared: neither the page nor the menu item is there")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
