# -*- coding: utf-8 -*-
r"""The messages in Notifications have something to say - and are not empty.

The cards *Where to send* and *What to speak up about* stored the message in the session as
bare text, but the page reads `flash.message` and `flash.level` from it.
The result: after saving an empty box popped up without a single word, so
it looked like an error without an explanation.

The messages must therefore go through `_flash()` - it stores the text and the level
and does the translation at once, not only in the template.

On top, one more trap: a switched-on channel with details not filled in was saved
silently. A person then waits for notifications that have no way to leave. Today
saving says so.

Run:
    .venv/bin/python tests/test_notification_messages.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hlasky.db")
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
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword"})


def flash_box(path: str, data: dict) -> tuple[str, str]:
    """(level, text) of the message after sending the form."""
    response = client.post(path, data=data, follow_redirects=True)
    match = re.search(r'class="flash ([^"]*)">.*?<span>(.*?)</span>',
                      response.text, re.S)
    if not match:
        return ("", "")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(2))).strip()
    return (match.group(1).strip(), text)


print("--- an empty save says what happened ---")
level, text = flash_box("/settings/notifications", {})
check(bool(text), f"where to send: the box is not empty ({text!r})")
check(level == "success", f"and it is a confirmation, not an error ({level!r})")

level, text = flash_box("/settings/notifications/events", {})
check(bool(text), f"what to speak up about: the box is not empty ({text!r})")
check(level == "success", f"and it is a confirmation ({level!r})")

print()
print("--- a switched-on channel not filled in is not saved silently ---")
level, text = flash_box("/settings/notifications",
                        {"smtp_enabled": "on", "telegram_enabled": "on"})
check(level == "warning", f"it is a warning ({level!r})")
check("E-mail" in text and "Telegram" in text,
      f"and it names what is unfinished ({text!r})")
check("smtp" not in text.lower(),
      "the channel is called by its name from the settings, not by the protocol's name")

level, text = flash_box("/settings/notifications", {
    "discord_enabled": "on",
    "discord_webhook": "https://discord.com/api/webhooks/1/x"})
check(level == "success", f"a filled-in channel is all right ({level!r}, {text!r})")

print()
print("--- a test message ---")
level, text = flash_box("/settings/notifications/test", {"channel": "nesmysl"})
check(level == "error" and bool(text), f"an unknown channel = an error ({text!r})")
level, text = flash_box("/settings/notifications/test", {"channel": "smtp"})
check(level == "error" and "Nepodařilo" in text,
      f"a channel not filled in says why ({text!r})")

print()
print("--- and bare text is stored nowhere ---")
# Exactly this was the cause of the empty box: session["flash"] = "text".
# The routes of the whole web - without base.py, where _flash() itself lives (it may write
# into the session, it is the only place).
source = "".join(
    p.read_text(encoding="utf-8")
    for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py")) if p.name != "base.py")
check('session["flash"] =' not in source,
      "the messages go through _flash(), not straight into the session")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
