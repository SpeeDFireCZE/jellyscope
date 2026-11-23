# -*- coding: utf-8 -*-
r"""The places CodeQL marked as a hole - and what really happens in them.

The static analysis reports that a value from the user flows into a path to a file and into
a redirect's address. It is true and it is also all right: both pass a check
CodeQL does not see, because it goes through another function.

That does not make the finding harmless for ever though. Were that check ever
to go, the static analysis would turn out right. That is why this test does not check
the code by eye, but **by an attack**: it tries exactly what the report fears.

What is watched and why CodeQL does not recognise it:

* `py/path-injection` at downloading a backup - the file's name is checked
  in `tasks.backup_file()`, so one function further;
* `py/url-redirection` at starting an analysis - the library's id is used only
  when such a library exists in the database (`stats.library()`);
* `py/clear-text-logging` at changing a setting - the value is written only
  for a setting we know to be harmless.

Run:
    .venv/bin/python tests/test_codeql_findings.py
"""
from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "nalezy.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id,name,collection_type,paths,"
                 "synced_at) VALUES ('lib-1','Filmy','movies','[]',?)",
                 (db.utcnow(),))
    conn.commit()

client = TestClient(web.app, follow_redirects=False)
client.post("/login", data={"username": "admin",
                            "password": "longpassword"})

print("--- downloading a backup lets out only a backup ---")
backups = Path(_tmp) / "backups"
backups.mkdir(exist_ok=True)
(backups / "jellyscope-2026-09-09.db").write_text("poctiva zaloha",
                                                  encoding="utf-8")
# One floor up something that must not get out. Two files on purpose:
#
#   `tajne.env`          - an ordinary path; the condition on the name's shape refuses it
#                          already, so it says nothing about the check of slashes.
#   `jellyscope-tajne.db`- is named **exactly the way a backup is to be
#                          named**. This one passes everything else and only the check
#                          that the name is not a path stops it. Without it the
#                          test would not try that check at all - checked by
#                          switching it off and the test not noticing.
(Path(_tmp) / "tajne.env").write_text("SECRET_KEY=tohle-se-ven-nesmi",
                                      encoding="utf-8")
(Path(_tmp) / "jellyscope-tajne.db").write_text(
    "tohle-se-ven-nesmi-dostat-taky", encoding="utf-8")
db.set_setting("backup_path", str(backups))
db.forget_settings()

response = client.get("/settings/backup/download",
                      params={"name": "jellyscope-2026-09-09.db"})
check(response.status_code == 200 and "poctiva" in response.text,
      "an honest backup downloads")

OUTSIDE_FOLDER = [
    # These are stopped by the condition on the name's shape already.
    "../tajne.env", "..\\tajne.env", "../../tajne.env",
    "jellyscope-../tajne.env", "/etc/passwd", "C:\\Windows\\win.ini",
    "tajne.env", "....//tajne.env", str(Path(_tmp) / "tajne.env"),
    # And these pass everything except the check that the name is not a path.
    # They must **start** with "jellyscope-" and end with ".db", otherwise
    # the condition on the name's shape refuses them already and says nothing about the check of slashes -
    # exactly by that this test used to behave blindly, passing.
    "jellyscope-x/../../jellyscope-tajne.db",
    "jellyscope-x\\..\\..\\jellyscope-tajne.db",
    "jellyscope-2026-09-09.db/../../jellyscope-tajne.db",
    "../jellyscope-tajne.db",
    str(Path(_tmp) / "jellyscope-tajne.db"),
]
passed = [u for u in OUTSIDE_FOLDER
          if "nesmi" in client.get("/settings/backup/download",
                                   params={"name": u}).text]
check(not passed, f"nothing gets out of the folder ({passed[:2]})")

print()
print("--- starting an analysis brings a person back only to us ---")
# `library_id` from the form gets into the address only when such a library
# really exists.
#
# Mind what this part really proves: the safety here is not made by that
# check of existence, but by **the fixed prefix** `/library/`. Even were the
# check switched off, the result would still be a path on our server - checked
# by switching it off and nothing happening. The check of existence is here so that
# the link leads somewhere, not for safety.
#
# This part therefore watches the result: whatever is sent in `library_id`,
# a person ends up with us. That is what matters.
REDIRECTS = ["//utocnik.cz", "https://utocnik.cz", "/\\utocnik.cz",
             "\\\\utocnik.cz", "javascript:alert(1)",
             "lib-1/../../https://utocnik.cz", "neexistujici"]
redirected = []
for attack in REDIRECTS:
    target = client.post("/settings/scan",
                         data={"mode": "missing", "library_id": attack}
                         ).headers.get("location", "")
    if target.startswith(("http://", "https://", "//", "\\\\", "/\\")):
        redirected.append((attack, target))
check(not redirected, f"a foreign address does not pass ({redirected[:2]})")

target = client.post("/settings/scan",
                     data={"mode": "missing", "library_id": "lib-1"}
                     ).headers.get("location", "")
check(target.startswith("/library/lib-1"),
      f"and to an existing library a person comes back ({target})")

print()
print("--- a setting's value goes into the log only where it is harmless ---")
captured = io.StringIO()
handler = logging.StreamHandler(captured)
handler.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("jellyscope.db")
logger.addHandler(handler)
level = logger.level
logger.setLevel(logging.INFO)
try:
    db.set_setting("jellyfin_api_key", "TAJNY-KLIC-Z-TESTU")
    db.set_setting("notify_smtp_heslo", "secretpass-from-test")
    db.set_setting("notify_smtp_komu", "petr@example.org")
    db.set_setting("poll_interval", "42")
finally:
    logger.removeHandler(handler)
    logger.setLevel(level)

written = captured.getvalue()
for secret in ("TAJNY-KLIC-Z-TESTU", "secretpass-from-test",
               "petr@example.org"):
    check(secret not in written, f"{secret[:22]!r} is not in the log")
check("jellyfin_api_key" in written, "but that it changed is in the log")
check("poll_interval: 10 -> 42" in written,
      "and a harmless setting is written out with its value")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
