# -*- coding: utf-8 -*-
r"""Accessible names: what a screen reader says about a window, a field and a choice.

Nothing on the screen shows these names, so nothing fails when one is missing - a screen reader only
says "dialog" for every window, "edit text" for a search field and reads the options of a choice
without its question ("Rounded", "Exact" - of what?). This test draws the pages over sample data
(the *Arr apps and Seerr through recorded answers, music and books, a dashboard with every card,
every section of Settings, the sign-in page) and walks their HTML:

A) every <dialog> has a name: aria-labelledby pointing at an element of the page (its heading),
   or aria-label;
B) every field a person fills in or picks from (input, select, textarea - not hidden fields and
   buttons) has a name: a <label for>, a <label> around it, aria-label, aria-labelledby or title;
   a placeholder does not count, it disappears as soon as one types;
C) every set of radio buttons is a group with a name: role="radiogroup" (or a fieldset with a legend)
   named by its question;
D) the one tooltip of the page is not a live region - hovering a chart announced every value it showed;
E) every <dialog> in the templates, the ones the sample data does not draw included, is named by
   aria-labelledby after an id of the same template.

Run:
    .venv/bin/python tests/test_accessible_names.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "accessible-names.db")
os.environ["SECRET_KEY"] = "test-key-accessible-names-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from _apps import (  # noqa: E402
    bazarr_client, bazarr_handler, connect_bazarr, connect_lidarr, connect_radarr, connect_seerr, connect_sonarr, fill_bazarr,
    fill_lidarr, fill_radarr, fill_seerr, fill_sonarr, lidarr_client, lidarr_handler, login, no_network, radarr_client,
    radarr_handler, seerr_client, seerr_handler, sonarr_client, sonarr_handler,
)
from _books import fill_books  # noqa: E402
from _music import fill_music  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, api, cards, db, demodata, seerr, updates  # noqa: E402
from jellyscope.arr import bazarr, lidarr, radarr, sonarr  # noqa: E402
from jellyscope.web import SETTINGS_SECTIONS, app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
demodata.seed()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
no_network()
substitute(sonarr, "SonarrClient", sonarr_client(sonarr_handler()))
substitute(radarr, "RadarrClient", radarr_client(radarr_handler()))
substitute(lidarr, "LidarrClient", lidarr_client(lidarr_handler()))
substitute(seerr, "SeerrClient", seerr_client(seerr_handler()))
substitute(bazarr, "BazarrClient", bazarr_client(bazarr_handler()))
for connect, fill in ((connect_sonarr, fill_sonarr), (connect_radarr, fill_radarr), (connect_lidarr, fill_lidarr),
                      (connect_seerr, fill_seerr), (connect_bazarr, fill_bazarr)):
    connect()
    fill()
fill_music()
fill_books()
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout([card.key for card in cards.ALL_CARDS])
cards.save_layout([("now_playing", "full"), ("most_watched", "half")], accounts.get_by_name("admin")["id"])
api.create_token("Grafana")
# A found update: the update window is drawn on every page.
db.set_setting(updates.FOUND_VERSION_KEY, "9.9.9")
db.set_setting(updates.FOUND_URL_KEY, "https://example.invalid/9.9.9")
db.set_setting(updates.FOUND_NOTES_KEY, json.dumps({"releases": [
    {"version": "9.9.9", "url": "https://example.invalid", "published_at": "2026-11-23T10:00:00Z", "body": "- A fix"}], "more": ""}))
db.forget_settings()


class Page(HTMLParser):
    """The dialogs, fields and radio buttons of a page, with what names them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.label_for: set[str] = set()
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.dialogs: list[dict[str, str]] = []
        self.fields: list[tuple[str, dict[str, str], bool]] = []
        self.radios: list[tuple[dict[str, str], list[dict[str, str]]]] = []
        self.tooltip: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "label" and a.get("for"):
            self.label_for.add(a["for"])
        if tag == "dialog":
            self.dialogs.append(a)
        if a.get("id") == "tooltip":
            self.tooltip = a
        if tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit", "button", "reset", "image"):
            in_label = any(t == "label" for t, _ in self.stack)
            self.fields.append((tag, a, in_label))
            if a.get("type") == "radio":
                self.radios.append((a, [attributes for _, attributes in self.stack]))
        if tag not in ("input", "br", "img", "meta", "link", "hr", "source", "wbr", "col", "area", "base", "embed", "track",
                       "path", "rect", "circle", "line", "polyline", "polygon", "use", "stop"):
            self.stack.append((tag, a))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def named_by(page: Page, a: dict[str, str]) -> bool:
    if a.get("aria-label", "").strip():
        return True
    references = a.get("aria-labelledby", "").split()
    return bool(references) and all(r in page.ids for r in references)


def field_name(a: dict[str, str]) -> str:
    return f"{a.get('type') or ''} {a.get('name') or a.get('id') or ''}".strip()


def unnamed(page: Page) -> tuple[list[str], list[str], list[str]]:
    dialogs = [d.get("id", "?") for d in page.dialogs if not named_by(page, d)]
    fields = [f"{tag} {field_name(a)}" for tag, a, in_label in page.fields
              if not (in_label or named_by(page, a) or a.get("title", "").strip() or a.get("id") in page.label_for)]
    radios = sorted({a.get("name", "?") for a, ancestors in page.radios
                     if not any(p.get("role") == "radiogroup" and named_by(page, p) for p in ancestors)})
    return dialogs, fields, radios


def walk(client: TestClient, pages: list[str]) -> None:
    global seen_dialogs, seen_fields, seen_radios
    for path in pages:
        response = client.get(path)
        page = Page()
        page.feed(response.text)
        dialogs, fields, radios = unnamed(page)
        seen_dialogs += len(page.dialogs)
        seen_fields += len(page.fields)
        seen_radios += len(page.radios)
        problems = "; ".join(part for part in (
            f"dialogs without a name {dialogs}" if dialogs else "", f"fields without a name {fields[:4]}" if fields else "",
            f"radio sets without a named group {radios}" if radios else "",
            f"the tooltip is a live region {page.tooltip}" if page.tooltip and (page.tooltip.get("aria-live") or page.tooltip.get("role"))
            else "") if part)
        check(response.status_code == 200 and not problems,
              f"{path}: {len(page.dialogs)} dialogs, {len(page.fields)} fields, all named{' - ' + problems if problems else ''}"
              + ("" if response.status_code == 200 else f" (HTTP {response.status_code})"))


seen_dialogs = seen_fields = seen_radios = 0
movie_library = (db.query_one("SELECT id FROM libraries WHERE collection_type = 'movies' LIMIT 1") or {}).get("id")
music_library = (db.query_one("SELECT id FROM libraries WHERE collection_type = 'music' LIMIT 1") or {}).get("id")
book_library = (db.query_one("SELECT id FROM libraries WHERE collection_type = 'books' LIMIT 1") or {}).get("id")
viewer_id = (db.query_one("SELECT user_id AS id FROM playback GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1") or {}).get("id")
movie_id = (db.query_one("SELECT p.item_id AS id FROM playback p JOIN items i ON i.id = p.item_id WHERE i.type = 'Movie' LIMIT 1")
            or {}).get("id")
PAGES = ["/?days=365", "/dashboard?days=365", "/account/dashboard", "/account", "/history", "/history?period=custom",
         "/history?search=a", "/video/insights?days=365", "/video/languages?days=365", "/video/languages/undetermined",
         "/video/compare", "/network?days=365", "/users?days=365", f"/users/{viewer_id}?days=365", f"/item/{movie_id}",
         "/library", f"/library/{movie_library}?tab=media", f"/library/{music_library}?tab=albums",
         f"/library/{book_library}?tab=books", "/arr/sonarr?days=365", "/arr/radarr?days=365", "/arr/lidarr?days=365",
         "/arr/bazarr?days=365", "/seerr?days=365", "/music/overview?days=365"] \
    + [f"/settings?section={key}" for key, _name, _admin_only in SETTINGS_SECTIONS]

print("--- A) - D) the pages ---")
walk(login("admin"), PAGES)
walk(TestClient(app), ["/login"])
check(seen_dialogs > 30 and seen_fields > 150 and seen_radios > 20,
      f"the test has something to check ({seen_dialogs} dialogs, {seen_fields} fields, {seen_radios} radio buttons)")

print()
print("--- E) every dialog of the templates ---")
TEMPLATES = sorted((PROJECT / "jellyscope" / "templates").rglob("*.html"))
DIALOG = re.compile(r"<dialog\b[^>]*>")
without = []
for path in TEMPLATES:
    text = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)
    for tag in DIALOG.findall(text):
        reference = re.search(r'aria-labelledby="([^"]+)"', tag)
        if not (reference and f'id="{reference.group(1)}"' in text) and "aria-label=" not in tag:
            without.append(f"{path.relative_to(PROJECT / 'jellyscope' / 'templates')}: {tag[:60]}")
check(not without, f"every <dialog> in the templates is named by an element of its template ({len(without)} {without[:4]})")
check(sum(len(DIALOG.findall(re.sub(r"\{#.*?#\}", "", p.read_text(encoding="utf-8"), flags=re.S))) for p in TEMPLATES) > 30,
      "and the search finds the dialogs")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
