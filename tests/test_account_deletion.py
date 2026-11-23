# -*- coding: utf-8 -*-
"""Deleting accounts - and above all that at least one administrator always stays.

The rule is simple and breaking it has a single, but unpleasant
consequence: **nobody can get into Jellyscope any more**. The database would then have
to be rescued by a person with access to the server.

It is tested in three places, because each protects something else:

  * `accounts.delete()` - the rule itself. It holds for code that would
    call this function from elsewhere than the web too.
  * the route `/settings/accounts/delete` - adds "not one's own account" and
    checks that only the administrator may delete.
  * the template - the button does not show at all by the last administrator. That is
    convenience, not protection; that stays in accounts.delete().
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# A home of its own: without it DATABASE_PATH would beat the stored choice
# of the database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "ucty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0
PASSWORD = "longpassword"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def sign_in(name: str) -> TestClient:
    client = TestClient(app)
    client.post("/login", data={"username": name, "password": PASSWORD})
    return client


def message(response: Any) -> str:
    m = re.search(r'<div class="flash \w+">.*?<span>([^<]*)</span>', response.text, re.S)
    return " ".join(m.group(1).split()) if m else ""


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

print("--- the rule itself (accounts.delete) ---")
only_one = accounts.create("spravce1", PASSWORD, is_admin=True)
try:
    accounts.delete(only_one)
    check(False, "the last administrator could NOT be deleted")
except accounts.AccountError as error:
    # The last administrator is a local one here, so the stricter rule speaks (one local always stays).
    check("posledního místního správce" in error.translated("cs").lower(),
          f"the last administrator is protected: {error}")

second = accounts.create("spravce2", PASSWORD, is_admin=True)
accounts.delete(only_one)
check(accounts.admin_count() == 1, "with two administrators one can be deleted")
try:
    accounts.delete(second)
    check(False, "and the last one could NOT be deleted")
except accounts.AccountError:
    check(True, "and the last one not again")


print()
print("--- through the interface ---")
admin_id = accounts.create("admin", PASSWORD, is_admin=True)
reader_id = accounts.create("reader", PASSWORD, is_admin=False)
client = sign_in("admin")

before = accounts.count()
response = client.post("/settings/accounts/delete",
                       data={"account_id": reader_id}, follow_redirects=True)
check(accounts.count() == before - 1, f"a reader can be deleted ({message(response)})")

response = client.post("/settings/accounts/delete",
                       data={"account_id": admin_id}, follow_redirects=True)
check(accounts.get(admin_id) is not None, "one's own account cannot be deleted")
check("vlastní účet" in message(response).lower(), f"and it says why: {message(response)}")

# A second administrator: one of them can be deleted while there are two.
colleague = accounts.create("kolega", PASSWORD, is_admin=True)
check(accounts.admin_count() == 3, f"now there are three administrators ({accounts.admin_count()})")
client.post("/settings/accounts/delete", data={"account_id": colleague},
            follow_redirects=True)
check(accounts.get(colleague) is None, "an administrator can be deleted when they do not stay alone")


print()
print("--- who may delete ---")
viewer = accounts.create("viewer", PASSWORD, is_admin=False)
viewer_client = sign_in("viewer")
response = viewer_client.post("/settings/accounts/delete",
                              data={"account_id": viewer}, follow_redirects=False)
check(response.status_code == 403, f"a reader may not delete ({response.status_code})")
check(accounts.get(viewer) is not None, "and the account stayed")

guest = TestClient(app)
response = guest.post("/settings/accounts/delete",
                      data={"account_id": viewer}, follow_redirects=False)
check(response.status_code in (302, 303, 307), "whoever is not signed in does not even get to the form")
check(accounts.get(viewer) is not None, "and the account stayed anyway")


print()
print("--- what is seen on the page ---")
# A single administrator stayed - by them the button is not to show at all.
for account in accounts.all_accounts():
    if account["is_admin"] and account["username"] != "admin":
        accounts.delete(account["id"])
check(accounts.admin_count() == 1, f"one administrator is left ({accounts.admin_count()})")

# The last administrator is always the one signed in - if it were somebody else,
# there would be two administrators. So the explanation belongs to ONE'S OWN row.
html = client.get("/settings?section=accounts").text
admin_row = next(r for r in re.findall(r"<tr>.*?</tr>", html, re.S)
                 if "admin" in r and "(" in r)
check("poslední místní správce" in admin_row,
      "by the last administrator there is an explanation instead of the buttons")
check("/settings/accounts/delete" not in admin_row,
      "and the form for deleting is not there")
check("/settings/accounts/role" not in admin_row,
      "nor for removing the rights")

print()
print("--- one local administrator always stays ---")
# The user, 22. 11. 2026: one local administrator must always stay - the last one cannot be deleted or demoted (local
# accounts have no "disabled" to switch). An account from Jellyfin depends on Jellyfin: when Jellyfin is down, or the
# person is disabled there, only a local account gets into Settings. The rights of an account from Jellyfin are set in
# Jellyfin (they follow it at every users' sync), so not here.
jf_admin = accounts.from_jellyfin({"id": "aaaa0000bbbb1111cccc2222dddd3333", "name": "Jfadmin", "is_admin": True})
jf_viewer = accounts.from_jellyfin({"id": "eeee4444ffff5555aaaa6666bbbb7777", "name": "Jfviewer", "is_admin": False})
check(accounts.admin_count() == 2 and accounts.local_admin_count() == 1, "one local and one Jellyfin administrator")


def refused(action, *args) -> str:
    try:
        action(*args)
    except accounts.AccountError as error:
        return error.translated("en")
    return ""


check("last local administrator" in refused(accounts.delete, admin_id),
      "the last local administrator cannot be deleted, though another administrator (from Jellyfin) exists")
check("last local administrator" in refused(accounts.set_admin, admin_id, False), "nor demoted")
check("Jellyfin" in refused(accounts.set_admin, jf_admin["id"], False)
      and "Jellyfin" in refused(accounts.set_admin, jf_viewer["id"], True),
      "the rights of an account from Jellyfin are not changed here")
second_local = accounts.create("spravce3", PASSWORD, is_admin=True)
accounts.set_admin(second_local, False)
check(not accounts.get(second_local)["is_admin"], "with two local administrators one can be demoted")
accounts.delete(second_local)
check(accounts.get(second_local) is None and refused(accounts.delete, jf_admin["id"]) == ""
      and accounts.get(jf_admin["id"]) is None, "and deleted; an administrator from Jellyfin can be deleted too")

jf_admin = accounts.from_jellyfin({"id": "aaaa0000bbbb1111cccc2222dddd3333", "name": "Jfadmin", "is_admin": True})
html = client.get("/settings?section=accounts").text
rows = {name: next((r for r in re.findall(r"<tr>.*?</tr>", html, re.S) if name in r), "") for name in ("Jfadmin", "Jfviewer")}
check(all("práva z Jellyfinu" in r and "/settings/accounts/role" not in r for r in rows.values()),
      "a row from Jellyfin: no button for the role, its rights come from Jellyfin")
admin_row = next(r for r in re.findall(r"<tr>.*?</tr>", html, re.S) if "admin" in r and "(" in r)
check("poslední místní správce" in admin_row, "one's own row as the last local administrator says so")
check("Není tu žádný místní správce" not in html, "no warning while a local administrator exists")
with db.connect() as conn:       # an older installation: its only local administrator was deleted long ago
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'ffff0000eeee1111dddd2222cccc3333' WHERE id = ?", (admin_id,))
    conn.commit()
check("Není tu žádný místní správce" in client.get("/settings?section=accounts").text,
      "without a local administrator Settings → Accounts warns")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = NULL WHERE id = ?", (admin_id,))
    conn.commit()

# By a reader the button must be there - and red, because it is irreversible.
reader2 = accounts.create("ctenar2", PASSWORD, is_admin=False)
html = client.get("/settings?section=accounts").text
reader_row = next(r for r in re.findall(r"<tr>.*?</tr>", html, re.S)
                  if "ctenar2" in r)
check("/settings/accounts/delete" in reader_row, "by a reader the button is there")
check('class="btn danger"' in reader_row, "and it is red (an irreversible action)")
# The question's text is in the data-confirm attribute (base.js asks before
# submitting). It is read the way the browser will see it: the attribute is unescaped.
import html as _html  # noqa: E402

question = re.search(r'data-confirm="(.*?)"', reader_row, re.S)
question_text = _html.unescape(question.group(1)) if question else ""
check("ctenar2" in question_text and "nejde vzít zpět" in question_text,
      f"with a question that says what will happen ({question_text[:60]!r})")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
