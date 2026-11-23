# -*- coding: utf-8 -*-
r"""The registry of content outside video (jellyscope/content_kinds.py).

Spec: docs/superpowers/specs/2026-11-16-content-kinds-size-design.md, part 1.
Disks, the total size and the daily snapshots read files of other kinds
(music now, books later) through it: today's files with their paths,
totals, and the history for computing the past.

Run:
    .venv/bin/python tests/test_content_kinds.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "content-kinds.db")
os.environ["SECRET_KEY"] = "test-key-content-kinds"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"

from jellyscope import content_kinds, db, demodata  # noqa: E402

failures = 0
db.init_db()
demodata.seed()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def sql(text: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(text, params)
        conn.commit()


print("--- the registry ---")
music = content_kinds.BY_KEY["music"]
check((music.table, music.path, music.size, music.added, music.last_seen, music.missing)
      == ("music_track", "path", "size_bytes", "added_at", "synced_at", "missing"),
      "music -> the tracks table and its columns")
check(music.label == "Music" and music.share == "of which music {size}", "labels are translation keys")
check(tuple(content_kinds.BY_KEY.values()) == content_kinds.KINDS, "BY_KEY holds every kind")

print()
print("--- totals ---")
present = db.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM music_track WHERE missing = 0")
totals = content_kinds.totals()
check(totals["music"]["items"] == int(present["n"]) > 0 and totals["music"]["size"] == int(present["b"]) > 0
      and totals["music"]["without_size"] == 0, f"tracks in Jellyfin and their size ({totals['music']})")
ids = [r["id"] for r in db.query_all("SELECT id FROM music_track WHERE missing = 0 ORDER BY id LIMIT 5")]
sql("UPDATE music_track SET size_bytes = NULL WHERE id = ?", (ids[0],))
check(content_kinds.totals()["music"]["without_size"] == 1, "a track without a size is counted as such")
sql("UPDATE music_track SET missing = 1, synced_at = '2026-10-01 08:00:00' WHERE id = ?", (ids[1],))
after = content_kinds.totals()["music"]
check(after["items"] == int(present["n"]) - 1, "a track no longer in Jellyfin is not counted")

print()
print("--- today's files ---")
sql("UPDATE music_track SET path = '' WHERE id = ?", (ids[2],))
sql("UPDATE music_track SET path = NULL WHERE id = ?", (ids[3],))
# A path not asked yet (NULL, before the first full sync): the kind waits whole, a part of it
# measured on a disk would be stored as its measurement (final review 16. 11. 2026).
check([f for f in content_kinds.current_files() if f[0] == "music"] == [] and "music" not in content_kinds.kinds_with_paths(),
      "a track whose path was not asked yet keeps the whole kind off the disks")
sql("UPDATE music_track SET path = '' WHERE id = ?", (ids[3],))
files = [f for f in content_kinds.current_files() if f[0] == "music"]
paths = {path for _, path, _ in files}
check(len(files) == int(present["n"]) - 3 and all(kind == "music" for kind, _, _ in files),
      f"present tracks with a path only ({len(files)})")
check(all(path.startswith("/media/music/") and path.endswith(".flac") for path in paths),
      "demo tracks have paths under /media/music/")
check(any(size == 0 for _, _, size in files), "a track without a size comes with 0")

print()
print("--- history ---")
sql("UPDATE music_track SET missing = 1, synced_at = '' WHERE id = ?", (ids[4],))
everything = content_kinds.history()
history = [row for row in everything if row["kind"] == "music"]
by_path = {row["path"]: row for row in history}
gone = db.query_one("SELECT path, added_at FROM music_track WHERE id = ?", (ids[1],))
check(by_path.get(gone["path"], {}).get("gone") == "2026-10-01"
      and by_path[gone["path"]]["added"] == str(gone["added_at"])[:10],
      "a track gone from Jellyfin ends on the day of the last sync that saw it")
unknown = db.query_one("SELECT path FROM music_track WHERE id = ?", (ids[4],))
check(unknown["path"] not in by_path, "a track gone on an unknown day is left out")
check(all(row["kind"] == "music" and len(row["added"]) == 10 for row in history)
      and sum(1 for row in history if row["gone"]) == 1, "the others are present, with a day of adding")
check(len(history) == int(present["n"]) - 1, "every track with a known day (present ones and the one gone)")

print()
print("--- books (23. 11. 2026) ---")
books = content_kinds.BY_KEY["books"]
check((books.table, books.path, books.size, books.added, books.last_seen, books.missing)
      == ("book", "path", "size_bytes", "added_at", "synced_at", "missing")
      and books.label == "Books" and books.share == "of which books {size}", "books -> the table book and its columns")
book_present = db.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM book WHERE missing = 0")
check(content_kinds.totals()["books"]["items"] == int(book_present["n"]) > 0
      and content_kinds.totals()["books"]["size"] == int(book_present["b"]) > 0, "totals count the demo books")
book_files = [f for f in content_kinds.current_files() if f[0] == "books"]
check(len(book_files) == int(book_present["n"]) and all(p.startswith(("/media/books/", "/media/audiobooks/")) for _, p, _ in book_files),
      "the demo books have paths under /media/books/ and /media/audiobooks/")
check(sum(1 for row in everything if row["kind"] == "books") == int(book_present["n"]), "the history has every book with a day of adding")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
