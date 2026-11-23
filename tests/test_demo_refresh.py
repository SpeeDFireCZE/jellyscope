# -*- coding: utf-8 -*-
r"""The demo data do not age: after a day they are made again, moved to today.

The data are generated relative to the moment of the seed and a public demo
runs for weeks. Without refreshing "the last 30 days" emptied day by day,
until a line remained of the charts. Now it is remembered when the seed ran,
and after a day the data are made again - the same shape (a fixed
`random.seed`), only relative to today. The demo's account and settings stay.

Run:
    .venv/bin/python tests/test_demo_refresh.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "1"

from jellyscope import accounts, config, db, demodata  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def count(table: str) -> int:
    return int(db.query_value(f"SELECT COUNT(*) FROM {table}"))


def newest_playback() -> datetime:
    return datetime.strptime(
        str(db.query_value("SELECT MAX(started_at) FROM playback WHERE is_active = 0")),
        db.TIME_FORMAT).replace(tzinfo=timezone.utc)


db.init_db()

print("--- the first seed ---")
first = demodata.prepare(quiet=True)
check(first["items"] > 0 and first["plays"] > 0, f"the data came about ({first})")
check(demodata.age_hours() is not None and demodata.age_hours() < 1,
      "and it is remembered when")
check(not demodata.is_stale(), "fresh data are not stale")
again = demodata.prepare(quiet=True)
check(again == {"items": 0, "plays": 0, "users": 0},
      "a second run the same day makes nothing")
plays = count("playback")
titles = count("items")
people = count("users")

print()
print("--- after three days the data are old ---")
db.set_setting("ui_language", "en")
db.set_setting(demodata.SEED_KEY,
               (datetime.now(timezone.utc) - timedelta(days=3)).strftime(db.TIME_FORMAT))
db.forget_settings()
# We pretend time passed: we move all plays three days back - exactly what
# really passing time does with them. In Python, not with a database function:
# SQLite and PostgreSQL each compute with dates differently.
shift = [((datetime.strptime(str(r["started_at"]), db.TIME_FORMAT)
           - timedelta(days=3)).strftime(db.TIME_FORMAT), r["id"])
         for r in db.query_all("SELECT id, started_at FROM playback")]
with db.connect() as conn:
    conn.executemany("UPDATE playback SET started_at = ? WHERE id = ?", shift)
    conn.commit()
before = newest_playback()
check(demodata.is_stale(), "after three days it is stale")
check((datetime.now(timezone.utc) - before) > timedelta(days=2),
      f"and the newest playback really is old ({before:%Y-%m-%d})")

refreshed = demodata.prepare(quiet=True)
check(refreshed["plays"] > 0, "a run makes the data again")
check(count("playback") == plays and count("items") == titles
      and count("users") == people,
      f"the same shape: {count('playback')} plays, {count('items')} titles, {count('users')} people")
after = newest_playback()
check((datetime.now(timezone.utc) - after) < timedelta(days=1),
      f"and the newest playback is today again ({after:%Y-%m-%d})")
check(not demodata.is_stale(), "the stamp is fresh")
check(accounts.get_by_name("demo") is not None, "the demo account survived")
check(db.get_setting("ui_language", "") == "en", "the settings survived")
check(count("accounts") == 1, "the accounts do not multiply")

print()
print("--- an old demo without a stamp is refreshed too ---")
db.set_setting(demodata.SEED_KEY, "")
db.forget_settings()
check(demodata.is_stale(), "without a stamp = stale (a demo from before the refreshing)")
check(demodata.prepare(quiet=True)["plays"] > 0, "and it is made again")

print()
print("--- in the background the demo's task watches it ---")
source = "".join(p.read_text(encoding="utf-8")
                 for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py")))
check("demodata.keep_refreshing()" in source, "web/start.py starts it in the demo mode")
check(hasattr(demodata, "keep_refreshing"), "and it exists")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
