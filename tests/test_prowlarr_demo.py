# -*- coding: utf-8 -*-
r"""The demo's made-up Prowlarr (arr/prowlarr/demo.py): the indexers of the demo's apps through Prowlarr's real code.

In the demo mode (JELLYSCOPE_DEMO) the seed connects Prowlarr at a made-up address and runs its real sync and sample
(demo_apps.seed_apps) against the made-up Prowlarr: the indexers the made-up apps grab from (demo_world.INDEXERS - one
of them drops out now and then, one is switched off), the queries and grabs of the same apps over the whole period,
in the shape of Prowlarr 2.7. The seed also writes the samples of the indexers' status the background loop would
have taken over that period (through the real writer), and in the demo the loop then measures on. Watched:

(a) after demodata.prepare() Prowlarr is connected at its made-up address, its sync ended well and the seed logged no
    warning;
(b) its history is full in the last 7, 30 and 365 days: RSS and searches of Sonarr, Radarr, Lidarr and Readarr (and a
    person's in Prowlarr), grabs of the library's titles; the queries to the flaky indexer fail only while it is out,
    nothing is grabbed from it then nor ever from the switched-off one, which has had no query since it was switched
    off; every card of the history has several values;
(b2) the availability: in 30 days the flaky indexer has outages and less than 100 %, the reliable ones 100 %, the
    switched-off one is shown with its days unmeasured since; measured over the whole period, every minute; the
    schedule of outages is the same answer everywhere (down_at, indexerstatus, the samples);
(b3) the samples the seed wrote: from before the year until the seed, the loop's interval, a sample for every change
    of the state measured up to the next one;
(e) the live state: the indexers, which is switched off, the apps, the warnings - and in an outage the flaky one
    left out with its times;
(c) every path the code asked during the seed and the page has an answer: no 404;
(d) the page /arr/prowlarr and each of its cards (/partials/cards) give 200 without a connection error;
(f) the answers have Prowlarr's shape: the history in pages from the newest with `totalRecords`, numbers growing with
    the time, the indexers, the apps, the warnings; no key anywhere; a write changes nothing, an unknown path is 404;
(g) a sync later on fetches only what came since, a second one nothing (no doubled rows); a refresh fetches it again;
(h) a refresh while the loop measures: a round of the loop whose write meets the refresh's deletion, and rounds
    during the refresh, leave no sample of their own in the new seed's past; after it the loop measures on;
(i) in the demo mode the app's start runs the loop (web/start.py) and it stores samples;
(j) no socket is touched during the whole test.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goals 2, 4, 5)

Run:
    .venv/bin/python tests/test_prowlarr_demo.py
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "test-key-prowlarr-demo"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_switch, demo_world, demodata, stats, web  # noqa: E402
from jellyscope.arr import prowlarr, registry  # noqa: E402
from jellyscope.arr.prowlarr import live as prowlarr_live  # noqa: E402
from jellyscope.arr.prowlarr import samples, watch  # noqa: E402
from jellyscope.arr.prowlarr import sync as prowlarr_sync  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Every way out of the process is recorded and refused - but the test's own database on PostgreSQL (tests/_network.py).
NETWORK = refuse_network()

# What the seed logs as a warning or worse (a made-up app that failed is one such line, demo_apps).
WARNINGS: list[str] = []


class _Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        WARNINGS.append(f"{record.name}: {record.getMessage()}"[:200])


_collect = _Collect(level=logging.WARNING)
logging.getLogger().addHandler(_collect)

# The made-up Prowlarr, wrapped so the test sees every request the code sends it: (method, path after /api/v1/,
# status). The registry looks the handler up only when the fakes are loaded (registry._demo).
ASKED: list[tuple[str, str, int]] = []
try:
    made_up = importlib.import_module("jellyscope.arr.prowlarr.demo")
except ImportError as error:
    made_up = None
    print(f"       (jellyscope.arr.prowlarr.demo does not import: {error})")
ORIGINAL = made_up.handler if made_up is not None else None


def _recording(request: httpx.Request) -> httpx.Response:
    response = ORIGINAL(request)
    ASKED.append((request.method, request.url.path.split("/api/v1/", 1)[-1], response.status_code))
    return response


if made_up is not None:
    made_up.handler = _recording


def ask(method: str, path: str, **params) -> httpx.Response:
    """One request straight to the made-up Prowlarr (not recorded), at the address the seed stores."""
    if ORIGINAL is None:
        return httpx.Response(599)
    return ORIGINAL(httpx.Request(method, f"http://prowlarr.demo/api/v1/{path}", params=params or None))


def answer(path: str, **params):
    response = ask("GET", path, **params)
    try:
        return response.json()
    except ValueError:
        return None


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(UTC)


def _db(text: str) -> datetime:
    return datetime.strptime(str(text)[:19], db.TIME_FORMAT).replace(tzinfo=UTC)


def whole() -> list[dict]:
    """Prowlarr's whole history, page by page as the sync reads it (newest first)."""
    records, page = [], 1
    while page < 500:
        one = answer("history", page=page, pageSize=1000, sortKey="date", sortDirection="descending") or {}
        records += one.get("records") or []
        if not one.get("records") or page * 1000 >= int(one.get("totalRecords") or 0):
            break
        page += 1
    return records


def down_at(number: int, moment: datetime) -> bool:
    return bool(made_up and made_up.down_at(number, moment))


ERRORS = ("not responding", "could not", "not answering", "Traceback")
INDEXER_IDS = {name: number for number, (name, _protocol) in enumerate(demo_world.INDEXERS, start=1)}
FLAKY, DISABLED = INDEXER_IDS[demo_world.FLAKY_INDEXER], INDEXER_IDS[demo_world.DISABLED_INDEXER]
RELIABLE = sorted(set(INDEXER_IDS.values()) - {FLAKY, DISABLED})
ROUND = 60      # the loop's interval in the demo (Settings default)

db.init_db()
demodata.prepare(quiet=True)
seed_warnings = list(WARNINGS)
seeded_at = _db(db.get_setting(demodata.SEED_KEY, "1970-01-01 00:00:00"))

print("--- (a) the seed connects the made-up Prowlarr and syncs it through the real code ---")
settings = registry.APPS["prowlarr"].stored_settings()
check(bool(settings["enabled"]) and prowlarr.is_connected(), f"Prowlarr is connected ({settings})")
check(settings["url"] == "http://prowlarr.demo", f"at its made-up address ({settings['url']!r})")
check(registry.APPS["prowlarr"].demo is not None and made_up is not None
      and registry.APPS["prowlarr"].demo() is made_up.handler, "the registry gives the package's made-up handler (App.demo)")
check(demo_switch.registered(prowlarr.NAME), "the made-up Prowlarr is registered in the switch")
state = prowlarr.sync_state()
check(state["result"] == "ok" and not state["error"], f"its sync ended well ({state})")
check(not seed_warnings, f"the seed logged no warning ({seed_warnings[:3]})")

print()
print("--- (b) a history over the whole period, like a real one ---")
history = whole()
now = datetime.now(UTC)
dates = [_utc(r["date"]) for r in history]
check(len(history) > 20_000 and prowlarr.event_count() == len(history),
      f"the history is downloaded, every record counted ({len(history)}, {prowlarr.event_count()})")
check(bool(dates) and max(dates) <= now and min(dates) <= now - timedelta(days=366),
      f"from more than a year ago up to now ({min(dates, default=None)} - {max(dates, default=None)})")
check({r["eventType"] for r in history} == {"indexerRss", "indexerQuery", "releaseGrabbed"}, "RSS, searches and grabs")
sources = {r["data"]["source"] for r in history}
check(sources == {"Sonarr", "Radarr", "Lidarr", "Readarr", "Prowlarr"}, f"the demo's apps and a person in Prowlarr ({sorted(sources)})")
check({r["indexerId"] for r in history} == set(INDEXER_IDS.values()), "every indexer was queried")
grabs = [r for r in history if r["eventType"] == "releaseGrabbed"]
queries = [r for r in history if r["eventType"] != "releaseGrabbed"]
check(grabs and not [r for r in grabs if r["indexerId"] == DISABLED], f"nothing ever grabbed from {demo_world.DISABLED_INDEXER}")
failed = [r for r in queries if not r["successful"]]
check(failed and {r["indexerId"] for r in failed} == {FLAKY}, f"only {demo_world.FLAKY_INDEXER}'s queries fail ({len(failed)})")
check(all(down_at(FLAKY, _utc(r["date"])) for r in failed), "and only while it is out")
check(not [r for r in history if r["indexerId"] == FLAKY and r["successful"] and down_at(FLAKY, _utc(r["date"]))],
      "while it is out Prowlarr leaves it out: no answered query, no grab")
disabled_last = max((_utc(r["date"]) for r in queries if r["indexerId"] == DISABLED), default=now)
check(now - timedelta(days=30) < disabled_last < now - timedelta(days=7),
      f"{demo_world.DISABLED_INDEXER} was switched off within the last month, more than a week ago ({disabled_last})")
library = {s["title"] for s in demo_world.series()} | {m["title"] for m in demo_world.movies()}
library |= {a["title"] for a in demo_world.albums()} | {b["title"] for b in demo_world.books()}
words = {"".join(c for c in title if c.isalnum()).lower() for title in library}
grab_titles = {"".join(c for c in r["data"]["grabTitle"] if c.isalnum()).lower() for r in grabs}
check(sum(1 for word in words if any(word in title for title in grab_titles)) >= len(words) // 3,
      "the grabs are of the library's titles")
for days, minimum in ((7, 6), (30, 28), (365, 360)):
    analysis = stats.prowlarr_analysis(days)
    numbers = stats.prowlarr_numbers(days, analysis=analysis)
    by_day = stats.prowlarr_by_day(days, analysis=analysis)
    heatmap = stats.prowlarr_heatmap(days, analysis=analysis)
    who = stats.prowlarr_who(days, analysis=analysis)
    indexers = stats.prowlarr_indexers(days, analysis=analysis)
    grabbed = stats.prowlarr_grabbed(days, analysis=analysis)
    by_indexer = stats.prowlarr_grabbed_indexers(days, analysis=analysis)
    latest = stats.prowlarr_grabbed_latest(days, 10, analysis=analysis)
    check(numbers["searches"] > 0 and numbers["rss"] > numbers["searches"] and numbers["response"],
          f"{days} days: the numbers ({numbers})")
    check(len(by_day) >= minimum, f"{days} days: queries on every day ({len(by_day)})")
    check(len({h for row in heatmap for h, n in enumerate(row) if n}) >= 6, f"{days} days: searches at several hours")
    check(len([r for r in who if r["searches"]]) >= 3 and len([r for r in who if r["rss"]]) >= 4,
          f"{days} days: several apps search and read RSS ({[(r['app'], r['searches'], r['rss']) for r in who]})")
    check(len([r for r in indexers if r["response"]]) >= 4, f"{days} days: the indexers' response times")
    check(grabbed["grab_count"] >= (3 if days == 7 else 10) and len(by_indexer) >= 2 and latest,
          f"{days} days: grabbed releases from several indexers ({grabbed['grab_count']}, {len(by_indexer)})")
numbers_30 = stats.prowlarr_numbers(30)
check(numbers_30["failed_count"] > 0, f"30 days: failed queries ({numbers_30['failed_count']})")
year_grabs = stats.prowlarr_grabbed(365)
check(year_grabs["failed_count"] >= 1 and year_grabs["being_grabbed"] > 0 and year_grabs["redirected"] > 0,
      f"the year: a failed download, releases Prowlarr fetched and redirected ones ({year_grabs})")

print()
print("--- (b2) the indexers' availability ---")
for days in (7, 30, 365):
    found = stats.prowlarr_availability(days)
    rows = {r["indexer_id"]: r for r in found["indexers"]}
    flaky = rows.get(FLAKY, {})
    check(flaky.get("outages", 0) >= 1 and (flaky.get("availability") or 1) < 1,
          f"{days} days: {demo_world.FLAKY_INDEXER} has outages and less than 100 % ({flaky.get('outages')}, {flaky.get('availability')})")
    check(all(rows.get(n, {}).get("availability") == 1 and not rows.get(n, {}).get("outages") for n in RELIABLE),
          f"{days} days: the reliable ones 100 % ({[(n, rows.get(n, {}).get('availability')) for n in RELIABLE]})")
    check(found["measured_since"] == "" and found["first_sample"] and found["intervals"] == [ROUND],
          f"{days} days: measured over the whole period, every minute ({found['measured_since']!r}, {found['intervals']})")
found = stats.prowlarr_availability(30)
rows = {r["indexer_id"]: r for r in found["indexers"]}
off = rows.get(DISABLED, {})
strip = off.get("days") or []
check(bool(strip) and strip[-1] == stats.prowlarr.UNMEASURED and stats.prowlarr.FINE in strip,
      f"30 days: the switched-off one is there, measured until it was switched off ({strip[-3:]})")
check(off.get("availability") == 1 and DISABLED not in {r["indexer_id"] for r in stats.prowlarr_availability(7)["indexers"]},
      "it was fine while it was on; in 7 days it is not listed (no query, not on)")
flaky_30 = rows.get(FLAKY, {})
check(stats.prowlarr.OUTAGE in (flaky_30.get("days") or []) and 0.95 < (flaky_30.get("availability") or 0) < 1,
      f"the flaky one's outage days in the strip, about 99 % ({flaky_30.get('availability')})")
week = [made_up.outages(now + timedelta(days=7 * k), now + timedelta(days=7 * (k + 1))) for k in range(8)] if made_up else []
lengths = [(b - a).total_seconds() / 60 for one in week for a, b in one]
check(week and all(2 <= len(one) <= 5 for one in week) and lengths and all(20 <= m <= 40 for m in lengths),
      f"the schedule: a few outages of 20-40 minutes every week ({[len(one) for one in week]}, {min(lengths, default=0):.0f}-{max(lengths, default=0):.0f} min)")
sample_outage = week[0][0] if week and week[0] else (now, now)
inside = sample_outage[0] + timedelta(minutes=10)
check(down_at(FLAKY, inside) and not down_at(FLAKY, sample_outage[1]) and not down_at(FLAKY, sample_outage[0] - timedelta(seconds=1))
      and not any(down_at(n, inside) for n in (*RELIABLE, DISABLED)), "down_at: only the flaky one, only inside its outage")

print()
print("--- (b3) the samples the seed wrote: the loop's measuring over the past ---")
own = db.query_all("SELECT sampled_at, enabled_ids, interval_seconds, measured_until FROM prowlarr_sample"
                   " WHERE indexer_id = 0 ORDER BY sampled_at")
left_out = db.query_all("SELECT sampled_at, indexer_id, failing_since FROM prowlarr_sample WHERE indexer_id <> 0 ORDER BY sampled_at")
check(len(own) >= 150 and _db(own[0]["sampled_at"]) <= seeded_at - timedelta(days=366),
      f"from before the year ({len(own)} samples, the first {own[0]['sampled_at'] if own else None})")
check(all(r["interval_seconds"] == ROUND and r["measured_until"] and r["measured_until"] >= r["sampled_at"] for r in own),
      "each with the loop's interval and its last round")
steps = [(_db(b["sampled_at"]) - _db(a["measured_until"])).total_seconds() for a, b in zip(own, own[1:])]
check(steps and max(steps) <= ROUND * 1.5 and min(steps) > 0, f"each measured up to one round before the next ({min(steps, default=0)}-{max(steps, default=0)} s)")
check(_db(own[-1]["measured_until"]) >= seeded_at - timedelta(minutes=2), f"up to the seed ({own[-1]['measured_until'] if own else None})")
check(left_out and {r["indexer_id"] for r in left_out} == {FLAKY}
      and all(r["failing_since"] == r["sampled_at"] and down_at(FLAKY, _db(r["sampled_at"])) for r in left_out),
      f"a sample at the start of every outage of the flaky one ({len(left_out)})")
outage_starts = {a for a, _b in made_up.outages(_db(own[0]["sampled_at"]), seeded_at)} if made_up and own else set()
check({_db(r["sampled_at"]) for r in left_out} == {a for a in outage_starts if a > _db(own[0]["sampled_at"])},
      "exactly the schedule's outages")
lists = [r["enabled_ids"] for r in own]
switch = next((k for k, (a, b) in enumerate(zip(lists, lists[1:])) if a != b), None)
check(switch is not None and lists[0] == samples.id_list(INDEXER_IDS.values()) and lists[-1] == samples.id_list(RELIABLE + [FLAKY])
      and len(set(lists)) == 2 and disabled_last < _db(own[switch + 1]["sampled_at"]),
      "the switched-off one leaves the list once, after its last query")

print()
print("--- (e) the live state ---")
prowlarr_live.CACHE.clear()
live = asyncio.run(prowlarr.live_state())
by_name = {r["name"]: r for r in live["indexers"] or []}
check(live["available"] and not live["error"] and set(by_name) == set(INDEXER_IDS), f"the live state: every indexer ({sorted(by_name)})")
check(live["enabled_count"] == 4 and not by_name.get(demo_world.DISABLED_INDEXER, {}).get("enabled", True),
      f"four on, {demo_world.DISABLED_INDEXER} switched off ({live['enabled_count']})")
check(live["app_count"] == 4 and (live["warnings"] or 0) >= 1, f"four apps, a harmless warning ({live['app_count']}, {live['health']})")
real_now, real_utcnow = demo_world.now, db.utcnow
last_outage = made_up.outages(now - timedelta(days=4), now)[-1] if made_up else (now, now)
moment = last_outage[0] + timedelta(minutes=7)
demo_world.now = lambda: moment
db.utcnow = lambda: moment.strftime(db.TIME_FORMAT)
prowlarr_live.CACHE.clear()
in_outage = asyncio.run(prowlarr.live_state())
sample = asyncio.run(prowlarr_live.status_sample(now=moment.strftime(db.TIME_FORMAT)))
demo_world.now, db.utcnow = real_now, real_utcnow
prowlarr_live.CACHE.clear()
flaky_now = {r["name"]: r for r in in_outage["indexers"] or []}.get(demo_world.FLAKY_INDEXER, {})
check(flaky_now.get("blocked_until") and _db(flaky_now["blocked_until"]) > moment
      and flaky_now.get("failing_since") == last_outage[0].strftime(db.TIME_FORMAT) and in_outage["unavailable_count"] == 1,
      f"in an outage the flaky one is left out, failing since its start ({flaky_now})")
check(any(demo_world.FLAKY_INDEXER in h["message"] for h in in_outage["health"] or []), "and Prowlarr's warning names it")
check(sample is not None and [row[0] for row in sample[1]] == [FLAKY] and DISABLED not in sample[2],
      f"the loop's sample then: the flaky one left out, the switched-off one not measured ({sample})")

print()
print("--- (d) the page and every card, without a connection error ---")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
page = client.get("/arr/prowlarr")
check(page.status_code == 200, f"/arr/prowlarr gives 200 ({page.status_code})")
check(not [e for e in ERRORS if e in page.text], f"the page has no connection error ({[e for e in ERRORS if e in page.text]})")


def card(key: str, **parameters) -> tuple[int, str]:
    response = client.get("/partials/cards?" + urlencode({"keys": key, **parameters}), headers={"X-Requested-With": "fetch"},
                          follow_redirects=False)
    return response.status_code, response.text


broken = []
for one in registry.APPS["prowlarr"].cards():
    for days in (7, 30):
        status, text = card(one.key, days=days)
        errors = [e for e in ERRORS if e in text]
        if status != 200 or f'data-card-key="{one.key}"' not in text or errors:
            broken.append(f"{one.key} ({days}): {status} {errors}")
check(not broken, f"each of the {len(registry.APPS['prowlarr'].cards())} cards gives 200 without a connection error ({broken[:4]})")
status, text = card("prowlarr_availability", days=30)
check(status == 200 and all(name in text for name in INDEXER_IDS) and "measured since" not in text and "No data yet" not in text,
      "the availability card shows every indexer, measured over the whole period")
status, text = card("prowlarr_indexers_now")
check(status == 200 and "disabled manually" in text and demo_world.DISABLED_INDEXER in text,
      "the indexers now: the switched-off one marked")

print()
print("--- (f) the answers in Prowlarr's shape ---")
system = answer("system/status") or {}
check(system.get("appName") == "Prowlarr" and str(system.get("version", "")).startswith("2."), f"system/status: Prowlarr 2 ({system.get('version')})")
listed = answer("indexer") or []
check([(r.get("id"), r.get("name"), r.get("protocol")) for r in listed]
      == [(n, name, protocol) for n, (name, protocol) in enumerate(demo_world.INDEXERS, start=1)]
      and [r["enable"] for r in listed] == [n != DISABLED for n in INDEXER_IDS.values()] and all(r.get("privacy") for r in listed),
      "indexer: demo_world's indexers in Prowlarr's order, one switched off")
apps = answer("applications") or []
check(sorted(r.get("name") for r in apps) == ["Lidarr", "Radarr", "Readarr", "Sonarr"] and all(r.get("syncLevel") for r in apps),
      "applications: the four apps")
check(isinstance(answer("indexerstatus"), list) and all({"source", "type", "message"} <= set(h) for h in answer("health") or []),
      "indexerstatus and health: lists of Prowlarr's shape")
texts = "".join(ask("GET", path).text for path in ("indexer", "applications", "health", "system/status"))
check("apiKey" not in texts and "apikey" not in texts.lower(), "no key anywhere")
first_page = answer("history", page=1, pageSize=50, sortKey="date", sortDirection="descending") or {}
oldest = answer("history", page=1, pageSize=50, sortKey="date", sortDirection="ascending") or {}
past_end = answer("history", page=10_000, pageSize=50) or {}
check(first_page.get("totalRecords") == len(history) and len(first_page.get("records") or []) == 50
      and past_end.get("records") == [] and (oldest.get("records") or [{}])[0].get("id") == history[-1]["id"],
      "history: in pages from the newest, `totalRecords`, ascending from the oldest, empty past the end")
ordered = sorted(history, key=lambda r: (r["date"], r["id"]))
check([r["id"] for r in ordered] == sorted(r["id"] for r in history) and len({r["id"] for r in history}) == len(history),
      "the numbers grow with the time, none twice")
before = answer("history", page=1, pageSize=20)
check(ask("POST", "command", name="ApplicationIndexerSync").status_code == 201 and answer("history", page=1, pageSize=20) == before,
      "a command is accepted and changes nothing")
check(ask("GET", "no/such/path").status_code == 404 and ask("DELETE", "indexer/3").status_code == 404, "unknown paths are 404")

print()
print("--- (c) every path the code asked has an answer ---")
paths = {path for _method, path, _status in ASKED}
missing_paths = {"indexer", "history", "indexerstatus", "applications", "health"} - paths
check(not missing_paths, f"the seed and the page asked what a real Prowlarr is asked ({sorted(missing_paths)})")
check(all(status < 400 for _method, _path, status in ASKED),
      f"every answer fine, no 404 ({sorted({(p, s) for _m, p, s in ASKED if s >= 400})[:4]})")

print()
print("--- (g) later syncs fetch only what came since; a refresh fetches the same again ---")
counted = prowlarr.event_count()
again = asyncio.run(prowlarr.synchronize())
check(again["new_count"] == 0 and prowlarr.event_count() == counted, f"a second sync adds nothing ({again})")
base = datetime.now(UTC).replace(microsecond=0)
demo_world.now = lambda: base + timedelta(hours=20)
later = len(whole())
later_sync = asyncio.run(prowlarr.synchronize())
demo_world.now = real_now
check(later > counted and later_sync["new_count"] == later - counted and prowlarr.event_count() == later,
      f"a sync 20 hours later adds exactly what came since ({counted} -> {later}, {later_sync})")
WARNINGS.clear()
demodata.refresh()
check(abs(prowlarr.event_count() - counted) <= 40 and prowlarr.sync_state()["result"] == "ok",
      f"about the same history after the refresh ({counted} -> {prowlarr.event_count()})")
check(not WARNINGS, f"the refresh logged no warning ({WARNINGS[:3]})")


def own_samples() -> list[dict]:
    return db.query_all("SELECT sampled_at, measured_until FROM prowlarr_sample WHERE indexer_id = 0 ORDER BY sampled_at")


def seeded_past() -> tuple[datetime, list[str]]:
    seeded = _db(db.get_setting(demodata.SEED_KEY, ""))
    return seeded, [r["sampled_at"] for r in own_samples() if _db(r["sampled_at"]) < seeded - timedelta(minutes=1)]


clean_seed, clean_past = seeded_past()

print()
print("--- (h) a refresh while the loop measures ---")
chain = watch.Chain()
asyncio.run(watch.watch_once(chain))
check(bool(chain.sampled_at), f"the loop measures on after the seed ({chain.sampled_at!r})")
wiped, refreshed, outcome = threading.Event(), threading.Event(), {}
real_delete, real_extend, real_store = prowlarr_sync._delete, watch.extend_watched, watch.store_watched
late_stores: list[tuple[str, bool]] = []


def deleting() -> int:
    count = real_delete()
    wiped.set()
    return count


def refreshing() -> None:
    try:
        outcome["counts"] = demodata.refresh()
    except Exception as error:   # noqa: BLE001 - the test reports it
        outcome["error"] = repr(error)
    refreshed.set()


def late_extend(sampled_at: str, moment: str) -> str:
    """The loop's round got Prowlarr's answer just before the refresh began; its write lands after the deletion."""
    threading.Thread(target=refreshing).start()
    wiped.wait(30)
    return real_extend(sampled_at, moment)


def late_store(*args) -> bool:
    stored = real_store(*args)
    late_stores.append((args[0], stored))
    return stored


prowlarr_sync._delete, watch.extend_watched, watch.store_watched = deleting, late_extend, late_store
chain.last_round = chain.sampled_at = (datetime.now(UTC) - timedelta(seconds=30)).strftime(db.TIME_FORMAT)
round_at = datetime.now(UTC).strftime(db.TIME_FORMAT)
asyncio.run(watch.watch_once(chain, now=round_at))
watch.extend_watched, watch.store_watched = real_extend, real_store
# A round wholly inside the refresh (the flag set before and after it) is not answered; one at its end may be - the
# loop measuring on after the refresh.
rounds, unanswered, deadline = 0, 0, time.monotonic() + 60
while not refreshed.is_set() and time.monotonic() < deadline:
    inside = demo_world.REFRESHING.is_set()
    asyncio.run(watch.watch_once(chain))
    if inside and demo_world.REFRESHING.is_set():
        rounds += 1
        unanswered += samples.LOOP["answered"] is False
    time.sleep(0.01)
refreshed.wait(60)
prowlarr_sync._delete = real_delete
check("counts" in outcome and wiped.is_set(), f"the refresh ended ({outcome.get('error', '')})")
check(late_stores == [(round_at, True)], f"the late round found its sample deleted and stored a new one, inside the refresh ({late_stores})")
check(rounds > 0 and unanswered == rounds, f"during the refresh Prowlarr does not tell the loop the status ({unanswered} of {rounds} rounds)")
new_seed, new_past = seeded_past()
check(new_seed > clean_seed and round_at <= new_seed.strftime(db.TIME_FORMAT), "the late round's time is not newer than the new seed")
check(round_at not in {r["sampled_at"] for r in own_samples()}, "the late round's sample was not kept")
expected = [moment.strftime(db.TIME_FORMAT) for moment, _rows, _enabled in made_up.past_states(new_seed)] if made_up else []
check(new_past == [t for t in expected if _db(t) < new_seed - timedelta(minutes=1)] and len(set(new_past)) == len(new_past),
      f"the new seed's past is exactly its schedule, nothing doubled ({len(new_past)} of {len(expected)})")
check(not demo_world.REFRESHING.is_set(), "the flag of the refresh is down again")
time.sleep(1.1)                  # a new chain's sample in a second of its own
chain = watch.Chain()
asyncio.run(watch.watch_once(chain))
newest = own_samples()[-1]["sampled_at"] if own_samples() else ""
check(bool(chain.sampled_at) and newest == chain.sampled_at and _db(newest) >= new_seed, f"after it the loop measures on ({newest})")

print()
print("--- (i) in the demo mode the app's start runs the loop ---")
start_mark = datetime.now(UTC).strftime(db.TIME_FORMAT)
running_inside, stored_inside = 0, False
with TestClient(web.app):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        running_inside = samples.LOOP["running"]
        stored_inside = bool(db.query_value("SELECT COUNT(*) FROM prowlarr_sample WHERE indexer_id = 0 AND sampled_at >= ?",
                                            (start_mark,)))
        if running_inside and stored_inside:
            break
        time.sleep(0.1)
check(running_inside == 1 and stored_inside, f"the loop runs and stores its samples ({running_inside}, {stored_inside})")
check(samples.LOOP["running"] == 0, "the shutdown stops it")

print()
print("--- (j) no network ---")
check(not NETWORK, f"no socket was touched ({NETWORK[:3]})")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(demo_switch.transport(prowlarr.NAME) is None, "outside the demo mode the switch gives Prowlarr's client the network")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
