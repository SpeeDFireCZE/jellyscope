# -*- coding: utf-8 -*-
r"""The default page: what / (the logo, sign-in) opens - chosen in Settings > Interface.

Spec: docs/superpowers/specs/2026-11-16-menu-sections-design.md, "Výchozí stránka".

* Without a saved choice (an installation from before the menu by sections)
  it behaves as before: the custom dashboard when it is on and has something
  to show, otherwise Movies & series.
* A saved choice holds even while its page is unavailable (the content
  disappeared, the custom dashboard was switched off, the account may not
  open it); / then opens the first available page in the order Movies &
  series -> Music -> custom dashboard -> Library -> History -> My account,
  always one the account may open. The setting itself stays.
* The field offers only what exists now; a value outside the offer (or a
  form without the field) changes nothing.
* A viewer signed in through Jellyfin starts on their own page, as before.

Run:
    .venv/bin/python tests/test_default_page.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "default-page.db")
os.environ["SECRET_KEY"] = "test-key-default-page"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from _apps import login  # noqa: E402
from jellyscope import access, accounts, cards, db, demodata, navigation, web  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
demodata.seed()                       # movies, series and music


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def sql(text: str) -> None:
    with db.connect() as conn:
        conn.execute(text)
    db.forget_settings()


def setting(value: str) -> None:
    db.set_setting(navigation.DEFAULT_PAGE_KEY, value)
    db.forget_settings()


def home(client: TestClient, address: str = "/") -> str:
    response = client.get(address, follow_redirects=False)
    return response.headers.get("location", f"<{response.status_code}>")


def interface(client: TestClient, **fields: str) -> None:
    """Save Settings > Interface as the form does (the other fields at today's values)."""
    data = {"ui_max_streams": "10", "ui_max_viewers": "10", "ui_map_zoom": "click", "ui_skin": "new",
            "ui_dashboard": "1" if cards.is_enabled() else "0", "dashboard_name": cards.dashboard_name(), **fields}
    client.post("/settings/interface", data=data, follow_redirects=False)
    db.forget_settings()


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin_account = accounts.get_by_name("admin")
admin = login("admin")

print("--- without a saved choice it behaves as before ---")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "", "a fresh or upgraded installation has no choice saved")
check(home(admin) == "/video/overview", f"no choice, no custom dashboard: / -> /video/overview ({home(admin)})")
db.set_setting(cards.NAME_KEY, "Rodina")
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout(["summary"])
db.forget_settings()
check(home(admin) == "/dashboard", f"no choice, the custom dashboard on and filled: / -> /dashboard ({home(admin)})")
response = TestClient(web.app).post("/login", data={"username": "admin", "password": "longpassword", "method": "local"},
                                    follow_redirects=False)
check(response.headers.get("location") in ("/", "/dashboard"), "sign-in goes to the start page")

print()
print("--- without a saved choice the field says Automatic, and what opens ---")
# The final review of 16. 11. 2026: a hidden "untouched" field made the selected page impossible
# to confirm and the field looked like a choice that was not one. "Automatic" is an option of
# its own now: the custom dashboard when it has something to show, otherwise Movies & series.
page = admin.get("/settings?section=interface").text
field = re.search(r'<select[^>]*name="ui_default_page".*?</select>', page, re.S)
check(field is not None and re.search(r'<option value="auto" selected>', field.group(0)) is not None
      and "Teď se otevírá: Rodina" in page, "Automatic is selected and the field says the dashboard opens now")
interface(admin, ui_default_page="auto")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "", "saving Automatic keeps no choice")
interface(admin, ui_default_page="video")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "video" and home(admin) == "/video/overview",
      "an explicit Movies & series is saved and wins over the dashboard")
interface(admin, ui_default_page="auto")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "" and home(admin) == "/dashboard", "and Automatic goes back to the old way")

print()
print("--- an old overview bookmark ---")
# / with parameters is an overview bookmark from before (templates never link to /?... - a test
# guards it): it opens the overview of movies and series, whatever the default page is.
check(home(admin, "/?days=30&kind=movies") == "/video/overview?days=30&kind=movies",
      f"no choice, the dashboard filled: the bookmark opens the overview ({home(admin, '/?days=30&kind=movies')})")
setting("music")
check(home(admin, "/?days=30&kind=movies") == "/video/overview?days=30&kind=movies" and home(admin) == "/music/overview",
      f"music saved: the bookmark still opens the overview, bare / opens Music ({home(admin, '/?days=30&kind=movies')})")
setting("")

print()
print("--- a saved choice ---")
setting("music")
check(home(admin) == "/music/overview", f"music saved: / -> /music/overview ({home(admin)})")
signed_in = TestClient(web.app)
signed_in.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
check(signed_in.get("/").url.path == "/music/overview", "after signing in with a password the default page opens")
sql("UPDATE music_track SET missing = 1")
check(home(admin) == "/video/overview", f"music saved but gone: / -> /video/overview ({home(admin)})")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "music", "and the saved choice stays music")
page = admin.get("/settings?section=interface").text
check("Teď se otevírá" in page and "Filmy a seriály" in page, "the field says what opens instead")
setting("video")
page = admin.get("/settings?section=interface").text
field = re.search(r'<select[^>]*name="ui_default_page".*?</select>', page, re.S)
options = re.findall(r'<option value="(\w+)"', field.group(0)) if field else []
check(options == ["auto", "video", "books", "dashboard"], f"without music the field offers Books (the fixture has some) but no Music ({options})")
sql("UPDATE music_track SET missing = 0")
page = admin.get("/settings?section=interface").text
field = re.search(r'<select[^>]*name="ui_default_page".*?</select>', page, re.S)
options = re.findall(r'<option value="(\w+)"', field.group(0)) if field else []
check(options == ["auto", "video", "music", "books", "dashboard"] and "Teď se otevírá" not in page,
      f"with music it does, and an available choice needs no note ({options})")
check(dict(navigation.default_page_options(admin_account)).get("dashboard") == "Rodina",
      "the custom dashboard is offered under its name")

print()
print("--- the form keeps out what is not on offer ---")
for value in ("ebooks", "nesmysl", "/settings"):
    interface(admin, ui_default_page=value)
    check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "video", f"ui_default_page={value} is not saved")
interface(admin)
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "video", "a form without the field leaves it alone")
interface(admin, ui_default_page="music")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "music", "a choice on offer is saved")
db.set_setting(cards.ENABLED_KEY, "0")
db.forget_settings()
interface(admin, ui_default_page="dashboard")
check(db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "music", "the custom dashboard switched off is not on offer")
# Saved, switched on but with no card: on offer, yet / opens Movies & series - the field says so.
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout([])
setting("dashboard")
page = admin.get("/settings?section=interface").text
check(home(admin) == "/video/overview" and "Teď se otevírá: Filmy a seriály" in page,
      "an empty custom dashboard as the choice: the field says what opens instead")
db.set_setting(cards.ENABLED_KEY, "0")
setting("music")

print()
print("--- the account's permissions and viewers ---")
setting("video")
reader_account = accounts.get_by_name("reader")
defaults = [o.key for o in access.AREAS if o.default]


def reader_areas(areas: list[str]) -> TestClient:
    accounts.save_permissions(reader_account["id"], access.pack_permissions(areas, False))
    db.forget_settings()
    return login("reader")


# The default section, then the next ones - each at its first page the account may open, like the
# menu opens a section (code review 16. 11. 2026: a reader with Insights but no overview used to
# land on My account although Insights was in their menu).
VIDEO_AREAS = ("overview", "comparison", "charts", "languages")
reader = reader_areas([a for a in defaults if a != "overview"])
first = "/video/compare" if "comparison" in defaults else "/video/insights"     # Comparison is not a default area today
check(home(reader) == first, f"a reader without the overview: the section's first page they may open ({home(reader)})")
reader = reader_areas(["charts"])
check(home(reader) == "/video/insights", f"only Insights: Insights ({home(reader)})")
reader = reader_areas([a for a in defaults if a not in VIDEO_AREAS])
check(home(reader) == "/music/overview", f"no page of Movies & series: Music ({home(reader)})")
# Music has two roots like Movies & series (22. 11. 2026): the overview and the insights.
MUSIC_AREAS = ("music", "music_insights")
reader = reader_areas(["music_insights"])
check(home(reader) == "/music/insights", f"only Music's insights: its insights ({home(reader)})")
# Books (23. 11. 2026) come after Music in the order of the fallback.
BOOKS_AREAS = ("books", "books_insights")
reader = reader_areas([a for a in defaults if a not in (*VIDEO_AREAS, *MUSIC_AREAS)])
check(home(reader) == "/books/overview", f"neither Movies & series nor Music: Books ({home(reader)})")
reader = reader_areas(["books_insights"])
check(home(reader) == "/books/insights", f"only Books' insights: its insights ({home(reader)})")
reader = reader_areas([a for a in defaults if a not in (*VIDEO_AREAS, *MUSIC_AREAS, *BOOKS_AREAS)])
check(home(reader) == "/library", f"neither Movies & series, Music nor Books: the Library ({home(reader)})")
setting("books")
check(home(admin) == "/books/overview" and db.get_setting(navigation.DEFAULT_PAGE_KEY, "") == "books",
      f"books saved: / -> /books/overview ({home(admin)})")
sql("UPDATE book SET missing = 1")
check(home(admin) == "/video/overview", f"books saved but gone: / -> /video/overview ({home(admin)})")
sql("UPDATE book SET missing = 0")
setting("video")
reader = reader_areas([a for a in defaults if a not in (*VIDEO_AREAS, *MUSIC_AREAS, *BOOKS_AREAS, "library")])
check(home(reader) == "/account", f"without the Library and History: My account ({home(reader)})")
reader = reader_areas([*defaults, "overview"])
check(home(reader) == "/video/overview", f"with the overview the reader gets the default page ({home(reader)})")
viewer = accounts.from_jellyfin({"id": "u-jana", "name": "jana", "is_admin": False, "is_disabled": False})
viewer = accounts.get_by_name("jana") if not isinstance(viewer, dict) else viewer
check(navigation.default_page_url(viewer) == f"/users/{access.own_viewer(viewer)}" and access.own_viewer(viewer),
      f"a viewer from Jellyfin starts on their own page ({navigation.default_page_url(viewer)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
