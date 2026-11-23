# -*- coding: utf-8 -*-
r"""The app has its own time zone, independent of the machine.

Times used to be written out by the system's zone. A server running in UTC thus
moved the evening peak in the charts by two hours against what a person
lived through - and nothing could be done about it without touching the machine or the container.

The zone is now in Settings → General and holds for **all** outputs of time:
the history, the charts' labels and the tasks' times. An empty value means "by the
system", that is the original behaviour.

Splitting into days and hours (the daily chart, the heat map) is done by SQL - which reads
the process's zone, not our variable, and that is why the zone is set into the
environment too at the start. On Windows that is not possible and it is all right: the output of time is
right either way.

Run:
    .venv/bin/python tests/test_time_zone.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zona.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, formatting  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)

# A time stored in the database is always UTC.
STORED = "2026-08-26 08:33:26"

print("--- the same value in different zones ---")
db.set_setting("app_timezone", "UTC")
check(formatting.datetime_human(STORED) == "26.08.2026 08:33",
      f"UTC: {formatting.datetime_human(STORED)}")

db.set_setting("app_timezone", "Europe/Prague")
check(formatting.datetime_human(STORED) == "26.08.2026 10:33",
      f"Prague (summer time, +2): {formatting.datetime_human(STORED)}")

db.set_setting("app_timezone", "America/New_York")
check(formatting.datetime_human(STORED) == "26.08.2026 04:33",
      f"New York: {formatting.datetime_human(STORED)}")

print()
print("--- summer and winter time are counted apart ---")
# That is why the zone's NAME is stored, not a shift in hours: in January Prague is +1,
# in August +2. A stored shift would lie half the year.
db.set_setting("app_timezone", "Europe/Prague")
check(formatting.datetime_human("2026-01-15 08:00:00") == "15.01.2026 09:00",
      f"January +1: {formatting.datetime_human('2026-01-15 08:00:00')}")
check(formatting.datetime_human("2026-07-15 08:00:00") == "15.07.2026 10:00",
      f"July +2: {formatting.datetime_human('2026-07-15 08:00:00')}")

print()
print("--- the network chart speaks the same time as the history ---")
from datetime import datetime, timedelta, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from jellyscope import stats  # noqa: E402

start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
    hour=18, minute=0, second=0, microsecond=0)
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, bitrate,"
        " is_paused, is_active, play_method, remote_address, client, device_name)"
        " VALUES ('s1','u1','Pepa','film','Duna',?,?,?,3600,0,20000000,0,0,'DirectPlay',"
        "'10.0.0.5','Web','Chrome')",
        (start.strftime(db.TIME_FORMAT),
         (start + timedelta(hours=1)).strftime(db.TIME_FORMAT),
         (start + timedelta(hours=1)).strftime(db.TIME_FORMAT)))

for zone in ("UTC", "Europe/Prague", "America/New_York"):
    # The hour by the zone's rules, not hard-coded: Prague is +2 in summer and +1 in winter,
    # so a fixed "20" failed every winter (and New York after its change).
    expected_hour = start.astimezone(ZoneInfo(zone)).hour
    db.set_setting("app_timezone", zone)
    curve = stats.bandwidth_timeline(7, points=30)
    label = curve[0]["label"] if curve else ""
    history = formatting.datetime_human(start.strftime(db.TIME_FORMAT))
    matches = label.split()[-1].startswith(f"{expected_hour:02d}:")
    check(matches, f"{zone}: chart {label}, history {history}")

print()
print("--- an unknown zone does not fall over, it is just not used ---")
db.set_setting("app_timezone", "Neexistuje/Mesto")
check(formatting.zone() is None, "the zone is not accepted")
check(formatting.datetime_human(STORED) != "", "and the output of time still works")

print()
print("--- only a zone Python knows is saved into the settings ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

db.set_setting("app_timezone", "Europe/Prague")
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword"},
            follow_redirects=False)
client.post("/settings/language",
            data={"ui_language": "cs",
                  "app_timezone": "Neexistuje/Mesto"}, follow_redirects=False)
check(db.get_setting("app_timezone", "") == "Europe/Prague",
      "a typo does not overwrite the original value")
client.post("/settings/language",
            data={"ui_language": "cs",
                  "app_timezone": "Asia/Tokyo"}, follow_redirects=False)
check(db.get_setting("app_timezone", "") == "Asia/Tokyo", "a valid zone is saved")
client.post("/settings/language",
            data={"ui_language": "cs", "app_timezone": ""},
            follow_redirects=False)
check(db.get_setting("app_timezone", "") == "", "an empty field brings back the system's zone")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
