"""Shared helpers of the books tests.

Testing Jellyfin has no books: the answers in tests/books_responses/ are built by hand in the shape
of the music ones (tests/music_responses/): `libraries.json` (two libraries of the type "books" and
a movie one), `books.json` (e-books: epub with an author in `People` and an ISBN, pdf without an
author, cbz, mobi) and `audiobooks.json` (a multi-file audiobook - three `AudioBook` items with the
same `Album` and `AlbumArtist` -, an m4b, one with only `Artists`, one without any author).

`fake_jellyfin_books(...)` is a class in place of `JellyfinClient`: it does what the books sync asks
(`virtual_folders`, `items_page`), pages like the real server, filters by the item types asked for
and by the library (the item's path lies in a library's `Locations`) and records who asked for what.
It is substituted through `_package.substitute(books.sync, "JellyfinClient", fake_jellyfin_books(...))`.

The file starts with an underscore, so the suite does not run it as a test.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
RESPONSES = PROJECT / "tests" / "books_responses"


def recorded(name: str) -> Any:
    """A prepared answer (`libraries`, `books`, `audiobooks`) - a fresh copy every time."""
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


def all_items() -> list[dict[str, Any]]:
    """Every item (e-books and audiobooks), the newest added first - as the sync asks for them."""
    items = recorded("books")["Items"] + recorded("audiobooks")["Items"]
    return sorted(items, key=lambda i: i["DateCreated"], reverse=True)


def item(name: str, **changes: Any) -> dict[str, Any]:
    """An item after a prepared one (by `Name`), with values of its own (`Id`, `Name`, ...)."""
    found = next(i for i in all_items() if i["Name"] == name)
    return {**copy.deepcopy(found), **changes}


def _in_library(entry: dict[str, Any], library: dict[str, Any]) -> bool:
    sources = entry.get("MediaSources") or [{}]
    path = str(sources[0].get("Path") or "")
    if not path:                       # an item without a path is in every library (the tests' own bare items)
        return True
    return any(path.startswith(str(location)) for location in library.get("Locations") or [])


def fake_jellyfin_books(libraries: list[dict[str, Any]] | None = None,
                        items: list[dict[str, Any]] | None = None,
                        fail_at: int | None = None,
                        calls: list[tuple[str, int, int, str | None]] | None = None) -> type:
    """A class in place of `jellyfin.JellyfinClient` over prepared (or given) items.

    The lists are read again on every request, so a test may change them between syncs. `fail_at=10` =
    a request from the tenth item on ends with a Jellyfin error. `calls` collects
    (item types, start, limit, library).
    """
    from jellyscope.jellyfin import JellyfinError

    data = {"libraries": libraries if libraries is not None else recorded("libraries"),
            "items": items if items is not None else all_items()}

    class Client:
        DATA = data
        verze = "12.1.0"

        def __init__(self, base_url: str = "", api_key: str = "", *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def virtual_folders(self) -> list[dict[str, Any]]:
            await asyncio.sleep(0)
            return copy.deepcopy(self.DATA["libraries"])

        async def items_page(self, start_index: int, limit: int, item_types: str = "Movie,Episode",
                             user_id: str | None = None, parent_id: str | None = None,
                             sort_by: str = "SortName", sort_order: str = "Ascending",
                             fields: str | None = None) -> dict[str, Any]:
            await asyncio.sleep(0)
            if calls is not None:
                calls.append((item_types, start_index, limit, parent_id))
            if fail_at is not None and start_index >= fail_at:
                raise JellyfinError("Nepodarilo se spojit s Jellyfinem: spojeni bylo preruseno")
            wanted = set(item_types.split(","))
            library = next((lib for lib in self.DATA["libraries"] if lib.get("ItemId") == parent_id), None)
            found = [i for i in self.DATA["items"] if i.get("Type") in wanted and (library is None or _in_library(i, library))]
            return {"Items": copy.deepcopy(found[start_index:start_index + limit]),
                    "TotalRecordCount": len(found), "StartIndex": start_index}

    return Client


# Listening and reading for page tests: (reader, item name, seconds, hours ago, app, method, reason)
BOOK_LISTENS = (
    ("u-jana", "Kapitola 1", 1800, 2, "Finamp", "DirectPlay", None),
    ("u-jana", "Kapitola 2", 1800, 3, "Finamp", "DirectPlay", None),
    ("u-jana", "Duna", 900, 5, "Jellyfin Web", "DirectPlay", None),
    ("u-petr", "Hobit", 3600, 26, "Jellyfin Web", "Transcode", "AudioCodecNotSupported"),
    ("u-petr", "Duna", 1200, 30, "Jellyfin Web", "DirectPlay", None),
    ("u-eva", "Hobit", 2400, 24 * 5, "Symfonium", "DirectStream", None),
    ("u-eva", "Staré pověsti", 600, 24 * 40, "Jellyfin Web", "DirectPlay", None),
)
BOOK_READERS = {"u-jana": "Janička Čtenářová", "u-petr": "Petr Čtenář", "u-eva": "Eva Čtenářka"}


def fill_books(listens: tuple = BOOK_LISTENS) -> dict[str, str]:
    """Sync the prepared books and write listening and reading. Returns {item name: id}.

    The readers' names match nothing in the books - a test then tells by the bare text that a
    reader's name got onto a page. Audiobooks are `item_type` AudioBook, e-books Book.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime, timedelta

    from _package import substitute

    from jellyscope import books, db
    from jellyscope.books import sync

    restore = substitute(sync, "JellyfinClient", fake_jellyfin_books())
    try:
        _asyncio.run(books.synchronize())
    finally:
        restore()
    rows = {r["name"]: (r["id"], r["kind"]) for r in db.query_all("SELECT id, name, kind FROM book")}
    now = datetime.now(UTC)
    with db.connect() as conn:
        for uid, name in BOOK_READERS.items():
            conn.execute("INSERT INTO users (id, name) VALUES (?, ?) ON CONFLICT (id) DO NOTHING", (uid, name))
        for i, (uid, name, seconds, hours, app, method, reason) in enumerate(listens):
            item_id, kind = rows[name]
            when = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
                " play_method, transcode_reasons, media_runtime_ticks, started_at, last_seen_at, watched_seconds, paused_seconds,"
                " is_paused, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, 'Telefon', ?, ?, 400000000, ?, ?, ?, 0, 0, 0)",
                (f"books-test-{i}", uid, BOOK_READERS[uid], item_id, name, "AudioBook" if kind == "audiobook" else "Book",
                 app, method, reason, when, when, seconds))
        conn.commit()
    return {name: item_id for name, (item_id, _) in rows.items()}
