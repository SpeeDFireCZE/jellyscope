# -*- coding: utf-8 -*-
r"""The demo's made-up Seerr (seerr/demo.py): requests of the demo's viewers through Seerr's real code.

In the demo mode (JELLYSCOPE_DEMO) the seed connects Seerr at a made-up address and runs its real sync against the
made-up Seerr (demo_apps.seed_apps), which answers from the demo's library and viewers (demo_world) in the shape of
Seerr 3 (API v1); what Jellyscope itself caught at its earlier syncs (who waited, when it was approved or declined)
the made-up module replays through the sync's writer (`fill_past`). The page and its live cards then ask it the
same way they ask a real one. Watched:

(a) after demodata.prepare() Seerr is connected at its made-up address, its sync ended well, the seed logged no
    warning;
(b) the requests: made by the demo's viewers (their Jellyfin ids), for the demo's movies and series (paired with the
    library by the title's Jellyfin id and TMDB id) and for 4-6 titles the library does not have; 2-3 waiting,
    approved, available, declined, deleted in Seerr; each made days before the title came to the library, approved
    hours to days after it; spread over the whole period of the demo's playback;
(b2) every card of the page has data in the last 7, 30 and 365 days: the numbers, per day, when, the states, who, how
    fast (to the library and to the approval - the times Jellyscope caught), "Requested and watched?" with the
    requester's own plays, "Nobody started";
(c) the live cards "Waiting for approval" and "Latest requests" read the made-up Seerr and show its rows with the
    demo viewers' names; the page /seerr and each of its cards (/partials/cards) give 200 without a connection error;
(d) every path the code asked has an answer: no 404 but images and the request deleted in Seerr;
(e) the answers have Seerr's shape: the version, the requests in pages (take/skip, filter, sort, both directions),
    one request, the titles' names and posters' paths, the users, a write accepted without a change, images 404;
(f) the same moment gives the same answers;
(g) a refresh (demodata.refresh) deletes Seerr's data and fetches the same again;
(h) no socket is touched during the whole test.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 2)

Run:
    .venv/bin/python tests/test_seerr_demo.py
"""
from __future__ import annotations

import asyncio
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
os.environ["SECRET_KEY"] = "test-key-seerr-demo"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import cards_seerr, config, db, demo_apps, demo_switch, demo_world, demodata, seerr, stats, web  # noqa: E402

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

# The made-up Seerr, wrapped so the test sees every request the code sends it: (method, path, status) - the path after
# /api/v1/, or the whole path outside the API (the images). demo_apps.load_fakes looks the handler up only when the
# fakes are loaded, so the wrapper is what gets registered.
ASKED: list[tuple[str, str, int]] = []
try:
    made_up = importlib.import_module("jellyscope.seerr.demo")
except ImportError as error:
    made_up = None
    print(f"       (jellyscope.seerr.demo does not import: {error})")
ORIGINAL = made_up.handler if made_up is not None else None


def _path(request: httpx.Request) -> str:
    path = request.url.path
    return path.split("/api/v1/", 1)[1] if "/api/v1/" in path else path


def _recording(request: httpx.Request) -> httpx.Response:
    response = ORIGINAL(request)
    ASKED.append((request.method, _path(request), response.status_code))
    return response


if made_up is not None:
    made_up.handler = _recording


def ask(method: str, path: str, **params) -> httpx.Response:
    """One request straight to the made-up Seerr (not recorded), at the address the seed stores."""
    if ORIGINAL is None:
        return httpx.Response(599)
    url = path if path.startswith("/") else f"/api/v1/{path}"
    return ORIGINAL(httpx.Request(method, f"http://seerr.demo{url}", params=params or None))


def answer(path: str, **params):
    response = ask("GET", path, **params)
    try:
        return response.json()
    except ValueError:
        return None


def _utc(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(text or "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


ERRORS = ("not responding", "could not", "not answering", "Traceback")

db.init_db()
demodata.prepare(quiet=True)
seed_warnings = list(WARNINGS)
viewers = demo_world.viewers()
names = {v["jellyfin_id"]: v["name"] for v in viewers}
movies = demo_world.movies()
shows = demo_world.series()
library = {m["jellyfin_id"]: ("movie", m) for m in movies} | {s["jellyfin_id"]: ("tv", s) for s in shows}
library_tmdb = {("movie", m["tmdb_id"]) for m in movies} | {("tv", s["tmdb_id"]) for s in shows}
rows = [dict(r) for r in db.query_all("SELECT * FROM seerr_request ORDER BY id")]
now = datetime.now(UTC)

print("--- (a) the seed connects the made-up Seerr and syncs it through the real code ---")
settings = seerr.stored_settings()
check(bool(settings["enabled"]) and seerr.is_connected(), f"Seerr is connected ({settings})")
check(settings["url"] == "http://seerr.demo" and db.get_setting("seerr_api_key", "") == demo_apps.api_key("seerr"),
      f"at its made-up address with the made-up key ({settings['url']!r})")
check(made_up is not None and demo_switch.registered(seerr.NAME), "the made-up Seerr is registered in the switch")
state = seerr.sync_state()
check(state["result"] == "ok" and not state["error"], f"its sync ended well ({state})")
check(not seed_warnings, f"the seed logged no warning ({seed_warnings[:3]})")

print()
print("--- (b) the requests: the demo's viewers ask for the demo's titles ---")
check(len(rows) >= 25 and seerr.request_count() == len(rows), f"the requests are stored ({len(rows)})")
strangers = [r["id"] for r in rows if r["requester_jellyfin_id"] not in names or r["requester_name"] != names[r["requester_jellyfin_id"]]]
check(not strangers, f"every request is made by a viewer of the demo, under their name ({strangers[:5]})")
check(len({r["requester_jellyfin_id"] for r in rows}) >= 6, f"several of the household request ({len({r['requester_jellyfin_id'] for r in rows})})")
check({r["media_type"] for r in rows} == {"movie", "tv"}, "movies and series")
check(all(r["title"] for r in rows), f"every request has its title's name ({[r['id'] for r in rows if not r['title']]})")
paired = [r for r in rows if r["jellyfin_id"]]
wrong = [r["id"] for r in paired if r["jellyfin_id"] not in library or library[r["jellyfin_id"]][0] != r["media_type"]
         or library[r["jellyfin_id"]][1]["tmdb_id"] != r["tmdb_id"] or library[r["jellyfin_id"]][1]["title"] != r["title"]]
check(len(paired) >= 15 and not wrong, f"the library's titles by their Jellyfin id, TMDB id and name ({len(paired)}, {wrong[:5]})")
outside = {(r["media_type"], r["tmdb_id"]) for r in rows if (r["media_type"], r["tmdb_id"]) not in library_tmdb}
check(4 <= len(outside) <= 6 and all(r["tmdb_id"] for r in rows),
      f"4-6 titles the library does not have, each with a TMDB id ({len(outside)})")
live_rows = [r for r in rows if not r["deleted_at"]]
pending_rows = [r for r in live_rows if r["status"] == 1]
check(2 <= len(pending_rows) <= 3 and all((r["media_type"], r["tmdb_id"]) in outside for r in pending_rows),
      f"2-3 requests wait for approval, for titles outside the library ({len(pending_rows)})")
groups = {"approved": [r for r in live_rows if r["status"] == 2 and r["media_status"] not in (4, 5)],
          "available": [r for r in live_rows if r["status"] != 3 and r["media_status"] in (4, 5)],
          "declined": [r for r in live_rows if r["status"] == 3], "deleted": [r for r in rows if r["deleted_at"]],
          "removed": [r for r in live_rows if r["media_status"] == 7]}
check(all(groups.values()) and len(groups["available"]) >= 12,
      f"approved, available, declined, deleted in Seerr, gone from the library ({ {k: len(v) for k, v in groups.items()} })")
added = {m["jellyfin_id"]: m["added_at"] for m in movies}
first_arrival = {s["jellyfin_id"]: s for s in shows}
late = []
for r in paired:
    created = _utc(r["created_at"])
    if r["media_type"] == "movie":
        arrived = added[r["jellyfin_id"]]
        # The archive's movie is gone from the library: Seerr no longer says it is available.
        if r["media_status"] != 7 and not (r["available_at"] and _utc(r["available_at"]) == arrived.replace(microsecond=0)):
            late.append(f"{r['id']} available_at {r['available_at']}")
    else:
        episodes = [e for season in first_arrival[r["jellyfin_id"]]["seasons"].values() for e in season]
        arrived = min((e["added_at"] for e in episodes if e["added_at"] > created), default=None)
    if arrived is None or not timedelta(hours=1) <= arrived - created <= timedelta(days=21):
        late.append(f"{r['id']} {r['title']} {created} -> {arrived}")
check(not late, f"each request came days before its title came to the library, a movie's arrival is Jellyfin's ({late[:4]})")
caught = [r for r in rows if r["seen_pending"] and r["approved_at"]]
odd = [r["id"] for r in caught if not timedelta(minutes=5) <= _utc(r["approved_at"]) - _utc(r["created_at"]) <= timedelta(days=5)
       or (r["available_at"] and _utc(r["approved_at"]) > _utc(r["available_at"]))]
check(len(caught) >= 10 and not odd, f"approved hours to days after the request, before the title came ({len(caught)}, {odd[:5]})")
declined_caught = [r for r in groups["declined"] if r["seen_pending"] and r["declined_at"]]
check(bool(declined_caught), "the time of a decline is caught too")
check(all(r["seen_pending"] for r in groups["deleted"]), "the request deleted in Seerr was seen waiting before")
oldest = min((_utc(r["created_at"]) for r in rows), default=now)
months = {r["created_at"][:7] for r in rows if _utc(r["created_at"]) >= now - timedelta(days=365)}
check(oldest < now - timedelta(days=365) and len(months) >= 6,
      f"over the whole period of the playback ({oldest:%Y-%m-%d}, {len(months)} months of the year)")
check(all(_utc(r["created_at"]) <= now for r in rows), "nothing in the future")

print()
print("--- (b2) every card has data in the last 7, 30 and 365 days ---")
NEED = {7: 4, 30: 10, 365: 20}
for days, at_least in NEED.items():
    period = cards_seerr.seerr_period(days)
    analysis = stats.seerr_events(period)
    summary = stats.seerr_summary(period, analysis=analysis)
    check(summary["count"] >= at_least and summary["movies"] and summary["series_list"],
          f"{days} days: at least {at_least} requests, movies and series ({summary})")
    check(len(stats.seerr_by_day(period, analysis=analysis)) >= 2, f"{days} days: requests on several days")
    check(sum(map(sum, stats.seerr_heatmap(period, analysis=analysis))) == summary["count"], f"{days} days: when they are made")
    handling = stats.seerr_handling(period, analysis=analysis)
    check(sum(1 for n in handling["states"].values() if n) >= 3, f"{days} days: several states ({handling['states']})")
    check(len(stats.seerr_requesters(period, analysis=analysis)) >= 3, f"{days} days: several requesters")
    check(handling["to_library"]["count"] >= 2 and handling["to_approve"]["count"] >= 2,
          f"{days} days: how fast - to the library and to the approval ({handling['to_library']}, {handling['to_approve']})")
    watching = stats.seerr_watching(period, analysis=analysis)
    check(watching["available_count"] >= 2 and watching["requester"] >= 1,
          f"{days} days: requested and watched - by the requester themselves ({ {k: watching[k] for k in ('available_count', 'requester', 'other', 'nobody')} })")
    check(watching["nobody"] >= 1, f"{days} days: a requested title nobody started")
watching = stats.seerr_watching(365)
check(watching["other"] >= 1, f"the year: a title only someone else watched ({watching['other']})")
own = [r for r in stats.seerr_events(365)["requests"] if r["watching"] == "requester"]
plays_after = [r["id"] for r in own if db.query_value(
    "SELECT COUNT(*) FROM playback WHERE user_id = ? AND started_at >= ? AND watched_seconds > 0", (r["requester_jellyfin_id"], r["created_at"]))]
check(own and len(plays_after) == len(own), f"the requester's own plays after the request are in the playback ({len(plays_after)} of {len(own)})")

print()
print("--- (c) the live cards and the page ---")
from jellyscope.seerr import live  # noqa: E402

live.CACHE.clear()
pending = asyncio.run(seerr.pending())
check(pending["available"] and 2 <= pending["count"] <= 3 and len(pending["requests"]) == pending["count"],
      f"Waiting for approval: Seerr answers with 2-3 requests ({pending['available']}, {pending['count']}, {pending['error']})")
check(all(r["user_id"] in names and r["user_name"] == names[r["user_id"]] for r in pending["requests"]),
      f"with the demo viewers' names ({[r['user_name'] for r in pending['requests']]})")
check(all(r["title"] and not r["title"].startswith("TMDB") and r["waiting_seconds"] for r in pending["requests"]),
      f"with the titles' names and how long they wait ({[(r['title'], r['waiting_seconds']) for r in pending['requests']]})")
latest = asyncio.run(seerr.latest_requests())
check(latest["available"] and len(latest["requests"]) == len(live_rows),
      f"Latest requests: Seerr answers with every request it has ({len(latest['requests'])} of {len(live_rows)})")
check(all(r["user_id"] in names and r["user_name"] == names[r["user_id"]] for r in latest["requests"]),
      "with the demo viewers' names")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
page = client.get("/seerr")
check(page.status_code == 200, f"/seerr gives 200 ({page.status_code})")
check(not [e for e in ERRORS if e in page.text], f"the page has no connection error ({[e for e in ERRORS if e in page.text]})")
check(all(r["title"] in page.text for r in pending["requests"]) and all(r["user_name"] in page.text for r in pending["requests"]),
      "the page shows the waiting requests with their requesters")


def card(key: str, **parameters) -> tuple[int, str]:
    response = client.get("/partials/cards?" + urlencode({"keys": key, **parameters}), headers={"X-Requested-With": "fetch"},
                          follow_redirects=False)
    return response.status_code, response.text


broken = []
for days in (7, 30, 365):
    for one in cards_seerr.CARDS:
        status, text = card(one.key, days=days)
        problems = [e for e in ERRORS if e in text] + [e for e in ("chart-empty", "Nothing is waiting.") if e in text]
        if status != 200 or f'data-card-key="{one.key}"' not in text or problems:
            broken.append(f"{one.key}/{days}: {status} {problems}")
check(not broken, f"each of the {len(cards_seerr.CARDS)} cards gives 200 with data, without an error, in 7, 30 and 365 days ({broken[:4]})")
status, text = card(cards_seerr.card_key("latest"), days=30)
check(any(v["name"] in text for v in viewers), "Latest requests names the demo's viewers")
poster = client.get(f"/seerr/poster/{pending['requests'][0]['media_type']}/{pending['requests'][0]['tmdb_id']}") if pending["requests"] else None
check(poster is not None and poster.status_code in (200, 404), f"a waiting title's poster: a placeholder, no error ({poster and poster.status_code})")

print()
print("--- (d) every path the code asked has an answer ---")
paths = {path.split("/")[0] if path.startswith(("request/", "movie/", "tv/")) else path for _method, path, _status in ASKED}
missing_paths = {"request", "request", "movie", "tv", "settings/public"} - paths
check(not missing_paths, f"the seed and the page asked what a real Seerr is asked ({sorted(missing_paths)})")
deleted_ids = {f"request/{r['id']}" for r in groups["deleted"]}
not_found = sorted({f"{method} {path}" for method, path, status in ASKED
                    if status == 404 and not path.startswith("/imageproxy/") and path not in deleted_ids})
check(not not_found, f"no 404 but images and the request deleted in Seerr ({not_found[:5]})")
check(all(status < 400 for method, path, status in ASKED if status != 404), f"every other answer fine ({sorted({(p, s) for _m, p, s in ASKED if s >= 400})[:4]})")
check(any(path in deleted_ids and status == 404 for _m, path, status in ASKED), "the deleted request was asked for and is gone (404)")

print()
print("--- (e) the answers in Seerr's shape ---")
status_answer = answer("status") or {}
about = answer("settings/about") or {}
public = answer("settings/public") or {}
check(str(status_answer.get("version", "")).startswith("3.") and about.get("version") == status_answer.get("version"),
      f"status and settings/about: Seerr 3 ({status_answer.get('version')})")
check(about.get("totalRequests") == len(live_rows) and "appDataPath" in about, f"settings/about counts the requests ({about.get('totalRequests')})")
check(public.get("cacheImages") is True and public.get("applicationTitle") == "Seerr" and public.get("applicationUrl") == "",
      f"settings/public: it serves images, no address for people ({public})")
check(asyncio.run(seerr.check_connection("http://seerr.demo", demo_apps.api_key("seerr")))[0], "the connection test passes")
whole = answer("request", take=100, skip=0, filter="all", sort="added", sortDirection="asc") or {}
records = whole.get("results") or []
created = [r.get("createdAt") for r in records]
check(len(records) == len(live_rows) == (whole.get("pageInfo") or {}).get("results") and created == sorted(created),
      f"request: every request, oldest first ({len(records)}, {whole.get('pageInfo')})")
check([r["id"] for r in records] == sorted(r["id"] for r in records), "the ids grow with the time")
newest = answer("request", take=100, skip=0, filter="all", sort="added", sortDirection="desc") or {}
check([r["id"] for r in newest.get("results") or []] == [r["id"] for r in records][::-1], "newest first when asked")
pages, skip = [], 0
while skip < 200:
    one = answer("request", take=10, skip=skip, filter="all", sort="added", sortDirection="asc") or {}
    pages += one.get("results") or []
    info = one.get("pageInfo") or {}
    if not one.get("results"):
        break
    skip += 10
check([r["id"] for r in pages] == [r["id"] for r in records] and info.get("pageSize") == 10 and info.get("pages") == -(-len(records) // 10),
      f"take and skip page through them ({len(pages)}, {info})")
only_pending = answer("request", take=50, skip=0, filter="pending", sort="added") or {}
check({r["id"] for r in only_pending.get("results") or []} == {r["id"] for r in pending_rows}
      and (only_pending.get("pageInfo") or {}).get("results") == len(pending_rows), "filter=pending: only the waiting ones")
check(ask("GET", "request", filter="no-such-filter").status_code == 400, "an unknown filter is refused")
sample = records[-1] if records else {}
one = answer(f"request/{sample.get('id')}") or {}
check(one == sample, "request/{id}: that request")
check(set(sample) >= {"id", "status", "createdAt", "updatedAt", "type", "is4k", "isAutoRequest", "media", "seasons",
                      "modifiedBy", "requestedBy", "seasonCount"}
      and set(sample.get("media") or {}) >= {"id", "mediaType", "tmdbId", "tvdbId", "status", "status4k", "createdAt",
                                              "mediaAddedAt", "jellyfinMediaId", "jellyfinMediaId4k"},
      "a request has the fields of a real one")
check(all(isinstance(r["media"]["tmdbId"], int) and (r["type"] == "movie" or isinstance(r["media"]["tvdbId"], int)) for r in records),
      "TMDB ids (and a series' TVDB id) are numbers")
check(all(r["createdAt"].endswith(".000Z") for r in records), "times in Seerr's shape (milliseconds, Z)")
for r in groups["deleted"]:
    check(ask("GET", f"request/{r['id']}").status_code == 404, f"the deleted request {r['id']} is gone (404)")
movie_row = next((r for r in rows if r["media_type"] == "movie"), {})
tv_row = next((r for r in rows if r["media_type"] == "tv"), {})
movie_title, tv_title = answer(f"movie/{movie_row.get('tmdb_id')}") or {}, answer(f"tv/{tv_row.get('tmdb_id')}") or {}
check(movie_title.get("title") == movie_row.get("title") and str(movie_title.get("posterPath", "")).endswith(".jpg"),
      f"movie/{{tmdb}}: the name and a poster path ({movie_title})")
check(tv_title.get("name") == tv_row.get("title") and str(tv_title.get("posterPath", "")).endswith(".jpg"),
      f"tv/{{tmdb}}: the name and a poster path ({tv_title})")
check(ask("GET", "movie/1").status_code == 404, "an unknown title is 404")
users = answer("user", take=50, skip=0) or {}
check({u.get("jellyfinUserId") for u in users.get("results") or []} == set(names)
      and (users.get("pageInfo") or {}).get("results") == len(names), f"user: the demo's viewers ({(users.get('pageInfo') or {})})")
first_user = (users.get("results") or [{}])[0]
check(answer(f"user/{first_user.get('id')}") == first_user and first_user.get("displayName") in names.values(), "user/{id}: that user")
counts = answer("request/count") or {}
check(counts.get("total") == len(live_rows) and counts.get("pending") == len(pending_rows), f"request/count ({counts})")
if pending_rows:
    target = pending_rows[0]["id"]
    before_write = answer(f"request/{target}")
    check(ask("POST", f"request/{target}/approve").status_code in (200, 201) and answer(f"request/{target}") == before_write,
          "a POST approve is accepted and changes nothing")
check(ask("GET", "/imageproxy/tmdb/t/p/w342/abc.jpg").status_code == 404 and ask("GET", "no/such/path").status_code == 404,
      "images and unknown paths are 404")

print()
print("--- (f) the same moment gives the same answers ---")
base = datetime.now(UTC).replace(microsecond=0)
real_now = demo_world.now
demo_world.now = lambda: base
first = json.dumps(answer("request", take=100, skip=0, filter="all", sort="added"))
second = json.dumps(answer("request", take=100, skip=0, filter="all", sort="added"))
check(first == second, "two answers at the same moment are the same")
demo_world.now = lambda: base + timedelta(minutes=10)
later = answer("request", take=50, skip=0, filter="pending", sort="added") or {}
check({r["id"] for r in later.get("results") or []} == {r["id"] for r in pending_rows}, "ten minutes later the same requests wait")
demo_world.now = real_now

print()
print("--- (g) a refresh deletes the data and fetches the same again ---")
before = (seerr.request_count(), len(caught))
WARNINGS.clear()
demodata.refresh()
after_rows = db.query_all("SELECT * FROM seerr_request")
check((seerr.request_count(), sum(1 for r in after_rows if r["seen_pending"] and r["approved_at"])) == before
      and seerr.sync_state()["result"] == "ok", f"the same requests after the refresh ({before})")
check(not WARNINGS, f"the refresh logged no warning ({WARNINGS[:3]})")

print()
print("--- (h) no network ---")
check(not NETWORK, f"no socket was touched ({NETWORK[:3]})")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(demo_switch.transport(seerr.NAME) is None, "outside the demo mode the switch gives Seerr's client the network")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
