# -*- coding: utf-8 -*-
r"""A replaced file leaves no duplicate in the library - and does not merge a second copy.

When a file is replaced in Jellyfin (a better rip, transcoding), it is not a change: it is **a new item with a
new ItemId**. The quick sync adds it, but the old one stays in the library - merging by TMDB id cannot reach it
while the title has none. Until the full sync and the tidy (i.e. overnight) the same movie then stands in the
library twice.

Now it is straightened right after the addition: for every newcomer an older duplicate is looked for and
**Jellyfin is shown its ItemId directly**. That single request is the whole decision:

A duplicate is looked for **by TMDB id first** - that survives transcoding and Jellyfin filling in another name.
Only when the title has none, it goes by name and year (for an episode by the series and the numbers). For an
episode the tmdb id alone is not enough: Jellyfin reports the id of the whole series for an episode.

* Jellyfin **does not know** it -> the file was replaced. The old one goes to the archive and the history is
  rewritten to the new one; "how many times I saw it" belongs to the movie, not to the file.
* Jellyfin **knows** it -> it is a second copy (4K beside 1080p) and must not be merged. Half the history would
  disappear otherwise.

Verified for real against Jellyfin 10.11.11 too: replacing a file leaves one live title with its history, a
second copy in its own folder stays as two.

Run:
    .venv/bin/python tests/test_replaced_file.py
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "replaced.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


class FakeClient:
    """A Jellyfin that knows only what we tell it."""

    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.queries: list[list[str]] = []

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, str]]:
        self.queries.append(list(ids))
        return [{"Id": i} for i in ids if i in self.known]


def add_item(item_id: str, name: str, kind: str = "Movie", year: int = 2019,
             series: str = "", season: int | None = None,
             episode: int | None = None, tmdb: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, production_year, series_id,"
            " parent_index_number, index_number, tmdb_id, is_missing)"
            " VALUES (?,?,?,?,?,?,?,?,0)",
            (item_id, name, kind, year, series or None, season, episode,
             tmdb or None))
        conn.commit()


def add_plays(session: str, item_id: str, count: int = 1) -> None:
    with db.connect() as conn:
        for number in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, is_active) VALUES (?,?,?,?,?,?,?,?,0)",
                # Every record on another day: the same title in the same minute is a duplicate for the tidy and
                # gets merged - rightly, only this test would measure beside the point then.
                (f"{session}-{number}", "u-1", "Divák", item_id, "Film",
                 f"2026-09-0{number + 1} 10:00:00",
                 f"2026-09-0{number + 1} 10:20:00", 1200))
        conn.commit()


def state(item_id: str) -> tuple[int, int]:
    """(is in the archive, how many plays it has)"""
    archived = db.query_value("SELECT is_missing FROM items WHERE id = ?",
                              (item_id,), default=-1)
    plays = db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = ?",
                           (item_id,), default=0)
    return (int(archived), int(plays))


print("--- without a tmdb id a duplicate is recognised by name and year ---")
add_item("film-stary", "Zkouška", year=2019)
add_item("film-novy", "Zkouška", year=2019)
add_item("film-jiny", "Něco jiného", year=2019)
found = scanner.duplicates_of_new(["film-novy"])
check(found.get("film-novy") == ["film-stary"],
      f"an older one with the same name and year was found ({found})")
check("film-jiny" not in str(found), "another movie does not get mixed in")

add_item("dil-stary", "1. díl", kind="Episode", series="ser-1", season=1, episode=1)
add_item("dil-novy", "1. díl", kind="Episode", series="ser-1", season=1, episode=1)
add_item("dil-jiny", "2. díl", kind="Episode", series="ser-1", season=1, episode=2)
found = scanner.duplicates_of_new(["dil-novy"])
check(found.get("dil-novy") == ["dil-stary"],
      f"for an episode the series and the season and episode numbers decide ({found})")

print()
print("--- the TMDB id decides, not the name ---")
# Jellyfin often fills in another name for a transcoded file (another language, another edition). By name the
# duplicate would not be found; the tmdb id survives.
add_item("tmdb-stary", "Zkouška jinak", year=2001, tmdb="12345")
add_item("tmdb-novy", "Zkouška úplně jinak", year=2002, tmdb="12345")
found = scanner.duplicates_of_new(["tmdb-novy"])
check(found.get("tmdb-novy") == ["tmdb-stary"],
      f"the same tmdb id is enough, although the name and the year do not fit ({found})")

add_item("tmdb-jiny", "Něco", year=2001, tmdb="99999")
found = scanner.duplicates_of_new(["tmdb-jiny"])
check(not found, "another tmdb id does not pair")

# For an episode the tmdb id is the SERIES' - on its own it would merge a whole season.
add_item("ep-1", "1. díl", kind="Episode", series="s-2", season=1, episode=1,
         tmdb="777")
add_item("ep-2", "2. díl", kind="Episode", series="s-2", season=1, episode=2,
         tmdb="777")
add_item("ep-1-novy", "1. díl", kind="Episode", series="s-2", season=1, episode=1,
         tmdb="777")
found = scanner.duplicates_of_new(["ep-1-novy"])
check(found.get("ep-1-novy") == ["ep-1"],
      f"for an episode the season and episode numbers are added to tmdb ({found})")
check("ep-2" not in str(found), "the second episode is not merged")

add_item("ep-bez-cisla", "Speciál", kind="Episode", series="s-2", season=None,
         episode=None, tmdb="777")
found = scanner.duplicates_of_new(["ep-bez-cisla"])
check(not found,
      f"an episode without a number is not paired by tmdb at all ({found})")

print()
print("--- what Jellyfin does not know is a replaced file ---")
add_plays("stary", "film-stary", 3)
client = FakeClient(known={"film-novy"})      # Jellyfin no longer knows the old one
result = asyncio.run(scanner.straighten_after_new(client, ["film-novy"]))
check(client.queries and "film-stary" in client.queries[0],
      "Jellyfin was shown the old ItemId directly")
check(result["archived"] == 1, f"the old one went to the archive ({result})")
check(state("film-stary") == (1, 0), f"and is empty ({state('film-stary')})")
check(state("film-novy") == (0, 3),
      f"the history is on the new one ({state('film-novy')})")

print()
print("--- what Jellyfin knows is a second copy and is not touched ---")
add_item("kopie-stara", "Dvakrát", year=2020)
add_item("kopie-nova", "Dvakrát", year=2020)
add_plays("kopie", "kopie-stara", 2)
client = FakeClient(known={"kopie-stara", "kopie-nova"})
result = asyncio.run(scanner.straighten_after_new(client, ["kopie-nova"]))
check(result["archived"] == 0, f"nothing was archived ({result})")
check(state("kopie-stara") == (0, 2),
      f"the old copy kept its history ({state('kopie-stara')})")
check(state("kopie-nova") == (0, 0), "and the new one stayed empty")

print()
print("--- without newcomers nobody asks Jellyfin ---")
client = FakeClient(known=set())
result = asyncio.run(scanner.straighten_after_new(client, []))
check(not client.queries, "no request")
check(result["archived"] == 0, "and nothing changed")

add_item("samotny", "Sám doma", year=1990)
client = FakeClient(known={"samotny"})
result = asyncio.run(scanner.straighten_after_new(client, ["samotny"]))
check(not client.queries and result["duplicate_count"] == 0,
      "a title without a duplicate is not handled at all")

print()
print("--- the quick sync calls it by itself ---")
# Only the body of sync_recent (not a slice of the package's joined files).
body = inspect.getsource(scanner.sync_recent)
check("straighten_after_new(client, new_ids)" in body,
      "sync_recent straightens the library after new titles")
call_at = body.index("straighten_after_new")
check("async with JellyfinClient" in body[:call_at]
      and body[:call_at].count("async with JellyfinClient") == 1,
      "and does so while the connection to Jellyfin is open")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
