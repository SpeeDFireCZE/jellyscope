# -*- coding: utf-8 -*-
r"""A task's message is stored as the English key and its values and read in the reader's language.

A task writes its result into the task log (scan_log) and Settings → Tasks, Import and
Jellyfin show it. A message translated when it was written stayed in the language of that
moment: on a Czech server an account with English set read it in Czech. Since 21. 11. 2026
(the user: the log is always written in English and read in the reader's language) the key
goes into `message` and the values into `message_values`, and each reader gets their own
language - a message with numbers too. An old row (a finished sentence) shows as it was written.

Run:
    .venv/bin/python tests/test_task_message_language.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
HOME = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=HOME, DATABASE_PATH=str(Path(HOME) / "t.db"), SECRET_KEY="task-language",
                  JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="x", JELLYSCOPE_DEMO="0")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, i18n, scanner, tasks, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
KEY = "No *Arr app is connected."
CZECH = i18n.translate(KEY, "cs")
check(CZECH != KEY, f"the message has a Czech translation ({CZECH!r})")
accounts.create("eng_admin", "task-language-pw", is_admin=True)
accounts.create("cz_admin", "task-language-pw", is_admin=True)
accounts.set_language(accounts.get_by_name("eng_admin")["id"], "en")
accounts.set_language(accounts.get_by_name("cz_admin")["id"], "cs")


def tasks_page(user: str) -> str:
    client = TestClient(web.app)
    client.post("/login", data={"username": user, "password": "task-language-pw", "method": "local"})
    return client.get("/settings?section=tasks").text


print("--- a Czech server, an account in English ---")
db.set_setting("ui_language", "cs")
asyncio.run(i18n.without_account_language(tasks.run_now("arr")))
stored = scanner.last_scan("arr")["message"]
check(stored == KEY, f"the task log holds the key, not a translation ({stored!r})")
page = tasks_page("eng_admin")
check(KEY in page and CZECH not in page, "the English account reads it in English")
check(CZECH in tasks_page("cz_admin"), "a Czech account in Czech")

print()
print("--- an English server, an account in Czech ---")
db.set_setting("ui_language", "en")
asyncio.run(i18n.without_account_language(tasks.run_now("arr")))
check(scanner.last_scan("arr")["message"] == KEY, "stored as the key again")
check(CZECH in tasks_page("cz_admin"), "the Czech account reads it in Czech")

print()
print("--- an error without values: stored as its key too ---")
# A music sync refused because one is running already: MusicError(ALREADY_RUNNING) has no values.
from jellyscope import music  # noqa: E402


async def refused() -> dict:
    raise music.MusicError(music.ALREADY_RUNNING)

original = music.synchronize
music.synchronize = refused
try:
    db.set_setting("ui_language", "cs")
    asyncio.run(i18n.without_account_language(tasks.run_now("music")))
finally:
    music.synchronize = original
stored = scanner.last_scan("music")["message"]
check(stored == music.ALREADY_RUNNING, f"the task log holds the error's key ({stored!r})")
check(music.ALREADY_RUNNING in tasks_page("eng_admin"), "the English account reads it in English")

print()
print("--- an import's error: the key in the task log, the reader's language on the Import page ---")
from jellyscope import importers  # noqa: E402

db.set_setting("ui_language", "cs")
result = asyncio.run(i18n.without_account_language(importers.import_jellystat_json(b"{nonsense")))
stored = scanner.last_scan("import")["message"]
check(stored == "The file is not valid JSON.", f"the task log holds the key ({stored!r})")
check(result["message"].startswith("Soubor není platný JSON:"),
      f"the answer to whoever uploaded it is in their (the server's) language ({result['message'][:40]!r})")


def import_page(user: str) -> str:
    client = TestClient(web.app)
    client.post("/login", data={"username": user, "password": "task-language-pw", "method": "local"})
    return client.get("/settings?section=import").text


check("The file is not valid JSON." in import_page("eng_admin"), "the English account reads the stored key in English")
check("Soubor není platný JSON." in import_page("cz_admin"), "the Czech account in Czech")

print()
print("--- a message WITH values: each reader in their language, whoever started it ---")
SAMPLE = json.dumps({"jf_playback_activity": [{
    "Id": "jst-1", "NowPlayingItemId": "item-1", "NowPlayingItemName": "Dune", "SeriesName": None,
    "EpisodeId": None, "UserId": "u-1", "UserName": "Tom", "Client": "Jellyfin Web", "DeviceName": "Chrome",
    "PlayMethod": "DirectPlay", "PlaybackDuration": 7200, "ActivityDateInserted": "2026-11-01 20:00:00"}]}).encode()
db.set_setting("ui_language", "cs")
# Started by an English account (as the web's middleware sets it for the request).
token = i18n.set_account_language("en")
try:
    result = asyncio.run(importers.import_jellystat_json(SAMPLE))
finally:
    i18n.reset_account_language(token)
row = scanner.last_scan("import")
values = json.loads(row["message_values"] or "{}")
check(row["message"].startswith("Jellystat - new: {imported}") and values.get("imported") == 1,
      f"the task log holds the English key and the values ({row['message'][:40]!r}, {values})")
check("Jellystat - nových: 1" in import_page("cz_admin"), "the Czech account reads it in Czech")
check("Jellystat - new: 1" in import_page("eng_admin"), "the English account in English, though the server is Czech")
check(result["status"] == "ok", f"the import ran ({result.get('message')})")

print()
print("--- an old row, a finished sentence ---")
with db.connect() as conn:
    conn.execute("INSERT INTO scan_log (kind, started_at, finished_at, status, message) VALUES ('import', ?, ?, 'done', ?)",
                 (db.utcnow(), db.utcnow(), "Jellystat: 5 nových, 0 opravených"))
check("Jellystat: 5 nových, 0 opravených" in import_page("eng_admin"), "an old row shows as it was written")

print()
print("--- an error of Jellyfin's client and of ffprobe: English for the log, the reader's language to show ---")
from jellyscope import jellyfin, probe  # noqa: E402

error = jellyfin.JellyfinError("Could not connect to Jellyfin: {error}", error="timed out")
check(str(error) == "Could not connect to Jellyfin: timed out", f"str() is English ({error})")
check(i18n.error_text(error, "cs") == "Nepodařilo se spojit s Jellyfinem: timed out", "error_text() translates it")
check(i18n.message_parts(error) == (error.template, '{"error": "timed out"}'), "stored as its key and values")
missing = probe.ProbeError("The file does not exist or cannot be seen from here.")
check(i18n.message_parts(missing) == (missing.template, None), "one without values is stored as its key")
check(i18n.error_text(ValueError("plain")) == "plain" and i18n.message_parts(ValueError("plain")) == ("plain", None),
      "any other error stays as it is")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
