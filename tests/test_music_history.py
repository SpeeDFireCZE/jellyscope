# -*- coding: utf-8 -*-
r"""Music in History, on a viewer's page and in the sample data.

Watched:

A) History: a track's row is "Artist – Track" with a link to the track's row
   on the album's page; a track without an album and an item that neither the library
   nor the music knows are text without a link (they used to lead to /item/, which gave
   404); movies still lead to /item/; an account without the Music area has the track as
   text;
B) a viewer's page: the row "Poslech hudby" with the time, the plays
   and the most listened artist - only for an account with the Music area and only
   for a viewer who listened to something;
C) the sample data: after seed() there is music, the page /music/overview gives 200 and every
   music card has content; wipe() deletes it too.

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md

Run:
    .venv/bin/python tests/test_music_history.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hudba-historie.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import tile, card_html, login  # noqa: E402
from _music import fill_music  # noqa: E402
from jellyscope import access, accounts, db, demodata, formatting, cards_music, stats  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
DEFAULTS = [o.key for o in access.AREAS if o.default]


def reader_rights(areas: list[str] | None) -> None:
    accounts.save_permissions(reader_account["id"], None if areas is None else access.pack_permissions(areas, False))
    db.forget_settings()


tracks = fill_music()
albums = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM music_album")}
AURORA = albums["Aurora Sessions"]
when = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u-tomas', 'Tomáš Filmový')")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id) VALUES ('film-1', 'FilmVKnihovneQX', 'Movie', 'filmy')")
    conn.execute("INSERT INTO music_track (id, name, runtime_ticks) VALUES ('sama', 'SkladbaBezAlbaQX', 600000000)")
    for key, uid, item_id, name, kind in (("h-film", "u-tomas", "film-1", "FilmVKnihovneQX", "Movie"),
                                          ("h-nezname", "u-tomas", "nezname-1", "NeznamyTitulQX", "Movie"),
                                          ("h-sama", "u-eva", "sama", "SkladbaBezAlbaQX", "Audio")):
        conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                     " watched_seconds, paused_seconds, is_paused, is_active) VALUES (?, ?, 'x', ?, ?, ?, ?, ?, 120, 0, 0, 0)",
                     (key, uid, item_id, name, kind, when, when))
    conn.commit()
admin = login("admin")

print("--- A) History ---")
h = admin.get("/history?kind=all").text
check(f'href="/album/{AURORA}#track-{tracks["First Light"]}"' in h
      and re.search(r"Northern Lights Ensemble\s*&ndash;</span>\s*First Light", h) is not None,
      "a track: “Artist – Track” with a link to its row of the album")
check("SkladbaBezAlbaQX" in h and "skladba-sama" not in h and 'href="/item/sama"' not in h,
      "a track without an album is text without a link")
check("NeznamyTitulQX" in h and 'href="/item/nezname-1"' not in h,
      "an item that neither the library nor the music knows is text without a link")
check('href="/item/film-1"' in h, "a movie from the library still leads to /item/")
check(not any(f'href="/item/{sid}"' in h for sid in tracks.values()), "no track leads to /item/")
reader_rights([o for o in DEFAULTS if o != "music"] + ["library_who"])
reader = login("reader")
h_reader = reader.get("/history?kind=all").text
# A track in History tells who listened - "...including who listened" under Music (access.hidden_kinds), so without
# Music there is none.
check("First Light" not in h_reader and "SkladbaBezAlbaQX" not in h_reader and "FilmVKnihovneQX" in h_reader,
      "an account without Music: History without tracks, the movies stay")
reader_rights(DEFAULTS + ["library_who", "music_who"])
h_reader = reader.get("/history?kind=all").text
# An album page belongs to the Library (menu by sections, 16. 11. 2026), so the track leads to its album. Without the
# Library a reader has no History at all (it needs "...including who watched"); that /album/ is then closed and never
# linked is the permission matrix's job (test_rights_matrix: area addresses and dead links).
check("First Light" in h_reader and f'href="/album/{AURORA}#track-' in h_reader,
      "an account with who listened: the track leads to the album (the album belongs to the Library)")

print()
print("--- B) a viewer's page ---")
listening = stats.music_viewer("u-jana", 30)
d = admin.get("/users/u-jana?days=30").text
artist = re.search(r'Nejposlouchanější interpret</p>\s*<div class="tile-value"><a href="([^"]+)"[^>]*>([^<]+)</a>', d)
check("Poslech hudby" in d and tile(d, "Poslech hudby") == formatting.seconds_human(listening["seconds"])
      and tile(d, "Přehrání skladeb") == str(listening["plays"]) and listening["artist"] == "Northern Lights Ensemble"
      and artist is not None and artist.groups() == (f"/music/artist/{listening['artist_id']}", listening["artist"]),
      f"a viewer who listened: the Poslech hudby row, the artist is a link ({listening}; {artist and artist.groups()})")
check("Poslech hudby" not in admin.get("/users/u-tomas?days=30").text, "a viewer who did not listen has no row")
reader_rights([o for o in DEFAULTS if o != "music"] + ["users"])
d = reader.get("/users/u-jana?days=30")
check(d.status_code == 200 and "Poslech hudby" not in d.text, f"an account without Music does not see the row ({d.status_code})")
# Somebody else's listening and their artist are the Listeners card's data: "...including who listened" (music_who).
reader_rights(DEFAULTS + ["users"])
d = reader.get("/users/u-jana?days=30")
check(d.status_code == 200 and "Poslech hudby" not in d.text,
      f"an account with Music but without who listened does not see the row ({d.status_code})")
reader_rights(DEFAULTS + ["users", "music_who"])
check("Poslech hudby" in reader.get("/users/u-jana?days=30").text, "with who listened the row is there")
reader_rights(None)
# A viewer's own page needs only Music - they know about themselves.
accounts.create("jana", "longpassword")
jana = accounts.get_by_name("jana")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-jana' WHERE username = 'jana'")
    conn.commit()
db.set_setting(access.LOGIN_KEY, "1")
for areas, shows in ((["music"], True), (["library"], False)):
    accounts.save_permissions(jana["id"], access.pack_permissions(areas, False))
    db.forget_settings()
    d = login("jana").get("/users/u-jana?days=30")
    check(d.status_code == 200 and ("Poslech hudby" in d.text) == shows,
          f"a viewer's own page with {areas}: the row {'is there' if shows else 'is not'} ({d.status_code})")

print()
print("--- C) the sample data ---")
demodata.wipe()
check(not db.query_value("SELECT 1 FROM music_track LIMIT 1") and not db.query_value("SELECT 1 FROM music_album LIMIT 1")
      and not db.query_value("SELECT 1 FROM music_artist LIMIT 1"), "wipe() deletes the music too")
demodata.seed()
check(bool(db.query_value("SELECT 1 FROM music_track LIMIT 1"))
      and bool(db.query_value("SELECT 1 FROM playback WHERE item_type = 'Audio' LIMIT 1")),
      "after seed() there is music and listening")
r = admin.get("/music/overview?days=30")
ri = admin.get("/music/insights?days=30")           # the charts are on Insights (22. 11. 2026)
library_page = admin.get("/library").text           # ... and the library's cards in the Library
both = r.text + ri.text + library_page
EMPTY_MARKS = ("chart-empty", "Zatím žádná data")
without_content = [k.key for k in cards_music.CARDS if k.key != "music_library"
                   and (not card_html(both, k.key) or any(z in card_html(both, k.key) for z in EMPTY_MARKS))]
check(r.status_code == 200 and ri.status_code == 200 and not without_content,
      f"in the demo every music card has content ({r.status_code}, empty: {without_content})")
check(bool(re.search(r'href="/album/[\w-]+#track-', admin.get("/history?kind=music").text)),
      "in the sample History a track leads to the album")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
