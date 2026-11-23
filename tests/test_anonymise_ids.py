# -*- coding: utf-8 -*-
r"""With the names hidden, a viewer is known only by a stand-in id.

Pages and the API show an account with hidden names the viewer "Viewer 2"
instead, and instead of their id `skryto-2`. But whoever knew the real id
from Jellyfin (it is in the profile's address, say) used to ask for it
directly - `?user=<id>`, `/users/<id>` - and knew at once that "Viewer 2" is
Petr. And the viewer menu on History listed the real names and ids outright.
The other way round, the stand-in id the pages show themselves did not work:
a link to the viewer led to a 404.

Watched here:

* the stand-in id is translated back to the viewer (the viewer's page, the
  History filter, `?user=` in the API);
* the real id of somebody else finds nothing;
* the menu on History has no real names or ids and is not sorted by name
  (the order would give the names away);
* without hiding names (the administrator, an open key) nothing changes.

Run:
    .venv/bin/python tests/test_anonymise_ids.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "anonym_id.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Ids and names deliberately in the opposite order: by id the first is Zdena, by
# name Adam. A menu sorted by name would thus give away who is who.
ZDENA, ADAM = "a1aaaaaa", "b2bbbbbb"
db.init_db()
when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib1', 'Filmy', 'movies')")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled)"
                 " VALUES (?, 'Zdena', 0, 0), (?, 'Adam', 0, 0)", (ZDENA, ADAM))
    conn.execute("INSERT INTO items (id, name, type, library_id, tech_source, is_missing)"
                 " VALUES ('i1', 'Duna', 'Movie', 'lib1', 'jellyfin', 0)")
    for k, (who, name) in enumerate(((ZDENA, "Zdena"), (ADAM, "Adam"), (ADAM, "Adam"))):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " is_paused, is_active, play_method, client)"
            " VALUES (?, ?, ?, 'i1', 'Duna', 'Movie', ?, ?, 3600, 0, 0, 0, 'DirectPlay', 'Web')",
            (f"s{k}", who, name, when, when))
    conn.commit()

STAND_IN = {real: access.anonymise([{"user_id": real}], "")[0]["user_id"]
            for real in (ZDENA, ADAM)}
print("stand-in ids:", STAND_IN)
db.set_setting("reader_anonymise", "1")
db.forget_settings()

print("--- the rule ---")
reader_account = {"username": "x", "is_admin": False}
check(access.viewer_from_address(STAND_IN[ADAM], reader_account) == ADAM,
      "the stand-in id is translated back to the viewer")
check(access.viewer_from_address(ADAM, reader_account) not in (ADAM, None, ""),
      "somebody else's real id leads to nobody (not to „everybody“ either)")
for nonsense in ("skryto-0", "skryto-99", "skryto-?", "skryto-1x"):
    check(access.viewer_from_address(nonsense, reader_account) not in (ADAM, ZDENA, None, ""),
          f"{nonsense}: leads to nobody")
# A viewer from Jellyfin has the names hidden from the default setting already.
viewer_account = {"username": "adam", "is_admin": False, "jellyfin_user_id": ADAM}
check(access.viewer_from_address(ADAM, viewer_account) == ADAM,
      "one's own real id stays (the viewer knows about themselves)")
check(access.viewer_from_address(ADAM, {"username": "s", "is_admin": True}) == ADAM,
      "without hiding names (the administrator) the id does not change")
check(access.viewer_from_address(None, reader_account) is None
      and access.viewer_from_address("", reader_account) == "",
      "without an id it stays without an id")

print("--- a reader's pages with hidden names ---")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass")


def sign_in(name: str, password: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


reader = sign_in("reader", "readerpass")
links = sorted(set(re.findall(r'href="/users/([^"?/]+)', reader.get("/users").text)))
check(links == sorted(STAND_IN.values()), f"the viewer list links to the stand-in ids ({links})")
for link in links:
    page = reader.get(f"/users/{link}")
    check(page.status_code == 200 and "Adam" not in page.text and "Zdena" not in page.text,
          f"/users/{link} opens ({page.status_code}) and does not show the name")
check(reader.get(f"/users/{ADAM}").status_code == 404, "/users/<real id> finds nothing")

history = reader.get("/history").text
menu = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>',
                  history.split('name="user_id"', 1)[-1].split("</select>", 1)[0])
check(menu and not {"Adam", "Zdena"} & {name.strip() for _, name in menu}
      and not {ADAM, ZDENA} & {value for value, _ in menu},
      f"the viewer menu on History without real names and ids ({menu})")
numbers = [name.strip() for value, name in menu if value]
check(numbers == sorted(numbers), f"the menu is sorted by number, not by the real name ({numbers})")


def history_rows(text: str) -> list[str]:
    table = text.split('name="user_id"', 1)[0]
    return re.findall(r'href="/users/([^"?/]+)"', table)


only_adam = reader.get(f"/history?user_id={STAND_IN[ADAM]}").text
check(history_rows(only_adam) == [STAND_IN[ADAM]] * 2,
      f"the History filter by the stand-in id finds their plays ({history_rows(only_adam)})")
check(re.search(rf'<option value="{STAND_IN[ADAM]}"\s+selected', only_adam) is not None,
      "and it is selected in the menu")
# The filter's chip above the table reads the context key `user_name` - anonymising must know it too.
chip = re.search(r'<span class="chip active">([^<]*)</span>', only_adam)
check(chip is not None and "Adam" not in chip.group(1) and "Viewer" in chip.group(1),
      f"the filter's chip at the top shows the nickname, not the real name ({chip.group(1) if chip else None})")
real_one = reader.get(f"/history?user_id={ADAM}").text
check(history_rows(real_one) == [] and "Adam" not in real_one,
      f"the filter by the real id finds nothing ({history_rows(real_one)})")

print("--- the administrator (no hiding) ---")
admin = sign_in("admin", "longpassword")
check(admin.get(f"/users/{ADAM}").status_code == 200, "the administrator opens a viewer by the real id")
check(f'<option value="{ADAM}"' in admin.get("/history").text,
      "the administrator has the real ids in the menu")

print("--- API ---")
key = api.create_token("Widget", None, api.pack_key_permissions([], True, True))["token"]
H = {"Authorization": f"Bearer {key}"}
anonymous = TestClient(app)


def who_in_history(address: str, headers: dict[str, str]) -> list[str]:
    return [p["user_id"] for p in anonymous.get(address, headers=headers).json().get("playbacks", [])]


check(who_in_history(f"/api/v1/history?user={STAND_IN[ADAM]}", H) == [STAND_IN[ADAM]] * 2,
      "API ?user=<stand-in id> returns their plays")
check(who_in_history(f"/api/v1/history?user={ADAM}", H) == [],
      "API ?user=<real id> finds nothing")
open_key = {"Authorization": f"Bearer {api.create_token('Grafana')['token']}"}
check(who_in_history(f"/api/v1/history?user={ADAM}", open_key) == [ADAM] * 2,
      "a key without hiding names still looks up by the real id")

print()
print("--- a reader: a row without user_id is somebody else's too ---")
# An import from Playback Reporting can carry a name without a UserId. A reader has
# no viewer of their own, so every row is somebody else's for them - the one without an id too.
without_id = access.anonymise({"user_name": "PetrBezId", "label": "x"}, "")
check(without_id["user_name"] != "PetrBezId", f"for a reader a name without user_id is hidden ({without_id['user_name']})")
viewer_without_id = access.anonymise({"user_name": "PetrBezId"}, "u-jana")
check(viewer_without_id["user_name"] != "PetrBezId", "for a viewer too (as before)")

print()
print("--- a library with hours is not a viewer ---")
# A library's tile has id, name and hours (the length of its content). If that were enough
# for a viewer, the library would be called "Viewer ?" and the link would lead nowhere.
library = {"id": "filmy", "name": "Filmy", "collection_type": "movies", "item_count": 3, "hours": 4.0}
for mine in ("", "u-jana"):
    out = access.anonymise(dict(library), mine)
    check(out["id"] == "filmy" and out["name"] == "Filmy",
          f"the library stays a library (my_id={mine!r}: {out['id']}, {out['name']})")
viewer_table = {"id": "u-petr", "name": "PetrZQX", "is_administrator": 0, "hours": 2.0, "last_seen": "x"}
out = access.anonymise(dict(viewer_table), "u-jana")
check(out["name"] != "PetrZQX", "a row of the viewer table is still hidden")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
