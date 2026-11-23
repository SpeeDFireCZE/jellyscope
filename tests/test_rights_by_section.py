# -*- coding: utf-8 -*-
r"""The permission tree follows the menu by sections (16. 11. 2026).

The tree of areas (Settings: an account's rights, the reader group, Jellyfin
viewers, root API keys) used to follow the old flat menu - "Server overview"
at the root with the custom dashboard, comparison and now-playing under it.
Now it is in the order of the menu, every section of the menu a heading
(not a checkbox) with its rights under it - with a heading only over Movies &
series, the areas after it looked like its sub-settings (the user, 16. 11.):

    "Vlastní přehled"   "Přehled z vybraných karet"
    "Filmy a seriály"   "Přehled" ("Srovnání", "Právě se hraje"), "Zjištění a žebříčky titulů", "Jazyky"
    "Hudba"             "Přehled" (+ who listened)
    "*Arr"              "Sonarr, Radarr, Lidarr, Readarr, Bazarr a Prowlarr"
    "Seerr"             "Žádosti" (+ who requested)
    "Knihovna"          "Knihovny a tituly" (+ who watched)
    "Síť"               "Připojení a IP adresy"
    "Uživatelé"         "Seznam uživatelů"

The area keys stay (they are stored with accounts); only the order,
headings and labels change. One meaning changes: the custom dashboard no
longer needs the overview - each of its cards is still checked by its own
area. The card groups of the dashboard builder follow the sections, and the
tree of a root API key shows only areas that open something in the API.

Run:
    .venv/bin/python tests/test_rights_by_section.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prava-sekce.db")
os.environ["SECRET_KEY"] = "test-key-prava-sekce"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login  # noqa: E402
from jellyscope import access, accounts, api, cards, db, demodata, i18n  # noqa: E402

failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
demodata.seed()


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
admin = login("admin")
reader = accounts.get_by_name("reader")

print("--- the tree in the order of the menu ---")
tree = access.tree(access.READER)
keys = [row["key"] for row in tree]
check(keys == ["dashboard", "overview", "comparison", "now_playing", "charts", "languages", "music", "music_comparison",
               "music_who", "music_insights", "books", "books_comparison", "books_who", "books_insights", "arr", "seerr", "seerr_who", "library", "library_who", "network", "users"],
      f"order of the menu ({keys})")
by_key = {row["key"]: row for row in tree}
SECTIONS = ["Vlastní přehled", "Filmy a seriály", "Hudba", "Knihy", "*Arr", "Seerr", "Knihovna", "Síť", "Uživatelé"]
# The tree holds English keys; the expectations are the Czech the page shows (the server is Czech).
def cs(text: str) -> str:
    return i18n.translate(text, "cs") if text else text


sections = [cs(row["section"]) for row in tree]
check(all(sections) and list(dict.fromkeys(sections)) == SECTIONS,
      f"every area under a heading of its section, in the order of the menu ({list(dict.fromkeys(sections))})")
video = {"overview", "comparison", "now_playing", "charts", "languages"}
check({k for k, row in by_key.items() if cs(row["section"]) == "Filmy a seriály"} == video,
      "Movies & series holds its five areas")
labels = {k: cs(by_key[k]["name"]) for k in ("dashboard", "overview", "comparison", "music", "arr", "seerr", "library",
                                          "network", "users")}
check(labels == {"dashboard": "Přehled z vybraných karet", "overview": "Přehled", "comparison": "Srovnání",
                 "music": "Přehled", "arr": "Sonarr, Radarr, Lidarr, Readarr, Bazarr a Prowlarr", "seerr": "Žádosti",
                 "library": "Knihovny a tituly", "network": "Připojení a IP adresy", "users": "Seznam uživatelů"},
      f"under a heading the label names what the section shows, not the section again ({labels})")
check(not by_key["dashboard"]["parent"] and by_key["comparison"]["parent"] == "overview"
      and by_key["now_playing"]["parent"] == "overview",
      "the custom dashboard is a root, comparison and now-playing stay under the overview")
page = admin.get("/settings?section=accounts").text
missing = [s for s in SECTIONS if re.search(r'class="tree-section"[^>]*>\s*' + re.escape(s) + r"\s*<", page) is None
           or f'role="group" aria-label="{s}"' not in page]
check("Přehled serveru" not in page and not missing,
      f"Settings draw every heading, each section a group for screen readers ({missing})")

print()
print("--- Music: the areas split like the video ones ---")
# The user, 22. 11. 2026: the rights of Music the same as of Movies & series - the overview with the comparison under
# it, the insights apart (and "who listened" under the overview, as it was).
music_rows = {k: row for k, row in by_key.items() if cs(row["section"]) == "Hudba"}
check(list(music_rows) == ["music", "music_comparison", "music_who", "music_insights"]
      and {k: row["parent"] for k, row in music_rows.items()} == {"music": "", "music_comparison": "music",
                                                                   "music_who": "music", "music_insights": ""},
      f"Music: the overview with the comparison and who listened under it, the insights a root ({list(music_rows)})")
check({k: cs(music_rows[k]["name"]) for k in ("music", "music_comparison", "music_insights")}
      == {"music": "Přehled", "music_comparison": "Srovnání", "music_insights": "Zjištění a žebříčky"}
      and access.BY_KEY["music_insights"].default is True,
      "named like the video ones; the insights on by default, like the video insights")
for name, areas in (("hudba-prehled", ["music"]), ("hudba-zjisteni", ["music_insights"])):
    accounts.create(name, "longpassword")
    accounts.save_permissions(accounts.get_by_name(name)["id"], access.pack_permissions(areas, False))
only_overview, only_insights = accounts.get_by_name("hudba-prehled"), accounts.get_by_name("hudba-zjisteni")
opens = {path: (access.may_open(only_overview, path), access.may_open(only_insights, path))
         for path in ("/music/overview", "/music/artist/x", "/music/compare", "/music/insights")}
check(opens == {"/music/overview": (True, False), "/music/artist/x": (True, False), "/music/compare": (False, False),
                "/music/insights": (False, True)}, f"each page of Music opens with its own area only ({opens})")

print()
print("--- Books: the areas laid out like Music's ---")
books_rows = {k: row for k, row in by_key.items() if cs(row["section"]) == "Knihy"}
check(list(books_rows) == ["books", "books_comparison", "books_who", "books_insights"]
      and {k: row["parent"] for k, row in books_rows.items()} == {"books": "", "books_comparison": "books",
                                                                   "books_who": "books", "books_insights": ""},
      f"Books: the overview with the comparison and who read under it, the insights a root ({list(books_rows)})")
check({k: cs(books_rows[k]["name"]) for k in books_rows}
      == {"books": "Přehled", "books_comparison": "Srovnání", "books_who": "…včetně „kdo četl a poslouchal“",
          "books_insights": "Zjištění a žebříčky"}, "named like Music's")
check([access.BY_KEY[k].default for k in books_rows] == [True, False, access.BY_KEY["music_who"].default, True],
      "defaults: the overview and the insights on, the comparison off, who like Music's")
for name, areas in (("knihy-prehled", ["books"]), ("knihy-zjisteni", ["books_insights"])):
    accounts.create(name, "longpassword")
    accounts.save_permissions(accounts.get_by_name(name)["id"], access.pack_permissions(areas, False))
only_overview, only_insights = accounts.get_by_name("knihy-prehled"), accounts.get_by_name("knihy-zjisteni")
opens = {path: (access.may_open(only_overview, path), access.may_open(only_insights, path))
         for path in ("/books/overview", "/books/compare", "/books/insights")}
check(opens == {"/books/overview": (True, False), "/books/compare": (False, False), "/books/insights": (False, True)},
      f"each page of Books opens with its own area only ({opens})")

print()
print("--- outside the tree an area is named with its section ---")
# A section with one area of its own is named by the section alone ("Knihovna"), one with more by
# both ("Filmy a seriály · Přehled") - the table of keys, the note on a card, the dashboard builder.
headings = {k: tuple(cs(part) for part in access.BY_KEY[k].heading) for k in ("dashboard", "overview", "charts", "music", "music_who",
                                                  "music_insights", "books", "books_insights", "library", "network", "users")}
# Music has two roots since 22. 11. 2026 (the overview and the insights, like Movies & series), so both name the section.
check(headings == {"dashboard": ("Vlastní přehled",), "overview": ("Filmy a seriály", "Přehled"),
                   "charts": ("Filmy a seriály", "Zjištění a žebříčky titulů"), "music": ("Hudba", "Přehled"),
                   "music_who": ("Hudba", "…včetně „kdo poslouchal“"),
                   "music_insights": ("Hudba", "Zjištění a žebříčky"), "books": ("Knihy", "Přehled"), "books_insights": ("Knihy", "Zjištění a žebříčky"),
                   "library": ("Knihovna",),
                   "network": ("Síť",), "users": ("Uživatelé",)}, f"Area.heading ({headings})")

print()
print("--- the custom dashboard no longer needs the overview ---")
db.set_setting(cards.NAME_KEY, "Rodina")
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout(["codecs", "summary"])           # a Library card and an overview card
accounts.save_permissions(reader["id"], access.pack_permissions(["dashboard", "library"], False))
db.forget_settings()
reader = accounts.get_by_name("reader")
check(access.sees(access.READER, "dashboard", reader), "the dashboard right counts without the overview")
dashboard = login("reader").get("/dashboard")
check(dashboard.status_code == 200 and "<!-- card: codecs -->" in dashboard.text and "<!-- card: summary -->" not in dashboard.text,
      f"the reader sees the cards of their own areas only ({dashboard.status_code})")

print()
print("--- the dashboard builder groups cards by the sections ---")
groups = [tuple(cs(part) for part in heading) for heading, _ in cards.groups([cards.BY_KEY["now_playing"], cards.BY_KEY["codecs"]])]
check(groups == [("Filmy a seriály", "Přehled"), ("Knihovna",)], f"Movies & series · Overview, Library ({groups})")

print()
print("--- the dashboard's heading is its name in the menu ---")
tree_page = admin.get("/settings?section=accounts").text
check(re.search(r'class="tree-section"[^>]*>\s*Rodina\s*<', tree_page) is not None
      and 'role="group" aria-label="Rodina"' in tree_page and "(„Rodina“)" not in tree_page,
      "a named custom dashboard: the heading says Rodina, like the menu, and the label stays plain")

print()
print("--- a card without the right names the area that is missing ---")
# Now playing ticked, the overview above it not: the note points at the overview, the box the
# administrator has to tick - not at Now playing, which is ticked already.
# Saved while the account could see it (saving takes only cards the account may see), then the
# overview is taken away.
accounts.save_permissions(reader["id"], access.pack_permissions(["overview", "now_playing", "library"], False))
db.forget_settings()
own = login("reader")
own.post("/account/dashboard", data={"order": "now_playing,codecs"}, follow_redirects=False)
accounts.save_permissions(reader["id"], access.pack_permissions(["now_playing", "library"], False))
db.forget_settings()
mine = own.get("/account/dashboard").text
check("Nemáš oprávnění k oblasti „Filmy a seriály · Přehled“" in mine and "· Právě se hraje“" not in mine,
      "the note on Now playing names the overview it needs")

print()
print("--- a root API key: only areas that open something in the API ---")
api_page = admin.get("/settings?section=api").text
form = api_page[api_page.index('id="new-key-smi"'):]
form = form[:form.index("</form>")]
offered = set(re.findall(r'name="sees_(\w+)"', form))
with_api = {area for area in api.ADDRESS_AREAS.values() if area}
check(offered == with_api | {"library_who"} == api.key_areas(),
      f"the new key's tree: areas of the API and the names under them ({sorted(offered)})")
check("Filmy a seriály" in form, "with the same headings")
# The same rule when saving (a form from an older page) and in the table of keys.
admin.post("/settings/api/token", data={"name": "starsi-formular", "mode": "selected", "sees_languages": "on",
                                        "sees_dashboard": "on", "sees_library": "on"})
saved = db.query_value("SELECT permissions FROM api_tokens WHERE name = 'starsi-formular'", default="") or ""
check('"library"' in saved and "languages" not in saved and "dashboard" not in saved,
      f"saving keeps only areas a key can use ({saved})")
# A key saved before 16. 11. 2026 with areas the API has no address for (stored as it was then).
api.create_token("stary-klic", None, json.dumps({"areas": ["dashboard", "languages", "overview", "users"],
                                          "anonymise": False, "history": False}))
row = next(r for r in re.findall(r"<tr>.*?</tr>", admin.get("/settings?section=api").text, re.S) if ">stary-klic<" in r)
row = re.sub(r"\s+", " ", row)
check("Vlastní přehled" not in row and "Jazyky" not in row and "Filmy a seriály · Přehled" in row and "Uživatelé" in row,
      f"the table of keys names only what a key can use, an area with its section ({row[:220]})")
# Stored as well, not only hidden when read: should a later version give Languages an address in
# the API, an old key must not get it without the administrator seeing it (code review 16. 11.).
db.set_setting(api.CONVERSION_KEY_AREAS, "")
db.forget_settings()
check(api.drop_unusable_key_areas() is True, "the conversion of stored key rights runs once")
stored = json.loads(db.query_value("SELECT permissions FROM api_tokens WHERE name = 'stary-klic'", default="{}") or "{}")
check(stored.get("areas") == ["overview", "users"] and stored.get("anonymise") is False,
      f"an old key keeps only areas a key can use, the rest of its rights as they were ({stored})")
db.forget_settings()
check(api.drop_unusable_key_areas() is False, "and never again")

print()
print("--- after the upgrade the custom dashboard stays off where the overview was off ---")
# Before 16. 11. 2026 the dashboard needed the overview: a group or an account with the overview
# off had no dashboard, whatever its own box said. A one-off conversion keeps that so.
accounts.create("vlastni-a", "longpassword")
accounts.create("vlastni-b", "longpassword")
own_a, own_b = accounts.get_by_name("vlastni-a"), accounts.get_by_name("vlastni-b")
accounts.save_permissions(own_a["id"], access.pack_permissions(["dashboard", "library"], False))
accounts.save_permissions(own_b["id"], access.pack_permissions(["dashboard", "overview"], False))
db.set_setting(access.area_key(access.READER, access.BY_KEY["overview"]), "0")
db.set_setting(access.CONVERSION_DASHBOARD_KEY, "")
db.forget_settings()
check(access.keep_dashboard_with_overview() is True, "the conversion runs once")
db.forget_settings()
group_dashboard = db.get_setting(access.area_key(access.READER, access.BY_KEY["dashboard"]), "")
check(group_dashboard == "0", f"the reader group without the overview has no dashboard ({group_dashboard!r})")
check("dashboard" not in access.own_permissions(accounts.get_by_name("vlastni-a"))["areas"]
      and "dashboard" in access.own_permissions(accounts.get_by_name("vlastni-b"))["areas"],
      "an account's own rights: off without the overview, kept with it")
db.set_setting(access.area_key(access.READER, access.BY_KEY["dashboard"]), "1")
db.forget_settings()
check(access.keep_dashboard_with_overview() is False
      and db.get_setting(access.area_key(access.READER, access.BY_KEY["dashboard"]), "") == "1",
      "and never again: a dashboard switched on later stays on")

print()
print("--- after the upgrade whoever saw Music sees its comparison and insights too ---")
# Until 22. 11. 2026 one area "music" opened the whole of Music (the charts were on its overview). A one-off conversion
# gives the two new areas to whoever had it - and only to them: the insights are on by default, so a group with Music
# off is written "0" for them explicitly.
accounts.create("hudba-vlastni", "longpassword")
accounts.create("bez-hudby", "longpassword")
with_music, without_music = accounts.get_by_name("hudba-vlastni"), accounts.get_by_name("bez-hudby")
accounts.save_permissions(with_music["id"], access.pack_permissions(["music", "library"], False))
accounts.save_permissions(without_music["id"], access.pack_permissions(["library"], False))
for group, value in ((access.READER, "1"), (access.VIEWER, "0")):
    db.set_setting(access.area_key(group, access.BY_KEY["music"]), value)
    for key in ("music_comparison", "music_insights"):
        db.set_setting(access.area_key(group, access.BY_KEY[key]), "")
db.set_setting(access.CONVERSION_MUSIC_KEY, "")
db.forget_settings()
check(access.split_music_rights() is True, "the conversion runs once")
db.forget_settings()
groups = {group: [db.get_setting(access.area_key(group, access.BY_KEY[key]), "") for key in ("music_comparison",
                                                                                           "music_insights")]
          for group in (access.READER, access.VIEWER)}
check(groups == {access.READER: ["1", "1"], access.VIEWER: ["0", "0"]},
      f"a group with Music gets both, one without gets neither - not the insights' default ({groups})")
check(access.own_permissions(accounts.get_by_name("hudba-vlastni"))["areas"] >= {"music", "music_comparison",
                                                                               "music_insights"}
      and not access.own_permissions(accounts.get_by_name("bez-hudby"))["areas"] & {"music_comparison",
                                                                                    "music_insights"},
      "an account's own rights: both added with Music, nothing without it")
# A group on the defaults (a fresh install, or nobody ever saved its rights): nothing is written for it - Music, its
# insights and the comparison follow their defaults, the music comparison like the video one (off for Jellyfin viewers;
# the final review of 22. 11. 2026: before, the conversion switched it on for them on every fresh install).
group_keys = [access.area_key(access.VIEWER, access.BY_KEY[key]) for key in ("music", "music_comparison", "music_insights")]
with db.connect() as conn:
    conn.execute(f"DELETE FROM settings WHERE key IN ({', '.join('?' * len(group_keys))})", tuple(group_keys))
db.set_setting(access.CONVERSION_MUSIC_KEY, "")
db.forget_settings()
check(access.split_music_rights() is True, "the conversion over a group on the defaults")
db.forget_settings()
left_alone = [db.get_setting(key, "unstored") for key in group_keys]
check(left_alone == ["unstored"] * 3 and access.sees(access.VIEWER, "music") and access.sees(access.VIEWER, "music_insights")
      and access.sees(access.VIEWER, "music_comparison") == access.sees(access.VIEWER, "comparison") is False,
      f"nothing written for it: Music and its insights on, the comparison off like the video one ({left_alone})")
db.set_setting(access.area_key(access.READER, access.BY_KEY["music_insights"]), "0")
db.forget_settings()
check(access.split_music_rights() is False
      and db.get_setting(access.area_key(access.READER, access.BY_KEY["music_insights"]), "") == "0",
      "and never again: insights switched off later stay off")

print()
print("--- the Books areas follow Music's for a group with stored rights ---")
# A group with stored rights falls back to "on" for an area never stored (READER), so books_who (names) and
# books_comparison would open for it although music_who / music_comparison are off. A one-off conversion copies the two
# stored values; books and books_insights stay at their defaults; a group on the defaults gets nothing; an account's own
# list is untouched.
MUSIC_TO_BOOKS = (("music_who", "books_who"), ("music_comparison", "books_comparison"))
all_keys = [access.area_key(group, access.BY_KEY[key]) for group in (access.READER, access.VIEWER)
            for key in ("music_who", "music_comparison", "books", "books_who", "books_comparison", "books_insights")]
with db.connect() as conn:
    conn.execute(f"DELETE FROM settings WHERE key IN ({', '.join('?' * len(all_keys))})", tuple(all_keys))
db.set_setting(access.area_key(access.READER, access.BY_KEY["music_who"]), "0")
db.set_setting(access.area_key(access.READER, access.BY_KEY["music_comparison"]), "1")
db.set_setting(access.area_key(access.VIEWER, access.BY_KEY["music_who"]), "1")      # the viewer: only this one stored
db.set_setting(access.CONVERSION_BOOKS_KEY, "")
db.forget_settings()
own_before = access.own_permissions(accounts.get_by_name("hudba-vlastni"))
check(access.sees(access.READER, "books_who") is True, "before: the reader group would see the readers' names (the fallback)")
check(access.split_books_rights() is True, "the conversion runs once")
db.forget_settings()


def stored(group: str, key: str) -> str:
    return db.get_setting(access.area_key(group, access.BY_KEY[key]), "unstored")


check([stored(access.READER, "books_who"), stored(access.READER, "books_comparison")] == ["0", "1"]
      and stored(access.VIEWER, "books_who") == "1" and stored(access.VIEWER, "books_comparison") == "unstored",
      "a stored music_who / music_comparison is copied; one that was never stored writes nothing")
check(not access.sees(access.READER, "books_who") and access.sees(access.READER, "books_comparison")
      and access.sees(access.VIEWER, "books_who"),
      "the reader group no longer sees the names; the viewer group does as it did for Music")
check(all(stored(group, key) == "unstored" for group in (access.READER, access.VIEWER) for key in ("books", "books_insights")),
      "books and books_insights stay at their defaults")
check(access.own_permissions(accounts.get_by_name("hudba-vlastni")) == own_before,
      "an account's own list is untouched")
db.set_setting(access.area_key(access.READER, access.BY_KEY["books_who"]), "1")
db.forget_settings()
check(access.split_books_rights() is False and stored(access.READER, "books_who") == "1",
      "and never again: books_who switched on later stays on")
with db.connect() as conn:
    conn.execute(f"DELETE FROM settings WHERE key IN ({', '.join('?' * len(all_keys))})", tuple(all_keys))
db.set_setting(access.CONVERSION_BOOKS_KEY, "")
db.forget_settings()
check(access.split_books_rights() is True, "the conversion over a group on the defaults runs")
db.forget_settings()
check(all(stored(group, key) == "unstored" for group in (access.READER, access.VIEWER) for _, key in MUSIC_TO_BOOKS),
      "a group on the defaults: nothing is written for it")

print()
print("--- the conversion copies the EFFECTIVE value (the parent counts) ---")
# The form stores sub-boxes independently: Music off with "...who listened" still ticked is stored as music=0,
# music_who=1. The stored music_who alone would give readers' names to Books (on by default).
for group in (access.READER, access.VIEWER):
    for music, who_listened, comparison, want_who, want_comparison in (
            ("0", "1", "1", "0", "0"), ("1", "1", "1", "1", "1"), ("1", "0", "0", "0", "0")):
        with db.connect() as conn:
            conn.execute(f"DELETE FROM settings WHERE key IN ({', '.join('?' * len(all_keys))})", tuple(all_keys))
            conn.execute("DELETE FROM settings WHERE key = ?", (access.area_key(group, access.BY_KEY["music"]),))
        db.set_setting(access.area_key(group, access.BY_KEY["music"]), music)
        db.set_setting(access.area_key(group, access.BY_KEY["music_who"]), who_listened)
        db.set_setting(access.area_key(group, access.BY_KEY["music_comparison"]), comparison)
        db.set_setting(access.CONVERSION_BOOKS_KEY, "")
        db.forget_settings()
        access.split_books_rights()
        db.forget_settings()
        check(access.sees(group, "books_who") is (want_who == "1") and access.sees(group, "books_comparison") is (want_comparison == "1"),
              f"{group}: music={music} who={who_listened} comparison={comparison} -> books_who/comparison {want_who}/{want_comparison}")
    with db.connect() as conn:
        conn.execute(f"DELETE FROM settings WHERE key IN ({', '.join('?' * len(all_keys))})", tuple(all_keys))
        conn.execute("DELETE FROM settings WHERE key = ?", (access.area_key(group, access.BY_KEY["music"]),))
    db.set_setting(access.CONVERSION_BOOKS_KEY, "")
    db.forget_settings()
    access.split_books_rights()
    db.forget_settings()
    check(all(stored(group, key) == "unstored" for key in ("books_who", "books_comparison")),
          f"{group}: on the defaults nothing is written")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
