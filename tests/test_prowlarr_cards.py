# -*- coding: utf-8 -*-
r"""Prowlarr's cards from the registry: the live state and thirteen cards about indexers.

Every question is a card of its own in the registry (jellyscope/cards_prowlarr.py) as
with the other applications; the Prowlarr tab of the *Arr page and the custom
dashboards draw them. Watched:

A) the registry: 13 cards, keys, sections, area, templates, translations;
   an unconnected Prowlarr is not offered;
B) the data of every card over the downloaded history and the recorded live state;
C) every card is drawn alone in the dashboard and carries the name Prowlarr; the live state
   is read once a minute and with a short time limit;
D) Prowlarr does not answer: the live cards say so, the detail only for the administrator,
   the cards from the database go on, the key nowhere;
E) part of the live state does not load: None, not zero, and the card says so; an indexer
   whose block expired, but Prowlarr still reports it in a warning, is not
   "v pořádku";
F) the warning texts only for the administrator;
G) an indexer without a name, a removed indexer, an unknown application, releases only
   redirected;
H) the whole history reaches back to the first event; a disconnected Prowlarr.

Prowlarr is not run: the answers are recorded (tests/prowlarr_responses/),
the client is replaced by a client with httpx.MockTransport (tests/_apps.py).

Spec: docs/superpowers/specs/2026-11-15-prowlarr-design.md

Run:
    .venv/bin/python tests/test_prowlarr_cards.py
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-karty.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import (  # noqa: E402
    PROWLARR_RESPONSES, tile, card_html, recorded_response, fill_prowlarr, not_responding, login, connect_prowlarr, prowlarr_history,
    prowlarr_client, prowlarr_handler,
)
from _package import substitute  # noqa: E402
from jellyscope import accounts, cards, db, i18n, stats  # noqa: E402
from jellyscope.arr import prowlarr  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
KEY = "tajny-klic"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


HISTORY = prowlarr_history()
SEARCHES = [z for z in HISTORY if z["eventType"] == "indexerQuery"]
RSS = [z for z in HISTORY if z["eventType"] == "indexerRss"]
GRABBED = [z for z in HISTORY if z["eventType"] == "releaseGrabbed"]
FAILED = sum(not z["successful"] for z in [*SEARCHES, *RSS])
DOWNLOADED = [z for z in GRABBED if z["data"].get("grabMethod") == "Proxy"]
INDEXERS = recorded_response(PROWLARR_RESPONSES, "indexer")
INDEXER_NAMES = {i["id"]: i["name"] for i in INDEXERS}
HEALTH = recorded_response(PROWLARR_RESPONSES, "health")
IN_AN_HOUR = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
# The recorded block expired long ago - for the test it lasts another hour.
BLOCKS = [{**recorded_response(PROWLARR_RESPONSES, "indexerstatus")[0], "disabledTill": IN_AN_HOUR}]
BLOCKED = INDEXER_NAMES[BLOCKS[0]["indexerId"]]
DISABLED = {"id": 8, "name": "Vypnutý ručně", "protocol": "torrent", "privacy": "public", "enable": False}

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin_account, reader_account = accounts.get_by_name("admin"), accounts.get_by_name("reader")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()

print("--- A) the registry ---")
from jellyscope import cards_prowlarr  # noqa: E402

NAMES = ("live_numbers", "indexers_now", "health", "numbers", "by_day", "when", "who", "indexers", "response_time", "failures",
         "grabbed_numbers", "grabbed_indexers", "grabbed_latest")
CARDS = cards_prowlarr.CARDS
check(tuple(cards_prowlarr.NAMES) == NAMES and [k.key for k in CARDS] == [f"prowlarr_{j}" for j in NAMES]
      and cards_prowlarr.card_key("numbers") == "prowlarr_numbers", "13 cards prowlarr_<name> in the page's order")
check([i18n.translate(n, "cs") if n else "" for n, _ in cards_prowlarr.SECTIONS] == ["Teď v aplikaci", "Dotazy", "Výkon indexerů", "Vyžádaná vydání"]
      and [len(j) for _, j in cards_prowlarr.SECTIONS] == [3, 4, 3, 3], "four sections of the page by the spec")
check(all(k.area == "arr" and k.group == "Prowlarr" for k in CARDS), "the *Arr area, the Prowlarr group")
check({k.key for k in CARDS if not k.uses_period} == {f"prowlarr_{j}" for j in NAMES[:3]}, "only the three live cards without a period")
check(all(k in cards.ALL_CARDS for k in CARDS) and len(cards.ALL_CARDS) == 206, f"they are in the registry; there are 206 cards ({len(cards.ALL_CARDS)})")
order = [k.key for k in cards.ALL_CARDS]
check(order.index("prowlarr_live_numbers") == max(i for i, k in enumerate(order) if k.startswith("bazarr_")) + 1,
      "in the card offer they stand right after Bazarr's cards")
check({k.key for k in CARDS if k.has_table_window} == {"prowlarr_indexers", "prowlarr_grabbed_indexers", "prowlarr_grabbed_latest"},
      "cards with a table for the dialog")
missing_templates = sorted(k.template for k in CARDS if not (PROJECT / "jellyscope" / "templates" / k.template).exists())
check(not missing_templates and [k.template for k in CARDS] == [f"_card_prowlarr_{j}.html" for j in NAMES],
      f"every card has its template ({missing_templates})")
for language in ("cs", "es"):            # English is the source, Czech and Spanish translate it
    dictionary = json.loads((PROJECT / "jellyscope" / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    texts = {t for k in CARDS for t in (k.name, k.description)} | {n for n, _ in cards_prowlarr.SECTIONS} | {*cards_prowlarr.RESULT_LABELS.values()}
    missing = sorted(t for t in texts if t not in dictionary)
    check(not missing, f"the names, descriptions, sections and labels have a translation ({language}; missing {missing[:3]})")
check(not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == "Prowlarr"], "an unconnected Prowlarr is not offered")

print()
print("--- B) the cards' data ---")
asked: list[str] = []
timeouts: list[float] = []
overrides: dict[str, Any] = {"indexerstatus": BLOCKS, "indexer": [*INDEXERS, DISABLED]}


def handler(request: httpx.Request) -> httpx.Response:
    timeouts.append(float((request.extensions.get("timeout") or {}).get("read") or 0))
    return prowlarr_handler(asked, overrides)(request)


restore = substitute(prowlarr, "ProwlarrClient", prowlarr_client(handler))
connect_prowlarr(KEY)
fill_prowlarr()
prowlarr.live.CACHE.clear()
asked.clear()
timeouts.clear()


def data(owner: dict[str, Any] | None = None, period: Any = None) -> dict[str, Any]:
    """The data of Prowlarr's cards for an account; without a period for the whole history (the recorded history is then in the period, whenever it is)."""
    owner = owner or admin_account
    return cards.data_for(cards.allowed(list(CARDS), owner), period or stats.whole_history_period(), account=owner)


d = data()
z = d["prowlarr_live_numbers"]
check((z["available"], z["enabled_count"], z["unavailable_count"], z["app_count"], z["warnings"]) == (True, len(INDEXERS), 1, 2, len(HEALTH))
      and z["partly_loaded"] is False and "app" not in z,
      f"the numbers now: enabled indexers, unavailable, connected applications (only the count), warnings ({z['enabled_count']}, {z['unavailable_count']}, {z['app_count']}, {z['warnings']})")
now_rows = d["prowlarr_indexers_now"]["rows"]
others = sorted((i["name"] for i in INDEXERS if i["name"] != BLOCKED), key=str.casefold)
check([r["name"] for r in now_rows] == [BLOCKED, DISABLED["name"], *others] and now_rows[0]["blocked_until"] == IN_AN_HOUR.replace("T", " ").rstrip("Z")
      and now_rows[0]["failing_since"] and now_rows[1]["enabled"] is False and all(r["enabled"] and not r["blocked_until"] for r in now_rows[2:])
      and {r["protocol"] for r in now_rows} == {"usenet", "torrent"} and d["prowlarr_indexers_now"]["blocks_unknown"] is False,
      f"indexers now: the temporarily disabled one first (until when and failing since when), then the one disabled by hand, then the others ({[r['name'] for r in now_rows]})")
check([x["message"] for x in d["prowlarr_health"]["health"]] == [x["message"] for x in HEALTH] and d["prowlarr_health"]["warnings"] == len(HEALTH),
      "warnings: the administrator has the texts")
numbers = d["prowlarr_numbers"]
check((numbers["searches"], numbers["rss"], numbers["failed_count"]) == (len(SEARCHES), len(RSS), FAILED) and numbers["share"].endswith("%")
      and numbers["response"].endswith(" ms"), f"the query numbers from the history ({numbers})")
check(sum(b["searches"] for b in d["prowlarr_by_day"]["body"]) == len(SEARCHES) and sum(b["rss"] for b in d["prowlarr_by_day"]["body"]) == len(RSS),
      "by day: searches and RSS")
check(sum(map(sum, d["prowlarr_when"]["heatmap_data"])) == len(SEARCHES), "day × hour: searches only")
who = d["prowlarr_who"]["rows"]
check({r["name"] for r in who} == {z["data"]["source"] for z in HISTORY} and sum(r["searches"] for r in who) == len(SEARCHES)
      and sum(r["rss"] for r in who) == len(RSS) and sum(r["grabs"] for r in who) == len(GRABBED), f"who asks ({[(r['name'], r['searches'], r['rss'], r['grabs']) for r in who]})")
ix = d["prowlarr_indexers"]["rows"]
check({r["name"] for r in ix} == {INDEXER_NAMES[z["indexerId"]] for z in HISTORY} and sum(r["query_count"] for r in ix) == len(SEARCHES) + len(RSS)
      and sum(r["failed_count"] for r in ix) == FAILED and sum(r["grabs"] for r in ix) == len(GRABBED)
      and all(isinstance(r[k], str) for r in ix for k in ("failure_share", "response", "found_share")),
      f"the indexers' performance: numbers from the history, shares and the response time as ready text ({[(r['name'], r['query_count'], r['failure_share'], r['response'], r['found_share']) for r in ix]})")
response_rows = d["prowlarr_response_time"]["rows"]
check(response_rows and [r["response"] for r in response_rows] == sorted((r["response"] for r in response_rows), reverse=True) and all(r["name"] for r in response_rows),
      f"the indexers' response time: the slowest first ({[(r['name'], r['response']) for r in response_rows]})")
check(sum(b["failed_count"] for b in d["prowlarr_failures"]["body"]) == FAILED == d["prowlarr_failures"]["total"], "failed queries by day")
vc = d["prowlarr_grabbed_numbers"]
check((vc["grab_count"], vc["being_grabbed"], vc["redirected"]) == (len(GRABBED), len(DOWNLOADED), len(GRABBED) - len(DOWNLOADED))
      and vc["failed_count"] == str(sum(not z["successful"] for z in GRABBED)) and vc["response"].endswith(" ms"),
      f"grabbed releases: how many, how many Prowlarr downloaded itself, failures and the time ({vc})")
vi = d["prowlarr_grabbed_indexers"]["rows"]
only_redirected = {INDEXER_NAMES[z["indexerId"]] for z in GRABBED} - {INDEXER_NAMES[z["indexerId"]] for z in DOWNLOADED}
check(sum(r["grab_count"] for r in vi) == len(GRABBED) and only_redirected and all(r["failed_count"] == "–" for r in vi if r["name"] in only_redirected)
      and all(r["failed_count"] != "–" for r in vi if r["name"] not in only_redirected) and d["prowlarr_grabbed_indexers"]["unknown"] is True,
      f"grabbed by indexer: where Prowlarr only redirected, the failure is “–” ({[(r['name'], r['grab_count'], r['failed_count']) for r in vi]})")
vp = d["prowlarr_grabbed_latest"]["rows"]
newest_first = sorted(GRABBED, key=lambda z: (z["date"], z["id"]), reverse=True)
check([r["title"] for r in vp] == [z["data"]["grabTitle"] for z in newest_first]
      and {r["result"] for r in vp} == {"ok", "failed", "redirected"} and all(r["label"] and r["name"] and r["app"] for r in vp),
      f"the latest grabbed from the newest: succeeded / failed / redirected ({[(r['title'][:20], r['result']) for r in vp[:3]]})")
check(KEY not in json.dumps(d, default=str) and "TAJNY-KLIC" not in json.dumps(d, default=str), "the key is not in the cards' data")

print()
print("--- C) every card alone in the dashboard ---")
admin, reader = login("admin"), login("reader")
for card in CARDS:
    cards.save_layout([card.key])
    r = admin.get("/dashboard?days=0")
    check(r.status_code == 200 and "Prowlarr · " in card_html(r.text, card.key), f"{card.key} alone in the dashboard, with the name Prowlarr")
cards.save_layout([k.key for k in CARDS])
prowlarr.live.CACHE.clear()
asked.clear()
timeouts.clear()
everything = admin.get("/dashboard?days=0")
check(everything.status_code == 200 and len(re.findall(r"<!-- card: prowlarr_\w+ -->", everything.text)) == 13, "thirteen cards at once")
check(tile(everything.text, "Zapnuté indexery") == str(len(INDEXERS)) and tile(everything.text, "Nedostupné indexery") == "1"
      and tile(everything.text, "Hledání") == str(len(SEARCHES)) and tile(everything.text, "Vyžádaná vydání") == str(len(GRABBED)),
      f"the tiles of the live state and of the period ({tile(everything.text, 'Zapnuté indexery')}, {tile(everything.text, 'Hledání')}, {tile(everything.text, 'Vyžádaná vydání')})")
text = html_lib.unescape(everything.text)
check('class="heatmap"' in card_html(text, "prowlarr_when") and BLOCKED in card_html(text, "prowlarr_indexers_now")
      and "dočasně vypnutý" in card_html(text, "prowlarr_indexers_now") and newest_first[0]["data"]["grabTitle"] in card_html(text, "prowlarr_grabbed_latest"),
      "the heatmap, the temporarily disabled indexer and the latest release in their cards")
admin.get("/dashboard?days=30")
check(sorted(q.split("?")[0] for q in asked) == ["applications", "health", "indexer", "indexerstatus"],
      f"two pages within a minute = one read of the live state, four requests ({asked})")
check(timeouts and max(timeouts) <= 20, f"the live state reads with a short time limit ({sorted(set(timeouts))})")
check(KEY not in everything.text and "TAJNY-KLIC" not in everything.text and 'id="table-window"' in everything.text, "the key nowhere; the dashboard has the dialog for the whole table")
check(cards_prowlarr.response_time(None) == "–" and cards_prowlarr.response_time(248) == "248 ms" and cards_prowlarr.response_time(1250) == "1,2 s"
      and cards_prowlarr.response_time(12_000) == "12,0 s", f"the response time for a person: milliseconds, from a second seconds ({cards_prowlarr.response_time(1250)})")

print()
print("--- D) Prowlarr does not answer ---")
restore()
restore = substitute(prowlarr, "ProwlarrClient", prowlarr_client(not_responding()))
prowlarr.live.CACHE.clear()
r = admin.get("/dashboard?days=0").text
check(all("Aplikace neodpovídá" in card_html(r, f"prowlarr_{j}") for j in NAMES[:3]), "all three live cards say so")
check(tile(r, "Hledání") == str(len(SEARCHES)) and 'class="heatmap"' in card_html(r, "prowlarr_when"), "the cards from the database go on")
check("Connection refused" in card_html(r, "prowlarr_live_numbers") and KEY not in r, "the administrator sees the error's detail, the key nowhere")
r_reader = reader.get("/dashboard?days=0").text
check("Aplikace neodpovídá" in card_html(r_reader, "prowlarr_live_numbers") and "Connection refused" not in r_reader
      and "prowlarr.test" not in r_reader, "whoever is not the administrator sees only the sentence that the application does not answer")
restore()
restore = substitute(prowlarr, "ProwlarrClient", prowlarr_client(handler))

print()
print("--- E) part of the live state does not load ---")
prowlarr.live.CACHE.clear()
overrides["indexerstatus"] = httpx.Response(500)
partial = data()
check(partial["prowlarr_live_numbers"]["available"] and partial["prowlarr_live_numbers"]["partly_loaded"]
      and partial["prowlarr_live_numbers"]["unavailable_count"] is None and partial["prowlarr_live_numbers"]["enabled_count"] == len(INDEXERS)
      and partial["prowlarr_indexers_now"]["blocks_unknown"] is True,
      "the blocks do not load: the number of unavailable ones is unknown (None, not zero), the indexers are there")
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check(tile(r, "Nedostupné indexery") == "–" and "Část údajů se z aplikace nepodařilo načíst" in card_html(r, "prowlarr_live_numbers")
      and "se nepodařilo načíst" in card_html(r, "prowlarr_indexers_now") and "v pořádku" not in card_html(r, "prowlarr_indexers_now"),
      "and the cards say so: a dash instead of zero, no “v pořádku” by the indexers that nobody verified")
prowlarr.live.CACHE.clear()
overrides["indexerstatus"] = BLOCKS
overrides["indexer"] = httpx.Response(500)
overrides["health"] = httpx.Response(500)
partial = data()
check(partial["prowlarr_indexers_now"]["rows"] is None and partial["prowlarr_live_numbers"]["enabled_count"] is None
      and partial["prowlarr_health"]["warnings"] is None and partial["prowlarr_live_numbers"]["app_count"] == 2,
      "the indexers and the warnings do not load: unknown, the connected applications are there")
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check("Indexery se z aplikace nepodařilo načíst." in card_html(r, "prowlarr_indexers_now")
      and "Varování se z aplikace nepodařilo načíst." in card_html(r, "prowlarr_health"), "and the cards say so")
overrides["indexer"] = [*INDEXERS, DISABLED]
del overrides["health"]


def indexer_row(page: str, name: str) -> str:
    """The row of the table "Indexery teď" with the given indexer."""
    card = card_html(page, "prowlarr_indexers_now")
    return next((row for row in re.findall(r"<tr>.*?</tr>", card, re.S) if f">{name}" in row), "")


# The block expired (Prowlarr tries the indexer at the next query), but Prowlarr still reports it in a warning -
# it refreshes warnings only now and then. The card must not say "v pořádku" about the same indexer and warn a dialog lower.
prowlarr.live.CACHE.clear()
overrides["indexerstatus"] = []
after_block = data()
now_rows = after_block["prowlarr_indexers_now"]["rows"]
check(now_rows[0]["name"] == BLOCKED and now_rows[0]["reported"] is True and now_rows[0]["blocked_until"] is None
      and [r["name"] for r in now_rows[1:]] == [DISABLED["name"], *others] and not any(r["reported"] for r in now_rows[1:])
      and after_block["prowlarr_live_numbers"]["unavailable_count"] == 1,
      f"an indexer without a block that Prowlarr names in a warning: it is first, it counts among the unavailable ({[(r['name'], r['reported']) for r in now_rows]})")
for who, client in (("administrator", admin), ("reader", reader)):
    r = html_lib.unescape(client.get("/dashboard?days=0").text)
    row = indexer_row(r, BLOCKED)
    check("nedostupný" in row and "v pořádku" not in row and "v pořádku" in indexer_row(r, others[0])
          and tile(r, "Nedostupné indexery") == "1", f"{who}: that indexer has “nedostupný”, not “v pořádku”; the others are fine")
prowlarr.live.CACHE.clear()
overrides["health"] = [{"source": "IndexerStatusCheck", "type": "warning", "message": f"Indexers unavailable due to failures: {others[1]}"},
                       {"source": "UpdateCheck", "type": "warning", "message": f"New update is available: {BLOCKED}"}]
just_one = {r["name"]: r["reported"] for r in data()["prowlarr_indexers_now"]["rows"]}
check(just_one[others[1]] is True and just_one[others[0]] is False and just_one[BLOCKED] is False and others[1].startswith(others[0]),
      f"reported is only the indexer with exactly that name (not the one whose name is the start of another) and only from the warning about indexers ({just_one})")
# When all indexers fail, Prowlarr does not name them in the warning ("All indexers are unavailable…").
prowlarr.live.CACHE.clear()
overrides["health"] = [{"source": "IndexerStatusCheck", "type": "error", "message": "All indexers are unavailable due to failures"}]
all_failing = data()
check({r["name"]: r["reported"] for r in all_failing["prowlarr_indexers_now"]["rows"]} == {**{i["name"]: True for i in INDEXERS}, DISABLED["name"]: False}
      and all_failing["prowlarr_live_numbers"]["unavailable_count"] == len(INDEXERS),
      "a warning about indexers without names means all enabled ones: none is “v pořádku”, the one disabled by hand stays disabled")
# Prowlarr in another language writes the list of names with other punctuation - the names are looked for whole in the text, not by separators.
prowlarr.live.CACHE.clear()
overrides["health"] = [{"source": "IndexerLongTermStatusCheck", "type": "error", "message": f"索引器因故障不可用：{BLOCKED}、{others[1]}。"}]
other_language = {r["name"]: r["reported"] for r in data()["prowlarr_indexers_now"]["rows"]}
check(other_language[BLOCKED] is True and other_language[others[1]] is True and other_language[others[0]] is False,
      f"the names in a warning are found with other punctuation too; a shorter name inside a longer one does not count ({other_language})")
# The warnings do not load: whether Prowlarr reports some indexer is not known - "v pořádku" is not claimed and the count is unknown.
prowlarr.live.CACHE.clear()
overrides["indexerstatus"] = BLOCKS
overrides["health"] = httpx.Response(500)
without_health = data()
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check(without_health["prowlarr_live_numbers"]["unavailable_count"] is None and without_health["prowlarr_indexers_now"]["blocks_unknown"] is True
      and "dočasně vypnutý" in indexer_row(r, BLOCKED) and "zapnutý" in indexer_row(r, others[0])
      and "v pořádku" not in card_html(r, "prowlarr_indexers_now") and tile(r, "Nedostupné indexery") == "–",
      "the warnings do not load: the blocked one is still temporarily disabled, the others only “zapnutý” and the number of unavailable ones is “–”")
del overrides["health"]

print()
print("--- F) the warning texts only for the administrator ---")
prowlarr.live.CACHE.clear()
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check(HEALTH[0]["message"] in card_html(r, "prowlarr_health") and tile(r, "Varování aplikace") == str(len(HEALTH)), "the administrator sees what the application reports")
r_reader = html_lib.unescape(reader.get("/dashboard?days=0").text)
check(HEALTH[0]["message"] not in r_reader and "Aplikace hlásí varování" in card_html(r_reader, "prowlarr_health")
      and tile(r_reader, "Varování aplikace") == str(len(HEALTH)), "a reader does not see the texts - only that Prowlarr reports something")
d_reader = data(reader_account)
check(HEALTH[0]["message"] not in json.dumps(d_reader, default=str, ensure_ascii=False) and d_reader["prowlarr_health"]["warnings"] == len(HEALTH),
      "and the texts are not even in the cards' data for a reader")
prowlarr.live.CACHE.clear()
overrides["health"] = []
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check("Aplikace nehlásí žádné varování." in card_html(r, "prowlarr_health"), "without warnings the card says so")
del overrides["health"]

print()
print("--- G) an indexer without a name, a removed indexer, an unknown application, only redirected releases ---")
with db.connect() as conn:
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES ('2026-11-15 05:00:00', 77, '', 'query', 4, 1, 4500, 3, 2)")
    # Three sign-ins to indexer 77, two failed: they do not belong in the share of failed queries, the card says them separately.
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES ('2026-11-15 05:00:00', 77, '', 'auth', 3, 2, 0, 0, 0)")
    conn.execute("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at) VALUES (55, 'Dávno pryč', 'usenet', 'private', 1, 1, ?)",
                 (db.utcnow(),))
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES ('2026-11-15 05:00:00', 55, 'Sonarr', 'rss', 2, 0, 100, 2, 2)")
g = data()
nameless = next(r for r in g["prowlarr_indexers"]["rows"] if r["indexer_id"] == 77)
gone = next(r for r in g["prowlarr_indexers"]["rows"] if r["indexer_id"] == 55)
check(nameless["name"] == "indexer č. 77" and nameless["response"] == "1,5 s" and (gone["name"], gone["removed"]) == ("Dávno pryč", True)
      and next(r for r in g["prowlarr_who"]["rows"] if not r["app"])["name"] == "neznámá aplikace",
      f"an indexer without a name is “indexer č. N”, a removed one has its name and a mark, an application without a name is “neznámá aplikace” ({nameless['name']})")
cards.save_layout(["prowlarr_indexers", "prowlarr_grabbed_numbers", "prowlarr_grabbed_indexers"])
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check("indexer č. 77" in card_html(r, "prowlarr_indexers") and "odebraný" in card_html(r, "prowlarr_indexers"), "and the card writes it that way")
with db.connect() as conn:
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES ('2026-11-15 05:00:00', 0, 'Sonarr', 'query', 1, 0, 10, 1, 1)")
check(next(x for x in data()["prowlarr_indexers"]["rows"] if x["indexer_id"] == 0)["name"] == "neznámý indexer",
      "an event without an indexer (number 0) is “neznámý indexer”, not “indexer č. 0”")
check({k.key: k.width for k in CARDS if k.key in ("prowlarr_response_time", "prowlarr_failures")} == {"prowlarr_response_time": "full", "prowlarr_failures": "full"},
      "the response time and the failed queries by day are full width in the registry, as on the tab")
check((nameless["query_count"], nameless["failure_share"], nameless["login_failed"]) == (4, "25 %", 2)
      and "selhaná přihlášení: 2×" in card_html(r, "prowlarr_indexers") and card_html(r, "prowlarr_indexers").count("selhaná přihlášení") == 1,
      f"1 of 4 queries failed = 25 %; two failed sign-ins stand separately and only by the indexer that has them ({nameless['failure_share']})")
numbers_card = card_html(r, "prowlarr_grabbed_numbers")
check(f"stahoval sám: {len(DOWNLOADED)}×" in numbers_card and f"přesměroval: {len(GRABBED) - len(DOWNLOADED)}×" in numbers_card,
      "the numbers of grabbed: the card says how many releases Prowlarr downloaded itself and how many it only redirected")
with db.connect() as conn:
    conn.execute("UPDATE prowlarr_grab SET method = 'redirect', succeeded = 1, response = NULL")
only = data()["prowlarr_grabbed_numbers"]
r = html_lib.unescape(admin.get("/dashboard?days=0").text)
check((only["failed_count"], only["response"], only["being_grabbed"]) == ("–", "–", 0) and "jen přesměroval" in card_html(r, "prowlarr_grabbed_numbers")
      and tile(card_html(r, "prowlarr_grabbed_numbers"), "Selhalo") == "–",
      "only redirected releases: the failure and the time are “–” (unknown, not zero) and the card says why")

print()
print("--- H) the whole history, a disconnected Prowlarr ---")
with db.connect() as conn:
    conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                 " VALUES ('2024-03-01 10:00:00', 1, 'Sonarr', 'query', 9, 0, 900, 9, 9)")
before = len(SEARCHES) + 5                     # four searches of indexer 77 and one without an indexer from section G
check(data()["prowlarr_numbers"]["searches"] == before + 9 and data(period=stats.period_between("2026-11-01", "2026-11-30"))["prowlarr_numbers"]["searches"] == before,
      f"the whole history reaches back to the first event, November does not ({data()['prowlarr_numbers']['searches']}, {before})")
cards.save_layout(["prowlarr_numbers"])
check(tile(admin.get("/dashboard?days=0").text, "Hledání") == str(before + 9), "the whole history in the dashboard too")
prowlarr.save_settings(False, "http://prowlarr.test", "")
db.forget_settings()
check("<!-- card: prowlarr_numbers -->" not in admin.get("/dashboard").text and [k.key for k in cards.load_layout()] == ["prowlarr_numbers"]
      and not [k for k in cards.allowed(list(cards.ALL_CARDS), admin_account) if k.group == "Prowlarr"],
      "a disconnected Prowlarr: the cards are neither drawn nor offered, the layout stays")
restore()

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
