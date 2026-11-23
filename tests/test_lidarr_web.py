# -*- coding: utf-8 -*-
r"""The Lidarr tab of the *Arr page: sub-tabs of connected applications, statistics cards, the live state, the menu, rights.

Watched:

A) without a connected application the page does not exist (404) and is missing from the menu;
B) /arr redirects to the first connected application, an unconnected one returns 404,
   sub-tabs are only for the connected ones;
C) the page's sections: "Teď v aplikaci", "Co přibylo" (a chart and a heatmap), "Rychlost",
   "Zdroje a kvalita", "Upgrady"; the key nowhere. The cards themselves (the registry, dashboards,
   data) are watched by tests/test_lidarr_cards.py;
D) the live state (the queue, missing, cutoff, the calendar, health) is kept
   a minute in the cache - two pages in a row = one request to the application;
E) an unavailable application (Review Focus 4): the sentence "Aplikace neodpovídá",
   the statistics from the database go on showing, the key is not in the text;
F) the rights area "*Arr" is on by default: a reader sees the page; an account without it gets 403 and
   no link in the menu (Review Focus 5).

The applications are not run: the answers are built by hand in the shape of the API v1
(tests/lidarr_responses/), the client is replaced by a client with httpx.MockTransport.

Run:
    .venv/bin/python tests/test_lidarr_web.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lidarr-page.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import lidarr_client, lidarr_handler, no_network, not_responding  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, web  # noqa: E402
from jellyscope.arr import lidarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no application other than Lidarr is to ask here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


asked: list[str] = []
handlers = {"lidarr": lidarr_handler(asked)}
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
check(admin.get("/arr/lidarr", follow_redirects=False).status_code == 404, "nor /arr/lidarr")
check("*Arr" not in menu(admin.get("/account").text), "the menu has no *Arr")

print()
print("--- B) Lidarr connected ---")
restore = substitute(lidarr, "LidarrClient", lidarr_client(lambda request: handlers["lidarr"](request)))
lidarr.save_settings(True, "http://lidarr.test", "tajny-klic")
asyncio.run(lidarr.synchronize())
r = admin.get("/arr", follow_redirects=False)
check(r.status_code == 303 and r.headers.get("location") == "/arr/lidarr", f"/arr -> /arr/lidarr ({r.status_code})")
check(admin.get("/arr/sonarr").status_code == 404, "an unconnected Sonarr = 404")
check(admin.get("/arr/nothing").status_code == 404, "an unknown application = 404")

print()
print("--- C) the cards ---")
asked.clear()
s = admin.get("/arr/lidarr?days=365").text
check('href="/arr/lidarr"' in s and 'href="/arr/sonarr"' not in s, "a sub-tab only for the connected application")
check(all(f"<h2>{t}</h2>" in s for t in ("Teď v aplikaci", "Co přibylo", "Rychlost", "Zdroje a kvalita", "Upgrady")),
      "all five sections")
check('class="heatmap"' in s, "the imports' heatmap")
check("tajny-klic" not in s, "the key is not in the page")
check("Hvězdy" in s and "Kryštof" in s and "arr-test" in s, "the albums' and artists' names and the indexer from the history")
check("Has missing tracks" in s, "the queue: an item with a warning and its text")
check("Indexers unavailable due to failures" in s, "the warning from the application's health")
check("ruční výběr" in s, "how it was downloaded, in Czech")
check("Rozlišení" not in s, "no resolutions on a music page")
check(">*Arr<" in menu(admin.get("/account").text), "the menu has *Arr")
check("<title>*Arr · Lidarr · Jellyscope</title>" in s, "the window's title names the application")

# A manual import (no download_id) of an album is one import, not one per track: the page says 4 albums for 10 tracks.
value = re.search(r'Alba převzatá do knihovny</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(value is not None and value.group(1).strip() == "4", f"four albums imported, not ten tracks ({value and value.group(1)})")
value = re.search(r'Upgradů na album</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(value is not None and value.group(1).strip() == "1,0", f"upgrades per album with a decimal comma ({value and value.group(1)})")

# "Celá historie" (?days=0) starts with Lidarr's first event, not with the first play in Jellyscope.
with db.connect() as conn:
    conn.execute("INSERT INTO lidarr_event (arr_id, event_type, happened_at, title, artist, item_id, title_id, quality, size_bytes, download_id)"
                 " VALUES (900001, 'trackFileImported', '2025-01-15 10:00:00', 'Stary album', 'Stary interpret', 900001, 900001, 'MP3-320', 1000, 'old')")
    conn.commit()
check("Stary album" in admin.get("/arr/lidarr?days=0").text, "the whole history on /arr reaches back to the first *Arr event, not to the first play")
admin.get("/arr/lidarr?days=365")   # the remembered period back, so the further checks do not change

# The page made the same query and pairing for every card; the events are kept by the memory of one computation.
db_queries: list[str] = []
original_query_all = db.query_all
db.query_all = lambda sql, *a, **k: (db_queries.append(sql), original_query_all(sql, *a, **k))[1]
admin.get("/arr/lidarr?days=365")
db.query_all = original_query_all
check(sum("ORDER BY happened_at, arr_id" in d for d in db_queries) == 1,
      f"the page selects the period's events once ({sum('ORDER BY happened_at, arr_id' in d for d in db_queries)}×)")

print()
print("--- D) the live state's cache ---")
before = asked.count("queue")
admin.get("/arr/lidarr?days=365")
check(before == 1 and asked.count("queue") == 1, f"two pages within a minute = one request for the queue ({asked.count('queue')})")
# After the key is fixed the live state loads at once, not only in a minute.
lidarr.save_settings(True, "http://lidarr.test", "opraveny-klic")
admin.get("/arr/lidarr?days=365")
check(asked.count("queue") == 2, "a new key = a new load of the live state at once")

print()
print("--- G) the settings route (POST /settings/arr/lidarr) ---")
# The current key is "opraveny-klic" (saved above); the form never shows it and an empty field keeps it.
form = {"enabled": "1", "url": "http://lidarr.test:8686/lidarr", "api_key": "novy-klic", "action": "save"}
saved = admin.post("/settings/arr/lidarr", data=form, follow_redirects=True)
db.forget_settings()
check(db.get_setting("arr_lidarr_url") == "http://lidarr.test:8686/lidarr" and db.get_setting("arr_lidarr_api_key") == "novy-klic"
      and lidarr.stored_settings() == {"enabled": True, "url": "http://lidarr.test:8686/lidarr", "has_key": True},
      "action=save stores the address and the new key")
check("novy-klic" not in saved.text and "opraveny-klic" not in saved.text and "http://lidarr.test:8686/lidarr" in saved.text,
      "the response has the address but never the key")
admin.post("/settings/arr/lidarr", data={**form, "api_key": ""}, follow_redirects=False)
db.forget_settings()
check(db.get_setting("arr_lidarr_api_key") == "novy-klic", "an empty key field keeps the stored key")
tested = admin.post("/settings/arr/lidarr", data={"enabled": "1", "url": "http://lidarr.test", "api_key": "zkusebni-klic", "action": "test"})
db.forget_settings()
check(db.get_setting("arr_lidarr_url") == "http://lidarr.test:8686/lidarr" and db.get_setting("arr_lidarr_api_key") == "novy-klic",
      "action=test saves nothing")
check("flash error" not in tested.text and "3.1.6.5079" in tested.text and "zkusebni-klic" not in tested.text and "novy-klic" not in tested.text,
      "action=test against the faked Lidarr reports its version (3.1.6.5079 from the recording) and shows no key")
handlers["lidarr"] = not_responding()
failed = admin.post("/settings/arr/lidarr", data={"enabled": "1", "url": "http://lidarr.test", "api_key": "zkusebni-klic", "action": "test"})
check("flash error" in failed.text and "zkusebni-klic" not in failed.text and "novy-klic" not in failed.text,
      "a failed test shows an error, without a key")
handlers["lidarr"] = lidarr_handler(asked)
check(admin.post("/settings/arr/nothing", data=form, follow_redirects=False).status_code == 404, "an unknown app answers 404")
lidarr.save_settings(True, "http://lidarr.test", "opraveny-klic")
db.forget_settings()

print()
print("--- F) rights ---")
check(reader.get("/arr/lidarr").status_code == 200, "a reader sees the page (the area is on by default)")
# The application's health messages and the queue's messages are the same class of information as the error's text
# (the clients' addresses, paths on the disk) - they belong to the administrator. The others see that something is there, not what.
s_reader = reader.get("/arr/lidarr?days=365").text
check("Has missing tracks" not in s_reader and "Download failed" not in s_reader and "Indexers unavailable due to failures" not in s_reader,
      "whoever is not the administrator sees neither the application's warning texts nor the queue's messages")
check("Varování aplikace" in s_reader and "Hvězdy" in s_reader, "... but sees that the application reports something, and the albums in the queue")
check(">*Arr<" in menu(reader.get("/account").text), "a reader has *Arr in the menu")
# Review Focus 5: an account without the *Arr area has no page and no link.
accounts.save_permissions(accounts.get_by_name("reader")["id"], access.pack_permissions(["library", "overview", "dashboard"], False))
check(reader.get("/arr/lidarr").status_code == 403, "an account without the *Arr area: /arr/lidarr is 403")
check("/arr/lidarr" not in menu(reader.get("/account").text) and ">*Arr<" not in menu(reader.get("/account").text),
      "... and there is no link to it in its menu")
check(reader.get("/partials/arr/lidarr/downloading").status_code == 403, "... nor the slice of the card Downloading")
accounts.save_permissions(accounts.get_by_name("reader")["id"], None)

print()
print("--- Only part of the live requests fails ---")
handlers["lidarr"] = lidarr_handler(asked, instead={"queue": httpx.Response(500), "calendar": httpx.Response(500)})
lidarr.live.CACHE.clear()
partial = asyncio.run(lidarr.live_state())
check(partial["available"] is True and partial["queue"] is None and partial["calendar"] is None
      and partial["missing"] is not None and bool(partial["error"]),
      "what did not load is None (unknown), not zero; the error is not thrown away")
s = admin.get("/arr/lidarr?days=365").text
tile = re.search(r'Ve frontě</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(tile is not None and tile.group(1).strip() == "–" and "V příštích 7 dnech nic nevychází" not in s,
      f"the page shows neither “ve frontě 0” nor “nic nevychází” when it could not be found out ({tile and tile.group(1)})")
check("nepodařilo načíst" in s, "... and says that part did not load")

print()
print("--- E) an unavailable application (Review Focus 4) ---")
handlers["lidarr"] = not_responding()
lidarr.live.CACHE.clear()
s = admin.get("/arr/lidarr?days=365").text
check("Aplikace neodpovídá" in s, "the sentence “Aplikace neodpovídá”")
check("<h2>Co přibylo</h2>" in s and "Hvězdy" in s, "the statistics from the database go on showing")
check("tajny-klic" not in s and "opraveny-klic" not in s, "the key not even in the error")
# The error's text (internal addresses, a snippet of the application's answer) belongs to the administrator.
check("Connection refused" in s, "the administrator sees the error's detail")
s_reader = reader.get("/arr/lidarr?days=365").text
check("Aplikace neodpovídá" in s_reader and "Connection refused" not in s_reader,
      "whoever is not the administrator sees only the sentence that the application does not answer - without details")
state = asyncio.run(lidarr.live_state())
check(state["available"] is False and state["error"] and "opraveny-klic" not in state["error"],
      f"live_state: unavailable, with an error without the key ({state['error']})")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
