# -*- coding: utf-8 -*-
r"""Links: wherever a viewer or a title from the library is written out, it is a link to its detail.

Pages and cards keep coming and a link to the detail is easy to forget - nothing
falls over, it just cannot be clicked. This test therefore does not read the templates, but what
comes out of them: over the sample data and recorded answers of Sonarr, Radarr
and Seerr it draws all the pages and a dashboard with all the cards of the registry,
walks through the HTML and for every text that is a viewer's name or a title's name
it looks whether it stands inside a link. Watched:

A) every viewer and every title that is in the library is a link (on all
   pages and in all cards in the dashboard);
B) a title that is not in the library (a pending request in Seerr, a movie that
   Radarr is only downloading) has no link - a link that leads nowhere is
   worse than none;
C) the links lead where they should: a movie to /item/…, a series to /series/…,
   a viewer to /users/… - and that page exists;
D) *Arr is paired with the library by id, not by name: a movie by the TMDB id,
   a series by the id from Jellyfin (tests/test_sonarr_titles.py tries that separately);
E) whoever may not go into the library does not get links to titles.

Exceptions where a name is not to be a link: choices (<option>), the page's heading
and the dialogs of the same title on its own page, labels in charts (SVG),
paths to files.

A name is also looked for **inside a longer text** ("Hra o trůny · S01E02"): a card
that glues the name with something else into one piece of text and forgets the link
would otherwise pass (a finding of the final review). And the cards that write out titles or viewers
must have at least one in the test data - an empty card would
pass too.

Run:
    .venv/bin/python tests/test_links.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prokliky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _apps import (  # noqa: E402
    SEERR_RESPONSES, bazarr_client, bazarr_handler, no_network, fake_jellyfin_series, card_html, recorded_response, fill_bazarr, fill_lidarr,
    fill_radarr, fill_seerr, fill_sonarr, login, connect_bazarr, connect_lidarr, connect_radarr, connect_seerr, connect_sonarr,
    lidarr_client, lidarr_handler, radarr_client, radarr_handler, seerr_client, seerr_handler, sonarr_client, sonarr_handler,
)
from _package import substitute  # noqa: E402
from _music import fill_music  # noqa: E402
from jellyscope import access, accounts, cards, db, demodata, seerr  # noqa: E402
from jellyscope.arr import bazarr, lidarr, radarr, sonarr  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
demodata.seed()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")

# --- The library gets titles that *Arr and Seerr know too -------------------
REQUESTS = recorded_response(SEERR_RESPONSES, "request")["results"]


def request(number: int) -> dict:
    return next(z for z in REQUESTS if z["id"] == number)


def with_dashes(bare: str) -> str:
    return f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}"


MATRIX_ID = request(1)["media"]["jellyfinMediaId"]          # a movie from Seerr, in the library
DARK_ID = request(2)["media"]["jellyfinMediaId"]            # a series from Seerr, in the library
GOT_ID = "feca7a7c96c254bb6ed6a3c87574ce63"                # Sonarr's series: the series' id in Jellyfin
CHERNOBYL_ID = "287bce17ef706956df43daa0e47c24a7"
now = db.utcnow()
with db.connect() as conn:
    def movie(item_id: str, name: str, tmdb: str) -> None:
        conn.execute("INSERT INTO items (id, name, type, tmdb_id, date_created, synced_at) VALUES (?, ?, 'Movie', ?, ?, ?)",
                     (item_id, name, tmdb, now, now))

    def episode(item_id: str, series_id: str, series: str) -> None:
        conn.execute("INSERT INTO items (id, name, type, series_id, series_name, index_number, parent_index_number,"
                     " date_created, synced_at) VALUES (?, 'Pilot', 'Episode', ?, ?, 1, 1, ?, ?)",
                     (item_id, series_id, series, now, now))

    movie(MATRIX_ID, "The Matrix", "603")
    movie(request(4)["media"]["jellyfinMediaId"], "Interstellar", "157336")
    episode("prokliky-dark-1", DARK_ID, "Dark")
    # Radarr's movies: two are in the library (with the same TMDB id), two not. Sintel has a CZECH
    # name in the library - it is paired by the id, not by the name.
    movie("prokliky-bbb", "Big Buck Bunny", "10378")
    movie("prokliky-sintel", "Sintel (česky)", "45745")
    # Sonarr's series: the series' id in the library is stored with dashes, Jellyfin sends it without them.
    episode("prokliky-got-1", with_dashes(GOT_ID), "Game of Thrones")
    episode("prokliky-cher-1", CHERNOBYL_ID, "Černobyl")
    conn.execute("INSERT INTO users (id, name) VALUES (?, 'Žadatelka Jana')", (request(1)["requestedBy"]["jellyfinUserId"],))
    conn.commit()

no_network()
substitute(sonarr, "SonarrClient", sonarr_client(sonarr_handler()))
substitute(radarr, "RadarrClient", radarr_client(radarr_handler()))
substitute(seerr, "SeerrClient", seerr_client(seerr_handler()))
# Jellyfin tells *Arr which series it has and under which ids (Tvdb/Tmdb/Imdb as with a real server).
substitute(sonarr, "JellyfinClient", fake_jellyfin_series([
    {"Id": GOT_ID, "Name": "Game of Thrones", "ProviderIds": {"Tvdb": "121361", "Tmdb": "1399", "Imdb": "tt0944947"}},
    {"Id": CHERNOBYL_ID, "Name": "Černobyl", "ProviderIds": {"Tvdb": "360893", "Tmdb": "87108"}},
    # Dark is in the library (it came through Seerr) and Sonarr is downloading it right now - the card "Stahuje se".
    {"Id": DARK_ID, "Name": "Dark", "ProviderIds": {"Tvdb": "334824", "Tmdb": "70523"}},
]))
connect_sonarr()
fill_sonarr()
connect_radarr()
fill_radarr()
connect_seerr()
fill_seerr()
# Bazarr: it knows the titles by the numbers from Sonarr and Radarr - the card "Titulky získané opakovaně".
substitute(bazarr, "BazarrClient", bazarr_client(bazarr_handler()))
connect_bazarr()
fill_bazarr()
# Music (recordings of Jellyfin 10.11) and the listening of three viewers - the Music page, an artist and an album.
TRACKS = fill_music()
# Lidarr (music): two of its albums are in the music library (paired by the name and the artist), the others are not.
with db.connect() as conn:
    conn.execute("INSERT INTO music_artist (id, name, synced_at, missing) VALUES ('prokliky-kryst', 'Kryštof', ?, 0)", (now,))
    for album_id, name in (("prokliky-hvezdy", "Hvězdy"), ("prokliky-sipkova", "Šípková Růžena")):
        conn.execute("INSERT INTO music_album (id, name, artist_id, artist, missing, synced_at) VALUES (?, ?, 'prokliky-kryst', 'Kryštof', 0, ?)",
                     (album_id, name, now))
    conn.commit()
substitute(lidarr, "LidarrClient", lidarr_client(lidarr_handler()))
connect_lidarr()
fill_lidarr()
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
cards.save_layout([s.key for s in cards.ALL_CARDS])

VIEWERS = {str(r["name"]) for r in db.query_all("SELECT name FROM users") if r["name"]}
IN_LIBRARY = ({str(r["name"]) for r in db.query_all("SELECT DISTINCT name FROM items WHERE type = 'Movie'")}
              | {str(r["series_name"]) for r in db.query_all("SELECT DISTINCT series_name FROM items WHERE series_name IS NOT NULL")})
# The names under which *Arr and Seerr know a title. Sintel and Chernobyl are named differently
# in the library (in Czech), but they are the same titles - they are to have a link under the app's name too.
FROM_APPS = ({str(r["title"]) for r in db.query_all("SELECT title FROM sonarr_event UNION SELECT title FROM radarr_event"
                                                    " UNION SELECT title FROM lidarr_event UNION SELECT artist FROM lidarr_event")}
             | {str(r["title"]) for r in db.query_all("SELECT DISTINCT title FROM seerr_request")}
             | {str(r["title"]) for r in db.query_all("SELECT DISTINCT title FROM bazarr_event")}) - {""}
SAME_TITLE = {"Sintel", "Chernobyl"}
# Artists and albums from the music: they have a link to their page wherever they are written out.
ARTISTS = {str(r["name"]): str(r["id"]) for r in db.query_all("SELECT id, name FROM music_artist")}
ALBUMS = {str(r["name"]): str(r["id"]) for r in db.query_all("SELECT id, name FROM music_album")}
MUSIC = (set(ARTISTS) | set(ALBUMS)) - {""}
HAS_LINK = IN_LIBRARY | SAME_TITLE | MUSIC
TITLES = IN_LIBRARY | FROM_APPS | MUSIC
# Where a name is not to be a link: a choice, a title, the page's heading, a chart (SVG), a path to a file.
NO_LINK = {"option", "title", "script", "style", "h1", "text", "tspan", "pre", "code"}
# Names looked for inside a longer text too. Only from five characters (a short name like "Eva" or
# "Dark" would be found in any word or sentence) and not as a piece of a path to a file
# ("D:/filmy/Interstellar (2014)/Interstellar.mkv" - a slash before the name, a full stop after it).
IN_TEXT = re.compile(r"(?<![\w/\\.])(" + "|".join(re.escape(j) for j in sorted(VIEWERS | TITLES, key=len, reverse=True) if len(j) >= 5)
                     + r")(?![\w/\\.])")
EMPTY_TAGS = {"br", "img", "input", "meta", "link", "hr", "path", "rect", "circle", "line", "polyline", "polygon", "use", "stop"}


class Finder(HTMLParser):
    """Walks through a page and writes down every text that is a viewer's name or a title's name."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []
        self.card, self.h1, self.in_h1 = "", "", False
        self.findings: list[tuple[str, str, str, str]] = []      # (kind, text, link, card)

    def handle_comment(self, data: str) -> None:
        match = re.match(r"\s*card: (\w+)", data)
        if match:
            self.card = match.group(1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in EMPTY_TAGS:
            return
        self.stack.append((tag, dict(attrs).get("href") or ""))
        self.in_h1 = self.in_h1 or tag == "h1"

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag == "h1":
            self.in_h1 = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self.in_h1:
            self.h1 += text
        tags = {tag for tag, _ in self.stack}
        if not text or tags & NO_LINK or "nav" in tags:
            return
        if text not in VIEWERS and text not in TITLES:
            # A name inside a longer text: from here on it is treated as if it stood here alone.
            match = IN_TEXT.search(text)
            if not match:
                return
            text = match.group(1)
        link = next((href for tag, href in reversed(self.stack) if tag == "a"), "")
        self.findings.append(("viewer" if text in VIEWERS else "title", text, link, self.card))


admin = login("admin")
library_id = (db.query_one("SELECT id FROM libraries WHERE collection_type = 'movies' LIMIT 1") or {}).get("id")
viewer_id = (db.query_one("SELECT user_id AS id FROM playback GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1") or {}).get("id")
movie_id = (db.query_one("SELECT p.item_id AS id FROM playback p JOIN items i ON i.id = p.item_id WHERE i.type = 'Movie' LIMIT 1") or {}).get("id")
episode_row = db.query_one("SELECT i.id, i.series_id FROM playback p JOIN items i ON i.id = p.item_id WHERE i.type = 'Episode' LIMIT 1") or {}
PAGES = ["/?days=365", "/dashboard?days=365", "/video/compare", "/video/insights?days=365", "/video/languages?days=365",
         "/video/languages/undetermined", "/network?days=365", "/library", f"/library/{library_id}", "/users?days=365",
         f"/users/{viewer_id}?days=365", "/history", f"/item/{movie_id}", f"/item/{episode_row.get('id')}",
         f"/series/{episode_row.get('series_id')}", "/video/insights/top?days=365", "/arr/sonarr?days=365", "/arr/radarr?days=365",
         "/arr/lidarr?days=365", "/arr/bazarr?days=365", "/seerr?days=365", "/music/overview?days=365", "/music/compare?a_days=365", "/music/insights?days=365",
         f"/music/artist/{ARTISTS['Northern Lights Ensemble']}?days=365", f"/album/{ALBUMS['Aurora Sessions']}?days=365",
         f"/album/{ALBUMS['Okrajové případy']}?days=365"]


def walk(client, path: str) -> tuple[int, list[tuple[str, str, str, str]]]:
    """The findings on a page - without the name of the title or viewer the page itself belongs to (its detail)."""
    response = client.get(path)
    finder = Finder()
    finder.feed(response.text)
    return response.status_code, [n for n in finder.findings if n[1] != finder.h1.strip()]


print("--- A) + B) every page: a viewer and a title from the library is a link, a title outside the library not ---")
all_found: dict[str, list[tuple[str, str, str, str]]] = {}
for path in PAGES:
    code, findings = walk(admin, path)
    page = re.sub(r"/[0-9a-f-]{16,}|/demo-[\w-]+", "/<id>", path.split("?")[0])
    all_found[page] = findings
    without = defaultdict(set)
    for kind, text, link, card in findings:
        if not link and (kind == "viewer" or text in HAS_LINK):
            without[card or "page"].add(text)
    extra = sorted({text for kind, text, link, _ in findings if kind == "title" and link and text not in HAS_LINK})
    label = "; ".join(f"{where}: {sorted(what)[:3]}" for where, what in sorted(without.items()))
    check(code == 200 and not without, f"{page}: viewers and titles from the library are links ({len(findings)} places{'; NO LINK ' + label if without else ''})")
    check(not extra, f"{page}: a title that is not in the library has no link ({extra[:4]})")
check(sum(len(n) for n in all_found.values()) > 400 and len(all_found["/dashboard"]) > 150,
      f"the test has something to check ({sum(len(n) for n in all_found.values())} places, {len(all_found['/dashboard'])} of them in the dashboard with all cards)")
# The cards that write out titles or viewers really have them in the test data - otherwise
# a card without rows would pass, whether it has links or not. A new card with a list of titles belongs here.
WITH_NAMES = {f"arr_{app}_{name}" for app in ("sonarr", "radarr", "lidarr")
              for name in ("largest", "upgraded", "queue", "calendar", "downloading")} | {
    "bazarr_changed", "seerr_pending", "seerr_latest", "seerr_nobody", "seerr_who", "most_watched", "most_active_users",
    "users_chart", "users_table", "who_language", "insight_duplicates", "insight_speed", "insight_transcoded",
    "music_albums", "music_artists", "music_tracks", "music_listeners", "music_new", "music_largest", "music_unplayed"}
in_cards = {card for _, _, _, card in all_found["/dashboard"]}
check(WITH_NAMES <= in_cards, f"the cards with a list of titles or viewers have something to show in the data (empty: {sorted(WITH_NAMES - in_cards)})")

print()
print("--- C) + D) the links lead to the right detail ---")


def links(page: str, card: str, text: str) -> set[str]:
    return {link for _, t, link, k in all_found[page] if k == card and t == text}


check(links("/seerr", "seerr_latest", "The Matrix") == {f"/item/{MATRIX_ID}"}, f"Seerr: a movie from the library leads to /item/… ({links('/seerr', 'seerr_latest', 'The Matrix')})")
check(links("/seerr", "seerr_latest", "Dark") == {f"/series/{DARK_ID}"} and links("/seerr", "seerr_nobody", "Dark") <= {f"/series/{DARK_ID}"},
      f"Seerr: a series from the library leads to /series/… ({links('/seerr', 'seerr_latest', 'Dark')})")
check(links("/seerr", "seerr_pending", "Film 872585") == {""} and links("/seerr", "seerr_latest", "Film 27205") == {""},
      "Seerr: a pending and an approved request outside the library have no link")
check(links("/arr/radarr", "arr_radarr_largest", "Big Buck Bunny") == {"/item/prokliky-bbb"}
      and links("/arr/radarr", "arr_radarr_largest", "Sintel") == {"/item/prokliky-sintel"},
      f"Radarr: a movie is paired by the TMDB id, even when it is named differently in the library ({links('/arr/radarr', 'arr_radarr_largest', 'Sintel')})")
check(links("/arr/radarr", "arr_radarr_largest", "Elephants Dream") == {""} and links("/arr/radarr", "arr_radarr_queue", "Elephants Dream") == {""},
      "Radarr: a movie that is not in the library has no link")
check(links("/arr/radarr", "arr_radarr_calendar", "Big Buck Bunny") == {"/item/prokliky-bbb"}
      and links("/arr/radarr", "arr_radarr_upgraded", "Big Buck Bunny") == {"/item/prokliky-bbb"}, "Radarr: the calendar and upgraded titles too")
got = f"/series/{with_dashes(GOT_ID)}"
check(links("/arr/sonarr", "arr_sonarr_largest", "Game of Thrones") == {got} and links("/arr/sonarr", "arr_sonarr_queue", "Game of Thrones") == {got}
      and links("/arr/sonarr", "arr_sonarr_calendar", "Game of Thrones") == {got} and links("/arr/sonarr", "arr_sonarr_upgraded", "Game of Thrones") == {got},
      f"Sonarr: a series leads to /series/… with the id as the library has it ({links('/arr/sonarr', 'arr_sonarr_largest', 'Game of Thrones')})")
check(links("/arr/sonarr", "arr_sonarr_downloading", "Dark") == {f"/series/{DARK_ID}"}
      and links("/arr/sonarr", "arr_sonarr_downloading", "Chernobyl") == {f"/series/{CHERNOBYL_ID}"},
      f"Downloading: a series from the library leads to its detail ({links('/arr/sonarr', 'arr_sonarr_downloading', 'Dark')})")
check(links("/arr/sonarr", "arr_sonarr_largest", "Chernobyl") == {f"/series/{CHERNOBYL_ID}"},
      "Sonarr: a series is paired by the id from Jellyfin, even when it is named differently in the library")
HVEZDY, KRYST = "/album/prokliky-hvezdy", "/music/artist/prokliky-kryst"
check(all(links("/arr/lidarr", f"arr_lidarr_{card}", "Hvězdy") == {HVEZDY} for card in ("largest", "upgraded"))
      and links("/arr/lidarr", "arr_lidarr_largest", "Šípková Růžena") == {"/album/prokliky-sipkova"}
      and links("/arr/lidarr", "arr_lidarr_largest", "Kryštof") == {KRYST} and links("/arr/lidarr", "arr_lidarr_upgraded", "Kryštof") == {KRYST},
      f"Lidarr: an album from the music library leads to /album/…, its artist to /music/artist/… ({links('/arr/lidarr', 'arr_lidarr_largest', 'Hvězdy')})")
# "Artist - Album" in the queue and in Downloading is one text and one link: to the album (the artist's name is found first inside it).
check(links("/arr/lidarr", "arr_lidarr_queue", "Kryštof") == {HVEZDY, "/album/prokliky-sipkova"}
      and links("/arr/lidarr", "arr_lidarr_downloading", "Kryštof") == {HVEZDY, "/album/prokliky-sipkova"},
      "Lidarr: the queue and Downloading link an album from the library as a whole (Artist - Album)")
check(links("/arr/lidarr", "arr_lidarr_largest", "Blue Hour") == {""} and links("/arr/lidarr", "arr_lidarr_largest", "Nightingale Quartet") == {""}
      and links("/arr/lidarr", "arr_lidarr_calendar", "Nightingale Quartet") == {""} and links("/arr/lidarr", "arr_lidarr_downloading", "Nightingale Quartet") == {""},
      "Lidarr: an album and an artist that are not in the music library have no link, in any card")
check(links("/dashboard", "arr_lidarr_largest", "Hvězdy") == {HVEZDY}, "the same in Lidarr's card in the custom dashboard")
check(links("/dashboard", "arr_sonarr_largest", "Game of Thrones") == {got} and links("/dashboard", "seerr_latest", "The Matrix") == {f"/item/{MATRIX_ID}"},
      "the same in the cards in the custom dashboard")
check(links("/arr/bazarr", "bazarr_changed", "Game of Thrones") == {got} and links("/dashboard", "bazarr_changed", "Game of Thrones") == {got},
      f"Bazarr: it knows the title by the number from Sonarr, the link leads to the same detail ({links('/arr/bazarr', 'bazarr_changed', 'Game of Thrones')})")
NLE, AURORA = f"/music/artist/{ARTISTS['Northern Lights Ensemble']}", f"/album/{ALBUMS['Aurora Sessions']}"
check(links("/music/insights", "music_artists", "Northern Lights Ensemble") == {NLE} and links("/music/insights", "music_albums", "Aurora Sessions") == {AURORA}
      and links("/music/insights", "music_albums", "Northern Lights Ensemble") == {NLE},
      f"Music: an artist leads to their page, an album to its own - the artist under an album too ({links('/music/insights', 'music_albums', 'Northern Lights Ensemble')})")
# A walk of its own: both album pages have the same key /album/<id> in `all_found` and the second would overwrite the first.
on_artist = {o for _, text, o, _ in walk(admin, f"{NLE}?days=365")[1] if text == "Aurora Sessions"}
on_album = {o for _, text, o, _ in walk(admin, f"{AURORA}?days=365")[1] if text == "Northern Lights Ensemble"}
check(on_artist == {AURORA} and on_album == {NLE},
      f"the artist's page leads to the album and the album's page back to the artist ({on_artist}, {on_album})")
check(links("/history", "", "Northern Lights Ensemble") == {f"{AURORA}#track-{TRACKS['First Light']}",
                                                            f"{AURORA}#track-{TRACKS['Polar Drift']}"},
      f"History: the artist before the track is in the link to the track's row ({links('/history', '', 'Northern Lights Ensemble')})")
compare_page = admin.get("/video/compare").text
check(len(re.findall(r'<td class="strong">\s*<a href="/users/[^"]+">', compare_page)) >= 2
      and len(re.findall(r'<td class="strong">\s*<a href="/(?:series|item)/[^"]+">', compare_page)) >= 2,
      "Compare: viewers lead to /users/…, titles to their detail")
targets = sorted({link.split("#")[0] for findings in all_found.values() for _, _, link, card in findings
                  if link and card.startswith(("arr_", "seerr_", "hudba_"))})
dead = [target for target in targets if admin.get(target).status_code != 200]
check(len(targets) >= 6 and not dead, f"the links from the cards of *Arr, Seerr and music lead to existing pages ({len(targets)} targets, dead: {dead})")

print()
print("--- E) whoever may not go into the library does not get links to titles ---")
reader_account = accounts.get_by_name("reader")
accounts.save_permissions(reader_account["id"], access.pack_permissions(["seerr", "arr", "prehled", "dashboard"], False))
reader = login("reader")
for path, card, title in (("/seerr?days=365", "seerr_latest", "The Matrix"), ("/arr/sonarr?days=365", "arr_sonarr_largest", "Game of Thrones")):
    html = card_html(reader.get(path).text, card)
    check(title in html and 'href="/item/' not in html and 'href="/series/' not in html,
          f"{path.split('?')[0]}: without the Library area the title is seen, but it is not a link")

print()
print("--- F) edges: a play without a name, a copy without a path ---")
# A play that has neither a title's nor a series' name (an import without pairing) is the slowest
# transcode of the period: the tile under the number must not write out "None".
# Two copies of the same movie, one without a path: there must be no empty link in the expanded paths.
with db.connect() as conn:
    conn.execute("INSERT INTO playback (session_key, user_id, item_id, started_at, last_seen_at, watched_seconds,"
                 " transcode_fps_sum, transcode_fps_samples) VALUES ('prokliky-bez-nazvu', 'u-x', 'neni-v-knihovne', ?, ?, 600, 1, 1)",
                 (now, now))
    for item_id, path in (("prokliky-kopie-1", "/filmy/Dvojnik (2001)/Dvojnik.mkv"), ("prokliky-kopie-2", None)):
        conn.execute("INSERT INTO items (id, name, type, production_year, path, size_bytes, synced_at) VALUES (?, 'Dvojník', 'Movie', 2001, ?, 5, ?)",
                     (item_id, path, now))
    conn.commit()
insights = admin.get("/video/insights?days=365").text
speed = insights[insights.find('id="transcode-speed"'):]
speed = speed[:speed.find("</table>") if "</table>" in speed else 4000]
check("1 fps" in speed and ">None<" not in speed and "None</div>" not in speed,
      "Transcode speed: a play without a name does not write out “None”")
duplicates = insights[insights.find("Možné duplicity"):]
double_row = re.search(r"<tr[^>]*>(?:(?!</tr>).)*?Dvojník.*?</tr>", duplicates, re.S)
double = double_row.group(0) if double_row else ""
check(re.search(r'<a href="/item/prokliky-kopie-1">\s*Dvojník', double) is not None,
      "Possible duplicates: the name leads to the first copy (by id, the same in SQLite and PostgreSQL)")
check('href="/item/prokliky-kopie-1">/filmy/Dvojnik (2001)/Dvojnik.mkv</a>' in double and re.search(r'<a href="/item/[^"]+"></a>', double) is None,
      "… every path to its copy, a copy without a path no empty link")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
