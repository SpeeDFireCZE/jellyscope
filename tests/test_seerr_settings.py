# -*- coding: utf-8 -*-
r"""Settings → Seerr: connecting Seerr (requests for movies and series).

Seerr is not part of *Arr: it has its own section in Settings right after Jellyfin. Guarded:

A) saving: an address without http:// gets it, an empty key keeps the stored one, it can be switched
   on only with an address and a key, an address httpx refuses cannot be saved;
B) the connection test: the request goes to the address with its path behind a proxy
   (…/seerr/api/v1/…), the key only in the X-Api-Key header, Seerr must really be at the address
   (`settings/about` with `totalRequests`), no error message contains the key, a redirect tells
   which address to enter;
C) the web: the Seerr section is in Settings right after Jellyfin, the key is never shown, a reader
   saves nothing, the connection test saves nothing;
D) the key is a secret setting (db.SECRET_SETTINGS);
E) the line "Plakáty žádostí": whether Seerr serves images ("Enable Image Caching"), with it off an
   explanation and a link to Seerr's settings.

Seerr does not run: the answers are recorded from a real Seerr (tests/seerr_responses/) and
httpx.MockTransport returns them.

Run:
    .venv/bin/python tests/test_seerr_settings.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "seerr-settings.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from _package import substitute  # noqa: E402
from jellyscope import accounts, db, seerr, web  # noqa: E402

RESPONSES = PROJECT / "tests" / "seerr_responses"
failures = 0
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def recorded(name: str) -> object:
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


def seerr_transport(seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/api/v1/settings/about"):
            return httpx.Response(200, json=recorded("settings_about"))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def answering(code: int, body: str = "", content_type: str = "text/plain", headers: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _r: httpx.Response(code, text=body, headers={"Content-Type": content_type, **(headers or {})}))


print("--- A) saving ---")
check(seerr.is_connected() is False, "without settings Seerr is not connected")
check(seerr.stored_settings() == {"enabled": False, "url": "", "has_key": False}, "not set: off, without an address and a key")
check(seerr.save_settings(True, "", "tajny-klic") != "", "switching on without an address fails")
check(seerr.save_settings(True, "nas:5055", "") != "", "switching on without a key fails")
check(seerr.save_settings(True, "http://exa\xadmple.com", "k") != "", "an address httpx refuses is not saved")
check(seerr.save_settings(True, "nas:5055/seerr/", "tajny-klic") == "", "saving with an address and a key goes through")
check(seerr.stored_settings() == {"enabled": True, "url": "http://nas:5055/seerr/", "has_key": True},
      "the address without http:// got http://, the key is stored")
check(seerr.is_connected() is True, "Seerr is connected")
check(seerr.save_settings(True, "http://nas:5055/seerr", "") == "" and db.get_setting("seerr_api_key") == "tajny-klic",
      "an empty key keeps the stored one")
check(seerr.save_settings(False, "http://nas:5055/seerr", "") == "" and seerr.is_connected() is False
      and db.get_setting("seerr_api_key") == "tajny-klic", "switching off keeps the address and the key, only not connected")
seerr.save_settings(True, "http://nas:5055/seerr", "")

print()
print("--- B) connection test ---")
seen: list[httpx.Request] = []
ok, version = asyncio.run(seerr.check_connection("http://nas:5055/seerr/", "tajny-klic", transport=seerr_transport(seen)))
check(ok and version == recorded("settings_about")["version"], f"Seerr answers and returns its version ({version})")
check(bool(seen) and str(seen[0].url) == "http://nas:5055/seerr/api/v1/settings/about",
      f"the request with the path behind a proxy ({seen[0].url if seen else '-'})")
check(bool(seen) and seen[0].headers.get("X-Api-Key") == "tajny-klic" and "tajny-klic" not in str(seen[0].url),
      "the key only in the X-Api-Key header")
other_app = answering(200, json.dumps({"appName": "Sonarr", "version": "4.0"}), "application/json")
ok, error = asyncio.run(seerr.check_connection("http://nas:8989", "k", transport=other_app))
check(not ok and "Seerr" in error, f"something other than Seerr answers at the address ({error})")
for label, transport in (("401", answering(401)), ("a proxy's HTML login", answering(200, "<html>x</html>", "text/html")),
                         ("a list instead of an object", answering(200, "[1, 2]", "application/json")),
                         ("spatny-klic in the error body", answering(500, "spatny-klic"))):
    ok, error = asyncio.run(seerr.check_connection("http://nas", "spatny-klic", transport=transport))
    check(not ok and error and "spatny-klic" not in error, f"{label}: an error without the key ({error})")
redirect = answering(301, headers={"Location": "https://nas/seerr/api/v1/settings/about"})
ok, error = asyncio.run(seerr.check_connection("nas/seerr", "k", transport=redirect))
check(not ok and "https://nas/seerr" in error and "api/v1" not in error, f"a redirect: which address to enter ({error})")
ok, error = asyncio.run(seerr.check_connection("http://urluser:secretpass@nas/seerr", "k", transport=answering(
    301, headers={"Location": "/seerr2/api/v1/settings/about"})))
check(not ok and "secretpass" not in error and "urluser" not in error and "http://nas/seerr2" in error,
      f"credentials from the address are not in the hint after a redirect ({error})")
ok, error = asyncio.run(seerr.check_connection("http://exa\xadmple.com", "spatny-klic"))
check(not ok and error and "spatny-klic" not in error, f"an invalid address: an error, not a crash ({error})")

print()
print("--- C) web ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")


def sign_in(name: str) -> TestClient:
    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return client


admin, reader = sign_in("admin"), sign_in("reader")
page = admin.get("/settings?section=seerr").text
check('action="/settings/seerr"' in page and "http://nas:5055/seerr" in page, "the Seerr section with its form and address")
check("tajny-klic" not in page, "the key is never written into the page")
menu = re.findall(r'href="/settings\?section=([a-z_]+)"', page)
check(menu[:2] == ["jellyfin", "seerr"], f"Seerr is in Settings right after Jellyfin ({menu[:3]})")
check('section=seerr"' not in reader.get("/settings").text, "a reader does not have the section")
form = {"enabled": "1", "url": "http://jinam:5055", "api_key": "", "action": "save"}
check(reader.post("/settings/seerr", data=form, follow_redirects=False).status_code in (303, 403)
      and db.get_setting("seerr_url") == "http://nas:5055/seerr", "a reader saves nothing")
test_form = {"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "jiny-klic", "action": "test"}
test_answer = admin.post("/settings/seerr", data=test_form)
db.forget_settings()
check(db.get_setting("seerr_url") == "http://nas:5055/seerr" and db.get_setting("seerr_api_key") == "tajny-klic",
      "the connection test saves nothing")
check("jiny-klic" not in test_answer.text and "127.0.0.1:1" in test_answer.text and "flash error" in test_answer.text,
      "after the test it shows the tried address (not the key) and the error")
# Found live (13. 11. 2026): a refused key stayed in the unsaved form.
refused = admin.post("/settings/seerr", data={"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "klíč",
                                              "action": "save"})
check("API klíč obsahuje znak" in refused.text, "the form refuses a key with diacritics")
after_refusal = admin.post("/settings/seerr", data={"enabled": "1", "url": "http://127.0.0.1:1", "api_key": "",
                                                    "action": "test"})
check("API klíč obsahuje znak" not in after_refusal.text and "flash error" in after_refusal.text,
      "a refused key is not remembered: the next connection test does not repeat its error")
r = admin.post("/settings/seerr", data={"enabled": "1", "url": "nas:5055", "api_key": "", "action": "save"},
               follow_redirects=False)
db.forget_settings()
check(r.status_code == 303 and r.headers.get("location", "").startswith("/settings?section=seerr")
      and seerr.stored_settings()["url"] == "http://nas:5055", "the admin saves (not the key of the unsaved test, the stored one stays)")
check(db.get_setting("seerr_api_key") in ("tajny-klic", "jiny-klic"), "a key is stored")

# The saved key went to any address put into the form with the key field left empty: a test or a save sent from a
# page of another site on the same machine, or by another administrator, who never sees the key, had the app send
# it there (and keep it for that address).
ANOTHER_SERVER = "uložený klíč se posílá jen na server"
seerr.save_settings(True, "http://nas:5055/seerr", "tajny-klic")
db.forget_settings()
sent: list[tuple[str, str]] = []


async def record_key(url: str, key: str) -> tuple[bool, str]:
    sent.append((url, key))
    return True, "9.9"


def post(url: str, key: str = "", action: str = "test") -> str:
    return admin.post("/settings/seerr", data={"enabled": "1", "url": url, "api_key": key, "action": action}).text


async def no_images() -> None:
    """The test asks no Seerr whether it hands out images."""


put_backs = [substitute(seerr, "check_connection", record_key), substitute(seerr, "check_images", no_images)]
try:
    answers = [post(url) for url in ("http://utocnik.example:5055", "https://nas:5055/seerr", "http://nas:5056/seerr")]
    check(not sent and all(ANOTHER_SERVER in answer for answer in answers),
          f"a test at another host, scheme or port with the key field empty sends no key and asks for it ({sent})")
    answer = post("http://utocnik.example:5055", action="save")
    db.forget_settings()
    check(seerr.stored_settings()["url"] == "http://nas:5055/seerr" and ANOTHER_SERVER in answer,
          f"nor is it saved for another server ({seerr.stored_settings()['url']})")
    post("nas:5055/jinde")
    check(sent == [("nas:5055/jinde", "tajny-klic")],
          f"the same server (another path) with the key field empty tests with the saved key ({sent})")
    sent.clear()
    post("http://novy.doma:5055", "klic-novy")
    post("http://utocnik.example:5055")
    post("http://novy.doma:5055", "klic-novy")
    post("http://novy.doma:5055/x")
    check(sent == [("http://novy.doma:5055", "klic-novy")] * 2 + [("http://novy.doma:5055/x", "klic-novy")],
          f"a key typed at a test goes again only to the server it was typed for ({sent})")
finally:
    for put_back in put_backs:
        put_back()
seerr.save_settings(True, "http://nas:5055", "tajny-klic")
db.forget_settings()

print()
print("--- D) the key is a secret ---")
check("seerr_api_key" in db.SECRET_SETTINGS, "seerr_api_key is in db.SECRET_SETTINGS")
check("tajny-klic" not in json.dumps(db.get_public_settings()) and "jiny-klic" not in json.dumps(db.get_public_settings()),
      "the key is not in the public settings")

print()
print("--- E) Request posters (\"Enable Image Caching\") ---")
seerr.save_settings(True, "http://nas:5055", "tajny-klic")


def page_with_state(state: str) -> str:
    db.set_setting("seerr_images", state)
    db.forget_settings()
    return admin.get("/settings?section=seerr").text


def posters_line(html: str) -> str:
    """The part of the section from the heading "Plakáty žádostí" to the end of its paragraph (the rest of the page does not count)."""
    start = html.find("Plakáty žádostí")
    return html[start:html.find("</div>", start)] if start >= 0 else ""


s = posters_line(page_with_state("0"))
check(bool(s) and "vypnuto" in s and "Enable Image Caching" in s and "Settings → General" in s,
      "off: the line says what to switch on in Seerr and where")
link = re.search(r'<a[^>]+href="http://nas:5055/settings/main"[^>]*>', s)
check(link is not None and 'rel="noopener noreferrer"' in link.group(0) and 'target="_blank"' in link.group(0),
      "the link to Seerr's settings built from its address, in a new window and without a referrer")
check("vnitřní" in s, "and a sentence that an address from the inner network may not open in a browser")
s = posters_line(page_with_state("1"))
check("zapnuto" in s and "/settings/main" not in s and "vypnuto" not in s, "on: no instructions and no link")
s = posters_line(page_with_state(""))
check("neví se" in s and "Enable Image Caching" in s, "an unknown state: it says when it will be found out and what it is")
db.set_setting("seerr_web", "https://zadosti.example.org")
s = posters_line(page_with_state("0"))
check('href="https://zadosti.example.org/settings/main"' in s and "nas:5055/settings" not in s and "vnitřní" not in s,
      "Seerr has its own address for people (Application URL): the link leads there, without the note about the inner network")
db.set_setting("seerr_web", "")
seerr.save_settings(True, "http://urluser:secretpass@nas:5055/seerr", "")
s = page_with_state("0")
check('href="http://nas:5055/seerr/settings/main"' in s and "secretpass@nas:5055/seerr/settings" not in s,
      "the password from Seerr's address does not get into the link")
seerr.save_settings(False, "http://nas:5055", "")
check("Plakáty žádostí" not in page_with_state("0"), "a Seerr that is not connected has no such line (nothing to ask)")
seerr.save_settings(True, "http://nas:5055", "")
# The state is read from Seerr's public settings (no key in the answer, the key only in the header as always).
public_requests: list[httpx.Request] = []


def public_transport(request: httpx.Request) -> httpx.Response:
    public_requests.append(request)
    if request.url.path.endswith("/api/v1/settings/public"):
        return httpx.Response(200, json=recorded("settings_public"))
    return httpx.Response(404)


db.set_setting("seerr_images", "")
state = asyncio.run(seerr.check_images(transport=httpx.MockTransport(public_transport)))
check(state == ("1" if recorded("settings_public")["cacheImages"] else "0") == seerr.images_state()
      and [str(r.url) for r in public_requests] == ["http://nas:5055/api/v1/settings/public"],
      f"check_images: one request for settings/public, state {state!r}")
check("seerr_images" in db.UNLOGGED_SETTINGS and "seerr_web" in db.UNLOGGED_SETTINGS, "a change of the images state does not flood the settings log")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
