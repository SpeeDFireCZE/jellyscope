# -*- coding: utf-8 -*-
"""The choice of the preferred language, refreshing the recently added and the messages about the connection.

Three independent things, each with a trap of its own:

  * **The preferred language.** The Languages page had Czech hard-wired in SQL,
    in the functions' names and in the headings. Whoever set Jellyscope up in Portugal
    got a page about something they do not care about. The choice is saved, so after
    refreshing the page it stays – and only languages that are really in the library
    are offered.

  * **Recently added.** A background task finds a new movie and writes it into the
    database. On an already open Overview nothing happened though, until somebody
    refreshed the page by hand. The library's fingerprint serves that: it changes only
    when there really is something to show.

  * **„Nepodařilo se spojit.“** When Jellyfin answers slowly, it is something
    entirely different from when it does not answer at all. With a shared thirty-second
    ceiling a full sync of a large library fell over with a message about the connection –
    and the fault was then looked for in vain in the address and the API key.

Run:
    .venv/bin/python tests/test_language_choice_and_connection.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice of the
# database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vyber.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402

from jellyscope import access, accounts, db, demodata, jellyfin, langstats, scanner  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "secretpass1", is_admin=True)
accounts.create("reader", "readerpass1", is_admin=False)
demodata.seed()

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402


print("--- only languages that are in the library are offered ---")
options = langstats.library_language_options()
codes = [v["code"] for v in options]
check(len(codes) > 0, f"the library has some languages ({codes})")
check(len(codes) == len(set(codes)), "no language is in the list twice")
check("und" not in codes, "'Neuvedeno' is not offered as a choice")
check(all(v["title_count"] > 0 for v in options), "every choice has the number of titles")

# Sorted by count: the most frequent language at the top, where a person expects it.
counts = [v["title_count"] for v in options]
check(counts == sorted(counts, reverse=True), f"sorted by the number of titles ({counts})")

# A language that is really not in the library must not appear among the choices -
# otherwise something could be picked that makes a page of zeros only.
audio_rows = db.query_all(
    "SELECT audio_languages FROM items WHERE audio_languages IS NOT NULL")
actual = set()
for row in audio_rows:
    actual.update(part for part in str(row["audio_languages"]).split(",") if part)
check(set(codes) <= actual, "nothing that is not in the data is offered")


print()
print("--- the choice is saved and survives refreshing the page ---")
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "secretpass1"})

    default_language = langstats.preferred_language()
    check(default_language == "cs", f"without a setting Czech holds ({default_language})")

    other = next(code for code in codes if code != "cs")
    response = client.post("/video/languages/preferred", data={"code": other},
                           follow_redirects=False)
    check(response.status_code == 303, "the choice accepted")
    check(db.get_setting(langstats.PREFERRED_SETTING) == other,
          f"it was saved into the settings ({db.get_setting(langstats.PREFERRED_SETTING)})")
    check(langstats.preferred_language() == other, "and it is read back")

    # This is the core of the request: after refreshing the page the choice does not disappear.
    page = client.get("/video/languages?days=365").text
    name = langstats.languages.display(other)
    check(f'value="{other}"' in page and "selected" in page,
          "the stored one stays selected in the choice")

    # Every choice must show the number of titles. It looks like a detail,
    # but it is a trap: were the key called "items", Jinja in the template would
    # reach for the method `dict.items` instead of the value, and a dash would be written
    # instead of the number. A bug the test reveals and the eye at first sight does not.
    # Only the choice of the preferred language. The page also has the users' filter
    # in a dialog, and that one understandably does not write out the numbers of titles - were
    # every <option> on the page taken, the test would fail on unrelated things.
    select = re.search(r'<select id="preferred".*?</select>', page, re.S)
    options_in_html = re.findall(r"<option[^>]*>\s*(.*?)\s*</option>",
                                 select.group(0) if select else "", re.S)
    check(all(re.search(r"\(\s*\d", v) for v in options_in_html),
          f"every choice shows the number of titles ({options_in_html})")

    print()
    print("--- the headings change along with the choice ---")
    check(f"Podíl jazyka {name}" in re.sub(r"\s+", " ", page),
          f"the main number speaks about the chosen language ({name})")
    headings = [re.sub(r"\s+", " ", h) for h in re.findall(r"<h2>(.*?)</h2>", page, re.S)]
    missing_headings = [h for h in headings if "stopa chybí" in h]
    check(any(name in h for h in missing_headings),
          f"the heading of the list of missing tracks too ({missing_headings})")
    check(not any("češtin" in h.lower() for h in headings),
          f"and hard-coded Czech stayed nowhere ({headings})")

    print()
    print("--- every row leads through to a detail ---")
    missing = langstats.missing_preferred(365, other)
    check(bool(missing["rows"]), "the list contains something")
    check(all(r["detail_url"] for r in missing["rows"]), "every row has a link")
    # A series leads to the series (the episodes are there), a movie to the file's detail.
    for row in missing["rows"]:
        expected = (f"/series/{row['series_id']}" if row["series_id"]
                    else f"/item/{row['item_id']}")
        if row["detail_url"] != expected:
            check(False, f"a wrong target for {row['label']}: {row['detail_url']}")
            break
    else:
        check(True, "a series points at the series, a movie at the file")

    # And the link must really lead somewhere, not end at a 404.
    target = missing["rows"][0]["detail_url"]
    check(client.get(target).status_code == 200, f"the link {target} works")

    print()
    print("--- a series is reported whole only when the track is really missing in all episodes ---")
    # The reported bug: the language filter was applied to single episodes still
    # BEFORE grouping, so exactly the episodes that have the track dropped out of the group
    # - and the rest pretended "the whole series lacks the track", even when
    # maybe a single episode of twenty lacked it.
    now = db.utcnow()
    with db.connect() as conn:
        conn.execute("INSERT INTO libraries (id, name, synced_at)"
                     " VALUES ('lib-t','Serialy',?)", (now,))
        # Series A: Czech in all episodes except one.
        for number, audio in ((1, "cs,en"), (2, "cs,en"), (3, "en")):
            conn.execute(
                """INSERT INTO items (id, name, type, library_id, series_id,
                                      series_name, parent_index_number,
                                      index_number, audio_languages,
                                      subtitle_languages, is_missing, synced_at)
                   VALUES (?,?,'Episode','lib-t','ser-a','Skoro česky',1,?,?,'cs',0,?)""",
                (f"a-{number}", f"Díl {number}", number, audio, now))
        # Series B: Czech nowhere.
        for number in (1, 2):
            conn.execute(
                """INSERT INTO items (id, name, type, library_id, series_id,
                                      series_name, parent_index_number,
                                      index_number, audio_languages,
                                      subtitle_languages, is_missing, synced_at)
                   VALUES (?,?,'Episode','lib-t','ser-b','Vůbec česky',1,?,'en','',0,?)""",
                (f"b-{number}", f"Díl {number}", number, now))
        # A viewer saw in both series only the episode without Czech.
        for key, item in (("t-a", "a-3"), ("t-b", "b-1")):
            conn.execute(
                """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                         started_at, last_seen_at, watched_seconds,
                                         is_active)
                   VALUES (?, 'u-t', ?, 'x', ?, ?, 5400, 0)""",
                (key, item, now, now))

    result = langstats.missing_preferred(365, "cs", limit=50)
    by_link = {r["detail_url"]: r for r in result["rows"]}

    check("/series/ser-b" in by_link,
          f"a series without Czech in all episodes is one row ({list(by_link)})")
    check(by_link.get("/series/ser-b", {}).get("is_series") is True,
          "and it is marked as a whole series")

    check("/series/ser-a" not in by_link,
          "a series that has Czech in most episodes is NOT reported as a whole")
    check("/item/a-3" in by_link,
          f"instead of it that one episode is reported ({list(by_link)})")
    episode = by_link.get("/item/a-3", {})
    check("S01E03" in (episode.get("label") or ""),
          f"and it can be told which episode it is ({episode.get('label')})")
    check("Skoro česky" in (episode.get("label") or ""),
          "and which series it belongs to")
    check(episode.get("is_series") is False, "an episode does not pretend to be a whole series")

    # A movie must not have the same name once more under its name.
    movies = [r for r in result["rows"] if not r["is_series"] and not r["episode_name"]]
    check(all(r["label"] != r["episode_name"] for r in movies),
          "a movie's name does not repeat")

    print()
    print("--- only a language that is in the library can be picked ---")
    response = client.post("/video/languages/preferred", data={"code": "klingon"},
                           follow_redirects=False)
    check(db.get_setting(langstats.PREFERRED_SETTING) == other,
          "a made-up language did not overwrite the stored choice")

    print()
    print("--- only the administrator may change the choice ---")
    # It is a setting of the whole app, not a personal filter. Were anybody to switch it,
    # they would switch it for everybody else too.
    client.post("/logout")
    client.post("/login", data={"username": "reader", "password": "readerpass1"})
    response = client.post("/video/languages/preferred", data={"code": "cs"},
                           follow_redirects=False)
    check(response.status_code == 403, f"refused to a reader ({response.status_code})")
    check(db.get_setting(langstats.PREFERRED_SETTING) == other, "the choice stayed")
    check(client.get("/video/languages").status_code == 200, "but the reader sees the page")


print()
print("--- the library's fingerprint changes only when there is something to show ---")
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "secretpass1"})

    version = scanner.library_version()
    check(bool(version), f"the fingerprint exists ({version})")
    check(scanner.library_version() == version, "reading it again gives the same")

    # A title is added -> the fingerprint must change, otherwise the strip of recently
    # added on an already open page would never fill up.
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (id, name, type, date_created, is_missing, synced_at)
               VALUES ('cerstvy', 'Čerstvý film', 'Movie', ?, 0, ?)""",
            (db.utcnow(), db.utcnow()))
    check(scanner.library_version() != version, "after a title is added the fingerprint is different")

    # /health must offer it, because the page decides by it.
    health = client.get("/health").json()
    check(health.get("library_version") == scanner.library_version(),
          "/health reports the same fingerprint")
    check("task_running" in health, "and also whether a task runs (because of the jumping)")

    # The partial must be fetchable on its own and drawn with the same template.
    response = client.get("/partials/recently-added")
    check(response.status_code == 200, f"/partials/recently-added -> {response.status_code}")
    check("Čerstvý film" in response.text, "and it contains the newly added title")
    check("poster-card" in response.text, "it is drawn as a strip of posters")

    # The Overview must write the fingerprint out, otherwise the browser has nothing to compare.
    dashboard = client.get("/").text
    check('id="recently-added"' in dashboard, "the Overview frames that partial")
    check("data-version=" in dashboard, "and carries the fingerprint to compare")

    # Without signing in the partial must not be given out - it has names from the library in it.
    with TestClient(app) as guest:
        check(guest.get("/partials/recently-added",
                        follow_redirects=False).status_code == 303,
              "the partial is not given out to somebody not signed in")


print()
print("--- a slow answer is not the same as an unreachable server ---")
# This was the reported bug: the automatic sync reported that it
# could not connect to Jellyfin, and the fault was looked for in the address and the key.
# In fact the server answered, only more slowly than the allowed ceiling.
check(jellyfin.DEFAULT_TIMEOUT.connect <= 15,
      f"connecting has a short ceiling ({jellyfin.DEFAULT_TIMEOUT.connect} s)")
check(jellyfin.DEFAULT_TIMEOUT.read >= 120,
      f"but reading the answer a long one ({jellyfin.DEFAULT_TIMEOUT.read} s)")
check(jellyfin.DEFAULT_TIMEOUT.read > jellyfin.DEFAULT_TIMEOUT.connect,
      "reading must not have the same ceiling as connecting")
check(jellyfin.QUICK_TIMEOUT.read < jellyfin.DEFAULT_TIMEOUT.read,
      "the collector and the connection test have their own short ceiling")


class SlowServer(httpx.AsyncBaseTransport):
    """A server that exists, but does not send the answer in time."""

    def __init__(self) -> None:
        self.attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        raise httpx.ReadTimeout("simulace", request=request)


class Unreachable(httpx.AsyncBaseTransport):
    """A server that cannot be connected to at all."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulace", request=request)


async def ask(transport: httpx.AsyncBaseTransport) -> str:
    client = jellyfin.JellyfinClient("http://server:8096", "klic")
    # We slip in the transport, so the test needs neither the network nor a real Jellyfin.
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://server:8096",
                                       transport=transport)
    try:
        await client.system_info()
    except jellyfin.JellyfinError as exc:
        return str(exc)
    finally:
        await client.close()
    return ""


slow = SlowServer()
# The retry waits a few seconds between attempts. In the test we cut it to zero -
# we check the number of attempts and the message's wording, not the real waiting.
_sleep = asyncio.sleep


async def no_wait(_seconds: float) -> None:
    await _sleep(0)


jellyfin.asyncio.sleep = no_wait          # type: ignore[assignment]
try:
    message = asyncio.run(ask(slow))
finally:
    jellyfin.asyncio.sleep = _sleep         # type: ignore[assignment]

check("did not answer" in message and "in time" in message,
      f"a slow server reports a timeout: {message[:80]}")
check("Nepodarilo se spojit" not in message,
      "and does NOT report that it could not connect - that sent people looking for the fault elsewhere")
check(slow.attempts == jellyfin.TIMEOUT_RETRIES + 1,
      f"before it gives up, it tries again ({slow.attempts} attempts)")

message = asyncio.run(ask(Unreachable()))
check("Could not connect" in message,
      f"an unreachable server on the contrary reports the connection: {message[:80]}")


print()
print("--- a character that does not belong in the key or the address is told at the form ---")
# A key copied from a web page with an invisible space (U+200B), or `nas:port` in the address: httpx fails on
# both before anything is sent, with an error that is not JellyfinError. The form saved the value first and the
# page fell with a 500 - and from then on every sign-in through Jellyfin and every poster fell too.
BAD_KEY = "abcdef0123456789\u200b"
INVALID_KEY_CS = "API klíč obsahuje znak, který do něj nepatří"
db.set_setting("jellyfin_url", "http://jellyfin.doma:8096")
db.set_setting("jellyfin_api_key", "good-key-0123456789")
db.forget_settings()
with TestClient(app, raise_server_exceptions=False) as www:
    www.post("/login", data={"username": "admin", "password": "secretpass1"})
    for action in ("save", "test"):
        response = www.post("/settings/connection", follow_redirects=False, data={
            "jellyfin_url": "http://jellyfin.doma:8096", "jellyfin_api_key": BAD_KEY,
            "jellyfin_generation": "auto", "action": action})
        db.forget_settings()
        page = www.get("/settings?section=jellyfin").text if response.status_code == 303 else ""
        check(response.status_code == 303 and INVALID_KEY_CS in page,
              f"{action} with an invisible character in the key: the form says why ({response.status_code})")
        check(db.get_setting("jellyfin_api_key", "") == "good-key-0123456789",
              f"{action}: and the stored key stayed as it was")
    for address in ("nas:port", "http://ab\u00adc.doma"):
        response = www.post("/settings/connection", follow_redirects=False, data={
            "jellyfin_url": address, "jellyfin_api_key": "", "jellyfin_generation": "auto", "action": "save"})
        db.forget_settings()
        page = www.get("/settings?section=jellyfin").text if response.status_code == 303 else ""
        check(response.status_code == 303 and "Adresa aplikace není platná." in page,
              f"the address {address!r}: the form says it is not valid ({response.status_code})")
        check(db.get_setting("jellyfin_url", "") == "http://jellyfin.doma:8096",
              f"the address {address!r}: and the stored address stayed as it was")

# A value stored before the form guarded it: an error of the client, not a fallen page.
for url, key, label in (("http://jellyfin.doma:8096", BAD_KEY, "a key with an invisible character"),
                        ("http://nas:port", "good-key-0123456789", "an address with a port that is no number")):
    try:
        jellyfin.JellyfinClient(url, key)
        kind = "nothing"
    except Exception as error:   # noqa: BLE001 - which error it is, is what is checked
        kind = type(error).__name__
    check(kind == "JellyfinError", f"the client with {label} raises JellyfinError ({kind})")

db.set_setting("jellyfin_api_key", BAD_KEY)
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
an_item = (db.query_one("SELECT id FROM items WHERE image_tag IS NOT NULL LIMIT 1")
           or db.query_one("SELECT id FROM items LIMIT 1"))["id"]
with TestClient(app, raise_server_exceptions=False) as www:
    www.post("/login", data={"username": "admin", "password": "secretpass1"})
    poster = www.get(f"/image/{an_item}")
    check(poster.status_code == 404, f"a poster with the stored bad key: 404, not a fallen page ({poster.status_code})")
    signing_in = TestClient(app, raise_server_exceptions=False).post(
        "/login", data={"username": "mirek", "password": "whatever1", "method": "jellyfin"}, follow_redirects=False)
    # A refused sign-in (401), not just "no 500": a 303 would mean the bad key let somebody in.
    check(signing_in.status_code == 401, f"signing in through Jellyfin: refused with 401, no 500 ({signing_in.status_code})")
db.set_setting("jellyfin_api_key", "good-key-0123456789")
db.set_setting(access.LOGIN_KEY, "0")
db.forget_settings()


print()
print("--- a redirect or an answer that is not Jellyfin is no success ---")
# A reverse proxy that sends http:// on to https:// with an empty body: the connection test said "works: ? (Jellyfin ?)",
# the collector reported fine and collected nothing, and signing in through Jellyfin fell on response.json().


def proxy(status: int, body: bytes = b"", content_type: str = "") -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": content_type} if content_type else {}
        if 300 <= status < 400:
            headers["location"] = f"https://name:password@server{request.url.path}?api_key=klic"
        return httpx.Response(status, content=body, headers=headers)
    return httpx.MockTransport(answer)


async def outcome(transport: httpx.AsyncBaseTransport, call: str, *arguments: str) -> str:
    client = jellyfin.JellyfinClient("http://server:8096", "klic", version="12.0")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="http://server:8096", transport=transport)
    try:
        return f"returned {await getattr(client, call)(*arguments)!r}"
    except jellyfin.JellyfinError as error:
        return f"JellyfinError {error}"
    except Exception as error:   # noqa: BLE001 - which error it is, is what is checked
        return f"{type(error).__name__} {error}"
    finally:
        await client.close()


for call in ("system_info", "sessions", "users"):
    text = asyncio.run(outcome(proxy(308), call))
    check(text.startswith("JellyfinError") and "redirects elsewhere" in text and "https://server" in text,
          f"{call} with a redirect: an error that names the address to enter ({text[:110]})")
    check("password" not in text and "api_key" not in text and "/System" not in text and "/Sessions" not in text,
          f"{call}: the address offered has neither the credentials, nor the query, nor the API path")
text = asyncio.run(outcome(proxy(200), "system_info"))
check(text.startswith("JellyfinError") and "unexpected answer" in text,
      f"an empty answer to /System/Info is not a working connection ({text[:110]})")
text = asyncio.run(outcome(proxy(200, b'{"Something": "else"}', "application/json"), "system_info"))
check(text.startswith("JellyfinError"), f"nor is JSON without a version and an id ({text[:110]})")
text = asyncio.run(outcome(proxy(200, b'{"Version": "12.0.1", "Id": "abc"}', "application/json"), "system_info"))
check(text.startswith("returned") and "12.0.1" in text, f"a real answer passes ({text[:110]})")

PROXY_PAGE = (200, b"<html>Sign in to the proxy</html>", "text/html")
for call, arguments, label in (("verify_login", ("mirek", "heslo"), "a password"),
                               ("quick_connect_start", (), "starting Quick Connect"),
                               ("quick_connect_confirmed", ("tajemstvi",), "asking about Quick Connect"),
                               ("quick_connect_finish", ("tajemstvi",), "finishing Quick Connect")):
    text = asyncio.run(outcome(proxy(*PROXY_PAGE), call, *arguments))
    check(text.startswith(("JellyfinError", "returned None", "returned False")),
          f"{label} answered by a proxy's page: no exception the sign-in page falls on ({text[:90]})")
text = asyncio.run(outcome(proxy(308), "verify_login", "mirek", "heslo"))
check(text.startswith("JellyfinError") and "redirects elsewhere" in text,
      f"checking a password on a redirect: an error to the log, not a fallen page ({text[:90]})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
