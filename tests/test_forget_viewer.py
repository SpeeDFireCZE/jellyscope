# -*- coding: utf-8 -*-
r"""Forgetting a viewer: it asks, and then deletes exactly what it is to.

Two things, and one of them already cost data once:

1. **A click on the button by itself deletes nothing.** The confirmation used
   to be in a named function called the same as the `<select>` below it. An
   element with an `id` is a property of `window` and it overrode the function
   - the call failed on "is not a function", the browser skipped `onclick` and
   did the default action. The form was sent and the history vanished without
   a single question. The broken safeguard did not behave as broken; it
   behaved as if it were not there.

   So the trigger button is `type="button"` and does not belong to the form:
   **it cannot send, whatever happens to the script**. Opening the window is
   the only thing it can do; only the confirmation inside it can send. This is
   the essential part and the test guards it first.

2. **It deletes only that one.** The other viewers must not move and the
   account itself stays: it is in Jellyfin, it cannot be deleted from here,
   and the next sync would bring it back anyway.

Run:
    .venv/bin/python tests/test_forget_viewer.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "forgettings.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, purge  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


def viewer(user_id: str, name: str, count: int, active: int = 0) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)",
            (user_id, name, db.utcnow()))
        for index in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,?)",
                (f"{user_id}-{index}", user_id, name, f"item-{index}",
                 "2026-01-01 10:00:00", "2026-01-01 10:20:00", 1200,
                 active if index == 0 else 0))
        conn.commit()


viewer("u-mirek", "Mirek", 5, active=1)
viewer("u-jana", "Jana", 3)

print("--- only that one is deleted ---")
result = purge.forget_user("u-mirek")
check(result["deleted"] == 5, f"it returned the number deleted ({result['deleted']})")
check(result["name"] == "Mirek", f"and the name ({result['name']!r})")

left = db.query_value(
    "SELECT COUNT(*) FROM playback WHERE user_id = ?", ("u-mirek",))
check(left == 0, f"no record remained after Mirek ({left})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE user_id = ?",
                     ("u-jana",)) == 3, "Jana did not move")

# Even a playback running right now goes - otherwise a record would stay after
# the forgotten viewer that is written again in a moment.
check(db.query_value("SELECT COUNT(*) FROM playback WHERE is_active = 1") == 0,
      "the playback running right now vanished too")

# The account stays: it is in Jellyfin and the next sync would bring it back.
check(db.query_value("SELECT COUNT(*) FROM users WHERE id = ?",
                     ("u-mirek",)) == 1, "the account stayed in the list")

print()
print("--- who is not there cannot be forgotten ---")
empty = purge.forget_user("u-neexistuje")
check(empty["deleted"] == 0, "an unknown viewer deletes nothing")
empty = purge.forget_user("")
check(empty["deleted"] == 0, "and an empty id does not either")
check(db.query_value("SELECT COUNT(*) FROM playback") == 3,
      "nothing happened to the others meanwhile")

print()
print("--- it asks in its own window, and a click by itself deletes nothing ---")
# The Settings sections are in `templates/settings/` after the split, so the
# file is not looked for by name - the one the button really is in is looked
# for. The test thus survives moving the text into another file, but does not
# read foreign templates meanwhile.
#
# Gluing all templates into one string would work too, but it is needlessly
# risky: the expression below looks for a red button before the label
# "Forget", and in one big string it could start at a foreign button and
# read its attributes. One file closes that possibility.
template = ""
for _path in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    _text = _path.read_text(encoding="utf-8")
    if '_("Forget")' in _text:
        template = _text
        break
check(bool(template), "the template with the \"Forget\" button was found")

# Since the rebuild for CSP without inline code the handler is in
# static/js/app.js (the button carries data-action), not in onclick= in the tag.
trigger = re.search(
    r'<button class="btn danger"([^>]*?)data-action="forget-viewer"\s*>\s*'
    r'\{\{ _\("Forget"\) \}\}', template, re.S)
check(trigger is not None, "the trigger button was found")
scripts = (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8")
i_action = scripts.find('[data-action="forget-viewer"]')
check(i_action != -1, "and its handler is in app.js")
if trigger:
    attributes, handler = trigger.group(1), scripts[i_action:i_action + 600]
    # This is the essential safeguard: a button that cannot send cannot delete
    # anything on a script error. It used to be able to - and it deleted.
    check('type="button"' in attributes,
          "it is type=button, so it sends nothing by itself")
    check('form="forget"' not in attributes,
          "and does not belong to the form")
    check("showModal()" in handler, "it opens its own window")

window = re.search(r'<dialog id="modal-forget".*?</dialog>', template, re.S)
check(window is not None, "the window with the question is in the template")
if window:
    text = window.group(0)
    check('id="whom-to-forget"' in text, "the window names the concrete viewer")
    check('form="forget"' in text, "the confirmation in the window sends the form")
    check('method="dialog"' in text, "and it can be closed without consequence")
    # After opening the focus stands on Cancel - Enter out of habit deletes nothing.
    check(text.index("autofocus") < text.index('form="forget"'),
          "the focus stands on Cancel, not on deleting")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
