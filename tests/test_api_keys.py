# -*- coding: utf-8 -*-
r"""API keys per user: the permission, the owner, the state, the record of use, the rights.

Keys used to be made by the administrator only, and every key saw all the server's data.
Now a key belongs to an account and has exactly the rights of its owner; a reader or a
viewer from Jellyfin may make one only with the administrator's permission. The design:
docs/superpowers/specs/2026-10-15-api-klice-design.md.

Run:
    .venv/bin/python tests/test_api_keys.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api_klice.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://jellyfin.test"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from jellyscope import access, accounts, api, applog, db, jellyfin, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# A fake Jellyfin: it knows one viewer, so that an account from Jellyfin can sign in.
def server(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if (body.get("Username"), body.get("Pw")) == ("mirek", "tajne"):
        return httpx.Response(200, json={"User": {
            "Id": "u-mirek", "Name": "mirek", "Policy": {"IsAdministrator": False}}})
    return httpx.Response(401, json={})


async def fake_client(self):
    self._client = httpx.AsyncClient(transport=httpx.MockTransport(server),
                                     base_url="http://jellyfin.test")
    return self


jellyfin.JellyfinClient.__aenter__ = fake_client

db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass")
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()


def sign_in(name: str, password: str, method: str = "local") -> TestClient:
    k = TestClient(web.app)
    k.post("/login", data={"username": name, "password": password, "method": method},
           follow_redirects=False)
    return k


admin_client = sign_in("admin", "longpassword")
reader_client = sign_in("reader", "readerpass")
viewer_client = sign_in("mirek", "tajne", "jellyfin")


def account(name: str) -> dict:
    """A fresh row of the account (read again after every change)."""
    db.forget_settings()
    return accounts.get_by_name(name) or accounts.by_jellyfin_id("u-mirek") or {}


def viewer() -> dict:
    db.forget_settings()
    return accounts.by_jellyfin_id("u-mirek") or {}


print("--- the permission for a key of one's own ---")
check(access.may_have_key(account("admin")) is True, "the administrator always may")
check(access.may_have_key(account("reader")) is False, "a reader not by default")
check(access.may_have_key(viewer()) is False, "a viewer from Jellyfin not by default")
check(access.may_have_key(None) is False, "nobody (without an account) not")

db.set_setting("reader_may_have_key", "1")
check(access.may_have_key(account("reader")) is True, "the readers' group switched on: a reader may")
check(access.may_have_key(viewer()) is False, "and a viewer still not - the groups are separate")
check(access.group_may_have_key(access.READER) is True
      and access.group_may_have_key(access.VIEWER) is False, "group_may_have_key reads the group")

accounts.save_may_have_key(account("reader")["id"], "0")
check(access.may_have_key(account("reader")) is False, "the exception „no“ beats a group switched on")
accounts.save_may_have_key(viewer()["id"], "1")
check(access.may_have_key(viewer()) is True, "the exception „yes“ beats a group switched off")
accounts.save_may_have_key(account("reader")["id"], None)
check(access.may_have_key(account("reader")) is True, "without an exception by the group again")

# Nonsense in the column (a manual change of the database) = by the group, neither a
# closed nor an open door.
with db.connect() as conn:
    conn.execute("UPDATE accounts SET may_have_key = 'x' WHERE id = ?", (viewer()["id"],))
check(access.may_have_key(viewer()) is False, "a value other than 1/0 = by the group (viewer: no)")
accounts.save_may_have_key(viewer()["id"], "neco")
check(viewer().get("may_have_key") is None, "save_may_have_key stores only 1, 0 or nothing")
db.set_setting("reader_may_have_key", "0")


print("--- a key's state and owner ---")
with db.connect() as conn:
    key_columns = conn.table_columns("api_tokens")
check({"account_id", "status"} <= set(key_columns), "api_tokens has an owner and a state")

anonymous = TestClient(web.app)


def call(token: str, path: str = "/api/v1/summary"):
    return anonymous.get(path, headers={"Authorization": f"Bearer {token}"})


def bare_request(token: str) -> Request:
    """A bare request with the header - for calling verify_token directly."""
    return Request({"type": "http", "method": "GET", "path": "/api/v1/summary",
                    "query_string": b"",
                    "headers": [(b"authorization", f"Bearer {token}".encode())]})


def last_use() -> dict:
    """The last line of the API log (applog.read_api) under the names the test knows."""
    lines = applog.read_api(applog.MAX_LINES)["rows"]
    if not lines:
        return {}
    line = lines[0]
    return {"path": line["path"], "ip": line["ip"], "result": line["result"],
            "token_id": line["key_id"], "owner": line["owner"]}


def use_count() -> int:
    return len(applog.read_api(applog.MAX_LINES)["rows"])


# Review Focus 5: today's keys have no owner and must keep working after the
# update - otherwise Grafana and Homepage break.
server_key = api.create_token("Grafana")
check({"token", "display_name", "id"} <= set(server_key), "create returns the token, the name and the id")
check(call(server_key["token"]).status_code == 200, "the server's key (without an owner) passes")
verified = api.verify_token(bare_request(server_key["token"]))
check(verified["account"] is None and verified["role"] == access.ADMIN,
      "the server's key has full rights (the administrator's role, no account)")

check(api.disable_token(server_key["id"]) == "Grafana", "disable returns the key's name")
response = call(server_key["token"])
check(response.status_code == 403 and response.json().get("error") == "key_disabled",
      "a disabled key: 403 key_disabled")
check(api.enable_token(server_key["id"]) == "Grafana", "enable returns the key's name")
check(call(server_key["token"]).status_code == 200, "an enabled key passes again")
response = call(server_key["token"], "/api/v1/item/neexistuje")
check(response.status_code == 404 and "json" in response.headers.get("content-type", ""),
      "an error under /api/ is JSON, not an HTML page")
check(api.disable_token(999999) == "" and api.enable_token(999999) == "", "a key that does not exist: an empty name")

reader_id = account("reader")["id"]
reader_key = api.create_token("Homepage", reader_id)
response = call(reader_key["token"])
check(response.status_code == 403 and response.json().get("error") == "key_not_allowed",
      "a reader without the permission: 403 key_not_allowed")
db.set_setting("reader_may_have_key", "1")
check(call(reader_key["token"]).status_code == 200, "the permission on: the key passes")
verified = api.verify_token(bare_request(reader_key["token"]))
check((verified["account"] or {}).get("id") == reader_id and verified["role"] == access.READER,
      "verify_token returns the owner's account and its role")
db.set_setting("reader_may_have_key", "0")
check(call(reader_key["token"]).status_code == 403, "the permission taken back: 403 again")
check(any(r["id"] == reader_key["id"] for r in api.list_tokens(reader_id)),
      "taking the permission away does not delete the key")

# Review Focus 2: an administrator who loses the role gets a reader's rights.
second_admin = accounts.create("spravce2", "longpassword2", is_admin=True)
admin_key = api.create_token("Admin", second_admin)
check(call(admin_key["token"]).status_code == 200, "an administrator's key passes without the permission too")
accounts.set_admin(second_admin, False)
check(call(admin_key["token"]).status_code == 403,
      "an administrator without the role: the key follows the readers' permission (403)")

all_keys = api.list_tokens()
check({"id", "name", "preview", "created_at", "last_used_at", "status", "account_id",
       "owner"} <= set(all_keys[0]), "the list has the owner and the state")
by_id = {r["id"]: r for r in all_keys}
check(by_id[server_key["id"]]["owner"] is None
      and by_id[reader_key["id"]]["owner"] == "reader",
      "the list without a parameter: all keys and their owners")
check([r["id"] for r in api.list_tokens(reader_id)] == [reader_key["id"]],
      "list(account_id): only the keys of that account")
check(by_id[admin_key["id"]]["status"] == "active", "a new key is active")


print("--- the record of use ---")
call(server_key["token"], "/api/v1/summary?days=7")
line = last_use()
check(line.get("path") == "/api/v1/summary?days=7", "the address is written with its parameters")
check(line.get("ip") == "testclient", "the client's IP address is written")
check(line.get("result") == 200 and line.get("token_id") == server_key["id"],
      "the result is written and whose key it was")
api.disable_token(server_key["id"])
call(server_key["token"])
check(last_use().get("result") == 403, "a refused request is written too (403)")
api.enable_token(server_key["id"])

before = use_count()
call("js_tohle-neni-zadny-klic")
anonymous.get("/api/v1/summary")
check(use_count() == before, "an invalid or missing key is not written")

# Review Focus 4: a long address is not written into the log whole.
call(server_key["token"], "/api/v1/summary?x=" + "a" * 400)
check(len(last_use().get("path", "")) == 300, "a long address is cut to 300 characters")

original_record = api.record_use


def broken_record(*args, **kwargs):
    raise RuntimeError("the disk is full")


api.record_use = broken_record
logging.disable(logging.CRITICAL)
check(call(server_key["token"]).status_code == 200, "an error writing the use does not bring the answer down")
logging.disable(logging.NOTSET)
api.record_use = original_record

# Review Focus 1: a deleted account takes its key with it, the records stay.
db.set_setting("reader_may_have_key", "1")
temporary = accounts.create("docasny", "temporarypass")
temporary_key = api.create_token("Docasny", temporary)
check(call(temporary_key["token"]).status_code == 200, "the temporary account's key passes")
its_lines = len(applog.read_api(applog.MAX_LINES, key=temporary_key["id"])["rows"])
accounts.delete(temporary)
check(call(temporary_key["token"]).status_code == 401, "a deleted account: the key does not exist (401)")
check(its_lines > 0 and len(applog.read_api(applog.MAX_LINES, key=temporary_key["id"])["rows"]) == its_lines,
      "the records in the API log stay after the account is deleted too")
db.set_setting("reader_may_have_key", "0")


print("--- the owner's rights ---")
get_paths = sorted(r.path for r in api.router.routes if "GET" in getattr(r, "methods", ()))
check(set(get_paths) == set(api.ADDRESS_AREAS),
      "every address of the API has an area (and nothing more in ADDRESS_AREAS): "
      f"{sorted(set(get_paths) ^ set(api.ADDRESS_AREAS))}")

OTHER_NAME, OTHER_ID, OTHER_IP = "KarelCizinec", "u-karel", "10.0.0.5"
with db.connect() as conn:
    for uid, name in (("u-mirek", "mirek"), (OTHER_ID, OTHER_NAME)):
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (uid, name, db.utcnow()))
        for n in range(3):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, remote_address, is_active)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"{uid}-{n}", uid, name, "i-0", "Titul 0", db.utcnow(),
                 db.utcnow(), 1200, OTHER_IP if uid == OTHER_ID else "10.0.0.9",
                 1 if (uid == OTHER_ID and n == 0) else 0))
    conn.execute("INSERT INTO items (id, name, type) VALUES ('i-0','Titul 0','Movie')")
    conn.commit()

db.set_setting("reader_may_have_key", "1")
db.set_setting("viewer_may_have_key", "1")
db.set_setting(access.area_key(access.READER, access.BY_KEY["network"]), "0")
response = call(reader_key["token"], "/api/v1/bandwidth")
check(response.status_code == 403, f"a reader without the Network area: /bandwidth 403 ({response.status_code})")
check(last_use().get("result") == 403
      and last_use().get("path") == "/api/v1/bandwidth",
      "an address outside the area is written as 403")
addresses = call(reader_key["token"], "/api/v1").json().get("endpoints", {})
check("/api/v1/bandwidth" not in addresses and "/api/v1/summary" in addresses,
      "the index shows only the addresses the key may")
check(call(reader_key["token"], "/api/v1/summary").status_code == 200,
      "an area the reader has passes")
db.set_setting(access.area_key(access.READER, access.BY_KEY["network"]), "1")
check(call(reader_key["token"], "/api/v1/bandwidth").status_code == 200,
      "the Network area on: /bandwidth 200")
check("/api/v1/bandwidth" in call(reader_key["token"], "/api/v1").json()["endpoints"],
      "and the index shows it again")

# A leak test: a viewer sees everything the administration allows them - and still
# nowhere may there be somebody else's name, id or address.
for area in access.AREAS:
    db.set_setting(access.area_key(access.VIEWER, area), "1")
viewer_key = api.create_token("Mobil", viewer()["id"])
for path in get_paths:
    address = path.replace("{item_id}", "i-0")
    response = call(viewer_key["token"], address)
    leak = [s for s in (OTHER_NAME, OTHER_ID, OTHER_IP) if s in response.text]
    check(response.status_code == 200 and not leak,
          f"a viewer {address}: {response.status_code}, without somebody else's data {leak or ''}")

# Somebody else's `user` used to be quietly rewritten to the viewer themselves. Now it is 403:
# the key does not get other data than it asked for, and nobody thinks they see
# somebody else's history.
response = call(viewer_key["token"], f"/api/v1/history?user={OTHER_ID}")
check(response.status_code == 403 and response.json().get("error") == "own_history_only",
      "a viewer with ?user=somebody else's id: 403 own_history_only")
history = call(viewer_key["token"], "/api/v1/history").json()
check(history["count"] > 0 and all(p["user_id"] == "u-mirek" for p in history["playbacks"]),
      "a viewer without ?user gets only their own history")
playing = call(viewer_key["token"], "/api/v1/now-playing").json()
check(playing["count"] == 1 and playing["sessions"][0]["user"] != OTHER_NAME,
      "now playing: somebody else's session is visible, but without the name")

for which, key in (("the server's key", server_key), ("an administrator's key", admin_key)):
    if key is admin_key:
        accounts.set_admin(second_admin, True)
    text = call(key["token"], "/api/v1/users").text \
        + call(key["token"], "/api/v1/history").text
    check(OTHER_NAME in text and OTHER_ID in text, f"{which} sees the names (anonymising does not concern it)")
db.set_setting("reader_may_have_key", "0")
db.set_setting("viewer_may_have_key", "0")


print("--- Settings -> API ---")


def key_state(token_id: int) -> str:
    row = db.query_one("SELECT status FROM api_tokens WHERE id = ?", (token_id,))
    return row["status"] if row else ""


def send_form(client: TestClient, address: str, **data) -> int:
    return client.post(address, data={k: str(v) for k, v in data.items()},
                       follow_redirects=False).status_code


page = admin_client.get("/settings?section=api").text
check("Homepage" in page and "reader" in page, "the administrator sees the reader's key with its owner")
check("Root · všechno" in re.sub(r"\s+", " ", page),
      "a key without an owner is a root key with everything (Root · všechno)")

check(send_form(admin_client, "/settings/api/token/disable", token_id=reader_key["id"]) == 303
      and key_state(reader_key["id"]) == "disabled", "the administrator disables a key")
check("vypnutý" in admin_client.get("/settings?section=api").text, "the state is seen in the table")
check(send_form(admin_client, "/settings/api/token/enable", token_id=reader_key["id"]) == 303
      and key_state(reader_key["id"]) == "active", "the administrator enables the key again")
to_delete = api.create_token("Na smazání", reader_id)
check(send_form(admin_client, "/settings/api/token/revoke", token_id=to_delete["id"]) == 303
      and not key_state(to_delete["id"]), "the administrator revokes somebody else's key too")
check(send_form(admin_client, "/settings/api/token/disable", token_id="nesmysl") == 303,
      "a nonsense id brings nothing down")

call(server_key["token"], "/api/v1/summary?znacka=serveru")
call(reader_key["token"], "/api/v1/summary?znacka=ctenare")
page = admin_client.get("/settings?section=log&log_kind=api").text
check("/api/v1/summary?znacka=serveru" in page and "testclient" in page,
      "the API log in Settings → Log shows the address and the IP")
by_key = admin_client.get(f"/settings?section=log&log_kind=api&log_key={reader_key['id']}").text
check("znacka=ctenare" in by_key and "znacka=serveru" not in by_key, "the key filter shows only that key")
by_account = admin_client.get("/settings?section=log&log_kind=api&log_account=reader").text
check("znacka=ctenare" in by_account and "znacka=serveru" not in by_account, "the user filter shows only their keys")
check(admin_client.get("/settings?section=api&klic=abc&ucet=").status_code == 200,
      "a nonsense filter does not bring the page down")

# Settings -> API makes a root key: it belongs to nobody, not to the administrator either (see
# tests/test_root_keys.py). The administrator's own key is in My account.
new = admin_client.post("/settings/api/token", data={"name": "Moje Grafana", "mode": "all"},
                        follow_redirects=False)
mine = [r for r in api.list_tokens(account("admin")["id"]) if r["name"] == "Moje Grafana"]
check(new.status_code == 303 and not mine
      and [r["account_id"] for r in api.list_tokens() if r["name"] == "Moje Grafana"] == [None],
      "a key from Settings → API is a root key, it does not belong to the administrator")

for address in ("/settings/api/token/disable", "/settings/api/token/enable"):
    check(send_form(reader_client, address, token_id=server_key["id"]) == 403,
          f"a reader at {address}: 403")
check(key_state(server_key["id"]) == "active", "and the key's state did not change")


print("--- Settings -> Accounts ---")
# A tick box that is not ticked is not sent by the browser at all.
check(send_form(admin_client, "/settings/accounts/keys", reader_may_have_key="on") == 303
      and db.get_setting("reader_may_have_key") == "1"
      and db.get_setting("viewer_may_have_key") == "0", "groups: readers yes, viewers no")
send_form(admin_client, "/settings/accounts/keys", viewer_may_have_key="on")
check(db.get_setting("reader_may_have_key") == "0" and db.get_setting("viewer_may_have_key") == "1",
      "groups: the other way round")
page = admin_client.get("/settings?section=accounts").text
check('name="viewer_may_have_key" checked' in page
      and 'name="reader_may_have_key" checked' not in page, "the card shows the stored state")
check('name="may_have_key"' in page, "the Permissions window has the choice of an own key")

send_form(admin_client, "/settings/accounts/permissions", account_id=reader_id, mode="group",
          may_have_key="no")
check(account("reader").get("may_have_key") == "0" and account("reader").get("permissions") is None,
      "the mode by group: the key's exception is saved, the tree stays the group's")
send_form(admin_client, "/settings/accounts/permissions", account_id=reader_id, mode="custom",
          sees_overview="on", may_have_key="yes")
own = access.own_permissions(account("reader")) or {}
check(account("reader").get("may_have_key") == "1" and set(own.get("areas", ())) == {"overview"},
      "the custom mode: the key's exception is saved beside the tree")
send_form(admin_client, "/settings/accounts/permissions", account_id=reader_id, mode="custom",
          sees_overview="on", may_have_key="group")
own = access.own_permissions(account("reader")) or {}
check(account("reader").get("may_have_key") is None and set(own.get("areas", ())) == {"overview"},
      "„by group“ = no exception, the tree unchanged")
send_form(admin_client, "/settings/accounts/permissions", account_id=reader_id, mode="group")
check(account("reader").get("may_have_key") is None and account("reader").get("permissions") is None,
      "a form without the choice leaves the exception as it was")

check(send_form(reader_client, "/settings/accounts/keys", reader_may_have_key="on") == 403
      and db.get_setting("reader_may_have_key") == "0", "a reader may not change the permission (403)")
db.set_setting("viewer_may_have_key", "0")


print("--- My account ---")
newcomer_id = accounts.create("novacek", "newcomerpass")
newcomer_client = sign_in("novacek", "newcomerpass")
check('id="keys"' not in newcomer_client.get("/account").text,
      "without the permission and without keys there is no API keys card")
check(send_form(newcomer_client, "/account/key", name="Pokus") == 303 and not api.list_tokens(newcomer_id),
      "without the permission a POST creates nothing")

accounts.save_may_have_key(newcomer_id, "1")
check('id="keys"' in newcomer_client.get("/account").text, "with the permission the card is seen")
check(send_form(newcomer_client, "/account/key", name="Muj skript") == 303
      and [r["name"] for r in api.list_tokens(newcomer_id)] == ["Muj skript"],
      "the key made belongs to the session's account")
page = newcomer_client.get("/account").text
found = re.search(r"(js_[A-Za-z0-9_\-]{20,})", page)
new_key = found.group(1) if found else ""
check(bool(new_key) and call(new_key, "/api/v1/").status_code == 200,
      "the new key is shown and works on /api/v1/")
check(new_key and new_key not in newcomer_client.get("/account").text, "and the second time it is not shown")
check(new_key and new_key not in admin_client.get("/settings?section=api").text,
      "nor in Settings - the sessions do not mix")

check(send_form(newcomer_client, "/account/key/revoke", token_id=reader_key["id"]) == 303
      and key_state(reader_key["id"]) == "active", "somebody else's key cannot be revoked")

network = access.BY_KEY["network"]
db.set_setting(access.area_key(access.READER, network), "0")
page = newcomer_client.get("/account").text
check("GET /api/v1/bandwidth" not in page and "GET /api/v1/summary" in page,
      "the list of addresses on the card only by the account's rights")
db.set_setting(access.area_key(access.READER, network), "1")

accounts.save_may_have_key(newcomer_id, "0")
page = newcomer_client.get("/account").text
check('id="keys"' in page and 'action="/account/key"' not in page,
      "without the permission but with a key: the card is there, the form for a new one not")
send_form(newcomer_client, "/account/key", name="Druhy")
check(len(api.list_tokens(newcomer_id)) == 1, "an owner without the permission makes no new one")
my_key = api.list_tokens(newcomer_id)[0]["id"]
check(send_form(newcomer_client, "/account/key/revoke", token_id=my_key) == 303
      and not api.list_tokens(newcomer_id), "but revokes the old one")

db.set_setting("viewer_may_have_key", "1")
check(send_form(viewer_client, "/account/key", name="Telefon") == 303, "a viewer from Jellyfin makes a key (303)")
phone = [r["id"] for r in api.list_tokens(viewer()["id"]) if r["name"] == "Telefon"]
check(len(phone) == 1, "and the key belongs to them")
check(phone and send_form(viewer_client, "/account/key/revoke", token_id=phone[0]) == 303
      and not key_state(phone[0]), "a viewer revokes their key (303)")
check(send_form(viewer_client, "/settings/api/token", name="Cizi") == 403, "another POST still gives a viewer 403")
db.set_setting("viewer_may_have_key", "0")


print("--- after the review: Jellyfin closed for viewers ---")
db.set_setting("viewer_may_have_key", "1")
mirek_key = api.create_token("Mirkuv skript", viewer()["id"])
check(call(mirek_key["token"], "/api/v1/").status_code == 200, "the viewer's key passes")
db.set_setting(access.LOGIN_KEY, "0")
response = call(mirek_key["token"], "/api/v1/")
check(response.status_code == 403, f"signing in through Jellyfin switched off: the viewer's key 403 ({response.status_code})")
check(last_use().get("result") == 403, "and the refusal is written")
check(call(reader_key["token"], "/api/v1/").status_code in (200, 403)
      and call(server_key["token"], "/api/v1/").status_code == 200,
      "the keys of local accounts are not concerned")
db.set_setting(access.LOGIN_KEY, "1")
with db.connect() as conn:
    conn.execute("UPDATE users SET is_disabled = 1 WHERE id = 'u-mirek'")
    conn.commit()
check(call(mirek_key["token"], "/api/v1/").status_code == 403,
      "a viewer disabled in Jellyfin: the key 403")
with db.connect() as conn:
    conn.execute("UPDATE users SET is_disabled = 0 WHERE id = 'u-mirek'")
    conn.commit()
check(call(mirek_key["token"], "/api/v1/").status_code == 200, "enabled again it passes")
api.revoke_token(mirek_key["id"])
db.set_setting("viewer_may_have_key", "0")


print("--- after the review: the use is written only after the answer ---")
events: list[str] = []


def watched_record(*args, **kwargs):
    events.append("write")


async def asgi_request(path: str, token: str) -> None:
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "client": ("testclient", 50000),
             "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"),
                         (b"authorization", f"Bearer {token}".encode())]}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send_asgi(message):
        if message["type"] == "http.response.body" and not message.get("more_body"):
            events.append("sent")

    await web.app(scope, receive, send_asgi)


api.record_use = watched_record
asyncio.run(asgi_request("/api/v1/summary", server_key["token"]))
api.record_use = original_record
check(events == ["sent", "write"],
      f"the client has the answer before it is written ({events})")

# At the front door (api calls stats.overview) and in the submodule where it lives
# (comparison reads it there) - test_stats_package, check e.
original_overview = api.stats.overview


def broken_overview(*args, **kwargs):
    raise RuntimeError("a broken query")


api.stats.overview = api.stats.overview_page.overview = broken_overview
logging.disable(logging.CRITICAL)
response = TestClient(web.app, raise_server_exceptions=False).get(
    "/api/v1/summary?days=29", headers={"Authorization": f"Bearer {server_key['token']}"})
logging.disable(logging.NOTSET)
api.stats.overview = api.stats.overview_page.overview = original_overview
check(response.status_code == 500 and last_use().get("path") == "/api/v1/summary?days=29"
      and last_use().get("result") == 500, "an uncaught error (500) is written too")


class Catcher(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


catcher = Catcher()
web_log = logging.getLogger("jellyscope.web")
web_log.addHandler(catcher)
web_log.propagate = False
api.record_use = broken_record
web._last_write_error = 0.0
call(server_key["token"])
call(server_key["token"])
api.record_use = original_record
web_log.removeHandler(catcher)
web_log.propagate = True
write_errors = [r for r in catcher.records if "API key usage log entry" in r.getMessage()]
check(len(write_errors) == 1, f"a repeated write error is logged once, not every time ({len(write_errors)})")


print("--- after the review: the null character ---")
call(server_key["token"], "/api/v1/item/abc%00def")
check("\x00" not in last_use().get("path", "x\x00"),
      "a null character from the address does not get into the record")


print("--- after the review: My account ---")
accounts.save_may_have_key(newcomer_id, "1")
db.set_setting(access.area_key(access.READER, access.BY_KEY["overview"]), "0")
page = newcomer_client.get("/account").text
example = re.search(r"api/v1/([a-z\-/]*)</code></pre>", page)
allowed = re.findall(r"<code>GET /api/v1/([a-z/-]*)", page)
check(example is not None and example.group(1) in allowed,
      f"the curl example points at an address the key may ({example and example.group(1)})")
db.set_setting(access.area_key(access.READER, access.BY_KEY["overview"]), "1")
send_form(newcomer_client, "/account/key", name="Se sloupcem")
check("Vyrobeno" in newcomer_client.get("/account").text, "the keys table has the Vyrobeno column")
for r in api.list_tokens(newcomer_id):
    api.revoke_token(r["id"])
accounts.save_may_have_key(newcomer_id, None)


print("--- a new key in the table's row, Copy only until a reload ---")
WHOLE_KEY = r"js_[A-Za-z0-9_\-]{20,}"


def key_row(html: str, name: str) -> str:
    """The table's row (<tr>…</tr>) holding the key with this name."""
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if name in row:
            return row
    return ""


# The key's name keeps the diacritics on purpose: form -> database -> the page's row.
for who, key_name, client, create_url, page_url in (
        ("administrator", "Radek správce", admin_client, "/settings/api/token", "/settings?section=api"),
        ("user", "Radek uživatel", newcomer_client, "/account/key", "/account")):
    if client is newcomer_client:
        accounts.save_may_have_key(newcomer_id, "1")
    send_form(client, create_url, name=key_name, mode="all")
    html = client.get(page_url).text
    row = key_row(html, key_name)
    whole = re.findall(WHOLE_KEY, html)
    check(len(whole) == 1 and whole[0] in row,
          f"{who}: the whole key is there once, in its key's row")
    check('data-copy="' in row, f"{who}: the row has the Copy button")
    check("Klíč vznikl a vidíš ho naposledy." not in html,
          f"{who}: the box above the form is gone")
    check(whole and call(whole[0], "/api/v1/").status_code == 200, f"{who}: the key shown works")
    again = client.get(page_url).text
    check(not re.findall(WHOLE_KEY, again) and 'data-copy="' not in again,
          f"{who}: after a reload of the page the key and Copy are gone")
    check("*" * 12 in key_row(again, key_name), f"{who}: the start and asterisks are left")
for r in api.list_tokens(newcomer_id):
    api.revoke_token(r["id"])
for r in api.list_tokens():
    if r["name"] == "Radek správce":
        api.revoke_token(r["id"])

# A page with the whole key must not come back from the browser's cache (the Back
# button, also after signing out on a shared computer).
for client, create_url, page_url in ((admin_client, "/settings/api/token", "/settings?section=api"),
                                     (newcomer_client, "/account/key", "/account")):
    if client is newcomer_client:
        accounts.save_may_have_key(newcomer_id, "1")
    send_form(client, create_url, name="Bez mezipameti", mode="all")
    response = client.get(page_url)
    check(re.findall(WHOLE_KEY, response.text)
          and "no-store" in response.headers.get("cache-control", ""),
          f"{page_url}: the page with the whole key has Cache-Control: no-store")
for r in api.list_tokens():
    if r["name"] == "Bez mezipameti":
        api.revoke_token(r["id"])

# A key made in Settings belongs to the administrator, but is not to be shown on My account.
send_form(admin_client, "/settings/api/token", name="Jen v Nastaveni", mode="all")
check(not re.findall(WHOLE_KEY, admin_client.get("/account").text),
      "a key made in Settings is not shown on My account")
check(len(re.findall(WHOLE_KEY, admin_client.get("/settings?section=api").text)) == 1,
      "in Settings it is")
for r in api.list_tokens():
    if r["name"] == "Jen v Nastaveni":
        api.revoke_token(r["id"])


print("--- the guide as for the administrator ---")


def guide_window(html: str) -> str:
    start = html.find('id="modal-api-docs"')
    return html[start:html.find("</dialog>", start)] if start >= 0 else ""


network_key = access.area_key(access.READER, access.BY_KEY["network"])
original_network = db.get_setting(network_key, "")
db.set_setting(network_key, "0")
page = newcomer_client.get("/account").text
guide = guide_window(page)
check(bool(guide) and 'data-modal="modal-api-docs"' in page,
      "My account has the button and the window How to use it")
check("GET /api/v1/libraries" in guide and "GET /api/v1/bandwidth" not in guide,
      "the user's window has only the addresses their key may")
check("Authorization: Bearer" in guide, "the window explains the header with the key")
check("Správce vidí" in guide, "and says the administrator sees the key's use")
admin_guide = guide_window(admin_client.get("/settings?section=api").text)
check("GET /api/v1/bandwidth" in admin_guide and "GET /api/v1/summary" in admin_guide,
      "the administrator sees all addresses in the window")
# The list in the window is written by hand (for the labels) - it must not drift apart from
# what the API really has. Every address of ADDRESS_AREAS has to be in it.
missing = [a for a in api.allowed_paths(account("admin"))
           if "GET " + a.replace("{id}", "&lt;id&gt;") not in admin_guide]
check(not missing, f"the guide's window knows every address of the API ({missing})")
# Filtering and sorting: the notation, examples and the fields of the addresses. The fields
# are taken from api.LISTS - the same description the query is checked by - so the
# help cannot promise a field that does not exist.
check("pole[operátor]=hodnota" in guide and "path[starts_with]=" in guide
      and "sort=plays,added_at" in guide and "limit=5" in guide,
      "the user's window explains filters, sorting and the limit with an example")
check("never_finished." in admin_guide and "starts_with" in admin_guide
      and "not_contains" in admin_guide, "an example with a list's prefix and the table of operators")
check('data-endpoint-fields="/api/v1/least-played"' in guide and "<code>path</code>" in guide,
      "an address has an expandable list of fields")
check('data-endpoint-fields="/api/v1/bandwidth"' not in guide,
      "the fields of addresses the key may not are not seen")
check(all(f'data-endpoint-fields="{a}"' in admin_guide for a in api.LISTS),
      "the administrator sees the fields of all addresses with a list")
check("unknown_field" in guide and "400" in guide, "the window explains the error 400 with its code")
check("user=&amp;" not in admin_guide and "user=&" not in admin_guide,
      "the history example without an empty user=")
admin_account_guide = guide_window(admin_client.get("/account").text)
check("GET /api/v1/bandwidth" in admin_account_guide and "Správce vidí" not in admin_account_guide
      and "tvůj klíč smí" not in admin_account_guide,
      "the administrator on My account: all addresses, without the sentences for a reader")
if original_network:
    db.set_setting(network_key, original_network)
else:
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (network_key,))
        conn.commit()
    db.forget_settings()
accounts.save_may_have_key(newcomer_id, None)


print("--- the API by the account's permissions, not by the defaults ---")
# A viewer from Jellyfin: the group is set by Settings -> Jellyfin (/settings/viewers).
# The viewer group's settings go back after the section as they were - including what was
# not in the table at all (then the default holds).
GROUP_KEYS = ([access.LOGIN_KEY, "viewer_anonymise", "viewer_may_have_key"]
              + [access.area_key(access.VIEWER, o) for o in access.AREAS])
group_backup = {k: (db.query_one("SELECT value FROM settings WHERE key = ?", (k,)) or {}).get("value")
                for k in GROUP_KEYS}
db.set_setting("viewer_may_have_key", "1")
mirek_id = viewer()["id"]
rights_key = api.create_token("Opravneni", mirek_id)


def status(path: str) -> int:
    return call(rights_key["token"], path).status_code


send_form(admin_client, "/settings/viewers", jellyfin_login="on", sees_overview="on", sees_network="on",
          anonymise="on")
check(access.anonymising_enabled(access.VIEWER), "the viewer group hides the names")
check(status("/api/v1/summary") == 200 and status("/api/v1/bandwidth") == 200
      and status("/api/v1/libraries") == 403,
      "the viewer group saved in Settings → Jellyfin: the key may exactly its areas")
accounts.save_permissions(mirek_id, access.pack_permissions(["library"], True))
check(status("/api/v1/libraries") == 200, "the account's own rights add an area the group does not have")
check(status("/api/v1/summary") == 403 and status("/api/v1/bandwidth") == 403,
      "and take away areas the group has")
accounts.save_permissions(mirek_id, access.pack_permissions(["users"], False))
check(OTHER_NAME in call(rights_key["token"], "/api/v1/users").text,
      "own anonymising off: the key sees the names, even though the group hides them")
accounts.save_permissions(mirek_id, access.pack_permissions(["users"], True))
check(OTHER_NAME not in call(rights_key["token"], "/api/v1/users").text,
      "own anonymising on: the names hidden")
accounts.save_may_have_key(mirek_id, "0")
check(status("/api/v1/") == 403, "the account's exception „no keys“ beats a group that allows them")
accounts.save_may_have_key(mirek_id, None)
accounts.save_permissions(mirek_id, None)
api.revoke_token(rights_key["id"])
with db.connect() as conn:
    for setting_key, value in group_backup.items():
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (setting_key,))
        else:
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (value, setting_key))
    conn.commit()
db.forget_settings()

# The Permissions window of a viewer links to their group in the Jellyfin section - outside
# the switch's <label>, otherwise the link is a part of the choice's name and a click closes
# the window without saving. A reader has no link, their group is right here.
accounts_page = admin_client.get("/settings?section=accounts").text


def rights_window(html: str, account_id: int) -> str:
    start = html.find(f'id="modal-permissions-{account_id}"')
    return html[start:html.find("</dialog>", start)] if start >= 0 else ""


mirek_window = rights_window(accounts_page, mirek_id)
check('href="/settings?section=jellyfin#viewers"' in mirek_window
      and not re.search(r"<label[^>]*>(?:(?!</label>).)*section=jellyfin#viewers", mirek_window, re.S),
      "a viewer's Permissions window links to the group in the Jellyfin section (outside the choice's label)")
check("section=jellyfin#viewers" not in rights_window(accounts_page, reader_id),
      "a reader's Permissions window does not have that link")


print("--- the texts about the permission ---")
page = admin_client.get("/settings?section=accounts").text
check("Smí si vyrábět klíče" in page and "Smí si ho vyrobit" not in page,
      "the choice in the Permissions window speaks of keys, not of one")
check("Vlastní API klíče" in page, "the heading and the label are in the plural")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
