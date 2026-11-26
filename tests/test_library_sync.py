# -*- coding: utf-8 -*-
r"""The Library takes over every library Jellyfin has (menu by sections, 16. 11. 2026).

Until now the library sync stored only video libraries, so the music library
could not be browsed. Now:

* every library Jellyfin returns is stored (id, name, type, paths) - also
  types Jellyscope does not collect yet (photos, ...);
* items (movies, episodes) are still read only from video libraries
  (scanner: _sync_libraries returns those);
* the Library page has a tile for each: video as before, music with its
  albums and tracks, books with their works, an unsupported type with a sentence and no link;
* video lists over libraries (insights "by library", import by paths) keep
  to video libraries;
* a library missing from one run stays (a Jellyfin restart can return an
  empty list); missing from two runs in a row it is hidden, and it comes
  back when Jellyfin returns it again.

Responses: tests/music_responses/knihovny_10.json and knihovny_12.json
(recorded from Jellyfin 10.11 and 12.1) plus made-up types.

Run:
    .venv/bin/python tests/test_library_sync.py
"""
from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "library-sync.db")
os.environ["SECRET_KEY"] = "test-key-library-sync"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login  # noqa: E402
from _music import fake_jellyfin, recorded, fill_music  # noqa: E402
from jellyscope import accounts, db, demodata, i18n, insights, scanner, stats  # noqa: E402
from jellyscope.importers import creation  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def folder(item_id: str, name: str, kind: str | None, path: str) -> dict:
    data = {"Name": name, "ItemId": item_id, "Locations": [path]}
    if kind is not None:
        data["CollectionType"] = kind
    return data


RECORDED = recorded("knihovny_12")
MUSIC = next(f for f in RECORDED if f["CollectionType"] == "music")
MADE_UP = [folder("lib-books", "Knihy z poličky", "books", "/media/books"),
           folder("lib-photos", "Fotky", "photos", "/media/photos"),
           folder("lib-musicvideos", "Klipy", "musicvideos", "/media/clips"),
           folder("lib-boxsets", "Kolekce", "boxsets", "/media/collections"),
           folder("lib-playlists", "Playlisty", "playlists", "/media/playlists"),
           folder("lib-untyped", "Bez typu", None, "/media/mixed")]
FOLDERS = RECORDED + MADE_UP
# Jellyfin lists its Collections and Playlists folders among the libraries; they hold no
# content of their own (23. 11. 2026).
NOT_LIBRARIES = {"lib-boxsets", "lib-playlists"}
VIDEO_IDS = {f["ItemId"] for f in RECORDED if f["CollectionType"] in ("movies", "tvshows")} | {"lib-untyped"}


def sync(folders: list[dict]) -> list[dict]:
    client = fake_jellyfin(libraries=copy.deepcopy(folders), version="12")()
    return asyncio.run(scanner._sync_libraries(client))


def card_ids() -> set[str]:
    return {row["id"] for row in stats.library_cards()}


print("--- every library is stored, items are read from video libraries only ---")
returned = sync(FOLDERS)
stored = {row["id"]: row for row in db.query_all("SELECT * FROM libraries")}
check(set(stored) == {f["ItemId"] for f in FOLDERS} - NOT_LIBRARIES,
      f"every library is in the table, collections and playlists are not ({len(stored)})")
check(stored[MUSIC["ItemId"]]["collection_type"] == "music" and stored["lib-books"]["name"] == "Knihy z poličky"
      and "/media/books" in stored["lib-books"]["paths"], "with its name, type and paths")
check({lib["id"] for lib in returned} == VIDEO_IDS,
      f"items are read only from video libraries ({sorted(lib['name'] for lib in returned)})")
sync(recorded("knihovny_10"))
check(db.query_value("SELECT collection_type FROM libraries WHERE id = ?", (MUSIC["ItemId"],)) == "music",
      "Jellyfin 10.11 (just a music library) works the same")
sync(FOLDERS)

print()
print("--- tiles of the Library page ---")
fill_music()                                     # albums and tracks of the recorded music library
cards = {row["id"]: row for row in stats.library_cards()}
music = cards.get(MUSIC["ItemId"], {})
albums = db.query_value("SELECT COUNT(*) FROM music_album WHERE library_id = ? AND missing = 0", (MUSIC["ItemId"],))
tracks = db.query_value("SELECT COUNT(*) FROM music_track WHERE library_id = ? AND missing = 0", (MUSIC["ItemId"],))
check(music.get("kind") == "music" and music.get("albums") == albums > 0 and music.get("tracks") == tracks > 0
      and music.get("size_bytes", 0) > 0 and music.get("seconds", 0) > 0,
      f"the music library: albums, tracks, size and length ({ {k: music.get(k) for k in ('kind', 'albums', 'tracks')} })")
covers = db.query_all("SELECT id, cover_tag FROM music_album WHERE library_id = ? AND missing = 0 AND cover_tag <> ''"
                      " ORDER BY COALESCE(added_at, '') DESC, id", (MUSIC["ItemId"],))
check(bool(covers) and (music.get("cover_id"), music.get("cover_tag")) == (covers[0]["id"], covers[0]["cover_tag"]),
      f"the tile's cover is the newest album with a cover ({music.get('cover_id')})")
check(cards["lib-books"]["kind"] == "books" and cards["lib-photos"]["kind"] == "unsupported"
      and cards["lib-untyped"]["kind"] == "video", "books are collected (since 23. 11. 2026), photos are unsupported, a library without a type is video")
accounts.create("admin", "longpassword", is_admin=True)
page = login("admin").get("/library").text
tiles = re.split(r'(?=<(?:a|div) class="lib-card)', page)
photos = next((tile for tile in tiles if "Fotky" in tile and tile.startswith(("<a", "<div"))), "")
check(photos.startswith("<div") and "Obsah tohoto typu Jellyscope zatím nesbírá." in photos and "href=" not in photos,
      "the photos tile says Jellyscope does not collect them yet, and leads nowhere")
check(login("admin").get("/library/lib-photos").status_code == 404, "and a photos library has no page")
books = next((tile for tile in tiles if "Knihy z poličky" in tile and tile.startswith(("<a", "<div"))), "")
check(books.startswith("<a") and 'href="/library/lib-books"' in books and "nesbírá" not in books
      and login("admin").get("/library/lib-books").status_code == 200,
      "the books tile leads to the books library's page")
check(f'href="/library/{MUSIC["ItemId"]}"' in page and MUSIC["Name"] in page,
      "the music tile has Jellyfin's name and opens the library")

print()
print("--- video lists keep to video libraries ---")
by_library = {row["label"] for row in insights.storage_efficiency(30)}
video_names = {f["Name"] for f in FOLDERS if f["ItemId"] in VIDEO_IDS}
check(by_library == video_names, f"insights by library: video libraries only ({sorted(by_library)})")
paths = {library_id for _path, library_id in creation._libraries_by_path()}
check(paths <= VIDEO_IDS and "lib-books" not in paths, "import by paths: video libraries only")
check({row["id"] for row in stats.library_cards() if row["kind"] == "video"} == VIDEO_IDS, "video tiles are the video libraries")

print()
print("--- a library that disappears ---")
without_books = [f for f in FOLDERS if f["ItemId"] != "lib-books"]
# An empty answer is what a starting Jellyfin gives: it says nothing about any library, however
# often it comes - the frequent "recently added" sync can get it twice within minutes.
sync([])
sync([])
check(card_ids() >= {f["ItemId"] for f in FOLDERS} - NOT_LIBRARIES
      and db.query_value("SELECT MAX(missing_runs) FROM libraries") == 0,
      "empty answers from Jellyfin hide nothing and count nothing")
sync(without_books)
check("lib-books" in card_ids(), "missing from one run: still shown")
sync([])
check("lib-books" in card_ids(), "an empty answer in between does not count as a run without it")
sync(without_books)
check("lib-books" not in card_ids() and MUSIC["ItemId"] in card_ids(),
      "missing from two answers in a row: hidden, the others stay")
sync(FOLDERS)
check("lib-books" in card_ids(), "back in Jellyfin: shown again")
check(db.query_value("SELECT missing_runs FROM libraries WHERE id = 'lib-books'") == 0, "and its count starts over")
# Every library really removed from Jellyfin: the answers stay empty. Once Jellyfin has not
# listed a library for over an hour, an empty answer is no longer "Jellyfin is starting".
an_hour_and_more = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
with db.connect() as conn:
    conn.execute("UPDATE libraries SET synced_at = ?", (an_hour_and_more,))
sync([])
sync([])
check(not card_ids(), f"empty answers for over an hour count: every library is hidden ({sorted(card_ids())[:3]})")
sync(FOLDERS)
check(card_ids() >= {f["ItemId"] for f in FOLDERS} - NOT_LIBRARIES, "and they come back with Jellyfin")

print()
print("--- collections and playlists are not libraries (23. 11. 2026) ---")
# Version 1.9.0 stored Jellyfin's Collections folder as a library; the next sync removes it.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, synced_at, missing_runs)"
                 " VALUES ('old-boxsets', 'Collections', 'boxsets', '[]', ?, 0)", (db.utcnow(),))
check("old-boxsets" not in card_ids(), "the 1.9.0 row is not shown even before the next sync")
sync(FOLDERS)
check(db.query_value("SELECT COUNT(*) FROM libraries WHERE collection_type IN ('boxsets', 'playlists')") == 0,
      "a sync removes stored collections and playlists folders")
check("old-boxsets" not in card_ids() and not card_ids() & NOT_LIBRARIES, "and the Library page has no tile for them")

print()
print("--- the full sync and an empty or incomplete list of libraries ---")
# A starting Jellyfin answers, but lists no library yet - or only some of them. The items of a library it left
# out are still in Jellyfin: the sync must not send them to the archive, and an empty list must not report
# success with a growth snapshot of zero titles. A library is gone only after two lists in a row without it.
from _package import substitute  # noqa: E402

FILMS = {"lib-films-a": [f"film-a-{i}" for i in range(3)], "lib-films-b": [f"film-b-{i}" for i in range(4)]}
VIDEO_FOLDERS = [folder(library, library, "movies", f"/media/{library}") for library in FILMS]


class FullSyncJellyfin:
    """A Jellyfin for the whole sync: its list of libraries is VIDEO_SHOWN, the items come by library."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "FullSyncJellyfin":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def users(self) -> list[dict]:
        return [{"Id": "u-library-sync", "Name": "Divák"}]

    async def virtual_folders(self) -> list[dict]:
        return copy.deepcopy(VIDEO_SHOWN)

    async def item_count(self, parent_id: str | None = None, **kwargs) -> int:
        return len(FILMS.get(parent_id or "", []))

    async def iter_items(self, parent_id: str):  # noqa: ANN201
        for item_id in FILMS.get(parent_id, []):
            yield {"Id": item_id, "Name": item_id, "Type": "Movie", "RunTimeTicks": 60_000_000_000,
                   "DateCreated": "2026-01-01T00:00:00.0000000Z"}

    async def storage(self) -> dict:
        return {}

    async def items_by_ids(self, ids: list[str]) -> list[dict]:
        return []


def full_sync(shown: list[dict]) -> dict:
    """One full sync with these libraries listed - a day after the one before (the stamps go back)."""
    VIDEO_SHOWN[:] = shown
    with db.connect() as conn:
        conn.execute("UPDATE items SET synced_at = '2026-01-01 00:00:00'")
    restore = substitute(scanner, "JellyfinClient", FullSyncJellyfin)
    try:
        return asyncio.run(scanner.sync_library())
    finally:
        restore()


def live(library: str) -> int:
    return db.query_value("SELECT COUNT(*) FROM items WHERE library_id = ? AND is_missing = 0", (library,))


def task_text() -> str:
    record = scanner.last_scan("library") or {}
    return i18n.stored_text(record.get("message") or "", record.get("message_values") or "", "en")


def snapshots() -> int:
    return db.query_value("SELECT COUNT(*) FROM library_snapshot")


VIDEO_SHOWN: list[dict] = []
first = full_sync(VIDEO_FOLDERS)
check(first.get("status") == "ok" and live("lib-films-a") == 3 and live("lib-films-b") == 4,
      f"a full sync with both libraries: every film is in the library ({first})")
with db.connect() as conn:
    conn.execute("DELETE FROM library_snapshot")
result = full_sync([])
record = scanner.last_scan("library") or {}
check(live("lib-films-a") == 3 and live("lib-films-b") == 4,
      f"Jellyfin lists no library: no film goes to the archive ({live('lib-films-a')} + {live('lib-films-b')})")
check(result.get("status") == "error" and record.get("status") == "error"
      and "nothing was marked as gone" in task_text(),
      f"and the task says so instead of reporting success ({result.get('status')}, {task_text()!r})")
check(snapshots() == 0, "and writes no growth snapshot of an empty library")
result = full_sync([f for f in VIDEO_FOLDERS if f["ItemId"] == "lib-films-a"])
check(result.get("status") == "ok" and live("lib-films-a") == 3 and live("lib-films-b") == 4,
      f"one library left out of the list once: its films stay in the library ({live('lib-films-b')})")
check("lib-films-b" in task_text(), f"and the task log names it ({task_text()!r})")
FILMS["lib-films-a"].pop()
full_sync([f for f in VIDEO_FOLDERS if f["ItemId"] == "lib-films-a"])
check(live("lib-films-a") == 2, "a film gone from a library that was read still goes to the archive")
check(live("lib-films-b") == 0, "left out of two lists in a row: the library is gone and its films are archived")
full_sync(VIDEO_FOLDERS)
check(live("lib-films-b") == 4, "back in Jellyfin: its films are back too")

print()
print("--- demo data ---")
check(any(kind == "music" for _id, _name, kind in demodata.LIBRARIES), "the demo has a music library")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
