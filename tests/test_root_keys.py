# -*- coding: utf-8 -*-
r"""Root keys: an API key without an owner, with rights of its own.

A user key belongs to an account and follows its rights. A root key is made
by the administrator in Settings → API: it belongs to nobody and may only what the administrator
set for it - areas, the playback history, hidden names. Watched here:

* the rights are enforced at every request and a change holds at once;
* the history only with the tick (for accounts it is always seen);
* broken rights in the database never mean full rights;
* the key does not depend on the administrator who made it.

Run:
    .venv/bin/python tests/test_root_keys.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "root.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import api, api_endpoints, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
before = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime(db.TIME_FORMAT)  # noqa: E731
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES ('u1', 'Jana', 0, 0), ('u2', 'Petr', 0, 0)")
    conn.execute("INSERT INTO items (id, name, type, size_bytes, library_id, date_created,"
                 " path, tech_source, is_missing) VALUES ('i1', 'Duna', 'Movie', 1000,"
                 " 'lib1', ?, '/media/filmy/duna.mkv', 'jellyfin', 0)", (before(400),))
    for k, (uid, name) in enumerate((("u1", "Jana"), ("u2", "Petr"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, 3600, 0, 0, 0, 'DirectPlay', 'Web')",
            (f"s{k}", uid, name, before(1 + k), before(1 + k)))
    conn.commit()

client = TestClient(app)


def get(token: str, address: str):
    return client.get(address, headers={"Authorization": f"Bearer {token}"})


def code(response) -> str | None:
    try:
        return response.json().get("error")
    except ValueError:
        return None


print("--- the check ---")
widget = api.create_token("Widget", None, api.pack_key_permissions(["library"], False, False))
W = widget["token"]
check(get(W, "/api/v1/libraries").status_code == 200, "root with the Library: /libraries 200")
response = get(W, "/api/v1/users")
check(response.status_code == 403 and code(response) == "area_not_allowed",
      f"outside the areas: /users 403 area_not_allowed ({response.status_code})")
response = get(W, "/api/v1/history")
check(response.status_code == 403 and code(response) == "area_not_allowed",
      f"the history without the tick: 403 ({response.status_code})")
endpoints = get(W, "/api/v1/").json().get("endpoints", {})
check("/api/v1/libraries" in endpoints and "/api/v1/users" not in endpoints
      and "/api/v1/history" not in endpoints,
      f"the signpost only the allowed addresses ({sorted(endpoints)})")

result = api.save_key_permissions(widget["id"], api.pack_key_permissions(["library"], False, True))
check(result is not None and result["name"] == "Widget", "save_key_permissions returns the name")
history = get(W, "/api/v1/history")
names = {p["user"] for p in history.json().get("playbacks", [])} if history.status_code == 200 else set()
check(history.status_code == 200 and names == {"Jana", "Petr"},
      f"with the history: 200 and for all viewers ({history.status_code}, {names})")

api.save_key_permissions(widget["id"], api.pack_key_permissions(["library"], True, True))
names = {p["user"] for p in get(W, "/api/v1/history").json().get("playbacks", [])}
check(names and not names & {"Jana", "Petr"}, f"with hidden names the real ones are not there ({names})")
check(get(W, "/api/v1/history?user[contains]=Pe").json().get("playbacks") == [],
      "and the filter does not find the real name")

api.save_key_permissions(widget["id"], api.pack_key_permissions(["users"], False, False))
check(get(W, "/api/v1/users").status_code == 200 and get(W, "/api/v1/libraries").status_code == 403,
      "a change of the rights holds from the next request")

sub_only = api.create_token("Pod", None, api.pack_key_permissions(["library_who"], False, False))
check(get(sub_only["token"], "/api/v1/library").status_code == 403,
      "a sub-area without its parent: /library 403 (the parent area rule)")

server_key = api.create_token("Grafana")
paths = sorted(r.path for r in api.router.routes if "GET" in getattr(r, "methods", ()))


def answers(path: str) -> bool:
    """200, or for an *Arr app's or Seerr's address the 404 that says it is not connected (none is, here)."""
    endpoint = api_endpoints.by_path(path)
    response = get(server_key["token"], path.replace("{app}", ((endpoint.apps if endpoint else None) or ("sonarr",))[0]))
    return response.status_code == 200 or (response.status_code == 404 and response.json().get("error") == "app_not_connected")


wrong = [c for c in paths if "{item_id}" not in c and not answers(c)]
check(not wrong, f"a key without an owner and without rights = everything ({wrong})")

with db.connect() as conn:
    conn.execute("UPDATE api_tokens SET permissions = 'nesmysl{' WHERE id = ?", (sub_only["id"],))
    conn.commit()
check(get(sub_only["token"], "/api/v1/libraries").status_code == 403
      and get(sub_only["token"], "/api/v1/history").status_code == 403,
      "a broken JSON in the rights = nothing, not everything")
check(api.key_permissions("nesmysl{") == {"areas": set(), "anonymise": True, "history": False},
      "key_permissions: a broken JSON -> no rights, names hidden")
check(api.key_permissions(None) is None, "key_permissions: NULL = everything")
# A JSON edited by hand that can be read, but has wrong types: bool("false")
# is True - the history and the real names would pass. Only a real true opens.
check(api.key_permissions('{"areas": ["library"], "history": "false", "anonymise": "false"}')
      == {"areas": {"library"}, "anonymise": True, "history": False},
      "key_permissions: \"false\" as text does not open the history and does not reveal the names")
check(api.key_permissions('{"areas": ["users"]}')
      == {"areas": {"users"}, "anonymise": True, "history": False},
      "key_permissions: missing keys = no history, names hidden")
with db.connect() as conn:
    conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?",
                 ('{"areas": ["users"], "history": "false"}', sub_only["id"]))
    conn.commit()
users_response = get(sub_only["token"], "/api/v1/users")
check(get(sub_only["token"], "/api/v1/history").status_code == 403
      and users_response.status_code == 200
      and not {"Jana", "Petr"} & {u.get("name") for u in users_response.json().get("users", [])},
      "rights edited by hand with wrong types: without the history and with hidden names")
check(api.key_permissions(api.pack_key_permissions(["library", "neexistuje"], False, True))
      == {"areas": {"library"}, "anonymise": False, "history": True},
      "pack_key_permissions throws an unknown area away")

from jellyscope import accounts  # noqa: E402

admin_id = accounts.create("admin", "longpassword", is_admin=True)
user_key = api.create_token("Muj", admin_id)
check(api.save_key_permissions(user_key["id"], api.pack_key_permissions([], False, False)) is None
      and db.query_value("SELECT permissions FROM api_tokens WHERE id = ?", (user_key["id"],),
                         default=None) is None,
      "a user key cannot be changed through save_key_permissions")
check(api.permissions_for_log(None) == "all"
      and api.permissions_for_log(api.pack_key_permissions(["charts", "library"], False, True))
      == "areas=charts,library history=yes hide_names=no",
      "the description of the rights for the log")
check(any(r["id"] == widget["id"] and "permissions" in r for r in api.list_tokens()), "the list carries the rights")


print("--- Settings ---")
from jellyscope import access  # noqa: E402

# The application log's messages are caught straight from the logger - the log file
# is not created in the test.
class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


capture = Capture()
logging.getLogger("jellyscope").addHandler(capture)
logging.getLogger("jellyscope").setLevel(logging.INFO)

accounts.create("reader", "readerpass")
FULL_KEY = r"js_[A-Za-z0-9_\-]{20,}"


def sign_in(name: str, password: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


def send(k: TestClient, address: str, **data):
    return k.post(address, data=data, follow_redirects=False)


def root_keys() -> dict:
    return {r["id"]: r for r in api.list_tokens() if r["account_id"] is None}


def create_key(k: TestClient, **data) -> tuple[dict, str]:
    """Creates a key through the form; returns its row and the whole key from the page."""
    before = set(root_keys())
    send(k, "/settings/api/token", **data)
    new = [r for i, r in root_keys().items() if i not in before]
    full = re.findall(FULL_KEY, k.get("/settings?section=api").text)
    return (new[0] if len(new) == 1 else {}), (full[0] if len(full) == 1 else "")


admin_k = sign_in("admin", "longpassword")
reader_k = sign_in("reader", "readerpass")

board, N = create_key(admin_k, name="Nastenka", mode="selected",
                      sees_library="on", sees_neexistuje="on")
check(board.get("account_id", 0) is None
      and api.key_permissions(board.get("permissions"))
      == {"areas": {"library"}, "anonymise": False, "history": False},
      f"only the chosen: a root key only with the Library, an unknown area thrown away ({board.get('permissions')})")
check(get(N, "/api/v1/libraries").status_code == 200 and get(N, "/api/v1/users").status_code == 403,
      "the shown key may only its area")
check(any("Admin admin created root key Nastenka" in r
          and "areas=library history=no hide_names=no" in r for r in capture.lines),
      f"the creation is in the application log with the rights ({capture.lines[-3:]})")

all_key, _ = create_key(admin_k, name="Vse", mode="all", sees_library="on")
check(bool(all_key) and all_key["permissions"] is None, "Everything: the rights NULL")

history_only, H = create_key(admin_k, name="JenHistorie", mode="selected", history="on")
check(get(H, "/api/v1/history").status_code == 200 and get(H, "/api/v1/libraries").status_code == 403,
      "only the history without an area: the key is created and may only the history")

count = len(api.list_tokens())
send(admin_k, "/settings/api/token", name="Prazdny", mode="selected", anonymise="on")
check(len(api.list_tokens()) == count, "only the chosen without an area and without the history: the key is not created")
check("aspoň jednu oblast nebo historii" in admin_k.get("/settings?section=api").text,
      "and the page says why")
# The custom dashboard and the comparison have no address in the API (languages got one, 27. 11. 2026) - a key only
# with them could do nothing and the administrator would not know why.
count_before = len(api.list_tokens())
send(admin_k, "/settings/api/token", name="Jazyky", mode="selected", sees_dashboard="on",
     sees_comparison="on")
check(len(api.list_tokens()) == count_before
      and "nic neotevírají" in admin_k.get("/settings?section=api").text,
      "areas without an address in the API: the key is not created and the page says why")
check(api.root_key_paths(api.pack_key_permissions(["comparison"], False, False)) == []
      and "/api/v1/languages" in api.root_key_paths(api.pack_key_permissions(["languages"], False, False))
      and "/api/v1/history" in api.root_key_paths(api.pack_key_permissions([], False, True))
      and "/api/v1/libraries" in api.root_key_paths(api.pack_key_permissions(["library"], False, False)),
      "root_key_paths: what a key with rights really opens")
upload = admin_k.post("/settings/api/token", data={"mode": "all"},
                      files={"name": ("klic.txt", b"obsah", "text/plain")}, follow_redirects=False)
check(upload.status_code == 303 and not any("UploadFile" in r["name"] for r in api.list_tokens()),
      "a file sent instead of a name does not become the key's name")
check("klíč serveru" not in (access.anonymise_for.__doc__ or ""), "the docstring speaks about a root key")
count = len(api.list_tokens())
for wrong in ({"mode": "cokoliv", "sees_library": "on"}, {"sees_library": "on"}):
    send(admin_k, "/settings/api/token", name="Divny", **wrong)
check(len(api.list_tokens()) == count, "an unknown or missing mode: the key is not created")

check(send(reader_k, "/settings/api/token", name="Cizi", mode="all").status_code == 403
      and len(api.list_tokens()) == count, "a reader does not create a root key (403)")
check(send(reader_k, "/settings/api/token/permissions", token_id=board["id"], mode="all").status_code == 403
      and root_keys()[board["id"]]["permissions"] == board["permissions"], "nor edits one (403)")

send(admin_k, "/settings/api/token/permissions", token_id=user_key["id"],
     mode="selected", sees_library="on")
check(db.query_value("SELECT permissions FROM api_tokens WHERE id = ?", (user_key["id"],),
                     default=None) is None,
      "the root key's form does not change a user key")
check("Takový root klíč tu není" in admin_k.get("/settings?section=api").text, "and says so")

response = send(admin_k, "/settings/api/token/permissions", token_id=board["id"],
                mode="selected", sees_users="on", history="on")
check(response.status_code == 303 and get(N, "/api/v1/users").status_code == 200
      and get(N, "/api/v1/libraries").status_code == 403
      and get(N, "/api/v1/history").status_code == 200,
      "an edit through Settings holds from the next query")
check(any("Admin admin changed the rights of root key Nastenka" in r
          and "areas=library history=no hide_names=no -> areas=users history=yes hide_names=no" in r
          for r in capture.lines),
      f"the change is in the application log: who, before, after ({capture.lines[-2:]})")
send(admin_k, "/settings/api/token/permissions", token_id=board["id"], mode="selected")
check(get(N, "/api/v1/users").status_code == 200, "an invalid edit (nothing ticked) does not change the rights")

print("--- independence of the administrator ---")
second_id = accounts.create("druhy", "secondpass", is_admin=True)
second_k = sign_in("druhy", "secondpass")
_, D = create_key(second_k, name="Druheho", mode="selected", sees_library="on")


def works() -> bool:
    return (get(D, "/api/v1/libraries").status_code == 200
            and get(D, "/api/v1/users").status_code == 403)


check(bool(D) and works(), "the second administrator's root key works")
accounts.set_admin(second_id, False)
check(works(), "even when the administrator loses the role")
accounts.delete(second_id)
check(works(), "even when their account is deleted")

print("--- the page ---")


def key_row(html: str, name: str) -> str:
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if f">{name}<" in row:
            return re.sub(r"\s+", " ", row)
    return ""


html = admin_k.get("/settings?section=api").text
check("Root · všechno" in key_row(html, "Grafana"), "the server's key: Root · všechno")
check("Root · Uživatelé, historie" in key_row(html, "Nastenka"),
      f"chosen: a list of the areas and the history ({key_row(html, 'Nastenka')[:300]})")
check("Uživatelský · " in key_row(html, "Muj") and "admin" in key_row(html, "Muj"),
      "a user key: Uživatelský · the account")
check(f'data-modal="modal-key-permissions-{board["id"]}"' in html
      and f'id="modal-key-permissions-{board["id"]}"' in html
      and f'modal-key-permissions-{user_key["id"]}"' not in html,
      "the Rights button and dialog only by root keys")
check('id="modal-new-key"' in html and 'data-modal="modal-new-key"' in html,
      "a new key is made in a dialog")

all_fields = {f"sees_{o.key}" for o in access.AREAS}
accounts_page = admin_k.get("/settings?section=accounts").text
dialog = re.search(r'<dialog[^>]*id="modal-permissions-\d+".*?</dialog>', accounts_page, re.S)
check(dialog is not None and set(re.findall(r'name="(sees_[a-z_]+)"', dialog.group(0))) == all_fields,
      "the account's Rights dialog still has all the fields after the tree moved into a macro")
check(len(re.findall(r'name="sees_[a-z_]+" form="readers-form"', accounts_page)) == len(all_fields),
      "the readers' group tree too (with form=readers-form)")
jf = admin_k.get("/settings?section=jellyfin").text
check(len(re.findall(r'name="sees_[a-z_]+" form="viewers-form"', jf)) == len(all_fields),
      "the viewers' tree in the Jellyfin section too (with form=viewers-form)")

print("--- the log ---")
get(user_key["token"], "/api/v1/")


def body(html: str) -> str:
    match = re.search(r"<tbody>.*?</tbody>", html, re.S)
    return re.sub(r"\s+", " ", match.group(0)) if match else ""


log_api = admin_k.get("/settings?section=log&log_kind=api").text
check("root klíč" in body(log_api) and ">Muj<" in body(log_api).replace("> Muj <", ">Muj<"),
      "the API log: “root klíč” by a query with a root key, the user key is there too")
option = re.search(r'<option value="-"[^>]*>(.*?)</option>', log_api, re.S)
check(option is not None and option.group(1).strip() == "root klíče",
      "the user choice: the option “root klíče” with the value -")
check(re.search(rf'<option value="{board["id"]}"[^>]*>\s*Nastenka \(root klíč\)', log_api) is not None,
      "the key choice: “(root klíč)” by a root key")
root_only = body(admin_k.get("/settings?section=log&log_kind=api&log_account=-").text)
check("Nastenka" in root_only and "admin" not in root_only,
      "the filter log_account=- returns only root keys")
check("klíč serveru" not in log_api and "klíč serveru" not in admin_k.get("/settings?section=api").text,
      "“klíč serveru” is no longer on the API nor the Log pages")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
