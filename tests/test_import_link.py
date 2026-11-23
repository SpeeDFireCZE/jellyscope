# -*- coding: utf-8 -*-
"""Looking up items for imported history.

Neither Playback Reporting nor Jellystat sends a tmdb ID - they send Jellyfin's ItemId, which changes with
every transcoding of a file, though. The import therefore often refers to items that are no longer in the
library under that id. This test guards that the history finds its titles again.
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
from _package import substitute  # noqa: E402 - a substitute in every submodule of the package importers

_tmp = tempfile.mkdtemp()
# Its own home: without it DATABASE_PATH would override the database chosen in data/database.json and the
# test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "link.db")

from jellyscope import db, importers  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


def add_item(item_id: str, name: str, tmdb: str | None = None,
             series: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, tmdb_id, series_name, is_missing, synced_at)"
            " VALUES (?,?,?,?,?,0,?)",
            (item_id, name, "Episode" if series else "Movie", tmdb, series, db.utcnow()),
        )


def add_history(key: str, item_id: str, name: str, series: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback
               (session_key, item_id, item_name, series_name, started_at,
                last_seen_at, watched_seconds, is_active)
               VALUES (?,?,?,?,?,?,600,0)""",
            (key, item_id, name, series, db.utcnow(), db.utcnow()),
        )


def where_is(key: str) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT item_id FROM playback WHERE session_key = ?", (key,)
        ).fetchone()
    return str(row["item_id"])


print("--- orphan records are found ---")
add_item("v-knihovne", "Matrix", tmdb="603")
add_history("import:1", "stare-id", "Matrix")          # an orphan
add_history("import:2", "v-knihovne", "Matrix")        # in order

orphans = importers._orphan_item_ids()
check(orphans == ["stare-id"], f"only the orphan record was found: {orphans}")


print()
print("--- pairing by tmdb ID ---")
# This would normally come from Jellyfin; the answer's shape is the same.
answer = [{"Id": "stare-id", "ProviderIds": {"Tmdb": "603"}}]
items, rows = importers._link_by_tmdb(answer)
check(items == 1, "one item paired")
check(rows == 1, "one history record was rewritten")
check(where_is("import:1") == "v-knihovne", "the history points at the title in the library")
check(where_is("import:2") == "v-knihovne", "the record that was right from the start did not change")
check(importers._orphan_item_ids() == [], "no orphan record is left")


print()
print("--- an unknown tmdb pairs nothing ---")
add_history("import:3", "cizi-id", "Neznámý film")
items, _ = importers._link_by_tmdb([{"Id": "cizi-id", "ProviderIds": {"Tmdb": "9999"}}])
check(items == 0, "a tmdb that is not in the library pairs nothing")
check(where_is("import:3") == "cizi-id", "the record stayed where it was")


print()
print("--- pairing by name when Jellyfin does not know the id ---")
items, rows = importers._link_by_name()
check(items == 0, "the name 'Neznámý film' has no counterpart in the library")

add_history("import:4", "davno-pryc", "Matrix")
items, rows = importers._link_by_name()
check(items == 1, "a match of names paired one item")
check(where_is("import:4") == "v-knihovne", "the history found the title by name")


print()
print("--- an ambiguous match is rather not paired ---")
add_item("duna-1080", "Duna")
add_item("duna-2160", "Duna")
add_history("import:5", "duna-stare", "Duna")
items, _ = importers._link_by_name()
check(items == 0, "two equally named titles = we do not pair")
check(where_is("import:5") == "duna-stare",
      "rather unpaired than wrongly assigned")


print()
print("--- episodes pair including the series ---")
add_item("ep-nova", "1. díl", series="Kancelář")
add_history("import:6", "ep-stara", "1. díl", series="Kancelář")
items, _ = importers._link_by_name()
check(items == 1, "the episode paired")
check(where_is("import:6") == "ep-nova", "the episode's history points at the right episode")


print()
print("--- the whole pass does not fail even when Jellyfin does not answer ---")
# Jellyfin is not configured here, so the first step must fail and the function is to go on quietly to the
# match by name.
result = asyncio.run(importers.link_imported_history())
check(result["status"] != "error" if "status" in result else True,
      "the function returned a result instead of an exception")
check("by_tmdb" in result and "by_name" in result,
      f"the result holds both ways: {result}")

# Without orphan records nothing at all is to be done.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_id NOT IN (SELECT id FROM items)")
result = asyncio.run(importers.link_imported_history())
check(result["orphans"] == 0, "without orphan records nothing is looked up")


print()
print("--- orphans are looked up right in Jellyfin ---")
# The strongest trace imported history carries is the ItemId itself - it is the real one, only nothing here
# leads to it. Jellystat stores only the episode's name ("7. epizoda", "Pilot") and says nothing about the
# series, so pairing by name refuses to file such a record - and rightly so, every series has that name.
# Jellyfin knows the id, though, and tells the series and the episode's number.
now = db.utcnow()
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    conn.execute("DELETE FROM items")
    # An episode in the library - under ANOTHER id than the history carries.
    conn.execute(
        """INSERT INTO items (id, name, type, series_id, series_name, tmdb_id,
                              parent_index_number, index_number, is_missing, synced_at)
           VALUES ('nove-id','7. epizoda','Episode','ser-1','Kancelář','2316',
                   2,7,0,?)""", (now,))
    for i in range(3):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?,'u1','stare-id','7. epizoda','Episode',?,?,1800,0)""",
            (f"import:jst:u:{i}", now, now))
    # And an episode of a series that is no longer in the library. Jellyfin knows it, so it at least fills in
    # the series - the overviews then do not file it as a movie.
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 item_type, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('import:jst:u:9','u1','smazane-id','Pilot','Episode',?,?,1800,0)""",
        (now, now))


class JellyfinWithMemory:
    """A Jellyfin that knows both ids - just like the real one."""

    queries: list[list[str]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "JellyfinWithMemory":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        JellyfinWithMemory.queries.append(sorted(ids))
        return [
            {"Id": "stare-id", "Name": "7. epizoda", "Type": "Episode",
             "SeriesId": "ser-1", "SeriesName": "Kancelář",
             "ParentIndexNumber": 2, "IndexNumber": 7,
             "SeriesProviderIds": {"Tmdb": "2316"}},
            {"Id": "smazane-id", "Name": "Pilot", "Type": "Episode",
             "SeriesId": "ser-x", "SeriesName": "Odpadlík",
             "ParentIndexNumber": 1, "IndexNumber": 1,
             "SeriesProviderIds": {"Tmdb": "999"}},
        ]


substitute(importers, "JellyfinClient", JellyfinWithMemory)

check(importers.orphan_playback_count() == 4, "the starting state: four orphan rows")
check(importers.orphan_items_count() == 2, "but only two titles")

result = asyncio.run(importers.look_up_orphans_in_jellyfin())
check(result["found"] == 2, f"Jellyfin knows both ids ({result})")
check(result["linked"] == 1,
      f"the episode that is in the library under another id gets linked ({result['linked']})")
check(result["created_count"] == 1,
      f"and the one missing in the library is created ({result['created_count']})")

# The essential part: the id in imported history IS a Jellyfin id, so as soon as the item exists the records
# point at it by themselves - and it comes with tmdb_id, the series and the episode's numbers, so merging and
# filing work.
created = db.query_one("SELECT * FROM items WHERE id = 'smazane-id'")
check(created is not None, "the item really was created")
check(created["series_name"] == "Odpadlík" and created["tmdb_id"] == "999",
      f"with the series and tmdb too ({created['series_name']}, {created['tmdb_id']})")
check(created["parent_index_number"] == 1 and created["index_number"] == 1,
      "and with the season and episode numbers")
check(importers.orphan_playback_count() == 0,
      f"no orphan is left ({importers.orphan_playback_count()})")
check(JellyfinWithMemory.queries == [["smazane-id", "stare-id"]],
      f"we ask about both ids in one request ({JellyfinWithMemory.queries})")

linked = db.query_all("SELECT * FROM playback WHERE item_id = 'nove-id'")
check(len(linked) == 3, f"all three rows moved to the library's item ({len(linked)})")
check(linked[0]["series_name"] == "Kancelář", "and know about the series")

# The second time there is nothing to look up - and the item we have just created must not be created again.
second = asyncio.run(importers.look_up_orphans_in_jellyfin())
check(second["linked"] == 0 and second["created_count"] == 0,
      f"a repeated run changes nothing any more ({second})")
check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'smazane-id'") == 1,
      "and there is still only one item")


print()
print("--- what could not be filed: a list with the reason ---")
# The list is stored nowhere and is computed anew on every opening. A stored one would show the state after
# the last clean-up - i.e. something that need not hold any more.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    conn.execute("DELETE FROM items")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lb','Vše',?)",
                 (now,))
    # Two series, each with a "7. epizoda" - that is why the name is ambiguous.
    for series, name in (("ser-1", "Kancelář"), ("ser-2", "Sherlock")):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing, synced_at)
               VALUES (?,'7. epizoda','Episode','lb',?,?,2,7,0,?)""",
            (f"{series}-e7", series, name, now))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, is_missing, synced_at)
           VALUES ('film-duna','Duna','Movie','lb',0,?)""", (now,))

    for number, (title, series, count) in enumerate([
            ("7. epizoda", None, 25),                      # several matches
            ("Duna", None, 3),                             # one match
            ("Sherlock - s02e09 - Velká hra", None, 4),    # the series is there, the episode not
            ("Neznámý - s01e01 - Něco", None, 29),         # the series is not there
            ("Longlegs", None, 2)]):                       # nothing
        for n in range(count):
            conn.execute(
                """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                         series_name, item_type, started_at,
                                         last_seen_at, watched_seconds, is_active)
                   VALUES (?,'u1',?,?,?,'Episode',?,?,1800,0)""",
                (f"import:jst:{number}:{n}", f"osirele-{number}", title, series, now, now))

by_reason = {r["reason"]: r for r in importers.analyse_orphans()}
check(by_reason[importers.REASON_SEVERAL_MATCHES]["item_name"] == "7. epizoda",
      "an episode called 7. epizoda falls among the ambiguous")
check(by_reason[importers.REASON_SEVERAL_MATCHES]["match_count"] == 2,
      f"and it is known how many matches it has ({by_reason[importers.REASON_SEVERAL_MATCHES]['match_count']})")
check(by_reason[importers.REASON_ONE_MATCH]["item_name"] == "Duna",
      "a title with a single match is recognised")
check(by_reason[importers.REASON_SERIES_EXISTS]["series_from_name"] == "Sherlock",
      "the series is read out of a composed name")
check(importers.REASON_NO_SERIES in by_reason, "a series outside the library has its reason")
check(importers.REASON_NOTHING in by_reason, "and so has what has no trace at all")
check(all(d in importers.REASON_LABELS for d in by_reason),
      "every reason has its label for the page")

# One row per title, not per record - otherwise a list where one is to recognise something by name would hold
# the same sentence a hundred times.
check(by_reason[importers.REASON_SEVERAL_MATCHES]["row_count"] == 25,
      "the title shows how many records it concerns")
check(len(importers.analyse_orphans()) == 5, "five titles, not 63 records")


print()
print("--- manual assignment ---")
candidates = importers.candidates_for_orphan("Sherlock")
check(len(candidates) == 1 and candidates[0]["id"] == "ser-2-e7",
      f"the search in the library finds the episode by its series ({candidates})")
check(importers.candidates_for_orphan("S") == [],
      "a single letter is not searched for - it would return the whole library")

result = importers.assign_manually("osirele-0", "ser-2-e7")
check(result["status"] == "ok" and result["rows"] == 25,
      f"all 25 records are assigned ({result})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'ser-2-e7'") == 25,
      "and hang on the chosen episode")
check(db.query_value("SELECT series_name FROM playback WHERE item_id = 'ser-2-e7'")
      == "Sherlock", "including the series")
check(len(importers.analyse_orphans()) == 4, "one title less in the list")

check(importers.assign_manually("osirele-1", "neexistuje")["status"] == "error",
      "a target outside the library is refused")
check(importers.assign_manually("uz-neni", "film-duna")["status"] == "error",
      "and assigning something that is nowhere does not work either")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
