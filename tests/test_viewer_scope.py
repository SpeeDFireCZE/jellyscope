# -*- coding: utf-8 -*-
r"""A viewer signed in through Jellyfin sees themselves - and nobody else.

Signing in with the name and password from Jellyfin means that people who have no business
in somebody else's history come into Jellyscope too. Splitting the data therefore must not
stand on no page having been forgotten: **what is not explicitly
allowed is forbidden** (see `web.VIEWER_MAY_POST`).

This test is that safety net. It walks through all the application's routes as a viewer
and in every answer looks for traces of the other viewer - their name and their
id. When somebody adds a page and forgets the scope, it falls over here,
and not at somebody's server.

Verified:

* signing in against Jellyfin (a viewer, an administrator, a disabled account, a wrong password),
* the rights are taken from Jellyfin at every sign-in,
* local accounts are not affected by it,
* a viewer gets only to the allowed addresses and only through GET,
* no answer has somebody else's name or somebody else's id,
* the administrator still sees everything.

Run:
    .venv/bin/python tests/test_viewer_scope.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rozsah.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, jellyfin, web  # noqa: E402
from _package import flat_routes  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# --- a fake Jellyfin -------------------------------------------------
IS_ADMIN = {"sef": True}
JELLYFIN_ACCOUNTS = {
    ("mirek", "tajne"): {"Id": "u-mirek", "Name": "Mirek",
                         "Policy": {"IsAdministrator": False}},
    ("sef", "tajne"): {"Id": "u-sef", "Name": "Sef", "Policy": {}},
    ("vypnuty", "tajne"): {"Id": "u-vyp", "Name": "Vypnuty",
                           "Policy": {"IsAdministrator": False,
                                      "IsDisabled": True}},
}


def server(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    details = JELLYFIN_ACCOUNTS.get((body.get("Username", ""), body.get("Pw", "")))
    if details is None:
        return httpx.Response(401, json={})
    if details["Id"] == "u-sef":
        details = {**details,
                   "Policy": {"IsAdministrator": IS_ADMIN["sef"]}}
    return httpx.Response(200, json={"User": details})


async def fake_client(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = fake_client

db.init_db()
accounts.create("admin", "longpassword", is_admin=True)

# Signing in with a Jellyfin password is off by default -
# opening the statistics of the whole household is a decision, not a side effect
# of an update. The test switches it on, because it is about it.
from jellyscope import access  # noqa: E402

db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()

OTHER_NAME = "KarelCizinec"
OTHER_ID = "u-karel"
with db.connect() as conn:
    for uid, name in (("u-mirek", "Mirek"), (OTHER_ID, OTHER_NAME)):
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (uid, name, db.utcnow()))
        for index in range(4):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, remote_address, is_active)"
                " VALUES (?,?,?,?,?,?,?,?,?,0)",
                (f"{uid}-{index}", uid, name, f"i-{index}",
                 f"Titul {index}", "2026-09-01 10:00:00",
                 "2026-09-01 10:20:00", 1200, "10.0.0.5"))
    conn.execute("INSERT INTO items (id, name, type) VALUES ('i-0','Titul 0','Movie')")
    conn.commit()


def client() -> TestClient:
    return TestClient(web.app)


def sign_in(name: str, password: str, method: str = "") -> tuple[TestClient, int]:
    """Signing in with the one Sign in button (27. 11. 2026): the server picks the door by the name.

    `method` is what a sign-in page from before sent; it is not read any more - the checks below prove it.
    """
    k = client()
    response = k.post("/login",
                      data={"username": name, "password": password,
                            "method": method},
                      follow_redirects=False)
    return k, response.status_code


print("--- signing in through Jellyfin ---")
k_viewer, status = sign_in("mirek", "tajne")
check(status == 303, f"a viewer signs in ({status})")
account = dict(db.query_one(
    "SELECT username, is_admin, jellyfin_user_id, password_hash"
    " FROM accounts WHERE jellyfin_user_id = 'u-mirek'") or {})
check(account.get("jellyfin_user_id") == "u-mirek", "the account is tied to the viewer")
check(not account.get("is_admin"), "and is not an administrator")
check(not account.get("password_hash"),
      "the password is not stored here - in only through Jellyfin")

_, status = sign_in("vypnuty", "tajne")
check(status == 401, f"a disabled account may not get in ({status})")
_, status = sign_in("mirek", "spatne")
check(status == 401, f"a wrong password does not pass ({status})")
_, status = sign_in("admin", "longpassword")
check(status == 303, "a local account still works")
_, status = sign_in("admin", "tajne", method="local")
check(status == 401, "and the Jellyfin password does not let one into it")

print()
print("--- the server picks the door by the name, not by the form ---")
_, status = sign_in("mirek", "tajne", method="local")
check(status == 303,
      "an account from Jellyfin goes to Jellyfin whatever an old form says")
_, status = sign_in("admin", "longpassword", method="jellyfin")
check(status == 303,
      "and a local account's password is checked here (it never goes to Jellyfin)")
page = client().get("/login").text
check('formaction="/login/quick"' in page,
      "with signing in switched on the code (Quick Connect) is on the page")

print()
print("--- the rights are taken from Jellyfin at every sign-in ---")
sign_in("sef", "tajne")
check(bool(dict(db.query_one(
    "SELECT is_admin FROM accounts WHERE jellyfin_user_id = 'u-sef'"))["is_admin"]),
    "Jellyfin's administrator is an administrator here too")
IS_ADMIN["sef"] = False
sign_in("sef", "tajne")
check(not dict(db.query_one(
    "SELECT is_admin FROM accounts WHERE jellyfin_user_id = 'u-sef'"))["is_admin"],
    "removed rights hold right at the next sign-in")

print()
print("--- where a viewer may go and where not ---")
k_viewer, _ = sign_in("mirek", "tajne")
ALLOWED = ("/users/u-mirek", "/history", "/history/new", "/video/insights", "/library",
           "/item/i-0", "/video/partials/top-items", "/partials/recently-added")
FORBIDDEN = ("/users", "/users/" + OTHER_ID, "/network", "/settings",
             "/dashboard", "/video/languages", "/video/compare", "/partials/now-playing",
             "/partials/network-live", "/api/now-playing")

for path in ALLOWED:
    status = k_viewer.get(path, follow_redirects=False).status_code
    check(status == 200, f"may {path} ({status})")
for path in FORBIDDEN:
    status = k_viewer.get(path, follow_redirects=False).status_code
    check(status == 403, f"may not {path} ({status})")

response = k_viewer.get("/", follow_redirects=False)
check(response.headers.get("location") == "/users/u-mirek",
      f"the Overview sends them to their page ({response.headers.get('location')})")
check(k_viewer.post("/item/i-0/delete",
                    follow_redirects=False).status_code == 403,
      "and deletes nothing - a viewer only reads")

print()
print("--- not a trace of the other viewer in the answers ---")
# This is the safety net: when somebody adds a page and forgets the scope,
# it falls over here. ALL the application's routes are walked through, not chosen ones.
paths = []
for route in flat_routes(web.app.routes):
    path = getattr(route, "path", "")
    if not path or "GET" not in (getattr(route, "methods", None) or set()):
        continue
    if "{" in path:
        path = (path.replace("{user_id}", "u-mirek")
                    .replace("{item_id}", "i-0")
                    .replace("{series_id}", "i-0")
                    .replace("{library_id}", "lib"))
    if "{" in path:
        continue
    paths.append(path)

# First EVERYTHING that can be allowed to a viewer is switched on, and
# anonymising with it. It is the worst case: the administrator opened every page,
# but names and addresses are not to be seen.
for area in access.AREAS:
    db.set_setting(access.area_key(access.VIEWER, area), "1")
db.set_setting(f"{access.VIEWER}_anonymise", "1")
db.forget_settings()

leaks = []
for path in sorted(set(paths)):
    response = k_viewer.get(path, follow_redirects=False)
    text = response.text if "text" in response.headers.get("content-type", "") \
        or "json" in response.headers.get("content-type", "") else ""
    if OTHER_NAME in text or OTHER_ID in text:
        leaks.append(f"{path} ({response.status_code})")

# A lower bound: if the routes stopped being walked through (include_router hides them
# in _IncludedRouter - exactly that happened after web.py was split), the safety net
# would quietly pass with one address.
check(len(set(paths)) >= 45, f"the whole application is walked through, not a few addresses ({len(set(paths))})")
check(not leaks,
      f"walked through {len(set(paths))} addresses, the other viewer leaks on: {leaks}")

# And the same with the address: a viewer is not to see other people's IP even on Network.
ip_leaks = []
for path in sorted(set(paths)):
    response = k_viewer.get(path, follow_redirects=False)
    if "10.0.0.5" in response.text:
        ip_leaks.append(f"{path} ({response.status_code})")
check(not ip_leaks, f"and the other IP address leaks on: {ip_leaks}")

# Back to the default state, so the further checks measure what they should.
for area in access.AREAS:
    db.set_setting(access.area_key(access.VIEWER, area),
                   "1" if area.default else "0")
db.forget_settings()

print()
print("--- the history is only theirs, whatever they write into the address ---")
text = k_viewer.get(f"/history?user_id={OTHER_ID}").text
check(OTHER_NAME not in text and OTHER_ID not in text,
      "the forced filter beats the address")
check("Mirek" in text, "and they see their own records")

print()
print("--- what a viewer sees is set by the administrator ---")


def set_options(**options):
    for key, value in options.items():
        db.set_setting(key, "1" if value else "0")
    db.forget_settings()


set_options(**{access.area_key(access.VIEWER, access.BY_KEY["users"]): True})
check(k_viewer.get("/users").status_code == 200,
      "a switched-on list of viewers opens")
set_options(**{access.area_key(access.VIEWER, access.BY_KEY["users"]): False})
check(k_viewer.get("/users").status_code == 403, "a switched-off one not again")

set_options(**{access.area_key(access.VIEWER, access.BY_KEY["network"]): True})
check(k_viewer.get("/network").status_code == 200, "the same holds for Network")
set_options(**{access.area_key(access.VIEWER, access.BY_KEY["network"]): False})

# A sub-area alone is not enough - "who watched it" without the library has nowhere to be.
set_options(**{access.area_key(access.VIEWER, access.BY_KEY["library"]): False,
               access.area_key(access.VIEWER, access.BY_KEY["library_who"]): True})
check(not access.sees(access.VIEWER, "library_who"),
      "a sub-area does not switch on without the one above it")
check(k_viewer.get("/library").status_code == 403, "and the library is closed")
set_options(**{access.area_key(access.VIEWER, access.BY_KEY["library"]): True})
check(access.sees(access.VIEWER, "library_who"), "with the library switched on it holds")

print()
print("--- anonymising: numbers instead of names, a dash instead of the address ---")
set_options(**{access.area_key(access.VIEWER, access.BY_KEY["users"]): True,
               access.area_key(access.VIEWER, access.BY_KEY["network"]): True,
               f"{access.VIEWER}_anonymise": True})
text = k_viewer.get("/users").text
check(OTHER_NAME not in text, "the other name is not on the list of viewers")
check("Viewer " in text, "a nickname is there")
check("Mirek" in text, "and the viewer sees themselves under their own name")
check("10.0.0.5" not in k_viewer.get("/network").text,
      "the other IP address does not show")
check(access.nickname(OTHER_ID) == access.nickname(OTHER_ID),
      "the nickname is stable")

set_options(**{f"{access.VIEWER}_anonymise": False})
check(OTHER_NAME in k_viewer.get("/users").text,
      "without anonymising the names show (when the administrator wants it)")
set_options(**{f"{access.VIEWER}_anonymise": True,
               access.area_key(access.VIEWER, access.BY_KEY["users"]): False,
               access.area_key(access.VIEWER, access.BY_KEY["network"]): False})

print()
print("--- switched-off signing in lets nobody from Jellyfin in ---")
check(k_viewer.get("/history", follow_redirects=False).status_code == 200, "a viewer is signed in")
set_options(**{access.LOGIN_KEY: False})
response = k_viewer.get("/history", follow_redirects=False)
check(response.status_code in (302, 303, 307) and "/login" in response.headers.get("location", ""),
      f"switching it off ends the session of a viewer signed in before ({response.status_code})")
_, status = sign_in("mirek", "tajne")
check(status == 401, f"a viewer does not sign in ({status})")
check('formaction="/login/quick"' not in client().get("/login").text,
      "and the code is not on the sign-in page at all")
_, status = sign_in("admin", "longpassword")
check(status == 303, "a local account can still get in")
set_options(**{access.LOGIN_KEY: True})

print()
print("--- the settings can be saved from the page ---")
k_admin_saving, _ = sign_in("admin", "longpassword")
response = k_admin_saving.post(
    "/settings/viewers",
    data={"jellyfin_login": "on", "sees_charts": "on", "anonymise": "on"},
    follow_redirects=False)
check(response.headers.get("location", "").endswith("#viewers"),
      "saving returns to the card")
check(access.login_enabled() and access.sees(access.VIEWER, "charts"),
      "what was ticked was saved")
check(not access.sees(access.VIEWER, "users"),
      "and what is not ticked was switched off")
page = k_admin_saving.get("/settings?section=jellyfin").text
check('name="sees_charts"' in page and 'name="sees_network"' in page,
      "the tree is on the page whole")
check(page.count('form="viewers-form"') >= len(access.AREAS) + 2,
      "and all the tick boxes belong to its form")
for area in access.AREAS:
    db.set_setting(access.area_key(access.VIEWER, area),
                   "1" if area.default else "0")
db.set_setting(f"{access.VIEWER}_anonymise", "1")
db.forget_settings()

print()
print("--- a local reader account has its own tree ---")
# A reader account is created by the administrator by hand and has always seen the statistics whole.
# After an update nothing may therefore change for it - things can be taken away, not added.
accounts.create("reader", "longpassword", is_admin=False)
k_reader, status = sign_in("reader", "longpassword")
check(status == 303, f"a reader signs in ({status})")
for path in ("/video/overview", "/users", "/network", "/video/languages", "/video/compare",
             f"/users/{OTHER_ID}"):
    code = k_reader.get(path, follow_redirects=False).status_code
    check(code == 200, f"by default sees {path} ({code})")
check(k_reader.get("/settings", follow_redirects=False).status_code == 403,
      "may not go into Settings - that is the administrator's thing")
check(k_reader.post("/settings/connection", data={},
                    follow_redirects=False).status_code == 403,
      "and all the more changes nothing in them")
check(OTHER_NAME in k_reader.get("/users").text,
      "and sees the names, as they are used to")

set_options(**{access.area_key(access.READER,
                               access.BY_KEY["network"]): False})
check(k_reader.get("/network", follow_redirects=False).status_code == 403,
      "an unticked Network closes for them")
check(k_reader.get("/users", follow_redirects=False).status_code == 200,
      "the rest stays for them")

set_options(**{f"{access.READER}_anonymise": True})
text = k_reader.get("/users").text
check(OTHER_NAME not in text and "Viewer " in text,
      "with anonymising switched on there are numbers instead of names here too")
check("10.0.0.5" not in k_reader.get("/history").text,
      "and the address does not show even in the history")
set_options(**{f"{access.READER}_anonymise": False,
               access.area_key(access.READER,
                               access.BY_KEY["network"]): True})

check(access.role({"is_admin": 1}) == access.ADMIN, "role: administrator")
check(access.role({"jellyfin_user_id": "u-1"}) == access.VIEWER, "role: viewer")
check(access.role({"jellyfin_user_id": None}) == access.READER, "role: reader")

print()
print("--- and its tree is in Settings → Accounts ---")
k_admin_readers, _ = sign_in("admin", "longpassword")
page = k_admin_readers.get("/settings?section=accounts").text
check('id="readers"' in page and 'action="/settings/readers"' in page,
      "the card is there")
check(page.count('form="readers-form"') >= len(access.AREAS) + 1,
      "with all the tick boxes")
response = k_admin_readers.post("/settings/readers",
                                data={"sees_library": "on"},
                                follow_redirects=False)
check(response.headers.get("location", "").endswith("#readers"),
      "saving returns to the card")
check(access.sees(access.READER, "library")
      and not access.sees(access.READER, "network"),
      "and exactly what was ticked was saved")
for area in access.AREAS:
    db.set_setting(access.area_key(access.READER, area), "1")
db.forget_settings()

print()
print("--- the administrator still sees everything ---")
k_admin, _ = sign_in("admin", "longpassword")
for path in ("/video/overview", "/users", f"/users/{OTHER_ID}", "/network", "/settings",
             "/video/languages", "/video/compare"):
    status = k_admin.get(path, follow_redirects=False).status_code
    check(status == 200, f"the administrator may {path} ({status})")
check(OTHER_NAME in k_admin.get("/users").text,
      "and has the other viewers in the list")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
