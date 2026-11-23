# -*- coding: utf-8 -*-
r"""The artist and album pages: /music/artist/<id> and /album/<id>.

Guarded:

A) artist: the numbers for the period as in the statistics, their albums
   (links to the album page, the cover through /image/ with a fingerprint),
   the most listened tracks (a link to the album's row);
B) album: the tracks in the album's order with an anchor #track-<id>, the
   artist is a link back, the numbers as in the statistics, the cover;
C) an unknown id and an id of a nonsense shape = 404; an item that vanished
   from Jellyfin still has its page and says so;
D) an account without the Music area has no pages (403); without "…including who listened"
   neither the listeners' block nor a name is in the HTML; with it and with
   anonymising the block has no names;
E) incomplete data: an album without an artist and listening to a track
   without an album do not fail.

Music from recorded Jellyfin 10.11 responses, listening from tests/_music.py.

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md

Run:
    .venv/bin/python tests/test_music_detail.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hudba-detail.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import tile, login  # noqa: E402
from _music import LISTENERS, fill_music  # noqa: E402
from jellyscope import access, accounts, db, formatting, stats  # noqa: E402
from jellyscope.stats.music_library import album_detail  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def names_in(text: str) -> list[str]:
    return [name for name in LISTENERS.values() if name in text]


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
DEFAULTS = [o.key for o in access.AREAS if o.default]


def reader_rights(areas: list[str] | None, anonymise: bool = False) -> None:
    accounts.save_permissions(reader_account["id"], None if areas is None else access.pack_permissions(areas, anonymise))
    db.forget_settings()


tracks = fill_music()
artists = {r["name"]: dict(r) for r in db.query_all("SELECT * FROM music_artist")}
albums = {r["name"]: dict(r) for r in db.query_all("SELECT * FROM music_album")}
NLE, AURORA = artists["Northern Lights Ensemble"], albums["Aurora Sessions"]
admin = login("admin")

print("--- A) artist ---")
r = admin.get(f"/music/artist/{NLE['id']}?days=30")
s = r.text
data = stats.music_artist(NLE["id"], 30)
check(r.status_code == 200 and f"<h1>{NLE['name']}</h1>" in s, f"the artist page ({r.status_code})")
check(tile(s, "Poslech") == formatting.seconds_human(data["numbers"]["seconds"]) and tile(s, "Přehrání") == str(data["numbers"]["plays"])
      and tile(s, "Posluchačů") == str(data["numbers"]["listener_count"]) and data["numbers"]["plays"] == 2,
      f"the numbers for the period as in the statistics ({data['numbers']})")
check(f'href="/album/{AURORA["id"]}"' in s and f'/image/{AURORA["id"]}?kind=Primary&amp;w=300&amp;tag={AURORA["cover_tag"]}' in s,
      "the artist's album is a link with a cover and a fingerprint")
check(f'href="/album/{AURORA["id"]}#track-{tracks["First Light"]}"' in s
      and f'href="/album/{AURORA["id"]}#track-{tracks["Polar Drift"]}"' in s,
      "the most listened tracks lead to their album row")
check('href="/music/overview?' in s or 'href="/music/overview"' in s, "a link back to the Music page")

print()
print("--- B) album ---")
# Since 16. 11. 2026 the album page is in the Library, about the album over the whole history
# (tests/test_music_library.py has the rest); a period in the address changes nothing.
r = admin.get(f"/album/{AURORA['id']}?days=30")
s = r.text
data = album_detail(AURORA["id"])
check(r.status_code == 200 and f"<h1>{AURORA['name']}</h1>" in s, f"the album page ({r.status_code})")
anchors = re.findall(r'id="track-([\w-]+)"', s)
check(anchors == [x["id"] for x in data["tracks"]] and len(anchors) == 5, f"the tracks in the album's order with an anchor ({len(anchors)})")
check(f'href="/music/artist/{NLE["id"]}"' in s, "the album's artist is a link back")
check(tile(s, "Přehrání") == str(data["plays"]) and "Poslech" not in re.sub(r"<table.*?</table>", "", s, flags=re.S),
      f"tiles: plays over the whole time, no listening for the period ({data['plays']})")
check(f'/image/{AURORA["id"]}?kind=Primary&amp;w=300&amp;tag={AURORA["cover_tag"]}' in s, "the album cover with a fingerprint")
check(all(f in s for f in data["formats"]) and formatting.bytes_human(data["size"]) in s, "the album's format and size")

print()
print("--- C) unknown, nonsense and vanished ---")
for address in ("/music/artist/neni", "/album/neni", "/album/a.b", "/music/artist/a%20b", "/album/%C3%A9"):
    check(admin.get(address, follow_redirects=False).status_code == 404, f"{address} = 404")
synth = albums["Synth Tide"]
with db.connect() as conn:
    conn.execute("UPDATE music_album SET missing = 1 WHERE id = ?", (synth["id"],))
    conn.execute("UPDATE music_artist SET missing = 1 WHERE id = ?", (synth["artist_id"],))
    conn.commit()
for address in (f"/album/{synth['id']}", f"/music/artist/{synth['artist_id']}"):
    r = admin.get(address)
    check(r.status_code == 200 and "V Jellyfinu už není." in r.text, f"a vanished one has its page and says so ({address.split('/')[2]})")

print()
print("--- D) rights and names ---")
reader_rights([o for o in DEFAULTS if o != "music"])
reader = login("reader")
check(reader.get(f"/music/artist/{NLE['id']}", follow_redirects=False).status_code == 403, "without Music the artist = 403")
# The album page belongs to the Library (menu by sections, 16. 11. 2026); who listened stays with "music_who".
r = reader.get(f"/album/{AURORA['id']}?days=30", follow_redirects=False)
check(r.status_code == 200 and "Kdo poslouchal" not in r.text and not names_in(r.text),
      f"without Music, with the Library: the album opens, without names ({r.status_code}, {names_in(r.text)})")
reader_rights([o for o in DEFAULTS if o != "library"])
check(reader.get(f"/album/{AURORA['id']}", follow_redirects=False).status_code == 403, "without the Library the album = 403")
admin_page = admin.get(f"/album/{AURORA['id']}?days=30").text
check("Kdo poslouchal" in admin_page and "Janička Posluchačová" in admin_page, "the admin sees who listened to the album")
reader_rights(DEFAULTS)
for address in (f"/album/{AURORA['id']}?days=30", f"/music/artist/{NLE['id']}?days=30"):
    s = reader.get(address).text
    check("Kdo poslouchal" not in s and not names_in(s) and "/users/" not in s,
          f"without „…kdo poslouchal“ neither the block nor a name ({address.split('/')[2]}: {names_in(s)})")
reader_rights([*DEFAULTS, "music_who", "users"], anonymise=True)
for address in (f"/album/{AURORA['id']}?days=30", f"/music/artist/{NLE['id']}?days=30"):
    s = reader.get(address).text
    check("Kdo poslouchal" in s and not names_in(s) and 'href="/users/skryto-?' not in s,
          f"with the right and anonymising the block has no names ({address.split('/')[2]}: {names_in(s)})")
reader_rights(None)

print()
print("--- E) incomplete data ---")
without_artist = [a for a in albums.values() if not a["artist_id"]]
check(bool(without_artist), f"the recordings have an album without an artist ({[a['name'] for a in without_artist]})")
for a in without_artist:
    r = admin.get(f"/album/{a['id']}?days=30")
    check(r.status_code == 200 and "/music/artist/" not in r.text, f"an album without an artist: a page without a link to the artist ({a['name']})")
border_band = artists["Hraničáři"]
when = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
with db.connect() as conn:
    conn.execute("INSERT INTO music_track (id, name, artist_id, artist, runtime_ticks) VALUES ('osamela', 'Osamělá', ?, 'Hraničáři', 600000000)",
                 (border_band["id"],))
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at, last_seen_at,"
                 " watched_seconds, paused_seconds, is_paused, is_active) VALUES ('osamela', 'u-eva', 'Eva', 'osamela', 'Osamělá', 'Audio',"
                 " ?, ?, 60, 0, 0, 0)", (when, when))
    conn.commit()
r = admin.get(f"/music/artist/{border_band['id']}?days=30")
check(r.status_code == 200 and "Osamělá" in r.text and "#track-osamela" not in r.text,
      "a track without an album is on the artist page without a link")

print()
print("--- F) the album tile with a vanished track (a minor from the final review, 16. 11. 2026) ---")
with db.connect() as conn:
    conn.execute("UPDATE music_track SET missing = 1 WHERE id = ?", (tracks["Last Aurora"],))
    conn.commit()
s = admin.get(f"/album/{AURORA['id']}?days=30").text
check(tile(s, "Skladeb") == "4" and len(re.findall(r'id="track-([\w-]+)"', s)) == 5,
      f"„Skladeb“ counts only the tracks that are in Jellyfin (like the length and size); a vanished one stays in the list ({tile(s, 'Skladeb')})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
