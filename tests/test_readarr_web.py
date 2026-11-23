# -*- coding: utf-8 -*-
r"""The Readarr tab of the *Arr page: sub-tabs of connected applications, statistics cards, the live state, the menu, rights.

Watched:

A) without a connected application the page does not exist (404) and is missing from the menu;
B) /arr redirects to the first connected application, an unconnected one returns 404,
   sub-tabs are only for the connected ones;
C) the page's sections: "Teď v aplikaci", "Co přibylo" (a chart and a heatmap), "Rychlost",
   "Zdroje a kvalita", "Upgrady"; the key nowhere. The cards themselves (the registry, dashboards,
   data) are watched by tests/test_readarr_cards.py;
D) the live state (the queue, missing, cutoff, the calendar, health) is kept
   a minute in the cache - two pages in a row = one request to the application;
E) an unavailable application (Review Focus 4): the sentence "Aplikace neodpovídá",
   the statistics from the database go on showing, the key is not in the text;
F) the rights area "*Arr" is on by default: a reader sees the page; an account without it gets 403 and
   no link in the menu (Review Focus 5).

The applications are not run: the answers are built by hand in the shape of the API v1
(tests/readarr_responses/), the client is replaced by a client with httpx.MockTransport.

Run:
    .venv/bin/python tests/test_readarr_web.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "readarr-page.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import no_network, not_responding, readarr_client, readarr_handler  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, web  # noqa: E402
from jellyscope.arr import readarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no application other than Readarr is to ask here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


asked: list[str] = []
handlers = {"readarr": readarr_handler(asked)}
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


def menu(html: str) -> str:
    """Only the page's main navigation (so that text from elsewhere does not count)."""
    match = re.search(r"<nav[^>]*>.*?</nav>", html, re.S)
    return match.group(0) if match else ""


admin, reader = sign_in("admin"), sign_in("reader")

print("--- A) nothing is connected ---")
check(admin.get("/arr", follow_redirects=False).status_code == 404, "without a connected application /arr = 404")
check(admin.get("/arr/readarr", follow_redirects=False).status_code == 404, "nor /arr/readarr")
check("*Arr" not in menu(admin.get("/account").text), "the menu has no *Arr")

print()
print("--- B) Readarr connected ---")
restore = substitute(readarr, "ReadarrClient", readarr_client(lambda request: handlers["readarr"](request)))
readarr.save_settings(True, "http://readarr.test", "tajny-klic")
asyncio.run(readarr.synchronize())
r = admin.get("/arr", follow_redirects=False)
check(r.status_code == 303 and r.headers.get("location") == "/arr/readarr", f"/arr -> /arr/readarr ({r.status_code})")
check(admin.get("/arr/sonarr").status_code == 404, "an unconnected Sonarr = 404")
check(admin.get("/arr/nothing").status_code == 404, "an unknown application = 404")

print()
print("--- C) the cards ---")
asked.clear()
s = admin.get("/arr/readarr?days=365").text
check('href="/arr/readarr"' in s and 'href="/arr/sonarr"' not in s, "a sub-tab only for the connected application")
check(all(f"<h2>{t}</h2>" in s for t in ("Teď v aplikaci", "Co přibylo", "Rychlost", "Zdroje a kvalita", "Upgrady")),
      "all five sections")
check('class="heatmap"' in s, "the imports' heatmap")
check("tajny-klic" not in s, "the key is not in the page")
check("Válka s mloky" in s and "Karel Čapek" in s and "arr-test" in s, "the books' and authors' names and the indexer from the history")
check("/book/" not in s, "Review Focus 3: without a books library (a server without books) every book is a text, no link to /book/")
check("Has missing files" in s, "the queue: an item with a warning and its text")
check("Indexers unavailable due to failures" in s, "the warning from the application's health")
check("ruční výběr" in s, "how it was downloaded, in Czech")
check("Rozlišení" not in s, "no resolutions on a books page")
check(">*Arr<" in menu(admin.get("/account").text), "the menu has *Arr")
check("<title>*Arr · Readarr · Jellyscope</title>" in s, "the window's title names the application")

# A manual import (no download_id) of a book is one import, not one per file: the page says 4 books for 6 files.
value = re.search(r'Knihy převzaté do knihovny</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(value is not None and value.group(1).strip() == "4", f"four books imported, not six files ({value and value.group(1)})")
value = re.search(r'Upgradů na knihu</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(value is not None and value.group(1).strip() == "1,0", f"upgrades per book with a decimal comma ({value and value.group(1)})")

# "Celá historie" (?days=0) starts with Readarr's first event, not with the first play in Jellyscope.
with db.connect() as conn:
    conn.execute("INSERT INTO readarr_event (arr_id, event_type, happened_at, title, author, item_id, title_id, quality, size_bytes, download_id)"
                 " VALUES (900001, 'bookFileImported', '2025-01-15 10:00:00', 'Stary book', 'Stary author', 900001, 900001, 'MP3-320', 1000, 'old')")
    conn.commit()
check("Stary book" in admin.get("/arr/readarr?days=0").text, "the whole history on /arr reaches back to the first *Arr event, not to the first play")
admin.get("/arr/readarr?days=365")   # the remembered period back, so the further checks do not change

# The page made the same query and pairing for every card; the events are kept by the memory of one computation.
db_queries: list[str] = []
original_query_all = db.query_all
db.query_all = lambda sql, *a, **k: (db_queries.append(sql), original_query_all(sql, *a, **k))[1]
admin.get("/arr/readarr?days=365")
db.query_all = original_query_all
check(sum("ORDER BY happened_at, arr_id" in d for d in db_queries) == 1,
      f"the page selects the period's events once ({sum('ORDER BY happened_at, arr_id' in d for d in db_queries)}×)")

print()
print("--- D) the live state's cache ---")
before = asked.count("queue")
admin.get("/arr/readarr?days=365")
check(before == 1 and asked.count("queue") == 1, f"two pages within a minute = one request for the queue ({asked.count('queue')})")
# After the key is fixed the live state loads at once, not only in a minute.
readarr.save_settings(True, "http://readarr.test", "opraveny-klic")
admin.get("/arr/readarr?days=365")
check(asked.count("queue") == 2, "a new key = a new load of the live state at once")

print()
print("--- G) the settings route (POST /settings/arr/readarr) ---")
s_before_fork = admin.get("/arr/readarr?days=365").text
# The current key is "opraveny-klic" (saved above); the form never shows it and an empty field keeps it.
form = {"enabled": "1", "url": "http://readarr.test:8787/readarr", "api_key": "novy-klic", "action": "save"}
saved = admin.post("/settings/arr/readarr", data=form, follow_redirects=True)
db.forget_settings()
check(db.get_setting("arr_readarr_url") == "http://readarr.test:8787/readarr" and db.get_setting("arr_readarr_api_key") == "novy-klic"
      and readarr.stored_settings() == {"enabled": True, "url": "http://readarr.test:8787/readarr", "has_key": True},
      "action=save stores the address and the new key")
check("novy-klic" not in saved.text and "opraveny-klic" not in saved.text and "http://readarr.test:8787/readarr" in saved.text,
      "the response has the address but never the key")
admin.post("/settings/arr/readarr", data={**form, "api_key": ""}, follow_redirects=False)
db.forget_settings()
check(db.get_setting("arr_readarr_api_key") == "novy-klic", "an empty key field keeps the stored key")
tested = admin.post("/settings/arr/readarr", data={"enabled": "1", "url": "http://readarr.test", "api_key": "zkusebni-klic", "action": "test"})
db.forget_settings()
check(db.get_setting("arr_readarr_url") == "http://readarr.test:8787/readarr" and db.get_setting("arr_readarr_api_key") == "novy-klic",
      "action=test saves nothing")
check("flash error" not in tested.text and "0.4.18.2805" in tested.text and "zkusebni-klic" not in tested.text and "novy-klic" not in tested.text,
      "action=test against the faked Readarr reports its version (0.4.18.2805 from the recording) and shows no key")
# Review Focus 4: Chaptarr and Bookshelf answer to the same API - the test passes and the tab names the app that answers;
# Lidarr or Sonarr at the address is an error and does not change the name.
def status_as(app_name: str) -> dict:
    return {"system/status": httpx.Response(200, json={"appName": app_name, "version": "9.9.9"})}


check("through Readarr" in s_before_fork or "přes Readarr" in s_before_fork, "the subtitle of the tab names Readarr by default")
for fork in ("Chaptarr", "Bookshelf"):
    handlers["readarr"] = readarr_handler(asked, instead=status_as(fork))
    answer = admin.post("/settings/arr/readarr", data={"enabled": "1", "url": "http://readarr.test:8787/readarr", "api_key": "zkusebni-klic", "action": "test"})
    page = admin.get("/arr/readarr?days=365").text
    check("flash error" not in answer.text and "9.9.9" in answer.text and f"přes {fork}," in page,
          f"{fork} at the address: the test passes and the tab's subtitle names {fork}")
    # The user, 23. 11. 2026: the *Arr menu, the tab's heading and the Settings card name the app that is connected.
    check(re.search(rf'href="/arr/readarr"[^>]*>\s*{fork}\s*<', page) and f"<h1>{fork}</h1>" in page
          and f"*Arr · {fork} · Jellyscope" in page and "<h1>Readarr</h1>" not in page,
          f"{fork}: the menu item, the heading and the window title say {fork}, not Readarr")
    settings_page = admin.get("/settings?section=arr").text
    check(f"<h2>{fork}</h2>" in settings_page and "<h2>Sonarr</h2>" in settings_page,
          f"{fork}: the Settings card is named {fork} (the other apps keep their names)")
for other in ("Lidarr", "Sonarr"):
    handlers["readarr"] = readarr_handler(asked, instead=status_as(other))
    answer = admin.post("/settings/arr/readarr", data={"enabled": "1", "url": "http://readarr.test:8787/readarr", "api_key": "zkusebni-klic", "action": "test"})
    page = admin.get("/arr/readarr?days=365").text
    check("flash error" in answer.text and "9.9.9" not in answer.text and "přes Bookshelf," in page,
          f"{other} at the address is an error, the name of the app on the tab stays")
# An address that was only typed (not saved) does not rename the tab, even when a fork answers there.
handlers["readarr"] = readarr_handler(asked, instead=status_as("Chaptarr"))
answer = admin.post("/settings/arr/readarr", data={"enabled": "1", "url": "http://typed-only.test", "api_key": "zkusebni-klic", "action": "test"})
check("flash error" not in answer.text and "přes Bookshelf," in admin.get("/arr/readarr?days=365").text and "přes Chaptarr," not in admin.get("/arr/readarr?days=365").text,
      "testing an unsaved address (Chaptarr there) passes but the tab keeps the name stored before (Bookshelf)")
handlers["readarr"] = readarr_handler(asked)
admin.post("/settings/arr/readarr", data={"enabled": "1", "url": "http://readarr.test:8787/readarr", "api_key": "zkusebni-klic", "action": "test"})
back_page = admin.get("/arr/readarr?days=365").text
check("přes Readarr," in back_page and re.search(r'href="/arr/readarr"[^>]*>\s*Readarr\s*<', back_page)
      and "<h1>Readarr</h1>" in back_page and "<h1>Chaptarr</h1>" not in back_page,
      "back to Readarr: the subtitle, the menu item and the heading follow the app that answered last")
handlers["readarr"] = not_responding()
failed = admin.post("/settings/arr/readarr", data={"enabled": "1", "url": "http://readarr.test", "api_key": "zkusebni-klic", "action": "test"})
check("flash error" in failed.text and "zkusebni-klic" not in failed.text and "novy-klic" not in failed.text,
      "a failed test shows an error, without a key")
handlers["readarr"] = readarr_handler(asked)
check(admin.post("/settings/arr/nothing", data=form, follow_redirects=False).status_code == 404, "an unknown app answers 404")
readarr.save_settings(True, "http://readarr.test", "opraveny-klic")
db.forget_settings()

print()
print("--- F) rights ---")
check(reader.get("/arr/readarr").status_code == 200, "a reader sees the page (the area is on by default)")
# The application's health messages and the queue's messages are the same class of information as the error's text
# (the clients' addresses, paths on the disk) - they belong to the administrator. The others see that something is there, not what.
s_reader = reader.get("/arr/readarr?days=365").text
check("Has missing files" not in s_reader and "Download failed" not in s_reader and "Indexers unavailable due to failures" not in s_reader,
      "whoever is not the administrator sees neither the application's warning texts nor the queue's messages")
check("Varování aplikace" in s_reader and "Válka s mloky" in s_reader, "... but sees that the application reports something, and the books in the queue")
check(">*Arr<" in menu(reader.get("/account").text), "a reader has *Arr in the menu")
# Review Focus 5: an account without the *Arr area has no page and no link.
accounts.save_permissions(accounts.get_by_name("reader")["id"], access.pack_permissions(["library", "overview", "dashboard"], False))
check(reader.get("/arr/readarr").status_code == 403, "an account without the *Arr area: /arr/readarr is 403")
check("/arr/readarr" not in menu(reader.get("/account").text) and ">*Arr<" not in menu(reader.get("/account").text),
      "... and there is no link to it in its menu")
check(reader.get("/partials/arr/readarr/downloading").status_code == 403, "... nor the slice of the card Downloading")
accounts.save_permissions(accounts.get_by_name("reader")["id"], None)

print()
print("--- Only part of the live requests fails ---")
handlers["readarr"] = readarr_handler(asked, instead={"queue": httpx.Response(500), "calendar": httpx.Response(500)})
readarr.live.CACHE.clear()
partial = asyncio.run(readarr.live_state())
check(partial["available"] is True and partial["queue"] is None and partial["calendar"] is None
      and partial["missing"] is not None and bool(partial["error"]),
      "what did not load is None (unknown), not zero; the error is not thrown away")
s = admin.get("/arr/readarr?days=365").text
tile = re.search(r'Ve frontě</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(tile is not None and tile.group(1).strip() == "–" and "V příštích 7 dnech nic nevychází" not in s,
      f"the page shows neither “ve frontě 0” nor “nic nevychází” when it could not be found out ({tile and tile.group(1)})")
check("nepodařilo načíst" in s, "... and says that part did not load")

print()
print("--- E) an unavailable application (Review Focus 4) ---")
handlers["readarr"] = not_responding()
readarr.live.CACHE.clear()
s = admin.get("/arr/readarr?days=365").text
check("Aplikace neodpovídá" in s, "the sentence “Aplikace neodpovídá”")
check("<h2>Co přibylo</h2>" in s and "Válka s mloky" in s, "the statistics from the database go on showing")
check("tajny-klic" not in s and "opraveny-klic" not in s, "the key not even in the error")
# The error's text (internal addresses, a snippet of the application's answer) belongs to the administrator.
check("Connection refused" in s, "the administrator sees the error's detail")
s_reader = reader.get("/arr/readarr?days=365").text
check("Aplikace neodpovídá" in s_reader and "Connection refused" not in s_reader,
      "whoever is not the administrator sees only the sentence that the application does not answer - without details")
state = asyncio.run(readarr.live_state())
check(state["available"] is False and state["error"] and "opraveny-klic" not in state["error"],
      f"live_state: unavailable, with an error without the key ({state['error']})")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
