# -*- coding: utf-8 -*-
r"""The demo's made-up Readarr (arr/readarr/demo.py): the demo's books through Readarr's real code.

In the demo mode (JELLYSCOPE_DEMO) the seed connects Readarr at a made-up address and runs its real sync and
counts (demo_apps.seed_apps) against the made-up Readarr, which answers from the demo's books libraries
(demo_world.books) in the shape of Readarr v1 (0.4). The page and its live cards then ask it the same way they
ask a real one. The unit is the book: a grab per book, an import per file (an audiobook in parts has several).
Watched:

(a) after demodata.prepare() Readarr is connected at its made-up address, its sync ended well, the tab says
    "Readarr" and the seed logged no warning;
(b) its history has imports in the last 7, 30 and 365 days, upgrades, one or two failed downloads, a spread of
    the download times, its indexers and clients, one manual import; every book of the library is imported with
    as many files as it has and paired with it (by the ISBN where the library has one), the books coming out are
    not; the counts of missing and cutoff are not zero;
(b2) the recent weeks: new e-books and audiobooks of the last weeks are in the library, Readarr knew them before
    they came out, grabbed and imported each after its release and before Jellyfin took it in; the 7- and 30-day
    views have values of "how soon after release" in several buckets and an upgrade, the 30 days a late one; the
    calendar has the books coming out, which the library does not have; the new books are read and listened to
    after they came;
(c) every path the code asked during the seed and the page has an answer: no 404 but covers;
(d) the page /arr/readarr, its slice "Downloading" and each of its cards (/partials/cards) give 200 without a
    connection error;
(e) the card "Downloading" differs between a moment and ten minutes later (demo_world.now substituted);
(f) the answers have Readarr's shape: the history in pages oldest first and since a date, with the book and the
    author when asked, the queue (2-4 downloads, one with a warning), the calendar for 14 days, one health warning
    (an update is available), a POST command accepted without a change, the books with their editions (ISBN-13,
    e-book or not) and the authors, covers and unknown paths 404;
(g) a refresh (demodata.refresh) deletes Readarr's data and fetches the same again;
(h) no socket is touched during the whole test.

Spec: docs/superpowers/specs/2026-11-26-demo-full-design.md (goal 2)

Run:
    .venv/bin/python tests/test_readarr_demo.py
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
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
os.environ["SECRET_KEY"] = "test-key-readarr-demo"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import config, db, demo_apps, demo_switch, demo_world, demodata, services, stats, web  # noqa: E402
from jellyscope.arr import readarr, registry  # noqa: E402
from jellyscope.stats.shared import UNKNOWN  # noqa: E402

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

# The made-up Readarr, wrapped so the test sees every request the code sends it: (method, path after /api/v1/, status).
# The registry looks the handler up only when the fakes are loaded (registry._demo), so the wrapper is what gets
# registered.
ASKED: list[tuple[str, str, int]] = []
try:
    made_up = importlib.import_module("jellyscope.arr.readarr.demo")
except ImportError as error:
    made_up = None
    print(f"       (jellyscope.arr.readarr.demo does not import: {error})")
ORIGINAL = made_up.handler if made_up is not None else None


def _recording(request: httpx.Request) -> httpx.Response:
    response = ORIGINAL(request)
    ASKED.append((request.method, request.url.path.split("/api/v1/", 1)[-1], response.status_code))
    return response


if made_up is not None:
    made_up.handler = _recording


def ask(method: str, path: str, body: dict | None = None, **params) -> httpx.Response:
    """One request straight to the made-up Readarr (not recorded), at the address the seed stores."""
    if ORIGINAL is None:
        return httpx.Response(599)
    return ORIGINAL(httpx.Request(method, f"http://readarr.demo/api/v1/{path}", params=params or None, json=body))


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


def isbn_ok(text: str) -> bool:
    """An ISBN-13: thirteen digits with the right check digit."""
    if not re.fullmatch(r"97[89][0-9]{10}", str(text or "")):
        return False
    total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(text[:12]))
    return (10 - total % 10) % 10 == int(text[12])


ERRORS = ("not responding", "could not", "not answering", "Traceback")
INCLUDE = {"includeBook": "true", "includeAuthor": "true"}

db.init_db()
demodata.prepare(quiet=True)
catalog = demo_world.books()
seed_warnings = list(WARNINGS)
titles = {entry["title"] for entry in catalog}
by_number = {entry["number"]: entry for entry in catalog}
by_title = {entry["title"]: entry for entry in catalog}

print("--- (a) the seed connects the made-up Readarr and syncs it through the real code ---")
settings = registry.APPS["readarr"].stored_settings()
check(bool(settings["enabled"]) and readarr.is_connected(), f"Readarr is connected ({settings})")
check(settings["url"] == "http://readarr.demo", f"at its made-up address ({settings['url']!r})")
check(registry.APPS["readarr"].demo is not None and made_up is not None
      and registry.APPS["readarr"].demo() is made_up.handler, "the registry gives the package's made-up handler (App.demo)")
check(demo_switch.registered(readarr.NAME), "the made-up Readarr is registered in the switch")
state = readarr.sync_state()
check(state["result"] == "ok" and not state["error"], f"its sync ended well ({state})")
check(readarr.app_name() == "Readarr" and registry.APPS["readarr"].shown_name() == "Readarr",
      f"the tab says Readarr, not a fork ({readarr.app_name()})")
check(not seed_warnings, f"the seed logged no warning ({seed_warnings[:3]})")

print()
print("--- (b) a history over the whole period, like a real one ---")
check(readarr.event_count() > 80, f"the history is downloaded ({readarr.event_count()} records)")
for days in (7, 30, 365):
    imports = sum(row["count"] for row in stats.readarr_additions(days))
    check(imports > 0, f"book imports in the last {days} days ({imports})")
upgrades = stats.readarr_upgrades(365)
check(upgrades["count"] >= 3 and stats.readarr_upgrades(30)["count"] > 0,
      f"upgrades in the year and in the last 30 days ({upgrades['count']}, {stats.readarr_upgrades(30)['count']})")
check(upgrades["transitions"] and all(row["old"] != UNKNOWN for row in upgrades["transitions"]),
      f"every upgrade knows the quality it replaced ({upgrades['transitions'][:4]})")
failed = stats.readarr_failures(365)
check(1 <= failed <= 2, f"one or two failed downloads in the year ({failed})")
speed = stats.readarr_speed(365)
check(speed["downloads"]["count"] > 10 and speed["downloads"]["median"] != speed["downloads"]["p90"],
      f"download times with a spread ({speed['downloads']})")
whole = stats.readarr_speed(3650)["since_release"]
check(whole["not_counted"] > 0 and not whole["unknown"] and not whole["before_release"],
      f"books of authors added after their release (the back catalog) are not counted, none unknown ({whole})")
reliability = {row["name"]: row for row in stats.readarr_indexer_reliability(365)}
flaky = reliability.get(demo_world.FLAKY_INDEXER, {})
check(set(reliability) <= {name for name, _protocol in demo_world.INDEXERS} - {demo_world.DISABLED_INDEXER}
      and len(reliability) >= 3, f"grabs from the made-up indexers, none from the switched-off one ({sorted(reliability)})")
check(flaky.get("failed", 0) >= 1 and flaky.get("success") is not None and flaky["success"] < 1,
      f"the flaky indexer has the failures ({flaky})")
check(not sum(row["unmatched"] for row in reliability.values()), "every grab carries its download id")
sources = stats.readarr_sources(3650)
clients = {row["name"] for row in sources["clients"]} - {UNKNOWN}
check(clients == set(demo_world.DOWNLOAD_CLIENTS.values())
      and {row["name"] for row in sources["protocols"]} - {UNKNOWN} == {"usenet", "torrent"},
      f"both clients and protocols ({sources['clients']}, {sources['protocols']})")
qualities = {row["name"] for row in sources["qualities"]}
check(len(qualities) >= 5 and {"EPUB", "M4B", "MP3"} <= qualities and len(sources["groups"]) >= 4,
      f"several qualities of e-books and audiobooks and release groups ({sorted(qualities)})")
manual = db.query_all("SELECT DISTINCT title_id FROM readarr_event WHERE event_type = 'bookFileImported' AND download_id IS NULL")
check(len(manual) == 1, f"one book came by a manual import, without a download ({[r['title_id'] for r in manual]})")
last_import: dict[int, dict] = {}
for one in stats.readarr_events(3650)["imports"]:
    last_import[one["title_id"]] = one
check(all(last_import.get(entry["number"], {}).get("file_count") == entry["parts"] for entry in catalog),
      "every book is in the history with as many files as the library has (an audiobook in parts too) "
      f"({[(e['number'], last_import.get(e['number'], {}).get('file_count'), e['parts']) for e in catalog if last_import.get(e['number'], {}).get('file_count') != e['parts']][:3]})")
check(any(entry["parts"] > 1 for entry in catalog), "an audiobook in several files is among them")
stored_titles = {row["title"] for row in db.query_all("SELECT DISTINCT title FROM readarr_event")}
check(stored_titles == titles, f"the history names the demo's books, each once ({sorted(stored_titles ^ titles)})")
library = stats.readarr_library()
own = {n: library[n] for n in library if n in by_number}
check(sorted(own) == sorted(by_number) and all(own[n]["book_id"] == by_number[n]["jellyfin_id"] for n in own),
      f"every book of the library is paired with its book, an audiobook with its first part ({sorted(set(own) ^ set(by_number))})")
check(not [n for n in library if n not in by_number], "a book the library does not have yet links nowhere")
isbns = {r["title_id"]: str(r["isbns"]).split("|") for r in db.query_all("SELECT title_id, isbns FROM readarr_title")}
with_isbn = [entry for entry in catalog if entry.get("isbn")]
check(with_isbn and all(entry["isbn"] in isbns.get(entry["number"], []) for entry in with_isbn),
      f"a book whose file carries an ISBN shares it with an edition in Readarr ({len(with_isbn)})")
counts = services.stored_counts("arr_readarr", services.settings_fingerprint("http://readarr.demo", demo_apps.api_key("readarr")))
check((counts["missing"] or 0) > 0 and (counts["cutoff"] or 0) > 0, f"the counts of missing and cutoff are not zero ({counts})")

print()
print("--- (b2) the recent weeks: new books in the library and in Readarr, books coming out ---")
new_books = getattr(demodata, "NEW_BOOKS", {})
coming = getattr(demodata, "COMING_BOOKS", [])
check(len(new_books) >= 5 and len(coming) >= 3, f"new books of the last weeks and books coming out ({len(new_books)}, {len(coming)})")
new_entries = [by_title.get(value[0]) for value in new_books.values()]
first_new = len(demodata.BOOKS) + 1
check(all(new_entries) and [e["number"] for e in new_entries if e] == list(range(first_new, first_new + len(new_books)))
      and catalog[0]["title"] == demodata.BOOKS[0][1] and catalog[0]["number"] == 1,
      f"they follow the library's books in the catalog, the old ones keep their numbers ({[e and e['number'] for e in new_entries]})")
kinds = [e["audiobook"] for e in new_entries if e]
check(kinds.count(True) >= 2 and kinds.count(False) >= 2, f"e-books and audiobooks, both libraries ({kinds})")
check({e["author"] for e in new_entries if e} <= {b[2] for b in demodata.BOOKS}, "by the demo's authors")
now = datetime.now(UTC)
check(all(e and now - timedelta(days=40) <= e["added_at"] <= now for e in new_entries), "they came to the library in the last weeks")
release = getattr(demo_world, "book_release", lambda entry: None)
known = {int(b.get("id") or 0): b for b in answer("GET", "book") or []}
first_imports = {row["title_id"]: row for row in db.query_all(
    "SELECT title_id, MIN(happened_at) AS happened_at, MIN(released_at) AS released_at FROM readarr_event"
    " WHERE event_type = 'bookFileImported' GROUP BY title_id")}
out_of_order = []
for entry in (e for e in new_entries if e):
    own_book = known.get(entry["number"], {})
    released, added = moment(own_book.get("releaseDate")), moment(own_book.get("added"))
    author_added = moment((own_book.get("author") or {}).get("added"))
    imported = first_imports.get(entry["number"])
    if not (released and added and author_added and imported and released == release(entry) and (released.hour, released.minute) == (0, 0)
            and author_added <= added <= released <= _utc(imported["happened_at"]) <= entry["added_at"]
            and _utc(imported["released_at"]) == released):
        out_of_order.append(entry["title"])
check(not out_of_order, "Readarr knew each before it came out, imported it after its release (midnight) and before"
      f" Jellyfin took it in, with that release date ({out_of_order})")
new_titles = {value[0] for value in new_books.values()}
for days in (7, 30):
    analysis = stats.readarr_events(days)
    waited = [i for i in analysis["imports"] if not i["upgrade"] and i["released_at"] and i["title"] in new_titles]
    since_release = stats.readarr_speed(days)["since_release"]
    filled = sum(1 for n in since_release["buckets"].values() if n)
    check(len(waited) >= 3 and since_release["count"] >= 3 and filled >= 3,
          f"{days} days: how soon after release has values of the new books in several buckets"
          f" ({len(waited)} of {since_release['count']}, {since_release['buckets']})")
    recent_upgrades = stats.readarr_upgrades(days)
    check(recent_upgrades["count"] >= 1, f"{days} days: an upgrade ({recent_upgrades['count']})")
check(any(row["title"] in new_titles for row in stats.readarr_upgrades(7)["titles"]), "a new book got a better version a little later")
check(stats.readarr_speed(30)["since_release"]["buckets"]["longer"] >= 1, "30 days: one book came late")
coming_list = getattr(demo_world, "coming_books", lambda at: [])(now)
check(len(coming_list) == len(coming) and all(c["title"] not in by_title for c in coming_list),
      f"the books coming out are not in the library ({[c['title'] for c in coming_list]})")
ahead = answer("GET", "calendar", start=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               end=(now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"), includeAuthor="true") or []
soon = {c["title"] for c in coming_list if now <= c["released"] <= now + timedelta(days=14)}
week = {c["title"] for c in coming_list if now <= c["released"] <= now + timedelta(days=7)}
check(len(soon) >= 2 and week and soon <= {c.get("title") for c in ahead if not (c.get("statistics") or {}).get("bookFileCount")},
      f"the calendar has the books coming out in 14 days (in 7 too), without a file ({sorted(soon)}, {[c.get('title') for c in ahead]})")
check(all(bool((c.get("statistics") or {}).get("bookFileCount")) == (c.get("title") in by_title) for c in ahead),
      "a book of the calendar has files exactly when the library has it")
new_rows = [r["id"] for r in db.query_all_in("SELECT id FROM book WHERE title IN ({})", sorted(new_titles))]
read = db.query_all_in("SELECT p.started_at, b.added_at FROM playback p JOIN book b ON b.id = p.item_id WHERE p.item_id IN ({})", new_rows)
check(len(read) >= 8 and all(p["started_at"] >= p["added_at"] for p in read),
      f"the new books are read and listened to, each after the book came ({len(read)})")

print()
print("--- (d) the page and every card, without a connection error ---")
client = TestClient(web.app)
client.post("/login", data={"username": demodata.DEMO_USERNAME, "password": demodata.DEMO_PASSWORD}, follow_redirects=False)
page = client.get("/arr/readarr")
check(page.status_code == 200, f"/arr/readarr gives 200 ({page.status_code})")
check(not [e for e in ERRORS if e in page.text], f"the page has no connection error ({[e for e in ERRORS if e in page.text]})")
check(any(title in page.text for title in titles), "the page shows the demo's books")
health_text = ((answer("GET", "health") or [{}])[0] or {}).get("message", "")
check(bool(health_text) and health_text in page.text, f"the page shows the health warning ({health_text!r})")


def card(key: str, **parameters) -> tuple[int, str]:
    response = client.get("/partials/cards?" + urlencode({"keys": key, **parameters}), headers={"X-Requested-With": "fetch"},
                          follow_redirects=False)
    return response.status_code, response.text


broken = []
for one in registry.APPS["readarr"].cards():
    status, text = card(one.key, days=30)
    errors = [e for e in ERRORS if e in text]
    if status != 200 or f'data-card-key="{one.key}"' not in text or errors:
        broken.append(f"{one.key}: {status} {errors}")
check(not broken, f"each of the {len(registry.APPS['readarr'].cards())} cards gives 200 without a connection error ({broken[:4]})")
piece = client.get("/partials/arr/readarr/downloading")
check(piece.status_code == 200 and "session-bar" in piece.text and not [e for e in ERRORS if e in piece.text],
      f"the slice of the card Downloading gives 200 with progress ({piece.status_code})")

print()
print("--- (e) the card Downloading moves with the clock ---")
real_now = demo_world.now
base = datetime.now(UTC).replace(microsecond=0)


def downloading_at(at: datetime) -> str:
    demo_world.now = lambda: at
    readarr.progress._memory = None      # the card reads the queue again
    return card("arr_readarr_downloading")[1]


first, later = downloading_at(base), downloading_at(base + timedelta(minutes=10))
check("session-bar" in first and "session-bar" in later, "the card shows downloads at both moments")
check(first != later, "the card differs ten minutes later")
check(downloading_at(base) == first, "the same moment gives the same card")
demo_world.now = real_now

print()
print("--- (f) the answers in Readarr's shape ---")
status = answer("GET", "system/status") or {}
check(status.get("appName") == "Readarr" and str(status.get("version", "")).startswith("0.4."), f"system/status: Readarr 0.4 ({status.get('version')})")
pages, page_number = [], 1
while page_number < 50:
    one_page = answer("GET", "history", page=page_number, pageSize=100, sortKey="date", sortDirection="ascending", **INCLUDE) or {}
    pages.append(one_page)
    if not one_page.get("records") or page_number * 100 >= int(one_page.get("totalRecords") or 0):
        break
    page_number += 1
history = [record for one_page in pages for record in one_page.get("records") or []]
dates = [record.get("date", "") for record in history]
check(bool(history) and len(history) == pages[0].get("totalRecords") == readarr.event_count(),
      f"the history in pages: every record once, as many as the sync stored ({len(history)}, {pages[0].get('totalRecords')})")
check(dates == sorted(dates) and len({record.get("id") for record in history}) == len(history), "oldest first, every id its own")
check(all(isinstance(record.get("book"), dict) and record["book"].get("id") == record.get("bookId") and record["book"].get("releaseDate")
          and isinstance(record.get("author"), dict) and record["author"].get("id") == record.get("authorId") for record in history),
      "with the book (and its release) and the author when asked")
check(not any("book" in record or "author" in record for record in (answer("GET", "history", page=1, pageSize=5) or {}).get("records", [])),
      "without them when not asked")
event_kinds = {record.get("eventType") for record in history}
check(event_kinds == {"grabbed", "bookFileImported", "downloadImported", "bookFileDeleted", "downloadFailed"},
      f"the kinds of events ({sorted(event_kinds)})")
check(all(record["data"].get("fileId") for record in history if record.get("eventType") == "bookFileImported")
      and all(record["data"].get("reason") == "Upgrade" for record in history if record.get("eventType") == "bookFileDeleted"),
      "a file's import names the file, a deletion its reason")
check(all(moment(d) <= datetime.now(UTC) for d in dates), "nothing in the future")
since = datetime.now(UTC) - timedelta(days=20)
recent = answer("GET", "history/since", date=since.strftime("%Y-%m-%dT%H:%M:%SZ"), **INCLUDE) or []
check(isinstance(recent, list) and bool(recent) and all(moment(r["date"]) >= since for r in recent)
      and {r["id"] for r in recent} == {r["id"] for r in history if moment(r["date"]) >= since},
      f"history/since: exactly the records since the date ({len(recent)})")

demo_world.now = lambda: base
whole_queue = answer("GET", "queue", page=1, pageSize=200, includeUnknownAuthorItems="true", **INCLUDE) or {}
blocks = readarr.progress.blocks(whole_queue.get("records") or [])
states = [block["state"] for block in blocks]
check(2 <= len(blocks) <= 4 and whole_queue.get("totalRecords") == len(whole_queue.get("records") or []),
      f"the queue: 2-4 downloads ({len(blocks)}, {whole_queue.get('totalRecords')} records)")
check(states.count("downloading") >= 2 and sum(state in ("stuck", "warnings") for state in states) == 1,
      f"downloads in progress and one with a warning ({states})")
coming_titles = {c["title"] for c in coming_list}
check(all(block["book"] in titles | coming_titles and block["author"] for block in blocks)
      and any(block["book"] in coming_titles for block in blocks) and any(block["title_id"] in library for block in blocks),
      f"of the demo's books: one coming out, the others better versions of the library's ({[b['title'] for b in blocks]})")
check(len({block["author"] for block in blocks}) == len(blocks), "every download of another author")
first_page = answer("GET", "queue", pageSize=100, includeUnknownAuthorItems="true", **INCLUDE) or {}
check(first_page.get("totalRecords") == whole_queue.get("totalRecords"), "the live state's queue is the same queue")
check(ask("POST", "command", body={"name": "RefreshMonitoredDownloads"}).status_code == 201
      and answer("GET", "queue", page=1, pageSize=200, includeUnknownAuthorItems="true", **INCLUDE) == whole_queue,
      "a POST command is accepted and changes nothing")
calendar = answer("GET", "calendar", start=base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  end=(base + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"), includeAuthor="true") or []
check(bool(calendar) and all(base <= moment(entry.get("releaseDate")) <= base + timedelta(days=14) for entry in calendar)
      and any(moment(entry.get("releaseDate")) > base + timedelta(days=7) for entry in calendar),
      f"the calendar for 14 days ahead ({len(calendar)})")
check(all(entry.get("title") in titles | coming_titles and (entry.get("author") or {}).get("authorName") for entry in calendar),
      "with the books of the demo and their authors")
health = answer("GET", "health") or []
check(len(health) == 1 and health[0].get("type") == "warning" and health[0].get("source") == "UpdateCheck",
      f"one health warning: an update is available ({health})")
for name in ("missing", "cutoff"):
    wanted = answer("GET", f"wanted/{name}", pageSize=1) or {}
    check(int(wanted.get("totalRecords") or 0) > 0 and len(wanted.get("records") or []) == 1,
          f"wanted/{name}: the count with one record ({wanted.get('totalRecords')})")
missing_titles = {b.get("title") for b in (answer("GET", "wanted/missing", pageSize=50) or {}).get("records", [])}
check(missing_titles and missing_titles <= coming_titles, f"missing: books out already the library does not have ({sorted(missing_titles)})")
listed = answer("GET", "book") or []
own_ids = sorted(int(b["id"]) for b in listed if b.get("title") in titles)
check(own_ids == sorted(by_number) and all(by_number[int(b["id"])]["title"] == b["title"] for b in listed if b.get("title") in titles),
      "book: the library's books with their numbers")
check({b.get("title") for b in listed} - titles == coming_titles and all(int(b["id"]) not in own_ids for b in listed if b["title"] in coming_titles),
      "and the books coming out, with ids of their own")
check(all(str(b.get("foreignBookId", "")).isdigit() and b.get("editions") and (b.get("author") or {}).get("authorName") for b in listed),
      "every book has its Goodreads id, its edition and its author")
check(all(not b.get("images") or all("remoteUrl" not in image for image in b["images"]) for b in listed), "no image points outside (no remoteUrl)")
asked_ids = [entry["number"] for entry in catalog[:3]] + [next(iter(n for n in by_number if by_number[n]["audiobook"]))]
editions = answer("GET", "edition", bookId=[str(n) for n in asked_ids]) or []
check(editions and {int(e.get("bookId") or 0) for e in editions} == set(asked_ids)
      and all(isbn_ok(e.get("isbn13")) for e in editions if e.get("isbn13")),
      f"edition: the editions of the books asked about, with valid ISBN-13s ({len(editions)})")
ebook_flags = {int(e["bookId"]): e.get("isEbook") for e in editions if e.get("isEbook")}
check(all(by_number[n]["audiobook"] != bool(ebook_flags.get(n)) for n in asked_ids),
      "an e-book has an e-book edition, an audiobook none")
authors = answer("GET", "author") or []
check(sorted(a.get("authorName") for a in authors) == sorted({entry["author"] for entry in catalog})
      and all(str(a.get("foreignAuthorId", "")).isdigit() and moment(a.get("added")) for a in authors),
      f"author: the demo's authors with their Goodreads ids and when they came ({len(authors)})")
check(ask("GET", f"mediacover/book/{catalog[0]['number']}/cover.jpg").status_code == 404 and ask("GET", "no/such/path").status_code == 404,
      "covers and unknown paths are 404")
demo_world.now = real_now

print()
print("--- (c) every path the code asked has an answer ---")
paths = {path for _method, path, _status in ASKED}
missing_paths = {"system/status", "history", "book", "author", "edition", "wanted/missing", "wanted/cutoff", "queue", "calendar",
                 "health", "command"} - paths
check(not missing_paths, f"the seed and the page asked what a real Readarr is asked ({sorted(missing_paths)})")
not_found = sorted({f"{method} {path}" for method, path, status in ASKED if status == 404 and not path.startswith("mediacover/")})
check(not not_found, f"no 404 but covers ({not_found})")
check(all(status < 400 for method, path, status in ASKED if not path.startswith("mediacover/")),
      f"every answer fine ({sorted({(p, s) for _m, p, s in ASKED if s >= 400})[:4]})")

print()
print("--- (g) a refresh deletes the data and fetches the same again ---")
before = readarr.event_count()
WARNINGS.clear()
demodata.refresh()
check(readarr.event_count() == before and readarr.sync_state()["result"] == "ok",
      f"the same history after the refresh ({before} -> {readarr.event_count()})")
check(not WARNINGS, f"the refresh logged no warning ({WARNINGS[:3]})")
check(json.dumps([(b["id"], b["title"], b["foreignBookId"]) for b in answer("GET", "book") or []])
      == json.dumps([(b["id"], b["title"], b["foreignBookId"]) for b in listed]), "the same books after the refresh")

print()
print("--- (h) no network ---")
check(not NETWORK, f"no socket was touched ({NETWORK[:3]})")
os.environ["JELLYSCOPE_DEMO"] = "0"
config.load_config(reload=True)
check(demo_switch.transport(readarr.NAME) is None, "outside the demo mode the switch gives Readarr's client the network")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
