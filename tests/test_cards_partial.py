# -*- coding: utf-8 -*-
r"""One request draws the asked cards again; every card drawn from the registry has one wrapper.

An open page is to refresh its cards by itself (spec docs/superpowers/specs/2026-11-26-live-cards-design.md,
part A). For that the browser has to find each card on the page and get it drawn anew - for the account that
asks, with the page's period, choices and "in a dashboard", all the asked cards in one computation. Watched
here:

(a) every card a page draws from the registry stands in one wrapper `.card-slot` right after its mark
    `<!-- card: key -->`, with its key and its sources (data-card-key, data-card-sources), the whole card
    inside it and no wrapper in another - on every page that draws cards and in both custom dashboards;
(b) the pages that used to include the card templates by hand (Network, Languages, Insights, Users,
    Library) draw their cards through the registry, in the page's order;
(c) GET /partials/cards draws the asked cards the same as the page does: for every page, every card;
(d) rights and availability per card: what the account may not see, an unknown key and a disconnected
    app's card are simply missing (the same answer), never an error;
(e) the page's period and choices from the address, "in a dashboard", and the session (the remembered
    period, a choice, a message for the next page) is not changed by the request;
(f) one data_for for the whole list, a ceiling on the number of keys, signed out = no cards.

Run:
    .venv/bin/python tests/test_cards_partial.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cards_partial.db")
os.environ["SECRET_KEY"] = "test-secret-long-enough-for-the-session"
os.environ["JELLYSCOPE_DEMO"] = "0"
os.environ.pop("JELLYSCOPE_EVENT_DATE", None)

from fastapi.testclient import TestClient  # noqa: E402

from _package import substitute  # noqa: E402
from jellyscope import access, accounts, cards, db, demodata, seerr, stats  # noqa: E402
from jellyscope.arr import bazarr, prowlarr, sonarr  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}" + (f" ({detail})" if detail and not condition else ""))
    if not condition:
        failures += 1


# --- data: the demo's history, the apps connected (nobody listens - a refused connection is quick) -------------
db.init_db()
demodata.seed()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.create("viewer", "longpassword")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'demo-u2' WHERE username = 'viewer'")
    conn.commit()
for app_module in (sonarr, bazarr, prowlarr, seerr):
    app_module.save_settings(True, "http://127.0.0.1:1", "test-key")
db.set_setting(access.LOGIN_KEY, "1")
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(cards.NAME_KEY, "Family")
DASHBOARD = ["now_playing", "summary", "users_table", "map", "disks", "library_growth", "music_numbers",
             "music_formats", "arr_sonarr_numbers", "bazarr_providers", "seerr_latest", "insight_unused"]
cards.save_layout(DASHBOARD)
cards.save_layout(DASHBOARD + ["live_traffic", "books_numbers"], accounts.get_by_name("admin")["id"])
db.forget_settings()


def sign_in(name: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return k


def set_rights(name: str, enabled: list[str]) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET permissions = ? WHERE username = ?",
                     (access.pack_permissions(enabled, False), name))
        conn.commit()
    db.forget_settings()


class Slots(HTMLParser):
    """The cards of a page as its wrappers say: [(key, sources, inside)] and what is wrong with them.

    After a mark `<!-- card: key -->` the first element must be the card's wrapper with the same key; a mark
    inside a wrapper means the previous card did not close (or wrappers nest).
    """

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.html = html
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", html)]
        self.depth, self.open, self.pending = 0, [], None
        self.slots: list[tuple[str, str, str]] = []
        self.marks: list[str] = []
        self.faults: list[str] = []
        self.feed(html)
        self.close()
        if self.open:
            self.faults.append(f"not closed: {[key for key, _depth, _start in self.open]}")
        if self.pending:
            self.faults.append(f"{self.pending}: no wrapper after its mark")

    def position(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_comment(self, data: str) -> None:
        mark = re.fullmatch(r"\s*card: (\w+)\s*", data)
        if not mark:
            return
        if self.open:
            self.faults.append(f"{mark.group(1)}: its mark is inside the wrapper of {self.open[-1][0]}")
        if self.pending:
            self.faults.append(f"{self.pending}: no wrapper after its mark")
        self.marks.append(mark.group(1))
        self.pending = mark.group(1)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = dict(attrs)
        is_slot = tag == "div" and "card-slot" in (values.get("class") or "").split()
        if self.pending and not is_slot:
            self.faults.append(f"{self.pending}: after its mark comes <{tag} class={values.get('class')!r}>, not its wrapper")
            self.pending = None
        if tag != "div":
            return
        self.depth += 1
        if is_slot:
            key = values.get("data-card-key")
            if self.open:
                self.faults.append(f"{key}: a wrapper inside the wrapper of {self.open[-1][0]}")
            if key != self.pending:
                self.faults.append(f"a wrapper of {key} after the mark of {self.pending}")
            self.open.append((key, self.depth, self.position() + len(self.get_starttag_text() or "")))
            self.slots.append((key, values.get("data-card-sources") or "", ""))
            self.pending = None

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self.open and self.open[-1][1] == self.depth:
            key, _depth, start = self.open.pop()
            index = max(i for i, slot in enumerate(self.slots) if slot[0] == key)
            self.slots[index] = (key, self.slots[index][1], self.html[start:self.position()])
        self.depth -= 1


def flat(html: str) -> str:
    """A card's HTML for comparing: whitespace as one space, the charts' gradients without their number.

    The number comes from a counter of the whole process (charts._TRANSITION_ORDER) on purpose: a card drawn
    again must not take a name another chart on the open page already has.
    """
    return re.sub(r"gradient-(mini-)?\d+", "gradient-N", re.sub(r"\s+", " ", html)).strip()


def comparable(html: str) -> str:
    """A card as the partial and its page are to agree on it.

    A card with a refresher of its own (Now playing, Flowing right now, Downloading) follows the clock to the
    minute and its rhythm is that of "Now playing" when both stand on one page: only its own wrapper is
    compared - the page does not swap it, it refreshes itself.
    """
    if "data-refresh=" not in html:
        return flat(html)
    wrapper = re.search(r"<div[^>]*data-refresh=[^>]*>", html)
    return re.sub(r'data-refresh="\d*"', 'data-refresh=""', wrapper.group(0))


def slot_faults(html: str) -> list[str]:
    """What is wrong with the wrappers of one page: the parser's faults, and sources other than the card's."""
    found = Slots(html)
    faults = list(found.faults)
    if found.marks != [key for key, _sources, _inside in found.slots]:
        faults.append(f"marks {found.marks} vs wrappers {[key for key, _s, _i in found.slots]}")
    for key, sources, _inside in found.slots:
        card = cards.BY_KEY.get(key)
        if card is None or sources.split() != list(card.sources):
            faults.append(f"{key}: sources {sources!r}, the card says {card and card.sources}")
    return faults


admin = sign_in("admin")

print("--- (a) every card on every page stands in its wrapper with its key and sources ---")
PAGES = {
    "/video/overview": False, "/network": False, "/video/languages": False, "/video/insights": False,
    "/users": False, "/library": False, "/music/overview": False, "/music/insights": False,
    "/books/overview": False, "/books/insights": False, "/arr/sonarr": False, "/arr/bazarr": False,
    "/arr/prowlarr": False, "/seerr": False, "/dashboard": True, "/account/dashboard": True,
}
pages: dict[str, str] = {}
for address in PAGES:
    response = admin.get(address + "?days=30")
    pages[address] = response.text if response.status_code == 200 else ""
    found = Slots(pages[address])
    faults = slot_faults(pages[address])
    check(response.status_code == 200 and len(found.slots) >= 2 and not faults,
          f"{address}: {len(found.slots)} cards, each in its wrapper", f"{response.status_code}, {faults[:3]}")
# A trap: the parser finds a card left outside its wrapper, a missing wrapper and nested wrappers.
check(Slots('<!-- card: summary --><div class="card">x</div>').faults
      and Slots('<!-- card: summary --><div class="card-slot" data-card-key="summary"><div>'
                '<!-- card: tiles --><div class="card-slot" data-card-key="tiles"></div></div></div>').faults
      and not Slots('<!-- card: summary --><div class="card-slot" data-card-key="summary" data-card-sources="plays">'
                    '<div class="card">x</div></div>').faults,
      "a trap: a card without its wrapper and a wrapper inside another are found")
check(slot_faults('<!-- card: summary --><div class="card-slot" data-card-key="summary" data-card-sources="live">'
                  '</div>') != [], "a trap: wrong sources are found")
# The dashboards' cells keep their card inside: the cell, then the wrapper, then the card.
dashboard_cells = re.findall(r'<div class="dash-card[^"]*" data-card="(\w+)"\s+data-width="\w+">\s*<!-- card: (\w+) -->'
                             r'\s*<div class="card-slot"', pages["/dashboard"])
check(len(dashboard_cells) >= 8 and all(cell == card for cell, card in dashboard_cells),
      f"the dashboard: each cell holds its card's wrapper ({len(dashboard_cells)})")
# Network's live card keeps its own refresher with the page's period, now drawn by the registry.
live = re.search(r'id="network-live" data-refresh="[^"]*" data-partial="([^"]+)"', admin.get("/network?days=7").text)
check(bool(live) and live.group(1).replace("&amp;", "&") == "/partials/network-live?days=7",
      f"Network: the live card refreshes itself with the page's period ({live and live.group(1)})")
# In a dashboard Flowing right now refreshes itself only with Refreshing cards on - it never did there before them;
# on the Network page always, as it always did.
mine = admin.get("/account/dashboard?days=7").text
check('data-partial="/partials/network-live' in mine, "My dashboard, Refreshing cards on: Flowing right now refreshes itself")
db.set_setting("ui_self_refresh", "0")
db.forget_settings()
mine, network = admin.get("/account/dashboard?days=7").text, admin.get("/network?days=7").text
db.set_setting("ui_self_refresh", "1")
db.forget_settings()
check("<!-- card: live_traffic -->" in mine and "/partials/network-live" not in mine and 'id="network-live"' not in mine
      and re.search(r'id="network-live" data-refresh="[^"]*" data-partial="/partials/network-live', network) is not None,
      "Refreshing cards off: a dashboard's Flowing right now is drawn without its refresher, the Network page keeps it")

print()
print("--- (b) the pages that included cards by hand draw them through the registry, in their order ---")
ORDER = {
    "/network": ["network_numbers", "live_traffic", "peaks_by_day", "who_streamed", "by_player", "origin", "map"],
    "/video/languages": ["language_share", "who_language", "dubbing", "subtitles", "library_languages", "combinations",
                         "missing_track"],
    "/video/insights": ["insight_unused", "insight_transcoded", "insight_reasons", "insight_speed", "insight_quality",
                        "insight_large", "insight_libraries", "insight_duplicates", "insight_postponed"],
    "/users": ["users_chart", "users_table"],
    "/library": ["libraries", "disks", "library_total", "library_growth", "codecs", "resolutions", "dynamic_range",
                 "music_formats", "music_quality", "music_years", "music_library_genres", "music_largest",
                 "books_library", "books_formats", "books_years", "books_library_genres", "books_largest"],
}
for address, expected in ORDER.items():
    drawn = re.findall(r"<!-- card: (\w+) -->", pages[address])
    check(drawn == expected, f"{address}: the cards from the registry in the page's order", f"{drawn}")
# A number a page writes outside its cards would stand still while the card next to it refreshes, and the two would
# disagree: the Languages' main number and the Library's numbers of books stand in a card's slot (Language ratio,
# Books library numbers). The Library's numbers of music count the artists by the library's own rule, not by the
# card's - so they stay the section's, and the cards that add up to them (formats, quality: the tracks and their size;
# decades: the albums) are left out of refreshing by themselves (data-card-still), the section stays of one moment.
inside = sum(content.count('class="hero"') for _key, _sources, content in Slots(pages["/video/languages"]).slots)
check(inside == 1 and pages["/video/languages"].count('class="hero"') == 1,
      "/video/languages: the main number stands in the slot of Language ratio", f"{inside}")
library_page = pages["/library"]
books_part = library_page.split('id="books-in-library"', 1)[-1]
music_part = library_page.split('id="music-in-library"', 1)[-1].split('id="books-in-library"', 1)[0]
rows = books_part.count('class="kpi-row"')
inside = sum(content.count('class="kpi-row"') for _key, _sources, content in Slots(books_part).slots)
check(inside == 1 and rows == 1, "/library: the numbers of books stand in their card's slot", f"{rows} rows, {inside} in slots")
still = set(re.findall(r'data-card-key="(\w+)"[^>]*data-card-still="1"', music_part))
check(still == {"music_formats", "music_quality", "music_years"} and 'data-card-still' not in books_part,
      "/library: the music cards that add up to the section's numbers do not refresh by themselves", f"{sorted(still)}")

print()
print("--- (c) the partial draws every card the same as its page ---")


def partial(k: TestClient, keys: list[str], **parameters) -> tuple[int, str]:
    response = k.get("/partials/cards?" + urlencode({"keys": ",".join(keys), **parameters}),
                     headers={"X-Requested-With": "fetch"}, follow_redirects=False)
    return response.status_code, response.text


def page_and_partial(address: str, in_dashboard: bool) -> tuple[int, dict[str, str], dict[str, str], str]:
    """The page's cards and the same cards from the partial, asked right after it (with the page's period)."""
    on_page = {key: comparable(inside) for key, _sources, inside in Slots(admin.get(address + "?days=30").text).slots}
    status, text = partial(admin, list(on_page), days=30, in_dashboard=int(in_dashboard))
    return status, on_page, {key: comparable(inside) for key, _sources, inside in Slots(text).slots}, text


for address, in_dashboard in PAGES.items():
    status, on_page, again, text = page_and_partial(address, in_dashboard)
    if again != on_page:
        # "Last seen 59 min ago" turns into "1 h" when a minute passes between the two: once more, right away.
        status, on_page, again, text = page_and_partial(address, in_dashboard)
    differ = [key for key in on_page if again.get(key) != on_page[key]]
    check(status == 200 and len(on_page) >= 2 and list(again) == list(on_page) and not differ and not slot_faults(text),
          f"{address}: the partial draws its {len(on_page)} cards the same", f"{status}, differ {differ[:4]}")

print()
print("--- (d) rights and availability per card ---")
status, text = partial(admin, ["summary", "no_such_card", "summary", "arr_radarr_numbers", "users_table"], days=30)
check(status == 200 and [key for key, _s, _i in Slots(text).slots] == ["summary", "users_table"],
      "an unknown key, a disconnected app's card and a repeated key are left out; the order is the asked one",
      f"{status}, {[key for key, _s, _i in Slots(text).slots]}")
set_rights("reader", [area.key for area in access.AREAS if area.key not in ("network", "users")])
reader = sign_in("reader")
status, text = partial(reader, ["summary", "map", "users_table", "origin"], days=30)
check(status == 200 and [key for key, _s, _i in Slots(text).slots] == ["summary"],
      "a reader without Network and Users gets only the card they may see", f"{status}")
forbidden, unknown = partial(reader, ["map"], days=30), partial(reader, ["no_such_card"], days=30)
check(forbidden == unknown and forbidden[0] == 200 and "card-slot" not in forbidden[1],
      "a card the account may not see answers the same as a key that does not exist")
set_rights("viewer", ["overview", "library"])
viewer = sign_in("viewer")
status, text = partial(viewer, ["summary", "recently_added", "users_table", "map", "music_numbers"], days=30)
check(status == 200 and [key for key, _s, _i in Slots(text).slots] == ["summary", "recently_added"],
      "a viewer from Jellyfin gets the cards of their areas", f"{status}, {[key for key, _s, _i in Slots(text).slots]}")
status, text = partial(admin, [], days=30)
check(status == 200 and "card-slot" not in text, "no keys: an empty answer, not an error")

print()
print("--- (e) the page's period and choices, in a dashboard, and the session stays ---")


def summary_of(html: str) -> str:
    return next((inside for key, _s, inside in Slots(html).slots if key == "summary"), "")


week = summary_of(admin.get("/video/overview?days=7").text)
month = summary_of(admin.get("/video/overview?days=30").text)
status, text = partial(admin, ["summary"], days=7)
check(flat(week) != flat(month) and flat(summary_of(text)) == flat(week), "the partial computes with the page's period (a week)")
check(flat(summary_of(admin.get("/video/overview").text)) == flat(month),
      "and the session still remembers the month chosen last")
status, text = partial(admin, ["watching_by_day", "most_watched"], days=30, kind="movies", top_kind="series")
check(bool(re.search(r'class="chip active"[^>]*data-kind="movies"', text))
      and bool(re.search(r'class="chip active"[^>]*data-kind="series"', text)),
      "the cards' choices from the address (Movies on one card, Series on the other)")
overview = admin.get("/video/overview").text
check(not re.search(r'class="chip active"[^>]*data-kind="movies"', overview)
      and not re.search(r'class="chip active"[^>]*data-kind="series"', overview),
      "and the session's choices did not change")
status, text = partial(admin, ["music_numbers"], days=30, in_dashboard=1)
_status, alone = partial(admin, ["music_numbers"], days=30)
check(bool(re.search(r'<p class="card-group">[^<]*·', text)) and "card-group" not in alone,
      "in a dashboard an app's card carries its prefix, on its own page not")
_status, text = partial(admin, ["users_table"], days=30, in_dashboard=1)
_status, alone = partial(admin, ["users_table"], days=30)
check(flat(text) != flat(alone), "the users table is drawn as in a dashboard when the page is one")
# A message for the next page stays in the session: the partial is no page of its own.
saved = admin.post("/dashboard/layout", data={"order": ",".join(DASHBOARD)}, follow_redirects=False)
partial(admin, ["summary"], days=30)
check(saved.status_code == 303 and "Saved." in admin.get("/dashboard").text,
      "a message waiting for the next page is not eaten by the partial")

print()
print("--- (f) one computation, the ceiling, signed out ---")
calls: list[list[str]] = []
real_data_for = cards.data_for


def counting(layout, *args, **kwargs):
    calls.append([card.key for card in layout])
    return real_data_for(layout, *args, **kwargs)


tables: list[int] = []
real_table = stats.user_table


def counting_table(*args, **kwargs):
    tables.append(1)
    return real_table(*args, **kwargs)


restore = [substitute(cards, "data_for", counting), substitute(stats, "user_table", counting_table)]
try:
    status, text = partial(admin, ["users_chart", "users_table", "summary", "map"], days=30)
finally:
    for undo in restore:
        undo()
check(status == 200 and calls == [["users_chart", "users_table", "summary", "map"]],
      "one data_for for all the asked cards", f"{calls}")
check(len(tables) == 1, f"what two cards share is computed once ({len(tables)})")
calls.clear()
restore = substitute(cards, "data_for", counting)
try:
    too_many = partial(admin, ["summary"] * (len(cards.ALL_CARDS) + 1), days=30)
finally:
    restore()
check(too_many[0] == 400 and not calls, f"more keys than the registry has cards is refused unread ({too_many[0]})")
check(partial(admin, [card.key for card in cards.ALL_CARDS], days=30)[0] == 200,
      "every card of the registry at once (the largest dashboard) is allowed")
guest = TestClient(app)
status, text = partial(guest, ["summary"], days=30)
check(status in (303, 307, 401) and "card-slot" not in text, f"signed out: no cards ({status})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
