# -*- coding: utf-8 -*-
r"""The menu by sections: one tree of sections and pages (jellyscope/navigation.py).

The menu is data: navigation.SECTIONS says which sections exist, in which
order, with which pages; navigation.menu() picks what one account may open
and what Jellyfin has content for; base.html only draws it. This test
checks the tree against the spec (docs/superpowers/specs/2026-11-16-menu-sections-design.md):

* order of the sections and the pages of "Movies & series";
* only the section of the open page is expanded, the burger says
  "Section · Page";
* a section without content (no music, no movies and episodes, no
  connected *Arr app) is not in the menu and its pages answer 404;
* an *Arr app that is not connected is not in the submenu, and the *Arr
  page has no tab bar any more (the submenu replaced it);
* permissions: a reader without Comparison has no Comparison in the submenu;
* labels follow the account's language;
* the HTML menu holds exactly the entries of menu().

Run:
    .venv/bin/python tests/test_navigation.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "navigation.db")
os.environ["SECRET_KEY"] = "test-key-navigation"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import (no_network, not_responding, login, connect_bazarr, connect_radarr, connect_seerr,  # noqa: E402
                        connect_sonarr, sonarr_client)
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, demodata, i18n, navigation  # noqa: E402
from jellyscope.arr import bazarr, radarr, sonarr  # noqa: E402
from jellyscope.arr import registry as arr_registry  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
demodata.seed()                       # movies, series and music (demodata._seed_music)
no_network()                            # no app is ever asked over the network here
connect_sonarr()
connect_radarr()
connect_bazarr()                       # Prowlarr stays disconnected
connect_seerr()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin = accounts.get_by_name("admin")
reader = accounts.get_by_name("reader")
SECTION_KEYS = {section.key for section in navigation.SECTIONS}


def sql(text: str) -> None:
    with db.connect() as conn:
        conn.execute(text)


def sections(entries: list[dict]) -> list[str]:
    return [entry["key"] for entry in entries if entry["key"] in SECTION_KEYS]


def entry(entries: list[dict], key: str) -> dict:
    return next(e for e in entries if e["key"] == key)


print("--- the tree ---")
check([s.key for s in navigation.SECTIONS] == ["video", "music", "books", "arr", "seerr", "library", "network", "users",
                                              "history", "settings"],
      f"sections in the order of the spec ({[s.key for s in navigation.SECTIONS]})")
entries = navigation.menu(admin, "/video/compare")
check(sections(entries) == ["video", "music", "books", "arr", "seerr", "library", "network", "users", "history", "settings"],
      f"the administrator's menu has every section with content ({sections(entries)})")
video = entry(entries, "video")
check([p["label"] for p in video["pages"]] == ["Přehled", "Srovnání", "Zjištění", "Jazyky"]
      and [p["url"] for p in video["pages"]] == ["/video/overview", "/video/compare", "/video/insights", "/video/languages"],
      f"Movies & series: Overview, Comparison, Insights, Languages ({[p['label'] for p in video['pages']]})")
check(video["label"] == "Filmy a seriály" and video["url"] == "/video/overview", "the section is labelled and opens its first page")
check(video["active"] and video["expanded"] and not entry(entries, "music")["expanded"]
      and [p["active"] for p in video["pages"]] == [False, True, False, False],
      "only the section of the open page is expanded, its page is active")
check(navigation.current_title(entries) == "Filmy a seriály · Srovnání",
      f"the burger says Section · Page ({navigation.current_title(entries)})")
check([p["label"] for p in entry(entries, "music")["pages"]] == ["Přehled", "Srovnání", "Zjištění"],
      "Music has its overview, comparison and insights, like Movies & series")
books_menu = entry(navigation.menu(admin, "/books/compare"), "books")
check(books_menu["label"] == "Knihy" and [p["label"] for p in books_menu["pages"]] == ["Přehled", "Srovnání", "Zjištění"]
      and [p["url"] for p in books_menu["pages"]] == ["/books/overview", "/books/compare", "/books/insights"]
      and [p["active"] for p in books_menu["pages"]] == [False, True, False] and books_menu["expanded"],
      "Books, right after Music: Overview, Comparison, Insights; the open page is active")
insights_menu = entry(navigation.menu(admin, "/music/insights"), "music")
check([p["active"] for p in insights_menu["pages"]] == [False, False, True],
      "on Music's insights the insights are active, not the overview (whose prefix used to be all of /music/)")
check(navigation.current_title(navigation.menu(admin, "/network")) == "Síť", "a single item: just its name")
check(not any(e["expanded"] for e in navigation.menu(admin, "/account")), "a page outside the sections expands nothing")
artist = navigation.menu(admin, "/music/artist/demo-int-1")
check(entry(artist, "music")["expanded"] and entry(artist, "music")["pages"][0]["active"],
      "an artist's page belongs to Music")
check(entry(navigation.menu(admin, "/album/demo-alb-1"), "library")["active"], "an album's page belongs to the Library")
check(entry(navigation.menu(admin, "/book/demo-book-1"), "library")["active"], "a book's page belongs to the Library")

print()
print("--- *Arr: a submenu of the connected apps, no tabs on the page ---")
arr = navigation.menu(admin, "/arr/sonarr")
check([p["label"] for p in entry(arr, "arr")["pages"]] == ["Sonarr", "Radarr", "Bazarr"],
      f"Prowlarr is not connected, so it is not in the submenu ({[p['label'] for p in entry(arr, 'arr')['pages']]})")
check(entry(arr, "arr")["expanded"] and [p["active"] for p in entry(arr, "arr")["pages"]] == [True, False, False],
      "on /arr/sonarr the submenu is open with Sonarr active")
k_admin = login("admin")
# Sonarr that does not answer: the page still draws (its live cards say so), and nothing goes to the network.
restore = substitute(sonarr, "SonarrClient", sonarr_client(not_responding()))
page = k_admin.get("/arr/sonarr")
check(page.status_code == 200 and '<div class="tabs">' not in page.text and "<h1>Sonarr</h1>" in page.text,
      f"the *Arr page has no tab bar and its heading names the app ({page.status_code})")
check(k_admin.get("/arr", follow_redirects=False).headers.get("location") == "/arr/sonarr", "/arr still opens the first app")
calls = []
original_connected = arr_registry.connected


def counted_connected():
    calls.append(1)
    return original_connected()


restore_connected = substitute(arr_registry, "connected", counted_connected)
try:
    navigation.menu(admin, "/arr/sonarr")
finally:
    restore_connected()
check(len(calls) == 1, f"one menu asks for the connected apps once ({len(calls)})")
check(navigation.landing("arr", admin) == "/arr/sonarr", "the *Arr section lands on its first connected app")
sonarr.save_settings(False, "http://sonarr.test", "")
check(navigation.landing("arr", admin) == "/arr/radarr",
      f"a disconnected app is not a landing page - the menu's rule ({navigation.landing('arr', admin)})")
connect_sonarr()

print()
print("--- the HTML menu draws exactly menu() ---")


def nav_links(html: str) -> list[str]:
    nav = re.search(r'<nav class="nav"[^>]*>(.*?)</nav>', html, re.S)
    return re.findall(r'href="([^"]+)"', nav.group(1)) if nav else []


def expected_links(entries: list[dict]) -> list[str]:
    links = []
    for e in entries:
        links.append(e["url"])
        if e["expanded"]:
            links += [p["url"] for p in e["pages"]]
    return links


for path in ("/video/compare", "/music/overview", "/arr/sonarr", "/library", "/settings"):
    html = k_admin.get(path).text
    expected = expected_links(navigation.menu(admin, path))
    check(nav_links(html) == expected, f"{path}: the menu in the page = menu() ({nav_links(html)[:6]}…)")
html = k_admin.get("/video/compare").text
check("Filmy a seriály · Srovnání" in html, "the burger button carries Section · Page")
restore()

print()
print("--- a section without content is gone, and so are its pages ---")
sql("UPDATE music_track SET missing = 1")
check("music" not in sections(navigation.menu(admin, "/")), "no track left: no Music section")
check(k_admin.get("/music/overview").status_code == 404 and k_admin.get("/music").status_code == 404,
      "and its overview answers 404")
# An artist's page stays, like an album's in the Library: album and viewer pages link to it.
check(k_admin.get("/music/artist/demo-int-1").status_code == 200, "an artist's page stays (album pages link to it)")
sql("UPDATE music_track SET missing = 0")
sql("UPDATE book SET missing = 1")
check("books" not in sections(navigation.menu(admin, "/")), "no book left: no Books section")
check(all(k_admin.get(p).status_code == 404 for p in ("/books", "/books/overview", "/books/compare", "/books/insights")),
      "and all its pages answer 404")
sql("UPDATE book SET missing = 0")
check(all(k_admin.get(p).status_code == 200 for p in ("/books/overview", "/books/compare", "/books/insights")),
      "with books the three pages open")
sql("UPDATE items SET is_missing = 1 WHERE type IN ('Movie', 'Episode')")
check("video" not in sections(navigation.menu(admin, "/")), "no movie or episode left: no Movies & series section")
check(all(k_admin.get(p).status_code == 404 for p in ("/video/overview", "/video/compare", "/video/insights",
                                                         "/video/languages")),
      "and its pages answer 404")
sql("UPDATE items SET is_missing = 0")
for module in (sonarr, radarr, bazarr):
    module.save_settings(False, f"http://{module.__name__.rsplit('.', 1)[-1]}.test", "")
check("arr" not in sections(navigation.menu(admin, "/")), "no connected *Arr app: no *Arr section")
connect_sonarr()

print()
print("--- /video and /music open their section ---")
response = k_admin.get("/video", follow_redirects=False)
check(response.status_code == 303 and response.headers.get("location") == "/video/overview",
      f"/video -> the section's first page ({response.status_code}, {response.headers.get('location')})")
check(k_admin.get("/music?days=7", follow_redirects=False).headers.get("location") == "/music/overview?days=7",
      "/music -> its overview, parameters kept")

check(k_admin.get("/books?days=7", follow_redirects=False).headers.get("location") == "/books/overview?days=7",
      "/books -> its overview, parameters kept")

print()
print("--- permissions and language ---")
accounts.save_permissions(reader["id"], access.pack_permissions([o.key for o in access.AREAS if o.default and o.key != "srovnani"],
                                                                False))
db.forget_settings()
reader = accounts.get_by_name("reader")
reader_menu = navigation.menu(reader, "/video/insights")
labels = [p["label"] for p in entry(reader_menu, "video")["pages"]]
check("Srovnání" not in labels and "Zjištění" in labels, f"a reader without Comparison has none in the submenu ({labels})")
check(entry(reader_menu, "video")["url"] == entry(reader_menu, "video")["pages"][0]["url"],
      "the section opens the first page the reader may see")
check("settings" not in sections(reader_menu), "a reader has no Settings")
response = login("reader").get("/video", follow_redirects=False)
check(response.status_code == 303 and response.headers.get("location") == entry(reader_menu, "video")["url"],
      f"/video opens the reader's first page of the section, like the menu ({response.headers.get('location')})")
token = i18n.set_account_language("en")
try:
    english = navigation.menu(admin, "/video/compare")
finally:
    i18n.reset_account_language(token)
check(entry(english, "video")["label"] == "Movies & series" and navigation.current_title(english) == "Movies & series · Comparison",
      f"labels follow the account's language ({entry(english, 'video')['label']}, {navigation.current_title(english)})")
accounts.set_language(admin["id"], "en")
check("Movies &amp; series" in login("admin").get("/video/overview").text, "the page of an English account draws English labels")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
