# -*- coding: utf-8 -*-
r"""Settings → *Arr: connecting Radarr.

Radarr has its own card in Settings: on, address and API key. Guarded:

A) saving: an address without http:// gets it, an empty key keeps the stored one (as with Jellyfin -
   otherwise every save of the form would delete the key), it can be switched on only with an
   address and a key;
B) a "connected" Radarr is only one that is on, with an address and a key;
C) the connection test: the request goes to the address with its path behind a reverse proxy
   (http://nas/radarr/api/v3/...), the key in the X-Api-Key header, it checks that Radarr really is at
   the address (an address leading to another app with the same API does not pass), and no error
   message contains the key;
D) the web: Radarr's card, the key is never written into the page, a reader has no access, an unknown
   app answers 404, the connection test saves nothing;
E) the key is a secret setting (db.SECRET_SETTINGS) - it never gets into templates.

Radarr does not run in the test: the answers are recorded from a real Radarr (tests/radarr_responses/)
and httpx.MockTransport returns them. The order of connected apps on the page is guarded by
tests/test_arr_registry.py.

Run:
    .venv/bin/python tests/test_radarr_settings.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "radarr-settings.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _apps import no_network, radarr_client  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import accounts, db, web  # noqa: E402
from jellyscope.arr import radarr  # noqa: E402

RESPONSES = PROJECT / "tests" / "radarr_responses"
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
    """An app that answers system/status with Radarr's recorded answer - or with `state` (another app)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/api/v3/system/status"):
            return httpx.Response(200, json=state if state is not None else recorded("system_status"))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def answering(code: int, body: str = "", content_type: str = "text/plain") -> httpx.MockTransport:
    return httpx.MockTransport(lambda _r: httpx.Response(code, text=body, headers={"Content-Type": content_type}))


def connection_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


print("--- A, B) saving and a connected Radarr ---")
check(not radarr.is_connected(), "without settings Radarr is not connected")
check(radarr.stored_settings() == {"enabled": False, "url": "", "has_key": False},
      "an app not set up: off, without an address, without a key")
check(radarr.save_settings(False, "", "") == "" and not radarr.is_connected(),
      "a switched-off app can be saved without an address; it is not connected")
check(radarr.save_settings(True, "", "tajny-klic") != "", "switching on without an address fails (returns an error)")
check(radarr.save_settings(True, "nas:7878", "") != "", "switching on without a key fails (returns an error)")
check(radarr.save_settings(True, "ftp://nas:7878", "k") != "", "an address other than http(s) is refused")
check(not radarr.is_connected() and radarr.stored_settings()["url"] == "", "a refused save wrote nothing")
check(radarr.save_settings(True, "nas:7878/radarr/", "tajny-klic") == "", "saving with an address and a key goes through")
check(radarr.stored_settings() == {"enabled": True, "url": "http://nas:7878/radarr/", "has_key": True},
      "the address without http:// got http://, the key is stored (but not returned)")
check(radarr.is_connected(), "Radarr is connected")
check(radarr.save_settings(True, "http://nas:7878/radarr", "") == ""
      and db.get_setting("arr_radarr_api_key") == "tajny-klic", "an empty key on saving keeps the stored one")
check(radarr.save_settings(False, "http://nas:7878/radarr", "") == "" and not radarr.is_connected()
      and db.get_setting("arr_radarr_api_key") == "tajny-klic" and db.get_setting("arr_radarr_url") == "http://nas:7878/radarr",
      "switching off keeps the address and the key, only not connected")
check(radarr.save_settings(True, "http://nas:7878/radarr", "") == "" and radarr.is_connected(), "and it can be switched back on without typing the key")

print()
print("--- C) connection test ---")
seen: list[httpx.Request] = []
ok, version = asyncio.run(radarr.check_connection("http://nas:7878/radarr/", "tajny-klic", transport=transport(seen)))
check(ok and version == recorded("system_status")["version"], f"Radarr answers and returns its version ({version})")
check(bool(seen) and str(seen[0].url) == "http://nas:7878/radarr/api/v3/system/status",
      f"the request goes with the path behind a proxy ({seen[0].url if seen else '-'})")
check(bool(seen) and seen[0].headers.get("X-Api-Key") == "tajny-klic", "the key goes in the X-Api-Key header")
check(bool(seen) and "tajny-klic" not in str(seen[0].url), "the key is not in the address (addresses are logged)")
# Sonarr has the same API: a Radarr address leading to it by mistake would otherwise "work".
OTHER_APP = {"appName": "Sonarr", "version": "6.0.0.1"}
ok, error = asyncio.run(radarr.check_connection("http://nas:8989", "k", transport=transport(state=OTHER_APP)))
check(not ok and "Radarr" in error and "Sonarr" in error, f"a Radarr address leading to another app does not pass ({error})")
ok, error = asyncio.run(radarr.check_connection("http://nas:7878", "k", transport=transport(state={"version": "1"})))
check(not ok and "Radarr" in error, f"an app that does not say who it is does not pass either ({error})")
ok, error = asyncio.run(radarr.check_connection("http://nas:7878", "spatny-klic", transport=answering(401)))
check(not ok and "401" in error and "spatny-klic" not in error, f"a refused key: a message without the key ({error})")
ok, error = asyncio.run(radarr.check_connection("http://nas:7878", "spatny-klic",
                                                transport=httpx.MockTransport(connection_refused)))
check(not ok and error and "spatny-klic" not in error, f"the app does not run: a message without the key ({error})")
ok, error = asyncio.run(radarr.check_connection("http://nas", "spatny-klic",
                                                transport=answering(200, "<html>Přihlášení k proxy</html>", "text/html")))
check(not ok and error and "spatny-klic" not in error, f"a proxy's login (HTML) answers instead of the app ({error})")
ok, error = asyncio.run(radarr.check_connection("http://nas", "spatny-klic", transport=answering(404)))
check(not ok and "404" in error, f"the app is not at the address (404) ({error})")

# A review finding: http:// in front of a proxy with TLS answers 301 to https. The redirect is not
# followed (the key would go elsewhere), but the message is to say where the address leads.
redirect = httpx.MockTransport(lambda r: httpx.Response(
    301, headers={"Location": "https://nas/radarr/api/v3/system/status"}))
ok, error = asyncio.run(radarr.check_connection("nas/radarr", "spatny-klic", transport=redirect))
check(not ok and "https://nas/radarr" in error and "api/v3" not in error and "spatny-klic" not in error,
      f"a redirect: the message says which address to enter ({error})")

# Minors from a review: an address httpx refuses (a soft hyphen) and a JSON list from a proxy instead
# of an object - the connection test and the page used to crash (500).
check(radarr.save_settings(True, "http://exa\xadmple.com", "k") != "" and db.get_setting("arr_radarr_url") == "http://nas:7878/radarr",
      "an address httpx refuses is not saved")
ok, error = asyncio.run(radarr.check_connection("http://exa\xadmple.com", "spatny-klic"))
check(not ok and error and "spatny-klic" not in error, f"a connection test with such an address returns an error, it does not crash ({error})")
ok, error = asyncio.run(radarr.check_connection("http://nas", "k", transport=answering(200, "[1, 2]", "application/json")))
check(not ok and error, f"a list instead of the app's status = an error, not a crash ({error})")
db.set_setting("arr_radarr_url", "http://exa\xadmple.com")   # stored before it was guarded
db.forget_settings()
state = asyncio.run(radarr.live_state())
check(state["available"] is False and state["error"], f"the live state with such a stored address: unavailable, not a crash ({state['error']})")
db.set_setting("arr_radarr_url", "http://nas:7878/radarr")
db.forget_settings()
radarr.live.CACHE.clear()

# A security finding (13. 11. 2026): the address may carry credentials for a proxy
# (http://user:password@server). A relative redirect returned them in the hint with the password, and
# the app may repeat them in the error body - the error text goes into the page, the state and the log.
WITH_CREDENTIALS = "http://urluser:secretpass@nas:7878/radarr"


def exception_with_password(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("cannot connect to urluser:secretpass@nas", request=request)


for label, handler in (
        ("a relative redirect", lambda r: httpx.Response(301, headers={"Location": "/radarr2/api/v3/system/status"})),
        ("an absolute redirect with credentials", lambda r: httpx.Response(
            301, headers={"Location": "https://urluser:secretpass@nas/radarr/api/v3/system/status"})),
        ("the password in the error body", lambda r: httpx.Response(500, text="auth failed for urluser:secretpass")),
        ("the password in the exception's text", exception_with_password)):
    ok, error = asyncio.run(radarr.check_connection(WITH_CREDENTIALS, "k", transport=httpx.MockTransport(handler)))
    check(not ok and error and "secretpass" not in error, f"{label}: the password from the address is not in the error ({error})")
ok, error = asyncio.run(radarr.check_connection(WITH_CREDENTIALS, "k", transport=httpx.MockTransport(
    lambda r: httpx.Response(301, headers={"Location": "/radarr2/api/v3/system/status"}))))
check("http://nas:7878/radarr2" in error and "urluser" not in error,
      f"the hint after a redirect shows the address without credentials ({error})")

# A security pass (13. 11. 2026): an address with only a name (http://TOKEN@server - a token in place
# of the name, no password). Only the password was masked, so the token could end up in the error text.
# The key here is "k" and it is masked too - so the token has no letter k, else the check would pass by mistake.
ONLY_NAME = "http://zeton-9f3a7c2e@nas:7878/radarr"


def exception_with_token(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("cannot connect to zeton-9f3a7c2e@nas", request=request)


for label, handler in (
        ("the token in the error body", lambda r: httpx.Response(500, text="auth failed for zeton-9f3a7c2e")),
        ("the token in the exception's text", exception_with_token)):
    ok, error = asyncio.run(radarr.check_connection(ONLY_NAME, "k", transport=httpx.MockTransport(handler)))
    check(not ok and error and "zeton-9f3a7c2e" not in error, f"{label}: a name without a password from the address is not in the error ({error})")
# A name that has a password is no secret: it is not masked, so a short name ("admin") does not eat words of messages.
ok, error = asyncio.run(radarr.check_connection(WITH_CREDENTIALS, "k", transport=httpx.MockTransport(
    lambda r: httpx.Response(500, text="urluser has no access"))))
check(not ok and "urluser has no access" in error, f"a name with a password is not masked in the error text ({error})")
# Code review (14. 11. 2026): a short name without a password (http://admin@nas - a forgotten password,
# not a token) was masked too and ate ordinary words and the server's name from messages. A token
# tends to be long: a name of eight characters and more is masked.
for address_with_name in ("http://admin@nas:7878/radarr", "http://nas@nas:7878/radarr", "http://admin:@nas:7878/radarr"):
    ok, error = asyncio.run(radarr.check_connection(address_with_name, "9f3a7c2e-tajny", transport=httpx.MockTransport(
        lambda r: httpx.Response(500, text="admin nema pristup na nas"))))
    check(not ok and "admin nema pristup na nas" in error, f"a short name without a password is not masked: {address_with_name} ({error})")

# Code review (13. 11. 2026): a key with a character outside ASCII (diacritics, an invisible space
# copied with the key) brought the client down with UnicodeEncodeError - the connection test and the
# page = 500.
check(radarr.save_settings(True, "http://nas:7878/radarr", "kli\u200bc") != "",
      "a key with an invisible character is not saved (returns an error)")
check(db.get_setting("arr_radarr_api_key") == "tajny-klic", "... and the stored key stayed")
ok, error = asyncio.run(radarr.check_connection("http://nas:7878", "klíč"))
check(not ok and bool(error), f"a connection test with a key outside ASCII returns an error, it does not crash ({error})")
db.set_setting("arr_radarr_api_key", "klíč")          # stored before it was guarded
db.forget_settings()
live_state = asyncio.run(radarr.live_state())
check(live_state["available"] is False and bool(live_state["error"]), "the live state with such a stored key: unavailable, not a crash")
db.set_setting("arr_radarr_api_key", "tajny-klic")
db.forget_settings()
radarr.live.CACHE.clear()
# Code review: the error body was first cut to 200 characters and only then cleaned - a key split by
# the boundary was not replaced and its beginning went into the page and the log.
LONG = "dlouhy-tajny-klic-0123456789abcdef"
ok, error = asyncio.run(radarr.check_connection("http://nas:7878", LONG, transport=answering(500, "x" * 190 + LONG)))
check(not ok and LONG[:8] not in error, f"a key on the boundary of the error body's cut does not get out, not even in part ({error[-40:]})")


async def key_in_exception() -> str:
    try:
        async with radarr.RadarrClient("http://nas", "spatny-klic", transport=answering(500, "spatny-klic")) as client:
            await client.status()
    except radarr.RadarrError as error:
        return str(error)
    return ""
text = asyncio.run(key_in_exception())
check(bool(text) and "spatny-klic" not in text, f"not even an error body that repeats the key gets it into the message ({text})")

print()
print("--- D) web ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
# The form tries the connection to a Radarr that does not run: the connection is refused (without a way to the real network).
substitute(radarr, "RadarrClient", radarr_client(connection_refused))
page = admin.get("/settings?section=arr").text
check('action="/settings/arr/radarr"' in page and 'action="/settings/arr/radarr/sync"' in page,
      "Radarr's card with its form and the sync button")
check("tajny-klic" not in page, "the stored key is never written into the page")
check("http://nas:7878/radarr" in page, "the stored address is in the form")
check('href="/settings?section=arr"' in page, "the *Arr section is in the Settings menu")
tabs = page[page.find('<div class="tabs">'):]
section_order = [tabs.find(f'href="/settings?section={section}"') for section in ("jellyfin", "seerr", "arr", "data")]
check(all(p >= 0 for p in section_order) and section_order == sorted(section_order),
      f"in the Settings menu *Arr is right under Jellyfin and Seerr, before the others ({section_order})")
check('section=arr"' not in reader.get("/settings").text, "a reader does not have the *Arr section in the menu")
check("/settings/arr/radarr" not in reader.get("/settings?section=arr").text, "a reader does not get the *Arr forms")
form = {"enabled": "1", "url": "http://jinam:7878", "api_key": "", "action": "save"}
check(reader.post("/settings/arr/radarr", data=form, follow_redirects=False).status_code in (303, 403)
      and db.get_setting("arr_radarr_url") == "http://nas:7878/radarr", "a reader saves nothing")
check(admin.post("/settings/arr/nothing", data=form, follow_redirects=False).status_code == 404,
      "an unknown app answers 404")
test_form = {"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "jiny-klic", "action": "test"}
test_answer = admin.post("/settings/arr/radarr", data=test_form)
db.forget_settings()
check(db.get_setting("arr_radarr_url") == "http://nas:7878/radarr" and db.get_setting("arr_radarr_api_key") == "tajny-klic",
      "the connection test saves nothing")
check("jiny-klic" not in test_answer.text and "127.0.0.1:1" in test_answer.text,
      "after the test the form has the tried address, not the key")
check("flash error" in test_answer.text, "a failed test shows an error")
# Found live (13. 11. 2026): a key the form refused (a character outside ASCII) stayed in the unsaved
# form - and a connection test with an empty field then tried that one instead of the stored one.
refused = admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "klíč",
                                                   "action": "save"})
check("API klíč obsahuje znak" in refused.text, "the form refuses a key with diacritics")
after_refusal = admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "",
                                                         "action": "test"})
check("API klíč obsahuje znak" not in after_refusal.text and "flash error" in after_refusal.text,
      "a refused key is not remembered: the next connection test tries the stored key")
admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "http://nas:7878/radarr", "api_key": "", "action": "save"})
db.forget_settings()
check(db.get_setting("arr_radarr_api_key") == "tajny-klic" and db.get_setting("arr_radarr_url") == "http://nas:7878/radarr",
      "... and a save with an empty field goes through with the stored key")
r = admin.post("/settings/arr/radarr", data={"enabled": "1", "url": "nas:7878", "api_key": "", "action": "save"},
               follow_redirects=False)
db.forget_settings()
check(r.status_code == 303 and r.headers.get("location", "").startswith("/settings?section=arr"),
      "saving redirects back to the *Arr section")
check(radarr.stored_settings() == {"enabled": True, "url": "http://nas:7878", "has_key": True},
      "the admin saved Radarr (with the stored key)")
r = admin.post("/settings/arr/radarr", data={"url": "", "api_key": "", "action": "save"})
db.forget_settings()
check(not radarr.is_connected(), "an unticked \"on\" switches the app off")

print()
print("--- E) the key is a secret ---")
public = json.dumps(db.get_public_settings())
check("tajny-klic" not in public, "the key is not in the public settings (templates)")
check("arr_radarr_api_key" in db.SECRET_SETTINGS and db.get_setting("arr_radarr_api_key") == "tajny-klic",
      "Radarr's key is in db.SECRET_SETTINGS")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
