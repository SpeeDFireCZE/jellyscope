# -*- coding: utf-8 -*-
"""An episode's identity, unifying series in the statistics and tidying the history.

One bug stood behind a big part of what looked wrong in the application, and
it is worth describing properly:

    `tmdb_id_of()` returns the id of the **series** for an episode - an episode
    itself usually has none in TMDB. Merging transcoded files however took this
    id as the item's identity, so it considered any two episodes of the same
    series the same file. At every scan it therefore merged the history of all
    episodes into a single one.

It showed in places that at first sight have nothing to do with each other:
in the statistics it looked like a viewer saw one episode twenty times;
"watched titles" were many times fewer; and links pointed at items that the
merging had deleted meanwhile, so they ended in a 404.

Further tested here: unifying series in the overviews (most watched titles,
transcoded files), counting the libraries' usage and tidying the history after
two collectors running at once.

Run:
    .venv/bin/python tests/test_series_and_history.py
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

_tmp = tempfile.mkdtemp()
# An own home: without it the stored choice of the database in
# data/database.json would override DATABASE_PATH and the test would run
# against the live database.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "serialy.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import (collector, db, importers, insights, langstats,
                        scanner, stats)  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


def episode(number: int, ident: str | None = None) -> dict[str, Any]:
    """An episode the way Jellyfin sends it - that is with the series' TMDB id."""
    return {
        "Id": ident or f"ep-{number}", "Name": f"{number}. díl", "Type": "Episode",
        "SeriesId": "ser-1", "SeriesName": "Kancelář",
        "ParentIndexNumber": 1, "IndexNumber": number,
        "RunTimeTicks": 12_000_000_000,
        # An episode has no tmdb of its own - Jellyfin sends the id of the SERIES.
        "SeriesProviderIds": {"Tmdb": "2316"},
    }


def movie(ident: str, tmdb: str) -> dict[str, Any]:
    return {"Id": ident, "Name": "Duna", "Type": "Movie",
            "RunTimeTicks": 72_000_000_000, "ProviderIds": {"Tmdb": tmdb}}


print("--- an episode is identified not by tmdb but by tmdb + season + episode ---")
episodes = [episode(i) for i in range(1, 6)]
check(len({scanner.tmdb_id_of(d) for d in episodes}) == 1,
      "all episodes have the same tmdb (it is the series' id)")
identity = [scanner.item_identity(d) for d in episodes]
check(len(set(identity)) == 5, f"but each has its own identity ({identity[:2]}…)")
check(scanner.item_identity(movie("f1", "999")) == ("999", -1, -1),
      "for a movie the season and the episode are -1")

# An episode without a number cannot be determined unambiguously - such is better not merged.
without_number = episode(9)
without_number["IndexNumber"] = None
check(scanner.item_identity(without_number) is None,
      "an episode without a number is not merged at all")


print()
print("--- the episodes' history does not merge at a scan ---")
now = db.utcnow()
rows = [scanner._item_row(d, "lib", {}, now) for d in episodes]
pairs = [(scanner.item_identity(d), d["Id"]) for d in episodes]
scanner._write_batch(list(rows), [], list(pairs), True)

with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lib','Seriály',?)",
                 (now,))
    for i in range(1, 6):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     series_name, library_id, item_type,
                                     started_at, last_seen_at, ended_at,
                                     watched_seconds, play_method, is_active)
               VALUES (?, 'u1', ?, ?, 'Kancelář', 'lib', 'Episode', ?, ?,
                       datetime(?, '+30 minutes'), 1800, 'Transcode', 0)""",
            (f"s-{i}", f"ep-{i}", f"{i}. díl", now, now, now))


def distribution() -> dict[str, int]:
    return {r["item_id"]: r["n"] for r in db.query_all(
        "SELECT item_id, COUNT(*) AS n FROM playback GROUP BY item_id")}


before = distribution()
scanner._write_batch(list(rows), [], list(pairs), True)
scanner._write_batch(list(rows), [], list(pairs), True)
check(distribution() == before,
      f"two more scans did not move the history ({distribution()})")
check(len(distribution()) == 5, "each episode still has its own plays")


print()
print("--- but a transcoded file MUST be merged ---")
# This is why the merging came about: a new ItemId, the same episode.
new_file = episode(3, ident="ep-3-hevc")
scanner._write_batch([scanner._item_row(new_file, "lib", {}, now)], [],
                     [(scanner.item_identity(new_file), "ep-3-hevc")], True)
after = distribution()
check("ep-3-hevc" in after and "ep-3" not in after,
      f"the history of the 3rd episode moved to the new file ({sorted(after)})")
check(len(after) == 5, "and the other episodes stayed where they were")


print()
print("--- in the overviews a series shows as one title ---")
titles = stats.top_items(365, limit=10)
series_rows = [r for r in titles if r["is_series"]]
check(len(series_rows) == 1, f"the series is one row, not five ({len(series_rows)})")
check(series_rows[0]["label"] == "Kancelář", f"and it is named after the series ({series_rows[0]['label']})")
check(series_rows[0]["detail_url"] == "/series/ser-1",
      f"the link leads to the series ({series_rows[0]['detail_url']})")

transcoded = insights.transcode_offenders(365)
check(len(transcoded) == 1,
      f"among the transcoded ones the series is one row too ({len(transcoded)})")
check(transcoded[0]["detail_url"] == "/series/ser-1",
      f"and it can be clicked through as well ({transcoded[0]['detail_url']})")

# The movies/series filter - the same three options as with the watching by days.
check(all(r["is_series"] for r in stats.top_items(365, kind="series")),
      "the 'series' filter lets only series through")
check(not stats.top_items(365, kind="movies"),
      "the 'movies' filter has nothing yet, there are only episodes here")


print()
print("--- a series is told from the name too, when nothing else is there ---")
# Playback Reporting stores the name as "Series - s01e01 - Episode name" and
# says nothing else about the series. Besides, nothing may lead to the item in
# the library any more. Without parsing the name such a series fell apart into
# single episodes - the overview had five rows "Blue - s01e17 - Calypso"
# instead of one "Blue".
check(stats.series_from_name("Blue - s03e03 - Opičí dráha") == "Blue",
      "'Blue' is read from 'Blue - s03e03 - Opičí dráha'")
check(stats.series_from_name("Seal Team 6 - s02e07 - 7. epizoda") == "Seal Team 6",
      "a number in the series' name does not matter")
check(stats.series_from_name("Hanebný pancharti") is None,
      "for a movie nothing is made up")
check(stats.series_from_name("Něco - s01 - jiného") is None,
      "the episode number must be there too, not only the season")

with db.connect() as conn:
    for key, name in (
        ("imp-1", "Blue - s03e03 - Opičí dráha"),
        ("imp-2", "Blue - s01e17 - Calypso"),
        ("imp-3", "Blue - s01e37 - Dobrodružství"),
    ):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', ?, ?, ?, ?, 3600, 0)""",
            (key, f"pryc-{key}", name, now, now))

# The rule for the group itself: three different episodes must give one key.
# We check it directly, not through the chart - as long as "Blue" is not in
# the library, the chart (rightly) hides it as a title without a counterpart.
keys = {stats.title_key({"item_name": name})[0] for name in (
    "Blue - s03e03 - Opičí dráha", "Blue - s01e17 - Calypso",
    "Blue - s01e37 - Dobrodružství")}
check(keys == {"nazev:blue"}, f"three episodes give one key ({keys})")
check(stats.title_key({"item_name": "Blue - s01e17 - Calypso"})[1:] == ("Blue", True),
      "with the label 'Blue' and marked as a series")

# And when we have the same series in the library, it must merge together -
# not fall apart into "from the import" and "from our own collecting".
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, is_missing, synced_at)
           VALUES ('blue-ep1', '1. díl', 'Episode', 'lib', 'ser-blue', 'Blue',
                   1, 1, 0, ?)""", (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 series_name, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('blue-nove', 'u1', 'blue-ep1', '1. díl', 'Blue', ?, ?, 7200, 0)""",
        (now, now))

blue = [r for r in stats.top_items(365, limit=30) if r["label"] == "Blue"]
check(len(blue) == 1,
      f"the import and our own collecting are still one row ({len(blue)})")
check(blue[0]["plays"] == 4, f"and they add up ({blue[0]['plays']} plays)")
check(blue[0]["detail_url"] == "/series/ser-blue",
      f"the link leads to the series from the library ({blue[0]['detail_url']})")


print()
print("--- an episode is told by the name from the library too ---")
# A harder case than "Blue - s01e17": the record carries ONLY the episode's
# name ("Zvony") and nothing in the library leads to the item any more. The
# series cannot be told from the name, but the library knows the episode - and
# knows which series it belongs to. Without it a separate title came out of it
# in the overview, that is a movie that does not exist.
with db.connect() as conn:
    for ident, ep_name, series, series_name, number in (
        ("got-5", "Zvony", "ser-got", "Hra o trůny", 5),
        ("got-6", "Zimní vichry", "ser-got", "Hra o trůny", 6),
        ("hod-3", "Zelená rada", "ser-hod", "Rod Draka", 3),
        # A name two different series have - for such one nothing is guessed.
        ("got-1p", "Pilot", "ser-got", "Hra o trůny", 1),
        ("hod-1p", "Pilot", "ser-hod", "Rod Draka", 1),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing, synced_at)
               VALUES (?, ?, 'Episode', 'lib', ?, ?, 1, ?, 0, ?)""",
            (ident, ep_name, series, series_name, number, now))

    for key, ep_name in (("sir-1", "Zvony"), ("sir-2", "Zelená rada"),
                         ("sir-3", "Hra o trůny"), ("sir-4", "Pilot"),
                         ("sir-5", "Úplně neznámý titul")):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u9', ?, ?, ?, ?, 3600, 0)""",
            (key, f"osirely-{key}", ep_name, now, now))

by_label = {r["label"]: r for r in stats.top_items(365, limit=40)}
check("Zvony" not in by_label,
      f"the episode 'Zvony' is no longer a separate title ({list(by_label)[:6]})")
check("Zelená rada" not in by_label, "nor 'Zelená rada'")
check("Hra o trůny" in by_label and by_label["Hra o trůny"]["is_series"],
      "instead the series 'Hra o trůny' is there")
check(by_label.get("Rod Draka", {}).get("detail_url") == "/series/ser-hod",
      f"and it can be clicked through, even though no item leads to the episode "
      f"({by_label.get('Rod Draka', {}).get('detail_url')})")

# An ambiguous name must not be assigned - wrongly filed history is worse than
# the one we know is aside. Such a record does not show in the chart (it has
# no counterpart), but the main thing is that it **was not credited to another
# series** - that would be a quiet lie in the numbers.
check("Pilot" not in by_label,
      "two series have 'Pilot', so nothing is guessed and it does not belong among the titles")
pilot_key = stats.title_key({"item_name": "Pilot"},
                            stats._episodes_by_name())[0]
check(pilot_key.startswith("polozka:"),
      f"and it is filed under no series ({pilot_key})")

# The watched time is lost nowhere - only this chart does not show it.
stored_seconds = db.query_value(
    "SELECT watched_seconds FROM playback WHERE session_key = 'sir-5'")
check(stored_seconds == 3600,
      f"a title the library does not know stays in the history ({stored_seconds} s)")


print()
print("--- and tidying fixes it in the data too, not only in the overview ---")
# In the overview it fits even without tidying, but the record still points at
# an item that does not exist - and that carries on (language, library, the
# link from the history).
orphan_count = importers.orphan_playback_count()
check(orphan_count >= 3, f"tidying sees the orphaned records ({orphan_count})")
linked = importers.relink_orphans()
check(linked["rows"] >= 2, f"and links them to items ({linked})")

bells = db.query_one("SELECT item_id FROM playback WHERE session_key = 'sir-1'")
check(bells["item_id"] == "got-5",
      f"'Zvony' now point at the real episode ({bells['item_id']})")
pilot = db.query_one("SELECT item_id FROM playback WHERE session_key = 'sir-4'")
check(str(pilot["item_id"]).startswith("osirely-"),
      f"the ambiguous 'Pilot' stayed unlinked ({pilot['item_id']})")
check(importers.relink_orphans()["rows"] == 0, "the second time there is nothing to link")


print()
print("--- imported history is linked by the episode number ---")
# Real data from the live database: **all** 1035 orphaned records were from an
# import and not one got linked. The reason was simple - pairing by the name
# looked in the library for the whole string
#
#     "Seal Team 6 - s02e07 - 7. epizoda"
#
# but there the episode is simply called "7. epizoda". Yet the string holds
# everything that determines one specific episode: the series, season, number.
analysis = importers.split_episode_title("Seal Team 6 - s02e07 - 7. epizoda")
check(analysis == {"series": "Seal Team 6", "season": 2, "episode": 7,
                   "name": "7. epizoda"},
      f"the name is taken apart into its parts ({analysis})")
check(importers.split_episode_title("Nadace - s02e10 - Mýty o stvoření")["episode"] == 10,
      "a two-digit episode number")
check(importers.split_episode_title("48. díl") is None,
      "a bare episode name cannot be taken apart - and nothing is made up")
check(importers.split_episode_title("Mýtus") is None, "nor a movie's name")

with db.connect() as conn:
    # The library: the episodes are named the way Jellyfin named them -
    # WITHOUT the series as a prefix.
    for ident, ep_name, series, series_name, season, number in (
        ("st-207", "7. epizoda", "ser-st", "Seal Team 6", 2, 7),
        ("st-101", "Pilot", "ser-st", "Seal Team 6", 1, 1),
        ("nad-210", "Mýty o stvoření", "ser-nad", "Nadace", 2, 10),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing, synced_at)
               VALUES (?, ?, 'Episode', 'lib', ?, ?, ?, ?, 0, ?)""",
            (ident, ep_name, series, series_name, season, number, now))

    for key, old_id, name in (
        ("import:jst:2235", "064b516426e8aaaa", "Seal Team 6 - s02e07 - 7. epizoda"),
        ("import:jst:1942", "064b516426e8bbbb", "Seal Team 6 - s01e01 - Pilot"),
        ("import:pbr:2026", "a9e42f4f7498dddd", "Nadace - s02e10 - Mýty o stvoření"),
        ("import:jst:790c", "c6e1882cea33eeee", "48. díl"),
    ):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u7', ?, ?, 'Episode', ?, ?, 3600, 0)""",
            (key, old_id, name, now, now))

linked = importers.relink_orphans()
check(linked["items"] >= 3, f"three composed names got linked ({linked})")
for key, expected in (("import:jst:2235", "st-207"), ("import:jst:1942", "st-101"),
                      ("import:pbr:2026", "nad-210")):
    actual = db.query_one("SELECT item_id FROM playback WHERE session_key = ?",
                          (key,))["item_id"]
    check(actual == expected, f"{key} -> {actual} (expected {expected})")

rest = db.query_one("SELECT item_id FROM playback WHERE session_key = 'import:jst:790c'")
check(str(rest["item_id"]).startswith("c6e1882"),
      f"'48. díl' has nothing to hold on to and stays ({rest['item_id']})")
check(importers.relink_orphans()["items"] == 0, "the second time there is nothing to link")

by_label = {r["label"] for r in stats.top_items(365, limit=40)}
check("Seal Team 6" in by_label,
      f"and the chart has the series, not the episodes ({sorted(by_label)[:8]})")


print()
print("--- what cannot be filed does not show in the chart ---")
# Leftovers where it cannot be found out what they were. Every series in the
# library has a "6. díl", so no unambiguous match exists and assigning them
# blindly would mean crediting the watching to another series.
overview = stats.top_items(365, limit=40)
labels = {r["label"] for r in overview}
check("48. díl" not in labels, "an unfileable leftover is not shown")
check(all(r["detail_url"] for r in overview), "what stayed can be clicked through")
check(overview[0].get("_skryto", 0) >= 1,
      f"and it shows how many were hidden ({overview[0].get('_skryto')})")

# The archive must NOT be hidden. A title that was in the library and is no
# longer in Jellyfin is a valid statistic - not waste.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, is_missing, synced_at)
           VALUES ('archiv-1', 'Smazaný film', 'Movie', 'lib', 1, ?)""", (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('arch', 'u8', 'archiv-1', 'Smazaný film', ?, ?, 9999, 0)""",
        (now, now))
archived = [r for r in stats.top_items(365, limit=40) if r["label"] == "Smazaný film"]
check(len(archived) == 1, "an archived title stays in the chart")
check(archived[0]["detail_url"] == "/item/archiv-1",
      f"and it can be opened ({archived[0]['detail_url']})")


print()
print("--- the same viewing from two import sources ---")
# Jellystat and Playback Reporting write a different moment (the start of the
# playback vs. the write into the table), so the same movie shows up twice with
# a shift - sometimes by half an hour, with night watching up to the next
# morning. An overlap in time therefore does not catch them; it is told by an
# equally long playback of the same title on the same device within one day.
def twin(key, name, start, minutes, lang=None, device="MIBOX4", user="dvoj"):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     device_name, audio_language, started_at,
                                     last_seen_at, ended_at, watched_seconds, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (key, user, name, name, device, lang, start, start, start, minutes * 60))


# Exactly the pairs from the reported listing.
twin("import:jst:d1", "Zvony HBO", "2026-08-01 21:38:00", 109, "cs")
twin("import:pbr:d1", "Zvony HBO", "2026-08-02 10:00:00", 108)
twin("import:jst:d2", "Poslední ze Starků", "2026-08-01 20:16:00", 75, "cs")
twin("import:pbr:d2", "Poslední ze Starků", "2026-08-01 19:38:00", 74)
# And what must NOT be merged:
twin("import:jst:d3", "Duna", "2026-07-01 20:00:00", 120, "cs")
twin("import:jst:d4", "Duna", "2026-07-05 20:00:00", 120, "cs")   # another day
twin("import:jst:d5", "Matrix", "2026-07-10 20:00:00", 120, "cs")
twin("import:jst:d6", "Matrix", "2026-07-10 22:10:00", 60, "cs")  # another length
twin("import:jst:d7", "Sedm", "2026-07-11 20:00:00", 90)
twin("import:jst:d8", "Sedm", "2026-07-11 21:00:00", 90, device="telefon")
twin("vlastni:d9", "Vetřelec", "2026-07-12 20:00:00", 90, "cs")
twin("vlastni:d10", "Vetřelec", "2026-07-12 23:00:00", 90, "cs")  # both our own
# (on purpose without an overlap in time - otherwise it would be a duplicate
#  for the other merging and something else would be tested than should be)

check(importers.import_duplicate_count() == 2,
      f"exactly two pairs are found ({importers.import_duplicate_count()})")
result = importers.merge_import_duplicates()
check(result["removed"] == 2, f"and two rows are deleted ({result})")

remaining = {r["item_name"]: r for r in db.query_all(
    "SELECT item_name, started_at, watched_seconds, audio_language"
    "  FROM playback WHERE user_id = 'dvoj'")}
check(list(remaining).count("Zvony HBO") == 1, "'Zvony' stayed once")
check(remaining["Zvony HBO"]["audio_language"] == "cs",
      "the richer record stayed - the one that knows the language")
check(remaining["Zvony HBO"]["watched_seconds"] == 109 * 60,
      f"and the longer of both times ({remaining['Zvony HBO']['watched_seconds']} s)")
check(remaining["Poslední ze Starků"]["started_at"].endswith("19:38:00"),
      f"the earliest start is taken ({remaining['Poslední ze Starků']['started_at']})")

stayed = [r["item_name"] for r in db.query_all(
    "SELECT item_name FROM playback WHERE user_id = 'dvoj'")]
for title, count, why in (("Duna", 2, "another day"), ("Matrix", 2, "another length"),
                          ("Sedm", 2, "another device"),
                          ("Vetřelec", 2, "both from our own collecting")):
    check(stayed.count(title) == count, f"{title}: not merged ({why})")
check(importers.merge_import_duplicates()["removed"] == 0, "the second time nothing")


print()
print("--- the language from an import counts in the statistics ---")
# The filter on imports was here because we do NOT KNOW the language - not
# because of where the record comes from. When the source sends it, it is as
# good as from our own collecting. On the other hand a record without a language
# must not get into the statistics, otherwise "Neuvedeno" would flood them.
check(importers._language_from_source({"audiolanguage": "ces"}) == "cs",
      "the language is read from the backup's column")
check(importers._language_from_source({"AudioLanguage": "eng"}) == "en",
      "the spelling of the column's name does not matter")
check(importers._language_from_source({"itemname": "Duna"}) is None,
      "and nothing is made up when the column is missing")

# The language statistics are computed over the whole history, so for a while
# we keep only these three rows - and right away put them back.
with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = -watched_seconds")
    for key, lang, seconds in (("import:pbr:j1", "cs", 3600),
                               ("import:pbr:j2", "en", 3600),
                               ("import:pbr:j3", None, 3600)):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     audio_language, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'uj', ?, 'Film', ?, ?, ?, ?, 0)""",
            (key, key, lang, now, now, seconds))

langs = {r["code"]: r for r in
         langstats.watched_languages(365, langstats.colour_map())["rows"]}
check(set(langs) == {"cs", "en"},
      f"both languages from the import counted ({sorted(langs)})")
check("und" not in langs,
      "but the record without a language did not flood the statistics with 'Neuvedeno'")

with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = -watched_seconds"
                 " WHERE watched_seconds < 0")


print()
print("--- the libraries' usage cannot exceed 100 % ---")
# The watched items were counted including the archive, the total only from
# the live ones - apples and oranges. For a series some of whose episodes
# vanished, the usage came out over a hundred per cent.
# An own library, so what the sections below use is not touched.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lu','Vyuziti',?)",
                 (now,))
    for i in range(1, 4):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, is_missing, synced_at,
                                  size_bytes)
               VALUES (?, ?, 'Movie', 'lu', 0, ?, 1000000000)""",
            (f"zivy-{i}", f"Film {i}", now))
    for i in range(1, 3):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, is_missing, synced_at,
                                  size_bytes)
               VALUES (?, ?, 'Movie', 'lu', 1, ?, 1000000000)""",
            (f"pryc-{i}", f"Smazaný {i}", now))
    for item in ("zivy-1", "zivy-2", "zivy-3", "pryc-1", "pryc-2"):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     library_id, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'uv', ?, 'x', 'lu', ?, ?, 3600, 0)""",
            (f"v-{item}", item, now, now))

usage = next(r for r in insights.storage_efficiency(365)
             if r["label"] == "Vyuziti")
check(usage["item_count"] == 3, f"the total counts the live ones ({usage['item_count']})")
check(usage["watched_items"] == 3,
      f"and the watched ones too - not including the archive ({usage['watched_items']})")
check(usage["watched_items"] <= usage["item_count"],
      "so the usage cannot exceed 100 %")


print()
print("--- 'in poor quality' is told from both sides of the picture ---")
# Only the height used to decide (`i.height < 1000`) and the width did not get
# into the template at all. All widescreen releases paid for it: a common 1080p
# movie in the scope ratio has 1920x800, so the height is below the boundary,
# even though it is a full 1080p. And the "Rozlišení" column then showed
# something other than what the file really is.
# An own library: these items are big and in the series library they would
# outweigh the picture, which is taken from the biggest title.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lkv','Kvalita',?)",
                 (now,))
    for ident, name, width, height, bitrate in (
        ("kv-sd", "Starý film", 720, 576, 1_500_000),
        # This case the old condition marked wrongly - the height 800 is below
        # the boundary, but 1920 wide is a full 1080p.
        ("kv-scope1080", "1080p v poměru scope", 1920, 800, 8_000_000),
        ("kv-ultra", "Širokoúhlý 1080p", 2560, 1080, 8_000_000),
        ("kv-scope", "4K scope", 3840, 1608, 25_000_000),
        ("kv-bitrate", "Málo datového toku", 1920, 1080, 1_200_000),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, width, height,
                                  bitrate, size_bytes, is_missing, synced_at)
               VALUES (?, ?, 'Movie', 'lkv', ?, ?, ?, 5000000000, 0, ?)""",
            (ident, name, width, height, bitrate, now))
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, watched_seconds, is_active)
               VALUES (?, 'ukvalita', ?, ?, ?, ?, 7200, 0)""",
            (f"kv-{ident}", ident, name, now, now))

candidates = {r["label"]: r for r in insights.upgrade_candidates(365, limit=50)}
check("Starý film" in candidates, "a really low resolution is found")
check("Málo datového toku" in candidates, "and a low bitrate")
check("1080p v poměru scope" not in candidates,
      f"1080p in the scope ratio is NOT poor quality ({sorted(candidates)})")
check("Širokoúhlý 1080p" not in candidates, "nor a widescreen release")
check("4K scope" not in candidates, "and 4K in the scope ratio not at all")

old_film = candidates["Starý film"]
check(old_film["width"] == 720 and old_film["height"] == 576,
      f"both dimensions reach the template ({old_film['width']}x{old_film['height']})")
check(old_film["detail_url"] == "/item/kv-sd",
      f"and the row can be clicked through ({old_film['detail_url']})")


print()
print("--- the history takes the resolution from the session, not from the library ---")
# A 4K movie in the scope ratio has the height 1608 - below the boundary for 4K.
# The resolution is told by the width, but that one did not get into the
# template at all: only `i.height` was selected. Such a movie then came out as
# 1080p in the history, while the "Právě se hraje" card rightly reported it as 4K.
from jellyscope.formatting import resolution_human  # noqa: E402

with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, width, height, is_missing, synced_at)
           VALUES ('scope-4k', 'Hanebný pancharti', 'Movie', 3840, 1608, 0, ?)""",
        (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 video_width, video_height, started_at,
                                 last_seen_at, watched_seconds, is_active)
           VALUES ('scope-1', 'u1', 'scope-4k', 'Hanebný pancharti', 3840, 1608,
                   ?, ?, 3600, 0)""", (now, now))
    # An older record that did not store the dimensions yet - they must be looked up.
    # On purpose at another time: were it overlapping the previous one, it would
    # be a duplicate by the tidying rules (the same viewer, the same movie, an overlap).
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('scope-2', 'u1', 'scope-4k', 'Hanebný pancharti',
                   datetime(?, '-3 hours'), datetime(?, '-3 hours'), 3600, 0)""",
        (now, now))

rows = {r["session_key"]: r for r in stats.history(200, 0)}
for key, label in (("scope-1", "with the dimensions written"),
                   ("scope-2", "without them (looked up from the library)")):
    row = rows[key]
    # `.get()` on purpose: were the width to vanish from the query again, the
    # test should fail on a readable message, not on a KeyError.
    width, height = row.get("width"), row.get("height")
    check(resolution_human(height, width) == "4K",
          f"a 4K scope movie is 4K in the history too – {label} "
          f"({width}x{height} -> {resolution_human(height, width)})")


print()
print("--- the series library has the picture from the series, not from an episode ---")
# An episode in Jellyfin has no backdrop, that belongs to the series. The tile
# of the series library therefore stayed grey - the picture did not load.
cards = {k["id"]: k for k in stats.library_cards()}
check("lib" in cards, "the library was listed")
check(cards["lib"]["poster_id"] == "ser-1" or cards["lib"]["poster_id"] == "ser-blue",
      f"the picture is taken from the series, not from an episode ({cards['lib']['poster_id']})")


print()
print("--- a link must not lead to a 404 ---")
# A title for which there is nothing in the library any more (a deleted file, an import).
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('osirely', 'u1', 'uz-neexistuje', 'Zmizelý film', ?, ?, 9999, 0)""",
        (now, now))
orphan = [r for r in stats.top_items(365, limit=20) if r["label"] == "Zmizelý film"]
check(not orphan,
      "a title to which nothing in the library nor in the archive leads is not shown")
left_row = db.query_one("SELECT watched_seconds FROM playback WHERE session_key = 'osirely'")
check(left_row["watched_seconds"] == 9999,
      f"but it does not vanish from the history ({left_row['watched_seconds']} s)")


print()
print("--- the libraries' usage counts history without library_id too ---")
# Imported history does not know the library and older collecting did not fill
# it in for an item we did not synchronise yet.
with_library = insights.storage_efficiency(365)
with db.connect() as conn:
    conn.execute("UPDATE playback SET library_id = NULL")
without_library = insights.storage_efficiency(365)

check([r["hours"] for r in with_library] == [r["hours"] for r in without_library],
      "the watched time did not change - the library is looked up through the item")
check([r["watched_items"] for r in with_library] == [r["watched_items"] for r in without_library],
      "and the number of watched titles did not either")
check(any(r["hours"] for r in without_library), "and they are not zeros")


print()
print("--- two collectors at once must not make a duplicate ---")
STREAMS = [{"Type": "Video", "Index": 0, "Codec": "h264", "Width": 1920, "Height": 1080}]


def snapshot(key: str) -> dict[str, Any]:
    return {"Id": key, "UserId": "u9", "UserName": "Karel",
            "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False},
            "NowPlayingItem": {"Id": "film-x", "Name": "Duna", "Type": "Movie",
                               "RunTimeTicks": 72_000_000_000,
                               "MediaStreams": STREAMS}}


before_collect = db.query_value("SELECT COUNT(*) FROM playback")
# An old version of the application builds the session key differently than the new one.
collector._store_sessions([snapshot("relace-STARA")], max_gap_seconds=600)
collector._store_sessions([snapshot("relace-NOVA")], max_gap_seconds=600)
collector._store_sessions([snapshot("relace-NOVA")], max_gap_seconds=600)
added = db.query_value("SELECT COUNT(*) FROM playback") - before_collect
check(added == 1, f"a single record came about, not two ({added})")


print()
print("--- and what already lies in the database can be tidied ---")
with db.connect() as conn:
    # Exactly the user's situation: two versions wrote the same playback separately.
    for key, shift, duration in (("stara::x", "+0 minutes", 2400),
                                 ("nova::x", "+1 minutes", 2700)):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     started_at, last_seen_at, ended_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u5', 'film-y', 'Duna', datetime(?, ?), ?,
                       datetime(?, '+45 minutes'), ?, 0)""",
            (key, now, shift, now, now, duration))

check(importers.duplicate_playback_count() == 1,
      f"tidying knows there is one duplicate ({importers.duplicate_playback_count()})")
result = importers.merge_duplicate_playback()
check(result["removed"] == 1, f"and deletes exactly one row ({result})")
kept_row = db.query_one("SELECT watched_seconds FROM playback WHERE item_id = 'film-y'")
check(int(kept_row["watched_seconds"]) == 2700,
      f"the more complete one stayed, not summed ({kept_row['watched_seconds']} s)")
check(importers.merge_duplicate_playback()["removed"] == 0,
      "the second time it finds nothing")

print()
print("--- what must NOT be merged ---")
# This is the part where the most damage can be done: a deleted row of the
# history does not come back. Each case is a situation that really happens in
# a household.
cases = [
    ("a movie started yesterday and finished today",
     [("2026-08-15 22:00:00", "2026-08-15 23:00:00", 3600, "n-1", "TV", "u1"),
      ("2026-08-16 20:00:00", "2026-08-16 21:30:00", 5400, "n-1", "TV", "u1")]),
    ("finished and started it again right away",
     [("2026-08-14 10:00:00", "2026-08-14 12:00:00", 7200, "n-2", "TV", "u1"),
      ("2026-08-14 12:00:00", "2026-08-14 14:00:00", 7200, "n-2", "TV", "u1")]),
    ("the same movie on the TV and on the phone at once",
     [("2026-08-12 20:00:00", "2026-08-12 22:00:00", 7200, "n-3", "TV", "u1"),
      ("2026-08-12 20:30:00", "2026-08-12 21:00:00", 1800, "n-3", "telefon", "u1")]),
    ("two users at the same movie at the same moment",
     [("2026-08-11 20:00:00", "2026-08-11 22:00:00", 7200, "n-4", "TV", "u1"),
      ("2026-08-11 20:00:00", "2026-08-11 22:00:00", 7200, "n-4", "TV", "u2")]),
]
for label, records in cases:
    before = importers.duplicate_playback_count()
    with db.connect() as conn:
        for order, (start, end, seconds, item, device, user_id) in enumerate(records):
            conn.execute(
                """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                         device_id, started_at, last_seen_at,
                                         ended_at, watched_seconds, is_active)
                   VALUES (?,?,?,'Film',?,?,?,?,?,0)""",
                (f"{item}-{order}", user_id, item, device, start, end, end, seconds))
    added = importers.duplicate_playback_count() - before
    check(added == 0, f"{label} ({added} to merge)")

# And the other way round: a record from an old version that did not report the
# device should be merged. An unknown device contradicts nobody.
before = importers.duplicate_playback_count()
with db.connect() as conn:
    for key, start, end, seconds, device in (
        ("bez-zarizeni", "2026-08-10 20:00:00", "2026-08-10 20:40:00", 2400, None),
        ("se-zarizenim", "2026-08-10 20:00:30", "2026-08-10 20:45:00", 2700, "TV"),
    ):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     device_id, started_at, last_seen_at,
                                     ended_at, watched_seconds, is_active)
               VALUES (?, 'u1', 'n-5', 'Film', ?, ?, ?, ?, ?, 0)""",
            (key, device, start, end, end, seconds))
check(importers.duplicate_playback_count() - before == 1,
      "an unknown device does not prevent merging - otherwise the old version would slip away")


print()
print("--- merged episode history can be returned to the right episodes ---")
# The consequence of the bug described at the top: everything hangs on one
# episode, but the episode's name in the record stayed right.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE item_name LIKE '%díl%'")
    for i in range(1, 5):
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     series_name, started_at, last_seen_at,
                                     watched_seconds, is_active)
               VALUES (?, 'u1', 'ep-1', ?, 'Kancelář', ?, ?, 1800, 0)""",
            (f"slite-{i}", f"{i}. díl", now, now))

check(len(importers.misplaced_episode_rows()) == 3,
      f"three of four hang on another episode ({len(importers.misplaced_episode_rows())})")
repair = importers.repair_episode_links()
check(repair["moved"] == 3, f"and they return ({repair})")

where = {r["item_id"] for r in db.query_all(
    "SELECT DISTINCT item_id FROM playback WHERE item_name LIKE '%díl%'")}
check(len(where) == 4, f"each episode has its own plays again ({sorted(where)})")
check(importers.repair_episode_links()["moved"] == 0, "the second time there is nothing to return")


print()
print("--- import from the plugin's backup (TSV) ---")
tsv = "\n".join("\t".join(r) for r in [
    ["2026-03-01 20:00:00", "u1", "tsv-1", "Movie", "Duna", "DirectPlay",
     "Jellyfin Web", "Chrome", "3600"],
    ["2026-03-02 21:00:00", "u2", "tsv-2", "Movie", "Něco", "Transcode",
     "Findroid", "Pixel", "1800"],
    ["2026-03-03 22:00:00", "u1", "tsv-3", "Episode", "1. díl", "DirectPlay",
     "Kodi", "TV", "30"],
]).encode("utf-8")

result = asyncio.run(importers.import_playback_reporting_tsv(tsv, min_seconds=60))
check(result["status"] == "ok", f"a file without a header is read ({result.get('message')})")
check(result["imported"] == 2, f"two records, the short one was skipped ({result['imported']})")

# Some versions of the plugin write the header, others do not - we must tell it ourselves.
with_header = (b"DateCreated\tUserId\tItemId\tItemType\tItemName\t"
               b"PlaybackMethod\tClientName\tDeviceName\tPlayDuration\n" + tsv)
again = asyncio.run(importers.import_playback_reporting_tsv(with_header, min_seconds=60))
check(again["imported"] == 0 and again["duplicate"] == 2,
      f"the same content with a header is not doubled ({again['imported']}/{again['duplicate']})")

# An uploaded nonsense must give some advice, not fail.
wrong = asyncio.run(importers.import_playback_reporting_tsv(b'{"neco": "jineho"}'))
check(wrong["status"] == "error" and "Backup" in wrong["message"],
      f"another file advises where to make the backup ({wrong['message'][:60]}…)")
empty = asyncio.run(importers.import_playback_reporting_tsv(b""))
check(empty["status"] == "error", "an empty file does not fail")


print()
print("--- archived episodes do not count among the live ones ---")
# This was the difference "Jellyfin shows 100, Jellyscope 120": the library's
# list left archived episodes out, the series' detail listed them among the
# others. And from the difference it could not be told why.
with db.connect() as conn:
    for number in (11, 12, 13):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, is_missing,
                                  synced_at, size_bytes)
               VALUES (?, ?, 'Episode', 'lib', 'ser-1', 'Kancelář', 1, ?, 1, ?, 1000000000)""",
            (f"ep-smazany-{number}", f"{number}. díl", number, now))

detail = stats.series_detail("ser-1")
in_library = [r for r in stats.library_rows(50, 0) if r["id"] == "ser-1"]
check(detail["episode_count"] == in_library[0]["episode_count"],
      f"the detail and the library's list report the same count "
      f"({detail['episode_count']} vs {in_library[0]['episode_count']})")
check(detail["archived_count"] == 3,
      f"and the archived ones are listed apart ({detail['archived_count']})")
check(all(d["is_missing"] for d in detail["archived"]),
      "the archive holds only what really is not in Jellyfin")
check(all(not d["is_missing"] for season in detail["seasons"] for d in season["episodes"]),
      "the archived ones are not among the seasons")

# A series of which only the archive is left must still open - otherwise the
# link from the history would end in a 404.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE series_id = 'ser-1'")
all_archived = stats.series_detail("ser-1")
check(bool(all_archived) and all_archived["episode_count"] > 0,
      "a series only in the archive still opens")
check(all_archived["missing"] is True, "and it is marked as vanished")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 0 WHERE id LIKE 'ep-%'"
                 " AND id NOT LIKE 'ep-smazany-%'")


print()
print("--- recently added: a batch of episodes can be clicked open ---")
# When a series gets a whole season, the card should first look like any other
# - just the name. Only on hover the poster grows and shows links to the single
# episodes. Only the data belong here; the growing is CSS's job.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lr','Seriály',?)",
                 (now,))
    # A whole season came within a few minutes.
    for number in range(1, 6):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                                  parent_index_number, index_number, date_created,
                                  is_missing, synced_at)
               VALUES (?, ?, 'Episode', 'lr', 'ser-nova', 'Nová sezóna', 2, ?, ?, 0, ?)""",
            (f"nova-{number}", f"{number}. díl", number,
             f"2026-08-17 10:0{number}:00", now))
    # Another series - a single episode.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              is_missing, synced_at)
           VALUES ('sam-1', 'Poslední díl', 'Episode', 'lr', 'ser-sam', 'Jiný seriál',
                   1, 9, '2026-08-17 09:00:00', 0, ?)""", (now,))
    # And an episode of the same series from last month - it does not belong in the batch.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              is_missing, synced_at)
           VALUES ('sam-0', 'Starý díl', 'Episode', 'lr', 'ser-sam', 'Jiný seriál',
                   1, 8, '2026-07-01 09:00:00', 0, ?)""", (now,))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, date_created,
                              is_missing, synced_at)
           VALUES ('film-r', 'Nějaký film', 'Movie', 'lr', '2026-08-17 08:00:00', 0, ?)""",
        (now,))

recent = {r["title"]: r for r in stats.recently_added(10)}
new_season = recent["Nová sezóna"]
check(len(new_season["episodes"]) == 5,
      f"five episodes in the batch ({len(new_season['episodes'])})")
# Top to bottom like a list of episodes, not by the date added: Jellyfin loads
# the files in the order it finds them on the disk, so the batch comes shuffled -
# and reading "E1, E2, E3..." is the only thing that makes sense.
check([d["id"] for d in new_season["episodes"]] ==
      ["nova-1", "nova-2", "nova-3", "nova-4", "nova-5"],
      f"sorted from the first episode ({[d['id'] for d in new_season['episodes']]})")
check(new_season["series_url"] == "/series/ser-nova",
      f"and the name leads to the series ({new_season['series_url']})")

single = recent["Jiný seriál"]
check(single["episodes"] == [],
      f"for one episode no list is made ({len(single['episodes'])})")
check(single["id"] == "sam-1", "the card points straight at that episode")

check(recent["Nějaký film"]["episodes"] == [], "for a movie neither")

# An episode from last month must not count in the batch - otherwise something
# would show in the listing that has nothing to do with the novelty.
check(all(d["id"] != "sam-0" for d in single["episodes"]),
      "an old episode does not belong in the batch")

# The same episode in two files (an old version left beside the new one) are
# two items in Jellyfin. In the listing of novelties that would make two equal
# rows - and one of them points at the item the next scan merges away, so the
# link stops working.
with db.connect() as conn:
    conn.execute("UPDATE items SET height = 1080 WHERE id = 'nova-3'")
    # The copy is newer, but has no technical data yet.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, date_created,
                              height, is_missing, synced_at)
           VALUES ('nova-3-kopie', '3. díl', 'Episode', 'lr', 'ser-nova',
                   'Nová sezóna', 2, 3, '2026-08-17 10:04:30', NULL, 0, ?)""",
        (now,))

new_season = {r["title"]: r for r in stats.recently_added(10)}["Nová sezóna"]
numbers = [(d["parent_index_number"], d["index_number"]) for d in new_season["episodes"]]
check(numbers == [(2, 1), (2, 2), (2, 3), (2, 4), (2, 5)],
      f"the same episode a second time does not repeat in the listing ({numbers})")
check([d["id"] for d in new_season["episodes"]][2] == "nova-3",
      "the one that already has measured technical data stays")


print()
print("--- the resolution in Insights is computed from both sides of the picture ---")
# Two queries there selected only `i.height` and the width did not get into the
# template at all, so a 4K movie in the scope ratio (3840x1608) came out as
# 1080p - the height is below the boundary. Besides, the variant's label for
# duplicates was composed right from the height ("hevc / 1608p").
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM playback")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lz','Filmy',?)",
                 (now,))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, width, height, video_codec,
                              bitrate, size_bytes, date_created, path,
                              production_year, is_missing, synced_at)
           VALUES ('scope4k', 'Hanebný pancharti', 'Movie', 'lz', 3840, 1608, 'hevc',
                   25000000, 60000000000, '2020-01-01 00:00:00', '/m/x.mkv',
                   2009, 0, ?)""", (now,))
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 play_method, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('z1', 'uz', 'scope4k', 'Hanebný pancharti', 'Transcode',
                   ?, ?, 7200, 0)""", (now, now))

# Only what nobody watched belongs into "unwatched content" - hence an own item
# nobody started.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, width, height, video_codec,
                              bitrate, size_bytes, date_created, production_year,
                              is_missing, synced_at)
           VALUES ('scope-mrtvy', 'Nikdo to nevidel', 'Movie', 'lz', 3840, 1608,
                   'hevc', 25000000, 60000000000, '2020-01-01 00:00:00', 2009, 0, ?)""",
        (now,))

for name, rows in (
    ("unwatched content", insights.dead_storage(days=1)["rows"]),
    ("big and rarely watched", insights.oversized_rarely_watched(365)),
    ("transcoded files", insights.transcode_offenders(365)),
):
    row = rows[0] if rows else {}
    check(row.get("width") == 3840 and row.get("height") == 1608,
          f"{name}: both dimensions arrive ({row.get('width')}x{row.get('height')})")

# The variant's label for duplicates must use the same rule.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, width, height, video_codec,
                              size_bytes, production_year, is_missing, synced_at)
           VALUES ('scope-kopie', 'Hanebný pancharti', 'Movie', 'lz', 1920, 800,
                   'h264', 8000000000, 2009, 0, ?)""", (now,))
duplicates = insights.duplicate_candidates()
check(duplicates and "4K" in duplicates[0]["variants"],
      f"the variant is described by the same rule ({duplicates[0]['variants'] if duplicates else '-'})")
check(duplicates and "1608p" not in duplicates[0]["variants"],
      "and not by the bare height")


print()
print("--- the link from the most watched leads where the label does ---")
# Reported from use: the row had the right name and was in the right section,
# but the link led to a quite different item.
#
# It came about like this: a movie had the same name as an episode of another
# series, so by the name it was assigned to its group. It did not carry a
# `series_id` itself (hence the guessing from the name), so a link to its own
# item came out of it - and because it was watched more, it became the group's
# representative.
with db.connect() as conn:
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM playback")
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('lp','Vše',?)",
                 (now,))
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, series_id, series_name,
                              parent_index_number, index_number, is_missing, synced_at)
           VALUES ('dil-x','Skandál v Belgravii','Episode','lp','ser-sh','Sherlock',
                   2,1,0,?)""", (now,))
    # A movie of the same name. It has no series of its own - and must not have one.
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, is_missing, synced_at)
           VALUES ('film-x','Skandál v Belgravii','Movie','lp',0,?)""", (now,))
    for key, ident, series, kind, seconds in [
            ("sh-1", "dil-x", "Sherlock", "Episode", 3600),
            # The movie is watched more, so it would become the group's representative.
            ("sh-2", "film-x", None, "Movie", 7200)]:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                     item_type, series_name, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?,'u1',?,'Skandál v Belgravii',?,?,?,?,?,0)""",
            (key, ident, kind, series, now, now, seconds))

rows = {r["label"]: r for r in stats.top_items(365, limit=10)}
check(set(rows) == {"Sherlock", "Skandál v Belgravii"},
      f"the movie and the series stay two rows ({sorted(rows)})")
check(rows["Sherlock"]["detail_url"] == "/series/ser-sh",
      f"the series leads to the series ({rows['Sherlock']['detail_url']})")
check(rows["Sherlock"]["is_series"] is True, "and it is in the right section")
check(rows["Skandál v Belgravii"]["detail_url"] == "/item/film-x",
      f"the movie to its own item ({rows['Skandál v Belgravii']['detail_url']})")
check(round(rows["Sherlock"]["hours"], 1) == 1.0,
      f"and the hours are not added up across two different titles ({rows['Sherlock']['hours']})")

# A second safeguard for the case when a record SHOULD get into a series'
# group: an episode for which Jellyfin did not send the series' id is assigned
# to it by the name. The link then must not be taken from it (it has nothing
# to offer), but from the group's key - that is from the series' id.
group = stats._merge_titles(
    [{"item_id": "dil-bez-serialu", "item_name": "Velká hra",
      "series_name": "Sherlock", "existing_id": "dil-bez-serialu",
      "series_id": None, "i_series_name": None, "i_name": "Velká hra",
      "i_type": "Episode", "is_missing": 0, "seconds": 9999},
     {"item_id": "dil-x", "item_name": "Skandál v Belgravii",
      "series_name": "Sherlock", "existing_id": "dil-x",
      "series_id": "ser-sh", "i_series_name": "Sherlock",
      "i_name": "Skandál v Belgravii", "i_type": "Episode",
      "is_missing": 0, "seconds": 10}],
    stats.KIND_BOTH, 10, {"hours": "seconds"})
check(len(group) == 1, f"both episodes are one series ({len(group)})")
check(group[0]["detail_url"] == "/series/ser-sh",
      f"the link is taken from the group's key, not from the strongest record "
      f"({group[0]['detail_url']})")


print()
print("--- unifying names by the library ---")
# The name is stored with a playback together with the record. When a title is
# renamed later (or wrongly matched metadata get fixed in Jellyfin), the old
# record carries the original name - and in the statistics one title carries
# the name of another.
with db.connect() as conn:
    conn.execute("UPDATE items SET name = 'Skandál v Belgravii (2012)'"
                 " WHERE id = 'film-x'")

check(importers.stale_name_rows() == 1,
      f"the record that does not fit is found ({importers.stale_name_rows()})")
result = importers.unify_names()
check(result == {"items": 1, "rows": 1}, f"and it is unified ({result})")
check(db.query_value("SELECT item_name FROM playback WHERE item_id = 'film-x'")
      == "Skandál v Belgravii (2012)", "the name in the history fits the library")
check(importers.stale_name_rows() == 0, "the second time there is nothing to unify")

# An orphaned record is left alone: its name is the only thing we know about it.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 item_type, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES ('osirely','u1','uz-neexistuje','Něco dávného','Movie',
                   ?,?,1800,0)""", (now, now))
importers.unify_names()
check(db.query_value("SELECT item_name FROM playback WHERE session_key = 'osirely'")
      == "Něco dávného", "for a record without an item the name is not erased")


print()
print("--- the fingerprint of finished tasks ---")
# By a change of this number the page tells that a task ended - even when it
# ran wholly between two requests.
before_task = scanner.tasks_version()
scan_id = scanner.start_task_log("recent")
check(scanner.tasks_version() == before_task,
      "a started task does not change the fingerprint - there is nothing to show yet")
scanner.finish_task_log(scan_id, "done", total=1, ok=1, message="hotovo")
check(scanner.tasks_version() != before_task,
      f"a finished one does ({before_task} -> {scanner.tasks_version()})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
