# -*- coding: utf-8 -*-
r"""The demo's made-up Radarr (arr/radarr/demo.py): the demo's movies through Radarr's real code.

In the demo mode (JELLYSCOPE_DEMO) the seed connects Radarr at a made-up address and runs its real sync and
counts (demo_apps.seed_apps) against the made-up Radarr, which answers from the demo's library
(demo_world.movies) in the shape of Radarr v6. The page and its live cards then ask it the same way they ask a
real one. Watched:

(a) after demodata.prepare() Radarr is connected at its made-up address, its sync ended well and the seed logged
    no warning;
(b) its history has imports in the last 7, 30 and 365 days, upgrades, one or two failed downloads, a spread of
    the time from release to import, its indexers and clients; every movie of the library is paired with it (a
    second copy of a title was put into the library by hand, the archive is not linked); the counts of missing and
    cutoff are not zero;
(b2) the recent weeks: new movies of the last weeks are in the library, Radarr waited for them, grabbed and imported
    each after its digital release and before Jellyfin took it in; the 7- and 30-day views have values of "how
    soon after release" in several buckets and an upgrade, the 30 days a late one; the calendar has the movies
    coming out, which the library does not have; the new movies are watched after they came;
(c) every path the code asked during the seed and the page has an answer: no 404 but images;
(d) the page /arr/radarr, its slice "Downloading" and each of its cards (/partials/cards) give 200 without a
    connection error;
(e) the card "Downloading" differs between a moment and ten minutes later (demo_world.now substituted);
(f) the answers have Radarr's shape: the history in pages oldest first and since a date, the queue (2-4
    downloads, one with a warning), the calendar for 14 days, the health warnings, a POST command accepted
    without a change, the movies (also by TMDB id), posters and unknown paths 404;
(g) a refresh (demodata.refresh) deletes Radarr's data and fetches the same again;
(h) no socket is touched during the whole test.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 2)

Run:
    .venv/bin/python tests/test_radarr_demo.py
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
os.environ["SECRET_KEY"] = "test-key-radarr-demo"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_apps, demo_switch, demo_world, demodata, services, stats, web  # noqa: E402
from jellyscope.arr import radarr, registry  # noqa: E402

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

# The made-up Radarr, wrapped so the test sees every request the code sends it: (method, path after /api/v3/, status).
# The registry looks the handler up only when the fakes are loaded (registry._demo), so the wrapper is what gets
# registered.
ASKED: list[tuple[str, str, int]] = []
try:
    made_up = importlib.import_module("jellyscope.arr.radarr.demo")
except ImportError as error:
    made_up = None
    print(f"       (jellyscope.arr.radarr.demo does not import: {error})")
ORIGINAL = made_up.handler if made_up is not None else None


def _recording(request: httpx.Request) -> httpx.Response:
    response = ORIGINAL(request)
    ASKED.append((request.method, request.url.path.split("/api/v3/", 1)[-1], response.status_code))
    return response


if made_up is not None:
    made_up.handler = _recording


def ask(method: str, path: str, body: dict | None = None, **params) -> httpx.Response:
    """One request straight to the made-up Radarr (not recorded), at the address the seed stores."""
    if ORIGINAL is None:
        return httpx.Response(599)
    return ORIGINAL(httpx.Request(method, f"http://radarr.demo/api/v3/{path}", params=params or None, json=body))


def answer(method: str, path: str, **params):
    response = ask(method, path, **params)
    try:
        return response.json()
    except ValueError:
        return None


def moment(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(text or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


ERRORS = ("not responding", "could not", "not answering", "Traceback")

db.init_db()
demodata.prepare(quiet=True)
catalog = demo_world.movies()
seed_warnings = list(WARNINGS)
# Radarr has a title once: a second copy in the library (the demo's duplicates) was put there by hand.
managed: list[dict] = []
for entry in catalog:
    if entry["title"] not in {m["title"] for m in managed}:
        managed.append(entry)
titles = {entry["title"] for entry in managed}
managed_by_title = {entry["title"]: entry for entry in managed}

print("--- (a) the seed connects the made-up Radarr and syncs it through the real code ---")
settings = registry.APPS["radarr"].stored_settings()
check(bool(settings["enabled"]) and radarr.is_connected(), f"Radarr is connected ({settings})")
check(settings["url"] == "http://radarr.demo", f"at its made-up address ({settings['url']!r})")
check(registry.APPS["radarr"].demo is not None and made_up is not None
      and registry.APPS["radarr"].demo() is made_up.handler, "the registry gives the package's made-up handler (App.demo)")
check(demo_switch.registered(radarr.NAME), "the made-up Radarr is registered in the switch")
state = radarr.sync_state()
check(state["result"] == "ok" and not state["error"], f"its sync ended well ({state})")
check(not seed_warnings, f"the seed logged no warning ({seed_warnings[:3]})")

print()
print("--- (b) a history over the whole period, like a real one ---")
check(radarr.event_count() > 60, f"the history is downloaded ({radarr.event_count()} records)")
for days in (7, 30, 365):
    imports = sum(row["count"] for row in stats.radarr_additions(days))
    check(imports > 0, f"imports in the last {days} days ({imports})")
upgrades = stats.radarr_upgrades(365)
check(upgrades["count"] >= 3 and stats.radarr_upgrades(30)["count"] > 0,
      f"upgrades in the year and in the last 30 days ({upgrades['count']}, {stats.radarr_upgrades(30)['count']})")
check(all(row["to"] != "unknown" for row in upgrades["transitions"]), f"every upgrade pairs with its import ({upgrades['transitions'][:4]})")
failed = stats.radarr_failures(365)
check(1 <= failed <= 2, f"one or two failed downloads in the year ({failed})")
speed = stats.radarr_speed(365)
buckets = speed["since_release"]["buckets"]
check(speed["downloads"]["count"] > 10 and speed["downloads"]["median"] != speed["downloads"]["p90"],
      f"download times with a spread ({speed['downloads']})")
check(speed["since_release"]["count"] >= 5 and sum(1 for n in buckets.values() if n) >= 4,
      f"the time from release to import spread over the buckets ({buckets})")
whole = stats.radarr_speed(3650)["since_release"]
check(whole["not_counted"] > 0, f"movies added after their release (the back catalog) are not counted ({whole['not_counted']})")
reliability = {row["name"]: row for row in stats.radarr_indexer_reliability(365)}
flaky = reliability.get(demo_world.FLAKY_INDEXER, {})
check(set(reliability) <= {name for name, _protocol in demo_world.INDEXERS} - {demo_world.DISABLED_INDEXER}
      and len(reliability) >= 3, f"grabs from the made-up indexers, none from the switched-off one ({sorted(reliability)})")
check(flaky.get("failed", 0) >= 1 and flaky.get("success") is not None and flaky["success"] < 1,
      f"the flaky indexer has the failures ({flaky})")
check(not sum(row["unmatched"] for row in reliability.values()), "every grab carries its download id")
sources = stats.radarr_sources(3650)
check({row["name"] for row in sources["clients"]} == set(demo_world.DOWNLOAD_CLIENTS.values())
      and {row["name"] for row in sources["protocols"]} == {"usenet", "torrent"},
      f"both clients and protocols ({sources['clients']}, {sources['protocols']})")
check(len(sources["qualities"]) >= 4 and len(sources["groups"]) >= 4, f"several qualities and release groups ({sources['qualities']})")
stored_titles = {row["title"] for row in db.query_all("SELECT DISTINCT title FROM radarr_event")}
check(stored_titles == titles, f"the history names the demo's movies, each title once ({sorted(stored_titles ^ titles)})")
library = stats.radarr_library()
linked = {entry["number"] for entry in managed if not entry["missing"]}
check(sorted(library) == sorted(linked)
      and all(library[entry["number"]]["detail_url"] == f"/item/{entry['jellyfin_id']}" for entry in managed if entry["number"] in library),
      f"every movie of the library is paired by its TMDB id, the archive is not ({sorted(set(library) ^ linked)})")
counts = services.stored_counts("arr_radarr", services.settings_fingerprint("http://radarr.demo", demo_apps.api_key("radarr")))
check((counts["missing"] or 0) > 0 and (counts["cutoff"] or 0) > 0, f"the counts of missing and cutoff are not zero ({counts})")

print()
print("--- (b2) the recent weeks: new movies in the library and in Radarr, movies coming out ---")
new_movies = getattr(demodata, "NEW_MOVIES", {})
coming = getattr(demodata, "COMING_MOVIES", [])
check(len(new_movies) >= 6 and len(coming) >= 3, f"new movies of the last weeks and movies coming out ({len(new_movies)}, {len(coming)})")
by_title = {entry["title"]: entry for entry in catalog}
new_entries = [by_title.get(title) for title, *_rest in new_movies.values()]
first_new = len(demodata.MOVIES) + 4
check(all(new_entries) and [e["number"] for e in new_entries if e] == list(range(first_new, first_new + len(new_movies)))
      and catalog[0]["title"] == "Duna" and catalog[0]["number"] == 1,
      f"they follow the library's movies in the catalog, the old ones keep their numbers ({[e and e['number'] for e in new_entries]})")
now = datetime.now(UTC)
check(all(e and now - timedelta(days=40) <= e["added_at"] <= now for e in new_entries), "they came to the library in the last weeks")
release = getattr(demo_world, "movie_release", lambda entry: None)
known = {int(m.get("tmdbId") or 0): m for m in answer("GET", "movie") or []}
first_imports = {row["item_id"]: row for row in db.query_all(
    "SELECT item_id, MIN(happened_at) AS happened_at, MIN(released_at) AS released_at FROM radarr_event"
    " WHERE event_type LIKE ? GROUP BY item_id", ("%FolderImported",))}
out_of_order = []
for entry in (e for e in new_entries if e):
    own = known.get(int(entry["tmdb_id"] or 0), {})
    digital, added, imported = moment(own.get("digitalRelease")), moment(own.get("added")), first_imports.get(entry["number"])
    if not (digital and added and imported and digital == release(entry) and (digital.hour, digital.minute) == (0, 0)
            and added <= digital < _utc(imported["happened_at"]) <= entry["added_at"] and _utc(imported["released_at"]) == digital):
        out_of_order.append(entry["title"])
check(not out_of_order, "Radarr waited for each, imported it after its digital release (midnight) and before Jellyfin"
      f" took it in, with that release date ({out_of_order})")
new_titles = {title for title, *_rest in new_movies.values()}
for days in (7, 30):
    analysis = stats.radarr_events(days)
    waited = [i for i in analysis["imports"] if i["arr_id"] not in analysis["upgrade_imports"] and i["released_at"]
              and i["title"] in new_titles]
    since_release = stats.radarr_speed(days)["since_release"]
    filled = sum(1 for n in since_release["buckets"].values() if n)
    check(len(waited) >= 3 and since_release["count"] >= 3 and filled >= 3,
          f"{days} days: how soon after release has values of the new movies in several buckets"
          f" ({len(waited)} of {since_release['count']}, {since_release['buckets']})")
    check(stats.radarr_upgrades(days)["count"] >= 1, f"{days} days: an upgrade ({stats.radarr_upgrades(days)['count']})")
check(stats.radarr_speed(30)["since_release"]["buckets"]["longer"] >= 1, "30 days: one movie came late")
coming_list = getattr(demo_world, "coming_movies", lambda at: [])(now)
check(len(coming_list) == len(coming) and all(c["title"] not in by_title for c in coming_list),
      f"the movies coming out are not in the library ({[c['title'] for c in coming_list]})")
ahead = answer("GET", "calendar", start=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               end=(now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")) or []
soon = {c["title"] for c in coming_list if now <= c["released"] <= now + timedelta(days=14)}
check(len(soon) >= 2 and soon <= {c.get("title") for c in ahead if not c.get("hasFile")},
      f"the calendar has the movies coming out in 14 days, without a file ({sorted(soon)}, {[c.get('title') for c in ahead]})")
check(all(c.get("hasFile") == (c.get("title") in by_title) for c in ahead), "a movie of the calendar has a file exactly when the library has it")
viewed = db.query_all("SELECT p.started_at, i.date_created FROM playback p JOIN items i ON i.id = p.item_id"
                      " WHERE i.type = 'Movie' AND i.name IN ({})".format(", ".join("?" * len(new_titles))), tuple(sorted(new_titles)))
check(len(viewed) >= 8 and all(p["started_at"] >= p["date_created"].replace("T", " ")[:19] for p in viewed),
      f"the new movies are watched, each play after the movie came ({len(viewed)})")

print()
print("--- (d) the page and every card, without a connection error ---")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
page = client.get("/arr/radarr")
check(page.status_code == 200, f"/arr/radarr gives 200 ({page.status_code})")
check(not [e for e in ERRORS if e in page.text], f"the page has no connection error ({[e for e in ERRORS if e in page.text]})")
check(any(title in page.text for title in titles), "the page shows the demo's movies")
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
    radarr.live.CACHE.clear()
    text = client.get("/arr/radarr").text
    check(bool(ended) and "New update is available: " in text and (UNAVAILABLE in text) == out,
          f"{label} the page's health has an update{' and the flaky indexer' if out else ' and not the flaky indexer'}")
demo_world.now = real_now
radarr.live.CACHE.clear()


def card(key: str, **parameters) -> tuple[int, str]:
    response = client.get("/partials/cards?" + urlencode({"keys": key, **parameters}), headers={"X-Requested-With": "fetch"},
                          follow_redirects=False)
    return response.status_code, response.text


broken = []
for one in registry.APPS["radarr"].cards():
    status, text = card(one.key, days=30)
    errors = [e for e in ERRORS if e in text]
    if status != 200 or f'data-card-key="{one.key}"' not in text or errors:
        broken.append(f"{one.key}: {status} {errors}")
check(not broken, f"each of the {len(registry.APPS['radarr'].cards())} cards gives 200 without a connection error ({broken[:4]})")
piece = client.get("/partials/arr/radarr/downloading")
check(piece.status_code == 200 and "session-bar" in piece.text and not [e for e in ERRORS if e in piece.text],
      f"the slice of the card Downloading gives 200 with progress ({piece.status_code})")

print()
print("--- (e) the card Downloading moves with the clock ---")
real_now = demo_world.now
base = datetime.now(UTC).replace(microsecond=0)


def downloading_at(at: datetime) -> str:
    demo_world.now = lambda: at
    radarr.progress._memory = None      # the card reads the queue again
    return card("arr_radarr_downloading")[1]


first, later = downloading_at(base), downloading_at(base + timedelta(minutes=10))
check("session-bar" in first and "session-bar" in later, "the card shows downloads at both moments")
check(first != later, "the card differs ten minutes later")
check(downloading_at(base) == first, "the same moment gives the same card")
demo_world.now = real_now

print()
print("--- (f) the answers in Radarr's shape ---")
status = answer("GET", "system/status") or {}
check(status.get("appName") == "Radarr" and str(status.get("version", "")).startswith("6."), f"system/status: Radarr 6 ({status.get('version')})")
pages, page_number = [], 1
while page_number < 50:
    one_page = answer("GET", "history", page=page_number, pageSize=50, sortKey="date", sortDirection="ascending",
                      includeMovie="true") or {}
    pages.append(one_page)
    if not one_page.get("records") or page_number * 50 >= int(one_page.get("totalRecords") or 0):
        break
    page_number += 1
history = [record for one_page in pages for record in one_page.get("records") or []]
dates = [record.get("date", "") for record in history]
check(bool(history) and len(history) == pages[0].get("totalRecords") == radarr.event_count(),
      f"the history in pages: every record once, as many as the sync stored ({len(history)}, {pages[0].get('totalRecords')})")
check(dates == sorted(dates) and len({record.get("id") for record in history}) == len(history), "oldest first, every id its own")
check(all(isinstance(record.get("movie"), dict) and record["movie"].get("id") == record.get("movieId") for record in history),
      "with the movie when asked")
check(not any("movie" in record for record in (answer("GET", "history", page=1, pageSize=5) or {}).get("records", [])),
      "without it when not asked")
kinds = {record.get("eventType") for record in history}
check(kinds == {"grabbed", "downloadFolderImported", "movieFileDeleted", "downloadFailed"}, f"the kinds of events ({sorted(kinds)})")
check(all(moment(d) <= datetime.now(UTC) for d in dates), "nothing in the future")
check(all(not record["movie"].get("images") or all("remoteUrl" not in image for image in record["movie"]["images"])
          for record in history), "no image points outside (no remoteUrl)")
since = datetime.now(UTC) - timedelta(days=20)
recent = answer("GET", "history/since", date=since.strftime("%Y-%m-%dT%H:%M:%SZ"), includeMovie="true") or []
check(isinstance(recent, list) and bool(recent) and all(moment(r["date"]) >= since for r in recent)
      and {r["id"] for r in recent} == {r["id"] for r in history if moment(r["date"]) >= since},
      f"history/since: exactly the records since the date ({len(recent)})")

demo_world.now = lambda: base
whole_queue = answer("GET", "queue", page=1, pageSize=200, includeUnknownMovieItems="true", includeMovie="true") or {}
blocks = radarr.progress.blocks(whole_queue.get("records") or [])
states = [block["state"] for block in blocks]
check(2 <= len(blocks) <= 4 and whole_queue.get("totalRecords") == len(whole_queue.get("records") or []),
      f"the queue: 2-4 downloads ({len(blocks)}, {whole_queue.get('totalRecords')} records)")
check(states.count("downloading") >= 2 and sum(state in ("stuck", "warnings") for state in states) == 1,
      f"downloads in progress and one with a warning ({states})")
coming_titles = {c["title"] for c in coming_list}
check(all(block["title"] in titles | coming_titles for block in blocks)
      and any(block["title"] in coming_titles for block in blocks) and any(block["title_id"] in library for block in blocks),
      f"of the demo's movies: one coming out, the others better versions of the library's ({[b['title'] for b in blocks]})")
first_page = answer("GET", "queue", pageSize=100, includeUnknownMovieItems="true", includeMovie="true") or {}
check(first_page.get("totalRecords") == whole_queue.get("totalRecords"), "the live state's queue is the same queue")
check(ask("POST", "command", body={"name": "RefreshMonitoredDownloads"}).status_code == 201
      and answer("GET", "queue", page=1, pageSize=200, includeUnknownMovieItems="true", includeMovie="true") == whole_queue,
      "a POST command is accepted and changes nothing")
calendar = answer("GET", "calendar", start=base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  end=(base + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")) or []


def in_window(entry: dict) -> list[datetime]:
    return [at for at in (moment(entry.get(name)) for name in ("inCinemas", "digitalRelease", "physicalRelease"))
            if at and base <= at <= base + timedelta(days=14)]


check(bool(calendar) and all(in_window(entry) for entry in calendar)
      and any(max(in_window(entry)) > base + timedelta(days=7) for entry in calendar), f"the calendar for 14 days ahead ({len(calendar)})")
check(all(entry.get("title") in titles | coming_titles for entry in calendar), "with the movies of the demo")
health = answer("GET", "health") or []
out = demo_world.flaky_down(base)
check([h.get("source") for h in health] == ["UpdateCheck", *(["IndexerStatusCheck"] if out else [])]
      and all(h.get("type") == "warning" and {"source", "type", "message", "wikiUrl"} <= set(h) for h in health),
      f"health: an update{', the flaky indexer (in an outage now)' if out else ''} in Radarr's shape ({health})")
for name in ("missing", "cutoff"):
    wanted = answer("GET", f"wanted/{name}", pageSize=1) or {}
    check(int(wanted.get("totalRecords") or 0) > 0 and len(wanted.get("records") or []) == 1,
          f"wanted/{name}: the count with one record ({wanted.get('totalRecords')})")
missing_titles = {m.get("title") for m in (answer("GET", "wanted/missing", pageSize=50) or {}).get("records", [])}
check(missing_titles and missing_titles <= coming_titles, f"missing: movies out already the library does not have ({sorted(missing_titles)})")
listed = answer("GET", "movie") or []
own_ids = sorted(int(m["id"]) for m in listed if m.get("title") in titles)
check(own_ids == sorted(entry["number"] for entry in managed)
      and all(str(m["tmdbId"]) == managed_by_title[m["title"]]["tmdb_id"] for m in listed if m.get("title") in titles),
      "movie: the library's movies with their numbers and TMDB ids")
check({m.get("title") for m in listed} - titles == coming_titles and all(int(m["id"]) not in own_ids for m in listed if m["title"] in coming_titles),
      "and the movies coming out, with ids of their own")
check(all(str(m.get("imdbId", "")).startswith("tt") and str(m["imdbId"])[2:].isdigit() for m in listed), "every movie has an IMDb id")
one = answer("GET", "movie", tmdbId=managed[0]["tmdb_id"]) or []
check([m.get("id") for m in one] == [managed[0]["number"]], "movie?tmdbId= gives that one movie")
check(ask("GET", f"mediacover/{managed[0]['number']}/poster-250.jpg").status_code == 404 and ask("GET", "no/such/path").status_code == 404,
      "posters and unknown paths are 404")
demo_world.now = real_now

print()
print("--- (c) every path the code asked has an answer ---")
paths = {path for _method, path, _status in ASKED}
missing_paths = {"system/status", "history", "movie", "wanted/missing", "wanted/cutoff", "queue", "calendar", "health",
                 "command"} - paths
check(not missing_paths, f"the seed and the page asked what a real Radarr is asked ({sorted(missing_paths)})")
not_found = sorted({f"{method} {path}" for method, path, status in ASKED if status == 404 and not path.startswith("mediacover/")})
check(not not_found, f"no 404 but images ({not_found})")
check(all(status < 400 for method, path, status in ASKED if not path.startswith("mediacover/")),
      f"every answer fine ({sorted({(p, s) for _m, p, s in ASKED if s >= 400})[:4]})")

print()
print("--- (g) a refresh deletes the data and fetches the same again ---")
before = radarr.event_count()
WARNINGS.clear()
demodata.refresh()
check(radarr.event_count() == before and radarr.sync_state()["result"] == "ok",
      f"the same history after the refresh ({before} -> {radarr.event_count()})")
check(not WARNINGS, f"the refresh logged no warning ({WARNINGS[:3]})")
check(json.dumps([(m["id"], m["title"], m["tmdbId"]) for m in answer("GET", "movie") or []])
      == json.dumps([(m["id"], m["title"], m["tmdbId"]) for m in listed]), "the same movies after the refresh")

print()
print("--- (h) no network ---")
check(not NETWORK, f"no socket was touched ({NETWORK[:3]})")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(demo_switch.transport(radarr.NAME) is None, "outside the demo mode the switch gives Radarr's client the network")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
