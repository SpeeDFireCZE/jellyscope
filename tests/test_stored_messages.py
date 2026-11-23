# -*- coding: utf-8 -*-
r"""A stored message is a key and its values, translated for whoever reads it.

The task log, the collector's error and an item's analysis error are written once and read later by
people with different languages. A finished sentence would stay in the language of the moment it was
written; the English key with its values apart (`i18n.Message`, JSON) is translated when shown
(`i18n.stored_text`). An old row - a finished sentence - shows as it was written.

Run:
    .venv/bin/python tests/test_stored_messages.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
HOME = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=HOME, DATABASE_PATH=str(Path(HOME) / "t.db"), SECRET_KEY="stored-messages",
                  JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="x", JELLYSCOPE_DEMO="0")

from jellyscope import i18n, jellyfin  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- a message: a key and its values ---")
m = i18n.Message("Cleared out {n} playbacks older than {days} days.", n=3, days=30)
check(str(m) == "Cleared out 3 playbacks older than 30 days.", f"str() is the English sentence ({m})")
check(m.translated("cs") == "Odklizena 3 přehrávání starší než 30 dní.", f"translated() picks the Czech form ({m.translated('cs')})")
template, values = i18n.message_parts(m)
check(template == m.template and json.loads(values) == {"n": 3, "days": 30}, f"stored as the key and JSON ({values})")
check(i18n.stored_text(template, values, "cs") == m.translated("cs"), "and read back in a language")
check(i18n.stored_text(template, values, "en") == str(m), "and in English")

print()
print("--- without values, an old row ---")
check(i18n.stored_text("Another task is already running.", None, "cs") == "Jiná úloha už běží.", "a key without values is translated")
check(i18n.stored_text("Odklizeno 5 přehrávání.", None, "en") == "Odklizeno 5 přehrávání.", "an old finished row shows as written")
check(i18n.stored_text(None, None, "cs") == "" and i18n.stored_text("", None, "cs") == "", "nothing stored, nothing shown")

print()
print("--- a message inside a message ---")
inner = jellyfin.JellyfinError("Could not connect to Jellyfin: {error}", error="timed out")
outer = i18n.Message("Nothing is deleted without a backup: {reason}", reason=inner)
check(i18n.stored_text(*i18n.message_parts(outer), "cs") == "Bez zálohy se nemaže: Nepodařilo se spojit s Jellyfinem: timed out",
      f"the inner error is translated into the same language ({i18n.stored_text(*i18n.message_parts(outer), 'cs')})")
check(i18n.translate_with("Nothing is deleted without a backup: {reason}", "cs", reason=inner)
      == "Bez zálohy se nemaže: Nepodařilo se spojit s Jellyfinem: timed out", "translate_with does the same with an error value")
check(str(outer) == "Nothing is deleted without a backup: Could not connect to Jellyfin: timed out", "English all the way in str()")

print()
print("--- what JSON cannot hold, what does not fit ---")
odd = i18n.message_parts(i18n.Message("The backup failed: {error}", error=OSError("disk full")))
check(json.loads(odd[1]) == {"error": "disk full"}, f"a value JSON cannot hold is stored as text ({odd[1]})")
path = i18n.message_parts(i18n.Message("The folder cannot be created: {error}", error=Path("/data/x")))
check(json.loads(path[1]) == {"error": "/data/x"}, "a path too")
flag = i18n.message_parts(i18n.Message("{n} records", n=True))
check(json.loads(flag[1]) == {"n": True}, "a bool stays a bool, not a number")
check(i18n.message_parts(ValueError("plain")) == ("plain", None), "any other error: its text, no values")
long_values = json.loads(i18n.message_parts(i18n.Message("The backup failed: {error}", error=OSError("x" * 3000)))[1])
check(len(long_values["error"]) == i18n.MAX_VALUE_CHARS, f"a long value is cut like the template ({len(long_values['error'])})")
nested = i18n.Message("Nothing is deleted without a backup: {reason}",
                      reason=jellyfin.JellyfinError("Could not connect to Jellyfin: {error}", error="y" * 3000))
nested_values = json.loads(i18n.message_parts(nested)[1])
check(len(nested_values["reason"]["values"]["error"]) == i18n.MAX_VALUE_CHARS, "a nested one too")
check(i18n.message_parts("The file is empty.") == ("The file is empty.", None), "a text: itself")
key_only = jellyfin.JellyfinError("The API key has no permission for this (403).")
check(i18n.message_parts(key_only) == ("The API key has no permission for this (403).", None), "an error without values: its key")
check(i18n.stored_text("The backup failed: {error}", '{"wrong": 1}', "cs") == "The backup failed: {error}",
      "a placeholder that does not fit shows the template, no exception")
check(i18n.stored_text("{n} records", '{"n": 1}', "cs") == "1 záznam", "a count picks its form from a stored number")
check(i18n.stored_text("{n} records", '{"n": 5}', "cs") == "5 záznamů", "and another form")
check(i18n.stored_text("The file is empty.", "not json", "cs") == "Soubor je prázdný.", "unreadable values: the template alone")
check(i18n.stored_text("A key no longer in en.json: {x}", '{"x": 5}', "cs") == "A key no longer in en.json: 5",
      "an unknown template is filled in English")

print()
print("--- in templates ---")
from jinja2 import Environment  # noqa: E402

env = Environment()
i18n.register(env)
check("stored_text" in env.globals, "templates have stored_text()")

print()
print("--- the collector's error and an item's analysis error, for each reader ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, collector, db, notifications, probe, scanner, web  # noqa: E402

db.init_db()
db.set_setting("ui_language", "cs")
for name, language in (("eng_reader", "en"), ("cz_reader", "cs")):
    accounts.create(name, "stored-messages-pw", is_admin=True)
    accounts.set_language(accounts.get_by_name(name)["id"], language)


def page(user: str, address: str) -> str:
    client = TestClient(web.app)
    client.post("/login", data={"username": user, "password": "stored-messages-pw", "method": "local"})
    return client.get(address).text


collector.store_error(jellyfin.JellyfinError("Could not connect to Jellyfin: {error}", error="refused"))
check(db.get_setting(collector.ERROR_KEY) == "Could not connect to Jellyfin: {error}"
      and json.loads(db.get_setting(collector.ERROR_VALUES_KEY)) == {"error": "refused"},
      "the collector's error is stored as its key and values")
check("Nepodařilo se spojit s Jellyfinem: refused" in page("cz_reader", "/"), "the side panel shows it in Czech to the Czech account")
check("Could not connect to Jellyfin: refused" in page("eng_reader", "/"), "and in English to the English one")
db.set_setting(collector.LAST_POLL_KEY, "2026-01-01 00:00:00")
state = notifications._collector_state()
check(state["reason"] == "Nepodařilo se spojit s Jellyfinem: refused", f"a notification has it in the server's language ({state['reason']!r})")
collector.clear_error()
check(db.get_setting(collector.ERROR_KEY) == "" and db.get_setting(collector.ERROR_VALUES_KEY) == "", "a good poll clears both")

with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at) VALUES ('pol-1', 'Dune', 'Movie', 0, ?)",
                 (db.utcnow(),))
scanner._save_tech_error("pol-1", probe.ProbeError("ffprobe did not answer within {seconds} s.", seconds=60))
row = db.query_one("SELECT tech_error, tech_error_values FROM items WHERE id = 'pol-1'")
check(row["tech_error"] == "ffprobe did not answer within {seconds} s." and json.loads(row["tech_error_values"]) == {"seconds": 60},
      "an analysis error is stored as its key and values")
check("ffprobe neodpověděl do 60 s." in page("cz_reader", "/item/pol-1"), "the item's page shows it in Czech")
check("ffprobe did not answer within 60 s." in page("eng_reader", "/item/pol-1"), "and in English")
scanner._save_tech("pol-1", {"container": "mkv"})
row = db.query_one("SELECT tech_error, tech_error_values FROM items WHERE id = 'pol-1'")
check(row["tech_error"] is None and row["tech_error_values"] is None, "a good analysis clears both")

print()
print("--- an API key without a name ---")
from jellyscope import api  # noqa: E402

unnamed = api.create_token("", None)
named = api.create_token("Home Assistant", None)
check(db.query_one("SELECT name FROM api_tokens WHERE id = ?", (unnamed["id"],))["name"] == "",
      "a key without a name is stored without one (no language in the data)")
cz_api, en_api = page("cz_reader", "/settings?section=api"), page("eng_reader", "/settings?section=api")
check(">bez názvu<" in cz_api and ">unnamed<" in en_api, "Settings → API shows it in the reader's language")
check(">Home Assistant<" in cz_api and ">Home Assistant<" in en_api, "a key with a name shows its name")
check(api.revoke_token(unnamed["id"]) == i18n.translate("unnamed"), "revoking it names it in the reader's language")
check(api.revoke_token(unnamed["id"]) == "", "a key that is gone has no name (nothing revoked)")

print()
print("--- a translation whose placeholders do not fit (a typo from Weblate) ---")
KEY = "The folder cannot be created: {error}"
saved = i18n.TRANSLATIONS["cs"][KEY]
i18n.TRANSLATIONS["cs"][KEY] = "Složku nelze vytvořit: {chyba}"   # a placeholder that does not fit, on purpose
try:
    broken = i18n.Message(KEY, error="disk full").translated("cs")
    check(broken == "The folder cannot be created: disk full", f"Message.translated() falls back to English ({broken!r})")
    failed = jellyfin.JellyfinError(KEY, error="disk full")
    check(i18n.error_text(failed, "cs") == "The folder cannot be created: disk full", "and an error's translated() too")
finally:
    i18n.TRANSLATIONS["cs"][KEY] = saved

print()
print("--- the error of an *Arr app in the task log ---")
import asyncio  # noqa: E402
import dataclasses  # noqa: E402

from jellyscope import tasks  # noqa: E402
from jellyscope.arr import registry  # noqa: E402
from jellyscope.services import ServiceError  # noqa: E402


async def failing_sync() -> dict:
    raise ServiceError("Could not connect to Jellyfin: {error}", error="refused")

saved_apps, saved_connected = dict(registry.APPS), registry.connected
registry.APPS["sonarr"] = dataclasses.replace(registry.APPS["sonarr"], synchronize=failing_sync)
registry.connected = lambda: ["sonarr"]
try:
    results = asyncio.run(registry.synchronize_all())
    check(isinstance(results["sonarr"]["error"], ServiceError), "the app's error is handed on as an error, not a sentence")
    asyncio.run(i18n.without_account_language(tasks.run_now("arr")))
finally:
    registry.APPS.clear(); registry.APPS.update(saved_apps); registry.connected = saved_connected
row = scanner.last_scan("arr")
check(i18n.stored_text(row["message"], row["message_values"], "en") == "Sonarr: error - Could not connect to Jellyfin: refused",
      f"an English reader reads it in English ({i18n.stored_text(row['message'], row['message_values'], 'en')!r})")
check(i18n.stored_text(row["message"], row["message_values"], "cs").endswith("Nepodařilo se spojit s Jellyfinem: refused"),
      "a Czech one in Czech")

print()
print("--- the detailed map's error ---")
from jellyscope import detailed_map  # noqa: E402


class _Response:
    def raise_for_status(self) -> None:
        pass

    async def aiter_bytes(self, size: int):
        for _ in range(3):
            yield b"x" * 10


class _Stream:
    async def __aenter__(self):
        return _Response()

    async def __aexit__(self, *args) -> None:
        pass


class _Client:
    def stream(self, method: str, url: str) -> _Stream:
        return _Stream()


saved_max = detailed_map.MAX_BYTES
detailed_map.MAX_BYTES = 15
try:
    result = asyncio.run(detailed_map.download_and_process(client=_Client()))
finally:
    detailed_map.MAX_BYTES = saved_max
check(result["status"] == "error" and isinstance(result.get("error"), i18n.TranslatableError),
      f"a file over the ceiling is an error with a key ({result})")
scan_id = scanner.start_task_log("map")
scanner.finish_task_log(scan_id, "error", message=result["error"])
reason = detailed_map.last_error
token = i18n.set_account_language("en")
try:
    english = (reason() or {}).get("reason", "")
finally:
    i18n.reset_account_language(token)
check("is larger than" in english, f"the map card reads it in the reader's language ({english!r})")

print()
print("--- an unnamed key in the log and in the API log's filter ---")
client = TestClient(web.app)
client.post("/login", data={"username": "cz_reader", "password": "stored-messages-pw", "method": "local"})
from jellyscope import applog  # noqa: E402

applog.setup()
client.post("/settings/api/token", data={"name": "", "mode": "all"})
for handler in __import__("logging").getLogger().handlers:
    handler.flush()
written = applog.log_path().read_text(encoding="utf-8") if applog.log_path() else ""
check("created root key" in written and "bez názvu" not in written, "the log file names it without the reader's word")
api_log_page = client.get("/settings?section=log&log_kind=api").text
check(re.search(r"<option value=\"\d+\"[^>]*>\s*bez názvu \(", api_log_page) is not None,
      "the API log's key filter shows it in the reader's language")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
