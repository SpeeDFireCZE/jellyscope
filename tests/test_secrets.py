# -*- coding: utf-8 -*-
"""The API key to Jellyfin must not get into the browser.

Whoever has that key can do everything with somebody else's Jellyfin that its owner can.
Jellyscope therefore keeps it on the server only: images are downloaded through it,
templates never write it out and it must not get into a page even by accident.

The test does not check it by reading the code, but **walks through the whole application signed in
and looks for the key in the output** - in the HTML, in the headers and in the cookies.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice
# of the database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "secrets.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0

# The key unusual on purpose, so it cannot be confused with anything else on the page.
KEY = "JELLYFIN-KLIC-NESMI-UNIKNOUT-9f3a7c2e"
ADDRESS = "http://jellyfin.doma.local:8096"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
db.set_setting("jellyfin_url", ADDRESS)
db.set_setting("jellyfin_api_key", KEY)

# At least one item, so that some poster is drawn on the Overview at all -
# otherwise the check "images go through us" would test an empty page.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name) VALUES ('lib', 'Filmy')")
    conn.execute(
        """INSERT INTO items (id, name, type, library_id, date_created,
                              is_missing, synced_at)
           VALUES ('film-1', 'Zkušební film', 'Movie', 'lib', ?, 0, ?)""",
        (db.utcnow(), db.utcnow()),
    )

client = TestClient(app)
client.post("/login", data={"username": "admin", "password": "longpassword"})


print("--- the key really is in the stored settings ---")
check(db.get_setting("jellyfin_api_key") == KEY, "the key is in the database")
check(KEY not in db.get_public_settings().values(),
      "get_public_settings() does not contain it")
check(KEY in db.get_settings().values(),
      "get_settings() contains it (the server needs it)")


print()
print("--- no page writes it out ---")
PAGES = [
    "/", "/?kind=movies", "/video/partials/daily?kind=both",
    "/video/insights", "/video/languages", "/library", "/users", "/history",
    "/settings", "/settings?section=jellyfin", "/settings?section=data",
    "/settings?section=tasks", "/settings?section=import",
    "/settings?section=database", "/settings?section=accounts",
    "/settings?section=general",
]
for path in PAGES:
    response = client.get(path)
    in_body = KEY in response.text
    in_headers = any(KEY in str(h) for h in response.headers.values())
    check(not in_body and not in_headers,
          f"{response.status_code}  {path}")

print()
check(all(KEY not in (c or "") for c in client.cookies.values()),
      "the key is not even in the cookies")


print()
print("--- the form shows only dots, not the value ---")
page = client.get("/settings?section=jellyfin").text
check('type="password"' in page, "the field for the key is of the password type")
check('value="' + KEY not in page, "the field does not have the key in value")
check("••••" in page, "there are only dots instead of the key")
check(ADDRESS in page, "the server's address shows (it is not secret, it must be changeable)")


print()
print("--- images go through us, not straight from Jellyfin ---")
# If the page linked straight to Jellyfin, the browser would have to know
# its address and the key. That is why images are downloaded through /image/.
overview = client.get("/").text
check("/image/" in overview, "posters load through /image/")
check(ADDRESS not in overview, "Jellyfin's address does not get into the overview")
check("api_key=" not in overview and "ApiKey" not in overview,
      "no link has the key as a parameter")


print()
print("--- the key cannot be pulled out without signing in either ---")
host = TestClient(app)   # without signing in
for path in ("/", "/settings", "/settings?section=jellyfin"):
    reply = host.get(path, follow_redirects=False)
    check(reply.status_code in (302, 303, 307) and KEY not in reply.text,
          f"whoever is not signed in gets a redirect to /login ({path})")


print()
print("--- a reader (not the administrator) does not get to Jellyfin's settings ---")
accounts.create("reader", "longpassword2", is_admin=False)
reader = TestClient(app)
reader.post("/login", data={"username": "reader", "password": "longpassword2"})
reply = reader.get("/settings?section=jellyfin")
check(KEY not in reply.text, "a reader does not see the key")
reply = reader.post("/settings/connection",
                    data={"jellyfin_url": "http://x", "action": "test"},
                    follow_redirects=False)
check(reply.status_code == 403, "a reader cannot even try the connection")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
