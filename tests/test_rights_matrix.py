r"""Every permission holds - on pages, in fragments and in the API.

The tree "who sees what" (access.AREAS) is guarded by a middleware by addresses. That
is not enough though: one area's page can include another one's content (the Overview the card
"Právě se hraje" or "Nedávno přidané"). Here therefore for every area
separately only that one is switched off and everything the account may open is walked through:

* the switched-off area's addresses do not open (403, for the Overview a redirect),
* the switched-off area's content is on no other page and not in the API,
* with everything switched on the same content really shows - otherwise the marks
  would watch nothing.

The same for the API: an account's key sees exactly what its owner does, and a root key
with chosen areas only those areas.

Run:
    .venv/bin/python tests/test_rights_matrix.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "matice.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"
os.environ.pop("JELLYSCOPE_EVENT_DATE", None)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, cards, collector, db, seerr, web  # noqa: E402
from jellyscope.arr import bazarr, prowlarr, sonarr  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# --- data ----------------------------------------------------------------
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.create("viewer", "longpassword")
# *Arr: a connected Sonarr, so the /arr/sonarr page exists. Nobody listens at the address -
# a refused connection is quick and the page shows "not answering".
sonarr.save_settings(True, "http://127.0.0.1:1", "test-klic")
# Bazarr (the third tab of the *Arr page) the same: connected, nobody listens.
bazarr.save_settings(True, "http://127.0.0.1:1", "test-klic")
# Prowlarr (the fourth tab) too.
prowlarr.save_settings(True, "http://127.0.0.1:1", "test-klic")
# Seerr: connected (nobody listens either) and one request with the requester's name -
# the mark of the area "…including who requested" (CONTENT).
seerr.save_settings(True, "http://127.0.0.1:1", "test-klic")
with db.connect() as conn:
    conn.execute("INSERT INTO seerr_request (id, media_type, tmdb_id, title, status, requester_name, created_at)"
                 " VALUES (1, 'movie', '1', 'Film žádosti', 1, 'Žadatel Matice', ?)", (db.utcnow(),))
    # Subtitles from Bazarr with a provider whose name is nowhere else - the mark of the *Arr area (CONTENT):
    # it is on the Bazarr tab and in the Bazarr card in the custom dashboard.
    conn.execute("INSERT INTO bazarr_event (fingerprint, kind, title, item_id, action, language, provider, score, happened_at, synced_at)"
                 " VALUES ('matice', 'movie', 'Film titulků', 1, 1, 'cs', 'PoskytovatelMaticeQX', 90, ?, ?)", (db.utcnow(), db.utcnow()))
    # Prowlarr: an indexer with a name that is nowhere else - the second mark of the *Arr area (CONTENT):
    # it is on the Prowlarr tab and in the Prowlarr card in the custom dashboard.
    conn.execute("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at) VALUES (1, 'IndexerMaticeQX', 'usenet',"
                 " 'private', 1, 0, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES (?, 1, 'Sonarr', 'query', 3, 0, 300, 3, 2)", (db.utcnow()[:13] + ":00:00",))
    conn.commit()
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def at(offset: timedelta) -> str:
    return (NOW + offset).strftime(db.TIME_FORMAT)


with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE username = 'viewer'")
    conn.execute("UPDATE accounts SET may_have_key = '1' WHERE username IN ('reader', 'viewer')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-petr', 'PetrZQX', 0, 0), ('u-jana', 'JanaZQX', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for item_id, title in (("film-1", "KnihaTitulQX"), ("film-2", "ZivyTitulQX")):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, production_year, path, is_missing,"
            " date_created, video_codec, height, width) VALUES (?, ?, 'Movie', 'filmy', 2024, ?, 0, ?,"
            " 'hevc', 2160, 3840)",
            (item_id, title, f"/media/filmy/{title}.mkv", at(-timedelta(days=1))))
    # A finished play (Petr and Jana on film-1) and one running right now (Jana on film-2).
    for key, uid, name, address in (("hotovo-p", "u-petr", "PetrZQX", "203.0.113.77"),
                                    ("hotovo-j", "u-jana", "JanaZQX", "203.0.113.78")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
            " library_id, client, device_name, remote_address, play_method, audio_language,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
            " VALUES (?, ?, ?, 'film-1', 'KnihaTitulQX', 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak',"
            " ?, 'DirectPlay', 'cze', ?, ?, ?, 5400, 0, 0)",
            (key, uid, name, address, at(-timedelta(hours=5)), at(-timedelta(hours=3)),
             at(-timedelta(hours=3))))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, client, device_name, remote_address, play_method, started_at, last_seen_at,"
        " watched_seconds, paused_seconds, position_ticks, is_active, media_runtime_ticks)"
        " VALUES ('zive-j', 'u-jana', 'JanaZQX', 'film-2', 'ZivyTitulQX', 'Movie', 'filmy',"
        " 'Jellyfin Web', 'Obyvak', '198.51.100.9', 'DirectPlay', ?, ?, 600, 0, 6000000000, 1,"
        " 72000000000)", (at(-timedelta(minutes=10)), at(-timedelta(seconds=5))))
    conn.commit()
# Music: Jellyfin has a music library (so the /music/overview page exists), in it an artist with an album
# (their pages are addresses of the Music area) and Jana's listening - in History the track leads to
# the album's row, and only with the Music area (otherwise it is text). The marks of the music areas are the cards themselves
# (CONTENT); a listener's name cannot be a mark, it is in History and in Users too.
with db.connect() as conn:
    conn.execute("INSERT INTO music_artist (id, name) VALUES ('interpret-m', 'InterpretMaticeQX')")
    conn.execute("INSERT INTO music_album (id, name, artist_id, artist, added_at) VALUES ('album-m', 'AlbumMaticeQX',"
                 " 'interpret-m', 'InterpretMaticeQX', ?)", (at(-timedelta(days=2)),))
    conn.execute("INSERT INTO music_track (id, name, album_id, album, artist_id, artist, track_number, runtime_ticks, codec)"
                 " VALUES ('skladba-m', 'SkladbaMaticeQX', 'album-m', 'AlbumMaticeQX', 'interpret-m', 'InterpretMaticeQX', 1,"
                 " 2000000000, 'flac')")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
        " play_method, started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
        " VALUES ('hudba-j', 'u-jana', 'JanaZQX', 'skladba-m', 'SkladbaMaticeQX', 'Audio', 'Finamp', 'Telefon',"
        " 'DirectPlay', ?, ?, ?, 200, 0, 0)", (at(-timedelta(hours=2)), at(-timedelta(hours=1)), at(-timedelta(hours=1))))
    # Books: one e-book is enough for the section to exist (its pages are empty frames so far).
    conn.execute("INSERT INTO book (id, kind, name, title, author) VALUES ('kniha-m', 'book', 'KnihaMaticeQX', 'KnihaMaticeQX', 'AutorQX')")
    conn.commit()

# The custom dashboard switched on and in it a card from the Overview - so /dashboard has something to open.
db.set_setting(cards.ENABLED_KEY, "1")
# A viewer's key holds only with signing in through Jellyfin switched on (the viewer is an account from there).
db.set_setting(access.LOGIN_KEY, "1")
# The collector "runs" - only then the left panel writes out the state with the number of plays.
db.set_setting(collector.STATUS_KEY, "ok")
cards.save_layout([("souhrn", "cela"), ("bazarr_providers", "cela"), ("prowlarr_indexers", "cela"),
                      ("music_numbers", "cela"), ("music_listeners", "pul")])
db.forget_settings()

ALL_AREAS = [o.key for o in access.AREAS]


def set_rights(name: str, enabled: list[str], anonymise: bool = False) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET permissions = ? WHERE username = ?",
                     (access.pack_permissions(enabled, anonymise), name))
        conn.commit()
    db.forget_settings()


def effective(enabled: set[str]) -> set[str]:
    """Which areas hold: a sub-area only with its parent switched on."""
    result = set()
    for area in access.AREAS:
        if area.key in enabled and (not area.parent or area.parent in enabled):
            result.add(area.key)
    return result


def sign_in(name: str) -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return k


# Every area's addresses, concretely.
AREA_ADDRESSES = {
    "charts": ["/video/insights", "/video/insights/top"],
    "library": ["/library", "/library/filmy", "/item/film-1", "/album/album-m", "/partials/recently-added",
                 "/library/partials/growth"],
    "library_who": [],
    "overview": ["/video/overview", "/video/partials/daily"],
    "dashboard": ["/dashboard"],
    "comparison": ["/video/compare"],
    "now_playing": ["/partials/now-playing", "/api/now-playing"],
    "languages": ["/video/languages"],
    "users": ["/users", "/users/u-jana"],
    "network": ["/network", "/partials/network-live"],
    "arr": ["/arr/sonarr", "/arr/bazarr", "/arr/prowlarr"],
    "seerr": ["/seerr"],
    "seerr_who": [],
    "music": ["/music/overview", "/music/artist/interpret-m"],
    "music_comparison": ["/music/compare"],
    "music_who": [],
    "music_insights": ["/music/insights"],
    "books": ["/books/overview"],
    "books_comparison": ["/books/compare"],
    "books_who": [],
    "books_insights": ["/books/insights"],
}
check(set(AREA_ADDRESSES) == set(ALL_AREAS), f"the test knows all the areas ({sorted(set(ALL_AREAS) - set(AREA_ADDRESSES))})")
ALWAYS_PAGES = ["/history", "/account", "/health", "/library/filmy?tab=activity"]
# Addresses that belong to more areas: they open when at least one holds.
# The Movies/Series switch by the Most watched card on the Overview calls it
# the same way as the Insights page.
SHARED = {"/video/partials/top-items": ("charts", "overview")}
ALL_PAGES = [a for addresses in AREA_ADDRESSES.values() for a in addresses] + ALWAYS_PAGES + list(SHARED)

# An area's content: (label, mark) - the mark must be nowhere the area does not hold.
CONTENT = {
    # The running play's title is in History and in the rankings too - that is all right.
    # The play "now" is recognised by the card and the position in the title (position_seconds).
    "now_playing": [("the playback card", '<div class="session"'), ("the playback's position", '"position_seconds"')],
    "library": [("Recently added", 'id="recently-added"')],
    "network": [("the IP address", "203.0.113.77"), ("the live one's IP address", "198.51.100.9")],
    "seerr_who": [("the requester's name", "Žadatel Matice")],
    # The music cards are on the Music page and in the custom dashboard. A listener's name cannot be a mark:
    # it is in History and in Users too, which other areas control - tests/test_music_web.py watches the names.
    "music": [("a music card", "<!-- card: music_numbers -->")],
    "music_who": [("the Listeners card", "<!-- card: music_listeners -->")],
    # Music's charts and what nobody listens to are the insights' (22. 11. 2026), not the overview's.
    "music_insights": [("a music chart", "<!-- card: music_albums -->"), ("what nobody listens to", "<!-- card: music_unplayed -->")],
    # The Bazarr card is in the custom dashboard too: without the *Arr area it must not show even there.
    "arr": [("a subtitles provider from Bazarr", "PoskytovatelMaticeQX"), ("an indexer from Prowlarr", "IndexerMaticeQX")],
}
# The number of running plays (/health) is "who watches what now" too.
CONTENT["now_playing"].append(("the number of plays in /health", '"active_sessions"'))
# ...and in the left panel of every page (under the collector's state) and in the API's summary.
CONTENT["now_playing"].append(("the number of plays in the panel", "aktivních přehrávání"))
# In the API the summary too (on the My account page "active_now" is only in the guide's sample).
API_CONTENT = {"now_playing": [("the number of plays in the API summary", '"active_now"')]}
# Who watched it - the title's detail and the library's activity (names elsewhere are governed by anonymising).
WHO_WATCHED = ("/item/film-1", "/library/filmy?tab=activity")
NAME = "JanaZQX"


def blocked(status: int) -> bool:
    return status in (303, 307, 403, 404)


def dead_links(k: TestClient, pages: dict) -> list[tuple[str, str]]:
    """Links from the pages that end at "you may not" (403) or nowhere (404)."""
    dead, seen = [], {}
    for address, (status, text) in pages.items():
        if status != 200:
            continue
        for target in sorted(set(re.findall(r'href="(/[^"#]*)"', text))):
            if target.startswith(("/static/", "/image/", "/logout")):
                continue
            if target not in seen:
                seen[target] = k.get(target, follow_redirects=False).status_code
            if seen[target] in (403, 404):
                dead.append((address, target))
    return dead


def walk(name: str, enabled: set[str], label: str) -> None:
    set_rights(name, sorted(enabled))
    valid = effective(enabled)
    k = sign_in(name)
    pages = {}
    for address in ALL_PAGES:
        r = k.get(address, follow_redirects=False)
        pages[address] = (r.status_code, r.text if r.status_code == 200 else "")
    # 1) The areas' addresses: they open only when the area holds.
    for area, addresses in AREA_ADDRESSES.items():
        for address in addresses:
            if name == "viewer" and address == "/users/u-jana":
                continue      # a viewer may open only their own viewer page - test_viewer_scope watches it
            status = pages[address][0]
            if area in valid:
                check(status == 200, f"{label}: {address} opens ({status})")
            else:
                check(blocked(status), f"{label}: {address} is closed ({status})")
    for address, areas in SHARED.items():
        status = pages[address][0]
        if set(areas) & valid:
            check(status == 200, f"{label}: {address} opens ({status})")
        else:
            check(blocked(status), f"{label}: {address} is closed ({status})")
    # History: a viewer sees only their own, so always. A reader has no viewer of their own
    # - their History is all somebody else's (names, devices), therefore only
    # with "…including who watched it". The menu then does not offer it to a reader.
    may_history = name == "viewer" or "library_who" in valid
    status = pages["/history"][0]
    check((status == 200) == may_history,
          f"{label}: /history {'opens' if may_history else 'is closed'} ({status})")
    if name == "reader":
        in_menu = 'href="/history"' in pages["/account"][1]
        check(in_menu == may_history, f"{label}: History in the menu - {'yes' if in_menu else 'no'}")
    # The whole ranking (/video/insights/top) only with its area - otherwise the link would lead to "you may not".
    status, text = pages["/video/overview"]
    if status == 200 and "charts" not in valid:
        check('href="/video/insights/top' not in text, f"{label}: the Overview does not link to the whole ranking")
    # A link behind which "you may not" waits is worse than none.
    dead = dead_links(k, pages)
    check(not dead, f"{label}: no link leads to a closed page ({len(dead)}: {dead[:4]})")
    # 2) The switched-off areas' content on no open page.
    for area, marks in CONTENT.items():
        if area in valid:
            continue
        for title, mark in marks:
            where = [a for a, (status, text) in pages.items() if mark in text]
            check(not where, f"{label}: {title} ({area}) shows nowhere ({where})")
    for address in WHO_WATCHED:
        status, text = pages[address]
        if status != 200:
            continue
        if "library_who" in valid:
            check(NAME in text, f"{label}: {address} writes out the viewers (knihovna_kdo on)")
        else:
            check(NAME not in text, f"{label}: {address} without viewers (knihovna_kdo off)")


print("--- with everything switched on the areas' content really shows (the marks do not watch nothing) ---")
set_rights("reader", ALL_AREAS)
k = sign_in("reader")
texts = {a: k.get(a, follow_redirects=False).text for a in ALL_PAGES}
for area, marks in CONTENT.items():
    for title, mark in marks:
        check(any(mark in t for t in texts.values()), f"{title} ({area}) is seen at least somewhere")
check('"active_now"' in TestClient(web.app).get("/api/v1/summary", headers={
    "Authorization": f"Bearer {api.create_token('kontrola', account_id=accounts.get_by_name('reader')['id'])['token']}"}).text,
      "the number of plays in the API summary is seen with everything switched on")
for address in WHO_WATCHED:
    check(NAME in texts[address], f"{address}: the viewers are seen (knihovna_kdo on)")

for name in ("reader", "viewer"):
    print()
    print(f"--- {name}: always only one area switched off ---")
    walk(name, set(ALL_AREAS), f"{name}, everything")
    for area in ALL_AREAS:
        walk(name, set(ALL_AREAS) - {area}, f"{name} without {area}")


print()
print("--- with hidden names somebody else's name is nowhere (pages and API) ---")

for name, others in (("reader", ("JanaZQX", "PetrZQX")), ("viewer", ("JanaZQX",))):
    set_rights(name, ALL_AREAS, anonymise=True)
    k = sign_in(name)
    pages = {}
    for address in ALL_PAGES:
        r = k.get(address, follow_redirects=False)
        pages[address] = (r.status_code, r.text)
        if r.status_code != 200:
            continue
        leaked = [j for j in others if j in r.text]
        check(not leaked, f"{name} with hidden names: {address} without others' names ({leaked})")
    # A stand-in id instead of the real one must not lead to an empty page.
    dead = dead_links(k, pages)
    check(not dead, f"{name} with hidden names: no dead link ({len(dead)}: {dead[:4]})")

print()
print("--- the Viewers chart in the library: one viewer = one column ---")
# A renamed viewer has both names in the history until the next sync.
from jellyscope import stats  # noqa: E402

with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
        " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
        " VALUES ('prejmenovana', 'u-jana', 'JanaStaraQX', 'film-1', 'KnihaTitulQX', 'Movie', 'filmy', ?, ?, ?,"
        " 1800, 0, 0)", (at(-timedelta(hours=8)), at(-timedelta(hours=7)), at(-timedelta(hours=7))))
    conn.commit()
viewers = stats.library_activity("filmy", 30)["top_users"]
janas = [r for r in viewers if r.get("user_id") == "u-jana"]
# 5400 s of the finished + 600 s of the running + 1800 s under the old name
check(len(janas) == 1 and abs(float(janas[0]["hours"]) - 7800 / 3600) < 0.01 and janas[0]["label"] == "JanaZQX",
      f"the renamed viewer is one column with all the hours ({[(r['label'], round(float(r['hours']), 2)) for r in janas]})")
# An import without UserId: different names stay different columns (NULL would otherwise
# merge into one in GROUP BY).
with db.connect() as conn:
    for key, name in (("import-a", "ImportAQX"), ("import-b", "ImportBQX")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds, is_active)"
            " VALUES (?, NULL, ?, 'film-1', 'KnihaTitulQX', 'Movie', 'filmy', ?, ?, ?, 600, 0, 0)",
            (key, name, at(-timedelta(hours=8)), at(-timedelta(hours=7)), at(-timedelta(hours=7))))
    conn.commit()
without_id = sorted(r["label"] for r in stats.library_activity("filmy", 30)["top_users"] if not r.get("user_id"))
check(without_id == ["ImportAQX", "ImportBQX"], f"two names without user_id are two columns ({without_id})")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key IN ('prejmenovana', 'import-a', 'import-b')")
    conn.commit()

# --- API ------------------------------------------------------------------
print()
print("--- the API: an account's key sees exactly what its owner does ---")
API_ADDRESSES = {path.replace("{item_id}", "film-1"): area for path, area in api.ADDRESS_AREAS.items()
                 if path not in ("/api/v1", "/api/v1/")}
keys = {}
for name in ("reader", "viewer"):
    account = accounts.get_by_name(name)
    keys[name] = api.create_token(f"klic-{name}", account_id=account["id"])["token"]


def api_get(token: str, address: str):
    return TestClient(web.app).get(address, headers={"Authorization": f"Bearer {token}"})


def walk_api(token: str, valid: set[str], history: bool, label: str) -> None:
    answers = {}
    for address, area in API_ADDRESSES.items():
        r = api_get(token, address)
        answers[address] = r
        may = (history if address == "/api/v1/history" else (area is None or area in valid))
        if may:
            check(r.status_code == 200, f"{label}: {address} answers ({r.status_code})")
        else:
            check(r.status_code == 403, f"{label}: {address} is closed ({r.status_code})")
    for area, marks in list(CONTENT.items()) + list(API_CONTENT.items()):
        if area in valid:
            continue
        for title, mark in marks:
            needle = mark if area in API_CONTENT and (title, mark) in API_CONTENT[area] else mark.strip('"')
            where = [a for a, r in answers.items() if r.status_code == 200 and needle in r.text]
            if title == "the playback card":
                continue      # an HTML mark - it has no business in the JSON API
            check(not where, f"{label}: {title} ({area}) is in no API answer ({where})")
    if "library_who" not in valid:
        where = [a for a in ("/api/v1/item/film-1", "/api/v1/library", "/api/v1/libraries", "/api/v1/recently-added")
                 if answers[a].status_code == 200 and NAME in answers[a].text]
        check(not where, f"{label}: the library in the API without viewers (knihovna_kdo off) ({where})")


for name in ("reader", "viewer"):
    for area in [None] + ALL_AREAS:
        enabled = set(ALL_AREAS) - ({area} if area else set())
        set_rights(name, sorted(enabled))
        # An account key's history = the account's history (see /history above).
        walk_api(keys[name], effective(enabled),
                 name == "viewer" or "library_who" in effective(enabled),
                 f"key {name} " + (f"without {area}" if area else "with everything"))

print()
print("--- the API: a root key with one area opens only that one ---")
for area in ALL_AREAS:
    enabled = {area} | ({access.BY_KEY[area].parent} - {""})
    for history in (False, True):
        token = api.create_token(f"root-{area}-{history}",
                                 permissions=api.pack_key_permissions(sorted(enabled), False, history))["token"]
        walk_api(token, effective(enabled), history,
                 f"root {'+'.join(sorted(enabled))}{' + history' if history else ''}")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
