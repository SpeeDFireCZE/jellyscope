# -*- coding: utf-8 -*-
r"""The whole ranking of the most watched titles - a dialog above the chart.

The chart on the Overview shows ten titles. The link under it opens a dialog with
all of them; without JavaScript it leads to a page with the same table. One route
serves both and it is told by the header `fetch` sends.

Run:
    .venv/bin/python tests/test_top_items_all.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "top.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

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
accounts.create("reader", "longpassword", is_admin=False)

# Fourteen movies, each watched for a different time - more than ten, so there is
# something to hide behind the chart.
when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    for i in range(1, 15):
        conn.execute("INSERT INTO items (id, name, type, is_missing)"
                     " VALUES (?, ?, 'Movie', 0)", (f"f{i}", f"FilmQX{i:02d}"))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, watched_seconds,"
            " is_active) VALUES (?, 'u1', 'Jana', ?, ?, 'Movie', ?, ?, ?, 0)",
            (f"s{i}", f"f{i}", f"FilmQX{i:02d}", when, when, 600 * i))
    conn.execute("INSERT INTO items (id, name, type, series_name, series_id, is_missing)"
                 " VALUES ('e1', 'Díl 1', 'Episode', 'SerialQX', 'ser1', 0)")
    conn.execute("INSERT INTO items (id, name, type, is_missing)"
                 " VALUES ('ser1', 'SerialQX', 'Series', 0)")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id,"
        " item_name, item_type, series_name, started_at, last_seen_at,"
        " watched_seconds, is_active) VALUES ('se', 'u1', 'Jana', 'e1', 'Díl 1',"
        " 'Episode', 'SerialQX', ?, ?, 99999, 0)", (when, when))
    conn.commit()

k = TestClient(web.app)
k.post("/login", data={"username": "admin", "password": "longpassword",
                       "method": "local"})

print("--- the Overview has a link to the whole ranking ---")
overview = k.get("/?top_kind=both").text
check('data-modal-load="modal-top-all"' in overview, "the link carries the dialog's id")
check('href="/video/insights/top?kind=both"' in overview, "and the address with the kind")
check('id="modal-top-all"' in overview, "the dialog is on the page")
check(overview.count("FilmQX") <= 10 * 2, "the chart itself shows at most ten")

print()
print("--- the partial for the dialog ---")
piece = k.get("/video/insights/top?kind=both", headers={"X-Requested-With": "fetch"}).text
check("<nav" not in piece, "it is a partial, not the whole page")
check(piece.count("<tr>") == 1 + 15, f"all fifteen titles ({piece.count('<tr>') - 1})")
check(piece.index("SerialQX") < piece.index("FilmQX14"),
      "sorted from the most watched (the series is first)")
check("seriál" in piece, "the series is marked")
check('href="/item/f14"' in piece, "a movie can be clicked through")

print()
print("--- without JavaScript the whole page ---")
page = k.get("/video/insights/top?kind=both").text
check("<nav" in page and "FilmQX01" in page, "a page with the menu and the table")

print()
print("--- the kind filter holds ---")
movies_only = k.get("/video/insights/top?kind=movies", headers={"X-Requested-With": "fetch"}).text
check("SerialQX" not in movies_only and "FilmQX01" in movies_only, "only movies")
series_only = k.get("/video/insights/top?kind=series", headers={"X-Requested-With": "fetch"}).text
check("SerialQX" in series_only and "FilmQX01" not in series_only, "only series")

print()
print("--- rights: a reader without the rankings may not ---")
db.set_setting("reader_sees_charts", "0")
db.forget_settings()
c = TestClient(web.app)
c.post("/login", data={"username": "reader", "password": "longpassword",
                       "method": "local"})
check(c.get("/video/insights/top", follow_redirects=False).status_code == 403,
      "the ranking is closed like the Insights")
db.set_setting("reader_sees_charts", "1")
db.forget_settings()
check(c.get("/video/insights/top", follow_redirects=False).status_code == 200,
      "and open with the rankings switched on")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
