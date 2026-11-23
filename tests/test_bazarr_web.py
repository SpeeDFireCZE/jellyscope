# -*- coding: utf-8 -*-
r"""Bazarr as the fourth app of the *Arr page: the list of apps, Settings, the task, the tab, the menu.

Bazarr connects and behaves just like Sonarr and Radarr: a card in Settings
→ *Arr, a tab on the *Arr page, the same task, the same area of rights. Watched:

A) the list of apps: Bazarr third, with everything the web and the task need;
B) a Bazarr that is not connected has neither a tab nor a menu item;
C) Settings → *Arr: Bazarr's card, the connection test, the key is not written out;
D) the tab: the cards in the order of the sections, the headings, the sub-tabs; Bazarr alone
   is enough for the *Arr menu item; the speed card only with Sonarr;
E) Sync now, the task "Synchronizace *Arr", Delete the downloaded data;
F) rights: without the *Arr area 403, a reader without the app's texts;
G) the field "Řádků v tabulkách *Arr" with Bazarr alone too; an unknown time format
   is told by the card in Settings.

Bazarr is not started: the answers are recorded (tests/bazarr_responses/).

Spec: docs/superpowers/specs/2026-11-14-bazarr-design.md

Run:
    .venv/bin/python tests/test_bazarr_web.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bazarr-zalozka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import (  # noqa: E402
    BAZARR_RESPONSES, bazarr_client, bazarr_handler, no_network, tile, card_html, recorded_response, fill_bazarr, login, connect_sonarr,
)
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, cards_bazarr, tasks  # noqa: E402
from jellyscope.arr import bazarr, registry  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # whatever the test does not fake on its own falls over at the first request - it never goes to the network
KEY = "tajny-klic"
VERSION = recorded_response(BAZARR_RESPONSES, "system_status")["data"]["bazarr_version"]
HISTORY = len(recorded_response(BAZARR_RESPONSES, "episodes_history")["data"]) + len(recorded_response(BAZARR_RESPONSES, "movies_history")["data"])
ALL = [cards_bazarr.card_key(j) for _, names in cards_bazarr.SECTIONS for j in names]
WITHOUT_SPEED = [k for k in ALL if k != "bazarr_speed"]


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def menu(html: str) -> str:
    found = re.search(r"<nav[^>]*>.*?</nav>", html, re.S)
    return found.group(0) if found else ""


def tabs(html: str) -> list[str]:
    """The *Arr apps in the submenu of the menu, in order (the page lost its tab bar on 16. 11. 2026)."""
    block = re.search(r'<div class="nav-sub">(.*?)</div>', menu(html), re.S)
    return re.findall(r'href="(/arr/\w+)"', block.group(1)) if block else []


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
space: dict[str, Any] = {}
substitute(bazarr, "BazarrClient", bazarr_client(bazarr_handler(None, space)))
admin, reader = login("admin"), login("reader")

print("--- A) the list of apps ---")
check(list(registry.APPS)[:5] == ["sonarr", "radarr", "lidarr", "readarr", "bazarr"], f"Bazarr is the fifth app of the *Arr page, after Readarr ({list(registry.APPS)})")
b = registry.APPS.get("bazarr")
check(b is not None and (b.key, b.name, b.template, b.sample_url) == ("bazarr", "Bazarr", "arr_bazarr.html", "http://localhost:6767")
      and b.sections == cards_bazarr.SECTIONS and b.card_key("numbers") == "bazarr_numbers" and [k.key for k in b.cards()] == ALL,
      "Bazarr's details: the name, the tab's template, the example address, the sections and the cards")
check(b is not None and all(callable(getattr(b, name, None)) for name in
                            ("stored_settings", "save_settings", "check_connection", "synchronize",
                             "sync_running", "sync_state", "event_count", "delete_downloaded_data", "period")),
      "and all the functions the web and the task call")
check(b is not None and b.stored_settings() == {"enabled": False, "url": "", "has_key": False} and b.event_count() == 0 and b.sync_running() is False,
      "a Bazarr not connected: empty settings, no data")

print()
print("--- B) a Bazarr not connected ---")
check(registry.connected() == [] and admin.get("/arr/bazarr").status_code == 404
      and admin.get("/arr", follow_redirects=False).status_code == 404, "neither the tab nor the index exist")
check('href="/arr/' not in menu(admin.get("/account").text), "and the menu has no *Arr item")

print()
print("--- C) Settings → *Arr ---")
page = html_lib.unescape(admin.get("/settings?section=arr").text)
card = page[page.find('id="arr-bazarr"'):]
check('action="/settings/arr/bazarr"' in card and 'placeholder="http://localhost:6767"' in card
      and "V Bazarru: Settings → General → Security → API Key." in card and "http://server/bazarr." in card
      and "http://server/sonarr" not in card, "Bazarr's card with the form, an example address (behind a proxy too) and the help for the key")
check(page.find('id="arr-sonarr"') < page.find('id="arr-radarr"') < page.find('id="arr-bazarr"'), "after Sonarr and Radarr")
check("Sonarru, Radarru, Lidarru, Readarru, Bazarru a Prowlarru" in page, "the section's opening sentence knows Bazarr too")
r = admin.post("/settings/arr/bazarr", data={"enabled": "1", "url": "http://bazarr.test", "api_key": KEY, "action": "test"})
check(f"Spojení v pořádku: Bazarr {VERSION}" in html_lib.unescape(r.text) and KEY not in r.text and not bazarr.is_connected(),
      f"the connection test says Bazarr's version and saves nothing ({VERSION})")
r = admin.post("/settings/arr/bazarr", data={"enabled": "1", "url": "http://bazarr.test", "api_key": "", "action": "save"})
check(bazarr.is_connected() and db.get_setting("arr_bazarr_api_key") == KEY and KEY not in r.text
      and "Nastavení Bazarr uloženo." in html_lib.unescape(r.text), "saving after the test takes the tried key; it is not written into the page")
check(reader.post("/settings/arr/bazarr", data={"enabled": "", "url": "", "api_key": "", "action": "save"},
                  follow_redirects=False).status_code in (303, 403) and bazarr.is_connected(), "a reader saves nothing")

print()
print("--- D) the Bazarr tab ---")
check(registry.connected() == ["bazarr"] and 'href="/arr/' in menu(admin.get("/account").text)
      and admin.get("/arr", follow_redirects=False).headers.get("location") == "/arr/bazarr",
      "Bazarr alone is enough: the *Arr menu item leads to its tab")
fill_bazarr()
s = admin.get("/arr/bazarr?days=0")
check(s.status_code == 200 and re.findall(r"<!-- card: (\w+) -->", s.text) == WITHOUT_SPEED,
      f"the cards in the order of the sections, without the speed ({len(re.findall(r'<!-- card: ', s.text))})")
headings = re.findall(r'<div class="section-head">\s*<div>\s*<h2>([^<]+)</h2>', s.text)
check(headings == ["Teď v aplikaci", "Co se stáhlo", "Jazyky a poskytovatelé", "Jak dobré jsou"],
      f"the sections' headings; the speed section without a card has no heading ({headings})")
check(tabs(s.text) == ["/arr/bazarr"] and "<title>*Arr · Bazarr" in s.text, "the sub-tab only Bazarr, the page's title")
check(tile(s.text, "Získané titulky") is not None and tile(s.text, "Dílů bez titulků") is not None
      and "<h2>Bazarr · " not in s.text and "card-group" not in s.text and KEY not in s.text,
      "the tiles of the period and of the live state; on its own tab the cards have no prefix; the key nowhere")
check('id="table-window"' in s.text and 'href="/arr/bazarr?days=30"' in s.text,
      "the page has the window for the whole table and the period switch points at the tab")
template = (PROJECT / "jellyscope" / "templates" / "arr_bazarr.html").read_text(encoding="utf-8")
check(re.findall(r'\{\{ c\("(\w+)"\) \}\}', template) == [j for _, names in cards_bazarr.SECTIONS for j in names]
      and re.findall(r'\{\{ page_section\("([^"]+)"', template) == [n for n, _ in cards_bazarr.SECTIONS],
      "the tab's template draws exactly the cards and the sections of cards_bazarr.SECTIONS")
connect_sonarr()
db.forget_settings()
s2 = admin.get("/arr/bazarr?days=0")
check(re.findall(r"<!-- card: (\w+) -->", s2.text) == ALL and "Jak rychle přijdou" in s2.text
      and tabs(s2.text) == ["/arr/sonarr", "/arr/bazarr"] and admin.get("/arr", follow_redirects=False).headers.get("location") == "/arr/sonarr",
      "with Sonarr the speed card comes with its heading; Bazarr is after it in the sub-tabs")
db.set_setting("arr_sonarr_enabled", "")
db.forget_settings()

print()
print("--- E) the sync, the task, the deletion ---")
r = admin.post("/settings/arr/bazarr/sync")
check("Bazarr: přibylo 0 záznamů historie." in html_lib.unescape(r.text), "Sync now: nothing new, a message with the name")
card = html_lib.unescape(r.text)
card = card[card.find('id="arr-bazarr"'):]
check(f"Stažená historie: {HISTORY}" in re.sub(r"\s+", " ", card) and "Poslední synchronizace" in card, "the card shows the number of records and the last sync")
result = asyncio.run(tasks.run_now("arr"))
check(result["status"] == "ok" and result["message"] == "Bazarr: 0 nových záznamů", f"the task „Synchronizace *Arr“ syncs Bazarr too ({result})")
check("Bazarr" in tasks.TASKS["arr"].description, "and its description says so")
r = admin.post("/settings/arr/bazarr/delete")
check(bazarr.event_count() == 0 and "flash error" not in r.text, "Delete the downloaded data deletes the subtitle history (after a backup)")
fill_bazarr()

print()
print("--- F) rights ---")
space["system/health"] = {"data": [{"object": "/data/media/tv", "issue": "Path not accessible from Bazarr"}]}
bazarr.live.CACHE.clear()
s_admin, s_reader = admin.get("/arr/bazarr?days=0").text, reader.get("/arr/bazarr?days=0").text
check("Path not accessible from Bazarr" in card_html(s_admin, "bazarr_health"), "the administrator sees the warning texts")
check(re.findall(r"<!-- card: (\w+) -->", s_reader) == WITHOUT_SPEED and "Path not accessible" not in s_reader
      and "/data/media/tv" not in s_reader and "Aplikace hlásí varování" in card_html(s_reader, "bazarr_health"),
      "a reader sees the tab (the area is on by default), not the warning texts")
accounts.save_permissions(reader_account["id"], access.pack_permissions(["knihovna", "prehled", "dashboard"], False))
check(reader.get("/arr/bazarr").status_code == 403 and 'href="/arr/' not in menu(reader.get("/account").text),
      "without the *Arr area: 403 and no menu item")
accounts.save_permissions(reader_account["id"], None)
space.clear()
bazarr.live.CACHE.clear()

print()
print("--- G) Interface and an unknown time format ---")
interface = admin.get("/settings?section=interface").text
check('name="ui_max_rows_arr"' in interface and 'name="ui_rows_arr"' in interface, "the field „Řádků v tabulkách *Arr“ with Bazarr alone too")
SENTENCE = "Čas části historie se nepodařilo přečíst"
check(SENTENCE not in admin.get("/settings?section=arr").text, "with a time that was read the card says nothing")
with db.connect() as conn:
    conn.execute("UPDATE bazarr_event SET happened_at = NULL, time_exact = 0 WHERE fingerprint = (SELECT MIN(fingerprint) FROM bazarr_event)")
    conn.commit()
check(SENTENCE in admin.get("/settings?section=arr").text, "an unknown time format: the card in Settings says so")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
