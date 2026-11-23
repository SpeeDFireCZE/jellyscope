# -*- coding: utf-8 -*-
r"""Error messages at the first start and at signing in speak the chosen language.

The bug this watches: on the first-start page a person switched
the interface to English, mistyped the password - and got the Czech message
„Heslo musí mít aspoň 8 znaků.“

There are two traps in it and each alone is enough:

  1. The message does not go through `_flash()`, which translates, but straight into the template.
  2. The language chosen in the form **is not saved yet** in the database, so
     a translation "by the settings" would reach for the previous one. It must be translated
     explicitly into the language from the form.

On top, the messages are put together from a template and a value („aspoň {n} znaků“), so
the finished sentence is not in the translation dictionary - the template is translated and the value
is filled in only afterwards. `accounts.AccountError.translated()` can do that.

The order in the test is not random: the first-start page must be tried
before an account comes about in the database - after that it only redirects to
signing in.

Run:
    .venv/bin/python tests/test_translated_messages.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hlasky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, i18n  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
# The settings are Czech - exactly as at the very first start, when
# a person is only picking the language in the form.
db.set_setting("ui_language", "cs")

print("--- the first start: the message in the language from the FORM, not from the settings ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    response = client.post("/setup", data={
        "username": "admin", "password": "krat", "password_again": "krat",
        "ui_language": "en",
    })
    check(response.status_code == 400, f"a short password does not pass ({response.status_code})")
    check("The password must be at least 8 characters." in response.text,
          "and the message comes in English")
    check("Heslo musí mít aspoň" not in response.text,
          "the Czech variant is not in the page")

    response = client.post("/setup", data={
        "username": "admin", "password": "kratke", "password_again": "kratke",
        "ui_language": "cs",
    })
    check("Heslo musí mít aspoň 8 znaků." in response.text,
          "with the Czech choice Czech stays")

    # Only now do we really create the account - the next checks count on it.
    response = client.post("/setup", data={
        "username": "admin", "password": "longpassword",
        "password_again": "longpassword", "ui_language": "cs",
    }, follow_redirects=False)
    check(response.status_code == 303, f"a right password passes ({response.status_code})")


print()
print("--- the message remembers the template and the value ---")
try:
    accounts.validate_password("krat")
    error = None
except accounts.AccountError as exc:
    error = exc

check(error is not None, "a short password is an error")
check(str(error) == "The password must be at least 8 characters.",
      f"the message itself is the English source ({error})")
check(error.translated("cs") == "Heslo musí mít aspoň 8 znaků.",
      f"in Czech with the number filled in ({error.translated('cs')})")
check(error.translated("en") == str(error), "English is the source")
# The number is filled in ONLY AFTER the translation - otherwise the finished sentence would not be
# found in the dictionary and would stay Czech.
check("{n}" not in error.translated("en"), "no placeholder is left in the translated sentence")

try:
    accounts.create("admin", "longpassword")
    error = None
except accounts.AccountError as exc:
    error = exc
check(error is not None and "admin" in error.translated("en"),
      f"the account's name is filled into the English sentence too ({error and error.translated('en')})")


print()
print("--- signing in ---")
check(i18n.translate("Wrong username or password.", "en") == "Wrong username or password.",
      f"a wrong password ({i18n.translate('Wrong username or password.', 'en')})")
check(i18n.translate("Too many attempts. Try again in {n} min.", "en").format(n=5)
      == "Too many attempts. Try again in 5 min.",
      "a lockout with the time filled in")
# The sentence about a permanent lockout is put together from two strings - it easily happens that
# only half gets into the dictionary and the translation is then not found at all.
long_sentence = ("Signing in from this address is blocked. "
                 "An administrator can lift it in Settings.")
check(long_sentence in i18n.EN and i18n.translate(long_sentence, "cs").startswith("Přihlašování z této adresy"),
      "the two-line sentence about a permanent lockout too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
