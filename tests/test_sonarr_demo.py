# -*- coding: utf-8 -*-
r"""The demo's made-up Sonarr (arr/sonarr/demo.py): the demo's series through Sonarr's real code.

In the demo mode (JELLYSCOPE_DEMO) the seed connects Sonarr at a made-up address and runs its real sync and
counts (demo_apps.seed_apps) against the made-up Sonarr, which answers from the demo's library
(demo_world.series) in the shape of Sonarr v4. The page and its live cards then ask it the same way they ask a
real one. Watched:

(a) after demodata.prepare() Sonarr is connected at its made-up address, its sync ended well and the seed logged
    no warning;
(b) its history has imports in the last 7, 30 and 365 days, upgrades, one or two failed downloads, a spread of
    the time from release to import, its indexers and clients; every series is paired with the library; the
    counts of missing and cutoff are not zero;
(c) every path the code asked during the seed and the page has an answer: no 404 but images;
(d) the page /arr/sonarr, its slice "Downloading" and each of its cards (/partials/cards) give 200 without a
    connection error;
(e) the card "Downloading" differs between a moment and ten minutes later (demo_world.now substituted);
(f) the answers have Sonarr's shape: the history in pages oldest first and since a date, the queue (2-4
    downloads, one with a warning), the calendar for 14 days, the health warnings, a POST command accepted
    without a change, posters and unknown paths 404;
(g) a refresh (demodata.refresh) deletes Sonarr's data and fetches the same again;
(h) no socket is touched during the whole test.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 2)

Run:
    .venv/bin/python tests/test_sonarr_demo.py
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import tempfile
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
os.environ["SECRET_KEY"] = "test-key-sonarr-demo"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_apps, demo_switch, demo_world, demodata, services, stats, web  # noqa: E402
from jellyscope.arr import registry, sonarr  # noqa: E402

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

# The made-up Sonarr, wrapped so the test sees every request the code sends it: (method, path after /api/v3/, status).
# The registry looks the handler up only when the fakes are loaded (registry._demo), so the wrapper is what gets
# registered.
ASKED: list[tuple[str, str, int]] = []
try:
    made_up = importlib.import_module("jellyscope.arr.sonarr.demo")
except ImportError as error:
    made_up = None
    print(f"       (jellyscope.arr.sonarr.demo does not import: {error})")
ORIGINAL = made_up.handler if made_up is not None else None


def _recording(request: httpx.Request) -> httpx.Response:
    response = ORIGINAL(request)
    ASKED.append((request.method, request.url.path.split("/api/v3/", 1)[-1], response.status_code))
    return response


if made_up is not None:
    made_up.handler = _recording


def ask(method: str, path: str, body: dict | None = None, **params) -> httpx.Response:
    """One request straight to the made-up Sonarr (not recorded), at the address the seed stores."""
    if ORIGINAL is None:
        return httpx.Response(599)
    return ORIGINAL(httpx.Request(method, f"http://sonarr.demo/api/v3/{path}", params=params or None, json=body))


def answer(method: str, path: str, **params):
    response = ask(method, path, **params)
    try:
        return response.json()
    except ValueError:
        return None


def moment(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


ERRORS = ("not responding", "could not", "not answering", "Traceback")

db.init_db()
demodata.prepare(quiet=True)
series = demo_world.series()
titles = {entry["title"] for entry in series}
seed_warnings = list(WARNINGS)

print("--- (a) the seed connects the made-up Sonarr and syncs it through the real code ---")
settings = registry.APPS["sonarr"].stored_settings()
check(bool(settings["enabled"]) and sonarr.is_connected(), f"Sonarr is connected ({settings})")
check(settings["url"] == "http://sonarr.demo", f"at its made-up address ({settings['url']!r})")
check(registry.APPS["sonarr"].demo is not None and made_up is not None
      and registry.APPS["sonarr"].demo() is made_up.handler, "the registry gives the package's made-up handler (App.demo)")
check(demo_switch.registered(sonarr.NAME), "the made-up Sonarr is registered in the switch")
state = sonarr.sync_state()
check(state["result"] == "ok" and not state["error"], f"its sync ended well ({state})")
check(not seed_warnings, f"the seed logged no warning ({seed_warnings[:3]})")

print()
print("--- (b) a history over the whole period, like a real one ---")
check(sonarr.event_count() > 100, f"the history is downloaded ({sonarr.event_count()} records)")
for days in (7, 30, 365):
    imports = sum(row["count"] for row in stats.sonarr_additions(days))
    check(imports > 0, f"imports in the last {days} days ({imports})")
upgrades = stats.sonarr_upgrades(365)
check(upgrades["count"] >= 3 and stats.sonarr_upgrades(30)["count"] > 0,
      f"upgrades in the year and in the last 30 days ({upgrades['count']}, {stats.sonarr_upgrades(30)['count']})")
check(all(row["to"] != "unknown" for row in upgrades["transitions"]), f"every upgrade pairs with its import ({upgrades['transitions'][:4]})")
failed = stats.sonarr_failures(365)
check(1 <= failed <= 2, f"one or two failed downloads in the year ({failed})")
speed = stats.sonarr_speed(365)
buckets = speed["since_release"]["buckets"]
check(speed["downloads"]["count"] > 20 and speed["downloads"]["median"] != speed["downloads"]["p90"],
      f"download times with a spread ({speed['downloads']})")
check(speed["since_release"]["count"] > 20 and sum(1 for n in buckets.values() if n) >= 4,
      f"the time from release to import spread over the buckets ({buckets})")
check(speed["since_release"]["not_counted"] > 0, f"a series added after its episodes aired is not counted ({speed['since_release']['not_counted']})")
reliability = {row["name"]: row for row in stats.sonarr_indexer_reliability(365)}
flaky = reliability.get(demo_world.FLAKY_INDEXER, {})
check(set(reliability) <= {name for name, _protocol in demo_world.INDEXERS} - {demo_world.DISABLED_INDEXER}
      and len(reliability) >= 3, f"grabs from the made-up indexers, none from the switched-off one ({sorted(reliability)})")
check(flaky.get("failed", 0) >= 1 and flaky.get("success") is not None and flaky["success"] < 1,
      f"the flaky indexer has the failures ({flaky})")
check(not sum(row["unmatched"] for row in reliability.values()), "every grab carries its download id")
sources = stats.sonarr_sources(365)
check({row["name"] for row in sources["clients"]} == set(demo_world.DOWNLOAD_CLIENTS.values())
      and {row["name"] for row in sources["protocols"]} == {"usenet", "torrent"},
      f"both clients and protocols ({sources['clients']}, {sources['protocols']})")
check(len(sources["qualities"]) >= 4 and len(sources["groups"]) >= 4, "several qualities and release groups")
stored_titles = {row["title"] for row in db.query_all("SELECT DISTINCT title FROM sonarr_event")}
check(stored_titles == titles, f"the history names the demo's series ({sorted(stored_titles)})")
library = stats.sonarr_library()
check(sorted(library) == [entry["number"] for entry in series]
      and all(library[entry["number"]]["detail_url"] == f"/series/{entry['jellyfin_id']}" for entry in series),
      f"every series is paired with the library by its ids ({sorted(library)})")
counts = services.stored_counts("arr_sonarr", services.settings_fingerprint("http://sonarr.demo", demo_apps.api_key("sonarr")))
check((counts["missing"] or 0) > 0 and (counts["cutoff"] or 0) > 0, f"the counts of missing and cutoff are not zero ({counts})")

print()
print("--- (b2) the recent weeks: series airing now, in the library and in Sonarr ---")
airing = {int(key.rsplit("-", 1)[1]) + 1 for key in getattr(demo_world, "AIRING", {})}
by_number = {entry["number"]: entry for entry in series}
new_title = (getattr(demodata, "NEW_SERIES", None) or ["?"])[0]
new_number = len(demodata.SERIES) + 1
check(by_number.get(new_number, {}).get("title") == new_title and new_number in airing,
      f"the new series {new_title!r} is series {new_number} of the catalog and airs ({sorted(airing)})")
check(len(airing) >= 4, f"at least four series air now ({sorted(airing)})")
now = datetime.now(UTC)
recent: dict[int, list[dict]] = {}
for number in sorted(airing):
    for episodes in by_number.get(number, {}).get("seasons", {}).values():
        for episode in episodes:
            when = demo_world.aired(number, episode["season"], episode["episode"])
            if when is not None:
                recent.setdefault(number, []).append({**episode, "aired": when})
new_episodes = recent.get(new_number, [])
check(len(new_episodes) >= 8 and min((e["added_at"] for e in new_episodes), default=now) >= now - timedelta(weeks=11),
      f"the new series has its weekly episodes of the last ~10 weeks in the library ({len(new_episodes)})")
check(all(len(recent.get(number, [])) >= 3 for number in airing), f"every airing series has its latest episodes ({ {n: len(e) for n, e in recent.items()} })")
check(all(e["aired"] < e["added_at"] <= now for episodes in recent.values() for e in episodes),
      "every one of them is in the library after it aired")
weekly = [sorted(e["aired"] for e in episodes) for episodes in recent.values()]
check(all(all(b - a == timedelta(weeks=1) for a, b in zip(days, days[1:])) for days in weekly), "one episode a week")
imports = {row["item_id"]: row for row in db.query_all(
    "SELECT item_id, MIN(happened_at) AS happened_at, MIN(released_at) AS released_at FROM sonarr_event"
    " WHERE event_type LIKE ? GROUP BY item_id", ("%FolderImported",))}


def _utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


out_of_order = [e["number"] for episodes in recent.values() for e in episodes
                if e["number"] not in imports or not (e["aired"] < _utc(imports[e["number"]]["happened_at"]) <= e["added_at"])
                or _utc(imports[e["number"]]["released_at"]) != e["aired"]]
check(not out_of_order, f"Sonarr imported each after it aired and before Jellyfin took it in, with its air date ({out_of_order[:5]})")
airing_titles = {by_number[number]["title"] for number in airing}
for days in (7, 30):
    analysis = stats.sonarr_events(days)
    waited = [i for i in analysis["imports"] if i["arr_id"] not in analysis["upgrade_imports"] and i["released_at"]
              and i["title"] in airing_titles]
    since_release = stats.sonarr_speed(days)["since_release"]
    filled = sum(1 for n in since_release["buckets"].values() if n)
    check(len(waited) >= 3 and since_release["count"] >= 3 and filled >= 3,
          f"{days} days: how soon after release has values of the airing series in several buckets"
          f" ({len(waited)} of {since_release['count']}, {since_release['buckets']})")
    check(stats.sonarr_upgrades(days)["count"] >= 1, f"{days} days: an upgrade ({stats.sonarr_upgrades(days)['count']})")
check(stats.sonarr_speed(30)["since_release"]["buckets"]["longer"] >= 1, "30 days: one episode came late")
calendar_ahead = answer("GET", "calendar", start=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        end=(now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"), includeSeries="true") or []
check(all(sum(1 for c in calendar_ahead if c["seriesId"] == number) == 2 for number in airing),
      "the calendar has the next two episodes of every airing series")
check(all(not any(c["id"] == e["number"] for c in calendar_ahead) for episodes in recent.values() for e in episodes),
      "and none of those the library has")
viewed = db.query_all("SELECT p.started_at, i.date_created FROM playback p JOIN items i ON i.id = p.item_id"
                      " WHERE i.series_name = ?", (new_title,))
check(len(viewed) >= 5 and all(p["started_at"] >= p["date_created"].replace("T", " ")[:19] for p in viewed),
      f"the new series is watched, each play after the episode came ({len(viewed)})")

print()
print("--- (d) the page and every card, without a connection error ---")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
page = client.get("/arr/sonarr")
check(page.status_code == 200, f"/arr/sonarr gives 200 ({page.status_code})")
check(not [e for e in ERRORS if e in page.text], f"the page has no connection error ({[e for e in ERRORS if e in page.text]})")
check(any(title in page.text for title in titles), "the page shows the demo's series")
# The health follows the flaky indexer's outages (demo_world's schedule, the one Prowlarr leaves it out by): the page
# (the demo's account is an admin: it sees the messages) in the last outage that ended and an hour after it.
real_now = demo_world.now
UNAVAILABLE = f"Indexers unavailable due to failures: {demo_world.FLAKY_INDEXER}"
ended = [(datetime.fromtimestamp(a, UTC), datetime.fromtimestamp(b, UTC)) for a, b in demo_world.flaky_outages(
    int((datetime.now(UTC) - timedelta(days=4)).timestamp()), int(datetime.now(UTC).timestamp()))
    if b + 3600 < datetime.now(UTC).timestamp()]
outage = ended[-1] if ended else (real_now(), real_now())
for label, at, out in (("in an outage", outage[0] + timedelta(minutes=5), True), ("an hour after it", outage[1] + timedelta(hours=1), False)):
    demo_world.now = lambda at=at: at
    sonarr.live.CACHE.clear()
    text = client.get("/arr/sonarr").text
    check(bool(ended) and "New update is available: " in text and (UNAVAILABLE in text) == out,
          f"{label} the page's health has an update{' and the flaky indexer' if out else ' and not the flaky indexer'}")
demo_world.now = real_now
sonarr.live.CACHE.clear()


def card(key: str, **parameters) -> tuple[int, str]:
    response = client.get("/partials/cards?" + urlencode({"keys": key, **parameters}), headers={"X-Requested-With": "fetch"},
                          follow_redirects=False)
    return response.status_code, response.text


broken = []
for one in registry.APPS["sonarr"].cards():
    status, text = card(one.key, days=30)
    errors = [e for e in ERRORS if e in text]
    if status != 200 or f'data-card-key="{one.key}"' not in text or errors:
        broken.append(f"{one.key}: {status} {errors}")
check(not broken, f"each of the {len(registry.APPS['sonarr'].cards())} cards gives 200 without a connection error ({broken[:4]})")
piece = client.get("/partials/arr/sonarr/downloading")
check(piece.status_code == 200 and "session-bar" in piece.text and not [e for e in ERRORS if e in piece.text],
      f"the slice of the card Downloading gives 200 with progress ({piece.status_code})")

print()
print("--- (e) the card Downloading moves with the clock ---")
real_now = demo_world.now
base = datetime.now(UTC).replace(microsecond=0)


def downloading_at(at: datetime) -> str:
    demo_world.now = lambda: at
    sonarr.progress._memory = None      # the card reads the queue again
    return card("arr_sonarr_downloading")[1]


first, later = downloading_at(base), downloading_at(base + timedelta(minutes=10))
check("session-bar" in first and "session-bar" in later, "the card shows downloads at both moments")
check(first != later, "the card differs ten minutes later")
check(downloading_at(base) == first, "the same moment gives the same card")
demo_world.now = real_now

print()
print("--- (f) the answers in Sonarr's shape ---")
status = answer("GET", "system/status") or {}
check(status.get("appName") == "Sonarr" and str(status.get("version", "")).startswith("4."), f"system/status: Sonarr 4 ({status.get('version')})")
pages, page_number = [], 1
while page_number < 50:
    one_page = answer("GET", "history", page=page_number, pageSize=50, sortKey="date", sortDirection="ascending",
                      includeSeries="true", includeEpisode="true") or {}
    pages.append(one_page)
    if not one_page.get("records") or page_number * 50 >= int(one_page.get("totalRecords") or 0):
        break
    page_number += 1
history = [record for one_page in pages for record in one_page.get("records") or []]
dates = [record.get("date", "") for record in history]
check(bool(history) and len(history) == pages[0].get("totalRecords") == sonarr.event_count(),
      f"the history in pages: every record once, as many as the sync stored ({len(history)}, {pages[0].get('totalRecords')})")
check(dates == sorted(dates) and len({record.get("id") for record in history}) == len(history), "oldest first, every id its own")
check(all(isinstance(record.get("series"), dict) and isinstance(record.get("episode"), dict) for record in history),
      "with the series and the episode when asked")
check(not any("series" in record for record in (answer("GET", "history", page=1, pageSize=5) or {}).get("records", [])),
      "without them when not asked")
kinds = {record.get("eventType") for record in history}
check(kinds == {"grabbed", "downloadFolderImported", "episodeFileDeleted", "downloadFailed"}, f"the kinds of events ({sorted(kinds)})")
check(all(moment(d) <= datetime.now(UTC) for d in dates), "nothing in the future")
since = datetime.now(UTC) - timedelta(days=20)
recent = answer("GET", "history/since", date=since.strftime("%Y-%m-%dT%H:%M:%SZ"), includeSeries="true", includeEpisode="true") or []
check(isinstance(recent, list) and bool(recent) and all(moment(r["date"]) >= since for r in recent)
      and {r["id"] for r in recent} == {r["id"] for r in history if moment(r["date"]) >= since},
      f"history/since: exactly the records since the date ({len(recent)})")

demo_world.now = lambda: base
whole = answer("GET", "queue", page=1, pageSize=200, includeUnknownSeriesItems="true", includeSeries="true", includeEpisode="true") or {}
blocks = sonarr.progress.blocks(whole.get("records") or [])
states = [block["state"] for block in blocks]
check(2 <= len(blocks) <= 4 and whole.get("totalRecords") == len(whole.get("records") or []),
      f"the queue: 2-4 downloads ({len(blocks)}, {whole.get('totalRecords')} records)")
check(states.count("downloading") >= 2 and sum(state in ("stuck", "warnings") for state in states) == 1,
      f"downloads in progress and one with a warning ({states})")
check(any(block["episode_count"] > 1 for block in blocks), "one of them a season pack")
check(all(block["title"] in titles and block["title_id"] in library for block in blocks), "of the demo's series, paired")
check(ask("POST", "command", body={"name": "RefreshMonitoredDownloads"}).status_code == 201
      and answer("GET", "queue", page=1, pageSize=200, includeUnknownSeriesItems="true", includeSeries="true",
                 includeEpisode="true") == whole, "a POST command is accepted and changes nothing")
calendar = answer("GET", "calendar", start=base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  end=(base + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"), includeSeries="true") or []
aired = [moment(entry["airDateUtc"]) for entry in calendar]
check(bool(calendar) and all(base <= at <= base + timedelta(days=14) for at in aired)
      and any(at > base + timedelta(days=7) for at in aired), f"the calendar for 14 days ahead ({len(calendar)})")
check(all(entry.get("series", {}).get("title") in titles for entry in calendar), "with the series of the demo")
health = answer("GET", "health") or []
out = demo_world.flaky_down(base)
check([h.get("source") for h in health] == ["UpdateCheck", *(["IndexerStatusCheck"] if out else [])]
      and all(h.get("type") == "warning" and {"source", "type", "message", "wikiUrl"} <= set(h) for h in health),
      f"health: an update{', the flaky indexer (in an outage now)' if out else ''} in Sonarr's shape ({health})")
for name in ("missing", "cutoff"):
    wanted = answer("GET", f"wanted/{name}", pageSize=1) or {}
    check(int(wanted.get("totalRecords") or 0) > 0 and len(wanted.get("records") or []) == 1,
          f"wanted/{name}: the count with one record ({wanted.get('totalRecords')})")
listed = answer("GET", "series") or []
check(sorted(int(s["id"]) for s in listed) == [entry["number"] for entry in series]
      and all(str(s["tvdbId"]) == entry["tvdb_id"] for s, entry in zip(sorted(listed, key=lambda s: s["id"]), series)),
      "series: the demo's series with their numbers and TVDB ids")
one = answer("GET", "series", tvdbId=series[0]["tvdb_id"]) or []
check([s.get("id") for s in one] == [series[0]["number"]], "series?tvdbId= gives that one series")
check(ask("GET", f"mediacover/{series[0]['number']}/poster-250.jpg").status_code == 404 and ask("GET", "no/such/path").status_code == 404,
      "posters and unknown paths are 404")
demo_world.now = real_now

print()
print("--- (c) every path the code asked has an answer ---")
paths = {path for _method, path, _status in ASKED}
missing_paths = {"system/status", "history", "series", "wanted/missing", "wanted/cutoff", "queue", "calendar", "health",
                 "command"} - paths
check(not missing_paths, f"the seed and the page asked what a real Sonarr is asked ({sorted(missing_paths)})")
not_found = sorted({f"{method} {path}" for method, path, status in ASKED if status == 404 and not path.startswith("mediacover/")})
check(not not_found, f"no 404 but images ({not_found})")
check(all(status < 400 for method, path, status in ASKED if not path.startswith("mediacover/")),
      f"every answer fine ({sorted({(p, s) for _m, p, s in ASKED if s >= 400})[:4]})")

print()
print("--- (g) a refresh deletes the data and fetches the same again ---")
before = sonarr.event_count()
WARNINGS.clear()
demodata.refresh()
check(sonarr.event_count() == before and sonarr.sync_state()["result"] == "ok",
      f"the same history after the refresh ({before} -> {sonarr.event_count()})")
check(not WARNINGS, f"the refresh logged no warning ({WARNINGS[:3]})")
check(json.dumps([(s["id"], s["title"], s["tvdbId"]) for s in answer("GET", "series") or []])
      == json.dumps([(s["id"], s["title"], s["tvdbId"]) for s in listed]), "the same series after the refresh")

print()
print("--- (h) no network ---")
check(not NETWORK, f"no socket was touched ({NETWORK[:3]})")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(demo_switch.transport(sonarr.NAME) is None, "outside the demo mode the switch gives Sonarr's client the network")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
