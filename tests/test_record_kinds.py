# -*- coding: utf-8 -*-
r"""The “Other” filter and the record's kind in the history.

Why this exists: the chart “Watching by day” got a third series
“Other”, but it could not be picked by a filter - and above all nothing at all could be
found out about it. The hours could be seen, but the record could not be found in the history, because
the list did not show what kind it was.

Into “Other” falls everything that is neither a movie nor an episode: live TV, music,
home videos - and above all **a taken-over history without a type**, which neither Jellystat
nor Playback Reporting sends. Exactly that empty value is treacherous:
in SQL a NULL is not "different from 'Movie'", so a condition of its own must catch it,
otherwise it would drop out of the filter.

Run:
    .venv/bin/python tests/test_record_kinds.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "druhy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from _package import sample_movie  # noqa: E402
from jellyscope import accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)
accounts.create("admin", "longpassword", is_admin=True)


def play(item_type: str | None, name: str, seconds: int = 3600) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, ended_at,"
            " watched_seconds) VALUES (?, 'u1', 'Jana', ?, ?, ?,"
            " datetime('now','-2 hours'), datetime('now','-1 hours'),"
            " datetime('now','-1 hours'), ?)",
            (f"s-{name}", f"id-{name}", name, item_type, seconds),
        )


play("Movie", "Film jedna", 3600)
play("Episode", "Díl jedna", 1800)
play("TvChannel", "ČT24", 900)
play(None, "Z importu", 600)      # there is no type at all
play("", "Prázdný typ", 300)      # the type is an empty string
# A taken-over history sometimes carries the kind "Series" or "Season" instead of "Episode":
# we know which series was watched, but not which episode. Such a record does not belong
# into “Other” - there a person looks for concerts and live TV.
play("Series", "Seriál bez dílu", 1800)
play("Season", "Řada bez dílu", 1800)


print("--- what falls into “Other” ---")
daily = stats.daily_activity_split(2)
other_sum = round(sum(r["other_hours"] for r in daily), 2)
# 900 + 600 + 300 seconds = 1800 = half an hour
check(abs(other_sum - 0.5) < 0.01,
      f"the hours in “Other” match ({other_sum} h)")
check(abs(round(sum(r["movie_hours"] for r in daily), 2) - 1.0) < 0.01,
      "movies are not counted into it")
# 1800 + 1800 + 1800 seconds = an hour and a half: an episode, a series and a season
series_sum = round(sum(r["series_hours"] for r in daily), 2)
check(abs(series_sum - 1.5) < 0.01,
      f"series take “Series” and “Season” too ({series_sum} h)")


print()
print("--- the “Other” filter ---")
check(stats.KIND_OTHER in stats.ALLOWED_KINDS, "the filter is among the allowed ones")

rows = stats.history(kind=stats.KIND_OTHER)
names = sorted(r["item_name"] for r in rows)
check(names == ["Prázdný typ", "Z importu", "ČT24"],
      f"the history returns exactly those three records ({names})")
# This is the trap: without a special condition for NULL one of three would be left.
check(any(r["item_name"] == "Z importu" for r in rows),
      "including the record that has no type at all (NULL)")
check(stats.history_count(kind=stats.KIND_OTHER) == 3,
      f"and the counter says the same ({stats.history_count(kind=stats.KIND_OTHER)})")

check([r["item_name"] for r in stats.history(kind=stats.KIND_MOVIE)] == ["Film jedna"],
      "the movies filter stayed as it was")
check(len(stats.history(kind=stats.KIND_BOTH)) == 7, "the mix shows everything")

# The click-through from the table under the chart carries the filter along, so the dividing line
# in the list must be the same as in the curve above it.
series = sorted(r["item_name"] for r in stats.history(kind=stats.KIND_SERIES))
check(series == ["Díl jedna", "Seriál bez dílu", "Řada bez dílu"],
      f"the list of series matches the chart ({series})")


print()
print("--- what “Other” is made of ---")
breakdown = stats.others_breakdown(2)
labels = [r["label"] for r in breakdown]
check("Živé vysílání" in labels, f"live TV is named in Czech ({labels})")
check("Neznámý (z importu)" in labels, "and the missing type too")
check(sum(r["plays"] for r in breakdown) == 3, "the plays match")
# NULL and an empty string are two groups for SQL, one thing for a person.
check(len(labels) == len(set(labels)),
      f"every kind is in the list once ({labels})")
check(breakdown[0]["hours"] >= breakdown[-1]["hours"], "sorted from the largest")


print()
print("--- the names of kinds ---")
check(stats.type_label("Episode") == "Díl seriálu", "Episode -> Díl seriálu")
check(stats.type_label("Movie") == "Film", "Movie -> Film")
check(stats.type_label(None) == "Neznámý (z importu)", "nothing -> from an import")
# An unknown kind shows the way it came. It is a trace by which it can be
# tracked down what it was - hiding it under "Other" would throw it away.
check(stats.type_label("Kdovico") == "Kdovico", "an unknown kind is not hidden")


print()
print("--- the pages can take it ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)

    r = client.get("/?kind=other")
    check(r.status_code == 200, f"Overview with the “Other” filter ({r.status_code})")
    check("Mix" in r.text, "the switch is called Mix, not Obojí")
    check("Obojí" not in r.text.split('id="daily-card"')[-1][:2000],
          "“Obojí” is no longer by the chart")
    check("Živé vysílání" in r.text,
          "and under the chart it is seen what “Other” is made of")

    r = client.get("/video/partials/daily?kind=other")
    check(r.status_code == 200 and "Ostatní" in r.text,
          "the partial for switching can do the same")
    check("data-tip" in r.text, "the legend carries a bubble with an explanation")

    r = client.get("/history?kind=other")
    check(r.status_code == 200, "the history with the “Other” filter")
    check("ČT24" in r.text and "Film jedna" not in r.text,
          "and it shows only those records")
    check("Živé vysílání" in r.text, "the Kind column shows what it was")

    # The most watched titles do not offer “Other”: they are made of the names
    # of titles, about which nothing is known for these records.
    r = client.get("/?top_kind=other")
    check(r.status_code == 200, "a foreign value for the most watched breaks nothing")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
