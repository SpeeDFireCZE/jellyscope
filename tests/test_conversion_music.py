# -*- coding: utf-8 -*-
r"""The music group converted to English names end to end (step 1 of the database spec).

The old shape - the tables hudba_interpret/album/skladba with Czech columns and indexes, the settings
task_hudba_enabled, ui_max_rows_hudba, hudba_stav, a task log of the kind "hudba", a dashboard with the
card "hudba_alba" - is made from today's schema by the map turned around (jellyscope/schema_names.py,
new → old). init_db then converts it: the same rows under the new names, the card renamed, an unknown
key left alone, the music pages and the dashboard work.

Run:
    .venv/bin/python tests/test_conversion_music.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_music.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-music.db")
os.environ["SECRET_KEY"] = "conversion-music-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, cards, conversion, db, schema_names, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def run(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def tables() -> set[str]:
    with db.connect() as conn:
        return conversion._tables(conn)


db.init_db()
db.set_setting("backup_path", str(Path(_tmp) / "backups"))

print("--- the old shape, made by the map turned around ---")
music = [t for t in schema_names.TABLES if t.new.startswith("music_")]
check({t.new for t in music} == {"music_artist", "music_album", "music_track"}, f"the map has the music tables ({[t.new for t in music]})")
with db.connect() as conn:
    for table in music:
        for old_index, new_index in table.indexes.items():
            conn.execute(f"DROP INDEX IF EXISTS {new_index}")
        conn.execute(f"ALTER TABLE {table.new} RENAME TO {table.old}")
        for old, new in table.columns.items():
            if new in conn.table_columns(table.old):
                conn.execute(f"ALTER TABLE {table.old} RENAME COLUMN {new} TO {old}")
    conn.execute("CREATE INDEX idx_hudba_skladba_album ON hudba_skladba (album_id)")
    conn.execute("INSERT INTO hudba_interpret (id, nazev, zanry, obrazek_tag, synchronizovano, chybi)"
                 " VALUES ('ar1', 'The Beatles', 'Rock', 'tag1', '2026-11-01 10:00:00', 0)")
    conn.execute("INSERT INTO hudba_album (id, nazev, interpret_id, interpret, rok, obal_tag, pridano, knihovna_id, synchronizovano, chybi)"
                 " VALUES ('al1', 'Abbey Road', 'ar1', 'The Beatles', 1969, 'c1', '2026-11-01 10:00:00', 'lib-m', '2026-11-01 10:00:00', 0)")
    conn.execute("INSERT INTO hudba_skladba (id, nazev, album_id, album, interpret_id, interpret, interpreti, cislo, disk,"
                 " delka_ticks, rok, kontejner, kodek, bitrate, vzorkovani, hloubka, kanaly, velikost, cesta, pridano, knihovna_id,"
                 " synchronizovano, chybi) VALUES ('t1', 'Come Together', 'al1', 'Abbey Road', 'ar1', 'The Beatles', 'The Beatles',"
                 " 1, 1, 2590000000, 1969, 'flac', 'flac', 900000, 44100, 16, 2, 30000000, '/music/abbey/01.flac',"
                 " '2026-11-01 10:00:00', 'lib-m', '2026-11-01 10:00:00', 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib-m', 'Hudba', 'music')")
    for new, old in ((n, o) for o, n in schema_names.SETTING_KEYS.items() if n.startswith(("task_music", "music_", "ui_max_rows_music"))):
        conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (new, old))
    conn.execute("INSERT INTO settings (key, value) VALUES ('task_hudba_enabled', '0'), ('ui_max_rows_hudba', '7'),"
                 " ('hudba_stav', ?), ('hudba_sync_minutes', '45')", (json.dumps({"vysledek": "ok", "novych": 3}),))
    conn.execute("DELETE FROM scan_log")
    conn.execute("INSERT INTO scan_log (kind, started_at, status, message) VALUES ('hudba', '2026-11-21 10:00:00', 'done', 'x')")
    conn.execute("DELETE FROM dashboard_layout")
    for old, new in next(t for t in schema_names.TABLES if t.new == "dashboard_layout").columns.items():
        conn.execute(f"ALTER TABLE dashboard_layout RENAME COLUMN {new} TO {old}")   # the layout of before step 4
    conn.execute("INSERT INTO dashboard_layout (account_id, sekce, poradi, sirka) VALUES (NULL, 'hudba_alba', 1, 'pul'),"
                 " (NULL, 'neznama_karta', 2, NULL)")
check({"hudba_interpret", "hudba_album", "hudba_skladba"} <= tables(), "the old tables are there")
config = db.database_config()
if not config.is_postgres:                 # a copy of the old shape for the start by run.py below
    import shutil
    import sqlite3
    old_copy = Path(_tmp) / "old-shape-copy.db"
    db.close_pool()
    shutil.copyfile(config.path, old_copy)
    with sqlite3.connect(old_copy) as copy:
        copy.execute("UPDATE settings SET value = ? WHERE key = 'backup_path'", (str(Path(_tmp) / "run-backups"),))

print()
print("--- the start converts it ---")
db.init_db()
db.forget_settings()
check({"music_artist", "music_album", "music_track"} <= tables() and not ({"hudba_interpret", "hudba_album", "hudba_skladba"} & tables()),
      "the music tables have their new names")
track = dict(db.query_one("SELECT * FROM music_track WHERE id = 't1'") or {})
check(track.get("name") == "Come Together" and track.get("artist_id") == "ar1" and track.get("track_number") == 1
      and track.get("runtime_ticks") == 2590000000 and track.get("sample_rate") == 44100 and track.get("bit_depth") == 16
      and track.get("channels") == 2 and track.get("size_bytes") == 30000000 and track.get("path") == "/music/abbey/01.flac"
      and track.get("library_id") == "lib-m" and track.get("missing") == 0, f"a track under its new column names ({track})")
album = dict(db.query_one("SELECT name, artist, year, cover_tag, added_at FROM music_album WHERE id = 'al1'") or {})
check(album == {"name": "Abbey Road", "artist": "The Beatles", "year": 1969, "cover_tag": "c1", "added_at": "2026-11-01 10:00:00"},
      f"an album too ({album})")
check(dict(db.query_one("SELECT name, genres, image_tag FROM music_artist WHERE id = 'ar1'") or {})
      == {"name": "The Beatles", "genres": "Rock", "image_tag": "tag1"}, "and an artist")
check(db.get_setting("task_music_enabled") == "0" and db.get_setting("ui_max_rows_music") == "7"
      and db.get_setting("music_sync_minutes") == "45" and json.loads(db.get_setting("music_state")) == {"result": "ok", "new_count": 3},
      "the settings under their new keys, the state's JSON too")
check(all(db.query_one("SELECT 1 AS x FROM settings WHERE key = ?", (k,)) is None
          for k in ("task_hudba_enabled", "ui_max_rows_hudba", "hudba_stav", "hudba_sync_minutes")), "no old key left")
check([dict(r)["kind"] for r in db.query_all("SELECT kind FROM scan_log")] == ["music"], "the task log's kind")
layout = [dict(r)["card_key"] for r in db.query_all("SELECT card_key FROM dashboard_layout ORDER BY position")]
check(layout == ["music_albums", "neznama_karta"], f"the music card renamed, the unknown key left alone ({layout})")
check([card.key for card in cards.load_layout()] == ["music_albums"], "the dashboard for everyone still shows the card")
backups = sorted((Path(_tmp) / "backups").glob("*before-conversion*"))
check(len(backups) == 1, "with a backup before")

print()
print("--- the music pages ---")
accounts.create("music_admin", "conversion-music-pw", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "music_admin", "password": "conversion-music-pw", "method": "local"})
for address, needle in (("/music/overview", "Abbey Road"), ("/album/al1", "Come Together"), ("/music/artist/ar1", "The Beatles")):
    answer = client.get(address)
    check(answer.status_code == 200 and needle in answer.text, f"{address} answers with the data ({answer.status_code})")
check(conversion.convert() is None, "a second start does nothing")

print()
print("--- started by run.py, the log file tells of the conversion ---")
# run.py prepares the database before the web starts; the conversion happens there. Its summary is what the
# README and update.sh send a person to the log for - it must not be lost before the log file is set up.
if config.is_postgres:
    print("SKIP   on PostgreSQL (the order of the start does not depend on the database)")
else:
    import subprocess
    run_home = Path(tempfile.mkdtemp(prefix="run-py-"))
    (run_home / "data").mkdir()
    env = {**os.environ, "JELLYSCOPE_HOME": str(run_home), "DATABASE_PATH": str(old_copy)}
    started = subprocess.run([sys.executable, "-c", "import runpy, sys, uvicorn\n"
                              "uvicorn.run = lambda *a, **k: None\n"
                              "sys.argv = ['run.py']\n"
                              "runpy.run_path('run.py', run_name='__main__')"],
                             cwd=PROJECT, env=env, capture_output=True, text=True, timeout=600)
    log_file = run_home / "data" / "logs" / "jellyscope.log"
    text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    check(started.returncode == 0 and "database converted to English names" in text,
          f"the log file has the summary ({started.returncode}, {len(text)} chars) {started.stderr[-300:]}")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
