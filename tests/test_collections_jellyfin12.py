# -*- coding: utf-8 -*-
r"""Jellyfin 12 hides movies behind collections - Jellyscope says it does not want that.

Reproduced on 10. 9. 2026 on Jellyfin 12.0.0: the query
`/Items?ParentId=<library>&Recursive=true&IncludeItemTypes=Movie,Episode`
returned one item of the type `BoxSet` instead of three movies from a
collection - even through the type filter. The movie library then held the
collection and its movies were missing, viewing included. The parameter
`CollapseBoxSetItems=false` turns it off (verified live: 5 movies, no
collection); on 10.x it has no effect.

What is guarded here (without Jellyfin, through a fake server):

* every query for items sends `CollapseBoxSetItems=false`,
* when the server still sends a wrapper (a collection, a series, a folder),
  neither the full nor the quick sync writes it - but an unsorted file
  (`Video`) it does,
* a collection that got into the database earlier vanishes by the cleanup.

Run:
    .venv/bin/python tests/test_collections_jellyfin12.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kolekce.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from jellyscope import db, scanner  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()

MOVIE = {"Id": "f1", "Name": "Matrix", "Type": "Movie", "ProductionYear": 1999,
         "DateCreated": "2026-09-01T10:00:00Z"}
COLLECTION = {"Id": "k1", "Name": "Harry Potter kolekce", "Type": "BoxSet",
              "IsFolder": True, "DateCreated": "2026-09-02T10:00:00Z"}
# A freshly added file Jellyfin has not sorted yet: it sends it as `Video`.
# That one IS to be stored - on the next run it is corrected to a movie or an episode.
UNSORTED = {"Id": "v1", "Name": "nazev.souboru.mkv", "Type": "Video",
            "IsFolder": False, "DateCreated": "2026-09-03T10:00:00Z"}
QUERIES: list[dict[str, list[str]]] = []


def server(request: httpx.Request) -> httpx.Response:
    """A fake Jellyfin 12: for items it returns a movie and a collection."""
    QUERIES.append(parse_qs(request.url.query.decode()))
    if request.url.path.endswith("/Items"):
        return httpx.Response(200, json={"Items": [MOVIE, COLLECTION, UNSORTED],
                                         "TotalRecordCount": 3})
    return httpx.Response(200, json={})


def client() -> JellyfinClient:
    k = JellyfinClient("http://jellyfin.test", "test-key", version="12.0.0")
    k._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                  base_url="http://jellyfin.test")
    return k


print("--- the queries say they do not want collections ---")


async def queries():
    async with client() as jf:
        await jf.items_page(0, 100, "Movie,Episode", None, "lib1")
        await jf.items_by_ids(["f1"])
        [i async for i in jf.iter_items(parent_id="lib1")]
        await jf.recent_items(None, parent_id="lib1")
        await jf.item_count(parent_id="lib1")


asyncio.run(queries())
without = [d for d in QUERIES if d.get("CollapseBoxSetItems") != ["false"]]
check(len(QUERIES) >= 5 and not without,
      f"all {len(QUERIES)} queries for items carry CollapseBoxSetItems=false")

print()
print("--- what the server still sends is not written ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, synced_at)"
                 " VALUES ('lib1', 'Filmy', 'movies', '[]', ?)", (db.utcnow(),))
    conn.commit()


async def full():
    async with client() as jf:
        return await scanner._sync_items_of_library(
            jf, {"id": "lib1", "name": "Filmy"}, use_jellyfin_tech=True)


count = asyncio.run(full())
kinds = sorted(r["type"] for r in db.query_all("SELECT type FROM items"))
check(kinds == ["Movie", "Video"],
      f"the full sync wrote the movie and the unsorted file, not the collection ({kinds})")
check(count == 2, f"and counts only what it wrote ({count})")

db.query_all("DELETE FROM items")
new = asyncio.run(scanner._save_new_items([MOVIE, COLLECTION, UNSORTED], "lib1", True))
kinds = sorted(r["type"] for r in db.query_all("SELECT type FROM items"))
check(kinds == ["Movie", "Video"] and sorted(new) == ["f1", "v1"],
      f"the quick sync the same ({kinds}, new {sorted(new)})")
check(scanner.is_wrapper({"Type": "Movie", "IsFolder": True}), "a wrapper is told by IsFolder too")

print()
print("--- a collection from earlier vanishes by the cleanup ---")
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, synced_at)"
                 " VALUES ('k-stara', 'Stará kolekce', 'BoxSet', 'lib1', ?)",
                 (db.utcnow(),))
    conn.commit()
cleaned = scanner.clean_up_phantoms()
check(cleaned == 1 and db.query_value(
    "SELECT COUNT(*) FROM items WHERE type = 'BoxSet'") == 0,
    f"the cleanup removed the collection ({cleaned})")
check(db.query_value("SELECT COUNT(*) FROM items WHERE type = 'Video'") == 1,
      "and left the unsorted file alone")
# A foreign kind nobody thought of in the list of wrappers (an old import) is
# to vanish too - otherwise `_mark_missing` would archive it again every night.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, synced_at)"
                 " VALUES ('mv1', 'Klip', 'MusicVideo', 'lib1', ?)", (db.utcnow(),))
    conn.commit()
check(scanner.clean_up_phantoms() == 1 and db.query_value(
    "SELECT COUNT(*) FROM items WHERE type = 'MusicVideo'") == 0,
    "the cleanup gets rid of a kind nobody thought of too")
check(db.query_value("SELECT COUNT(*) FROM items WHERE type = 'Video'") == 1,
      "and still leaves `Video` alone")
source = "".join(p.read_text(encoding="utf-8")
                 for p in sorted((PROJECT / "jellyscope" / "scanner").glob("*.py")))
body = source[source.index("async def sync_library"):source.index("async def _sync_users")]
check("clean_up_phantoms" in body, "and the cleanup runs at the end of the full sync too, not only at start")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
