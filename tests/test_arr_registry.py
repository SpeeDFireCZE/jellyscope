# -*- coding: utf-8 -*-
r"""The registry of the *Arr page's apps (jellyscope/arr/registry.py).

The *Arr page, the cards in Settings → *Arr, the task "Synchronizace *Arr" and the menu ask one registry
of apps instead of having Sonarr and Radarr written in. Another app (Bazarr, Prowlarr…) is then only
added to the registry. Guarded:

A) the registry: the apps in the tabs' order, each with everything the web and the task need;
B) an app's functions do the same as its package's functions;
C) the web and the task ask the registry (the sources) and behave as before;
D) several apps connected at once: the order, secret keys, an error of one does not stop the other,
   the task "Synchronizace *Arr", deleting one app's data;
E) the cards of several apps on one dashboard: each its own data, the events selected once per app,
   unavailable and disconnected apps, a dashboard stored before every app had cards of its own;
F) the shared base of the live cards (cards_base.live): only one asks for the same thing at a time.

What concerns one app is guarded by its own tests (tests/test_<app>_*.py). Here is only what
concerns several apps at once, and the apps do not run - the answers are recorded.

Run:
    .venv/bin/python tests/test_arr_registry.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import html as html_lib
import inspect
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "registry.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    UNFAKED_REQUESTS, bazarr_client, bazarr_handler, no_network, tile, card_html, not_responding, login, connect_bazarr,
    connect_prowlarr, connect_radarr, connect_sonarr, prowlarr_client, prowlarr_handler, radarr_client, radarr_handler,
    sonarr_client, sonarr_handler, connect_lidarr, lidarr_client, lidarr_handler, connect_readarr, readarr_client, readarr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import (  # noqa: E402
    accounts, db, scanner, seerr, cards, cards_lidarr, cards_radarr, cards_readarr, cards_sonarr, cards_base, services, stats, tasks,
)
from jellyscope.arr import bazarr, lidarr, prowlarr, radarr, readarr, registry, sonarr  # noqa: E402
from jellyscope.arr.bazarr import sync as bazarr_sync  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # what the test does not fake its own way fails on the first request - it never goes to the network


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def text(path: str) -> str:
    return (PROJECT / "jellyscope" / path).read_text(encoding="utf-8")


print("--- A) the registry ---")
# Sonarr and Radarr are first in the registry, Lidarr (music) goes after them; further apps go after it.
check(list(registry.APPS) == ["sonarr", "radarr", "lidarr", "readarr", "bazarr", "prowlarr"] and registry.APPS["sonarr"].name == "Sonarr"
      and registry.APPS["radarr"].key == "radarr" and registry.APPS["lidarr"].name == "Lidarr",
      f"Sonarr, Radarr, Lidarr, Readarr, Bazarr and Prowlarr in the tabs' order ({list(registry.APPS)})")
check({a.template for a in registry.APPS.values()} == {f"arr_{key}.html" for key in registry.APPS}
      and {a.already_running for a in registry.APPS.values()} == {registry.ALREADY_RUNNING},
      "every app has its own page template (arr_<app>.html); all report \"already running\" the same way")
check([key for key, a in registry.APPS.items() if a.delete_note] == ["prowlarr"],
      "only Prowlarr has an extra note in the deletion dialog (it keeps its history for a limited time)")
fields = {f.name for f in dataclasses.fields(registry.App)}
check(fields >= {"key", "name", "stored_settings", "save_settings", "check_connection", "synchronize", "sync_running",
                 "sync_state", "event_count", "delete_downloaded_data", "cards", "sections", "template", "period",
                 "key_hint"}, f"an app carries everything the web and the task need ({sorted(fields)})")
check(all(a.template and a.sections and a.key_hint for a in registry.APPS.values()), "each has a page template, sections and a hint for the key")

# What the apps share is written once - in services.py. Three copied versions would drift apart in time.
check(not any(hasattr(package, "synchronize_all") for package in (sonarr, radarr, lidarr, readarr, bazarr, prowlarr))
      and not any("def synchronize_all" in text(f"arr/{app}/sync.py") for app in registry.APPS),
      "a single function syncs every app (registry.synchronize_all) - a copy in one app's package would leave the others out")
check(registry.ALREADY_RUNNING is services.SYNC_ALREADY_RUNNING is sonarr.sync.ALREADY_RUNNING is radarr.sync.ALREADY_RUNNING
      is lidarr.sync.ALREADY_RUNNING is readarr.sync.ALREADY_RUNNING is bazarr.sync.ALREADY_RUNNING is seerr.sync.ALREADY_RUNNING is prowlarr.sync.ALREADY_RUNNING,
      "\"the sync is already running\" is one shared sentence, not eight copies")
all_sources = "".join(f.read_text(encoding="utf-8") for f in (PROJECT / "jellyscope").rglob("*.py"))
check(all_sources.count('"{name} sync is already running."') == 1 and all_sources.count('"The app cannot be enabled without an address."') == 1
      and all_sources.count('"The app cannot be enabled without an API key."') == 1,
      "the sentence about a running sync and the rules for switching on (address, key) are in the sources once")
for package in (sonarr, radarr, lidarr, readarr, bazarr, prowlarr, seerr):
    check("save_service_settings(" in inspect.getsource(package.settings), f"the package {package.__name__.rsplit('.', 1)[-1]}'s settings store through the shared function")
check(services.is_connected({"enabled": True, "url": "http://x", "has_key": True})
      and not services.is_connected({"enabled": True, "url": "", "has_key": True})
      and not services.is_connected({"enabled": False, "url": "http://x", "has_key": True})
      and not services.is_connected({"enabled": True, "url": "http://x", "has_key": False}), "connected = switched on, with an address and a key")

print()
print("--- B) an app's functions = its package's functions ---")
# A dict, so an app's handler can be swapped at run time (a refused key, an app that does not answer).
HANDLERS = {"sonarr": sonarr_handler(), "radarr": radarr_handler()}


def fake_sonarr() -> None:
    substitute(sonarr, "SonarrClient", sonarr_client(lambda request: HANDLERS["sonarr"](request)))


def fake_radarr() -> None:
    substitute(radarr, "RadarrClient", radarr_client(lambda request: HANDLERS["radarr"](request)))


fake_sonarr()
fake_radarr()
check(registry.connected() == [], "nothing connected")
connect_radarr()
radarr_app = registry.APPS["radarr"]
check(registry.connected() == ["radarr"], "the connected apps in the registry's order")
check(radarr_app.stored_settings() == radarr.stored_settings() and radarr_app.stored_settings()["has_key"], "the app's settings")
check([c.key for c in registry.APPS["sonarr"].cards()] == [c.key for c in cards_sonarr.CARDS]
      and registry.APPS["sonarr"].sections == cards_sonarr.SECTIONS, "the app's cards and sections")
check(radarr_app.event_count() == 0 and radarr_app.sync_running() is False and radarr_app.sync_state() == radarr.sync_state(), "the count of records, running and the state")
results = asyncio.run(registry.synchronize_all())
check(set(results) == {"radarr"} and results["radarr"].get("new_count", 0) > 0 and radarr_app.event_count() == radarr.event_count() > 0,
      f"syncing every connected one ({results})")
check(radarr_app.save_settings(True, "http://radarr.test", "") == "" and db.get_setting("arr_radarr_api_key") == "tajny-klic",
      "saving through the registry: an empty key keeps the stored one")
ok, version = asyncio.run(radarr_app.check_connection("http://radarr.test", "tajny-klic"))
check(ok and version, f"the connection test through the registry ({version})")

# Only Radarr is connected: Sonarr is not synced, asks nobody and offers nothing.
UNFAKED_REQUESTS.clear()
sonarr_requests: list[str] = []
HANDLERS["sonarr"] = sonarr_handler(sonarr_requests)
check(asyncio.run(sonarr.title_poster("121361")) is None and sonarr_requests == [],
      "a Sonarr that is not connected gives no poster for Seerr and asks nothing")
check(registry.APPS["sonarr"].downloading is not None and registry.APPS["radarr"].provisional_poster is not None
      and registry.APPS["lidarr"].downloading is not None and registry.APPS["lidarr"].provisional_poster is not None
      and registry.APPS["lidarr"].downloading_template == "_lidarr_downloading_body.html"
      and registry.APPS["bazarr"].downloading is None and registry.APPS["prowlarr"].provisional_poster is None
      and registry.APPS["bazarr"].downloading_template == ""
      and registry.APPS["sonarr"].downloading_template == "_sonarr_downloading_body.html"
      and registry.APPS["radarr"].downloading_template == "_radarr_downloading_body.html",
      "only the apps that download have the slice \"Stahuje se\" and the provisional poster in the registry")

# A test that fakes only one app does not talk to the other over the network: the first request is the test's error.
no_network()
fake_sonarr()
try:
    asyncio.run(radarr.synchronize())
    unfaked = "synchronizace prošla"
except AssertionError as error:
    unfaked = str(error)
check("did not fake the client of radarr" in unfaked and UNFAKED_REQUESTS and UNFAKED_REQUESTS[0].startswith("radarr GET"),
      f"the client of an app the test did not fake fails on the first request ({unfaked[:70]})")
check(asyncio.run(sonarr.SonarrClient("http://sonarr.test", "k").status()).get("appName") == "Sonarr",
      "and the faked app still answers with the recorded answer")
UNFAKED_REQUESTS.clear()
fake_radarr()
radarr.delete_data()
results = asyncio.run(registry.synchronize_all())

check(asyncio.run(radarr_app.delete_downloaded_data()) > 0 and radarr_app.event_count() == 0, "deleting the downloaded data through the registry")
# Bazarr has its own package, but through the registry it is handled the same way.
substitute(bazarr, "BazarrClient", bazarr_client(bazarr_handler()))
connect_bazarr()
bazarr_app = registry.APPS["bazarr"]
check(registry.connected() == ["radarr", "bazarr"], "a connected Bazarr is in the registry after Radarr")
results = asyncio.run(registry.synchronize_all())
check(set(results) == {"radarr", "bazarr"} and results["bazarr"]["new_count"] == bazarr_app.event_count() == bazarr.event_count() > 0
      and bazarr_app.sync_state()["result"] == "ok", f"syncing every app fetches Bazarr's history too ({results['bazarr']})")


async def bazarr_twice() -> dict:
    """The task meets a sync that is already running: that is no error.

    The test holds the mark a running sync holds (bazarr_sync._RUNNING) instead of starting a real one: a real
    one with the stub client finished while the task was still syncing Radarr, and the check depended
    on which of the two was quicker.
    """
    bazarr_sync._RUNNING.add(bazarr_sync.NAME)
    try:
        return await registry.synchronize_all()
    finally:
        bazarr_sync._RUNNING.discard(bazarr_sync.NAME)


check(asyncio.run(bazarr_twice())["bazarr"] == {"running": True}, "Bazarr's running sync is reported to the task as \"already running\", not as an error")
check(asyncio.run(bazarr_app.delete_downloaded_data()) > 0 and bazarr_app.event_count() == 0, "deleting Bazarr's downloaded data through the registry")
bazarr.save_settings(False, "http://bazarr.test", "")
db.forget_settings()
# Prowlarr too: its own package, the same way through the registry.
substitute(prowlarr, "ProwlarrClient", prowlarr_client(prowlarr_handler()))
connect_prowlarr()
prowlarr_app = registry.APPS["prowlarr"]
results = asyncio.run(registry.synchronize_all())
check(registry.connected() == ["radarr", "prowlarr"] and results["prowlarr"]["new_count"] == prowlarr_app.event_count() == prowlarr.event_count() > 0
      and prowlarr_app.sync_state()["result"] == "ok", f"syncing every app counts Prowlarr's history too ({results['prowlarr']})")
check(asyncio.run(registry.synchronize_all())["prowlarr"] == {"new_count": 0, "total": 0} and prowlarr_app.event_count() == results["prowlarr"]["new_count"],
      "and a second run adds nothing a second time")
check(asyncio.run(prowlarr_app.delete_downloaded_data()) > 0 and prowlarr_app.event_count() == 0, "deleting Prowlarr's downloaded data through the registry")
prowlarr.save_settings(False, "http://prowlarr.test", "")
db.forget_settings()

print()
print("--- C) the web and the task ask the registry ---")
for file in ("web/arr.py", "web/settings/arr.py", "web/settings/page.py", "tasks.py"):
    source = text(file)
    check("registry." in source and not re.search(r"\b(sonarr|radarr|bazarr|prowlarr)\b", source),
          f"{file}: asks the registry of apps, has none written in")
accounts.create("admin", "longpassword", is_admin=True)
admin = login("admin")
page = admin.get("/settings?section=arr").text
places = [page.find(f'action="/settings/arr/{key}"') for key in registry.APPS]
check(all(place >= 0 for place in places) and places == sorted(places), f"Settings -> *Arr: a card for every app, in the registry's order ({places})")
check(admin.get("/arr", follow_redirects=False).headers.get("location") == "/arr/radarr"
      and admin.get("/arr/sonarr").status_code == 404 and admin.get("/arr/radarr").status_code == 200,
      "the *Arr page: a signpost to the first connected app, one not connected 404")
check(admin.get("/arr/neznama").status_code == 404 and admin.post("/settings/arr/neznama", data={}, follow_redirects=False).status_code == 404,
      "an unknown app is 404 on the page and in the settings")

print()
print("--- D) several apps connected at once ---")
connect_sonarr()
check(registry.connected() == ["sonarr", "radarr"], f"Sonarr and Radarr connected: in the registry's order ({registry.connected()})")


def tabs(html: str) -> list[str]:
    """The *Arr apps in the submenu of the menu, in order (the page lost its tab bar on 16. 11. 2026)."""
    nav = re.search(r'<nav class="nav">(.*?)</nav>', html, re.S)
    block = re.search(r'<div class="nav-sub">(.*?)</div>', nav.group(1), re.S) if nav else None
    return re.findall(r'href="(/arr/\w+)"', block.group(1)) if block else []


prowlarr.save_settings(True, "http://prowlarr.test", "")
db.forget_settings()
check(tabs(admin.get("/arr/prowlarr").text) == ["/arr/sonarr", "/arr/radarr", "/arr/prowlarr"]
      and tabs(admin.get("/arr/sonarr").text) == ["/arr/sonarr", "/arr/radarr", "/arr/prowlarr"]
      and admin.get("/arr", follow_redirects=False).headers.get("location") == "/arr/sonarr",
      f"the *Arr page's subtabs: the connected apps in the registry's order, the signpost to the first ({tabs(admin.get('/arr/prowlarr').text)})")
prowlarr.save_settings(False, "http://prowlarr.test", "")
db.forget_settings()
check({f"arr_{key}_api_key" for key in registry.APPS} <= set(db.SECRET_SETTINGS),
      "the key of every app of the registry is a secret setting (db.SECRET_SETTINGS)")
everything = asyncio.run(registry.synchronize_all())
counts = {"sonarr": sonarr.event_count(), "radarr": radarr.event_count()}
check(set(everything) == {"sonarr", "radarr"} and everything["sonarr"]["new_count"] == counts["sonarr"] > 0 and counts["radarr"] > 0
      and counts["sonarr"] != counts["radarr"], f"syncing both: each its own history into its own tables ({counts})")

# One app's wrong key does not stop the other (so the task runs it).
mark_before = db.get_setting("arr_sonarr_last_sync", "")
HANDLERS["sonarr"] = lambda request: httpx.Response(401)
everything = asyncio.run(registry.synchronize_all())
HANDLERS["sonarr"] = sonarr_handler()
state = sonarr.sync_state()
check(state["result"] == "error" and "401" in state["error"] and "tajny-klic" not in state["error"],
      f"Sonarr's error is in its state, without the key ({state['error']})")
check(bool(mark_before) and db.get_setting("arr_sonarr_last_sync", "") == mark_before and sonarr.event_count() == counts["sonarr"],
      "the error did not move the sync mark and deleted nothing")
check(radarr.sync_state()["result"] == "ok" and set(everything) == {"sonarr", "radarr"} and everything["sonarr"].get("error")
      and everything["radarr"].get("new_count") == 0, f"Radarr was fetched anyway; the result carries both apps ({ {k: sorted(v) for k, v in everything.items()} })")
asyncio.run(registry.synchronize_all())

task = tasks.TASKS.get("arr")
check(task is not None and task.default_minutes == 60 and task.interval_setting == "arr_sync_minutes"
      and task.log_kind == "arr", "the task arr: the interval arr_sync_minutes, 60 minutes by default")
check(task is not None and tasks.is_enabled(task), "the task is switched on by default")
# A manual and a scheduled sync meet - the task is to say "already running" (nothing happens), not "chyba - " with
# an empty or an old text; and it fetches the other app.
state_before = db.get_setting("arr_sonarr_state", "")
sonarr.sync._RUNNING.add(sonarr.NAME)
result = asyncio.run(tasks.run_now("arr"))
sonarr.sync._RUNNING.discard(sonarr.NAME)
check(result["status"] == "ok" and "Sonarr: synchronizace už běží" in result["message"]
      and "Radarr: 0 nových záznamů" in result["message"], f"concurrency: the task says Sonarr is already running and fetches Radarr ({result})")
check(db.get_setting("arr_sonarr_state", "") == state_before, "concurrency does not overwrite the app's state")
last = scanner.last_scan("arr")
check(last is not None and last.get("status") == "ok", "the task's run is in the tasks' log (the scheduler counts the next one by it)")

# (That a backup is made before a deletion is guarded by each app's sync test.)
answer = admin.post("/settings/arr/radarr/delete", follow_redirects=False)
db.forget_settings()
check(answer.status_code == 303 and radarr.event_count() == 0 and sonarr.event_count() == counts["sonarr"],
      f"deleting Radarr's downloaded data leaves Sonarr's data ({radarr.event_count()}, {sonarr.event_count()})")
titles = {table: (db.query_one(f"SELECT COUNT(*) AS n FROM {table}") or {}).get("n") for table in ("sonarr_title", "radarr_title")}
check(titles["sonarr_title"] > 0 and titles["radarr_title"] == 0, f"the list of titles too: Radarr's was deleted, Sonarr's stayed ({titles})")
asyncio.run(registry.synchronize_all())
check(radarr.event_count() == counts["radarr"], "and the next sync fetches Radarr's whole history again")

print()
print("--- D2) Lidarr (music) next to Sonarr and Radarr ---")
substitute(lidarr, "LidarrClient", lidarr_client(lidarr_handler()))
connect_lidarr()
check(registry.connected() == ["sonarr", "radarr", "lidarr"], f"Lidarr is third in the registry's order ({registry.connected()})")
check([c.key for c in registry.APPS["lidarr"].cards()] == [c.key for c in cards_lidarr.CARDS]
      and registry.APPS["lidarr"].sections == cards_lidarr.SECTIONS and registry.APPS["lidarr"].sample_url == "http://localhost:8686",
      "the app's cards, sections and the sample address")
check(tabs(admin.get("/arr/lidarr").text) == ["/arr/sonarr", "/arr/radarr", "/arr/lidarr"], "the submenu of the *Arr page has Lidarr after Radarr")
settings_page = admin.get("/settings?section=arr").text
check('action="/settings/arr/lidarr"' in settings_page and "tajny-klic" not in settings_page, "Settings has Lidarr's card and no key in it")
everything = asyncio.run(registry.synchronize_all())
check(set(everything) == {"sonarr", "radarr", "lidarr"} and everything["lidarr"]["new_count"] == lidarr.event_count() > 0,
      f"the task syncs Lidarr's history with the others ({ {k: v.get('new_count') for k, v in everything.items()} })")
result = asyncio.run(tasks.run_now("arr"))
check(result["status"] == "ok" and "Lidarr: 0 nových záznamů" in result["message"], f"the task's message names Lidarr ({result})")
lidarr_count = lidarr.event_count()
answer = admin.post("/settings/arr/lidarr/delete", follow_redirects=False)
db.forget_settings()
check(answer.status_code == 303 and lidarr.event_count() == 0 and sonarr.event_count() > 0 and radarr.event_count() > 0,
      "deleting Lidarr's downloaded data leaves the others' data")
asyncio.run(registry.synchronize_all())
check(lidarr.event_count() == lidarr_count, "and the next sync fetches Lidarr's whole history again")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
cards.save_layout(["arr_sonarr_largest", "arr_lidarr_largest", "arr_lidarr_numbers"])
d = admin.get("/dashboard?days=365").text
check("Hvězdy" in card_html(d, "arr_lidarr_largest") and "Hvězdy" not in card_html(d, "arr_sonarr_largest")
      and "Game of Thrones" in card_html(d, "arr_sonarr_largest") and "Game of Thrones" not in card_html(d, "arr_lidarr_largest"),
      "Sonarr's and Lidarr's cards side by side each have their own data")
check("Lidarr · Největší převzatá alba" in d and "Sonarr · Největší převzaté soubory" in d, "the cards' headings carry the app")
lidarr.live.CACHE.clear()
lidarr.save_settings(False, "http://lidarr.test", "")
db.forget_settings()
lidarr.delete_data()
check(registry.connected() == ["sonarr", "radarr"], "Lidarr disconnected again for the rest of the test")

print()
print("--- D3) Readarr (books) after Lidarr ---")
substitute(readarr, "ReadarrClient", readarr_client(readarr_handler()))
connect_lidarr()
connect_readarr()
check(registry.connected() == ["sonarr", "radarr", "lidarr", "readarr"], f"Readarr is fourth in the registry's order, after Lidarr ({registry.connected()})")
check([c.key for c in registry.APPS["readarr"].cards()] == [c.key for c in cards_readarr.CARDS]
      and registry.APPS["readarr"].sections == cards_readarr.SECTIONS and registry.APPS["readarr"].sample_url == "http://localhost:8787"
      and registry.APPS["readarr"].name == "Readarr" and registry.APPS["readarr"].template == "arr_readarr.html"
      and registry.APPS["readarr"].downloading is not None and registry.APPS["readarr"].provisional_poster is not None
      and registry.APPS["readarr"].downloading_template == "_readarr_downloading_body.html"
      and registry.APPS["readarr"].detected_name is not None and registry.APPS["readarr"].detected_name() == "Readarr",
      "the app's cards, sections, the sample address, the live slice and the detected name")
check(tabs(admin.get("/arr/readarr").text) == ["/arr/sonarr", "/arr/radarr", "/arr/lidarr", "/arr/readarr"], "the submenu of the *Arr page has Readarr after Lidarr")
settings_page = admin.get("/settings?section=arr").text
check('action="/settings/arr/readarr"' in settings_page and "tajny-klic" not in settings_page and "http://localhost:8787" in settings_page,
      "Settings has Readarr's card (with the sample address) and no key in it")
everything = asyncio.run(registry.synchronize_all())
check(set(everything) == {"sonarr", "radarr", "lidarr", "readarr"} and everything["readarr"]["new_count"] == readarr.event_count() > 0,
      f"the task syncs Readarr's history with the others ({ {k: v.get('new_count') for k, v in everything.items()} })")
result = asyncio.run(tasks.run_now("arr"))
check(result["status"] == "ok" and "Readarr: 0 nových záznamů" in result["message"], f"the task's message names Readarr ({result})")
readarr_count = readarr.event_count()
answer = admin.post("/settings/arr/readarr/delete", follow_redirects=False)
db.forget_settings()
check(answer.status_code == 303 and readarr.event_count() == 0 and sonarr.event_count() > 0 and lidarr.event_count() > 0,
      "deleting Readarr's downloaded data leaves the others' data")
asyncio.run(registry.synchronize_all())
check(readarr.event_count() == readarr_count, "and the next sync fetches Readarr's whole history again")
cards.save_layout(["arr_sonarr_largest", "arr_readarr_largest", "arr_readarr_numbers"])
d = admin.get("/dashboard?days=365").text
check("Válka s mloky" in card_html(d, "arr_readarr_largest") and "Válka s mloky" not in card_html(d, "arr_sonarr_largest")
      and "Game of Thrones" in card_html(d, "arr_sonarr_largest") and "Game of Thrones" not in card_html(d, "arr_readarr_largest"),
      "Sonarr's and Readarr's cards side by side each have their own data")
check("Readarr · Největší převzaté knihy" in d and "Sonarr · Největší převzaté soubory" in d, "the cards' headings carry the app")
for package in (readarr, lidarr):
    package.live.CACHE.clear()
    package.save_settings(False, f"http://{package.NAME.lower()}.test", "")
    db.forget_settings()
    package.delete_data()
check(registry.connected() == ["sonarr", "radarr"], "Readarr and Lidarr disconnected again for the rest of the test")

print()
print("--- E) the cards of several apps on one dashboard ---")
accounts.create("reader", "longpassword")
admin_account = accounts.get_by_name("admin")
reader = login("reader")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
BOTH_CARDS = (*cards_sonarr.CARDS, *cards_radarr.CARDS)
db_queries: list[str] = []
original_query_all = db.query_all


def _listen(sql, *a, **k):
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


def event_selections(action) -> dict[str, int]:
    """How many times `action` selected the period's events from which app's table (the `_analysis` query of its statistics)."""
    db_queries.clear()
    db.query_all = _listen
    try:
        action()
    finally:
        db.query_all = original_query_all
    return {table: sum(f"FROM {table}" in q and "ORDER BY happened_at, arr_id" in q for q in db_queries)
            for table in ("sonarr_event", "radarr_event")}


def forget_live_state() -> None:
    sonarr.live.CACHE.clear()
    radarr.live.CACHE.clear()


cards.save_layout(["arr_sonarr_largest", "arr_radarr_largest", "arr_sonarr_numbers", "arr_radarr_numbers"])
d = admin.get("/dashboard?days=365").text
check("Sintel" in card_html(d, "arr_radarr_largest") and "Sintel" not in card_html(d, "arr_sonarr_largest")
      and "Game of Thrones" in card_html(d, "arr_sonarr_largest")
      and "Game of Thrones" not in card_html(d, "arr_radarr_largest"), "Sonarr and Radarr side by side each have their own data")
total = {"sonarr": sum(r["count"] for r in stats.sonarr_additions(365)), "radarr": sum(r["count"] for r in stats.radarr_additions(365))}
check(total["sonarr"] != total["radarr"]
      and tile(card_html(d, "arr_sonarr_numbers"), "Převzato do knihovny") == str(total["sonarr"])
      and tile(card_html(d, "arr_radarr_numbers"), "Převzato do knihovny") == str(total["radarr"]),
      f"the tiles of a card with the same name too ({total})")
check("Sonarr · Největší převzaté soubory" in d and "Radarr · Největší převzaté soubory" in d, "the cards' headings carry the app")
how_many = event_selections(lambda: admin.get("/dashboard?days=365"))
check(how_many == {"sonarr_event": 1, "radarr_event": 1}, f"a dashboard with both apps' cards selects the events once per app ({how_many})")
cards.save_layout([c.key for c in BOTH_CARDS])
all_cards = admin.get("/dashboard?days=365")
check(all_cards.status_code == 200 and len(re.findall(r"<!-- card: arr_\w+ -->", all_cards.text)) == 46, "all 46 cards at once on one dashboard")
cards.save_layout(["arr_sonarr_downloading", "arr_radarr_downloading"])
downloading = html_lib.unescape(admin.get("/dashboard").text)
check("Sonarr · Stahuje se" in downloading and "Radarr · Stahuje se" in downloading and downloading.count('data-partial="/partials/arr/') == 2
      and "The Expanse" in card_html(downloading, "arr_sonarr_downloading") and "Inception" in card_html(downloading, "arr_radarr_downloading")
      and "Inception" not in card_html(downloading, "arr_sonarr_downloading"),
      "both cards \"Stahuje se\" side by side: each with its own slice and its own queue")
with db.connect() as conn:
    conn.execute("INSERT INTO sonarr_event (arr_id, event_type, happened_at, title, episode, item_id, quality, size_bytes)"
                 " VALUES (900001, 'downloadFolderImported', '2025-01-15 10:00:00', 'Stary serial', 'S01E01', 900001, 'WEBDL-720p', 1000)")
    conn.execute("INSERT INTO radarr_event (arr_id, event_type, happened_at, title, item_id, quality, size_bytes)"
                 " VALUES (900001, 'downloadFolderImported', '2024-06-01 10:00:00', 'Stary film', 900001, 'WEBDL-720p', 1000)")
    conn.commit()
cards.save_layout(["arr_sonarr_largest", "arr_radarr_largest"])
whole = admin.get("/dashboard?days=0").text
check("Stary serial" in card_html(whole, "arr_sonarr_largest") and "Stary film" in card_html(whole, "arr_radarr_largest")
      and "Stary film" not in card_html(whole, "arr_sonarr_largest"), "the whole history on the dashboard: every app from its own first event")
admin.get("/dashboard?days=365")   # the remembered period back

# Both apps unavailable, the dashboard has their live cards and the cards from the database.
attempts: dict[str, list[str]] = {"sonarr": [], "radarr": []}
HANDLERS["sonarr"], HANDLERS["radarr"] = not_responding(attempts["sonarr"]), not_responding(attempts["radarr"])
forget_live_state()
live_names = ("live_numbers", "queue", "calendar", "health")
cards.save_layout([app_cards.card_key(n) for app_cards in (cards_sonarr, cards_radarr) for n in (*live_names, "largest")])
r = admin.get("/dashboard?days=365")
check(r.status_code == 200 and all("Aplikace neodpovídá" in card_html(r.text, app_cards.card_key(n))
                                    for app_cards in (cards_sonarr, cards_radarr) for n in live_names),
      "both apps unavailable: the dashboard draws and every live card says so")
check("Game of Thrones" in card_html(r.text, "arr_sonarr_largest") and "Sintel" in card_html(r.text, "arr_radarr_largest"),
      "the cards from the database go on")
check(attempts["sonarr"].count("queue") == 1 and attempts["radarr"].count("queue") == 1,
      f"an app's four live cards = one request for the live state ({attempts['sonarr'].count('queue')}, {attempts['radarr'].count('queue')})")
check("tajny-klic" not in r.text, "the key nowhere")
# One answers, the other does not: the first one's cards are fine.
HANDLERS["sonarr"] = sonarr_handler()
forget_live_state()
r = admin.get("/dashboard?days=365")
check("Aplikace neodpovídá" not in card_html(r.text, "arr_sonarr_live_numbers") and "Aplikace neodpovídá" in card_html(r.text, "arr_radarr_live_numbers")
      and tile(card_html(r.text, "arr_sonarr_live_numbers"), "Ve frontě") not in (None, "–"),
      "only Radarr unavailable: Sonarr's live cards have their numbers")
HANDLERS["radarr"] = radarr_handler()
forget_live_state()

# The admin disconnects one app: the dashboard leaves its card out for everyone, the other is not concerned.
radarr.save_settings(False, "http://radarr.test", "")
db.forget_settings()
cards.save_layout(["arr_sonarr_numbers", "arr_radarr_numbers"])
how_many = event_selections(lambda: admin.get("/dashboard?days=365"))
after = reader.get("/dashboard?days=365").text
check("<!-- card: arr_radarr_numbers -->" not in after and "<!-- card: arr_sonarr_numbers -->" in after
      and how_many == {"sonarr_event": 1, "radarr_event": 0}, f"a disconnected app: the dashboard leaves its card out and does not compute it ({how_many})")
check([c.key for c in cards.load_layout()] == ["arr_sonarr_numbers", "arr_radarr_numbers"] and
      admin.get("/arr/radarr").status_code == 404 and admin.get("/arr/sonarr").status_code == 200,
      "the layout stays; only a connected app has a tab")
radarr.save_settings(True, "http://radarr.test", "")
db.forget_settings()
forget_live_state()

# A dashboard stored before every app had cards of its own: the keys in the database are the same,
# the layout still draws and with the same numbers as the app's tab.
STORED = ("arr_sonarr_numbers", "arr_radarr_queue", "arr_radarr_largest", "arr_sonarr_largest")
with db.connect() as conn:
    conn.execute("DELETE FROM dashboard_layout WHERE account_id IS NULL")
    for order, card_key in enumerate(STORED):
        conn.execute("INSERT INTO dashboard_layout (account_id, card_key, position, width) VALUES (NULL, ?, ?, '')", (card_key, order))
    conn.commit()
dashboard = admin.get("/dashboard?days=365").text
app_tabs = {key: admin.get(f"/arr/{key}?days=365").text for key in ("sonarr", "radarr")}


def card_titles(html: str, card_key: str) -> list[str]:
    return re.findall(r'<td class="strong">(?:<a [^>]*>)?([^<]+)', card_html(html, card_key))


check(re.findall(r"<!-- card: (\w+) -->", dashboard) == list(STORED), "a layout stored before the split draws whole and in its order")
check(all(tile(card_html(dashboard, "arr_sonarr_numbers"), label) == tile(card_html(app_tabs["sonarr"], "arr_sonarr_numbers"), label) is not None
          for label in ("Převzato do knihovny", "Upgrady", "Selhaná stažení v období")), "Sonarr's numbers on the dashboard = the numbers on its tab")
check(all(card_titles(dashboard, k) == card_titles(app_tabs[k.split("_")[1]], k) and card_titles(dashboard, k)
          for k in ("arr_radarr_largest", "arr_sonarr_largest")), "the largest files on the dashboard = the same rows as on the tab, each app its own")
check("Sonarr · " in card_html(dashboard, "arr_sonarr_largest") and "Radarr · " in card_html(dashboard, "arr_radarr_queue"),
      "a card on the dashboard carries its app's name")
# Only Radarr connected: the *Arr page leads to it, Sonarr has no tab, no slice and no offered cards.
sonarr.save_settings(False, "http://sonarr.test", "")
db.forget_settings()
r = admin.get("/arr", follow_redirects=False)
check(r.status_code == 303 and r.headers.get("location") == "/arr/radarr" and admin.get("/arr/radarr").status_code == 200,
      f"only Radarr: /arr leads to /arr/radarr ({r.headers.get('location')})")
check(admin.get("/arr/sonarr").status_code == 404 and admin.get("/partials/arr/sonarr/downloading").status_code == 404
      and admin.get("/partials/arr/radarr/downloading").status_code == 200, "Sonarr has neither a tab nor a slice, Radarr has both")
offered = {c.key for c in cards.allowed(list(cards.ALL_CARDS), admin_account)}
check(not any(k.startswith("arr_sonarr_") for k in offered) and {c.key for c in cards_radarr.CARDS} <= offered,
      "Sonarr's cards are not offered, all of Radarr's are")
only_radarr = admin.get("/dashboard?days=365").text
check(re.findall(r"<!-- card: (\w+) -->", only_radarr) == ["arr_radarr_queue", "arr_radarr_largest"],
      "the dashboard draws only the connected app's cards")
# Nothing connected: the task says so and ends.
radarr.save_settings(False, "http://radarr.test", "")
db.forget_settings()
result = asyncio.run(tasks.run_now("arr"))
check(result == {"status": "ok", "message": "No *Arr app is connected."}, f"the task without apps - the key, the log reads it in any language ({result})")
check(UNFAKED_REQUESTS == [], f"no request went to an app the test did not fake ({UNFAKED_REQUESTS[:2]})")

# A request to an app the test did not fake fails with AssertionError - but syncing every app swallows one app's
# error on purpose (so it does not stop the others) and the test would pass silently. So `no_network()` checks it
# itself at the end of the test: even when the test reached "DONE - failures: 0", it ends with an error.
import subprocess  # noqa: E402

FORGETFUL_TEST = """
import asyncio, atexit, os, sys, tempfile
sys.path.insert(0, 'tests')
atexit.register(lambda: print('uklizeno'))       # a clean-up registered before the check - it must run anyway
tmp = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=tmp, DATABASE_PATH=os.path.join(tmp, 'x.db'), SECRET_KEY='k',
                  JELLYFIN_URL='http://127.0.0.1:1', JELLYFIN_API_KEY='t')
from _apps import no_network, connect_sonarr
from jellyscope import db
from jellyscope.arr import registry
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()
connect_sonarr()                                 # connected, but nobody faked it
results = asyncio.run(registry.synchronize_all())      # it swallows the error into {"chyba": ...}
print('vysledek:', sorted(results['sonarr']))
print('DONE - failures: 0')
sys.exit(0)
"""
run = subprocess.run([sys.executable, "-c", FORGETFUL_TEST], cwd=PROJECT, capture_output=True, text=True, timeout=180)
check(run.returncode == 1 and "DONE - failures: 0" in run.stdout and "did not fake" in run.stdout.split("DONE")[-1]
      and run.stdout.rstrip().endswith("uklizeno") and "did not fake" in run.stderr,
      f"a test that asked an app it did not fake ends with an error, even though the code swallowed it on the way (code {run.returncode}, {run.stdout.strip().splitlines()[-1][:90] if run.stdout.strip() else run.stderr.strip()[-90:]})")

print()
print("--- F) the live state: only one asks for the same thing at a time ---")
# A review: an app that accepts the connection and does not answer held every page view up to the timeout and each
# started its own requests. Now only one asks for the same thing at a time; the others wait and take the result from
# the function's cache.
slow = {"running": 0, "most": 0, "volani": 0, "pamet": {}}


async def slow_state(app: str) -> dict:
    """Like an app's live_state: its own cache and a load that takes a while."""
    if app in slow["pamet"]:
        return slow["pamet"][app]
    slow["volani"] += 1
    slow["running"] += 1
    slow["most"] = max(slow["most"], slow["running"])
    await asyncio.sleep(0.3)
    slow["running"] -= 1
    slow["pamet"][app] = {"app": app}
    return slow["pamet"][app]


def concurrently(*apps: str) -> list[dict]:
    results: list[dict] = []
    threads = [threading.Thread(target=lambda a=a: results.append(cards_base.live(slow_state, a))) for a in apps]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    return results


live_results = concurrently("sonarr", "sonarr", "sonarr", "sonarr")
check(slow["volani"] == 1 and live_results == [{"app": "sonarr"}] * 4,
      f"four concurrent views: the app is asked once, the others take its result ({slow['volani']} loads)")
slow.update(volani=0, most=0, pamet={})
concurrently("sonarr", "radarr")
check(slow["volani"] == 2 and slow["most"] == 2, "two different apps do not wait for each other (both run at once)")


async def fails(app: str) -> dict:
    raise RuntimeError("chyba v programu")


def try_to_fail() -> None:
    try:
        cards_base.live(fails, "sonarr")
    except RuntimeError:
        pass


try_to_fail()
second = threading.Thread(target=try_to_fail)
second.start()
second.join(5)
check(not second.is_alive(), "an exception in the load does not leave the request locked for the next one")

# Code review (14. 11. 2026): the lock was waited for without a limit. A request that got stuck completely would lock the
# card for every further view; now it waits at most MAX_WAIT and then the view asks by itself.
stuck = {"volani": 0}


async def got_stuck(app: str) -> dict:
    stuck["volani"] += 1
    if stuck["volani"] == 1:
        await asyncio.sleep(1.5)            # the first request hangs
    return {"volani": stuck["volani"]}


original_wait = cards_base.MAX_WAIT
cards_base.MAX_WAIT = 0.2
first = threading.Thread(target=lambda: cards_base.live(got_stuck, "sonarr"))
first.start()
time.sleep(0.1)
start = time.monotonic()
second_result = cards_base.live(got_stuck, "sonarr")
took = time.monotonic() - start
first.join(5)
cards_base.MAX_WAIT = original_wait
check(second_result == {"volani": 2} and took < 1.0,
      f"a stuck request does not lock the card for the others: after the limit the view asks by itself ({took:.2f} s)")
check(isinstance(cards_base.MAX_WAIT, float) and cards_base.MAX_WAIT > 1,
      f"the substitution of MAX_WAIT is undone: later live() calls wait the real limit ({cards_base.MAX_WAIT!r})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
