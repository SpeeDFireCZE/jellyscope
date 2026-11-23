# -*- coding: utf-8 -*-
r"""The music library in the Library: an album grid and an album page built like a series.

Spec: docs/superpowers/specs/2026-11-16-menu-sections-design.md, "Knihovna podle Jellyfinu".

* /library/<id of a music library> is a grid of albums with covers (like the
  series of a video library), searchable by album or artist - case and
  diacritics do not matter, % and _ are plain characters -, sorted by added,
  name, artist, year or size, 60 to a page.
* /album/<id> belongs to the Library: tiles (tracks, discs, length, size,
  plays over the whole history), "About the album", tracks by disc with
  their format and bitrate, the artist as a link to their page in Music,
  "Who listened" only with "...including who listened". An unknown id is
  404, a vanished album keeps its page with a sentence.

Run:
    .venv/bin/python tests/test_music_library.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "music-library.db")
os.environ["SECRET_KEY"] = "test-key-music-library"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login  # noqa: E402
from _package import substitute  # noqa: E402
from _music import LISTENERS, fake_jellyfin, fill_music  # noqa: E402
from jellyscope import access, accounts, db, scanner, stats  # noqa: E402
import importlib  # noqa: E402

# The module itself: `stats.music_library` (it used to share its name with a function at the package door).
library_module = importlib.import_module("jellyscope.stats.music_library")
from jellyscope.stats.music_library import album_detail, library_albums, library_listening, library_overview  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


asyncio.run(scanner._sync_libraries(fake_jellyfin(version="10")()))     # the Music library of Jellyfin 10.11
fill_music()                                                               # its albums, tracks and listening
LIBRARY = db.query_value("SELECT id FROM libraries WHERE collection_type = 'music'", default="")
ALBUMS = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM music_album")}
AURORA = ALBUMS["Aurora Sessions"]
ARTIST = db.query_value("SELECT artist_id FROM music_album WHERE id = ?", (AURORA,))
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin = login("admin")


def names(result: dict) -> list[str]:
    return [album["name"] for album in result["albums"]]


print("--- the album grid ---")
check(bool(LIBRARY) and AURORA, "the recorded music library and its albums are in the database")
page = admin.get(f"/library/{LIBRARY}?tab=albums")
check(page.status_code == 200 and f'href="/album/{AURORA}"' in page.text and f"/image/{AURORA}?kind=Primary" in page.text,
      f"/library/<music> is a grid of albums with covers leading to /album/<id> ({page.status_code})")
for search in ("aurora", "AURORA", "Sessions"):
    check(names(library_albums(LIBRARY, search=search)) == ["Aurora Sessions"], f"search {search!r} finds Aurora Sessions")
check(names(library_albums(LIBRARY, search="okrajove")) == ["Okrajové případy"], "search without diacritics finds Okrajové případy")
artist_name = db.query_value("SELECT artist FROM music_album WHERE id = ?", (AURORA,))
check("Aurora Sessions" in names(library_albums(LIBRARY, search=artist_name.upper())), "search by the artist finds their albums")
check(library_albums(LIBRARY, search="%")["total"] == 0 and library_albums(LIBRARY, search="_")["total"] == 0,
      "% and _ are plain characters, not wildcards")
check(f'href="/album/{AURORA}"' in admin.get(f"/library/{LIBRARY}?tab=albums&search=AURORA").text, "the page searches too")
by_year = [album["year"] for album in library_albums(LIBRARY, sort="year")["albums"]]
known = [year for year in by_year if year]
check(known == sorted(known, reverse=True) and by_year[:len(known)] == known, f"sorted by year, newest first, unknown last ({by_year})")
by_size = [album["size_bytes"] for album in library_albums(LIBRARY, sort="size")["albums"]]
check(by_size == sorted(by_size, reverse=True), f"sorted by size, largest first ({by_size})")
check(names(library_albums(LIBRARY, sort="nonsense")) == names(library_albums(LIBRARY, sort="added")), "an unknown sort is by added")

print()
print("--- the album page ---")
detail = album_detail(AURORA)
r = admin.get(f"/album/{AURORA}")
html = r.text
check(r.status_code == 200 and all(label in html for label in ("Skladeb", "Disků", "Délka", "Velikost", "Přehrání")),
      "tiles: tracks, discs, length, size, plays")
check(detail is not None and detail["plays"] > 0 and detail["plays"] == sum(t["plays"] for t in detail["tracks"]),
      f"plays over the whole history, the sum of its tracks ({detail and detail['plays']})")
check("O albu" in html and "Music" in html and "FLAC" in html, "About the album: library, formats")
check("kb/s" in html and f'href="/music/artist/{ARTIST}"' in html, "tracks with their bitrate; the artist leads to Music")
check(any(name in html for name in LISTENERS.values()) and "Kdo poslouchal" in html, "the administrator sees who listened")
check("days=" not in re.sub(r'href="/(?:music|album)[^"]*"', "", html.split('<div class="kpi-row">')[0]),
      "no period switch: the album page is about the album, not a period")
defaults = [o.key for o in access.AREAS if o.default]
reader = accounts.get_by_name("reader")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()
reader_page = login("reader").get(f"/album/{AURORA}").text
check("Kdo poslouchal" not in reader_page and not any(name in reader_page for name in LISTENERS.values()),
      "without ...including who listened: no listeners")
accounts.save_permissions(reader["id"], access.pack_permissions([a for a in defaults if a != "library"], False))
db.forget_settings()
check(login("reader").get(f"/album/{AURORA}", follow_redirects=False).status_code == 403, "without the Library: 403")
check(admin.get("/album/neni-takove").status_code == 404, "an unknown id is 404")
with db.connect() as conn:
    conn.execute("UPDATE music_album SET missing = 1 WHERE id = ?", (AURORA,))
vanished = admin.get(f"/album/{AURORA}")
check(vanished.status_code == 200 and "V Jellyfinu už není" in vanished.text, "a vanished album keeps its page with a sentence")
check("Aurora Sessions" not in names(library_albums(LIBRARY)), "and is not in the grid")
with db.connect() as conn:
    conn.execute("UPDATE music_album SET missing = 0 WHERE id = ?", (AURORA,))

print()
print("--- tabs of a music library: Overview, Albums, Activity (like a video library) ---")
# A second music library with one album, one track and one listen: it must not count in the first.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, missing_runs) VALUES ('lib-m2', 'Druhá hudba', 'music', '[]', 0)")
    conn.execute("INSERT INTO music_album (id, name, artist, library_id, year) VALUES ('m2-album', 'Cizí album', 'Cizinec', 'lib-m2', 1985)")
    conn.execute("INSERT INTO music_track (id, name, album_id, artist, codec, bitrate, size_bytes, runtime_ticks, library_id)"
                 " VALUES ('m2-track', 'Cizí skladba', 'm2-album', 'Cizinec', 'mp3', 320000, 9000000, 2000000000, 'lib-m2')")
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                 " watched_seconds, paused_seconds, is_paused, is_active) VALUES ('m2', 'u-cizi', 'Cizí posluchač', 'm2-track',"
                 " 'Cizí skladba', 'Audio', ?, ?, 120, 0, 0, 0)", (db.utcnow(), db.utcnow()))
own = {
    "albums": db.query_value("SELECT COUNT(*) FROM music_album WHERE missing = 0 AND library_id = ?", (LIBRARY,)),
    "tracks": db.query_value("SELECT COUNT(*) FROM music_track WHERE missing = 0 AND library_id = ?", (LIBRARY,)),
}
overview = library_overview(LIBRARY)
check(overview["numbers"]["album_count"] == own["albums"] and overview["numbers"]["track_count"] == own["tracks"],
      f"the overview counts only this library ({overview['numbers']}, expected {own})")
check(library_overview()["numbers"]["album_count"] == own["albums"] + 1, "without a library it counts every music library")
check(sum(f["track_count"] for f in overview["music_formats"]["rows"]) == own["tracks"],
      "formats count the tracks of this library only")
page = admin.get(f"/library/{LIBRARY}").text
tabs = re.findall(rf'href="/library/{LIBRARY}\?tab=(\w+)"', page)
check(tabs[:3] == ["overview", "albums", "activity"] and 'class="active"' in page,
      f"three tabs, Overview open by default ({tabs[:3]})")
check(all(f"<!-- card: {key} -->" in page for key in ("music_formats", "music_quality", "music_library_genres", "music_years",
                                                     "music_largest")),
      "the overview has formats, sound quality, genres, decades and the largest albums")
check("Cizí album" not in page.split("<!-- card: music_largest -->")[1].split("<!-- card:")[0],
      "the largest albums of this library only")
check("Cizinec" not in page, "nothing of the other library on its overview")
albums_tab = admin.get(f"/library/{LIBRARY}?tab=albums").text
check(f'href="/album/{AURORA}"' in albums_tab and "Cizí album" not in albums_tab, "the Albums tab is the grid")
listening = library_listening(LIBRARY, 365)
all_rows = stats.music_analysis(365)["rows"]
expected_plays = sum(1 for row in all_rows if row["plays"] and row["item_id"] != "m2-track")
check(listening["totals"]["plays"] == expected_plays and listening["totals"]["plays"] > 0,
      f"activity: plays of this library only ({listening['totals']}, expected {expected_plays})")
activity = admin.get(f"/library/{LIBRARY}?tab=activity&days=365").text
check(all(label in activity for label in ("Poslech", "Přehrání", "Posluchačů", "Různých alb", "Nejposlouchanější alba"))
      and "Aurora Sessions" in activity, "activity: tiles and the most played albums")
check("Poslední poslech" in activity and any(name in activity for name in LISTENERS.values())
      and "Cizí posluchač" not in activity and f'href="/album/{AURORA}#track-' in activity,
      "the administrator sees listeners and the last listens, a track leads to its album row")
check(f'href="/library/{LIBRARY}?tab=activity&amp;days=7"' in activity, "the period switch keeps the tab")
whole = admin.get(f"/library/{LIBRARY}?tab=activity&days=0").text
check("Co se z knihovny poslouchalo za celou historii." in whole, "the whole history says so")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()
reader_activity = login("reader").get(f"/library/{LIBRARY}?tab=activity&days=365").text
check("Přehrání" in reader_activity and "Poslední poslech" not in reader_activity
      and not any(name in reader_activity for name in LISTENERS.values()),
      "without ...including who listened: numbers and albums, no names")
empty = admin.get("/library/lib-m2?tab=activity&days=7")
check(empty.status_code == 200 and admin.get("/library/lib-m2").status_code == 200, "a small library has its tabs too")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type, paths, missing_runs) VALUES ('lib-m3', 'Prázdná', 'music', '[]', 0)")
check(all(admin.get(f"/library/lib-m3?tab={tab}").status_code == 200 for tab in ("overview", "albums", "activity")),
      "an empty music library: every tab opens")
# Back from an album to the grid it came from, not to the Overview (code review, 16. 11. 2026).
check(f'href="/library/{LIBRARY}?tab=albums"' in admin.get(f"/album/{AURORA}").text, "an album leads back to the album grid")
# Genres are capped like on the Music page (the rest in the window).
with db.connect() as conn:
    conn.execute("INSERT INTO music_track (id, name, album_id, artist, codec, size_bytes, runtime_ticks, library_id, genres)"
                 " VALUES ('m2-genres', 'Mnoho žánrů', 'm2-album', 'Cizinec', 'flac', 1000, 10000000, 'lib-m2', ?)",
                 ("|".join(f"Žánr {n:03d}" for n in range(150)),))
genres = re.findall(r"Žánr \d{3}", admin.get("/library/lib-m2").text)
check(0 < len(set(genres)) <= 100, f"genres on the Overview are capped like on the Music page ({len(set(genres))})")
# Artists: those of the tracks and of the albums, the same rule for one library and for all.
with db.connect() as conn:
    conn.execute("INSERT INTO music_artist (id, name) VALUES ('bez-skladeb', 'Interpret bez skladeb')")
artist_ids = {r["artist_id"] for r in db.query_all(
    "SELECT artist_id FROM music_track WHERE missing = 0 AND artist_id IS NOT NULL"
    " UNION SELECT artist_id FROM music_album WHERE missing = 0 AND artist_id IS NOT NULL")}
check(library_overview()["numbers"]["artist_count"] == len(artist_ids),
      f"every music library: artists of tracks and albums ({library_overview()['numbers']['artist_count']}, {len(artist_ids)})")

# Music statistics (formats, quality, listening) belong to the Music area ("…the music library
# and how it is played"); the album grid and album pages to the Library (the spec).
accounts.save_permissions(reader["id"], access.pack_permissions([a for a in defaults if a != "music"], False))
db.forget_settings()
reader_client = login("reader")
reader_page = reader_client.get(f"/library/{LIBRARY}").text
reader_tabs = re.findall(rf'href="/library/{LIBRARY}\?tab=(\w+)"', reader_page)
check(reader_tabs == ["albums"] and f'href="/album/{AURORA}"' in reader_page and "<!-- card: hudba_" not in reader_page,
      f"without Music: only the Albums tab, no music cards ({reader_tabs})")
check("Přehrání" not in reader_client.get(f"/library/{LIBRARY}?tab=activity&days=365").text,
      "and no Activity even through the address")
check('id="music-in-library"' not in reader_client.get("/library").text, "nor the Music section of the Library page")
accounts.save_permissions(reader["id"], access.pack_permissions(defaults, False))
db.forget_settings()

print()
print("--- the Library page: music next to the video statistics ---")
index = admin.get("/library").text
library_section = index.split('id="music-in-library"')[1] if 'id="music-in-library"' in index else ""
check(all(f"<!-- card: {key} -->" in library_section for key in ("music_formats", "music_quality", "music_years",
                                                                "music_library_genres", "music_largest")),
      "the Library page's Music section has the library's cards of Music (moved from its overview, 22. 11. 2026)")
summary = library_overview()
check(f">{summary['numbers']['album_count']}<" in index.split('id="music-in-library"')[1], "with the counts of every music library")
calls = []
original_decades = library_module.music_decades


def counted_decades(*args, **kwargs):
    calls.append(1)
    return original_decades(*args, **kwargs)


restore_decades = substitute(stats, "music_decades", counted_decades)
try:
    admin.get("/library")
finally:
    restore_decades()
check(len(calls) == 1, f"the Library page computes its cards - the decades once (one of its cards since 22. 11. 2026, {len(calls)}×)")

print()
print("--- 60 albums to a page ---")
with db.connect() as conn:
    for n in range(130):
        conn.execute("INSERT INTO music_album (id, name, artist, library_id, added_at) VALUES (?, ?, 'Plnič', ?, ?)",
                     (f"plnic-{n:03d}", f"Plnicí album {n:03d}", LIBRARY, f"2020-01-01 00:{n // 60:02d}:{n % 60:02d}"))
total = library_albums(LIBRARY)["total"]
last = library_albums(LIBRARY, page=3)
check(total == 130 + len(ALBUMS) and len(library_albums(LIBRARY, page=1)["albums"]) == 60
      and len(last["albums"]) == total - 120, f"three pages of 60 ({total} albums, last page {len(last['albums'])})")
check("Strana 3 z 3" in admin.get(f"/library/{LIBRARY}?tab=albums&page=3").text, "the page has a pager")
with db.connect() as conn:
    for n in range(130, 450):
        conn.execute("INSERT INTO music_album (id, name, artist, library_id, added_at) VALUES (?, ?, 'Plnič', ?, ?)",
                     (f"plnic-{n:03d}", f"Plnicí album {n:03d}", LIBRARY, f"2020-01-01 01:{n // 60:02d}:{n % 60:02d}"))
# One page of the grid sums the tracks of its own 60 albums, not of the whole library; sorting
# by size needs every album, in one query (deferred minors of 16. 11. 2026, fixed the same day).
queries: list[tuple[str, int]] = []


def recording(sql: str, params=()):
    queries.append((sql, len(params)))
    return original_query_all(sql, params)


original_query_all = db.query_all
restore = substitute(db, "query_all", recording)
try:
    by_added = library_albums(LIBRARY, page=2)
    over_tracks = [(sql, n) for sql, n in queries if "music_track" in sql]
    queries.clear()
    by_size = library_albums(LIBRARY, sort="size")
    size_queries = [sql for sql, _ in queries if "music_track" in sql]
finally:
    restore()
check(bool(over_tracks) and all(" IN (" in sql and n <= 60 for sql, n in over_tracks),
      f"a page sums the tracks of its own albums only ({[(n, sql[:40]) for sql, n in over_tracks]})")
check(len(by_added["albums"]) == 60 and all("track_count" in a and "size_bytes" in a for a in by_added["albums"]),
      "and every album of the page has its track count and size")
check(len(size_queries) == 1 and by_size["total"] > 400,
      f"sorting by size sums every album of the library in one query ({len(size_queries)} for {by_size['total']} albums)")
sizes = [album["size_bytes"] for album in by_size["albums"]]
check(sizes == sorted(sizes, reverse=True) and sizes[0] > 0, "and the largest come first")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
