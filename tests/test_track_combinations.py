# -*- coding: utf-8 -*-
"""The dialog with all the combinations of tracks.

The card "Kombinace stop" shows the four most frequent and sums the rest up into the row
"Ostatní (N dalších kombinací)". Until now that row only stated that
something is hidden, and gave no way to show it — with a library where "Ostatní" holds
maybe a quarter of the titles, it is a dead end.

What it stands on:

* **The dialog has the full list**, not just the hidden rest: a person wants
  to see the whole ranking, not two torn-off pieces.
* **The percentages are counted from the same whole** as on the card. Were they
  counted in the dialog only from the rows listed, "56 %" would mean something else
  in every place and it could not be told.
* **When the card hides nothing, the dialog is not drawn.** It would be a second copy
  of the same table and the button would have nothing to open.

Run:
    .venv/bin/python tests/test_track_combinations.py
"""
from __future__ import annotations

import os

import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kombinace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, langstats  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def put_item(number: int, codes: str, size: int = 1024 ** 3) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, audio_languages, size_bytes,"
            " is_missing) VALUES (?,?,?,?,?,0)",
            (f"i{number}", f"Film {number}", "Movie", codes, size))
        conn.commit()


db.init_db()

# A library with twelve combinations: four large and eight small. Exactly
# the case the "Ostatní" row came about for.
LARGE = [("cs,en", 40), ("en", 30), ("cs", 20), ("cs,en,sk", 10)]
SMALL = ["de", "fr", "en,fr", "cs,de", "pl", "en,pl", "cs,en,de", "hu"]

number = 0
for codes, how_many in LARGE:
    for _ in range(how_many):
        number += 1
        put_item(number, codes)
for codes in SMALL:
    for _ in range(2):
        number += 1
        put_item(number, codes)

TOTAL_COMBINATIONS = len(LARGE) + len(SMALL)
TOTAL_TITLES = sum(how_many for _, how_many in LARGE) + len(SMALL) * 2

print("--- the data ---")
card_rows = langstats.language_combinations()
all_rows = langstats.all_combinations()
check(len(card_rows) == 5, f'the card shows four and “Ostatní” ({len(card_rows)} rows)')
check(len(all_rows) == TOTAL_COMBINATIONS,
      f"the dialog has all the combinations ({len(all_rows)} of {TOTAL_COMBINATIONS})")

# In sum it must match - otherwise the dialog would speak about another library than the card.
check(sum(int(r["item_count"]) for r in all_rows) == TOTAL_TITLES,
      f"and all the titles ({TOTAL_TITLES})")
check(sum(int(r["item_count"]) for r in card_rows) == TOTAL_TITLES,
      "the card has the same number of them with the summary")

# Percentages of the same whole: the first row must come out the same in both places.
check(abs(card_rows[0]["percent"] - all_rows[0]["percent"]) < 0.001,
      f"the percentages match ({card_rows[0]['percent']:.1f} % vs {all_rows[0]['percent']:.1f} %)")
check(abs(sum(r["percent"] for r in all_rows) - 100) < 0.01,
      "and in the dialog they add up to a hundred")

# The "Ostatní" row reports how many combinations were hidden.
hidden = card_rows[-1].get("combinations")
check(hidden == len(SMALL), f"the summary row reports {hidden} hidden combinations")

print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    response = client.get("/video/languages")
    check(response.status_code == 200, "Languages load")
    html = response.text

    # The "Ostatní" row is a button, not dead text.
    check('data-modal="modal-combinations"' in html, "“Ostatní” is a button into the dialog")
    check('id="modal-combinations"' in html, "and the dialog really is on the page")

    # The dialog must have the small combinations too - it is opened for them.
    dialog = html.split('id="modal-combinations"', 1)[1]
    missing = [j for j in SMALL
               if langstats.languages.combination_label(j) not in dialog]
    check(not missing, f"the dialog has the small combinations too (missing: {missing})")

    # And the sum, so it is seen what the percentages are counted from.
    check("</tfoot>" in dialog, "the dialog has a summary row")

    # A trap: were the dialog filled with the same rows as the card, all the tests
    # above would pass anyway. The card must NOT have those small combinations - otherwise
    # nothing is hidden and the dialog is good for nothing.
    # The search is in the table's CELLS, not in the whole page: a two-letter name
    # like "PL" would otherwise hit any word at all - last time
    # "AGPL-3.0" in the footer.
    card_html = html.split('id="modal-combinations"', 1)[0]
    cells = re.findall(r'<td class="strong">(.*?)</td>', card_html, re.S)
    cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", b)).strip() for b in cells]
    extra = [j for j in SMALL
             if langstats.languages.combination_label(j) in cells]
    check(not extra, f"and they are not on the card itself (extra: {extra})")

print()
print("--- when there is nothing to hide, the dialog is not drawn ---")
with db.connect() as conn:
    conn.execute("DELETE FROM items WHERE audio_languages NOT IN"
                 " ('cs,en', 'en', 'cs', 'cs,en,sk')")
    conn.commit()

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    html = client.get("/video/languages").text
    check(len(langstats.language_combinations()) == 4, "four combinations were left")
    check('id="modal-combinations"' not in html, "and the dialog is not drawn")
    check('data-modal="modal-combinations"' not in html, "nor the button into it")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
