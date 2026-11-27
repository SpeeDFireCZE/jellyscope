# -*- coding: utf-8 -*-
r"""The demo as a whole: video, music, books, the six *Arr apps and Seerr, prepared the way a server starts it.

In the demo mode (JELLYSCOPE_DEMO) `demodata.prepare()` makes the library and connects the seven made-up apps
through their real syncs (demo_apps). The tests of each app watch its own data; this one watches the demo as a
visitor and its owner meet it:

(a) a database an earlier version made (the library without the apps, an older content version) is made again by
    prepare() at once: all seven apps are connected and filled, and the start's printout (demo_apps.summary) names
    them;
(b) one made-up app that fails during the seed - its handler raises, or it answers what no app answers - does not
    keep the demo from starting: prepare() ends, the other six apps are filled and the log has exactly one line
    about it;
(c) a visitor (the demo account) who tries an app's "Synchronize", "Delete downloaded data" or saving its settings
    gets the demo's refusal, as for every other button, and nothing changes;
(d) the demo account's menu has Movies & series, Music, Books, *Arr with the six apps and Seerr, and every page of
    it opens; the custom overview with every card of the registry (cards.save_layout) is drawn whole, without an
    error; the Disks card and Library growth know the storage (a capacity entered by hand, the snapshots of the
    last weeks): its capacity, the free space and when it runs out;
(e) prepare() on a clean database takes at most 15 s (the container's healthcheck waits 40 s for the start);
(f) prepare() logs no warning and no error - on a clean database and when it makes an old one again;
(g) the daily refresh (demodata.refresh) while the apps' live cards are asked and Prowlarr's loop measures (the
    lifespan runs): it ends, no answer falls, no error in the log, every app is filled again and nothing is doubled.
No socket is touched during the whole test.

Every part gets a clean database of its own (on PostgreSQL every table is dropped, tests_on_pg).

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (the plan's Review Focus 2-5)

Run:
    .venv/bin/python tests/test_demo_whole.py
"""
from __future__ import annotations

import importlib
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
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
os.environ["SECRET_KEY"] = "test-key-demo-whole"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import (accounts, cards, config, db, demo_apps, demo_switch, demodata, formatting, navigation,  # noqa: E402
                        seerr, stats, tasks, web)
from jellyscope.arr import registry  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Every way out of the process is recorded and refused - but the test's own database on PostgreSQL (tests/_network.py).
NETWORK = refuse_network()

# What the log gets, from INFO up - what the application's log file gets (applog.setup).
RECORDS: list[logging.LogRecord] = []


class _Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        RECORDS.append(record)


logging.getLogger().addHandler(_Collect(level=logging.INFO))
logging.getLogger("jellyscope").setLevel(logging.INFO)


def lines(records: list[logging.LogRecord], level: int = logging.WARNING) -> list[str]:
    return [f"{r.levelname} {r.name}: {r.getMessage()}"[:240] for r in records if r.levelno >= level]


# The seven apps the demo connects: the registry's in its order, Seerr last.
KEYS = (*registry.APPS, "seerr")
NAMES = {**{key: app.name for key, app in registry.APPS.items()}, "seerr": seerr.NAME}


def seven() -> dict[str, dict]:
    """Each app's state: connected, how many records, the last sync's result, its settings."""
    state = {key: {"connected": key in registry.connected(), "count": app.event_count(), "sync": app.sync_state(),
                   "settings": app.stored_settings()} for key, app in registry.APPS.items()}
    state["seerr"] = {"connected": seerr.is_connected(), "count": seerr.request_count(), "sync": seerr.sync_state(),
                      "settings": seerr.stored_settings()}
    return state


def filled(state: dict, key: str) -> bool:
    one = state[key]
    return one["connected"] and one["count"] > 0 and one["sync"].get("result") == "ok" and not one["sync"].get("error")


def forget_live() -> None:
    """The apps' live answers kept in memory - of the database before."""
    for module in [f"jellyscope.arr.{key}.live" for key in registry.APPS] + ["jellyscope.seerr.live"]:
        cache = getattr(importlib.import_module(module), "CACHE", None)
        if cache is not None:
            cache.clear()


def clean_database(name: str) -> None:
    """A clean database for the next part: a new SQLite file; on PostgreSQL every table of Jellyscope dropped."""
    if db.database_config().is_postgres:
        with db.connect() as conn:
            tasks._drop_jellyscope_tables(conn)
            conn.commit()
    else:
        os.environ.update(DATABASE_PATH=str(Path(_tmp) / f"{name}.db"))
        config.load_config(reload=True)
        db.database_config(reload=True)
    db.forget_settings()
    demo_switch.clear()
    forget_live()
    db.init_db()


def prepare() -> tuple[dict, list[logging.LogRecord], float]:
    """demodata.prepare() the way run.py calls it: its counts, what it logged, how long it took."""
    first = len(RECORDS)
    started = time.perf_counter()
    counts = demodata.prepare(quiet=True)
    took = time.perf_counter() - started
    return counts, RECORDS[first:], took


print("--- (e) (f) prepare() on a clean database: fast and quiet ---")
db.init_db()
counts, logged, took = prepare()
state = seven()
check(counts["items"] > 0 and counts["plays"] > 0, f"the library and the plays are made ({counts})")
check(took <= 15, f"prepare() takes at most 15 s ({took:.1f} s)")
print(f"       prepare() on a clean database: {took:.2f} s")
check(not lines(logged), f"it logs no warning and no error ({lines(logged)[:3]})")
check(all(filled(state, key) for key in KEYS),
      f"all seven apps are connected and filled ({ {k: (v['connected'], v['count'], v['sync'].get('result')) for k, v in state.items()} })")
summary = demo_apps.summary()
check(list(summary) == [NAMES[key] for key in KEYS] and all(n > 0 for n in summary.values()),
      f"the start's printout names the seven apps with their records ({summary})")
check(not NETWORK, f"no socket ({NETWORK[:2]})")

print()
print("--- (d) the demo account's menu and the custom overview with every card ---")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
account = accounts.get_by_name(demodata.DEMO_USERNAME)
first_web = len(RECORDS)
entries = {entry["key"]: entry for entry in navigation.menu(account, "/arr/sonarr")}
check({"video", "music", "books", "arr", "seerr"} <= set(entries), f"Movies & series, Music, Books, *Arr and Seerr ({list(entries)})")
arr_pages = [page["url"] for page in entries.get("arr", {}).get("pages", [])]
check(arr_pages == [f"/arr/{key}" for key in registry.APPS], f"*Arr has the six apps in the registry's order ({arr_pages})")
page = client.get("/arr/sonarr").text
links = ["/video/overview", "/music/overview", "/books/overview", *(f"/arr/{key}" for key in registry.APPS), "/seerr"]
check(all(f'href="{url}"' in page for url in links), f"the page's menu links them ({[u for u in links if f'href={chr(34)}{u}{chr(34)}' not in page]})")
ERRORS = ("Traceback", "not responding", "not answering", "could not", "Internal Server Error")


def errors_in(text: str) -> list[str]:
    return [error for error in ERRORS if error in text]


broken = []
for entry in entries.values():
    for one in entry["pages"] or [entry]:
        response = client.get(one["url"])
        problems = errors_in(response.text)
        if response.status_code != 200 or problems:
            broken.append(f"{one['url']}: {response.status_code} {problems}")
check(not broken, f"every page of the menu opens without an error ({broken[:4]})")
# The storage: the made-up Jellyfin (10.11) does not break the disks down, so its owner entered the capacity by hand
# (Settings -> Data) and the app wrote its daily snapshots - the library's and the one disk row's - up to the seed.
seed_day = datetime.strptime(db.get_setting(demodata.SEED_KEY, ""), db.TIME_FORMAT).replace(tzinfo=UTC) \
    .astimezone(formatting.zone()).date().isoformat()
disk_days = db.query_all("SELECT day, kind, source, total, free, library FROM library_disk_snapshot ORDER BY day")
library_days = db.query_all("SELECT day, size_bytes, free_space FROM library_snapshot ORDER BY day")
check(len(disk_days) >= 31 and disk_days[-1]["day"] == seed_day
      and all(d["kind"] == "single" and d["source"] == "manual" and d["total"] and d["free"] for d in disk_days)
      and [d["day"] for d in disk_days] == [d["day"] for d in library_days]
      and [(d["free"], d["library"]) for d in disk_days] == [(d["free_space"], d["size_bytes"]) for d in library_days],
      f"the storage measured every day up to the seed: a capacity entered by hand, the free space ({len(disk_days)} days,"
      f" {disk_days[-1:] if disk_days else None})")
# The free space is the capacity less the library, which grew over the weeks (two titles of the archive went today).
check(len(disk_days) >= 3 and all(d["free"] == d["total"] - d["library"] for d in disk_days)
      and disk_days[0]["free"] > disk_days[-2]["free"], "the free space is the capacity less the library, shrinking as it grew")
for days in (7, 30, 365):
    row = stats.disks_overview(days, stats.library_growth, stats.disk_growth)["disks"][0]
    growth = stats.library_growth(days)
    check(row["source"] == "manual" and row["total"] and row["free"] and row["days_left"]
          and growth.get("free_space") == row["free"] and growth.get("days_left") == row["days_left"]
          and growth.get("backfilled") == (days == 365),
          f"{days} days: the Disks card has the capacity, the free space and when it runs out, Library growth the same"
          f" ({row['total']}, {row['free']}, {row['days_left']} days; measured {not growth.get('backfilled')})")
check("capacity entered by hand" in client.get("/library").text, "the Library page's Disks card says where the capacity comes from")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Demo")
every = [card.key for card in cards.ALL_CARDS]
saved = cards.save_layout(every)
db.forget_settings()
check(saved == every, f"the custom overview holds every card of the registry ({len(saved)} of {len(every)})")
unavailable = [card.key for card in cards.ALL_CARDS if not cards.is_available(card)]
check(not unavailable, f"in the demo every card has what it reads from ({unavailable[:5]})")
for days in (7, 30, 365):
    response = client.get(f"/dashboard?days={days}")
    drawn = re.findall(r'class="dash-card[^"]*" data-card="(\w+)"', response.text)
    problems = errors_in(response.text)
    check(response.status_code == 200 and drawn == every and not problems,
          f"{days} days: the custom overview draws all {len(every)} cards without an error ({response.status_code}, {len(drawn)}, {problems})")
check(not lines(RECORDS[first_web:]), f"looking round the demo logs no warning ({lines(RECORDS[first_web:])[:3]})")

print()
print("--- (c) a visitor's Synchronize, Delete downloaded data and Save of an app: refused, nothing changes ---")
REFUSED = "info: This is a demo, so nothing here changes. On your own install the button works."


def flash(page: str) -> str:
    """The message the page shows after the redirect ("level: text"), or "". Only the toast: in the demo every page
    carries the same sentence for the script that stops a form in the browser (base.html, demo-flash)."""
    found = re.search(r'id="flash-toast">\s*<div class="flash (\w+)">\s*<span class="dot"></span>\s*<span>(.*?)</span>',
                      page, re.S)
    return f"{found.group(1)}: {found.group(2).strip()}" if found else ""


before, settings_before = seven(), db.get_settings()
attempts = []
for key in registry.APPS:
    attempts += [(f"/settings/arr/{key}/sync", {}), (f"/settings/arr/{key}/delete", {}),
                 (f"/settings/arr/{key}", {"enabled": "1", "url": f"http://elsewhere.example:{8000 + len(attempts)}",
                                            "api_key": "f" * 32, "action": "save"})]
attempts += [("/settings/seerr/sync", {}), ("/settings/seerr/delete", {}),
             ("/settings/seerr", {"enabled": "", "url": "http://elsewhere.example:5055", "api_key": "f" * 32, "action": "save"})]
not_refused = []
for path, data in attempts:
    response = client.post(path, data=data, follow_redirects=False)
    shown = flash(client.get("/settings?section=arr").text) if response.status_code == 303 else ""
    if response.status_code != 303 or shown != REFUSED:
        not_refused.append(f"{path} {response.status_code} {shown[:60]!r}")
check(not not_refused, f"each of the {len(attempts)} gets the demo's message instead of the action ({not_refused[:4]})")
db.forget_settings()
after = seven()
check(after == before, f"every app keeps its records, its settings and its last sync ({[k for k in KEYS if after[k] != before[k]]})")
changed = sorted(k for k in set(settings_before) | set(db.get_settings()) if settings_before.get(k) != db.get_settings().get(k))
check(not changed, f"no setting changed ({changed[:5]})")

print()
print("--- (b) one made-up app fails during the seed: the demo starts, the other six are filled, one line ---")


def broken_radarr(request: httpx.Request) -> httpx.Response:
    raise RuntimeError("a made-up failure of the demo's Radarr")


def odd_lidarr(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>Lidarr is starting, please wait</html>", headers={"Content-Type": "text/html"})


for key, handler, what in (("radarr", broken_radarr, "raises"), ("lidarr", odd_lidarr, "answers a page, not JSON")):
    clean_database(f"failing-{key}")
    made_up = importlib.import_module(f"jellyscope.arr.{key}.demo")
    original = made_up.handler
    made_up.handler = handler
    try:
        try:
            counts, logged, took = prepare()
            ended = True
        except Exception as error:   # noqa: BLE001 - the check says it
            counts, logged, ended = {"items": 0}, RECORDS[-5:], False
            print(f"       prepare() raised: {error!r}")
    finally:
        made_up.handler = original
    state = seven()
    others = [k for k in KEYS if k != key]
    check(ended and counts["items"] > 0, f"{NAMES[key]} {what}: prepare() ends and the library is made ({counts})")
    check(all(filled(state, k) for k in others),
          f"the other six are filled ({ {k: (state[k]['connected'], state[k]['count']) for k in others} })")
    about = [r for r in logged if r.levelno >= logging.WARNING or NAMES[key] in r.getMessage() and "fail" in r.getMessage()]
    check(len(about) == 1 and about[0].levelno == logging.WARNING and NAMES[key] in about[0].getMessage()
          and about[0].exc_info is None, f"the log has exactly one line about it, a warning naming {NAMES[key]} ({lines(about, 0)})")
    check(demodata.already_seeded() and not demodata.content_changed(),
          "the seed is stored as done: the next start does not make it again")

print()
print("--- (a) a database an earlier version made (no apps, an older content version) is made again with the apps ---")
clean_database("earlier")
original_load, original_seed = demo_apps.load_fakes, demo_apps.seed_apps
demo_apps.load_fakes, demo_apps.seed_apps = (lambda: None), (lambda: {})
try:
    demodata.prepare(quiet=True)
finally:
    demo_apps.load_fakes, demo_apps.seed_apps = original_load, original_seed
OLDER = "2"
db.set_setting(demodata.CONTENT_KEY, OLDER)
db.forget_settings()
earlier = seven()
check(not any(v["connected"] or v["count"] for v in earlier.values()) and demodata.already_seeded() and not demodata.is_stale(),
      f"the earlier demo: a fresh library, no app connected, nothing downloaded ({ {k: v['count'] for k, v in earlier.items()} })")
check(demodata.content_changed() and int(OLDER) < demodata.DEMO_CONTENT_VERSION, f"its content version {OLDER} is older")
counts, logged, took = prepare()
state = seven()
check(counts["items"] > 0, f"prepare() makes it again at once ({counts}, {took:.1f} s)")
check(db.get_setting(demodata.CONTENT_KEY, "") == str(demodata.DEMO_CONTENT_VERSION), "with today's content version")
check(all(filled(state, key) for key in KEYS),
      f"all seven apps are connected and filled ({ {k: (v['connected'], v['count'], v['sync'].get('result')) for k, v in state.items()} })")
summary = demo_apps.summary()
check(list(summary) == [NAMES[key] for key in KEYS] and all(n > 0 for n in summary.values()),
      f"the start's printout names the seven apps ({summary})")
check(not lines(logged), f"it logs no warning and no error ({lines(logged)[:3]})")
again, logged, took = prepare()
check(again == {"items": 0, "plays": 0, "users": 0} and seven() == state, "the next start keeps what it made")

print()
print("--- (g) the daily refresh while the live cards are asked and Prowlarr's loop measures ---")
LIVE_CARDS = [card.key for card in cards.ALL_CARDS if card.group in NAMES.values() and not card.uses_period]
LIVE_URLS = ([f"/partials/cards?keys={key}" for key in LIVE_CARDS]
             + [f"/partials/arr/{key}/downloading" for key, app in registry.APPS.items() if app.downloading]
             + ["/partials/now-playing"])


def library() -> dict[str, int]:
    """The demo's rows that a write on the side could double: the library, the plays, the live stream, Prowlarr's
    samples, the requests deleted in Seerr, the storage's daily snapshots."""
    sql = {"items": "SELECT COUNT(*) FROM items", "plays": "SELECT COUNT(*) FROM playback",
           "live": f"SELECT COUNT(*) FROM playback WHERE session_key = '{demodata.LIVE_KEY}'",
           "tracks": "SELECT COUNT(*) FROM music_track", "books": "SELECT COUNT(*) FROM book",
           "samples": "SELECT COUNT(*) FROM prowlarr_sample", "disk_days": "SELECT COUNT(*) FROM library_disk_snapshot",
           "library_days": "SELECT COUNT(*) FROM library_snapshot",
           "deleted_requests": "SELECT COUNT(*) FROM seerr_request WHERE deleted_at IS NOT NULL"}
    return {name: int(db.query_value(query)) for name, query in sql.items()}


seeded = library()                                # what the seed wrote, before the loop runs
# The lifespan runs: Prowlarr's loop and the live stream. A page that falls is a 500 here, as for a visitor.
with TestClient(web.app, raise_server_exceptions=False) as live_client:
    live_client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD},
                     follow_redirects=False)
    before = seven()
    first = len(RECORDS)
    outcome: dict[str, object] = {}

    def _refresh() -> None:
        try:
            outcome["counts"] = demodata.refresh()
        except Exception as error:   # noqa: BLE001 - the check says it
            outcome["error"] = error

    worker = threading.Thread(target=_refresh)
    asked, answers = 0, []
    worker.start()
    while worker.is_alive():
        forget_live()             # every round asks the made-up apps, not the memory
        url = LIVE_URLS[asked % len(LIVE_URLS)]
        answers.append((url, live_client.get(url).status_code))
        asked += 1
    worker.join()
    during = RECORDS[first:]
    after, rows_after = seven(), library()
check("error" not in outcome and outcome.get("counts", {}).get("items", 0) > 0,
      f"the refresh ends ({outcome.get('error')!r}, {outcome.get('counts')})")
failed = sorted({f"{url} {status}" for url, status in answers if status >= 500})
check(asked >= len(LIVE_URLS) and not failed,
      f"{asked} live answers during it, none of them fell ({failed[:4]})")
check(not [r for r in during if r.levelno >= logging.ERROR or r.exc_info],
      f"no error and no exception in the log meanwhile ({lines(during, logging.ERROR)[:3]})")
print(f"       warnings meanwhile: {lines(during)[:4]}")
steady = [k for k in KEYS if k != "prowlarr"]
check(all(after[k]["count"] == before[k]["count"] for k in steady) and all(filled(after, k) for k in KEYS),
      f"every app is filled again, nothing doubled ({ {k: (before[k]['count'], after[k]['count']) for k in KEYS} })")
# Prowlarr's history goes on with the clock (its RSS and searches on an absolute grid): the few seconds between the two
# seeds may add an event or two, never a second history.
check(0 <= after["prowlarr"]["count"] - before["prowlarr"]["count"] <= 20,
      f"Prowlarr's history is the same, the clock aside ({before['prowlarr']['count']} -> {after['prowlarr']['count']})")
# Prowlarr's samples: the seed writes them again (fill_past deletes every sample first, a stray one from a round during
# the refresh is not kept); the loop may store a new one after the seed (its chain broke, arr/prowlarr/demo.py).
check({k: v for k, v in rows_after.items() if k != "samples"} == {k: v for k, v in seeded.items() if k != "samples"}
      and rows_after["live"] == 1 and 0 <= rows_after["samples"] - seeded["samples"] <= 2,
      f"the library, the plays, one live stream, Prowlarr's samples, Seerr's deleted request, the storage's days"
      f" ({seeded} -> {rows_after})")

print()
print("--- outside the demo mode ---")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(registry.connected() and demo_apps.summary() == {},
      "the printout names no app of a real installation, connected or not")
os.environ["JELLYSCOPE_DEMO"] = "1"
config.load_config(reload=True)

print()
check(not NETWORK, f"no socket during the whole test ({NETWORK[:2]})")
print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
