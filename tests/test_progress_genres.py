# -*- coding: utf-8 -*-
"""The task's progress indicator and the genre breakdown in the user detail.

Two things that are unrelated, but both were added for the same reason: so
the page says what is going on, instead of only staying silent.

  * **Progress** – the sync of a big library takes minutes. We learn the total
    count in advance (Jellyfin sends it as `TotalRecordCount`), so real
    percentages can be shown, not only a spinner.
  * **Genres** – one title has more of them, so its time counts into each of
    them and the percentages do not add up to a hundred. That must be visible.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in every submodule of the scanner package

_tmp = tempfile.mkdtemp()
# An own home: without it the stored choice of the database in
# data/database.json would override DATABASE_PATH and the test would run
# against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "progress.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import charts, db, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


TOTAL = 600
snapshots: list[dict[str, Any]] = []


class FakeClient:
    """A Jellyfin that collects snapshots of the progress indicator while it is gone through."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": "u1", "Name": "Karel", "Policy": {}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib", "Name": "Filmy", "CollectionType": "movies"}]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        return TOTAL

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        for i in range(TOTAL):
            if i % 150 == 0:
                snapshots.append(scanner.progress())
            yield {
                "Id": f"film-{i:04d}", "Name": f"Film {i}", "Type": "Movie",
                "DateCreated": "2026-01-01T00:00:00.0000000Z",
                "RunTimeTicks": 60_000_000_000,
                # Jellyfin sends the genres as a list. On purpose two for every
                # movie, so it is visible that the time counts into both.
                "Genres": ["Akční", "Sci-Fi"] if i % 2 else ["Drama"],
            }


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

print("--- when nothing runs, the progress is empty ---")
check(scanner.progress() == {}, f"empty: {scanner.progress()}")


print()
print("--- the sync reports how much it has done ---")
substitute(scanner, "JellyfinClient", FakeClient)
result = asyncio.run(scanner.sync_library())
check(result.get("status") == "ok", f"it ran: {result.get('status')}")

measured = [s for s in snapshots if s]
check(len(measured) >= 3, f"the progress could be read during the run ({len(measured)}x)")
check(all(s["total"] == TOTAL for s in measured),
      f"it knows the total count in advance: {[s['total'] for s in measured]}")
check(all(s["kind"] == "library" for s in measured), "it knows which task runs")

done = [s["done"] for s in measured]
check(done == sorted(done), f"the done ones only grow: {done}")
check(done[-1] > done[0], f"and it really moves: {done}")

percents = [s["percent"] for s in measured]
check(all(0 <= p <= 100 for p in percents), f"the percentages make sense: {percents}")

check(scanner.progress() == {}, "after the end the progress is empty again")


print()
print("--- without a known whole no percentages are made up ---")
# When the count cannot be found out, the indicator must admit it, not guess.
scanner._start_progress("library", 0)
scanner._add_progress(42)
try:
    original = scanner.is_scan_running
    substitute(scanner, "is_scan_running", lambda: True)
    state = scanner.progress()
finally:
    substitute(scanner, "is_scan_running", original)
check(state["percent"] is None, f"the percentage is None, not 0: {state['percent']}")
check(state["done"] == 42, "but the done items keep counting")
scanner._clear_progress()


print()
print("--- the genres were stored and the breakdown matches ---")
stored = db.query_value(
    "SELECT COUNT(*) FROM items WHERE genres IS NOT NULL AND genres != ''")
check(stored == TOTAL, f"every title has genres ({stored})")
sample = db.query_one("SELECT genres FROM items WHERE id = 'film-0001'")
check(sample["genres"] == "Akční|Sci-Fi", f"separated by a vertical bar: {sample['genres']}")

# The user: an hour of Drama (film-0000), two hours of `Akční`+`Sci-Fi` (film-0001).
with db.connect() as conn:
    for ident, seconds in (("film-0000", 3600), ("film-0001", 7200)):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u1', 'Karel', ?, 'Film', ?, ?, ?, 0)""",
            (f"relace-{ident}", ident, db.utcnow(), db.utcnow(), seconds),
        )

genres = {row["label"]: row for row in stats.user_genres("u1", 30)}
check(set(genres) == {"Akční", "Sci-Fi", "Drama"}, f"three genres: {set(genres)}")
check(round(genres["Akční"]["hours"], 2) == 2.0, f"Akční 2 h ({genres['Akční']['hours']})")
check(round(genres["Sci-Fi"]["hours"], 2) == 2.0, "Sci-Fi 2 h too (the same movie)")
check(round(genres["Drama"]["hours"], 2) == 1.0, "Drama 1 h")
# The sum of the genre hours (5) is higher than the watched time (3) - and
# that is right, because a movie counts into each of its genres.
check(round(sum(z["hours"] for z in genres.values())) == 5,
      "the sum over genres is higher than the time - one title counts more times")
check(abs(sum(z["percent"] for z in genres.values()) - 100) < 0.01,
      "the percentages still add up to a hundred (it is a share of the sum)")


print()
print("--- the pie chart ---")
html = charts.donut_chart(list(genres.values()), "label", "hours")
check("donut-seg" in html, "the sectors were drawn")
check(html.count("<circle") == 3, f"three parts ({html.count('<circle')})")
check("Akční" in html and "40 %" in html, "the legend carries the name and the share")
check("data-tip" in html, "on hover the value is visible")
check("var(--series-" in html and "#" not in html.split("stroke=")[1][:20],
      "the colours come from variables, not hard-coded")
check("Zatím" in charts.donut_chart([], "label", "hours"),
      "empty data say there is nothing")
# Division by zero: only zeros must not bring the chart down.
check("Zatím" in charts.donut_chart([{"label": "X", "hours": 0}], "label", "hours"),
      "only zeros too")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
