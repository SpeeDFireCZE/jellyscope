# -*- coding: utf-8 -*-
r"""Something always runs in the demo: the made-up stream is kept alive and plays on.

The "Právě se hraje" card is the first thing a demo visitor sees. The seed puts
one playback into it, but it stood still - the position from the moment of the
seed, "last seen" too. It was enough for anything to close it (a restart with
the collector over the same database), and the card reported that nobody watches.

Now the demo's task revives it every few seconds and moves the position by the
time that passed. At the end of the movie it starts from the beginning again.

Run:
    .venv/bin/python tests/test_demo_live_stream.py
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

from jellyscope import collector, config, db, demodata, stats  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def running() -> list[dict]:
    return [r for r in stats.active_sessions() if r["session_key"] == demodata.LIVE_KEY]


db.init_db()
demodata.prepare(quiet=True)

print("--- after the seed the stream runs ---")
check(len(running()) == 1, "the made-up stream is in Právě se hraje")

print()
print("--- a closed stream comes back to life ---")
# That is what the collector does at start: it closes what was left hanging.
collector.close_orphans()
check(running() == [], "a closed one vanishes from the card (the bug's starting state)")
now = datetime(2026, 10, 17, 18, 0, 0, tzinfo=timezone.utc)
demodata.keep_live(now)
rows = running()
check(len(rows) == 1, "keep_live returns it to the card")
check(bool(rows) and rows[0]["last_seen_at"] == now.strftime(db.TIME_FORMAT)
      and rows[0]["ended_at"] is None, "last seen now, without an end")

print()
print("--- it plays on ---")
before = rows[0] if rows else {}
demodata.keep_live(now + timedelta(seconds=30))
after = (running() or [{}])[0]
shift = (after.get("position_seconds") or 0) - (before.get("position_seconds") or 0)
check(29 <= shift <= 31, f"the position moves by the time that passed ({shift:.1f} s)")
check((after.get("watched_seconds") or 0) - (before.get("watched_seconds") or 0) == 30,
      "the watched time too")
check(after.get("progress") is not None and 0 < after["progress"] < 100,
      f"the progress indicator is visible ({after.get('progress')} %)")

print()
print("--- at the end of the movie it starts again ---")
length = after.get("runtime_seconds") or 0
check(length > 0, f"the movie has a length ({length:.0f} s)")
for minutes in (90, 160, 400, 1000):
    demodata.keep_live(now + timedelta(minutes=minutes))
    row = (running() or [{}])[0]
    position = row.get("position_seconds") or 0
    check(0 < position < length and row.get("watched_seconds", 0) <= length,
          f"after {minutes} min the position is inside the movie ({position:.0f} s of {length:.0f})")

print()
print("--- the live flow sees it ---")
check(stats.flow_now()["stream_count"] == 1, "Právě teče counts one stream")

print()
print("--- in the background the demo's task watches it ---")
source = "".join(p.read_text(encoding="utf-8")
                 for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py")))
check("demodata.keep_live_forever()" in source, "web/start.py starts it in the demo mode")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
