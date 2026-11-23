# -*- coding: utf-8 -*-
r"""Seerr's cards from the registry: the /seerr page and custom dashboards, requests with a poster.

The Seerr page used to be four full-width cards. Now every question is
a card of its own in the registry (jellyscope/cards_seerr.py) and the requests show
with a poster: the pending ones as blocks (like "Právě se hraje"), the latest requests
and "Nikdo nepustil" as a strip of posters (like "Nedávno přidané"). Watched:

A) the registry: 10 cards, keys, areas ("Kdo žádá" under "…včetně kdo žádal"),
   widths, templates, translations; an unconnected Seerr is not offered;
B) the /seerr page draws the cards in the order of the sections with the same numbers the
   statistics give;
C) posters: a title from the library has the same image as "Nedávno přidané"
   (a series the series' poster), a title outside the library a substitute poster;
D) pending requests: blocks from the longest waiting; the latest requests: from
   the newest, with the state, and the same ones as a table for the dialog;
E) the requesters' names only with "…včetně kdo žádal" - without it they are not even in the data;
F) every card is drawn alone in the dashboard and carries the name Seerr;
G) Seerr does not answer: the live card says so, the detail only for the administrator, the rest goes on;
H) the whole history reaches back to the first request; a disconnected Seerr.

Seerr is not run: the answers are recorded (tests/seerr_responses/), the client
is replaced by a client with httpx.MockTransport (tests/_apps.py).

Spec: docs/superpowers/specs/2026-11-13-karty-arr-seerr-design.md

Run:
    .venv/bin/python tests/test_seerr_cards.py
"""
from __future__ import annotations

import html as html_lib
import json
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "seerr-karty.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import (  # noqa: E402
    SEERR_RESPONSES, tile, card_html, recorded_response, fill_seerr, not_responding, login, connect_seerr, seerr_client,
    seerr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, db, i18n, seerr, stats  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


REQUESTS = recorded_response(SEERR_RESPONSES, "request")["results"]


def request(number: int) -> dict:
    return next(z for z in REQUESTS if z["id"] == number)


def with_dashes(bare: str) -> str:
    return f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}"


# Jellyscope's library: the movie of request 1 (with a poster fingerprint, the id with dashes -
# Seerr sends it without them) and two episodes of the series of request 2. The movie of request 4
# is in the library too, but without a fingerprint. The other titles are not in the library.
MOVIE_ID = with_dashes(request(1)["media"]["jellyfinMediaId"])
SERIES_ID = request(2)["media"]["jellyfinMediaId"]
MOVIE4_ID = request(4)["media"]["jellyfinMediaId"]
JANA_ID = with_dashes(request(1)["requestedBy"]["jellyfinUserId"])
PETR_ID = request(4)["requestedBy"]["jellyfinUserId"]
now = db.utcnow()
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES (?, 'Janička Testová')", (JANA_ID,))
    conn.execute("INSERT INTO users (id, name) VALUES (?, 'Petr Žadatel')", (PETR_ID,))
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, image_tag, date_created, synced_at)"
                 " VALUES (?, 'The Matrix', 'Movie', '603', 'otisk1', ?, ?)", (MOVIE_ID, now, now))
    conn.execute("INSERT INTO items (id, name, type, tmdb_id, date_created, synced_at)"
                 " VALUES (?, 'Interstellar', 'Movie', '157336', ?, ?)", (MOVIE4_ID, "2026-01-01 10:00:00", now))
    for episode in ("d1", "d2"):
        conn.execute("INSERT INTO items (id, name, type, series_id, series_name, series_image_tag, date_created, synced_at)"
                     " VALUES (?, ?, 'Episode', ?, 'Dark', 'otisk-serialu', ?, ?)",
                     (episode, f"Díl {episode}", SERIES_ID, "2026-01-01 10:00:00", now))
    conn.commit()

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin_account, reader_account = accounts.get_by_name("admin"), accounts.get_by_name("reader")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
DEFAULTS = [o.key for o in access.AREAS if o.default]


def reader_rights(areas: list[str] | None, anonymise: bool = False) -> None:
    """Sets the reader's rights explicitly (None = the group's default)."""
    accounts.save_permissions(reader_account["id"], None if areas is None else access.pack_permissions(areas, anonymise))
    db.forget_settings()


print("--- A) the registry ---")
from jellyscope import cards_seerr  # noqa: E402

NAMES = ("pending", "latest", "numbers", "by_day", "when", "states", "who", "speed", "watching", "nobody")
check(tuple(cards_seerr.NAMES) == NAMES and [k.key for k in cards_seerr.CARDS] == [f"seerr_{j}" for j in NAMES],
      "10 cards seerr_<name> in the page's order")
check([j for _, names in cards_seerr.SECTIONS for j in names] == list(NAMES)
      and [i18n.translate(n, "cs") if n else "" for n, _ in cards_seerr.SECTIONS] == ["", "Žádosti v období", "Požádal a dívá se?"], "the page's sections")
check({k.key for k in cards_seerr.CARDS if k.area == "seerr_who"} == {"seerr_who"}
      and all(k.area in ("seerr", "seerr_who") and k.group == "Seerr" for k in cards_seerr.CARDS),
      "the Seerr area, Kdo žádá under an area of its own; the Seerr group")
check({k.key for k in cards_seerr.CARDS if not k.uses_period} == {"seerr_pending"}, "only the live card without a period")
check(all(k in cards.ALL_CARDS for k in cards_seerr.CARDS) and len(cards.ALL_CARDS) == 206, f"they are in the registry; there are 206 cards ({len(cards.ALL_CARDS)})")
WIDTHS = {"pending": "full", "latest": "full", "numbers": "full", "by_day": "full", "when": "half", "states": "half",
          "who": "half", "speed": "half", "watching": "full", "nobody": "full"}
CARD_NAMES = {"pending": "Čeká na vyřízení", "latest": "Poslední žádosti", "numbers": "Čísla žádostí",
              "by_day": "Žádosti po dnech", "when": "Kdy se žádá", "states": "Jak žádosti dopadly", "who": "Kdo žádá",
              "speed": "Jak rychle se vyřizují", "watching": "Požádal a dívá se?", "nobody": "Nikdo nepustil"}
check({j: k.width for j, k in zip(NAMES, cards_seerr.CARDS, strict=True)} == WIDTHS
      and {j: i18n.translate(k.name, "cs") for j, k in zip(NAMES, cards_seerr.CARDS, strict=True)} == CARD_NAMES, "widths and names by the spec")
check({k.key for k in cards_seerr.CARDS if k.has_table_window} == {"seerr_latest", "seerr_who", "seerr_nobody"},
      "cards with a table for the dialog")
missing_templates = sorted(k.template for k in cards_seerr.CARDS if not (PROJECT / "jellyscope" / "templates" / k.template).exists())
check(not missing_templates and not (PROJECT / "jellyscope" / "templates" / "_seerr_karty.html").exists(),
      f"the cards' templates exist, the old _seerr_karty.html is gone ({missing_templates})")
for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    missing = sorted({t for k in cards_seerr.CARDS for t in (k.name, k.description) if t not in dictionary}
                     | {t for t in (*cards_seerr.STATE_LABELS.values(), *cards_seerr.STATE_SHORT.values()) if t not in dictionary})
    check(not missing, f"the names, descriptions and states have a translation ({language}; missing {missing[:3]})")
check(set(cards_seerr.STATE_SHORT) == set(cards_seerr.STATE_LABELS)
      == {"available", "approved", "waiting", "removed", "declined", "failed", "deleted"}, "a state on the poster for every group of requests")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == "Seerr"], "an unconnected Seerr is not offered")

print()
print("--- B) Seerr connected: the page from the registry ---")
asked: list[str] = []
restore = substitute(seerr, "SeerrClient", seerr_client(seerr_handler(asked)))
connect_seerr()
fill_seerr()
admin, reader = login("admin"), login("reader")
s = admin.get("/seerr?days=365").text
check(re.findall(r"<!-- card: (\w+) -->", s) == [f"seerr_{j}" for j in NAMES], "the cards in the page's order")
check("<h2>Žádosti v období</h2>" in s and "<h2>Požádal a dívá se?</h2>" in s, "the sections' headings")
check(tile(s, "Žádostí") == str(len(REQUESTS)) and tile(s, "Filmy") == str(sum(z["type"] == "movie" for z in REQUESTS)),
      f"tiles: all the requests ({tile(s, 'Žádostí')})")
watching = stats.seerr_watching(365)
check(tile(s, "Vyžádané tituly v knihovně") == str(watching["available_count"]) and tile(s, "Nikdo") == str(watching["nobody"])
      and watching["available_count"] >= 2, f"Požádal a dívá se? ({watching['available_count']}, nikdo {watching['nobody']})")
check('class="heatmap"' in card_html(s, "seerr_when") and "v knihovně" in card_html(s, "seerr_states"), "the heatmap and the states in their cards")
check("tajny-klic" not in s and 'id="table-window"' in s, "the key nowhere; the page has the dialog for the whole table")
check("<h2>Seerr · " not in s and "card-group" not in s, "on its own page the cards have no prefix")
check({k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == "Seerr"} == {f"seerr_{j}" for j in NAMES},
      "a connected Seerr is offered whole")

print()
print("--- C) posters ---")
latest_html = html_lib.unescape(card_html(s, "seerr_latest"))
movie_address = f"/image/{MOVIE_ID}?kind=Primary&w=300&tag=otisk1"
check(movie_address in latest_html and movie_address in html_lib.unescape(admin.get("/").text),
      "a title from the library: the same image as Nedávno přidané (the same address = the same file)")
check(f"/image/{SERIES_ID}?kind=Primary&w=300&tag=otisk-serialu" in latest_html, "a series has the series' poster, not an episode's")
check(f'/image/{MOVIE4_ID}?kind=Primary&w=300"' in latest_html, "a movie without a fingerprint: the address without tag=")
rows = {z["request_id"]: z for z in stats.seerr_latest(365)}
check(rows[1]["poster_id"] == MOVIE_ID and rows[1]["poster_tag"] == "otisk1" and rows[2]["poster_id"] == SERIES_ID
      and rows[10]["poster_id"] == "" and rows[10]["poster_tag"] == "", "stats: poster_id and poster_tag, empty outside the library")
pending_html = card_html(s, "seerr_pending")
# A title outside the library has a provisional poster from the route /seerr/poster/… (from Radarr, Sonarr
# or Seerr - tests/test_seerr_posters.py). Whether some application has it shows only
# at loading; the substitute is ready in the frame and a script shows it (test_js_browser).
check('src="/seerr/poster/movie/872585"' in pending_html and 'src="/seerr/poster/movie/496243"' in pending_html
      and pending_html.count('data-on-error="fallback"') == 2 and pending_html.count("Plakát chybí") == 2 and "no-image" not in pending_html,
      "a title outside the library: a provisional poster and a substitute ready in case it does not load")
check(latest_html.count('data-on-error="fallback"') == len(REQUESTS) == latest_html.count("Plakát chybí")
      and latest_html.count('src="/image/') == 3 and latest_html.count('src="/seerr/poster/') == len(REQUESTS) - 3,
      "in the strip every poster has a substitute ready: three from Jellyfin, the others provisional")
check(cards_seerr.poster({"media_type": "movie", "tmdb_id": "", "poster_id": ""}) == "", "a request without a TMDB id has no address at all (the substitute straight away)")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = ?", (MOVIE4_ID,))
    conn.commit()
check(next(z for z in stats.seerr_latest(365) if z["request_id"] == 4)["poster_id"] == "", "a title that disappeared from Jellyfin has no poster from Jellyfin")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 0 WHERE id = ?", (MOVIE4_ID,))
    conn.commit()

print()
print("--- D) pending and latest requests ---")
check('class="sessions"' in pending_html and pending_html.count('class="session"') == 2 and "Čeká na vyřízení (2)" in pending_html,
      "the pending ones as blocks with a poster, with the count in the heading")
check(0 < pending_html.find("Film 872585") < pending_html.find("Film 496243"), "from the longest waiting, even though the application returns the newest first")
check(len(re.findall(r"čeká \d", pending_html)) == 2 and "film" in pending_html, "by each, how long it waits, and movie/series")
latest = stats.seerr_latest(365)
expected = [z["id"] for z in sorted(REQUESTS, key=lambda z: (z["createdAt"], z["id"]), reverse=True)]
check([z["request_id"] for z in latest] == expected and len(latest) == len(REQUESTS),
      f"the latest requests from the newest ({[z['request_id'] for z in latest]})")
check(all("id" not in z for z in latest), "the request's id is under zadost_id (anonymising takes `id` for a viewer's id)")
check({z["group"] for z in latest} >= {"waiting", "declined", "available", "approved"}, "the state of every request")
check([z["request_id"] for z in stats.seerr_latest(365, limit=3)] == expected[:3], "the limit takes the newest ones")
# Since 23. 11. 2026 the state is in the bottom badge; the top one says movie or series (as "Recently added").
badges = re.findall(r'<span class="poster-badge bottom"[^>]*>\s*([^<]+?)\s*</span>', latest_html)
check(len(badges) == len(REQUESTS) and {"čeká", "zamítnuto", "v knihovně", "schváleno"} <= set(badges), f"the state on the poster ({sorted(set(badges))})")
kinds = re.findall(r'<span class="poster-badge"[^>]*>\s*([^<]+?)\s*</span>', latest_html)
check(len(kinds) == len(REQUESTS) and set(kinds) <= {"film", "seriál"}, f"the type on the poster ({sorted(set(kinds))})")
check(0 < latest_html.find("Seriál 95396") < latest_html.find("The Matrix") and "data-table-window" in latest_html
      and 'class="modal-only"' in latest_html and latest_html.count("<tr") == len(REQUESTS) + 1,
      "the strip from the newest and the same requests as a table for the dialog")

print()
print("--- E) the requesters' names only with “…včetně kdo žádal” ---")
check("Janička Testová" in pending_html and f'href="/users/{JANA_ID}"' in pending_html and ">eva<" in s.replace(" ", "").replace("\n", ""),
      "the administrator: a requester = a Jellyscope viewer with a name and a link, the others by the name from Seerr")
check("Janička Testová" in card_html(s, "seerr_who") and "Janička Testová" in latest_html, "the names in the ranking and in the strip too")
# Seerr has its own number of rows in Settings → Interface → Long lists (not shared with the Insights nor with *Arr).
requesters = card_html(s, "seerr_who").count("<tr") - 1
db.set_setting("ui_max_rows", "50")
db.set_setting("ui_max_rows_arr", "50")
db.set_setting("ui_max_rows_seerr", "1")
db.forget_settings()
who_short = card_html(admin.get("/seerr?days=365").text, "seerr_who")
db.set_setting("ui_max_rows_seerr", "10")
db.set_setting("ui_max_rows_arr", "10")
db.set_setting("ui_max_rows", "10")
db.forget_settings()
check(requesters >= 2 and who_short.count('class="extra-row"') == requesters - 1 and "zobrazit celou tabulku" in who_short,
      f"Seerr's own limit: one requester straight away, the others in the dialog ({who_short.count('extra-row')} of {requesters})")
reader_rights(DEFAULTS)
reader_account = accounts.get_by_name("reader")
check("seerr" in DEFAULTS and "seerr_who" not in DEFAULTS, "the Seerr area is on by default, “…kdo žádal” off")
data = cards.data_for(cards.allowed(list(cards_seerr.CARDS), reader_account), 365, account=reader_account)
data_text = json.dumps(data, default=str, ensure_ascii=False)
check("user_name" not in data_text and "user_id" not in data_text and "Janička" not in data_text and "seerr_who" not in data,
      "without the right the requesters' names and ids are not in the data and the Kdo žádá card is not computed")
s_reader = reader.get("/seerr?days=365").text
check(reader.get("/seerr").status_code == 200 and "<!-- card: seerr_who -->" not in s_reader
      and len(re.findall(r"<!-- card: (\w+) -->", s_reader)) == 9, "a reader sees the page, without the Kdo žádá card")
check(not any(j in s_reader for j in ("Janička Testová", "Petr Žadatel", ">eva<", "/users/")), "and without a single requester's name")
cards.save_layout(["seerr_who", "seerr_latest"])
reader_rights([*DEFAULTS, "overview", "dashboard"])      # the dashboard for everybody is an area of its own
p_reader = reader.get("/dashboard?days=365").text
check("<!-- card: seerr_who -->" not in p_reader and "<!-- card: seerr_latest -->" in p_reader and "Janička" not in p_reader,
      "the same in the dashboard for everybody")
check("Janička Testová" in card_html(admin.get("/dashboard?days=365").text, "seerr_who"), "the administrator has the names there")
reader_rights([*DEFAULTS, "seerr_who", "users"], anonymise=True)
s_anonymous = reader.get("/seerr?days=365").text
check("<!-- card: seerr_who -->" in s_anonymous and not any(j in s_anonymous for j in ("Janička Testová", "Petr Žadatel", ">eva<"))
      and 'href="/users/skryto-?' not in s_anonymous, "with the right and anonymising: the card is there, the names hidden, no dead link")
reader_rights(None)

print()
print("--- E2) the help with posters only for the administrator and only with “Enable Image Caching” off ---")
HINT = "Zapni v Seerru Enable Image Caching"
db.set_setting("seerr_images", "0")
db.forget_settings()
s_off = admin.get("/seerr?days=365").text
check(HINT in card_html(s_off, "seerr_pending") and HINT in card_html(s_off, "seerr_latest")
      and 'href="http://seerr.test/settings/main"' in card_html(s_off, "seerr_pending"),
      "the administrator sees the reason and a link to Seerr's settings under the cards with provisional posters")
check(HINT not in card_html(s_off, "seerr_nobody"), "by the card where all the posters are from Jellyfin there is no help")
# Review (13. 11. 2026): a title from "Nikdo nepustil" may be missing in Jellyscope's library even though Seerr
# reports it as available (it disappeared from Jellyfin, or Jellyscope has not scanned it yet). The card then draws
# a provisional poster - and with Seerr's images off a substitute that it told the administrator nothing about.
HINT_NOBODY = "Titul, který Jellyscope v knihovně nenašel"
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE series_id = ?", (SERIES_ID,))
    conn.commit()
nobody_html = html_lib.unescape(card_html(admin.get("/seerr?days=365").text, "seerr_nobody"))
check('src="/seerr/poster/tv/' in nobody_html and HINT_NOBODY in nobody_html and HINT in nobody_html
      and 'href="http://seerr.test/settings/main"' in nobody_html and "Plakát čekající nebo zamítnuté" not in nobody_html,
      "“Nikdo nepustil” with a provisional poster: the administrator sees the reason (with its own sentence) and a link to Seerr's settings")
check(HINT_NOBODY not in reader.get("/seerr?days=365").text, "... the others not")
cards.save_layout(["seerr_nobody", "seerr_latest"])
dashboard_html = html_lib.unescape(admin.get("/dashboard?days=365").text)
check(HINT_NOBODY in card_html(dashboard_html, "seerr_nobody") and HINT_NOBODY not in card_html(dashboard_html, "seerr_latest")
      and "Plakát čekající nebo zamítnuté" in card_html(dashboard_html, "seerr_latest"),
      "in the dashboard every card has its own sentence (one's sentence does not overflow into the next)")
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 0 WHERE series_id = ?", (SERIES_ID,))
    conn.commit()
s_other = reader.get("/seerr?days=365").text
check("Enable Image Caching" not in s_other and "seerr.test" not in s_other and "Plakát chybí" in s_other,
      "the others only a substitute poster: without the reason and without Seerr's address")
data = cards.data_for(cards.allowed(list(cards_seerr.CARDS), reader_account), 365, account=reader_account)
check("seerr.test" not in json.dumps(data, default=str), "Seerr's address is not even in the cards' data for a reader")
cards.save_layout(["seerr_latest"])
check(HINT in card_html(admin.get("/dashboard?days=365").text, "seerr_latest"), "the help in the dashboard too")
for images_state in ("1", ""):
    db.set_setting("seerr_images", images_state)
    db.forget_settings()
    check("Enable Image Caching" not in admin.get("/seerr?days=365").text, f"state {images_state!r}: no help")

print()
print("--- F) every card alone in the dashboard ---")
for card in cards_seerr.CARDS:
    cards.save_layout([card.key])
    r = admin.get("/dashboard?days=365")
    check(r.status_code == 200 and "Seerr · " in card_html(r.text, card.key), f"{card.key} alone in the dashboard, with the name Seerr")
cards.save_layout([k.key for k in cards_seerr.CARDS])
everything = admin.get("/dashboard?days=365")
check(everything.status_code == 200 and len(re.findall(r"<!-- card: seerr_\w+ -->", everything.text)) == 10, "all 10 cards at once")
db_queries: list[str] = []
original_query_all = db.query_all


def _eavesdrop(sql, *a, **k):
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


db.query_all = _eavesdrop
try:
    admin.get("/seerr?days=365")
finally:
    db.query_all = original_query_all
count = sum("FROM seerr_request WHERE created_at >= ?" in q for q in db_queries)
check(count == 1, f"the page selects the period's requests once ({count}×)")

print()
print("--- G) Seerr does not answer ---")
restore()
restore = substitute(seerr, "SeerrClient", seerr_client(not_responding()))
seerr.live.CACHE.clear()
cards.save_layout(["seerr_pending", "seerr_numbers", "seerr_latest"])
r = admin.get("/dashboard?days=365")
check(r.status_code == 200 and "Seerr neodpovídá" in card_html(r.text, "seerr_pending")
      and tile(card_html(r.text, "seerr_numbers"), "Žádostí") == str(len(REQUESTS)) and "The Matrix" in card_html(r.text, "seerr_latest"),
      "the live card says so, the cards from the database go on")
check("Connection refused" in r.text and "tajny-klic" not in r.text, "the administrator sees the error's detail, the key nowhere")
s_reader = reader.get("/seerr?days=365").text
check("Seerr neodpovídá" in s_reader and "Connection refused" not in s_reader and tile(s_reader, "Žádostí") == str(len(REQUESTS)),
      "whoever is not the administrator sees only the sentence that Seerr does not answer - and the statistics from the database")
restore()
restore = substitute(seerr, "SeerrClient", seerr_client(seerr_handler(asked)))
seerr.live.CACHE.clear()

print()
print("--- H) the whole history, a disconnected Seerr ---")
with db.connect() as conn:
    conn.execute("INSERT INTO seerr_request (id, media_type, tmdb_id, title, status, requester_name, created_at)"
                 " VALUES (999, 'movie', '1', 'Stara zadost', 3, 'host', '2025-01-15 10:00:00')")
    conn.commit()
check(tile(admin.get("/seerr?days=0").text, "Žádostí") == str(len(REQUESTS) + 1), "the whole history on the page from the first request")
cards.save_layout(["seerr_latest"])
check("Stara zadost" in card_html(admin.get("/dashboard?days=0").text, "seerr_latest"), "the whole history in the dashboard from the first request")
admin.get("/seerr?days=365")
seerr.save_settings(False, "http://seerr.test", "")
db.forget_settings()
check(admin.get("/seerr").status_code == 404 and "<!-- card: seerr_latest -->" not in reader.get("/dashboard").text
      and [k.key for k in cards.load_layout()] == ["seerr_latest"], "a disconnected Seerr: the page is not there, the card is not drawn, the layout stays")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
