# -*- coding: utf-8 -*-
r"""Bazarr: the time zone from Settings, the accounts' rights and the cards in the custom dashboards.

The Bazarr tab and its cards are to behave like the rest of the app:

A) **the time zone** (Settings → General): days, hours and the period's bounds are
   counted in the app's zone, and a change of the zone holds at once, without a restart;
B) **the accounts' rights**: the tab, the menu item and the cards are had only by an account
   with the *Arr area - a local reader and a viewer signed in through Jellyfin alike; hidden names
   (anonymising) rewrite nothing in Bazarr's cards; the app's texts only for the administrator;
C) **dashboards**: in the dashboard for everybody and in My dashboard, Bazarr's cards are
   offered, drawn and counted only for whoever has the right to them; a card the account
   lost the right to stays in My dashboard with a sentence saying why.

Bazarr is not started: the live state is recorded (tests/bazarr_responses/), the history
is a few rows of its own with exact times.

Run:
    .venv/bin/python tests/test_bazarr_time_and_rights.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bazarr-cas-prava.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import bazarr_client, bazarr_handler, no_network, tile, card_html, login, connect_bazarr, connect_sonarr  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, cards_bazarr, db, stats  # noqa: E402
from jellyscope.arr import bazarr  # noqa: E402

failures = 0
db.init_db()
no_network()      # whatever the test does not fake on its own falls over at the first request - it never goes to the network
PROVIDER = "PoskytovatelZQX"            # a mark of the content of Bazarr's cards: it appears nowhere else
WARNING = "Cesta varování ZQX"


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
    # A viewer = an account belonging to a viewer from Jellyfin (signing in through Jellyfin).
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE username = 'viewer'")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u-petr', 'PetrZQX', 0, 0), ('u-jana', 'JanaZQX', 0, 0)")
    # Two subtitles got with an exact time in UTC: Friday 6. 11. 2026 23:30 and Wednesday 4. 11. 12:00.
    for i, date in enumerate(("2026-11-06 23:30:00", "2026-11-04 12:00:00")):
        conn.execute("INSERT INTO bazarr_event (fingerprint, kind, title, episode, title_id, item_id, action, language, provider, score, happened_at,"
                     " synced_at) VALUES (?, 'episode', 'Seriál pásma', 'S01E01', 1, ?, 1, 'cs', ?, 90, ?, ?)",
                     (f"pasmo-{i}", 10 + i, PROVIDER, date, now))
    # Sonarr took over the first episode's file half an hour before the subtitles (the speed card).
    conn.execute("INSERT INTO sonarr_event (arr_id, event_type, happened_at, title, episode, item_id, title_id) VALUES"
                 " (1, 'downloadFolderImported', '2026-11-06 23:00:00', 'Seriál pásma', 'S01E01', 10, 1)")
    conn.commit()
db.set_setting(access.LOGIN_KEY, "1")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
db.forget_settings()
space: dict[str, Any] = {"system/health": {"data": [{"object": "/data/tv", "issue": WARNING}]}}
substitute(bazarr, "BazarrClient", bazarr_client(bazarr_handler(None, space)))
connect_bazarr()
admin, reader, viewer = login("admin"), login("reader"), login("viewer")
ACCOUNTS = {"reader": reader, "viewer": viewer}
ALL = [o.key for o in access.AREAS]
WITHOUT_ARR = [o for o in ALL if o != "arr"]
CARDS = cards_bazarr.CARDS


def account(name: str) -> dict[str, Any]:
    return accounts.get_by_name(name)


def rights(name: str, areas: list[str] | None, anonymise: bool = False) -> None:
    """An account's rights explicitly (None = the default by group)."""
    accounts.save_permissions(account(name)["id"], None if areas is None else access.pack_permissions(areas, anonymise))
    db.forget_settings()


def zone(name: str) -> None:
    """Stores the time zone the way the form in Settings → General does."""
    admin.post("/settings/language", data={"ui_language": "cs", "app_timezone": name}, follow_redirects=False)


def got(client: Any, day: str, page: str = "/arr/bazarr") -> str | None:
    """The tile "Získané titulky" for one day (a custom period since-until)."""
    return tile(client.get(f"{page}?since={day}&until={day}").text, "Získané titulky")


def cards_data(period: Any = None, who: str = "admin") -> dict[str, Any]:
    whose = account(who)
    return cards.data_for(cards.allowed(list(CARDS), whose), period or stats.period_between("2026-11-01", "2026-11-10"), account=whose)


print("--- A) the time zone from Settings ---")
zone("UTC")
check(db.get_setting("app_timezone") == "UTC" and got(admin, "2026-11-06") == "1" and got(admin, "2026-11-07") == "0",
      f"UTC: the subtitles from Friday 23:30 belong to 6 November ({got(admin, '2026-11-06')}, {got(admin, '2026-11-07')})")
d = cards_data()
check(d["bazarr_when"]["heatmap_data"][4][23] == 1 and d["bazarr_when"]["heatmap_data"][2][12] == 1
      and {b["day"]: b["episodes"] for b in d["bazarr_by_day"]["body"] if b["episodes"]} == {"2026-11-04": 1, "2026-11-06": 1},
      "UTC: the grid Friday 23 h and Wednesday 12 h, the chart by day 4 and 6 November")
zone("Europe/Prague")
check(got(admin, "2026-11-06") == "0" and got(admin, "2026-11-07") == "1",
      f"Prague (without a restart): the same subtitles belong to Saturday 7 November ({got(admin, '2026-11-06')}, {got(admin, '2026-11-07')})")
d = cards_data()
check(d["bazarr_when"]["heatmap_data"][5][0] == 1 and d["bazarr_when"]["heatmap_data"][2][13] == 1 and d["bazarr_when"]["heatmap_data"][4][23] == 0
      and {b["day"]: b["episodes"] for b in d["bazarr_by_day"]["body"] if b["episodes"]} == {"2026-11-04": 1, "2026-11-07": 1},
      "Prague: the grid Saturday 0 h and Wednesday 13 h, the chart by day 4 and 7 November")
zone("America/New_York")
d = cards_data()
check(d["bazarr_when"]["heatmap_data"][4][18] == 1 and d["bazarr_when"]["heatmap_data"][2][7] == 1 and got(admin, "2026-11-06") == "1",
      "New York: Friday 18 h and Wednesday 7 h")
cards.save_layout(["bazarr_numbers", "bazarr_by_day"])
zone("Europe/Prague")
check(got(admin, "2026-11-07", "/dashboard") == "1" and got(admin, "2026-11-06", "/dashboard") == "0",
      "the zone shows the same way in the cards of the custom dashboard")
empty_day = admin.get("/dashboard?since=2026-11-06&until=2026-11-06").text
check("V tomhle období se žádné titulky nezískaly." in card_html(empty_day, "bazarr_by_day"), "and a day without subtitles says so in the chart too")
speeds = {}
for time_zone in ("UTC", "Europe/Prague", "Asia/Tokyo"):
    zone(time_zone)
    speeds[time_zone] = stats.bazarr_speed(stats.period_between("2026-11-01", "2026-11-10"))["median"]
check(set(speeds.values()) == {1800}, f"from the file to the subtitles is half an hour in every zone - a difference of times does not depend on the zone ({speeds})")
zone("Europe/Prague")

print()
print("--- B) the accounts' rights ---")
PERIOD = "?since=2026-11-01&until=2026-11-10"
for name, client in ACCOUNTS.items():
    for label, areas, expected in (("default rights", None, None), ("with the *Arr area", ALL, True), ("without the *Arr area", WITHOUT_ARR, False)):
        rights(name, areas)
        may = access.account_sees(account(name), "arr")
        state = client.get("/arr/bazarr" + PERIOD, follow_redirects=False)
        in_menu = 'href="/arr/' in client.get("/account").text      # the *Arr section opens its first app
        card_count = len(cards.allowed(list(CARDS), account(name)))
        check((expected is None or may == expected) and (state.status_code == 200) == may == in_menu == bool(card_count)
              and (state.status_code in (200, 403)),
              f"{name}, {label}: the tab, the menu item and the cards {'are there' if may else 'are not'} ({state.status_code}, menu {in_menu}, cards {card_count})")
        if may:
            check(PROVIDER in state.text and WARNING not in state.text and "/data/tv" not in state.text
                  and "Aplikace hlásí varování" in card_html(state.text, "bazarr_health"),
                  f"{name}, {label}: sees the statistics, not the app's warning texts")
        else:
            check(PROVIDER not in client.get("/account").text and PROVIDER not in client.get("/").text,
                  f"{name}, {label}: the content of Bazarr's cards is not elsewhere either")
check(WARNING in card_html(admin.get("/arr/bazarr" + PERIOD).text, "bazarr_health"), "the administrator sees the warning texts")
# Hidden names: Bazarr's cards say nothing about people, anonymising must not rewrite anything in them.
model = cards_data(who="admin")
for name, client in ACCOUNTS.items():
    rights(name, ALL, anonymise=True)
    page = client.get("/arr/bazarr" + PERIOD).text
    hidden = access.anonymise_for(cards_data(who=name), account(name))
    same = all(hidden[k] == model[k] for k in ("bazarr_providers", "bazarr_languages", "bazarr_numbers", "bazarr_when", "bazarr_providers_now",
                                               "bazarr_missing_languages"))
    check(access.hides_names(account(name)) and same and PROVIDER in page and "Divák " not in card_html(page, "bazarr_providers"),
          f"{name} with hidden names: Bazarr's cards unchanged (the provider is not „Divák N“)")
    rights(name, None)

print()
print("--- C) the dashboard for everybody ---")


def offered(text: str) -> list[str]:
    return re.findall(r'data-add="(bazarr_\w+)"', text)


builder = admin.get("/dashboard").text
check(sorted(offered(builder)) == sorted(k.key for k in CARDS if k.key != "bazarr_speed"),
      f"the administrator is offered thirteen of Bazarr's cards when composing - the speed only with Sonarr or Radarr ({len(offered(builder))})")
connect_sonarr()
db.forget_settings()
check(sorted(offered(admin.get("/dashboard").text)) == sorted(k.key for k in CARDS), "with Sonarr all fourteen")
cards.save_layout(["summary", "bazarr_numbers", "bazarr_providers"])
db_queries: list[str] = []
original_query_all = db.query_all


def _listen(sql: str, *a: Any, **k: Any) -> Any:
    db_queries.append(sql)
    return original_query_all(sql, *a, **k)


def subtitle_queries(client: Any, address: str) -> tuple[str, int]:
    db_queries.clear()
    db.query_all = _listen
    try:
        text = client.get(address).text
    finally:
        db.query_all = original_query_all
    return text, sum("bazarr_event" in q for q in db_queries)


for name, client in ACCOUNTS.items():
    rights(name, ALL)
    text, queries = subtitle_queries(client, "/dashboard" + PERIOD)
    check(re.findall(r"<!-- card: (\w+) -->", text) == ["summary", "bazarr_numbers", "bazarr_providers"] and PROVIDER in text
          and "Bazarr · " in card_html(text, "bazarr_providers") and queries >= 1 and 'data-add=' not in text,
          f"{name} with the *Arr area: sees Bazarr's cards in the dashboard for everybody (cannot compose it)")
    rights(name, WITHOUT_ARR)
    text, queries = subtitle_queries(client, "/dashboard" + PERIOD)
    check(re.findall(r"<!-- card: (\w+) -->", text) == ["summary"] and PROVIDER not in text and queries == 0,
          f"{name} without the *Arr area: Bazarr's cards are neither drawn nor counted for them, the rest of the dashboard stays ({queries} queries)")
    rights(name, [o for o in ALL if o != "dashboard"])
    check(client.get("/dashboard", follow_redirects=False).status_code in (303, 403), f"{name} without the Custom dashboard area does not get to it")

print()
print("--- C) My dashboard ---")
for name, client in ACCOUNTS.items():
    account_id = account(name)["id"]
    rights(name, ALL)
    mine = client.get("/account/dashboard").text
    check(sorted(offered(mine)) == sorted(k.key for k in CARDS), f"{name} with the *Arr area: My dashboard offers all fourteen of Bazarr's cards ({len(offered(mine))})")
    client.post("/account/dashboard", data={"order": "bazarr_numbers:cela,bazarr_providers:pul"}, follow_redirects=False)
    mine = client.get("/account/dashboard" + PERIOD).text
    check([s.key for s in cards.load_layout(account_id)] == ["bazarr_numbers", "bazarr_providers"] and PROVIDER in mine
          and tile(mine, "Získané titulky") == "2", f"{name}: added the cards and sees data in them")
    # The right goes: the cards stay in place with a sentence, the data is not counted and no more are offered.
    rights(name, WITHOUT_ARR)
    mine, queries = subtitle_queries(client, "/account/dashboard" + PERIOD)
    clean = html_lib.unescape(mine)
    check(clean.count("Nemáš oprávnění k oblasti „*Arr“") == 2 and PROVIDER not in mine and tile(mine, "Získané titulky") is None
          and queries == 0 and offered(mine) == [],
          f"{name} after losing the right: a sentence about the right instead of the cards, no data, an offer without Bazarr's cards ({queries} queries)")
    client.post("/account/dashboard", data={"order": "bazarr_numbers:cela,bazarr_providers:pul,bazarr_languages:pul,summary:full"},
                follow_redirects=False)
    stored = [s.key for s in cards.load_layout(account_id)]
    check("bazarr_languages" not in stored and stored[:2] == ["bazarr_numbers", "bazarr_providers"],
          f"{name} without the right does not add a Bazarr card even by a forged form; does not lose their two ({stored})")
    rights(name, ALL)
    check(PROVIDER in client.get("/account/dashboard" + PERIOD).text, f"{name}: after the right comes back the cards are back")
# Bazarr disconnected: a card in My dashboard stays with a sentence, in the dashboard for everybody it is not drawn.
bazarr.save_settings(False, "http://bazarr.test", "")
db.forget_settings()
mine = html_lib.unescape(reader.get("/account/dashboard" + PERIOD).text)
for_all = reader.get("/dashboard" + PERIOD).text
check("Bazarr není připojen" in mine and PROVIDER not in mine and "<!-- card: bazarr_numbers -->" not in for_all and offered(mine) == [],
      "a disconnected Bazarr: My dashboard says so at the cards, the dashboard for everybody leaves them out, the offer is without them")
check([s.key for s in cards.load_layout(account("reader")["id"])][:2] == ["bazarr_numbers", "bazarr_providers"], "and the layout stays")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
