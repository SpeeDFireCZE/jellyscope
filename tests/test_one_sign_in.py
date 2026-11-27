# -*- coding: utf-8 -*-
"""One Sign in button: the server picks the door by the name (27. 11. 2026).

A name with a local account that has a password is checked here only - a wrong password is a refusal and the
password never leaves for Jellyfin, whatever the form says and even when Jellyfin has a user of that name. Any
other name is checked against Jellyfin when signing in through Jellyfin is on; when Jellyfin does not know it, or
does not answer, or the sign-in is off, the same refusal. The sign-in page has one button.

Run:
    .venv/bin/python tests/test_one_sign_in.py
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

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "one-sign-in.db")
os.environ["SECRET_KEY"] = "one-sign-in-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, db, jellyfin, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


PASSWORD = "TajneHesloQXZ!42"
db.init_db()
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", "ApiKlicQXZ0123456789")
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
accounts.create("admin", "longpassword", is_admin=True)

# Every request the app sends to Jellyfin; Jellyfin knows "mirek" and also an "admin" (with the local admin's password).
REQUESTS: list[httpx.Request] = []
TIMEOUT = {"on": False}


def server(request: httpx.Request) -> httpx.Response:
    REQUESTS.append(request)
    if TIMEOUT["on"]:
        raise httpx.ReadTimeout("Jellyfin does not answer", request=request)
    if request.url.path == "/Users/AuthenticateByName":
        body = json.loads(request.content or b"{}")
        users = {("mirek", PASSWORD): ("u-mirek", "Mirek"), ("admin", "longpassword"): ("u-admin-jf", "admin")}
        found = users.get((body.get("Username"), body.get("Pw")))
        if found:
            return httpx.Response(200, json={"AccessToken": "TokenQXZ", "User": {"Id": found[0], "Name": found[1], "Policy": {}}})
        return httpx.Response(401, json={})
    if request.url.path == "/Sessions/Logout":
        return httpx.Response(204)
    return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})


async def substituted_client(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server), base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = substituted_client


def sign_in(username: str, password: str, **extra: str) -> tuple[httpx.Response, TestClient, int]:
    """Signs in on a fresh browser; returns the answer, the browser and how many requests reached Jellyfin."""
    before = len(REQUESTS)
    # The brake would block the test's own refusals after eight: every sign-in starts from a clean slate.
    accounts._attempts.clear()
    with db.connect() as conn:
        conn.execute("DELETE FROM login_blocks")
        conn.commit()
    browser = TestClient(web.app)
    response = browser.post("/login", data={"username": username, "password": password, **extra}, follow_redirects=False)
    return response, browser, len(REQUESTS) - before


def signed_in(browser: TestClient) -> bool:
    return browser.get("/account", follow_redirects=False).status_code == 200


print("--- a local account: checked here only ---")
response, browser, asked = sign_in("admin", "longpassword")
check(response.status_code == 303 and signed_in(browser) and asked == 0, "a local account with its password: in, Jellyfin not asked",
      f"({response.status_code} asked {asked})")
response, browser, asked = sign_in("admin", "wrong-password")
check(response.status_code == 401 and not signed_in(browser) and asked == 0,
      "a wrong password of a local account: refused, the password never went to Jellyfin", f"({response.status_code} asked {asked})")
response, browser, asked = sign_in("admin", "wrong-password", method="jellyfin")
check(response.status_code == 401 and asked == 0, "...whatever the old form's method says", f"({response.status_code} asked {asked})")
response, browser, asked = sign_in("ADMIN", "longpassword")
check(response.status_code == 303 and asked == 0, "the name in other letters is the same local account")
check("Wrong username or password." in sign_in("admin", "wrong-password")[0].text, "the refusal's sentence")

print("--- any other name: Jellyfin ---")
response, browser, asked = sign_in("mirek", PASSWORD)
check(response.status_code == 303 and signed_in(browser) and asked >= 1, "a name Jellyfin knows: in through Jellyfin",
      f"({response.status_code} asked {asked})")
mirek = accounts.get_by_name("Mirek")
check(bool(mirek) and mirek.get("jellyfin_user_id") == "u-mirek" and not mirek.get("password_hash"),
      "...the account from Jellyfin made, without a password")
check(accounts.has_local_password("admin") and not accounts.has_local_password("Mirek") and not accounts.has_local_password("nobody"),
      "has_local_password: only an account with a password here")
response, browser, asked = sign_in("mirek", PASSWORD, method="local")
check(response.status_code == 303 and asked >= 1, "an account from Jellyfin signs in through Jellyfin again (the form's method ignored)")
response, browser, asked = sign_in("mirek", "wrong")
check(response.status_code == 401 and not signed_in(browser) and "Wrong username or password." in response.text,
      "a wrong Jellyfin password: the same refusal")
response, browser, asked = sign_in("nobody", "whatever-password")
check(response.status_code == 401 and asked >= 1, "a name nobody knows: refused after asking Jellyfin")
TIMEOUT["on"] = True
response, browser, asked = sign_in("nobody", "whatever-password")
TIMEOUT["on"] = False
check(response.status_code == 401, "Jellyfin does not answer: refused, never a 500", f"({response.status_code})")
db.set_setting(access.LOGIN_KEY, "0")
db.forget_settings()
response, browser, asked = sign_in("mirek", PASSWORD)
check(response.status_code == 401 and asked == 0, "signing in through Jellyfin off: refused, Jellyfin not asked",
      f"({response.status_code} asked {asked})")
check(sign_in("admin", "longpassword")[0].status_code == 303, "...a local account still gets in")
# The time of a refusal must not tell which names have a local account (accounts.authenticate hashes for an unknown
# name for that reason): when Jellyfin is not asked, the hash is computed for nothing (code review 27. 11.).
HASHED: list[str] = []
real_hash = accounts.hash_password
accounts.hash_password = lambda password: HASHED.append(password) or real_hash(password)
sign_in("nobody", "whatever-password")
off_hashed = len(HASHED)
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
sign_in("nobody", "")
empty_hashed = len(HASHED) - off_hashed
accounts.hash_password = real_hash
db.set_setting(access.LOGIN_KEY, "0")
db.forget_settings()
check(off_hashed >= 1 and empty_hashed >= 1,
      "a name without a local account, Jellyfin not asked (off, an empty password): the hash all the same",
      f"(off {off_hashed}, empty {empty_hashed})")

print("--- the page ---")
for login_on in ("1", "0"):
    db.set_setting(access.LOGIN_KEY, login_on)
    db.forget_settings()
    page = TestClient(web.app).get("/login").text
    form = page[page.index('action="/login"'):page.index("</form>")]
    submits = re.findall(r"<button[^>]*type=\"submit\"[^>]*>", form)
    plain = [b for b in submits if "formaction" not in b]
    check(len(plain) == 1 and 'name="method"' not in form and "Sign in with Jellyfin" not in page,
          f"one Sign in button (Jellyfin sign-in {'on' if login_on == '1' else 'off'})", f"({submits})")
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
check("Sign in with a code (Quick Connect)" in TestClient(web.app).get("/login").text, "the code (Quick Connect) stays")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
