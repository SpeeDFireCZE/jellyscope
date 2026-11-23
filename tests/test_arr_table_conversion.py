# -*- coding: utf-8 -*-
r"""Converting the shared tables of Sonarr and Radarr to tables per app.

Until 15. 11. 2026 Sonarr and Radarr had shared tables `arr_event` and
`arr_titul` with a column `app`. Now every app has its own (`sonarr_udalost`,
`sonarr_titul`, `radarr_udalost`, `radarr_titul`), and a database in the old
shape is converted by itself at start (`db.init_db`). Guarded:

A) a new database has the new tables and not the old ones;
B) an old database: after the start the rows are in the tables of their app,
   the old tables are gone, the sync marks stayed; a movie has no episode,
   TVDB id or Jellyfin series id - Radarr does not have those columns;
C) a second start changes nothing, and the next sync downloads nothing twice;
D) unusual states: only one of the old tables, a titles table from the time
   before the column `pridano`, new tables with a part of the rows already, a
   row of an unknown app;
E) an error in the middle of the conversion changes nothing, and the next
   start converts.

Runs on SQLite and on PostgreSQL (through .superpowers/tools/test_on_pg.py).

Spec: docs/superpowers/specs/2026-11-15-arr-strom-design.md

Run:
    .venv/bin/python tests/test_arr_table_conversion.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "arr-conversion.db")
os.environ["SECRET_KEY"] = "test-signing-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import (  # noqa: E402
    RADARR_RESPONSES, SONARR_RESPONSES, no_network, recorded_response, radarr_client, radarr_handler, sonarr_client, sonarr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import db, schema_names  # noqa: E402
from jellyscope.arr import radarr, sonarr  # noqa: E402

failures = 0

# The old tables as the schema created them before the split (jellyscope/schema.sql until 15. 11. 2026).
OLD_EVENTS_DDL = """CREATE TABLE arr_event (
    app TEXT NOT NULL, arr_id INTEGER NOT NULL, typ TEXT NOT NULL, datum TEXT NOT NULL, titul TEXT NOT NULL DEFAULT '',
    epizoda TEXT NOT NULL DEFAULT '', polozka INTEGER, titul_id INTEGER, kvalita TEXT NOT NULL DEFAULT '', rozliseni INTEGER,
    velikost BIGINT, indexer TEXT NOT NULL DEFAULT '', release_group TEXT NOT NULL DEFAULT '', klient TEXT NOT NULL DEFAULT '',
    protokol TEXT NOT NULL DEFAULT '', zdroj TEXT NOT NULL DEFAULT '', duvod TEXT NOT NULL DEFAULT '', download_id TEXT,
    vydano TEXT, cf_skore INTEGER, PRIMARY KEY (app, arr_id))"""
OLD_INDEX = "CREATE INDEX idx_arr_event_typ ON arr_event (app, typ, datum)"
OLD_TITLES_DDL = """CREATE TABLE arr_titul (
    app TEXT NOT NULL, titul_id INTEGER NOT NULL, nazev TEXT NOT NULL DEFAULT '', tmdb_id TEXT NOT NULL DEFAULT '',
    tvdb_id TEXT NOT NULL DEFAULT '', imdb_id TEXT NOT NULL DEFAULT '', jellyfin_id TEXT NOT NULL DEFAULT '',
    {added}synchronizovano TEXT, PRIMARY KEY (app, titul_id))"""
NEW = ("sonarr_event", "sonarr_title", "radarr_event", "radarr_title")
OLD = ("arr_event", "arr_titul")


def renamed(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """A row of the old shared tables as the new table holds it: today's column names (schema_names)."""
    names = next((t.columns for t in schema_names.TABLES if t.new == table), {})
    return {names.get(k, k): v for k, v in row.items()}


def as_old(row: dict[str, Any]) -> dict[str, Any]:
    """A row today's code makes, under the column names of the old shared table."""
    back = {new: old for t in schema_names.TABLES if t.new in ("sonarr_event", "radarr_event") for old, new in t.columns.items()}
    return {back.get(k, k): v for k, v in row.items()}


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def columns(table: str) -> set[str]:
    with db.connect() as conn:
        return set(conn.table_columns(table))


def count(table: str) -> int:
    return int(db.query_value(f"SELECT COUNT(*) FROM {table}") or 0)


def run(*statements: str) -> None:
    with db.connect() as conn:
        for statement in statements:
            conn.execute(statement)
        conn.commit()


def insert(table: str, rows: list[dict[str, Any]]) -> None:
    with db.connect() as conn:
        for row in rows:
            conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         tuple(row.values()))
        conn.commit()


def clear() -> None:
    """Empty new tables, no old ones - the starting state for the next case."""
    with db.connect() as conn:
        for table in OLD:
            if conn.table_columns(table):
                conn.execute(f"DROP TABLE {table}")
        for table in NEW:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


def create_old(events: list[dict[str, Any]] | None, titles: list[dict[str, Any]] | None, with_added: bool = True) -> None:
    """The old tables with rows; None = the table is not created at all."""
    if events is not None:
        run(OLD_EVENTS_DDL, OLD_INDEX)
        insert("arr_event", events)
    if titles is not None:
        run(OLD_TITLES_DDL.format(added="pridano TEXT, " if with_added else ""))
        insert("arr_titul", [{k: v for k, v in title.items() if with_added or k != "pridano"} for title in titles])


# Rows the way the old code stored them: the history from the recorded responses, with the column `app`.
def old_events(app: str) -> list[dict[str, Any]]:
    package, recordings = {"sonarr": (sonarr, SONARR_RESPONSES), "radarr": (radarr, RADARR_RESPONSES)}[app]
    rows = [as_old(package.event_from_history(record)) for record in recorded_response(recordings, "history_since")]
    return [{"app": app, "epizoda": "", **r} for r in rows if r["arr_id"] is not None and r["datum"]]


EVENTS = old_events("sonarr") + old_events("radarr")
TITLES = [
    {"app": "sonarr", "titul_id": 1, "nazev": "Game of Thrones", "tmdb_id": "1399", "tvdb_id": "121361", "imdb_id": "tt0944947",
     "jellyfin_id": "abc123", "pridano": "2026-11-01 10:00:00", "synchronizovano": "2026-11-14 08:00:00"},
    {"app": "sonarr", "titul_id": 2, "nazev": "Chernobyl", "tmdb_id": "87108", "tvdb_id": "360893", "imdb_id": "",
     "jellyfin_id": "", "pridano": None, "synchronizovano": "2026-11-14 08:00:00"},
    {"app": "radarr", "titul_id": 1, "nazev": "Sintel", "tmdb_id": "45745", "tvdb_id": "", "imdb_id": "tt1727587",
     "jellyfin_id": "", "pridano": "2026-11-02 11:00:00", "synchronizovano": "2026-11-14 08:00:00"},
]
SONARR_EVENTS = sum(e["app"] == "sonarr" for e in EVENTS)
RADARR_EVENTS = sum(e["app"] == "radarr" for e in EVENTS)

print("--- A) a new database ---")
db.init_db()
check(all(columns(t) for t in NEW) and not any(columns(t) for t in OLD),
      "a new database has the tables per app and not the old shared ones")
check("app" not in columns("sonarr_event") | columns("radarr_event") | columns("sonarr_title") | columns("radarr_title"),
      "the column `app` is not in them - the table is the app itself")
check("episode" in columns("sonarr_event") and "episode" not in columns("radarr_event"), "only Sonarr has the episode")
check({"tvdb_id", "jellyfin_id"} <= columns("sonarr_title") and not {"tvdb_id", "jellyfin_id"} & columns("radarr_title"),
      "only Sonarr has the TVDB id and the Jellyfin series id (Radarr never filled them)")
check(all(t in db.TABLES for t in NEW) and not any(t in db.TABLES for t in OLD),
      "the list of tables for the backup and the database conversion knows the new tables, not the old ones")

print()
print("--- B) an old database is converted at start ---")
check(SONARR_EVENTS > 5 and RADARR_EVENTS > 5, f"there is something to convert ({SONARR_EVENTS} + {RADARR_EVENTS} events)")
create_old(EVENTS, TITLES)
db.set_setting("arr_sonarr_synchronizace", "2026-11-14 08:00:00")
db.set_setting("arr_radarr_synchronizace", "2026-11-14 09:00:00")
db.set_setting("arr_sonarr_tituly", "2026-11-14 08:00:05")
db.forget_settings()
db.init_db()
check(not any(columns(t) for t in OLD), "the old tables are gone")
check(count("sonarr_event") == SONARR_EVENTS and count("radarr_event") == RADARR_EVENTS,
      f"the events are with their app ({count('sonarr_event')}, {count('radarr_event')})")
original = next(e for e in EVENTS if e["app"] == "sonarr" and e["typ"] == "grabbed")
converted = db.query_one("SELECT * FROM sonarr_event WHERE arr_id = ?", (original["arr_id"],))
check(converted == renamed("sonarr_event", {k: v for k, v in original.items() if k != "app"}),
      "a Sonarr row was converted whole into the new columns, only without the column `app`")
movie = next(e for e in EVENTS if e["app"] == "radarr")
converted_movie = db.query_one("SELECT * FROM radarr_event WHERE arr_id = ?", (movie["arr_id"],))
check(converted_movie == renamed("radarr_event", {k: v for k, v in movie.items() if k not in ("app", "epizoda")}),
      "a Radarr row too - without `app` and without the episode")
check(db.query_one("SELECT * FROM sonarr_title WHERE title_id = 1") == renamed("sonarr_title", {k: v for k, v in TITLES[0].items() if k != "app"})
      and count("sonarr_title") == 2, "series with the pairing to Jellyfin and the time added")
check(db.query_one("SELECT * FROM radarr_title WHERE title_id = 1")
      == renamed("radarr_title", {k: v for k, v in TITLES[2].items() if k not in ("app", "tvdb_id", "jellyfin_id")})
      and count("radarr_title") == 1,
      "a movie without the columns Radarr does not have")
check(db.get_setting("arr_sonarr_last_sync") == "2026-11-14 08:00:00" and db.get_setting("arr_radarr_last_sync") == "2026-11-14 09:00:00"
      and db.get_setting("arr_sonarr_titles") == "2026-11-14 08:00:05", "the sync marks stayed as they were, under their new keys")
check(sonarr.event_count() == SONARR_EVENTS and radarr.event_count() == RADARR_EVENTS, "the apps' packages see their data")

print()
print("--- C) a second start changes nothing, the sync downloads nothing twice ---")
before = {t: db.query_all(f"SELECT * FROM {t} ORDER BY 1") for t in NEW}
db.init_db()
check({t: db.query_all(f"SELECT * FROM {t} ORDER BY 1") for t in NEW} == before and not any(columns(t) for t in OLD),
      "a second start leaves everything as it is")
no_network()
substitute(sonarr, "SonarrClient", sonarr_client(sonarr_handler()))
substitute(radarr, "RadarrClient", radarr_client(radarr_handler()))
sonarr.save_settings(True, "http://sonarr.test", "secret-key")
radarr.save_settings(True, "http://radarr.test", "secret-key")
db.forget_settings()
results = (asyncio.run(sonarr.synchronize()), asyncio.run(radarr.synchronize()))
check([v["new_count"] for v in results] == [0, 0] and count("sonarr_event") == SONARR_EVENTS and count("radarr_event") == RADARR_EVENTS,
      f"the next sync after the conversion adds nothing ({results})")

print()
print("--- D) unusual states ---")
clear()
create_old(EVENTS, None)
db.init_db()
check(count("sonarr_event") == SONARR_EVENTS and count("sonarr_title") == 0 and not columns("arr_event"),
      "only the old events table, without the titles table: it is converted")
clear()
create_old(None, TITLES, with_added=False)
db.init_db()
check(count("sonarr_title") == 2 and count("radarr_title") == 1 and not columns("arr_titul")
      and db.query_value("SELECT COUNT(*) FROM sonarr_title WHERE added_at IS NOT NULL") == 0
      and db.query_value("SELECT jellyfin_id FROM sonarr_title WHERE title_id = 1") == "abc123",
      "only a titles table from the time before the column `pridano`: it is converted, the time added stays unknown")
# The new tables have a part of the rows already (a database joined by hand): nothing doubles, nothing is missing,
# what is in the new one already stands.
clear()
already_there = [renamed("sonarr_event", {k: v for k, v in e.items() if k != "app"}) for e in EVENTS if e["app"] == "sonarr"][:3]
insert("sonarr_event", [{**e, "title": "Už převedený"} for e in already_there])
create_old(EVENTS, TITLES)
db.init_db()
check(count("sonarr_event") == SONARR_EVENTS and count("radarr_event") == RADARR_EVENTS
      and db.query_value("SELECT COUNT(*) FROM sonarr_event WHERE title = 'Už převedený'") == 3 and not columns("arr_event"),
      "new tables with a part of the rows already: nothing twice, nothing missing, a row the new one has already is not overwritten")
# ... and the conversion tells the truth about how many rows it really converted: three were in the new table
# already, those do not count as converted and the log mentions them separately (it used to report the row count
# of the old table, however many got inserted).
import logging  # noqa: E402


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


clear()
insert("sonarr_event", [{**e, "title": "Už převedený"} for e in already_there])
create_old(EVENTS, TITLES)
capture = Capture()
log_db = logging.getLogger("jellyscope.db")
level = log_db.level
log_db.setLevel(logging.INFO)       # the conversion's messages are at the info level
log_db.addHandler(capture)
with db.connect() as conn:
    converted_count = db._convert_arr_tables(conn)
    conn.commit()
log_db.removeHandler(capture)
log_db.setLevel(level)
check(converted_count == len(EVENTS) + len(TITLES) - 3,
      f"the conversion returns the number of rows it really inserted ({converted_count}, in the old tables {len(EVENTS) + len(TITLES)}, 3 were in the new one already)")
reported = [rec.args for rec in capture.records if "tables converted to per-app" in str(rec.msg)]
already_reported = [rec.args for rec in capture.records if "were already in the new tables" in str(rec.msg)]
check(reported == [(len(EVENTS) + len(TITLES) - 3,)] and already_reported == [("arr_event", 3)],
      f"log: as many converted as were inserted, and separately how many rows the new table had already ({reported}, {already_reported})")
# Everything is in the new tables already (the conversion repeats over the same data): nothing is inserted, the
# old table is deleted all the same - and the log says so anyway, with a zero. Deleting a table must not happen
# without a word.
create_old(EVENTS, None)
capture = Capture()
log_db.setLevel(logging.INFO)
log_db.addHandler(capture)
with db.connect() as conn:
    second_time = db._convert_arr_tables(conn)
    conn.commit()
log_db.removeHandler(capture)
log_db.setLevel(level)
check(second_time == 0 and not columns("arr_event") and [rec.args for rec in capture.records if "tables converted to per-app" in str(rec.msg)] == [(0,)]
      and [rec.args for rec in capture.records if "were already in the new tables" in str(rec.msg)] == [("arr_event", len(EVENTS))],
      f"every row in the new tables already: nothing is inserted, the old table disappears and the log says it ({[(str(rec.msg)[:30], rec.args) for rec in capture.records]})")
# A row of an app Jellyscope does not know has nowhere to go: it is not converted, and the old table is deleted all the same.
clear()
create_old(EVENTS + [{**EVENTS[0], "app": "lidarr", "arr_id": 999001}], None)
db.init_db()
check(count("sonarr_event") == SONARR_EVENTS and count("radarr_event") == RADARR_EVENTS and not columns("arr_event")
      and db.query_value("SELECT COUNT(*) FROM sonarr_event WHERE arr_id = 999001") == 0,
      "a row of an unknown app is not converted, the others are and the old table is gone")

print()
print("--- E) an error in the middle of the conversion ---")
clear()
create_old(EVENTS, TITLES)
real = db._convert_table


def fails_on_titles(conn: Any, old: str, targets: Any) -> int:
    if old == "arr_titul":
        raise RuntimeError("a failure in the middle of the conversion")
    return real(conn, old, targets)


db._convert_table = fails_on_titles
try:
    db.init_db()
    failed = ""
except RuntimeError as error:
    failed = str(error)
finally:
    db._convert_table = real
check(failed == "a failure in the middle of the conversion" and count("arr_event") == len(EVENTS) and count("arr_titul") == len(TITLES)
      and count("sonarr_event") == 0 and count("radarr_event") == 0,
      f"after the error everything stayed as before: the old tables full, the new ones empty ({failed or 'no error happened'})")
db.init_db()
check(count("sonarr_event") == SONARR_EVENTS and count("radarr_title") == 1 and not any(columns(t) for t in OLD),
      "and the next start converts everything")
clear()

print()
print("--- F) PostgreSQL: a table of the same name in another schema ---")
# One PostgreSQL database can have more schemas (say a second instance or a backup of tables put aside by hand).
# A table of the same name in another schema is not ours: the start does not convert it, does not delete it and
# is not confused by its columns.
with db.connect() as conn:
    is_postgres = conn.is_postgres
if not is_postgres:
    print("skipped - SQLite has no schemas (run it through PostgreSQL)")
else:
    run("DROP SCHEMA IF EXISTS other_schema CASCADE", "CREATE SCHEMA other_schema",
        "CREATE TABLE other_schema.arr_event (app TEXT, foreign_column INTEGER)",
        "INSERT INTO other_schema.arr_event VALUES ('sonarr', 1)",
        "CREATE TABLE other_schema.only_elsewhere (x INTEGER)",
        "CREATE TABLE other_schema.settings (key TEXT, value TEXT, extra INTEGER)")
    with db.connect() as conn:
        foreign = (conn.table_columns("only_elsewhere"), conn.table_columns("arr_event"), db.columns_of(conn, "only_elsewhere"))
        ours = (db.columns_of(conn, "settings"), conn.table_columns("settings"), db._column_types(conn, "settings"))
    check(foreign == (set(), set(), []), f"a table that is only in another schema does not exist for us ({foreign})")
    check("extra" not in ours[0] and len(ours[0]) == len(set(ours[0])) and "extra" not in ours[1] and "extra" not in ours[2],
          f"the columns of our table do not mix with a table of the same name elsewhere ({ours[0]})")
    try:
        db.init_db()
        start = "ok"
    except Exception as error:  # noqa: BLE001 - exactly this is guarded
        start = repr(error)[:160]
    check(start == "ok" and db.query_value("SELECT COUNT(*) FROM other_schema.arr_event") == 1 and count("sonarr_event") == 0,
          f"the start neither converts nor deletes a foreign `arr_event` and does not fail ({start})")
    run("DROP SCHEMA other_schema CASCADE")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
