# -*- coding: utf-8 -*-
r"""Prowlarr as the last application of the *Arr page: the list of applications, Settings, the task, the tab, the menu.

Prowlarr connects and behaves the same as Sonarr, Radarr and Bazarr: a card
in Settings → *Arr, a tab on the *Arr page, the same task, the same rights area.
Watched:

A) the list of applications: Prowlarr fourth, with everything the web and the task need;
B) an unconnected Prowlarr has neither a tab nor a menu item;
C) Settings → *Arr: Prowlarr's card, the connection test, the key is not written out;
D) the tab: the cards in the order of the sections, headings, sub-tabs; Prowlarr alone is enough
   for the *Arr menu item;
E) "Synchronizovat teď", the task "Synchronizace *Arr", "Smazat stažená data"
   (with a warning that Prowlarr keeps the history only for a limited time), a change of the address;
F) rights: without the *Arr area 403, a reader without texts from the application;
G) the field "Řádků v tabulkách *Arr" with Prowlarr alone too.

Prowlarr is not run: the answers are recorded (tests/prowlarr_responses/).

Spec: docs/superpowers/specs/2026-11-15-prowlarr-design.md

Run:
    .venv/bin/python tests/test_prowlarr_web.py
"""
from __future__ import annotations

import asyncio
import html as html_lib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-zalozka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import (  # noqa: E402
    PROWLARR_RESPONSES, no_network, tile, card_html, recorded_response, fill_prowlarr, login, prowlarr_history,
    prowlarr_client, prowlarr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, cards_prowlarr, tasks  # noqa: E402
from jellyscope.arr import prowlarr, registry  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # what the test does not replace in its own way falls over at the first request - it never goes to the network
KEY = "tajny-klic"
VERSION = recorded_response(PROWLARR_RESPONSES, "system_status")["version"]
HISTORY = len(prowlarr_history())
HEALTH = recorded_response(PROWLARR_RESPONSES, "health")[0]["message"]
ALL_CARDS = [cards_prowlarr.card_key(j) for _, names in cards_prowlarr.SECTIONS for j in names]


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def menu(html: str) -> str:
    match = re.search(r"<nav[^>]*>.*?</nav>", html, re.S)
    return match.group(0) if match else ""


def subtabs(html: str) -> list[str]:
    """The *Arr apps in the submenu of the menu, in order (the page lost its tab bar on 16. 11. 2026)."""
    block = re.search(r'<div class="nav-sub">(.*?)</div>', menu(html), re.S)
    return re.findall(r'href="(/arr/\w+)"', block.group(1)) if block else []


def settings_card(html: str) -> str:
    """Prowlarr's card in Settings → *Arr (from its mark to the end of the page - Prowlarr is the last)."""
    plain = html_lib.unescape(html)
    return plain[plain.find('id="arr-prowlarr"'):]


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
overrides: dict[str, Any] = {}
substitute(prowlarr, "ProwlarrClient", prowlarr_client(prowlarr_handler(None, overrides)))
admin, reader = login("admin"), login("reader")

print("--- A) the list of applications ---")
check(list(registry.APPS) == ["sonarr", "radarr", "lidarr", "readarr", "bazarr", "prowlarr"], f"Prowlarr is the last application of the *Arr page ({list(registry.APPS)})")
p = registry.APPS.get("prowlarr")
check(p is not None and (p.key, p.name, p.template, p.sample_url) == ("prowlarr", "Prowlarr", "arr_prowlarr.html", "http://localhost:9696")
      and p.sections == cards_prowlarr.SECTIONS and p.card_key("numbers") == "prowlarr_numbers" and [k.key for k in p.cards()] == ALL_CARDS,
      "Prowlarr's details: the name, the tab's template, the sample address, the sections and the cards")
check(p is not None and all(callable(getattr(p, name, None)) for name in
                            ("stored_settings", "save_settings", "check_connection", "synchronize",
                             "sync_running", "sync_state", "event_count", "delete_downloaded_data", "period")),
      "and all the functions the web and the task call")
check(p is not None and p.stored_settings() == {"enabled": False, "url": "", "has_key": False} and p.event_count() == 0 and p.sync_running() is False,
      "an unconnected Prowlarr: empty settings, no data")

print()
print("--- B) an unconnected Prowlarr ---")
check(registry.connected() == [] and admin.get("/arr/prowlarr").status_code == 404
      and admin.get("/arr", follow_redirects=False).status_code == 404, "neither the tab nor the signpost are there")
check('href="/arr/' not in menu(admin.get("/account").text), "and the menu has no *Arr item")

print()
print("--- C) Settings → *Arr ---")
page = html_lib.unescape(admin.get("/settings?section=arr").text)
card = settings_card(page)
check('action="/settings/arr/prowlarr"' in card and 'placeholder="http://localhost:9696"' in card
      and "V aplikaci: Settings → General → Security → API Key." in card and "http://server/prowlarr." in card
      and "http://server/bazarr" not in card, "Prowlarr's card with a form, a sample address (behind a proxy too) and help for the key")
check(page.find('id="arr-radarr"') < page.find('id="arr-bazarr"') < page.find('id="arr-prowlarr"'), "after Sonarr, Radarr and Bazarr")
check("Sonarru, Radarru, Lidarru, Readarru, Bazarru a Prowlarru" in page, "the section's opening sentence knows Prowlarr too")
r = admin.post("/settings/arr/prowlarr", data={"enabled": "1", "url": "http://prowlarr.test", "api_key": KEY, "action": "test"})
check(f"Spojení v pořádku: Prowlarr {VERSION}" in html_lib.unescape(r.text) and KEY not in r.text and not prowlarr.is_connected(),
      f"the connection test tells Prowlarr's version and stores nothing ({VERSION})")
r = admin.post("/settings/arr/prowlarr", data={"enabled": "1", "url": "http://prowlarr.test", "api_key": "", "action": "save"})
check(prowlarr.is_connected() and db.get_setting("arr_prowlarr_api_key") == KEY and KEY not in r.text
      and "Nastavení Prowlarr uloženo." in html_lib.unescape(r.text), "saving after the test takes the tried key; it is not written into the page")
check(reader.post("/settings/arr/prowlarr", data={"enabled": "", "url": "", "api_key": "", "action": "save"},
                  follow_redirects=False).status_code in (303, 403) and prowlarr.is_connected(), "a reader saves nothing")

print()
print("--- D) the Prowlarr tab ---")
check(registry.connected() == ["prowlarr"] and 'href="/arr/' in menu(admin.get("/account").text)
      and admin.get("/arr", follow_redirects=False).headers.get("location") == "/arr/prowlarr",
      "Prowlarr alone is enough: the *Arr menu item leads to its tab")
fill_prowlarr()
s = admin.get("/arr/prowlarr?days=0")
check(s.status_code == 200 and re.findall(r"<!-- card: (\w+) -->", s.text) == ALL_CARDS,
      f"thirteen cards in the order of the sections ({len(re.findall(r'<!-- card: ', s.text))})")
headings = re.findall(r'<div class="section-head">\s*<div>\s*<h2>([^<]+)</h2>', s.text)
check(headings == ["Teď v aplikaci", "Dotazy", "Výkon indexerů", "Vyžádaná vydání"], f"the sections' headings ({headings})")
check(subtabs(s.text) == ["/arr/prowlarr"] and "<title>*Arr · Prowlarr" in s.text, "the sub-tab only Prowlarr, the page's title")
check(tile(s.text, "Hledání") is not None and tile(s.text, "Zapnuté indexery") is not None
      and "<h2>Prowlarr · " not in s.text and "card-group" not in s.text and KEY not in s.text and "TAJNY-KLIC" not in s.text,
      "the tiles of the period and of the live state; on its own tab the cards have no prefix; the key nowhere")
check('id="table-window"' in s.text and 'href="/arr/prowlarr?days=30"' in s.text,
      "the page has the dialog for the whole table and the period switch aims at the tab")
template = (PROJECT / "jellyscope" / "templates" / "arr_prowlarr.html").read_text(encoding="utf-8")
check(re.findall(r'\{\{ c\("(\w+)"\) \}\}', template) == [j for _, names in cards_prowlarr.SECTIONS for j in names]
      and re.findall(r'\{\{ page_section\("([^"]+)"', template) == [n for n, _ in cards_prowlarr.SECTIONS],
      "the tab's template draws exactly the cards and sections from cards_prowlarr.SECTIONS")
# (The order of the sub-tabs with more connected applications is watched by tests/test_arr_registry.py.)

print()
print("--- E) synchronisation, the task, deleting, a change of the address ---")
r = admin.post("/settings/arr/prowlarr/sync")
check("Prowlarr: přibylo 0 záznamů historie." in html_lib.unescape(r.text), "Synchronizovat teď: nothing new, a message with the name")
card = settings_card(r.text)
check(f"Stažená historie: {HISTORY}" in re.sub(r"\s+", " ", card) and "Poslední synchronizace" in card, "the card shows the number of events and the last synchronisation")
check("Prowlarr si historii drží jen omezenou dobu" in card and "Prowlarr si historii drží" not in page,
      "the delete dialog says that Prowlarr no longer has the older history (the other applications do not have that sentence)")
result = asyncio.run(tasks.run_now("arr"))
check(result["status"] == "ok" and result["message"] == "Prowlarr: 0 nových záznamů", f"the task “Synchronizace *Arr” synchronises Prowlarr too ({result})")
check("Prowlarr" in tasks.TASKS["arr"].description and "Prowlarr" in next(o.description for o in access.AREAS if o.key == "arr"),
      "the description of the task and of the rights area names Prowlarr")
r = admin.post("/settings/arr/prowlarr", data={"enabled": "1", "url": "http://jiny-prowlarr.test", "api_key": "", "action": "save"})
check("Adresa Prowlarr se změnila a stažená historie je z původní adresy" in html_lib.unescape(r.text),
      "a change of the address with downloaded data: a warning that it may be another instance")
admin.post("/settings/arr/prowlarr", data={"enabled": "1", "url": "http://prowlarr.test", "api_key": "", "action": "save"})
r = admin.post("/settings/arr/prowlarr/delete")
check(prowlarr.event_count() == 0 and db.get_setting("arr_prowlarr_last_id", "") == "" and "flash error" not in r.text,
      "Smazat stažená data deletes the sums, the releases and the marks (after a backup)")
fill_prowlarr()
check(prowlarr.event_count() == HISTORY, "and the next synchronisation reads the history again")

print()
print("--- F) rights ---")
prowlarr.live.CACHE.clear()
s_admin, s_reader = html_lib.unescape(admin.get("/arr/prowlarr?days=0").text), html_lib.unescape(reader.get("/arr/prowlarr?days=0").text)
check(HEALTH in card_html(s_admin, "prowlarr_health"), "the administrator sees the warning texts")
check(re.findall(r"<!-- card: (\w+) -->", s_reader) == ALL_CARDS and HEALTH not in s_reader
      and "Aplikace hlásí varování" in card_html(s_reader, "prowlarr_health"),
      "a reader sees the tab (the area is on by default), the warning texts not")
accounts.save_permissions(reader_account["id"], access.pack_permissions(["knihovna", "prehled", "dashboard"], False))
check(reader.get("/arr/prowlarr").status_code == 403 and 'href="/arr/' not in menu(reader.get("/account").text),
      "without the *Arr area: 403 and no menu item")
accounts.save_permissions(reader_account["id"], None)
prowlarr.live.CACHE.clear()

print()
print("--- G) Interface ---")
interface_page = admin.get("/settings?section=interface").text
check('name="ui_max_rows_arr"' in interface_page and 'name="ui_rows_arr"' in interface_page, "the field “Řádků v tabulkách *Arr” with Prowlarr alone too")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
