# -*- coding: utf-8 -*-
r"""Bazarr's cards from the registry: the live state and fourteen cards about subtitles.

Every question is a card of its own in the registry (jellyscope/cards_bazarr.py), as
for Sonarr and Radarr; they are drawn by the Bazarr tab of the *Arr page and by the custom
dashboards. Watched:

A) the registry: 14 cards, keys, sections, the area, widths, templates, translations;
   a Bazarr that is not connected is not offered;
B) the data of every card over the downloaded history and the recorded live state;
C) every card is drawn on its own in a dashboard and carries the name Bazarr; the live state
   is read once a minute and never with a long timeout;
D) Bazarr does not answer: the live cards say so, the detail only for the administrator,
   the cards from the database go on, the key nowhere;
E) more missing than is read; a part of the live state does not load;
F) the warning texts only for the administrator;
G) the speed card only with a connected Sonarr or Radarr;
H) subtitles got repeatedly: a title from the library is a link, an unknown one text;
I) the whole history reaches to the first event; a disconnected Bazarr.

Bazarr is not started: the answers are recorded (tests/bazarr_responses/), the client
is faked by a client with httpx.MockTransport (tests/_apps.py).

Spec: docs/superpowers/specs/2026-11-14-bazarr-design.md

Run:
    .venv/bin/python tests/test_bazarr_cards.py
"""
from __future__ import annotations

import html as html_lib
import json
import asyncio
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bazarr-karty.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    BAZARR_RESPONSES, bazarr_client, bazarr_handler, no_network, tile, card_html, recorded_response, fill_bazarr, not_responding,
    login, connect_bazarr, connect_radarr, connect_sonarr,
)
from _package import substitute  # noqa: E402
from jellyscope import accounts, cards, db, i18n, stats  # noqa: E402
from jellyscope.arr import bazarr, radarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # whatever the test does not fake on its own falls over at the first request - it never goes to the network
KEY = "tajny-klic"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


EPISODES = recorded_response(BAZARR_RESPONSES, "episodes_history")["data"]
MOVIES = recorded_response(BAZARR_RESPONSES, "movies_history")["data"]
ACQUIRED = [z for z in [*EPISODES, *MOVIES] if z["action"] in bazarr.ACQUIRED]
BADGES = recorded_response(BAZARR_RESPONSES, "badges")
PROVIDERS = recorded_response(BAZARR_RESPONSES, "providers")["data"]
MISSING = [*recorded_response(BAZARR_RESPONSES, "episodes_wanted")["data"], *recorded_response(BAZARR_RESPONSES, "movies_wanted")["data"]]
THROTTLED = sorted(p["name"] for p in PROVIDERS if p["status"] != "Good")

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin_account, reader_account = accounts.get_by_name("admin"), accounts.get_by_name("reader")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()

print("--- A) the registry ---")
from jellyscope import cards_bazarr  # noqa: E402

NAMES = ("live_numbers", "missing_languages", "providers_now", "health", "numbers", "by_day", "when", "how", "languages", "providers",
         "score", "upgrades", "changed", "speed")
CARDS = cards_bazarr.CARDS
check(tuple(cards_bazarr.NAMES) == NAMES and [k.key for k in CARDS] == [f"bazarr_{j}" for j in NAMES]
      and cards_bazarr.card_key("numbers") == "bazarr_numbers", "14 cards bazarr_<name> in the page's order")
check([i18n.translate(n, "cs") if n else "" for n, _ in cards_bazarr.SECTIONS] == ["Teď v aplikaci", "Co se stáhlo", "Jazyky a poskytovatelé", "Jak dobré jsou",
                                             "Jak rychle přijdou"]
      and [len(j) for _, j in cards_bazarr.SECTIONS] == [4, 4, 2, 3, 1], "five sections of the page by the spec")
check(all(k.area == "arr" and k.group == "Bazarr" for k in CARDS), "the *Arr area, the Bazarr group")
check({k.key for k in CARDS if not k.uses_period} == {f"bazarr_{j}" for j in NAMES[:4]}, "without a period only four live cards")
check(all(k in cards.ALL_CARDS for k in CARDS) and len(cards.ALL_CARDS) == 206, f"they are in the registry; there are 206 cards ({len(cards.ALL_CARDS)})")
check({k.key for k in CARDS if k.has_table_window} == {"bazarr_languages", "bazarr_providers", "bazarr_changed",
                                                  "bazarr_providers_now", "bazarr_health"},
      "the cards with a table for the window")
missing_template = sorted(k.template for k in CARDS if not (PROJECT / "jellyscope" / "templates" / k.template).exists())
check(not missing_template and [k.template for k in CARDS] == [f"_card_bazarr_{j}.html" for j in NAMES],
      f"every card has its template ({missing_template})")
for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    texts = {t for k in CARDS for t in (k.name, k.description)} | {n for n, _ in cards_bazarr.SECTIONS}
    texts |= {*cards_bazarr.ACTION_LABELS.values(), *cards_bazarr.BUCKET_LABELS.values(), *cards_bazarr.SCORE_LABELS.values()}
    missing = sorted(t for t in texts if t not in dictionary)
    check(not missing, f"names, descriptions, sections and labels have a translation ({language}; missing {missing[:3]})")
check(set(cards_bazarr.ACTION_LABELS) == {*bazarr.ACTIONS.values(), "other"}, "every action has a label")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == "Bazarr"], "a Bazarr not connected is not offered")

print()
print("--- B) the cards' data ---")
queries: list[str] = []
limits: list[float] = []
space: dict[str, Any] = {}


def handler(request: httpx.Request) -> httpx.Response:
    limits.append(float((request.extensions.get("timeout") or {}).get("read") or 0))
    return bazarr_handler(queries, space)(request)


restore = substitute(bazarr, "BazarrClient", bazarr_client(handler))
connect_bazarr(KEY)
fill_bazarr()
asyncio.run(bazarr.refresh_counts())     # the *Arr task reads the lists of the missing (23. 11. 2026)
bazarr.live.CACHE.clear()
queries.clear()
limits.clear()


def data(account: dict[str, Any] | None = None, period: Any = None) -> dict[str, Any]:
    """Bazarr's card data for an account; without a period for the whole history (the recorded history is then within the period, whenever it is)."""
    account = account or admin_account
    return cards.data_for(cards.allowed(list(CARDS), account), period or stats.whole_history_period(), account=account)


d = data()
z = d["bazarr_live_numbers"]
check((z["available"], z["episode_count"], z["movie_count"], z["limited_count"], z["warnings"]) == (True, BADGES["episodes"], BADGES["movies"], len(THROTTLED), 0)
      and z["partly_loaded"] is False, f"the numbers now: episodes and movies without subtitles, throttled providers, warnings ({z['episode_count']}, {z['movie_count']}, {z['limited_count']})")
expected_languages = Counter(j["code2"] for item in MISSING for j in item["missing_subtitles"])
languages_now = d["bazarr_missing_languages"]
check({r["language"]: r["count"] for r in languages_now["rows"]} == dict(expected_languages) and languages_now["incomplete_list"] is False
      and [r["count"] for r in languages_now["rows"]] == sorted(expected_languages.values(), reverse=True)
      and all(r["label"] for r in languages_now["rows"]), f"missing subtitles by language, the most frequent first ({dict(expected_languages)})")
now = d["bazarr_providers_now"]["rows"]
check([p["name"] for p in now[:len(THROTTLED)]] == THROTTLED and all(not p["fine"] and p["state"] and p["again"] for p in now[:len(THROTTLED)])
      and [p["name"] for p in now[len(THROTTLED):]] == sorted(p["name"] for p in PROVIDERS if p["status"] == "Good")
      and all(p["fine"] and not p["again"] for p in now[len(THROTTLED):]),
      f"providers now: the throttled first with the reason and when Bazarr tries again ({[(p['name'], p['state'], p['again']) for p in now[:2]]})")
check(d["bazarr_health"]["health"] == [] and d["bazarr_health"]["warnings"] == 0, "warnings: Bazarr reports none")
numbers = d["bazarr_numbers"]
check(numbers["obtained_count"] == len(ACQUIRED) and numbers["automatic"] == sum(z["action"] == 1 for z in ACQUIRED)
      and numbers["manual"] == sum(z["action"] in (2, 4) for z in ACQUIRED), f"the period's numbers from the history ({numbers})")
check(sum(b["episodes"] for b in d["bazarr_by_day"]["body"]) == sum(z["action"] in bazarr.ACQUIRED for z in EPISODES)
      and sum(b["movies"] for b in d["bazarr_by_day"]["body"]) == sum(z["action"] in bazarr.ACQUIRED for z in MOVIES), "by day: episodes and movies")
check(sum(map(sum, d["bazarr_when"]["heatmap_data"])) == len(ACQUIRED), "day × hour")
how = d["bazarr_how"]
check(sum(r["count"] for r in how["rows"]) == len(ACQUIRED) and how["rows"][0]["label"] == "staženo automaticky"
      and "smazáno" in how["outside"] and all("count" in r and r["label"] for r in how["rows"]),
      f"how the subtitles were got: the acquired as columns, deletions apart ({how['outside']})")
languages = d["bazarr_languages"]["rows"]
check(sum(r["count"] for r in languages) == len(ACQUIRED) and {r["language"] for r in languages} == {z["language"]["code2"] for z in ACQUIRED}
      and all(r["name"] for r in languages), f"the languages of the subtitles got ({[(r['language'], r['count']) for r in languages]})")
providers = d["bazarr_providers"]["rows"]
check(sum(r["count"] for r in providers) == len(ACQUIRED) and {r["provider"] for r in providers} == {z["provider"] for z in ACQUIRED}
      and next(r["name"] for r in providers if r["provider"] == "manual") == "vlastní soubor",
      f"the providers of the subtitles got; „manual“ is an own file ({[(r['provider'], r['count']) for r in providers]})")
score = d["bazarr_score"]
check(score["count"] == sum(z["score"] is not None for z in ACQUIRED) and score["median"].endswith("%")
      and sum(r["count"] for r in score["buckets"]) == score["count"] and [r["label"] for r in score["buckets"]]
      == ["pod 70 %", "70–80 %", "80–90 %", "90 % a víc"], f"the score: the median and four buckets ({score['median']}, {score['count']})")
repeated = stats.bazarr_changed(stats.whole_history_period(first=stats.bazarr_first()), 100)
check(d["bazarr_upgrades"]["count"] == 0 and d["bazarr_upgrades"]["improvements"] == "–", "upgrades: none in the recorded history, the card copes")
check([(r["title"], r["episode"], r["count"]) for r in d["bazarr_changed"]["rows"]] == [(r["title"], r["episode"], r["count"]) for r in repeated]
      and all(r["detail_url"] is None and r["language_name"] for r in d["bazarr_changed"]["rows"]),
      f"got repeatedly by the statistics; without Sonarr and Radarr without links ({len(repeated)})")
check("bazarr_speed" not in d, "without Sonarr and Radarr the speed card is not counted")
check(KEY not in json.dumps(d, default=str), "the key is not in the cards' data")

print()
print("--- C) every card on its own in a dashboard ---")
admin, reader = login("admin"), login("reader")
for card in CARDS:
    if card.key == "bazarr_speed":
        continue
    cards.save_layout([card.key])
    r = admin.get("/dashboard?days=0")
    check(r.status_code == 200 and "Bazarr · " in card_html(r.text, card.key), f"{card.key} on its own in a dashboard, with the name Bazarr")
cards.save_layout([k.key for k in CARDS])
bazarr.live.CACHE.clear()
queries.clear()
limits.clear()
everything = admin.get("/dashboard?days=0")
check(everything.status_code == 200 and len(re.findall(r"<!-- card: bazarr_\w+ -->", everything.text)) == 13, "thirteen cards at once (the speed waits for Sonarr)")
check(tile(everything.text, "Dílů bez titulků") == str(BADGES["episodes"]) and tile(everything.text, "Získané titulky") == str(len(ACQUIRED)),
      f"the tiles of the live state and the period ({tile(everything.text, 'Dílů bez titulků')}, {tile(everything.text, 'Získané titulky')})")
check('class="heatmap"' in card_html(everything.text, "bazarr_when") and THROTTLED[0] in card_html(everything.text, "bazarr_providers_now"),
      "the heatmap and the providers in their cards")
admin.get("/dashboard?days=30")
check(sum(q.startswith("badges?") for q in queries) == 1 and len(queries) == 3,
      f"two pages within a minute = one read of the live state, three cheap queries ({queries})")
check(limits and max(limits) <= 3, f"the live state reads with the short limit, 3 s ({sorted(set(limits))})")
check(KEY not in everything.text and 'id="table-window"' in everything.text, "the key nowhere; the dashboard has the window for the whole table")
# The missing subtitles by language are the most expensive part (up to 2,000 episodes and 2,000 movies) and change
# slowly: since 23. 11. 2026 the *Arr sync reads them (fill_bazarr above) and the live state only adds them.
bazarr.live.CACHE.pop("bazarr")
queries.clear()
after_a_minute = data()
check(sorted(q.split("?")[0] for q in queries) == ["badges", "providers", "system/health"]
      and after_a_minute["bazarr_missing_languages"]["rows"] and after_a_minute["bazarr_missing_languages"]["counted_at"]
      and after_a_minute["bazarr_live_numbers"]["episode_count"] == BADGES["episodes"],
      f"after a minute only the three cheap queries, the languages from the sync with their time ({queries})")
bazarr.live.CACHE.pop("bazarr")
space["badges"] = {**BADGES, "episodes": BADGES["episodes"] + 1}
queries.clear()
after_change = data()
check(len(queries) == 3 and after_change["bazarr_live_numbers"]["episode_count"] == BADGES["episodes"] + 1
      and after_change["bazarr_missing_languages"]["rows"],
      f"a changed count is not read live from the lists: the languages wait for the next sync ({sorted(q.split('?')[0] for q in queries)})")
space.clear()
counted_at = data()["bazarr_missing_languages"]["counted_at"]
space["movies/wanted"] = httpx.Response(500)
asyncio.run(bazarr.refresh_counts())
bazarr.live.CACHE.clear()
check(data()["bazarr_missing_languages"]["rows"] and data()["bazarr_missing_languages"]["counted_at"] == counted_at,
      "the missing movies do not load in the task: the last languages stay with their time")
space.clear()
asyncio.run(bazarr.refresh_counts())
bazarr.live.CACHE.clear()
check(data()["bazarr_missing_languages"]["counted_at"] >= counted_at and data()["bazarr_missing_languages"]["rows"],
      "and the next run reads them again")
bazarr.live.CACHE.clear()
# The texts Bazarr writes instead of "in N hours", and its own language codes.
AGAIN = {"in 12 hours": "za 12 h", "in a minute": "za 1 min", "in an hour": "za 1 h", "in 45 minutes": "za 45 min", "tomorrow": "zítra",
         "day after": "pozítří", "in 3 days": "za 3 d", "next week": "příští týden", "in 2 weeks": "za 2 týd.", "next month": "příští měsíc",
         "kdoví kdy": "kdoví kdy", "": ""}
wrong = {text: cards_bazarr.retry_in(text) for text, czech in AGAIN.items() if cards_bazarr.retry_in(text) != czech}
check(not wrong, f"when Bazarr tries again is in Czech in every shape it writes; an unknown shape stays ({wrong})")
check(cards_bazarr.language("pb") == "Portugalština (Brazílie)" and cards_bazarr.language("zt", hi=True) == "Čínština (tradiční) · pro neslyšící"
      and cards_bazarr.language("cs") == "Čeština", f"Bazarr's own language codes have a name ({cards_bazarr.language('pb')}, {cards_bazarr.language('zt')})")

print()
print("--- D) Bazarr does not answer ---")
restore()
restore = substitute(bazarr, "BazarrClient", bazarr_client(not_responding()))
bazarr.live.CACHE.clear()
r = admin.get("/dashboard?days=0").text
check(all("Aplikace neodpovídá" in card_html(r, f"bazarr_{j}") for j in ("live_numbers", "providers_now", "health")),
      "the three live cards say so")
check("spočítáno" in card_html(r, "bazarr_missing_languages"), "the languages from the last sync stay, with their time")
check(tile(r, "Získané titulky") == str(len(ACQUIRED)) and 'class="heatmap"' in card_html(r, "bazarr_when"), "the cards from the database go on")
check("Connection refused" in card_html(r, "bazarr_live_numbers") and KEY not in r, "the administrator sees the error's detail, the key nowhere")
r_reader = reader.get("/dashboard?days=0").text
check("Aplikace neodpovídá" in card_html(r_reader, "bazarr_live_numbers") and "Connection refused" not in r_reader
      and "bazarr.test" not in r_reader, "whoever is not the administrator sees only the sentence that the app does not answer")
restore()
restore = substitute(bazarr, "BazarrClient", bazarr_client(handler))

print()
print("--- E) more missing than is read; a part does not load ---")
bazarr.live.CACHE.clear()
space["episodes/wanted"] = {"data": recorded_response(BAZARR_RESPONSES, "episodes_wanted")["data"], "total": bazarr.live.MISSING_LIMIT + 500}
asyncio.run(bazarr.refresh_counts())     # the *Arr task reads the lists of the missing (23. 11. 2026)
r = admin.get("/dashboard?days=0").text
check(data()["bazarr_missing_languages"]["incomplete_list"] is True and "ve skutečnosti jich chybí víc" in card_html(r, "bazarr_missing_languages"),
      "more missing than 2,000: the card says it counts the languages from the first part only")
bazarr.live.CACHE.clear()
space.clear()
space["providers"] = httpx.Response(500)
partial = data()
check(partial["bazarr_live_numbers"]["available"] and partial["bazarr_live_numbers"]["partly_loaded"]
      and partial["bazarr_live_numbers"]["limited_count"] == BADGES["providers"] and partial["bazarr_providers_now"]["rows"] is None,
      "the providers do not load: the number of throttled from the badges, the list unknown (None, not empty)")
r = admin.get("/dashboard?days=0").text
check("Poskytovatele se z aplikace nepodařilo načíst." in card_html(r, "bazarr_providers_now")
      and "Část údajů se z aplikace nepodařilo načíst" in card_html(r, "bazarr_live_numbers"), "and the cards say so")
space.clear()
# The warnings do not load, but the badges know their number: the administrator must not be told "the administrator sees the details".
bazarr.live.CACHE.clear()
space["system/health"] = httpx.Response(500)
space["badges"] = {**BADGES, "status": 2}
health = card_html(admin.get("/dashboard?days=0").text, "bazarr_health")
check("Aplikace hlásí varování: 2. Podrobnosti se z ní nepodařilo načíst." in health and "vidí správce" not in health,
      "warnings whose texts did not load: the card says the number and that the details are missing")
space.clear()

print()
print("--- F) the warning texts only for the administrator ---")
bazarr.live.CACHE.clear()
space["system/health"] = {"data": [{"object": "/data/media/tv", "issue": "Path not accessible from Bazarr"}]}
r = admin.get("/dashboard?days=0").text
check("Path not accessible from Bazarr" in card_html(r, "bazarr_health") and "/data/media/tv" in card_html(r, "bazarr_health")
      and tile(r, "Varování aplikace") == "1", "the administrator sees what the warning concerns and what is wrong")
r_reader = reader.get("/dashboard?days=0").text
check("Path not accessible" not in r_reader and "/data/media/tv" not in r_reader
      and "Aplikace hlásí varování" in card_html(r_reader, "bazarr_health") and tile(r_reader, "Varování aplikace") == "1",
      "a reader does not see the texts - only that Bazarr reports something")
d_reader = data(reader_account)
check("/data/media/tv" not in json.dumps(d_reader, default=str) and d_reader["bazarr_health"]["warnings"] == 1,
      "and the paths are not even in the cards' data for a reader")
space.clear()
bazarr.live.CACHE.clear()

print()
print("--- G) the speed only with Sonarr or Radarr ---")
check("bazarr_speed" not in {k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account)}, "without Sonarr and Radarr it is not offered")
connect_radarr()
db.forget_settings()
check("bazarr_speed" in {k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account)}, "with a connected Radarr alone it is offered")
radarr.save_settings(False, "http://radarr.test", "")
db.forget_settings()
check("bazarr_speed" not in {k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account)}, "after disconnecting Radarr not again")
connect_sonarr()
db.forget_settings()
check("bazarr_speed" in {k.key for k in cards.allowed(list(cards.ALL_CARDS), admin_account)}, "with a connected Sonarr yes")
episode = next(z for z in EPISODES if z["action"] == 1)
with db.connect() as conn:
    event = conn.execute("SELECT happened_at, item_id FROM bazarr_event WHERE kind = 'episode' AND action = 1 ORDER BY happened_at, fingerprint LIMIT 1").fetchone()
    conn.execute("INSERT INTO sonarr_event (arr_id, event_type, happened_at, title, episode, item_id, title_id) VALUES"
                 " (1, 'downloadFolderImported', ?, 'x', '', ?, 1)", (event["happened_at"], event["item_id"]))
    conn.commit()
speed = data()["bazarr_speed"]
check(speed["count"] >= 1 and speed["median"] != "–" and [r["label"] for r in speed["buckets"]]
      == ["do 10 minut", "do hodiny", "do dne", "do týdne", "déle"] and speed["not_imported"],
      f"the speed: the median, the buckets and the sentence about the titles without an import ({speed['median']}, {speed['count']})")
cards.save_layout(["bazarr_speed"])
r = admin.get("/dashboard?days=0").text
check("Bazarr · " in card_html(r, "bazarr_speed") and tile(card_html(r, "bazarr_speed"), "Medián") == speed["median"],
      "the speed card in the dashboard")
check(f"převzetí souboru není: {speed['not_imported']}×." in card_html(r, "bazarr_speed")
      and f"Započteno: {speed['count']}×." in card_html(r, "bazarr_speed") and "titulů" not in card_html(r, "bazarr_speed"),
      "the sentences about what is counted and what is not do not decline by the number („1 titulů“)")

print()
print("--- H) got repeatedly: a link only to a title from the library ---")
now_time = db.utcnow()
with db.connect() as conn:
    for i, (title, title_id, item) in enumerate((("Známý seriál", 501, 9001), ("Známý seriál", 501, 9001),
                                                 ("Neznámý seriál", 502, 9002), ("Neznámý seriál", 502, 9002))):
        conn.execute("INSERT INTO bazarr_event (fingerprint, kind, title, episode, title_id, item_id, action, language, provider, score, happened_at,"
                     " synced_at) VALUES (?, 'episode', ?, 'S01E01', ?, ?, ?, 'cs', 'gestdown', ?, ?, ?)",
                     (f"rucne-{i}", title, title_id, item, 1 if i % 2 == 0 else 3, 70.0 + 10 * (i % 2), now_time, now_time))
    conn.execute("INSERT INTO sonarr_title (title_id, name, tvdb_id, jellyfin_id, synced_at) VALUES (501, 'Známý seriál',"
                 " '1', 'serial501', ?)", (now_time,))
    conn.execute("INSERT INTO items (id, name, type, series_id, series_name, date_created, synced_at) VALUES ('dil501', 'Díl', 'Episode',"
                 " 'serial501', 'Známý seriál', ?, ?)", (now_time, now_time))
    conn.commit()
changed = [r for r in data()["bazarr_changed"]["rows"] if r["title"].endswith("seriál")]
check({r["title"]: r["detail_url"] for r in changed} == {"Známý seriál": "/series/serial501", "Neznámý seriál": None}
      and all(r["count"] == 2 and r["language_name"] for r in changed), f"two new titles with two acquisitions each; only the one from the library has a link ({changed})")
cards.save_layout(["bazarr_changed", "bazarr_upgrades"])
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
cut = card_html(r, "bazarr_changed")
check('<a href="/series/serial501">Známý seriál</a>' in cut and "Neznámý seriál" in cut and "/series/None" not in cut
      and 'href="/series/serial502"' not in cut, "in the card the known title is a link and the unknown plain text")
check(tile(card_html(r, "bazarr_upgrades"), "Upgradů titulků") == "2" and "+10" in card_html(r, "bazarr_upgrades"),
      "upgrades: the number and the median improvement of the score")

print()
print("--- I) the whole history, a disconnected Bazarr ---")
with db.connect() as conn:
    conn.execute("INSERT INTO bazarr_event (fingerprint, kind, title, item_id, action, language, happened_at, synced_at)"
                 " VALUES ('stary', 'movie', 'Starý film', 77, 1, 'cs', '2024-03-01 10:00:00', ?)", (now_time,))
    conn.commit()
before = len(ACQUIRED) + 4
check(data()["bazarr_numbers"]["obtained_count"] == before + 1 and data(period=30)["bazarr_numbers"]["obtained_count"] == before,
      f"the whole history reaches to the first subtitle event, thirty days does not ({data()['bazarr_numbers']['obtained_count']}, {data(period=30)['bazarr_numbers']['obtained_count']}, {before})")
cards.save_layout(["bazarr_numbers"])
check(tile(admin.get("/dashboard?days=0").text, "Získané titulky") == str(before + 1), "the whole history in the dashboard too")
bazarr.save_settings(False, "http://bazarr.test", "")
db.forget_settings()
check("<!-- card: bazarr_numbers -->" not in admin.get("/dashboard").text and [k.key for k in cards.load_layout()] == ["bazarr_numbers"]
      and not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == "Bazarr"],
      "a disconnected Bazarr: the cards are neither drawn nor offered, the layout stays")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
