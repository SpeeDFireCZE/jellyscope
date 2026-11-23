# -*- coding: utf-8 -*-
r"""Dashboards: a named dashboard for everybody, My dashboard, the cards' rights.

Design: docs/superpowers/specs/2026-10-16-prehledy-design.md.

* **The dashboard for everybody** (formerly "Vlastní přehled") has a name the
  administrator enters. It is in the menu and in the heading; without it the dashboard cannot be switched on.
  An existing switched-on dashboard without a name gets "Vlastní přehled".

Run:
    .venv/bin/python tests/test_dashboards.py
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
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prehledy.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _package import sample_movie  # noqa: E402
from jellyscope import access, accounts, cards, db, jellyfin, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def translations_of(sentence: str) -> list[str]:
    """The sentence in all the interface's languages (English is the key itself)."""
    result = [sentence]
    for language in ("cs", "es", "de"):
        path = PROJECT / "jellyscope" / "translations" / f"{language}.json"
        translation = json.loads(path.read_text(encoding="utf-8")).get(sentence)
        if translation:
            result.append(translation)
    return result


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()          # the Movies & series section exists only with a movie (navigation.has_video)
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass")
db.forget_settings()


def sign_in(name: str, password: str) -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


admin = sign_in("admin", "longpassword")
reader = sign_in("reader", "readerpass")


def set_interface(client: TestClient, **fields) -> int:
    return client.post("/settings/interface", data={k: str(v) for k, v in fields.items()},
                       follow_redirects=False).status_code


def menu_link(html: str, address: str) -> str:
    """The text of the link in the menu (without tags), or empty."""
    match = re.search(r'<a[^>]*href="' + re.escape(address) + r'"[^>]*>(.*?)</a>', html, re.S)
    return re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else ""


def name_refused(text: str) -> bool:
    try:
        cards.check_dashboard_name(text)
    except ValueError:
        return True
    return False


print("--- the name: the check ---")
check(cards.check_dashboard_name("  Rodinný   přehled ") == "Rodinný přehled",
      "it trims the edges and merges spaces")
check(name_refused("") and name_refused("   "), "an empty name does not pass")
check(cards.check_dashboard_name("x" * 40) == "x" * 40 and name_refused("x" * 41),
      "at most 40 characters")
check(cards.check_dashboard_name("Ž" * 40) == "Ž" * 40, "characters count, not bytes")
forbidden = translations_of("Overview") + translations_of("My overview") + ["přehled", "MŮJ PŘEHLED"]
check(len(forbidden) >= 7, f"forbidden names in all languages ({forbidden})")
check(all(name_refused(z) for z in forbidden),
      "the name must not be confused with the Overview or with My dashboard in any language")

print("--- the name: Settings -> Interface ---")
set_interface(admin, ui_dashboard=1, dashboard_name="")
db.forget_settings()
check(not cards.is_enabled(), "without a name the dashboard does not switch on")
check("potřebuje název" in admin.get("/settings?section=interface").text,
      "and the message says why")
check(set_interface(admin, ui_dashboard=1, dashboard_name="Rodina") == 303,
      "with a name it passes")
db.forget_settings()
check(cards.is_enabled() and cards.dashboard_name() == "Rodina", "switched on and named Rodina")
check('value="Rodina"' in admin.get("/settings?section=interface").text,
      "the name is filled in the form")

set_interface(admin, ui_dashboard=1, dashboard_name="Přehled")
db.forget_settings()
check(cards.is_enabled() and cards.dashboard_name() == "Rodina",
      "an invalid name for a switched-on dashboard: the old one stays")

# A switched-off dashboard remembers its old name. Switching it on with an invalid one must not work though:
# the message says "it does not pass" and the dashboard must not quietly come up under the old name.
set_interface(admin, ui_dashboard=0, dashboard_name="Rodina")
for invalid in ("Přehled", ""):
    set_interface(admin, ui_dashboard=1, dashboard_name=invalid)
    db.forget_settings()
    check(not cards.is_enabled() and cards.dashboard_name() == "Rodina",
          f"switching on with an invalid name ({invalid!r}): it stays off")
set_interface(admin, ui_dashboard=1, dashboard_name="Rodina")
db.forget_settings()

history = admin.get("/history").text
check(menu_link(history, "/dashboard") == "Rodina", "the menu has the name, not “Vlastní přehled”")
page = admin.get("/dashboard").text
check("<h1>Rodina</h1>" in page, "the page's heading is the name")
check(re.search(r"<title>[^<]*Rodina", page) is not None, "the window title too")
accounts_page = admin.get("/settings?section=accounts").text
check(re.search(r'class="tree-section"[^>]*>\s*Rodina\s*<', accounts_page) is not None,
      "the rights tree shows which page it allows: the heading is the name from the menu")

set_interface(admin, ui_dashboard=1, dashboard_name="<script>x</script>")
db.forget_settings()
history = admin.get("/history").text
page = admin.get("/dashboard").text
check(cards.dashboard_name() == "<script>x</script>", "the name is stored as it is")
check("<script>x</script>" not in history and "&lt;script&gt;x&lt;/script&gt;" in history
      and "<h1>&lt;script&gt;x&lt;/script&gt;</h1>" in page,
      "in the menu and the heading it is escaped")
set_interface(admin, ui_dashboard=1, dashboard_name="Rodina")

print("--- the name: an existing installation ---")
db.set_setting(cards.NAME_KEY, "")
db.set_setting(cards.ENABLED_KEY, "1")
db.forget_settings()
check(cards.fill_in_dashboard_name() is True and cards.dashboard_name() == "Vlastní přehled",
      "a switched-on dashboard without a name gets “Vlastní přehled”")
check(cards.fill_in_dashboard_name() is False and cards.dashboard_name() == "Vlastní přehled",
      "the second start no longer changes anything")
db.set_setting(cards.NAME_KEY, "Kino")
db.forget_settings()
check(cards.fill_in_dashboard_name() is False and cards.dashboard_name() == "Kino", "a renamed one is not overwritten")
db.set_setting(cards.NAME_KEY, "")
db.set_setting(cards.ENABLED_KEY, "0")
db.forget_settings()
check(cards.fill_in_dashboard_name() is False and cards.dashboard_name() == "",
      "a switched-off dashboard stays without a name")


print("--- the cards' rights ---")
# A card's area = the area of the page the card comes from (the table in the spec).
EXPECTED_AREAS = {
    "overview": {"summary", "tiles", "watching_by_day", "most_watched",
                "most_active_users", "delivery", "players", "when_watched"},
    "now_playing": {"now_playing"},
    "library": {"recently_added", "library_total", "library_growth", "codecs",
                 "resolutions", "dynamic_range"},
    "network": {"live_traffic", "peaks_by_day", "who_streamed", "by_player", "origin"},
    "languages": {"language_share", "dubbing", "subtitles", "library_languages"},
}
invalid_areas = [s.key for s in cards.ALL_CARDS if getattr(s, "area", None) not in access.BY_KEY]
check(not invalid_areas, f"every card has a valid rights area ({invalid_areas})")
differences = [(key, area, getattr(cards.BY_KEY.get(key), "area", None))
               for area, keys in EXPECTED_AREAS.items() for key in keys
               if getattr(cards.BY_KEY.get(key), "area", None) != area]
check(not differences, f"the areas of the 24 existing cards by the spec ({differences})")

NETWORK_KEY = access.area_key(access.READER, access.BY_KEY["network"])
SUMMARY, LOCATIONS = cards.BY_KEY["summary"], cards.BY_KEY["origin"]


def account(name: str) -> dict:
    db.forget_settings()
    return accounts.get_by_name(name) or {}


db.set_setting(NETWORK_KEY, "0")
check(cards.may_see(LOCATIONS, account("admin")) is True, "the administrator may see every card")
check(cards.may_see(LOCATIONS, account("reader")) is False and cards.may_see(SUMMARY, account("reader")) is True,
      "a reader without the Network area may not see a card from Network, a card from the Overview yes")
check(cards.allowed([SUMMARY, LOCATIONS], account("reader")) == [SUMMARY],
      "allowed() keeps only the cards the account may see")

with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
                 " VALUES ('u-karel', 'Karel', 0, 0, ?)", (db.utcnow(),))
    conn.execute("INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
                 " started_at, last_seen_at, watched_seconds, remote_address, is_active)"
                 " VALUES ('s1', 'u-karel', 'Karel', 'i-1', 'Titul', ?, ?, 1800, '10.0.0.5', 0)",
                 (db.utcnow(), db.utcnow()))
    conn.commit()
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Rodina")
cards.save_layout(["summary", "origin"])


def cards_on_page(html: str) -> list[str]:
    return re.findall(r'class="dash-card[^"]*" data-card="(\w+)"', html)


# A forbidden card's data is not even computed.
locations_calls: list[int] = []
original_locations = cards.stats.watching_locations


def watched_locations(*args, **kwargs):
    locations_calls.append(1)
    return original_locations(*args, **kwargs)


cards.stats.watching_locations = watched_locations
page = reader.get("/dashboard").text
cards.stats.watching_locations = original_locations
check(cards_on_page(page) == ["summary"],
      f"a reader without Network: only allowed cards on the dashboard for everybody ({cards_on_page(page)})")
check("10.0.0.5" not in page and "Odkud se dívají" not in page,
      "not a piece of the forbidden card's data (no IP address)")
check(not locations_calls, "a forbidden card's data is not computed")
check("data-add=" not in page, "a reader has no dialog with cards (as today)")

admin_page = admin.get("/dashboard").text
check(cards_on_page(admin_page) == ["summary", "origin"]
      # "All" = all available: cards of an app that is not connected (Sonarr,
      # Radarr) are not offered - see test_custom_dashboard.py and test_sonarr_cards.py.
      and all(f'data-add="{s.key}"' in admin_page for s in cards.ALL_CARDS if cards.is_available(s))
      and not any(f'data-add="{s.key}"' in admin_page for s in cards.ALL_CARDS if not cards.is_available(s)),
      "the administrator sees both cards and the dialog offers all available ones")

db.set_setting(NETWORK_KEY, "1")
page = reader.get("/dashboard").text
check(cards_on_page(page) == ["summary", "origin"] and "10.0.0.5" in page,
      "after the Network area is given back the card is back, address included")

# Whoever may not see a single card in the dashboard does not have it in the menu - an empty page
# with "nothing in it yet" would lie. The administrator always has it in the menu.
cards.save_layout(["origin"])
db.set_setting(NETWORK_KEY, "0")
check(menu_link(reader.get("/history").text, "/dashboard") == "",
      "a reader who may see nothing in the dashboard does not have it in the menu")
response = reader.get("/dashboard", follow_redirects=False)
check(response.status_code == 303 and response.headers.get("location") == "/",
      "and the direct address sends them back to the Overview, not to “nothing in it yet”")
check(menu_link(admin.get("/history").text, "/dashboard") == "Rodina",
      "the administrator yes")


# After signing in it goes to the dashboard only when the account sees something in it. Otherwise
# it would land on an empty page it does not even have in the menu.
def where_after_sign_in(name: str, password: str) -> str:
    response = TestClient(web.app).post(
        "/login", data={"username": name, "password": password, "method": "local"},
        follow_redirects=False)
    return response.headers.get("location", "")


check(where_after_sign_in("reader", "readerpass") == "/video/overview",
      "a reader who may see nothing in the dashboard lands on the Overview after signing in")
check(where_after_sign_in("admin", "longpassword") == "/dashboard",
      "the administrator after signing in on the dashboard for everybody")
db.set_setting(NETWORK_KEY, "1")
check(menu_link(reader.get("/history").text, "/dashboard") == "Rodina",
      "with the Network area the reader has it in the menu again")
check(where_after_sign_in("reader", "readerpass") == "/dashboard",
      "with the Network area after signing in on the dashboard again")


# Landing must be only where the account may go - a "you have no access here" page right
# after entering the password is the worst possible welcome.
def landing(name: str, password: str) -> tuple[str, int]:
    """Where a person ends up after signing in (after redirects too) and with what code."""
    response = TestClient(web.app).post(
        "/login", data={"username": name, "password": password, "method": "local"})
    return response.url.path, response.status_code


DASHBOARD_KEY = access.area_key(access.READER, access.BY_KEY["dashboard"])
OVERVIEW_KEY = access.area_key(access.READER, access.BY_KEY["overview"])
db.set_setting(DASHBOARD_KEY, "0")
check(landing("reader", "readerpass") == ("/video/overview", 200),
      "a reader without the area of the dashboard for everybody lands on the Overview, not on 403")
db.set_setting(OVERVIEW_KEY, "0")
where, status = landing("reader", "readerpass")
check(where not in ("/video/overview", "/dashboard") and status == 200,
      f"without the Overview on the first page they may open ({where} {status})")
db.set_setting(DASHBOARD_KEY, "1")
db.set_setting(OVERVIEW_KEY, "1")
check(landing("reader", "readerpass") == ("/dashboard", 200),
      "with both areas on the dashboard for everybody again")
cards.save_layout(["summary", "origin"])


print("--- My dashboard ---")
ADMIN_ID, READER_ID = account("admin")["id"], account("reader")["id"]


def my_dashboard_card(html: str) -> str:
    """The text of the button on the My dashboard card on the My account page, or empty."""
    card = re.search(r'id="my-dashboard".*?</div>\s*</div>', html, re.S)
    button = re.search(r'<a[^>]*href="/account/dashboard"[^>]*>(.*?)</a>', card.group(0), re.S) \
        if card else None
    return re.sub(r"<[^>]+>", "", button.group(1)).strip() if button else ""


page = reader.get("/account/dashboard")
check(page.status_code == 200 and "Můj přehled je zatím prázdný." in page.text
      and 'data-modal="modal-layout"' in page.text,
      "an empty My dashboard: a prompt and a button for adding cards")
# On a computer the surface is a grid of six columns. The prompt without a rule of its own
# took one of them - a narrow tile with the text word by word.
rule = re.search(r"\.dash-prompt\s*\{([^}]*)\}",
                 (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8"))
check(bool(rule) and "grid-column: 1 / -1" in rule.group(1),
      "the empty dashboard's prompt spans the whole width of the grid")
check(cards.load_layout(READER_ID) == [],
      "an empty one stays empty - it does not borrow the layout of the dashboard for everybody")
check(menu_link(reader.get("/history").text, "/account/dashboard") == "",
      "an empty My dashboard is not in the menu")
check(my_dashboard_card(reader.get("/account").text) == "Poskládat",
      "on My account a card with the button Poskládat")

saving = reader.post("/account/dashboard", data={"order": "summary:full,codecs:half"},
                     follow_redirects=False)
check(saving.status_code == 303, "a reader saves their My dashboard")
check(cards_on_page(reader.get("/account/dashboard").text) == ["summary", "codecs"],
      "and sees their cards in it")
check(menu_link(reader.get("/history").text, "/account/dashboard") == "Můj přehled",
      "with cards it is in the menu")
check(my_dashboard_card(reader.get("/account").text) == "Otevřít", "on My account the button Otevřít")
check(cards_on_page(reader.get("/dashboard").text) == ["summary", "origin"],
      "the dashboard for everybody did not change by it")

# Review Focus 1: somebody else's My dashboard only from the session, never from the address or the form.
cards.save_layout(["tiles"], ADMIN_ID)
reader.post("/account/dashboard", data={"order": "codecs", "account_id": str(ADMIN_ID)},
            follow_redirects=False)
check([s.key for s in cards.load_layout(ADMIN_ID)] == ["tiles"]
      and [s.key for s in cards.load_layout(READER_ID)] == ["codecs"],
      "account_id in the form is ignored: only one's own changes")
check(cards_on_page(reader.get(f"/account/dashboard?account_id={ADMIN_ID}").text) == ["codecs"],
      "account_id in the address is ignored: everybody sees their own")

# A viewer from Jellyfin may save My dashboard, other POSTs still not.
def jellyfin_server(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if (body.get("Username"), body.get("Pw")) == ("mirek", "tajne"):
        return httpx.Response(200, json={"User": {
            "Id": "u-mirek", "Name": "mirek", "Policy": {"IsAdministrator": False}}})
    return httpx.Response(401, json={})


async def fake_client(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(jellyfin_server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = fake_client
db.set_setting(access.LOGIN_KEY, "1")
viewer = TestClient(web.app)
viewer.post("/login", data={"username": "mirek", "password": "tajne", "method": "jellyfin"},
            follow_redirects=False)
check(viewer.post("/account/dashboard", data={"order": "codecs"},
                  follow_redirects=False).status_code == 303,
      "a viewer from Jellyfin saves their My dashboard (303)")
check(viewer.post("/dashboard/layout", data={"order": "codecs"},
                  follow_redirects=False).status_code == 403,
      "the layout for everybody still not (403)")

# Review Focus 2: a removed right - a message instead of data, no IP address.
reader.post("/account/dashboard", data={"order": "summary,origin"}, follow_redirects=False)
db.set_setting(NETWORK_KEY, "0")
page = reader.get("/account/dashboard").text
check("Nemáš oprávnění k oblasti „Síť“" in page
      and "Odkud se dívají" in page,
      "a card outside the rights: a message with the card's name and the area")
check("10.0.0.5" not in page, "and not a piece of its data")
check(cards_on_page(page) == ["summary", "origin"], "the card stays in its place")
check('data-add="origin"' not in page
      and re.search(r'class="dash-tile[^"]*"\s+data-card="origin"', page) is not None,
      "the dialog does not offer it, but it is in the layout, so it can be removed")
db.set_setting(NETWORK_KEY, "1")
page = reader.get("/account/dashboard").text
check("10.0.0.5" in page and "Nemáš oprávnění k oblasti" not in page,
      "after the Network area is given back the card is back with data")

# By a forged form an account does not add a card without the right; one it already
# has in the dashboard it does not lose by rearranging though.
cards.save_layout(["summary"], READER_ID)
db.set_setting(NETWORK_KEY, "0")
reader.post("/account/dashboard", data={"order": "summary,origin"}, follow_redirects=False)
check([s.key for s in cards.load_layout(READER_ID)] == ["summary"],
      "it does not add a card without the right by a forged form")
cards.save_layout(["summary", "origin"], READER_ID)
reader.post("/account/dashboard", data={"order": "origin,summary"}, follow_redirects=False)
check([s.key for s in cards.load_layout(READER_ID)] == ["origin", "summary"],
      "a card it already has it does not lose by rearranging")
db.set_setting(NETWORK_KEY, "1")

temporary = accounts.create("docasny", "temporarypass")
cards.save_layout(["codecs"], temporary)
accounts.delete(temporary)
leftover = db.query_one("SELECT COUNT(*) AS n FROM dashboard_layout WHERE account_id = ?", (temporary,))
check(int(leftover["n"]) == 0, "a deleted account takes its My dashboard with it")

print()
print("--- downloading GeoIP only for the administrator ---")
# POST /network/geoip is for the administrator only. The button for anybody else would
# lead to "you have no access here".
check('action="/network/geoip"' in admin.get("/network").text,
      "the administrator has the button for downloading GeoIP on the Network page")
check('action="/network/geoip"' not in reader.get("/network").text,
      "a reader does not")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
