# -*- coding: utf-8 -*-
r"""The *Arr counts that change slowly are counted by the sync and stored (the user, 23. 11. 2026).

The pages of the *Arr apps waited for `wanted/missing` and `wanted/cutoff` on every load after a minute - and a
big Sonarr did not count the cutoff within 20 s, so the page took 21 s. Now the *Arr sync counts them with a long
time limit and stores them with their time; the pages only read them. The shared part (jellyscope/services.py):

A) count_wanted() counts both with COUNT_TIMEOUT and stores them with the time;
B) a count that fails keeps the last known one and its time;
C) the counts belong to the app's address and key (another instance = nothing counted yet), each app its own;
D) every app's client passes the time limit to the request (record_count(..., timeout=)).

Spec: docs/superpowers/specs/2026-11-23-arr-live-counts-design.md

Run:
    .venv/bin/python tests/test_wanted_counts.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "arr-counts.db")
os.environ["SECRET_KEY"] = "test-key-arr-counts"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from jellyscope import db, services  # noqa: E402
from jellyscope.arr.lidarr.client import LidarrClient  # noqa: E402
from jellyscope.arr.radarr.client import RadarrClient  # noqa: E402
from jellyscope.arr.readarr.client import ReadarrClient  # noqa: E402
from jellyscope.arr.sonarr.client import SonarrClient  # noqa: E402

failures = 0
db.init_db()
TIMEOUT = object()          # a count that does not arrive in time


def check(condition: Any, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


class Fake:
    """A client of an *Arr app that knows only record_count; notes what it was asked with which limit."""
    asked: list[tuple[str, Any]] = []

    def __init__(self, **counts: Any) -> None:
        self.counts = counts
        Fake.asked = []

    async def record_count(self, path: str, timeout: Any = None) -> int:
        Fake.asked.append((path, timeout))
        value = self.counts[path.split("/", 1)[1]]
        if value is TIMEOUT:
            raise httpx.ReadTimeout("the app did not answer in time")
        return value


print("--- A) both counts with the long limit, stored with their time ---")
counts = asyncio.run(services.count_wanted("sonarr", Fake(missing=12, cutoff=34), "A"))
check(counts == {"missing": 12, "cutoff": 34}, f"the counts ({counts})")
check([path for path, _t in Fake.asked] == ["wanted/missing", "wanted/cutoff"]
      and all(t is services.COUNT_TIMEOUT for _p, t in Fake.asked), "both asked with COUNT_TIMEOUT")
check(services.COUNT_TIMEOUT.read == 300.0 and services.LIVE_TIMEOUT.read == 3.0
      and services.LIVE_TIMEOUT.connect == 3.0, "the limits: counting 5 minutes, live data 3 s")
stored = services.stored_counts("sonarr", "A")
check(stored["missing"] == 12 and stored["cutoff"] == 34 and stored["missing_at"] and stored["cutoff_at"],
      f"stored with their time ({stored})")


print()
print("--- B) a count that fails keeps the last known one ---")
counts = asyncio.run(services.count_wanted("sonarr", Fake(missing=13, cutoff=TIMEOUT), "A"))
check(counts == {"missing": 13, "cutoff": None}, f"the failed one is None in the answer ({counts})")
again = services.stored_counts("sonarr", "A")
check(again["missing"] == 13 and again["cutoff"] == 34 and again["cutoff_at"] == stored["cutoff_at"],
      f"the cutoff stays 34 with its old time ({again})")


print()
print("--- C) another instance, another app ---")
nothing = {"missing": None, "missing_at": "", "cutoff": None, "cutoff_at": ""}
check(services.stored_counts("sonarr", "B") == nothing, "another address or key: nothing counted yet")
check(services.stored_counts("radarr", "A") == nothing, "each app its own")
fingerprint = services.settings_fingerprint("http://sonarr.test", "tajny-klic")
check(fingerprint.startswith("http://sonarr.test|") and "tajny-klic" not in fingerprint,
      "the fingerprint carries the address and only a hash of the key")
db.set_setting("sonarr_counts", "not json")
check(services.stored_counts("sonarr", "A") == nothing, "a damaged record = nothing counted yet")


print()
print("--- D) every client passes the limit to the request ---")
for client_class, api in ((SonarrClient, "v3"), (RadarrClient, "v3"), (LidarrClient, "v1"), (ReadarrClient, "v1")):
    limits: list[Any] = []

    def answer(request: httpx.Request, limits: list[Any] = limits, api: str = api) -> httpx.Response:
        limits.append((request.url.path, request.extensions.get("timeout", {}).get("read")))
        return httpx.Response(200, json={"totalRecords": 7, "records": []})

    async def ask(client_class: Any = client_class, answer: Any = answer) -> int:
        async with client_class("http://app.test", "key", transport=httpx.MockTransport(answer)) as client:
            return await client.record_count("wanted/cutoff", timeout=services.COUNT_TIMEOUT)

    count = asyncio.run(ask())
    check(count == 7 and limits == [(f"/api/{api}/wanted/cutoff", 300.0)],
          f"{client_class.__name__}: the count with the long limit ({limits})")


print()
print("--- E) a sync once an hour is no change worth a line in the log (the final review) ---")
import logging  # noqa: E402

records: list[str] = []


class Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        records.append(record.getMessage())


logging.getLogger("jellyscope.db").addHandler(Collect())
logging.getLogger("jellyscope.db").setLevel(logging.INFO)
real_now = db.utcnow
for prefix in ("arr_sonarr", "arr_radarr", "arr_lidarr", "arr_readarr"):
    db.utcnow = lambda: "2026-11-23 10:00:00"
    asyncio.run(services.count_wanted(prefix, Fake(missing=1, cutoff=2), "A"))
    db.utcnow = lambda: "2026-11-23 11:00:00"
    asyncio.run(services.count_wanted(prefix, Fake(missing=1, cutoff=2), "A"))
db.utcnow = real_now
db.set_setting("arr_bazarr_missing_languages", '{"at": "2026-11-23 10:00:00"}')
db.set_setting("arr_bazarr_missing_languages", '{"at": "2026-11-23 11:00:00"}')
noise = [r for r in records if "_counts" in r or "missing_languages" in r]
check(not noise, f"the stored counts and languages write no 'setting … changed' line ({noise[:3]})")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
