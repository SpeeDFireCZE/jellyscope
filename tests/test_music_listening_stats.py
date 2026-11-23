# -*- coding: utf-8 -*-
r"""Music: the listening statistics (stats/music_listening.py) over hand-counted data.

Listening is a history row of the kind "Audio"; the track, album and artist
come from the music tables. Guarded:

A) the period's analysis: one query on the history, music only, the period only;
B) numbers: listening time, plays (at least 30 seconds, or at least half of
   the track), listeners, distinct artists and tracks;
C) by days and day of the week × hour (in the application's zone);
D) listeners and their most listened artist;
E) charts of artists, albums, tracks and genres; a track the
   synchronisation does not know yet counts under its name without an artist;
F) technique: direct × transcoded and the reasons, apps, data over the network;
G) the start of the listening history and "the whole history".

Run:
    .venv/bin/python tests/test_music_listening_stats.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hudba-statistiky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import db, formatting, stats  # noqa: E402

failures = 0
db.init_db()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


NOW = datetime.now(UTC).replace(microsecond=0)


def ago(**amount) -> str:
    return (NOW - timedelta(**amount)).strftime("%Y-%m-%d %H:%M:%S")


with db.connect() as conn:
    for uid, name in (("u-jana", "Jana"), ("u-petr", "Petr"), ("u-eva", "Eva")):
        conn.execute("INSERT INTO users (id, name) VALUES (?, ?)", (uid, name))
    for iid, title in (("ia", "Alfa"), ("ib", "Beta"), ("ig", "Gama")):
        conn.execute("INSERT INTO music_artist (id, name) VALUES (?, ?)", (iid, title))
    for aid, title, iid, artist, cover in (("al1", "První", "ia", "Alfa", "t1"), ("al2", "Druhé", "ib", "Beta", ""),
                                           ("al3", "Třetí", "ig", "Gama", "")):
        conn.execute("INSERT INTO music_album (id, name, artist_id, artist, cover_tag) VALUES (?, ?, ?, ?, ?)",
                     (aid, title, iid, artist, cover))
    for sid, title, aid, album, iid, artist, length, genres, bitrate in (
            ("s1", "Jedna", "al1", "První", "ia", "Alfa", 240, "Rock|Pop", 900000),
            ("s2", "Dvě", "al1", "První", "ia", "Alfa", 300, "Rock", 900000),
            ("s3", "Tři", "al2", "Druhé", "ib", "Beta", 30, "Jazz", 128000),
            ("s4", "Čtyři", "al3", "Třetí", "ig", "Gama", 200, "", 320000)):
        conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, runtime_ticks, genres, bitrate, codec)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'flac')", (sid, title, aid, album, iid, artist, length * 10_000_000, genres, bitrate))
    LISTENING = [  # (key, viewer, name, track, title, seconds, when, app, method, reason, playback bitrate, length in the session)
        ("p1", "u-jana", "Jana", "s1", "Jedna", 240, ago(hours=2), "Finamp", "DirectPlay", None, None, 240),
        ("p2", "u-jana", "Jana", "s1", "Jedna", 20, ago(hours=3), "Finamp", "DirectPlay", None, None, 240),
        ("p3", "u-petr", "Petr", "s2", "Dvě", 300, ago(hours=26), "Jellyfin Web", "Transcode", "AudioCodecNotSupported", 192000, 300),
        ("p4", "u-petr", "Petr", "s3", "Tři", 20, ago(hours=27), "Jellyfin Web", "DirectPlay", None, None, 30),
        ("p5", "u-eva", "Eva", "s4", "Čtyři", 200, ago(days=5), "Feishin", "DirectStream", None, None, 200),
        ("p6", "u-jana", "Jana", "neznama-1", "Neznámá skladba", 100, ago(days=6), "Finamp", "DirectPlay", None, None, 150),
        ("p7", "u-petr", "Petr", "s1", "Jedna", 240, ago(days=40), "Finamp", "DirectPlay", None, None, 240),
    ]
    for key, uid, name, sid, title, seconds, when, app, method, reason, bitrate, length in LISTENING:
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
                     " play_method, transcode_reasons, bitrate, media_runtime_ticks, started_at, last_seen_at, watched_seconds,"
                     " paused_seconds, is_paused, is_active) VALUES (?, ?, ?, ?, ?, 'Audio', ?, 'Telefon', ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)",
                     (key, uid, name, sid, title, app, method, reason, bitrate, length * 10_000_000, when, when, seconds))
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                 " watched_seconds, paused_seconds, is_paused, is_active) VALUES ('film', 'u-jana', 'Jana', 'f1', 'Film', 'Movie',"
                 " ?, ?, 5000, 0, 0, 0)", (ago(hours=4), ago(hours=4)))
    conn.commit()

print("--- A) the period's analysis ---")
queries: list[str] = []
original, original_iter = db.query_all, db.query_iter


def _count(sql, *a, **k):
    queries.append(sql)
    return original(sql, *a, **k)


def _count_iter(sql, *a, **k):
    # The analysis reads row by row (db.query_iter) - it is counted the same.
    queries.append(sql)
    return original_iter(sql, *a, **k)


db.query_all, db.query_iter = _count, _count_iter
try:
    r = stats.music_analysis(30)
    numbers = stats.music_numbers(r)
    for name in ("music_by_day", "music_heatmap", "music_listeners", "music_artists", "music_albums", "music_tracks",
                 "music_genres", "music_play_method", "music_apps", "music_network"):
        getattr(stats, name)(r)
finally:
    db.query_all, db.query_iter = original, original_iter
check(sum("FROM playback" in q for q in queries) == 1, f"the page asks the history once ({sum('FROM playback' in q for q in queries)}×)")
check(sorted(row["session_key"] for row in r["rows"]) == ["p1", "p2", "p3", "p4", "p5", "p6"],
      f"listening only and the period only ({sorted(row['session_key'] for row in r['rows'])})")

print()
print("--- B) numbers ---")
check(numbers == {"seconds": 880, "plays": 5, "listener_count": 3, "artist_count": 3, "track_count": 5}, f"the listening numbers ({numbers})")
check(stats.PLAY_FROM_SECONDS == 30, "a play counts from 30 seconds")
plays = {row["session_key"]: row["plays"] for row in r["rows"]}
check(plays == {"p1": True, "p2": False, "p3": True, "p4": True, "p5": True, "p6": True},
      f"20 seconds of a four-minute track is no play, 20 of a half-minute one is ({plays})")

print()
print("--- C) by days, day of the week × hour ---")
zone = formatting.zone()


def local(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(zone)


expected_days: dict[str, list[int]] = {}
grid = [[0.0] * 24 for _ in range(7)]
for key, _u, _j, _s, _n, seconds, when, *_ in LISTENING[:6]:
    day = local(when).date().isoformat()
    expected_days.setdefault(day, [0, 0])
    expected_days[day][0] += seconds
    expected_days[day][1] += int(plays[key])
    grid[local(when).weekday()][local(when).hour] += seconds / 3600
check(stats.music_by_day(r) == [{"day": d, "seconds": s, "plays": p} for d, (s, p) in sorted(expected_days.items())],
      "listening by days in the application's zone")
check([[round(h, 6) for h in row] for row in stats.music_heatmap(r)] == [[round(h, 6) for h in row] for row in grid],
      "listening hours by day of the week and hour (row 0 = Monday)")

print()
print("--- D) listeners ---")
check([(p["user_id"], p["user_name"], p["seconds"], p["plays"], p["artist"]) for p in stats.music_listeners(r)]
      == [("u-jana", "Jana", 360, 2, "Alfa"), ("u-petr", "Petr", 320, 2, "Alfa"), ("u-eva", "Eva", 200, 1, "Gama")],
      f"listeners from the most hours, with the most listened artist ({stats.music_listeners(r)})")

print()
print("--- E) charts ---")
artists = [(i["id"], i["name"], i["seconds"], i["plays"], i["listener_count"]) for i in stats.music_artists(r)]
check(artists == [("ia", "Alfa", 560, 2, 2), ("ig", "Gama", 200, 1, 1), (None, "", 100, 1, 1), ("ib", "Beta", 20, 1, 1)],
      f"artists; a track the synchronisation does not know is under an unknown artist ({artists})")
albums = [(a["id"], a["name"], a["artist"], a["cover_tag"], a["seconds"], a["plays"]) for a in stats.music_albums(r)]
check(albums == [("al1", "První", "Alfa", "t1", 560, 2), ("al3", "Třetí", "Gama", "", 200, 1), (None, "", "", "", 100, 1),
                 ("al2", "Druhé", "Beta", "", 20, 1)], f"albums with a cover ({albums})")
tracks = [(s["id"], s["name"], s["album_id"], s["artist"], s["seconds"], s["plays"], s["listener_count"]) for s in stats.music_tracks(r)]
check(tracks == [("s2", "Dvě", "al1", "Alfa", 300, 1, 1), ("s1", "Jedna", "al1", "Alfa", 260, 1, 1), ("s4", "Čtyři", "al3", "Gama", 200, 1, 1),
                 ("neznama-1", "Neznámá skladba", None, "", 100, 1, 1), ("s3", "Tři", "al2", "Beta", 20, 1, 1)],
      f"tracks; an unknown one under its name ({tracks})")
check(len(stats.music_tracks(r, limit=2)) == 2, "the chart's limit")
genres = [(z["genre"], z["seconds"], z["plays"]) for z in stats.music_genres(r)]
check(genres == [("Rock", 560, 2), ("Pop", 260, 1), ("Jazz", 20, 1)], f"genres: a track with several genres counts to each ({genres})")

print()
print("--- F) technique ---")
method = stats.music_play_method(r)
check((method["direct"], method["transcoded"], method["reasons"]) == (580, 300, [{"reason": "AudioCodecNotSupported", "count": 1}]),
      f"direct × transcoded (seconds) and the reasons ({method})")
apps = [(a["app"], a["seconds"], a["plays"]) for a in stats.music_apps(r)]
check(apps == [("Finamp", 360, 2), ("Jellyfin Web", 320, 2), ("Feishin", 200, 1)], f"apps ({apps})")
network = stats.music_network(r)
check(network == {"byte_count": 44_770_000, "without_bitrate": 1},
      f"data over the network: the playback bitrate, otherwise the file's bitrate; listening without a bitrate is only counted ({network})")

print()
print("--- G) the start of the listening history ---")
check(stats.music_first() == LISTENING[6][6], f"the first listening ({stats.music_first()})")
whole = stats.music_analysis(stats.whole_history_period(stats.music_first()))
check(stats.music_numbers(whole)["seconds"] == 880 + 240, "the whole listening history reaches the first listening")
empty = stats.music_analysis(stats.period_between("2020-01-01", "2020-01-02"))
check(stats.music_numbers(empty) == {"seconds": 0, "plays": 0, "listener_count": 0, "artist_count": 0, "track_count": 0}
      and stats.music_artists(empty) == [] and stats.music_network(empty) == {"byte_count": 0, "without_bitrate": 0},
      "a period without listening gives zeros and empty lists")

# Review of 15. 11. 2026: a tie in the chart was to be sorted by the name, but it was sorted by the first
# listening (at that moment the name was still under the key "interpret" / "album").
def _listen(interpret_id: str, interpret: str, album_id: str, album: str) -> dict:
    return {"artist_id": interpret_id, "artist": interpret, "album_id": album_id, "album": album, "album_artist": interpret,
            "album_artist_id": interpret_id, "cover_tag": None, "watched_seconds": 100, "plays": True, "user_id": "u", "user_name": "U"}


tie = {"rows": [_listen("iz", "Zeta", "az", "Zima"), _listen("ia", "Alfa", "aa", "Astra")]}
check([i["name"] for i in stats.music_artists(tie)] == ["Alfa", "Zeta"] and [a["name"] for a in stats.music_albums(tie)] == ["Astra", "Zima"],
      "a tie in the artist and album charts is sorted by the name, not by the first listening")

print()
print("--- speed and memory of a long period ---")
# Final review (15. 11. 2026): the Music page for a year or the whole history took 5-15 s with 300 000
# listens and the analysis took over 800 MB. Half of the time went to the cards by days and by hours,
# which converted every listen's time to the local zone again; the memory went to the rows, each of
# which carried its own copy of the same texts (the name, the app, the artist, the album, the genres).
import tracemalloc  # noqa: E402

from jellyscope.stats import music_listening as listening  # noqa: E402

LISTENS = 20000
now = datetime.now(UTC)
with db.connect() as conn:
    conn.executemany(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name, play_method,"
        " started_at, last_seen_at, watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, ?, ?, ?, ?, 'Audio', ?, ?,"
        " 'DirectPlay', ?, ?, 200, 0, 0, 0)",
        [(f"hromadne-{i}", f"u{i % 5}", f"Posluchač {i % 5}", f"sk{i % 300}", f"Skladba {i % 300}", "Finamp", "Telefon",
          (now - timedelta(minutes=37 * i)).strftime("%Y-%m-%d %H:%M:%S"), (now - timedelta(minutes=37 * i)).strftime("%Y-%m-%d %H:%M:%S"))
         for i in range(LISTENS)])
    conn.commit()
tracemalloc.start()
big = stats.music_analysis(stats.whole_history_period(first=stats.music_first()))
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
per_listen = peak / max(1, len(big["rows"]))
check(len(big["rows"]) >= LISTENS and per_listen < 1500,
      f"the analysis of a long period: memory per listen ({per_listen:.0f} B, {len(big['rows'])} listens)")
conversions = {"n": 0}
from jellyscope.stats import shared as stats_shared  # noqa: E402

_original_local = stats_shared._local


def _count(text):
    conversions["n"] += 1
    return _original_local(text)


stats_shared._local = _count
try:
    by_day, grid = stats.music_by_day(big), stats.music_heatmap(big)
finally:
    stats_shared._local = _original_local
check(conversions["n"] == 0 and sum(d["seconds"] for d in by_day) == sum(x["watched_seconds"] for x in big["rows"])
      and abs(sum(map(sum, grid)) * 3600 - sum(x["watched_seconds"] for x in big["rows"])) < 1,
      f"the cards by days and by hours do not convert the time again - the analysis row carries the local time ({conversions['n']} conversions)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
