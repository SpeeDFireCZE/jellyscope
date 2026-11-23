# -*- coding: utf-8 -*-
"""Merging items by tmdb_id, and the archive.

The situation it solves: you transcode a movie and replace the original file. Jellyfin does not see it as a
change - it creates a new item with a new ItemId. Without merging the play history would fall apart into two
titles.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# The database must be temporary, so that the test does not touch real data.
_tmp = tempfile.mkdtemp()
# Its own home: without it DATABASE_PATH would override the database chosen in data/database.json and the test
# would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "merge.db")

from jellyscope import db, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


def add_item(item_id: str, tmdb: str | None, name: str = "Film") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, tmdb_id, is_missing, synced_at)"
            " VALUES (?,?,?,?,0,?)",
            (item_id, name, "Movie", tmdb, db.utcnow()),
        )


def add_play(item_id: str, seconds: int) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback
               (session_key, item_id, item_name, started_at, last_seen_at,
                watched_seconds, is_active)
               VALUES (?,?,?,?,?,?,0)""",
            (f"s-{item_id}-{seconds}", item_id, "Film",
             db.utcnow(), db.utcnow(), seconds),
        )


def watched(item_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(watched_seconds), 0) AS s"
            "  FROM playback WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    return int(row["s"])


def item(item_id: str):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


print("--- tmdb_id from Jellyfin's answer ---")
check(scanner.tmdb_id_of({"ProviderIds": {"Tmdb": "603"}}) == "603",
      "reads the key Tmdb")
check(scanner.tmdb_id_of({"ProviderIds": {"TmdbId": "603"}}) == "603",
      "reads the key TmdbId too")
check(scanner.tmdb_id_of({"ProviderIds": {"tmdb": "603"}}) == "603",
      "the case of letters does not matter")
check(scanner.tmdb_id_of({"ProviderIds": {"Imdb": "tt0133093"}}) is None,
      "another provider is not mistaken for TMDB")
check(scanner.tmdb_id_of({"SeriesProviderIds": {"Tmdb": "1396"}}) == "1396",
      "for an episode it takes the series' id")
# And exactly that is why tmdb_id alone must not be used as an item's identity: all episodes of a series have
# the same one. In detail in test_series_and_history.py.
check(scanner.item_identity({"Type": "Episode", "ParentIndexNumber": 1,
                             "IndexNumber": 3,
                             "SeriesProviderIds": {"Tmdb": "1396"}})
      == ("1396", 1, 3),
      "an episode's identity is tmdb + season + episode")
check(scanner.tmdb_id_of({}) is None, "without ProviderIds it returns None")
check(scanner.tmdb_id_of({"ProviderIds": {"Tmdb": ""}}) is None,
      "an empty value does not count")
check(scanner.tmdb_id_of({"ProviderIds": "nesmysl"}) is None,
      "a broken answer does not fail")


print()
print("--- a transcoded file: a new ItemId, the same tmdb ---")
add_item("stare-id", "603", "Matrix")
add_play("stare-id", 5400)
check(watched("stare-id") == 5400, "the starting state: 5400 s on the old id")

merged = scanner._merge_by_tmdb([(("603", -1, -1), "nove-id")])
check(merged == 1, "one merge ran")
check(item("stare-id") is None, "the old item disappeared")
check(item("nove-id") is not None, "the item is under the new id")
check(watched("nove-id") == 5400, "the history moved over without loss")
check(watched("stare-id") == 0, "nothing is left on the old id")
check(item("nove-id")["name"] == "Matrix", "the name and the other data stayed")


print()
print("--- the new id is in the database already (the sync came before the merging) ---")
add_item("stara-2", "550", "Klub rváčů")
add_play("stara-2", 1200)
add_item("nova-2", "550", "Klub rváčů")
add_play("nova-2", 300)

merged = scanner._merge_by_tmdb([(("550", -1, -1), "nova-2")])
check(merged == 1, "a merge ran")
check(item("stara-2") is None, "the duplicate item is gone")
check(watched("nova-2") == 1500, "the history of both added up (1200 + 300)")


print()
print("--- what must not be merged ---")
add_item("jiny-film", "999", "Jiný film")
add_play("jiny-film", 600)
merged = scanner._merge_by_tmdb([(("111", -1, -1), "neexistuje")])
check(merged == 0, "an unknown tmdb merges nothing")
check(watched("jiny-film") == 600, "a foreign item is not touched")

merged = scanner._merge_by_tmdb([(("999", -1, -1), "jiny-film")])
check(merged == 0, "the same id on both sides is not merged with itself")
check(item("jiny-film") is not None, "the item did not disappear")

check(scanner._merge_by_tmdb([]) == 0, "an empty list passes without an error")


print()
print("--- several candidates: the one with the most history wins ---")
add_item("maly", "777", "Duna")
add_play("maly", 100)
add_item("velky", "777", "Duna")
add_play("velky", 9000)

scanner._merge_by_tmdb([(("777", -1, -1), "duna-nova")])
check(watched("duna-nova") == 9000,
      "the history of the item with the most watched moved over")
check(item("velky") is None, "the chosen item was renamed")
check(item("maly") is not None, "the other stayed untouched")


print()
print("--- the archive instead of deleting ---")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = ?", ("jiny-film",))
check(item("jiny-film")["is_missing"] == 1, "an item can be marked as archived")
check(watched("jiny-film") == 600, "archiving does not delete the history")

# Merging must wake an archived item up again - the file came back.
scanner._merge_by_tmdb([(("999", -1, -1), "jiny-film-nove")])
check(item("jiny-film-nove")["is_missing"] == 0,
      "merging returns the item from the archive among the live ones")


print()
print("--- a second copy of the same episode must not be merged ---")
# The difference between "a replaced file" and "two copies": with a replacement the old item disappeared from
# Jellyfin, with two copies both are still there. The merging cannot tell by itself - both have the same tmdb +
# season + episode -, so it would delete one. The next scan would bring it back and delete the other; the items
# would take turns and a link to the one just eaten would give 404.


def add_episode(item_id: str, tmdb: str, season: int, episode: int,
                synced_at: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (id, name, type, tmdb_id, parent_index_number,
                                  index_number, is_missing, synced_at)
               VALUES (?,?,'Episode',?,?,?,0,?)""",
            (item_id, f"S{season:02d}E{episode:02d}", tmdb, season, episode,
             synced_at or db.utcnow()),
        )


add_episode("kopie-A", "1396", 3, 3)
add_play("kopie-A", 900)

merged = scanner._merge_by_tmdb([(("1396", 3, 3), "kopie-B")],
                                protected={"kopie-A"})
check(merged == 0, "an item we know is in Jellyfin is not merged")
check(item("kopie-A") is not None, "and did not disappear")
check(watched("kopie-A") == 900, "the history stayed with it")

# The second way it is told: the stamp `synced_at`. What Jellyfin sent in this run exists - and an existing item
# is not merged. The full sync needs no other protection.
run_start = db.utcnow()
add_episode("kopie-C", "1396", 4, 1, synced_at=run_start)
merged = scanner._merge_by_tmdb([(("1396", 4, 1), "kopie-D")], seen_since=run_start)
check(merged == 0, "an item seen in this run is not merged")
check(item("kopie-C") is not None, "it stayed")

# And the counterpart: a really replaced file. The old item has a stamp from last time, because Jellyfin did not
# send it any more - there we must merge, otherwise the history falls apart into two titles.
add_episode("vymeneny-stary", "1396", 5, 2, synced_at="2020-01-01 00:00:00")
add_play("vymeneny-stary", 1800)
merged = scanner._merge_by_tmdb([(("1396", 5, 2), "vymeneny-novy")],
                                seen_since=run_start)
check(merged == 1, "a replaced file is still merged")
check(watched("vymeneny-novy") == 1800, "and the history moved over")

# The batch being written is live by definition - the protection is taken from it by itself, without the caller
# having to list it.
add_episode("davka-A", "1396", 6, 6)
add_play("davka-A", 300)
row = scanner._item_row(
    {"Id": "davka-A", "Name": "S06E06", "Type": "Episode",
     "ParentIndexNumber": 6, "IndexNumber": 6,
     "SeriesProviderIds": {"Tmdb": "1396"}},
    "lib", {}, db.utcnow())
scanner._write_batch([row], [], [(("1396", 6, 6), "davka-B")], True)
check(item("davka-A") is not None,
      "the merging does not take an item of the batch being written")
check(watched("davka-A") == 300, "and the history stayed with it")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
