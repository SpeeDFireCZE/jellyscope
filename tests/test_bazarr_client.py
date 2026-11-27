# -*- coding: utf-8 -*-
r"""Bazarr: the API client, settings, the connection test and recognising the time format.

Bazarr (subtitles for Sonarr and Radarr) has its own API under `<address>/api/` and the key in the
header `X-API-KEY`. Guarded:

A) the client: the key only in a header, errors without the key, from the history only the fields
   used (paths to subtitles and descriptions are not read);
B) settings and the connection test: an empty key keeps the stored one, the key is secret, the test
   recognises that Bazarr is not at the address;
C) time: Bazarr returns an event's time only as text in the system's format (`11/14/26 08:31:35`) in
   its zone - the format is recognised from the whole page.

The app does not run: the answers are recorded from a real Bazarr 1.6.2 (tests/bazarr_responses/; paths and names
in them are neutral).

Spec: docs/superpowers/specs/2026-11-14-bazarr-design.md

Run:
    .venv/bin/python tests/test_bazarr_client.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bazarr-client.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import BAZARR_RESPONSES, SONARR_RESPONSES, bazarr_handler, recorded_response  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import bazarr  # noqa: E402
from jellyscope.arr.bazarr import times  # noqa: E402

failures = 0
db.init_db()
KEY = "tajny-klic"
STATUS = recorded_response(BAZARR_RESPONSES, "system_status")
requests: list[httpx.Request] = []


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def bazarr_transport(instead: dict[str, Any] | None = None) -> httpx.MockTransport:
    handler = bazarr_handler(None, instead)

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)
    return httpx.MockTransport(record)


def answer(code: int, body: str = "", kind: str = "text/plain", headers: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _r: httpx.Response(code, text=body, headers={"Content-Type": kind, **(headers or {})}))


async def with_client(method: str, *args: Any, instead: dict[str, Any] | None = None) -> Any:
    async with bazarr.BazarrClient("http://nas:6767/bazarr", KEY, transport=bazarr_transport(instead)) as c:
        return await getattr(c, method)(*args)


print("--- A) the client ---")
status = asyncio.run(with_client("status"))
check(status.get("bazarr_version") == STATUS["data"]["bazarr_version"] and "timezone" in status
      and str(requests[-1].url) == "http://nas:6767/bazarr/api/system/status" and requests[-1].headers["X-API-KEY"] == KEY
      and KEY not in str(requests[-1].url), f"Bazarr's status: /api/ after the address with its path, the key only in a header ({requests[-1].url})")
h = asyncio.run(with_client("history", "episode", 0, 500))
recorded = recorded_response(BAZARR_RESPONSES, "episodes_history")
check(h["total"] == recorded["total"] and len(h["data"]) == len(recorded["data"]) > 0
      and requests[-1].url.path.endswith("/api/episodes/history") and dict(requests[-1].url.params) == {"start": "0", "length": "500"},
      f"the episodes' history page by page ({len(h['data'])} of {h['total']})")
first = next(r for r in h["data"] if r.get("action") == 1)
check({"action", "seriesTitle", "episode_number", "sonarrSeriesId", "sonarrEpisodeId", "language", "provider", "score", "subs_id",
       "parsed_timestamp"} <= set(first) and {"code2", "hi", "forced"} <= set(first["language"])
      and not ({"subtitles_path", "description", "matches", "dont_matches", "path", "timestamp"} & set(first)),
      f"a history record carries only what is used - paths and descriptions are not read ({sorted(first)})")
# An answer without `total` (another version, a proxy): an unknown count is None, not the page's length - else
# the read would end after the first full page.
without_total = asyncio.run(with_client("history", "episode", 0, 500, instead={"episodes/history": httpx.Response(200, json={"data": recorded["data"][:3]})}))
check(without_total["total"] is None and len(without_total["data"]) == 3, f"an answer without total: the count is unknown ({without_total['total']})")
f = asyncio.run(with_client("history", "movie", 0, 2))
check(len(f["data"]) == 2 and f["total"] == recorded_response(BAZARR_RESPONSES, "movies_history")["total"] and f["data"][0].get("radarrId")
      and f["data"][0].get("title") and requests[-1].url.path.endswith("/api/movies/history"), "the movies' history: a page of two records")
check(asyncio.run(with_client("badges")) == recorded_response(BAZARR_RESPONSES, "badges"), "badges: the counts of missing and throttled")
missing = asyncio.run(with_client("missing", "episode", 2000))
check(missing["total"] == recorded_response(BAZARR_RESPONSES, "episodes_wanted")["total"] and missing["data"]
      and set(missing["data"][0]) == {"missing_subtitles"} and "code2" in missing["data"][0]["missing_subtitles"][0],
      f"missing subtitles: only the languages, not names and paths ({sorted(missing['data'][0])})")
providers = asyncio.run(with_client("providers"))
check(len(providers) == len(recorded_response(BAZARR_RESPONSES, "providers")["data"]) and {"name", "status", "retry"} <= set(providers[0]),
      "providers with their state")
check(asyncio.run(with_client("health")) == recorded_response(BAZARR_RESPONSES, "system_health")["data"], "Bazarr's warnings")


async def status_error(transport: httpx.MockTransport, key: str = KEY) -> str:
    try:
        async with bazarr.BazarrClient("http://nas:6767", key, transport=transport) as c:
            await c.status()
    except bazarr.BazarrError as error:
        return str(error)
    return ""


check("401" in asyncio.run(status_error(answer(401))), "a refused key is an understandable error")
text = asyncio.run(status_error(answer(500, f"chyba, klíč {KEY}")))
check("500" in text and KEY not in text, f"the error body goes out without the key ({text})")
check(asyncio.run(status_error(answer(200, "<html>Sign in</html>", "text/html"))) != ""
      and asyncio.run(status_error(answer(200, "[1, 2]", "application/json"))) != ""
      and asyncio.run(status_error(answer(200, '{"data": [1]}', "application/json"))) != "",
      "a proxy's login page, a list instead of an object and a status without an object are errors")

print()
print("--- B) settings and the connection test ---")
check(bazarr.is_connected() is False and bazarr.stored_settings() == {"enabled": False, "url": "", "has_key": False}, "without settings it is not connected")
check(bazarr.save_settings(True, "nas:6767/bazarr/", "") != "" and bazarr.is_connected() is False, "switching on without a key does not work")
check(bazarr.save_settings(True, "nas:6767/bazarr/", KEY) == ""
      and bazarr.stored_settings() == {"enabled": True, "url": "http://nas:6767/bazarr/", "has_key": True} and bazarr.is_connected(),
      f"a save adds http:// and connects ({bazarr.stored_settings()})")
check(bazarr.save_settings(True, "http://nas:6767/bazarr", "") == "" and db.get_setting("arr_bazarr_api_key") == KEY,
      "an empty key keeps the stored one")
check(bazarr.save_settings(True, "ftp://nas", KEY) != "" and bazarr.save_settings(True, "http://nas", "klíč s diakritikou") != "",
      "an invalid address and a key with a character outside ASCII are refused")
db.forget_settings()
check("arr_bazarr_api_key" in db.SECRET_SETTINGS and KEY not in json.dumps(db.get_public_settings())
      and {"arr_bazarr_state", "arr_bazarr_last_sync"} <= set(db.UNLOGGED_SETTINGS),
      "the key is secret (it does not go into a page), the state and the sync mark do not fill the settings log")
requests.clear()
ok, version = asyncio.run(bazarr.check_connection("nas:6767/bazarr/", KEY, transport=bazarr_transport()))
check(ok and version == STATUS["data"]["bazarr_version"] and str(requests[0].url) == "http://nas:6767/bazarr/api/system/status",
      f"the connection test returns Bazarr's version ({version})")
sonarr_status = httpx.MockTransport(lambda r: httpx.Response(200, json=recorded_response(SONARR_RESPONSES, "system_status"))
                                    if r.url.path.endswith("/api/system/status") else httpx.Response(404))
ok, error = asyncio.run(bazarr.check_connection("http://nas:8989", "k", transport=sonarr_status))
check(not ok and "Bazarr" in error, f"another app answers at the address: the test does not pass ({error})")
redirect = answer(301, headers={"Location": "https://nas/bazarr/api/system/status"})
for label, transport in (("401", answer(401)), ("redirect", redirect), ("HTML", answer(200, "<html>", "text/html")),
                         ("list", answer(200, "[]", "application/json"))):
    ok, error = asyncio.run(bazarr.check_connection("http://nas/bazarr", "spatny-klic", transport=transport))
    check(not ok and bool(error) and "spatny-klic" not in error, f"the connection test, {label}: an error without the key ({error[:70]})")
check("https://nas/bazarr" in asyncio.run(bazarr.check_connection("nas/bazarr", "k", transport=redirect))[1],
      "a redirect to https: the message offers the address to enter")
check(asyncio.run(bazarr.check_connection("", KEY))[0] is False, "without an address nothing is tested")

print()
print("--- C) recognising the time format ---")
T = datetime(2026, 11, 14, 12, 0)
C = "%m/%d/%y %H:%M:%S"
check(times.FORMATS[0] == C, "the first known format is the one Bazarr really sends (the C locale)")
recorded_times = [r["parsed_timestamp"] for r in recorded["data"]]
check(times.detect_format(recorded_times, datetime(2026, 11, 15)) == C, f"the recorded history: the C locale's format ({recorded_times[0]})")
check(times.detect_format(["11/14/26 08:31:35", "11/13/26 23:10:00"], T) == C, "month/day/year")
check(times.detect_format(["14.11.2026 08:31:35"], T) == "%d.%m.%Y %H:%M:%S", "the Czech notation")
check(times.detect_format(["05/06/26 08:00:00", "12/05/26 09:00:00"], T) == "%d/%m/%y %H:%M:%S",
      "day/month: as month/day the times would rise and December is in the future")
check(times.detect_format(["05/06/26 08:00:00", "04/06/26 09:00:00"], T) == C, "undecidable (both give falling times): the first known format")
check(times.detect_format(["12/11/26 08:00:00"], T) == "%d/%m/%y %H:%M:%S", "a single record: the 11th of December would be in the future, it is the 12th of November")
check(times.detect_format(["11/14/2026 08:31:35 AM", "11/13/2026 11:10:00 PM"], T) is not None, "a notation with AM/PM")
check(times.detect_format(["nesmysl"], T) is None and times.detect_format([], T) is None and times.detect_format([None, ""], T) is None,
      "an unreadable time, an empty page or only missing times: the format is not recognised")
# One broken record must not take the date away from the whole page (500 records): the format is recognised
# from the rest and only that one stays without a date.
check(times.detect_format(["11/14/26 08:31:35", None, "", "11/13/26 08:00:00"], T) == C, "a record's missing time is skipped")
check(times.detect_format(["11/14/26 08:31:35", "nesmysl", "11/13/26 08:00:00"], T) == C, "one unreadable record among readable ones does not spoil the format")
check(times.detect_format(["12/30/26 08:00:00", "11/14/26 08:31:35", "11/13/26 08:00:00"], T) == C,
      "neither does one record from the future (a clock thrown off) among the others")
check(times.detect_format(["11/14/26 08:31:35", "x", "y"], T) is None, "when most cannot be read, the format is not recognised")
# A format that reads only part of the page must not win: day/month read as month/day "fits" for every day
# up to the twelfth, but swaps the day and the month - and such a time would be stored as exact for ever.
half = [f"{day:02d}/11/26 10:00:00" for day in range(14, 0, -1)]            # the 14th to the 1st of November, day/month/year
half.insert(3, "14/11/26 23:59:59")                                          # one record out of order (the right format drops out by it)
check(times.detect_format(half, datetime(2026, 12, 20)) is None,
      f"a page no format fits whole: rather none than one that reads only the days up to the twelfth ({times.detect_format(half, datetime(2026, 12, 20))})")
check(times.detect_format([f"11/{day:02d}/26 10:00:00" for day in range(30, 0, -1)] + ["nesmysl"], T + timedelta(days=30)) == C,
      "one broken record of thirty-one does not spoil the format (at most a twentieth of the page, at least one)")
D = "%d/%m/%y %H:%M:%S"
check(times.detect_format(["05/06/26 08:00:00", "04/06/26 09:00:00"], T, preferred=D) == D
      and times.detect_format(["11/14/26 08:31:35", "11/13/26 08:00:00"], T, preferred=D) == C,
      "the format recognised last takes precedence where the page itself does not decide - and does not override a page that rules it out")
check(times.to_utc("11/14/26 08:31:35", C, "Europe/Prague") == "2026-11-14 07:31:35", "Bazarr's zone -> UTC")
check(times.to_utc("07/14/26 08:31:35", C, "Europe/Prague") == "2026-07-14 06:31:35", "summer time")
check(times.to_utc("11/14/26 08:31:35", C, "Etc/UTC") == "2026-11-14 08:31:35", "Bazarr in UTC")
db.set_setting("app_timezone", "America/New_York")
db.forget_settings()
check(times.to_utc("11/14/26 08:31:35", C, "Neni/Zona") == times.to_utc("11/14/26 08:31:35", C, "") == "2026-11-14 13:31:35",
      "an unknown or empty zone = the app's zone")
check(times.to_utc("nesmysl", C, "Etc/UTC") is None, "an unreadable text is no time")
# "America" is a folder in the zone database: ZoneInfo fails on it with a system error, not "unknown zone".
check(times.to_utc("11/14/26 08:31:35", C, "America") == "2026-11-14 13:31:35", "a zone that is only a folder's name counts as unknown")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
