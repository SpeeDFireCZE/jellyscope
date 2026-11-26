# -*- coding: utf-8 -*-
r"""The read-only API: lets in only a token and nothing can be changed through it.

The API is the only way into Jellyscope that does not go through signing in. So
this test watches mainly what must NOT happen:

* **Without a token nobody learns anything.** Not "how many users there are",
  not what a library is called.
* **The token is not passed in the address.** The address shows up in a proxy's
  log, in the browser's history and in a link somebody passes on - and the token
  with it. Whoever tries to send it as `?token=` gets 401 like anybody else.
* **The database holds a fingerprint, not the token.** Whoever reads the database
  (from a backup, say) gains no access by it.
* **Nothing changes through the API.** Under `/api/` there are only GETs; a POST must
  end at 405, not at "the method is ignored and the page loads".
* **A revoked token stops working at once**, and the others are not touched.

Run:
    .venv/bin/python tests/test_api.py
"""
from __future__ import annotations

import os
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import api, db  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


db.init_db()

# A little data, so the answers have something to show.
start = datetime.now(timezone.utc) - timedelta(days=2)
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, size_bytes, tech_source, is_missing)"
        " VALUES ('i1', 'Duna', 'Movie', ?, 'jellyfin', 0)", (1024 ** 3,))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id,"
        " item_name, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active, play_method)"
        " VALUES ('s1','u1','Jana','i1','Duna',?,?,3600,0,0,1,'Transcode (v)')",
        (start.strftime(db.TIME_FORMAT), start.strftime(db.TIME_FORMAT)))
    conn.commit()

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

client = TestClient(app)

print("--- without a token nobody learns anything ---")
for path in ("/api/v1/", "/api/v1/summary", "/api/v1/now-playing",
             "/api/v1/library"):
    response = client.get(path)
    check(response.status_code == 401, f"{path} -> 401 ({response.status_code})")
check("Bearer" in client.get("/api/v1/summary").headers.get("www-authenticate", ""),
      "and the answer says how to prove oneself")

print()
print("--- with a token, yes ---")
created = api.create_token("Grafana")
TOKEN = created["token"]
header = {"Authorization": f"Bearer {TOKEN}"}

response = client.get("/api/v1/summary?days=30", headers=header)
check(response.status_code == 200, f"the summary passes ({response.status_code})")
data = response.json()
check(data["plays"] == 1, f"and carries numbers ({data['plays']} plays)")
check(data["watched_hours"] == 1.0, f"in hours ({data['watched_hours']})")
check(data["active_now"] == 1, "the number playing right now too")
check(data["transcode_share_percent"] == 100.0,
      f"and the share of transcodes ({data['transcode_share_percent']} %)")

playing = client.get("/api/v1/now-playing", headers=header).json()
check(playing["count"] == 1, f"playing right now: {playing['count']}")
check(playing["sessions"][0]["user"] == "Jana", "with the viewer's name")
check(playing["sessions"][0]["is_transcode"] is True, "and the transcode recognised")

library = client.get("/api/v1/library", headers=header).json()
check(library["items"] == 1 and library["size_bytes"] == 1024 ** 3,
      f"library: {library['items']} items, {library['size_human']}")
# Numbers on pages follow the page's language ("1.0 GB" in English); the JSON keeps the
# notation it always had - a client may read the text.
check(library["size_human"] == "1,0 GB", f"the API's size text keeps its notation ({library['size_human']})")

index = client.get("/api/v1/", headers=header).json()
check("/api/v1/summary" in index["endpoints"], "the index lists the addresses")

print()
print("--- the token belongs in the header, not in the address ---")
# The address shows up in a proxy's log and in the browser's history. If the API took the
# token from the address, it would be the most convenient and the worst solution at once.
check(client.get(f"/api/v1/summary?token={TOKEN}").status_code == 401,
      "a token in the address does not count")
check(client.get("/api/v1/summary",
                 headers={"Authorization": TOKEN}).status_code == 401,
      "nor without the word Bearer")
check(client.get("/api/v1/summary",
                 headers={"Authorization": "Bearer js_takovy_neexistuje"}).status_code == 401,
      "a made-up token not either")

print()
print("--- the database holds a fingerprint, not the token ---")
stored = db.query_all("SELECT token_hash, preview FROM api_tokens")
check(all(TOKEN not in str(r["token_hash"]) for r in stored),
      "the token itself is not in the database")
check(stored[0]["preview"] == TOKEN[:5],
      f"only the first five characters ({stored[0]['preview']})")
check(len(stored[0]["preview"]) == api.PREVIEW_CHARS,
      f"and not a character more ({len(stored[0]['preview'])})")
check(len(TOKEN) > 40, f"and it is long enough not to be guessed ({len(TOKEN)})")

print()
print("--- nothing can be changed through the API ---")
for path in ("/api/v1/summary", "/api/v1/library", "/api/v1/now-playing"):
    response = client.post(path, headers=header, json={"neco": 1})
    check(response.status_code == 405,
          f"POST {path} -> 405 ({response.status_code})")
    response = client.delete(path, headers=header)
    check(response.status_code == 405,
          f"DELETE {path} -> 405 ({response.status_code})")

print()
print("--- last used ---")
row = db.query_one("SELECT last_used_at FROM api_tokens LIMIT 1")
check(bool(row["last_used_at"]), "the use was written down")

print()
print("--- revoking holds at once and only for that one ---")
second = api.create_token("Homepage")
check(len(api.list_tokens()) == 2, f"there are two tokens ({len(api.list_tokens())})")

revoked = api.revoke_token(db.query_value("SELECT id FROM api_tokens WHERE name = 'Grafana'"))
check(revoked == "Grafana", f"the right one was revoked ({revoked})")
check(client.get("/api/v1/summary", headers=header).status_code == 401,
      "the revoked token stopped working")
check(client.get("/api/v1/summary",
                 headers={"Authorization": f"Bearer {second['token']}"}
                 ).status_code == 200,
      "the second token still works")

print()
print("--- nonsense in ?days= returns 400 and breaks nothing ---")
# `days` comes from the address, so from anybody, and ends in a query to the
# database. It need not be a number, and it need not be a sensible number: empty,
# letters, a negative value, a piece of SQL. It used to be quietly evened out to a
# sensible number of days and the data came back; now it is 400 - and still nothing
# falls over and nothing goes into the query.
for naughty in ("999999999999999999999", "-1", "abc", "", "1;DROP TABLE playback",
                "1 OR 1=1", "0", "1e400", "../../etc/passwd", "NaN"):
    response = client.get(f"/api/v1/summary?days={naughty}",
                          headers={"Authorization": f"Bearer {second['token']}"})
    check(response.status_code == 400 and response.json().get("error") == "invalid_value",
          f"days={naughty[:24]!r} -> 400 invalid_value ({response.status_code})")
response = client.get("/api/v1/summary?days=30",
                      headers={"Authorization": f"Bearer {second['token']}"})
check(response.status_code == 200 and response.json().get("days") == 30, "days=30 passes")

# The table the attack tried to drop is still there.
check(db.query_value("SELECT COUNT(*) FROM playback") is not None,
      "and the playback table survived the attack")

print()
print("--- keys are managed only by the administrator, in their section ---")
import re  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword", is_admin=False)

with TestClient(app) as reader:
    reader.post("/login", data={"username": "reader", "password": "longpassword"},
                follow_redirects=False)
    # Settings are for the administrator only, so a reader does not even get to
    # the API section - and learns nothing about the keys.
    page = reader.get("/settings?section=api")
    check(page.status_code == 403, "a reader does not get into Settings")
    check("modal-api-docs" not in page.text,
          "and does not see the API section")
    check("Vyrobit klíč" not in page.text, "nor the button that makes a key")
    response = reader.post("/settings/api/token", data={"name": "podvrh", "mode": "all"},
                           follow_redirects=False)
    check(response.status_code == 403, f"and may not make a key ({response.status_code})")

with TestClient(app) as admin:
    admin.post("/login", data={"username": "admin", "password": "longpassword"},
               follow_redirects=False)
    page = admin.get("/settings?section=api")
    check(page.status_code == 200, "the administrator sees the API section")
    check("modal-api-docs" in page.text,
          "and it has the button with the documentation")
    check("/api/v1/summary" in page.text, "which lists the addresses")

    before = len(api.list_tokens())
    response = admin.post("/settings/api/token", data={"name": "Ze stránky", "mode": "all"},
                          follow_redirects=False)
    check(response.status_code == 303, "making a key redirects")
    check(len(api.list_tokens()) == before + 1, "and the key was added")

    # The key is shown ONCE. That is the most important property of the whole
    # screen: the second time the page has nowhere to take it from, because only
    # the fingerprint is stored.
    page = admin.get("/settings?section=api").text
    whole = re.findall(r"js_[A-Za-z0-9_-]{20,}", page)
    check(len(whole) == 1, f"the key is shown whole once ({len(whole)}x)")

    again = admin.get("/settings?section=api").text
    check(not re.findall(r"js_[A-Za-z0-9_-]{20,}", again),
          "on the next load it is no longer there whole")
    check(whole[0][:5] in again, "its first five characters are left")
    check("*" * 24 in again, "and the rest are asterisks")

    # And nowhere else on the pages at all.
    for path in ("/settings?section=accounts", "/settings?section=general", "/"):
        check(not re.findall(r"js_[A-Za-z0-9_-]{20,}", admin.get(path).text),
              f"the whole key is not shown elsewhere either: {path}")

    # Revoking from the page.
    last = api.list_tokens()[0]
    response = admin.post("/settings/api/token/revoke",
                          data={"token_id": last["id"]},
                          follow_redirects=False)
    check(response.status_code == 303, "revoking redirects")
    check(all(r["id"] != last["id"] for r in api.list_tokens()),
          "and the key is gone from the list")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
