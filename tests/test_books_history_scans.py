# -*- coding: utf-8 -*-
"""The books checks over the history (reading_reported, books_unread) do not scan the whole playback table.

Both reach the history through the books (`item_id IN (SELECT id FROM book)`), so the index on (item_id, ...)
is used; the Overview computes reading_reported once per page. The query plan is checked only on SQLite.

Run:
    .venv/bin/python tests/test_books_history_scans.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "scans.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import cards_base, cards_books, db, stats  # noqa: E402
from jellyscope.stats import books_library, books_listening  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
now = db.utcnow()
with db.connect() as conn:
    conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, library_id)"
                 " VALUES ('b1', 'book', 'B1', 'B1', 'A', 'b1|a', 'lib')")
    for i in range(50):
        conn.execute("INSERT INTO playback (session_key, item_id, item_name, item_type, started_at, last_seen_at, watched_seconds, user_name)"
                     " VALUES (?, ?, 'Film', 'Movie', ?, ?, 600, 'a')", (f"m{i}", f"film{i}", now, now))
    conn.commit()

print("--- the plans ---")
captured: list[str] = []
real_value, real_all = db.query_value, db.query_all


def spy_value(sql, params=(), **kwargs):
    captured.append(sql)
    return real_value(sql, params, **kwargs)


def spy_all(sql, params=(), **kwargs):
    captured.append(sql)
    return real_all(sql, params, **kwargs)


db.query_value, db.query_all = spy_value, spy_all
try:
    books_listening.reading_reported()
    books_library.books_unread([])
finally:
    db.query_value, db.query_all = real_value, real_all
if db.current_dialect() == "sqlite":
    for sql in captured:
        plan = " | ".join(str(r["detail"]) for r in db.query_all("EXPLAIN QUERY PLAN " + sql, (30,) if "?" in sql else ()))
        scans = [part for part in plan.split(" | ") if "SCAN playback" in part or "SCAN p" == part[:6]]
        check("idx_playback_item_time" in plan and not scans, f"reads the history through the index, no full scan ({plan})")
else:
    print("(the query plan is checked only on SQLite)")

print()
print("--- behaviour ---")
check(books_listening.reading_reported() is False, "no reading reported")
with db.connect() as conn:
    conn.execute("INSERT INTO playback (session_key, item_id, item_name, item_type, started_at, last_seen_at, watched_seconds, user_name)"
                 " VALUES ('r1', 'b1', 'B1', 'Book', ?, ?, 900, 'a')", (now, now))
    conn.commit()
check(books_listening.reading_reported() is True, "an e-book playback of a known book is reported")
check(books_library.books_unread(None) == [], "a read book is not unread")

print()
print("--- once per page ---")
calls = []
real = stats.reading_reported
stats.reading_reported = lambda: calls.append(1) or real()
try:
    token = cards_base._memo.set({})
    try:
        cards_books._d_numbers(30)
        cards_books._d_numbers(30)
    finally:
        cards_base._memo.reset(token)
finally:
    stats.reading_reported = real
check(len(calls) == 1, f"reading_reported is computed once per page ({len(calls)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
