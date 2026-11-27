# -*- coding: utf-8 -*-
"""Network traffic apart from where people watch from (27. 11. 2026).

The area "network" shows the traffic without an address or a place; its sub-area "network_origin" ("...including
where they watch from") adds the addresses, the map and the countries. Whoever had Network before the update gets
the sub-area once at start - groups, accounts' own rights and root keys - so nothing changes until the administrator
unticks it.

Run:
    .venv/bin/python tests/test_network_rights.py
    .venv/bin/python .superpowers/tools/test_on_pg.py tests/test_network_rights.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "network-rights.db")
os.environ["SECRET_KEY"] = "network-rights-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _network import refuse_network  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, cards, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


refuse_network()
db.init_db()

print("--- the area ---")
origin = access.BY_KEY.get("network_origin")
check(origin is not None and origin.parent == "network" and not origin.paths and origin.default is False,
      "network_origin is a sub-area of Network without addresses, off by default", f"({origin})")
check(not access.BY_KEY["network"].default, "Network itself stays off by default")
check("network_origin" in api.key_areas(), "a root key's tree offers it (a sub-area under an area with an address)")

print("--- the conversion at start: whoever had Network gets where people watch from ---")
db.set_setting(api.CONVERSION_KEY_AREAS, "")
db.set_setting(access.CONVERSION_NETWORK_KEY, "")
db.set_setting(api.CONVERSION_NETWORK_KEYS, "")
db.set_setting(access.area_key(access.READER, access.BY_KEY["network"]), "1")
db.set_setting(access.area_key(access.VIEWER, access.BY_KEY["network"]), "0")
db.forget_settings()
UNSTORED = "<unstored>"
with_network = accounts.create("with-network", "longpassword")
without_network = accounts.create("without-network", "longpassword")
accounts.save_permissions(with_network, access.pack_permissions(["network", "library"], True))
accounts.save_permissions(without_network, access.pack_permissions(["library"], False))
# Root keys stored as 1.8.x left them (before the conversion of 16. 11.): one with Network, one without.
old_network = api.create_token("old-network")
old_other = api.create_token("old-other")
with db.connect() as conn:
    conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?",
                 (json.dumps({"areas": ["network", "users", "dashboard"], "anonymise": False, "history": True}), old_network["id"]))
    conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?",
                 (json.dumps({"areas": ["users"], "anonymise": True, "history": False}), old_other["id"]))
    conn.commit()
# The order of web/start.py: the areas a key could use until 1.9 first, then the sub-area.
check(api.drop_unusable_key_areas() is True, "the old keys' conversion ran")
check(access.split_network_rights() is True and api.add_network_origin_to_keys() is True, "both network conversions ran now")
db.forget_settings()
check(db.get_setting(access.area_key(access.READER, origin), "") == "1", "the readers had Network: they have the sub-area")
check(db.get_setting(access.area_key(access.VIEWER, origin), "") == "0", "the viewers did not: they do not")
# A group whose Network was never stored follows the defaults: nothing is written for it (a second database state).
db.set_setting(access.CONVERSION_NETWORK_KEY, "")
with db.connect() as conn:
    conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (access.area_key(access.VIEWER, access.BY_KEY["network"]),
                                                             access.area_key(access.VIEWER, origin)))
    conn.commit()
db.forget_settings()
access.split_network_rights()
db.forget_settings()
check(db.get_setting(access.area_key(access.VIEWER, origin), UNSTORED) == UNSTORED,
      "a group whose Network was never stored: nothing written, the defaults hold")
db.set_setting(access.area_key(access.VIEWER, access.BY_KEY["network"]), "0")
db.set_setting(access.area_key(access.VIEWER, origin), "0")
db.forget_settings()
check(access.own_permissions(accounts.get(with_network))["areas"] == {"library", "network", "network_origin"}
      and access.own_permissions(accounts.get(with_network))["anonymise"] is True,
      "an account's own rights with Network get it, the rest as it was")
check(access.own_permissions(accounts.get(without_network))["areas"] == {"library"}, "an account without Network: unchanged")


def stored_key(token_id: int) -> dict:
    return json.loads(db.query_value("SELECT permissions FROM api_tokens WHERE id = ?", (token_id,), default="{}") or "{}")


check(stored_key(old_network["id"]) == {"areas": ["network", "network_origin", "users"], "anonymise": False, "history": True},
      "an old root key with Network survives the old conversion and gets the sub-area", f"({stored_key(old_network['id'])})")
check(stored_key(old_other["id"]) == {"areas": ["users"], "anonymise": True, "history": False},
      "a root key without Network: unchanged", f"({stored_key(old_other['id'])})")
check(access.split_network_rights() is False and api.add_network_origin_to_keys() is False, "the second run does nothing")
db.set_setting(access.area_key(access.READER, origin), "0")
db.forget_settings()
access.split_network_rights()
check(db.get_setting(access.area_key(access.READER, origin), "") == "0", "...and what the administrator set afterwards stays")
fresh_group = db.get_setting(access.area_key(access.VIEWER, origin), "<unstored>")
check(fresh_group == "0", "a group whose Network was stored as off gets the sub-area off")


print("--- the pages and the API ---")
from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def at(offset: timedelta) -> str:
    return (NOW + offset).strftime(db.TIME_FORMAT)


IP, LIVE_IP = "203.0.113.77", "198.51.100.9"
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES ('u-petr', 'PetrZQX', 0, 0),"
                 " ('u-jana', 'JanaZQX', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for key, uid, name, address, seconds in (("p", "u-petr", "PetrZQX", IP, 3600), ("j", "u-jana", "JanaZQX", "203.0.113.78", 7200)):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id, client,"
            " device_name, remote_address, play_method, started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
            " is_active, bitrate) VALUES (?, ?, ?, 'film-1', 'TitulQX', 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak', ?,"
            " 'DirectPlay', ?, ?, ?, ?, 0, 0, 8000000)",
            (key, uid, name, address, at(-timedelta(hours=5)), at(-timedelta(hours=3)), at(-timedelta(hours=3)), seconds))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id, client,"
        " device_name, remote_address, play_method, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " position_ticks, is_active, media_runtime_ticks, bitrate) VALUES ('live', 'u-jana', 'JanaZQX', 'film-1', 'TitulQX',"
        " 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak', ?, 'DirectPlay', ?, ?, 600, 0, 6000000000, 1, 72000000000, 8000000)",
        (LIVE_IP, at(-timedelta(minutes=10)), at(-timedelta(seconds=5))))
    conn.commit()
db.set_setting(access.LOGIN_KEY, "1")
accounts.create("boss", "longpassword", is_admin=True)
reader_id = accounts.create("reader", "longpassword")
db.forget_settings()


def reader_with(areas: list[str], anonymise: bool = False) -> TestClient:
    accounts.save_permissions(reader_id, access.pack_permissions(areas, anonymise))
    db.forget_settings()
    k = TestClient(web.app)
    k.post("/login", data={"username": "reader", "password": "longpassword"})
    return k


TRAFFIC = ("network_numbers", "live_traffic", "peaks_by_day", "who_streamed", "by_player")
ORIGIN = ("origin", "map")
k = reader_with(["network"])
page = k.get("/network")
cards_on = {key for key in TRAFFIC + ORIGIN if f"<!-- card: {key} -->" in page.text}
check(page.status_code == 200 and cards_on == set(TRAFFIC), f"Network alone: the traffic's cards, not where from ({sorted(cards_on)})")
check(IP not in page.text and LIVE_IP not in page.text, "...and no IP address on the page")
# /api/now-playing belongs to Now playing; the addresses in it to where they watch from.
live = reader_with(["network", "overview", "now_playing"]).get("/api/now-playing").json()
check(live["sessions"] and all("remote_address" not in s and "ip" not in s for s in live["sessions"]),
      "/api/now-playing without the addresses", f"({live})")
k = reader_with(["network", "network_origin"])
page = k.get("/network")
cards_on = {key for key in TRAFFIC + ORIGIN if f"<!-- card: {key} -->" in page.text}
check(cards_on == set(TRAFFIC + ORIGIN) and IP in page.text, f"with where they watch from: the addresses and the map too ({sorted(cards_on)})")
check(any(s.get("remote_address") == LIVE_IP
          for s in reader_with(["network", "network_origin", "overview", "now_playing"]).get("/api/now-playing").json()["sessions"]),
      "...and /api/now-playing has the address")
k = reader_with(["network_origin", "dashboard"])
check(k.get("/network").status_code in (303, 403), "where they watch from without Network: no Network page")
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout(["map", "origin", "summary"])
db.forget_settings()
leaked = [path for path in ("/dashboard", "/partials/cards?keys=map,origin")
          if any(mark in k.get(path).text for mark in ("<!-- card: map -->", "<!-- card: origin -->", IP))]
check(not leaked, f"...nor the map or the addresses in the dashboards: a sub-area needs its parent ({leaked})")
cards.save_layout([])


def bandwidth(areas: list[str], query: str = "") -> tuple[int, dict]:
    token = api.create_token(f"net-{'-'.join(areas)}-{len(query)}", permissions=api.pack_key_permissions(areas, False, False))["token"]
    response = TestClient(web.app).get(f"/api/v1/bandwidth{query}", headers={"Authorization": f"Bearer {token}"})
    return response.status_code, response.json()


status_all, everything = 200, TestClient(web.app).get(
    "/api/v1/bandwidth", headers={"Authorization": f"Bearer {api.create_token('net-everything')['token']}"}).json()
status, data = bandwidth(["network"])
check(status == 200 and data["countries"] is None and data["origin"] is None
      and data["total_bytes"] == everything["total_bytes"] > 0 and data["peak_mbit"] == everything["peak_mbit"],
      "a key with Network: the numbers; home against internet and the countries null (the card 'Where they watch from')",
      f"({status} {data})")
status, data = bandwidth(["network", "network_origin"])
check(status == 200 and isinstance(data["countries"], list) and set(data["origin"] or {}) == {"home", "internet", "unknown"},
      "with where they watch from: home against internet and the list of countries", f"({status} {data})")
check(isinstance(everything["countries"], list), "a key with everything: the list too")
status, data = bandwidth(["network"], "?countries.plays[gte]=1")
check(status == 200 and data["countries"] is None, "a filter on the hidden countries is no 400, nor shows them", f"({status} {data})")
admin = TestClient(web.app)
admin.post("/login", data={"username": "boss", "password": "longpassword"})
settings_page = admin.get("/settings?section=api").text
form = settings_page[settings_page.index('id="new-key-smi"'):]
form = form[:form.index("</form>")]
check('name="sees_network_origin"' in form and "Adds home against internet and the countries to /api/v1/bandwidth." in form,
      "the new key's tree offers where they watch from with what it adds to the API")


print("--- who streamed the most follows the anonymising ---")


def who_streamed(html: str) -> str:
    start = html.find("<!-- card: who_streamed -->")
    stop = html.find("<!-- card:", start + 10)
    return html[start:stop if stop > 0 else start + 6000] if start >= 0 else ""


card = who_streamed(reader_with(["network"]).get("/network").text)
check("JanaZQX" in card and "PetrZQX" in card, "without anonymising: the real names", f"({card[:200]})")
page = reader_with(["network"], anonymise=True).get("/network").text
card = who_streamed(page)
check(card and "JanaZQX" not in page and "PetrZQX" not in page and "u-jana" not in page and "u-petr" not in page,
      "with hidden names: no real name nor id anywhere on the page", f"({card[:300]})")
check("Viewer 1" in card or "Viewer 2" in card, "...a substitute instead", f"({card[:300]})")
check('href="/users/u-' not in page, "...and no link leads to the real viewer")
viewer_id = accounts.create("viewer", "longpassword")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE id = ?", (viewer_id,))
    conn.commit()
accounts.save_permissions(viewer_id, access.pack_permissions(["network"], True))
db.forget_settings()
k = TestClient(web.app)
k.post("/login", data={"username": "viewer", "password": "longpassword"})
card = who_streamed(k.get("/network").text)
check("PetrZQX" in card and "JanaZQX" not in card, "a viewer sees themselves by name, the others not", f"({card[:300]})")

print("--- the dashboards ---")
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout(["network_numbers", "map", "origin"])
db.forget_settings()
k = reader_with(["network", "dashboard"])
shared = k.get("/dashboard").text
check("<!-- card: network_numbers -->" in shared and "<!-- card: map -->" not in shared and IP not in shared,
      "the shared dashboard: the traffic's card, not the map nor the addresses")
partial = k.get("/partials/cards?keys=map,origin,network_numbers").text
check("<!-- card: map -->" not in partial and "<!-- card: origin -->" not in partial and IP not in partial,
      "...nor when its cards are drawn again (/partials/cards)")
k.post("/account/dashboard", data={"order": "network_numbers,map"}, follow_redirects=False)
check([c.key for c in cards.load_layout(reader_id)] == ["network_numbers"], "My overview does not take a card the account may not see")
# Added while the account had where they watch from, then taken away: the card stays in place with a note why.
k = reader_with(["network", "network_origin", "dashboard"])
k.post("/account/dashboard", data={"order": "network_numbers,map"}, follow_redirects=False)
k = reader_with(["network", "dashboard"])
mine = k.get("/account/dashboard").text
check([c.key for c in cards.load_layout(reader_id)] == ["network_numbers", "map"] and "dash-no-permission" in mine
      and "where they watch from" in mine and IP not in mine,
      "My overview with the map added before: a note naming the area, no data")
k = reader_with(["network", "network_origin", "dashboard"])
shared = k.get("/dashboard").text
check("<!-- card: map -->" in shared and "<!-- card: origin -->" in shared and IP in shared,
      "with where they watch from: the map and the addresses in the shared dashboard too")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
