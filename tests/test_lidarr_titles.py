# -*- coding: utf-8 -*-
r"""Lidarr's albums and their counterparts in the music library (links from Lidarr's cards).

Lidarr knows an album by its own id and name; the history Jellyscope fetches has nothing to find it
by in the library. So the sync also fetches Lidarr's list of albums and artists (the table
`lidarr_title`, one row per album) and pairs it with the music library Jellyscope already has
(`music_album`, `music_artist`). Guarded:

A) the list of albums is stored (the ids from the app as text, the artist's name from the list of
   artists, the MusicBrainz ids of the releases joined by a bar, a broken record is skipped);
B) an album pairs by a MusicBrainz release id of the library (any of the album's releases), else by
   its name and its artist's name (case and diacritics do not matter); an artist by the MusicBrainz
   artist id, else by its name; an album outside the library has no counterpart;
C) a library without music, an album that went missing from the library, an album of the same name
   by another artist: no pairing, no error; the pairing is recomputed on every sync;
D) the list is fetched the first time, after a day, after an upgrade and when the history brings an
   unknown album - not on every sync; a failed list brings neither the history sync nor the stored
   albums down;
E) deleting the downloaded data deletes the albums too;
F) odd ids (digits outside ASCII, a MusicBrainz id that is no UUID).

Lidarr does not run: the answers are built by hand in the shape of the API v1 (tests/lidarr_responses/).

Run:
    .venv/bin/python tests/test_lidarr_titles.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lidarr-titles.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import LIDARR_RESPONSES, connect_lidarr, fill_lidarr, lidarr_client, lidarr_handler, no_network, recorded_response  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import lidarr  # noqa: E402

failures = 0
db.init_db()
no_network()      # no app other than Lidarr is to be asked here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def make_stale() -> None:
    """The list of albums is "old" (fetched long ago) - the next sync fetches it again."""
    db.set_setting("arr_lidarr_titles", "2026-01-01 00:00:00")
    db.forget_settings()


def titles() -> dict[int, dict]:
    return {r["title_id"]: dict(r) for r in db.query_all("SELECT * FROM lidarr_title ORDER BY title_id")}


ALBUMS = recorded_response(LIDARR_RESPONSES, "album")      # 11 and 12 by the first artist, 21 and 22 by the second
ARTISTS = recorded_response(LIDARR_RESPONSES, "artist")
RELEASES_11 = [r["foreignReleaseId"] for r in ALBUMS[0]["releases"]]
KRYSTOF_MB = ARTISTS[0]["foreignArtistId"]
NOW = db.utcnow()


def library(albums: list[tuple], artists: list[tuple]) -> None:
    """The music library: albums (id, name, artist id, artist, musicbrainz id, missing), artists (id, name, musicbrainz id)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM music_album")
        conn.execute("DELETE FROM music_artist")
        for item_id, name, artist_id, artist, mb, missing in albums:
            conn.execute("INSERT INTO music_album (id, name, artist_id, artist, musicbrainz_id, missing, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (item_id, name, artist_id, artist, mb, missing, NOW))
        for item_id, name, mb in artists:
            conn.execute("INSERT INTO music_artist (id, name, musicbrainz_id, synced_at, missing) VALUES (?, ?, ?, ?, 0)", (item_id, name, mb, NOW))
        conn.commit()


requests: list[str] = []
handlers = {"lidarr": lidarr_handler(requests)}
substitute(lidarr, "LidarrClient", lidarr_client(lambda request: handlers["lidarr"](request)))

print("--- A) the list of albums is stored on a sync ---")
connect_lidarr()
fill_lidarr()
rows = titles()
check(len(rows) == len(ALBUMS) == 4, f"every album of Lidarr ({len(rows)})")
check(rows[11]["name"] == "Hvězdy" and rows[11]["artist"] == "Kryštof" and rows[11]["artist_id"] == 1,
      f"an album: the name, the artist's name from the list of artists and its id ({rows[11]})")
check(rows[11]["mb_release_group"] == ALBUMS[0]["foreignAlbumId"] and rows[11]["mb_releases"] == "|".join(RELEASES_11) and rows[11]["mb_artist"] == KRYSTOF_MB,
      "the MusicBrainz ids: the release group, the releases joined by a bar, the artist")
check(rows[11]["track_count"] == 3 and rows[11]["released_at"] == "2019-05-17 00:00:00", f"the track count and the release date in UTC ({rows[11]['track_count']}, {rows[11]['released_at']})")
check(requests.count("album") == 1 and requests.count("artist") == 1, f"one request for the list of albums and one for the artists ({requests})")
check(all(r["synced_at"] for r in rows.values()), "the time of the sync is stored")
check(rows[11]["added_at"] == rows[12]["added_at"] == "2026-11-12 20:50:07" and rows[21]["added_at"] == "2026-11-12 20:51:30",
      f"every album carries the time its artist was added to Lidarr ({rows[11]['added_at']}, {rows[21]['added_at']})")
check(lidarr.title_rows([{"id": 9, "artistId": 1}], [{"id": 1, "added": "0001-01-01T00:00:00Z"}])[0]["added_at"] is None
      and lidarr.title_rows([{"id": 9, "artistId": 1}])[0]["added_at"] is None, "an empty .NET date or no artist: the time is unknown (None)")
parsed = lidarr.title_rows(
    [{"id": 5, "title": "A", "artistId": 7, "foreignAlbumId": "B1111111-0000-4000-8000-000000000005", "releaseDate": "2020-01-02T00:00:00Z",
      "releases": [{"foreignReleaseId": "C1111111-0000-4000-8000-000000000001"}, {"foreignReleaseId": "nesmysl"}, {"x": 1}, "x"],
      "statistics": {"trackCount": 9}, "artist": {"artistName": "Embedded", "foreignArtistId": KRYSTOF_MB}},
     {"id": "x", "title": "B"}, "nesmysl", {"title": "bez id"}, {"id": 6, "title": None, "statistics": None, "releases": None, "releaseDate": "0001-01-01T00:00:00Z"}],
    [{"id": 7, "artistName": "From list", "foreignArtistId": KRYSTOF_MB}])
check([(r["title_id"], r["name"], r["artist"], r["mb_releases"], r["track_count"], r["released_at"]) for r in parsed]
      == [(5, "A", "From list", "c1111111-0000-4000-8000-000000000001", 9, "2020-01-02 00:00:00"), (6, "", "", "", 0, None)],
      f"a broken record is skipped, a MusicBrainz id is lowercase, a release that is no id is dropped, the list of artists wins over the embedded artist ({parsed})")
check(lidarr.title_rows([{"id": 8, "title": "E", "artist": {"artistName": "Embedded", "foreignArtistId": KRYSTOF_MB}}])[0]["artist"] == "Embedded",
      "without the list of artists the artist embedded in the album is used")

print()
print("--- B) an album and an artist pair with the library ---")
library(
    albums=[("jf-stars", "Stars (a different title in the library)", "jf-kryst", "Kryštof (CZ)", RELEASES_11[1], 0),     # by the 2nd release's MusicBrainz id
            ("jf-sipkova", "SIPKOVA RUZENA", "jf-kryst", "KRYSTOF", "", 0),                                              # by the name, without case and diacritics
            ("jf-lining", "Silver Lining", "jf-other", "Other Band", "", 0),                                            # the same name, another artist
            ("jf-blue", "Blue Hour", "jf-nq", "Nightingale Quartet", "", 1)],                                           # the right name but gone from the library
    artists=[("jf-kryst", "Kryštof (CZ)", KRYSTOF_MB), ("jf-nq", "NIGHTINGALE quartet", ""), ("jf-other", "Other Band", "")])
asyncio.run(lidarr.synchronize())
rows = titles()
check(rows[11]["jellyfin_id"] == "jf-stars", f"an album by a MusicBrainz id of any of its releases, though its name differs ({rows[11]['jellyfin_id']})")
check(rows[12]["jellyfin_id"] == "jf-sipkova", f"an album without a shared id by its name and its artist, case and diacritics do not matter ({rows[12]['jellyfin_id']})")
check(rows[21]["jellyfin_id"] == "", "an album of the same name by another artist is not its counterpart")
check(rows[22]["jellyfin_id"] == "", "an album that went missing from the library is not paired")
check(rows[11]["jellyfin_artist_id"] == "jf-kryst" and rows[12]["jellyfin_artist_id"] == "jf-kryst",
      "an artist by the MusicBrainz artist id, though the names differ")
check(rows[21]["jellyfin_artist_id"] == "jf-nq" and rows[22]["jellyfin_artist_id"] == "jf-nq", "an artist without an id by its name (case does not matter)")
check(lidarr.titles._folded("Kryštof") == lidarr.titles._folded("KRYSTOF") == "krystof", "the names are compared without case and diacritics")
# An album with a wrong MusicBrainz id in the library but the right name and artist still pairs by the name.
library(albums=[("jf-x", "Hvezdy", "jf-kryst", "Krystof", "ffffffff-0000-4000-8000-000000000000", 0)], artists=[("jf-kryst", "Krystof", "")])
asyncio.run(lidarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-x" and titles()[11]["jellyfin_artist_id"] == "jf-kryst",
      "an id that matches nothing does not stop the pairing by the name")
# The id wins over the name: two albums, one with the shared id and one of the same name.
library(albums=[("jf-name", "Hvezdy", "jf-kryst", "Krystof", "", 0), ("jf-id", "Elsewhere", "jf-kryst", "Krystof", RELEASES_11[0].upper(), 0)],
        artists=[("jf-kryst", "Krystof", "")])
asyncio.run(lidarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-id", "a shared MusicBrainz id wins over a name match (the id is compared without case)")

print()
print("--- C) no music, an album that left the library ---")
library([], [])
result = asyncio.run(lidarr.synchronize())
check("new_count" in result and all(r["jellyfin_id"] == "" and r["jellyfin_artist_id"] == "" for r in titles().values()) and len(titles()) == 4,
      "a library without music: no pairing, no error, the albums stay")
library(albums=[("jf-stars", "Stars", "jf-kryst", "Kryštof", RELEASES_11[0], 0)], artists=[("jf-kryst", "Kryštof", KRYSTOF_MB)])
asyncio.run(lidarr.synchronize())
check(titles()[11]["jellyfin_id"] == "jf-stars", "the album comes into the library: paired on the next sync")
with db.connect() as conn:
    conn.execute("UPDATE music_album SET missing = 1")
    conn.commit()
asyncio.run(lidarr.synchronize())
check(titles()[11]["jellyfin_id"] == "" and titles()[11]["jellyfin_artist_id"] == "jf-kryst", "the album left the library: it loses its pairing (a link would lead nowhere), the artist is still there")
library([], [])
asyncio.run(lidarr.synchronize())
check(titles()[11]["jellyfin_artist_id"] == "", "an artist that left loses its pairing too")

print()
print("--- D) when the list is fetched ---")
library(albums=[("jf-stars", "Stars", "jf-kryst", "Kryštof", RELEASES_11[0], 0)], artists=[("jf-kryst", "Kryštof", KRYSTOF_MB)])
before = requests.count("album")
asyncio.run(lidarr.synchronize())
asyncio.run(lidarr.synchronize())
check(requests.count("album") == before, f"further syncs the same day do not fetch the list ({requests.count('album') - before}x)")
check(titles()[11]["jellyfin_id"] == "jf-stars", "but the albums still pair with the library on every sync")
with db.connect() as conn:      # the history brings an album Jellyscope does not know
    conn.execute("DELETE FROM lidarr_title WHERE title_id = 22")
    conn.commit()
asyncio.run(lidarr.synchronize())
check(requests.count("album") == before + 1 and 22 in titles(), "an unknown album in the history: the list is fetched at once")
make_stale()
asyncio.run(lidarr.synchronize())
check(requests.count("album") == before + 2, "after a day the list is refreshed")
db.set_setting("arr_lidarr_titles_version", "")
db.forget_settings()
asyncio.run(lidarr.synchronize())
check(requests.count("album") == before + 3 and db.get_setting("arr_lidarr_titles_version", "") != "", "a list fetched by an older version is fetched once again")
check("arr_lidarr_titles" in db.UNLOGGED_SETTINGS and "arr_lidarr_titles_version" in db.UNLOGGED_SETTINGS, "the list's marks do not flood the settings log")

handlers["lidarr"] = lidarr_handler(requests, instead={"album": httpx.Response(500, text="error")})
make_stale()
before = requests.count("album")
result = asyncio.run(lidarr.synchronize())
check("new_count" in result and lidarr.sync_state()["result"] == "ok" and len(titles()) == 4 and requests.count("album") == before + 1,
      "a failed list of albums does not bring the history sync down and leaves the stored albums")
asyncio.run(lidarr.synchronize())
check(requests.count("album") == before + 2, "after a failed fetch the list is tried at the very next sync")
handlers["lidarr"] = lidarr_handler(requests, instead={"artist": httpx.Response(500, text="error")})
make_stale()
asyncio.run(lidarr.synchronize())
check(len(titles()) == 4 and lidarr.sync_state()["result"] == "ok", "a failed list of artists changes nothing either")
handlers["lidarr"] = lidarr_handler(requests, instead={"album": httpx.Response(200, json=ALBUMS[:1])})
make_stale()
asyncio.run(lidarr.synchronize())
check(len(titles()) == 4, "an album the app no longer has stays in the table (the history still points at it)")
handlers["lidarr"] = lidarr_handler(requests)
original = lidarr.titles._pair_albums
lidarr.titles._pair_albums = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected"))      # type: ignore[assignment]
try:
    result = asyncio.run(lidarr.synchronize())
except Exception as error:      # noqa: BLE001
    result = {"exception": repr(error)}
finally:
    lidarr.titles._pair_albums = original
db.forget_settings()
check("new_count" in result and lidarr.sync_state()["result"] == "ok", f"an unexpected error in the title step does not bring the history sync down ({result})")

print()
print("--- E) deleting the downloaded data ---")
check(lidarr.delete_data() > 0 and titles() == {} and (db.forget_settings() or True)
      and db.get_setting("arr_lidarr_titles", "") == "" and db.get_setting("arr_lidarr_titles_version", "") == "",
      "deleting Lidarr's data deletes its albums and the list's fetch mark")
check(db.query_value("SELECT COUNT(*) FROM music_album") == 1, "the music library itself is not touched")

print()
print("--- F) odd ids ---")
try:
    odd: object = lidarr.title_rows([{"id": "²", "title": "x"}, {"id": 1, "title": "Divný", "artistId": "²", "foreignAlbumId": "٣" * 36,
                                                                        "statistics": {"trackCount": "²"}, "releases": [{"foreignReleaseId": "٣" * 36}]},
                                     {"id": 2, "title": "Dobrý", "foreignAlbumId": "not-a-uuid"}])
except ValueError as error:
    odd = f"exception {error}"
check(isinstance(odd, list) and [(r["title_id"], r["artist_id"], r["mb_release_group"], r["mb_releases"], r["track_count"]) for r in odd]
      == [(1, None, "", "", 0), (2, None, "", "", 0)], f"ids that are no ids are stored empty and the list does not crash ({odd})")
try:
    source: object = lidarr.sync._source("²")
except ValueError as error:
    source = f"exception {error}"
check(source == "²", f"an event's source of a digit outside ASCII stays a text ({source})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
