# -*- coding: utf-8 -*-
r"""Prowlarr: the API client, settings and the connection test.

Prowlarr (indexers for Sonarr and Radarr) has its API under `<address>/api/v1/` and the key in the
header `X-Api-Key`. Guarded:

A) the client: the key only in a header, errors without the key; from the history only the fields used
   - the queries' addresses (they often hold the indexers' keys), `host` and the searched text are not
   read; the `fields` (keys) of indexers and apps are not read;
B) settings and the connection test: an empty key keeps the stored one, the key is secret, the test
   recognises that Prowlarr is not at the address.

The app does not run: the answers are recorded from a real Prowlarr 2.7.0 (tests/prowlarr_responses/;
paths and names in them are neutral). The places with keys are replaced in the recordings by
the marks `TAJNY-KLIC-…` - the client must not read them.

Spec: docs/superpowers/specs/2026-11-15-prowlarr-design.md

Run:
    .venv/bin/python tests/test_prowlarr_client.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-client.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import PROWLARR_RESPONSES, recorded_response, prowlarr_history, prowlarr_handler  # noqa: E402
from jellyscope import db, services  # noqa: E402
from jellyscope.arr import prowlarr  # noqa: E402

failures = 0
db.init_db()
KEY = "tajny-klic"
STATUS = recorded_response(PROWLARR_RESPONSES, "system_status")
HISTORY = prowlarr_history()
NEWEST_FIRST = sorted(HISTORY, key=lambda r: (r["date"], r["id"]), reverse=True)
requests: list[httpx.Request] = []


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def prowlarr_transport(instead: dict[str, Any] | None = None) -> httpx.MockTransport:
    handler = prowlarr_handler(None, instead)

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)
    return httpx.MockTransport(record)


def answer(code: int, body: str = "", kind: str = "text/plain", headers: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _r: httpx.Response(code, text=body, headers={"Content-Type": kind, **(headers or {})}))


async def with_client(method: str, *args: Any, instead: dict[str, Any] | None = None) -> Any:
    async with prowlarr.ProwlarrClient("http://nas:9696/prowlarr", KEY, transport=prowlarr_transport(instead)) as c:
        return await getattr(c, method)(*args)


print("--- A) the client ---")
check("TAJNY-KLIC-INDEXERU" in json.dumps(HISTORY) and "TAJNY-KLIC-APLIKACE" in json.dumps(recorded_response(PROWLARR_RESPONSES, "applications"))
      and "TAJNY-KLIC-APLIKACE" in json.dumps(recorded_response(PROWLARR_RESPONSES, "indexer")),
      "the recordings have marks in place of keys (there is something to guard)")
status = asyncio.run(with_client("status"))
check(status.get("appName") == "Prowlarr" and status.get("version") == STATUS["version"]
      and str(requests[-1].url) == "http://nas:9696/prowlarr/api/v1/system/status" and requests[-1].headers["X-Api-Key"] == KEY
      and KEY not in str(requests[-1].url), f"Prowlarr's status: /api/v1/ after the address with its path, the key only in a header ({requests[-1].url})")
h = asyncio.run(with_client("history", 1, 10))
check(h["total"] == len(HISTORY) and [r["id"] for r in h["records"]] == [r["id"] for r in NEWEST_FIRST[:10]]
      and h["records"][0]["id"] == max(r["id"] for r in HISTORY)
      and dict(requests[-1].url.params) == {"page": "1", "pageSize": "10", "sortKey": "date", "sortDirection": "descending"},
      f"the history page by page from the newest event ({len(h['records'])} of {h['total']})")
h3 = asyncio.run(with_client("history", 3, 10))
check([r["id"] for r in h3["records"]] == [r["id"] for r in NEWEST_FIRST[20:30]] and 0 < len(h3["records"]) < 10, "the last page is incomplete")
check(asyncio.run(with_client("history", 9, 10)) == {"records": [], "total": len(HISTORY)}, "a page past the end of the history is empty")
query = next(r for r in h["records"] if r["eventType"] == "indexerQuery")
grabbed = next(r for r in asyncio.run(with_client("history", 1, 100))["records"] if r["eventType"] == "releaseGrabbed" and "elapsedTime" in r["data"])
check(set(query) == {"id", "indexerId", "date", "successful", "eventType", "data"}
      and set(query["data"]) <= {"elapsedTime", "queryResults", "cached", "source"} and {"elapsedTime", "queryResults", "source"} <= set(query["data"]),
      f"a query record carries only what is used ({sorted(query['data'])})")
check({"grabTitle", "grabMethod", "source", "elapsedTime"} <= set(grabbed["data"]) and "url" not in grabbed["data"],
      f"a grabbed release's record: the name, the method, the app, the time ({sorted(grabbed['data'])})")
everything = json.dumps(asyncio.run(with_client("history", 1, 100)))
check("TAJNY-KLIC" not in everything and "apikey" not in everything.lower() and '"url"' not in everything and '"host"' not in everything
      and '"query"' not in everything,
      "the queries' addresses, the host and the searched text are never read from the history")
without_total = asyncio.run(with_client("history", 1, 10, instead={"history": httpx.Response(200, json={"records": HISTORY[:3]})}))
check(without_total["total"] is None and len(without_total["records"]) == 3, "an answer without totalRecords: the count is unknown")
indexers = asyncio.run(with_client("indexers"))
check([set(i) for i in indexers] == [{"id", "name", "protocol", "privacy", "enable"}] * len(recorded_response(PROWLARR_RESPONSES, "indexer"))
      and {i["protocol"] for i in indexers} == {"usenet", "torrent"},
      f"indexers: only the number, the name, the protocol, the privacy, enabled ({[i['name'] for i in indexers]})")
applications = asyncio.run(with_client("applications"))
check({a["name"] for a in applications} == {"Sonarr", "Radarr"} and all(set(a) <= {"id", "name", "implementation", "syncLevel"} for a in applications)
      and "TAJNY-KLIC" not in json.dumps([indexers, applications]), "apps: the name and the sync level; the fields with keys are not read")
blocked = asyncio.run(with_client("blocked"))
check(blocked == recorded_response(PROWLARR_RESPONSES, "indexerstatus") and {"indexerId", "disabledTill", "mostRecentFailure", "initialFailure"} <= set(blocked[0]),
      "blocked indexers: since when they fail and until when they are switched off")
health = asyncio.run(with_client("health"))
check(len(health) == 1 and {"type", "message"} <= set(health[0]), "Prowlarr's warnings")
check(set(prowlarr.KINDS) == {"indexerQuery", "indexerRss", "indexerAuth", "releaseGrabbed"}
      and set(prowlarr.KINDS.values()) == {"query", "rss", "auth", "grabbed"}
      and {r["eventType"] for r in HISTORY} <= set(prowlarr.KINDS), "event kinds: every one from the recorded history has its kind")


async def status_error(transport: httpx.MockTransport, key: str = KEY) -> str:
    try:
        async with prowlarr.ProwlarrClient("http://nas:9696", key, transport=transport) as c:
            await c.status()
    except prowlarr.ProwlarrError as error:
        return str(error)
    return ""


check("401" in asyncio.run(status_error(answer(401))), "a refused key is an understandable error")
text = asyncio.run(status_error(answer(500, f"chyba, klíč {KEY}")))
check("500" in text and KEY not in text, f"the error body goes out without the key ({text})")
check(asyncio.run(status_error(answer(200, "<html>Sign in</html>", "text/html"))) != ""
      and asyncio.run(status_error(answer(200, "[1, 2]", "application/json"))) != "",
      "a proxy's login page and a list instead of an object are errors")


async def history_error(transport: httpx.MockTransport) -> str:
    try:
        async with prowlarr.ProwlarrClient("http://nas:9696", KEY, transport=transport) as c:
            await c.history(1, 10)
    except prowlarr.ProwlarrError as error:
        return str(error)
    return ""


async def indexer_error(transport: httpx.MockTransport) -> str:
    try:
        async with prowlarr.ProwlarrClient("http://nas:9696", KEY, transport=transport) as c:
            await c.indexers()
    except prowlarr.ProwlarrError as error:
        return str(error)
    return ""


check(asyncio.run(indexer_error(answer(200, '{"error": "x"}', "application/json"))) != ""
      and asyncio.run(indexer_error(answer(200, "[]", "application/json"))) == "",
      "a list of indexers in an unexpected shape is an error (not \"no indexers\"); an empty list is fine")
check(asyncio.run(history_error(answer(200, '{"records": "nic"}', "application/json"))) != ""
      and asyncio.run(history_error(answer(200, "[]", "application/json"))) != "", "a history in an unexpected shape is an error, not an empty history")
check(issubclass(prowlarr.ProwlarrError, services.ServiceError), "Prowlarr's error is a service error (the web can translate it)")

print()
print("--- B) settings and the connection test ---")
check(prowlarr.is_connected() is False and prowlarr.stored_settings() == {"enabled": False, "url": "", "has_key": False}, "without settings it is not connected")
check(prowlarr.save_settings(True, "nas:9696/prowlarr/", "") != "" and prowlarr.is_connected() is False, "switching on without a key does not work")
check(prowlarr.save_settings(True, "nas:9696/prowlarr/", KEY) == ""
      and prowlarr.stored_settings() == {"enabled": True, "url": "http://nas:9696/prowlarr/", "has_key": True} and prowlarr.is_connected(),
      f"a save adds http:// and connects ({prowlarr.stored_settings()})")
check(prowlarr.save_settings(True, "http://nas:9696/prowlarr", "") == "" and db.get_setting("arr_prowlarr_api_key") == KEY,
      "an empty key keeps the stored one")
check(prowlarr.save_settings(True, "ftp://nas", KEY) != "" and prowlarr.save_settings(True, "http://nas", "klíč s diakritikou") != "",
      "an invalid address and a key with a character outside ASCII are refused")
db.forget_settings()
check("arr_prowlarr_api_key" in db.SECRET_SETTINGS and KEY not in json.dumps(db.get_public_settings())
      and {"arr_prowlarr_state", "arr_prowlarr_last_id", "arr_prowlarr_last_event_at", "arr_prowlarr_counted"} <= set(db.UNLOGGED_SETTINGS),
      "the key is secret (it does not go into a page); the state and the sync marks do not fill the settings log")
requests.clear()
ok, version = asyncio.run(prowlarr.check_connection("nas:9696/prowlarr/", KEY, transport=prowlarr_transport()))
check(ok and version == STATUS["version"] and str(requests[0].url) == "http://nas:9696/prowlarr/api/v1/system/status",
      f"the connection test returns Prowlarr's version ({version})")
# The status as another app of the same family returns it (it has the same API, only another name).
sonarr_status = httpx.MockTransport(lambda r: httpx.Response(200, json={"appName": "Sonarr", "instanceName": "Sonarr", "version": "4.0.20.3014"}))
ok, error = asyncio.run(prowlarr.check_connection("http://nas:8989", "k", transport=sonarr_status))
check(not ok and "Sonarr" in error and "Prowlarr" in error, f"another app answers at the address: the test says which ({error})")
redirect = answer(301, headers={"Location": "https://nas/prowlarr/api/v1/system/status"})
for label, transport in (("401", answer(401)), ("redirect", redirect), ("HTML", answer(200, "<html>", "text/html")),
                         ("list", answer(200, "[]", "application/json"))):
    ok, error = asyncio.run(prowlarr.check_connection("http://nas/prowlarr", "spatny-klic", transport=transport))
    check(not ok and bool(error) and "spatny-klic" not in error, f"the connection test, {label}: an error without the key ({error[:70]})")
check("https://nas/prowlarr" in asyncio.run(prowlarr.check_connection("nas/prowlarr", "k", transport=redirect))[1],
      "a redirect to https: the message offers the address to enter")
check(asyncio.run(prowlarr.check_connection("", KEY))[0] is False, "without an address nothing is tested")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
