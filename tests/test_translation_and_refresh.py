# -*- coding: utf-8 -*-
"""Loading the metadata of one item and gaps in the English translation.

Two things together, because both are about what the user really sees:

1. **Refreshing one item.** When you fix the metadata of one
   movie in Jellyfin, there should be no need to run the sync of the whole
   library for it. Jellyfin may only be read from while doing so.

2. **Translation.** Sentences put together in Python (chart labels, task messages,
   "před 5 min") do not get into the dictionary by themselves - and when one is put together
   with a variable part, it is never found in the dictionary. Exactly that happened
   with "Dabing, nebo originál?", where half of the sentence stayed Czech.
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
from _package import substitute  # noqa: E402 - a stand-in in all submodules of the scanner package

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "preklad.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import (accounts, applog, db, formatting, i18n,  # noqa: E402
                        langstats, scanner)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass", is_admin=False)

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lib','Filmy',?)",
                 (db.utcnow(),))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, production_year,
                              audio_languages, is_missing, synced_at)
           VALUES ('film-1', 'Starý název', 'Movie', 'lib', 1999, 'en', 0, ?)""",
        (db.utcnow(),))


print("--- loading the metadata of one item ---")
asked: list[list[str]] = []


class FakeClient:
    """A Jellyfin that knows one item - and reports what it was asked for."""

    answer: list[dict[str, Any]] = [{
        "Id": "film-1", "Name": "Opravený název", "Type": "Movie",
        "ProductionYear": 2001, "RunTimeTicks": 60_000_000_000,
        "MediaSources": [{
            "Container": "mkv", "Size": 5_000_000_000, "Path": "/m/f.mkv",
            "MediaStreams": [
                {"Type": "Video", "Index": 0, "Codec": "hevc",
                 "Width": 1920, "Height": 1080},
                {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "cze"},
                {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "eng"},
            ],
        }],
    }]

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        asked.append(list(ids))
        return [i for i in self.answer if i["Id"] in ids]


substitute(scanner, "JellyfinClient", FakeClient)

result = asyncio.run(scanner.refresh_item("film-1"))
check(result["status"] == "ok", f"the refresh ran ({result})")
check(asked == [["film-1"]], f"we asked only for that one item ({asked})")

row = db.query_one("SELECT * FROM items WHERE id = 'film-1'")
check(row["name"] == "Opravený název", f"the name was overwritten ({row['name']})")
check(row["production_year"] == 2001, f"the year too ({row['production_year']})")
check(row["library_id"] == "lib",
      "the library stayed - Jellyfin does not send it in the answer and an empty one would "
      "drop the item from the overviews")
check("cs" in (row["audio_languages"] or ""),
      f"the track's language was filled in ({row['audio_languages']})")

streams = db.query_all("SELECT * FROM item_streams WHERE item_id = 'film-1'")
check(len(streams) == 3, f"the streams were overwritten ({len(streams)})")

# What Jellyfin does not know must not be quietly deleted - the history points at it.
unknown = asyncio.run(scanner.refresh_item("neexistuje"))
check(unknown["status"] == "error", "an unknown id returns an error")
FakeClient.answer = []
gone = asyncio.run(scanner.refresh_item("film-1"))
check(gone["status"] == "error", "an item Jellyfin does not know, too")
check(db.query_one("SELECT * FROM items WHERE id = 'film-1'") is not None,
      "and it stayed in the database")


print()
print("--- the button is only for the administrator ---")
from fastapi.testclient import TestClient  # noqa: E402

import jellyscope.web as web  # noqa: E402

substitute(scanner, "JellyfinClient", FakeClient)

admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword"})
reader = TestClient(web.app)
reader.post("/login", data={"username": "reader", "password": "readerpass"})

page = admin.get("/item/film-1").text
check('action="/item/film-1/refresh"' in page, "the administrator sees the button")
check('action="/item/film-1/refresh"' not in reader.get("/item/film-1").text,
      "a reader not")
check(reader.post("/item/film-1/refresh", follow_redirects=False).status_code == 403,
      "and does not get the route")


print()
print("--- the English translation where sentences are put together in Python ---")
db.set_setting("ui_language", "en")
try:
    # "Dabing, nebo originál?" - the label carries the language's name, so
    # the whole sentence is never found in the dictionary. It must be put together from pieces.
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, audio_languages,
                                  is_missing, synced_at)
               VALUES ('film-2','Duna','Movie','lib','cs,en',0,?)""",
            (db.utcnow(),))
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, audio_language, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES ('s1','u1','film-2','Duna','Movie','cs',?,?,3600,0)""",
            (db.utcnow(), db.utcnow()))
    db.set_setting("preferred_language", "cs")
    labels = [r["label"] for r in langstats.dubbed_vs_original(90)]
    check(labels and all("i když" not in p and "jiná možnost" not in p
                         and "Jiný jazyk" not in p for p in labels),
          f"the dubbing labels are in English ({labels})")

    check(formatting.relative_human(db.utcnow()) == "just now",
          f"the relative time is in English ({formatting.relative_human(db.utcnow())})")

    from jellyscope import tasks  # noqa: E402
    # The source is English: a task's name is shown as it is in English and has a Czech translation.
    for task in tasks.TASKS.values():
        check(i18n.translate(task.name, "cs") != task.name,
              f"the task's name has a Czech translation: {task.name}")
        check(i18n.translate(task.description, "cs") != task.description,
              f"the task's description has a Czech translation: {task.name}")

    tasks_page = admin.get("/settings?section=tasks").text
    for czech in ["Synchronizace knihovny", "Stáhne z Jellyfinu",
                  "Záloha databáze", "Nově přidané tituly"]:
        check(czech not in tasks_page, f"the English Tasks have no {czech!r}")

    import_page = admin.get("/settings?section=import").text
    check("Vybrat soubor" not in import_page and "Choose file" in import_page,
          "choosing a file is an own button, not the browser's native element")
finally:
    db.set_setting("ui_language", "cs")


print()
print("--- the log's language ---")
# Since 21. 11. 2026 the file is always English and the Log page translates each line for its reader
# (applog.translate_line) - see tests/test_log_language.py for the page.
import logging  # noqa: E402

LINE = "2026-11-21 10:00:00,000 INFO    jellyscope.t: "


def translated(message: str, language: str = "cs") -> str:
    return applog.translate_line(LINE + message, language)[len(LINE):]


check(translated("database ready", "en") == "database ready", "in English (the source) nothing is translated")
check(translated("database ready") == "databaze pripravena", f"in Czech yes ({translated('database ready')})")
check(translated("this is not in the dictionary") == "this is not in the dictionary",
      "a missing translation does not throw the message away")

# Names from the interface are translated too - a Czech reader would otherwise read
# "naplanovana uloha: Recently added titles".
from jellyscope import tasks as _tasks  # noqa: E402

check(translated("scheduled task: " + _tasks.TASKS["recent"].name) == "naplanovana uloha: Nově přidané tituly",
      "the task's name in the message is translated too (marked as a name from the interface)")
# Any other value is data: a token, a library, a status that happens to equal an English key
# ("Overview", "error") must stay as it is for a Czech reader.
check(translated("backup deleted: Overview") == "zaloha smazana: Overview", "a value equal to a key stays as it is")
check(translated("backup deleted: jellyscope-20260818.db") == "zaloha smazana: jellyscope-20260818.db",
      "and the file's name stays as it is")
# With a message the dictionary does not know (from uvicorn, say), the values are left
# alone - anything can be in them and translating data would be a mistake.
check(translated("foreign message Recently added titles") == "foreign message Recently added titles",
      "in a foreign message the values are not translated")


# The dictionary must match the messages in the sources - otherwise translating would quietly stop
# as soon as somebody rewrites a message.
import ast  # noqa: E402

in_source = set()
for source_file in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    for node in ast.walk(ast.parse(source_file.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("debug", "info", "warning", "error",
                                       "exception", "critical")
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            in_source.add(node.args[0].value)

missing = in_source - set(i18n.LOG_EN)
extra = set(i18n.LOG_EN) - in_source
check(not missing, f"every message is in the template ({len(missing)} missing: {sorted(missing)[:3]})")
check(not extra, f"and no entry stayed after a deleted message ({sorted(extra)[:3]})")
untranslated = in_source - set(i18n.LOG_TRANSLATIONS.get("cs", {}))
check(not untranslated, f"every message has a Czech translation ({len(untranslated)} missing: {sorted(untranslated)[:3]})")

# The placeholders must match, otherwise the log would fall apart only while running.
import re  # noqa: E402

pattern = re.compile(r"%\(?\w*\)?[sdrf]")
mismatched = [f"{code}: {k}" for code, table in i18n.LOG_TRANSLATIONS.items()
              for k, v in table.items() if pattern.findall(k) != pattern.findall(v)]
check(not mismatched, f"the placeholders (%s, %d) match in every language ({mismatched[:2]})")

# And now the same for real: every message is WRITTEN into a real file
# with the English translation and the right number of values. The dictionary matching
# is not enough by itself - a translation with a different kind of placeholder (%s instead of %d) passes
# the check above and falls over only while running, at a particular message nobody
# expected.

log_path = applog.setup()
probe = logging.getLogger("jellyscope.translation_probe")


def write_all() -> list[tuple[str, str]]:
    crashed = []
    for line in sorted(in_source):
        placeholders = pattern.findall(line)
        if any(z.startswith("%(") for z in placeholders):
            # Named placeholders - the application sends a dict, not a tuple.
            values = ({j: "x" for j in re.findall(r"%\((\w+)\)", line)},)
        else:
            values = tuple(7 if z[-1] in "df" else "x" for z in placeholders)
        try:
            probe.warning(line, *values)
        except Exception as error:      # noqa: BLE001
            crashed.append((line[:44], str(error)[:44]))
    for handler in logging.getLogger().handlers:
        handler.flush()
    return crashed


before = len(Path(log_path).read_text(encoding="utf-8").splitlines())
crashed = write_all()
check(not crashed, f"every message is really written in English ({crashed[:2]})")

# No Czech message may be in the file - it is always English.
written = Path(log_path).read_text(encoding="utf-8").splitlines()[before:]
czech_lines = [r.split("] ", 1)[-1] for r in written
               if re.search(r"[ěščřžýáíéůúňť]", r.split(": ", 2)[-1])]
check(not czech_lines, f"and no Czech sentence is in the file ({czech_lines[:1]})")

# Every written line is read back in Czech: a translation with another kind or number of placeholders
# would leave it English (or fail) - only the lines whose template has no Czech translation may stay.
probe_lines = [r for r in written if " jellyscope.translation_probe: " in r]
unread = [r.split(": ", 1)[-1][:50] for r in probe_lines if applog.translate_line(r, "cs") == r
          and applog.match_template(r.split("jellyscope.translation_probe: ", 1)[1]) is not None
          and i18n.LOG_TRANSLATIONS["cs"].get(applog.match_template(r.split("jellyscope.translation_probe: ", 1)[1])[0])
          not in (None, applog.match_template(r.split("jellyscope.translation_probe: ", 1)[1])[0])]
check(len(probe_lines) == len(in_source) and not unread,
      f"every written line reads in Czech ({len(probe_lines)} lines, {len(unread)} left English: {unread[:2]})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
