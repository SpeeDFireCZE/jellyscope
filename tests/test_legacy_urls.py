# -*- coding: utf-8 -*-
r"""Old addresses keep working: every one redirects (308) to its new address.

The menu by sections moved the pages of each section under the section's
address (/video/..., /music/...). Bookmarks, links in old notes and forms
of pages still open in a browser keep working: every old address answers
308 with the new address, query string included, and the new address is
checked by the permission guard as usual - an old address never bypasses
it. The table of old -> new addresses is jellyscope/legacy_urls.py.

Run:
    .venv/bin/python tests/test_legacy_urls.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "legacy-urls.db")
os.environ["SECRET_KEY"] = "test-key-legacy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from _apps import login  # noqa: E402
from _package import flat_routes  # noqa: E402
from jellyscope import access, accounts, db, demo_world, demodata, legacy_urls, web  # noqa: E402

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
QUERY = "days=30&kind=movies"
# An artist and an album of the demo's music (demodata._seed_music), by their readable keys (demo_world).
ARTIST, ALBUM = demo_world.jellyfin_id("demo-int-1"), demo_world.jellyfin_id("demo-alb-1")

print("--- every old address redirects to its new address, query included ---")
# Path parameters get sample values (the same name on both sides of an entry); every method of an
# entry is tried - a form's POST keeps its method and body through 308.
SAMPLE = {"app": "radarr", "item_id": "it-1", "library_id": "5", "title_id": "77", "detail": "1", "x": "3",
          "y": "4", "kind": "tv", "tmdb_id": "123", "artist_id": ARTIST, "album_id": ALBUM}
# The pages among the new addresses (not images, downloads or fragments) open for the admin.
PAGES = {new for _old, new, _methods in legacy_urls.LEGACY_URLS}      # every entry is a page


def filled(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: SAMPLE[m.group(1)], path)


routes = {(r.path, m) for r in flat_routes(web.app.router.routes) if hasattr(r, "path")
          for m in (getattr(r, "methods", None) or [])}
for old, new, methods in legacy_urls.LEGACY_URLS:
    check(all((new, m) in routes for m in methods), f"{new} exists for {methods}")
    for method in methods:
        response = admin.request(method, f"{filled(old)}?{QUERY}", follow_redirects=False)
        check(response.status_code == 308 and response.headers.get("location") == f"{filled(new)}?{QUERY}",
              f"{method} {filled(old)} -> 308 {filled(new)}?{QUERY} ({response.status_code}, "
              f"{response.headers.get('location')})")
    if new in PAGES:
        check(admin.get(filled(new)).status_code == 200, f"{filled(new)} opens (200)")
check(legacy_urls.legacy_target("/insights", "days=7") == "/video/insights?days=7"
      and legacy_urls.legacy_target("/insights", "") == "/video/insights"
      and legacy_urls.legacy_target("/neznama", "") is None and legacy_urls.is_legacy("/top-items")
      and not legacy_urls.is_legacy("/video/insights"), "legacy_target and is_legacy")

print()
print("--- the overview moved from / ---")
response = admin.get("/?days=7&kind=series", follow_redirects=False)
check(response.status_code in (303, 307, 308) and response.headers.get("location") == "/video/overview?days=7&kind=series",
      f"/?days=7&kind=series -> /video/overview?days=7&kind=series ({response.status_code}, {response.headers.get('location')})")
response = admin.get("/", follow_redirects=False)
check(response.status_code in (303, 307) and response.headers.get("location") == "/video/overview",
      f"/ -> /video/overview ({response.status_code}, {response.headers.get('location')})")
check("Přehled" in admin.get("/video/overview").text, "the overview is at /video/overview")

print()
print("--- the music section is under /music (its old Czech addresses are gone, test_addresses) ---")
# The demo data have music (demodata._seed_music): an artist and an album with known ids.
for new in ("/music/overview?days=30", f"/music/artist/{ARTIST}", f"/album/{ALBUM}"):
    check(admin.get(new).status_code == 200, f"{new} opens (200)")
music = admin.get("/music/overview?days=365").text
check(bool(re.search(r'href="/music/artist/[0-9a-f]{32}', music) and re.search(r'href="/album/[0-9a-f]{32}', music)),
      "the Music page links artists to /music/artist/ and albums to /album/")
check(bool(re.search(r'href="/album/[0-9a-f]{32}', admin.get("/history?kind=music").text)),
      "History links a track to its album at /album/")

print()
print("--- no template links to an old address or to / with parameters ---")
# An old address only redirects, and / opens the default page (which may be Music or the
# dashboard): a link like href="/?kind=movies" on the overview would leave the overview.
# The code review of 16. 11. 2026 found such links in the day card, the top-items card,
# the pager of files without a language and "back to the overview".
TEMPLATES = PROJECT / "jellyscope" / "templates"
stale = []
for template in sorted(TEMPLATES.rglob("*.html")):
    text = template.read_text(encoding="utf-8")
    for found in re.finditer(r"""["'](/[^"'\s{]*)""", text):
        address = found.group(1)
        path = address.split("?", 1)[0].split("#", 1)[0]
        if address.startswith("/?") or legacy_urls.is_legacy(path):
            stale.append(f"{template.relative_to(TEMPLATES)}: {address}")
check(not stale, f"templates link to new addresses only ({stale[:6]})")
overview = admin.get("/video/overview").text
check('href="?kind=movies"' in overview and 'href="?top_kind=movies"' in overview,
      "the kind chips of the overview cards stay on the page they are on")
from jellyscope import cards  # noqa: E402
db.set_setting(cards.NAME_KEY, "Rodina")
db.set_setting(cards.ENABLED_KEY, "1")
cards.save_layout(["watching_by_day"])
db.forget_settings()
dashboard = admin.get("/dashboard?kind=movies").text
chip = re.search(r'<a class="chip active"[^>]*data-kind="(\w+)"', dashboard)
check(chip is not None and chip.group(1) == "movies", f"a dashboard reads the chip's choice from its address ({chip and chip.group(1)})")
db.set_setting(cards.ENABLED_KEY, "0")
db.forget_settings()
back = re.search(r'<a [^>]*href="([^"]*)"[^>]*>&larr;', admin.get("/video/insights/top").text)
check(back is not None and back.group(1) == "/video/overview",
      f"the full ranking leads back to the overview, not to the default page ({back and back.group(1)})")

print()
print("--- only pages redirect; the application's own addresses do not ---")
# A form target, a fragment, an image: the templates and scripts use the new address, a page left
# open across an update is reloaded by it - an old one is simply unknown.
for method, old in (("POST", "/languages/preferred"), ("GET", "/partials/daily"), ("POST", "/account/klic"),
                    ("GET", "/partials/arr/radarr/stahuje-se"), ("GET", "/arr/radarr/plakat/1")):
    response = admin.request(method, old, follow_redirects=False)
    check(response.status_code in (404, 405) and not legacy_urls.is_legacy(old),
          f"{method} {old}: no redirect ({response.status_code})")

print()
print("--- old parameter names of a page or an API address redirect too, for a while ---")
for old, new in (("/video/overview?od=2026-01-01&do=2026-01-31&days=7",
                  "/video/overview?since=2026-01-01&until=2026-01-31&days=7"),
                 ("/history?obdobi=vse&kind=movies", "/history?period=all&kind=movies"),
                 ("/history?obdobi=dnes", "/history?period=today"),
                 ("/library?rust_disk=vse", "/library?growth_disk=all"),
                 ("/srovnani?a_od=2026-01-01&a_do=2026-01-31", "/video/compare?a_since=2026-01-01&a_until=2026-01-31"),
                 ("/settings?section=log&log_druh=api", "/settings?section=log&log_kind=api")):
    response = admin.get(old, follow_redirects=False)
    check(response.status_code == 308 and response.headers.get("location") == new,
          f"{old} -> 308 {new} ({response.status_code}, {response.headers.get('location')})")
# The redirect runs before signing in: it must never send anyone off this server. A path that decodes
# to "//host" (or is one) would make Location a scheme-relative address of another site (review of 10d2).
for path in ("/%2Fevil.example/login?od=1", "//evil.example/login?od=1", "/%5Cevil.example/login?od=1"):
    response = admin.get(path, follow_redirects=False)
    target = response.headers.get("location", "")
    check(not target.startswith(("//", "/\\")), f"{path}: the redirect stays on this server ({response.status_code}, {target!r})")
response = admin.get("/users/skryto-%3F?od=1", follow_redirects=False)
check(response.headers.get("location", "").startswith("/users/skryto-%3F?"),
      f"an encoded segment stays encoded in the redirect ({response.headers.get('location')!r})")
# The API never read any of those names (its own are English), so it keeps its own answer to an
# unknown parameter - a JSON 400 naming the parameter that was sent, not a redirect to a new name.
response = admin.get("/api/v1/summary?od=2026-01-01", follow_redirects=False)
check(response.status_code != 308, f"an API address keeps its own answer to an old name ({response.status_code})")
for method, path in (("GET", "/video/partials/daily?od=2026-01-01"), ("POST", "/library?rust_disk=vse"),
                     ("GET", "/video/overview?since=2026-01-01&until=2026-01-31")):
    response = admin.request(method, path, follow_redirects=False)
    check(response.status_code != 308, f"{method} {path}: no redirect ({response.status_code})")

print()
print("--- an old address never bypasses the permission guard ---")
reader = accounts.get_by_name("reader")
accounts.save_permissions(reader["id"], access.pack_permissions([o.key for o in access.AREAS if o.default and o.key != "charts"], False))
db.forget_settings()
reader_client = login("reader")
response = reader_client.get("/insights")
check(response.status_code == 403 and str(response.url).endswith("/video/insights"),
      f"a reader without Insights: /insights -> /video/insights -> 403 ({response.status_code}, {response.url})")
check(reader_client.get("/library").status_code == 200, "the reader still opens the Library (another default area)")
response = reader_client.get("/video/insights?od=2026-01-01&do=2026-01-31", follow_redirects=False)
check(response.status_code == 308 and reader_client.get(response.headers.get("location", "/")).status_code == 403,
      f"old parameters only redirect; the new address stops a reader without Insights ({response.status_code})")
response = reader_client.get("/hudba", follow_redirects=False)
check(response.status_code == 308 and response.headers.get("location") == "/music/overview"
      and reader_client.get("/hudba").status_code == 200,
      f"a reader with Music: an old Czech bookmark leads to the page, not to 403 ({response.status_code})")

print()
print("--- a form from a page older than the upgrade saves nothing (part 10d3 review) ---")
# The field names are English since part 10d3, with no old names read (decision 5). A Save from a tab
# left open across the upgrade would send the old names: the route would read empty values and save
# them - the whole dashboard layout gone, an account's own permissions back to its group's, the
# notification address emptied. Until TODO item 0 such a form is turned back unsaved, with a word why.
from jellyscope import cards, notifications  # noqa: E402
cards.save_layout("codecs,summary")
db.set_setting(notifications.setting_key("smtp", "komu"), "kept@example.test")
db.forget_settings()
for action, form in (("/dashboard/layout", {"poradi": "codecs"}),
                     ("/settings/notifications", {"smtp_host": "x", "smtp_komu": ""}),
                     ("/settings/accounts/permissions", {"account_id": "1", "rezim": "skupina"})):
    response = admin.post(action, data=form, headers={"referer": "http://testserver/settings?section=notifications"},
                          follow_redirects=False)
    db.forget_settings()
    page = admin.get(response.headers.get("location", "/"), follow_redirects=True).text if response.status_code == 303 else ""
    check(response.status_code == 303 and "načti" in page.lower()
          and [c.key for c in cards.load_layout()] == ["codecs", "summary"]
          and db.get_setting(notifications.setting_key("smtp", "komu"), "") == "kept@example.test",
          f"{action} with an old field name: nothing saved, the page says to reload ({response.status_code})")
# The database step 1 renamed the music task's key ("music" -> "music"), and with it the fields of the
# Tasks form built from it: an old tab would turn the music task off.
from jellyscope import tasks as _tasks  # noqa: E402
db.set_setting(_tasks.TASKS["music"].enabled_setting, "1")
db.set_setting("music_sync_minutes", "45")
db.forget_settings()
response = admin.post("/settings/tasks", data={"enabled_sync": "1", "enabled_hudba": "1", "minutes_hudba": "45"},  # old name on purpose
                      headers={"referer": "http://testserver/settings?section=tasks"}, follow_redirects=False)
db.forget_settings()
check(response.status_code == 303 and db.get_setting(_tasks.TASKS["music"].enabled_setting) == "1"
      and db.get_setting("music_sync_minutes") == "45",
      f"the Tasks form with the music task's old field names saves nothing ({response.status_code})")
check(admin.post("/dashboard/layout", data={"order": "summary"}, follow_redirects=False).status_code == 303
      and [c.key for c in cards.load_layout()] == ["summary"], "the same form with the new name saves")
backs = []
for referer in ("https://evil.example/x", "http://testserver//evil.example/x", "http://testserver/\\evil.example"):
    outside = admin.post("/dashboard/layout", data={"poradi": "codecs"}, headers={"referer": referer},  # old name on purpose
                         follow_redirects=False)
    backs.append(outside.headers.get("location", ""))
check(backs == ["/", "/", "/"], f"the way back never leaves this server ({backs})")


print("--- an old session cookie (database step 6) keeps what was chosen ---")
import base64  # noqa: E402
import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402

from _apps import session_data  # noqa: E402
from jellyscope import api, web_sessions  # noqa: E402
from jellyscope.config import load_config  # noqa: E402

SIGNER = TimestampSigner(str(load_config().secret_key))
COOKIE = "jellyscope_session"


def old_cookie(data: dict) -> str:
    """A session cookie the way Starlette signs it - here with the keys of the code before the step."""
    return SIGNER.sign(base64.b64encode(json.dumps(data).encode("utf-8"))).decode("utf-8")


def session_of(client: TestClient) -> dict:
    raw = client.cookies.get(COOKIE) or ""
    return json.loads(base64.b64decode(SIGNER.unsign(raw.encode("utf-8")))) if raw else {}


def with_cookie(data: dict) -> TestClient:
    client = TestClient(web.app)
    client.cookies.set(COOKIE, old_cookie(data), domain="testserver.local")   # where the answer's cookie lands
    return client


admin_account = accounts.get_by_name("admin")
# A cookie from before the sessions on the server has no token: it is signed out once (the owner, 25. 11. 2026).
without_record = {"account_id": admin_account["id"], "otisk": accounts.session_fingerprint(admin_account)}
old = with_cookie(without_record)
check(old.get("/video/overview", follow_redirects=False).status_code in (303, 307) and not session_of(old),
      "a cookie of an older version has no session on the server: it signs in once more")
# The keys are converted all the same - here in a cookie that has its record (tests/_apps.session_data).
signed = {**without_record, web_sessions.COOKIE_KEY: session_data(admin_account["id"])[web_sessions.COOKIE_KEY]}
old = with_cookie(signed)
overview = old.get("/video/overview", follow_redirects=False)
now = session_of(old)
check(overview.status_code == 200 and now.get("fingerprint") == signed["otisk"] and "otisk" not in now,
      f"signed in with the old key of the fingerprint, the session carries the new one ({overview.status_code}, {sorted(now)})")
chosen = with_cookie({**signed, "days": {"od": "2026-11-01", "do": "2026-11-10"},
                      "srovnani_a": {"od": "2026-10-01", "do": "2026-10-31"}, "srovnani_b": 30, "rust_disk": "dev-a"})
compare_page = chosen.get("/video/compare", follow_redirects=False)
now = session_of(chosen)
check(compare_page.status_code == 200 and now.get("days") == {"since": "2026-11-01", "until": "2026-11-10"}
      and now.get("compare_a") == {"since": "2026-10-01", "until": "2026-10-31"} and now.get("compare_b") == 30
      and now.get("growth_disk") == "dev-a" and not {"srovnani_a", "srovnani_b", "rust_disk"} & set(now),
      f"the period, the two compared periods and the growth disk under their new keys ({now})")
token = api.create_token("fresh from the old session")
fresh = with_cookie({**signed, "novy_api_token": {"id": token["id"], "token": token["token"]}})
api_page = fresh.get("/settings?section=api", follow_redirects=False).text
check(token["token"] in api_page and "new_api_token" not in session_of(fresh) and "novy_api_token" not in session_of(fresh),
      "a key made just before the upgrade is shown once, then gone")
both = with_cookie({**signed, "days": 7, "rust_disk": "old-disk", "growth_disk": "new-disk"})
both.get("/video/overview", follow_redirects=False)
check(session_of(both).get("growth_disk") == "new-disk" and "rust_disk" not in session_of(both),
      "an old and a new key: the new one wins, the old one goes")
plain = {"account_id": 1, "fingerprint": "x", "days": {"since": "2026-11-01", "until": "2026-11-02"}}
check(legacy_urls.upgrade_session(dict(plain)) is False, "a session of this version is left as it is")
print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
