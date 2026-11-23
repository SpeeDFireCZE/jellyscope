# -*- coding: utf-8 -*-
r"""One account's own rights beat the group's settings.

The group (reader accounts, viewers from Jellyfin) is the default. When the administrator
switches "custom" on for one account, only what is ticked for it holds
- and the group stops being read for it entirely. Going back to the group
throws the own tree away.

Also watched here: the menu shows only links that can be opened, and
a broken JSON on an account does not close everything to a person, but falls back to the group.

Run:
    .venv/bin/python tests/test_account_rights.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "prava.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from _package import sample_movie  # noqa: E402
from jellyscope import access, accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()          # the Movies & series section exists only with a movie (navigation.has_video)
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword", is_admin=False)
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u-petr', 'PetrZQX', 0, 0)")
    conn.commit()

# The readers' group: the network off, the overview on, without anonymising.
db.set_setting("reader_sees_network", "0")
db.set_setting("reader_sees_overview", "1")
db.set_setting("reader_anonymise", "0")
db.forget_settings()


def sign_in(name: str) -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": name, "password": "longpassword",
                           "method": "local"})
    return k


def status(k: TestClient, path: str) -> int:
    return k.get(path, follow_redirects=False).status_code


# Whoever may not open the Overview is not let into / : it sends them (303) to the first page they
# may open, instead of "you have no access here" - signing in aims at / too.
OVERVIEW_CLOSED = 303


def menu(k: TestClient) -> str:
    html = k.get("/video/insights").text
    start = html.index('<nav class="nav">')
    return html[start:html.index("</nav>", start)]


reader_id = accounts.get_by_name("reader")["id"]

print("--- without own rights the group holds ---")
reader = sign_in("reader")
check(status(reader, "/video/overview") == 200, "it opens the overview (the group has it)")
check(status(reader, "/network") == 403, "not the network (the group does not have it)")
check('href="/network"' not in menu(reader), "and Network is not in the menu")
check('href="/video/overview"' in menu(reader), "the Overview is in the menu")
check('href="/settings"' not in menu(reader), "a reader does not have Settings in the menu")
check("PetrZQX" in reader.get("/users").text, "it sees the names (the group does not anonymise)")

print()
print("--- the administrator gives the account own rights: the network yes, the overview no, anonymising ---")
admin = sign_in("admin")
response = admin.post("/settings/accounts/permissions", data={
    "account_id": str(reader_id), "mode": "custom",
    "sees_network": "on", "sees_users": "on", "sees_charts": "on",
    "anonymise": "on",
}, follow_redirects=False)
check(response.status_code == 303, f"saving passed ({response.status_code})")
account = accounts.get(reader_id)
check(access.own_permissions(account) is not None, "the account has own rights")

check(status(reader, "/network") == 200, "it opens the network now")
check(status(reader, "/") == OVERVIEW_CLOSED, "the overview no longer - the own rights do not have it")
check('href="/network"' in menu(reader), "Network appeared in the menu")
check('href="/"' not in menu(reader), "the Overview disappeared from the menu")
users_page = reader.get("/users").text
check("PetrZQX" not in users_page and "Divák" in users_page,
      "the names are hidden (own anonymising)")

print()
print("--- a change of the group does not concern an account with own rights ---")
db.set_setting("reader_sees_network", "1")
db.set_setting("reader_sees_overview", "0")
db.forget_settings()
check(status(reader, "/network") == 200 and status(reader, "/") == OVERVIEW_CLOSED,
      "the account still behaves by its own")
other_id = accounts.create("druhy", "longpassword", is_admin=False)
second = sign_in("druhy")
check(status(second, "/") == OVERVIEW_CLOSED and status(second, "/network") == 200,
      "while an account without own rights goes with the group")

print()
print("--- back to the group ---")
admin.post("/settings/accounts/permissions", data={
    "account_id": str(reader_id), "mode": "group",
    "sees_overview": "on",     # a tick is ignored - the mode is group
})
check(access.own_permissions(accounts.get(reader_id)) is None,
      "the own rights are gone")
check(status(reader, "/") == OVERVIEW_CLOSED and status(reader, "/network") == 200,
      "and the group holds again (with its state changed in the meantime too)")

print()
print("--- who may not do what ---")
admin_id = accounts.get_by_name("admin")["id"]
admin.post("/settings/accounts/permissions", data={
    "account_id": str(admin_id), "mode": "custom"})
check(access.own_permissions(accounts.get(admin_id)) is None,
      "own rights are not written for the administrator - they see everything anyway")
response = reader.post("/settings/accounts/permissions", data={
    "account_id": str(reader_id), "mode": "custom", "sees_overview": "on"},
    follow_redirects=False)
check(response.status_code == 403, f"a reader does not set rights for themselves ({response.status_code})")
check(access.own_permissions(accounts.get(reader_id)) is None, "and nothing was written")

print()
print("--- a broken record = the group, not a closed door ---")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET permissions = '{nesmysl' WHERE id = ?",
                 (reader_id,))
    conn.commit()
check(access.own_permissions(accounts.get(reader_id)) is None,
      "a broken JSON is taken as “no own rights”")
check(status(reader, "/network") == 200, "and the person behaves by the group")

print()
print("--- the tree in Settings shows what holds for the account ---")
accounts.save_permissions(reader_id, access.pack_permissions(["languages"], False))
page = admin.get("/settings?section=accounts").text
check(f'id="modal-permissions-{reader_id}"' in page, "the account has a dialog with the rights")
check(f'id="modal-permissions-{admin_id}"' not in page, "the administrator not")
check("vlastní práva" in page, "the account shows the badge “vlastní práva”")
start = page.index(f'id="modal-permissions-{reader_id}"')
dialog = page[start:page.index("</dialog>", start)]


def ticked(html: str, name: str) -> bool:
    """Is the tick box with this name ticked?"""
    match = re.search(r'<input[^>]*name="' + name + r'"[^>]*>', html)
    return bool(match and "checked" in match.group(0))


check(ticked(dialog, "sees_languages"), "what the account has of its own is ticked")
check(not ticked(dialog, "sees_network"), "and nothing the group has and the account not")
check(bool(re.search(r'<input[^>]*value="custom"[^>]*checked', dialog)),
      "the switch stands at “custom”")


# The tree with labels is longer than a laptop's screen. Because of
# the suggestions the dialog has `overflow: visible`, so without a scroll of its own the content
# ran out below the dialog's edge. Only the content scrolls, the Save button stays
# seen at the bottom.
dialog_start = page.rindex("<dialog", 0, start)
check("modal-scrolling" in page[dialog_start:start],
      "the Rights dialog has scrolling content (modal-scrolling)")
body = re.search(r'<div class="modal-body">(.*?)<div class="modal-foot">', dialog, re.S)
check(bool(body) and 'name="sees_languages"' in body.group(1)
      and 'name="may_have_key"' in body.group(1),
      "the tree and the key's choice are in the scrolling part")
check(bool(re.search(r'<div class="modal-foot">\s*<button[^>]*type="submit"', dialog)),
      "the Save button is in the foot, outside the scroll")

# The rights of viewers from Jellyfin are set by Jellyfin (beside signing in),
# not here - the Accounts page must say so and link there.
card_start = page.find('id="readers"')
card_end = page.find('<div class="card"', card_start + 1)
readers_card = page[card_start:card_end if card_end > 0 else len(page)]
check('href="/settings?section=jellyfin#viewers"' in readers_card,
      "the group's card in Accounts links to the viewers' rights in Settings → Jellyfin")
# The information must not get lost in the text: it is in an information box and the link is
# a button.
box = re.search(r'<div class="flash info[^"]*"[^>]*>(.*?)</div>', readers_card, re.S)
check(bool(box) and re.search(r'<a class="btn"[^>]*href="/settings\?section=jellyfin#viewers"',
                              box.group(1)) is not None,
      "it is in an information box and the link is a button")
check('id="viewers"' in admin.get("/settings?section=jellyfin").text,
      "and the link's target (the #viewers card in the Jellyfin section) exists")

print()
print("--- without an account no area is seen ---")
# A summary without signing in (an e-mail, an export) must not get cards from areas
# that anybody has switched off - where the account is missing, closed holds.
from jellyscope import cards  # noqa: E402

check(not any(access.account_sees(None, o.key) for o in access.AREAS), "account_sees(None, …) is always no")
check(cards.allowed(list(cards.ALL_CARDS), None) == [], "without an account no dashboard card")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
