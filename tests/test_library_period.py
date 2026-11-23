# -*- coding: utf-8 -*-
r"""The Activity tab in the library counts for the chosen period.

It used to have a fixed 90 days and ignored the choice of period from the other pages -
while that could not be told anywhere, "za posledních 90 dnů" stood
only under the first tile. A person who had 30 days chosen elsewhere read
numbers for 90 and did not know it.

Now it takes the same period as the Overview or Users (a custom range too,
and the one remembered from another page) and has its own switch that stays on
the Activity tab. The recent plays are still the last 25 regardless of
the period.

Run:
    .venv/bin/python tests/test_library_period.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obdobi.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)


def ago(day_count: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=day_count)).strftime(db.TIME_FORMAT)


# Three movies, each played once - 3, 40 and 200 days ago. By how many
# of them fall into the period, it is recognised what period the page counts for.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for item_id, name, day_count, who in (("novy", "Nedávný film", 3, "Jana"),
                                          ("stredni", "Loňský podzim", 40, "Petr"),
                                          ("stary", "Dávný film", 200, "Eva")):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, is_missing) VALUES (?, ?, 'Movie', 'filmy', 0)",
            (item_id, name))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
            " library_id, started_at, last_seen_at, watched_seconds, is_active)"
            " VALUES (?, ?, ?, ?, ?, 'Movie', 'filmy', ?, ?, 3600, 0)",
            (f"s-{item_id}", "u-" + who, who, item_id, name, ago(day_count), ago(day_count)))
    conn.commit()

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})


def activity(query: str = "") -> str:
    return client.get("/library/filmy?tab=activity" + query).text


def plays(html: str) -> int:
    """The number in the "Spuštění" tile."""
    match = re.search(r"Spuštění</p>\s*<div class=\"tile-value\">\s*([\d\s ]+)", html)
    if not match:
        # So it does not look like an error in counting: the tile's HTML changed.
        raise AssertionError("the Spuštění tile was not found - did the tile macro change?")
    return int(re.sub(r"\D", "", match.group(1)))


def chart(html: str, heading: str) -> str:
    """The piece of the page from the card's heading to the end of the card."""
    start = html.find(f"<h2>{heading}</h2>")
    return html[start:html.find('<div class="card', start + 10)] if start >= 0 else ""


print("--- Activity counts for the chosen period ---")
for days, want in ((7, 1), (30, 1), (90, 2), (365, 3)):
    html = activity(f"&days={days}")
    check(plays(html) == want, f"for {days} days the plays are {want} ({plays(html)})")

html = activity("&days=7")
most_watched = chart(html, "Nejsledovanější")
check("Nedávný film" in most_watched and "Loňský podzim" not in most_watched,
      "the Most watched chart goes by the same period as the tile")
viewers = chart(html, "Diváci")
check("Jana" in viewers and "Petr" not in viewers, "the Viewers chart too")
check("Dávný film" in html.split("Poslední přehrávání", 1)[-1],
      "the recent plays are regardless of the period (even 200 days old)")
# "90 dnů" is still on the page - as a button of the switch. What is watched is the sentence
# about the period the numbers are for.
check("sledovalo za posledních 7 dnů" in html and "za posledních 90 dnů" not in html,
      "the page says what period the numbers are for - the chosen 7 days, not a fixed 90")
# "za posledních" + "rok" would give the wrong Czech "za posledních rok" - a year has
# its own sentence. And "za 30 dnů" without "posledních" could also be read as “in 30
# days from now".
check("sledovalo za poslední rok" in activity("&days=365"), "for a year the sentence is “za poslední rok”")

print()
print("--- the period is remembered across pages ---")
client.get("/users?days=365")
check(plays(activity()) == 3, "a year chosen on Users holds in Activity too")
client.get("/users?days=30")
check(plays(activity()) == 1, "and switching back to 30 days too")

print()
print("--- a custom range ---")
since_day = (date.today() - timedelta(days=45)).isoformat()
until_day = (date.today() - timedelta(days=35)).isoformat()
html = activity(f"&since={since_day}&until={until_day}")
check(plays(html) == 1 and "Loňský podzim" in chart(html, "Nejsledovanější"),
      f"the range {since_day} to {until_day} catches only the play 40 days ago ({plays(html)})")
check("sledovalo ve vybraném období." in html,
      "with a custom range the sentence does not repeat the dates - they are on the switch's button")

print()
print("--- the period switch stays on Activity ---")
html = activity("&days=30")
links = re.findall(r'href="([^"]*days=\d+[^"]*)"', html)
check(links and all("tab=activity" in o for o in links),
      f"the switch's links lead to the Activity tab ({links[:2]})")
check('data-modal="modal-period"' in html, "and the choice of a custom period is there too")
form = html.split('class="period-form"', 1)[-1].split("</form>", 1)[0]
check('name="tab" value="activity"' in form,
      "the custom period's form returns to Activity")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
