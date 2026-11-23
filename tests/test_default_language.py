# -*- coding: utf-8 -*-
r"""A new installation speaks English; an upgraded one keeps its language.

English is the source of the texts and the language of a new installation (the
interface and the log). An installation that exists already has its choice stored -
init_db fills in only the settings that are missing - so an upgrade switches nobody's
language. An account without a language of its own follows the server.

Run:
    .venv/bin/python tests/test_default_language.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
HOME = tempfile.mkdtemp()
os.environ.update(JELLYSCOPE_HOME=HOME, DATABASE_PATH=str(Path(HOME) / "d.db"), SECRET_KEY="default-language",
                  JELLYFIN_URL="http://127.0.0.1:1", JELLYFIN_API_KEY="x", JELLYSCOPE_DEMO="0")

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, i18n, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- a new installation ---")
db.init_db()
db.forget_settings()
accounts.create("default_admin", "default-language-pw", is_admin=True)    # the sign-in page, not the first run
check(i18n.DEFAULT_LANGUAGE == "en", f"the default language is English ({i18n.DEFAULT_LANGUAGE})")
check(db.get_setting("ui_language") == "en", f"a new database: the interface in English ({db.get_setting('ui_language')})")
check(db.get_setting("log_language", "") == "", "and no log language - the log file is always English")
login_page = TestClient(web.app).get("/login").text
check("Sign in" in login_page and "Přihlásit" not in login_page, "the sign-in page is English")

print()
print("--- an upgraded installation keeps its choice ---")
db.set_setting("ui_language", "cs")
db.init_db()                                  # the start of the new version over the old database
db.forget_settings()
check(db.get_setting("ui_language") == "cs", "a database with Czech stored stays Czech after init_db")
check("Přihlásit" in TestClient(web.app).get("/login").text, "and its sign-in page is Czech")

client = TestClient(web.app)
client.post("/login", data={"username": "default_admin", "password": "default-language-pw", "method": "local"})
check("Odhlásit" in client.get("/account").text, "an account without a language of its own follows the Czech server")

print()
print("--- a value nobody offers ---")
answer = client.post("/settings/language", data={"ui_language": "klingon", "app_timezone": ""},
                     follow_redirects=False)
db.forget_settings()
check(answer.status_code == 303 and db.get_setting("ui_language") == "en",
      f"an unknown language from the form becomes English ({db.get_setting('ui_language')})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
