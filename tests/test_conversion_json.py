# -*- coding: utf-8 -*-
r"""The conversion to English names inside JSON (step 5 of the database spec) and the orphaned api_pouziti.

Some cells hold JSON whose keys and values are Czech too - the permissions of accounts and API keys, the
states of syncs, the disks, the tiles of the map. The map names them (schema_names.JSON_KEYS: the keys of
objects at any depth, JSON_VALUES: the values under a key, items of a list too); the conversion rewrites
them in the same transaction and checks them like every other value. What is no JSON stays as it is; a
key the map does not know stays; an object with the old and the new key already stays untouched (no
value is lost). The orphaned table api_pouziti (read by nothing) is dropped; its rows are in the backup.

The map here is the test's own (the real one fills group by group).

Run:
    .venv/bin/python tests/test_conversion_json.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_json.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-json.db")
os.environ["SECRET_KEY"] = "conversion-json-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import conversion, db, schema_names  # noqa: E402

config = db.database_config()
failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def run(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)
        conn.commit()


def tables() -> set[str]:
    with db.connect() as conn:
        return conversion._tables(conn)


def setting(key: str) -> str | None:
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return None if row is None else dict(row)["value"]


# The map of this test.
schema_names.TABLES = (schema_names.TableRename("json_old", "json_new", columns={"obsah": "content"}),)
schema_names.SETTING_KEYS = {"stary_stav": "new_state", "stary_zdroj": "new_source"}
# A plain value of one setting (no JSON): only that setting's value is mapped, the same word elsewhere stays.
schema_names.SETTING_VALUES = {"new_source": {"rucne": "manual"}}
schema_names.VALUES = {}
schema_names.JSON_KEYS = {
    ("settings", "new_state"): {"kdy": "at", "vysledek": "result", "hodnoty": "values"},
    ("json_new", "content"): {"oblasti": "areas", "anonymizace": "anonymise", "vnoreny": "nested"},
}
schema_names.JSON_VALUES = {
    (("settings", "new_state"), "result"): {"chyba": "error"},
    (("json_new", "content"), "areas"): {"prehled": "overview", "sit": "network"},
}
db.init_db()
db.set_setting("backup_path", str(Path(_tmp) / "backups"))

print("--- the old shape ---")
run("CREATE TABLE json_old (id INTEGER PRIMARY KEY, obsah TEXT)")
CELLS = {
    1: '{"oblasti": ["prehled", "sit", "jine"], "anonymizace": true}',
    2: '{"vnoreny": {"oblasti": ["sit"]}, "nezname": 1}',
    3: "not json at all",
    4: None,
    5: '{"areas": ["overview"], "oblasti": ["sit"]}',
    6: '{"oblasti":["prehled"]}',
    7: '{"oblasti": ["prehled"], "jméno": "Petr Č."}',
    8: '["prehled", "sit"]',
}
for number, text in CELLS.items():
    run("INSERT INTO json_old (id, obsah) VALUES (?, ?)", (number, text))
STATE = {"kdy": "2026-11-21 10:00:00", "vysledek": "chyba", "hodnoty": {"error": "timeout"}, "novych": 3}
run("DELETE FROM settings WHERE key IN ('stary_stav', 'new_state')")
run("INSERT INTO settings (key, value) VALUES ('stary_stav', ?)", (json.dumps(STATE),))
run("DELETE FROM settings WHERE key IN ('stary_zdroj', 'new_source', 'jiny_klic')")
run("INSERT INTO settings (key, value) VALUES ('stary_zdroj', 'rucne'), ('jiny_klic', 'rucne')")
# As the schema of 1.8.x before the API log became a file created it: AUTOINCREMENT (SQLite keeps a row of it in
# sqlite_sequence, which goes with the table) / BIGSERIAL (PostgreSQL: its own sequence).
run("CREATE TABLE api_pouziti (id BIGSERIAL PRIMARY KEY, token_id BIGINT, cesta TEXT)" if config.is_postgres
    else "CREATE TABLE api_pouziti (id INTEGER PRIMARY KEY AUTOINCREMENT, token_id INTEGER, cesta TEXT)")
run("INSERT INTO api_pouziti (token_id, cesta) VALUES (7, '/api/v1/library'), (7, '/api/v1/users')")
check({"json_old", "api_pouziti"} <= tables(), "the old table, the orphaned one and the state")
if not config.is_postgres:
    # An admin (or a database tool) ran ANALYZE: sqlite_stat1 has a row of every table and index, and a dropped table
    # or index takes its rows along - like sqlite_sequence, no table of the application's.
    run("ANALYZE")

import logging  # noqa: E402


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.getMessage()}")


captured = _Lines()
logging.getLogger("jellyscope").addHandler(captured)
saved_level = logging.getLogger("jellyscope").level
logging.getLogger("jellyscope").setLevel(logging.DEBUG)
try:
    db.init_db()
finally:
    logging.getLogger("jellyscope").removeHandler(captured)
    logging.getLogger("jellyscope").setLevel(saved_level)
db.forget_settings()
print("--- converted ---")
unreadable = [line for line in captured.lines if line.startswith("WARNING") and "json_new.content" in line]
check(len(unreadable) == 1 and " 1 " in unreadable[0], f"the cell that is no JSON is in the log, by count ({unreadable})")
summary_line = next((line for line in captured.lines if "converted to English names" in line), "")
check("6 JSON cells" in summary_line and "1 tables dropped" in summary_line,
      f"the summary counts the JSON cells rewritten and the dropped table ({summary_line})")
check("json_new" in tables() and "json_old" not in tables() and "api_pouziti" not in tables(),
      "the table renamed, the orphaned api_pouziti gone")
cells = {dict(r)["id"]: dict(r)["content"] for r in db.query_all("SELECT id, content FROM json_new")}
check(json.loads(cells[1]) == {"areas": ["overview", "network", "jine"], "anonymise": True},
      f"keys renamed, the values of a list mapped, an unknown value kept ({cells[1]})")
check(json.loads(cells[2]) == {"nested": {"areas": ["network"]}, "nezname": 1},
      f"at any depth; a key the map does not know kept ({cells[2]})")
check(cells[3] == CELLS[3] and cells[4] is None, "no JSON and NULL stay as they are")
check(cells[5] == CELLS[5], f"an object with the old and the new key stays untouched ({cells[5]})")
check(cells[6] == '{"areas":["overview"]}', f"a compact text stays compact ({cells[6]})")
check(json.loads(cells[7]) == {"areas": ["overview"], "jméno": "Petr Č."} and "jméno" in cells[7],
      f"letters with diacritics stay as letters ({cells[7]})")
check(cells[8] == CELLS[8], "values not under a key of the map stay")
state = json.loads(setting("new_state") or "{}")
check(state == {"at": "2026-11-21 10:00:00", "result": "error", "values": {"error": "timeout"}, "novych": 3}
      and setting("stary_stav") is None, f"the setting moved and its JSON converted ({state})")
check(setting("new_source") == "manual" and setting("stary_zdroj") is None and setting("jiny_klic") == "rucne",
      "a plain value mapped only in its own setting (SETTING_VALUES), the same word elsewhere kept")

backups = sorted((Path(_tmp) / "backups").glob("*before-conversion*"))
if config.is_postgres:
    kept = bool(backups) and "api_pouziti" in backups[-1].read_text(encoding="utf-8")
else:
    with sqlite3.connect(backups[-1]) as raw:
        kept = raw.execute("SELECT COUNT(*) FROM api_pouziti").fetchone()[0] == 2
check(len(backups) == 1 and kept, f"the backup before the conversion has the rows of api_pouziti ({[b.name for b in backups]})")

with db.connect() as conn:
    again = conversion.plan(conn)
check(not again, f"a second start has nothing to do ({again})")

print("--- many JSON cells: rewritten row by row, not text by text over the whole table ---")
import time  # noqa: E402

TILES = 4000
run("CREATE TABLE json_tiles (detail_level INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, content TEXT NOT NULL,"
    " PRIMARY KEY (detail_level, x, y))")
rows = [(1, n % 100, n // 100, json.dumps({"vnoreny": {"oblasti": ["prehled"]}, "path": f"M{n} " + "L1 2 " * 1150}))
        for n in range(TILES)]
with db.connect() as conn:
    conn.executemany("INSERT INTO json_tiles (detail_level, x, y, content) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
schema_names.JSON_KEYS = {**schema_names.JSON_KEYS, ("json_tiles", "content"): {"vnoreny": "nested", "oblasti": "areas"}}
schema_names.JSON_VALUES = {**schema_names.JSON_VALUES, (("json_tiles", "content"), "areas"): {"prehled": "overview"}}
started = time.monotonic()
db.init_db()
took = time.monotonic() - started
converted = [json.loads(dict(r)["content"])["nested"] for r in db.query_all("SELECT content FROM json_tiles")]
check(len(converted) == TILES and all(c == {"areas": ["overview"]} for c in converted), f"all {TILES} cells converted")
check(took < 10, f"{TILES} cells of about 6 KB in {took:.1f} s (one statement per row by its key; text by text it was minutes)")

print("--- a setting under both keys (a manual restore into a live database): the old value through the map first ---")
schema_names.SETTING_KEYS = {**schema_names.SETTING_KEYS, "ui_map_podklad": "ui_map_base", "stary_stav2": "new_state2"}
schema_names.SETTING_VALUES = {**schema_names.SETTING_VALUES, "ui_map_base": {"kresleny": "drawn"}}
schema_names.JSON_KEYS = {**schema_names.JSON_KEYS, ("settings", "new_state2"): {"kdy": "at", "vysledek": "result"}}
schema_names.JSON_VALUES = {**schema_names.JSON_VALUES, (("settings", "new_state2"), "result"): {"chyba": "error"}}
run("DELETE FROM settings WHERE key IN ('ui_map_podklad', 'ui_map_base', 'stary_stav2', 'new_state2')")
run("INSERT INTO settings (key, value) VALUES ('ui_map_podklad', 'kresleny'), ('ui_map_base', 'osm'),"
    " ('stary_stav2', ?), ('new_state2', ?)",
    (json.dumps({"kdy": "2026-11-21 10:00:00", "vysledek": "chyba"}), '{"at":"2026-11-21 10:00:00","result":"error"}'))
try:
    db.init_db()
    refused_both = None
except db.ConversionFailed as error:
    refused_both = error
db.forget_settings()
check(refused_both is None and setting("ui_map_base") == "osm" and setting("ui_map_podklad") is None,
      f"the old key holding the old default (kresleny = drawn) gives way to the chosen new value ({refused_both})")
check(setting("new_state2") == '{"at":"2026-11-21 10:00:00","result":"error"}' and setting("stary_stav2") is None,
      "the old JSON that converts to the new one (another formatting) is the same value: the old key goes")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
