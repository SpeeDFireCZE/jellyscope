# -*- coding: utf-8 -*-
r""""Against the previous period" must not produce nonsense.

A real case: the filter "letos" showed **1 619 572 %**. The number was
computed correctly - the previous window of the same length fell into the time when
Jellyscope was not running yet, so today was compared with a few seconds
of history. But such a percentage says nothing about today; it only says "last time
there was almost nothing".

Two safeguards:

* When the previous window starts earlier than our history reaches, the comparison is
  not shown at all and instead it says why. A quietly missing arrow looks
  like an error and a person looks for it at their own end.
* And even where the data is there, above a thousand percent it switches to a multiple and above
  a hundredfold straight to words.

Run:
    .venv/bin/python tests/test_period_comparison.py
"""
from __future__ import annotations

import os
import re
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "srovnani.db")
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
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def add_play(days_ago: float, seconds: int, key: str) -> None:
    when = NOW - timedelta(days=days_ago)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', 'Petr', 'film-1', 'Duna', 'Movie', ?, ?, ?, 0)""",
            (key, when.strftime(db.TIME_FORMAT), when.strftime(db.TIME_FORMAT), seconds),
        )


print("--- without data nothing is compared ---")
check(stats.first_record() == "", "an empty database has no first record")
check(stats.can_compare(30) is False, "and there is nothing to compare with")

# The history starts 40 days ago - exactly the situation where the filter reaches further.
for day in range(0, 40, 4):
    add_play(day, 3600, f"s{day}")

print()
print("--- it compares only with a period we have data from ---")
check(stats.can_compare(7) is True, "a week against a week yes")
check(stats.can_compare(30) is False,
      "a month not - the previous month reaches before the start of the history")
this_year = stats.period_between("2026-01-01", NOW.date().isoformat())
check(stats.can_compare(this_year) is False, "and 'letos' not at all")

print()
print("--- the previous window follows on, does not overlap ---")
window = stats.previous_period(7)
check(window.until == stats._as_period(7).since, "it ends where the chosen one starts")
check(window.days == 7, "and is of the same length")
check(window.relative is False, "it has a fixed end, it is not 'the last N days'")

print()
print("--- on the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402


def hero(html: str) -> str:
    m = re.search(r'class="delta[^"]*">(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)

    text = hero(client.get(f"/?since=2026-01-01&until={NOW.date().isoformat()}").text)
    check("%" not in text, f"for 'letos' the percentages do not show ({text})")
    check("historie začíná" in text, "instead of them there is an explanation why")

    # And the main number says which period it is actually for - with a custom
    # range `day_labels` has nothing to offer.
    page = client.get(f"/?since=2026-01-01&until={NOW.date().isoformat()}").text
    heading = re.search(r'<p class="tile-label">(.*?)</p>', page, re.S)
    check("2026" in re.sub(r"\s+", " ", heading.group(1)),
          f"the heading shows the range ({re.sub(r'  +', ' ', heading.group(1)).strip()})")

    check("%" in hero(client.get("/?days=7").text) or "beze změny" in hero(
        client.get("/?days=7").text), "for a week it compares normally")

print()
print("--- huge numbers are not written as percentages ---")
# Here it is about the formatting itself, not the data - we try the macro directly.
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from jellyscope import formatting, i18n  # noqa: E402

environment = Environment(loader=FileSystemLoader(
    str(PROJECT / "jellyscope" / "templates")))
formatting.register(environment)
i18n.register(environment)
macros = environment.get_template("_macros.html").module


def change(value: float) -> str:
    return re.sub(r"\s+", " ", str(macros.change(value))).strip()


check(change(42.0) == "42 %", f"an ordinary change stays in percent ({change(42.0)})")
check(change(-42.0) == "42 %", "the arrow carries the sign, not the number")
check("×" in change(1500.0), f"above a thousand percent a multiple is written ({change(1500.0)})")
check(change(1619572.0) == "minule skoro nic",
      f"and above a hundredfold straight in words ({change(1619572.0)})")
# Exactly the number that uncovered all this.

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
