# -*- coding: utf-8 -*-
r"""Music: the sync of artists, albums and tracks from Jellyfin, and its task.

Jellyfin does not run: the answers are recorded (tests/music_responses/, see tests/_music.py). It
guards:

G) no music library: nothing is fetched, tables stay, there is no page; two runs at once - the
   second is refused;
A) the first run: everything (a full pass), columns from Jellyfin's answer, edge cases from 10.11
   (hi-res, two artists in one field, disc 2, m4a, no album), an artist only on a track is added,
   MusicBrainz ids; answers of 12.1 are read too; a second run adds nothing;
B) incremental: reading stops at the first page where everything is known;
C) a full pass after 24 hours: what vanished gets `missing`, what came back loses it;
D) an error in the middle of a full pass: nothing is marked missing, the time of the last full pass
   stays, the state says the error without the key;
E) incomplete metadata: no source, no genres, no length, no album nor artist;
F) pairing afterwards: an imported history row without a type whose item is a track is music;
H) the task "Synchronizace hudby";
J) small things from the final review;
I) memory of a full pass;
K) the file path of a track.

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md

Run:
    .venv/bin/python tests/test_music_sync.py
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "music-sync.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "tajny-klic-jellyfinu"

from _package import substitute  # noqa: E402
from _music import fake_jellyfin, recorded, track  # noqa: E402
from jellyscope import db, scanner, stats, tasks  # noqa: E402
from jellyscope.music import sync  # noqa: E402
from jellyscope import music  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def count(table: str, where: str = "1 = 1") -> int:
    return int(db.query_value(f"SELECT COUNT(*) FROM {table} WHERE {where}") or 0)


def row(table: str, name: str) -> dict:
    return db.query_one(f"SELECT * FROM {table} WHERE name = ?", (name,)) or {}


def run_sync(client: type, **kwargs) -> dict:
    restore = substitute(sync, "JellyfinClient", client)
    try:
        return asyncio.run(music.synchronize(**kwargs))
    finally:
        restore()


def clean() -> None:
    with db.connect() as conn:
        for table in ("music_track", "music_album", "music_artist"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM settings WHERE key LIKE 'music_%'")
        conn.commit()


print("--- G) no music library, two at once ---")
film_libraries = [lib for lib in recorded("knihovny_12") if lib.get("CollectionType") != "music"]
check(run_sync(fake_jellyfin(libraries=film_libraries)) == {"library_count": 0} and count("music_track") == 0
      and not music.has_music(), "Jellyfin without a music library: nothing is fetched and there is no Music page")


async def two_at_once() -> list:
    return await asyncio.gather(music.synchronize(), music.synchronize(), return_exceptions=True)


restore = substitute(sync, "JellyfinClient", fake_jellyfin(libraries=film_libraries))
results = asyncio.run(two_at_once())
restore()
check(sum(isinstance(r, music.MusicError) and "already running" in str(r) for r in results) == 1,
      f"two runs at once: the second is refused ({results})")
check(not music.sync_running(), "and nothing runs afterwards")

print()
print("--- A) the first run: everything ---")
calls: list = []
result = run_sync(fake_jellyfin(calls=calls))
check(result.get("full") is True and result.get("track_count") == 17 and result.get("album_count") == 5, f"the first run is a full pass ({result})")
check(count("music_track") == 17 and count("music_album") == 5 and count("music_artist") == 5,
      f"17 tracks, 5 albums, 4 artists from the list + 1 only from a track ({count('music_track')}, {count('music_album')}, "
      f"{count('music_artist')})")
check(music.has_music(), "after the sync there is music")
hires = row("music_track", "Hi-res s MusicBrainz")
check((hires.get("codec"), hires.get("sample_rate"), hires.get("bit_depth"), hires.get("channels"), hires.get("bitrate"), hires.get("size_bytes"))
      == ("flac", 96000, 24, 1, 775841, 581881),
      f"a track's technical data from the audio stream and the source ({hires.get('codec')}, {hires.get('bit_depth')})")
check((hires.get("album"), hires.get("artist"), hires.get("track_number"), hires.get("disk"), hires.get("year"), hires.get("genres"))
      == ("Okrajové případy", "Hraničáři", 1, None, 2024, "Experimental") and hires.get("album_id") and hires.get("artist_id")
      and hires.get("added_at", "").startswith("20") and hires.get("missing") == 0 and hires.get("library_id"),
      "a track: album, artist, number, year, genres, time added, library")
m4a = row("music_track", "Bez žánru, disk 2")
check((m4a.get("codec"), m4a.get("disk"), m4a.get("genres"), m4a.get("container")) == ("aac", 2, "", "mov,mp4,m4a,3gp,3g2,mj2"),
      f"m4a: the format from the codec, not the container; disc two; no genre ({m4a.get('codec')}, {m4a.get('disk')})")
no_album = row("music_track", "Skladba bez alba a interpreta")
check(no_album.get("album") == "Bez alba" and not no_album.get("artist") and not no_album.get("artist_id"),
      f"a track without an album: the album name from its folder (Jellyfin's album), no artist ({no_album.get('album')!r})")
two = row("music_track", "Dva interpreti")
guest = row("music_artist", "Hraničáři; Host z venku")
check(two.get("artist") == "Hraničáři; Host z venku" and guest.get("id") == two.get("artist_id") and guest.get("missing") == 0,
      "an artist found only on a track is added from the track")
album_row = row("music_album", "Okrajové případy")
check((album_row.get("musicbrainz_id"), album_row.get("artist"), album_row.get("cover_tag"))
      == ("11111111-1111-4111-8111-111111111111", "Hraničáři", ""),
      f"an album: MusicBrainz, album artist, cover ({album_row.get('musicbrainz_id')})")
check(row("music_album", "Aurora Sessions").get("cover_tag") and row("music_album", "Aurora Sessions").get("year") == 2021,
      "an album with a cover and a year")
check(row("music_artist", "Hraničáři").get("musicbrainz_id") == "22222222-2222-4222-8222-222222222222",
      "an artist's MusicBrainz id is added from tracks (the artist in the list does not have it)")
types = {t for t, *_ in calls}
check(types == {"MusicArtist", "MusicAlbum", "Audio"}, f"it asks for artists, albums and tracks ({types})")
calls.clear()
second = run_sync(fake_jellyfin(calls=calls))
check(second.get("new_count") == 0 and second.get("full") is False and count("music_track") == 17
      and sorted(start for t, start, _ in calls if t == "Audio") == [0],
      f"a second run adds nothing and reads only the first page ({second}, {calls})")
# Answers of Jellyfin 12.1 (other ids) - read the same into a clean database.
clean()
result_12 = run_sync(fake_jellyfin(version="12"))
check((result_12.get("artist_count"), result_12.get("album_count"), result_12.get("track_count")) == (3, 3, 13)
      and row("music_track", "First Light").get("codec") == "flac", f"answers of Jellyfin 12.1 are read the same ({result_12})")
clean()
run_sync(fake_jellyfin())

print()
print("--- B) incremental ---")
tracks = copy.deepcopy(recorded("skladby_10")["Items"])
new_track = track("First Light", Id="nova-skladba-1", Name="Nová skladba", DateCreated="2026-11-15T19:00:00.0000000Z")
calls.clear()
restore = substitute(sync, "PAGE_SIZE", 5)
result = run_sync(fake_jellyfin(tracks=[new_track, *tracks], calls=calls))
check(result.get("new_count") == 1 and row("music_track", "Nová skladba").get("album") == "Aurora Sessions",
      f"a new track comes ({result})")
check(sorted(start for t, start, _ in calls if t == "Audio") == [0, 5],
      f"reading stops at the first page where everything is known ({sorted(start for t, start, _ in calls if t == 'Audio')})")
renamed = copy.deepcopy(tracks)
renamed[12]["Name"] = "Přejmenovaná"
run_sync(fake_jellyfin(tracks=[new_track, *renamed]))
check(not row("music_track", "Přejmenovaná"), "an incremental run does not see a rename deeper down (waits for the full pass)")
restore()

print()
print("--- C) a full pass after 24 hours ---")
now = datetime.now(UTC)
without_one = [new_track, *tracks]
gone_track = without_one.pop(3)
albums = copy.deepcopy(recorded("alba_10")["Items"])
gone_album = next(a for a in albums if a["Name"] == "Bez alba")
# 22, not 23: a full pass is due an hour early (FULL_PASS_SLACK), a daily task starts a minute earlier or later.
db.set_setting("music_full_pass", (now - timedelta(hours=22)).strftime("%Y-%m-%d %H:%M:%S"))
result = run_sync(fake_jellyfin(tracks=without_one, albums=[a for a in albums if a is not gone_album]))
check(result.get("full") is False and count("music_track", "missing = 1") == 0, "22 hours after a full pass: incremental only, nothing missing")
db.set_setting("music_full_pass", (now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S"))
result = run_sync(fake_jellyfin(tracks=[t for t in without_one if t["Name"] != "Přejmenovaná"],
                                   albums=[a for a in albums if a is not gone_album]))
missing = {r["name"] for r in db.query_all("SELECT name FROM music_track WHERE missing = 1")}
check(result.get("full") is True and missing == {gone_track["Name"]} and row("music_album", "Bez alba").get("missing") == 1,
      f"after 24 hours a full pass: the vanished track and album have `missing` ({missing})")
check(count("music_track") == 18, "rows of the vanished stay (history and charts keep their names)")
result = run_sync(fake_jellyfin(tracks=[new_track, *tracks], albums=albums), full=True)
check(count("music_track", "missing = 1") == 0 and row("music_album", "Bez alba").get("missing") == 0,
      "a track and an album that came back lose `missing`")
check(row("music_track", "Přejmenovaná").get("id") == tracks[12]["Id"] or row("music_track", tracks[12]["Name"]),
      "a full pass fixes the name as Jellyfin has it")

print()
print("--- D) an error in the middle of a full pass ---")
last_full = db.get_setting("music_full_pass", "")
restore = substitute(sync, "PAGE_SIZE", 5)
try:
    run_sync(fake_jellyfin(tracks=without_one, fail_at=("Audio", 5)), full=True)
    failed = ""
except music.MusicError as error:
    failed = str(error)
restore()
state = music.sync_state()
check(bool(failed) and count("music_track", "missing = 1") == 0 and db.get_setting("music_full_pass", "") == last_full,
      f"an error in the middle: nothing is marked missing, the time of the full pass stays ({failed[:60]})")
check(state.get("result") == "error" and state.get("error") and "tajny-klic-jellyfinu" not in str(state),
      f"the state says the error, Jellyfin's key is not in it ({state.get('error')})")

print()
print("--- E) incomplete metadata ---")
odd = [
    track("First Light", Id="divna-1", Name="Bez zdroje", MediaSources=None, Genres=None, RunTimeTicks=None, ProductionYear=None),
    track("First Light", Id="divna-2", Name="Bez proudu", MediaSources=[{"Size": "x", "Bitrate": None, "MediaStreams": []}]),
    track("First Light", Id="divna-3", Name="Bez alba úplně", AlbumId=None, Album=None, ArtistItems=None, AlbumArtists=None, Artists=None),
    {"Id": "divna-4", "Name": None, "Type": "Audio"},
]
try:
    result = run_sync(fake_jellyfin(tracks=[*odd, new_track, *tracks], albums=albums))
    failed = ""
except Exception as error:  # noqa: BLE001 - exactly this is guarded
    failed = repr(error)
check(not failed and count("music_track", "id LIKE 'divna-%'") == 4, f"tracks without data are stored and nothing fails ({failed[:80]})")
no_source = row("music_track", "Bez zdroje")
check(no_source.get("codec") in ("", None) and no_source.get("size_bytes") is None and no_source.get("genres") == ""
      and no_source.get("runtime_ticks") is None, "a missing source and genres are empty values")
check(row("music_track", "Bez proudu").get("size_bytes") is None, "a nonsense size is an empty value")
check(db.query_one("SELECT name, album_id FROM music_track WHERE id = 'divna-4'") == {"name": "", "album_id": None},
      "a track without a name or an album has an empty name")

print()
print("--- F) pairing imported history afterwards ---")
now_text = db.utcnow()
with db.connect() as conn:
    for key, item_id, item_type in (("imp-1", tracks[0]["Id"], None), ("imp-2", "neznamy-film", None), ("imp-3", tracks[1]["Id"], "Movie")):
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                     " watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, 'u1', 'Jana', ?, 'Převzaté', ?, ?, ?, 300, 0, 0, 0)",
                     (key, item_id, item_type, now_text, now_text))
    conn.commit()
before = stats.overview(1)
result = run_sync(fake_jellyfin(tracks=[new_track, *tracks], albums=albums))
types_of_rows = {r["session_key"]: r["item_type"] for r in db.query_all("SELECT session_key, item_type FROM playback WHERE session_key LIKE 'imp-%'")}
check(types_of_rows == {"imp-1": "Audio", "imp-2": None, "imp-3": "Movie"} and result.get("paired") == 1,
      f"a row without a type whose item is a track is music; the others stay ({types_of_rows}, {result.get('paired')})")
check(stats.overview(1)["plays"] == before["plays"] - 1 and stats.history_count(kind="music") == 1,
      "the paired listening leaves the watching numbers and is in History under Music")
# Review of 15. 11. 2026: Playback Reporting and Jellystat store ids with dashes (and capitals), music
# has them without. The import normalises ids only against items, where music is not - listening then
# stayed without a track (no artist, no album, out of the charts, an album "nobody plays").


def with_dashes(bare: str) -> str:
    return f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}".upper()


with db.connect() as conn:
    for key, item_id, item_type in (("imp-4", with_dashes(tracks[2]["Id"]), None), ("imp-5", with_dashes(tracks[3]["Id"]), "Audio")):
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                     " watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, 'u1', 'Jana', ?, 'Převzaté', ?, ?, ?, 300, 0, 0, 0)",
                     (key, item_id, item_type, now_text, now_text))
    conn.commit()
run_sync(fake_jellyfin(tracks=[new_track, *tracks], albums=albums))
rows = {r["session_key"]: (r["item_id"], r["item_type"]) for r in db.query_all("SELECT session_key, item_id, item_type FROM playback"
                                                                               " WHERE session_key IN ('imp-4', 'imp-5')")}
in_breakdown = {r["session_key"]: r["track_id"] for r in stats.music_analysis(1)["rows"] if r["session_key"] in ("imp-4", "imp-5")}
check(rows == {"imp-4": (tracks[2]["Id"], "Audio"), "imp-5": (tracks[3]["Id"], "Audio")}
      and in_breakdown == {"imp-4": tracks[2]["Id"], "imp-5": tracks[3]["Id"]},
      f"an imported id with dashes is normalised to the track's id and the listening has its track ({rows})")

print()
print("--- H) the task ---")
task = tasks.TASKS.get("music")
check(task is not None and task.default_minutes == 60 and task.interval_setting == "music_sync_minutes"
      and task.log_kind == "music" and tasks.is_enabled(task), "the task music: 60 minutes (music_sync_minutes), on")
restore = substitute(sync, "JellyfinClient", fake_jellyfin(tracks=[new_track, *tracks], albums=albums))
outcome = asyncio.run(tasks.run_now("music"))
restore()
check(outcome["status"] == "ok" and "Hudba" in outcome["message"] and (scanner.last_scan("music") or {}).get("status") == "ok",
      f"the task's run is in the task log ({outcome})")
restore = substitute(sync, "JellyfinClient", fake_jellyfin(libraries=film_libraries))
outcome = asyncio.run(tasks.run_now("music"))
restore()
check(outcome == {"status": "ok", "message": "Jellyfin has no music library."}, f"without a music library - the key ({outcome})")
restore = substitute(sync, "JellyfinClient", fake_jellyfin(fail_at=("MusicArtist", 0)))
outcome = asyncio.run(tasks.run_now("music"))
restore()
check(outcome["status"] == "error" and outcome["message"] and "tajny-klic-jellyfinu" not in outcome["message"],
      f"a Jellyfin error: the task ends with an error and a text ({outcome})")

print()
print("--- J) small things from the final review (16. 11. 2026) ---")
import threading  # noqa: E402

# An artist's MusicBrainz id: the list of artists does not have it, it comes from tracks. An incremental
# run overwrote it with an empty value for every artist it read - and filled it only from the tracks it
# read in that run.
run_sync(fake_jellyfin(), full=True)
with_mbid = {r["id"]: r["musicbrainz_id"] for r in db.query_all("SELECT id, musicbrainz_id FROM music_artist WHERE musicbrainz_id <> ''")}
restore_page = substitute(sync, "PAGE_SIZE", 1)
try:
    run_sync(fake_jellyfin(), full=False)
finally:
    restore_page()
after_incremental = {r["id"]: r["musicbrainz_id"] for r in db.query_all("SELECT id, musicbrainz_id FROM music_artist")}
check(bool(with_mbid) and all(after_incremental.get(i) == m for i, m in with_mbid.items()),
      f"an incremental run does not erase an artist's MusicBrainz id ({len(with_mbid)} with an id, after the run "
      f"{sum(1 for i in with_mbid if after_incremental.get(i))})")

# Pairing imported history reads the whole history: in a thread, not in the main loop (the web and the
# collector would stand still meanwhile).
threads: list[bool] = []
_original_pair = sync._pair_history


def _pair_recording_thread() -> int:
    threads.append(threading.current_thread() is threading.main_thread())
    return _original_pair()


restore_pair = substitute(sync, "_pair_history", _pair_recording_thread)
try:
    run_sync(fake_jellyfin())
finally:
    restore_pair()
check(threads == [False], f"pairing the history runs outside the main thread ({threads})")

# A music library vanished from Jellyfin: its content is missing (the Music tab goes); should it come
# back, the next run is a full pass and everything returns.
check(music.has_music() and count("music_track", "missing = 0") > 0, "before the library goes there is music")
missing_before = count("music_track", "missing = 1")
libraries_without_music = [lib for lib in recorded("knihovny_12") if lib.get("CollectionType") != "music"]
# Review of 16. 11. 2026: one run is not enough - at its start (or with a token without rights) Jellyfin
# returns the list of libraries empty or incomplete and music would vanish for an hour. A library is gone
# only after two runs in a row.
run_sync(fake_jellyfin(libraries=libraries_without_music))
check(music.has_music() and count("music_track", "missing = 0") > 0, "one run without a music library: nothing missing yet (may be momentary)")
run_sync(fake_jellyfin(libraries=libraries_without_music))
check(not music.has_music() and count("music_track", "missing = 0") == 0 and count("music_album", "missing = 0") == 0
      and count("music_artist", "missing = 0") == 0 and count("music_track") > 0,
      "the music library vanished from Jellyfin: everything is missing (rows stay), there is no music")
result = run_sync(fake_jellyfin())
check(result.get("full") is True and music.has_music() and count("music_track", "missing = 1") == missing_before,
      f"the library came back: a full pass and everything is back ({result.get('full')}, missing {count('music_track', 'missing = 1')}"
      f" as before {missing_before})")

# One of several music libraries vanished: its albums and tracks are missing right after it is confirmed
# (two runs), not only after the daily full pass.
with db.connect() as conn:
    conn.execute("INSERT INTO music_album (id, name, library_id, synced_at) VALUES ('jina-album', 'Jiné album', 'jina-knihovna', ?)",
                 (db.utcnow(),))
    conn.execute("INSERT INTO music_track (id, name, album_id, library_id, synced_at) VALUES ('jina-skladba', 'Jiná',"
                 " 'jina-album', 'jina-knihovna', ?)", (db.utcnow(),))
    conn.commit()
run_sync(fake_jellyfin(), full=False)
first = row("music_track", "Jiná").get("missing")
run_sync(fake_jellyfin(), full=False)
check(first == 0 and row("music_track", "Jiná").get("missing") == 1 and row("music_album", "Jiné album").get("missing") == 1
      and row("music_track", "First Light").get("missing") == 0,
      "one of the music libraries vanished: after two runs its albums and tracks are missing, the others stay")

# The daily full pass in a run Jellyfin left one of the libraries out (once - it may be starting): that library
# was not read, so its albums, tracks and artist are not marked missing. Only _gone_libraries decides, after two
# runs in a row.
db.set_setting(sync.GONE_LIBRARIES_KEY, "")
with db.connect() as conn:
    conn.execute("INSERT INTO music_artist (id, name, synced_at) VALUES ('druhy-interpret', 'Druhý interpret', ?)",
                 (db.utcnow(),))
    conn.execute("INSERT INTO music_album (id, name, artist_id, library_id, synced_at) VALUES ('druhe-album', 'Druhé album',"
                 " 'druhy-interpret', 'druha-knihovna', ?)", (db.utcnow(),))
    conn.execute("INSERT INTO music_track (id, name, album_id, artist_id, library_id, synced_at) VALUES ('druha-skladba',"
                 " 'Druhá', 'druhe-album', 'druhy-interpret', 'druha-knihovna', ?)", (db.utcnow(),))
    conn.commit()
run_sync(fake_jellyfin(), full=True)
check(row("music_track", "Druhá").get("missing") == 0 and row("music_album", "Druhé album").get("missing") == 0
      and row("music_artist", "Druhý interpret").get("missing") == 0,
      "a full pass without one of the libraries: its tracks, album and artist are not missing after one run")
run_sync(fake_jellyfin(), full=True)
check(row("music_track", "Druhá").get("missing") == 1 and row("music_album", "Druhé album").get("missing") == 1
      and row("music_artist", "Druhý interpret").get("missing") == 1 and row("music_track", "First Light").get("missing") == 0,
      "left out of two runs in a row: they are missing, the others stay")

# A MusicBrainz id is kept only for artists (their list does not have it). Albums and tracks have their
# own: when somebody deletes it in Jellyfin (a fixed tag), it goes here too.
import _music  # noqa: E402

without_mbid = _music.album("Okrajové případy", ProviderIds={})
run_sync(fake_jellyfin(albums=[without_mbid, *[a for a in recorded("alba_10")["Items"] if a.get("Name") != "Okrajové případy"]]),
         full=True)
check(row("music_album", "Okrajové případy").get("musicbrainz_id") == "", "a MusicBrainz id deleted from an album is deleted in Jellyscope too")

# Pages are written to the database in a thread (hundreds of writes per run for a big library).
writes: list[bool] = []
_original_write = sync._write


def _write_recording_thread(*args, **kwargs):
    writes.append(threading.current_thread() is threading.main_thread())
    return _original_write(*args, **kwargs)


restore_write = substitute(sync, "_write", _write_recording_thread)
try:
    run_sync(fake_jellyfin(), full=True)
finally:
    restore_write()
check(writes and not any(writes), f"pages are written outside the main thread ({len(writes)} writes, in the main one {sum(writes)})")

# "Is there music?" is asked on every page (the menu): the query uses the index even when everything is missing.
if db.current_dialect() == "sqlite":
    plan = " | ".join(str(r["detail"]) for r in db.query_all("EXPLAIN QUERY PLAN SELECT 1 FROM music_track WHERE missing = 0 LIMIT 1"))
    check("USING" in plan and "INDEX" in plan, f"the query \"is there music?\" uses the index ({plan})")
check(db.get_setting("hudba_knihoven", "") == "", "the dead setting hudba_knihoven is no longer written")

print()
print("--- I) memory of a full pass ---")
# Final review (15. 11. 2026): a full pass kept the raw JSON of every track until the end of the run
# (about 8 KB per track) - only for the names and MusicBrainz ids of artists. A library of 100,000
# tracks took about 0.8 GB on the first run and on every daily full pass.
import json  # noqa: E402
import tracemalloc  # noqa: E402

TEMPLATE = json.dumps(track())
TRACKS = 4000


class _BigLibrary:
    """A fake Jellyfin with a big library: every page is freshly parsed JSON (as from httpx)."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def virtual_folders(self):
        return [{"Name": "Hudba", "ItemId": "velka-knihovna", "CollectionType": "music"}]

    async def items_page(self, start_index, limit, item_types, user_id=None, parent_id=None, sort_by=None, sort_order=None,
                         fields=None):
        await asyncio.sleep(0)
        if item_types != "Audio":
            return {"Items": [], "TotalRecordCount": 0}
        items = []
        for i in range(start_index, min(start_index + limit, TRACKS)):
            item = json.loads(TEMPLATE)
            item["Id"] = f"velka{i:027d}"
            items.append(item)
        return {"Items": items, "TotalRecordCount": TRACKS}


tracemalloc.start()
result = run_sync(_BigLibrary, full=True)
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
check(result.get("track_count") == TRACKS and peak < 12_000_000,
      f"a full pass of {TRACKS} tracks does not keep Jellyfin's raw answers (peak {peak / 1e6:.1f} MB, {result.get('track_count')})")

print()
print("--- K) the file path of a track (16. 11. 2026, content_kinds.py) ---")
# Disks count music by the path: Jellyfin sends it in MediaSources already (10.11 and 12.1).
for version, answers in (("10", "skladby_10"), ("12", "skladby_12")):
    clean()
    run_sync(fake_jellyfin(version=version))
    expected = {item["Id"]: item["MediaSources"][0]["Path"] for item in recorded(answers)["Items"]}
    stored = {r["id"]: r["path"] for r in db.query_all("SELECT id, path FROM music_track")}
    check(stored == expected and all(expected.values()),
          f"Jellyfin {version}: every track keeps MediaSources[0].Path ({len(stored)})")
clean()
tracks = copy.deepcopy(recorded("skladby_10")["Items"])
del tracks[0]["MediaSources"][0]["Path"]
run_sync(fake_jellyfin(tracks=tracks), full=True)
check(db.query_value("SELECT path FROM music_track WHERE id = ?", (tracks[0]["Id"],)) == "",
      "a track Jellyfin gives no path for: '' (asked, none given), not NULL")
# Tracks synced before this version have no path: an incremental sync stops at the first known
# page, so a full one runs until no present track lacks it - and then not again.
clean()
run_sync(fake_jellyfin(), full=True)
db.set_setting("music_full_pass", (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))
check(sync._full_pass_due() is False, "an hour after a full sync, with every path known: no full sync")
with db.connect() as conn:
    conn.execute("UPDATE music_track SET path = NULL WHERE id = ?", (tracks[1]["Id"],))
    conn.commit()
check(sync._full_pass_due() is True, "a present track without a path: a full sync, however recent the last")
result = run_sync(fake_jellyfin())
check(result.get("full") is True and db.query_value("SELECT COUNT(*) FROM music_track WHERE path IS NULL") == 0,
      f"the sync runs in full and fills the path ({result.get('full')})")
check(sync._full_pass_due() is False, "and the next one is incremental again")
with db.connect() as conn:
    conn.execute("UPDATE music_track SET path = NULL, missing = 1 WHERE id = ?", (tracks[2]["Id"],))
    conn.commit()
check(sync._full_pass_due() is False, "a track gone from Jellyfin without a path forces nothing")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
