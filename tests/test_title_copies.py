# -*- coding: utf-8 -*-
r"""Copies of the same movie in one library as one record.

A movie that is in one Jellyfin library twice (two items with the same
TMDB id - two files in two folders) used to be in Jellyscope twice: two
tiles in the library, two titles in the ranking, two histories and no link
between them. A series' episode twice was always under one record though
(it belongs to the series). Watched:

A) the recount (`scanner.recount_copies`): the main copy is the one that has been
   in the library the longest; movies in different libraries, without a TMDB id, series'
   episodes and archived items are not merged; a repeated recount writes
   nothing; a copy disappearing and moving to another library;
B) the library's list, "Recently added" and the rankings: one record, sums;
C) the detail: the files of all copies in the "Soubor" card, the plays together;
D) the click-throughs and posters from the *Arr and Seerr cards lead to the main copy.

Nothing is merged in the database - the scanner only notes the main one
by the secondary copy (`items.main_id`). A movie in two different libraries stays
two records everywhere.

Spec: docs/superpowers/specs/2026-11-14-kopie-titulu-design.md

Run:
    .venv/bin/python tests/test_title_copies.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kopie.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, scanner  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def db_exec(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)
        conn.commit()


def film(iid: str, tmdb: str, library: str = "filmy", added: str | None = "2026-01-01 10:00:00", missing: int = 0,
         name: str | None = None, height: int = 1080, size: int = 8 * 10**9) -> None:
    db_exec("INSERT INTO items (id, name, type, library_id, tmdb_id, path, width, height, size_bytes, date_created,"
            " synced_at, is_missing) VALUES (?, ?, 'Movie', ?, ?, ?, 1920, ?, ?, ?, ?, ?)",
            (iid, name or f"Film {tmdb or iid}", library, tmdb or None, f"/media/{library}/{iid}/film.mkv", height, size,
             added, db.utcnow(), missing))


def episode(iid: str, number: int, added: str = "2026-01-01 10:00:00") -> None:
    db_exec("INSERT INTO items (id, name, type, library_id, tmdb_id, series_id, series_name, parent_index_number,"
            " index_number, path, size_bytes, date_created, synced_at, is_missing) VALUES (?, ?, 'Episode', 'serialy', '500',"
            " 's1', 'Seriál S', 1, ?, ?, ?, ?, ?, 0)",
            (iid, f"Díl {number}", number, f"/media/serialy/{iid}.mkv", 10**9, added, db.utcnow()))


def main_copies() -> dict[str, str]:
    """{the secondary copy's id: the main one's id} - only items that have a main copy."""
    return {r["id"]: r["main_id"] for r in db.query_all("SELECT id, main_id FROM items WHERE main_id IS NOT NULL")}


db_exec("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
db_exec("INSERT INTO libraries (id, name, collection_type) VALUES ('test_filmy', 'Test filmy', 'movies')")
db_exec("INSERT INTO libraries (id, name, collection_type) VALUES ('serialy', 'Seriály', 'tvshows')")

print("--- A) recounting the copies ---")
film("a1", "100", added="2026-02-01 10:00:00")
film("a2", "100")
film("a3", "100", added=None)
film("b1", "200")
film("b2", "200", library="test_filmy")      # the same movie in another library
film("c1", "")
film("c2", "")                               # two movies without a TMDB id
film("d1", "400")
film("d2", "400", missing=1)                 # the second copy is in the archive
episode("e1", 1)
episode("e2", 1)                             # a series' episode twice
check(scanner.recount_copies() == 2 and main_copies() == {"a1": "a2", "a3": "a2"},
      f"the main one is the copy that has been in the library the longest; without a date behind the others ({main_copies()})")
check(scanner.recount_copies() == 0, "a repeated recount writes nothing")
db_exec("UPDATE items SET is_missing = 1 WHERE id = 'a1'")       # the middle one by date disappears
check(scanner.recount_copies() == 1 and main_copies() == {"a3": "a2"},
      f"a vanished copy stops being a copy, the rest holds ({main_copies()})")
db_exec("UPDATE items SET is_missing = 1 WHERE id = 'a2'")       # the main one disappears
check(scanner.recount_copies() == 1 and main_copies() == {}, f"one was left - there is nothing to merge ({main_copies()})")
db_exec("UPDATE items SET is_missing = 0 WHERE id IN ('a1', 'a2')")
scanner.recount_copies()
db_exec("UPDATE items SET library_id = 'test_filmy' WHERE id = 'a3'")
check(scanner.recount_copies() == 1 and main_copies() == {"a1": "a2"},
      f"a copy moved to another library stops being a copy ({main_copies()})")

print()
print("--- B) the library's list, Recently added and the rankings ---")
from _apps import login  # noqa: E402
from jellyscope import accounts, stats  # noqa: E402

GB = 10**9


def play(key: str, item: str, seconds: int = 3000) -> None:
    when = db.utcnow()
    db_exec("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at, last_seen_at,"
            " watched_seconds) VALUES (?, 'u1', 'Jana', ?, 'x', ?, ?, ?)", (key, item, when, when, seconds))


# A clean table: "Film A" twice in the Filmy library (the 4K added later, the main one is the older 1080p),
# "Film B" once in each of two libraries. Every item played once.
db_exec("DELETE FROM items")
film("a1", "100", added="2026-02-01 10:00:00", name="Film A", height=2160, size=40 * GB)
film("a2", "100", name="Film A", height=1080, size=8 * GB)
film("b1", "200", name="Film B")
film("b2", "200", library="test_filmy", name="Film B")
episode("e1", 1)
episode("e2", 1)
db_exec("INSERT INTO users (id, name) VALUES ('u1', 'Jana')")
for number, item in enumerate(("a1", "a2", "b1", "b2", "e1", "e2")):
    play(f"p{number}", item)
scanner.recount_copies()
accounts.create("admin", "longpassword", is_admin=True)
admin = login("admin")

rows = {r["id"]: r for r in stats.library_rows(50, 0, "filmy")}
check(set(rows) == {"a2", "b1"} and stats.library_rows_count("filmy") == 2,
      f"a movie with copies is one tile with the main copy's id ({sorted(rows)}, {stats.library_rows_count('filmy')})")
a = rows.get("a2") or {}
check((a.get("episode_count"), a.get("is_series"), a.get("size_bytes"), a.get("height"), a.get("plays")) == (2, 0, 48 * GB, 2160, 2),
      f"the tile: the number of files, the size as a sum, the highest resolution, the plays as a sum ({dict(a) if a else a})")
check({r["id"] for r in stats.library_rows(50, 0, "test_filmy")} == {"b2"}, "the same movie in another library stays its tile")
html = admin.get("/library/filmy?tab=media").text
check(html.count('href="/item/a2"') == 1 and 'href="/item/a1"' not in html and "2× na disku" in html,
      "the tile leads to the main copy and says the movie is on the disk 2×")
check("1× na disku" not in html, "a movie with one file has no mark")
series_rows = stats.library_rows(50, 0, "serialy")
check(len(series_rows) == 1 and series_rows[0]["episode_count"] == 2 and series_rows[0]["is_series"] == 1,
      "a series unchanged: one tile, two files")
db_exec("UPDATE items SET is_missing = 1 WHERE id = 'a1'")
scanner.recount_copies()
check({r["id"] for r in stats.library_rows(50, 0, "filmy", archived=True)} == {"a1"}
      and {r["id"] for r in stats.library_rows(50, 0, "filmy")} == {"a2", "b1"},
      "an archived copy is in the Archive alone, the live one in the library")
db_exec("UPDATE items SET is_missing = 0 WHERE id = 'a1'")
scanner.recount_copies()

top = stats.top_items(365, limit=10, kind=stats.KIND_MOVIE)
film_a = [t for t in top if t["label"] == "Film A"]
check(len(film_a) == 1 and film_a[0]["plays"] == 2 and film_a[0]["detail_url"] == "/item/a2",
      f"the ranking: one title, the plays summed, a link to the main copy ({[(t['label'], t['plays'], t['detail_url']) for t in top]})")
check(sorted(t["detail_url"] for t in top if t["label"] == "Film B") == ["/item/b1", "/item/b2"],
      "a movie in two libraries: two titles as before")
recent = [r["title"] for r in stats.recently_added(18)]
check(recent.count("Film A") == 1 and recent.count("Film B") == 2 and recent.count("Seriál S") == 1,
      f"Recently added: the copies once, another library apart, a series once ({recent})")

print()
print("--- C) the detail: the files of all copies in the Soubor card, the plays together ---")
from _apps import tile  # noqa: E402
from jellyscope import access  # noqa: E402


def version(item: str, source: str, order: int, height: int) -> None:
    """A version inside one item (Jellyfin joined two files in one folder)."""
    db_exec("INSERT INTO item_versions (item_id, source_id, position, name, path, width, height, size_bytes) VALUES"
            " (?, ?, ?, ?, ?, 1920, ?, ?)", (item, source, order, f"{height}p", f"/media/filmy/{item}/{source}.mkv", height, 5 * GB))


check(stats.title_items("a1") == ["a2", "a1"] and stats.title_items("a2") == ["a2", "a1"]
      and stats.title_items("b1") == ["b1"] and stats.title_items("e1") == ["e1"],
      "a title's copies: the main one first; a movie with one item and a series' episode only themselves")
files = stats.title_files("a1")
check([(s["item_id"], s["href"], s["active"]) for s in files] == [("a2", "/item/a2", False), ("a1", "/item/a1", True)]
      and files[1]["height"] == 2160 and files[1]["size_bytes"] == 40 * GB,
      f"the files of both copies, the selected one is the open item's file ({[(s['item_id'], s['href'], s['active']) for s in files]})")
check(stats.title_files("b1") == [] and stats.title_files("e1") == [], "a title with one file has no switch")
for address in ("/item/a1", "/item/a2"):
    html = admin.get(address).text
    file_card = html[html.find("<h2>Soubor</h2>"):]
    check('href="/item/a1"' in file_card and 'href="/item/a2"' in file_card and "tenhle titul je na disku 2&times;" in file_card,
          f"{address}: the chips of both copies are in the Soubor card")
    check(tile(html, "Přehrání") == "2" and html.count("Jana") >= 2,
          f"{address}: the plays of both copies together ({tile(html, 'Přehrání')})")
    own, second = f"/media/filmy/{address[-2:]}/film.mkv", f"/media/filmy/{'a2' if address.endswith('a1') else 'a1'}/film.mkv"
    check(file_card.count(own) == 2 and file_card.count(second) == 1 and f'title="{second}"' in file_card,
          f"{address}: the path in the details belongs to the selected copy; every chip carries its file's path (two copies in the same quality have the same label otherwise)")
# A minor from the review: a long list of ids goes to the database in batches - and the sorting, the limit and the summary
# held only inside one batch. With a batch of one item it must come out the same as with one batch.
summary_whole, last_whole = stats.item_playback_summary("a1"), stats.item_playback("a1", limit=1)
original_query_all_in = db.query_all_in
db.query_all_in = lambda sql, values, before=(), batch=400: original_query_all_in(sql, values, before, 1)
summary_batched, last_batched, all_batched = stats.item_playback_summary("a1"), stats.item_playback("a1", limit=1), stats.item_playback("a1")
db.query_all_in = original_query_all_in
check((summary_batched.get("plays"), summary_batched.get("users"), summary_batched.get("last_played")) == (2, 1, summary_whole["last_played"])
      and abs(summary_batched["hours"] - summary_whole["hours"]) < 1e-9,
      f"a title's playback summary does not depend on the batches ({ {k: summary_batched.get(k) for k in ('plays', 'users')} })")
check(len(last_batched) == 1 and last_batched[0]["id"] == last_whole[0]["id"]
      and [r["id"] for r in all_batched] == [r["id"] for r in stats.item_playback("a1")],
      f"a title's last playback: the limit and the order hold across all batches ({len(last_batched)})")
version("a1", "v1", 0, 2160)
version("a1", "v2", 1, 1080)
three = stats.title_files("a1", "v2")
check([s["href"] for s in three] == ["/item/a2", "/item/a1?version=v1", "/item/a1?version=v2"] and [s["active"] for s in three] == [False, False, True],
      f"a copy with versions: three files and ?version= selects the right one ({[(s['href'], s['active']) for s in three]})")
check([s["active"] for s in stats.title_files("a1")] == [False, True, False], "without ?version= the open item's first version is selected")
check("tenhle titul je na disku 3&times;" in admin.get("/item/a1?version=v2").text, "the detail counts all three files")
# A minor from the review: the tile counted items (2×), the detail files (3×). They count the same - files.
movie_tiles = {r["id"]: r for r in stats.library_rows(50, 0, "filmy")}
library_html = admin.get("/library/filmy?tab=media").text
check(movie_tiles["a2"]["file_count"] == 3 and movie_tiles["b1"]["file_count"] == 1 and "3× na disku" in library_html
      and "2× na disku" not in library_html and ">3&times;<" in library_html.replace(" ", ""),
      f"the tile counts files like the detail: a copy with two versions and the second copy are 3× ({movie_tiles['a2'].get('file_count')})")
db_exec("DELETE FROM item_versions")
accounts.create("reader", "longpassword")
accounts.save_permissions(accounts.get_by_name("reader")["id"], access.pack_permissions(["library"], False))
db.forget_settings()
reader_html = login("reader").get("/item/a1").text
check(tile(reader_html, "Přehrání") == "2" and "Jana" not in reader_html,
      "without “…including who watched it”: the summary for both copies yes, who watched no")
episode_html = admin.get("/item/e1").text
check('href="/item/e2"' in episode_html and "na disku" not in episode_html and tile(episode_html, "Přehrání") == "1",
      "a series' episode unchanged: a link to the second file, only its own plays")

print()
print("--- D) click-throughs and posters from the *Arr and Seerr cards ---")
# Radarr and Seerr know a movie only by its TMDB id - and that fits both copies. The item
# with the lower id used to win, so a random copy; now the one that stands for the record.
for title_id, name, tmdb in ((1, "Film A", "100"), (2, "Film B", "200")):
    db_exec("INSERT INTO radarr_title (title_id, name, tmdb_id) VALUES (?, ?, ?)", (title_id, name, tmdb))
check(stats.radarr_detail_links({1, 2}) == {1: "/item/a2", 2: "/item/b1"},
      f"Radarr: copies in one library lead to the main one; a movie in two libraries to the lower id as before ({stats.radarr_detail_links({1, 2})})")
db_exec("UPDATE items SET image_tag = 'otisk-' || id")
check(stats.seerr_posters([("movie", "", "100"), ("movie", "", "200")])
      == {("movie", "", "100"): ("a2", "otisk-a2"), ("movie", "", "200"): ("b1", "otisk-b1")},
      f"Seerr: the main copy's poster and link ({stats.seerr_posters([('movie', '', '100'), ('movie', '', '200')])})")
# A minor from the review: Seerr points at one item, but the viewer may have played the other copy of the same
# movie - "Requested and watching?" therefore looks for the plays of all the title's copies.
from jellyscope.stats import seerr as stats_seerr  # noqa: E402

per_request = stats_seerr._items_by_id(["a1", "a2", "b1"])
check({k: sorted(v) for k, v in per_request.items()} == {("movie", "a1"): ["a1", "a2"], ("movie", "a2"): ["a1", "a2"], ("movie", "b1"): ["b1"]},
      f"Seerr: the plays of all the title's copies belong to a request, a movie in another library not ({dict(per_request)})")
# A minor from the review: "Různých titulů" counted two copies of the same movie as two titles.
# Each of the six items is played once; both copies of Film A are one title -> five.
db_exec("UPDATE playback SET library_id = (SELECT library_id FROM items WHERE items.id = playback.item_id)")
distinct = {"overview": stats.overview(30)["item_count"], "viewer": stats.user_detail("u1", 30)["totals"]["item_count"],
            "viewers table": next(r["item_count"] for r in stats.user_table(30) if r["id"] == "u1"),
            "library Filmy": stats.library_activity("filmy", 30)["totals"]["item_count"],
            "library Test filmy": stats.library_activity("test_filmy", 30)["totals"]["item_count"]}
check(distinct == {"overview": 5, "viewer": 5, "viewers table": 5, "library Filmy": 2, "library Test filmy": 1},
      f"“Různých titulů”: a movie's copy in one library is one title ({distinct})")
from jellyscope import langstats  # noqa: E402

by_language = {r["code"]: r["item_count"] for r in langstats.watched_languages(30, {})["rows"]}
check(by_language == {"und": 5}, f"“Titulů” by languages: a movie's copies once too ({by_language})")
# The review: a play without a viewer's id (from an import) is not another viewer.
db_exec("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at, last_seen_at, watched_seconds)"
        " VALUES ('import:x', NULL, 'Někdo', 'a1', 'x', ?, ?, 600)", (db.utcnow(), db.utcnow()))
with_import = stats.item_playback_summary("a2")
db_exec("DELETE FROM playback WHERE session_key = 'import:x'")
check((with_import["plays"], with_import["users"]) == (3, 1),
      f"a title's summary: a play without a viewer's id counts, an extra viewer not ({with_import})")

print()
print("--- E) a full sync: the second live copy is not merged, the copies are marked ---")
# The final review (14. 11. 2026): a full sync took for an "old" item (a file swap)
# every item of the same identity it had not yet seen in this run - so the second copy too, which
# comes only in the next batch after 200, or the same movie in another library. It deleted (or renamed)
# it and credited its plays to the first copy. A quick sync asks Jellyfin at such a moment
# whether the item still exists; the full one did not ask.
import asyncio  # noqa: E402
import time  # noqa: E402
from typing import Any  # noqa: E402

from _package import substitute  # noqa: E402


def from_jellyfin(iid: str, name: str, tmdb: str, added: str = "2026-01-01T00:00:00.0000000Z") -> dict[str, Any]:
    return {"Id": iid, "Name": name, "Type": "Movie", "DateCreated": added, "ProviderIds": {"Tmdb": tmdb},
            "RunTimeTicks": 60_000_000_000}


# The library "kino": Film K twice - the first and the last item, between them 205 others (two batches of writing) -
# and Film M, which is in the library "kino2" too.
JELLYFIN: dict[str, list[dict[str, Any]]] = {
    "kino": [from_jellyfin("k1", "Film K", "700"), from_jellyfin("m1", "Film M", "800"),
             *(from_jellyfin(f"v{i:03d}", f"Výplň {i}", str(9000 + i)) for i in range(205)),
             from_jellyfin("k2", "Film K", "700", added="2026-03-01T00:00:00.0000000Z")],
    "kino2": [from_jellyfin("m2", "Film M", "800")],
}
id_queries: list[list[str]] = []


class FakeJellyfin:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeJellyfin":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def users(self) -> list[dict[str, Any]]:
        return [{"Id": "u1", "Name": "Jana", "Policy": {"IsAdministrator": False}}]

    async def virtual_folders(self) -> list[dict[str, Any]]:
        return [{"ItemId": library, "Name": library, "CollectionType": "movies"} for library in JELLYFIN]

    async def item_count(self, item_types: str = "", parent_id: str | None = None) -> int:
        return len(JELLYFIN.get(parent_id or "", []))

    async def iter_items(self, parent_id: str):        # noqa: ANN201
        for item in JELLYFIN[parent_id]:
            yield item
        # The second library starts a second later - as with a real, larger library.
        await asyncio.sleep(1.1)

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        id_queries.append(sorted(ids))
        return [item for library in JELLYFIN.values() for item in library if item["Id"] in ids]

    async def recent_items(self, since: Any, cap: int = 2000, parent_id: str | None = None) -> list[dict[str, Any]]:
        """What was added to the library (a quick sync): the items the test marked as new."""
        return [item for item in JELLYFIN.get(parent_id or "", []) if item["Id"] in NEW]


NEW: set[str] = set()


def plays_by_item() -> dict[str, int]:
    return {r["item_id"]: r["n"] for r in db.query_all("SELECT item_id, COUNT(*) AS n FROM playback GROUP BY item_id")}


def movies_in_libraries() -> dict[str, str]:
    return {r["id"]: r["library_id"] for r in db.query_all(
        "SELECT id, library_id FROM items WHERE is_missing = 0 AND id IN ('k1', 'k2', 'k3', 'm1', 'm2')")}


for table in ("playback", "item_versions", "items", "libraries"):
    db_exec(f"DELETE FROM {table}")
substitute(scanner, "JellyfinClient", FakeJellyfin)
first = asyncio.run(scanner.sync_library())
check(first.get("status") == "ok" and main_copies() == {"k2": "k1"},
      f"a full sync marks the copies at the end ({first.get('status')}, {main_copies()})")
for number, item in enumerate(("k1", "k2", "m1", "m2")):
    play(f"e{number}", item)
time.sleep(1.1)                                   # the next run is later than the stamps from the first one
second = asyncio.run(scanner.sync_library())
check(second.get("status") == "ok" and plays_by_item() == {"k1": 1, "k2": 1, "m1": 1, "m2": 1},
      f"another full sync leaves the plays with their items ({plays_by_item()})")
check(movies_in_libraries() == {"k1": "kino", "k2": "kino", "m1": "kino", "m2": "kino2"} and main_copies() == {"k2": "k1"},
      f"both copies and the movie in two libraries stay where they were ({movies_in_libraries()}, {main_copies()})")
check(all(query for query in id_queries) and len(id_queries) <= 3,
      f"Jellyfin is asked only about the items merging would take ({id_queries})")
# A real file swap: k2 disappears from Jellyfin, in its place is k3 (a new id, the same movie).
JELLYFIN["kino"][-1] = from_jellyfin("k3", "Film K", "700", added="2026-03-01T00:00:00.0000000Z")
time.sleep(1.1)
third = asyncio.run(scanner.sync_library())
after_swap = plays_by_item()
# Who gets the vanished copy's history (the first copy, or the new one) is decided by the order Jellyfin
# sends them in - what matters is that it stays with the title, with an item that exists.
check(third.get("status") == "ok" and after_swap.get("k1", 0) + after_swap.get("k3", 0) == 2 and "k2" not in after_swap
      and (after_swap.get("m1"), after_swap.get("m2")) == (1, 1) and "k2" not in movies_in_libraries() and main_copies() == {"k3": "k1"},
      f"a file swap still merges: the vanished copy's history stays with the title ({after_swap}, {main_copies()})")
# A minor from the review: no test watched recounting the copies after a quick sync. A second copy of
# Film M (m9) is added to the library. Without the recount it stays unmarked - the first run with the recount switched
# off shows that - and the quick sync must mark it.
JELLYFIN["kino"].append(from_jellyfin("m9", "Film M", "800", added="2026-04-01T00:00:00.0000000Z"))
NEW.add("m9")
restore_recount = substitute(scanner, "recount_copies_after_sync", lambda: 0)
without_recount = asyncio.run(scanner.sync_recent())
after_run_without = main_copies()
restore_recount()
NEW.clear()
with_recount = asyncio.run(scanner.sync_recent())
check(without_recount.get("status") == "ok" and without_recount.get("items") == 1 and "m9" not in after_run_without
      and with_recount.get("status") == "ok" and main_copies() == {"k3": "k1", "m9": "m1"},
      f"a quick sync marks the copies at the end ({after_run_without} -> {main_copies()})")

print()
print("--- F) an item that stopped being a copy does not lead to somebody else's record even for a moment ---")
# The final review: between archiving or merging an item and the next recount `main_id` stayed
# old. The tile then led to an archived detail (or to an id that no longer exists) and the archived
# copy summed up somebody else's plays. On top, the recount did not run after a stopped sync.
for table in ("playback", "item_versions", "items"):
    db_exec(f"DELETE FROM {table}")
film("f1", "100")
film("f2", "100", added="2026-02-01 10:00:00")
film("f3", "100", added="2026-03-01 10:00:00")
scanner.recount_copies()
for number, item in enumerate(("f1", "f2", "f3")):
    play(f"f{number}", item)
scanner.archive(["f3"])                           # a secondary copy disappeared (a quick sync)
check(main_copies() == {"f2": "f1"} and stats.title_items("f3") == ["f3"] and stats.item_playback_summary("f3")["plays"] == 1
      and stats.title_items("f1") == ["f1", "f2"],
      f"an archived secondary copy has only its own plays and no longer belongs to the title ({main_copies()}, {stats.title_items('f3')})")
db_exec("UPDATE items SET synced_at = '2020-01-01 00:00:00' WHERE id = 'f1'")
scanner._mark_missing("2026-01-01 00:00:00")      # the main copy disappeared (a full sync)
check(main_copies() == {} and {r["id"] for r in stats.library_rows(50, 0, "filmy")} == {"f2"}
      and stats.title_items("f1") == ["f1"] and stats.item_playback_summary("f1")["plays"] == 1,
      f"after the main copy disappears the tile leads to the live item, not into the archive ({main_copies()}, "
      f"{[r['id'] for r in stats.library_rows(50, 0, 'filmy')]})")
# A file swap of the main copy: the old item g1 is renamed to the new g9 - the secondary g2 must not
# keep pointing at an id that no longer exists.
for table in ("playback", "items"):
    db_exec(f"DELETE FROM {table}")
film("g1", "300")
film("g2", "300", added="2026-02-01 10:00:00")
scanner.recount_copies()
play("g-1", "g1")
scanner._merge_by_tmdb([(("300", -1, -1), "g9")], protected={"g2"})
existing = {r["id"] for r in db.query_all("SELECT id FROM items")}
check(existing == {"g9", "g2"} and set(main_copies().values()) <= existing
      and {r["id"] for r in stats.library_rows(50, 0, "filmy")} <= existing,
      f"after the main copy is renamed nobody points at an id that does not exist ({main_copies()}, {sorted(existing)})")
check(scanner.recount_copies() == 1 and main_copies() == {"g2": "g9"}, f"and the recount then joins the copies again ({main_copies()})")

print()
print("--- G) lists by files do not merge copies ---")
# The final review: the Insights cards "Most transcoded files" and "…in weak quality" speak
# about files (which to convert, which to replace). With the titles' shared key the copies merged into one
# row that carried the details of one file and a link to the other.
from jellyscope import insights  # noqa: E402

db_exec("DELETE FROM playback")
db_exec("UPDATE items SET width = 1280, height = 720, video_codec = 'h264' WHERE id = 'g9'")
db_exec("UPDATE items SET width = 3840, height = 2160, video_codec = 'hevc' WHERE id = 'g2'")
for number, item in enumerate(("g9", "g2", "g2")):
    when = db.utcnow()
    db_exec("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, started_at, last_seen_at,"
            " watched_seconds, play_method) VALUES (?, 'u1', 'Jana', ?, 'Film 300', ?, ?, 3000, 'Transcode')",
            (f"t{number}", item, when, when))
transcoded = sorted((r["detail_url"], int(r["transcodes"]), r["video_codec"]) for r in insights.transcode_offenders(365))
check(transcoded == [("/item/g2", 2, "hevc"), ("/item/g9", 1, "h264")],
      f"transcoded files: every copy its own row, its own details and its own link ({transcoded})")
weak = [(r["detail_url"], int(r["plays"])) for r in insights.upgrade_candidates(365)]
check(weak == [("/item/g9", 1)], f"weak quality: only the file that is weak, with its plays ({weak})")
top_g = [(r["detail_url"], r["plays"]) for r in stats.top_items(365, limit=10, kind=stats.KIND_MOVIE)]
check(top_g == [("/item/g9", 3)], f"the ranking of titles is still merged ({top_g})")

# A minor from the review: two movies without a TMDB id (an empty text, not NULL) are not copies of each other.
for table in ("playback", "items"):
    db_exec(f"DELETE FROM {table}")
film("n1", "x")
film("n2", "x", added="2026-02-01 10:00:00")
db_exec("UPDATE items SET tmdb_id = '' WHERE id IN ('n1', 'n2')")
check(scanner.recount_copies() == 0 and main_copies() == {} and stats.library_rows_count("filmy") == 2,
      f"movies with an empty TMDB id are not merged ({main_copies()})")

print()
print("--- H) the app's start marks the copies at once (after an upgrade, not only after the first sync) ---")
# A minor from the review: no test watched the call at the start. Two copies without a mark, as
# the first start of a new version finds them; the app's start is to mark them before it lets the web go.
from jellyscope.web import app as application  # noqa: E402
from jellyscope.web import start as web_start  # noqa: E402

film("s1", "900")
film("s2", "900", added="2026-02-01 10:00:00")
before_start = main_copies()


async def start_up() -> dict[str, str]:
    async with web_start.lifespan(application):
        return main_copies()


after_start = asyncio.run(start_up())
check(before_start == {} and after_start == {"s2": "s1"}, f"the app's start marks the copies ({before_start} -> {after_start})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
