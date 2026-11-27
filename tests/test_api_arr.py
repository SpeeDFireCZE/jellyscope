# -*- coding: utf-8 -*-
"""The *Arr apps in the API: /api/v1/arr, /arr/{app}/queue, /calendar and /health (live), and /stats.

In the demo mode, against the made-up apps (demo_apps): the shapes and raw numbers, an app that is not connected
(404 app_not_connected), an address an app does not have (404 not_available_for_app), an app that does not answer
(200 with available false, within the live state's limit), the live state's one-minute memory (a second request
asks the app nothing), and the rights (the area arr; a reader's own key without it).

Run:
    .venv/bin/python tests/test_api_arr.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api_arr.db")
os.environ["SECRET_KEY"] = "api-arr-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "1"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import access, accounts, api, db, demo_switch, demodata, web  # noqa: E402
from jellyscope.arr import registry  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


refuse_network()
db.init_db()
demodata.prepare(quiet=True)
client = TestClient(web.app)
LIVES = {key: importlib.import_module(f"jellyscope.arr.{key}.live") for key in registry.APPS}


def ask(token: str, path: str) -> tuple[int, dict]:
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    return response.status_code, response.json()


def forget_live() -> None:
    for module in LIVES.values():
        getattr(module, "CACHE", {}).clear()


everything = api.create_token("everything")["token"]

print("--- /api/v1/arr ---")
status, data = ask(everything, "/api/v1/arr")
apps = {row["app"]: row for row in data.get("apps") or []}
check(status == 200 and set(apps) == set(registry.APPS) and all(
    set(row) == {"app", "name", "connected", "warnings", "last_sync", "last_sync_result"} for row in apps.values()),
      "every app with its state", f"({status} {data})")
check(all(row["connected"] and row["last_sync"] and isinstance(row["warnings"], int) for row in apps.values()),
      "the demo's apps are connected, synced and their warnings counted")

print("--- the queue, calendar and health of the four download apps ---")
QUEUE = {"title", "detail", "status", "progress_share", "size_bytes", "left_bytes", "seconds_left", "client", "protocol"}
for key in ("sonarr", "radarr", "lidarr", "readarr"):
    status, queue = ask(everything, f"/api/v1/arr/{key}/queue")
    items = queue.get("queue") or []
    check(status == 200 and queue.get("available") is True and queue.get("reason") is None and items
          and all(set(i) == QUEUE for i in items) and all(0 <= i["progress_share"] <= 1 for i in items)
          and all(isinstance(i["left_bytes"], int) and i["left_bytes"] >= 0 for i in items),
          f"{key}: the queue with progress shares", f"({status} {items[:1]})")
    check(isinstance(queue.get("errors"), list) and all(set(e) == {"title", "detail", "status", "message"} for e in queue["errors"]),
          f"{key}: the queue's errors", f"({queue.get('errors')})")
    status, calendar = ask(everything, f"/api/v1/arr/{key}/calendar?days=14")
    check(status == 200 and calendar.get("calendar") and all(set(c) == {"title", "detail", "release", "has_file"}
                                                            for c in calendar["calendar"]),
          f"{key}: the calendar", f"({status} {calendar.get('calendar', [])[:1]})")
    status, health = ask(everything, f"/api/v1/arr/{key}/health")
    check(status == 200 and all(set(h) == {"type", "message"} for h in health.get("health") or []),
          f"{key}: the health", f"({status} {health})")
for key, part in (("bazarr", "queue"), ("prowlarr", "calendar")):
    status, data = ask(everything, f"/api/v1/arr/{key}/{part}")
    check(status == 404 and data.get("error") == "not_available_for_app", f"{key} has no {part}: 404 not_available_for_app")
check(ask(everything, "/api/v1/arr/bazarr/health")[0] == 200, "Bazarr has its health")
status, data = ask(everything, "/api/v1/arr/nothing/queue")
check(status == 404 and data.get("error") == "not_found", "an unknown app is a 404")

print("--- /api/v1/arr/{app}/stats and /api/v1/arr/prowlarr/indexers ---")
DOWNLOADS = {"days", "grabbed", "imported", "upgrades", "failed", "release_to_download_median_seconds",
             "release_to_download_p90_seconds", "grab_to_import_median_seconds", "missing", "cutoff_unmet", "indexers"}
for key in ("sonarr", "radarr", "lidarr", "readarr"):
    status, data = ask(everything, f"/api/v1/arr/{key}/stats?days=30")
    check(status == 200 and set(data) == DOWNLOADS and data["imported"] > 0 and data["grabbed"] >= data["imported"]
          and isinstance(data["release_to_download_median_seconds"], int) and data["release_to_download_median_seconds"] > 0
          and isinstance(data["missing"], int) and all(
              set(i) == {"name", "grabbed", "imported", "failed", "success_share"} and 0 <= i["success_share"] <= 1
              for i in data["indexers"]) and data["indexers"],
          f"{key}: downloads, speed, missing and the indexers' reliability", f"({status} {data})")
kometa = next((i for i in ask(everything, "/api/v1/arr/sonarr/stats?days=30")[1]["indexers"] if i["name"] == "Kometa"), {})
check(kometa.get("failed", 0) >= 1 and kometa.get("success_share", 1) < 1, "Kometa fails sometimes in Sonarr too", f"({kometa})")
status, data = ask(everything, "/api/v1/arr/bazarr/stats?days=30")
check(status == 200 and set(data) == {"days", "obtained", "automatic", "manual", "upgrades", "synced", "missing_episodes",
                                      "missing_movies", "providers", "providers_now"}
      and data["obtained"] > 0 and all(set(p) == {"name", "count", "share", "score"} and 0 <= p["share"] <= 1 for p in data["providers"])
      and any(p["throttled"] and p["reason"] for p in data["providers_now"])
      and all(set(p) == {"name", "throttled", "reason"} for p in data["providers_now"]),
      "bazarr: subtitles, missing, providers and the throttled one", f"({status} {data})")
status, data = ask(everything, "/api/v1/arr/prowlarr/stats?days=30")
check(status == 200 and set(data) == {"days", "searches", "rss", "failed_queries", "failure_share", "response_ms"}
      and data["searches"] > 0 and 0 <= data["failure_share"] <= 1, "prowlarr: queries and their failures", f"({status} {data})")
status, data = ask(everything, "/api/v1/arr/prowlarr/indexers?days=30")
indexers = {i["name"]: i for i in data.get("indexers") or []}
check(status == 200 and indexers and all(set(i) == {"id", "name", "enabled", "status_now", "availability_share", "outages",
                                                    "longest_outage_seconds", "queries", "failed_query_share"} for i in indexers.values()),
      "prowlarr: every indexer's availability", f"({status} {data})")
check(0 < (indexers.get("Kometa") or {}).get("availability_share", 1) < 1 and indexers["Kometa"]["outages"] > 0,
      "Kometa is the flaky one", f"({indexers.get('Kometa')})")
check((indexers.get("Mlynek") or {}).get("enabled") is False and indexers["Mlynek"]["status_now"] == "disabled",
      "Mlynek is switched off", f"({indexers.get('Mlynek')})")
status, data = ask(everything, "/api/v1/arr/sonarr/indexers")
check(status == 404 and data.get("error") == "not_available_for_app", "indexers only in Prowlarr")

print("--- the apps' own texts only for the administrator (as on the pages: cards_<app>.for_account) ---")
chosen = api.create_token("arr", permissions=api.pack_key_permissions({"arr"}, False, False))["token"]
admin_health = ask(everything, "/api/v1/arr/sonarr/health")[1]["health"]
reader_health = ask(chosen, "/api/v1/arr/sonarr/health")[1]["health"]
check(admin_health and all(h["message"] for h in admin_health), "a key with everything gets the health messages")
check(len(reader_health) == len(admin_health) and all(h["message"] is None and h["type"] for h in reader_health),
      "a key with chosen rights: the count and type, no message", f"({reader_health})")
# Bazarr says its health in its own shape (object, issue; the made-up one has none - a real one reports a throttled
# provider elsewhere): the API gives it the same {type, message} as the others (code review 27. 11.).
from jellyscope.arr.bazarr import client as bazarr_client, live as bazarr_live  # noqa: E402
real_health = bazarr_client.BazarrClient.health


async def _bazarr_health(self):
    return [{"object": "SonarrQX", "issue": "Sonarr is not reachable QX"}]


bazarr_client.BazarrClient.health = _bazarr_health
bazarr_live.CACHE.clear()
admin_bazarr = ask(everything, "/api/v1/arr/bazarr/health")[1]["health"]
reader_bazarr = ask(chosen, "/api/v1/arr/bazarr/health")[1]["health"]
bazarr_client.BazarrClient.health = real_health
bazarr_live.CACHE.clear()
check(len(admin_bazarr) == 1 and "Sonarr is not reachable QX" in (admin_bazarr[0]["message"] or "")
      and "SonarrQX" in admin_bazarr[0]["message"], "Bazarr's health: the administrator reads what and why", f"({admin_bazarr})")
check(len(reader_bazarr) == 1 and reader_bazarr[0]["message"] is None and "QX" not in str(reader_bazarr),
      "...the others only that there is one (paths on disk tend to be in Bazarr's texts)", f"({reader_bazarr})")
admin_errors = [e for key in ("sonarr", "radarr", "lidarr", "readarr") for e in ask(everything, f"/api/v1/arr/{key}/queue")[1]["errors"]]
reader_errors = [e for key in ("sonarr", "radarr", "lidarr", "readarr") for e in ask(chosen, f"/api/v1/arr/{key}/queue")[1]["errors"]]
check(admin_errors and any(e["message"] for e in admin_errors) and len(reader_errors) == len(admin_errors)
      and all(e["message"] is None for e in reader_errors), "the queue's error messages too", f"({reader_errors[:1]})")

print("--- the live state's memory: a second request asks the app nothing ---")
forget_live()
asked: list[str] = []
real = demo_switch._HANDLERS["Sonarr"]


def counting(request: httpx.Request) -> httpx.Response:
    asked.append(request.url.path)
    return real(request)


demo_switch.register("Sonarr", counting)
ask(everything, "/api/v1/arr/sonarr/health")
first = len(asked)
ask(everything, "/api/v1/arr/sonarr/health")
check(first > 0 and len(asked) == first, "the second request within a minute is answered from memory", f"({first} {len(asked)})")

print("--- an app that does not answer ---")


def not_answering(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("the made-up app does not answer", request=request)


demo_switch.register("Sonarr", not_answering)
forget_live()
began = time.perf_counter()
status, data = ask(everything, "/api/v1/arr/sonarr/queue")
took = time.perf_counter() - began
check(status == 200 and data.get("available") is False and data.get("reason") == "app_not_answering"
      and data.get("queue") is None and took < 4, "200 with available false, at once", f"({status} {data} {took:.1f} s)")
demo_switch.register("Sonarr", real)
forget_live()

print("--- an app that is not connected ---")
registry.APPS["radarr"].save_settings(False, "", "")
status, data = ask(everything, "/api/v1/arr/radarr/queue")
check(status == 404 and data.get("error") == "app_not_connected", "404 app_not_connected", f"({status} {data})")
check(next(r for r in ask(everything, "/api/v1/arr")[1]["apps"] if r["app"] == "radarr")["connected"] is False,
      "and the overview says it is not connected")

print("--- the rights ---")
no_arr = api.create_token("library", permissions=api.pack_key_permissions({"library"}, False, False))["token"]
check(ask(no_arr, "/api/v1/arr")[0] == 403 and ask(no_arr, "/api/v1/arr/sonarr/queue")[0] == 403, "a key without arr: 403")
reader_id = accounts.create("reader", "readerpassword")
db.set_setting(access.area_key(access.READER, access.BY_KEY["arr"]), "0")
db.forget_settings()
accounts.save_may_have_key(reader_id, "1")
reader_key = api.create_own_token("reader", reader_id)
check(ask(reader_key["token"], "/api/v1/arr/sonarr/health")[0] == 403, "a reader's own key without arr: 403")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
