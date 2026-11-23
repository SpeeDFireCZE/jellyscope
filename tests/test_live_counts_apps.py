# -*- coding: utf-8 -*-
r"""The pages of Sonarr, Radarr, Lidarr and Readarr do not wait for slow counts (the user, 23. 11. 2026).

The page of an *Arr app waited after every minute for `wanted/missing` and `wanted/cutoff` - a big Sonarr did not
count the cutoff within 20 s, so the page took 21 s. For every one of the four apps (Readarr stands for Chaptarr and
Bookshelf too) the test watches:

A) the sync counts missing and cutoff with the long limit and stores them;
B) the page asks the app for no count, every live request of it (the queue, the calendar, health, the queue of
   "Downloading") waits at most 3 s, the tiles show the stored counts with their time;
C) a cutoff that does not come in time even in the sync: the sync ends "ok", the last count and its time stay;
D) the app does not answer: the sentence, and the stored counts stay visible;
E) before the first count: "zatím nespočítáno", no warning about a part that did not load.

Spec: docs/superpowers/specs/2026-11-23-arr-live-counts-design.md

Run:
    .venv/bin/python tests/test_live_counts_apps.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "live-counts.db")
os.environ["SECRET_KEY"] = "test-key-live-counts"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _apps  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, services, web  # noqa: E402
from jellyscope.arr import lidarr, radarr, readarr, registry, sonarr  # noqa: E402

_apps.no_network()
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")       # the checks read Czech


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def tile_note(html: str, label: str) -> tuple[str, str]:
    """(the value, the note) of the tile with the label."""
    found = re.search(rf'{re.escape(label)}</p>\s*<div class="tile-value">([^<]*)</div>\s*'
                      rf'(?:<div class="delta flat">([^<]*)</div>)?', html)
    return (found.group(1).strip(), (found.group(2) or "").strip()) if found else ("", "")


accounts.create("admin", "longpassword", is_admin=True)
admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})

APPS: list[tuple[str, Any, str, Callable[..., Any], Callable[..., Any], Path]] = [
    ("sonarr", sonarr, "SonarrClient", _apps.sonarr_handler, _apps.sonarr_client, _apps.SONARR_RESPONSES),
    ("radarr", radarr, "RadarrClient", _apps.radarr_handler, _apps.radarr_client, _apps.RADARR_RESPONSES),
    ("lidarr", lidarr, "LidarrClient", _apps.lidarr_handler, _apps.lidarr_client, _apps.LIDARR_RESPONSES),
    ("readarr", readarr, "ReadarrClient", _apps.readarr_handler, _apps.readarr_client, _apps.READARR_RESPONSES),
]

for app, module, client_name, handler_of, client_of, responses in APPS:
    print()
    print(f"=== {app} ===")
    prefix = module.client.SETTINGS_PREFIX
    expected = {name: int(_apps.recorded_response(responses, f"wanted_{name}")["totalRecords"])
                for name in ("missing", "cutoff")}
    asked: list[str] = []
    limits: list[tuple[str, Any]] = []
    recorded = handler_of(asked)

    def with_limits(request: httpx.Request, recorded: Any = recorded, limits: list[Any] = limits) -> httpx.Response:
        limits.append((request.url.path.rsplit("/api/", 1)[-1].split("/", 1)[-1],
                       request.extensions.get("timeout", {}).get("read")))
        return recorded(request)

    def slow_cutoff(request: httpx.Request, recorded: Any = recorded) -> httpx.Response:
        if request.url.path.endswith("/wanted/cutoff"):
            raise httpx.ReadTimeout("the app counts the cutoff for minutes", request=request)
        return recorded(request)

    def stored() -> dict[str, Any]:
        return services.stored_counts(prefix, services.settings_fingerprint(f"http://{app}.test", "tajny-klic"))

    restore = substitute(module, client_name, client_of(with_limits))
    getattr(_apps, f"connect_{app}")()

    print("--- A) the *Arr sync task counts, the Settings button does not ---")
    asyncio.run(module.synchronize())       # what "Sync now" in Settings runs and waits for
    check(not [path for path, _read in limits if path.startswith("wanted/")],
          f"{app}: the Settings button's sync asks for no count - it stays quick")
    limits.clear()
    asyncio.run(registry.synchronize_all())  # the *Arr sync task (in the background)
    counted = [(path, read) for path, read in limits if path.startswith("wanted/")]
    check(sorted(counted) == [("wanted/cutoff", 300.0), ("wanted/missing", 300.0)],
          f"{app}: the sync asks both counts with the long limit ({counted})")
    check(stored()["missing"] == expected["missing"] and stored()["cutoff"] == expected["cutoff"],
          f"{app}: stored ({stored()}, recorded {expected})")

    print("--- B) the page ---")
    module.live.CACHE.clear()
    module.progress._memory = None          # the Downloading card reads the queue again
    asked.clear()
    limits.clear()
    page = admin.get(f"/arr/{app}?days=365").text
    check(not any(path.startswith("wanted/") for path in asked), f"{app}: the page asks for no count ({sorted(set(asked))})")
    check(limits and all(read == 3.0 for _path, read in limits),
          f"{app}: every live request of the page waits at most 3 s ({sorted(set(limits))})")
    missing = tile_note(page, "Chybí")
    check(missing[0] == f"{expected['missing']}" and "spočítáno" in missing[1]
          and tile_note(page, "Nesplněný cutoff")[0] == f"{expected['cutoff']}",
          f"{app}: the tiles show the stored counts with their time ({missing})")

    print("--- C) a cutoff that does not come in time ---")
    counted_at = stored()["cutoff_at"]
    restore()
    restore = substitute(module, client_name, client_of(slow_cutoff))
    asyncio.run(registry.synchronize_all())
    check(module.sync_state()["result"] == "ok", f"{app}: the sync ends ok")
    check(stored()["cutoff"] == expected["cutoff"] and stored()["cutoff_at"] == counted_at,
          f"{app}: the last cutoff and its time stay")

    print("--- D) the app does not answer ---")
    restore()
    restore = substitute(module, client_name, client_of(_apps.not_responding()))
    module.live.CACHE.clear()
    module.progress._memory = None
    page = admin.get(f"/arr/{app}?days=365").text
    check("Aplikace neodpovídá" in page and tile_note(page, "Chybí")[0] == f"{expected['missing']}",
          f"{app}: the sentence, and the stored counts stay visible")

    print("--- E) before the first count ---")
    restore()
    restore = substitute(module, client_name, client_of(recorded))
    db.set_setting(f"{prefix}_counts", "")
    module.live.CACHE.clear()
    page = admin.get(f"/arr/{app}?days=365").text
    check(tile_note(page, "Chybí") == ("–", "zatím nespočítáno") and "nepodařilo načíst" not in page,
          f"{app}: “zatím nespočítáno”, no warning about a part that did not load ({tile_note(page, 'Chybí')})")
    restore()
    module.save_settings(False, f"http://{app}.test", "tajny-klic")



print()
print("=== an app that accepts and does not answer (the final review, 23. 11. 2026) ===")
# The page loaded the live state and the Downloading card one after the other, and the card waited for the refresh
# command before reading the queue: with a hung app 6-9 s, not "at most 3 s". This transport answers like such an
# app - nothing until the request's own read limit runs out.
import time  # noqa: E402


class Hanging(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(float(request.extensions.get("timeout", {}).get("read") or 30))
        raise httpx.ReadTimeout("the app accepted and says nothing", request=request)


def hanging(client_class: Any) -> Any:
    real = getattr(client_class, "_real", client_class)

    class Client(real):  # type: ignore[misc, valid-type]
        _real = real

        def __init__(self, url: str, key: str, transport: Any = None, timeout: Any = None) -> None:
            super().__init__(url, key, transport=Hanging(), timeout=timeout)
    return Client


for app, module, client_name, _handler_of, _client_of, _responses in APPS:
    getattr(_apps, f"connect_{app}")()
    restore = substitute(module, client_name, hanging(getattr(module, client_name)))
    module.live.CACHE.clear()
    module.progress._memory = None
    module.progress._last = (module.progress._now(), True)        # a download ran: the refresh command is due
    started = time.monotonic()
    page = admin.get(f"/arr/{app}?days=365").text
    took = time.monotonic() - started
    check("Aplikace neodpovídá" in page and took < 3.8, f"{app}: the page of a hung app in at most ~3 s ({took:.1f} s)")
    module.progress._memory = None
    module.progress._last = (module.progress._now(), True)
    started = time.monotonic()
    admin.get(f"/partials/arr/{app}/downloading")
    took = time.monotonic() - started
    check(took < 3.8, f"{app}: the Downloading card's refresh in at most ~3 s, the command beside the queue ({took:.1f} s)")
    restore()
    module.save_settings(False, f"http://{app}.test", "tajny-klic")


print()
print("=== one slow answer during downloads does not stop Downloading for a minute (the final review) ===")
# With 3 s a slow queue answer is an error; cached for a minute with the card's calm rhythm, "Stahuje se" would stand
# still in the middle of a download. The first error after a read that saw a download is retried at the fast rhythm;
# a second error in a row is calm again (an app that is down is not asked every 5 s).
for app, module, client_name, _handler_of, client_of, _responses in APPS:
    getattr(_apps, f"connect_{app}")()
    progress = module.progress
    real_now = progress._now
    clock = {"now": 1000.0}
    progress._now = lambda clock=clock: clock["now"]
    asked = []

    def failing(request: httpx.Request, asked: list[str] = asked) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(500)

    restore = substitute(module, client_name, client_of(failing))
    progress._memory = None
    progress._last = (clock["now"], True)          # the last read saw a download
    first = asyncio.run(module.download_state())
    check(first["error"] and first["refresh"] == progress.FAST_REFRESH,
          f"{app}: the first error while downloading - the card asks again at the fast rhythm ({first['refresh']})")
    clock["now"] += progress.FAST_REFRESH + 0.5
    before = len(asked)
    second = asyncio.run(module.download_state())
    check(len(asked) > before, f"{app}: ... and that refresh really asks the app again, not the cached error")
    check(second["error"] and second["refresh"] == progress.CALM_REFRESH,
          f"{app}: a second error in a row - calm again ({second['refresh']})")
    restore()
    progress._now = real_now
    progress._memory = None
    module.save_settings(False, f"http://{app}.test", "tajny-klic")


print()
print("=== bazarr ===")
# Bazarr's expensive part were the two lists of episodes and movies without subtitles (thousands of items) that
# the card "Chybějící titulky podle jazyka" counts languages from: the sync reads them now. The counts in Bazarr's
# header, the providers and the warnings stay live (one cheap request each), with the short limit.
from jellyscope.arr import bazarr, prowlarr  # noqa: E402

asked = []
limits = []
bazarr_recorded = _apps.bazarr_handler(asked)


def bazarr_limits(request: httpx.Request) -> httpx.Response:
    limits.append((request.url.path.split("/api/", 1)[-1], request.extensions.get("timeout", {}).get("read")))
    return bazarr_recorded(request)


def bazarr_languages() -> dict[str, Any]:
    return bazarr.live.stored_languages(services.settings_fingerprint("http://bazarr.test", "tajny-klic"))


restore = substitute(bazarr, "BazarrClient", _apps.bazarr_client(bazarr_limits))
_apps.connect_bazarr()
asyncio.run(bazarr.synchronize())
check(not [path for path, _read in limits if path.endswith("/wanted")],
      "bazarr: the Settings button's sync reads no list of the missing - it stays quick")
limits.clear()
asyncio.run(registry.synchronize_all())
wanted = [(path, read) for path, read in limits if path.endswith("/wanted")]
check(sorted(wanted) == [("episodes/wanted", 300.0), ("movies/wanted", 300.0)],
      f"bazarr: the sync reads both lists of the missing with the long limit ({wanted})")
check(bazarr_languages()["languages"] and bazarr_languages()["languages_at"], f"bazarr: the languages stored ({bazarr_languages()})")
bazarr.live.CACHE.clear()
asked.clear()
limits.clear()
page = admin.get("/arr/bazarr?days=365").text
check(not any("/wanted" in path for path in asked), f"bazarr: the page reads no list of the missing ({sorted(set(asked))})")
check(limits and all(read == 3.0 for _path, read in limits), f"bazarr: every live request waits at most 3 s ({sorted(set(limits))})")
check("spočítáno" in page, "bazarr: the card of missing subtitles says when they were counted")
counted_at = bazarr_languages()["languages_at"]


def bazarr_slow(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/episodes/wanted"):
        raise httpx.ReadTimeout("Bazarr reads the missing for minutes", request=request)
    return bazarr_recorded(request)


restore()
restore = substitute(bazarr, "BazarrClient", _apps.bazarr_client(bazarr_slow))
asyncio.run(registry.synchronize_all())
check(bazarr.sync_state()["result"] == "ok" and bazarr_languages()["languages_at"] == counted_at,
      "bazarr: a list that does not come in time: the sync ends ok, the last languages stay")
restore()
restore = substitute(bazarr, "BazarrClient", _apps.bazarr_client(bazarr_recorded))
db.set_setting(bazarr.live.LANGUAGES_KEY, "")
bazarr.live.CACHE.clear()
page = admin.get("/arr/bazarr?days=365").text
check("Zatím nespočítáno" in page and "nepodařilo načíst" not in page,
      "bazarr: before the first count the card says so, no warning about a part that did not load")
restore()

print()
print("=== prowlarr ===")
limits = []
prowlarr_recorded = _apps.prowlarr_handler()


def prowlarr_limits(request: httpx.Request) -> httpx.Response:
    limits.append((request.url.path.split("/api/v1/", 1)[-1], request.extensions.get("timeout", {}).get("read")))
    return prowlarr_recorded(request)


restore = substitute(prowlarr, "ProwlarrClient", _apps.prowlarr_client(prowlarr_limits))
_apps.connect_prowlarr()
prowlarr.live.CACHE.clear()
admin.get("/arr/prowlarr?days=365")
check(limits and all(read == 3.0 for _path, read in limits), f"prowlarr: every live request waits at most 3 s ({sorted(set(limits))})")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
