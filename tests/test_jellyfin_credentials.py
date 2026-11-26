# -*- coding: utf-8 -*-
r"""The sign-in details for Jellyfin must not leak anywhere.

Signing in with a Jellyfin password means that **somebody else's password** passes
through the app and that it holds an API key to the whole server. This test watches
the ways they could leak - because they can leak quietly and it shows
only when it is too late.

What is checked:

* the password from the sign-in window is stored nowhere - neither in the account, nor in
  the settings, nor in the log,
* the API key and the password go **in a header**, never in the address (addresses end up
  in the proxy's log and in the browser's history),
* the token that checking the password in Jellyfin makes is cancelled at once,
* the API key is never written out on a page or into an API answer,
* the app's log has neither the key nor the password,
* neither a viewer nor a reader gets to the key (Settings is for the administrator).

Run:
    .venv/bin/python tests/test_jellyfin_credentials.py
"""
from __future__ import annotations

import json
import re
import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "udaje.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, applog, db, jellyfin, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Unusual strings on purpose - they cannot appear in a file or on a page
# by chance.
KEY = "ApiKlicQXZ0123456789"
PASSWORD = "TajneHesloQXZ!42"

db.init_db()
# The app switches the log file on in its lifespan, which TestClient without `with` never runs - so
# the test switches it on itself, before the sign-ins whose traces the log checks below look for.
applog.setup()
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", KEY)
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
accounts.create("admin", "longpassword", is_admin=True)

REQUESTS: list[httpx.Request] = []
CANCELLED: list[str] = []


def server(request: httpx.Request) -> httpx.Response:
    REQUESTS.append(request)
    if request.url.path == "/Users/AuthenticateByName":
        body = json.loads(request.content or b"{}")
        if body.get("Username") == "mirek" and body.get("Pw") == PASSWORD:
            return httpx.Response(200, json={
                "AccessToken": "TokenQXZ",
                "User": {"Id": "u-mirek", "Name": "Mirek", "Policy": {}}})
        return httpx.Response(401, json={})
    if request.url.path == "/Sessions/Logout":
        CANCELLED.append(request.headers.get("Authorization", ""))
        return httpx.Response(204)
    return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})


# (address, key) of every client the app opens - the requests themselves all go to the mock server.
CLIENTS: list[tuple[str, str]] = []


async def substituted_client(self):
    CLIENTS.append((self.base_url, self.api_key))
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = substituted_client

client = TestClient(web.app)
response = client.post("/login", data={"username": "mirek", "password": PASSWORD,
                                       "method": "jellyfin"},
                       follow_redirects=False)

print("--- somebody else's password passes and stays nowhere ---")
check(response.status_code == 303, f"the viewer signed in ({response.status_code})")

# The two buttons of the sign-in page send the way of signing in as a field. The route's default is
# the local sign-in, so a field renamed on one side only would still sign in locally - only the
# Jellyfin button would break. Hence both sides are checked: the page's buttons and the route.
page = TestClient(web.app).get("/login").text
buttons = set(re.findall(r'<button[^>]*\bname="(\w+)"[^>]*\bvalue="(\w+)"', page))
check(buttons == {("method", "local"), ("method", "jellyfin")},
      f"the sign-in page's buttons send method=local / method=jellyfin ({sorted(buttons)})")
count = len(REQUESTS)
old_client = TestClient(web.app)
old = old_client.post("/login", data={"username": "mirek", "password": PASSWORD, "zpusob": "jellyfin"},  # old name on purpose
                      follow_redirects=False)
# A sign-in page older than the upgrade: turned back with "reload the page" (web/legacy.py), nobody signed in.
check(len(REQUESTS) == count and old_client.get("/", follow_redirects=False).headers.get("location", "").startswith("/login"),
      f"the old field name is not read: the Jellyfin user is not signed in through Jellyfin ({old.status_code})")

account = dict(db.query_one(
    "SELECT * FROM accounts WHERE jellyfin_user_id = 'u-mirek'") or {})
check(PASSWORD not in json.dumps(account, default=str),
      "the password is not in the account (not even as a hash of something)")
check(not account.get("password_hash"), "and the field for the password stayed empty")

settings_rows = db.query_all("SELECT key, value FROM settings")
check(not [r for r in settings_rows if PASSWORD in str(dict(r)["value"])],
      "the password is not in the settings")

db_file = Path(str(db.database_config().path))
if not db_file.is_absolute():
    db_file = PROJECT / db_file
content = db_file.read_bytes() if db_file.exists() else b""
check(PASSWORD.encode() not in content, "the password is not even in the database file")

print()
print("--- what went to Jellyfin ---")
sign_in_requests = [z for z in REQUESTS
                    if z.url.path == "/Users/AuthenticateByName"]
check(len(sign_in_requests) == 1, f"one query for checking ({len(sign_in_requests)})")
request = sign_in_requests[0]
check(PASSWORD not in str(request.url),
      "the password is not in the address - addresses are logged in the proxy and in the browser")
check(KEY not in str(request.url), "and the API key not either")
check("Authorization" in request.headers, "the details go in a header")
check(request.url.host == "jellyfin.test",
      f"and nowhere else than to the server from the settings ({request.url.host})")

check(CANCELLED and "TokenQXZ" in CANCELLED[0],
      f"the token from the check was cancelled at once ({CANCELLED})")
check(db.get_setting("jellyfin_api_key", "") == KEY,
      "our API key stayed unchanged")

print()
print("--- the key never shows on a page ---")
admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword",
                           "method": "local"})
for path in ("/settings?section=jellyfin", "/settings?section=data",
             "/health", "/"):
    text = admin.get(path).text
    check(KEY not in text, f"{path} does not write the key out")

print()
print("--- the log has neither the key nor the password ---")
log = logging.getLogger("jellyscope.zkouska")
log.info("nastaveni jellyfin_api_key zmeneno")
lines = ""
for path in applog.log_dir().glob("*.log"):
    lines += path.read_text(encoding="utf-8", errors="replace")
# Without a log file the two checks below compare an empty text and pass whatever was logged.
check("nastaveni jellyfin_api_key zmeneno" in lines, "the app's log file really has the lines (the checks below are not empty)")
check(KEY not in lines, "the key is not in the log")
check(PASSWORD not in lines, "the password is not in the log")

print()
print("--- the key does not get into the text of an error ---")
# A debugging proxy or a gateway may repeat the request's headers in the body of its error page. The error's
# text goes into the side panel, the log and the notifications (Discord, Telegram, e-mail).
import asyncio  # noqa: E402


def echoing(request: httpx.Request) -> httpx.Response:
    if request.url.path in ("/Users", "/QuickConnect/Initiate"):
        raise httpx.ConnectError(f"refused, sent {request.headers.get('Authorization')}")
    # /Sessions whole; the folders with the key across the cut of the body at 200 characters (its start must not leak).
    padding = "x" * (140 if request.url.path == "/Library/VirtualFolders" else 0)
    return httpx.Response(502, text=padding + f"Bad gateway, request headers: {request.headers.get('Authorization')}")


async def error_of(call: str) -> str:
    client = jellyfin.JellyfinClient("http://jellyfin.test", KEY, version="12.0")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(echoing), base_url="http://jellyfin.test",
                                       headers=jellyfin.headers(KEY, "12.0"))
    try:
        await getattr(client, call)()
    except jellyfin.JellyfinError as error:
        return str(error) + str(error.values)
    finally:
        await client.close()
    return ""


for call, label in (("sessions", "an error page that repeats the headers"), ("virtual_folders", "the key across the cut"),
                    ("users", "a connection error's text"), ("quick_connect_start", "the same when signing in")):
    text = asyncio.run(error_of(call))
    check(text and KEY not in text and KEY[:8] not in text, f"{label}: the key is not in the error ({text[-70:]!r})")

# The Playback Reporting importer posts its query through the client's own httpx client, past `_get`: the body
# of the error page went into the task's message, the Import page and the log file whole.
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402

from jellyscope import importers, scanner  # noqa: E402


class EchoingClient(jellyfin.JellyfinClient):
    """A gateway's error page that repeats the request's headers - the key across the cut at 300 characters."""

    async def __aenter__(self) -> "EchoingClient":
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(
                502, text="x" * 257 + f" request headers: {request.headers.get('Authorization')}")),
            base_url="http://jellyfin.test", headers=jellyfin.headers(self.api_key, "12.0"))
        return self


restore_client = substitute(importers, "JellyfinClient", EchoingClient)
try:
    imported = asyncio.run(importers.import_playback_reporting())
    found, detected = asyncio.run(importers.playback_reporting_available())
finally:
    restore_client()
for handler in logging.getLogger().handlers:
    handler.flush()
task_message = str((scanner.last_scan("import") or {}).get("message_values") or "")
logged = "".join(p.read_text(encoding="utf-8", errors="replace") for p in applog.log_dir().glob("*.log"))
for where, text in (("the import's answer", str(imported.get("message"))), ("the task log", task_message),
                    ("the log file", logged), ("the plugin check", detected)):
    check("request headers" in text and KEY[:4] not in text,
          f"Playback Reporting, {where}: the error is told without the key, its start neither ({text[-60:]!r})")

print()
print("--- an unencrypted connection is seen in Settings ---")
# The password for Jellyfin cannot be encrypted: encryption is done by https, not by the app
# above it. What can be done is to say it out loud - and only where it holds.
from jellyscope import jellyfin as _jf  # noqa: E402

check(_jf.unencrypted_over_network("http://192.168.1.10:8096"),
      "http to another machine = warn")
check(not _jf.unencrypted_over_network("https://jellyfin.doma.cz"),
      "https is all right")
check(not _jf.unencrypted_over_network("http://localhost:8096"),
      "localhost sends nothing over the network")
check(not _jf.unencrypted_over_network(""), "an empty address does not warn")

db.set_setting("jellyfin_url", "http://192.168.1.10:8096")
db.forget_settings()
page = admin.get("/settings?section=jellyfin").text
check("není šifrované" in page or "not encrypted" in page,
      "and the administrator sees it in the card")
db.set_setting("jellyfin_url", "https://jellyfin.doma.cz")
db.forget_settings()
check("není šifrované" not in admin.get("/settings?section=jellyfin").text,
      "on https there is no warning")
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.forget_settings()

print()
print("--- the saved key goes only to the server it was saved for ---")
# The saved key went to any address put into the form with the key field left empty: a test or a save sent from a
# page of another site on the same machine, or by another administrator, who never sees the key, had the app send
# it there (and keep it for that address).
ANOTHER_SERVER = "the saved key is sent only to the server it was saved for"


def connection(url: str, key: str = "", action: str = "test") -> str:
    admin.post("/settings/connection", follow_redirects=False, data={
        "jellyfin_url": url, "jellyfin_api_key": key, "jellyfin_generation": "auto", "action": action})
    db.forget_settings()
    return admin.get("/settings?section=jellyfin").text


def went_to(*addresses: str) -> list[tuple[str, str]]:
    return [(url, key) for url, key in CLIENTS if url.startswith(addresses)]


CLIENTS.clear()
OTHERS = ("http://utocnik.example:8096", "https://jellyfin.test", "http://jellyfin.test:8097")
answers = [connection(url) for url in OTHERS]
check(not went_to(*OTHERS) and all(ANOTHER_SERVER in answer for answer in answers),
      f"a test at another host, scheme or port with the key field empty sends no key and asks for it ({went_to(*OTHERS)})")
answer = connection("http://utocnik.example:8096", action="save")
check(db.get_setting("jellyfin_url") == "http://jellyfin.test" and db.get_setting("jellyfin_api_key") == KEY
      and ANOTHER_SERVER in answer and not went_to(*OTHERS),
      f"nor is it saved for another server ({db.get_setting('jellyfin_url')})")
connection("jellyfin.test/jinde")
check(went_to("http://jellyfin.test/jinde") == [("http://jellyfin.test/jinde", KEY)],
      f"the same server (another path) with the key field empty tests with the saved key ({went_to('http://jellyfin.test/')})")
CLIENTS.clear()
connection("http://novy.doma:8096", "KlicNovyQXZ")
connection("http://utocnik.example:8096")
connection("http://novy.doma:8096", "KlicNovyQXZ")
connection("http://novy.doma:8096/x")
check(not went_to("http://utocnik") and went_to("http://novy.doma") == [("http://novy.doma:8096", "KlicNovyQXZ")] * 2
      + [("http://novy.doma:8096/x", "KlicNovyQXZ")],
      f"a key typed at a test goes again only to the server it was typed for ({went_to('http://novy', 'http://utocnik')})")
db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", KEY)
db.forget_settings()

print()
print("--- only the administrator gets to the key ---")
viewer = TestClient(web.app)
viewer.post("/login", data={"username": "mirek", "password": PASSWORD,
                            "method": "jellyfin"})
for path in ("/settings?section=jellyfin", "/settings"):
    response = viewer.get(path, follow_redirects=False)
    check(response.status_code == 403,
          f"a viewer may not open {path} ({response.status_code})")
    check(KEY not in response.text, "and the key is not in the answer")

accounts.create("reader", "longpassword", is_admin=False)
reader = TestClient(web.app)
reader.post("/login", data={"username": "reader", "password": "longpassword",
                            "method": "local"})
response = reader.get("/settings?section=jellyfin", follow_redirects=False)
check(response.status_code == 403,
      f"a reader may not open Settings ({response.status_code})")
check(KEY not in response.text, "and the key is not in the answer")

print()
print("--- with signing in switched off Jellyfin is not even asked ---")
db.set_setting(access.LOGIN_KEY, "0")
db.forget_settings()
count = len(REQUESTS)
TestClient(web.app).post("/login", data={"username": "mirek",
                                         "password": PASSWORD,
                                         "method": "jellyfin"})
check(len(REQUESTS) == count,
      "no query went anywhere - neither the password nor the key")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
