# -*- coding: utf-8 -*-
r"""Lidarr: cards from the registry - the /arr/lidarr tab and custom dashboards.

Every question is a card of its own in the registry (jellyscope/cards_lidarr.py)
and the tab and the dashboards draw it the same way. The unit is the album (the tracks of one
download are one import). Watched:

A) the registry: 23 cards, keys `arr_lidarr_<name>`, area, group, widths, templates, translations;
   an unconnected Lidarr is not offered;
B) the /arr/lidarr tab draws exactly Lidarr's cards in the order of the sections, with the sections'
   headings and with the same numbers the statistics give; an album that is in the music library is a link to
   its page, its artist to the artist's, an album that is not has a text only (Review Focus 3);
C) every card is drawn alone in the dashboard too and carries the application's name;
E) the period's events are selected once (with a custom range too) and "the whole history" reaches
   back to Lidarr's first event; the live state for an account is put together once per computation;
F) long tables follow Settings → Interface → Long lists;
G) rights: texts from the application only for the administrator, without the *Arr area no card and no
   page (403, nothing in the menu), without the Library area no link to an album, without the Music area none
   to an artist - the name stays as a text (Review Focus 5);
H) live data: without warnings, a partial failure, an unavailable Lidarr, a disconnected Lidarr.

What concerns more applications at once (Lidarr and Sonarr in one dashboard, a dashboard saved
before the split) is watched by tests/test_arr_registry.py.

Lidarr is not run: the answers are built by hand in the shape of the API v1 (tests/lidarr_responses/),
the client is replaced by a client with httpx.MockTransport (tests/_apps.py).

Run:
    .venv/bin/python tests/test_lidarr_cards.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "lidarr-cards.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    LIDARR_RESPONSES, card_html, connect_lidarr, fill_lidarr, lidarr_client, lidarr_handler, login, no_network, not_responding,
    recorded_response, tile,
)
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, cards_lidarr, cards_music, db, i18n, stats  # noqa: E402
from jellyscope.arr import lidarr  # noqa: E402
from jellyscope.arr.lidarr import progress  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # no application other than Lidarr is to ask here


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin_account, reader_account = accounts.get_by_name("admin"), accounts.get_by_name("reader")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()

print("--- A) the registry ---")
CARDS = cards_lidarr.CARDS
NAMES = [j for _, names in cards_lidarr.SECTIONS for j in names]
check(len(NAMES) == 23 and len(set(NAMES)) == 23, f"23 card names ({len(NAMES)})")
check([i18n.translate(n, "cs") if n else "" for n, _ in cards_lidarr.SECTIONS] == ["Teď v aplikaci", "Co přibylo", "Rychlost", "Zdroje a kvalita", "Upgrady"],
      "five sections in the page's order")
check(len(CARDS) == 23 and len({k.key for k in CARDS}) == 23, "23 cards, the keys different")
check([k.key for k in CARDS] == [f"arr_lidarr_{j}" for j in NAMES] and cards_lidarr.card_key("queue") == "arr_lidarr_queue",
      "keys arr_lidarr_<name>, in the order of the sections")
check("resolutions" not in NAMES, "music has no resolutions card")
check(all(k.area == "arr" and k.group == "Lidarr" for k in CARDS), "the *Arr area and the Lidarr group")
check(all(k in cards.ALL_CARDS for k in CARDS), "all are in the registry (cards.ALL_CARDS)")
live_keys = {cards_lidarr.card_key(j) for j in ("live_numbers", "queue", "calendar", "health")}
check({k.key for k in CARDS if not k.uses_period} == live_keys | {"arr_lidarr_downloading"}, "only the live cards without a period")
WIDTHS = {"live_numbers": "full", "downloading": "full", "queue": "half", "calendar": "half", "health": "full", "numbers": "full", "by_day": "full",
          "when": "half", "best_days": "half", "largest": "full", "downloads": "half", "after_release": "half",
          "indexer_reliability": "full", "indexers": "third", "groups": "third", "clients": "third", "protocols": "third", "qualities": "third",
          "upgrades": "full", "how_all": "half", "how_upgrades": "half", "transitions": "half", "upgraded": "half"}
wrong = [(k.key, k.width) for j, k in zip(NAMES, CARDS, strict=True) if k.width != WIDTHS[j]]
check(not wrong, f"the widths by the spec ({wrong[:3]})")
with_table = {"largest", "indexer_reliability", "indexers", "groups", "clients", "protocols", "qualities", "transitions", "upgraded",
              "queue", "calendar", "health"}   # the live lists too (23. 11. 2026)
check({j for j, k in zip(NAMES, CARDS, strict=True) if k.has_table_window} == with_table,
      "cards with a long table want the dialog for the whole table")
CARD_NAMES = {"live_numbers": "Čísla teď", "downloading": "Stahuje se", "queue": "Fronta s chybou nebo varováním", "calendar": "Vyjde v příštích 7 dnech",
              "health": "Varování aplikace", "numbers": "Čísla období", "by_day": "Přírůstky po dnech",
              "when": "Kdy se převádí do knihovny", "best_days": "Nejlepší dny", "largest": "Největší převzatá alba",
              "downloads": "Jak dlouho se stahuje", "after_release": "Jak brzy po vydání", "indexer_reliability": "Spolehlivost indexerů", "indexers": "Indexery",
              "groups": "Release skupiny", "clients": "Klienti stahování", "protocols": "Usenet a torrent",
              "qualities": "Kvalita převzatých alb", "upgrades": "Čísla upgradů",
              "how_all": "Jak se stahovalo všechno", "how_upgrades": "Jak se stahovaly upgrady",
              "transitions": "Z jaké kvality na jakou", "upgraded": "Nejčastěji upgradovaná alba"}
check({j: i18n.translate(k.name, "cs") for j, k in zip(NAMES, CARDS, strict=True)} == CARD_NAMES, "the cards' names by the spec")
TEMPLATES = PROJECT / "jellyscope" / "templates"
missing_templates = sorted({k.template for k in CARDS if not (TEMPLATES / k.template).exists()})
check(not missing_templates and all(k.template.startswith("_card_lidarr_") for k in CARDS),
      f"the cards' templates exist and are Lidarr's ({missing_templates})")
for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    missing = sorted({t for k in CARDS for t in (k.name, k.description) if t not in dictionary})
    check(not missing, f"the cards' names and descriptions have a translation ({language}; missing {missing[:3]})")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.area == "arr"],
      "an unconnected Lidarr is not offered")

print()
print("--- B) Lidarr connected: the tab from the registry ---")
ALBUMS = recorded_response(LIDARR_RESPONSES, "album")
ARTISTS = recorded_response(LIDARR_RESPONSES, "artist")
NOW = db.utcnow()
# The music library: "Hvězdy" pairs by the MusicBrainz id of its release (with a cover), "Šípková Růžena" by its name and its
# artist (without a cover); "Blue Hour" and the rest are not in the library at all (Review Focus 3).
with db.connect() as conn:
    conn.execute("INSERT INTO music_artist (id, name, musicbrainz_id, synced_at, missing) VALUES ('jf-kryst', 'Kryštof', ?, ?, 0)",
                 (ARTISTS[0]["foreignArtistId"], NOW))
    conn.execute("INSERT INTO music_album (id, name, artist_id, artist, musicbrainz_id, cover_tag, missing, synced_at) VALUES"
                 " ('jf-stars', 'Hvězdy (CZ)', 'jf-kryst', 'Kryštof', ?, 'tag1', 0, ?)", (ALBUMS[0]["releases"][0]["foreignReleaseId"], NOW))
    conn.execute("INSERT INTO music_album (id, name, artist_id, artist, musicbrainz_id, cover_tag, missing, synced_at) VALUES"
                 " ('jf-sipkova', 'Šípková Růžena', 'jf-kryst', 'Kryštof', '', '', 0, ?)", (NOW,))
    conn.commit()
asked: list[str] = []
# A dict, so the handler can be swapped while running (part does not load, Lidarr does not answer).
handlers = {"lidarr": lidarr_handler(asked)}
restore = substitute(lidarr, "LidarrClient", lidarr_client(lambda request: handlers["lidarr"](request)))
connect_lidarr()
fill_lidarr()
admin, reader = login("admin"), login("reader")

s = admin.get("/arr/lidarr?days=365").text
order = re.findall(r"<!-- card: (\w+) -->", s)
check(order == [cards_lidarr.card_key(j) for j in NAMES], f"23 Lidarr cards in the order of the sections ({len(order)})")
check(all(f"<h2>{i18n.translate(n, 'cs')}</h2>" in s for n, _ in cards_lidarr.SECTIONS), "the sections' headings (the page is Czech)")
additions = stats.lidarr_additions(365)
total = sum(r["count"] for r in additions)
check(tile(s, "Alba převzatá do knihovny") == str(total) == "4", f"Alba převzatá do knihovny = the sum of the additions, four albums ({tile(s, 'Alba převzatá do knihovny')})")
numbers = card_html(s, "arr_lidarr_numbers")
# 475 000 000 B = 452.9 MiB, the filter `bytes` counts in binary units.
check("10 skladeb" in numbers and "453 MB" in numbers, "the tile says how many tracks the albums have (10) and their size (453 MB)")
check(tile(s, "Upgrady") == str(sum(r["upgrade_count"] for r in additions)) == "1"
      and tile(s, "Selhaná stažení v období") == str(stats.lidarr_failures(365)) == "1", "Upgrady and Selhaná stažení")
check(tile(s, "Upgradů na album") == "1,0" and tile(s, "Upgradovaná alba") == "1",
      f"Review Focus 1: three replaced tracks of one album = one upgrade, per album 1,0 ({tile(s, 'Upgradů na album')})")
check("<h2>Lidarr · " not in s and "card-group" not in s, "on its own tab the cards have no application prefix")
check('class="heatmap"' in card_html(s, "arr_lidarr_when") and "arr-test" in card_html(s, "arr_lidarr_indexers"),
      "the heatmap and the indexer are in their cards")
check("Has missing tracks" in card_html(s, "arr_lidarr_queue") and "Download failed" in card_html(s, "arr_lidarr_queue")
      and "Indexers unavailable due to failures" in card_html(s, "arr_lidarr_health"),
      "the administrator: the queue's messages and the application's warning in their cards")
check("ruční výběr" in card_html(s, "arr_lidarr_how_all"), "how it was downloaded, in Czech")
# Indexer reliability over the recorded history: every grab has a download_id - arr-test brought three albums,
# the tracker failed once, and that comes first.
reliability = card_html(s, "arr_lidarr_indexer_reliability")
reliability_rows = [(re.search(r'<td class="strong[^"]*">([^<]*)', tr).group(1).strip(), re.findall(r'<td class="num[^"]*">([^<]*)</td>', tr))
                    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", reliability, re.S) if "<td" in tr]
check(reliability_rows == [("tracker", ["1", "0", "1", "0", "0,0 %"]), ("arr-test", ["3", "3", "0", "0", "100,0 %"])],
      f"Spolehlivost indexerů: downloads, imported, failed, cannot be matched, success rate ({reliability_rows})")
check("<th class=\"num\">Stahování</th>" in reliability and "několika alby je jedno stahování" in reliability,
      "the first column counts downloads and the note says one release is one download")
check("ne dostupnost jeho API" in reliability and "nelze spárovat" not in reliability,
      "the card says it is the releases' reliability, not the API's availability; everything matched, no sentence about it")
rates = [cards_lidarr.success_rate(share) for share in (None, 1.0, 0.9996, 2 / 3, 0.0)]
check(rates == ["–", "100,0 %", "99,9 %", "66,6 %", "0,0 %"],
      f"the success rate with one decimal, rounded down: one failure in thousands is not 100 % ({rates})")
check("Resolution" not in s and "Rozlišení" not in s, "no resolutions on a music page")
# "Jak brzy po vydání": all albums of the test came out years before their import (a back catalogue) or are upgrades.
after_release = card_html(s, "arr_lidarr_after_release")
check("Nepočítají se 3 převzatá alba" in after_release and "interpret přibyl do aplikace až po vydání" in after_release
      and "jen to, na co se čekalo" in after_release and "neví" not in after_release,
      "Jak brzy po vydání: albums of artists added after the release are not counted and the card says how many")
# The health pill carries the message's type (it once read a key health rows do not have and stayed empty).
check('<span class="badge warning">warning</span>' in card_html(s, "arr_lidarr_health"), "the health pill shows the type of the warning")
check("tajny-klic" not in s, "the key is not in the page")
check('id="table-window"' in s, "the tab has the dialog for the whole table")
check({k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.area == "arr"} == {k.key for k in CARDS},
      "exactly Lidarr's cards are offered")
check(admin.get("/arr/radarr").status_code == 404, "an application that is not connected has no tab")

print()
print("--- B2) links into the music library (Review Focus 3) ---")
largest = card_html(s, "arr_lidarr_largest")
check('href="/album/jf-stars"' in largest and 'href="/album/jf-sipkova"' in largest and 'href="/music/artist/jf-kryst"' in largest,
      "an album in the library (by the MusicBrainz id, by the name) is a link to /album/<id>, its artist to /music/artist/<id>")
check("Blue Hour" in largest and not re.search(r"<a [^>]*>\s*Blue Hour", largest) and not re.search(r"<a [^>]*>\s*Nightingale Quartet", largest),
      "an album and an artist that are not in the library are a text without a link")
check(">Hvězdy<" in largest and "Hvězdy (CZ)" not in largest, "the row carries Lidarr's name of the album, the link only leads to the library's page")
check('href="/album/jf-stars"' in card_html(s, "arr_lidarr_upgraded") and 'href="/music/artist/jf-kryst"' in card_html(s, "arr_lidarr_upgraded"),
      "most upgraded albums link the same way")
check('href="/album/jf-stars"' in card_html(s, "arr_lidarr_queue"), "the queue links the album too")
download = card_html(s, "arr_lidarr_downloading")
check(f"/image/jf-stars?kind=Primary&amp;w={cards_music.COVER_WIDTH}&amp;tag=tag1" in download and 'href="/album/jf-stars"' in download,
      "Downloading: an album from the library has the cover of Jellyfin (the helper of the music cards) and links to its page")
check("/arr/lidarr/poster/31" in download and "/arr/lidarr/poster/22" in download and "/arr/lidarr/poster/12" in download,
      "an album with no cover in the library or outside it has the provisional cover of Lidarr")
check("modal-downloading-lidarr-22" in s and "2 stahování" in download, "an album with two downloads has a window with the breakdown")
rec = {**next(r for r in json.loads((LIDARR_RESPONSES / "queue.json").read_text(encoding="utf-8"))["records"] if r.get("albumId") == 31)}
shared = cards_lidarr.downloading({"available": True, "error": "", "more_count": 0, "incomplete": False, "live": True, "refresh": 5, "blocks": progress.blocks(
    [{**rec, "downloadId": "D", "albumId": a, "album": {"id": a, "title": f"Album {a}"}} for a in (71, 72)] + [{**rec, "downloadId": "E", "albumId": 73}])}, False)
note = i18n.translate_plural("part of a download of {n} albums", 2, i18n.current_language())
check([b["shared_text"] for b in shared["blocks"]] == [note, note, ""] and "2" in note,
      f"a block that is a part of a download of several albums says so ({[b['shared_text'] for b in shared['blocks']]})")
check(not re.search(r'href="/album/[^"]*"[^>]*>\s*Blue Hour', download) and "Blue Hour" in download, "an album outside the library has no link in the downloading card either")

print()
print("--- C) every card alone in the dashboard ---")
for card in CARDS:
    cards.save_layout([card.key])
    r = admin.get("/dashboard?days=365")
    piece = card_html(r.text, card.key)
    check(r.status_code == 200 and "Lidarr · " in piece, f"{card.key} alone in the dashboard, with the application's name")
cards.save_layout(["arr_lidarr_numbers"])
check("modal-period" in admin.get("/dashboard").text, "a period card wants the period switch")
cards.save_layout(["arr_lidarr_queue"])
check("modal-period" not in admin.get("/dashboard").text, "a live card not")
cards.save_layout([k.key for k in CARDS])
everything = admin.get("/dashboard?days=365")
check(everything.status_code == 200 and len(re.findall(r"<!-- card: arr_lidarr_\w+ -->", everything.text)) == 23, "all 23 cards at once in one dashboard")

print()
print("--- E) the period's events once, the whole history from the first event ---")
db_queries: list[str] = []
original_query_all = db.query_all


def _eavesdrop(sql, *a, **k):
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


def event_selections(action) -> int:
    """How many times `action` selected the period's events (the `_analysis` query of Lidarr's statistics)."""
    db_queries.clear()
    db.query_all = _eavesdrop
    try:
        action()
    finally:
        db.query_all = original_query_all
    return sum("FROM lidarr_event" in q and "ORDER BY happened_at, arr_id" in q for q in db_queries)


count = event_selections(lambda: admin.get("/arr/lidarr?days=365"))
check(count == 1, f"the tab selects the period's events once ({count}×)")
count = event_selections(lambda: admin.get("/arr/lidarr?since=2026-11-01&until=2026-11-13"))
check(count == 1, f"with a custom date range too ({count}×)")
check(tile(admin.get("/arr/lidarr?since=2026-11-01&until=2026-11-13").text, "Alba převzatá do knihovny") == "1"
      and tile(admin.get("/arr/lidarr?since=2026-10-01&until=2026-10-31").text, "Alba převzatá do knihovny") == "0",
      "a custom range counts only its days (the first album is from 12. 11.)")
count = event_selections(lambda: admin.get("/dashboard?days=365"))
check(count == 1, f"a dashboard with all of Lidarr's cards: once too ({count}×)")
with db.connect() as conn:
    conn.execute("INSERT INTO lidarr_event (arr_id, event_type, happened_at, title, artist, item_id, title_id, quality, size_bytes, download_id)"
                 " VALUES (900001, 'trackFileImported', '2025-01-15 10:00:00', 'Stary album', 'Stary interpret', 900001, 900001, 'MP3-320', 1000, 'old')")
    conn.commit()
check("Stary album" in card_html(admin.get("/arr/lidarr?days=0").text, "arr_lidarr_largest"),
      "the whole history on the tab reaches back to Lidarr's first event, not to the first play")
count = event_selections(lambda: admin.get("/arr/lidarr?days=0"))
check(count == 1, f"the whole history selects the events once too ({count}×)")
cards.save_layout(["arr_lidarr_largest"])
check("Stary album" in card_html(admin.get("/dashboard?days=0").text, "arr_lidarr_largest"),
      "the whole history in the dashboard: from Lidarr's first event too")
admin.get("/arr/lidarr?days=365")   # the remembered period back

# The four live cards of one application put together the live state for an account once - one lookup of the queue's and
# the calendar's albums in the library for each, not one per card.
lookups: list[str] = []
original_library = stats.lidarr_library
restore_library = substitute(stats, "lidarr_library", lambda ids=None: (lookups.append("lidarr"), original_library(ids))[1])
lidarr.live.CACHE.clear()
computed = cards.data_for([k for k in cards.ALL_CARDS if k.key in live_keys], 30, account=admin_account)
restore_library()
check(all(computed[key]["available"] for key in live_keys) and len(lookups) == 2,
      f"the four live cards of Lidarr look the albums up in the library once - the queue and the calendar ({len(lookups)}×)")

print()
print("--- F) long tables by Settings -> Interface ---")
db.set_setting("ui_max_rows_arr", "3")
db.forget_settings()
s = admin.get("/arr/lidarr?days=365").text
largest = card_html(s, "arr_lidarr_largest")
check('class="extra-row"' in largest and "zobrazit celou tabulku" in largest, "a table over the limit: extra rows and a button to the dialog")
all_rows = len(stats.lidarr_largest(365, limit=100))
check(largest.count("<tr") - 1 == min(all_rows, 100) and all_rows > 3, "the card has all the rows (the hidden ones wait for the dialog), not a fixed ten")
db.set_setting("ui_max_rows", "50")
db.set_setting("ui_max_rows_arr", "2")
db.set_setting("ui_max_rows_arr_mobile", "1")
db.forget_settings()
largest = card_html(admin.get("/arr/lidarr?days=365").text, "arr_lidarr_largest")
check(largest.count('class="extra-row"') == all_rows - 2 and largest.count('class="desktop-only"') == 1 and "zobrazit celou tabulku" in largest,
      f"the *Arr limit of its own: 2 rows straight away (1 on a phone), even though the Insights have 50 ({largest.count('extra-row')} hidden of {all_rows})")
db.set_setting("ui_max_rows_arr", "1")
db.forget_settings()
tab = admin.get("/arr/lidarr?days=365").text
tables = {j: card_html(tab, f"arr_lidarr_{j}") for j in sorted(with_table)}
card_rows = {j: (h.count("<tr") - h.count("<thead"), h.count('class="extra-row"')) for j, h in tables.items() if "<table" in h}
long_ones = {j: r for j, r in card_rows.items() if r[0] >= 2}
check(len(long_ones) >= 4 and all(extra == rows - 1 for rows, extra in long_ones.values()),
      f"the *Arr limit holds for all of Lidarr's tables ({card_rows})")
template_texts = {t.name: t.read_text(encoding="utf-8") for t in TEMPLATES.glob("_card_lidarr_*.html")}
with_rows = {name: text for name, text in template_texts.items() if "{{ row_class(" in text}
wrong_templates = sorted(name for name, text in with_rows.items()
                         if 'row_class(loop.index, "arr")' not in text or ', "arr") }}' not in text)
check(not wrong_templates and len(with_rows) >= 4, f"Lidarr's table templates tell the macros whose limit holds ({len(with_rows)} templates, wrong {wrong_templates})")
db.set_setting("ui_max_rows", "10")
db.set_setting("ui_max_rows_arr", "10")
db.set_setting("ui_max_rows_arr_mobile", "5")
db.forget_settings()

print()
print("--- G) rights ---")
s_reader = reader.get("/arr/lidarr?days=365").text
check(reader.get("/arr/lidarr").status_code == 200 and len(re.findall(r"<!-- card: (\w+) -->", s_reader)) == 23,
      "a reader sees the tab and the cards (the area is on by default)")
check("Has missing tracks" not in s_reader and "Download failed" not in s_reader and "Indexers unavailable due to failures" not in s_reader,
      "whoever is not the administrator sees neither the application's warning texts nor the queue's messages")
check("Aplikace hlásí varování" in card_html(s_reader, "arr_lidarr_health") and "Hvězdy" in card_html(s_reader, "arr_lidarr_queue"),
      "... but sees that the application reports something, and the albums in the queue")
cards.save_layout(["arr_lidarr_health", "arr_lidarr_queue"])
p_reader = reader.get("/dashboard").text
check("Indexers unavailable" not in p_reader and "Has missing tracks" not in p_reader and "Aplikace hlásí varování" in p_reader,
      "the same in the dashboard: the card is computed for the account that reads it")
check("Indexers unavailable" in admin.get("/dashboard").text, "and the administrator has the texts there")
# An unknown time of adding (the list of albums knows nothing of the artist) has its own sentence.
with db.connect() as conn:
    conn.execute("UPDATE lidarr_title SET added_at = NULL")
    conn.commit()
without_time = card_html(admin.get("/arr/lidarr?days=365").text, "arr_lidarr_after_release")
check("neví, kdy interpret do aplikace přibyl" in without_time and "až po vydání." not in without_time.split("neví, kdy")[0][-120:],
      "Jak brzy po vydání: an unknown time of adding has its own sentence, not “až po vydání”")
# Review Focus 5: an account without the Music area has the artists as text, without the Library area the albums as text.
EVERYTHING = [a for a in access.BY_KEY if a not in ("music", "library")]


def links_for(areas: list[str]) -> str:
    accounts.save_permissions(reader_account["id"], access.pack_permissions(areas, False))
    return card_html(reader.get("/arr/lidarr?days=365").text, "arr_lidarr_largest")


without_music = links_for([*EVERYTHING, "library"])
check('href="/album/jf-stars"' in without_music and "/music/artist/" not in without_music and ">Kryštof<" in without_music.replace(" ", ""),
      "without the Music area the albums still link, the artist is a text (no dead link)")
without_library = links_for([*EVERYTHING, "music"])
check("/album/" not in without_library and 'href="/music/artist/jf-kryst"' in without_library and "Hvězdy" in without_library,
      "without the Library area the albums are a text, the artist still links")
neither = links_for(EVERYTHING)
check("/album/" not in neither and "/music/artist/" not in neither and "Hvězdy" in neither and "Kryštof" in neither,
      "without both the names stay and nothing links")
# Without the *Arr area: no page, nothing in the menu, no card offered or computed.
accounts.save_permissions(reader_account["id"], access.pack_permissions(["library", "overview", "dashboard"], False))
reader_without = accounts.get_by_name("reader")
check(reader.get("/arr/lidarr").status_code == 403 and 'href="/arr/lidarr"' not in reader.get("/account").text,
      "without the *Arr area /arr/lidarr is 403 and there is no link to it in the menu")
check(cards.allowed(list(CARDS), reader_without) == [], "without the *Arr area no card")
cards.save_layout(["arr_lidarr_numbers", "codecs"])
count = event_selections(lambda: reader.get("/dashboard"))
without = reader.get("/dashboard").text
check("arr_lidarr_numbers" not in without and "Alba převzatá do knihovny" not in without and "<!-- card: codecs -->" in without and count == 0,
      "the dashboard for everybody neither draws nor computes Lidarr's card for them")
cards.save_layout(["arr_lidarr_numbers"], reader_account["id"])
mine = reader.get("/account/dashboard").text
check("Nemáš oprávnění k oblasti" in mine and "Alba převzatá do knihovny" not in mine, "My dashboard: a message about the permission instead of the card")
accounts.save_permissions(reader_account["id"], None)
cards.save_layout([], reader_account["id"])

print()
print("--- H) live data: without warnings, part does not load, Lidarr does not answer, disconnected ---")
cards.save_layout([])
handlers["lidarr"] = lidarr_handler(asked, instead={"health": httpx.Response(200, json=[])})
lidarr.live.CACHE.clear()
without_warning = admin.get("/arr/lidarr?days=365").text
check("Aplikace nehlásí žádné varování." in card_html(without_warning, "arr_lidarr_health"), "the health card without warnings says so")
check(tile(card_html(without_warning, "arr_lidarr_live_numbers"), "Varování aplikace") == "0", "and the tile shows zero")

error500 = httpx.Response(500)
handlers["lidarr"] = lidarr_handler(asked, instead={"queue": error500, "calendar": error500})
lidarr.live.CACHE.clear()
s = admin.get("/arr/lidarr?days=365").text
# Missing and cutoff come from the *Arr task (23. 11. 2026, none ran here); a live part that loaded is health.
check(tile(s, "Ve frontě") == "–" and tile(s, "Varování aplikace") not in (None, "–"), "what did not load is a dash, not zero")
check("nepodařilo načíst" in card_html(s, "arr_lidarr_live_numbers"), "the card with the numbers says part did not load")
check("Frontu se z aplikace nepodařilo načíst." in card_html(s, "arr_lidarr_queue")
      and "Kalendář se z aplikace nepodařilo načíst." in card_html(s, "arr_lidarr_calendar")
      and "V příštích 7 dnech nic nevychází" not in s and "Ve frontě není nic s chybou" not in s,
      "the queue and the calendar do not say “nothing” when it could not be found out")

# Lidarr unavailable, the dashboard has its live cards and a card from the database.
attempts: list[str] = []
handlers["lidarr"] = not_responding(attempts)
lidarr.live.CACHE.clear()
live_names = ("live_numbers", "queue", "calendar", "health")
cards.save_layout([cards_lidarr.card_key(j) for j in (*live_names, "largest")])
r = admin.get("/dashboard?days=365")
check(r.status_code == 200 and all("Aplikace neodpovídá" in card_html(r.text, cards_lidarr.card_key(j)) for j in live_names),
      "Lidarr unavailable: the dashboard is drawn and every live card says so")
check("Hvězdy" in card_html(r.text, "arr_lidarr_largest"), "the card from the database goes on")
check(attempts.count("queue") == 1, f"four live cards = one request for the live state ({attempts.count('queue')})")
check(tile(card_html(r.text, "arr_lidarr_live_numbers"), "Ve frontě") == "–", "the tiles of the live numbers are dashes")
check("Connection refused" in card_html(r.text, "arr_lidarr_live_numbers") and "tajny-klic" not in r.text,
      "the administrator sees the error's detail, the key nowhere")
s_reader = reader.get("/arr/lidarr?days=365").text
check("Aplikace neodpovídá" in s_reader and "Connection refused" not in s_reader and "Hvězdy" in s_reader,
      "whoever is not the administrator sees only the sentence that the application does not answer - and the statistics from the database")

# The administrator disconnects Lidarr: the layout stays, the card is neither drawn nor computed.
lidarr.save_settings(False, "http://lidarr.test", "")
db.forget_settings()
cards.save_layout(["arr_lidarr_numbers", "codecs"])
count = event_selections(lambda: admin.get("/dashboard?days=365"))
after = reader.get("/dashboard?days=365").text
check("<!-- card: arr_lidarr_numbers -->" not in after and "<!-- card: codecs -->" in after and count == 0,
      "a disconnected Lidarr: the dashboard for everybody leaves its card out and does not compute it")
check([k.key for k in cards.load_layout()] == ["arr_lidarr_numbers", "codecs"] and admin.get("/arr/lidarr").status_code == 404
      and admin.get("/partials/arr/lidarr/downloading").status_code == 404,
      "the layout stays; neither the tab nor the partial of the disconnected application are there")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.area == "arr"], "and its cards stop being offered")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
