# -*- coding: utf-8 -*-
r"""Music: the library's statistics and the data of the artist, album and viewer pages.

Guarded:

A) the library's numbers: artists, albums, tracks, size, length; what disappeared from Jellyfin (`missing`) does not
   count;
B) formats by codec and the sound quality: lossless × lossy by bitrate bands (the bounds 128 and 256 kb/s), hi-res
   (more than 16 bits or more than 48 kHz), unknown;
C) the library's genres, decades of release, newly added albums, the largest albums;
D) what is not listened to: albums without a single play in the whole history (not only in the period);
E) the artist and album pages: a known, a vanished and an unknown id; the album's tracks in the order disc and
   number; listening in the period;
F) a viewer: their listening in the period, even when they listened to nothing.

Run:
    .venv/bin/python tests/test_music_library_stats.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "music-library-test.db")
os.environ["SECRET_KEY"] = "test-key"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, stats  # noqa: E402
from jellyscope.stats.music_library import album_detail  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


NOW = datetime.now(UTC).replace(microsecond=0)


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


MB = 1_000_000
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u-jana', 'Jana')")
    conn.execute("INSERT INTO users (id, name) VALUES ('u-petr', 'Petr')")
    for artist_id, name, vanished in (("ia", "Alfa", 0), ("ib", "Beta", 0), ("ig", "Gama", 0), ("iz", "Vanished", 1)):
        conn.execute("INSERT INTO music_artist (id, name, missing) VALUES (?, ?, ?)", (artist_id, name, vanished))
    ALBUMS = (  # id, name, artist, year, genres, added (days ago), vanished
        ("al1", "Hi-res", "ia", 2021, "Ambient", 1, 0), ("al2", "Radio", "ia", 1999, "Lo-fi", 10, 0),
        ("al3", "Synth", "ib", 1985, "Synthwave|Pop", 30, 0), ("al4", "Silence", "ig", None, "", 300, 0),
        ("al5", "Gone", "iz", 2000, "Rock", 5, 1))
    for album_id, name, artist_id, year, genres, days, vanished in ALBUMS:
        conn.execute("INSERT INTO music_album (id, name, artist_id, artist, year, genres, cover_tag, added_at, missing)"
                     " VALUES (?, ?, ?, (SELECT name FROM music_artist WHERE id = ?), ?, ?, ?, ?, ?)",
                     (album_id, name, artist_id, artist_id, year, genres, f"tag-{album_id}", ago(days=days), vanished))
    TRACKS = (  # id, name, album, artist, number, disc, length s, codec, bitrate, sample rate, bit depth, size, genres, vanished
        ("s1", "Hi 1", "al1", "ia", 1, None, 300, "flac", 2_300_000, 96000, 24, 80 * MB, "Ambient", 0),
        ("s2", "Hi 2", "al1", "ia", 2, None, 300, "flac", 1_000_000, 44100, 16, 40 * MB, "Ambient", 0),
        ("s3", "Radio 1", "al2", "ia", 1, None, 200, "mp3", 320_000, 44100, None, 8 * MB, "Lo-fi", 0),
        ("s4", "Radio 2", "al2", "ia", 2, None, 200, "mp3", 128_000, 44100, None, 3 * MB, "Lo-fi", 0),
        ("s5", "Synth 2-1", "al3", "ib", 1, 2, 180, "aac", 96_000, 44100, None, 2 * MB, "Synthwave|Pop", 0),
        ("s6", "Synth 1-2", "al3", "ib", 2, 1, 180, "aac", 256_000, 48000, None, 5 * MB, "Synthwave", 0),
        ("s7", "Synth 1-1", "al3", "ib", 1, 1, 180, "opus", None, 48000, None, 4 * MB, "", 0),
        ("s8", "Silence", "al4", "ig", 1, None, 60, "", None, None, None, None, "", 0),
        ("s9", "Gone", "al5", "iz", 1, None, 100, "flac", 900_000, 44100, 16, 10 * MB, "Rock", 1))
    for track_id, name, album_id, artist_id, number, disc, length, codec, bitrate, rate, depth, size, genres, vanished in TRACKS:
        conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, disk, runtime_ticks, codec,"
                     " bitrate, sample_rate, bit_depth, size_bytes, genres, missing) VALUES (?, ?, ?, (SELECT name FROM music_album WHERE id = ?),"
                     " ?, (SELECT name FROM music_artist WHERE id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (track_id, name, album_id, album_id, artist_id, artist_id, number, disc, length * 10_000_000, codec, bitrate,
                      rate, depth, size, genres, vanished))
    for key, uid, track_id, seconds, when in (("p1", "u-jana", "s1", 300, ago(hours=5)), ("p2", "u-petr", "s1", 100, ago(days=2)),
                                              ("p3", "u-jana", "s3", 200, ago(days=3)), ("p4", "u-jana", "s5", 10, ago(days=4)),
                                              ("p5", "u-petr", "s4", 200, ago(days=60))):
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                     " watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, ?, ?, ?, 'x', 'Audio', ?, ?, ?, 0, 0, 0)",
                     (key, uid, "Jana" if uid == "u-jana" else "Petr", track_id, when, when, seconds))
    conn.commit()

print("--- A) the library's numbers ---")
check(stats.music_library_summary() == {"artist_count": 3, "album_count": 4, "track_count": 8, "size_bytes": 142 * MB, "seconds": 1600},
      f"artists, albums, tracks, size and length without what vanished ({stats.music_library_summary()})")

print()
print("--- B) formats and quality ---")
formats = [(f["format"], f["track_count"], round(f["share"], 1), f["size_bytes"]) for f in stats.music_formats()]
check(formats == [("AAC", 2, 25.0, 7 * MB), ("FLAC", 2, 25.0, 120 * MB), ("MP3", 2, 25.0, 11 * MB), ("Opus", 1, 12.5, 4 * MB),
                  ("", 1, 12.5, 0)], f"formats by codec, an unknown one as empty ({formats})")
check(stats.music_quality() == {"lossless": 2, "hires": 1, "lossy": {"up_to_128": 1, "up_to_256": 1, "from_256": 2}, "unknown": 2},
      f"lossless, hi-res, lossy by bands below 128, 128-255, from 256 kb/s (128 and 256 belong higher), unknown ({stats.music_quality()})")
check("flac" in stats.LOSSLESS and "alac" in stats.LOSSLESS and "mp3" not in stats.LOSSLESS, "the list of lossless codecs")

print()
print("--- C) genres, years, new and largest albums ---")
check([(g["genre"], g["track_count"]) for g in stats.library_genres()] == [("Ambient", 2), ("Lo-fi", 2), ("Synthwave", 2), ("Pop", 1)],
      f"genres by the count of tracks ({stats.library_genres()})")
check(stats.music_decades() == [{"decades": 1980, "album_count": 1}, {"decades": 1990, "album_count": 1}, {"decades": 2020, "album_count": 1},
                                {"decades": None, "album_count": 1}], f"decades of release, without a year at the end ({stats.music_decades()})")
check([a["id"] for a in stats.music_newest()] == ["al1", "al2", "al3", "al4"] and stats.music_newest()[0]["track_count"] == 2
      and stats.music_newest()[0]["cover_tag"] == "tag-al1", "newly added albums from the newest, with tracks and cover")
check([(a["id"], a["size_bytes"], a["track_count"]) for a in stats.music_largest()] == [("al1", 120 * MB, 2), ("al2", 11 * MB, 2),
                                                                                  ("al3", 11 * MB, 3), ("al4", 0, 1)],
      f"the largest albums ({[(a['id'], a['size_bytes']) for a in stats.music_largest()]})")

print()
print("--- D) what is not listened to ---")
check([a["id"] for a in stats.music_unplayed()] == ["al4", "al3"],
      f"without a play in the whole history: Silence and Synth (10 seconds is not a play), the longest in the library "
      f"first; Radio (a listen 60 days ago) not ({[a['id'] for a in stats.music_unplayed()]})")

print()
print("--- E) the artist and album pages ---")
alfa = stats.music_artist("ia", 30)
check(alfa and alfa["artist"]["name"] == "Alfa" and alfa["numbers"] == {"seconds": 600, "plays": 3, "listener_count": 2}
      and [a["id"] for a in alfa["albums"]] == ["al1", "al2"],
      f"artist: the period's numbers and their albums ({alfa and alfa['numbers']})")
check(alfa and [(s["id"], s["seconds"]) for s in alfa["tracks"]] == [("s1", 400), ("s3", 200)]
      and [(p["user_name"], p["seconds"]) for p in alfa["listeners"]] == [("Jana", 500), ("Petr", 100)],
      "artist: the most listened tracks and the listeners")
check(stats.music_artist("none", 30) is None and stats.music_artist("iz", 30)["artist"]["missing"] == 1,
      "an unknown artist = None; a vanished one has a page with a flag")
# The album page (album_detail, 16. 11. 2026: in the Library, over the whole history).
synth = album_detail("al3")
check(synth and [s["id"] for s in synth["tracks"]] == ["s7", "s6", "s5"] and synth["album"]["artist"] == "Beta",
      f"album: tracks in the order disc and number ({synth and [s['id'] for s in synth['tracks']]})")
check(synth and synth["tracks"][2]["plays"] == 0 and synth["tracks"][2]["seconds"] == 10 and synth["plays"] == 0,
      "album: listening of the tracks (10 seconds of a three-minute track is not a play)")
check(album_detail("none") is None and album_detail("al5")["album"]["missing"] == 1, "an unknown album None, a vanished one with a flag")

print()
print("--- E2) an album of various artists, only the rows of the page ---")
# Review 15. 11. 2026: the artist page counted listening only by the track's first artist, so the album's artist (a
# compilation "Various artists", "B feat. A") had zero on its albums.
with db.connect() as conn:
    conn.execute("INSERT INTO music_artist (id, name) VALUES ('iv', 'Various')")
    conn.execute("INSERT INTO music_album (id, name, artist_id, artist, added_at) VALUES ('al6', 'Compilation', 'iv', 'Various', ?)",
                 (ago(days=2),))
    conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, runtime_ticks)"
                 " VALUES ('s10', 'On the compilation', 'al6', 'Compilation', 'ig', 'Gama', 1, 1800000000)")
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                 " watched_seconds, paused_seconds, is_paused, is_active) VALUES ('p6', 'u-petr', 'Petr', 's10', 'x', 'Audio', ?, ?, 50, 0, 0, 0)",
                 (ago(days=1), ago(days=1)))
    conn.commit()
various = stats.music_artist("iv", 30)
check([(a["id"], a["seconds"]) for a in various["albums"]] == [("al6", 50)] and stats.music_artist("ig", 30)["numbers"]["seconds"] == 50,
      f"an album on its artist's page shows its listening, even when someone else recorded the tracks ({various['albums']})")
# A minor of the final review (16. 11. 2026): the artist page's numbers differed from the artists' ranking (the page also
# counted the artist's albums). Now an artist's number is the same thing everywhere: the listening of their tracks.
ranking = {i["id"]: i["seconds"] for i in stats.music_artists(stats.music_analysis(30))}
check(all(stats.music_artist(i, 30)["numbers"]["seconds"] == ranking.get(i, 0) for i in ("ia", "ib", "ig", "iv"))
      and various["numbers"]["seconds"] == 0,
      f"the artist page's numbers fit the ranking ({ranking}; Various {various['numbers']['seconds']})")
# Review: the viewer, artist and album pages pulled the whole server's listening of the period into Python and filtered
# only there.
MARK = "LEFT JOIN music_track s ON s.id = p.item_id"
returned: list[int] = []
_original_query_iter = db.query_iter


def _listen(sql, *a, **k):
    # The analysis reads row by row (db.query_iter): the rows the database really returned are counted.
    rows = list(_original_query_iter(sql, *a, **k))
    if MARK in sql:
        returned.append(len(rows))
    return iter(rows)


db.query_iter = _listen
try:
    stats.music_viewer("u-jana", 30)
    stats.music_artist("ia", 30)
    album_detail("al3")
finally:
    db.query_iter = _original_query_iter
check(returned == [3, 3, 1], f"the database returns only the listening of the viewer, artist and album ({returned}; 5 in the period)")

print()
print("--- F) a viewer ---")
check(stats.music_viewer("u-jana", 30) == {"seconds": 510, "plays": 2, "artist_id": "ia", "artist": "Alfa"},
      f"the viewer's listening in the period ({stats.music_viewer('u-jana', 30)})")
check(stats.music_viewer("nobody", 30) == {"seconds": 0, "plays": 0, "artist_id": None, "artist": ""}, "a viewer without listening")

print()
print("--- G) minors of the final review (16. 11. 2026) ---")
# A track of zero length (Jellyfin RunTimeTicks 0): the play rule takes the length from the play. Python did so, the SQL of
# the card "Not listened to" did not - the album was then "unplayed" even with a play.
with db.connect() as conn:
    conn.execute("INSERT INTO music_album (id, name, artist_id, artist, added_at) VALUES ('al7', 'No length', 'ig', 'Gama', ?)",
                 (ago(days=3),))
    conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, runtime_ticks)"
                 " VALUES ('s11', 'Zero', 'al7', 'No length', 'ig', 'Gama', 1, 0)")
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, media_runtime_ticks, started_at,"
                 " last_seen_at, watched_seconds, paused_seconds, is_paused, is_active) VALUES ('p7', 'u-petr', 'Petr', 's11', 'x', 'Audio',"
                 " 300000000, ?, ?, 20, 0, 0, 0)", (ago(hours=2), ago(hours=2)))
    conn.commit()
check(album_detail("al7")["plays"] == 1 and "al7" not in [a["id"] for a in stats.music_unplayed()],
      "a track of zero length: 20 s of 30 s is a play for the card \"Not listened to\" too")
# The order of album lists ends with an unambiguous key (a.id): albums of the same name with the same date or size would
# otherwise swap places between loads on PostgreSQL.
orders: list[str] = []
_original_all = db.query_all


def _record_order(sql, *a, **k):
    if "FROM music_album a" in sql and "ORDER BY" in sql:
        orders.append(sql.rsplit("ORDER BY", 1)[1].split("LIMIT")[0].strip())
    return _original_all(sql, *a, **k)


db.query_all = _record_order
try:
    stats.music_newest()
    stats.music_largest()
    stats.music_unplayed()
    stats.music_artist("ia", 30)
finally:
    db.query_all = _original_all
check(len(orders) == 4 and all(r.endswith("a.id") for r in orders), f"album lists sort by the id last ({orders})")
# Review 16. 11. 2026: an album on an artist's page is to show its whole listening, not only the listening of this
# artist's tracks (an artist with one track on the compilation "Various").
with db.connect() as conn:
    conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, runtime_ticks)"
                 " VALUES ('s12', 'Second on the compilation', 'al6', 'Compilation', 'ib', 'Beta', 2, 1800000000)")
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                 " watched_seconds, paused_seconds, is_paused, is_active) VALUES ('p8', 'u-jana', 'Jana', 's12', 'x', 'Audio', ?, ?, 40, 0, 0, 0)",
                 (ago(hours=3), ago(hours=3)))
    conn.commit()
compilation = {i: next((a["seconds"] for a in stats.music_artist(i, 30)["albums"] if a["id"] == "al6"), None) for i in ("ig", "ib", "iv")}
check(compilation == {"ig": 90, "ib": 90, "iv": 90}, f"an album on the page of any of its artists shows its whole listening ({compilation})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
