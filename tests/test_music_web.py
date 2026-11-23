# -*- coding: utf-8 -*-
r"""The Music page: cards from the registry, the menu, two permission areas, a viewer from Jellyfin, anonymising.

Watched:

A) without music the page does not exist (404), it is missing from the menu and Settings has no field
   for the number of rows of its tables;
B) with music: the cards in the order of the sections with headings, the tiles' numbers as in
   the statistics, the artist and the album are links, Music is in the menu right after
   the Library;
C) an account without the Music area does not have the page (403) and in the dashboard for everybody
   does not see the music cards;
D) with Music without "…including who listened": the Listeners card is not there and a listener's
   name is nowhere on the page or in the cards' data; the same for a viewer
   signed in from Jellyfin; with the right and anonymising the card is without names;
E) an empty music library (synced, without listening) has the sentences
   "nothing yet", not empty tables;
F) Settings → Interface → Long lists: music's own number of rows.

The listeners' names match nothing in the music (tests/_music.py:LISTENERS),
so even the page's bare text gives them away.

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md

Run:
    .venv/bin/python tests/test_music_web.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hudba-stranka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from _apps import tile, card_html, login  # noqa: E402
from _music import LISTENERS, fill_music  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope import access, accounts, cards, cards_music, db, formatting, jellyfin, stats, web  # noqa: E402

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
    return [name for name in LISTENERS.values() if name in text]


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


def settings_form() -> str:
    return admin.get("/settings?section=interface").text


admin = login("admin")

print("--- A) without music ---")
check(admin.get("/music/overview", follow_redirects=False).status_code == 404, "without music /music/overview = 404")
check("/music/overview" not in menu(admin.get("/library").text), "the menu has no Music")
check('name="ui_max_rows_music"' not in settings_form() and 'name="ui_rows_music"' not in settings_form(),
      "Settings has no field for the music tables")
# The user, 16. 11. 2026: the Music tab only when Jellyfin has some music - an empty music library is not enough.
check(admin.get("/music/overview", follow_redirects=False).status_code == 404
      and "/music/overview" not in menu(admin.get("/library").text)
      and 'name="ui_max_rows_music"' not in settings_form(),
      "an empty music library in Jellyfin: neither the page, the menu nor the field in Settings is there")

print()
print("--- B) with music ---")
tracks = fill_music()
artists = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM music_artist")}
albums = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM music_album")}
r = admin.get("/music/overview?days=30")
s = r.text
overview_keys = [cards_music.card_key(n) for n in cards_music.PAGES["overview"]]
check(r.status_code == 200 and re.findall(r"<!-- card: (\w+) -->", s) == overview_keys
      and re.findall(r"<!-- card: (\w+) -->", s)[0] == "music_new"
      and s.index("<!-- card: music_new -->") < s.index("<h2>Statistiky</h2>"),
      "the overview's cards in the order of the video overview, the newly added albums above the statistics")
check(not any(f"<h2>{heading}</h2>" in s for heading in ("Poslech", "Žebříčky", "Knihovna", "Technika"))
      and "<h1>Hudba</h1>" not in s and not any(f"<!-- card: music_{n} -->" in s for n in cards_music.PAGES["library"]),
      "one heading Statistics like the video overview; no library card (they are in the Library)")
check('href="/music/insights"' in card_html(s, "music_most_played") and 'href="/album/' in card_html(s, "music_most_played"),
      "the short chart: albums leading to their page and a link to the whole charts on Insights")
# Insights (22. 11. 2026, like Movies & series): the full charts and what nobody listens to.
ri = admin.get("/music/insights?days=30")
si = ri.text
CARD_MARK = r"<!-- card: (\w+) -->"   # outside the f-strings: a backslash in one needs Python 3.12
check(ri.status_code == 200 and re.findall(r"<!-- card: (\w+) -->", si)
      == [cards_music.card_key(n) for n in cards_music.PAGES["insights"]],
      f"Insights: the charts and what nobody listens to ({re.findall(CARD_MARK, si)})")
numbers = stats.music_numbers(stats.music_analysis(30))
check(numbers["plays"] == 5 and tile(s, "Poslech") == formatting.seconds_human(numbers["seconds"])
      and tile(s, "Přehrání") == str(numbers["plays"]) and tile(s, "Posluchači") == str(numbers["listener_count"])
      and tile(s, "Interpretů") == str(numbers["artist_count"]),
      f"the tiles as the statistics ({numbers}; {tile(s, 'Poslech')})")
check(f'href="/music/artist/{artists["Northern Lights Ensemble"]}"' in card_html(si, "music_artists")
      and f'href="/album/{albums["Aurora Sessions"]}"' in card_html(si, "music_albums")
      and f'href="/album/{albums["Aurora Sessions"]}#track-{tracks["First Light"]}"' in card_html(si, "music_tracks"),
      "the artist and the album are links, a track leads to its row of the album")
addresses = menu(s)          # on a page of Music its section is open, with its pages
# Menu by sections (16. 11. 2026): Music is the section right after Movies & series (absent here,
# there are no movies), so it comes before the Library.
check("/music/overview" in addresses and "/library" in addresses
      and addresses.index("/music/overview") < addresses.index("/music/insights") < addresses.index("/library"),
      f"the Music section is in the menu before the Library, Insights after its overview ({addresses})")
check(len(names_in(card_html(s, "music_listeners"))) == 3, "the administrator sees the listeners by name")

print()
print("--- C) without the Music area ---")
check("music" in DEFAULTS and "music_who" not in DEFAULTS,
      "the Music area is on by default, “…who listened” off")
cards.save_layout(["music_numbers", "music_listeners", "music_artists"])
reader_rights([o for o in DEFAULTS if o not in ("music", "music_insights")] + ["overview", "dashboard"])
reader = login("reader")
check(reader.get("/music/overview", follow_redirects=False).status_code == 403, "an account without Music: /music/overview = 403")
# Insights have an area of their own (22. 11. 2026, like the video insights): without it the page is closed and a saved
# dashboard card of a chart is not drawn - but stays in the layout (it shows again with the right).
reader_rights([o for o in DEFAULTS if o != "music_insights"] + ["overview", "dashboard"])
cards.save_layout(["music_numbers", "music_albums"])
check(reader.get("/music/insights", follow_redirects=False).status_code == 403
      and reader.get("/music/overview", follow_redirects=False).status_code == 200,
      "without the insights: /music/insights = 403, the overview opens")
check('href="/music/insights"' not in reader.get("/music/overview?days=30").text,
      "and the short chart has no link to the insights it may not open")
p = reader.get("/dashboard?days=30").text
check("<!-- card: music_albums -->" not in p and "<!-- card: music_numbers -->" in p
      and "music_albums" in [card.key for card in cards.load_layout()],
      "a chart in the dashboard: not drawn without the insights, kept in the layout")
offered = cards.groups(cards.allowed(list(cards.ALL_CARDS), accounts.get_by_name("admin")))
check(any(heading == (cards_music.NAME,) and any(card.key == "music_albums" for card in group) for heading, group in offered),
      "the dashboard builder still offers the charts under Music (the music cards are grouped by their group)")
reader_rights([o for o in DEFAULTS if o not in ("music", "music_insights")] + ["overview", "dashboard"])
cards.save_layout(["music_numbers", "music_listeners", "music_artists"])
p = reader.get("/dashboard?days=30").text
check("<!-- card: music_" not in p and "/music/overview" not in menu(p),
      "in the dashboard for everybody it does not see the music cards, the menu does not have it")

print()
print("--- D) the listeners' names ---")
reader_rights([*DEFAULTS, "overview", "dashboard"])
reader_account = accounts.get_by_name("reader")
s_reader = reader.get("/music/overview?days=30").text
check("<!-- card: music_listeners -->" not in s_reader
      and len(re.findall(r"<!-- card: (\w+) -->", s_reader)) == len(cards_music.PAGES["overview"]) - 1,
      "with Music without “…who listened”: the page without the Listeners card")
check(not names_in(s_reader) and "/users/" not in s_reader, f"and without a single listener's name ({names_in(s_reader)})")
data = cards.data_for(cards.allowed(list(cards_music.CARDS), reader_account), 30, account=reader_account)
data_text = json.dumps(data, default=str, ensure_ascii=False)
check(not names_in(data_text) and "user_name" not in data_text and "user_id" not in data_text and "music_listeners" not in data,
      "not in the cards' data either (the Listeners card is not counted)")
p = reader.get("/dashboard?days=30").text
check("<!-- card: music_numbers -->" in p and "<!-- card: music_listeners -->" not in p and not names_in(p),
      "the same in the dashboard for everybody")
reader_rights([*DEFAULTS, "music_who", "users"], anonymise=True)
s_anonymised = reader.get("/music/overview?days=30").text
check("<!-- card: music_listeners -->" in s_anonymised and not names_in(s_anonymised)
      and 'href="/users/skryto-?' not in s_anonymised,
      f"with the right and anonymising: the card is there, the names hidden, no dead link ({names_in(s_anonymised)})")
reader_rights(None)

# A viewer from Jellyfin (Jana): a viewer's default areas, so Music without "…who listened".
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
    sign_in_response = viewer.post("/login", data={"username": "jana", "password": "x", "method": "jellyfin"},
                                   follow_redirects=False)
finally:
    jellyfin.JellyfinClient.__aenter__ = original_aenter
s_viewer = viewer.get("/music/overview?days=30").text
check(sign_in_response.status_code == 303 and "<!-- card: music_numbers -->" in s_viewer
      and "<!-- card: music_listeners -->" not in s_viewer and not names_in(s_viewer),
      f"a viewer from Jellyfin: has the page, neither the Listeners card nor the names ({sign_in_response.status_code}, {names_in(s_viewer)})")

print()
print("--- E) music without listening ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_type = 'Audio'")
    conn.commit()
r = admin.get("/music/overview?days=30")
ri = admin.get("/music/insights?days=30")
both = r.text + ri.text
empty_tables = [k.key for k in cards_music.CARDS
                if "<table" in card_html(both, k.key) and "<td" not in card_html(both, k.key)]
check(r.status_code == 200 and ri.status_code == 200 and not empty_tables,
      f"music without listening: the pages are there, no empty table ({empty_tables})")
check("V tomhle období se nic neposlouchalo." in card_html(ri.text, "music_artists")
      and "V tomhle období se nic neposlouchalo." in card_html(ri.text, "music_albums")
      and "<td" in card_html(admin.get("/library").text, "music_formats"),
      "the listening cards say “nothing yet”, the library cards (in the Library) have content")

print()
print("--- F) Settings: the number of rows of the music tables ---")
html = settings_form()
check('name="ui_max_rows_music"' in html and 'name="ui_max_rows_music_mobile"' in html
      and 'name="ui_rows_music" value="1"' in html,
      "with music the fields and the form's mark are there")
admin.post("/settings/interface", data={"ui_max_rows": "10", "ui_max_rows_mobile": "5", "ui_rows_music": "1",
                                        "ui_max_rows_music": "7", "ui_max_rows_music_mobile": "3"})
db.forget_settings()
check(web._limits()["table_limits"]["music"] == (7, 3), f"the numbers are saved ({web._limits()['table_limits']['music']})")
admin.post("/settings/interface", data={"ui_max_rows": "10", "ui_max_rows_mobile": "5"})
db.forget_settings()
check(web._limits()["table_limits"]["music"] == (7, 3), "a form without the music fields does not overwrite them")

print()
print("--- G) the music disappeared from Jellyfin ---")
with db.connect() as conn:
    conn.execute("UPDATE music_track SET missing = 1")
    conn.commit()
check(admin.get("/music/overview", follow_redirects=False).status_code == 404
      and "/music/overview" not in menu(admin.get("/library").text),
      "all the tracks disappeared: neither the page nor the menu item is there")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
