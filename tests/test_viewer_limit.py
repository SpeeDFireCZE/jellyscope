# -*- coding: utf-8 -*-
r"""How many viewers the Languages page shows before it hides the rest in a dialog.

There are two limits and that is on purpose: more bars fit on a wide screen
than on a phone, where a bar is full width and right under the viewers there is
a legend and a table too. The server does not know what a person looks at -
it sends both and CSS decides (`desktop-only` / `mobile-only`).

The trap it watches: between the two limits the page shows ALL the bars
(no summary button instead of them), but the ones above the mobile limit must
be wrapped so that a narrow screen hides them - and the dialog must be on the page
even when it hides only on a phone. Without it the button
"zobrazit všech" would have nothing to open.

Run:
    .venv/bin/python tests/test_viewer_limit.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "stropy.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import sample_movie  # noqa: E402
from jellyscope import db  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)

# Eight viewers, each with an hour in a different language. Eight because it lies between
# the default limits (phone 5, web 10) - exactly in the band where the page
# is to behave differently at each width.
VIEWERS = 8
start = datetime.now(timezone.utc) - timedelta(days=1)
with db.connect() as conn:
    for number in range(1, VIEWERS + 1):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, started_at, last_seen_at, ended_at, watched_seconds,"
            " paused_seconds, is_paused, is_active, audio_language)"
            " VALUES (?,?,?,'film','Duna',?,?,?,3600,0,0,0,?)",
            (f"s{number}", f"u{number}", f"Divak {number}",
             start.strftime(db.TIME_FORMAT),
             (start + timedelta(hours=1)).strftime(db.TIME_FORMAT),
             (start + timedelta(hours=1)).strftime(db.TIME_FORMAT),
             "cs" if number % 2 else "en"))
    conn.commit()

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, langstats, web  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(app)
client.post("/login", data={"username": "admin", "password": "longpassword"},
            follow_redirects=False)


print("--- between the limits: the web shows all, the phone only part ---")
people = langstats.languages_by_user(30, {})
check(len(people) == VIEWERS, f"the data has {len(people)} viewers")
check(web._limits()["people_limit"] == 10 and web._limits()["people_limit_mobile"] == 5,
      f"the default limits are 10 and 5 ({web._limits()})")

html = client.get("/video/languages?days=30").text
card = html.split('id="modal-languages"', 1)[0]
hidden = len(re.findall(r'<div class="desktop-only">', card))
check(hidden == VIEWERS - 5,
      f"above the mobile limit {hidden} bars hide (expected {VIEWERS - 5})")
check('class="btn-row mobile-only"' in card,
      "and on a phone there is a button instead of them")
check('id="modal-languages"' in html,
      "the dialog is on the page, even though it hides only on a phone")
check('<strong>' not in card or "Diváci jsou skrytí" not in card,
      "on a wide screen the summary button is not drawn")

print()
print("--- below both limits nothing hides ---")
# Non-vacuity: if `desktop-only` were always written, this case would find it
# too - and the test above would mean nothing.
client.post("/settings/interface", follow_redirects=False,
            data={"ui_max_streams": "10", "ui_max_streams_mobile": "3",
                  "ui_max_viewers": "10", "ui_max_viewers_mobile": "10"})
db.forget_settings()
html = client.get("/video/languages?days=30").text
card = html.split('id="modal-languages"', 1)[0]
check('<div class="desktop-only">' not in card, "no bar hides")
check('class="btn-row mobile-only"' not in card, "nor the button for a phone")
check('id="modal-languages"' not in html, "and the dialog is not drawn at all")

print()
print("--- below the mobile limit, above the web one: everything hides ---")
client.post("/settings/interface", follow_redirects=False,
            data={"ui_max_streams": "10", "ui_max_streams_mobile": "3",
                  "ui_max_viewers": "3", "ui_max_viewers_mobile": "3"})
db.forget_settings()
html = client.get("/video/languages?days=30").text
card = html.split('id="modal-languages"', 1)[0]
check("Diváci jsou skrytí" in card, "instead of the bars there is a summary button")
check('<div class="desktop-only">' not in card,
      "and the single bars are no longer dealt with - all are hidden")
check('id="modal-languages"' in html, "the dialog is ready")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
