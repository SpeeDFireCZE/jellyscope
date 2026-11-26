# -*- coding: utf-8 -*-
"""The Comparison page: two arbitrary periods side by side.

Not to be confused with `test_period_comparison.py` - that one watches “against
the previous period" on the Overview, where a person does not pick the second window.

The Overview has long been able to do "against the last period", but a person does not
pick the last period — it is the window right before the chosen one. To the question “was August better
than December?" it gives no answer.

Four places where an error is easily kept quiet, and that is why they are measured:

* **Periods of different length.** In a sum the longer period wins almost always,
  so beside the sum must stand the daily average — and the page must say that
  the lengths differ.
* **An overlap.** When the periods have a shared piece, a part of the data is in both
  columns and the difference between them does not measure a change.
* **Percentages.** The difference of two percentages is in percentage points.
  "From 12 % to 18 %" is +6 points; +50 % is formally right and confusing.
* **Delivery from an import.** Playback Reporting writes “Transcode (v:h264
  a:direct)". With a match on the exact name, the transcoding from an import would be missing
  in the comparison — it must be summed by role.

Run:
    .venv/bin/python tests/test_two_periods.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dve_obdobi.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import sample_movie  # noqa: E402
from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


NOW = datetime.now(timezone.utc).replace(tzinfo=None)
counter = 0


def play(days_ago: float, seconds: int, user: str = "Petr",
         title: str = "Duna", method: str = "DirectPlay") -> None:
    global counter
    counter += 1
    when = NOW - timedelta(days=days_ago)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, started_at,
                                     last_seen_at, watched_seconds,
                                     play_method, is_active)
               VALUES (?,?,?,?,?, 'Movie', ?, ?, ?, ?, 0)""",
            (f"s{counter}", "u-" + user, user, "film-" + title, title,
             when.strftime(db.TIME_FORMAT), when.strftime(db.TIME_FORMAT),
             seconds, method))
        conn.commit()


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)


def period(since_ago: float, until_ago: float) -> stats.Period:
    """The window between "X days ago" and "Y days ago", in UTC - bounds without rounding."""
    start = (NOW - timedelta(days=since_ago)).strftime(db.TIME_FORMAT)
    end = (NOW - timedelta(days=until_ago)).strftime(db.TIME_FORMAT)
    return stats.Period(since=start, until=end, days=max(1, round(since_ago - until_ago)),
                        relative=False, since_local=start, until_local=end)


# Two unrelated windows: A = 1-10 days ago, B = 21-30 days ago.
A = period(10, 1)
B = period(30, 21)

# A: 10 hours in all. B: 5 hours. The numbers are round on purpose, so that
# even a small shift is recognised on them.
for day in (2, 4, 6, 8, 9):
    play(day, 2 * 3600)                      # 5 x 2 h = 10 h
for day in (22, 24, 26):
    play(day, int(5 / 3 * 3600), user="Jana", title="Alien")  # 5 h

print("--- what was measured gets compared ---")
v = stats.comparison(A, B)
watching = {r["label"]: r for r in v["groups"][0]["rows"]}

watched = watching["Odsledováno"]
check(round(watched["a"], 2) == 10.0, f"period A has 10 h ({watched['a']:.2f})")
check(round(watched["b"], 2) == 5.0, f"period B has 5 h ({watched['b']:.2f})")
check(round(watched["difference"], 2) == 5.0,
      f"the difference is +5 h ({watched['difference']:.2f})")
check(round(watched["relative"]) == 100,
      f"and relatively double ({watched['relative']:.0f} %)")

check(watching["Spuštění"]["a"] == 5 and watching["Spuštění"]["b"] == 3,
      "the plays match (5 vs 3)")
check(watching["Diváci"]["a"] == 1 and watching["Diváci"]["b"] == 1,
      "the viewers match")

# Both windows are nine days, so the daily average is the sum divided by nine.
daily = watching["Denní průměr"]
check(abs(daily["a"] - 10 / 9) < 0.01, f"the daily average of A ({daily['a']:.3f} h)")
check(abs(daily["b"] - 5 / 9) < 0.01, f"the daily average of B ({daily['b']:.3f} h)")

print()
print("--- periods of different length are recognised ---")
check(v["same_length"], "two nine-day windows are equally long")

long_period = period(40, 1)      # 39 days
unequal = stats.comparison(long_period, B)
check(not unequal["same_length"], "39 days vs 9 days are not passed off as equally long")
check(round(unequal["a"]["days"]) == 39 and round(unequal["b"]["days"]) == 9,
      f"and the lengths are reported ({unequal['a']['days']:.0f} vs {unequal['b']['days']:.0f} days)")

# The daily average is the only number that makes sense with windows of different length:
# the sum of A is bigger, the average smaller.
watching_unequal = {r["label"]: r for r in unequal["groups"][0]["rows"]}
check(watching_unequal["Odsledováno"]["difference"] > 0, "in the sum the longer period leads")
check(watching_unequal["Denní průměr"]["difference"] < 0,
      "but in the daily average the shorter - that is why the row is there")

# The Comparison's default pair: "the last 30 days" and the same span 30 days back. Both hold the part of today that
# has passed (25. 11. 2026), and the daily average divides by that real length - not by 30 whole days.
default_pair = stats.comparison(30, stats.previous_period(30))
check(default_pair["same_length"] and abs(default_pair["a"]["days"] - default_pair["b"]["days"]) < 0.05
      and 29 <= default_pair["a"]["days"] < 30,
      f"the default pair is equally long, the time that has really passed ({default_pair['a']['days']:.2f} vs"
      f" {default_pair['b']['days']:.2f} days)")

print()
print("--- an overlap does not hide ---")
check(not v["overlap"], "windows that are not adjacent do not overlap")
check(stats.comparison(A, period(12, 5))["overlap"], "an overlap is recognised")
check(stats.comparison(A, A)["overlap"], "and the same window with itself too")

print()
print("--- percentages are counted in points, not in percent of a percent ---")
# We add a transcoding to B, so the transcode share is not zero in both windows.
play(23, 3600, user="Jana", title="Alien",
     method="Transcode (v:h264 a:direct)")
v2 = stats.comparison(A, B)
delivery = {r["label"]: r for r in v2["groups"][1]["rows"]}

share = delivery["Podíl transcode"]
check(share["relative"] is None, "a percentage gets no relative change")
check(abs(share["difference"] - (float(share["a"]) - float(share["b"]))) < 0.001,
      f"the difference is a plain difference in points ({share['difference']:.1f})")
check(share["b"] > 0, f"and in B the transcode really got counted ({share['b']:.1f} %)")

# A trap: the imported history has "Transcode (v:h264 a:direct)". Were it
# summed by the exact name, this row would be zero.
check(delivery["Transcode"]["b"] > 0,
      f"transcoding from an import counts as transcode ({delivery['Transcode']['b']:.2f} h)")
check(delivery["Přímé přehrávání"]["a"] > 0, "and direct play apart")

print()
print("--- languages ---")
# In A people watched in Czech, in B in English. Exactly this is what the comparison is to show.
play(3, 4 * 3600, user="Petr", title="Duna")
play(25, 4 * 3600, user="Jana", title="Alien")
with db.connect() as conn:
    conn.execute("UPDATE playback SET audio_language = 'cs'"
                 " WHERE started_at >= ? AND started_at < ?", (A.since, A.until))
    conn.execute("UPDATE playback SET audio_language = 'en'"
                 " WHERE started_at >= ? AND started_at < ?", (B.since, B.until))
    conn.commit()

v3 = stats.comparison(A, B)
language_group = next((s for s in v3["groups"]
                       if s.get("with_share")), None)
check(language_group is not None, "the group of languages is in the comparison")
by_language = {r["label"]: r for r in (language_group or {"rows": []})["rows"]}
check(len(by_language) >= 2,
      f"and both languages are in it ({list(by_language)})")

from jellyscope import languages  # noqa: E402

czech = by_language.get(languages.display("cs"))
english = by_language.get(languages.display("en"))
check(czech is not None and english is not None,
      f"Czech and English were both found ({list(by_language)})")
if czech and english:
    check(czech["a"] > 0 and czech["b"] == 0,
          f"Czech was watched only in A ({czech['a']:.1f} vs {czech['b']:.1f} h)")
    check(english["b"] > 0 and english["a"] == 0,
          f"English only in B ({english['a']:.1f} vs {english['b']:.1f} h)")
    # A trap: with the INTERSECTION of both periods every language that is in only one
    # would drop out of the table - that is, exactly the answer a person looks for.
    check(czech["share_a"] > 0 and czech["share_b"] == 0,
          "and the share is reported for both columns")

print()
print("--- the library ---")
# Without a snapshot the table is not drawn: "nothing was added" and "we do not know" are two
# different things, and mixing them up means lying.
check(not v3["library_known"], "without daily snapshots the library's growth is not claimed")


def snapshot(days_ago: float, item_count: int, size: int) -> None:
    day = (NOW - timedelta(days=days_ago)).date().isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO library_snapshot (day, item_count, movie_count, episode_count,"
            " size_bytes, uhd, hdr, without_tech, free_space, written_at)"
            " VALUES (?,?,?,0,?,0,0,0,NULL,?)"
            " ON CONFLICT (day) DO UPDATE SET item_count = excluded.item_count,"
            " size_bytes = excluded.size_bytes",
            (day, item_count, item_count, size, db.utcnow()))
        conn.commit()


# A: 20 items and 100 GB were added. B: 5 items and 10 GB.
snapshot(9, 100, 1_000 * 1024 ** 3)
snapshot(2, 120, 1_100 * 1024 ** 3)
snapshot(29, 50, 500 * 1024 ** 3)
snapshot(22, 55, 510 * 1024 ** 3)

v4 = stats.comparison(A, B)
check(v4["library_known"], "with snapshots it can be compared")
library = next((s for s in v4["groups"] if s["heading"] in ("Knihovna", "Library")), None)
check(library is not None, "and the Library group is in the table")
by_row = {r["label"]: r for r in (library or {"rows": []})["rows"]}
items_row = next(iter(by_row.values()), None)
check(items_row is not None and items_row["a"] == 20 and items_row["b"] == 5,
      f"the increase of items matches ({items_row['a']} vs {items_row['b']})")
data = list(by_row.values())[1]
check(round(data["a"] / 1024 ** 3) == 100 and round(data["b"] / 1024 ** 3) == 10,
      f"the increase of data too ({data['a'] / 1024 ** 3:.0f} vs {data['b'] / 1024 ** 3:.0f} GB)")

print()
print("--- emptiness is not passed off as a comparison ---")
empty = stats.comparison(period(400, 380), period(370, 360))
check(not empty["has_data"], "two empty periods have nothing to compare")
check(v["has_data"], "and periods with data do")

print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    response = client.get("/video/compare")
    check(response.status_code == 200, "Comparison loads")
    html = response.text
    check('name="a_since"' in html and 'name="b_since"' in html,
          "both periods have a prefix of their own in the address")
    check('id="modal-period-a"' in html and 'id="modal-period-b"' in html,
          "and each its own dialog with its own range")

    # Switching one period must not bring the other down: the link carries the other along.
    custom = client.get("/video/compare?a_since=2026-08-01&a_until=2026-08-31&b_days=7").text
    check("a_since=2026-08-01" in custom,
          "the second switch's links carry the first one's own range")

    # A negative difference must be written with its sign. The `hours` and `bytes` filters
    # take a negative number as zero, so a naive output would claim "0 min".
    falling = client.get("/video/compare?a_days=365&b_days=7").text
    check("&minus;" in falling or "−" in falling,
          "a negative difference is written with its sign, not as zero")

    # The link in the menu.
    check('href="/video/compare"' in html, "and the menu has something to click")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
