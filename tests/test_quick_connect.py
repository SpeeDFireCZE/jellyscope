# -*- coding: utf-8 -*-
r"""Signing in with a code (Quick Connect): the password does not pass this way at all.

Signing in with a Jellyfin password means that somebody else's password passes through our
application. Quick Connect turns it around: Jellyscope asks for a code, a person
confirms it in their **already signed-in Jellyfin**, and only then we get
the identity.

It all stands on one difference the test watches: what goes to the browser is
**the code**, what stays on the server is **the secret**. The code by itself lets nobody
anywhere - without the confirmation in Jellyfin it is six digits. The secret lets one in,
and that is why it must never appear on the page nor in the session cookie (signed, not
encrypted: it waits in the server's memory, web/base.py hold_secret).

Run:
    .venv/bin/python tests/test_quick_connect.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "quick.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import json  # noqa: E402

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import read_session  # noqa: E402
from jellyscope import access, accounts, db, jellyfin, web  # noqa: E402
from jellyscope.web import base as web_base  # noqa: E402
from jellyscope.web import login as web_login  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


SECRET = "TajemstviQXZ0123456789"
CODE = "909919"

db.init_db()
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", "ApiKlicQXZ")
db.forget_settings()
# Without a single account /login sends to the first setup - the sign-in
# page would not be drawn at all.
accounts.create("admin", "longpassword", is_admin=True)

# The state of the pretend server: what it answers and what it has already got from us.
state = {"enabled": True, "confirmed": False, "known": True}
REQUESTS: list[httpx.Request] = []
REVOKED: list[str] = []


def server(request: httpx.Request) -> httpx.Response:
    REQUESTS.append(request)
    path = request.url.path
    if path == "/QuickConnect/Enabled":
        return httpx.Response(200, text="true" if state["enabled"] else "false")
    if path == "/QuickConnect/Initiate":
        return httpx.Response(200, json={"Authenticated": False,
                                         "Secret": SECRET, "Code": CODE})
    if path == "/QuickConnect/Connect":
        if not state["known"]:
            # Jellyfin forgets a code after ten minutes; an unknown secret is a 404 there.
            return httpx.Response(404)
        return httpx.Response(200, json={"Authenticated": state["confirmed"]})
    if path == "/Users/AuthenticateWithQuickConnect":
        return httpx.Response(200, json={
            "AccessToken": "TokenQXZ",
            "User": {"Id": "u-mirek", "Name": "Mirek", "Policy": {},
                     "PrimaryImageTag": "qc-novy"}})
    if path == "/Sessions/Logout":
        REVOKED.append(request.headers.get("Authorization", ""))
        return httpx.Response(204)
    return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})


async def fake_client(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = fake_client

print("--- until the administrator switches the Jellyfin login on, there is no way through here ---")
client = TestClient(web.app)
check("/login/quick" not in client.get("/login").text,
      "the button does not show on the sign-in page")
response = client.post("/login/quick")
check(response.status_code == 400, f"and the route refuses ({response.status_code})")
check(not REQUESTS, "nobody asked Jellyfin anything meanwhile")

db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()

print()
print("--- switched on: the code is on the page, the secret stayed on the server ---")
page = client.get("/login").text
check("/login/quick" in page, "the button appeared")

response = client.post("/login/quick")
check(response.status_code == 200, f"the dialog with the code opened "
                                   f"({response.status_code})")
check(CODE in response.text, "and the code is seen in it")
check(SECRET not in response.text,
      "the secret is NOT on the page - with it anybody would sign in")
# The session cookie is signed, not encrypted: whoever sees it reads it - so it is read here without the key (audit SA-11).
check(SECRET not in json.dumps(read_session(client)),
      f"nor in the session cookie - it carries only a handle ({sorted(read_session(client))})")

print()
print("--- waiting for the confirmation ---")
status_response = client.get("/login/quick/status")
check(status_response.json() == {"done": False},
      f"unconfirmed = nothing more than \"not yet\" ({status_response.json()})")
check(not db.query_one(
    "SELECT id FROM accounts WHERE jellyfin_user_id = 'u-mirek'"),
    "the account has not been created yet")
check(client.get("/", follow_redirects=False).status_code == 303,
      "and nobody got in yet")

# The viewer is already synchronised, with the old profile image.
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, image_tag)"
                 " VALUES ('u-mirek', 'Mirek', 0, 0, 'qc-stary')")
    conn.commit()

state["confirmed"] = True
web_login._quick_polls.clear()      # the status may ask Jellyfin once a second; this is the next second
status_response = client.get("/login/quick/status")
check(status_response.json() == {"done": True},
      f"after the confirmation they signed in ({status_response.json()})")

account = db.query_one(
    "SELECT * FROM accounts WHERE jellyfin_user_id = 'u-mirek'")
check(account is not None, "the account was created from what Jellyfin said")
check(not (dict(account or {}).get("password_hash") or ""),
      "and it has no password - none was given through Quick Connect")
check(not dict(account or {}).get("is_admin"),
      "whoever is not an administrator in Jellyfin is not an administrator here either")
check(db.query_one("SELECT image_tag FROM users WHERE id = 'u-mirek'")["image_tag"]
      == "qc-novy", "the profile image's fingerprint was refreshed with the sign-in")

print()
print("--- what went out and what we left behind ---")
check(not [z for z in REQUESTS if z.url.path == "/Users/AuthenticateByName"],
      "the server never learnt any password")
initiations = [z for z in REQUESTS if z.url.path == "/QuickConnect/Initiate"]
check(len(initiations) == 1, f"the code was requested once ({len(initiations)})")
check("Authorization" in initiations[0].headers,
      "our key goes in the header, not in the address")
check("ApiKlicQXZ" not in str(initiations[0].url), "it really is not in the address")
check(all(z.url.host == "jellyfin.test" for z in REQUESTS),
      "and everything only to the server from the settings")
check(REVOKED and "TokenQXZ" in REVOKED[0],
      f"the token from the sign-in was revoked right away ({REVOKED})")

# `DeviceId` must be the same at the start and at the end - Jellyfin pairs
# the request with the confirmation by it. If it differed, the code could be confirmed
# and the sign-in would still never finish.
completions = [z for z in REQUESTS
               if z.url.path == "/Users/AuthenticateWithQuickConnect"]
check(len(completions) == 1, "and it finished once")
check(initiations[0].headers.get("Authorization")
      == completions[0].headers.get("Authorization"),
      "the start and the end report with the same device")

print()
print("--- a viewer sees their own, may not go into the settings ---")
check(client.get("/settings", follow_redirects=False).status_code == 403,
      "Settings are for the administrator")
check(client.get("/", follow_redirects=False).status_code == 303,
      "and the signpost sends them to their own page")

print()
print("--- when the server does not have Quick Connect switched on ---")
state["enabled"] = False
fresh = TestClient(web.app)
response = fresh.post("/login/quick")
check(response.status_code == 400, f"the dialog with the code does not open "
                                   f"({response.status_code})")
check("Quick Connect" in response.text,
      "and the page says it is switched on in Jellyfin")
state["enabled"] = True

print()
print("--- an expired or somebody else's code lets nobody in ---")
stranger = TestClient(web.app)
check(stranger.get("/login/quick/status").json() == {"done": False, "end": True},
      "without a start there is nothing to finish")

switched_off = TestClient(web.app)
switched_off.post("/login/quick")
db.set_setting(access.LOGIN_KEY, "0")
db.forget_settings()
check(switched_off.get("/login/quick/status").json()
      == {"done": False, "end": True},
      "and a code in progress ends the moment the administrator switches the login off")

print()
print("--- a code Jellyfin forgot, an old code and a status asked too often ---")
# The status answered "not yet" for ever once Jellyfin had forgotten the code: the page never said it expired, and
# whoever had the cookie of one started code could make Jellyscope ask Jellyfin as fast as they liked (audit SA-13).
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()


def asked_status() -> int:
    return len([z for z in REQUESTS if z.url.path == "/QuickConnect/Connect"])


forgotten = TestClient(web.app)
forgotten.post("/login/quick")
forgotten_cookies = httpx.Cookies(forgotten.cookies)
forgotten_handle = read_session(forgotten).get("quick_handle")
state["known"], state["confirmed"] = False, False
web_login._quick_polls.clear()      # a clean count: the status asks Jellyfin at most once a second per code
before = asked_status()
check(forgotten.get("/login/quick/status").json() == {"done": False, "end": True},
      "a code Jellyfin no longer knows ends, so the page says it is no longer valid")
check(forgotten.get("/login/quick/status").json() == {"done": False, "end": True} and asked_status() == before + 1,
      "and the secret is dropped - nothing more is asked")
replayed = TestClient(web.app)
replayed.cookies = forgotten_cookies
for _ in range(10):
    replayed.get("/login/quick/status")
check(asked_status() == before + 1, f"nor through an old copy of the cookie, within the second ({asked_status() - before})")
state["known"] = True

old_code = TestClient(web.app)
old_code.post("/login/quick")
lifetime = web_login.QUICK_CODE_LIFETIME
web_login.QUICK_CODE_LIFETIME = -1
before = asked_status()
try:
    answer = old_code.get("/login/quick/status").json()
finally:
    web_login.QUICK_CODE_LIFETIME = lifetime
check(answer == {"done": False, "end": True} and asked_status() == before,
      f"a code older than Jellyfin's lifetime ends without asking Jellyfin ({answer})")

impatient = TestClient(web.app)
impatient.post("/login/quick")
web_login._quick_polls.clear()
before = asked_status()
answers = [impatient.get("/login/quick/status").json() for _ in range(30)]
check(asked_status() - before == 1 and all(a == {"done": False} for a in answers),
      f"30 questions in a row ask Jellyfin once - at most once a second ({asked_status() - before})")

print()
print("--- the secret lives on the server only as long as the code ---")
# The secret is held in the server's memory under a handle (web/base.py hold_secret), and only until the code is
# confirmed, refused or expires. A restart forgets it: a code in progress simply has to be started again.
restarted = TestClient(web.app)
restarted.post("/login/quick")
handle = read_session(restarted).get("quick_handle")
web_base._held_secrets.clear()          # what a restart of the app does to the memory
before = asked_status()
check(restarted.get("/login/quick/status").json() == {"done": False, "end": True} and asked_status() == before,
      "after a restart the code ends without an error, nobody asks Jellyfin")
check(handle and "quick_handle" not in read_session(restarted), "and the unknown handle left the session")
check(forgotten_handle and web_base.peek_secret(forgotten_handle) is None,
      "a code Jellyfin forgot took its secret out of the server's memory too")

signed_in = TestClient(web.app)
signed_in.post("/login/quick")
handle = read_session(signed_in).get("quick_handle")
copy_before = httpx.Cookies(signed_in.cookies)
state["confirmed"] = True
web_login._quick_polls.clear()
check(signed_in.get("/login/quick/status").json() == {"done": True}, "a confirmed code signs in")
check(web_base.peek_secret(handle) is None, "and its secret is forgotten at once")
finished = len([z for z in REQUESTS if z.url.path == "/Users/AuthenticateWithQuickConnect"])
web_login._quick_polls.clear()
again = TestClient(web.app)
again.cookies = copy_before
check(again.get("/login/quick/status").json() == {"done": False, "end": True}
      and len([z for z in REQUESTS if z.url.path == "/Users/AuthenticateWithQuickConnect"]) == finished,
      "a copy of the cookie from before the sign-in signs nobody in a second time")
state["confirmed"] = False

print()
print("--- the brake: codes cannot be started endlessly ---")
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
state["enabled"] = True
from jellyscope import accounts as _accounts  # noqa: E402

_accounts._attempts.clear()
initiations_before = len([z for z in REQUESTS if z.url.path == "/QuickConnect/Initiate"])
guesser = TestClient(web.app)
codes = [guesser.post("/login/quick").status_code for _ in range(12)]
check(codes[:7] == [200] * 7, f"a few codes pass ({codes[:7]})")
check(429 in codes, f"then the brake ({codes})")
initiated = len([z for z in REQUESTS if z.url.path == "/QuickConnect/Initiate"]) - initiations_before
check(initiated < 12, f"and no more go to Jellyfin ({initiated} of 12)")
check(guesser.post("/login", data={"username": "admin", "password": "longpassword",
                                   "method": "local"},
                   follow_redirects=False).status_code == 429,
      "the block holds for the password too - it is one address")
with db.connect() as conn:
    conn.execute("DELETE FROM login_blocks")
    conn.commit()
_accounts._attempts.clear()

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
