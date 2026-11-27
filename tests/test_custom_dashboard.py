# -*- coding: utf-8 -*-
r"""A custom dashboard - a page put together from existing sections.

It is switched on in Settings -> Interface and holds for the whole server, the same as
the other settings. The administrator puts it together; the others see it.

What it stands on:

* **A section is a pair** - a piece of a template and a function for data, written in the registry
  (jellyscope/cards.py). The templates are the same ones the original
  pages use, so the two places cannot drift apart.
* **Only what is put together is computed.** The Overview computes ten sections
  for everybody; here only the data of the chosen ones runs.
* **Switched off it is as if it were not there** - the tab is not in the menu and the address leads back
  to the Overview.

Run:
    .venv/bin/python tests/test_custom_dashboard.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prehled.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from _apps import (  # noqa: E402
    bazarr_client, bazarr_handler, no_network, fill_bazarr, fill_lidarr, fill_prowlarr, fill_radarr, fill_readarr, fill_seerr, fill_sonarr,
    connect_bazarr, connect_lidarr, connect_prowlarr, connect_radarr, connect_readarr, connect_seerr, connect_sonarr, lidarr_client, lidarr_handler,
    readarr_client, readarr_handler,
    prowlarr_client, prowlarr_handler, radarr_client, radarr_handler, seerr_client, seerr_handler, sonarr_client, sonarr_handler,
)
from _package import substitute  # noqa: E402
from _books import fill_books  # noqa: E402
from _music import fill_music  # noqa: E402
from jellyscope import accounts, cards, cards_books, cards_music, db, demodata, i18n, seerr  # noqa: E402
from jellyscope.arr import bazarr, lidarr, prowlarr, radarr, readarr, sonarr  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def switch(enabled: bool) -> None:
    db.set_setting(cards.ENABLED_KEY, "1" if enabled else "0")
    db.forget_settings()


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
demodata.seed()
accounts.create("admin", "longpassword", is_admin=True)

# The applications' cards (Sonarr, Radarr, Lidarr, Readarr, Bazarr, Prowlarr, Seerr) are in the registry available only
# with a connected application (and the music cards only with music). So that this test sees the registry whole, all of them
# are connected here - instead of the network they talk to recorded answers (tests/_apps.py)
# and their history and requests are downloaded into the database. What the registry does
# with an unconnected application is watched by the block "cards by applications" below
# and by the card tests of every application (tests/test_<app>_cards.py).
no_network()
substitute(sonarr, "SonarrClient", sonarr_client(sonarr_handler()))
substitute(radarr, "RadarrClient", radarr_client(radarr_handler()))
connect_sonarr()
fill_sonarr()
connect_radarr()
fill_radarr()
substitute(lidarr, "LidarrClient", lidarr_client(lidarr_handler()))
connect_lidarr()
fill_lidarr()
substitute(readarr, "ReadarrClient", readarr_client(readarr_handler()))
connect_readarr()
fill_readarr()
substitute(seerr, "SeerrClient", seerr_client(seerr_handler()))
connect_seerr()
fill_seerr()
substitute(bazarr, "BazarrClient", bazarr_client(bazarr_handler()))
connect_bazarr()
fill_bazarr()
substitute(prowlarr, "ProwlarrClient", prowlarr_client(prowlarr_handler()))
connect_prowlarr()
fill_prowlarr()
# Music: the cards of the Music page are in the registry only with music (recorded answers of Jellyfin, tests/_music.py).
fill_music()
# Books: the cards of the Books pages are in the registry only with books (answers built by hand, tests/_books.py).
fill_books()
accounts.create("viewer", "longpassword", is_admin=False)

print("--- the registry of sections ---")
check(len(cards.ALL_CARDS) >= 10, f"the number of sections is {len(cards.ALL_CARDS)}")
check(len({s.key for s in cards.ALL_CARDS}) == len(cards.ALL_CARDS),
      "the keys do not repeat")
for s in cards.ALL_CARDS:
    check((PROJECT / "jellyscope" / "templates" / s.template).exists(),
          f"the template {s.template} exists")

# A card's name and description are translated only while running (`_(card.name)`), so
# the dictionary check of the templates does not see them - it is watched here.
import json  # noqa: E402

for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json")
                            .read_text(encoding="utf-8"))
    missing = [t for s in cards.ALL_CARDS for t in (s.name, s.description) if t not in dictionary]
    check(not missing, f"the cards' names and descriptions have a translation ({language})", f"(missing: {missing})")

# Sections must not overwrite each other's values in one context. The same key is
# fine as long as it means the same - "Celkem odsledováno" and the tiles under
# it draw from the same summary. An error would be if they sent different things
# under one name: one section would then draw somebody else's numbers.
by_key: dict[str, list[tuple[str, object]]] = {}
for s in cards.ALL_CARDS:
    for key, value in s.data(30).items():
        by_key.setdefault(key, []).append((s.key, value))

mismatched = [key for key, sources in by_key.items()
              if len(sources) > 1 and any(h != sources[0][1] for _, h in sources[1:])]
check(not mismatched, "shared keys mean the same for all sections",
      f"(they differ: {mismatched})")

print()
print("--- the queries pass on PostgreSQL too ---")
# `column IS ?` SQLite takes, PostgreSQL not - `IS %s` is a syntax error
# for it. And because the layout is read at EVERY request
# (because of the tab in the menu), it brought down not one page, but the whole
# application. So we catch the real queries, not only the source.
from jellyscope import dialect  # noqa: E402

captured: list[str] = []
_original_all = db.query_all
_original_connect = db.connect


def _capture_all(sql, params=()):
    captured.append(sql)
    return _original_all(sql, params)


class _Eavesdrop:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        captured.append(sql)
        return self._conn.execute(sql, params)

    def executemany(self, sql, params):
        captured.append(sql)
        return self._conn.executemany(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


import contextlib  # noqa: E402
import importlib  # noqa: E402


@contextlib.contextmanager
def _capture_connect(*args, **kwargs):
    with _original_connect(*args, **kwargs) as conn:
        yield _Eavesdrop(conn)


db.query_all = _capture_all
db.connect = _capture_connect
try:
    cards.load_layout()
    cards.load_layout(account_id=1)
    cards.save_layout(["codecs"])
    cards.save_layout(["codecs"], account_id=1)
finally:
    db.query_all = _original_all
    db.connect = _original_connect

ours = [s for s in captured if "dashboard_layout" in s]
check(len(ours) >= 4, f"caught {len(ours)} queries over the layout")
translated = [dialect.translate(s, dialect.POSTGRES) for s in ours]
# It is looked for regardless of case - `%s` becomes `%S` with `.upper()`
# and a comparison with "IS %s" would then never find anything.
import re as _re  # noqa: E402

FAULTY = _re.compile(r"\bIS\s+%s", _re.I)
wrong = [s for s in translated if FAULTY.search(s)]
check(not wrong, "none ends as `IS %s`", f"({wrong})")
check(any(_re.search(r"\bIS\s+NULL", s, _re.I) for s in translated),
      "the shared layout asks for IS NULL")

# The check of itself: if the search stopped searching, it would stay silent.
check(bool(FAULTY.search(dialect.translate(
    "SELECT 1 FROM t WHERE a IS ?", dialect.POSTGRES))),
    "and the old form `IS ?` would really fail")

cards.save_layout([])

print()
print("--- saving the layout ---")
check(cards.load_layout() == [], "a new layout is empty")
saved = cards.save_layout(["most_active_users", "now_playing"])
check(saved == ["most_active_users", "now_playing"],
      "it is saved in the given order")
check([s.key for s in cards.load_layout()] == saved, "and loaded that way too")

# Only what can be drawn belongs in the database. Otherwise it would be enough to remove a section
# from the registry and the page would fall over on something somebody put there
# half a year ago.
check(cards.save_layout(["now_playing", "neexistuje", "now_playing"])
      == ["now_playing"], "unknown keys and duplicates are thrown away")

# The offer in the dialog is no longer "the rest of the registry": all sections show
# and the used ones grey out. If they were left out, the list would jump around when adding
# and removing and a person would lose the place where they were. It is verified
# on the page below.

print()
print("--- only what is put together is computed ---")
calls: list[str] = []
original = cards.ALL_CARDS


class _Spy:
    """A section that writes down that somebody asked for its data."""

    def __init__(self, key: str) -> None:
        self.key = key

    def __call__(self, period: object) -> dict[str, object]:
        calls.append(self.key)
        return {f"data_{self.key}": 1}


spied = tuple(
    cards.Card(s.key, s.name, s.description, s.template, _Spy(s.key),
               s.uses_period, s.width, s.wrap_in_card, area=s.area)
    for s in original)
cards.data_for([spied[0], spied[2]], 30)
check(calls == [spied[0].key, spied[2].key],
      f"only two sections were asked ({calls})")

print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)

    switch(False)
    response = client.get("/dashboard", follow_redirects=False)
    check(response.status_code == 303 and response.headers.get("location") == "/",
          "a switched-off dashboard leads back to the Overview")
    check('href="/dashboard"' not in client.get("/").text, "and is not in the menu")

    switch(True)
    cards.save_layout([])
    page = client.get("/dashboard").text
    check("ještě v něm nic není" in page, "switched on and empty it says so")
    # An empty dashboard is not shown to the others, but the administrator sees it - otherwise
    # they would switch it on and have no way in to put it together.
    check('href="/dashboard"' in client.get("/").text,
          "the administrator has an empty dashboard in the menu - otherwise they would not get to it")

    cards.save_layout(["now_playing", "most_active_users"])
    page = client.get("/dashboard").text
    check(page.count('class="dash-card') == 2, "two sections are drawn")
    check('href="/dashboard"' in client.get("/").text, "and the tab is in the menu")
    check("Upravit přehled" in page, "the administrator has the edit button")
    # On a phone dragging scrolls the grid: the arrows of a tile are the way for a finger and a keyboard, and the hint
    # says so in a sentence of its own - the one before stays as it was (de.json, a translation of others, holds it).
    hint = i18n.translate("The tiles match the panels on the page. Drag to reorder, use the button to change the width.")
    arrows = i18n.translate("The arrows on a tile move it too.")
    check(hint in page and arrows in page and page.index(hint) < page.index(arrows) and arrows != hint,
          "the layout editor's hint mentions the arrows after the sentence about dragging")

    # The dialog offers the WHOLE registry and greys out the used sections. If they were
    # left out, the list would jump around when adding and removing.
    check(page.count("data-add=") == len(cards.ALL_CARDS),
          f"the offer has all the sections ({page.count('data-add=')})")
    check(page.count("disabled") >= 2,
          "and the ones that are in the dashboard are greyed out")

    # The period filter shows only when at least one section
    # uses it - otherwise a switch that does nothing would stand at the top.
    cards.save_layout(["now_playing", "codecs"])
    check("modal-period" not in client.get("/dashboard").text,
          "with sections without a period the filter does not show")
    cards.save_layout(["now_playing", "most_active_users"])
    check("modal-period" in client.get("/dashboard").text,
          "and with a period it does")

    # Saving from the page
    response = client.post("/dashboard/layout",
                           data={"order": "codecs,resolutions"},
                           follow_redirects=False)
    check(response.status_code == 303, "saving redirects back")
    check([s.key for s in cards.load_layout()] == ["codecs", "resolutions"],
          "and the order is really saved")

    print()
    print("--- every section is drawn alone in the dashboard too ---")
    # A template inserted into a page sees what that page
    # imported too - the macro `tile`, say. When a section sits in the dashboard
    # alone, a missing import brings it down ("'tile' is undefined") - and exactly
    # that happened with "Odkud se dívají". That is why each is tried separately.
    for s in cards.ALL_CARDS:
        cards.save_layout([s.key])
        response = client.get("/dashboard")
        check(response.status_code == 200, f"the section {s.key} alone in the dashboard",
              f"(HTTP {response.status_code})")

    # And all at once in one page - if two sections
    # overwrote each other's variable, it cracks here.
    cards.save_layout([s.key for s in cards.ALL_CARDS])
    everything = client.get("/dashboard")
    check(everything.status_code == 200, "and all in one dashboard")
    count = everything.text.count('class="dash-card')
    check(count == len(cards.ALL_CARDS),
          f"all are drawn ({count} of {len(cards.ALL_CARDS)})")

    print()
    print("--- the width marks are in one place ---")
    # The marks (⅓ ½ 1) are needed by the template and by JavaScript. If each had
    # its own, one of the copies would drift apart over time - so they come from the registry
    # and travel to the browser in an attribute.
    cards.save_layout("codecs:third,resolutions:half,origin:full")
    page = client.get("/dashboard").text
    # The label has the keys of the widths, not the marks: "⅓" cannot be printed by a cp1250 console
    # and the test would fall over on print instead of on what it watches.
    check(all(z in page for z in cards.WIDTHS.values()),
          f"all the marks are drawn ({list(cards.WIDTHS)})")
    check("data-widths=" in page, "and JavaScript gets them from the page")
    # The attribute must be in apostrophes: `tojson` escapes `'`, but not `"`,
    # so in double quotes the JSON would end the tag in the middle.
    check("data-widths='" in page, "the attribute is in apostrophes, so the JSON does not end the tag")

    print()
    print("--- the panel's width ---")
    # The width is stored with the placement, not with the section: the same statistic can be
    # full width on one server and in a third on another.
    client.post("/dashboard/layout",
                data={"order": "codecs:third,resolutions:full"},
                follow_redirects=False)
    saved = cards.load_layout()
    check([s.width for s in saved] == ["third", "full"],
          f"the widths are saved ({[s.width for s in saved]})")
    page = client.get("/dashboard").text
    check("dash-third" in page and "dash-full" in page,
          "and show in the page")

    # An unknown width (another version, a forged form) is ignored and the one
    # from the registry holds - the page must not break because of it.
    client.post("/dashboard/layout", data={"order": "codecs:obrovska"},
                follow_redirects=False)
    check(cards.load_layout()[0].width == cards.BY_KEY["codecs"].width,
          "an unknown width falls back to the one from the registry")

print()
print("--- who may put it together ---")
cards.save_layout(["codecs", "resolutions"])
with TestClient(app) as viewer:
    viewer.post("/login", data={"username": "viewer", "password": "longpassword"},
                follow_redirects=False)
    page = viewer.get("/dashboard").text
    check(page.count('class="dash-card') == 2, "a non-administrator sees the dashboard")
    check("Upravit přehled" not in page, "but has no edit button")
    check(viewer.post("/dashboard/layout", data={"order": "now_playing"},
                      follow_redirects=False).status_code in (302, 303, 401, 403),
          "and cannot save it")
    check([s.key for s in cards.load_layout()] == ["codecs", "resolutions"],
          "the layout stayed as it was")

print()
print("--- switching on leads straight to putting it together ---")
switch(False)
cards.save_layout([])
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    response = client.post("/settings/interface",
                           data={"ui_max_streams": "10", "ui_max_viewers": "10",
                                 "ui_map_zoom": "click", "ui_skin": "new",
                                 "ui_exact_time": "0", "ui_dashboard": "1",
                                 "dashboard_name": "Rodina"},
                           follow_redirects=False)
    check(response.headers.get("location") == "/dashboard",
          "after switching on it throws one straight into the dashboard")

    # A dashboard already put together a person no longer needs to see - they stay
    # in the settings where they are.
    cards.save_layout(["codecs"])
    switch(False)
    response = client.post("/settings/interface",
                           data={"ui_max_streams": "10", "ui_max_viewers": "10",
                                 "ui_map_zoom": "click", "ui_skin": "new",
                                 "ui_exact_time": "0", "ui_dashboard": "1",
                                 "dashboard_name": "Rodina"},
                           follow_redirects=False)
    check(response.headers.get("location", "").startswith("/settings"),
          "with a dashboard put together it stays in the settings")

print()
print("--- an empty dashboard and a non-administrator ---")
switch(True)
cards.save_layout([])
with TestClient(app) as viewer:
    viewer.post("/login", data={"username": "viewer", "password": "longpassword"},
                follow_redirects=False)
    check('href="/dashboard"' not in viewer.get("/").text,
          "whoever does not put it together is not bothered by an empty tab in the menu")

print()
print("--- the redirect after signing in ---")
switch(False)
with TestClient(app) as c:
    r = c.post("/login", data={"username": "admin", "password": "longpassword"},
               follow_redirects=False)
    check(r.headers.get("location") == "/video/overview", "switched off -> the Overview (the default page)")

switch(True)
cards.save_layout([])
with TestClient(app) as c:
    r = c.post("/login", data={"username": "admin", "password": "longpassword"},
               follow_redirects=False)
    check(r.headers.get("location") == "/video/overview",
          "switched on, but empty -> the Overview too (an empty page is a worse start)")

cards.save_layout(["now_playing"])
with TestClient(app) as c:
    r = c.post("/login", data={"username": "admin", "password": "longpassword"},
               follow_redirects=False)
    check(r.headers.get("location") == "/dashboard", "switched on with content -> there")

print()
print("--- the original pages draw the same sections ---")
# If the extraction made a copy, this would pass even after the two places drifted apart -
# that is why we ask for content that comes from the shared template.
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    for address, headings in (
            ("/", ["Nejaktivnější uživatelé", "Jak server obsah doručuje",
                   "Přehrávače", "Kdy se sleduje"]),
            ("/network", ["Špička po dnech", "Kdo nejvíc streamoval"]),
            ("/library", ["Kodeky", "Rozlišení", "Dynamický rozsah"])):
        text = client.get(address).text
        missing = [n for n in headings if n not in text]
        check(not missing, f"{address} still has its cards", f"missing {missing}")

print()
print("--- the cards from the Insights ---")
# A card = a template the Insights page and the dashboard insert. Nothing changes
# on the page; the dashboard can draw it alone.
INSIGHTS = {
    "insight_unused": "Místo, které nikdo nevyužívá",
    "insight_transcoded": "Nejčastěji transcodované soubory",
    "insight_reasons": "Proč se transcoduje",
    "insight_speed": "Rychlost převodu",
    "insight_quality": "Díváš se na to hodně, ale ve slabé kvalitě",
    "insight_large": "Zabírá hodně, sledováno skoro vůbec",
    "insight_libraries": "Využití knihoven",
    "insight_duplicates": "Možné duplicity",
    "insight_postponed": "Začaté a odložené",
}
check(all(cards.BY_KEY.get(k) is not None and cards.BY_KEY[k].area == "charts"
          for k in INSIGHTS),
      "all 9 Insights cards are in the registry with the area Insights and title rankings")
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    text = client.get("/video/insights").text
    missing = [n for n in INSIGHTS.values() if n not in text]
    check(not missing, "/video/insights still has all 9 cards", f"missing {missing}")
    check('id="table-window"' in text, "and the dialog for the whole table")
    switch(True)
    db.set_setting(cards.NAME_KEY, "Rodina")
    for key, heading in INSIGHTS.items():
        if key not in cards.BY_KEY:
            continue
        cards.save_layout([key])
        response = client.get("/dashboard")
        check(response.status_code == 200 and heading in response.text,
              f"the card {key} is drawn alone in the dashboard")
    cards.save_layout(["insight_transcoded"])
    check('id="table-window"' in client.get("/dashboard").text,
          "a dashboard with an Insights table has the dialog for the whole table")
    cards.save_layout(["codecs"])
    check('id="table-window"' not in client.get("/dashboard").text,
          "without such a card it does not need the dialog")

print()
print("--- the cards Languages, Users, Library, Network ---")
# What tells a card in the dashboard (a heading or a text that is nowhere else).
MORE = {
    "who_language": "Kdo v jakém jazyce sleduje",
    "combinations": "Kombinace stop",
    "missing_track": "Sledujete to, ale stopa chybí",
    "users_chart": "Sledovanost podle uživatele",
    "users_table": "<h2>Uživatelé</h2>",
    "libraries": "lib-grid",
    "network_numbers": "Průměrný tok jednoho streamu",
    "map": "<h2>Mapa</h2>",
}
MORE_AREAS = {"who_language": "languages", "combinations": "languages", "missing_track": "languages",
              "users_chart": "users", "users_table": "users",
              "libraries": "library", "network_numbers": "network", "map": "network_origin"}
check(all(getattr(cards.BY_KEY.get(k), "area", None) == o for k, o in MORE_AREAS.items()),
      "8 more cards are in the registry with the right area")
# 42 page cards (+ Disks 7. 11. 2026), 2 × 24 *Arr cards (Sonarr, Radarr) and 10 Seerr cards (13. 11. 2026), 20 music cards,
# 17 books cards (15. 11. 2026), 14 Bazarr and 14 Prowlarr cards (Indexer availability 25. 11. 2026), 23 Lidarr and
# 23 Readarr cards; Indexer reliability in Sonarr, Radarr, Lidarr and Readarr (25. 11. 2026). Together 211.
check(len(cards.ALL_CARDS) == 211, f"the builder has 211 cards ({len(cards.ALL_CARDS)})")
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    for address, headings in (
            ("/video/languages", ["Kdo v jakém jazyce sleduje", "Kombinace stop"]),
            ("/users", ["Sledovanost podle uživatele", "<h2>Přehled</h2>"]),
            ("/library", ["lib-grid"]),
            ("/network", ["Průměrný tok jednoho streamu", "<h2>Mapa</h2>"])):
        text = client.get(address).text
        missing = [n for n in headings if n not in text]
        check(not missing, f"{address} still has its cards", f"missing {missing}")
    switch(True)
    db.set_setting(cards.NAME_KEY, "Rodina")
    for key, mark in MORE.items():
        if key not in cards.BY_KEY:
            continue
        cards.save_layout([key])
        response = client.get("/dashboard")
        check(response.status_code == 200 and mark in response.text,
              f"the card {key} is drawn alone in the dashboard")
    # Downloading GeoIP is the administrator's action on the Network page, not a button in the dashboard.
    cards.save_layout(["map"])
    check('action="/network/geoip"' not in client.get("/dashboard").text,
          "the map in the dashboard does not offer downloading GeoIP (it is on the Network page)")

    # Review Focus 4: all the cards at once.
    cards.save_layout([s.key for s in cards.ALL_CARDS])
    response = client.get("/dashboard")
    drawn = re.findall(r'class="dash-card[^"]*" data-card="(\w+)"', response.text)
    check(response.status_code == 200 and len(drawn) == len(cards.ALL_CARDS),
          f"a dashboard with all the cards at once is drawn ({len(drawn)} of {len(cards.ALL_CARDS)})")

print()
print("--- a card in the dashboard gets the same data as on its page ---")
# The key under which a card reads data must mean the same in the dashboard as on
# the page the card comes from. Otherwise the card is drawn, only with somebody else's
# numbers: "Celkem za všechny knihovny" got the languages' coverage instead of
# the technical data and showed only zeros.
from jellyscope.web import base as web_base  # noqa: E402

AREA_PAGE = {"overview": "/video/overview", "now_playing": "/video/overview", "library": "/library",
             "network": "/network", "network_origin": "/network", "languages": "/video/languages", "users": "/users",
             "charts": "/video/insights"}
# A card whose page is not the page of its area (the area by the spec).
CARD_PAGE = {"recently_added": "/video/overview"}
# *Arr cards have a page by the application: arr_sonarr_fronta -> /arr/sonarr.
# Bazarr's cards the tab /arr/bazarr, Prowlarr's /arr/prowlarr, Seerr's cards (and "Kdo žádá" with its own area) the page /seerr,
# the music cards (and "Posluchači" with its own area) the page /music/overview.
APP_PAGES = {"/arr/sonarr", "/arr/radarr", "/arr/lidarr", "/arr/readarr", "/arr/bazarr", "/arr/prowlarr", "/seerr", "/music/overview", "/music/insights",
             "/books/overview", "/books/insights"}


def card_page(card: cards.Card) -> str:
    if card.group == "Bazarr":
        return "/arr/bazarr"
    if card.group == "Prowlarr":
        return "/arr/prowlarr"
    if card.area == "arr":
        return "/arr/" + card.key.split("_")[1]
    if card.group == "Seerr":
        return "/seerr"
    if card.group == cards_music.NAME:      # the charts and what nobody listens to are on Insights (22. 11. 2026)
        if card.key in MUSIC_LIBRARY_CARDS:  # ... and the library's cards in the Library's Music section
            return "/library"
        return "/music/insights" if card.area == "music_insights" else "/music/overview"
    if card.group == cards_books.NAME:      # the same layout: the library's cards are in the Library's Books section
        if card.key in BOOKS_LIBRARY_CARDS:
            return "/library"
        return "/books/insights" if card.area == "books_insights" else "/books/overview"
    return CARD_PAGE.get(card.key) or AREA_PAGE[card.area]


ALL_PAGES = set(AREA_PAGE.values()) | APP_PAGES
admin_account = accounts.get_by_name("admin")
# The live flow changes with every call - it cannot even be compared with itself.
CHANGES_OVER_TIME = {"live"}

# Live cards (Seerr's pending requests, the *Arr state) keep the application's state a minute in the cache and
# a request's waiting time is computed at the moment of downloading. The page and the card's recomputation here therefore
# get the same numbers only when the cache does not expire between them - on PostgreSQL, where
# the test runs longer than a minute, the check sometimes fell over even though the code is fine. While the pages
# are taken and compared the state is therefore held, as if a minute had not passed.
LIVE_STATES = tuple(importlib.import_module(f"jellyscope.{path}.live")
                    for path in ("seerr", "arr.sonarr", "arr.radarr", "arr.lidarr", "arr.readarr", "arr.prowlarr", "arr.bazarr"))


@contextlib.contextmanager
def hold_live_state():
    """For the duration of the block the applications' live state in the cache does not expire."""
    original = [module.TTL for module in LIVE_STATES]
    for module in LIVE_STATES:
        module.TTL = float("inf")
    try:
        yield
    finally:
        for module, ttl in zip(LIVE_STATES, original):
            module.TTL = ttl


# The availability card says what measures the indexers right now (`measuring`): the background loop, which runs while
# the app does. The pages are drawn by the running app (TestClient's lifespan starts the loop), the card is recomputed
# here after it stopped - so the comparison sees the loop's state as its page saw it when it was drawn.
LOOP_STATE = importlib.import_module("jellyscope.arr.prowlarr.samples").LOOP
LOOP_AS_PAGES_SAW: dict[str, dict] = {}


@contextlib.contextmanager
def loop_as_page_saw(path: str):
    """For the duration of the block the indexers' measuring loop is in the state the page was drawn with."""
    original = dict(LOOP_STATE)
    LOOP_STATE.update(LOOP_AS_PAGES_SAW.get(path, original))
    try:
        yield
    finally:
        LOOP_STATE.clear()
        LOOP_STATE.update(original)


def age_live_state(seconds: float) -> None:
    """Shifts the age of everything in the live state's cache, as if so many seconds had passed."""
    for module in LIVE_STATES:
        for key, entry in list(module.CACHE.items()):
            module.CACHE[key] = (entry[0] - seconds, *entry[1:])


def load_contexts(addresses) -> dict[str, dict]:
    """The context each of the pages gives the template - loaded now, as the administrator."""
    captured: dict[str, dict] = {}
    original_response = web_base.templates.TemplateResponse

    def capture(request, name, *args, **kwargs):
        captured.setdefault(request.url.path, dict(args[0] if args else kwargs.get("context", {})))
        LOOP_AS_PAGES_SAW.setdefault(request.url.path, dict(LOOP_STATE))
        return original_response(request, name, *args, **kwargs)

    web_base.templates.TemplateResponse = capture
    try:
        with TestClient(app) as page_client:
            page_client.post("/login", data={"username": "admin", "password": "longpassword"},
                             follow_redirects=False)
            for address in addresses:
                page_client.get(address)
    finally:
        web_base.templates.TemplateResponse = original_response
    return captured


# The Library page draws the library's cards of Music in its Music section (`content_sections`, the kind "music",
# `data.card_data` by the card's key) - the numbers card as the tiles of that section (`data.numbers`).
MUSIC_LIBRARY_CARDS = {cards_music.card_key(name) for name in cards_music.PAGES["library"]}


BOOKS_LIBRARY_CARDS = {cards_books.card_key(name) for name in cards_books.PAGES["library"]}


def _in_library(context: dict, template: str, numbers_key: str) -> dict:
    """The section of the Library page with this template: its cards by key, the numbers under the numbers card's key."""
    for section in context.get("content_sections") or []:
        data = section.get("data") or {}
        if section.get("template") == template and "card_data" in data:
            return {**data["card_data"], numbers_key: data.get("numbers")}
    return {}


def differences(card: cards.Card, context: dict) -> tuple[list[str], list[str]]:
    """The card's keys that differ from the context of its page, and the keys the page lacks."""
    differ, missing_keys = [], []
    if card.key in MUSIC_LIBRARY_CARDS:
        context = {**context, **_in_library(context, "library/_music_summary.html", cards_music.card_key("library"))}
    if card.key in BOOKS_LIBRARY_CARDS:
        context = {**context, **_in_library(context, "library/_books_summary.html", cards_books.card_key("library"))}
    # Through data_for and for the administrator - the same way as the card is computed by the dashboard
    # they opened (the applications' cards hide texts from the application by the account).
    for key, value in cards.data_for([card], context.get("days"), account=admin_account).items():
        if key in CHANGES_OVER_TIME:
            continue
        if key not in context:
            missing_keys.append(f"{card.key}.{key} ({card_page(card)})")
        elif value != context[key]:
            differ.append(f"{card.key}.{key} ({card_page(card)})")
    return differ, missing_keys


differing, missing = [], []
with hold_live_state():
    contexts = load_contexts(ALL_PAGES)
    for card in cards.ALL_CARDS:
        with loop_as_page_saw(card_page(card)):
            card_differs, card_missing = differences(card, contexts.get(card_page(card), {}))
        differing += card_differs
        missing += card_missing
check(len(contexts) == len(ALL_PAGES),
      f"the context of all the pages caught ({sorted(contexts)})")
check(not missing, "every key of a card is on its page too", f"missing {missing}")
check(not differing, "a card gets the same data in the dashboard as on its page", f"they differ {differing}")

# A trap: the cache's boundary. The page, then two minutes "pass" (the cache's age is shifted) and
# a second for real: without holding a live card differs from its page, with holding not.
live_card = cards.BY_KEY["seerr_pending"]
seerr_context = load_contexts(["/seerr"])["/seerr"]
age_live_state(120)
time.sleep(1.2)
check(differences(live_card, seerr_context)[0] == ["seerr_pending.seerr_pending (/seerr)"],
      "a trap: when the cache expires between the page and the recomputation, a live card differs from the page")
with hold_live_state():
    seerr_context = load_contexts(["/seerr"])["/seerr"]
    age_live_state(120)
    time.sleep(1.2)
    check(differences(live_card, seerr_context) == ([], []),
          "with the live state held the cache's boundary does not bring the comparison down",
          f"they differ {differences(live_card, seerr_context)[0]}")

# The same trap for the indexers' measuring loop: recomputed after the app stopped, the availability card says the
# *Arr sync measures, while the page drawn by the running app said the background loop does.
availability = cards.BY_KEY["prowlarr_availability"]
LOOP_AS_PAGES_SAW.pop("/arr/prowlarr", None)
prowlarr_context = load_contexts(["/arr/prowlarr"])["/arr/prowlarr"]
check(LOOP_AS_PAGES_SAW["/arr/prowlarr"]["running"] > 0 and LOOP_STATE["running"] == 0
      and differences(availability, prowlarr_context)[0] == ["prowlarr_availability.prowlarr_availability (/arr/prowlarr)"],
      "a trap: recomputed outside the running app, the availability card says another way of measuring")
with loop_as_page_saw("/arr/prowlarr"):
    check(differences(availability, prowlarr_context) == ([], []),
          "with the loop as its page saw it, the availability card is the same as on the page",
          f"they differ {differences(availability, prowlarr_context)[0]}")

print()
print("--- what more cards need is computed once ---")
# The users' chart and table take the same table, four language cards the same
# colours of languages. A dashboard with all of them is not to compute them again for each card.
from jellyscope import langstats, stats  # noqa: E402

calls: dict[str, int] = {"user_table": 0, "colour_map": 0}
original = {"user_table": stats.user_table, "colour_map": langstats.colour_map}


def _counting(name):
    def wrapper(*args, **kwargs):
        calls[name] += 1
        return original[name](*args, **kwargs)
    return wrapper


stats.user_table = _counting("user_table")
langstats.colour_map = _counting("colour_map")
try:
    data = cards.data_for([cards.BY_KEY[k] for k in (
        "users_chart", "users_table", "language_share", "who_language",
        "library_languages")], 30)
finally:
    stats.user_table = original["user_table"]
    langstats.colour_map = original["colour_map"]
check(calls == {"user_table": 1, "colour_map": 1} and "rows" in data and "by_user" in data,
      f"the users' table and the languages' colours only once ({calls})")

print()
print("--- cards by applications: the group, availability, the account, data under a key of their own ---")
# *Arr cards exist more than once (Sonarr, Radarr) with the same template. So that they do not
# overwrite each other's data in one dashboard, each returns a dict under ITS key
# and the template reads it as `d`. A card has a group too (a heading in the offer
# and a prefix of the name), knows whether its application is connected, and knows for
# which account it is computed. It is tried on two trial cards, not on
# real ones - the registry is swapped for the duration of the block.
import asyncio  # noqa: E402

from jinja2 import ChoiceLoader, DictLoader  # noqa: E402

from jellyscope import cards_base  # noqa: E402

connected = {"a": True, "b": True}
trial_calls = {"a": 0, "b": 0}


def _trial(app: str) -> cards.Card:
    def data(period: object) -> dict[str, object]:
        trial_calls[app] += 1
        return {f"zk_{app}": {"app": app, "is_admin": cards_base.current_is_admin(),
                              "library": cards_base.current_sees("library")}}
    return cards.Card(f"zk_{app}", "Kodeky", "Čím je knihovna zakódovaná. Období neřeší, je to stav knihovny.",
                      "_card_zk.html", data, uses_period=False, group=app.upper(),
                      available=lambda: connected[app], area="library")


runs = {"n": 0}


async def _state(what: str) -> dict[str, object]:
    runs["n"] += 1
    return {"what": what, "run": runs["n"]}


def _trial_live(key: str) -> cards.Card:
    return cards.Card(key, "Kodeky", "Čím je knihovna zakódovaná. Období neřeší, je to stav knihovny.",
                      "_card_zk.html", lambda o: {key: cards_base.live(_state, "x")},
                      uses_period=False, area="library")


A, B, Z1, Z2 = _trial("a"), _trial("b"), _trial_live("z1"), _trial_live("z2")

check(A.group == "A" and cards.BY_KEY["codecs"].group == "" and cards.BY_KEY["codecs"].available is None,
      "a card has a group and availability; the old cards none (always available)")
check(cards.data_for([A], 30)["zk_a"]["is_admin"] is False and cards.data_for([A], 30)["zk_a"]["library"] is False,
      "without an account the least rights")
check(cards.data_for([A], 30, account=admin_account)["zk_a"] == {"app": "a", "is_admin": True, "library": True},
      "the account gets into the card")
check(cards_base.current_account() is None and cards_base.current_is_admin() is False, "outside data_for there is no account")
check(cards.is_available(A) and cards.is_available(cards.BY_KEY["codecs"]), "is_available: a connected one and a card without a condition")

groups = cards.groups([cards.BY_KEY["codecs"], A, cards.BY_KEY["map"], B, cards.BY_KEY["resolutions"]])


def in_czech(heading: tuple[str, ...]) -> tuple[str, ...]:
    """A group's heading holds English keys; the expectations below are their Czech translations."""
    return tuple(i18n.translate(part, "cs") for part in heading)


check([in_czech(n) for n, _ in groups] == [("Knihovna",), ("A",), ("Síť",), ("B",)]
      and [k.key for k in groups[0][1]] == ["codecs", "resolutions"],
      f"groups: the heading = the group, otherwise the area; the order of the first occurrence ({[n for n, _ in groups]})")
check([in_czech(n) for n, _ in cards.groups([cards.BY_KEY["now_playing"]])] == [("Filmy a seriály", "Přehled")],
      "a sub-area (Now playing) sorts under its parent area, with the menu section too")

original_list, original_by_key = cards.ALL_CARDS, cards.BY_KEY
original_loader = web_base.templates.env.loader
cards.ALL_CARDS = (*original_list, A, B)
cards.BY_KEY = {s.key: s for s in cards.ALL_CARDS}
web_base.templates.env.loader = ChoiceLoader([
    DictLoader({"_card_zk.html": '<div class="card zk">{{ prefix }}{{ d.app }}|{{ d.is_admin }}</div>'}),
    original_loader])
try:
    switch(True)
    with TestClient(app) as client, TestClient(app) as viewer:
        client.post("/login", data={"username": "admin", "password": "longpassword"}, follow_redirects=False)
        viewer.post("/login", data={"username": "viewer", "password": "longpassword"}, follow_redirects=False)
        cards.save_layout(["zk_a", "zk_b"])
        html = client.get("/dashboard").text
        check('zk">A · a|True' in html and 'zk">B · b|True' in html,
              "two cards of the same template do not overwrite each other's data and carry the group's prefix")
        check('<span class="dash-tile-name">A · Kodeky</span>' in html, "the layout's tile carries the group")
        check('<span class="dash-item-name">B · Kodeky</span>' in html, "the offer's item too")
        check('zk">A · a|False' in viewer.get("/dashboard").text, "the card knows a non-administrator reads it")

        # B disconnected
        connected["b"] = False
        trial_calls["b"] = 0
        html2 = client.get("/dashboard").text
        check('data-add="zk_b"' not in html2 and 'data-add="zk_a"' in html2, "an unavailable card is not offered")
        headings = re.findall(r'<p class="dash-menu-heading">([^<]+)</p>', html2)
        check(len(headings) >= 2 and "A" in headings and "B" not in headings and "Knihovna" in headings,
              f"the offer has the groups' headings, only with something to offer ({headings})")
        check(html2.count("data-add=") == len(cards.ALL_CARDS) - 1, "the other cards stayed in the offer")
        check('zk">B' not in html2 and trial_calls["b"] == 0, "the dashboard for everybody neither draws nor computes it")
        check('zk">B' not in viewer.get("/dashboard").text and 'data-card="zk_b"' not in viewer.get("/dashboard").text
              and trial_calls["b"] == 0, "nor for whoever only reads the dashboard")
        # The administrator when editing: the tile stays (greyed out), otherwise saving
        # another change would quietly throw the disconnected card out of the layout.
        tile_b = re.search(r'<div class="dash-tile[^"]*dash-unavailable[^"]*"\s+data-card="zk_b"', html2)
        check(tile_b is not None, "the administrator has its tile in the layout (marked), so saving does not delete it")
        check(re.search(r'class="dash-card[^"]*" data-card="zk_b"', html2) is None, "but the page has no panel")
        check([s.key for s in cards.load_layout()] == ["zk_a", "zk_b"], "the saved layout stays")

        cards.save_layout(["zk_a", "zk_b"], admin_account["id"])
        trial_calls["b"] = 0
        mine = client.get("/account/dashboard").text
        check("B není připojená" in mine and 'zk">B' not in mine and trial_calls["b"] == 0,
              "My dashboard leaves it in its place with a sentence and does not compute it")
        check(re.search(r'class="dash-card[^"]*" data-card="zk_b"', mine) is not None and 'zk">A · a|True' in mine,
              "in its place, next to the connected card")
        client.post("/account/dashboard", data={"order": "zk_b:pul,zk_a"}, follow_redirects=False)
        check([s.key for s in cards.load_layout(admin_account["id"])] == ["zk_b", "zk_a"],
              "rearranging My dashboard does not lose the disconnected card")
        client.post("/account/dashboard", data={"order": "zk_a"}, follow_redirects=False)
        client.post("/account/dashboard", data={"order": "zk_a,zk_b"}, follow_redirects=False)
        check([s.key for s in cards.load_layout(admin_account["id"])] == ["zk_a"],
              "but adding a disconnected card is not possible")

        connected["b"] = True
        check('zk">B · b|True' in client.get("/dashboard").text, "after connecting the card is back")
finally:
    cards.ALL_CARDS, cards.BY_KEY = original_list, original_by_key
    web_base.templates.env.loader = original_loader
    cards.save_layout([])
    cards.save_layout([], admin_account["id"])

# The live state: cards are synchronous, they run an async client through live().
runs["n"] = 0
first = cards.data_for([Z1, Z2], 30)
second = cards.data_for([Z1], 30)
check(first["z1"] == first["z2"] == {"what": "x", "run": 1} and second["z1"]["run"] == 2 and runs["n"] == 2,
      f"the live state once per computation, in the next computation again ({runs['n']})")


async def _in_loop() -> object:
    return cards_base.live(_state, "x")


try:
    asyncio.run(_in_loop())
    check(False, "live() in the asyncio loop is an error, not a hang")
except RuntimeError as error:
    check("to_thread" in str(error), f"live() in the asyncio loop is an error, not a hang ({error})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
