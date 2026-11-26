# -*- coding: utf-8 -*-
r"""The Seerr page: cards, the live card "Čeká na vyřízení", the menu, two rights areas, anonymising.

Watched:

A) without a connected Seerr the page does not exist (404) and is missing from the menu;
B) the page's sections and cards ("Čeká na vyřízení", "Poslední žádosti",
   "Žádosti v období", "Požádal a dívá se?"); a requester paired with a Jellyscope viewer has
   their name and a link to the detail; the key nowhere. The cards themselves (the registry,
   posters, dashboards) are watched by tests/test_seerr_cards.py;
C) the area "…včetně kdo žádal" switched off (a reader by default): the card
   "Kdo žádá" is missing and the requester's name is nowhere in the page;
D) with "…kdo žádal" and anonymising the others' names hide;
E) the live cards ("Čeká na vyřízení", "Poslední žádosti") from the cache (a minute), a new key = a new load;
F) an unavailable Seerr: "Seerr neodpovídá", the statistics from the database go on, the latest requests
   from the database with a note about the last sync;
G) the whole history (?days=0) starts with the first request, not with the first play.

Seerr is not run: the answers are recorded from a real Seerr
(tests/seerr_responses/), the client is replaced by a client with httpx.MockTransport.

Run:
    .venv/bin/python tests/test_seerr_web.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "seerr-stranka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, seerr, web  # noqa: E402
from jellyscope.seerr import client as seerr_client  # noqa: E402

RESPONSES = PROJECT / "tests" / "seerr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> dict:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


REQUESTS = recorded("request")["results"]
asked: list[str] = []


def seerr_mock(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/api/v1/", 1)[-1]
    asked.append(f"{path}?{request.url.params.get('filter', '')}")
    if path == "request":
        if request.url.params.get("filter") == "pending":
            pending = recorded("request_pending")
            # The newest first, like Overseerr (it ignores sortDirection) - the page sorts them itself.
            return httpx.Response(200, json={**pending, "results": pending["results"][::-1]})
        return httpx.Response(200, json=recorded("request"))
    if path.startswith(("movie/", "tv/")):
        return httpx.Response(200, json={"title": "Film " + path.split("/")[1], "name": "Seriál " + path.split("/")[1]})
    if path == "settings/about":
        return httpx.Response(200, json=recorded("settings_about"))
    return httpx.Response(404)


def not_answering(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


def client_with(handler) -> type:
    class Client(seerr_client.SeerrClient):
        def __init__(self, url: str, key: str, transport=None, timeout=None) -> None:
            super().__init__(url, key, transport=httpx.MockTransport(handler), timeout=timeout)
    return Client


def menu(html: str) -> str:
    match = re.search(r"<nav[^>]*>.*?</nav>", html, re.S)
    return match.group(0) if match else ""


def tile(html: str, caption: str) -> str:
    match = re.search(rf'{re.escape(caption)}</p>\s*<div class="tile-value">([^<]+)</div>', html)
    return match.group(1).strip() if match else ""


# Jellyscope's viewers: Jana and Petr from Seerr (the same Jellyfin id, here with dashes),
# Eva is not a Jellyscope viewer.
def jellyfin_id(who: str) -> str:
    return next(z["requestedBy"]["jellyfinUserId"] for z in REQUESTS if z["requestedBy"]["jellyfinUsername"] == who)


JANA_ID = jellyfin_id("jana")
JANA_WITH_DASHES = f"{JANA_ID[:8]}-{JANA_ID[8:12]}-{JANA_ID[12:16]}-{JANA_ID[16:20]}-{JANA_ID[20:]}"
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES (?, 'Janička Testová')", (JANA_WITH_DASHES,))
    conn.execute("INSERT INTO users (id, name) VALUES (?, 'Petr Žadatel')", (jellyfin_id("petr"),))
    conn.commit()
NAMES = ("Janička Testová", "Petr Žadatel", ">eva<")

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")

print("--- A) Seerr is not connected ---")
check(admin.get("/seerr", follow_redirects=False).status_code == 404, "without Seerr /seerr = 404")
check(">Seerr<" not in menu(admin.get("/account").text), "the menu has no Seerr")

print()
print("--- B) the cards ---")
restore = substitute(seerr, "SeerrClient", client_with(seerr_mock))
seerr.save_settings(True, "http://seerr.test", "tajny-klic")
asyncio.run(seerr.synchronize())
asked.clear()
s = admin.get("/seerr?days=365").text
# The cards themselves (the registry, posters, dashboards) are watched by tests/test_seerr_cards.py.
check(all(t in s for t in ("Čeká na vyřízení", "Poslední žádosti", "<h2>Žádosti v období</h2>", "Kdo žádá",
                           "Jak rychle se vyřizují", "<h2>Požádal a dívá se?</h2>", "Nikdo nepustil")),
      "the page's sections and cards")
check('class="heatmap"' in s and "tajny-klic" not in s, "the heatmap; the key nowhere")
check("Janička Testová" in s and f'href="/users/{JANA_WITH_DASHES}"' in s, "a requester = a Jellyscope viewer, a name and a link")
check(">eva<" in s, "a requester without a Jellyscope viewer: the name from Seerr")
check(tile(s, "Žádostí") == str(len(REQUESTS)), f"tile: all the requests ({tile(s, 'Žádostí')})")
pending_part = s[s.index("Čeká na vyřízení"):]
check(0 < pending_part.find("Film 872585") < pending_part.find("Film 496243"),
      "the pending ones from the longest waiting, even though the application returns the newest first")
check(">Seerr<" in menu(admin.get("/account").text), "the menu has Seerr")


def card_html(page: str, title: str) -> str:
    """The HTML of the card with the heading `title`, up to the next card."""
    start = re.search(rf"{re.escape(title)}[^<]*</h2>", page).start()     # "Čeká na vyřízení (3)"
    end = page.find('<div class="card', start)
    return page[start:end if end != -1 else len(page)]


def poster_badges(html: str, frame_class: str) -> list[list[tuple[str, str]]]:
    """For every poster frame: its badges as (class, text)."""
    frames = re.findall(rf'<(?:a|span) class="{frame_class}[^"]*"[^>]*>(.*?)</(?:a|span)>\s*(?:<a|<div|<span class="poster-name|$)',
                        html, re.S)
    return [re.findall(r'<span class="(poster-badge[^"]*)"[^>]*>([^<]+)</span>', frame) for frame in frames]


# The type of the request on the poster, as "Recently added" marks an episode (the user, 23. 11. 2026): the badge in
# the top left corner; the state of a request moves to the bottom one.
KINDS = {"film", "seriál"}
for title, frame in (("Poslední žádosti", "poster-frame"), ("Nikdo nepustil", "poster-frame"),
                     ("Čeká na vyřízení", "session-poster")):
    badges = poster_badges(card_html(s, title), frame)
    check(badges and all(any(cls == "poster-badge" and text.strip() in KINDS for cls, text in b) for b in badges),
          f"{title}: every poster has the badge film/seriál ({badges[:3]})")
latest_badges = poster_badges(card_html(s, "Poslední žádosti"), "poster-frame")
check(all(any(cls == "poster-badge bottom" for cls, _text in b) for b in latest_badges),
      "Poslední žádosti: the state of the request in the bottom badge")
check({text.strip() for b in latest_badges for cls, text in b if cls == "poster-badge"} == KINDS,
      "the requests are movies and series, the badges say which")

print()
print("--- C) without “…kdo žádal” ---")
# A reader account sees everything by default (access._default) - the "…kdo žádal" switched off by default
# holds for viewers from Jellyfin. Here the reader is set explicitly.
DEFAULTS = [o.key for o in access.AREAS if o.default]
with db.connect() as conn:
    conn.execute("UPDATE accounts SET permissions = ? WHERE username = 'reader'", (access.pack_permissions(DEFAULTS, False),))
    conn.commit()
db.forget_settings()
check("seerr" in DEFAULTS and "seerr_who" not in DEFAULTS, "the Seerr area is on by default, “…kdo žádal” off")
reader = sign_in("reader")
s = reader.get("/seerr?days=365").text
check(reader.get("/seerr").status_code == 200 and "<h2>Žádosti v období</h2>" in s, "a reader sees the page (the Seerr area on)")
check("Kdo žádá" not in s and not any(j in s for j in NAMES),
      f"without “…kdo žádal”: no card and no requester's name ({['Kdo žádá' in s] + [j for j in NAMES if j in s]})")

print()
print("--- D) with “…kdo žádal” and anonymising ---")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET permissions = ? WHERE username = 'reader'",
                 (access.pack_permissions(DEFAULTS + ["seerr_who", "users"], True),))
    conn.commit()
db.forget_settings()
s = sign_in("reader").get("/seerr?days=365").text
check("Kdo žádá" in s and not any(j in s for j in NAMES), "the card is there, anonymising hid the names")
# A review finding: a requester who is not a Jellyscope viewer got the link /users/skryto-? with anonymising
# (anonymising took the request's id for a viewer's id). A link that leads nowhere is worse than none.
check('href="/users/skryto-?' not in s, "anonymising does not make a dead link to a viewer who is not there")

print()
print("--- E) the cache ---")
before = asked.count("request?pending")
admin.get("/seerr?days=365")
check(before == 1 and asked.count("request?pending") == 1, f"two pages within a minute = one request for the pending ones ({asked.count('request?pending')})")
# "Poslední žádosti" reads Seerr live too (26. 11. 2026), through the same one-minute cache.
check(asked.count("request?all") == 1, f"... and one request for the latest ones ({asked.count('request?all')})")
seerr.save_settings(True, "http://seerr.test", "novy-klic")
admin.get("/seerr?days=365")
check(asked.count("request?pending") == 2 and asked.count("request?all") == 2, "a new key = a new load at once")

print()
print("--- F) an unavailable Seerr ---")
restore()
restore = substitute(seerr, "SeerrClient", client_with(not_answering))
seerr.live.CACHE.clear()
s = admin.get("/seerr?days=365").text
check("Seerr neodpovídá" in s and "<h2>Žádosti v období</h2>" in s and "tajny-klic" not in s and "novy-klic" not in s,
      "the sentence “Seerr neodpovídá”, the statistics from the database go on, the key nowhere")
# A security finding (13. 11. 2026): the error's detail only for the administrator.
check("Connection refused" in s, "the administrator sees the error's detail")
latest_down = card_html(s, "Poslední žádosti")
check("Žádosti jsou z poslední synchronizace (" in latest_down and "Connection refused" not in latest_down
      and "Janička Testová" in latest_down, "the latest requests from the database, with a note that they are from the last sync")
seerr.live.CACHE.clear()
s_reader = sign_in("reader").get("/seerr?days=365").text
check("Seerr neodpovídá" in s_reader and "Connection refused" not in s_reader,
      "whoever is not the administrator sees only the sentence that Seerr does not answer - without details")
check("Žádosti jsou z poslední synchronizace (" in card_html(s_reader, "Poslední žádosti"), "... and the note under the latest requests too")
restore()

print()
print("--- G) the whole history ---")
with db.connect() as conn:
    conn.execute("INSERT INTO seerr_request (id, media_type, tmdb_id, title, status, requester_name, created_at)"
                 " VALUES (999, 'movie', '1', 'Starý film', 3, 'host', '2025-01-15 10:00:00')")
    conn.commit()
s = admin.get("/seerr?days=0").text
check(tile(s, "Žádostí") == str(len(REQUESTS) + 1), f"the whole history from the first request ({tile(s, 'Žádostí')})")
admin.get("/seerr?days=365")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
