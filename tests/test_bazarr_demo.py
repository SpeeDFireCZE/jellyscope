# -*- coding: utf-8 -*-
r"""The demo's made-up Bazarr (arr/bazarr/demo.py): subtitles for the demo's series and movies through Bazarr's real code.

In the demo mode (JELLYSCOPE_DEMO) the seed connects Bazarr at a made-up address and runs its real sync and counts
(demo_apps.seed_apps) against the made-up Bazarr, which answers with subtitles for the demo's library
(demo_world.series, demo_world.movies) - the same numbers the made-up Sonarr and Radarr give them (sonarrSeriesId,
sonarrEpisodeId, radarrId) - in the shape of Bazarr 1.6. The page and its live cards then ask it the same way they
ask a real one. Watched:

(a) after demodata.prepare() Bazarr is connected at its made-up address, its sync ended well and the seed logged no
    warning;
(b) its history is full in the last 7, 30 and 365 days: subtitles shortly after each file came, upgrades, manual
    picks, own files, translations, timing syncs, a deletion; the series airing now and the new movies have theirs;
    every episode and movie is one the made-up Sonarr and Radarr imported, with their numbers, and its first subtitles
    came after that import; in the 7 and 30 days every history card has several values; files from before Bazarr came
    are not counted in its speed;
(b2) the live state: episodes and movies without subtitles (now, in 12 and in 25 hours), the missing languages stored
    by the sync, several providers with one throttled (a reason and when Bazarr tries again);
(c) every path the code asked during the seed and the page has an answer: no 404;
(d) the page /arr/bazarr and each of its cards (/partials/cards) give 200 without a connection error, the providers'
    card shows the throttled one (Bazarr has no card "Downloading");
(f) the answers have Bazarr's shape: the history in pages from the newest with `total`, its time as text in Bazarr's
    zone, the wanted lists with the profiles' languages, the providers, the warnings, the badges; a write changes
    nothing, an unknown path is 404;
(g) a sync later on fetches only what came since, a second one nothing (no doubled rows); a refresh (demodata.refresh)
    deletes Bazarr's data and fetches the same again;
(h) no socket is touched during the whole test.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 2)

Run:
    .venv/bin/python tests/test_bazarr_demo.py
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "test-key-bazarr-demo"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_switch, demo_world, demodata, stats, web  # noqa: E402
from jellyscope.arr import bazarr, registry  # noqa: E402
from jellyscope.arr.bazarr import live as bazarr_live  # noqa: E402

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

# The made-up Bazarr, wrapped so the test sees every request the code sends it: (method, path after /api/, status).
# The registry looks the handler up only when the fakes are loaded (registry._demo), so the wrapper is what gets
# registered.
ASKED: list[tuple[str, str, int]] = []
try:
    made_up = importlib.import_module("jellyscope.arr.bazarr.demo")
except ImportError as error:
    made_up = None
    print(f"       (jellyscope.arr.bazarr.demo does not import: {error})")
ORIGINAL = made_up.handler if made_up is not None else None


def _recording(request: httpx.Request) -> httpx.Response:
    response = ORIGINAL(request)
    ASKED.append((request.method, request.url.path.split("/api/", 1)[-1], response.status_code))
    return response


if made_up is not None:
    made_up.handler = _recording


def ask(method: str, path: str, **params) -> httpx.Response:
    """One request straight to the made-up Bazarr (not recorded), at the address the seed stores."""
    if ORIGINAL is None:
        return httpx.Response(599)
    return ORIGINAL(httpx.Request(method, f"http://bazarr.demo/api/{path}", params=params or None))


def answer(path: str, **params):
    response = ask("GET", path, **params)
    try:
        return response.json()
    except ValueError:
        return None


def _utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def whole(kind: str) -> list[dict]:
    """Bazarr's whole history of one kind, page by page as the sync reads it (newest first)."""
    records, start = [], 0
    while start < 10_000:
        page = answer(f"{kind}/history", start=start, length=100) or {}
        records += page.get("data") or []
        start += 100
        if not page.get("data") or start >= int(page.get("total") or 0):
            break
    return records


ERRORS = ("not responding", "could not", "not answering", "Traceback")

db.init_db()
demodata.prepare(quiet=True)
seed_warnings = list(WARNINGS)
series = demo_world.series()
movies = demo_world.movies()
episodes = {e["number"]: (s, e) for s in series for season in s["seasons"].values() for e in season}

print("--- (a) the seed connects the made-up Bazarr and syncs it through the real code ---")
settings = registry.APPS["bazarr"].stored_settings()
check(bool(settings["enabled"]) and bazarr.is_connected(), f"Bazarr is connected ({settings})")
check(settings["url"] == "http://bazarr.demo", f"at its made-up address ({settings['url']!r})")
check(registry.APPS["bazarr"].demo is not None and made_up is not None
      and registry.APPS["bazarr"].demo() is made_up.handler, "the registry gives the package's made-up handler (App.demo)")
check(demo_switch.registered(bazarr.NAME), "the made-up Bazarr is registered in the switch")
state = bazarr.sync_state()
check(state["result"] == "ok" and not state["error"] and not state["without_time"],
      f"its sync ended well, every record with its time ({state})")
check(not seed_warnings, f"the seed logged no warning ({seed_warnings[:3]})")

print()
print("--- (b) a history over the whole period, like a real one ---")
check(bazarr.event_count() > 150, f"the history is downloaded ({bazarr.event_count()} records)")
rows = db.query_all("SELECT kind, title, episode, title_id, item_id, action, language, hi, provider, score, happened_at,"
                    " time_exact FROM bazarr_event")
now = datetime.now(UTC)
check(all(r["time_exact"] == 1 and r["happened_at"] and _utc(r["happened_at"]) <= now for r in rows),
      "every record has an exact time, none in the future")
for days in (7, 30, 365):
    numbers = stats.bazarr_numbers(days)
    check(numbers["obtained_count"] > 0, f"subtitles obtained in the last {days} days ({numbers})")
actions = {r["action"] for r in rows}
check(actions == {0, 1, 2, 3, 4, 5, 6}, f"downloads, manual picks, upgrades, own files, translations, syncs, a deletion ({sorted(actions)})")
check({r["language"] for r in rows} == {"cs", "en", "sk"} and any(r["hi"] for r in rows),
      f"the profiles' languages, English for the hearing impaired too ({sorted({r['language'] for r in rows})})")
providers_used = {r["provider"] for r in rows if r["action"] in (1, 2, 3)}
check(len(providers_used) >= 4 and "manual" in {r["provider"] for r in rows if r["action"] == 4},
      f"several providers, an own file under 'manual' ({sorted(providers_used)})")
# The numbers of the made-up Sonarr and Radarr: an episode is Sonarr's episode id, its series Sonarr's series id.
episode_ids = {r["item_id"] for r in rows if r["kind"] == "episode"}
movie_ids = {r["item_id"] for r in rows if r["kind"] == "movie"}
sonarr_imports = stats.sonarr_imports(episode_ids)
radarr_imports = stats.radarr_imports(movie_ids)
check(episode_ids and episode_ids <= set(episodes) and set(sonarr_imports) == episode_ids,
      f"every episode is one of the library's, imported by the made-up Sonarr ({sorted(episode_ids - set(sonarr_imports))[:5]})")
check(all(r["title_id"] == episodes[r["item_id"]][0]["number"] and r["title"] == episodes[r["item_id"]][0]["title"]
          for r in rows if r["kind"] == "episode" and r["item_id"] in episodes),
      "with its series' number and title (sonarrSeriesId)")
movie_numbers = {m["number"]: m for m in movies}
check(movie_ids and movie_ids <= set(movie_numbers) and set(radarr_imports) == movie_ids,
      f"every movie is one of the library's, imported by the made-up Radarr ({sorted(movie_ids - set(radarr_imports))[:5]})")
check(all(r["title_id"] == r["item_id"] and r["title"] == movie_numbers[r["item_id"]]["title"] for r in rows
          if r["kind"] == "movie" and r["item_id"] in movie_numbers), "with its number (radarrId) and title")
first_subtitles: dict[tuple, str] = {}
for r in sorted(rows, key=lambda r: r["happened_at"]):
    if r["action"] in bazarr.ACQUIRED:
        first_subtitles.setdefault((r["kind"], r["item_id"], r["language"]), r["happened_at"])
library_added = {**{n: e["added_at"] for n, (_s, e) in episodes.items()}, **{-n: m["added_at"] for n, m in movie_numbers.items()}}
too_early = [key for key, at in first_subtitles.items()
             if not (imports := (sonarr_imports if key[0] == "episode" else radarr_imports).get(key[1]))
             or at < imports[0] or _utc(at) < library_added[key[1] if key[0] == "episode" else -key[1]]]
check(not too_early, f"every first subtitles came after the file's import and after Jellyfin took it in ({too_early[:4]})")
airing = [s for s in series if f"demo-series-{s['number'] - 1}" in demo_world.AIRING]
recent = {r["title_id"] for r in rows if r["kind"] == "episode" and _utc(r["happened_at"]) >= now - timedelta(days=30)}
check(len(airing) >= 4 and all(s["number"] in recent for s in airing),
      f"the series airing now have subtitles in the last 30 days ({sorted(s['title'] for s in airing if s['number'] not in recent)})")
new_titles = {title for title, *_rest in demodata.NEW_MOVIES.values()}
with_subtitles = {r["title"] for r in rows if r["kind"] == "movie"}
check(new_titles <= with_subtitles, f"the new movies have subtitles ({sorted(new_titles - with_subtitles)})")
duplicates = [m for m in movies if m["title"] in {o["title"] for o in movies if o["number"] < m["number"]}]
check(duplicates and not {m["number"] for m in duplicates} & movie_ids,
      "a second copy of a title (put into the library by hand, not in Radarr) has none")
for days in (7, 30):
    analysis = stats.bazarr_analysis(days)
    numbers = stats.bazarr_numbers(days, analysis=analysis)
    by_day = stats.bazarr_by_day(days, analysis=analysis)
    how = [r for r in stats.bazarr_actions(days, analysis=analysis) if r["action"] not in ("synced", "deleted")]
    languages = stats.bazarr_languages(days, analysis=analysis)
    providers = stats.bazarr_providers(days, analysis=analysis)
    scores = stats.bazarr_scores(days, analysis=analysis)
    upgrades = stats.bazarr_upgrades(days, analysis=analysis)
    changed = stats.bazarr_changed(days, analysis=analysis)
    speed = stats.bazarr_speed(days, analysis=analysis)
    check(numbers["obtained_count"] >= 15 and numbers["automatic"] >= 10 and numbers["upgrade_count"] >= 1,
          f"{days} days: the numbers ({numbers})")
    check(len(by_day) >= 3 and sum(r["episodes"] for r in by_day) > 0 and sum(r["movies"] for r in by_day) > 0,
          f"{days} days: subtitles on several days, for episodes and for movies ({len(by_day)} days)")
    check(len(how) >= 2, f"{days} days: obtained in more ways than one ({how})")
    check(len(languages) >= 2 and len(providers) >= 3, f"{days} days: several languages and providers"
          f" ({[r['language'] for r in languages]}, {[r['provider'] for r in providers]})")
    check(scores["count"] >= 10 and sum(1 for n in scores["buckets"].values() if n) >= 2,
          f"{days} days: the match with a spread ({scores})")
    check(upgrades["count"] >= 1 and upgrades["paired"] >= 1 and (upgrades["improvements"] or 0) > 0,
          f"{days} days: upgrades to better subtitles ({upgrades})")
    check(len(changed) >= 1, f"{days} days: subtitles obtained more than once ({len(changed)})")
    check(speed["count"] >= 5 and sum(1 for n in speed["buckets"].values() if n) >= 3,
          f"{days} days: how fast subtitles came after the file, in several buckets ({speed['count']}, {speed['buckets']})")
check(len(stats.bazarr_languages(30)) == 3, f"30 days: Czech, English and Slovak ({stats.bazarr_languages(30)})")
whole_speed = stats.bazarr_speed(3650)
check(whole_speed["before_bazarr"] > 0 and whole_speed["not_imported"] == 0,
      f"the files from before Bazarr came are not counted, every other file has its import ({whole_speed})")
check(stats.bazarr_numbers(365)["obtained_count"] > stats.bazarr_numbers(30)["obtained_count"]
      > stats.bazarr_numbers(7)["obtained_count"], "the year has more than 30 days, 30 days more than 7")

print()
print("--- (b2) the live state: what is missing, the providers ---")
bazarr_live.CACHE.clear()
live = asyncio.run(bazarr.live_state())
check(live["available"] and not live["error"], f"the live state loads ({live['error']!r})")
check((live["episode_count"] or 0) > 0 and (live["movie_count"] or 0) > 0,
      f"episodes and movies without subtitles ({live['episode_count']}, {live['movie_count']})")
check(bool(live["languages"]) and {r["language"] for r in live["languages"]} <= {"cs", "en", "sk"},
      f"the missing languages stored by the sync ({live['languages']})")
real_now = demo_world.now
base = datetime.now(UTC).replace(microsecond=0)
later_counts = []
for hours in (12, 25):
    demo_world.now = lambda at=base + timedelta(hours=hours): at
    badges = answer("badges") or {}
    later_counts.append((hours, badges.get("episodes"), badges.get("movies")))
demo_world.now = real_now
check(all((episodes_missing or 0) > 0 and (movies_missing or 0) > 0 for _h, episodes_missing, movies_missing in later_counts),
      f"something is still missing in 12 and in 25 hours ({later_counts})")
providers_now = live["providers"] or []
throttled = [p for p in providers_now if not p["fine"]]
check(len(providers_now) >= 4 and len(throttled) == 1 and live["limited_count"] == 1,
      f"several providers, one throttled ({[(p['name'], p['state']) for p in providers_now]})")
check(bool(throttled) and throttled[0]["state"] not in ("", "Good") and throttled[0]["again"].startswith("in "),
      f"with a reason and when Bazarr tries again ({throttled})")
check(throttled and throttled[0]["name"] in providers_used, "a provider the history knows")

print()
print("--- (d) the page and every card, without a connection error ---")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
page = client.get("/arr/bazarr")
check(page.status_code == 200, f"/arr/bazarr gives 200 ({page.status_code})")
check(not [e for e in ERRORS if e in page.text], f"the page has no connection error ({[e for e in ERRORS if e in page.text]})")
check(any(s["title"] in page.text for s in series) or any(m["title"] in page.text for m in movies),
      "the page shows the demo's titles")


def card(key: str, **parameters) -> tuple[int, str]:
    response = client.get("/partials/cards?" + urlencode({"keys": key, **parameters}), headers={"X-Requested-With": "fetch"},
                          follow_redirects=False)
    return response.status_code, response.text


broken = []
for one in registry.APPS["bazarr"].cards():
    for days in (7, 30):
        status, text = card(one.key, days=days)
        errors = [e for e in ERRORS if e in text]
        if status != 200 or f'data-card-key="{one.key}"' not in text or errors:
            broken.append(f"{one.key} ({days}): {status} {errors}")
check(not broken, f"each of the {len(registry.APPS['bazarr'].cards())} cards gives 200 without a connection error ({broken[:4]})")
status, text = card("bazarr_providers_now")
check(status == 200 and bool(throttled) and throttled[0]["name"] in text and throttled[0]["state"] in text,
      "the providers' card shows the throttled one with its reason")
check(registry.APPS["bazarr"].downloading is None, "Bazarr has no card Downloading")

print()
print("--- (f) the answers in Bazarr's shape ---")
status = (answer("system/status") or {}).get("data") or {}
check(str(status.get("bazarr_version", "")).startswith("1.") and status.get("timezone"),
      f"system/status: Bazarr 1 with its zone ({status.get('bazarr_version')}, {status.get('timezone')})")
zone = ZoneInfo(status.get("timezone") or "UTC")
for kind, id_field in (("episodes", "sonarrEpisodeId"), ("movies", "radarrId")):
    history = whole(kind)
    first_page = answer(f"{kind}/history", start=0, length=5) or {}
    check(len(history) == first_page.get("total") and len(first_page.get("data") or []) == 5,
          f"{kind}/history in pages, `total` the whole count ({len(history)}, {first_page.get('total')})")
    texts = [r.get("parsed_timestamp", "") for r in history]
    try:
        times = [datetime.strptime(t, "%m/%d/%y %H:%M:%S") for t in texts]
    except ValueError:
        times = []
    check(bool(times) and times == sorted(times, reverse=True), f"{kind}: newest first, the time as text (%x %X)")
    stored = {r["happened_at"] for r in rows if r["kind"] == kind[:-1]}
    converted = {t.replace(tzinfo=zone).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") for t in times}
    check(converted == stored, f"{kind}: the text in Bazarr's zone is the stored time ({len(converted ^ stored)} differ)")
    check(all(isinstance(r.get("language"), dict) and r["language"].get("code2") in ("cs", "en", "sk")
              and isinstance(r.get(id_field), int) and "description" in r and "subtitles_path" in r for r in history),
          f"{kind}: the record's fields")
    check(all((r.get("score") is None) == (r.get("action") in (0, 5, 6)) for r in history)
          and all(str(r["score"]).endswith("%") for r in history if r.get("score")),
          f"{kind}: a score in percent where Bazarr has one")
    wanted = answer(f"{kind}/wanted", start=0, length=1000) or {}
    data = wanted.get("data") or []
    check(bool(data) and wanted.get("total") == len(data)
          and all(r.get("missing_subtitles") and isinstance(r.get(id_field), int) for r in data),
          f"{kind}/wanted: items with the languages they lack ({len(data)})")
    known = set(episodes) if kind == "episodes" else {n for n, m in movie_numbers.items() if not m["missing"]}
    check(all(r[id_field] in known for r in data), f"{kind}/wanted: only items of the library with a file")
badges = answer("badges") or {}
health = (answer("system/health") or {}).get("data")
check(isinstance(health, list) and all(set(w) == {"object", "issue"} for w in health), f"system/health: the warnings' list ({health})")
check(badges.get("episodes") == len((answer("episodes/wanted", start=0, length=1000) or {}).get("data") or [])
      and badges.get("movies") == len((answer("movies/wanted", start=0, length=1000) or {}).get("data") or [])
      and badges.get("providers") == 1 and badges.get("status") == len(health or []),
      f"badges: the counts of the wanted lists, providers and warnings ({badges})")
providers_answer = (answer("providers") or {}).get("data") or []
check(providers_answer and all(set(p) == {"name", "status", "retry"} for p in providers_answer)
      and [p["name"] for p in providers_answer] == sorted(p["name"] for p in providers_answer),
      f"providers: name, status, retry ({providers_answer[:2]})")
history_before = answer("episodes/history", start=0, length=50)
check(ask("POST", "system/tasks", taskid="search_wanted_movies").status_code == 204
      and answer("episodes/history", start=0, length=50) == history_before, "a write is accepted and changes nothing")
check(ask("GET", "no/such/path").status_code == 404 and ask("GET", "system/languages/profiles").status_code == 404,
      "unknown paths are 404")

print()
print("--- (c) every path the code asked has an answer ---")
paths = {path for _method, path, _status in ASKED}
missing_paths = {"system/status", "episodes/history", "movies/history", "episodes/wanted", "movies/wanted", "badges",
                 "providers", "system/health"} - paths
check(not missing_paths, f"the seed and the page asked what a real Bazarr is asked ({sorted(missing_paths)})")
check(all(status < 400 for _method, _path, status in ASKED),
      f"every answer fine, no 404 ({sorted({(p, s) for _m, p, s in ASKED if s >= 400})[:4]})")

print()
print("--- (g) later syncs fetch only what came since; a refresh fetches the same again ---")
before = bazarr.event_count()
again = asyncio.run(bazarr.synchronize())
check(again["new_count"] == 0 and bazarr.event_count() == before, f"a second sync adds nothing ({again})")
tomorrow = base + timedelta(hours=20)
demo_world.now = lambda: tomorrow
later = sum(len(whole(kind)) for kind in ("episodes", "movies"))
later_sync = asyncio.run(bazarr.synchronize())
demo_world.now = real_now
check(later >= before and later_sync["new_count"] == later - before and bazarr.event_count() == later,
      f"a sync 20 hours later adds exactly what came since ({before} -> {later}, {later_sync})")
WARNINGS.clear()
demodata.refresh()
check(bazarr.event_count() == before and bazarr.sync_state()["result"] == "ok",
      f"the same history after the refresh ({before} -> {bazarr.event_count()})")
check(not WARNINGS, f"the refresh logged no warning ({WARNINGS[:3]})")

print()
print("--- (h) no network ---")
check(not NETWORK, f"no socket was touched ({NETWORK[:3]})")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(demo_switch.transport(bazarr.NAME) is None, "outside the demo mode the switch gives Bazarr's client the network")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
