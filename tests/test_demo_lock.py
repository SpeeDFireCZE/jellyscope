# -*- coding: utf-8 -*-
r"""The demo mode: everything is seen, nothing changes.

The demo runs at a public address and the sign-in details are right there
in the sign-in window - anybody gets in. Without a safeguard the first
visitor would rewrite Jellyfin's address, start an import or a restart, and for
everybody else the demo would be over.

It is watched by one middleware, not in every route separately: a route that
would be forgotten is exactly the one somebody finds.

Run:
    .venv/bin/python tests/test_demo_lock.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "1"

from _package import sample_movie  # noqa: E402
from jellyscope import accounts, config, db  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)
accounts.create("demo", "demodemo", is_admin=True)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

client = TestClient(web.app)
client.post("/login", data={"username": "demo", "password": "demodemo"},
            follow_redirects=False)

print("--- what must not run in the demo ---")
# Behind every path is what it would do in live use.
DANGEROUS = {
    "/settings/save": {"jellyfin_url": "http://cizi:8096"},
    "/settings/connection": {"url": "http://cizi:8096", "api_key": "x"},
    "/settings/database": {"kind": "sqlite"},
    "/settings/sync": {},
    "/settings/scan": {},
    "/settings/restart": {},
    "/settings/update": {},
    "/settings/tasks": {},
    "/settings/tasks/run": {"key": "sync"},
    "/settings/import/playback-reporting": {},
    "/settings/history/tidy": {},
    "/settings/accounts/create": {"username": "utocnik", "password": "longpassword"},
    "/settings/backup/download": {},
}
# A 303 alone is not enough: a route redirects after doing its work too.
# So we ask for the message shown after the redirect - it comes about
# only when the action did NOT run.
MESSAGE = "ukázka"
for path, data in DANGEROUS.items():
    response = client.post(path, data=data, follow_redirects=False)
    page = client.get("/").text if response.status_code == 303 else ""
    check(response.status_code == 303 and MESSAGE in page,
          f"{path} → a message instead of the action ({response.status_code})")

check(db.get_setting("jellyfin_url", "") != "http://cizi:8096",
      "Jellyfin's address stayed untouched")
check(accounts.get_by_name("utocnik") is None, "and no account was added")

print()
print("--- the harmless is locked too ---")
# Anything saved holds for all the next visitors: whoever switches to
# Czech switches it for the one after them too. So not even the language is saved.
db.set_setting("ui_language", "en")
client.post("/settings/language", data={"ui_language": "cs"},
            follow_redirects=False)
check(db.get_setting("ui_language") == "en", "the interface language stayed as it was")

db.set_setting("ui_max_streams", "10")
client.post("/settings/interface",
            data={"ui_max_streams": "5", "ui_max_viewers": "5",
                  "ui_map_zoom": "wheel"}, follow_redirects=False)
check(db.get_setting("ui_max_streams") == "10", "and the interface settings too")

print()
print("--- what MUST work in the demo ---")
# Without signing in and out there would be nothing to show.
check("/login" in web.DEMO_ALLOWED and "/logout" in web.DEMO_ALLOWED,
      "signing in and out are the only exceptions")
check(len(web.DEMO_ALLOWED) == 2, f"and really the only ones ({sorted(web.DEMO_ALLOWED)})")

print()
print("--- the message shows in place, the page does not move ---")
# The redirect is only a fallback in case there were no JavaScript. The browser
# does not send the form at all - see the handler in static/js/app.js.
# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
base = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
        + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
check("demo-flash" in base, "the template has a place for the message")
check("event.preventDefault()" in base and "ALLOWED" in base,
      "and a handler that stops the sending")
page = client.get("/settings?section=data").text
check("demo-flash" in page, "the settings page has the message")
check("demo" in page.lower(), "and a sign that nothing is saved")

print()
print("--- browsing works whole ---")
for path in ("/", "/video/insights", "/video/languages", "/network", "/library", "/users",
             "/history", "/settings?section=jellyfin"):
    check(client.get(path).status_code == 200, f"{path} opens")

print()
print("--- other visitors' addresses are not shown ---")
# The sign-in details are on the sign-in page, so anybody reads Blocked addresses and the Log - and in them the IP
# address of whoever mistyped the password, or of a robot trying passwords: a stranger's personal data, public.
from jellyscope import applog  # noqa: E402

applog.setup()
VISITORS = {"198.51.100.77": "198.51.100.77", "2001:db8:77::5": "2001:db8:77:"}   # an IPv6 block is its /64
for address in VISITORS:
    stranger = TestClient(web.app, client=(address, 5555))
    for attempt in range(8):
        stranger.post("/login", data={"username": "demo", "password": f"wrong-{attempt}"}, follow_redirects=False)
applog.write_api(1, "key", "-", "/api/v1/summary", "198.51.100.77", 200)
for handler in logging.getLogger().handlers:
    handler.flush()
check(all(address in applog.log_path().read_text(encoding="utf-8") for address in VISITORS)
      and len(accounts.list_blocks()) == 2, "the attempts are blocked and logged (the checks below are not empty)")
for path in ("/settings?section=blocks", "/settings?section=log", "/settings?section=log&log_kind=api",
             f"/settings/log/download?file={applog.log_path().name}", f"/settings/log/download?file={applog.API_NAME}"):
    text = client.get(path).text
    shown = [address for address, seen in VISITORS.items() if seen in text]
    check(not shown, f"{path}: no visitor's address ({shown})")
# Nor can an address be guessed by the API log's filter (the field shows back what was typed into it).
guessed = client.get("/settings?section=log&log_kind=api&log_ip=198.51.100.77").text
check("Nothing in the latest entries matches the filter." in guessed, "a guessed address finds no request")
blocks_page = client.get("/settings?section=blocks").text
check(blocks_page.count(applog.HIDDEN_ADDRESS) >= 2, "the blocks are listed, with the address hidden")
check("198.51.100.77" in applog.log_path().read_text(encoding="utf-8"), "the log file itself keeps the address")

print()
print("--- outside the demo nothing is blocked ---")
os.environ.pop("JELLYSCOPE_DEMO")
config.load_config(reload=True)
response = client.post("/settings/language", data={"ui_language": "cs"},
                       follow_redirects=False)
check(response.status_code == 303, "ordinary use stays as it was")
check(web._demo_blocks.__doc__ is not None, "and the function is described")

print()
print("--- the demo is prepared even where demo.py does not run ---")
# The image runs run.py. Without the preparation the public demo would come up empty
# and locked: nothing is saved in the demo mode, so not even an administrator could
# set it up. That is why the preparation is in demodata and is called from both
# starters.
import tempfile as _tempfile  # noqa: E402

from jellyscope import demodata  # noqa: E402

check(hasattr(demodata, "prepare"), "demodata can prepare the demo")

# An empty database aside, so the one the tests above run in is not touched.
_second = _tempfile.mkdtemp()
_original = os.environ["DATABASE_PATH"]
os.environ["DATABASE_PATH"] = str(Path(_second) / "prazdna.db")
config.load_config(reload=True)
# db remembers its database once chosen - without this the "empty" database was the one above
# (empty only by chance until it got a movie for the Movies & series section).
db.database_config(reload=True)
db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

check(demodata.already_seeded() is False, "a new database is empty")
counts = demodata.prepare(quiet=True)
check(counts["items"] > 0, f"after the preparation it has data ({counts['items']} titles)")
check(accounts.get_by_name(demodata.DEMO_USERNAME) is not None,
      "and an account to sign in to the demo with")

# The second time nothing - otherwise every restart of the container would double the demo.
again = demodata.prepare(quiet=True)
check(again["items"] == 0, "a second run does not double the data")

os.environ["DATABASE_PATH"] = _original
config.load_config(reload=True)
db.database_config(reload=True)

# The container's log is run.py's printout (docker compose logs): it names the made-up apps with their records, as
# demo.py does, and says that Jellyfin is made up too - not an address to try.
import re  # noqa: E402
import subprocess  # noqa: E402

_third = Path(_tempfile.mkdtemp())
started = subprocess.run(
    [sys.executable, "-c", "import run, uvicorn; uvicorn.run = lambda *a, **k: None; run.main()"], cwd=PROJECT,
    env={**os.environ, "JELLYSCOPE_HOME": str(_third), "DATABASE_PATH": str(_third / "data" / "demo.db"),
         "JELLYSCOPE_DEMO": "1"}, capture_output=True, text=True, timeout=300)
printout = started.stdout
apps_line = next((line for line in printout.splitlines() if line.startswith("Made-up apps:")), "")
jellyfin_line = next((line for line in printout.splitlines() if line.startswith("Jellyfin:")), "")
check(started.returncode == 0 and re.fullmatch(r"Made-up apps:\s+Sonarr \(\d+ records\), Radarr \(\d+ records\), .*Seerr \(\d+ records\)",
                                               apps_line) is not None,
      f"run.py's printout names the made-up apps with their records ({apps_line!r}, {started.stderr[-200:]!r})")
check("made up" in jellyfin_line, f"and the made-up Jellyfin as made up ({jellyfin_line!r})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
