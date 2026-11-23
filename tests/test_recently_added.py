# -*- coding: utf-8 -*-
r"""One big series must not push everything else out of "Recently added".

What happened: Jellyfin misidentified a series and added every episode as a title of its own. After the metadata
were fixed, all the episodes were written anew, so they got today's date added.

The list took a fixed count of the newest ROWS and only then grouped them by series. Two hundred episodes with
today's date filled that cap on their own, so only one series was left in "Recently added" and nothing else - and
it never came back, because those dates stayed the newest.

Run:
    .venv/bin/python tests/test_recently_added.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "recently_added.db")
os.environ["SECRET_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def hours_ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).strftime(db.TIME_FORMAT)


def write(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


write("INSERT INTO libraries (id, name, collection_type)"
      " VALUES ('lib','Library','movies')")


def movie(item_id: str, name: str, added: str) -> None:
    write(
        """INSERT INTO items (id, name, type, library_id, date_created, is_missing)
           VALUES (?,?,'Movie','lib',?,0)""",
        (item_id, name, added),
    )


def episode(item_id: str, number: int, added: str, series: str = "series-1") -> None:
    write(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              is_missing)
           VALUES (?,?,'Episode','lib',?,'Big series',1,?,?,0)""",
        (item_id, f"Episode {number}", series, number, added),
    )


print("--- a series of two hundred episodes, added all at once ---")
# Exactly what Jellyfin does after fixing a misidentified series.
for number in range(200):
    episode(f"episode-{number}", number + 1, hours_ago(1))

# And five movies that came earlier. They ARE to stay in the list - they are the last additions before that series.
for number in range(5):
    movie(f"movie-{number}", f"Movie {number}", hours_ago(24 + number))

listing = stats.recently_added(limit=18)
titles = [row["title"] for row in listing]
check(len(listing) > 1, f"the list holds more than the one series ({len(listing)} items)")
check("Big series" in titles, "the series is there - it came last")
check(all(f"Movie {i}" in titles for i in range(5)),
      f"and the movies did not vanish from it ({titles})")

print()
print("--- the series is one row, not two hundred ---")
check(titles.count("Big series") == 1, "the series is listed once")
series = next(r for r in listing if r["title"] == "Big series")
check(len(series["episodes"]) == 200,
      f"and carries the whole batch of episodes ({len(series['episodes'])})")

print()
print("--- the order stays by the date added ---")
check(titles[0] == "Big series", "the newest is first")
check(titles[1] == "Movie 0", f"then the newest movie ({titles[1]})")

print()
print("--- the limit holds for groups, not rows ---")
for number in range(30):
    movie(f"more-{number}", f"More {number}", hours_ago(48 + number))
check(len(stats.recently_added(limit=18)) == 18,
      "exactly as many groups are listed as were asked for")
check(len(stats.recently_added(limit=3)) == 3, "and a smaller limit fits too")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
