# -*- coding: utf-8 -*-
r"""Prowlarr: the time zone from Settings, the accounts' rights and the cards in custom dashboards.

The Prowlarr tab and its cards are to behave like the rest of the application:

A) **the time zone** (Settings → General): days, hours and the bounds of a period are
   computed in the application's zone and a change of the zone holds at once, without a restart - even though
   the queries are stored as hourly sums in UTC;
B) **the accounts' rights**: the tab, the menu item and the cards only an account with the *Arr
   area has - a local reader and a viewer signed in through Jellyfin; hidden names
   (anonymising) rewrite nothing in Prowlarr's cards (neither an application nor an indexer
   is a person); texts from the application only for the administrator;
C) **dashboards**: in the dashboard for everybody and in My dashboard Prowlarr's cards
   are offered, drawn and computed only for whoever has the right to them; a card to
   which the account lost the right stays in My dashboard with a sentence why.

Prowlarr is not run: the live state is recorded (tests/prowlarr_responses/),
the history is a few rows of our own with exact times.

Run:
    .venv/bin/python tests/test_prowlarr_time_and_rights.py
"""
from __future__ import annotations

import html as html_lib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prowlarr-cas-prava.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import tile, card_html, login, connect_prowlarr, prowlarr_client, prowlarr_handler  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, cards_prowlarr, db, stats  # noqa: E402
from jellyscope.arr import prowlarr  # noqa: E402

failures = 0
db.init_db()
INDEXER = "IndexerZQX"                       # the mark of the content of Prowlarr's cards: it occurs nowhere else
WARNING_TEXT = "Text varování ZQX"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.create("viewer", "longpassword")
now = db.utcnow()
with db.connect() as conn:
    # A viewer = an account that belongs to a viewer from Jellyfin (signing in through Jellyfin).
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE username = 'viewer'")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u-petr', 'PetrZQX', 0, 0), ('u-jana', 'JanaZQX', 0, 0)")
    conn.execute("INSERT INTO prowlarr_indexer (id, name, protocol, privacy, enabled, removed, seen_at) VALUES (1, ?, 'torrent', 'private', 1, 0, ?)",
                 (INDEXER, now))
    # One search in the hour that starts on Friday 6. 11. 2026 at 23:00 UTC, and one on Wednesday 4. 11. at 12:00.
    for hour in ("2026-11-06 23:00:00", "2026-11-04 12:00:00"):
        conn.execute("INSERT INTO prowlarr_hour (hour, indexer_id, app, kind, event_count, failed_count, response_sum, response_count, with_results)"
                     " VALUES (?, 1, 'Sonarr', 'query', 1, 0, 120, 1, 1)", (hour,))
    # A grabbed release on Friday at 23:30 UTC.
    conn.execute("INSERT INTO prowlarr_grab (id, happened_at, indexer_id, app, title, method, succeeded, response, client)"
                 " VALUES (1, '2026-11-06 23:30:00', 1, 'Sonarr', 'Vydani.ZQX.S01E01', 'proxy', 1, 40, '')")
db.set_setting(access.LOGIN_KEY, "1")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
overrides: dict[str, Any] = {"health": [{"source": "IndexerStatusCheck", "type": "warning", "message": WARNING_TEXT}]}
substitute(prowlarr, "ProwlarrClient", prowlarr_client(prowlarr_handler(None, overrides)))
connect_prowlarr()
admin, reader, viewer = login("admin"), login("reader"), login("viewer")
ACCOUNTS = {"reader": reader, "viewer": viewer}
ALL_AREAS = [o.key for o in access.AREAS]
WITHOUT_ARR = [o for o in ALL_AREAS if o != "arr"]
CARDS = cards_prowlarr.CARDS


def account(name: str) -> dict[str, Any]:
    return accounts.get_by_name(name)


def set_rights(name: str, areas: list[str] | None, anonymise: bool = False) -> None:
    """The account's rights explicitly (None = the default by the group)."""
    accounts.save_permissions(account(name)["id"], None if areas is None else access.pack_permissions(areas, anonymise))
    db.forget_settings()


def set_zone(name: str) -> None:
    """Stores the time zone the way the form in Settings → General does."""
    admin.post("/settings/language", data={"ui_language": "cs", "app_timezone": name}, follow_redirects=False)


def searches(client: Any, day: str, page: str = "/arr/prowlarr") -> str | None:
    """The tile "Hledání" for one day (a custom period since-until)."""
    return tile(client.get(f"{page}?since={day}&until={day}").text, "Hledání")


def releases(client: Any, day: str) -> str | None:
    """The tile "Vyžádaná vydání" for one day."""
    return tile(client.get(f"/arr/prowlarr?since={day}&until={day}").text, "Vyžádaná vydání")


def cards_data(period: Any = None, who: str = "admin") -> dict[str, Any]:
    whose = account(who)
    return cards.data_for(cards.allowed(list(CARDS), whose), period or stats.period_between("2026-11-01", "2026-11-10"), account=whose)


def days(d: dict[str, Any]) -> dict[str, int]:
    return {b["day"]: b["searches"] for b in d["prowlarr_by_day"]["body"] if b["searches"]}


print("--- A) the time zone from Settings ---")
set_zone("UTC")
check(db.get_setting("app_timezone") == "UTC" and searches(admin, "2026-11-06") == "1" and searches(admin, "2026-11-07") == "0"
      and releases(admin, "2026-11-06") == "1" and releases(admin, "2026-11-07") == "0",
      f"UTC: the search and the release from Friday after 23:00 belong to 6 November ({searches(admin, '2026-11-06')}, {searches(admin, '2026-11-07')})")
d = cards_data()
check(d["prowlarr_when"]["heatmap_data"][4][23] == 1 and d["prowlarr_when"]["heatmap_data"][2][12] == 1 and days(d) == {"2026-11-04": 1, "2026-11-06": 1},
      "UTC: the grid Friday 23 h and Wednesday 12 h, the chart by day 4 and 6 November")
set_zone("Europe/Prague")
check(searches(admin, "2026-11-06") == "0" and searches(admin, "2026-11-07") == "1"
      and releases(admin, "2026-11-06") == "0" and releases(admin, "2026-11-07") == "1",
      f"Prague (without a restart): the same belongs to Saturday 7 November ({searches(admin, '2026-11-06')}, {searches(admin, '2026-11-07')})")
d = cards_data()
check(d["prowlarr_when"]["heatmap_data"][5][0] == 1 and d["prowlarr_when"]["heatmap_data"][2][13] == 1 and d["prowlarr_when"]["heatmap_data"][4][23] == 0
      and days(d) == {"2026-11-04": 1, "2026-11-07": 1}, "Prague: the grid Saturday 0 h and Wednesday 13 h, the chart by day 4 and 7 November")
set_zone("America/New_York")
d = cards_data()
check(d["prowlarr_when"]["heatmap_data"][4][18] == 1 and d["prowlarr_when"]["heatmap_data"][2][7] == 1 and searches(admin, "2026-11-06") == "1",
      "New York: Friday 18 h and Wednesday 7 h")
cards.save_layout(["prowlarr_numbers", "prowlarr_by_day"])
set_zone("Europe/Prague")
check(searches(admin, "2026-11-07", "/dashboard") == "1" and searches(admin, "2026-11-06", "/dashboard") == "0",
      "the zone shows the same way with the cards in the custom dashboard")
empty_day = admin.get("/dashboard?since=2026-11-06&until=2026-11-06").text
check("V tomhle období přes Prowlarr neprošel žádný dotaz." in card_html(empty_day, "prowlarr_by_day"), "and a day without queries says so in the chart too")
# The release's time in the card is in the application's zone (in the dialog with the whole table and in the bubble over "před N dny").
latest = card_html(admin.get("/arr/prowlarr?since=2026-11-01&until=2026-11-10").text, "prowlarr_grabbed_latest")
check("07.11.2026 00:30" in latest and "06.11.2026 23:30" not in latest, "the grabbed release's time is in the application's zone (Prague: 7. 11. 00:30)")

print()
print("--- B) the accounts' rights ---")
PERIOD = "?since=2026-11-01&until=2026-11-10"
for name, client in ACCOUNTS.items():
    for label, areas, expected in (("default rights", None, None), ("with the *Arr area", ALL_AREAS, True), ("without the *Arr area", WITHOUT_ARR, False)):
        set_rights(name, areas)
        may = access.account_sees(account(name), "arr")
        response = client.get("/arr/prowlarr" + PERIOD, follow_redirects=False)
        in_menu = 'href="/arr/' in client.get("/account").text      # the *Arr section opens its first app
        card_count = len(cards.allowed(list(CARDS), account(name)))
        check((expected is None or may == expected) and (response.status_code == 200) == may == in_menu == bool(card_count)
              and (response.status_code in (200, 403)),
              f"{name}, {label}: the tab, the menu item and the cards {'are' if may else 'are not'} there ({response.status_code}, menu {in_menu}, cards {card_count})")
        if may:
            check(INDEXER in response.text and WARNING_TEXT not in response.text and "Aplikace hlásí varování" in card_html(response.text, "prowlarr_health"),
                  f"{name}, {label}: sees the statistics, the warning texts from the application not")
        else:
            check(INDEXER not in client.get("/account").text and INDEXER not in client.get("/").text,
                  f"{name}, {label}: the content of Prowlarr's cards is nowhere else either")
check(WARNING_TEXT in card_html(admin.get("/arr/prowlarr" + PERIOD).text, "prowlarr_health"), "the administrator sees the warning texts")
# Hidden names: Prowlarr's cards say nothing about people, anonymising must rewrite nothing in them.
reference = cards_data(who="admin")
for name, client in ACCOUNTS.items():
    set_rights(name, ALL_AREAS, anonymise=True)
    page = client.get("/arr/prowlarr" + PERIOD).text
    hidden = access.anonymise_for(cards_data(who=name), account(name))
    same = all(hidden[k] == reference[k] for k in ("prowlarr_indexers", "prowlarr_who", "prowlarr_numbers", "prowlarr_when", "prowlarr_response_time",
                                                   "prowlarr_grabbed_indexers", "prowlarr_grabbed_latest", "prowlarr_indexers_now"))
    check(access.hides_names(account(name)) and same and INDEXER in page and "Divák " not in card_html(page, "prowlarr_who")
          and "Sonarr" in card_html(page, "prowlarr_who") and "Divák " not in card_html(page, "prowlarr_indexers"),
          f"{name} with hidden names: Prowlarr's cards unchanged (neither an application nor an indexer is “Divák N”)")
    set_rights(name, None)

print()
print("--- C) the dashboard for everybody ---")


def offered(text: str) -> list[str]:
    return re.findall(r'data-add="(prowlarr_\w+)"', text)


builder = admin.get("/dashboard").text
check(sorted(offered(builder)) == sorted(k.key for k in CARDS), f"the administrator is offered all thirteen Prowlarr cards when building ({len(offered(builder))})")
cards.save_layout(["summary", "prowlarr_numbers", "prowlarr_indexers"])
db_queries: list[str] = []
original_query_all = db.query_all


def _eavesdrop(sql: str, *a: Any, **k: Any) -> Any:
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


def prowlarr_queries(client: Any, address: str) -> tuple[str, int]:
    db_queries.clear()
    db.query_all = _eavesdrop
    try:
        text = client.get(address).text
    finally:
        db.query_all = original_query_all
    return text, sum("prowlarr_" in q for q in db_queries)


for name, client in ACCOUNTS.items():
    set_rights(name, ALL_AREAS)
    text, query_count = prowlarr_queries(client, "/dashboard" + PERIOD)
    check(re.findall(r"<!-- card: (\w+) -->", text) == ["summary", "prowlarr_numbers", "prowlarr_indexers"] and INDEXER in text
          and "Prowlarr · " in card_html(text, "prowlarr_indexers") and query_count >= 1 and 'data-add=' not in text,
          f"{name} with the *Arr area: sees Prowlarr's cards in the dashboard for everybody (cannot build it)")
    set_rights(name, WITHOUT_ARR)
    text, query_count = prowlarr_queries(client, "/dashboard" + PERIOD)
    check(re.findall(r"<!-- card: (\w+) -->", text) == ["summary"] and INDEXER not in text and query_count == 0,
          f"{name} without the *Arr area: Prowlarr's cards are neither drawn nor computed for them, the rest of the dashboard stays ({query_count} queries)")
    set_rights(name, [o for o in ALL_AREAS if o != "dashboard"])
    check(client.get("/dashboard", follow_redirects=False).status_code in (303, 403), f"{name} without the custom dashboard area does not get to it")

print()
print("--- C) My dashboard ---")
for name, client in ACCOUNTS.items():
    account_id = account(name)["id"]
    set_rights(name, ALL_AREAS)
    mine = client.get("/account/dashboard").text
    check(sorted(offered(mine)) == sorted(k.key for k in CARDS), f"{name} with the *Arr area: My dashboard offers all thirteen Prowlarr cards ({len(offered(mine))})")
    client.post("/account/dashboard", data={"order": "prowlarr_numbers:cela,prowlarr_indexers:pul"}, follow_redirects=False)
    mine = client.get("/account/dashboard" + PERIOD).text
    check([s.key for s in cards.load_layout(account_id)] == ["prowlarr_numbers", "prowlarr_indexers"] and INDEXER in mine
          and tile(mine, "Hledání") == "2", f"{name}: added the cards and sees data in them")
    # The right disappears: the cards stay in their place with a sentence, the data is not computed and no more are offered.
    set_rights(name, WITHOUT_ARR)
    mine, query_count = prowlarr_queries(client, "/account/dashboard" + PERIOD)
    plain = html_lib.unescape(mine)
    check(plain.count("Nemáš oprávnění k oblasti „*Arr“") == 2 and INDEXER not in mine and tile(mine, "Hledání") is None
          and query_count == 0 and offered(mine) == [],
          f"{name} after losing the right: a sentence about the permission instead of the cards, no data, the offer without Prowlarr's cards ({query_count} queries)")
    client.post("/account/dashboard", data={"order": "prowlarr_numbers:cela,prowlarr_indexers:pul,prowlarr_who:pul,summary:full"},
                follow_redirects=False)
    saved = [s.key for s in cards.load_layout(account_id)]
    check("prowlarr_who" not in saved and saved[:2] == ["prowlarr_numbers", "prowlarr_indexers"],
          f"{name} without the right does not add a Prowlarr card even by a forged form; does not lose their two ({saved})")
    set_rights(name, ALL_AREAS)
    check(INDEXER in client.get("/account/dashboard" + PERIOD).text, f"{name}: after the right is given back the cards are back")
# Prowlarr disconnected: a card in My dashboard stays with a sentence, in the dashboard for everybody it is not drawn.
prowlarr.save_settings(False, "http://prowlarr.test", "")
db.forget_settings()
mine = html_lib.unescape(reader.get("/account/dashboard" + PERIOD).text)
for_all = reader.get("/dashboard" + PERIOD).text
check("Prowlarr není připojen" in mine and INDEXER not in mine and "<!-- card: prowlarr_numbers -->" not in for_all and offered(mine) == [],
      "a disconnected Prowlarr: My dashboard says so by the cards, the dashboard for everybody leaves them out, the offer is without them")
check([s.key for s in cards.load_layout(account("reader")["id"])][:2] == ["prowlarr_numbers", "prowlarr_indexers"], "and the layout stays")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
