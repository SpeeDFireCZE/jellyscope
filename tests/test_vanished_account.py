# -*- coding: utf-8 -*-
r"""An account from Jellyfin disappears when the person disappears in Jellyfin.

A viewer's account is made by itself at the first sign-in with a Jellyfin password. When
the administrator removes that person in Jellyfin, a row that leads nowhere stayed in
Settings → Accounts - and if that person came back under the same id,
they would inherit it with the rights too. Now the sync removes it.

**Only the account** is removed, so only the sign-in. The row in `users`, the history
and the statistics stay: who watched what does not change by removing the account.

Run:
    .venv/bin/python tests/test_vanished_account.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "ucty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, scanner, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


class FakeClient:
    """Only `users()` - the users' sync needs no more."""

    def __init__(self, user_list):
        self.user_list = user_list

    async def users(self):
        return self.user_list


def sync(user_list) -> int:
    return asyncio.run(scanner._sync_users(FakeClient(user_list)))


def account(name: str):
    return db.query_one("SELECT * FROM accounts WHERE username = ?", (name,))


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword", is_admin=False)

# Jellyfin's Policy says both - a value it does not say is not changed by the sync.
MIREK = {"Id": "aaaaaaaabbbbccccddddeeeeeeeeeeee", "Name": "Mirek",
         "Policy": {"IsAdministrator": False, "IsDisabled": False}}
JANA = {"Id": "11111111222233334444555555555555", "Name": "Jana",
        "Policy": {"IsAdministrator": False, "IsDisabled": False}}

# Two viewers signed in - each has an account.
accounts.from_jellyfin({"id": MIREK["Id"], "name": "Mirek", "is_admin": False})
accounts.from_jellyfin({"id": JANA["Id"], "name": "Jana", "is_admin": False})
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, is_missing)"
                 " VALUES ('film-1','Film','Movie',0)")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id,"
        " item_name, item_type, started_at, last_seen_at, watched_seconds,"
        " is_active) VALUES ('k1', ?, 'Mirek', 'film-1', 'Film', 'Movie',"
        " '2026-09-01 20:00:00', '2026-09-01 21:30:00', 5400, 0)",
        (MIREK["Id"],))
    conn.commit()

print("--- while they are in Jellyfin, the account stays ---")
sync([MIREK, JANA])
check(account("Mirek") is not None and account("Jana") is not None,
      "both accounts are here")
check(account("admin") is not None and account("reader") is not None,
      "local accounts are not concerned")

print()
print("--- Mirek signs in, then the administrator removes him in Jellyfin ---")
sync([JANA])
check(account("Mirek") is None, "Mirek's account is gone")
check(account("Jana") is not None, "Jana's stayed")
check(account("reader") is not None and account("admin") is not None,
      "local accounts stayed")
check(db.query_one("SELECT name FROM users WHERE id = ?", (MIREK["Id"],))
      is not None, "the viewer's row in `users` stayed - the history has somebody to belong to")
count = db.query_one("SELECT COUNT(*) AS n FROM playback WHERE user_id = ?",
                     (MIREK["Id"],))
check(int(dict(count or {}).get("n") or 0) == 1,
      "and his history too - the statistics do not change")

print()
print("--- safeguards ---")
sync([])
check(account("Jana") is not None,
      "an empty answer from Jellyfin deletes nobody (that is a connection error, not an empty server)")

# The same id, once with dashes - that is not another person.
with_dashes = "11111111-2222-3333-4444-555555555555"
sync([{"Id": with_dashes, "Name": "Jana", "Policy": {}}])
check(account("Jana") is not None, "an id with dashes is the same person - the account stayed")

# The last administrator stays, even when they are from Jellyfin.
with db.connect() as conn:
    conn.execute("UPDATE accounts SET is_admin = 0 WHERE username = 'admin'")
    conn.execute("UPDATE accounts SET is_admin = 1 WHERE username = 'Jana'")
    conn.commit()
sync([MIREK])
check(account("Jana") is not None, "the last administrator stays, even when Jellyfin does not know them")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET is_admin = 1 WHERE username = 'admin'")
    conn.commit()
sync([MIREK])
check(account("Jana") is None, "as soon as the administrator is not the last, they are removed too")

print()
print("--- a signed-in viewer drops out after the account is deleted ---")
import httpx  # noqa: E402

from jellyscope import access, jellyfin  # noqa: E402

db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
client = TestClient(web.app)


LOGIN_POLICY: dict = {}      # what the fake Jellyfin says about Mirek at a sign-in


def server(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/Users/AuthenticateByName":
        return httpx.Response(200, json={
            "AccessToken": "t", "User": {"Id": MIREK["Id"], "Name": "Mirek",
                                         "Policy": LOGIN_POLICY}})
    return httpx.Response(204)


async def fake_enter(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = fake_enter
response = client.post("/login", data={"username": "Mirek", "password": "x",
                                        "method": "jellyfin"},
                       follow_redirects=False)
check(response.status_code == 303, f"Mirek signed in ({response.status_code})")
check(client.get("/history", follow_redirects=False).status_code == 200,
      "and sees his history")
sync([JANA])
response = client.get("/history", follow_redirects=False)
check(response.status_code == 303 and "/login" in response.headers.get("location", ""),
      f"after the removal in Jellyfin the next click sends him to the sign-in ({response.status_code})")

print()
print("--- a viewer disabled in Jellyfin is out at the next click, and so are their API keys ---")
# A disabled person cannot sign in (Jellyfin says so at the sign-in) - but a browser signed in before used to go on
# seeing everything for as long as the cookie lived, and an own API key went on working. The users' sync (with every
# quick refresh) brings the state; the account is not deleted - enabled again, the person is back with their settings.
from jellyscope import api  # noqa: E402

DISABLED = {**MIREK, "Policy": {"IsAdministrator": False, "IsDisabled": True}}
db.set_setting("viewer_may_have_key", "1")      # viewers may have a key of their own
db.forget_settings()
sync([MIREK, JANA])
client = TestClient(web.app)
client.post("/login", data={"username": "Mirek", "password": "x", "method": "jellyfin"}, follow_redirects=False)
check(client.get("/history", follow_redirects=False).status_code == 200, "Mirek is signed in again")
mirek = account("Mirek")
key = api.create_token("Mirek's key", account_id=mirek["id"])["token"]


def with_key() -> int:
    return TestClient(web.app).get("/api/v1/", headers={"Authorization": f"Bearer {key}"}).status_code


check(with_key() == 200, f"his own API key works ({with_key()})")
sync([DISABLED, JANA])
response = client.get("/history", follow_redirects=False)
check(response.status_code == 303 and "/login" in response.headers.get("location", ""),
      f"disabled in Jellyfin, the next click sends him to the sign-in ({response.status_code})")
check(with_key() == 403, f"and his API key is refused ({with_key()})")
check(account("Mirek") is not None, "the account stays (only disabled, not removed)")
sync([MIREK, JANA])
check(with_key() == 200, "enabled again, the key works again")
with db.connect() as conn:       # the account's id with dashes, the synced one without: the same person
    conn.execute("UPDATE accounts SET jellyfin_user_id = ? WHERE username = 'Mirek'",
                 ("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",))
    conn.commit()
sync([DISABLED, JANA])
check(with_key() == 403, f"an id with dashes is the same person - disabled, refused ({with_key()})")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = ? WHERE username = 'Mirek'", (MIREK["Id"],))
    conn.commit()
sync([MIREK, JANA])
client.post("/login", data={"username": "Mirek", "password": "x", "method": "jellyfin"}, follow_redirects=False)
check(client.get("/history", follow_redirects=False).status_code == 200, "and he signs in again")
sync([DISABLED, JANA])
sync([{**MIREK, "Policy": None}, JANA])
check(with_key() == 403, "an answer without Policy changes nothing: the disabled stays disabled")
with db.connect() as conn:       # two rows of one person: an old one with dashes, never synced (NULL), enabled
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled, synced_at)"
                 " VALUES ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'Mirek', 0, 0, NULL)")
    conn.commit()
check(with_key() == 403, "the row synced last counts, not one never synced (NULL sorts differently on PostgreSQL)")
with db.connect() as conn:
    conn.execute("DELETE FROM users WHERE id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'")
    conn.commit()
sync([MIREK, JANA])
local = TestClient(web.app)
local.post("/login", data={"username": "reader", "password": "longpassword"}, follow_redirects=False)
sync([DISABLED, JANA])
check(local.get("/history", follow_redirects=False).status_code == 200, "a local account is not concerned")

print()
print("--- an administrator's rights follow Jellyfin at every sync, not only at the next sign-in ---")
ADMIN_MIREK = {**MIREK, "Policy": {"IsAdministrator": True, "IsDisabled": False}}
sync([ADMIN_MIREK, JANA])
check(bool(account("Mirek")["is_admin"]), "made an administrator in Jellyfin, he is one here after the sync")
admin_browser = TestClient(web.app)
LOGIN_POLICY = {"IsAdministrator": True}     # the sign-in takes the rights over from Jellyfin too
admin_browser.post("/login", data={"username": "Mirek", "password": "x", "method": "jellyfin"}, follow_redirects=False)
LOGIN_POLICY = {}
check(admin_browser.get("/settings", follow_redirects=False).status_code == 200, "and opens Settings")
sync([{**MIREK, "Policy": {}}, JANA])
check(bool(account("Mirek")["is_admin"]), "a user without Policy in the answer changes nothing")
check(with_key() == 200, "the administrator's own key works")
sync([{**MIREK, "Policy": {"IsAdministrator": True, "IsDisabled": True}}, JANA])
check(with_key() == 403 and admin_browser.get("/settings", follow_redirects=False).status_code != 200,
      f"an administrator disabled in Jellyfin: the key refused and the browser out ({with_key()})")
code = TestClient(web.app).get("/api/v1/", headers={"Authorization": f"Bearer {key}"}).json().get("error")
check(code == "account_disabled", f"an administrator's key says the account is disabled, not a viewer ({code})")
sync([ADMIN_MIREK, JANA])
LOGIN_POLICY = {"IsAdministrator": True}
admin_browser.post("/login", data={"username": "Mirek", "password": "x", "method": "jellyfin"}, follow_redirects=False)
LOGIN_POLICY = {}
check(admin_browser.get("/settings", follow_redirects=False).status_code == 200, "enabled again, he signs in and opens Settings")
sync([MIREK, JANA])
check(not account("Mirek")["is_admin"], "the rights taken away in Jellyfin are gone after the sync")
check(admin_browser.get("/settings", follow_redirects=False).status_code != 200,
      "and the browser signed in as an administrator no longer opens Settings")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET is_admin = 0 WHERE username = 'admin'")
    conn.execute("UPDATE accounts SET is_admin = 1 WHERE username = 'Mirek'")
    conn.commit()
sync([MIREK, JANA])
check(bool(account("Mirek")["is_admin"]), "the last administrator keeps the rights, as when removed in Jellyfin")
# Such an installation has no local administrator (it should always have one - see test_account_deletion): the
# last administrator from Jellyfin is then not shut out by being disabled, nor demoted at a sign-in.
sync([{**MIREK, "Policy": {"IsAdministrator": True, "IsDisabled": True}}, JANA])
check(with_key() == 200, "with no local administrator the last one from Jellyfin is not shut out when disabled")
sync([ADMIN_MIREK, JANA])
TestClient(web.app).post("/login", data={"username": "Mirek", "password": "x", "method": "jellyfin"},
                         follow_redirects=False)
check(bool(account("Mirek")["is_admin"]), "nor demoted at a sign-in")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET is_admin = 1 WHERE username = 'admin'")
    conn.commit()

print()
print("--- a kept cookie does not open the account that gets a deleted account's id ---")
# Ids come back: a restored backup, or a copy to another database, sets the counter to the highest id there is. An
# account from Jellyfin has no password hash, so a fingerprint of the hash alone was the same for all of them and
# a deleted viewer's cookie opened whoever got the id next - an administrator too.
sync([MIREK, JANA])
kept = TestClient(web.app)
kept.post("/login", data={"username": "Mirek", "password": "x", "method": "jellyfin"}, follow_redirects=False)
check(kept.get("/history", follow_redirects=False).status_code == 200, "Mirek is signed in")
freed = account("Mirek")
with db.connect() as conn:
    conn.execute("DELETE FROM accounts WHERE id = ?", (freed["id"],))
    conn.execute("INSERT INTO accounts (id, username, password_hash, is_admin, created_at, jellyfin_user_id)"
                 " VALUES (?, 'Owner', '', 1, ?, 'ffffffff000011112222333333333333')", (freed["id"], db.utcnow()))
    conn.commit()
response = kept.get("/settings", follow_redirects=False)
check(response.status_code in (302, 303, 307) and "/login" in response.headers.get("location", ""),
      f"the deleted viewer's cookie does not open the new administrator under the same id ({response.status_code})")
jana = accounts.from_jellyfin({"id": JANA["Id"], "name": "Jana", "is_admin": False})
check(accounts.session_fingerprint(account("Owner")) != accounts.session_fingerprint(jana),
      "two accounts from Jellyfin have different fingerprints")
import hashlib  # noqa: E402

local_hash = str(account("reader")["password_hash"])
check(accounts.session_fingerprint(account("reader"))
      == hashlib.sha256(("relace:" + local_hash).encode("utf-8")).hexdigest()[:16],
      "a local account keeps the fingerprint it had, so its sessions hold after an upgrade")
with db.connect() as conn:
    conn.execute("DELETE FROM accounts WHERE username = 'Owner'")
    conn.commit()

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
