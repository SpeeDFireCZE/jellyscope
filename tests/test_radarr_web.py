# -*- coding: utf-8 -*-
r"""The Radarr tab of the *Arr page: sub-tabs of connected applications, statistics cards, the live state, the menu, rights.

Watched:

A) without a connected application the page does not exist (404) and is missing from the menu;
B) /arr redirects to the first connected application, an unconnected one returns 404,
   sub-tabs are only for the connected ones;
C) the page's sections: "Teď v aplikaci", "Co přibylo" (a chart and a heatmap), "Rychlost",
   "Zdroje a kvalita", "Upgrady"; the key nowhere. The cards themselves (the registry, dashboards,
   data) are watched by tests/test_radarr_cards.py;
D) the live state (the queue, missing, cutoff, the calendar, health) is kept
   a minute in the cache - two pages in a row = one request to the application;
E) an unavailable application (Review Focus 4): the sentence "Aplikace neodpovídá",
   the statistics from the database go on showing, the key is not in the text;
F) the rights area "*Arr" is on by default: a reader sees the page.

The applications are not run: the answers are recorded from a real Radarr
(tests/radarr_responses/), the client is replaced by a client with httpx.MockTransport.

Run:
    .venv/bin/python tests/test_radarr_web.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "arr-stranka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import no_network, radarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, web  # noqa: E402
from jellyscope.arr import radarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "radarr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no application other than Radarr is to ask here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> object:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


asked: list[str] = []
# Radarr's warning with an address from the internal network: the text belongs to the administrator only.
WARNING_TEXT = "Unable to communicate with qBittorrent at http://192.168.1.5:8080"


def recorded_radarr(request: httpx.Request) -> httpx.Response:
    """The recorded Radarr: its real answer to every API address."""
    path = request.url.path.split("/api/v3/", 1)[-1]
    # The whole queue for the live card "Stahuje se" (pageSize=200) has a name and a recording of its own
    # with progress: the tests of the live state's cache below count only its request for the queue.
    if path == "queue" and request.url.params.get("pageSize") == "200":
        asked.append("queue/cela")
        return httpx.Response(200, json=recorded("queue_stahuje"))
    asked.append(path)
    names = {"history/since": "history_since", "queue": "queue", "wanted/missing": "wanted_missing",
             "wanted/cutoff": "wanted_cutoff", "calendar": "calendar", "system/status": "system_status"}
    if path == "health":           # the recorded Radarr has no warning - here it has one, so there is something to hide
        return httpx.Response(200, json=[{"source": "DownloadClientCheck", "type": "warning", "wikiUrl": "",
                                          "message": WARNING_TEXT}])
    if path == "history":          # the first synchronisation goes page by page (the whole on one page)
        history = recorded("history_since")
        return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": len(history),
                                         "records": history})
    if path not in names:
        return httpx.Response(404)
    return httpx.Response(200, json=recorded(names[path]))


def not_answering(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


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
check(admin.get("/arr/radarr", follow_redirects=False).status_code == 404, "nor /arr/radarr")
check("*Arr" not in menu(admin.get("/account").text), "the menu has no *Arr")

print()
print("--- B) Radarr connected ---")
restore = substitute(radarr, "RadarrClient", radarr_client(recorded_radarr))
radarr.save_settings(True, "http://radarr.test", "tajny-klic")
asyncio.run(radarr.synchronize())
r = admin.get("/arr", follow_redirects=False)
check(r.status_code == 303 and r.headers.get("location") == "/arr/radarr", f"/arr -> /arr/radarr ({r.status_code})")
check(admin.get("/arr/sonarr").status_code == 404, "an unconnected Sonarr = 404")
check(admin.get("/arr/lidarr").status_code == 404, "an unconnected Lidarr = 404")

print()
print("--- C) the cards ---")
asked.clear()
s = admin.get("/arr/radarr?days=365").text
check('href="/arr/radarr"' in s and 'href="/arr/sonarr"' not in s, "a sub-tab only for the connected application")
check(all(f"<h2>{t}</h2>" in s for t in ("Teď v aplikaci", "Co přibylo", "Rychlost", "Zdroje a kvalita", "Upgrady")),
      "all five sections")
check('class="heatmap"' in s, "the imports' heatmap")
check("tajny-klic" not in s, "the key is not in the page")
check("Sintel" in s and "Big Buck Bunny" in s and "arr-test" in s, "the movies' names and the indexer from the history")
check("No audio tracks detected" in s, "the queue: an item with a warning and its text")
check(WARNING_TEXT in s, "the warning from the application's health")
check("ruční výběr" in s, "how it was downloaded, in Czech")
check(">*Arr<" in menu(admin.get("/account").text), "the menu has *Arr")

# Durations readably: from two days up in days - a movie from 2006 imported today would
# otherwise stand in the tile as "136 089 h" (found live, 12. 11. 2026).
# (The helpers are shared by the cards of all applications: cards_base.py.)
from jellyscope import cards_base  # noqa: E402

check(cards_base.duration(None) == "–" and cards_base.duration(2760) == "46 min", "duration: empty = –, short in minutes")
check(cards_base.duration(47 * 3600).startswith("47 h") and cards_base.duration(2 * 86400) == "2 d",
      "duration: up to two days in hours, then in days")
check(cards_base.duration(136089 * 3600).replace("\xa0", " ") == "5 670 d", f"duration: years in days ({cards_base.duration(136089 * 3600)})")
check(not re.search(r"\d{1,3}(?:[ \xa0]\d{3})+ h", s), "no duration in thousands of hours in the page")

# Review finding 2: "Upgradů na položku" is an average (1,5), the number filter cut it to 1.
value = re.search(r'Upgradů na položku</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(value is not None and value.group(1).strip() == "1,0", f"upgrades per item with a decimal comma ({value and value.group(1)})")
check(cards_base.per_item(1.5) == "1,5" and cards_base.per_item(2.67) == "2,7" and cards_base.per_item(0) == "0",
      "the average to one decimal place, zero without decimals")

# Review finding 1: "Celá historie" (?days=0) started with the first play in Jellyscope
# (without a play with today) - Radarr's older history was quietly left out.
with db.connect() as conn:
    conn.execute("INSERT INTO radarr_event (arr_id, event_type, happened_at, title, item_id, quality, size_bytes)"
                 " VALUES (900001, 'downloadFolderImported', '2025-01-15 10:00:00', 'Stary film',"
                 " 900001, 'WEBDL-720p', 1000)")
    conn.commit()
s0 = admin.get("/arr/radarr?days=0").text
check("Stary film" in s0, "the whole history on /arr reaches back to the first *Arr event, not to the first play")
admin.get("/arr/radarr?days=365")   # the remembered period back, so the further checks do not change

# A minor from the review: the page made the same query and pairing five times (for every card).
# Now there are twenty-three cards and the events are kept by the memory of one computation (cards_base._once).
db_queries: list[str] = []
original_query_all = db.query_all
db.query_all = lambda sql, *a, **k: (db_queries.append(sql), original_query_all(sql, *a, **k))[1]
admin.get("/arr/radarr?days=365")
db.query_all = original_query_all
check(sum("ORDER BY happened_at, arr_id" in d for d in db_queries) == 1,
      f"the page selects the period's events once ({sum('ORDER BY happened_at, arr_id' in d for d in db_queries)}×)")

print()
print("--- D) the live state's cache ---")
before = asked.count("queue")
admin.get("/arr/radarr?days=365")
check(before == 1 and asked.count("queue") == 1, f"two pages within a minute = one request for the queue ({asked.count('queue')})")
# A minor from the review: after the key is fixed the live state loads at once, not only in a minute.
radarr.save_settings(True, "http://radarr.test", "opraveny-klic")
admin.get("/arr/radarr?days=365")
check(asked.count("queue") == 2, "a new key = a new load of the live state at once")

print()
print("--- F) rights ---")
check(reader.get("/arr/radarr").status_code == 200, "a reader sees the page (the area is on by default)")
# Code review: the application's health messages and the queue's messages are the same class of information as the
# error's text (the clients' addresses, paths on the disk) - they belong to the administrator. The others see that something is there, not what.
s_reader = reader.get("/arr/radarr?days=365").text
check(WARNING_TEXT not in s_reader and "192.168.1.5" not in s_reader and "No audio tracks detected" not in s_reader,
      "whoever is not the administrator sees neither the application's warning texts nor the queue's messages")
check("Varování aplikace" in s_reader and "Elephants Dream" in s_reader,
      "... but sees that the application reports something, and the movies in the queue")
check(">*Arr<" in menu(reader.get("/account").text), "a reader has *Arr in the menu")

print()
print("--- Code review: only part of the live requests fails ---")


def without_queue_and_calendar(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v3/", 1)[-1]
    return httpx.Response(500) if path in ("queue", "calendar") else recorded_radarr(request)


restore()
restore = substitute(radarr, "RadarrClient", radarr_client(without_queue_and_calendar))
radarr.live.CACHE.clear()
partial = asyncio.run(radarr.live_state())
check(partial["available"] is True and partial["queue"] is None and partial["calendar"] is None
      and partial["health"] is not None and bool(partial["error"]),
      "what did not load is None (unknown), not zero; the error is not thrown away")
s = admin.get("/arr/radarr?days=365").text
tile = re.search(r'Ve frontě</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(tile is not None and tile.group(1).strip() == "–" and "V příštích 7 dnech nic nevychází" not in s,
      f"the page shows neither “ve frontě 0” nor “nic nevychází” when it could not be found out ({tile and tile.group(1)})")
check("nepodařilo načíst" in s, "... and says that part did not load")

print()
print("--- E) an unavailable application (Review Focus 4) ---")
restore()
restore = substitute(radarr, "RadarrClient", radarr_client(not_answering))
radarr.live.CACHE.clear()
s = admin.get("/arr/radarr?days=365").text
check("Aplikace neodpovídá" in s, "the sentence “Aplikace neodpovídá”")
check("<h2>Co přibylo</h2>" in s and "Sintel" in s, "the statistics from the database go on showing")
check("tajny-klic" not in s, "the key not even in the error")
# A security finding (13. 11. 2026): the error's text (internal addresses, a snippet of the application's answer) belongs
# to the administrator. The others see only that the application does not answer.
check("Connection refused" in s, "the administrator sees the error's detail")
s_reader = reader.get("/arr/radarr?days=365").text
check("Aplikace neodpovídá" in s_reader and "Connection refused" not in s_reader,
      "whoever is not the administrator sees only the sentence that the application does not answer - without details")
state = asyncio.run(radarr.live_state())
check(state["available"] is False and state["error"] and "tajny-klic" not in state["error"],
      f"live_state: unavailable, with an error without the key ({state['error']})")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
