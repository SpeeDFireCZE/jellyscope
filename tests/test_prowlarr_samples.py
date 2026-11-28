# -*- coding: utf-8 -*-
r"""Prowlarr: stored samples of the indexers' status (who Prowlarr leaves out, measured now and then).

Prowlarr's history does not see a block - an indexer that refuses connections writes nothing there,
Prowlarr only leaves it out for a while. So Jellyscope keeps samples of `indexerstatus`
(`prowlarr_sample`): a row for every sample (indexer 0) and a row for every indexer left out at
that moment, with since when it fails, until when it is left out and its most recent failure.
Watched:

A) the table is in both schemas and in the list of tables;
B) the *Arr sync task samples after the Prowlarr part: a clean sample (nobody left out) is stored
   too, a left-out indexer with its times, every run (no limit), "Sync now" alone does not sample;
C) a page that loads the live state samples at most once every 10 minutes, from the answers it
   fetched anyway (no request of its own);
D) nothing is stored when the status or the list of indexers did not load; the warnings are not
   needed; a failed write breaks neither the page nor the sync;
E) "Delete downloaded data" deletes the samples, the history retention removes old ones, a
   renumbered indexer's samples move with its sums;
F) the usual interval between samples (what the availability counts as a gap) follows the *Arr sync
   task: its interval while it runs on its own, otherwise the pages' ten minutes.

Prowlarr is not run: the answers are recorded (tests/prowlarr_responses/), the client talks to
httpx.MockTransport (tests/_apps.py).

Spec: docs/superpowers/specs/2026-11-26-live-cards-design.md (B1)

Run:
    .venv/bin/python tests/test_prowlarr_samples.py
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-samples.db")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    PROWLARR_RESPONSES, connect_prowlarr, login, prowlarr_client, prowlarr_handler, recorded_response,
)
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, dialect, purge  # noqa: E402
from jellyscope.arr import prowlarr, registry  # noqa: E402

failures = 0
db.init_db()
KEY = "secret-key"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


INDEXERS = recorded_response(PROWLARR_RESPONSES, "indexer")
RECORDED_BLOCK = recorded_response(PROWLARR_RESPONSES, "indexerstatus")[0]
IN_AN_HOUR = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
AN_HOUR_AGO = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
# The recorded block expired long ago - for the test it lasts another hour.
BLOCKED = {**RECORDED_BLOCK, "disabledTill": IN_AN_HOUR}
OTHER = next(i["id"] for i in INDEXERS if i["id"] != BLOCKED["indexerId"])
# A block that expired already (by Jellyscope's clock) and a block of an indexer that is not in the list.
EXPIRED = {**RECORDED_BLOCK, "indexerId": OTHER, "disabledTill": AN_HOUR_AGO}
UNKNOWN = {**RECORDED_BLOCK, "indexerId": 77, "disabledTill": IN_AN_HOUR}

asked: list[str] = []
overrides: dict[str, Any] = {"indexerstatus": []}
substitute(prowlarr, "ProwlarrClient", prowlarr_client(prowlarr_handler(asked, overrides)))
connect_prowlarr(KEY)

captured: list[str] = []


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        captured.append(record.getMessage())


logging.getLogger("jellyscope").addHandler(Capture())


def samples() -> list[tuple[Any, ...]]:
    """The stored samples as (time, indexer, failing since, left out until, most recent failure), oldest first."""
    return [(r["sampled_at"], r["indexer_id"], r["failing_since"], r["skipped_until"], r["last_failure"])
            for r in db.query_all("SELECT * FROM prowlarr_sample ORDER BY sampled_at, indexer_id")]


def runs() -> list[str]:
    """The times of the samples (their own rows, indexer 0)."""
    return [s[0] for s in samples() if s[1] == 0]


def own_rows() -> list[tuple[Any, ...]]:
    """The samples' own rows as (time, the indexers enabled then, the usual interval in seconds), oldest first."""
    return [(r["sampled_at"], r["enabled_ids"], r["interval_seconds"])
            for r in db.query_all("SELECT * FROM prowlarr_sample WHERE indexer_id = 0 ORDER BY sampled_at")]


# The recording's indexers are all enabled: a sample's own row names them all, as samples.id_list writes them.
ENABLED_IDS = "".join(f",{n}" for n in sorted(i["id"] for i in INDEXERS if i["enable"])) + ","


def shift_samples(minutes: int) -> None:
    """Move every stored sample `minutes` into the past, as if it were taken that long ago."""
    rows = [dict(r) for r in db.query_all("SELECT * FROM prowlarr_sample")]
    with db.connect() as conn:
        conn.execute("DELETE FROM prowlarr_sample")
        for row in rows:
            row["sampled_at"] = (datetime.strptime(row["sampled_at"], db.TIME_FORMAT) - timedelta(minutes=minutes)).strftime(db.TIME_FORMAT)
            conn.execute(f"INSERT INTO prowlarr_sample ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))


def clear_samples() -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM prowlarr_sample")


def sync_task() -> dict[str, Any]:
    """The Prowlarr part of the *Arr sync task (the registry syncs every connected app)."""
    return asyncio.run(registry.synchronize_all())["prowlarr"]


def page_load() -> dict[str, Any]:
    """The live state as a page loads it, after the one-minute cache expired."""
    prowlarr.live.CACHE.clear()
    return asyncio.run(prowlarr.live_state())


def utc(text: str) -> str:
    return text.replace("T", " ").rstrip("Z")


print("--- A) the table ---")
for schema in ("schema.sql", "schema_postgres.sql"):
    text = (PROJECT / "jellyscope" / schema).read_text(encoding="utf-8")
    check("CREATE TABLE IF NOT EXISTS prowlarr_sample (" in text, f"{schema} creates prowlarr_sample")
check("prowlarr_sample" in db.TABLES and db.TABLES.index("prowlarr_sample") > db.TABLES.index("prowlarr_indexer"),
      "the table is in db.TABLES (the backup and the conversion between databases) beside Prowlarr's other tables")
check(samples() == [], "nothing is sampled before the first sync or page")

print()
print("--- B) the *Arr sync task samples after the Prowlarr part ---")
first = sync_task()
check(first.get("new_count", 0) > 0 and len(runs()) == 1 and samples() == [(runs()[0], 0, None, None, None)],
      f"a clean sample after the sync: a row of the sample itself, nobody left out ({first}, {samples()})")
check(abs(datetime.strptime(runs()[0], db.TIME_FORMAT).replace(tzinfo=UTC) - datetime.now(UTC)) < timedelta(minutes=1),
      f"the sample carries its own time in UTC ({runs()[0]})")
check(len(INDEXERS) == 3 and own_rows() == [(runs()[0], ENABLED_IDS, 3600)],
      f"its own row keeps the indexers there and enabled at that moment and the usual interval in force, the task's hour ({own_rows()})")
shift_samples(1)
overrides["indexerstatus"] = [BLOCKED, EXPIRED, UNKNOWN]
sync_task()
latest = runs()[-1]
check(len(runs()) == 2 and [s for s in samples() if s[0] == latest] == [
        (latest, 0, None, None, None),
        (latest, BLOCKED["indexerId"], utc(BLOCKED["initialFailure"]), utc(IN_AN_HOUR), utc(BLOCKED["mostRecentFailure"]))],
      f"an indexer left out: since when it fails, until when it is left out, its most recent failure; an expired block and an "
      f"indexer missing from the list are not rows ({samples()})")
shift_samples(1)
sync_task()
check(len(runs()) == 3, f"the task samples on every run, without the page's ten-minute limit ({runs()})")
before = samples()
asyncio.run(prowlarr.synchronize())
check(samples() == before, "\"Sync now\" in Settings (the sync alone) takes no sample - only the task does")

print()
print("--- C) a page samples at most once every 10 minutes ---")
clear_samples()
overrides["indexerstatus"] = [BLOCKED]
asked.clear()
state = page_load()
check(state["available"] and len(runs()) == 1 and len(samples()) == 2, f"a page that loads the live state samples ({samples()})")
check(sorted(a.split("?")[0] for a in asked) == ["applications", "health", "indexer", "indexerstatus"],
      f"from the answers the live state fetched anyway: no request of its own ({asked})")
# One indexer switched off by hand and the *Arr task off (only pages sample, every ten minutes at most): the next page's
# sample says so - the disabled indexer is not in its list, the interval is the pages' ten minutes.
clear_samples()
overrides["indexer"] = [{**i, "enable": False} if i["id"] == OTHER else i for i in INDEXERS]
db.set_setting(prowlarr.samples.TASK_ENABLED, "0")
db.forget_settings()
page_load()
check(own_rows() == [(runs()[0], "".join(f",{i['id']}" for i in sorted(INDEXERS, key=lambda i: i["id"]) if i["id"] != OTHER) + ",", 600)],
      f"a page's sample: without the indexer switched off by hand, the interval of the pages ({own_rows()})")
del overrides["indexer"]
db.set_setting(prowlarr.samples.TASK_ENABLED, "1")
db.forget_settings()
clear_samples()
page_load()
page_load()
shift_samples(9)
page_load()
check(len(runs()) == 1, f"another page within 10 minutes does not sample again ({runs()})")
shift_samples(2)
page_load()
check(len(runs()) == 2, f"after 10 minutes the next page samples again ({runs()})")
asked.clear()
asyncio.run(prowlarr.live_state())
check(len(runs()) == 2 and asked == [], "a page served from the one-minute cache neither asks nor samples")
accounts.create("admin", "longpassword", is_admin=True)
admin = login("admin")
shift_samples(11)
prowlarr.live.CACHE.clear()
page = admin.get("/arr/prowlarr?days=0")
check(page.status_code == 200 and len(runs()) == 3, f"the Prowlarr tab samples as well ({page.status_code}, {runs()})")

print()
print("--- D) nothing is stored when the status did not load ---")
shift_samples(11)
before = samples()
overrides["indexerstatus"] = httpx.Response(500)
partial = page_load()
check(partial["available"] and samples() == before, "a page whose status did not load stores nothing")
sync_task()
check(samples() == before, "neither does the sync task (the history synced, the status did not load)")
# Status 200 that is no list (a reverse proxy's or an error object, an empty body) says nothing about who is left out:
# not "nobody", which would end a real outage early - the same rule as the list of indexers.
for answer, label in ((httpx.Response(200, json={"message": "Bad gateway"}), "an object"), (httpx.Response(200, content=b""), "an empty body")):
    overrides["indexerstatus"] = answer
    page_load()
    sync_task()
    check(samples() == before and page_load()["unavailable_count"] is None,
          f"status 200 with {label} in place of the list: no sample from a page nor the task, the count unknown")
overrides["indexerstatus"] = [BLOCKED]
overrides["indexer"] = httpx.Response(500)
page_load()
check(samples() == before, "without the list of indexers there is no sample either")
del overrides["indexer"]
overrides["health"] = httpx.Response(500)
page_load()
check(len(runs()) == len([s for s in before if s[1] == 0]) + 1, "the warnings are not needed: without them the sample is stored")
del overrides["health"]
# A write that fails (here the table is gone) must not break the page nor the sync.
shift_samples(11)
with db.connect() as conn:
    conn.execute("DROP TABLE prowlarr_sample")
captured.clear()
broken = page_load()
check(broken["available"] and broken["indexers"] and broken["unavailable_count"] == 1, "a failed write does not break the live state of the page")
check(any("could not store a sample of the indexers' status" in m for m in captured), f"it is logged ({captured[-1:]})")
result = sync_task()
check("error" not in result and "new_count" in result and not any("the counts were not updated" in m for m in captured),
      f"nor the sync task ({result})")
db.init_db()
check(samples() == [], "(the table is back)")


@contextlib.contextmanager
def another_writer() -> Iterator[None]:
    """Another writer holding the samples' lock meanwhile (a VACUUM, a restore, a long import)."""
    if db.current_dialect() == dialect.POSTGRES:
        with db.connect() as conn:
            conn.execute("LOCK TABLE prowlarr_sample IN EXCLUSIVE MODE")
            yield
            conn.rollback()
        return
    blocker = sqlite3.connect(os.environ["DATABASE_PATH"], isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


# A page's sample is a write on the side: it waits for another writer only briefly (as the sessions' touch) and is
# skipped quietly - not the connection's full lock wait (15 s on SQLite, no limit on PostgreSQL) on every page.
sync_task()
shift_samples(11)
before = samples()
captured.clear()
with another_writer():
    started = time.monotonic()
    waiting = page_load()
    waited = time.monotonic() - started
check(waiting["available"] and samples() == before and waited < 5,
      f"a page under another writer's lock skips its sample after a short wait ({waited:.1f} s)")
check(not any("could not store a sample" in m for m in captured), f"quietly: the next page tries again ({captured[-1:]})")
page_load()
check(len(runs()) == len([s for s in before if s[1] == 0]) + 1, "and the next page, the writer gone, samples")

print()
print("--- E) deleting, retention, renumbering ---")
sync_task()
check(samples(), "(a sample is there)")
deleted = asyncio.run(registry.APPS["prowlarr"].delete_downloaded_data())
check(deleted > 0 and samples() == [], "\"Delete downloaded data\" of Prowlarr deletes the samples with the history")


def stored_at(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime(db.TIME_FORMAT)


OLD, KEPT = stored_at(400), stored_at(300)
with db.connect() as conn:
    for at in (OLD, KEPT):
        conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id) VALUES (?, 0)", (at,))
        conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id, failing_since, skipped_until) VALUES (?, 2, ?, ?)", (at, at, at))
purge.delete_old(365)
check(sorted({s[0] for s in samples()}) == [KEPT], f"the history retention removes the samples older than the kept history ({samples()})")
clear_samples()

# A renumbered indexer: under the number of a removed one another indexer appears - the old one's samples move with its
# sums under its own negative number, so they keep its name.
asyncio.run(prowlarr.synchronize())
SET_ASIDE = next(i for i in INDEXERS if i["id"] == OTHER)
with db.connect() as conn:
    conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id, enabled_ids, interval_seconds) VALUES ('2026-11-20 10:00:00', 0, ?, 3600)",
                 (f",1{OTHER},{OTHER},",))
    conn.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id, failing_since, skipped_until)"
                 " VALUES ('2026-11-20 10:00:00', ?, '2026-11-20 09:00:00', '2026-11-20 11:00:00')", (OTHER,))
overrides["indexer"] = [i for i in INDEXERS if i["id"] != OTHER]
asyncio.run(prowlarr.synchronize())
overrides["indexer"] = [*overrides["indexer"], {**SET_ASIDE, "name": "Quite another indexer"}]
asyncio.run(prowlarr.synchronize())
names = {r["id"]: r["name"] for r in db.query_all("SELECT id, name FROM prowlarr_indexer")}
moved = [s[1] for s in samples() if s[1] != 0]
check(names[OTHER] == "Quite another indexer" and len(moved) == 1 and moved[0] < 0 and names.get(moved[0]) == SET_ASIDE["name"],
      f"the removed indexer's samples moved with it under its own number and keep its name ({moved}, {names})")
check(own_rows() == [("2026-11-20 10:00:00", f",1{OTHER},{moved[0]},", 3600)],
      f"so does its number in the samples' lists of enabled indexers - whole, not the {OTHER} of 1{OTHER} ({own_rows()})")
del overrides["indexer"]

print()
print("--- F) the usual interval between samples ---")
from jellyscope import tasks  # noqa: E402

ARR_TASK = tasks.TASKS["arr"]
check((ARR_TASK.enabled_setting, ARR_TASK.interval_setting) == (prowlarr.samples.TASK_ENABLED, prowlarr.samples.TASK_MINUTES)
      and ARR_TASK.default_minutes == prowlarr.samples.TASK_DEFAULT_MINUTES,
      "the interval is read from the *Arr sync task's own settings, with its default")
check(prowlarr.sample_interval() == timedelta(minutes=ARR_TASK.default_minutes), f"by default the task's hour ({prowlarr.sample_interval()})")
db.set_setting(ARR_TASK.interval_setting, "15")
db.forget_settings()
check(prowlarr.sample_interval() == timedelta(minutes=15), f"the task every 15 minutes: 15 minutes ({prowlarr.sample_interval()})")
db.set_setting(ARR_TASK.interval_setting, "0")
db.forget_settings()
check(prowlarr.sample_interval() == prowlarr.samples.EVERY, "the task without an interval: only the pages sample, every ten minutes at most")
db.set_setting(ARR_TASK.interval_setting, "120")
db.set_setting(ARR_TASK.enabled_setting, "0")
db.forget_settings()
check(prowlarr.sample_interval() == prowlarr.samples.EVERY, "the task switched off: the pages' ten minutes")
db.set_setting(ARR_TASK.enabled_setting, "1")
db.forget_settings()
check(prowlarr.sample_interval() == timedelta(hours=2), "switched on again: its two hours")
# The *Arr sync switched to a time every day (28. 11. 2026): it samples once a day, whatever its stored interval says.
check(ARR_TASK.mode_setting == prowlarr.samples.TASK_MODE, "the chosen schedule is read from the task's own setting")
tasks.set_mode(ARR_TASK, daily=True)
db.forget_settings()
check(prowlarr.sample_interval() == timedelta(days=1), f"the task daily: a day ({prowlarr.sample_interval()})")
tasks.set_mode(ARR_TASK, daily=False)
db.forget_settings()
check(prowlarr.sample_interval() == timedelta(hours=2), "every N again: its two hours")

print()
print("--- a samples table from before its last columns gets them at the start ---")
# Shown on SQLite, where the old shape can be made as a file; the migration itself runs on both databases.
if db.database_config().kind == "sqlite":
    # The table grew while the feature was being built (enabled_ids, interval_seconds, then measured_until). A database
    # that ran an earlier build has the table without them, `CREATE TABLE IF NOT EXISTS` skips it, and the card failed
    # with "no such column" while the measuring loop wrote a warning every round. The start adds what is missing.
    old_shape = str(Path(_tmp) / "old-samples.db")
    with contextlib.closing(sqlite3.connect(old_shape)) as raw:
        raw.execute("CREATE TABLE prowlarr_sample (sampled_at TEXT NOT NULL, indexer_id INTEGER NOT NULL, failing_since TEXT,"
                    " skipped_until TEXT, last_failure TEXT, PRIMARY KEY (sampled_at, indexer_id))")
        raw.execute("INSERT INTO prowlarr_sample (sampled_at, indexer_id) VALUES ('2026-11-25 10:00:00', 0)")
        raw.commit()
    from jellyscope import config as _config  # noqa: E402 - the path of the database is kept there too

    previous_path = os.environ["DATABASE_PATH"]
    os.environ["DATABASE_PATH"] = old_shape
    _config.load_config(reload=True)
    db.database_config(reload=True)
    db.init_db()
    with db.connect() as conn:
        columns = set(conn.table_columns("prowlarr_sample"))
        kept = conn.execute("SELECT COUNT(*) FROM prowlarr_sample").fetchone()[0]
    check({"enabled_ids", "interval_seconds", "measured_until"} <= columns and kept == 1 and db.database_config().path == old_shape,
          f"the missing columns are added and the stored sample stays ({sorted(columns)}, {kept} row)")
    os.environ["DATABASE_PATH"] = previous_path
    _config.load_config(reload=True)
    db.database_config(reload=True)
    db.forget_settings()
else:
    print("(skipped on PostgreSQL: the old table is made as an SQLite file)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
