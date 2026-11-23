# -*- coding: utf-8 -*-
r"""Jellyfin 12 and older: what we identify ourselves with and where we ask.

Jellyfin 12.0 stopped parsing the old sign-in headers
(`X-Emby-Token`, `X-Emby-Authorization`, `X-MediaBrowser-Token`), dropped
the paths `/emby/` and `/mediabrowser/` and changed for `GetItems` when
`Recursive` holds. `Authorization: MediaBrowser Token="..."` stayed valid -
that is, what Jellyscope has always identified itself with.

What this test watches:

* **The header with the key always goes and in the right shape.** Were it to
  break, the user gets 401 and will look for it in the API key, where it is not.
* **The old header is sent only to old servers.** It is useless to twelve
  and repeats the key in every query on top.
* **An unknown version behaves as an old server.** The opposite choice would mean
  a refused connection on an old server; a superfluous header on a new one
  means nothing.
* **A manual choice of the generation beats the detected version** and when it is saved
  the other value is forgotten, so the two numbers do not contradict each other.
* **The fallback path `/Users/{id}/Items` is not tried with twelve** - there
  `/Items` is the right one and a failure means something else.

Run:
    .venv/bin/python tests/test_jellyfin_version.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in all the files of the web/settings subpackage

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "verze.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

import httpx  # noqa: E402

from jellyscope import db, jellyfin  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


db.init_db()

print("--- reading the version ---")
for text, want in (("12.0.1", (12, 0)), ("12.1", (12, 1)), ("10.10.7", (10, 10)),
                   ("", (0, 0)), ("nesmysl", (0, 0)), (None, (0, 0)),
                   ("10.11.0-rc1", (10, 11))):
    got = jellyfin.server_version(text)
    check(got == want, f"{text!r} -> {got}")

print()
print("--- who is an “old server” ---")
check(jellyfin.old_server("10.10.7") is True, "ten yes")
check(jellyfin.old_server("12.0.1") is False, "twelve no")
check(jellyfin.old_server("13.2") is False, "and nothing newer either")
# An unknown version behaves as an old one: a superfluous header does not bother a new server,
# whereas a missing one would mean a refused key for old ones.
check(jellyfin.old_server("") is True, "an unknown version is taken as old")

print()
print("--- headers ---")
new = jellyfin.headers("KLIC", "12.0.1")
old = jellyfin.headers("KLIC", "10.10.7")
check(new["Authorization"] == 'MediaBrowser Token="KLIC"',
      f"the modern header is always there ({new['Authorization']})")
check(old["Authorization"] == new["Authorization"],
      "and it is the same for both generations")
check("X-Emby-Token" not in new, "twelve is not sent the old header")
check(old.get("X-Emby-Token") == "KLIC", "ten is")
check("X-Emby-Token" in jellyfin.headers("KLIC", ""),
      "and with an unknown version too")
# The key never in the address - it is logged in the proxy and in the browser's history.
check(all("KLIC" not in header_name for header_name in new),
      "the key is in the header's value, not in its name")

print()
print("--- what the server really gets ---")
# A real request through httpx, only with a slipped-in transport. Here it is no longer
# the function that is tested, but what would come to Jellyfin.
seen: list[httpx.Request] = []


def answer(request: httpx.Request) -> httpx.Response:
    seen.append(request)
    return httpx.Response(200, json={"Version": "12.0.1", "ServerName": "test"})


async def ask(version_text: str) -> httpx.Request:
    client = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC", version=version_text)
    client._client = httpx.AsyncClient(
        base_url="http://jellyfin.test",
        headers=jellyfin.headers("KLIC", version_text),
        transport=httpx.MockTransport(answer))
    async with client:
        await client.system_info()
    return seen[-1]


request = asyncio.run(ask("12.0.1"))
check(request.headers.get("authorization") == 'MediaBrowser Token="KLIC"',
      "twelve gets the modern header")
check("x-emby-token" not in request.headers, "and not the old one")
check("api_key" not in str(request.url) and "ApiKey" not in str(request.url),
      f"the key is not in the address ({request.url})")
check(str(request.url).endswith("/System/Info"),
      f"and the path has no /emby/ prefix ({request.url})")

request = asyncio.run(ask("10.10.7"))
check(request.headers.get("x-emby-token") == "KLIC",
      "ten gets the old one too")

print()
print("--- the version is remembered ---")
db.set_setting(jellyfin.GENERATION_KEY, "auto")
db.forget_settings()
asyncio.run(ask(""))
db.forget_settings()
check(db.get_setting(jellyfin.VERSION_KEY, "") == "12.0.1",
      f"after a query to /System/Info ({db.get_setting(jellyfin.VERSION_KEY, '')})")

print()
print("--- a manual choice beats the detected version ---")
db.set_setting(jellyfin.VERSION_KEY, "10.10.7")
db.set_setting(jellyfin.GENERATION_KEY, "12")
db.forget_settings()
client = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC")
check(client.version == "12.0", f"the choice 12 holds ({client.version})")
check("X-Emby-Token" not in jellyfin.headers("KLIC", client.version),
      "and the old header is not sent")
asyncio.run(client.close())

db.set_setting(jellyfin.GENERATION_KEY, "10")
db.forget_settings()
client = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC")
check(jellyfin.old_server(client.version) is True, "the choice 10 holds too")
asyncio.run(client.close())

# The detected version is an OBSERVATION of the server: it is written even with a manual choice,
# so that it is seen when the two numbers contradict each other. The behaviour follows the choice.
db.set_setting(jellyfin.VERSION_KEY, "")
db.forget_settings()
asyncio.run(ask(jellyfin.GENERATIONS["10"]))
db.forget_settings()
check(db.get_setting(jellyfin.VERSION_KEY, "") == "12.0.1",
      f"even with a manual choice what the server reports is written "
      f"({db.get_setting(jellyfin.VERSION_KEY, '')})")
client = jellyfin.JellyfinClient("http://jellyfin.test", "KLIC")
check(jellyfin.old_server(client.version) is True,
      "but the manual choice (10) still decides")
asyncio.run(client.close())

print()
print("--- switching the generation: what stays and what is detected again ---")
# The address and the key are the same for both generations - retyping them would be
# a nuisance for nothing. The detected version belongs to the previous choice though, so it
# is thrown away and detected again; otherwise the page would keep two numbers that
# contradict each other.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402
from jellyscope.web import settings as web_settings  # noqa: E402

db.set_setting("jellyfin_url", "http://jellyfin.doma:8096")
db.set_setting("jellyfin_api_key", "TAJNY-KLIC")
db.set_setting(jellyfin.VERSION_KEY, "10.10.7")
db.set_setting(jellyfin.GENERATION_KEY, "auto")
db.forget_settings()

accounts.create("admin", "longpassword", is_admin=True)
with TestClient(app) as www:
    www.post("/login", data={"username": "admin", "password": "longpassword"},
             follow_redirects=False)

    def save(generation: str) -> None:
        # An empty key in the form means "keep the current one".
        www.post("/settings/connection", follow_redirects=False, data={
            "jellyfin_url": "http://jellyfin.doma:8096",
            "jellyfin_api_key": "",
            "jellyfin_generation": generation,
            "action": "save"})
        db.forget_settings()

    save("12")
    check(db.get_setting(jellyfin.GENERATION_KEY, "") == "12", "the choice was saved")
    check(db.get_setting("jellyfin_api_key", "") == "TAJNY-KLIC",
          "the API key stayed")
    check(db.get_setting("jellyfin_url", "") == "http://jellyfin.doma:8096",
          "the address stayed")
    check(db.get_setting(jellyfin.VERSION_KEY, "") == "",
          f"the detected version was thrown away, it is detected again "
          f"({db.get_setting(jellyfin.VERSION_KEY, '')!r})")

    # Saving the same settings again throws nothing away - otherwise every
    # saving of the address would delete the version we just detected.
    db.set_setting(jellyfin.VERSION_KEY, "12.0.1")
    db.forget_settings()
    save("12")
    check(db.get_setting(jellyfin.VERSION_KEY, "") == "12.0.1",
          "saving without a change of the choice leaves the version be")

    save("auto")
    check(db.get_setting(jellyfin.VERSION_KEY, "") == "",
          "the way back to “detect” detects it again too")
    check(db.get_setting("jellyfin_api_key", "") == "TAJNY-KLIC",
          "and the key still holds")

    save("vymysl")
    check(db.get_setting(jellyfin.GENERATION_KEY, "") == "auto",
          "nonsense falls back to detecting")

print()
print("--- saving the connection detects the version by itself ---")
# The version used to be detected only at "Test connection" or at
# a sync. Whoever switched the generation and pressed Save looked at emptiness
# and did not know whether something happened.


class FakeClient:
    """A Jellyfin that always answers. It is slipped in instead of the network one."""

    last_url = ""

    def __init__(self, url, key, timeout=None, version=None):
        FakeClient.last_url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def system_info(self):
        db.set_setting(jellyfin.VERSION_KEY, "12.0.1")
        return {"Version": "12.0.1", "ServerName": "falesny"}


# The route /settings/connection lives in `web/settings/connection.py` and has its own
# name `JellyfinClient`. Slipping the client into `web` would do nothing -
# the test would pass without trying anything.
original_client = web_settings.JellyfinClient
substitute(web_settings, "JellyfinClient", FakeClient)
try:
    db.set_setting(jellyfin.VERSION_KEY, "")
    db.set_setting(jellyfin.GENERATION_KEY, "auto")
    db.forget_settings()
    with TestClient(app) as www:
        www.post("/login", data={"username": "admin", "password": "longpassword"},
                 follow_redirects=False)
        response = www.post("/settings/connection", follow_redirects=False, data={
            "jellyfin_url": "http://jellyfin.doma:8096",
            "jellyfin_api_key": "TAJNY-KLIC",
            "jellyfin_generation": "auto",
            "action": "save"})
    db.forget_settings()
    check(response.status_code == 303, "saving redirects")
    check(db.get_setting(jellyfin.VERSION_KEY, "") == "12.0.1",
          f"and the version is detected straight away ({db.get_setting(jellyfin.VERSION_KEY, '')})")
    check(FakeClient.last_url == "http://jellyfin.doma:8096",
          "it asked the saved address")
finally:
    substitute(web_settings, "JellyfinClient", original_client)


print()
print("--- when the server does not run, saving is not spoiled by it ---")
# Saving the settings must not depend on whether Jellyfin is up
# right now. The version simply stays empty and is filled in next time.
db.set_setting(jellyfin.VERSION_KEY, "")
db.forget_settings()
with TestClient(app) as www:
    www.post("/login", data={"username": "admin", "password": "longpassword"},
             follow_redirects=False)
    response = www.post("/settings/connection", follow_redirects=False, data={
        # Nobody listens on port 1 - the connection fails at once.
        "jellyfin_url": "http://127.0.0.1:1",
        "jellyfin_api_key": "TAJNY-KLIC",
        "jellyfin_generation": "auto",
        "action": "save"})
db.forget_settings()
check(response.status_code == 303, "saving passes even so")
check(db.get_setting("jellyfin_url", "") == "http://127.0.0.1:1",
      "the address was saved")
check(db.get_setting(jellyfin.VERSION_KEY, "") == "",
      "the version stayed empty, it is filled in next time")

print()
print("--- a mismatch of the choice and the server is recognised ---")
# When a person chooses twelve and the server reports ten, it is not an error -
# that is what the choice is for. But it is to be seen.
db.set_setting(jellyfin.GENERATION_KEY, "12")
db.set_setting(jellyfin.VERSION_KEY, "10.10.7")
db.forget_settings()
check(web_settings._generation_mismatch() is True, "12 against the reported 10.10.7")
db.set_setting(jellyfin.VERSION_KEY, "12.0.1")
db.forget_settings()
check(web_settings._generation_mismatch() is False, "12 against the reported 12.0.1 matches")
db.set_setting(jellyfin.GENERATION_KEY, "auto")
db.forget_settings()
check(web_settings._generation_mismatch() is False, "with detecting nothing is compared")

print()
print("--- the fallback path only with old servers ---")
source = (PROJECT / "jellyscope" / "jellyfin.py").read_text(encoding="utf-8")
check(source.count("not old_server(self.version)") == 2,
      "both places ask for the version (iter_items and recent_items)")
check('f"/Users/{user_id}/Items" if user_id else "/Items"' in source,
      "and without a user we still ask for /Items")

print()
print("--- nothing dropped stayed in the project ---")
# Twelve dropped /emby/ and /mediabrowser/, it does not parse api_key in the address.
files = list((PROJECT / "jellyscope").glob("**/*.py"))
bad = []
for path in files:
    text = path.read_text(encoding="utf-8")
    for pattern in ('"/emby', "'/emby", '"/mediabrowser', "api_key=' +"):
        if pattern in text:
            bad.append(f"{path.name}: {pattern}")
check(not bad, f"no dropped path ({bad})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
