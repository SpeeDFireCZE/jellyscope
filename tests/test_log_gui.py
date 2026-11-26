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
# A connection string's password is masked however long it is, and a long line full of "://x:" without an "@" is
# read once: the pattern used to start over from every "://" (80 KB took 8-11 s on the Log page).
import time  # noqa: E402

check(applog._mask(f"postgres://jelly:{'p' * 400}@db:5432 and ://a:b c@d", []) == "postgres://jelly:***@db:5432 and ://a:b c@d",
      "a long password in a connection string is masked, a value with a space in it is no password")
started = time.perf_counter()
masked = applog._mask("://a:" * 8000 + " " + "://a:" * 8000, [])
took = time.perf_counter() - started
check(masked == "://a:" * 8000 + " " + "://a:" * 8000 and took < 0.5,
      f"a long line full of '://x:' is masked in linear time ({took:.2f} s)")
check("***" in whole, "asterisks are in their place")
# The rest of the row must stay readable - otherwise masking would throw away even what
# a person looks into the log for.
check("na /Items" in whole, "the message's surrounding text stays")

# Every stored secret of the app, not a list of its own beside it: the keys of the *Arr apps and of Seerr were
# missing, so a key of Sonarr in a log line would have shown whole. The secrets are found in the code itself, apart
# from db.SECRET_SETTINGS (the list the masking reads): a new channel's token stored under a key that list lacks
# would otherwise be masked by nothing and checked by nothing.
import ast  # noqa: E402
import re  # noqa: E402

SECRET_NAME = re.compile(r"(?:password|token|webhook|api_key|secret)$")


def named_setting_keys(sources: dict[Path, str]) -> set[str]:
    """The settings keys the code names: a constant handed to get_setting/set_setting, a notification channel's
    field (`notifications.setting_key("smtp", "password")`) and an *Arr app's f"{SETTINGS_PREFIX}_api_key"."""
    trees = {path: ast.parse(text) for path, text in sources.items()}
    prefixes = {path.parent: node.value.value for path, tree in trees.items() for node in tree.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and any(getattr(target, "id", "") == "SETTINGS_PREFIX" for target in node.targets)}
    keys: set[str] = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            name, first = getattr(node.func, "attr", None) or getattr(node.func, "id", ""), node.args[0]
            if name in ("get_setting", "set_setting") and isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
            elif name in ("get_setting", "set_setting") and isinstance(first, ast.JoinedStr):
                parts = [prefixes.get(path.parent) if isinstance(v, ast.FormattedValue) and getattr(v.value, "id", "") == "SETTINGS_PREFIX"
                         else v.value if isinstance(v, ast.Constant) else None for v in first.values]
                keys |= {"".join(parts)} if None not in parts else set()
            elif name == "setting_key" and len(node.args) == 2 and all(isinstance(a, ast.Constant) for a in node.args):
                keys.add(f"notify_{node.args[0].value}_{node.args[1].value}")
    return keys


code = {path: path.read_text(encoding="utf-8") for path in (PROJECT / "jellyscope").rglob("*.py") if "__pycache__" not in path.parts}
stored_secrets = {key for key in named_setting_keys(code) if SECRET_NAME.search(key)}
check(len(stored_secrets) >= 11 and stored_secrets <= set(db.SECRET_SETTINGS),
      f"every secret the code stores is in db.SECRET_SETTINGS ({sorted(stored_secrets - set(db.SECRET_SETTINGS))})")
sample = {PROJECT / "jellyscope" / "arr" / "newarr" / "client.py": 'SETTINGS_PREFIX = "arr_newarr"\n',
          PROJECT / "jellyscope" / "arr" / "newarr" / "live.py": 'db.get_setting(f"{SETTINGS_PREFIX}_api_key")\n',
          PROJECT / "jellyscope" / "notifications.py": 'db.set_setting(setting_key("ntfy", "token"), x)\nget_setting("ui_language")\n'}
check({key for key in named_setting_keys(sample) if SECRET_NAME.search(key)} == {"arr_newarr_api_key", "notify_ntfy_token"},
      "a new channel's token and a new app's key are found in the code")
other_secrets = {key: f"OtherSecret{number:02d}Value" for number, key in enumerate(sorted(stored_secrets))
                 if key != "jellyfin_api_key"}
for key, value in other_secrets.items():
    db.set_setting(key, value)
db.forget_settings()
for value in other_secrets.values():
    logger.error("a library repeated the value %s in its message", value)
whole = "\n".join(r["text"] for r in applog.read_lines(limit=200)["lines"])
shown = [key for key, value in other_secrets.items() if value in whole]
check(not shown, f"no stored secret appears in the listing ({shown})")


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
