# -*- coding: utf-8 -*-
"""The library sync must straighten imported history by itself.

The previous test (test_import_then_connect) checks that *the straightening works*. This one checks something
else and just as important: that it **really runs** when the user connects Jellyfin for the first time and lets
the library download. If the call got lost, everything else would be right and the imported data would still
stay with question marks.

There is no Jellyfin here - a substituted client returning prepared data takes its place. So we test the real
`scanner.sync_library()`, not a description of it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in every submodule of the package scanner

_tmp = tempfile.mkdtemp()
# Its own home: without it DATABASE_PATH would override the database chosen in data/database.json and the test
# would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sync.db")

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


USER = "aa11bb22cc33dd44ee55ff6600112233"
MOVIE = "0011223344556677889900aabbccddee"


class FakeClient:
    """The smallest possible stand-in for JellyfinClient - only what the scanner needs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": USER, "Name": "Karel", "Policy": {"IsAdministrator": False}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": "lib-filmy", "Name": "Filmy", "CollectionType": "movies"}]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        return 1

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        yield {
            "Id": MOVIE,
            "Name": "Matrix",
            "Type": "Movie",
            "DateCreated": "2026-01-01T00:00:00.0000000Z",
            "ProviderIds": {"Tmdb": "603"},
            "RunTimeTicks": 60_000_000_000,
        }


db.init_db()

# The history as it stays after an import without a connected Jellyfin: we know no name, no type, and the user's
# id is written with dashes.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 item_name, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('import:jst:1:x', 'aa11bb22-cc33-dd44-ee55-ff6600112233', '?',
                   ?, 'Matrix', ?, ?, 3600, 0)""",
        (MOVIE, db.utcnow(), db.utcnow()),
    )

before = db.query_one("SELECT user_name, item_type, library_id FROM playback")
check(before["user_name"] == "?", "before the sync there is a question mark instead of the name")
check(before["item_type"] is None, "before the sync the item's type is not known")


print()
print("--- the library sync ---")
substitute(scanner, "JellyfinClient", FakeClient)
result = asyncio.run(scanner.sync_library())
check(result.get("status") == "ok", f"the sync passed: {result}")
check(result.get("users") == 1 and result.get("items") == 1,
      f"the user and the title were downloaded: {result}")


print()
print("--- the history straightened without anybody asking ---")
after = db.query_one(
    "SELECT user_id, user_name, item_type, library_id FROM playback")
check(after["user_id"] == USER, "the user's id is in Jellyfin's shape")
check(after["user_name"] == "Karel", "the user's name was filled in")
check(after["item_type"] == "Movie", "the item's type was filled in")
check(after["library_id"] == "lib-filmy", "the library was filled in")

item = db.query_one("SELECT tmdb_id FROM items WHERE id = ?", (MOVIE,))
check(item["tmdb_id"] == "603", "the title has the tmdb ID from Jellyfin")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
