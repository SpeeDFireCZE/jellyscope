# -*- coding: utf-8 -*-
r"""Tidying the history: what is deleted, what must not be deleted and what it says in advance.

Deleting data is the only operation that cannot be taken back, so this test
watches mainly what must NOT happen:

* **Without switching on nothing is deleted.** Not even when the threshold is set and the data
  are old. Whoever did not ask for tidying must not lose anything just by
  updating.
* **A session running right now stays.** It belongs to the collector, who has it in the making;
  deleting it means creating it again within ten seconds, only without its start.
* **The number in the settings matches what really gets deleted.** A preview that
  shows a thousand fewer is worse than none.
* **Forgetting a viewer concerns only them.** It touches nobody else.

Run:
    .venv/bin/python tests/test_purge.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "odklizeni.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, purge, tasks  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def session(number: int, days_ago: int, user: str = "u1",
            active: int = 0) -> None:
    start = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, started_at, last_seen_at, watched_seconds,"
            " paused_seconds, is_paused, is_active)"
            " VALUES (?,?,?,'film','Duna',?,?,3600,0,0,?)",
            (f"s{number}", user, f"Divák {user}",
             start.strftime(db.TIME_FORMAT),
             start.strftime(db.TIME_FORMAT), active))
        conn.commit()


def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM playback"))


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

print("--- the default state: off ---")
# This is the most important property of the whole feature.
check(purge.enabled() is False, "tidying is off after installation")
check(purge.retention_days() == 365, f"and the threshold is a year ({purge.retention_days()} days)")
check(db.get_setting("task_purge_enabled", "?") == "0",
      "the task itself is off too")

# Ten sessions: five old, four fresh, one old but LIVE.
for number in range(1, 6):
    session(number, days_ago=400)
for number in range(6, 10):
    session(number, days_ago=10)
session(10, days_ago=500, active=1)
check(count() == 10, f"the database has ten sessions ({count()})")

print()
print("--- the preview says what would go ---")
overview = purge.overview()
check(overview["total"] == 10, f"it counts the whole history ({overview['total']})")
check(overview["leaving"] == 5,
      f"and that with a one-year threshold five would go ({overview['leaving']})")

# Not vacuous: another threshold must give another number.
check(purge.overview(days=5)["leaving"] == 9,
      f"with a five-day threshold nine ({purge.overview(days=5)['leaving']})")
check(purge.overview(days=3650)["leaving"] == 0,
      "and with a ten-year one nothing")

print()
print("--- tidying switched off does not delete ---")
result = asyncio.run(tasks.TASKS["purge"].runner())
check(count() == 10, f"the task deleted nothing with tidying switched off ({count()})")
check(result.get("deleted") == 0, "and says so in the result too")

print()
print("--- tidying switched on deletes, but only the old and finished ---")
db.set_setting("task_purge_enabled", "1")
db.forget_settings()
check(purge.enabled() is True, "tidying is on")

result = asyncio.run(tasks.TASKS["purge"].runner())
check(result["deleted"] == 5, f"it deleted five sessions ({result['deleted']})")
check(count() == 5, f"and five were left in the database ({count()})")

live = db.query_one("SELECT COUNT(*) AS count FROM playback WHERE is_active = 1")
check(int(live["count"]) == 1,
      "a LIVE session half a year old stayed - it belongs to the collector")

# The second run has nothing to do any more.
check(asyncio.run(tasks.TASKS["purge"].runner())["deleted"] == 0,
      "the second run has nothing to delete")

print()
print("--- forgetting a viewer ---")
for number in range(20, 23):
    session(number, days_ago=10, user="u2")
check(count() == 8, f"a second viewer was added ({count()} sessions)")

with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u2', 'Karel')")
    conn.commit()

result = purge.forget_user("u2")
check(result["deleted"] == 3, f"their three sessions were deleted ({result['deleted']})")
check(result["name"] == "Karel", "and the message knows their name")
check(count() == 5, f"the others were not touched ({count()})")
check(db.query_one("SELECT name FROM users WHERE id = 'u2'") is not None,
      "the account stays - that is in Jellyfin, not with us")
check(purge.forget_user("u2")["deleted"] == 0,
      "the second time there is nothing to forget")
check(purge.forget_user("")["deleted"] == 0,
      "an empty id deletes nothing")

print()
print("--- the offer holds only viewers of whom something is left ---")
listing = purge.viewers()
check([r["user_id"] for r in listing] == ["u1"],
      f"a single viewer was left ({[r['user_id'] for r in listing]})")

print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/settings?section=tasks")
    check(page.status_code == 200, "Tasks load")
    html = page.text
    check('name="history_retention_days"' in html, "there is the field with the threshold")
    check("Odklízení historie" in html, "and the group's heading")
    check('action="/settings/history/forget"' in html,
          "and the form for forgetting a viewer")

    # Saving the threshold.
    client.post("/settings/tasks", follow_redirects=False, data={
        "backup_path": "", "backup_keep": "7", "pg_dump_path": "",
        "history_retention_days": "90"})
    db.forget_settings()
    check(purge.retention_days() == 90,
          f"the saved threshold holds ({purge.retention_days()})")

    # Nonsense is evened into bounds, not saved.
    client.post("/settings/tasks", follow_redirects=False, data={
        "backup_path": "", "backup_keep": "7", "pg_dump_path": "",
        "history_retention_days": "2"})
    db.forget_settings()
    check(purge.retention_days() == purge.MIN_DAYS,
          f"two days are evened to {purge.MIN_DAYS} ({purge.retention_days()})")

    # Forgetting through the page.
    before = count()
    response = client.post("/settings/history/forget", data={"user_id": "u1"},
                           follow_redirects=False)
    check(response.status_code == 303, "forgetting redirects back")
    check(count() == 0, f"and that viewer's history is gone ({before} -> {count()})")

print()
print("--- in the demo mode nothing is deleted ---")
# The demo runs at a public address and the sign-in details are in the sign-in
# window - anybody gets in. The lock is one middleware that lets
# only the paths from the list of exceptions through; a new route falls into it by itself, so
# let it stay that way. (That the lock really stops is watched by
# test_demo_lock.py - it switches the demo mode on before importing
# the app, because the configuration is read exactly then.)
from jellyscope import web  # noqa: E402

check("/settings/history/forget" not in web.DEMO_ALLOWED,
      "forgetting a viewer is not among the exceptions the demo lets through")
check("/settings/tasks" not in web.DEMO_ALLOWED,
      "nor saving the tasks")
check(web.DEMO_ALLOWED == frozenset({"/login", "/logout"}),
      f"the exceptions are only signing in and out ({sorted(web.DEMO_ALLOWED)})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
