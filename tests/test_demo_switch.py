# -*- coding: utf-8 -*-
r"""The demo's made-up apps reach the clients only in the demo mode.

In the demo every app's client (services.HttpClient) and Jellyfin's (jellyfin.JellyfinClient) gets the
made-up app registered under its name (demo_switch) in place of the network; a name with nothing
registered answers 503, so nothing leaves the process; an address without http(s) or a host (never entered) is
refused before the made-up app, as the network refuses it. A real installation never gets one: its Sonarr
talks to its Sonarr, whatever is registered. The seed connects the registered apps and syncs them
through their real code (demo_apps.seed_apps), the daily refresh deletes what they downloaded first
(demo_apps.wipe_apps), and the web's start registers the made-up apps before anything else.

The test starts as a real installation (JELLYSCOPE_DEMO=0) and switches the demo mode on halfway.
Every socket call is substituted: the real installation is to try the network, the demo never. Only the test's own
database goes through, on PostgreSQL (tests/_network.py) - checked last.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 3)

Run:
    .venv/bin/python tests/test_demo_switch.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import sys
import tempfile
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "demo-switch-test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_world, demodata, seerr  # noqa: E402
from jellyscope.arr import registry  # noqa: E402
from jellyscope.jellyfin import JellyfinClient, JellyfinError  # noqa: E402
from jellyscope.services import HttpClient, ServiceError  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Every way out of the process is recorded and refused - but the test's own database on PostgreSQL (tests/_network.py).
NETWORK = refuse_network()

KEY = "0123456789abcdef0123456789abcdef"
SEEN: list[httpx.Request] = []


def fake_sonarr(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    return httpx.Response(200, json={"appName": "Sonarr", "version": "4.0-demo"})


def fake_jellyfin(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    return httpx.Response(200, json=[{"Id": "a" * 32, "Name": "Petr"}])


async def _ask(factory, call):
    async with factory() as client:
        return await call(client)


def outcome(factory, call) -> tuple[str, object]:
    """("answer", the content) or ("error", the client's error text)."""
    try:
        return "answer", asyncio.run(_ask(factory, call))
    except (ServiceError, JellyfinError) as error:
        return "error", str(error)


def sonarr_at(url: str, **kwargs):
    return lambda: HttpClient("Sonarr", url, "/api/v3/", KEY, **kwargs)


def status(client):
    return client._get("system/status")


def users(client):
    return client.users()


async def series(client):
    return [item async for item in client.iter_items(item_types="Series")]


def guarded(factory, call) -> tuple[str, object]:
    """outcome(), and ("crash", the exception) for what no caller of a client catches."""
    try:
        return outcome(factory, call)
    except Exception as error:   # noqa: BLE001 - the check says it
        return "crash", repr(error)[:120]


# Addresses the network refuses before it is asked: none entered, no protocol, another protocol.
ODD = ("", "sonarr.demo", "ftp://sonarr.demo")


def connected_apps() -> list[str]:
    return registry.connected() + (["seerr"] if seerr.is_connected() else [])


db.init_db()

from jellyscope import demo_apps, demo_switch  # noqa: E402

print("--- (a) a real installation: never a made-up app ---")
check(demo_switch.transport("Sonarr") is None and demo_switch.transport("Jellyfin") is None,
      "outside the demo mode the switch gives no transport")
demo_switch.register("Sonarr", fake_sonarr)
demo_switch.register("Jellyfin", fake_jellyfin)
check(demo_switch.transport("Sonarr") is None, "not even for a name with a made-up app registered")
kind, answer = outcome(sonarr_at("http://127.0.0.1:1"), status)
check(kind == "error" and "not answering" in str(answer) and not SEEN and NETWORK,
      f"Sonarr's client goes to the network and gets a connection error, not the made-up answer ({kind}: {str(answer)[:70]})")
NETWORK.clear()
kind, answer = outcome(lambda: JellyfinClient("http://127.0.0.1:1", KEY), users)
check(kind == "error" and "connect" in str(answer) and not SEEN and NETWORK,
      f"Jellyfin's client the same ({kind}: {str(answer)[:70]})")
NETWORK.clear()
real_odd = {url: guarded(sonarr_at(url), status) for url in ODD}
real_series = guarded(lambda: JellyfinClient("", KEY), series)
check(all(kind == "error" for kind, _answer in real_odd.values()) and real_series[0] == "error" and not SEEN and not NETWORK,
      f"an address without http(s): the clients' error before the network ({real_odd}, {real_series})")
sonarr = registry.APPS["sonarr"]
calls: list[str] = []


async def _synced() -> dict:
    calls.append("sync")
    return {"new_count": 0}


registry.APPS["sonarr"] = dataclasses.replace(sonarr, demo=lambda: fake_sonarr, synchronize=_synced)
demo_switch.clear()
demo_apps.load_fakes()
check(not demo_switch.registered("Sonarr"), "load_fakes registers nothing outside the demo mode")
demo_switch.register("Sonarr", fake_sonarr)
check(demo_apps.seed_apps() == {} and not calls and connected_apps() == [],
      f"seed_apps connects and syncs nothing outside the demo mode ({connected_apps()}, {calls})")
check(sonarr.save_settings(True, "http://127.0.0.1:1", KEY) == "" and registry.connected() == ["sonarr"],
      "a real installation's own Sonarr is connected")
NETWORK.clear()
ok, said = asyncio.run(sonarr.check_connection("http://127.0.0.1:1", KEY))
check(not ok and not SEEN and NETWORK,
      f"its connection test goes through Sonarr's own client to the network, not to the made-up app ({said[:70]})")
deleted: list[str] = []


async def _deleted() -> int:
    deleted.append("sonarr")
    return 0


registry.APPS["sonarr"] = dataclasses.replace(registry.APPS["sonarr"], delete_downloaded_data=_deleted)
demo_apps.wipe_apps()
check(not deleted and registry.connected() == ["sonarr"],
      "wipe_apps deletes nothing of a real installation's apps")
check(sonarr.save_settings(False, "", "") == "" and registry.connected() == [], "(Sonarr disconnected again)")
registry.APPS["sonarr"] = sonarr
demo_switch.clear()
NETWORK.clear()
SEEN.clear()

print("--- (b) the demo without a made-up app: 503, nothing to the network ---")
os.environ["JELLYSCOPE_DEMO"] = "1"
config.load_config(reload=True)
check(isinstance(demo_switch.transport("Sonarr"), httpx.MockTransport), "in the demo mode every name gets a transport")
kind, answer = outcome(sonarr_at("http://sonarr.demo"), status)
check(kind == "error" and "503" in str(answer), f"an app with nothing registered answers 503 ({kind}: {str(answer)[:70]})")
kind, answer = outcome(lambda: JellyfinClient("http://jellyfin.demo", KEY), users)
check(kind == "error" and "503" in str(answer), f"Jellyfin with nothing registered too ({kind}: {str(answer)[:70]})")
check(not NETWORK, f"no socket was touched ({NETWORK[:2]})")

print("--- (c) a registered made-up app answers Sonarr's client ---")
demo_switch.register("Sonarr", fake_sonarr)
check(demo_switch.registered("Sonarr") and not demo_switch.registered("Radarr"), "registered() knows what is registered")
kind, answer = outcome(sonarr_at("http://sonarr.demo"), status)
check(kind == "answer" and answer == {"appName": "Sonarr", "version": "4.0-demo"}, f"the made-up answer ({kind}: {answer})")
check(len(SEEN) == 1 and str(SEEN[0].url) == "http://sonarr.demo/api/v3/system/status"
      and SEEN[0].headers.get("X-Api-Key") == KEY, "the made-up app gets the real request: the address, the API path, the key")
kind, answer = outcome(lambda: HttpClient("Radarr", "http://radarr.demo", "/api/v3/", KEY), status)
check(kind == "error" and "503" in str(answer) and len(SEEN) == 1, "another app's client does not get Sonarr's made-up app")
own = httpx.MockTransport(lambda request: httpx.Response(200, json={"appName": "own"}))
kind, answer = outcome(sonarr_at("http://sonarr.demo", transport=own), status)
check(kind == "answer" and answer == {"appName": "own"} and len(SEEN) == 1, "a transport passed in (tests) still wins")
SEEN.clear()

print("--- (d) and Jellyfin's client under the name Jellyfin ---")
demo_switch.register("Jellyfin", fake_jellyfin)
kind, answer = outcome(lambda: JellyfinClient("http://jellyfin.demo", KEY), users)
check(kind == "answer" and answer == [{"Id": "a" * 32, "Name": "Petr"}], f"the made-up Jellyfin answers ({kind}: {answer})")
check(len(SEEN) == 1 and SEEN[0].url.path == "/Users", "with the real request")
check(not NETWORK, f"still no socket ({NETWORK[:2]})")
SEEN.clear()

print("--- (d2) an address without http(s) or a host: refused before the made-up app, as the network refuses it ---")
demo_odd = {url: guarded(sonarr_at(url), status) for url in ODD}
check(demo_odd == real_odd and not SEEN, f"Sonarr's client: the same error as without the demo, the made-up app not asked ({demo_odd})")
demo_series = guarded(lambda: JellyfinClient("", KEY), series)
check(demo_series == real_series and not SEEN,
      f"Jellyfin's series without an address (Sonarr's pairing): the same error, no ValueError ({demo_series})")
kind, answer = guarded(sonarr_at("http://"), status)
check(kind == "error" and not SEEN and not NETWORK, f"an address without a host: an error, the made-up app not asked ({kind}: {answer})")
SEEN.clear()

print("--- (e) the seed without made-up apps connects nothing ---")
demo_switch.clear()
check(demo_apps.seed_apps() == {} and connected_apps() == [], f"seed_apps gives {{}} and no app is connected ({connected_apps()})")
demo_apps.wipe_apps()
check(connected_apps() == [], "wipe_apps with nothing connected does nothing")

print("--- the registry's field demo and loading the made-up apps ---")
field = {f.name: f for f in dataclasses.fields(registry.App)}.get("demo")
check(field is not None and field.default is None, "App has the field demo, None by default (the app is not in the demo)")
package_dir = Path(tempfile.mkdtemp())
(package_dir / "madeupapp").mkdir()
(package_dir / "madeupapp" / "__init__.py").write_text("", encoding="utf-8")
(package_dir / "madeupapp" / "demo.py").write_text(
    "import httpx\n\n\ndef handler(request):\n    return httpx.Response(200, json={'made_up': True})\n", encoding="utf-8")
sys.path.insert(0, str(package_dir))
import madeupapp  # noqa: E402

lazy = registry._demo(madeupapp)
check("madeupapp.demo" not in sys.modules, "registry._demo imports the package's demo.py only when asked")
made = lazy()
check(made(httpx.Request("GET", "http://x.demo/")).json() == {"made_up": True}, "and then gives its handler")
check(demo_apps._optional("no_such_made_up_module") is None and demo_apps._optional("seerr.no_such_made_up_module") is None,
      "a made-up module that does not exist yet is skipped")
registry.APPS["sonarr"] = dataclasses.replace(sonarr, demo=lambda: fake_sonarr)
# Radarr has a made-up app of its own (arr/radarr/demo.py): here it stands for an app without one.
radarr_app = registry.APPS["radarr"]
registry.APPS["radarr"] = dataclasses.replace(radarr_app, demo=None)
demo_apps.load_fakes()
demo_apps.load_fakes()
check(demo_switch.registered("Sonarr") and not demo_switch.registered("Radarr"),
      "load_fakes registers the app with demo= under its client's name (twice is the same), not one without it")
kind, answer = outcome(sonarr_at("http://sonarr.demo"), status)
check(kind == "answer" and answer["appName"] == "Sonarr", "and the client gets it")
registry.APPS["sonarr"] = sonarr
registry.APPS["radarr"] = radarr_app
demo_switch.clear()
SEEN.clear()

print("--- seed_apps: settings, the real sync and counts, one app's failure ---")
radarr = registry.APPS["radarr"]
order: list[str] = []


async def sonarr_sync() -> dict:
    # Through the real core with what seed_apps stored: the made-up app gets the made-up key.
    settings = sonarr.stored_settings()
    async with HttpClient("Sonarr", settings["url"], "/api/v3/", db.get_setting("arr_sonarr_api_key", "")) as client:
        await client._get("system/status")
    order.append("sonarr sync")
    return {"new_count": 7}


async def sonarr_counts() -> None:
    order.append("sonarr counts")


async def radarr_sync() -> dict:
    order.append("radarr sync")
    raise RuntimeError("a made-up failure")


registry.APPS["sonarr"] = dataclasses.replace(sonarr, synchronize=sonarr_sync, refresh_counts=sonarr_counts,
                                              event_count=lambda: 7)
registry.APPS["radarr"] = dataclasses.replace(radarr, synchronize=radarr_sync)
demo_switch.register("Sonarr", fake_sonarr)
demo_switch.register("Radarr", lambda request: httpx.Response(200, json={}))
records: list[logging.LogRecord] = []


class _Catch(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        records.append(record)


catcher = _Catch(logging.WARNING)
logging.getLogger("jellyscope").addHandler(catcher)
counts = demo_apps.seed_apps()
check(counts == {"sonarr": 7}, f"the counts of the filled apps; the failed one is left out ({counts})")
check(order == ["sonarr sync", "sonarr counts", "radarr sync"], f"the sync, then the counts, app by app ({order})")
check(sonarr.stored_settings() == {"enabled": True, "url": "http://sonarr.demo", "has_key": True}
      and db.get_setting("arr_sonarr_api_key", "") == demo_world.jellyfin_id("key-sonarr"),
      "Sonarr is stored on, at http://sonarr.demo, with the made-up key")
check(len(SEEN) == 1 and SEEN[0].headers.get("X-Api-Key") == demo_world.jellyfin_id("key-sonarr"),
      "its sync reached the made-up app with that key")
lines = [r.getMessage() for r in records]
check(len(lines) == 1 and "Radarr" in lines[0] and "a made-up failure" in lines[0],
      f"Radarr's failure is one line in the log ({lines})")
check(registry.connected() == ["sonarr", "radarr"] and not seerr.is_connected(),
      f"only the apps with a made-up one registered are connected ({connected_apps()})")
check(not NETWORK, f"no socket ({NETWORK[:2]})")

print("--- wipe_apps: the downloaded data of every connected app, one failure does not stop the others ---")
wiped: list[str] = []


async def sonarr_delete() -> int:
    wiped.append("sonarr")
    raise RuntimeError("a made-up deletion failure")


async def radarr_delete() -> int:
    wiped.append("radarr")
    return 3


async def lidarr_delete() -> int:
    wiped.append("lidarr")
    return 0


lidarr = registry.APPS["lidarr"]
registry.APPS["sonarr"] = dataclasses.replace(registry.APPS["sonarr"], delete_downloaded_data=sonarr_delete)
registry.APPS["radarr"] = dataclasses.replace(registry.APPS["radarr"], delete_downloaded_data=radarr_delete)
registry.APPS["lidarr"] = dataclasses.replace(lidarr, delete_downloaded_data=lidarr_delete)
records.clear()
demo_apps.wipe_apps()
check(wiped == ["sonarr", "radarr"], f"the connected apps' data are deleted, the others' are not touched ({wiped})")
lines = [r.getMessage() for r in records]
check(len(lines) == 1 and "Sonarr" in lines[0] and "a made-up deletion failure" in lines[0],
      f"Sonarr's failure is one line in the log, Radarr is deleted after it ({lines})")
logging.getLogger("jellyscope").removeHandler(catcher)
registry.APPS.update(sonarr=sonarr, radarr=radarr, lidarr=lidarr)
for app in (sonarr, radarr):
    app.save_settings(False, "", "")
demo_switch.clear()
SEEN.clear()

print("--- the seed and the refresh go through demo_apps ---")
steps: list[str] = []
originals = demo_apps.load_fakes, demo_apps.seed_apps, demo_apps.wipe_apps


def library_ready() -> bool:
    return all(int(db.query_value(f"SELECT COUNT(*) FROM {table}")) > 0 for table in ("items", "music_track", "book", "playback"))


demo_apps.load_fakes = lambda: steps.append("load")
demo_apps.seed_apps = lambda: steps.append(f"seed apps, library {library_ready()}") or {}
demo_apps.wipe_apps = lambda: steps.append(f"wipe apps, library {library_ready()}")
demodata.seed()
check(steps == ["load", "seed apps, library True"],
      f"seed() registers the made-up apps and fills them after the video, music and books ({steps})")
steps.clear()
demodata.refresh()
check(steps[:1] == ["wipe apps, library True"] and steps[1:] == ["load", "seed apps, library True"],
      f"refresh() deletes the apps' data before the made-up library, then seeds again ({steps})")
demo_apps.load_fakes, demo_apps.seed_apps, demo_apps.wipe_apps = originals

print("--- the web's start registers the made-up apps in the demo mode ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

registry.APPS["sonarr"] = dataclasses.replace(sonarr, demo=lambda: fake_sonarr)
demo_switch.clear()
with TestClient(web.app) as client:
    check(demo_switch.registered("Sonarr"), "after the start the made-up app is registered")
registry.APPS["sonarr"] = sonarr
demo_switch.clear()
check(not NETWORK, f"nothing in the demo mode touched a socket ({NETWORK[:2]})")

print("--- the guard lets through the test's own database only ---")
import socket  # noqa: E402

database = db.database_config()


def refused(call) -> bool:
    """The attempt is refused and recorded."""
    before = len(NETWORK)
    try:
        call()
    except ConnectionRefusedError:
        return len(NETWORK) == before + 1
    return False


def connect(address: tuple[str, int]) -> None:
    with socket.socket() as one:
        one.connect(address)


check(refused(lambda: socket.getaddrinfo("example.org", 443)) and refused(lambda: socket.create_connection(("192.0.2.7", 8096)))
      and refused(lambda: connect(("192.0.2.7", 8096))), "another host: refused and recorded")
if database.is_postgres:
    check(bool(socket.getaddrinfo(database.host, database.port)), f"the database's host and port go through ({database.host})")
    check(refused(lambda: socket.getaddrinfo(database.host, database.port + 1)), "another port of its host is refused")
else:
    check(refused(lambda: socket.getaddrinfo("postgres", 5432)), "on SQLite no database server goes through either")
NETWORK.clear()

print(f"\nDONE - failures: {failures}")
sys.exit(1 if failures else 0)
