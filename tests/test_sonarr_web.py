# -*- coding: utf-8 -*-
r"""The Sonarr tab of the *Arr page: sub-tabs of connected applications, statistics cards, the live state, the menu, rights.

Watched:

A) without a connected application the page does not exist (404) and is missing from the menu;
B) /arr redirects to the first connected application, an unconnected one returns 404,
   sub-tabs are only for the connected ones;
C) the page's sections: "Teď v aplikaci", "Co přibylo" (a chart and a heatmap), "Rychlost",
   "Zdroje a kvalita", "Upgrady"; the key nowhere. The cards themselves (the registry, dashboards,
   data) are watched by tests/test_sonarr_cards.py;
D) the live state (the queue, missing, cutoff, the calendar, health) is kept
   a minute in the cache - two pages in a row = one request to the application;
E) an unavailable application (Review Focus 4): the sentence "Aplikace neodpovídá",
   the statistics from the database go on showing, the key is not in the text;
F) the rights area "*Arr" is on by default: a reader sees the page.

The applications are not run: the answers are recorded from a real Sonarr
(tests/sonarr_responses/), the client is replaced by a client with httpx.MockTransport.

Run:
    .venv/bin/python tests/test_sonarr_web.py
"""
from __future__ import annotations

import asyncio
import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "arr-stranka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import no_network, sonarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, services, web  # noqa: E402
from jellyscope.arr import registry, sonarr  # noqa: E402
from jellyscope.arr.sonarr.client import SETTINGS_PREFIX  # noqa: E402

RESPONSES = PROJECT / "tests" / "sonarr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no application other than Sonarr is to ask here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> object:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


asked: list[str] = []


def recorded_sonarr(request: httpx.Request) -> httpx.Response:
    """The recorded Sonarr: its real answer to every API address."""
    path = request.url.path.split("/api/v3/", 1)[-1]
    # The whole queue for the live card "Stahuje se" (pageSize=200) has a name and a recording of its own
    # with progress: the tests of the live state's cache below count only its request for the queue.
    if path == "queue" and request.url.params.get("pageSize") == "200":
        asked.append("queue/cela")
        return httpx.Response(200, json=recorded("queue_stahuje"))
    asked.append(path)
    names = {"history/since": "history_since", "queue": "queue", "wanted/missing": "wanted_missing",
             "wanted/cutoff": "wanted_cutoff", "calendar": "calendar", "health": "health",
             "system/status": "system_status"}
    if path == "history":          # the first synchronisation goes page by page (the whole on one page)
        history = recorded("history_since")
        return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": len(history),
                                         "records": history})
    if path not in names:
        return httpx.Response(404)
    return httpx.Response(200, json=recorded(names[path]))


def tile_note(html: str, label: str) -> tuple[str, str]:
    """(the value, the note) of the tile with the label."""
    found = re.search(rf'{re.escape(label)}</p>\s*<div class="tile-value">([^<]*)</div>\s*(?:<div class="delta flat">([^<]*)</div>)?',
                      html)
    return (found.group(1).strip(), (found.group(2) or "").strip()) if found else ("", "")


def sonarr_counts() -> dict[str, Any]:
    return services.stored_counts(SETTINGS_PREFIX, services.settings_fingerprint("http://sonarr.test", "opraveny-klic"))


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
check(admin.get("/arr/sonarr", follow_redirects=False).status_code == 404, "nor /arr/sonarr")
check("*Arr" not in menu(admin.get("/account").text), "the menu has no *Arr")

print()
print("--- B) Sonarr connected ---")
restore = substitute(sonarr, "SonarrClient", sonarr_client(recorded_sonarr))
sonarr.save_settings(True, "http://sonarr.test", "tajny-klic")
asyncio.run(sonarr.synchronize())
r = admin.get("/arr", follow_redirects=False)
check(r.status_code == 303 and r.headers.get("location") == "/arr/sonarr", f"/arr -> /arr/sonarr ({r.status_code})")
check(admin.get("/arr/radarr").status_code == 404, "an unconnected Radarr = 404")
check(admin.get("/arr/lidarr").status_code == 404, "an unconnected Lidarr = 404")

print()
print("--- C) the cards ---")
asked.clear()
s = admin.get("/arr/sonarr?days=365").text
check('href="/arr/sonarr"' in s and 'href="/arr/radarr"' not in s, "a sub-tab only for the connected application")
check(all(f"<h2>{t}</h2>" in s for t in ("Teď v aplikaci", "Co přibylo", "Rychlost", "Zdroje a kvalita", "Upgrady")),
      "all five sections")
check('class="heatmap"' in s, "the imports' heatmap")
check("tajny-klic" not in s, "the key is not in the page")
check("Game of Thrones" in s and "arr-test" in s, "the titles' names and the indexer from the history")
check("No audio tracks detected" in s, "the queue: an item with a warning and its text")
check("No indexers available with RSS sync enabled" in s, "the warning from the application's health")
check("ruční výběr" in s, "how it was downloaded, in Czech")
check(">*Arr<" in menu(admin.get("/account").text), "the menu has *Arr")

# Durations readably: from two days up in days - an episode from 2011 imported today would
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
# (without a play with today) - Sonarr's older history was quietly left out.
with db.connect() as conn:
    conn.execute("INSERT INTO sonarr_event (arr_id, event_type, happened_at, title, episode, item_id, quality, size_bytes)"
                 " VALUES (900001, 'downloadFolderImported', '2025-01-15 10:00:00', 'Stary serial',"
                 " 'S01E01', 900001, 'WEBDL-720p', 1000)")
    conn.commit()
s0 = admin.get("/arr/sonarr?days=0").text
check("Stary serial" in s0, "the whole history on /arr reaches back to the first *Arr event, not to the first play")
admin.get("/arr/sonarr?days=365")   # the remembered period back, so the further checks do not change

# A minor from the review: the page made the same query and pairing five times (for every card).
# Now there are twenty-three cards and the events are kept by the memory of one computation (cards_base._once).
db_queries: list[str] = []
original_query_all = db.query_all
db.query_all = lambda sql, *a, **k: (db_queries.append(sql), original_query_all(sql, *a, **k))[1]
admin.get("/arr/sonarr?days=365")
db.query_all = original_query_all
check(sum("ORDER BY happened_at, arr_id" in d for d in db_queries) == 1,
      f"the page selects the period's events once ({sum('ORDER BY happened_at, arr_id' in d for d in db_queries)}×)")

print()
print("--- D) the live state's cache ---")
before = asked.count("queue")
admin.get("/arr/sonarr?days=365")
check(before == 1 and asked.count("queue") == 1, f"two pages within a minute = one request for the queue ({asked.count('queue')})")
# A minor from the review: after the key is fixed the live state loads at once, not only in a minute.
sonarr.save_settings(True, "http://sonarr.test", "opraveny-klic")
admin.get("/arr/sonarr?days=365")
check(asked.count("queue") == 2, "a new key = a new load of the live state at once")

print()
print("--- F) rights ---")
check(reader.get("/arr/sonarr").status_code == 200, "a reader sees the page (the area is on by default)")
# Code review: the application's health messages and the queue's messages are the same class of information as the
# error's text (the clients' addresses, paths on the disk) - they belong to the administrator. The others see that something is there, not what.
s_reader = reader.get("/arr/sonarr?days=365").text
check("No indexers available with RSS sync enabled" not in s_reader and "No audio tracks detected" not in s_reader,
      "whoever is not the administrator sees neither the application's warning texts nor the queue's messages")
check("Varování aplikace" in s_reader and "Game of Thrones" in s_reader,
      "... but sees that the application reports something, and the titles in the queue")
check(">*Arr<" in menu(reader.get("/account").text), "a reader has *Arr in the menu")

print()
print("--- Code review: only part of the live requests fails ---")


def without_queue_and_calendar(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v3/", 1)[-1]
    return httpx.Response(500) if path in ("queue", "calendar") else recorded_sonarr(request)


restore()
restore = substitute(sonarr, "SonarrClient", sonarr_client(without_queue_and_calendar))
sonarr.live.CACHE.clear()
partial = asyncio.run(sonarr.live_state())
check(partial["available"] is True and partial["queue"] is None and partial["calendar"] is None
      and partial["health"] is not None and bool(partial["error"]),
      "what did not load is None (unknown), not zero; the error is not thrown away")
s = admin.get("/arr/sonarr?days=365").text
tile = re.search(r'Ve frontě</p>\s*<div class="tile-value">([^<]+)</div>', s)
check(tile is not None and tile.group(1).strip() == "–" and "V příštích 7 dnech nic nevychází" not in s,
      f"the page shows neither “ve frontě 0” nor “nic nevychází” when it could not be found out ({tile and tile.group(1)})")
check("nepodařilo načíst" in s, "... and says that part did not load")

# Audit QA-3: an odd record (a text or a number where an object belongs) does not break the whole state - it is named
# by what is there, like Readarr's. A text in statusMessages raised AttributeError and every page view failed (500).
ODD_QUEUE = {"totalRecords": 3, "records": [
    {"title": "Odd release", "seriesId": 7, "trackedDownloadStatus": "warning", "series": "text", "episode": ["x"],
     "statusMessages": ["a text", 5, None, {"title": "Odd", "messages": "not a list"}, {"title": "Fine", "messages": ["m1", "m2", "m3"]}]},
    {"title": "Another", "seriesId": 8, "status": "failed", "series": 5, "episode": 7, "statusMessages": "oops"},
    "not a record"]}
ODD_CALENDAR = [{"seriesId": 9, "title": "Calendar odd", "series": [1], "seasonNumber": 1, "episodeNumber": 2,
                 "airDateUtc": "2026-11-25T00:00:00Z"}, 3]


def odd_records(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v3/", 1)[-1]
    if path == "queue" and request.url.params.get("pageSize") != "200":
        return httpx.Response(200, json=ODD_QUEUE)
    if path == "calendar":
        return httpx.Response(200, json=ODD_CALENDAR)
    return recorded_sonarr(request)


restore()
restore = substitute(sonarr, "SonarrClient", sonarr_client(odd_records))
sonarr.live.CACHE.clear()
try:
    odd_state = asyncio.run(sonarr.live_state())
except Exception as error:      # noqa: BLE001 - the check below reports it
    odd_state = {"available": False, "queue": None, "calendar": None, "error": repr(error)}
odd = odd_state["queue"]["with_error"] if odd_state["queue"] else None
check(odd_state["available"] and odd is not None
      and [(e["title"], e["title_id"], e["episode"]) for e in odd] == [("Odd release", 7, ""), ("Another", 8, "")]
      and odd[0]["message"] == "m1; m2" and odd[1]["message"] == "",
      f"an odd queue record (series, episode, statusMessages of the wrong kind) is named by its release and does not break the state ({odd or odd_state['error']})")
check(odd_state["calendar"] is not None
      and [(c["title"], c["title_id"], c["episode"]) for c in odd_state["calendar"]] == [("Calendar odd", 9, "S01E02")],
      f"an odd calendar record is named by its title ({odd_state['calendar']})")
try:
    odd_page = reader.get("/arr/sonarr?days=365").status_code
except Exception as error:      # noqa: BLE001 - the check below reports it
    odd_page = repr(error)
check(odd_page == 200, f"the page with an odd queue record shows ({odd_page})")
# Audit stats-apps-forms-3: the queue answer itself of the wrong kind (a text for the count, a number or an object for
# the list) is no error of the whole state either - it ran after the requests, outside any try, and every view failed.
odd_answers = []
for answer in ({"totalRecords": "n/a", "records": []}, {"records": 5}, {"totalRecords": [1], "records": {"a": 1}},
               {"totalRecords": None, "records": [{"title": "A"}, "x"]}):
    try:
        odd_answers.append(sonarr.live._queue(answer)["count"])
    except Exception as error:      # noqa: BLE001 - the check below reports it
        odd_answers.append(repr(error))
check(odd_answers == [0, 0, 0, 1], f"a queue answer of the wrong kind counts what is there ({odd_answers})")

# Audit QA-6: the minute queue (the tile "Ve frontě", the card of queue items with an error) left out a download
# Sonarr did not pair with any title - the API leaves it out without `includeUnknownSeriesItems` - while the card "Stahuje se"
# (whole_queue) showed it. Such an item is the one that needs a hand (a manual import).
UNKNOWN_ITEM = {"title": "Unknown.Release.1080p", "status": "completed", "trackedDownloadStatus": "warning",
                "trackedDownloadState": "importPending", "statusMessages": [{"title": "x", "messages": ["Manual import required"]}]}


def with_unknown_item(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/api/v3/queue") and request.url.params.get("pageSize") != "200":
        records = [UNKNOWN_ITEM] if request.url.params.get("includeUnknownSeriesItems") == "true" else []
        return httpx.Response(200, json={"totalRecords": len(records), "records": records})
    return recorded_sonarr(request)


restore()
restore = substitute(sonarr, "SonarrClient", sonarr_client(with_unknown_item))
sonarr.live.CACHE.clear()
unknown_state = asyncio.run(sonarr.live_state())
check(unknown_state["queue"] is not None and unknown_state["queue"]["count"] == 1
      and [e["title"] for e in unknown_state["queue"]["with_error"]] == ["Unknown.Release.1080p"],
      f"a download Sonarr did not pair is in the queue's count and among the items with an error ({unknown_state['queue']})")

print()
print("--- E) an unavailable application (Review Focus 4) ---")
restore()
restore = substitute(sonarr, "SonarrClient", sonarr_client(not_answering))
sonarr.live.CACHE.clear()
s = admin.get("/arr/sonarr?days=365").text
check("Aplikace neodpovídá" in s, "the sentence “Aplikace neodpovídá”")
check("<h2>Co přibylo</h2>" in s and "Game of Thrones" in s, "the statistics from the database go on showing")
check("tajny-klic" not in s, "the key not even in the error")
# A security finding (13. 11. 2026): the error's text (internal addresses, a snippet of the application's answer) belongs
# to the administrator. The others see only that the application does not answer.
check("Connection refused" in s, "the administrator sees the error's detail")
s_reader = reader.get("/arr/sonarr?days=365").text
check("Aplikace neodpovídá" in s_reader and "Connection refused" not in s_reader,
      "whoever is not the administrator sees only the sentence that the application does not answer - without details")
state = asyncio.run(sonarr.live_state())
check(state["available"] is False and state["error"] and "tajny-klic" not in state["error"],
      f"live_state: unavailable, with an error without the key ({state['error']})")
restore()

print()
print("--- G) the counts from the sync, live data with a short limit (23. 11. 2026) ---")
# A big Sonarr did not count the cutoff within 20 s and every page load after a minute waited for it (21 s). The
# sync counts missing and cutoff now, the page only reads them; the queue, calendar and health stay live, 3 s.
limits: list[tuple[str, Any]] = []


def recorded_with_limits(request: httpx.Request) -> httpx.Response:
    limits.append((request.url.path.split("/api/v3/", 1)[-1], request.extensions.get("timeout", {}).get("read")))
    return recorded_sonarr(request)


restore = substitute(sonarr, "SonarrClient", sonarr_client(recorded_with_limits))
asyncio.run(registry.synchronize_all())   # the *Arr sync task counts (with the key D set)
sonarr.live.CACHE.clear()
sonarr.progress._memory = None      # the Downloading card reads the queue again
asked.clear()
limits.clear()
s = admin.get("/arr/sonarr?days=365").text
check(not any(path.startswith("wanted/") for path in asked), f"the page asks the app for no count ({sorted(set(asked))})")
check(limits and all(read == 3.0 for _path, read in limits),
      f"every live request of the page (the queue, Downloading too) waits at most 3 s ({sorted(set(limits))})")
check(tile_note(s, "Chybí")[0] == "73" and tile_note(s, "Nesplněný cutoff")[0] == "5"
      and "spočítáno" in tile_note(s, "Chybí")[1], f"the counts from the sync, with their time ({tile_note(s, 'Chybí')})")
counted_at = sonarr_counts()["cutoff_at"]
restore()
restore = substitute(sonarr, "SonarrClient", sonarr_client(not_answering))
sonarr.live.CACHE.clear()
s = admin.get("/arr/sonarr?days=365").text
check("Aplikace neodpovídá" in s and tile_note(s, "Chybí")[0] == "73",
      "the app does not answer: the stored counts stay visible with their time")


def slow_cutoff(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/wanted/cutoff"):
        raise httpx.ReadTimeout("Sonarr counts the cutoff for minutes", request=request)
    return recorded_sonarr(request)


restore()
restore = substitute(sonarr, "SonarrClient", sonarr_client(slow_cutoff))
asyncio.run(registry.synchronize_all())
check(sonarr.sync_state()["result"] == "ok", "a count that does not come in time does not spoil the sync")
check(sonarr_counts()["cutoff"] == 5 and sonarr_counts()["cutoff_at"] == counted_at,
      "the cutoff stays the last known one with its time")
restore()
db.set_setting(f"{SETTINGS_PREFIX}_counts", "")
sonarr.live.CACHE.clear()
restore = substitute(sonarr, "SonarrClient", sonarr_client(recorded_sonarr))
s = admin.get("/arr/sonarr?days=365").text
check(tile_note(s, "Chybí") == ("–", "zatím nespočítáno") and "nepodařilo načíst" not in s,
      f"before the first count: “zatím nespočítáno”, no warning about a part that did not load ({tile_note(s, 'Chybí')})")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
