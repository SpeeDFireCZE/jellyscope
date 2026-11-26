# -*- coding: utf-8 -*-
r"""Music cards in the registry: the /music/overview page and custom dashboards.

The Music page is put together from the registry's cards (jellyscope/cards_music.py) -
the same can be put into a custom dashboard. Watched:

A) the registry: 19 cards music_<name> in the order of the sections, the areas (Listeners under
   "…including who listened"), widths, names, templates, translations; the cards stand
   at the end of the registry (after the apps' cards); the library's cards have no period;
B) without music the cards are not offered, with music all of them;
C) every card is drawn alone in a custom dashboard and carries "Hudba · ";
   all 19 at once;
D) the period's listening is selected once per page and per whole dashboard;
E) long tables follow their own number of rows (ui_max_rows_music)
   and the templates tell the macros it by the name "music".

The music is taken from recorded Jellyfin 10.11 answers (tests/music_responses/),
the listening is written by tests/_music.py:fill_music.

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md

Run:
    .venv/bin/python tests/test_music_cards.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hudba-karty.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import card_html, login  # noqa: E402
from _music import fill_music  # noqa: E402
from jellyscope import accounts, cards, cards_music, db, i18n, web  # noqa: E402

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
# Music laid out like Movies & series (the user, 22. 11. 2026): the cards by the pages they are on - the overview in
# the order of the video one (a short chart beside the listeners), the charts and what nobody listens to on Insights,
# the library's cards in the Library.
PAGES = {"overview": ("new", "numbers", "by_day", "listeners", "most_played", "method", "clients", "network", "when"),
         "insights": ("albums", "artists", "tracks", "genres", "unplayed"),
         "library": ("library", "formats", "quality", "years", "library_genres", "largest")}
NAMES = tuple(name for names in PAGES.values() for name in names)
check(cards_music.PAGES == PAGES and tuple(cards_music.NAMES) == NAMES
      and [k.key for k in cards_music.CARDS] == [f"music_{j}" for j in NAMES],
      "20 cards music_<name> in the order of the pages")
INSIGHTS = {f"music_{j}" for j in PAGES["insights"]}
check({k.key for k in cards_music.CARDS if k.area == "music_who"} == {"music_listeners"}
      and {k.key for k in cards_music.CARDS if k.area == "music_insights"} == INSIGHTS
      and all(k.area in ("music", "music_who", "music_insights") and i18n.translate(k.group, "cs") == "Hudba"
              for k in cards_music.CARDS),
      "the Music area, Listeners under an area of its own, the charts under the insights; the Music group")
LIBRARY_CARDS = {"library", "formats", "quality", "years", "new", "library_genres", "largest", "unplayed"}
check({k.key for k in cards_music.CARDS if not k.uses_period} == {f"music_{j}" for j in LIBRARY_CARDS},
      "the library's cards have no period (the unlistened count the whole history)")
check(all(k in cards.ALL_CARDS for k in cards_music.CARDS) and len(cards.ALL_CARDS) == 211
      and list(cards.ALL_CARDS[-37:-17]) == list(cards_music.CARDS) and cards.ALL_CARDS[-38].group == "Seerr",
      f"after the apps' cards (Books come last); there are 211 cards ({len(cards.ALL_CARDS)})")
WIDTHS = {"numbers": "full", "by_day": "full", "when": "full", "listeners": "half", "most_played": "half",
          "albums": "full", "artists": "half",
          "tracks": "half", "genres": "full", "library": "full", "formats": "third", "quality": "third", "years": "third",
          "new": "full", "library_genres": "half", "largest": "half", "unplayed": "full", "method": "half", "clients": "half",
          "network": "full"}
TITLES = {"numbers": "Čísla poslechu", "by_day": "Poslech po dnech", "when": "Kdy se poslouchá", "listeners": "Posluchači",
          "most_played": "Nejposlouchanější",
          "albums": "Nejposlouchanější alba", "artists": "Nejposlouchanější interpreti", "tracks": "Nejposlouchanější skladby",
          "genres": "Žánry podle poslechu", "library": "Čísla hudební knihovny", "formats": "Formáty souborů",
          "quality": "Kvalita zvuku", "years": "Roky vydání", "new": "Nově přidaná alba", "library_genres": "Žánry v knihovně",
          "largest": "Největší alba", "unplayed": "Co se neposlouchá", "method": "Přímo, nebo překódováno",
          "clients": "Aplikace a zařízení", "network": "Data po síti"}
check({j: k.width for j, k in zip(NAMES, cards_music.CARDS, strict=True)} == WIDTHS
      and {j: i18n.translate(k.name, "cs") for j, k in zip(NAMES, cards_music.CARDS, strict=True)} == TITLES,
      "widths and names by the plan")
WITH_WINDOW = {"listeners", "albums", "artists", "tracks", "genres", "formats", "new", "library_genres", "largest",
               "unplayed", "clients"}
check({k.key for k in cards_music.CARDS if k.has_table_window} == {f"music_{j}" for j in WITH_WINDOW},
      "the cards with a table for the dialog")
missing_templates = sorted(k.template for k in cards_music.CARDS if not (TEMPLATES / k.template).exists())
check(not missing_templates and all(k.template == f"_card_music_{j}.html"
                                     for j, k in zip(NAMES, cards_music.CARDS, strict=True)),
      f"the cards' templates exist ({missing_templates})")
for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    missing = sorted({t for k in cards_music.CARDS for t in (k.name, k.description) if t not in dictionary})
    check(not missing, f"the names and descriptions have a translation ({language}; missing {missing[:3]})")

print()
print("--- B) without music the cards are not offered ---")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == cards_music.NAME],
      "without music the cards are not offered")
fill_music()
check({k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == cards_music.NAME} == {f"music_{j}" for j in NAMES},
      "with music all of them are offered")

print()
print("--- C) every card alone in a dashboard ---")
admin = login("admin")
for card in cards_music.CARDS:
    cards.save_layout([card.key])
    r = admin.get("/dashboard?days=30")
    check(r.status_code == 200 and "Hudba · " in card_html(r.text, card.key),
          f"{card.key} alone in a dashboard, with the prefix Hudba")
cards.save_layout([k.key for k in cards_music.CARDS])
everything = admin.get("/dashboard?days=30")
check(everything.status_code == 200 and len(re.findall(r"<!-- card: music_\w+ -->", everything.text))
      == len(cards_music.CARDS) == 20, "all 20 cards at once")

print()
print("--- D) the period's listening once ---")
MARK = "LEFT JOIN music_track s ON s.id = p.item_id"
db_queries: list[str] = []
original_query_all, original_query_iter = db.query_all, db.query_iter


def _listen(sql, *a, **k):
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


def _listen_iter(sql, *a, **k):
    # The analysis of listening reads row by row (db.query_iter).
    db_queries.append(sql)
    return original_query_iter(sql, *a, **k)


db.query_all, db.query_iter = _listen, _listen_iter
try:
    page = admin.get("/music/overview?days=30")
    on_page = sum(MARK in q for q in db_queries)
    db_queries.clear()
    admin.get("/dashboard?days=30")
    on_dashboard = sum(MARK in q for q in db_queries)
finally:
    db.query_all, db.query_iter = original_query_all, original_query_iter
check(page.status_code == 200 and on_page == 1 and on_dashboard == 1,
      f"the page and the dashboard select the period's listening once ({on_page}×, {on_dashboard}×)")
# The review of 15. 11. 2026: four library cards (numbers, formats, quality, genres) each read the whole
# table of tracks separately - with a library of 50,000 tracks 200,000 rows per opening of the page.
TRACKS_QUERY = "FROM music_track WHERE missing = 0"
# The library's cards left Music's overview for the Library (22. 11. 2026): a dashboard with every music card has them.
db_queries.clear()
db.query_all = _listen
try:
    admin.get("/dashboard?days=30")
finally:
    db.query_all = original_query_all
library_track_reads = sum(q.strip().endswith(TRACKS_QUERY) for q in db_queries)
check(library_track_reads == 1, f"the library's tracks are read once by the cards of a dashboard ({library_track_reads}×)")

print()
print("--- E) long tables ---")
check(web.TABLE_LIMITS.get("music") == ("ui_max_rows_music", 10, 5)
      and db.DEFAULT_SETTINGS.get("ui_max_rows_music") == "10" and db.DEFAULT_SETTINGS.get("ui_max_rows_music_mobile") == "5",
      "music's own number of rows: 10, on a phone 5")
s = admin.get("/music/insights?days=30").text        # the charts are on Insights (22. 11. 2026)
artist_rows = card_html(s, "music_artists").count("<tr") - 1
for key, value in (("ui_max_rows", "50"), ("ui_max_rows_arr", "50"), ("ui_max_rows_seerr", "50"), ("ui_max_rows_music", "1")):
    db.set_setting(key, value)
db.forget_settings()
short = card_html(admin.get("/music/insights?days=30").text, "music_artists")
for key, value in (("ui_max_rows", "10"), ("ui_max_rows_arr", "10"), ("ui_max_rows_seerr", "10"), ("ui_max_rows_music", "10")):
    db.set_setting(key, value)
db.forget_settings()
check(artist_rows >= 3 and short.count('class="extra-row"') == artist_rows - 1 and "zobrazit celou tabulku" in short,
      f"music's own ceiling: one artist right away, the others in the dialog ({short.count('extra-row')} of {artist_rows})")
bad, calls_total = [], 0
for template in sorted(TEMPLATES.glob("_card_music_*.html")):
    for call in re.findall(r"(?:row_class|whole_table_button)\([^)]*\)", template.read_text(encoding="utf-8")):
        calls_total += 1
        if not call.rstrip(")").endswith('"music"'):
            bad.append(f"{template.name}: {call}")
# The calls must be found: after the macros are renamed a pattern with the old name would find nothing and pass empty.
check(calls_total >= 10 and not bad,
      f"the tables' templates tell the macros \"music\" ({calls_total} calls, wrong {bad[:2]})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
