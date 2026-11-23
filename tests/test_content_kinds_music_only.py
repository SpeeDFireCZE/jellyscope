# -*- coding: utf-8 -*-
r"""A server with music and no movies or series: growth and the daily snapshot.

Final review of 16. 11. 2026: the backfill stopped where `items` had nothing and the daily snapshot needed video
items - a music-only server had an empty growth curve while "Total size" on the same page showed its music.

Run:
    .venv/bin/python tests/test_content_kinds_music_only.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "content-kinds-music-only.db")
os.environ["SECRET_KEY"] = "test-key-content-kinds-music-only"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"

from jellyscope import db, scanner, stats  # noqa: E402

failures = 0
MB = 1024 ** 2
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


with db.connect() as conn:
    for number, back in ((1, 20), (2, 20), (3, 5)):
        added = (datetime.now(UTC) - timedelta(days=back)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO music_track (id, name, path, size_bytes, added_at, synced_at, missing)"
                     " VALUES (?, ?, ?, ?, ?, ?, 0)", (f"t{number}", f"t{number}", f"/music/{number}.flac", 10 * MB,
                                                       added, db.utcnow()))
    conn.commit()

curve = stats.snapshots(30)
check(len(curve) >= 20 and curve[-1]["size_bytes"] == 30 * MB == stats.tech_coverage()["size_bytes"],
      f"the backfill draws the music ({len(curve)} days, last {curve[-1]['size_bytes'] // MB if curve else None} MB)")
check(curve and curve[-10]["size_bytes"] == 20 * MB, "with the track added 5 days ago coming later")
snapshot = scanner.write_snapshot()
check(snapshot is not None and snapshot["size_bytes"] == 30 * MB and snapshot["item_count"] == 0,
      "the daily snapshot is written without a single movie")
growth = stats.library_growth(30)
check(growth.get("size") == 30 * MB and growth.get("increase") == 10 * MB,
      f"growth: the size and what came in the period ({growth.get('size')}, {growth.get('increase')})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
