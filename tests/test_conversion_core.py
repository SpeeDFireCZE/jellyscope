# -*- coding: utf-8 -*-
r"""The core tables converted to English names end to end (step 4 of the database spec).

The state is written by the application itself (accounts, own permissions, layouts, API keys,
map tiles, ...), what the application shows of it is noted, the tables are turned into the old
shape by the map turned around (jellyscope/schema_names.py, new → old: tables, columns, indexes,
on PostgreSQL the primary keys) and init_db converts them. Afterwards the rows are the same, the
application shows the same, and no old name is left.

Run:
    .venv/bin/python tests/test_conversion_core.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_conversion_core.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "conversion-core.db")
os.environ["SECRET_KEY"] = "conversion-core-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, cards, conversion, db, detailed_map, schema_names, seasonal, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def tables() -> set[str]:
    with db.connect() as conn:
        return conversion._tables(conn)


def columns(table: str) -> set[str]:
    with db.connect() as conn:
        return conn.table_columns(table)


def index_names() -> set[str]:
    """Indexes, on PostgreSQL with the primary keys."""
    with db.connect() as conn:
        if conn.is_postgres:
            rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()").fetchall()
        else:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    return {next(iter(r.values())) if isinstance(r, dict) else r[0] for r in rows}


def has_primary_key(table: str) -> bool:
    """On PostgreSQL: the table has a primary key (its name follows the table's). SQLite names none."""
    with db.connect() as conn:
        if not conn.is_postgres:
            return False
        return conn.execute("SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid"
                            " WHERE t.relname = ? AND c.contype = 'p'", (table,)).fetchone() is not None


def counts(names: tuple[str, ...]) -> dict[str, int | None]:
    """Rows of the tables (None = the table is not there)."""
    present = tables()
    return {name: int(db.query_value(f"SELECT COUNT(*) FROM {name}", default=0)) if name in present else None
            for name in names}


def renames(new_names: tuple[str, ...]) -> list[schema_names.TableRename]:
    return [t for t in schema_names.TABLES if t.new in new_names]


def old_tables(new_names: tuple[str, ...]) -> None:
    """The tables as a database before the conversion has them: today's tables, their columns, indexes and
    (on PostgreSQL) primary keys renamed back by the map. A database before the conversion has no lock view."""
    with db.connect() as conn:
        conn.execute(f"DROP VIEW IF EXISTS {getattr(schema_names, 'LOCK_VIEW', 'playback_kos')}")
        for table in renames(new_names):
            if table.old != table.new:
                conn.execute(f"ALTER TABLE {table.new} RENAME TO {table.old}")
                if conn.is_postgres:
                    conn.execute(f"ALTER INDEX IF EXISTS {table.new}_pkey RENAME TO {table.old}_pkey")
            for old, new in table.columns.items():
                if new in conn.table_columns(table.old):
                    conn.execute(f"ALTER TABLE {table.old} RENAME COLUMN {new} TO {old}")
            for old_index, new_index in table.indexes.items():
                if conn.is_postgres:
                    conn.execute(f"ALTER INDEX IF EXISTS {new_index} RENAME TO {old_index}")
                    continue
                # SQLite cannot rename an index: it is made again under the old name (over the old table and columns).
                found = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (new_index,)).fetchone()
                if found:
                    conn.execute(f"DROP INDEX {new_index}")
                    conn.execute(found[0].replace(new_index, old_index, 1))


def check_shape(new_names: tuple[str, ...], converted: bool) -> None:
    """The old shape has the old names (tables, columns, indexes), the converted database the new ones and no old."""
    have_tables, have_indexes = tables(), index_names()
    wrong = []
    for table in renames(new_names):
        name, other = (table.new, table.old) if converted else (table.old, table.new)
        if name not in have_tables or (other != name and other in have_tables):
            wrong.append(f"table {name}")
            continue
        present = columns(name)
        wrong += [f"{name}.{col}" for old, new in table.columns.items()
                  for col, gone in [((new, old) if converted else (old, new))] if col not in present or gone in present]
        wrong += [f"index {idx}" for old, new in table.indexes.items()
                  for idx, gone in [((new, old) if converted else (old, new))] if idx not in have_indexes or gone in have_indexes]
        if table.old != table.new and has_primary_key(name):    # PostgreSQL names it after the table
            if f"{name}_pkey" not in have_indexes or f"{other}_pkey" in have_indexes:
                wrong.append(f"primary key {name}_pkey")
    check(bool(renames(new_names)) and not wrong,
          f"{'/'.join(new_names)}: the {'new' if converted else 'old'} names ({wrong[:6]})")


db.init_db()
admin_id = accounts.create("core_admin", "conversion-core-pw", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "core_admin", "password": "conversion-core-pw", "method": "local"})


print("--- dashboard layouts, accounts, API keys, map tiles (database step 4, task 1) ---")
TASK1 = ("dashboard_layout", "accounts", "api_tokens", "detailed_map_tile")
keys = [card.key for card in cards.ALL_CARDS]
widths = sorted(cards.WIDTHS)
cards.save_layout([(keys[0], ""), (keys[1], widths[0]), (keys[2], widths[-1])])
reader_id = accounts.create("core_reader", "conversion-core-pw")
cards.save_layout([(keys[3], widths[0]), (keys[0], "")], account_id=reader_id)
accounts.save_permissions(reader_id, access.pack_permissions(["dashboard", "seerr", "knihovna"], True))
accounts.save_may_have_key(reader_id, "1")
accounts.set_event_motion(reader_id, False)
full_key = api.create_token("full")
limited_key = api.create_token("limited", permissions=api.pack_key_permissions([sorted(api.key_areas())[0]], True, False))
disabled_key = api.create_token("disabled")
api.disable_token(disabled_key["id"])
own_key = api.create_token("own", account_id=reader_id)
detailed_map._save({(0, 0, 0): {"water": ["M0 0L9 9"]}, (2, 1, 3): {"roads": ["M1 1L2 2", "M3 3"]}})
PATHS = ("/api/v1/libraries", "/api/v1/top-items", "/api/v1/users")


def observe_task1() -> dict[str, Any]:
    reader = accounts.get(reader_id)
    tokens = sorted(tuple(sorted(str(v) for k, v in t.items() if "used" not in k)) for t in api.list_tokens())
    seen = {
        "shared": [(card.key, card.width) for card in cards.load_layout()],
        "own": [(card.key, card.width) for card in cards.load_layout(reader_id)],
        "reader's cards": [card.key for card in cards.allowed(cards.load_layout(), reader)],
        "areas": {area.key: access.account_sees(reader, area.key) for area in access.AREAS},
        "may have a key": access.may_have_key(reader),
        "motion": seasonal.motion(reader),
        "tokens": tokens,
        "own tokens": len(api.list_tokens(reader_id)),
        "tiles": (detailed_map.tile(0, 0, 0), detailed_map.tile(2, 1, 3), detailed_map.tile(2, 0, 0)),
    }
    for name, key in (("full", full_key), ("limited", limited_key), ("disabled", disabled_key), ("own", own_key)):
        seen[f"api {name}"] = [client.get(path, headers={"Authorization": f"Bearer {key['token']}"}).status_code for path in PATHS]
    page = client.get("/settings?section=api")
    seen["api page"] = (page.status_code, full_key["token"][:api.PREVIEW_CHARS] in page.text)
    return seen


before = observe_task1()
check(before["api full"][0] == 200 and before["api disabled"][0] in (401, 403) and before["tiles"][1]
      and before["may have a key"] is True and before["motion"] is False and before["api page"] == (200, True),
      f"the state is written and shows ({before['api full']}, {before['api disabled']})")
rows_before = counts(TASK1)
old_tables(TASK1)
check_shape(TASK1, converted=False)

db.init_db()
db.forget_settings()
check_shape(TASK1, converted=True)
check(None not in rows_before.values() and counts(TASK1) == rows_before, f"the same rows ({rows_before})")
after = observe_task1()
check(after == before, f"the application shows the same ({[k for k in before if before[k] != after.get(k)]})")
check(after["api disabled"] == before["api disabled"] and after["api limited"] == before["api limited"],
      "a disabled key stays disabled, a limited one limited")


print("--- library and disk snapshots (database step 4, task 2) ---")
from datetime import datetime, timedelta, timezone  # noqa: E402

from jellyscope import formatting, scanner, stats  # noqa: E402

TASK2 = ("library_snapshot", "library_disk_snapshot")
GB = 1024 ** 3
TODAY = datetime.now(formatting.zone()).date()


def day(ago: int) -> str:
    return (TODAY - timedelta(days=ago)).isoformat()


def add_item(item_id: str, path: str, size: int, created_ago: int) -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=created_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    with db.connect() as conn:
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, date_created, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?, ?)", (item_id, item_id, path, size, created, db.utcnow()))
        conn.commit()


def set_jellyfin(free_movies: int, used_movies: int) -> None:
    def folder(path: str, device: str, free: int, used: int) -> dict[str, Any]:
        return {"Path": path, "FreeSpace": free, "UsedSpace": used, "StorageType": "Fixed", "DeviceId": device}
    scanner.remember_folders({"Libraries": [
        {"Name": "Filmy", "Folders": [folder("/media/filmy", "dev-a", free_movies, used_movies)]},
        {"Name": "Serialy", "Folders": [folder("/media/serialy", "dev-b", 300 * GB, 700 * GB)]}]})
    db.forget_settings()


def physical(table: str, new_column: str) -> str | None:
    """The column's name in the table now: the new one, or the old one the map names (None = neither)."""
    present = columns(table)
    if new_column in present:
        return new_column
    rename = next((t for t in schema_names.TABLES if t.new == table), None)
    old = next((o for o, n in (rename.columns if rename else {}).items() if n == new_column), None)
    return old if old in present else None


def keyless(value: Any) -> Any:
    """The values without the names of the keys (the keys that mirror a column change their names)."""
    if isinstance(value, dict):
        return sorted((keyless(v) for v in value.values()), key=repr)
    if isinstance(value, (list, tuple)):
        return [keyless(v) for v in value]
    return value


def fingerprints(disk: str) -> list[str]:
    day_column, fingerprint = physical("library_disk_snapshot", "day"), physical("library_disk_snapshot", "fingerprint")
    if not (day_column and fingerprint):
        return []
    return [str(r["f"]) for r in db.query_all(f"SELECT {fingerprint} AS f FROM library_disk_snapshot WHERE disk = ?"
                                              f" ORDER BY {day_column}", (disk,))]


db.set_setting(scanner.CONVERTED_KEY, "1")
db.forget_settings()
add_item("a", "/media/filmy/a.mkv", 30 * GB, 30)
add_item("s", "/media/serialy/s.mkv", 50 * GB, 10)
set_jellyfin(520 * GB, 480 * GB)
scanner.write_disk_snapshot(day(8))
add_item("b", "/media/filmy/b.mkv", 10 * GB, 6)
set_jellyfin(500 * GB, 500 * GB)
scanner.write_disk_snapshot(day(4))
add_item("c", "/media/filmy/c.mkv", 5 * GB, 2)
set_jellyfin(490 * GB, 510 * GB)
scanner.write_disk_snapshot(day(1))
scanner.write_snapshot(490 * GB)
disks = sorted(stats.disk_snapshots(30))


def observe_task2() -> dict[str, Any]:
    return {
        "disk rows": keyless(stats.disk_snapshots(30)),
        "overview": keyless(stats.disks_overview(30)),
        "runs out first": keyless(stats.runs_out_first(30)),
        "to choose": keyless(stats.disks_to_choose()),
        "outside": keyless(stats.outside_series(30)),
        "library rows": keyless(stats.snapshots(30)),
        "library growth": keyless(stats.library_growth(30)),
        "disk growth": {disk: (keyless(stats.disk_growth(30, disk)), keyless(stats.disk_growth_series(30, disk))) for disk in disks},
    }


before2 = observe_task2()
check(len(disks) == 2 and len(before2["disk rows"][0]) == 3 and before2["library rows"],
      f"three snapshots of two disks and a library snapshot ({disks})")
prints_before = {disk: fingerprints(disk) for disk in disks}
rows_before2 = counts(TASK2)
old_tables(TASK2)
check_shape(TASK2, converted=False)

db.init_db()
db.forget_settings()
check_shape(TASK2, converted=True)
check(None not in rows_before2.values() and counts(TASK2) == rows_before2, f"the same rows ({rows_before2})")
after2 = observe_task2()
check(after2 == before2, f"the disks, the forecast and the growth are the same ({[k for k in before2 if before2[k] != after2.get(k)]})")
check(all(len(v) == 3 for v in prints_before.values()) and prints_before == {disk: fingerprints(disk) for disk in disks},
      f"the fingerprints are kept ({prints_before})")
scanner.write_disk_snapshot(day(0))
prints_after = {disk: fingerprints(disk) for disk in disks}
check(all(len(prints_after[d]) == 4 and len(set(prints_after[d])) == 1 for d in disks),
      f"the next snapshot carries the same fingerprint: the forecast goes on over all four days ({prints_after})")
check(stats.runs_out_first(30) is not None and keyless(stats.disk_snapshots(30)) != before2["disk rows"],
      "after it the forecast is computed and the new day is in the rows")


print("--- items, versions, playback, the bin, forgettings (database step 4, task 3) ---")
from jellyscope import insights, purge  # noqa: E402

TASK3 = ("items", "item_versions", "playback", "playback_bin", "forgettings")


def name_of(table: str, new: str, old: str) -> str:
    """A table's or column's name as the database has it now: the new one, else the old one."""
    if table == "":
        return new if new in tables() else old
    return new if new in columns(table) else old


def sql(statement: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(statement, params)
        conn.commit()


RECENT = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
main_id = name_of("items", "main_id", "hlavni_id")
sql("INSERT INTO libraries (id, name, collection_type) VALUES ('lib-c', 'Filmy', 'movies')")
for item_id, name, main in (("m1", "Duna", None), ("m1b", "Duna", "m1"), ("m2", "Matrix", None)):
    sql(f"INSERT INTO items (id, name, type, library_id, path, size_bytes, is_missing, date_created, synced_at, tmdb_id,"
        f" tech_source, width, height, {main_id}) VALUES (?, ?, 'Movie', 'lib-c', ?, ?, 0, ?, ?, '438631', 'jellyfin', 3840, 2160, ?)",
        (item_id, name, f"/media/c/{item_id}.mkv", 20 * GB, RECENT, db.utcnow(), main))
position, version_name = name_of("item_versions", "position", "poradi"), name_of("item_versions", "name", "nazev")
for number, (source, label) in enumerate((("s-4k", "4K HDR"), ("s-hd", "1080p"))):
    sql(f"INSERT INTO item_versions (item_id, source_id, {position}, {version_name}, path, container, width, height, size_bytes,"
        f" synced_at) VALUES ('m1', ?, ?, ?, ?, 'mkv', 1920, 1080, ?, ?)", (source, number, label, f"/media/c/m1-{source}.mkv", GB, db.utcnow()))
fps_sum, fps_samples = name_of("playback", "transcode_fps_sum", "transcode_fps_soucet"), name_of("playback", "transcode_fps_samples", "transcode_fps_vzorku")
for user_id, user_name, item_id, total, samples in (("u1", "Jana", "m1", 2400, 100), ("u1", "Jana", "m1b", 900, 30),
                                                    ("u2", "Petr", "m2", 1500, 60), ("u2", "Petr", "m1", 500, 25)):
    sql("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES (?, ?, 0, 0) ON CONFLICT (id) DO NOTHING", (user_id, user_name))
    sql(f"INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id, play_method,"
        f" started_at, last_seen_at, ended_at, watched_seconds, is_active, transcode_video_direct, transcode_fps, video_fps,"
        f" {fps_sum}, {fps_samples}) VALUES (?, ?, ?, ?, 'Duna', 'Movie', 'lib-c', 'Transcode', ?, ?, ?, 3600, 0, 0, 20, 24, ?, ?)",
        (f"k-{user_id}-{item_id}", user_id, user_name, item_id, RECENT, RECENT, RECENT, total, samples))
petr_rows = sorted((str(r["item_id"]), int(r["s"]), int(r["n"])) for r in db.query_all(
    f"SELECT item_id, {fps_sum} AS s, {fps_samples} AS n FROM playback WHERE user_id = 'u2'"))
forgotten = purge.forget_user("u2", to_bin=True)
event_id = int(db.query_value(f"SELECT MAX(id) FROM {name_of('', 'forgettings', 'zapomenuti')}", default=0) or 0)


def observe_task3() -> dict[str, Any]:
    return {
        "title items": stats.title_items("m1"),
        "versions": keyless(stats.item_versions("m1")),
        "files": keyless(stats.title_files("m1")),
        "plays": keyless(stats.item_playback("m1")),
        "recent": keyless(stats.recently_added()),
        "speeds": keyless(insights.transcode_speeds(30)),
        "forgettings": keyless(purge.forgettings(False)),
        "bin": purge.bin_has_rows(),
    }


before3 = observe_task3()
check(before3["title items"] == ["m1", "m1b"] and len(before3["versions"]) == 2 and before3["bin"] and event_id
      and len(petr_rows) == 2, f"a copy, two versions, plays with the speed of a transcode, a viewer in the bin ({forgotten})")
rows_before3 = counts(TASK3)
old_tables(TASK3)
check_shape(TASK3, converted=False)

db.init_db()
db.forget_settings()
check_shape(TASK3, converted=True)
check(None not in rows_before3.values() and counts(TASK3) == rows_before3, f"the same rows ({rows_before3})")
after3 = observe_task3()
check(after3 == before3, f"copies, versions, plays, speeds and the forgotten the same ({[k for k in before3 if before3[k] != after3.get(k)]})")
restored = purge.restore_from_bin(event_id)
back = sorted((str(r["item_id"]), int(r["s"]), int(r["n"])) for r in db.query_all(
    f"SELECT item_id, {name_of('playback', 'transcode_fps_sum', 'transcode_fps_soucet')} AS s,"
    f" {name_of('playback', 'transcode_fps_samples', 'transcode_fps_vzorku')} AS n FROM playback WHERE user_id = 'u2'"))
check(back == petr_rows and not purge.bin_has_rows(),
      f"the viewer comes back from the bin with every column, the speed of a transcode too ({restored}, {back})")


print("--- the lock against 1.8.x and what a start of 1.8.x leaves behind (database step 4, task 4) ---")
LOCK = getattr(schema_names, "LOCK_VIEW", "playback_kos")


def views() -> set[str]:
    with db.connect() as conn:
        if conn.is_postgres:
            rows = conn.execute("SELECT table_name AS name FROM information_schema.views"
                                " WHERE table_schema = current_schema()").fetchall()
        else:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall()
    return {str(dict(r)["name"]) for r in rows}


check(LOCK == "playback_kos" and LOCK in views() and LOCK not in tables(),
      f"a database of 1.9.0 has the view named like the bin of 1.8.x ({sorted(views())})")
# The start of 1.8.4 runs its tables script and its migrations before it reaches the bin (and the view stops it).
# On SQLite those commit at once: empty old tables and old columns holding NULL or their default stay.
ALL = TASK1 + TASK2 + TASK3
rows_before4 = counts(ALL)
shown_before4 = (observe_task1(), observe_task2(), observe_task3())
LEFTOVER_TABLES = ("mapa_dlazdice", "zapomenuti")
sql("CREATE TABLE mapa_dlazdice (podrobnost INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, obsah TEXT NOT NULL,"
    " PRIMARY KEY (podrobnost, x, y))")
sql("CREATE TABLE zapomenuti (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, jmeno TEXT NOT NULL, relaci INTEGER NOT NULL,"
    " zapomenuto_v TEXT NOT NULL, zaloha TEXT NOT NULL DEFAULT '', stav TEXT NOT NULL DEFAULT 'kos')")
LEFTOVER_COLUMNS = (("dashboard_layout", "sirka", "TEXT"), ("accounts", "pristup", "TEXT"), ("accounts", "smi_klic", "TEXT"),
                    ("api_tokens", "prava", "TEXT"), ("api_tokens", "stav", "TEXT NOT NULL DEFAULT 'aktivni'"),
                    ("items", "hlavni_id", "TEXT"), ("playback", "transcode_fps_soucet", "INTEGER NOT NULL DEFAULT 0"),
                    ("library_disk_snapshot", "otisk", "TEXT NOT NULL DEFAULT ''"))
for table, column, kind in LEFTOVER_COLUMNS:
    sql(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
try:
    db.init_db()
    refused_leftovers = None
except db.ConversionFailed as error:
    refused_leftovers = error
db.forget_settings()
left = [t for t in LEFTOVER_TABLES if t in tables()] + [f"{t}.{c}" for t, c, _ in LEFTOVER_COLUMNS if c in columns(t)]
check(refused_leftovers is None and not left and counts(ALL) == rows_before4 and LOCK in views(),
      f"the next start of 1.9.0 drops the leftovers and keeps every row ({refused_leftovers}, {left})")
shown_after4 = (observe_task1(), observe_task2(), observe_task3())
check(shown_after4 == shown_before4, f"and the application shows the same ({[i + 1 for i in range(3) if shown_after4[i] != shown_before4[i]]})")

# An old column with a real value next to the new one is no leftover: data under both names stops the start.
sql("ALTER TABLE dashboard_layout ADD COLUMN sirka TEXT")
sql("UPDATE dashboard_layout SET sirka = 'pul'")
try:
    db.init_db()
    refused = None
except db.ConversionFailed as error:
    refused = error
check(refused is not None and "sirka" in columns("dashboard_layout"), f"a value under the old name: the start is refused ({refused!r})")
sql("ALTER TABLE dashboard_layout DROP COLUMN sirka")
sql("ALTER TABLE accounts ADD COLUMN pristup TEXT DEFAULT NULL")
sql("UPDATE accounts SET pristup = '{}'")
try:
    db.init_db()
    refused_null = None
except db.ConversionFailed as error:
    refused_null = error
check(refused_null is not None and "pristup" in columns("accounts"),
      f"a value under an old name declared DEFAULT NULL is data too: the start is refused ({refused_null!r})")
sql("ALTER TABLE accounts DROP COLUMN pristup")
db.init_db()
check("sirka" not in columns("dashboard_layout") and counts(ALL) == rows_before4, "without it the start goes on")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
