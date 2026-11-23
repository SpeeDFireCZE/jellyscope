# -*- coding: utf-8 -*-
r"""Messages after a finished action speak the language a person has set.

The bug this watches: after an import or after a task finished, the English
interface showed the Czech sentence „Jellystat: naimportováno 42 záznamů…“.

The reason was in how those messages were put together. `_flash()` can translate, but
it was given **a finished sentence** put together by an f-string - and a finished sentence with a number
inside is not in the translation dictionary and never will be. Only the template
can be translated, the values are filled into it only afterwards.

The check has two parts:

  1. **The mechanics** - `_flash()` really translates the template and only then
     fills in the values.
  2. **Completeness** - every template used in `web/` has
     an English counterpart. This is the part that watches the future:
     the test reveals a new message without a translation before anybody sees it.

Run:
    .venv/bin/python tests/test_action_messages.py
"""
from __future__ import annotations

import ast
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

from jellyscope import db, i18n  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


print("--- the template is translated, only then a value is filled in ---")
template = ("Jellystat: imported {n} records "
            "(out of {found} found, {duplicates} already existed).")
english = i18n.translate_with(template, "en", n=42, found=50, duplicates=8)    # a count: its form by n
check(english == "Jellystat: imported 42 records "
                 "(out of 50 found, 8 already existed).",
      f"import ({english})")
check("{" not in english, "no placeholder is left in the finished sentence")

czech = i18n.translate_with(template, "cs", n=42, found=50, duplicates=8)
check(czech.startswith("Jellystat: naimportováno 42 záznamů"),
      f"Czech is a translation of the English key ({czech})")


print()
print("--- _flash: translating and filling in at once ---")

from jellyscope import web  # noqa: E402


class FakeRequest:
    """The request needs only a session - _flash uses nothing else."""

    def __init__(self) -> None:
        self.session: dict[str, object] = {}


db.set_setting("ui_language", "en")
i18n.forget_language() if hasattr(i18n, "forget_language") else None
db.forget_settings()

request = FakeRequest()
web._flash(request, "Backup {name} deleted.", "success", name="jellyscope-2026.db")
message = request.session["flash"]["message"]
check(message == "Backup jellyscope-2026.db deleted.", f"in English ({message})")

db.set_setting("ui_language", "cs")
db.forget_settings()
request = FakeRequest()
web._flash(request, "Backup {name} deleted.", "success", name="jellyscope-2026.db")
check(request.session["flash"]["message"] == "Záloha jellyscope-2026.db smazána.",
      "and in Czech still too")

# The message after an import, through the route's own helper: every sentence of it is a count
# with its forms, in the server's language - a plain lookup of a count's key found nothing and
# wrote the English key on a Czech page.
from jellyscope.web import base as web_base  # noqa: E402

for language, expected in (("cs", ("Jellystat: naimportováno 42 záznamů", "3 starší záznamy se přenesly",
                                   "1 záznam už v databázi byl")),
                           ("en", ("Jellystat: imported 42 records", "3 older records were moved",
                                   "1 record was already in the database"))):
    db.set_setting("ui_language", language)
    db.forget_settings()
    request = FakeRequest()
    web_base._import_message(request, {"status": "ok", "imported": 42, "found": 50, "duplicate": 8, "repaired": 3,
                                       "known_elsewhere": 1},
                             "Jellystat: imported {n} records (out of {found} found, {duplicates} already existed).")
    text = request.session["flash"]["message"]
    check(all(part in text for part in expected), f"the import message in {language}: {text!r}")
db.set_setting("ui_language", "cs")
db.forget_settings()

# A wrong placeholder in a template must not bring the page down - the action's result is
# more important than a perfect sentence.
request = FakeRequest()
web._flash(request, "Něco s {chybnou} značkou", "info", jina=1)
check("flash" in request.session, "a typo in a placeholder does not throw the message away")


print()
print("--- every message has an English counterpart ---")
# The messages are in all the web's files, not just in one - Settings
# has a module of its own. Reading only one would mean the test stops seeing
# most of the messages and its silence no longer says anything.
trees = [ast.parse(p.read_text(encoding="utf-8"))
         for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py"))]
templates: set[str] = set()
f_strings: list[int] = []
for node in [u for s in trees for u in ast.walk(s)]:
    if not (isinstance(node, ast.Call)
            and getattr(node.func, "id", "") in ("_flash", "_t")):
        continue
    if node.func.id == "_flash":
        arg = node.args[1] if len(node.args) >= 2 else None
    else:
        arg = node.args[0] if node.args else None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        templates.add(arg.value)
    elif isinstance(arg, ast.JoinedStr):
        # An f-string: a finished sentence with the values filled in. It cannot be
        # translated and will never be in the dictionary - exactly the bug this
        # test came about for. Such a node used to be skipped quietly, so the
        # test was silent about exactly what it is to watch.
        f_strings.append(node.lineno)

check(not f_strings,
      f"no message is an f-string; lines {f_strings}")

missing = sorted(s for s in templates if s not in i18n.EN and not i18n.is_plural(s))    # a count has its forms
check(len(templates) > 40, f"the check really went through the messages ({len(templates)})")
check(not missing, f"all have a translation; missing {len(missing)}: {missing[:3]}")

# The placeholders in a translation must match the original - otherwise filling in falls over
# exactly in the language the other person speaks.
import re  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")
mismatched = []
for template in templates:
    translation = i18n.EN.get(template)
    if translation and set(PLACEHOLDER.findall(template)) != set(PLACEHOLDER.findall(translation)):
        mismatched.append(template)
check(not mismatched, f"and the same placeholders as the original; mismatched: {mismatched[:3]}")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
