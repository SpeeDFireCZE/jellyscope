# -*- coding: utf-8 -*-
r"""Books cards in the registry: the /books/overview page, /books/insights and custom dashboards.

The Books pages are put together from the registry's cards (jellyscope/cards_books.py) - the same can be put into a
custom dashboard. Watched:

A) the registry: 17 cards books_<name> in the order of the pages, the areas (Readers and listeners under "...including
   who read and listened", the charts under the insights), widths, names, templates, translations; the cards stand at
   the end of the registry (after Music); the library's cards have no period;
B) without books the cards are not offered, with books all of them;
C) every card is drawn alone in a custom dashboard and carries "Knihy · "; all 17 at once;
D) the period's rows are selected once per page and per whole dashboard, the library's works once;
E) the numbers card says Jellyfin reports no reading instead of a zero when no e-book was ever reported;
F) the tables' templates tell the macros nothing special (they take the standard number of rows).

The books come from tests/books_responses/ (built by hand), the listening from tests/_books.py:fill_books.

Spec: docs/superpowers/specs/2026-11-23-books-design.md

Run:
    .venv/bin/python tests/test_books_cards.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "books-cards.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import card_html, login  # noqa: E402
from _books import fill_books  # noqa: E402
from jellyscope import accounts, cards, cards_books, cards_music, db, i18n  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
admin_account = accounts.get_by_name("admin")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
TEMPLATES = PROJECT / "jellyscope" / "templates"

print("--- A) the registry ---")
PAGES = {"overview": ("new", "numbers", "by_day", "readers", "most_read", "method", "clients", "when"),
         "insights": ("works", "authors", "genres", "unread"),
         "library": ("library", "formats", "years", "library_genres", "largest")}
NAMES = tuple(name for names in PAGES.values() for name in names)
check(cards_books.PAGES == PAGES and tuple(cards_books.NAMES) == NAMES
      and [k.key for k in cards_books.CARDS] == [f"books_{j}" for j in NAMES],
      "17 cards books_<name> in the order of the pages")
INSIGHTS = {f"books_{j}" for j in PAGES["insights"]}
check({k.key for k in cards_books.CARDS if k.area == "books_who"} == {"books_readers"}
      and {k.key for k in cards_books.CARDS if k.area == "books_insights"} == INSIGHTS
      and all(k.area in ("books", "books_who", "books_insights") and i18n.translate(k.group, "cs") == "Knihy"
              for k in cards_books.CARDS),
      "the Books area, Readers under an area of its own, the charts under the insights; the Books group")
LIBRARY_CARDS = {"library", "formats", "years", "new", "library_genres", "largest", "unread"}
check({k.key for k in cards_books.CARDS if not k.uses_period} == {f"books_{j}" for j in LIBRARY_CARDS},
      "the library's cards have no period (what nobody reads counts the whole history)")
check(all(k in cards.ALL_CARDS for k in cards_books.CARDS) and len(cards.ALL_CARDS) == 206
      and list(cards.ALL_CARDS[-17:]) == list(cards_books.CARDS) and list(cards.ALL_CARDS[-37:-17]) == list(cards_music.CARDS),
      f"at the end of the registry, after Music; there are 206 cards ({len(cards.ALL_CARDS)})")
WIDTHS = {"new": "full", "numbers": "full", "by_day": "full", "readers": "half", "most_read": "half", "method": "half",
          "clients": "half", "when": "full", "works": "full", "authors": "half", "genres": "half", "unread": "full",
          "library": "full", "formats": "third", "years": "third", "library_genres": "third", "largest": "full"}
TITLES = {"new": "Nově přidané knihy", "numbers": "Čísla čtení a poslechu", "by_day": "Čtení a poslech po dnech",
          "readers": "Čtenáři a posluchači", "most_read": "Nejčtenější", "method": "Audioknihy: přímo, nebo překódováno",
          "clients": "Aplikace a zařízení", "when": "Kdy se čte a poslouchá", "works": "Nejčtenější knihy",
          "authors": "Nejčtenější autoři", "genres": "Žánry podle čtení", "unread": "Co nikdo nečte",
          "library": "Čísla knihovny knih", "formats": "Formáty souborů", "years": "Roky vydání",
          "library_genres": "Žánry v knihovně", "largest": "Největší knihy"}
check({j: k.width for j, k in zip(NAMES, cards_books.CARDS, strict=True)} == WIDTHS
      and {j: i18n.translate(k.name, "cs") for j, k in zip(NAMES, cards_books.CARDS, strict=True)} == TITLES,
      "widths and names by the plan")
WITH_WINDOW = {"readers", "works", "authors", "genres", "formats", "new", "library_genres", "largest", "unread", "clients"}
check({k.key for k in cards_books.CARDS if k.has_table_window} == {f"books_{j}" for j in WITH_WINDOW},
      "the cards with a table for the dialog")
missing_templates = sorted(k.template for k in cards_books.CARDS if not (TEMPLATES / k.template).exists())
check(not missing_templates and all(k.template == f"_card_books_{j}.html" for j, k in zip(NAMES, cards_books.CARDS, strict=True)),
      f"the cards' templates exist ({missing_templates})")
for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    missing = sorted({t for k in cards_books.CARDS for t in (k.name, k.description) if t not in dictionary})
    check(not missing, f"the names and descriptions have a translation ({language}; missing {missing[:3]})")

print()
print("--- B) without books the cards are not offered ---")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == cards_books.NAME],
      "without books the cards are not offered")
fill_books()
check({k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == cards_books.NAME} == {f"books_{j}" for j in NAMES},
      "with books all of them are offered")

print()
print("--- C) every card alone in a dashboard ---")
admin = login("admin")
for card in cards_books.CARDS:
    cards.save_layout([card.key])
    r = admin.get("/dashboard?days=30")
    check(r.status_code == 200 and "Knihy · " in card_html(r.text, card.key),
          f"{card.key} alone in a dashboard, with the prefix Knihy")
cards.save_layout([k.key for k in cards_books.CARDS])
everything = admin.get("/dashboard?days=30")
check(everything.status_code == 200 and len(re.findall(r"<!-- card: books_\w+ -->", everything.text)) == len(cards_books.CARDS) == 17,
      "all 17 cards at once")

print()
print("--- D) the period's rows once ---")
ANALYSIS = "LEFT JOIN book b ON b.id = p.item_id"
WORKS = "cover_tag, added_at, library_id, missing, title_key FROM book"
db_queries: list[str] = []
original_query_all, original_query_iter = db.query_all, db.query_iter


def _listen(sql, *a, **k):
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


def _listen_iter(sql, *a, **k):
    # The analysis reads row by row (db.query_iter).
    db_queries.append(sql)
    return original_query_iter(sql, *a, **k)


db.query_all, db.query_iter = _listen, _listen_iter
try:
    page = admin.get("/books/overview?days=30")
    on_page = sum(ANALYSIS in q for q in db_queries)
    db_queries.clear()
    admin.get("/dashboard?days=30")
    on_dashboard = sum(ANALYSIS in q for q in db_queries)
    works_reads = sum(WORKS in q for q in db_queries)
finally:
    db.query_all, db.query_iter = original_query_all, original_query_iter
check(page.status_code == 200 and on_page == 1 and on_dashboard == 1,
      f"the page and the dashboard select the period's rows once ({on_page}×, {on_dashboard}×)")
# the table of books is read by the analysis (the index of works) once and by the library's cards once
check(works_reads == 2, f"the books' table: once for the period's analysis and once for all the library's cards ({works_reads}×)")

print()
print("--- E) Jellyfin does not report reading ---")
numbers = card_html(admin.get("/books/overview?days=30").text, "books_numbers")
check("Jellyfin čtení e-knih nehlásí." not in numbers, "with reading reported there is no such note")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type = 'Book'")
    conn.commit()
cards.save_layout(["books_numbers"])
for html in (card_html(admin.get("/books/overview?days=30").text, "books_numbers"),
             card_html(admin.get("/dashboard?days=30").text, "books_numbers")):
    check("Jellyfin čtení e-knih nehlásí." in html and "Čas čtení" in html, "no reading ever reported: the tile says so")

print()
print("--- F) tables ---")
bad = [t.name for t in sorted(TEMPLATES.glob("_card_books_*.html"))
       if re.search(r'(?:row_class|whole_table_button)\([^)]*"(?:music|arr|seerr)"', t.read_text(encoding="utf-8"))]
check(not bad, f"no books table takes the rows of another section ({bad})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
