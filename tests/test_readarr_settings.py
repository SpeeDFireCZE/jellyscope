# -*- coding: utf-8 -*-
r"""Settings → *Arr: connecting Readarr.

Readarr has its own card in Settings: on, address and API key. Guarded:

A) saving: an address without http:// gets it, an empty key keeps the stored one (as with Jellyfin -
   otherwise every save of the form would delete the key), it can be switched on only with an
   address and a key;
B) a "connected" Readarr is only one that is on, with an address and a key;
C) the connection test: the request goes to the address with its path behind a reverse proxy
   (http://nas/readarr/api/v1/...), the key in the X-Api-Key header, it checks that Readarr (or one of
   its forks, Chaptarr and Bookshelf, which answer to the same API) really is at the address (an
   address leading to another app with the same API does not pass), the name the app gave is stored
   (`arr_readarr_app_name`, not a secret), and no error message contains the key;
E) the key is a secret setting (db.SECRET_SETTINGS) - it never gets into templates.

Readarr does not run in the test: the answers are recorded from a real Readarr (tests/readarr_responses/)
and httpx.MockTransport returns them. The tab, the card in Settings and the order of
connected apps on the page come with the registry (tests/test_arr_registry.py).

Run:
    .venv/bin/python tests/test_readarr_settings.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "readarr-settings.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from _apps import no_network  # noqa: E402
from jellyscope import db  # noqa: E402
from jellyscope.arr import readarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "readarr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
no_network()      # what has no transport of its own here must not reach the network


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> object:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


def transport(seen: list[httpx.Request] | None = None, state: object = None) -> httpx.MockTransport:
    """An app that answers system/status with Readarr's recorded answer - or with `state` (another app)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/api/v1/system/status"):
            return httpx.Response(200, json=state if state is not None else recorded("system_status"))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def answering(code: int, body: str = "", content_type: str = "text/plain") -> httpx.MockTransport:
    return httpx.MockTransport(lambda _r: httpx.Response(code, text=body, headers={"Content-Type": content_type}))


def connection_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


print("--- A, B) saving and a connected Readarr ---")
check(not readarr.is_connected(), "without settings Readarr is not connected")
check(readarr.stored_settings() == {"enabled": False, "url": "", "has_key": False},
      "an app not set up: off, without an address, without a key")
check(readarr.save_settings(False, "", "") == "" and not readarr.is_connected(),
      "a switched-off app can be saved without an address; it is not connected")
check(readarr.save_settings(True, "", "tajny-klic") != "", "switching on without an address fails (returns an error)")
check(readarr.save_settings(True, "nas:8787", "") != "", "switching on without a key fails (returns an error)")
check(readarr.save_settings(True, "ftp://nas:8787", "k") != "", "an address other than http(s) is refused")
check(not readarr.is_connected() and readarr.stored_settings()["url"] == "", "a refused save wrote nothing")
check(readarr.save_settings(True, "nas:8787/readarr/", "tajny-klic") == "", "saving with an address and a key goes through")
check(readarr.stored_settings() == {"enabled": True, "url": "http://nas:8787/readarr/", "has_key": True},
      "the address without http:// got http://, the key is stored (but not returned)")
check(readarr.is_connected(), "Readarr is connected")
check(readarr.save_settings(True, "http://nas:8787/readarr", "") == ""
      and db.get_setting("arr_readarr_api_key") == "tajny-klic", "an empty key on saving keeps the stored one")
check(readarr.save_settings(False, "http://nas:8787/readarr", "") == "" and not readarr.is_connected()
      and db.get_setting("arr_readarr_api_key") == "tajny-klic" and db.get_setting("arr_readarr_url") == "http://nas:8787/readarr",
      "switching off keeps the address and the key, only not connected")
check(readarr.save_settings(True, "http://nas:8787/readarr", "") == "" and readarr.is_connected(), "and it can be switched back on without typing the key")

print()
print("--- C) connection test ---")
seen: list[httpx.Request] = []
ok, version = asyncio.run(readarr.check_connection("http://nas:8787/readarr/", "tajny-klic", transport=transport(seen)))
check(ok and version == recorded("system_status")["version"], f"Readarr answers and returns its version ({version})")
check(bool(seen) and str(seen[0].url) == "http://nas:8787/readarr/api/v1/system/status",
      f"the request goes with the path behind a proxy ({seen[0].url if seen else '-'})")
check(bool(seen) and seen[0].headers.get("X-Api-Key") == "tajny-klic", "the key goes in the X-Api-Key header")
check(bool(seen) and "tajny-klic" not in str(seen[0].url), "the key is not in the address (addresses are logged)")
# The app's name is stored for the tab's subtitle - and the forks pass: a Readarr family has one connection.
check(db.get_setting("arr_readarr_app_name", "") == "Readarr", "the app's name from the connection test is stored (arr_readarr_app_name)")
check("arr_readarr_app_name" not in db.SECRET_SETTINGS and "arr_readarr_app_name" in db.get_public_settings(),
      "the app's name is no secret")
for fork in ("Chaptarr", "Bookshelf", "READARR"):
    ok, version = asyncio.run(readarr.check_connection("http://nas:8787/readarr", "k", transport=transport(state={"appName": fork, "version": "0.4.1"})))
    check(ok and version == "0.4.1", f"{fork} answers at the address: the connection test passes ({version})")
    stored = db.get_setting("arr_readarr_app_name", "")
    check(stored == (fork if fork != "READARR" else "Readarr"), f"the name {fork} is stored as the app's name ({stored})")
ok, version = asyncio.run(readarr.check_connection("http://nas:8787/readarr/", "k", transport=transport()))
check(ok and db.get_setting("arr_readarr_app_name", "") == "Readarr", "and back: Readarr again (the address with a trailing slash is the saved one)")
# An address that was typed but is not the saved one: the test passes, but the stored name stays (it is the saved app's).
ok, version = asyncio.run(readarr.check_connection("http://other-box:8787", "k", transport=transport(state={"appName": "Chaptarr", "version": "0.4.1"})))
check(ok and db.get_setting("arr_readarr_app_name", "") == "Readarr", "testing an unsaved address stores no app name")
# Sonarr has the same API: a Readarr address leading to it by mistake would otherwise "work".
OTHER_APP = {"appName": "Sonarr", "version": "6.0.0.1"}
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", "k", transport=transport(state=OTHER_APP)))
check(not ok and "Readarr" in error and "Sonarr" in error, f"a Readarr address leading to another app does not pass ({error})")
# Lidarr has the very same API too (and the same kind of key) - an address leading to it is not Readarr.
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", "k", transport=transport(state={"appName": "Lidarr", "version": "3.1"})))
check(not ok and "Readarr" in error and "Lidarr" in error, f"Lidarr at a Readarr address does not pass ({error})")
check(db.get_setting("arr_readarr_app_name", "") == "Readarr", "a refused test did not overwrite the stored app name")
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", "k", transport=transport(state={"version": "1"})))
check(not ok and "Readarr" in error, f"an app that does not say who it is does not pass either ({error})")
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", "spatny-klic", transport=answering(401)))
check(not ok and "401" in error and "spatny-klic" not in error, f"a refused key: a message without the key ({error})")
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", "spatny-klic",
                                                transport=httpx.MockTransport(connection_refused)))
check(not ok and error and "spatny-klic" not in error, f"the app does not run: a message without the key ({error})")
ok, error = asyncio.run(readarr.check_connection("http://nas", "spatny-klic",
                                                transport=answering(200, "<html>Přihlášení k proxy</html>", "text/html")))
check(not ok and error and "spatny-klic" not in error, f"a proxy's login (HTML) answers instead of the app ({error})")
ok, error = asyncio.run(readarr.check_connection("http://nas", "spatny-klic", transport=answering(404)))
check(not ok and "404" in error, f"the app is not at the address (404) ({error})")

# A review finding: http:// in front of a proxy with TLS answers 301 to https. The redirect is not
# followed (the key would go elsewhere), but the message is to say where the address leads.
redirect = httpx.MockTransport(lambda r: httpx.Response(
    301, headers={"Location": "https://nas/readarr/api/v1/system/status"}))
ok, error = asyncio.run(readarr.check_connection("nas/readarr", "spatny-klic", transport=redirect))
check(not ok and "https://nas/readarr" in error and "api/v1" not in error and "spatny-klic" not in error,
      f"a redirect: the message says which address to enter ({error})")

# Minors from a review: an address httpx refuses (a soft hyphen) and a JSON list from a proxy instead
# of an object - the connection test and the page used to crash (500).
check(readarr.save_settings(True, "http://exa\xadmple.com", "k") != "" and db.get_setting("arr_readarr_url") == "http://nas:8787/readarr",
      "an address httpx refuses is not saved")
ok, error = asyncio.run(readarr.check_connection("http://exa\xadmple.com", "spatny-klic"))
check(not ok and error and "spatny-klic" not in error, f"a connection test with such an address returns an error, it does not crash ({error})")
ok, error = asyncio.run(readarr.check_connection("http://nas", "k", transport=answering(200, "[1, 2]", "application/json")))
check(not ok and error, f"a list instead of the app's status = an error, not a crash ({error})")
db.set_setting("arr_readarr_url", "http://exa\xadmple.com")   # stored before it was guarded
db.forget_settings()
try:
    asyncio.run(readarr.synchronize())
    refused = ""
except readarr.ReadarrError as error:
    refused = str(error)
check(bool(refused) and readarr.sync_state()["result"] == "error",
      f"a sync with such a stored address ends in an error of the app, not a crash ({refused})")
db.set_setting("arr_readarr_url", "http://nas:8787/readarr")
db.forget_settings()

# A security finding (13. 11. 2026): the address may carry credentials for a proxy
# (http://user:password@server). A relative redirect returned them in the hint with the password, and
# the app may repeat them in the error body - the error text goes into the page, the state and the log.
WITH_CREDENTIALS = "http://urluser:secretpass@nas:8787/readarr"


def exception_with_password(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("cannot connect to urluser:secretpass@nas", request=request)


for label, handler in (
        ("a relative redirect", lambda r: httpx.Response(301, headers={"Location": "/readarr2/api/v1/system/status"})),
        ("an absolute redirect with credentials", lambda r: httpx.Response(
            301, headers={"Location": "https://urluser:secretpass@nas/readarr/api/v1/system/status"})),
        ("the password in the error body", lambda r: httpx.Response(500, text="auth failed for urluser:secretpass")),
        ("the password in the exception's text", exception_with_password)):
    ok, error = asyncio.run(readarr.check_connection(WITH_CREDENTIALS, "k", transport=httpx.MockTransport(handler)))
    check(not ok and error and "secretpass" not in error, f"{label}: the password from the address is not in the error ({error})")
ok, error = asyncio.run(readarr.check_connection(WITH_CREDENTIALS, "k", transport=httpx.MockTransport(
    lambda r: httpx.Response(301, headers={"Location": "/readarr2/api/v1/system/status"}))))
check("http://nas:8787/readarr2" in error and "urluser" not in error,
      f"the hint after a redirect shows the address without credentials ({error})")

# A security pass (13. 11. 2026): an address with only a name (http://TOKEN@server - a token in place
# of the name, no password). Only the password was masked, so the token could end up in the error text.
# The key here is "k" and it is masked too - so the token has no letter k, else the check would pass by mistake.
ONLY_NAME = "http://zeton-9f3a7c2e@nas:8787/readarr"


def exception_with_token(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("cannot connect to zeton-9f3a7c2e@nas", request=request)


for label, handler in (
        ("the token in the error body", lambda r: httpx.Response(500, text="auth failed for zeton-9f3a7c2e")),
        ("the token in the exception's text", exception_with_token)):
    ok, error = asyncio.run(readarr.check_connection(ONLY_NAME, "k", transport=httpx.MockTransport(handler)))
    check(not ok and error and "zeton-9f3a7c2e" not in error, f"{label}: a name without a password from the address is not in the error ({error})")
# A name that has a password is no secret: it is not masked, so a short name ("admin") does not eat words of messages.
ok, error = asyncio.run(readarr.check_connection(WITH_CREDENTIALS, "k", transport=httpx.MockTransport(
    lambda r: httpx.Response(500, text="urluser has no access"))))
check(not ok and "urluser has no access" in error, f"a name with a password is not masked in the error text ({error})")
# Code review (14. 11. 2026): a short name without a password (http://admin@nas - a forgotten password,
# not a token) was masked too and ate ordinary words and the server's name from messages. A token
# tends to be long: a name of eight characters and more is masked.
for address_with_name in ("http://admin@nas:8787/readarr", "http://nas@nas:8787/readarr", "http://admin:@nas:8787/readarr"):
    ok, error = asyncio.run(readarr.check_connection(address_with_name, "9f3a7c2e-tajny", transport=httpx.MockTransport(
        lambda r: httpx.Response(500, text="admin nema pristup na nas"))))
    check(not ok and "admin nema pristup na nas" in error, f"a short name without a password is not masked: {address_with_name} ({error})")

# Code review (13. 11. 2026): a key with a character outside ASCII (diacritics, an invisible space
# copied with the key) brought the client down with UnicodeEncodeError - the connection test and the
# page = 500.
check(readarr.save_settings(True, "http://nas:8787/readarr", "kli\u200bc") != "",
      "a key with an invisible character is not saved (returns an error)")
check(db.get_setting("arr_readarr_api_key") == "tajny-klic", "... and the stored key stayed")
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", "klíč"))
check(not ok and bool(error), f"a connection test with a key outside ASCII returns an error, it does not crash ({error})")
db.set_setting("arr_readarr_api_key", "klíč")          # stored before it was guarded
db.forget_settings()
try:
    asyncio.run(readarr.synchronize())
    refused = ""
except readarr.ReadarrError as error:
    refused = str(error)
check(bool(refused) and "klíč" not in refused, f"a sync with such a stored key ends in an error without the key, not a crash ({refused})")
db.set_setting("arr_readarr_api_key", "tajny-klic")
db.forget_settings()
# Code review: the error body was first cut to 200 characters and only then cleaned - a key split by
# the boundary was not replaced and its beginning went into the page and the log.
LONG = "dlouhy-tajny-klic-0123456789abcdef"
ok, error = asyncio.run(readarr.check_connection("http://nas:8787", LONG, transport=answering(500, "x" * 190 + LONG)))
check(not ok and LONG[:8] not in error, f"a key on the boundary of the error body's cut does not get out, not even in part ({error[-40:]})")


async def key_in_exception() -> str:
    try:
        async with readarr.ReadarrClient("http://nas", "spatny-klic", transport=answering(500, "spatny-klic")) as client:
            await client.status()
    except readarr.ReadarrError as error:
        return str(error)
    return ""
text = asyncio.run(key_in_exception())
check(bool(text) and "spatny-klic" not in text, f"not even an error body that repeats the key gets it into the message ({text})")

print()
print("--- E) the key is a secret ---")
public = json.dumps(db.get_public_settings())
check("tajny-klic" not in public, "the key is not in the public settings (templates)")
check("arr_readarr_api_key" in db.SECRET_SETTINGS and db.get_setting("arr_readarr_api_key") == "tajny-klic",
      "Readarr's key is in db.SECRET_SETTINGS")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
