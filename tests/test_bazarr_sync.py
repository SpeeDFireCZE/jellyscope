# -*- coding: utf-8 -*-
r"""Bazarr: the sync of the subtitle history into the table `bazarr_event`.

Bazarr cannot return "only newer than...", its records have no number and the event's time is only
text. So the sync reads the history page by page from the newest, recognises a record by its
fingerprint and reads the time by itself. Guarded:

A) a record converted to a row (the language, the score, the episode, the fingerprint; a record
   without a language, a provider and a score);
B) the first run fetches everything, further ones end at the first page without anything new;
C) a big history: paging with an overlap, an interrupted first run and increment, pages moving during
   the read both ways, an answer without a count, two same events in the same second;
D) time: Bazarr's zone (and its change), an unknown format, one unreadable record, a format recognised
   on one page holds for the further ones too;
E) errors, concurrency, deleting the data; the key is not in the state.

The app does not run: the answers are recorded (tests/bazarr_responses/), the big history is built
from a recorded record.

Spec: docs/superpowers/specs/2026-11-14-bazarr-design.md

Run:
    .venv/bin/python tests/test_bazarr_sync.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bazarr-sync.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import BAZARR_RESPONSES, bazarr_client, bazarr_handler, recorded_response, connect_bazarr  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import bazarr  # noqa: E402
from jellyscope.arr.bazarr import sync  # noqa: E402

failures = 0
db.init_db()
# The sync's warnings (the logger jellyscope.arr) - the test looks at what went to the log.
log_lines: list[str] = []


class _ToList(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        log_lines.append(record.getMessage())


logging.getLogger("jellyscope.arr").addHandler(_ToList(level=logging.WARNING))
KEY = "tajny-klic"
EPISODES = recorded_response(BAZARR_RESPONSES, "episodes_history")
MOVIES = recorded_response(BAZARR_RESPONSES, "movies_history")
STATUS = recorded_response(BAZARR_RESPONSES, "system_status")
RECORDED = len(EPISODES["data"]) + len(MOVIES["data"])
TEMPLATE = next(r for r in EPISODES["data"] if r["action"] == 1)
C = "%m/%d/%y %H:%M:%S"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def rows(where: str = "", params: tuple = ()) -> list[dict[str, Any]]:
    return db.query_all(f"SELECT * FROM bazarr_event {where} ORDER BY happened_at DESC, fingerprint", params)


def make(i: int, base: datetime = datetime(2026, 11, 1, 12, 0), **changes: Any) -> dict[str, Any]:
    """A history record after the recorded one: the i-th newest (a minute older than the previous one), with a subs_id of its own."""
    return {**TEMPLATE, "subs_id": f"vyrobeny-{i}", "parsed_timestamp": (base - timedelta(minutes=i)).strftime(C), **changes}


# What Bazarr returns right now: the test changes it (a big history, errors, another zone).
answers: dict[str, Any] = {}
requests: list[str] = []
before_request: list[Any] = []               # functions called before every request for the episodes' history (moving pages)
without_total = {"on": False}                # a Bazarr that sends no `total` in its answer


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/episodes/history"):
        for function in before_request:
            function(request)
    answer = bazarr_handler(requests, answers)(request)
    if without_total["on"] and request.url.path.endswith("/history"):
        return httpx.Response(200, json={"data": answer.json()["data"]})
    return answer


def next_page(request: httpx.Request) -> bool:
    """Is it a request for the second or a further page? (Where exactly it starts is set by the pages' overlap.)"""
    return request.url.params.get("start") != "0"


def bazarr_fingerprints() -> set[str]:
    """The fingerprints of everything Bazarr has in its episodes' history right now."""
    return {bazarr.fingerprint(bazarr.event_from_history("episode", r)) for r in answers["episodes/history"]["data"]}


def pages(kind: str = "episodes") -> int:
    return sum(r.startswith(f"{kind}/history") for r in requests)


def synchronize() -> dict[str, Any]:
    return asyncio.run(bazarr.synchronize())


substitute(bazarr, "BazarrClient", bazarr_client(handler))
connect_bazarr(KEY)

print("--- A) converting a record ---")
r = bazarr.event_from_history("episode", TEMPLATE)
check((r["kind"], r["title"], r["title_id"], r["item_id"], r["action"]) == ("episode", TEMPLATE["seriesTitle"], TEMPLATE["sonarrSeriesId"], TEMPLATE["sonarrEpisodeId"], 1)
      and r["episode"] == "S{:02d}E{:02d}".format(*map(int, TEMPLATE["episode_number"].split("x"))),
      f"an episode: the title, the numbers from Sonarr, the episode as S01E02 ({r['title']} {r['episode']})")
check(r["language"] == TEMPLATE["language"]["code2"] and r["hi"] in (0, 1) and r["forced"] == 0 and r["provider"] == TEMPLATE["provider"]
      and r["score"] == float(TEMPLATE["score"].rstrip("%")) and r["time_text"] == TEMPLATE["parsed_timestamp"],
      f"the language, the provider and the score as a number ({r['language']}, {r['provider']}, {r['score']})")
deleted = bazarr.event_from_history("episode", next(r for r in EPISODES["data"] if r["action"] == 0))
uploaded = bazarr.event_from_history("episode", next(r for r in EPISODES["data"] if r["action"] == 4))
check(deleted["action"] == 0 and deleted["score"] is None and deleted["provider"] == "" and uploaded["provider"] == "manual"
      and uploaded["score"] == 100.0, "a deletion has no provider and no score; an upload has the provider \"manual\" and 100 %")
movie = bazarr.event_from_history("movie", MOVIES["data"][0])
check((movie["kind"], movie["title"], movie["episode"]) == ("movie", MOVIES["data"][0]["title"], "")
      and movie["title_id"] == movie["item_id"] == MOVIES["data"][0]["radarrId"], "a movie: the title and the number from Radarr, no episode")
odd = bazarr.event_from_history("episode", {**TEMPLATE, "score": "n/a", "language": None, "episode_number": "x", "action": "abc", "provider": None,
                                        "sonarrEpisodeId": "nic"})
check((odd["score"], odd["language"], odd["hi"], odd["episode"], odd["action"], odd["provider"], odd["item_id"])
      == (None, "", 0, "", -1, "", None), f"a record without a language, a score and with nonsense converts, it does not fail ({odd})")
check(bazarr.event_from_history("episode", {**TEMPLATE, "score": "250%"})["score"] is None
      and bazarr.event_from_history("episode", {**TEMPLATE, "language": {"code3": "ces", "name": "Czech"}})["language"] == "ces",
      "a score outside 0-100 is no score; a language without a two-letter code takes the three-letter one")
fingerprints = {bazarr.fingerprint(bazarr.event_from_history("episode", r)) for r in EPISODES["data"]}
check(len(fingerprints) == len(EPISODES["data"]) and bazarr.fingerprint(r) == bazarr.fingerprint(bazarr.event_from_history("episode", dict(TEMPLATE)))
      and bazarr.fingerprint(r) != bazarr.fingerprint(bazarr.event_from_history("movie", TEMPLATE)) and len(bazarr.fingerprint(r)) == 40,
      "the fingerprint: the same record the same, different records different (it replaces the record's number)")
check(bazarr.fingerprint(r) != bazarr.fingerprint({**r, "time_text": "11/14/26 17:56:16"}) and bazarr.fingerprint(r) != bazarr.fingerprint({**r, "subs_id": "jine"}),
      "the fingerprint stands on the text of the time as Bazarr sent it and on the subtitles' number at the provider")

for schema in ("schema.sql", "schema_postgres.sql"):
    schema_text = (PROJECT / "jellyscope" / schema).read_text(encoding="utf-8")
    check("ON bazarr_event (time_exact) WHERE time_exact = 0" in schema_text,
          f"{schema}: records without an exact time have an index of their own (partial) - Settings ask for them on every view")

print()
print("--- B) the first run and the increment ---")
check(bazarr.event_count() == 0 and bazarr.sync_running() is False and bazarr.sync_state()["result"] == "", "empty")
first = synchronize()
check(first["total"] == RECORDED, f"\"celkem\" is the count of the records read ({first['total']})")
check(first["new_count"] == RECORDED == bazarr.event_count() and pages("episodes") == 1 and pages("movies") == 1,
      f"the first run fetches the whole history of episodes and movies ({first}, {bazarr.event_count()})")
expected_time = datetime.strptime(TEMPLATE["parsed_timestamp"], C).strftime(db.TIME_FORMAT)        # the recorded Bazarr runs in UTC
stored = db.query_one("SELECT * FROM bazarr_event WHERE fingerprint = ?", (bazarr.fingerprint(r),)) or {}
check(stored.get("happened_at") == expected_time and stored.get("time_exact") == 1 and stored.get("language") == r["language"]
      and stored.get("score") == r["score"] and stored.get("synced_at"), f"the row has the event's time in UTC ({stored.get('happened_at')})")
state = bazarr.sync_state()
check(state["result"] == "ok" and state["new_count"] == RECORDED and state["without_time"] is False and db.get_setting("arr_bazarr_last_sync"),
      f"the sync state and the mark ({state})")
requests.clear()
second = synchronize()
check(second["new_count"] == 0 and bazarr.event_count() == RECORDED and pages("episodes") == 1 and pages("movies") == 1,
      f"the second run: nothing new, one page each ({second})")
answers["episodes/history"] = {"data": [make(0, datetime(2026, 11, 14, 19, 0)), *EPISODES["data"]], "total": EPISODES["total"] + 1}
third = synchronize()
check(third["new_count"] == 1 and bazarr.event_count() == RECORDED + 1 and len({x["fingerprint"] for x in rows()}) == RECORDED + 1,
      f"a new record at the start of the history is added once ({third})")
answers["episodes/history"] = {"data": [], "total": 0}
answers["movies/history"] = {"data": [], "total": 0}
synchronize()
check(bazarr.event_count() == RECORDED + 1, "history Bazarr deleted meanwhile stays in Jellyscope")

print()
print("--- C) a big history ---")
check(bazarr.delete_data() == RECORDED + 1 and bazarr.event_count() == 0 and not db.get_setting("arr_bazarr_last_sync")
      and bazarr.sync_state()["result"] == "", "deleting the data deletes the rows, the mark and the state")
sync.PAGE_SIZE = 100
BIG = [make(i) for i in range(250)]
answers["episodes/history"] = {"data": list(BIG), "total": 250}
# An interrupted first run: Bazarr fails on the second page.


def fail_on_second(request: httpx.Request) -> None:
    if next_page(request):
        raise httpx.ConnectError("Connection refused", request=request)


before_request.append(fail_on_second)
try:
    synchronize()
    failed = ""
except bazarr.BazarrError as error:
    failed = str(error)
before_request.clear()
after_interruption = bazarr.event_count()
check(bool(failed) and after_interruption == 100 and not db.get_setting("arr_bazarr_last_sync")
      and bazarr.sync_state()["result"] == "error" and KEY not in (db.get_setting("arr_bazarr_state") or ""),
      f"an interrupted first run: the error in the state (without the key), the mark does not move, the first page stays ({after_interruption})")
requests.clear()
completion = synchronize()
check(completion["total"] == 250, f"records of the pages' overlap do not count into \"celkem\" twice ({completion['total']})")
check(bazarr.event_count() == 250 and completion["new_count"] == 150 and pages("episodes") == 3,
      f"the next run runs through, even though it knows the first page already ({completion}, pages {pages('episodes')})")
# An increment to a big history: five new at the start -> the first page has news, the second no longer.
answers["episodes/history"] = {"data": [*(make(-1 - i) for i in reversed(range(5))), *BIG], "total": 255}
requests.clear()
increment = synchronize()
check(increment["new_count"] == 5 and bazarr.event_count() == 255 and pages("episodes") == 2,
      f"an increment: it reads only up to the first page without news ({increment}, pages {pages('episodes')})")
# An interrupted increment: 150 records were added (two pages of news), Bazarr fails on the second. The first
# page is stored already - if the next run ended at the first page without news, the other 50 would never
# arrive. So after an interruption the whole history is read again.
bazarr.delete_data()
answers["episodes/history"] = {"data": list(BIG), "total": 250}
synchronize()
answers["episodes/history"] = {"data": [*(make(-1 - i) for i in reversed(range(150))), *BIG], "total": 400}
mark_before_second: list[str] = []
before_request.append(lambda request: mark_before_second.append(db.get_setting("arr_bazarr_last_sync") or "")
                      if next_page(request) else None)
before_request.append(fail_on_second)
try:
    synchronize()
    failed = ""
except bazarr.BazarrError as error:
    failed = str(error)
before_request.clear()
check(bool(failed) and bazarr.event_count() == 350, f"an interrupted increment: the first page of news stays stored ({bazarr.event_count()})")
check(mark_before_second == [""], f"the mark disappeared before the first news was written - not even a crash of the process in the middle of a run leaves a hole ({mark_before_second})")
requests.clear()
synchronize()
check(bazarr.event_count() == 400 and pages("episodes") == 5,
      f"the next run reads the news after the interruption too - it reads the whole history ({bazarr.event_count()}, pages {pages('episodes')})")
requests.clear()
synchronize()
check(pages("episodes") == 1, "and the next one only the first page again")
# Pages moving during the first run: a new record arrives between pages and pushes the rows further.
bazarr.delete_data()
answers["episodes/history"] = {"data": list(BIG), "total": 250}
added = {"yet": False}


def add_during_read(request: httpx.Request) -> None:
    if next_page(request) and not added["yet"]:
        added["yet"] = True
        answers["episodes/history"] = {"data": [make(-50), *BIG], "total": 251}


before_request.append(add_during_read)
synchronize()
before_request.clear()
check(bazarr.event_count() == 250 and len({x["fingerprint"] for x in rows()}) == 250,
      f"pages moving during the read: nothing doubles and none of the original records is missing ({bazarr.event_count()})")
synchronize()
check(bazarr.event_count() == 251, "and the record added during the read arrives next time")
# A move the other way: Bazarr shortens the history between pages (a deleted series takes its records with
# it) and the rest moves up. Without the pages' overlap the same number of records would be skipped and
# further runs, which end early, would never read them.
bazarr.delete_data()
answers["episodes/history"] = {"data": list(BIG), "total": 250}
shortened = {"yet": False}


def shorten_during_read(request: httpx.Request) -> None:
    if next_page(request) and not shortened["yet"]:
        shortened["yet"] = True
        answers["episodes/history"] = {"data": BIG[5:], "total": 245}


before_request.append(shorten_during_read)
synchronize()
before_request.clear()
stored_fingerprints = {x["fingerprint"] for x in rows()}
check(bazarr_fingerprints() <= stored_fingerprints and len(stored_fingerprints) == 250,
      f"Bazarr deleted five records during the read: nothing of what it has left is missing ({len(bazarr_fingerprints() - stored_fingerprints)} missing, stored {len(stored_fingerprints)})")
# An answer without `total`: it reads as long as pages come - not only the first.
bazarr.delete_data()
answers["episodes/history"] = {"data": list(BIG), "total": 250}
without_total["on"] = True
synchronize()
check(bazarr.event_count() == 250, f"a Bazarr that sends no count of records: the history is read whole ({bazarr.event_count()})")
# ... and when on top of that it does not page (it keeps returning the same page), the read does not loop.
bazarr.delete_data()
same_page = {"on": True}


def always_first(request: httpx.Request) -> None:
    """A server that ignores `start`: it rewrites it to zero."""
    if same_page["on"] and next_page(request):
        request.url = request.url.copy_set_param("start", "0")


before_request.append(always_first)
requests.clear()
synchronize()
before_request.clear()
without_total["on"] = False
check(pages("episodes") <= 3 and bazarr.event_count() == 100,
      f"a server that does not page and sends no count: the read ends at the repeated page ({pages('episodes')} requests, not ten thousand)")
# Two really different events with the same data in the same second (two timing syncs of the same episode):
# Bazarr keeps them as two records, it gives them no number. In the history they are to be twice too.
bazarr.delete_data()
extra_action = datetime(2026, 11, 2, 9, 0)
TWINS = {**make(0, extra_action, action=5, provider=None, score=None), "subs_id": None}
answers["episodes/history"] = {"data": [make(1, extra_action), TWINS, dict(TWINS), make(3, extra_action)], "total": 4}
pair = synchronize()
check(pair["new_count"] == 4 and bazarr.event_count() == 4, f"two same events in the same second are two rows ({pair})")
check(synchronize()["new_count"] == 0 and bazarr.event_count() == 4, "and the next sync does not multiply them")
answers["episodes/history"] = {"data": [make(-1, extra_action), make(1, extra_action), TWINS], "total": 3}
check(synchronize()["new_count"] == 1 and bazarr.event_count() == 5, "one of the pair seen alone (the other is on the next page) adds no new row")
sync.PAGE_SIZE = 500

print()
print("--- D) time: the zone and an unknown format ---")
bazarr.delete_data()
answers["system/status"] = {"data": {**STATUS["data"], "timezone": "Europe/Prague"}}
answers["episodes/history"] = {"data": [make(0, datetime(2026, 11, 14, 8, 31, 35))], "total": 1}
synchronize()
check([x["happened_at"] for x in rows()] == ["2026-11-14 07:31:35"], f"the time converts from Bazarr's zone to UTC ({[x['happened_at'] for x in rows()]})")
# Bazarr switched to another zone (the container's TZ fixed). Bazarr stores the local time without a zone
# (datetime.now()), so the text of an old event stays the same - only the zone it reports changes.
# It is still the same event: it must not be added a second time and the time it has is not recalculated by the new zone.
for zone in ("Etc/UTC", "Undefined", "America/New_York"):
    answers["system/status"] = {"data": {**STATUS["data"], "timezone": zone}}
    after_change = synchronize()
    check(after_change["new_count"] == 0 and [x["happened_at"] for x in rows()] == ["2026-11-14 07:31:35"],
          f"a change of Bazarr's zone to {zone} does not double the history and does not rewrite the stored time ({after_change}, {[x['happened_at'] for x in rows()]})")
# One unreadable record among readable ones: only it loses the date, not the whole page.
bazarr.delete_data()
answers["system/status"] = STATUS
answers["episodes/history"] = {"data": [make(0), make(1, parsed_timestamp=""), make(2, parsed_timestamp="kdoví kdy"), make(3)], "total": 4}
log_lines.clear()
synchronize()
check(sum("time format" in line for line in log_lines) == 1, f"a warning that part of the times were not read goes to the log ({log_lines})")
check(sorted(x["happened_at"] is None for x in rows()) == [False, False, True, True] and bazarr.sync_state()["without_time"] is True,
      f"two unreadable times on a page: only those two records are without a date ({[x['happened_at'] for x in rows()]})")
# A format recognised on one page holds where the page itself does not decide: the episodes have a day
# greater than twelve (day/month), the movies could be read either way.
bazarr.delete_data()
answers["episodes/history"] = {"data": [make(0, parsed_timestamp="14/11/26 10:00:00"), make(1, parsed_timestamp="13/11/26 10:00:00")], "total": 2}
answers["movies/history"] = {"data": [{**MOVIES["data"][0], "parsed_timestamp": "05/06/26 08:00:00"},
                                      {**MOVIES["data"][1], "parsed_timestamp": "04/06/26 09:00:00"}], "total": 2}
log_lines.clear()
synchronize()
check(sorted(x["happened_at"] for x in rows("WHERE kind = 'movie'")) == ["2026-06-04 09:00:00", "2026-06-05 08:00:00"],
      "the movies are read in the format recognised for the episodes - the 5th of June, not the 6th of May")
check(not any("time format" in line for line in log_lines), "and when everything was read, there is no warning in the log")
# The format inherited from the episodes, but the movies come garbled: none is read and the log must say so.
bazarr.delete_data()
answers["movies/history"] = {"data": [{**MOVIES["data"][0], "parsed_timestamp": "kdoví"}, {**MOVIES["data"][1], "parsed_timestamp": "kdysi"}], "total": 2}
log_lines.clear()
synchronize()
check(sum("time format" in line for line in log_lines) == 1 and all(x["happened_at"] is None for x in rows("WHERE kind = 'movie'")),
      "a page on which nothing can be read with the inherited format: records without a date and a warning in the log")
answers["movies/history"] = {"data": [], "total": 0}
bazarr.delete_data()
answers["system/status"] = STATUS
UNKNOWN = [make(i, parsed_timestamp=f"14 listopadu 2026, {17 - i}:00") for i in range(3)]
answers["episodes/history"] = {"data": list(UNKNOWN), "total": 3}
synchronize()
check(bazarr.event_count() == 3 and all(x["happened_at"] is None for x in rows()) and bazarr.sync_state()["without_time"] is True
      and bazarr.sync_state()["result"] == "ok",
      "an unknown time format: the first run stores the records without a date and the state says so")
answers["episodes/history"] = {"data": [make(9, parsed_timestamp="14 listopadu 2026, 18:00"), *UNKNOWN], "total": 4}
before = db.utcnow()
synchronize()
new = [x for x in rows() if x["happened_at"] is not None]
check(len(new) == 1 and new[0]["time_exact"] == 0 and new[0]["happened_at"] >= before and sum(x["happened_at"] is None for x in rows()) == 3,
      f"a further run: a new record gets the time Jellyscope first saw it; the old ones stay without a date ({[x['happened_at'] for x in new]})")
synchronize()
check([x["happened_at"] for x in rows() if x["happened_at"] is not None] == [new[0]["happened_at"]], "and that time does not change with further runs")

print()
print("--- E) errors, concurrency, deleting ---")
bazarr.delete_data()
answers.clear()
synchronize()
mark, count = db.get_setting("arr_bazarr_last_sync"), bazarr.event_count()
answers["episodes/history"] = httpx.Response(401)
try:
    synchronize()
    error_401 = ""
except bazarr.BazarrError as error:
    error_401 = error.translated()
db.forget_settings()
check("401" in error_401 and bazarr.event_count() == count and db.get_setting("arr_bazarr_last_sync") == mark
      and bazarr.sync_state()["result"] == "error" and "401" in bazarr.sync_state()["error"],
      f"an app error deletes nothing and does not move the mark ({error_401})")
answers.clear()


async def twice() -> list[Any]:
    return await asyncio.gather(bazarr.synchronize(), bazarr.synchronize(), return_exceptions=True)


results = asyncio.run(twice())
errors = [r for r in results if isinstance(r, bazarr.BazarrError)]
check(len(errors) == 1 and errors[0].template == "{name} sync is already running." and bazarr.sync_running() is False,
      f"a concurrent sync is refused ({[type(r).__name__ for r in results]})")


async def delete_during() -> Any:
    task = asyncio.create_task(bazarr.synchronize())
    await asyncio.sleep(0)
    try:
        return await bazarr.delete_downloaded_data()
    except bazarr.BazarrError as error:
        return error
    finally:
        await task


check(isinstance(asyncio.run(delete_during()), bazarr.BazarrError) and bazarr.event_count() == count, "deleting during a sync is refused")
check(asyncio.run(bazarr.delete_downloaded_data()) == count and bazarr.event_count() == 0, "deleting the downloaded data (from the web)")
check(KEY not in (db.get_setting("arr_bazarr_state") or ""), "the key is not in the sync state")
db.set_setting("arr_bazarr_url", "")
db.forget_settings()
try:
    synchronize()
    without_connection = ""
except bazarr.BazarrError as error:
    without_connection = str(error)
check(bool(without_connection), "a Bazarr without an address is not synced")

print()
print("--- after the conversion to English names (database step 2) ---")
# The fingerprint is the primary key and was computed from the Czech kind ("episode"/"film") before 1.9.0. The stored
# kind is "episode"/"movie" now, but the fingerprint's input stays the old word - otherwise the next sync after
# the conversion would store every event again under a new fingerprint.
import hashlib  # noqa: E402

from jellyscope import schema_names  # noqa: E402


def old_fingerprint(czech_kind: str, event: dict[str, Any]) -> str:
    """The fingerprint as the code before 1.9.0 made it."""
    by = (czech_kind, event["item_id"], event["action"], event["language"], event["hi"], event["forced"],
          event["provider"], event["subs_id"], event["time_text"])
    return hashlib.sha1("|".join(str(c) for c in by).encode(), usedforsecurity=False).hexdigest()


episode, movie = bazarr.event_from_history("episode", TEMPLATE), bazarr.event_from_history("movie", TEMPLATE)
check(bazarr.fingerprint(episode) == old_fingerprint("dil", episode) and bazarr.fingerprint(movie) == old_fingerprint("film", movie),
      "the fingerprint is computed from the old word of the kind")
answers.clear()
answers.update({"episodes/history": dict(EPISODES), "movies/history": dict(MOVIES)})
connect_bazarr(KEY)
db.set_setting(sync.MARK_KEY, "")
synchronize()
stored = int(db.query_value("SELECT COUNT(*) FROM bazarr_event") or 0)
table = next(t for t in schema_names.TABLES if t.new == "bazarr_event")
with db.connect() as conn:                 # the table as 1.8.x / the dev before step 2 had it
    for index in table.indexes.values():
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute("UPDATE bazarr_event SET kind = CASE kind WHEN 'episode' THEN 'dil' ELSE 'film' END")
    conn.execute("ALTER TABLE bazarr_event RENAME TO bazarr_udalost")
    for old, new in table.columns.items():
        conn.execute(f"ALTER TABLE bazarr_udalost RENAME COLUMN {new} TO {old}")
    conn.commit()
db.init_db()
again = synchronize()
check(stored > 0 and again["new_count"] == 0 and int(db.query_value("SELECT COUNT(*) FROM bazarr_event") or 0) == stored
      and db.query_value("SELECT COUNT(*) FROM bazarr_event WHERE kind NOT IN ('episode', 'movie')") == 0,
      f"the sync right after the conversion stores no event twice ({stored} stored, {again['new_count']} new)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
