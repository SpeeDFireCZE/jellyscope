# -*- coding: utf-8 -*-
"""Choosing the right version of pg_dump for a PostgreSQL backup.

The reported error was:

    pg_dump: error: server version: 17.5; pg_dump version: 12.22
    pg_dump: error: aborting because of server version mismatch

`pg_dump` can back up a server of the **same or an older** version, never
a newer one. On Ubuntu 20.04 the client 12 is in PATH, while the server runs 17 -
and the backup therefore quietly failed at every attempt.

What matters is that on Debian and Ubuntu clients can be installed
**side by side in more versions** (`/usr/lib/postgresql/<version>/bin/`) and PATH
tends to have the oldest one. So it is enough to look next to it before giving up
straight away.

Run:
    .venv/bin/python tests/test_backup_pgdump.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a stand-in in all submodules of the scanner package

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice
# of the database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zaloha.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import db, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

# Neither the tools nor the server are here - we slip in what is to be "found".
original_dumps = tasks.available_pg_dumps
original_server = tasks._server_major


def arrange(found: list[tuple[int, str]], server: int) -> None:
    tasks.available_pg_dumps = lambda path="": list(found)   # type: ignore[assignment]
    tasks._server_major = lambda config: server             # type: ignore[assignment]


print("--- the newest one that is enough for the server is chosen ---")
arrange([(17, "/usr/lib/postgresql/17/bin/pg_dump"),
         (15, "/usr/lib/postgresql/15/bin/pg_dump"),
         (12, "/usr/bin/pg_dump")], server=15)
chosen = tasks._choose_pg_dump(None)
check(chosen.endswith("/17/bin/pg_dump"),
      f"a newer client manages the server 15 ({chosen})")

arrange([(15, "/usr/lib/postgresql/15/bin/pg_dump"),
         (12, "/usr/bin/pg_dump")], server=15)
check(tasks._choose_pg_dump(None).endswith("/15/bin/pg_dump"),
      "the same version of course too")

# This is the core of the matter: PATH has an old client, but a newer one lies next to it.
arrange([(17, "/usr/lib/postgresql/17/bin/pg_dump"),
         (12, "/usr/bin/pg_dump")], server=17)
check(tasks._choose_pg_dump(None).endswith("/17/bin/pg_dump"),
      "PATH has an old client, but the right one lies next to it")


print()
print("--- when none is enough, it advises what to install ---")
arrange([(12, "/usr/bin/pg_dump")], server=17)
try:
    tasks._choose_pg_dump(None)
    check(False, "it should have ended with an error")
except RuntimeError as exc:
    message = str(exc)
    check("12" in message and "17" in message,
          "the message names both versions")
    check("postgresql-client-17" in message,
          f"and the exact command to install ({message.splitlines()[1].strip()})")
    check("/usr/bin/pg_dump" in message,
          "and what is installed now")
    check("Settings → Tasks" in message, "and that the path can be filled in by hand")
    check("Nastavení → Úlohy" in exc.translated("cs") and "postgresql-client-17" in exc.translated("cs"),
          "the same advice for a Czech reader, the command unchanged")


print()
print("--- without a single tool ---")
arrange([], server=17)
try:
    tasks._choose_pg_dump(None)
    check(False, "it should have ended with an error")
except RuntimeError as exc:
    check("could not be found" in str(exc), f"it is said in English for the log ({exc})")
    check("se nepodařilo najít" in exc.translated("cs"), "and in Czech for a Czech reader")


print()
print("--- an unknown server version does not block the backup ---")
# When the version cannot be found out (an old server, missing permissions),
# it is better to try with the newest available one than to refuse to back up.
arrange([(15, "/usr/lib/postgresql/15/bin/pg_dump"),
         (12, "/usr/bin/pg_dump")], server=0)
check(tasks._choose_pg_dump(None).endswith("/15/bin/pg_dump"),
      "simply the newest one is taken")

tasks.available_pg_dumps = original_dumps
tasks._server_major = original_server


print()
print("--- a path set by hand takes precedence ---")
fake = Path(_tmp) / "pg_dump"
fake.write_text("#!/bin/sh\necho 'pg_dump (PostgreSQL) 16.2'\n", encoding="utf-8")
found = tasks.available_pg_dumps(str(fake))
check(len(found) == 1 and found[0][1] == str(fake),
      f"a filled-in path pushes out the search ({found})")

# A folder instead of a file is worked out - the same as with ffprobe.
from_folder = tasks.available_pg_dumps(_tmp)
check(len(from_folder) == 1 and from_folder[0][1] == str(fake),
      f"and a folder instead of a file is worked out ({from_folder})")


print()
print("--- on SQLite none of it is dealt with ---")
check(tasks.server_version() == 0,
      "the server version is 0, so the section in Settings does not even show")
check(not db.database_config().is_postgres, "and the backup goes through the built-in function")


print()
print("--- an own backup when pg_dump is not available ---")
# A client of a newer version cannot always be installed: the PGDG repository for
# Ubuntu 20.04 no longer exists, because that version ended its support. Refusing
# to back up in such a situation would be the worst solution - Jellyscope knows its
# tables and can export them itself.
from jellyscope import demodata  # noqa: E402

demodata.seed()
destination = Path(_tmp) / "vlastni.sql"
size = tasks._dump_own(destination)
text = destination.read_text(encoding="utf-8")

check(size > 0 and destination.exists(), f"the file was made ({size} B)")
check("CREATE TABLE" in text, "it contains the schema")
check(text.count("INSERT INTO") > 100, f"and the data ({text.count('INSERT INTO')} INSERTs)")
check(text.startswith("-- Jellyscope backup (own export)"), "and a header that makes clear what it is")
check(tasks._is_own_export(text[:200]), "the restore knows it as its own export")
check(tasks._is_own_export("-- Záloha Jellyscope (vlastní export)\n-- vytvořeno: 2026-11-01 02:00:00 UTC\n"),
      "and a backup written by 1.8.x (a Czech header) too - it still restores")
check(not tasks._is_own_export("--\n-- PostgreSQL database dump\n--\n"), "the output of pg_dump is not its own")
check("psql" in text, "with instructions for restoring")
check(text.rstrip().endswith("COMMIT;"), "all in one transaction")

# The order of the tables must respect the foreign keys, otherwise a restore falls over.
order = [text.index(f"INSERT INTO {t} ") for t in ("items", "item_streams")
         if f"INSERT INTO {t} " in text]
check(len(order) < 2 or order[0] < order[1],
      "the items are inserted before their streams (a foreign key)")

# An apostrophe in a movie's name must not break the backup.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, is_missing, synced_at)"
        " VALUES ('apostrof', ?, 'Movie', 0, ?)",
        ("Ohnivý oheň'; DROP TABLE items; --", db.utcnow()))
tasks._dump_own(destination)
text = destination.read_text(encoding="utf-8")
check("''; DROP TABLE" in text,
      "the apostrophe is doubled, so no command comes of it")
check(tasks._sql_value(None) == "NULL", "an empty value is NULL")
check(tasks._sql_value(42) == "42", "a number is not wrapped in apostrophes")
check(tasks._sql_value("a'b") == "'a''b'", "and text is")


print()
print("--- pg_dump opens the file itself ---")
# Originally pg_dump was handed an open descriptor through redirecting
# the output and in some environments that failed with
# "could not write to output file: Bad file descriptor". With the switch -f
# pg_dump is responsible for the file and the error is then its own, understandable.
import subprocess as _subprocess  # noqa: E402

captured: dict[str, object] = {}
_original_run = _subprocess.run


def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
    captured["command"] = list(command)
    captured["kwargs"] = kwargs
    destination = Path(command[command.index("-f") + 1])
    destination.write_text("-- dump" + chr(10), encoding="utf-8")

    class Result:
        returncode = 0
        stdout = b""
        stderr = b""

    return Result()


class _Config:
    host, port, user, database, password = "h", 5432, "u", "d", "tajne"


_subprocess.run = _fake_run                             # type: ignore[assignment]
tasks.available_pg_dumps = lambda path="": [(17, "/usr/lib/postgresql/17/bin/pg_dump")]
tasks._server_major = lambda config: 17                # type: ignore[assignment]
pg_destination = Path(_tmp) / "pgdump.sql"
pg_size = tasks._dump_postgres(_Config(), pg_destination)
_subprocess.run = _original_run                         # type: ignore[assignment]
tasks.available_pg_dumps = original_dumps
tasks._server_major = original_server

command = captured["command"]
check("-f" in command and command[command.index("-f") + 1] == str(pg_destination),
      f"the file is passed by the switch -f ({' '.join(command[-2:])})")
check(captured["kwargs"].get("stdout") is _subprocess.PIPE,
      "the output is not redirected into an open file")
check(captured["kwargs"]["env"].get("PGPASSWORD") == "tajne",
      "the password goes through an environment variable, not on the command line")
check("tajne" not in " ".join(command), "and it is not in the process's arguments")
check(pg_size > 0, f"the size is returned ({pg_size} B)")


print()
print("--- a pg_dump backup that is not whole is refused before psql drops anything ---")
# Found 26. 11. 2026: psql drops Jellyscope's tables and replays the file in ONE transaction - but a file with no
# statements (a dump cut off, or anything named like a backup) commits the drops and restores nothing: the data is
# gone. pg_dump ends its plain output with "-- PostgreSQL database dump complete" (then "\unrestrict ..."); a file
# without that line is refused and psql never runs. The own export has its own check (COMMIT; at the end).
psql_calls: list[list[str]] = []


def _psql_run(command, **kwargs):  # type: ignore[no-untyped-def]
    psql_calls.append(list(command))

    class Result:
        returncode = 0
        stdout = b""
        stderr = b""

    return Result()


WHOLE_DUMP = ("--\n-- PostgreSQL database dump\n--\n\n\\restrict abc\n\nSET statement_timeout = 0;\n"
              "CREATE TABLE public.items (id text);\n\n--\n-- PostgreSQL database dump complete\n--\n\n\\unrestrict abc\n")
original_find_psql, original_drops = tasks._find_psql, tasks._jellyscope_drops
_subprocess.run = _psql_run                             # type: ignore[assignment]
tasks._find_psql = lambda: "/usr/bin/psql"             # type: ignore[assignment]
tasks._jellyscope_drops = lambda conn: ["DROP TABLE IF EXISTS items CASCADE"]  # type: ignore[assignment]
try:
    for label, text in (("a file with no statements", "-- dump\n"),
                        ("a pg_dump cut off before its end", WHOLE_DUMP.split("--\n-- PostgreSQL database dump complete")[0])):
        dump_file = Path(_tmp) / f"jellyscope-20260101-00000{len(psql_calls)}.sql"
        dump_file.write_text(text, encoding="utf-8")
        psql_calls.clear()
        try:
            tasks._restore_postgres(_Config(), dump_file)
            refused = False
        except tasks.BackupError:
            refused = True
        check(refused and not psql_calls, f"{label}: refused, psql does not run and drops nothing ({refused}, {len(psql_calls)} runs)")
    whole_file = Path(_tmp) / "jellyscope-20260101-000009.sql"
    whole_file.write_text(WHOLE_DUMP, encoding="utf-8")
    psql_calls.clear()
    tasks._restore_postgres(_Config(), whole_file)
    check(len(psql_calls) == 1 and "--single-transaction" in psql_calls[0],
          "a whole pg_dump still goes to psql, in one transaction")
finally:
    _subprocess.run = _original_run                     # type: ignore[assignment]
    tasks._find_psql, tasks._jellyscope_drops = original_find_psql, original_drops


print()
print("--- the progress indicator with the file analysis ---")
# The indicator rose three times for one file (after taking the semaphore,
# after the analysis and after the write), so it got up to 300 % and the interface
# showed 135 %, say. One file is one step.
import asyncio  # noqa: E402

from jellyscope import probe, scanner  # noqa: E402

with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lp','L',?)",
                 (db.utcnow(),))
    for number in range(1, 11):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, path, is_missing, synced_at)
               VALUES (?, ?, 'Movie', 'lp', ?, 0, ?)""",
            (f"p{number}", f"Film {number}", f"/media/film{number}.mkv", db.utcnow()))


async def _fake_probe(path: str, tool: str) -> dict[str, object]:
    # Two files fail on purpose - they too must be counted exactly once.
    if path.endswith(("3.mkv", "7.mkv")):
        raise probe.ProbeError("soubor nenalezen")
    return {"container": "mkv", "video_codec": "h264", "width": 1920, "height": 1080}


probe.probe_file = _fake_probe                          # type: ignore[assignment]
probe.find_ffprobe = lambda path="": "/usr/bin/ffprobe"  # type: ignore[assignment]

percents: list[int] = []
_original_step = scanner._add_progress


def _watch(count: int) -> None:
    _original_step(count)
    state = scanner.progress()
    if state and state.get("percent") is not None:
        percents.append(state["percent"])


substitute(scanner, "_add_progress", _watch)
result = asyncio.run(scanner.run_tech_scan(only_missing=False))
substitute(scanner, "_add_progress", _original_step)

check(result["total"] == 10, f"ten files were analysed ({result['total']})")
check(len(percents) == 10,
      f"and the indicator moved exactly ten times ({len(percents)})")
check(max(percents) == 100, f"at most to a hundred percent ({max(percents)})")
check(result["ok"] == 8 and result["failed"] == 2,
      f"the faulty files count too ({result['ok']}/{result['failed']})")


print()
print("--- downloading and deleting a backup from Settings ---")
# A backup that cannot be got out of the machine is useless - and an old
# backup that cannot be deleted takes up space. Both must work from the browser.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "secretpass1", is_admin=True)
accounts.create("reader", "readerpass1", is_admin=False)

folder = Path(_tmp) / "backups"
folder.mkdir(exist_ok=True)
db.set_setting("backup_path", str(folder))
(folder / "jellyscope-20260817-100000.db").write_bytes(b"SQLite format 3")
(folder / "jellyscope-20260817-110000.sql").write_text("-- dump", encoding="utf-8")
# A foreign file one level up - it must not get out.
(Path(_tmp) / "tajne.txt").write_text("SECRET_KEY=nedostupne", encoding="utf-8")

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "secretpass1"})

    page = client.get("/settings?section=tasks").text
    check("backup/download" in page and "backup/delete" in page,
          "every backup has the buttons")

    response = client.get("/settings/backup/download",
                          params={"name": "jellyscope-20260817-110000.sql"})
    check(response.status_code == 200, f"the backup is downloaded ({response.status_code})")
    check("attachment" in response.headers.get("content-disposition", ""),
          "and the browser saves it instead of showing it")

    # A name from the address is not a path - the same trap as with the log viewer.
    for attempt in ("../tajne.txt", "..\tajne.txt", "/etc/passwd",
                    "neexistuje.db", "x.db", ""):
        response = client.get("/settings/backup/download", params={"name": attempt})
        check(response.status_code == 404, f"{attempt!r} is not downloaded")

    response = client.post("/settings/backup/delete",
                           data={"name": "jellyscope-20260817-100000.db"},
                           follow_redirects=False)
    check(response.status_code == 303
          and not (folder / "jellyscope-20260817-100000.db").exists(),
          "the backup is deleted")

    client.post("/settings/backup/delete", data={"name": "../tajne.txt"},
                follow_redirects=False)
    check((Path(_tmp) / "tajne.txt").exists(),
          "a foreign file cannot be deleted")

    print()
    print("--- restoring a backup ---")
    # A restore is the only operation that can delete everything at once. That is why
    # Jellyscope first saves the current state - whoever mistakes a row
    # in the list does not lose the data.
    import asyncio as _asyncio  # noqa: E402

    with db.connect() as conn:
        conn.execute("DELETE FROM items")
        for ident, name in (("obn-a", "Film A"), ("obn-b", "Film B")):
            conn.execute(
                "INSERT INTO items (id, name, type, is_missing, synced_at)"
                " VALUES (?, ?, 'Movie', 0, ?)", (ident, name, db.utcnow()))

    done = _asyncio.run(tasks.backup_database())
    check(done["status"] == "ok", f"the backup of the state was made ({done.get('message', '')})")
    backup_name = Path(done["file"]).name

    # The state changes - and the restore is to bring it back.
    with db.connect() as conn:
        conn.execute("DELETE FROM items")
        conn.execute(
            "INSERT INTO items (id, name, type, is_missing, synced_at)"
            " VALUES ('obn-c', 'Film C', 'Movie', 0, ?)", (db.utcnow(),))

    result = tasks.restore_backup(backup_name)
    check(result["status"] == "ok", f"the restore ran ({result})")
    names = {r["name"] for r in db.query_all("SELECT name FROM items")}
    check(names == {"Film A", "Film B"}, f"the data is back ({sorted(names)})")
    check((folder / result["safety"]).exists(),
          f"and the state before the restore stayed saved ({result['safety']})")

    # Neither a foreign file nor nonsense can be restored.
    for attempt in ("../tajne.txt", "neexistuje.db", ""):
        refused = tasks.restore_backup(attempt)
        check(refused["status"] == "error", f"{attempt!r} is not restored")

    # And a backup from another database not either - overwriting SQLite with a dump
    # from PostgreSQL would break the application.
    (folder / "jellyscope-20260101-000000.sql").write_text("-- dump", encoding="utf-8")
    wrong = tasks.restore_backup("jellyscope-20260101-000000.sql")
    check(wrong["status"] == "error" and "PostgreSQL" in wrong["message"],
          f"a backup from another database is refused ({wrong['message'][:60]})")

    print()
    print("--- and a reader may do none of it ---")
    client.post("/logout")
    client.post("/login", data={"username": "reader", "password": "readerpass1"})
    download = client.get("/settings/backup/download",
                          params={"name": "jellyscope-20260817-110000.sql"})
    deletion = client.post("/settings/backup/delete",
                           data={"name": "jellyscope-20260817-110000.sql"},
                           follow_redirects=False)
    restore_response = client.post("/settings/backup/restore",
                                   data={"name": "jellyscope-20260817-110000.sql"},
                                   follow_redirects=False)
    check(download.status_code == 403, f"the download refused ({download.status_code})")
    check(deletion.status_code == 403, f"the deletion refused ({deletion.status_code})")
    check(restore_response.status_code == 403, f"the restore refused ({restore_response.status_code})")
    check((folder / "jellyscope-20260817-110000.sql").exists(),
          "and the backup stayed in its place")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
