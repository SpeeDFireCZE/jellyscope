# -*- coding: utf-8 -*-
r"""The labels under the network chart show local time, not UTC.

The database has times in UTC and without a zone. But `datetime.timestamp()` takes such a time as **local**, so the
result was shifted by the whole offset - and the concurrent flow chart labelled its axis in UTC, while the rest of the
app wrote local time. In summer that meant the evening peak came an hour earlier by the chart than it really was.

The test runs in a fixed zone (Europe/Prague), so that the result does not depend on where it runs.

Run:
    .venv/bin/python tests/test_chart_time.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "chart_time.db")
os.environ["SECRET_KEY"] = "test-key"
# A fixed zone, so that the test says the same on every machine. `tzset` is only on unix; on Windows Python takes the
# zone from the environment only at the start, so there we rely on computing the offset from the system (below).
os.environ["TZ"] = "Europe/Prague"
if hasattr(time, "tzset"):
    time.tzset()

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

# A play that started at 18:00 UTC yesterday.
start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
    hour=18, minute=0, second=0, microsecond=0)
end = start + timedelta(hours=1)

with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
        " bitrate, is_paused, is_active, play_method, remote_address, client, device_name)"
        " VALUES ('s1','u1','Joe','movie','Dune',?,?,?,3600,0,20000000,0,0,"
        "'DirectPlay','10.0.0.5','Web','Chrome')",
        (start.strftime(db.TIME_FORMAT), end.strftime(db.TIME_FORMAT),
         end.strftime(db.TIME_FORMAT)))

local = start.astimezone()

print("--- the chart's axis is in local time ---")
print(f"       in the database (UTC): {start.strftime('%d.%m. %H:%M')}")
print(f"       local time:            {local.strftime('%d.%m. %H:%M')}")

curve = stats.bandwidth_timeline(7, points=20)
check(bool(curve), "the chart has points")
if curve:
    label = curve[0]["label"]
    print(f"       label in the chart:    {label}")
    check(label.startswith(local.strftime("%d.%m. %H:")),
          f"the first point matches local time ({label})")
    # And above all: it MUST NOT be UTC. When the zone is shifted, it must show.
    if local.utcoffset() != timedelta(0):
        check(not label.startswith(start.strftime("%d.%m. %H:")),
              "and it is not UTC")
    else:
        print("       (a zone without a shift - the difference would not show)")

print()
print("--- the same holds for the peak's time above the chart ---")
summary = stats.bandwidth_summary(7)
check(summary["peak_at"].startswith(local.strftime("%d.%m. %H:")),
      f"the peak in local time ({summary['peak_at']})")

print()
print("--- the conversion itself ---")
# Without a zone the time would be taken as local and the timestamp would be shifted.
seconds = stats._seconds("2026-08-25 18:00:00")
expected = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc).timestamp()
check(seconds == expected, f"a time from the database reads as UTC ({seconds} vs {expected})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
