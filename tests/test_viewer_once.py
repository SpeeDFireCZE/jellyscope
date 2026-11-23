r"""A viewer is in the summaries once - by id, with the current name.

The history keeps the name from the time of the playback (refresh_playback_metadata
does not overwrite it on purpose). A summary grouped by name thus split
a renamed viewer into two columns with the hours split - and the other way round
merged two different people with the same name into one. Every summary by viewers
is watched here:

* a renamed viewer = one row with the current name (from `users`);
* two people with the same name = two rows, each with their id;
* rows without user_id (an import without UserId) are split by name.

Run:
    .venv/bin/python tests/test_viewer_once.py
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
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "divak_jednou.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

import re  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, langstats, stats  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
a_day_ago = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).strftime(db.TIME_FORMAT)
PLAYS = [  # key, user_id, the name in the history, seconds
    ("jana-stara", "u-jana", "JanaStara", 1800),   # renamed: two names, one person
    ("jana-nova", "u-jana", "Jana", 3600),
    ("petr-1", "u-petr", "Petr", 2400),            # two different Petrs
    ("petr-2", "u-petr2", "Petr", 1200),
    ("import-a", None, "ImportA", 600),            # an import without UserId
    ("import-b", None, "ImportB", 900),
]
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing)"
                 " VALUES ('f1', 'Film', 'Movie', 'filmy', 0)")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-jana', 'Jana', 0, 0), ('u-petr', 'Petr', 0, 0), ('u-petr2', 'Petr', 0, 0)")
    for key, uid, name, seconds in PLAYS:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active,"
            " bitrate, audio_language)"
            " VALUES (?, ?, ?, 'f1', 'Film', 'Movie', 'filmy', ?, ?, ?, ?, 0, 0, 8000000, 'cze')",
            (key, uid, name, a_day_ago, a_day_ago, a_day_ago, seconds))
    conn.commit()

EXPECTED = {  # (user_id, name) -> hours
    ("u-jana", "Jana"): 1.5, ("u-petr", "Petr"): 2400 / 3600, ("u-petr2", "Petr"): 1200 / 3600,
    (None, "ImportA"): 600 / 3600, (None, "ImportB"): 900 / 3600,
}


def compare(summary: str, rows: list[tuple]) -> None:
    got = sorted((((uid, name), round(hours, 4)) for uid, name, hours in rows), key=str)
    want = sorted(((key, round(hours, 4)) for key, hours in EXPECTED.items()), key=str)
    check(got == want, f"{summary}: every viewer once, with the current name ({got})")


print("--- summaries by viewers ---")
compare("stats.top_users",
        [(r["user_id"], r["label"], float(r["hours"])) for r in stats.top_users(30, limit=20)])
compare("stats.library_activity",
        [(r["user_id"], r["label"], float(r["hours"]))
         for r in stats.library_activity("filmy", 30)["top_users"]])
compare("langstats.languages_by_user",
        [(p["user_id"], p["name"], p["total"]) for p in langstats.languages_by_user(30, {})])
jana = next((p for p in langstats.languages_by_user(30, {}) if p["user_id"] == "u-jana"), None)
check(jana is not None and len(jana["segments"]) == 1,
      f"languages: the renamed one has Czech once, not twice ({jana and len(jana['segments'])})")
compare("stats.bandwidth_by (viewers)",
        [(r["user_id"], r["label"], r["byte_count"] * 8 / 8000000 / 3600)
         for r in stats.bandwidth_by(30, "user_name")])

print()
print("--- a viewer without an id has no link into the void ---")
# The administrator may open every viewer's page - but an imported name without
# UserId has no page. The link /users/None leads to a 404.
accounts.create("admin", "longpassword", is_admin=True)
k = TestClient(app)
k.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
for address in ("/", "/history", "/item/f1", "/library/filmy?tab=activity", "/video/languages", "/network"):
    r = k.get(address)
    bad = sorted(set(re.findall(r'href="(/users/(?:None|)?)"', r.text)))
    check(r.status_code == 200 and not bad, f"{address}: {r.status_code}, links without a viewer {bad}")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
