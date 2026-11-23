r"""The Overview (/) is drawn from the registry of cards - the same way as the custom dashboard.

The Overview used to have its cards put into the template by hand, and their rights were guarded
by hand with `vidi()`; two cards thus once slipped through unguarded. And because there were
two ways, they drifted apart: the cards' live behaviour (refreshing Now playing,
the Movies/Series switch) only the Overview had. Watched here:

* `/` has exactly the cards of `cards.OVERVIEW`, in this order, without the ones
  the account may not see (for a reader and a viewer, every area separately);
* the custom dashboard draws the same cards with the same live wrappers;
* a remembered Movies/Series choice (and "Ostatní") holds in the custom dashboard too;
* a "no right" card in My dashboard has no live wrapper.

Spec: docs/superpowers/specs/2026-11-06-prehled-z-registru-design.md

Run:
    .venv/bin/python tests/test_overview_from_registry.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prehled_registr.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, cards, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


NOW = datetime.now(UTC).replace(tzinfo=None)


def at(offset: timedelta) -> str:
    return (NOW + offset).strftime(db.TIME_FORMAT)


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.create("viewer", "longpassword")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-petr' WHERE username = 'viewer'")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-petr', 'Petr', 0, 0), ('u-jana', 'Jana', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, production_year, path, is_missing, date_created)"
                 " VALUES ('film-1', 'Film', 'Movie', 'filmy', 2024, '/media/filmy/Film.mkv', 0, ?)",
                 (at(-timedelta(days=1)),))
    for key, uid, name, kind in (("h1", "u-petr", "Petr", "Movie"), ("h2", "u-jana", "Jana", "Movie"),
                                 ("h3", "u-jana", "Jana", "TvChannel")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " client, device_name, play_method, started_at, last_seen_at, ended_at, watched_seconds,"
            " paused_seconds, is_active) VALUES (?, ?, ?, 'film-1', 'Film', ?, 'filmy', 'Jellyfin Web',"
            " 'Obyvak', 'DirectPlay', ?, ?, ?, 3600, 0, 0)",
            (key, uid, name, kind, at(-timedelta(hours=5)), at(-timedelta(hours=4)), at(-timedelta(hours=4))))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
        " client, device_name, play_method, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " position_ticks, is_active, media_runtime_ticks) VALUES ('zive', 'u-jana', 'Jana', 'film-1', 'Film',"
        " 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak', 'DirectPlay', ?, ?, 600, 0, 6000000000, 1, 72000000000)",
        (at(-timedelta(minutes=10)), at(-timedelta(seconds=5))))
    conn.commit()
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(access.LOGIN_KEY, "1")


def sign_in(name: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return k


def cards_on(html: str) -> list[str]:
    return re.findall(r"<!-- card: (\w+) -->", html)


WRAPPERS = re.compile(r'<div[^>]*id="(now-playing|recently-added|daily-card|top-items-card)"[^>]*>')


def wrappers(html: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in WRAPPERS.finditer(html)}


def active(html: str, kind: str) -> bool:
    return bool(re.search(rf'<a class="chip active"[^>]*?data-kind="{kind}"', html, re.S))


def piece(html: str, wrapper_id: str) -> str:
    """The piece of the page from the card's wrapper to the next wrapper (enough for its switch)."""
    i = html.find(f'id="{wrapper_id}"')
    return html[i:i + 6000] if i != -1 else ""


print("--- (a) the registry ---")
overview_keys = getattr(cards, "OVERVIEW", None)
check(bool(overview_keys) and all(k in cards.BY_KEY for k in overview_keys), f"cards.OVERVIEW has valid keys ({overview_keys})")
checker = getattr(cards, "check_card", None)
try:
    checker("neexistuje") if checker else None
    check(False, "check_card('neexistuje') throws a KeyError")
except KeyError:
    check(True, "check_card('neexistuje') throws a KeyError")
# A card from the registry that is not on the Overview (the map): in dashboard.html the route
# would not compute it and it would be quietly missing for everybody - so an error too, not silence.
try:
    checker("map") if checker else None
    check(False, "check_card('map') throws a KeyError (it is in the registry, not on the Overview)")
except KeyError:
    check(True, "check_card('map') throws a KeyError (it is in the registry, not on the Overview)")
check(checker is not None and checker("summary") == "", "check_card('summary') passes")
# A page with its own cards (*Arr, Seerr) passes which ones it computes.
try:
    check(checker is not None and checker("codecs", ["codecs"]) == "", "check_card('codecs', ['codecs']) passes: the page's cards")
except (KeyError, TypeError) as error:
    check(False, f"check_card('codecs', ['codecs']) passes: the page's cards ({error!r})")
try:
    checker("summary", ["codecs"]) if checker else None
    check(False, "check_card('summary', ['codecs']) throws a KeyError")
except KeyError:
    check(True, "check_card('summary', ['codecs']) throws a KeyError")
except TypeError as error:
    check(False, f"check_card('summary', ['codecs']) throws a KeyError ({error!r})")
OVERVIEW = list(overview_keys or [])

print()
print("--- (b) the administrator's Overview ---")
admin = sign_in("admin")
overview_html = admin.get("/").text
check(cards_on(overview_html) == OVERVIEW and len(OVERVIEW) == 10,
      f"/ has the OVERVIEW cards in order ({cards_on(overview_html)})")

print()
print("--- (c) a reader and a viewer, every area switched off separately ---")
ALL_AREAS = [o.key for o in access.AREAS]
for name in ("reader", "viewer"):
    for switched_off in [None] + ALL_AREAS:
        enabled = [o for o in ALL_AREAS if o != switched_off]
        with db.connect() as conn:
            conn.execute("UPDATE accounts SET permissions = ? WHERE username = ?",
                         (access.pack_permissions(enabled, False), name))
            conn.commit()
        account = accounts.get_by_name(name)
        r = sign_in(name).get("/", follow_redirects=False)
        if r.status_code != 200:
            continue
        want = [k for k in OVERVIEW if cards.may_see(cards.BY_KEY[k], account)]
        label = f"{name} without {switched_off}" if switched_off else f"{name} with everything"
        check(cards_on(r.text) == want and len(want) > 0, f"{label}: the cards on / by the rights ({cards_on(r.text)})")
        if not access.account_sees(account, "now_playing"):
            check('id="now-playing"' not in r.text, f"{label}: without Now playing not even its wrapper")
        if not access.account_sees(account, "library"):
            check('id="recently-added"' not in r.text, f"{label}: without the Library not even Recently added's wrapper")
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET permissions = NULL WHERE username = ?", (name,))
        conn.commit()

print()
print("--- (d) the custom dashboard with all the Overview's cards ---")
admin_id = accounts.get_by_name("admin")["id"]
cards.save_layout(OVERVIEW, admin_id)
r = admin.get("/account/dashboard")
check(r.status_code == 200, f"My dashboard with all ten cards is drawn ({r.status_code})")
check(cards_on(r.text) == OVERVIEW and len(OVERVIEW) == 10, f"and it has all ten cards ({cards_on(r.text)})")
on_overview, on_dashboard = wrappers(overview_html), wrappers(r.text)
check(len(on_overview) == 4 and on_dashboard == on_overview,
      f"the live wrappers are the same as on / ({sorted(on_dashboard)} vs {sorted(on_overview)})")

print()
print("--- (e) the remembered choices hold in the custom dashboard too ---")
admin.get("/video/partials/top-items?kind=movies")
r = admin.get("/account/dashboard")
check(active(piece(r.text, "top-items-card"), "movies"), "Most watched show the chosen Movies")
admin.get("/video/partials/daily?kind=other")
r = admin.get("/account/dashboard")
check(active(piece(r.text, "daily-card"), "other"), "Watching by day shows the chosen Ostatní")
check(active(piece(admin.get("/").text, "daily-card"), "other"), "and the same on /")

print()
print("--- (f) a card without the right in My dashboard has no live wrapper ---")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET permissions = ? WHERE username = 'viewer'",
                 (access.pack_permissions([o for o in ALL_AREAS if o != "now_playing"], False),))
    conn.commit()
cards.save_layout([("now_playing", ""), ("most_watched", "")], accounts.get_by_name("viewer")["id"])
r = sign_in("viewer").get("/account/dashboard")
check(r.status_code == 200 and "dash-no-permission" in r.text, f"the Now playing card is \"without the right\" ({r.status_code})")
check('data-partial="/partials/now-playing"' not in r.text and 'id="now-playing"' not in r.text,
      "and it has no wrapper that would ask for the forbidden partial")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
