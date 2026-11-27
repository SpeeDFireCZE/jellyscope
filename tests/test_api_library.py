# -*- coding: utf-8 -*-
"""Languages and disks in the API: /api/v1/languages and /api/v1/disks.

Over the demo's made-up library: the shapes and raw numbers (seconds, bytes, shares 0-1, the day a disk runs full),
the same numbers the Languages page and the Disks card show, and the rights (languages; library).

Run:
    .venv/bin/python tests/test_api_library.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api_library.db")
os.environ["SECRET_KEY"] = "api-library-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import api, db, demodata, langstats, scanner, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


db.init_db()
demodata.seed()
db.set_setting(scanner.CAPACITY_KEY, str(1024 ** 4))      # what the Disks card's form stores for 1 TB
db.forget_settings()
client = TestClient(web.app)


def ask(token: str, path: str) -> tuple[int, dict]:
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    return response.status_code, response.json()


everything = api.create_token("everything")["token"]

print("--- /api/v1/languages ---")
status, data = ask(everything, "/api/v1/languages?days=30")
page = langstats.watched_languages(30, langstats.colour_map())
check(status == 200 and set(data) == {"days", "known_share", "watched_seconds", "audio", "subtitles"},
      "the fields", f"({status} {sorted(data)})")
audio = data.get("audio") or []
check(audio and all(set(r) == {"language", "name", "plays", "watched_seconds", "viewers", "titles", "share"} for r in audio)
      and [r["language"] for r in audio] == [r["code"] for r in page["rows"]]
      and all(isinstance(r["watched_seconds"], int) and 0 <= r["share"] <= 1 for r in audio),
      "audio languages: the page's rows in seconds and shares 0-1", f"({audio[:1]})")
check(abs(data["known_share"] - page["known_share"] / 100) < 1e-6 and 0 < data["known_share"] <= 1, "known_share is a share")
subtitles = data.get("subtitles") or {}
check(set(subtitles) == {"with_seconds", "without_seconds", "share", "languages"} and subtitles["languages"]
      and all(set(r) == {"language", "name", "plays", "watched_seconds"} for r in subtitles["languages"]),
      "subtitles: with and without, and their languages", f"({subtitles})")

print("--- /api/v1/disks ---")
status, data = ask(everything, "/api/v1/disks?days=30")
card = stats.disks_overview(30, stats.library_growth, stats.disk_growth)["disks"]
disks = data.get("disks") or []
check(status == 200 and len(disks) == len(card) and all(set(d) == {
    "id", "name", "kind", "capacity_bytes", "free_bytes", "library_bytes", "outside_bytes", "free_share",
    "daily_growth_bytes", "days_left", "full_at"} for d in disks), "the fields of every disk", f"({status} {disks[:1]})")
disk = disks[0] if disks else {}
check(disk.get("capacity_bytes") == 1024 ** 4 and disk.get("free_bytes") == card[0]["free"]
      and 0 < disk.get("free_share", 0) < 1 and isinstance(disk.get("daily_growth_bytes"), int),
      "the capacity, free space and growth as the Disks card", f"({disk})")
check(disk.get("days_left") == card[0]["days_left"] and disk.get("full_at") == (date.today() + timedelta(days=card[0]["days_left"])).isoformat(),
      "the day the disk runs full = today + days left", f"({disk.get('days_left')} {disk.get('full_at')})")
db.set_setting(scanner.CAPACITY_KEY, "")
db.forget_settings()
status, data = ask(everything, "/api/v1/disks")
check(status == 200 and data["disks"] and data["disks"][0]["capacity_bytes"] is None and data["disks"][0]["full_at"] is None,
      "without a known capacity: null, not a made-up number")

# A quiet archive disk: 4 TB free, a megabyte a month - the day it runs full is past any calendar (code review 27. 11.).
real_overview = stats.disks_overview
stats.disks_overview = lambda *a, **k: {"disks": [{**real_overview(*a, **k)["disks"][0], "days_left": 120_000_000}]}
status, data = ask(everything, "/api/v1/disks")
stats.disks_overview = real_overview
check(status == 200 and data["disks"][0]["days_left"] == 120_000_000 and data["disks"][0]["full_at"] is None,
      "a disk that will not run full in any calendar: days left, no date, never a 500", f"({status} {str(data)[:160]})")

print("--- language names are English, whatever the server's language (an integration must not break with it) ---")
db.set_setting("ui_language", "cs")
db.forget_settings()
status, data = ask(everything, "/api/v1/languages?days=30")
names = {r["language"]: r["name"] for r in data["audio"] + data["subtitles"]["languages"]}
check(names.get("cs") == "Czech" and "Čeština" not in str(data) and "Neuvedeno" not in str(data),
      "Czech is Czech, not Čeština; unknown is not Neuvedeno", f"({names})")
db.set_setting("ui_language", "")
db.forget_settings()

print("--- the rights ---")
only_library = api.create_token("library", permissions=api.pack_key_permissions({"library"}, False, False))["token"]
only_languages = api.create_token("languages", permissions=api.pack_key_permissions({"languages"}, False, False))["token"]
check(ask(only_library, "/api/v1/disks")[0] == 200 and ask(only_library, "/api/v1/languages")[0] == 403,
      "library opens the disks, not the languages")
check(ask(only_languages, "/api/v1/languages")[0] == 200 and ask(only_languages, "/api/v1/disks")[0] == 403,
      "languages open the languages, not the disks")
for path in ("/api/v1/languages?days=0", "/api/v1/disks?unknown=1"):
    status, data = ask(everything, path)
    check(status == 400 and data.get("error"), f"{path} is a 400 with an error code", f"({status} {data})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
