# -*- coding: utf-8 -*-
"""A work the books sync does not know has no page: Most read, the readers card and the comparison give no /book/ link.

The history of an item that is not in `book` (deleted from the library, not synced yet) still counts, its title is
text only - a link would answer 404.

Run:
    .venv/bin/python tests/test_books_dead_links.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dead-links.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import cards_books, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
with db.connect() as conn:
    conn.execute("INSERT INTO book (id, kind, name, title, author, title_key, library_id)"
                 " VALUES ('known', 'audiobook', 'Known', 'Known', 'A', 'known|a', 'lib')")
    for key, item_id, name, seconds in (("s1", "gone1", "Deleted Audiobook", 7200), ("s2", "known", "Known", 600)):
        conn.execute("INSERT INTO playback (session_key, item_id, item_name, item_type, started_at, last_seen_at, watched_seconds,"
                     " user_name, user_id) VALUES (?, ?, ?, 'AudioBook', ?, ?, ?, 'a', 'u1')", (key, item_id, name, now, now, seconds))
    conn.commit()

analysis = stats.books_analysis(30)
rows = cards_books._with_links(stats.books_works(analysis, 10))
by_title = {r["title"]: r for r in rows}
check(by_title["Deleted Audiobook"]["link"] is None and by_title["Known"]["link"] == "/book/known",
      f"Most read: an unknown work has no link, a known one has ({[r['link'] for r in rows]})")
check(stats.books_readers(analysis)[0]["work_id"] is None and stats.books_readers(analysis)[0]["work"] == "Deleted Audiobook",
      "the readers card: the top work is text only")
comparison = stats.books_comparison(30, 30)
flat = repr(comparison)
check("/book/gone1" not in flat and "/book/known" in flat, "the comparison links only the known work")
check("Deleted Audiobook" in flat, "and still lists the unknown one by its title")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
