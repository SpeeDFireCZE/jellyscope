# -*- coding: utf-8 -*-
r"""The library's daily snapshot and growth over time.

Otherwise Jellyscope remembers only the CURRENT state of the library; only
the playback has a history. One row per day (the size, the number of items, how many are
4K, how many without metadata) opens questions there was nothing to answer from
so far: how the library grows, what was added in a period and whether the transcode
got better after an intervention.

What it stands on:

* **It is written after a successful sync**, not from a task of its own - after
  a stopped or failed one the snapshot would catch half of the library and in the chart
  it would become a drop that did not happen.
* **One row per day**, the same day is overwritten: the last known state
  of the day holds.
* **The estimate has two forms** - "the space runs out in X days" where we see
  the files, otherwise "in a year it grows to X". Promising the first where only
  the second holds would be worse than silence.

Run:
    .venv/bin/python tests/test_library_snapshot.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "snimek.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import cards, content_kinds, db, demodata, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def snapshot(day: str, size: int, items: int = 100,
             free: int | None = None) -> None:
    """Slips in a snapshot for the given day - for computing the growth."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO library_snapshot (day, item_count, movie_count, episode_count,"
            " size_bytes, uhd, hdr, without_tech, free_space, written_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (day) DO UPDATE SET size_bytes = excluded.size_bytes,"
            " item_count = excluded.item_count, free_space = excluded.free_space",
            (day, items, items, 0, size, 0, 0, 0, free, db.utcnow()))
        conn.commit()


db.init_db()
demodata.seed()

print("--- writing the snapshot ---")
written = scanner.write_snapshot()
check(written is not None, "the snapshot is written")
check(written["item_count"] > 0 and written["size_bytes"] > 0,
      f"and it carries real numbers ({written['item_count']} items)")

# The numbers must match what is in the library - otherwise the growth chart would draw
# something other than the rest of the application.
actual = db.query_one(
    "SELECT COUNT(*) AS item_count, COALESCE(SUM(COALESCE(size_bytes, 0)), 0) AS size_bytes"
    " FROM items WHERE is_missing = 0")
check(written["item_count"] == actual["item_count"], "the number of items matches the library")
# Since 16. 11. 2026 the size is the whole library - movies and episodes and music
# (content_kinds.py); the number of items stays video.
music = sum(kind["size"] for kind in content_kinds.totals().values())   # music and books (the demo has both)
check(music > 0 and written["size_bytes"] == actual["size_bytes"] + music, "the size too (with music)")

coverage = stats.tech_coverage()
check(written["without_tech"] == coverage["missing"],
      "and the number of items without technical data matches the Insights")

# The same day is overwritten, not doubled: the last known state of the day holds.
scanner.write_snapshot()
scanner.write_snapshot()
check(db.query_value("SELECT COUNT(*) FROM library_snapshot") == 1,
      "a repeated write of the same day overwrites the row, does not add one")

print()
print("--- the past is filled in from the creation date ---")
# Snapshots start only with this version, but the past is carried by `date_created`
# and for items in the archive by `synced_at`. So the curve reaches before the first snapshot too.
today = date.today()
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()

year_ago = (today - timedelta(days=365)).isoformat()
half_year_ago = (today - timedelta(days=180)).isoformat()
with db.connect() as conn:
    conn.execute("UPDATE items SET date_created = ?", (year_ago + " 12:00:00",))
    conn.execute("UPDATE items SET date_created = ? WHERE id IN"
                 " (SELECT id FROM items LIMIT 5)", (half_year_ago + " 12:00:00",))
    conn.commit()

curve = stats.snapshots(400)
check(len(curve) > 300, f"the curve reaches back over a year ({len(curve)} days)")
check(all(row["backfilled"] for row in curve),
      "and without a single snapshot it is all filled in")
# Five items were added only half a year ago - until then there must be fewer of them.
older = next(r for r in curve if r["day"] == (today - timedelta(days=200)).isoformat())
newer = next(r for r in curve if r["day"] == (today - timedelta(days=100)).isoformat())
check(newer["item_count"] == older["item_count"] + 5,
      f"and the increase matches ({older['item_count']} → {newer['item_count']})")

# THE MOST IMPORTANT INVARIANT: the curve's last point must equal what
# "Velikost celkem" reports on the same page. When they differ, the chart
# contradicts the number right next to it - and exactly that happened: items
# in the archive missing `synced_at` never left the curve
# and kept it higher forever.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1, synced_at = NULL"
                 " WHERE id IN (SELECT id FROM items LIMIT 2)")
    conn.execute("UPDATE items SET is_missing = 1, synced_at = ''"
                 " WHERE id IN (SELECT id FROM items WHERE is_missing = 0 LIMIT 2)")
    conn.commit()

# "Velikost celkem" itself - since 16. 11. 2026 with music (content_kinds.py).
reality = int(stats.tech_coverage()["size_bytes"])
curve_end = stats.snapshots(400)[-1]
check(int(curve_end["size_bytes"]) == reality,
      f"the end of the curve matches the library's size"
      f" ({int(curve_end['size_bytes'])} vs {reality})")
check(int(curve_end["item_count"]) == db.query_value(
          "SELECT COUNT(*) FROM items WHERE is_missing = 0", default=0),
      "and the number of items too")

# Back again, so the further checks measure what they should.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 0, synced_at = ?", (db.utcnow(),))
    conn.commit()

# An item in the archive leaves the library on the day the application last
# saw it - otherwise the curve would only grow and deleting would vanish from it.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1, synced_at = ?"
                 " WHERE id IN (SELECT id FROM items LIMIT 3)",
                 ((today - timedelta(days=50)).isoformat() + " 12:00:00",))
    conn.commit()
with_loss = stats.snapshots(400)
before = next(r for r in with_loss if r["day"] == (today - timedelta(days=60)).isoformat())
after = next(r for r in with_loss if r["day"] == (today - timedelta(days=40)).isoformat())
check(after["item_count"] == before["item_count"] - 3,
      f"the loss from the archive is recognised ({before['item_count']} → {after['item_count']})")

# A measurement always takes precedence over the fill-in.
snapshot((today - timedelta(days=10)).isoformat(), 9_999 * 1024 ** 3, items=777)
mixed = stats.snapshots(400)
backfilled = [r for r in mixed if r["backfilled"]]
measured = [r for r in mixed if not r["backfilled"]]
check(len(measured) == 1 and measured[0]["item_count"] == 777,
      "the snapshot gets into the curve as a measurement")
check(backfilled and backfilled[-1]["day"]
      < measured[0]["day"], "and the fill-in ends where the measurement starts")
check(all(r["day"] < measured[0]["day"] for r in backfilled),
      "the fill-in does not overlap the measured time")

# The fill-in is seated on the first measured value. We read the shape of the past from the data,
# but its height is systematically shifted (today's sizes),
# and without seating there is a step at the joint - that is, a claim about a particular day
# that is untrue: the library did not lose half overnight.
join_backfill = backfilled[-1]["size_bytes"]
join_measured = measured[0]["size_bytes"]
check(abs(join_backfill - join_measured) < join_measured * 0.05,
      f"the fill-in is seated on the measurement ({join_backfill / 1024 ** 3:.0f}"
      f" vs {join_measured / 1024 ** 3:.0f} GB)")
check(all(r["size_bytes"] >= 0 for r in backfilled),
      "and no day comes out negative")
# A trap: the shift is ONE number for the whole filled-in part, so the shape
# must not change - the increases between days stay as they were.
unseated = stats._backfilled_curve(backfilled[0]["day"],
                                   backfilled[-1]["day"])
original_diff = (unseated[-1]["size_bytes"] - unseated[0]["size_bytes"])
seated_diff = (backfilled[-1]["size_bytes"]
               - backfilled[0]["size_bytes"])
check(original_diff == seated_diff,
      f"seating does not change the curve's shape ({original_diff} vs {seated_diff})")

# And the curve goes into the chart as TWO series, so a measurement can be told from a fill-in.
data = cards.growth_data(400)
chart_backfilled = [r for r in data["snapshots"] if r["gb_backfilled"] is not None]
chart_measured = [r for r in data["snapshots"] if r["gb_measured"] is not None]
check(bool(chart_backfilled) and bool(chart_measured),
      f"the chart has both series ({len(chart_backfilled)} filled-in,"
      f" {len(chart_measured)} measured points)")
check(chart_backfilled[-1]["day"] == chart_measured[0]["day"],
      "and they join on a shared day, so there is no hole between them")
check(data["growth"]["backfilled"] and data["growth"]["backfilled_until"],
      f"the summary reports that part is filled in (until {data['growth']['backfilled_until']})")

# The chart must speak the same unit as the tile "Velikost celkem" next
# to it. When it always drew gigabytes, a twelve-terabyte
# library had 13 101 on the axis and 12,8 in the tile - one and the same number, only
# in another unit, which reads as "the chart shows more".
check(data["growth_unit"] in ("GB", "TB"),
      f"the data carry the unit ({data['growth_unit']})")

with db.connect() as conn:
    conn.execute("UPDATE items SET size_bytes = ?", (200 * 1024 ** 3,))
    conn.commit()
big = cards.growth_data(400)
check(big["growth_unit"] == "TB",
      f"for a terabyte library terabytes are drawn ({big['growth_unit']})")
most = max(r["gb"] for r in big["snapshots"])
check(most < 1000,
      f"and the numbers on the axis stay small ({most}) - not tens of thousands of gigabytes")
with db.connect() as conn:
    conn.execute("UPDATE items SET size_bytes = ?", (1024 ** 3,))
    # That slipped-in snapshot away too - the unit is chosen from the WHOLE curve,
    # so nine terabytes in the old snapshot would keep it up.
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()
small = cards.growth_data(400)
check(small["growth_unit"] == "GB",
      f"for a small library gigabytes stay ({small['growth_unit']})")

print()
print("--- growth over time ---")
# From here on it is about computing the growth from the snapshots itself, so the fill-in is switched off:
# `date_created` is the only thing it can build the past from.
with db.connect() as conn:
    conn.execute("UPDATE items SET date_created = NULL, is_missing = 0")
    # Music and books too: without a day of adding it is not computed into these snapshots either.
    conn.execute("UPDATE music_track SET added_at = NULL")
    conn.execute("UPDATE book SET added_at = NULL")
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()
for offset, size in ((30, 1_000 * 1024 ** 3), (0, 1_300 * 1024 ** 3)):
    snapshot((today - timedelta(days=offset)).isoformat(), size,
             items=100 + (30 - offset))

growth = stats.library_growth(90)
check(growth["enough_data"], "two snapshots are enough for a curve")
check(growth["increase"] == 300 * 1024 ** 3,
      f"the increase matches ({growth['increase'] / 1024 ** 3:.0f} GB)")
check(round(growth["daily"] / 1024 ** 3) == 10, "the daily increase too (10 GB)")
check(growth["item_count"] == 30, "and the increase of items")

# Without the free space only what we know is promised.
check(growth["days_left"] is None, "without the free space known no estimate is made")
check(growth["per_year"] and growth["per_year"] > growth["size"],
      "instead it says where it heads in a year")

print()
print("--- the estimate when we see the free space ---")
snapshot(today.isoformat(), 1_300 * 1024 ** 3, items=130,
         free=500 * 1024 ** 3)
growth = stats.library_growth(90)
check(growth["days_left"] == 50,
      f"500 GB free at 10 GB a day is 50 days ({growth['days_left']})")

print()
print("--- what cannot be computed is not claimed ---")
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    conn.commit()
empty = stats.library_growth(90)
check(not empty["enough_data"] and empty["days"] == 0,
      "without snapshots nothing is claimed")

snapshot(today.isoformat(), 1_000 * 1024 ** 3)
single = stats.library_growth(90)
check(not single["enough_data"], "one point is not growth")

# A shrinking library: there is no sense in reporting how long until the space runs out -
# and dividing by zero even less.
snapshot((today - timedelta(days=10)).isoformat(), 2_000 * 1024 ** 3, free=10 ** 12)
falling = stats.library_growth(90)
check(falling["enough_data"] and falling["increase"] < 0, "the loss is recognised")
check(falling["days_left"] is None and falling["per_year"] is None,
      "for a shrinking library no estimate is made")

snapshot((today - timedelta(days=5)).isoformat(), 1_000 * 1024 ** 3, free=10 ** 12)
standing = stats.library_growth(90)
check(standing["days_left"] is None, "nor for a standing one - dividing by zero does not work")

print()
print("--- in the chart the fill-in is told apart from the measurement ---")
from jellyscope import charts  # noqa: E402

POINTS = [{"day": f"2026-01-0{i}", "a": float(i)} for i in range(1, 6)]
SERIES = [{"key": "a", "label": "A"}]


def lines(svg: str) -> int:
    """How many lines (not fills) the chart drew."""
    return svg.count('fill="none" stroke=')


whole = charts.area_chart_multi(POINTS, "den", SERIES)
check(lines(whole) == 1, f"a continuous series is one line ({lines(whole)})")

# None = "the series does not hold here". It must become a break, not a drop to zero.
with_gap = charts.area_chart_multi(
    [dict(b, a=None) if b["day"] == "2026-01-03" else b for b in POINTS],
    "den", SERIES)
check(lines(with_gap) == 2, f"a series with a hole falls into two lines ({lines(with_gap)})")
# And above all: A MISSING KEY must still mean zero. All the other charts in the application
# stand on it - if it became a hole, they would fall apart.
without_key = charts.area_chart_multi(
    [{"day": b["day"]} if b["day"] == "2026-01-03" else b for b in POINTS],
    "den", SERIES)
check(lines(without_key) == 1,
      f"a missing key stays zero, not a hole ({lines(without_key)})")

print()
print("--- the section ---")
data = cards.growth_data(90)
check("snapshots" in data and "growth" in data, "the section supplies the snapshots and the summary")
check(all("gb" in row for row in data["snapshots"]),
      "the chart gets gigabytes, not bytes")
check("library_growth" in cards.BY_KEY, "and it is in the registry of sections")

print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/library")
    check(page.status_code == 200, "the Library loads")
    check("Růst knihovny" in page.text or "Library growth" in page.text,
          "and the growth is on it")

    # The section must hold up alone in a dashboard too - the same trap as with
    # "Odkud se dívají", where a missing import of a macro brought the whole page down.
    db.set_setting(cards.ENABLED_KEY, "1")
    db.forget_settings()
    cards.save_layout(["library_growth"])
    check(client.get("/dashboard").status_code == 200,
          "and alone in the custom dashboard too")

    print()
    print("--- the period on the Library is the same as everywhere else ---")
    # A year of snapshots, so the range can be told: with three rows
    # thirty days come out the same as a year and the test would measure nothing.
    for offset in range(0, 365, 3):
        snapshot((today - timedelta(days=offset)).isoformat(),
                 (1_000 - offset) * 1024 ** 3, items=200 - offset // 3)

    # No switch of its own on the card: one choice for the whole application,
    # the custom range included. If the Library had its own, a person would have
    # two different periods and would have to watch which one holds right now.
    page = client.get("/library?days=365").text
    check("modal-period" in page, "the period filter is on the page")

    def own_period_controls(html: str) -> list[str]:
        """Links, fields or a period dialog of the growth card's own (the card: from its id to the next card)."""
        start = html.find('id="library-growth"')
        if start < 0:
            return ["no growth card"]
        following = html.find('<div class="card"', start)
        card = html[start:following if following > 0 else len(html)]
        return re.findall(r'href="[^"]*[?&](?:days|since|until)=[^"]*"|name="(?:days|since|until)"|data-modal="modal-period[^"]*"',
                          card)

    check(not own_period_controls(page), f"and a switch of its own on the card is not ({own_period_controls(page)})")
    check(own_period_controls('<div id="library-growth"><a class="chip" href="/library?days=30">30</a></div>')
          == ['href="/library?days=30"'], "a trap: the card's own period chip (as it was until 918510f) is found")

    # The choice is shared with the rest of the application.
    client.get("/?days=30")
    check('href="/library?days=30"' in client.get("/library").text
          or 'chip active' in client.get("/library").text,
          "a choice from another page shows here too")

    def point_count(html: str) -> int:
        return html.count('class="chart-hit"')

    short = point_count(client.get("/library?days=30").text)
    long = point_count(client.get("/library?days=365").text)
    check(long > short, f"a longer period draws more points ({short} -> {long})")

    # A custom range - formerly that did not work on the card at all.
    since = (today - timedelta(days=60)).isoformat()
    until = (today - timedelta(days=30)).isoformat()
    custom = client.get(f"/library?since={since}&until={until}")
    check(custom.status_code == 200, "a custom range passes")
    check(0 < point_count(custom.text) < long,
          f"and it draws only its own section ({point_count(custom.text)} points)")

print()
print("--- the chart and the tile say the same number ---")
# The chart drew terabytes through `_fmt`, which above ten throws away
# the decimal place: 28,8 TB became "29 TB", while the tile next to
# the chart wrote 28,8. Half a terabyte's difference in two numbers about the same thing.
from jellyscope import charts, formatting  # noqa: E402

for tb in (28.8, 12.83, 1.75, 145.3):
    byte_count = int(tb * 1024 ** 4)
    from_chart = charts._value_label(round(byte_count / 1024 ** 4, 2), "TB")
    from_tile = formatting.bytes_human(byte_count)
    check(from_chart == from_tile, f"{tb} TB: chart {from_chart!r} = tile {from_tile!r}")

# And a trap: if `_value_label` went back to `_fmt`, this difference would come back.
check(charts._value_label(28.8, "TB") != "29 TB",
      "terabytes are not rounded to whole numbers")
check(charts._value_label(28.8, "h") == "29 h",
      "for hours a whole number stays on the contrary")

print()
print("--- a period that ends before the first snapshot is seated on it too ---")
# Two titles of 10 GB (January) and 5 GB (March), today's sizes; the first snapshot on 1 June measured 30 GB,
# the sizes of then. The backfill is shifted by +15 GB to meet it - also for a period that ends on 31 May,
# otherwise the same day is 15 GB in one period and 30 GB in another, and comparing May with June shows the
# library doubling overnight.
GB = 10 ** 9
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    conn.execute("UPDATE items SET date_created = NULL, is_missing = 0")
    conn.execute("UPDATE music_track SET added_at = NULL")
    conn.execute("UPDATE book SET added_at = NULL")
    two = [r["id"] for r in conn.execute("SELECT id FROM items ORDER BY id LIMIT 2").fetchall()]
    for item_id, created, size in zip(two, ("2026-01-10T10:00:00.0000000Z", "2026-03-01T10:00:00.0000000Z"),
                                      (10 * GB, 5 * GB)):
        conn.execute("UPDATE items SET date_created = ?, size_bytes = ? WHERE id = ?", (created, size, item_id))
    conn.commit()
snapshot("2026-06-01", 30 * GB, items=2)
snapshot("2026-06-10", 30 * GB, items=2)
may = stats.period_between("2026-04-01", "2026-05-31")
to_june = stats.period_between("2026-04-01", "2026-06-15")
in_may = {r["day"]: r for r in stats.snapshots(may)}
in_june = {r["day"]: r for r in stats.snapshots(to_june)}
check(in_june["2026-05-31"]["size_bytes"] == 30 * GB,
      f"a period spanning the first snapshot has 31 May seated ({in_june['2026-05-31']['size_bytes'] / GB:g} GB)")
check(in_may["2026-05-31"]["size_bytes"] == 30 * GB and in_may["2026-04-01"]["size_bytes"] == 30 * GB,
      f"a period ending the day before has the same 31 May ({in_may['2026-05-31']['size_bytes'] / GB:g} GB)"
      f" and 1 April ({in_may['2026-04-01']['size_bytes'] / GB:g} GB)")
check(max(in_may) == "2026-05-31" and all(r["backfilled"] for r in in_may.values()),
      "and it stays within its own days, all of them backfilled")
check(stats.library_growth(may, forecast=False)["size"] == 30 * GB,
      "the comparison's size at the end of the period matches")

print()
print("--- a title created on 1 January of the year 1 does not slow the curve down ---")
# Jellyfin can report DateCreated as DateTime.MinValue (0001-01-01). The curve must count such a title from the
# start without walking 739 000 days one by one to the period (about half a second for every curve on a page).
# Counted in the days the curve steps through, not in time: a timing check flaked on a loaded machine.
from jellyscope.stats import growth as stats_growth  # noqa: E402


def walked_days() -> int:
    """How many days the curve over April and May steps through (61 = only the period)."""
    steps: list[int] = []
    real = stats_growth.timedelta
    stats_growth.timedelta = lambda *a, **k: steps.append(1) or real(*a, **k)
    try:
        stats._backfilled_curve("2026-04-01", "2026-05-31")
    finally:
        stats_growth.timedelta = real
    return len(steps)


usual = walked_days()
with db.connect() as conn:
    third = conn.execute("SELECT id FROM items WHERE date_created IS NULL ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE items SET date_created = '0001-01-01T00:00:00.0000000Z', size_bytes = ? WHERE id = ?",
                 (1 * GB, third))
    conn.commit()
curve = {r["day"]: r for r in stats._backfilled_curve("2026-04-01", "2026-05-31")}
check(min(curve) == "2026-04-01" and curve["2026-04-01"]["size_bytes"] == 16 * GB
      and curve["2026-04-01"]["item_count"] == 3,
      f"the title counts from the start ({curve['2026-04-01']['size_bytes'] / GB:g} GB, "
      f"{curve['2026-04-01']['item_count']} titles)")
year_one = walked_days()
check(usual == 61 and year_one == usual,
      f"and the curve steps through the period's days only, as without it ({year_one} days vs {usual})")

print()
print("--- 'the last N days' grow over all N days, the chart keeps its N points ---")
# A snapshot holds the state at the end of its day. Since "the last 7 days" start at the midnight of the first of
# them, the increase measured from that first day's snapshot left out everything added during it: one movie a day
# for 7 days came out as 6. The base is the state at the end of the day before the first day.
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    conn.execute("UPDATE items SET date_created = NULL, is_missing = 0")
    conn.commit()
for ago in range(20, -1, -1):
    snapshot((today - timedelta(days=ago)).isoformat(), (20 - ago) * GB, items=100 + (20 - ago))
week = stats.library_growth(7)
check(week["enough_data"] and week["increase"] == 7 * GB and week["item_count"] == 7 and week["days"] == 7,
      f"one movie a day: 7 movies in the last 7 days ({week.get('increase', 0) / GB:g} GB, "
      f"{week.get('item_count')} items, {week.get('days')} days)")
check(len(stats.snapshots(7)) == 7, f"and the chart draws the seven days ({len(stats.snapshots(7))} points)")
both = stats.comparison(7, stats.previous_period(7))
library_rows = next((g["rows"] for g in both["groups"] if g.get("area") == "library"), [])
check(both["library_known"] and [(r["a"], r["b"]) for r in library_rows[:2]] == [(7, 7), (7 * GB, 7 * GB)],
      f"Comparison: 7 items and 7 GB added in both weeks ({[(r['a'], r['b']) for r in library_rows[:2]]})")

with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
    for ago in range(10, -1, -1):
        created = (today - timedelta(days=ago)).isoformat() + "T12:00:00.0000000Z"
        conn.execute("INSERT INTO items (id, name, type, path, size_bytes, is_missing, date_created, synced_at)"
                     " VALUES (?, ?, 'Movie', ?, ?, 0, ?, ?)",
                     (f"daily-{ago}", f"Daily {ago}", f"/media/daily/{ago}.mkv", GB, created, db.utcnow()))
    conn.commit()
backfilled_week = stats.library_growth(7)
check(backfilled_week["enough_data"] and backfilled_week["increase"] == 7 * GB and backfilled_week["item_count"] == 7,
      f"the backfilled past the same: 7 movies ({backfilled_week.get('increase', 0) / GB:g} GB, "
      f"{backfilled_week.get('item_count')} items)")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
