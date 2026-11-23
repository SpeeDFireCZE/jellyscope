# -*- coding: utf-8 -*-
"""The log viewer in Settings.

The log is the only place in the app where secrets can appear - all sorts of things get into
an error message, an API key or the database password included. Making it accessible
in the browser therefore means watching three things:

  * **Only the administrator sees it.** A reader must not get into the section even by a direct
    address.
  * **Secrets are masked.** It still holds that the API key cannot be got
    through the interface - see the same principle for the form in the Jellyfin section.
  * **A file name from the address is not a path.** `?log_file=../../.env` is a
    "name" too and must give nothing out.

Run:
    .venv/bin/python tests/test_log_gui.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice of the
# database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "log.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import accounts, applog, db  # noqa: E402

failures = 0

KEY = "SUPERTAJNYKLIC123456"
DB_PASSWORD = "MojeHesloKDatabazi42"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "secretpass1", is_admin=True)
accounts.create("reader", "readerpass1", is_admin=False)
db.set_setting("jellyfin_url", "http://media.doma:8096")
db.set_setting("jellyfin_api_key", KEY)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402


print("--- the log is written into a file ---")
path = applog.setup()
check(path is not None and path.exists(), f"the file came about ({path})")
check(path.parent.name == "logs" and path.parent.parent.name == "data",
      f"it lies in data/logs ({path})")
# The home is respected: otherwise the test would write into the live log.
check(str(path).startswith(_tmp), "and it follows JELLYSCOPE_HOME")

logger = logging.getLogger("jellyscope.test")
logger.info("bezna hlaska o prubehu")
logger.warning("neco se nepovedlo")
logger.error("chyba pri synchronizaci")

listing = applog.read_lines(limit=100)
texts = [r["text"] for r in listing["lines"]]
check(any("bezna hlaska" in t for t in texts),
      "messages at the INFO level are written too")
check(any("chyba pri synchronizaci" in t for t in texts), "and errors as well")
check(any(r["level"] == "ERROR" for r in listing["lines"]),
      "a row's level is recognised (because of the colour and the filter)")

# The filter by level must hide everything else.
errors_only = applog.read_lines(limit=100, level="ERROR")
check(errors_only["lines"] and all(r["level"] == "ERROR" for r in errors_only["lines"]),
      f"the filter for ERROR lets through only errors ({len(errors_only['lines'])})")


print()
print("--- secrets are masked in the listing ---")
logger.error("Jellyfin odmitl klic: api_key=%s na /Items", KEY)
logger.error("token: \"%s\"", KEY)
logger.error("psycopg: connection to postgres://jelly:%s@db:5432 failed", DB_PASSWORD)
logger.error("holy klic v hlasce: %s", KEY)

whole = "\n".join(r["text"] for r in applog.read_lines(limit=200)["lines"])
check(KEY not in whole, "the API key does not appear in the listing")
check(DB_PASSWORD not in whole, "nor the database password")
check("***" in whole, "asterisks are in their place")
# The rest of the row must stay readable - otherwise masking would throw away even what
# a person looks into the log for.
check("na /Items" in whole, "the message's surrounding text stays")


print()
print("--- in the interface only the administrator sees it ---")
with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "secretpass1"})
    response = client.get("/settings?section=log")
    check(response.status_code == 200, f"/settings?section=log -> {response.status_code}")
    page = response.text
    check("Log aplikace" in page, "the section is drawn")
    check("chyba pri synchronizaci" in page, "and the log's rows are in it")
    check("jellyscope.log" in page, "it is seen which file is read")

    # This is the same principle as for the form in the Jellyfin section: the key must not
    # get into the page even by a detour through the log.
    check(KEY not in page, "the API key does not get into the page even through the log")
    check(DB_PASSWORD not in page, "nor the database password")

    # The choices are in the address, so a link can be sent and the back button works.
    response = client.get("/settings?section=log&log_level=ERROR&log_lines=100")
    check(response.status_code == 200, "the filter through the address works")
    check("bezna hlaska" not in response.text,
          "and the INFO rows disappear with the filter for ERROR")

    print()
    print("--- nonsense from the address breaks nothing ---")
    for address in (
        "/settings?section=log&log_lines=0",
        "/settings?section=log&log_lines=-5",
        "/settings?section=log&log_lines=999999",
        "/settings?section=log&log_level=VYMYSLENA",
        "/settings?section=log&log_file=neexistuje.log",
    ):
        response = client.get(address)
        check(response.status_code == 200, f"{address.split('&', 1)[1]} -> 200")

    print()
    print("--- a file name from the address is not a path ---")
    # Were the name used as a path, any file on the disk could be read with this.
    # That is why only the bare name is taken and it is checked on top
    # that such a file really is in the folder with the logs.
    secret_file = Path(_tmp) / ".env"
    secret_file.write_text("SECRET_KEY=nedostupne-tajemstvi\n", encoding="utf-8")
    for attempt in ("../../.env", "..\\..\\.env", "/etc/passwd", "../log.db"):
        result = applog.read_lines(name=attempt, limit=50)
        check(not result["lines"] and not result["exists"],
              f"{attempt!r} gives nothing out")
        response = client.get("/settings?section=log", params={"log_file": attempt})
        check(response.status_code == 200 and "nedostupne-tajemstvi" not in response.text,
              f"{attempt!r} not through the interface either")

    print()
    print("--- a reader does not get into the section ---")
    client.post("/logout")
    client.post("/login", data={"username": "reader", "password": "readerpass1"})
    response = client.get("/settings?section=log")
    check(response.status_code == 403, "a reader does not get into Settings")
    check("Log aplikace" not in response.text,
          "and does not see the Log section even by a direct address")
    check("chyba pri synchronizaci" not in response.text, "and no rows of the log")
    check('section=log' not in response.text, "the signpost does not have it")


print()
print("--- the end of the file is read, not the whole ---")
# With a large log reading the whole file into memory would be a waste.
big = applog.log_dir() / "velky.log"
with big.open("w", encoding="utf-8") as file:
    for number in range(50_000):
        file.write(f"2026-01-01 00:00:00,000 INFO    test: radek cislo {number}\n")

listing = applog.read_lines(name="velky.log", limit=100)
check(len(listing["lines"]) == 100, f"exactly 100 rows come back ({len(listing['lines'])})")
check("radek cislo 49999" in listing["lines"][-1]["text"],
      f"and it is the end of the file ({listing['lines'][-1]['text'][-25:]})")
# A torn first row is thrown away - half a message confuses more than nothing.
check(listing["lines"][0]["text"].startswith("2026-01-01"),
      f"the first row is not torn in the middle ({listing['lines'][0]['text'][:30]})")

check(any(s["name"] == "velky.log" for s in applog.available_files()),
      "a new file appears in the offer by itself")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
