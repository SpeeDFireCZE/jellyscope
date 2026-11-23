# -*- coding: utf-8 -*-
"""Imported history of a book stays the book: no relinking by name to a film of the same name, no watching.

A book "Dune" and a film "Dune": imported rows of the book (no type, or typed "Movie" by an importer that
knows only movies) must not be relinked to the film, the books sync gives them their type, and the
watching numbers never count them. Jellystat's AudioBook / Book stay their own kinds.

Run:
    .venv/bin/python tests/test_import_books.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "import_books.db")

from jellyscope import db, stats  # noqa: E402
from jellyscope.books import sync  # noqa: E402
from jellyscope.importers import linking  # noqa: E402
from jellyscope.importers.jellystat import _jellystat_item_type  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
now = db.utcnow()
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing, synced_at) VALUES ('movie1', 'Dune', 'Movie', 0, ?)", (now,))
    conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, library_id)"
                 " VALUES ('book1', 'audiobook', 'Dune', 'Dune', 'Frank Herbert', 'dune|frank herbert', 'lib')")
    for key, item_type in (("s1", None), ("s2", "Movie")):
        conn.execute("INSERT INTO playback (session_key, item_id, item_name, item_type, started_at, last_seen_at, watched_seconds, user_name)"
                     " VALUES (?, 'book1', 'Dune', ?, ?, ?, 3600, 'a')", (key, item_type, now, now))
    conn.commit()

print("--- relinking by name ---")
check(linking._orphan_records() == [], "a row of a known book is no orphan")
linking._link_by_name()
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'movie1'") == 0, "the book's history is not relinked to the film")

print()
print("--- the books sync pairing ---")
sync._pair_history()
types = {r["session_key"]: r["item_type"] for r in db.query_all("SELECT session_key, item_type FROM playback")}
check(types == {"s1": "AudioBook", "s2": "AudioBook"}, f"a row without a type and one typed Movie become AudioBook ({types})")
check(db.query_value(f"SELECT COUNT(*) FROM playback p WHERE 1 = 1 {stats.watching_only('p.')}") == 0, "none of it counts as watching")

print()
print("--- Jellystat types ---")
check(_jellystat_item_type({"ItemType": "AudioBook", "NowPlayingItemName": "Dune"}) == "AudioBook", "AudioBook stays AudioBook")
check(_jellystat_item_type({"ItemType": "Book", "NowPlayingItemName": "Dune"}) == "Book", "Book stays Book")
check(_jellystat_item_type({"NowPlayingItemName": "Dune"}) == "Movie", "an untyped row is still a Movie")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
