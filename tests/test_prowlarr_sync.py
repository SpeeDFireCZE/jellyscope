# -*- coding: utf-8 -*-
r"""Prowlarr: the sync of the history into hourly sums and the list of grabbed releases.

There are too many queries to store each separately: Prowlarr's history is built into sums by hour,
indexer, app and kind (`prowlarr_hour`); grabbed releases are stored one by one (`prowlarr_grab`)
and the indexers' names apart (`prowlarr_indexer`). A sum cannot be "rewritten" like a row - what is
added once stays there. So above all it is guarded that no event is counted twice or left out:

A) converting a history record (numbers come as texts);
B) the first run and the increment: sums exactly by the history, a further run changes nothing;
C) pages: it reads from the newest event down to the last processed number; an interrupted read writes
   nothing, the write is one transaction, a history that changes during the read (added, removed) is
   not counted twice nor in part;
D) indexers: names, an indexer deleted in Prowlarr;
E) errors, concurrency, deleting the data; neither the key nor the queries' addresses anywhere.

The app does not run: the answers are recorded (tests/prowlarr_responses/), further events are built
from the recorded ones.

Spec: docs/superpowers/specs/2026-11-15-prowlarr-design.md

Run:
    .venv/bin/python tests/test_prowlarr_sync.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import PROWLARR_RESPONSES, recorded_response, connect_prowlarr, prowlarr_history, prowlarr_client, prowlarr_handler  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db, services  # noqa: E402
from jellyscope.arr import prowlarr  # noqa: E402
from jellyscope.arr.prowlarr import sync  # noqa: E402

failures = 0
db.init_db()
KEY = "tajny-klic"
HISTORY = prowlarr_history()
INDEXERS = recorded_response(PROWLARR_RESPONSES, "indexer")
QUERY = next(r for r in HISTORY if r["eventType"] == "indexerQuery" and r["successful"])
LAST_ID = max(r["id"] for r in HISTORY)
KNOWN = {"indexerQuery": "query", "indexerRss": "rss", "indexerAuth": "auth"}


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def make(number: int, date: str, kind: str = "indexerQuery", indexer: int = 1, successful: bool = True, **data: Any) -> dict[str, Any]:
    """A history event after the recorded query: its own number, time (`2026-11-15T09:10:00Z`) and data."""
    return {**QUERY, "id": number, "date": date, "eventType": kind, "indexerId": indexer, "successful": successful,
            "data": {**QUERY["data"], **{k: v for k, v in data.items() if v is not None}}}


def expected(events: list[dict[str, Any]]) -> dict[tuple, tuple]:
    """The sums the events are to make - computed here independently of the sync."""
    sums: dict[tuple, list[int]] = {}
    for r in events:
        kind = KNOWN.get(r["eventType"])
        if not kind:
            continue
        s = sums.setdefault((r["date"][:13].replace("T", " ") + ":00:00", r["indexerId"], r["data"].get("source") or "", kind), [0] * 5)
        s[0] += 1
        s[1] += not r["successful"]
        if r["successful"] and r["data"].get("cached") != "1" and str(r["data"].get("elapsedTime", "")).isdigit():
            s[2] += int(r["data"]["elapsedTime"])
            s[3] += 1
        s[4] += bool(r["successful"] and int(r["data"].get("queryResults") or 0) > 0)
    return {k: tuple(v) for k, v in sums.items()}


def sums() -> dict[tuple, tuple]:
    return {(r["hour"], r["indexer_id"], r["app"], r["kind"]): (r["event_count"], r["failed_count"], r["response_sum"], r["response_count"], r["with_results"])
            for r in db.query_all("SELECT * FROM prowlarr_hour")}


def grabbed() -> list[dict[str, Any]]:
    return db.query_all("SELECT * FROM prowlarr_grab ORDER BY id, happened_at")


# What Prowlarr returns right now: the test changes it (a bigger history, errors, another list of indexers).
answers: dict[str, Any] = {}
requests: list[str] = []
before_request: list[Any] = []               # functions called before every request for a page of history


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/history"):
        for function in before_request:
            function(request)
        if "vzdy" in answers:               # a server that can do neither pages nor a count: the same answer to every request
            requests.append(f"history?{request.url.params.get('page')}")
            return httpx.Response(200, json=answers["vzdy"])
        if "strop" in answers or "po_zaznamech" in answers or "vzestupne" in answers:
            # Paging of its own: a server that returns at most `strop` records per page (and counts the pages by it), and a
            # server that finds the count of all events only after the records (like Prowlarr: two queries).
            params = request.url.params
            requests.append(f"history?{params.get('page')}")
            number, size = int(params.get("page", 1)), min(int(params.get("pageSize", 10)), answers.get("strop", 10**9))
            ordered = sorted(answers["history"], key=lambda e: (str(e["date"]), e["id"]), reverse="vzestupne" not in answers)
            records = ordered[(number - 1) * size:number * size]
            if "po_zaznamech" in answers:
                answers["po_zaznamech"](request)
            return httpx.Response(200, json={"page": number, "pageSize": size, "totalRecords": len(answers["history"]), "records": records})
    return prowlarr_handler(requests, answers)(request)


def pages() -> list[str]:
    """Which pages of history were read."""
    return [r.split("?", 1)[1] for r in requests if r.startswith("history?")]


def page_of(request: httpx.Request) -> str:
    return str(request.url.params.get("page"))


def synchronize() -> dict[str, Any]:
    return asyncio.run(prowlarr.synchronize())


def attempt() -> str:
    """A sync that is to fail: returns the error's text (or "" when it did not fail)."""
    try:
        synchronize()
    except Exception as error:
        return str(error) or type(error).__name__
    return ""


def mark() -> str:
    """The number of the newest counted event."""
    db.forget_settings()
    return db.get_setting("arr_prowlarr_last_id", "")


def mark_time() -> str:
    """The time of the newest counted event (UTC)."""
    db.forget_settings()
    return db.get_setting("arr_prowlarr_last_event_at", "")


def newest_time(events: list[dict[str, Any]]) -> str:
    return max(r["date"] for r in events).replace("T", " ").rstrip("Z")


substitute(prowlarr, "ProwlarrClient", prowlarr_client(handler))
connect_prowlarr(KEY)

print("--- A) converting a record ---")
e = prowlarr.event_from_history(QUERY)
check((e["id"], e["kind"], e["indexer_id"], e["app"], e["succeeded"]) == (QUERY["id"], "query", QUERY["indexerId"], QUERY["data"]["source"], True)
      and e["happened_at"] == QUERY["date"].replace("T", " ").rstrip("Z") and e["response"] == int(QUERY["data"]["elapsedTime"])
      and e["result_count"] == int(QUERY["data"]["queryResults"]) and e["from_cache"] is False,
      f"a query: the time in UTC, the response time and the count of results as numbers (they are texts in the answer) ({e})")
proxy = prowlarr.event_from_history(next(r for r in HISTORY if r["eventType"] == "releaseGrabbed" and r["data"].get("grabMethod") == "Proxy" and r["successful"]))
redirected = prowlarr.event_from_history(next(r for r in HISTORY if r["eventType"] == "releaseGrabbed" and r["data"].get("grabMethod") == "Redirect"))
failed = prowlarr.event_from_history(next(r for r in HISTORY if r["eventType"] == "releaseGrabbed" and not r["successful"]))
check((proxy["kind"], proxy["method"], proxy["succeeded"]) == ("grabbed", "proxy", True) and isinstance(proxy["response"], int) and proxy["title"]
      and (redirected["method"], redirected["response"]) == ("redirect", None) and (failed["succeeded"], failed["method"]) == (False, "proxy"),
      "a grabbed release: one downloaded by Prowlarr has a time and a result, a redirected one does not")
check(prowlarr.event_from_history(make(900, "2026-11-15T09:10:00Z", "indexerInfo")) is None
      and prowlarr.event_from_history(make(900, "kdoví kdy")) is None and prowlarr.event_from_history({**QUERY, "id": "x"}) is None,
      "an unknown event kind, an unreadable time or number: None (the event is skipped)")
odd = prowlarr.event_from_history({**make(901, "2026-11-15T09:10:00Z", "indexerRss"), "data": None, "indexerId": None})
check((odd["kind"], odd["app"], odd["response"], odd["result_count"], odd["indexer_id"], odd["from_cache"]) == ("rss", "", None, None, 0, False),
      f"a record without data converts, it does not fail ({odd})")
from_cache = prowlarr.event_from_history(make(902, "2026-11-15T09:10:00Z", cached="1", elapsedTime="0"))
check(from_cache["from_cache"] is True and prowlarr.event_from_history(make(903, "2026-11-15T09:10:00Z", elapsedTime="-5"))["response"] is None
      and prowlarr.event_from_history(make(904, "2026-11-15T09:10:00Z", elapsedTime="99999999999999"))["response"] is None,
      "an answer from Prowlarr's cache is recognised; a negative or a nonsensically long response time is no response time")
long = prowlarr.event_from_history(make(905, "2026-11-15T09:10:00Z", source="x" * 500))
check(len(long["app"]) == 60, "the app's name (from the caller's header) is shortened")

print()
print("--- B) the first run and the increment ---")
check(prowlarr.event_count() == 0 and prowlarr.sync_running() is False and prowlarr.sync_state()["result"] == "", "empty")
answers["history"] = []
check(synchronize() == {"new_count": 0, "total": 0} and (mark(), mark_time()) == ("", "") and prowlarr.sync_state()["result"] == "ok",
      "a Prowlarr without history: nothing is stored and the run is fine")
del answers["history"]
first = synchronize()
AFTER_FIRST = expected(HISTORY)
check(sums() == AFTER_FIRST and first == {"new_count": len(HISTORY), "total": len(HISTORY)},
      f"the first run: sums by hour, indexer, app and kind exactly by the history ({first}, {len(AFTER_FIRST)} rows)")
recorded_grabs = [r for r in HISTORY if r["eventType"] == "releaseGrabbed"]
g = grabbed()
check([r["id"] for r in g] == [r["id"] for r in recorded_grabs]
      and [(r["title"], r["app"], r["succeeded"]) for r in g] == [(r["data"]["grabTitle"], r["data"]["source"], int(r["successful"])) for r in recorded_grabs]
      and {r["method"] for r in g} == {"proxy", "redirect"} and all(r["response"] is None for r in g if r["method"] == "redirect"),
      f"grabbed releases are stored one by one with their method ({len(g)})")
check(prowlarr.event_count() == len(HISTORY) and (mark(), mark_time()) == (str(LAST_ID), newest_time(HISTORY))
      and prowlarr.sync_state()["result"] == "ok" and prowlarr.sync_state()["new_count"] == len(HISTORY),
      f"the count of events, the number and the time of the newest counted event ({mark()}, {mark_time()}) and the state")
db_content = json.dumps([db.query_all(f"SELECT * FROM {t}") for t in ("prowlarr_hour", "prowlarr_grab", "prowlarr_indexer")], default=str)
check("TAJNY-KLIC" not in db_content and "apikey" not in db_content.lower() and "indexer.test" not in db_content,
      "neither the queries' addresses nor the keys of indexers and apps got into the database")
requests.clear()
second = synchronize()
check(second == {"new_count": 0, "total": 0} and sums() == AFTER_FIRST and len(grabbed()) == len(recorded_grabs) and pages() == ["1"],
      f"the second run: nothing new, one page of history and the sums did not change ({second}, {pages()})")
NEW = [make(LAST_ID + 1, "2026-11-15T07:05:00Z", elapsedTime="40", queryResults="3"),
       make(LAST_ID + 2, "2026-11-15T07:06:00Z", "indexerRss", elapsedTime="60", queryResults="0"),
       make(LAST_ID + 3, "2026-11-15T08:59:59Z", successful=False, elapsedTime="7", queryResults="0", source="Radarr"),
       make(LAST_ID + 4, "2026-11-15T09:00:00Z", "indexerAuth", indexer=3, elapsedTime="15"),
       {**recorded_grabs[0], "id": LAST_ID + 5, "date": "2026-11-15T09:01:00Z"}]
answers["history"] = [*HISTORY, *NEW]
third = synchronize()
ALL = expected([*HISTORY, *NEW])
check(third == {"new_count": 5, "total": 5} and sums() == ALL and len(grabbed()) == len(recorded_grabs) + 1 and mark() == str(LAST_ID + 5),
      f"the increment is added once, what is counted already is not counted again ({third})")
check(sums()[("2026-11-15 07:00:00", 1, QUERY["data"]["source"], "query")] == (1, 0, 40, 1, 1)
      and sums()[("2026-11-15 08:00:00", 1, "Radarr", "query")] == (1, 1, 0, 0, 0)
      and sums()[("2026-11-15 09:00:00", 3, QUERY["data"]["source"], "auth")][0] == 1,
      "the hour is the start of the hour in UTC; a failed query counts neither into the response time nor into \"found something\"; a sign-in has its own kind")
synchronize()
check(sums() == ALL, "and a further run without news is again without a change")
# Two events written at the same moment may have numbers in another order than their times. The number decides:
# an event with a newer number is counted, even if its time puts it among long processed ones.
DELAYED = make(LAST_ID + 6, "2026-11-15T06:00:00Z", indexer=QUERY["indexerId"], elapsedTime="11", queryResults="2")
HOUR_KEY = ("2026-11-15 06:00:00", QUERY["indexerId"], QUERY["data"]["source"], "query")
before = sums()[HOUR_KEY]
answers["history"] = [*HISTORY, *NEW, DELAYED]
fourth = synchronize()
after = sums()[HOUR_KEY]
check(fourth == {"new_count": 1, "total": 1} and after == (before[0] + 1, before[1], before[2] + 11, before[3] + 1, before[4] + 1)
      and (mark(), mark_time()) == (str(LAST_ID + 6), newest_time(NEW)),
      f"an event with a newer number and an older time is added to an hour that has a sum already ({before} -> {after}); the mark's time does not go back")
# An answer from Prowlarr's cache comes at once: it belongs into the count of queries and into "found something", not into the response time.
FROM_CACHE = make(LAST_ID + 7, "2026-11-15T09:30:00Z", indexer=QUERY["indexerId"], cached="1", elapsedTime="500", queryResults="2")
answers["history"] = [*HISTORY, *NEW, DELAYED, FROM_CACHE]
synchronize()
check(sums()[("2026-11-15 09:00:00", QUERY["indexerId"], QUERY["data"]["source"], "query")] == (1, 0, 0, 0, 1),
      f"a query answered from the cache: it counts, it found something, but it does not go into the sum of response times ({sums()[('2026-11-15 09:00:00', QUERY['indexerId'], QUERY['data']['source'], 'query')]})")

print()
print("--- C) pages, interruptions, a history that changes during the read ---")
check(prowlarr.delete_data() == len(HISTORY) + 7 and prowlarr.event_count() == 0 and (mark(), mark_time()) == ("", "")
      and prowlarr.sync_state()["result"] == "" and db.query_all("SELECT 1 FROM prowlarr_indexer") == [],
      "deleting the data empties the three tables, both marks and the state")
sync.PAGE_SIZE = 5
BIG = [*HISTORY, *NEW]                                     # 28 events = 6 pages of five
NEWEST_ID = max(r["id"] for r in BIG)
answers["history"] = list(BIG)
requests.clear()
whole = synchronize()
check(sums() == ALL and whole == {"new_count": len(BIG), "total": len(BIG)} and pages() == ["1", "2", "3", "4", "5", "6", "7"]
      and mark() == str(NEWEST_ID), f"the first run page by page from the newest event; an empty page confirms the end ({whole}, pages {pages()})")


def fail_on_third(request: httpx.Request) -> None:
    if page_of(request) == "3":
        raise httpx.ConnectError("Connection refused", request=request)


prowlarr.delete_data()
before_request.append(fail_on_third)
failed = attempt()
before_request.clear()
check(bool(failed) and sums() == {} and grabbed() == [] and (mark(), mark_time()) == ("", "") and prowlarr.sync_state()["result"] == "error"
      and KEY not in (db.get_setting("arr_prowlarr_state") or ""), f"a read interrupted on the third page writes nothing ({failed[:60]})")
check(synchronize()["new_count"] == len(BIG) and sums() == ALL, "and the next run reads the whole history - nothing twice, nothing extra")
# The sums and the number of the last event must be one transaction: if the sums were written and the number not,
# the next run would add the same events a second time.
MORE = [make(NEWEST_ID + 10 + i, f"2026-11-15T10:{i:02d}:00Z", elapsedTime="20", queryResults="1") for i in range(12)]
answers["history"] = [*BIG, *MORE]
def indexer_names() -> dict[int, tuple]:
    return {r["id"]: (r["name"], r["removed"]) for r in db.query_all("SELECT id, name, removed FROM prowlarr_indexer")}


answers["indexer"] = [{**INDEXERS[0], "name": "Přejmenovaný během zápisu"}]
before_write = (sums(), grabbed(), mark(), mark_time(), indexer_names(), db.get_setting("arr_prowlarr_counted", ""))
restore = substitute(prowlarr, "MARK_UPSERT", "INSERT INTO tabulka_ktera_neni (key, value) VALUES (?, ?)")
failed = attempt()
restore()
del answers["indexer"]
db.forget_settings()
check(bool(failed) and (sums(), grabbed(), mark(), mark_time(), indexer_names(), db.get_setting("arr_prowlarr_counted", "")) == before_write
      and prowlarr.sync_state()["result"] == "error",
      "the write is one transaction: when writing the marks fails, nothing is added and not even the indexers change")
requests.clear()
after_write = synchronize()
WITH_MORE = expected([*BIG, *MORE])
check(after_write == {"new_count": 12, "total": 12} and sums() == WITH_MORE and pages() == ["1", "2", "3", "4"],
      f"twelve news in pages of five: it reads up to the page on which there is nothing new any more ({after_write}, pages {pages()})")
# The sync keeps the count of counted events itself (it writes it in the same transaction). Settings → *Arr asks for it
# on every view and summing the whole table of sums for it would show with a big history.
original_query_value = db.query_value


def without_summing(sql: str, *a: Any, **k: Any) -> Any:
    if "prowlarr_" in sql:
        raise AssertionError(f"počet událostí se nemá sčítat z tabulek: {sql}")
    return original_query_value(sql, *a, **k)


db.forget_settings()
db.query_value = without_summing
try:
    kept = prowlarr.event_count()
except AssertionError as error:
    kept = str(error)
finally:
    db.query_value = original_query_value
check(kept == len(BIG) + 12 and db.get_setting("arr_prowlarr_counted") == str(len(BIG) + 12),
      f"the count of events is stored beside the marks and read without summing the table ({kept})")
# Data from before the counter (the setting is missing): the count is summed from the tables once and the next write stores it right.
db.set_setting("arr_prowlarr_counted", "")
db.forget_settings()
answers["history"] = [*BIG, *MORE, make(NEWEST_ID + 25, "2026-11-15T11:00:00Z", elapsedTime="20", queryResults="1")]
check(prowlarr.event_count() == len(BIG) + 12 and synchronize()["new_count"] == 1 and db.get_setting("arr_prowlarr_counted") == str(len(BIG) + 13)
      and prowlarr.event_count() == len(BIG) + 13, "without a stored count it is summed from the tables and the next write fills it in")
# Two events are added during the read: the rest of the history moves by two places and the third page starts
# with two records that were on the second already. They must not be counted a second time.
ADDED = [make(NEWEST_ID + 30, "2026-11-15T12:00:00Z", elapsedTime="30", queryResults="4"),
         make(NEWEST_ID + 31, "2026-11-15T12:00:01Z", "indexerRss", elapsedTime="35", queryResults="0")]
happened = {"yet": False}


def added_before_third(request: httpx.Request) -> None:
    if page_of(request) == "3" and not happened["yet"]:
        happened["yet"] = True
        answers["history"] = [*BIG, *ADDED]


prowlarr.delete_data()
answers["history"] = list(BIG)
before_request.append(added_before_third)
requests.clear()
run = synchronize()
before_request.clear()
check(sums() == ALL and run == {"new_count": len(BIG), "total": len(BIG)} and pages() == ["1", "2", "3", "4", "5", "6", "7"]
      and mark() == str(NEWEST_ID), f"events added during the read move the pages: nothing is counted twice ({run}, pages {pages()})")
check(synchronize() == {"new_count": 2, "total": 2} and sums() == expected([*BIG, *ADDED]), "and the next run counts the added ones")
# A whole page of them is added: the second page then consists only of records read already. It is not the end of the history.
FLOOD = [make(NEWEST_ID + 40 + i, f"2026-11-15T12:30:{i:02d}Z", elapsedTime="10", queryResults="1") for i in range(5)]
happened = {"yet": False}


def flood_before_second(request: httpx.Request) -> None:
    if page_of(request) == "2" and not happened["yet"]:
        happened["yet"] = True
        answers["history"] = [*BIG, *FLOOD]


prowlarr.delete_data()
answers["history"] = list(BIG)
before_request.append(flood_before_second)
requests.clear()
run = synchronize()
before_request.clear()
check(sums() == ALL and run["new_count"] == len(BIG) and pages() == ["1", "2", "3", "4", "5", "6", "7", "8"],
      f"a page made only of repeated records does not end the read ({run}, pages {pages()})")
# More than a page of them is added (seven with five per page): the third page then consists wholly of the first and the second.
# Repeated records must be recognised against everything the run has already seen, not only against the previous page.
INFLUX = [make(NEWEST_ID + 60 + i, f"2026-11-15T12:40:{i:02d}Z", elapsedTime="10", queryResults="1") for i in range(7)]
happened = {"yet": False}


def influx_before_third(request: httpx.Request) -> None:
    if page_of(request) == "3" and not happened["yet"]:
        happened["yet"] = True
        answers["history"] = [*BIG, *INFLUX]


prowlarr.delete_data()
answers["history"] = list(BIG)
before_request.append(influx_before_third)
requests.clear()
run = synchronize()
before_request.clear()
check(sums() == ALL and run["new_count"] == len(BIG) and pages() == [str(i) for i in range(1, 9)],
      f"a move by more than a page: nothing twice and it reads on without repeating from the start ({run}, pages {pages()})")
# Prowlarr finds the count of all events separately, only after the page's records. An event added in between
# is in the count but not on the page - that is no reason to read again (with a big history the first run would never finish).
MEANWHILE = [make(NEWEST_ID + 70 + i, f"2026-11-15T12:50:{i:02d}Z", elapsedTime="10", queryResults="1") for i in range(2)]
arriving = iter(MEANWHILE)


def added_after_records(request: httpx.Request) -> None:
    following = next(arriving, None) if page_of(request) in ("2", "4") else None
    if following:
        answers["history"] = [*answers["history"], following]


prowlarr.delete_data()
answers["history"] = list(BIG)
answers["po_zaznamech"] = added_after_records
requests.clear()
run = synchronize()
del answers["po_zaznamech"]
check(sums() == ALL and run["new_count"] == len(BIG) and pages() == [str(i) for i in range(1, 8)] and mark() == str(NEWEST_ID),
      f"a count computed only after the records (one event more): it reads on, no repeating from the start ({run}, pages {pages()})")
check(synchronize() == {"new_count": 2, "total": 2} and sums() == expected([*BIG, *MEANWHILE]), "and the next run counts the events added meanwhile")
# A server that returns at most three records per page however many are asked for: a short page is no end of the history.
prowlarr.delete_data()
answers["history"] = list(BIG)
answers["strop"] = 3
requests.clear()
run = synchronize()
del answers["strop"]
check(sums() == ALL and run == {"new_count": len(BIG), "total": len(BIG)} and pages() == [str(i) for i in range(1, 12)],
      f"a server that shortens the page: the history is read whole, the end is only an empty page ({run}, {len(pages())} pages)")
# A history longer than how many pages are read at most: an error, not a partial write (the mark would skip the rest).
prowlarr.delete_data()
answers["history"] = list(BIG)
sync.MAX_PAGES = 3
failed = attempt()
sync.MAX_PAGES = 10_000
check(bool(failed) and sums() == {} and mark() == "", f"more pages than may be read: an error and nothing written ({failed[:70]})")
# Something disappears from the history during the read (Prowlarr deletes old history by itself; with a deleted indexer
# its events disappear too). The rest moves and a piece would be skipped - it is recognised by the count and read again.
for label, rest in (("newest (a deleted indexer)", BIG[:-4]), ("oldest (a history clean-up)", BIG[4:])):
    prowlarr.delete_data()
    answers["history"] = list(BIG)
    happened = {"yet": False}

    def removed_before_third(request: httpx.Request, rest: list = rest) -> None:
        if page_of(request) == "3" and not happened["yet"]:
            happened["yet"] = True
            answers["history"] = list(rest)

    before_request.append(removed_before_third)
    requests.clear()
    run = synchronize()
    before_request.clear()
    check(sums() == expected(rest) and run["new_count"] == len(rest) and pages() == ["1", "2", "3", "1", "2", "3", "4", "5", "6"]
          and mark() == str(max(r["id"] for r in rest)),
          f"four {label} events disappeared during the read: it reads again from the start and the sums match what is left (pages {pages()})")
# A history that changes on every read: after three attempts an error, nothing is written.
prowlarr.delete_data()
shrinking = list(BIG)
answers["history"] = list(shrinking)


def keeps_shrinking(request: httpx.Request) -> None:
    if page_of(request) == "2":
        del shrinking[0]
        answers["history"] = list(shrinking)


before_request.append(keeps_shrinking)
requests.clear()
failed = attempt()
before_request.clear()
check(bool(failed) and sums() == {} and mark() == "" and pages() == ["1", "2"] * 3 and prowlarr.sync_state()["result"] == "error",
      f"a history that changes on every read: three attempts, then an error and nothing written ({failed[:70]})")
# A server that can do neither pages nor a count (it returns the same for every page): the history cannot be read. It is an error -
# writing the first page and moving the mark would throw the rest of the history away for good.
NEWEST_FIRST = sorted(BIG, key=lambda r: (r["date"], r["id"]), reverse=True)
answers["vzdy"] = {"records": NEWEST_FIRST[:5]}
requests.clear()
failed = attempt()
del answers["vzdy"]
check(bool(failed) and sums() == {} and mark() == "" and pages() == ["1", "2", "3", "4"],
      f"a server without paging: after three pages without anything new an error, nothing written ({failed[:70]}, pages {pages()})")
# Prowlarr's clock was ahead (events with tomorrow's time) and then went back: new events have today's time and in the sorting
# by date lie BELOW the old "tomorrow" ones. So a page full of old events with a future time is no end.
NOW = datetime.now(UTC)


def time_from_now(offset: timedelta) -> str:
    return (NOW + offset).strftime("%Y-%m-%dT%H:%M:%SZ")


TOMORROW = [make(i + 1, time_from_now(timedelta(days=1, seconds=i)), elapsedTime="10", queryResults="1") for i in range(8)]
TODAY = [make(9 + i, time_from_now(timedelta(hours=-1, seconds=i)), elapsedTime="10", queryResults="1") for i in range(12)]
prowlarr.delete_data()
answers["history"] = list(TOMORROW)
synchronize()
answers["history"] = [*TOMORROW, *TODAY]
requests.clear()
after_clock = synchronize()
check(after_clock == {"new_count": 12, "total": 12} and sums() == expected([*TOMORROW, *TODAY]) and mark() == "20",
      f"Prowlarr's clock ahead and then back: new events below the old ones with a future time are found ({after_clock}, pages {pages()})")
requests.clear()
check(synchronize() == {"new_count": 0, "total": 0} and len(pages()) <= 3, f"and a further run without news does not read the whole history (pages {pages()})")
# A server that does not sort from the newest (it ignores the sorting parameter): after the first run it would never find anything new.
prowlarr.delete_data()
answers["history"] = list(BIG)
answers["vzestupne"] = True
requests.clear()
failed = attempt()
del answers["vzestupne"]
check(bool(failed) and sums() == {} and mark() == "" and pages() == ["1"],
      f"a server that sorts from the oldest: an error right on the first page, nothing written ({failed[:70]})")
# A run with only events of an unknown kind: the mark stays (if Prowlarr renamed the kinds, the history must not be
# thrown away silently) - a few such are fine, many are an error the admin notices.
prowlarr.delete_data()
answers["history"] = list(BIG)
synchronize()
UNKNOWN = [make(NEWEST_ID + 100 + i, f"2026-11-15T14:{i // 60:02d}:{i % 60:02d}Z", "uplneNovyDruh") for i in range(100)]
answers["history"] = [*BIG, *UNKNOWN[:3]]
few = synchronize()
check(few == {"new_count": 0, "total": 3} and mark() == str(NEWEST_ID) and prowlarr.sync_state()["result"] == "ok",
      f"three events of an unknown kind and nothing known: nothing happened, the mark stays ({few})")
answers["history"] = [*BIG, *UNKNOWN]
failed = attempt()
check("does not know" in failed and mark() == str(NEWEST_ID) and sums() == ALL and prowlarr.sync_state()["result"] == "error",
      f"a hundred events of an unknown kind and nothing known: an error, the mark stays ({failed[:90]})")
answers["history"] = [*BIG, *UNKNOWN, make(NEWEST_ID + 300, "2026-11-15T15:00:00Z", elapsedTime="20", queryResults="1")]
with_known = synchronize()
check(with_known == {"new_count": 1, "total": 101} and mark() == str(NEWEST_ID + 300),
      f"as soon as an event of a known kind comes, the unknown ones are skipped and the mark moves ({with_known})")
# A kind Jellyscope knows and skips on purpose (indexerInfo), and a record with an unreadable time, are not "unknown
# events": the mark goes on past them and a hundred of them is no reason for the error "you need a newer Jellyscope".
INFO = [make(NEWEST_ID + 400 + i, f"2026-11-15T16:{i // 60:02d}:{i % 60:02d}Z", "indexerInfo") for i in range(100)]
answers["history"] = [*answers["history"], *INFO]
info = synchronize()
check(info == {"new_count": 0, "total": 100} and mark() == str(NEWEST_ID + 499) and prowlarr.sync_state()["result"] == "ok",
      f"a hundred events of a kind skipped on purpose: no error, the mark goes on ({info})")
WITHOUT_TIME = [make(NEWEST_ID + 600 + i, "kdoví kdy", elapsedTime="10") for i in range(100)]
answers["history"] = [*answers["history"], *WITHOUT_TIME]
without_time = synchronize()
check(without_time == {"new_count": 0, "total": 100} and mark() == str(NEWEST_ID + 699) and prowlarr.sync_state()["result"] == "ok",
      f"a hundred queries with an unreadable time: they are skipped, but they are no \"unknown events\" ({without_time})")
# The count of counted events must match the tables even when a grabbed release is not inserted (it is there already).
prowlarr.delete_data()
answers["history"] = list(BIG)
synchronize()
with db.connect() as conn:
    conn.execute("INSERT INTO prowlarr_grab (id, happened_at, indexer_id, app, title, method, succeeded, response, client)"
                 " VALUES (?, '2026-11-15 19:00:00', 1, 'Sonarr', 'Uz.tu.je', 'proxy', 1, 5, '')", (NEWEST_ID + 800,))
db.set_setting("arr_prowlarr_counted", "")
db.forget_settings()
answers["history"] = [*BIG, {**recorded_grabs[0], "id": NEWEST_ID + 800, "date": "2026-11-15T19:00:00Z"},
                      make(NEWEST_ID + 801, "2026-11-15T19:00:01Z", elapsedTime="20", queryResults="1")]
check(synchronize() == {"new_count": 2, "total": 2}, "(setup: the run sees a release that is stored already, and one query)")
db.forget_settings()
in_tables = (int(db.query_value("SELECT COALESCE(SUM(event_count), 0) FROM prowlarr_hour") or 0)
             + int(db.query_value("SELECT COUNT(*) FROM prowlarr_grab") or 0))
check(prowlarr.event_count() == in_tables == int(db.get_setting("arr_prowlarr_counted")),
      f"a grabbed release stored already is not counted a second time: the stored count = the tables ({prowlarr.event_count()}, {in_tables})")
# Sorting from the oldest is recognised even where the times do not help: all events in the same second (by the numbers),
# and pages of one record (by the neighbouring pages).
SAME_SECOND = [make(2000 + i, "2026-11-15T18:00:00Z", elapsedTime="10", queryResults="1") for i in range(12)]
for label, cap, page_list in (("events in the same second", None, ["1"]), ("pages of one record", 1, ["1", "2"])):
    prowlarr.delete_data()
    answers["history"] = list(SAME_SECOND) if cap is None else list(BIG)
    answers["vzestupne"] = True
    if cap:
        answers["strop"] = cap
    requests.clear()
    failed = attempt()
    answers.pop("strop", None)
    del answers["vzestupne"]
    check(bool(failed) and sums() == {} and mark() == "" and pages() == page_list,
          f"sorting from the oldest, {label}: an error, nothing written (pages {pages()})")
# Two processes over one database (two Jellyscope instances): both read the same new events. Only one may write
# them - the mark moves only from the value the run started with; the second writes nothing.
for label, start in (("the first run", []), ("a further run", BIG)):
    prowlarr.delete_data()
    answers["history"] = list(start)
    synchronize()
    answers["history"] = [*BIG, *MORE]
    before = (sums(), grabbed())
    happened = {"yet": False}

    def other_process(request: httpx.Request) -> None:
        """Between this run's read and write another process manages to count the same events and move the mark."""
        if not happened["yet"]:
            happened["yet"] = True
            with db.connect() as conn:
                conn.execute("INSERT INTO settings (key, value) VALUES ('arr_prowlarr_last_id', '999999')"
                             " ON CONFLICT (key) DO UPDATE SET value = excluded.value")
            db.forget_settings()

    before_request.append(other_process)
    try:
        synchronize()
        second_process = None
    except prowlarr.ProwlarrError as error:
        second_process = error
    before_request.clear()
    check(second_process is not None and second_process.template is services.SYNC_ALREADY_RUNNING and (sums(), grabbed()) == before
          and mark() == "999999" and prowlarr.sync_state()["result"] != "error",
          f"{label}: another process moved the mark meanwhile - this run adds nothing and reports \"already running\", not an error")
prowlarr.delete_data()
answers["history"] = [*BIG, make(NEWEST_ID + 50, "2026-11-15T13:00:00Z", "indexerInfo"), make(NEWEST_ID + 51, "2026-11-15T13:01:00Z", "novyDruh")]
unknown = synchronize()
check(unknown == {"new_count": len(BIG), "total": len(BIG) + 2} and mark() == str(NEWEST_ID + 51) and sums() == ALL,
      f"an unknown event kind is skipped and the number of the last event moves past it too ({unknown})")
# Prowlarr restored from an older backup (or reinstalled): the events' numbers go on from a smaller number than the last
# counted one. By the number the new events would look old - it is recognised by the time (an event newer than everything
# counted, and yet with a smaller number) and it goes on by the time.
G = recorded_grabs[-1]["id"]                                # a number under which one grabbed release is stored already
FROM_BACKUP = [r for r in HISTORY if r["id"] < G - 2]       # these are counted already (the same numbers and times)
AFTER_RESTORE = [make(G - 2, "2026-11-16T08:00:00Z", elapsedTime="50", queryResults="2"),
                 make(G - 1, "2026-11-16T08:00:05Z", "indexerRss", elapsedTime="70", queryResults="0"),
                 {**recorded_grabs[0], "id": G, "date": "2026-11-16T08:01:00Z"}]
check(G < int(mark()) and len(FROM_BACKUP) >= 7 and mark_time() < "2026-11-16", f"(setup: the mark {mark()} is further than the number {G})")
grabbed_before = len(grabbed())
answers["history"] = [*FROM_BACKUP, *AFTER_RESTORE]
requests.clear()
restore_run = synchronize()
check(restore_run == {"new_count": 3, "total": 3} and sums() == expected([*BIG, *AFTER_RESTORE]) and (mark(), mark_time()) == (str(G), "2026-11-16 08:01:00")
      and pages() == ["1", "1", "2"], f"the events' numbers started again: new events are recognised by time and counted once ({restore_run}, pages {pages()})")
check(len(grabbed()) == grabbed_before + 1 and len([r for r in grabbed() if r["id"] == G]) == 2,
      "a grabbed release with the same number from before the restore stayed, the new one is beside it")
answers["history"] = [*FROM_BACKUP, *AFTER_RESTORE, make(G + 1, "2026-11-16T09:00:00Z", elapsedTime="20", queryResults="1")]
check(synchronize() == {"new_count": 1, "total": 1} and mark() == str(G + 1), "and then it goes on by the numbers again")
# The same halfway: part of the new events have a number below the mark (Prowlarr used the deleted ones' numbers again), part above.
# One single event newer by time with a number below the mark is enough and the whole run goes by time - else those below the mark would vanish.
HALF = [make(G - 1, "2026-11-17T08:00:00Z", elapsedTime="10", queryResults="1"), make(G, "2026-11-17T08:00:01Z", elapsedTime="10", queryResults="1"),
        make(G + 2, "2026-11-17T08:00:02Z", elapsedTime="10", queryResults="1")]
before = sums()
answers["history"] = [*FROM_BACKUP, *HALF]
half = synchronize()
check(half == {"new_count": 3, "total": 3} and (mark(), mark_time()) == (str(G + 2), "2026-11-17 08:00:02")
      and sums() == {**before, **expected(HALF)}, f"numbers used again beside new ones: everything newer by time is counted ({half})")
# After a clean reinstall of Prowlarr the indexers' numbers start again too: number 1 is now another indexer. The old sums
# must stay with the old name - else the history of one indexer would show under the name of another.
OLD = next(i for i in INDEXERS if i["id"] == QUERY["indexerId"])
indexers_before = indexer_names()
before = sums()
AFTER_REINSTALL = [make(1, "2026-11-18T08:00:00Z", indexer=OLD["id"], elapsedTime="10", queryResults="1"),
                   make(2, "2026-11-18T08:00:05Z", "indexerRss", indexer=OLD["id"], elapsedTime="10", queryResults="1")]
answers["history"] = list(AFTER_REINSTALL)
answers["indexer"] = [{**OLD, "name": "Úplně jiný indexer"}, *[i for i in INDEXERS if i["id"] != OLD["id"]]]
reinstall = synchronize()
indexers_after = indexer_names()
set_aside = [number for number in indexers_after if number not in indexers_before]
check(reinstall == {"new_count": 2, "total": 2} and len(set_aside) == 1 and indexers_after[set_aside[0]] == (OLD["name"], 1)
      and indexers_after[OLD["id"]] == ("Úplně jiný indexer", 0),
      f"after a reinstall number {OLD['id']} has another name: the old indexer got a number of its own, kept its name and the mark \"removed\" ({indexers_after})")
A = set_aside[0] if set_aside else 0
check(sums() == {**{(h, A if i == OLD["id"] else i, a, d): v for (h, i, a, d), v in before.items()}, **expected(AFTER_REINSTALL)}
      and not [r for r in grabbed() if r["indexer_id"] == OLD["id"]] and any(r["indexer_id"] == A for r in grabbed()),
      "the old sums and grabbed releases moved to the old name, only the new events are under the number")
del answers["indexer"]
check(synchronize() == {"new_count": 0, "total": 0} and indexer_names()[A] == (OLD["name"], 1), "a further run does not touch the indexer set aside")
# An indexer with the same number and name is still the same one: a restore from a backup moves nothing (see above, "the numbers started again").
prowlarr.delete_data()
answers["history"] = list(BIG)
synchronize()
sync.PAGE_SIZE = 1000

print()
print("--- D) indexers ---")
stored = {r["id"]: r for r in db.query_all("SELECT * FROM prowlarr_indexer")}
check({i: (r["name"], r["protocol"], r["enabled"], r["removed"]) for i, r in stored.items()}
      == {i["id"]: (i["name"], i["protocol"], int(i["enable"]), 0) for i in INDEXERS}, f"the indexers' names, protocol and state ({sorted(r['name'] for r in stored.values())})")
answers["indexer"] = [{**INDEXERS[0], "name": "přejmenovaný", "enable": False}, {"id": 9, "name": "nový", "protocol": "torrent", "privacy": "public", "enable": True}]
synchronize()
after = {r["id"]: r for r in db.query_all("SELECT * FROM prowlarr_indexer")}
deleted = [i["id"] for i in INDEXERS[1:]]
check(after[INDEXERS[0]["id"]]["name"] == "přejmenovaný" and after[INDEXERS[0]["id"]]["enabled"] == 0 and after[9]["name"] == "nový"
      and all(after[i]["removed"] == 1 and after[i]["name"] == stored[i]["name"] for i in deleted),
      "a renaming and a switching off show, a new indexer is added, a deleted one stays with its name and the mark \"removed\"")
del answers["indexer"]
synchronize()
check(all(r["removed"] == 0 for r in db.query_all("SELECT * FROM prowlarr_indexer") if r["id"] != 9)
      and db.query_one("SELECT removed FROM prowlarr_indexer WHERE id = 9")["removed"] == 1, "an indexer that came back is no longer removed")
# Under the number of a removed indexer another one appears later (within one installation Prowlarr does not use numbers again,
# so it is another indexer): the old one keeps its name and sums under a number of its own, the new one starts from zero.
with db.connect() as conn:
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES ('2026-11-15 03:00:00', 9, 'Sonarr', 'query', 4, 0, 40, 4, 4)")
answers["indexer"] = [*INDEXERS, {"id": 9, "name": "Docela jiný", "protocol": "usenet", "privacy": "public", "enable": True}]
synchronize()
names = indexer_names()
moved = [number for number, (name, _) in names.items() if name == "nový"]
check(names[9] == ("Docela jiný", 0) and len(moved) == 1 and moved[0] < 0 and names[moved[0]][1] == 1
      and ("2026-11-15 03:00:00", moved[0], "Sonarr", "query") in sums() and ("2026-11-15 03:00:00", 9, "Sonarr", "query") not in sums(),
      f"another indexer under the number of a removed one: the old one moved with its sums, nothing got mixed ({names})")
answers["indexer"] = [{**INDEXERS[0], "name": "Jen přejmenovaný"}, *INDEXERS[1:]]
synchronize()
check(indexer_names()[INDEXERS[0]["id"]] == ("Jen přejmenovaný", 0) and len([c for c in indexer_names() if c < 0]) == 1,
      "another name for an indexer that was not removed is an ordinary renaming - nothing moves")
del answers["indexer"]
synchronize()
# A list of indexers in an unexpected shape (a proxy's page, an error object with status 200) is not "no indexers".
before = (indexer_names(), sums())
answers["indexer"] = httpx.Response(200, json={"error": "neco jineho"})
failed = attempt()
del answers["indexer"]
check(bool(failed) and (indexer_names(), sums()) == before and prowlarr.sync_state()["result"] == "error",
      f"an unreadable list of indexers is an error: nobody is marked as removed ({failed[:60]})")
synchronize()

print()
print("--- E) errors, concurrency, deleting ---")
before = (sums(), mark())
history_before = answers["history"]
answers["history"] = httpx.Response(401)
try:
    synchronize()
    error_401 = ""
except prowlarr.ProwlarrError as error:
    error_401 = error.translated()
check("401" in error_401 and (sums(), mark()) == before
      and prowlarr.sync_state()["result"] == "error" and "401" in prowlarr.sync_state()["error"],
      f"an app error deletes nothing and does not move the number of the last event ({error_401})")
answers["history"] = history_before


async def twice() -> list[Any]:
    return await asyncio.gather(prowlarr.synchronize(), prowlarr.synchronize(), return_exceptions=True)


results = asyncio.run(twice())
errors = [x for x in results if isinstance(x, prowlarr.ProwlarrError)]
check(len(errors) == 1 and errors[0].template is services.SYNC_ALREADY_RUNNING and prowlarr.sync_running() is False,
      f"a concurrent sync is refused with the shared sentence ({[type(x).__name__ for x in results]})")


async def delete_during() -> Any:
    task = asyncio.create_task(prowlarr.synchronize())
    await asyncio.sleep(0)
    try:
        return await prowlarr.delete_downloaded_data()
    except prowlarr.ProwlarrError as error:
        return error
    finally:
        await task


count = prowlarr.event_count()
check(isinstance(asyncio.run(delete_during()), prowlarr.ProwlarrError) and prowlarr.event_count() == count, "deleting during a sync is refused")
check(asyncio.run(prowlarr.delete_downloaded_data()) == count and prowlarr.event_count() == 0 and grabbed() == [], "deleting the downloaded data (from the web)")
check(KEY not in (db.get_setting("arr_prowlarr_state") or ""), "the key is not in the sync state")
db.set_setting("arr_prowlarr_url", "")
db.forget_settings()
try:
    synchronize()
    without_address = ""
except prowlarr.ProwlarrError as error:
    without_address = str(error)
check(bool(without_address), "a Prowlarr without an address is not synced")

print()
print("--- after the conversion to English names (database step 2) ---")
# The sums cannot be counted again and the marks say what is in them: a sync right after the conversion counts
# only what came after the mark. The old shape is made from today's by the map turned around.
from datetime import datetime, timedelta  # noqa: E402

from jellyscope import schema_names  # noqa: E402

answers.clear()
requests.clear()
before_request.clear()
answers["history"] = list(HISTORY)
connect_prowlarr(KEY)
prowlarr.delete_data()
synchronize()


def english_sums() -> dict[tuple, tuple]:
    return {(r["hour"], r["indexer_id"], r["app"], r["kind"]): (r["event_count"], r["failed_count"], r["response_sum"],
                                                                 r["response_count"], r["with_results"])
            for r in db.query_all("SELECT * FROM prowlarr_hour")}


def english_setting(key: str) -> str:
    db.forget_settings()
    return db.get_setting(key, "")


before_sums, before_grabs = english_sums(), db.query_value("SELECT COUNT(*) FROM prowlarr_grab")
before_marks = {k: english_setting(k) for k in ("arr_prowlarr_last_id", "arr_prowlarr_last_event_at", "arr_prowlarr_counted")}
renames = {t.new: t for t in schema_names.TABLES if t.new.startswith("prowlarr_")}
with db.connect() as conn:                 # the tables, the kinds and the keys as the dev before step 2 had them
    old_column = {new: {n: o for o, n in t.columns.items()} for new, t in renames.items()}
    conn.execute("UPDATE prowlarr_hour SET kind = 'dotaz' WHERE kind = 'query'")
    conn.execute("UPDATE prowlarr_grab SET method = 'presmerovani' WHERE method = 'redirect'")
    for new, table in renames.items():
        for index in table.indexes.values():
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        if table.old != new:
            conn.execute(f"ALTER TABLE {new} RENAME TO {table.old}")
        for column in conn.table_columns(table.old):
            if column in old_column[new]:
                conn.execute(f"ALTER TABLE {table.old} RENAME COLUMN {column} TO {old_column[new][column]}")
    for old, new in schema_names.SETTING_KEYS.items():
        if new.startswith("arr_prowlarr_"):
            conn.execute("UPDATE settings SET key = ? WHERE key = ?", (old, new))
    conn.commit()
db.init_db()
check(english_sums() == before_sums and db.query_value("SELECT COUNT(*) FROM prowlarr_grab") == before_grabs
      and {k: english_setting(k) for k in before_marks} == before_marks and bool(before_sums),
      f"converted: the sums, the grabbed releases and the marks as they were ({len(before_sums)} sums)")
again = synchronize()
check(again["new_count"] == 0 and english_sums() == before_sums, f"the sync right after the conversion counts nothing twice ({again})")
later = (datetime.strptime(newest_time(HISTORY), "%Y-%m-%d %H:%M:%S") + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
answers["history"] = [*HISTORY, make(9_000_001, later)]
more = synchronize()
total = sum(v[0] for v in english_sums().values())
check(more["new_count"] == 1 and total == sum(v[0] for v in before_sums.values()) + 1
      and english_setting("arr_prowlarr_counted") == str(int(before_marks["arr_prowlarr_counted"] or 0) + 1),
      f"and the next event is counted once, the count goes on ({more}, {total})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
