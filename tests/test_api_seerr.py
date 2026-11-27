# -*- coding: utf-8 -*-
"""Seerr in the API: /api/v1/seerr/requests, /pending (live) and /stats.

In the demo mode, against the made-up Seerr (demo_apps): the shapes and raw numbers, the status filter, the
requester only with "...who requested" (seerr_who) and never past the anonymising - a root key with hidden names,
a Jellyfin viewer's own key and a reader with hidden names see no other person's name or id -, and a Seerr that
is not connected (404 app_not_connected).

Run:
    .venv/bin/python tests/test_api_seerr.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api_seerr.db")
os.environ["SECRET_KEY"] = "api-seerr-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from _network import refuse_network  # noqa: E402
from jellyscope import access, accounts, api, db, demo_world, demodata, seerr, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


refuse_network()
db.init_db()
demodata.prepare(quiet=True)
client = TestClient(web.app)


def ask(token: str, path: str) -> tuple[int, dict]:
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    return response.status_code, response.json()


def root(areas: set[str], anonymise: bool = False) -> str:
    return api.create_token("root", permissions=api.pack_key_permissions(areas, anonymise, False))["token"]


everything = api.create_token("everything")["token"]
people = db.query_all("SELECT id, name FROM users")
names, ids = {row["name"] for row in people}, {row["id"] for row in people}
REQUEST = {"id", "kind", "title", "tmdb_id", "status", "requested_at", "is_4k", "requester", "requester_id"}

print("--- /api/v1/seerr/requests ---")
status, data = ask(everything, "/api/v1/seerr/requests?days=30")
requests = data.get("requests") or []
check(status == 200 and requests and all(set(r) == REQUEST for r in requests)
      and all(r["kind"] in ("movie", "tv") and r["status"] for r in requests)
      and any(r["requester"] in names for r in requests), "the requests with their fields and requesters", f"({status} {requests[:1]})")
check([r["requested_at"] for r in requests] == sorted((r["requested_at"] for r in requests), reverse=True), "the newest first")
status, waiting = ask(everything, "/api/v1/seerr/requests?days=365&status=waiting")
check(status == 200 and waiting["requests"] and all(r["status"] == "waiting" for r in waiting["requests"]), "the status filter")
check(ask(everything, "/api/v1/seerr/requests?status=nonsense")[0] == 400, "an unknown status is a 400")

print("--- /api/v1/seerr/pending (live) ---")
status, data = ask(everything, "/api/v1/seerr/pending")
check(status == 200 and data.get("available") is True and len(data.get("requests") or []) == 2
      and all(set(r) == REQUEST | {"waiting_seconds"} and r["status"] == "waiting" and r["waiting_seconds"] > 0 for r in data["requests"]),
      "the two waiting requests, live", f"({status} {data})")

print("--- /api/v1/seerr/stats ---")
status, data = ask(everything, "/api/v1/seerr/stats?days=30")
check(status == 200 and set(data) == {"days", "requests", "movies", "series", "waiting", "approved", "available", "declined",
                                      "approval_median_seconds", "to_library_median_seconds", "watched_by_requester",
                                      "watched_by_others", "unwatched"}
      and data["requests"] > 0 and data["approval_median_seconds"] > 0 and data["watched_by_requester"] > 0,
      "the statistics", f"({status} {data})")

print("--- the requester only with seerr_who, never past the anonymising ---")


def leaks(data: dict, allowed_name: str = "", allowed_id: str = "") -> list[str]:
    text = json.dumps(data, ensure_ascii=False)
    return sorted({n for n in names if n != allowed_name and f'"{n}"' in text} | {i for i in ids if i != allowed_id and i in text})


without_who = root({"seerr"})
for path in ("/api/v1/seerr/requests?days=365", "/api/v1/seerr/pending"):
    status, data = ask(without_who, path)
    check(status == 200 and data["requests"] and all(r["requester"] is None and r["requester_id"] is None for r in data["requests"])
          and not leaks(data), f"{path} without seerr_who: no requester", f"({status} {leaks(data)[:3]})")
hidden = root({"seerr", "seerr_who"}, anonymise=True)
for path in ("/api/v1/seerr/requests?days=365", "/api/v1/seerr/pending"):
    status, data = ask(hidden, path)
    check(status == 200 and data["requests"] and all(r["requester"] for r in data["requests"]) and not leaks(data),
          f"{path} with hidden names: a substitute, never the name", f"({status} {leaks(data)[:3]})")
viewer_id = demo_world.jellyfin_id("demo-u2")
viewer_name = next(row["name"] for row in people if row["id"] == viewer_id)
viewer = accounts.from_jellyfin({"id": viewer_id, "name": viewer_name, "is_admin": False})
db.set_setting(access.LOGIN_KEY, "1")
for area in ("seerr", "seerr_who"):
    db.set_setting(access.area_key(access.VIEWER, access.BY_KEY[area]), "1")
db.forget_settings()
accounts.save_may_have_key(viewer["id"], "1")
own = api.create_own_token("mine", viewer["id"])["token"]
status, data = ask(own, "/api/v1/seerr/requests?days=365")
check(status == 200 and data["requests"] and not leaks(data, viewer_name, viewer_id),
      "a viewer's own key: only their own name", f"({status} {leaks(data, viewer_name, viewer_id)[:3]})")
reader_id = accounts.create("reader", "readerpassword")
for area in ("seerr", "seerr_who"):
    db.set_setting(access.area_key(access.READER, access.BY_KEY[area]), "1")
db.set_setting("reader_anonymise", "1")
db.forget_settings()
accounts.save_may_have_key(reader_id, "1")
reader = api.create_own_token("reader", reader_id)["token"]
status, data = ask(reader, "/api/v1/seerr/requests?days=365")
check(status == 200 and data["requests"] and not leaks(data), "a reader with hidden names", f"({status} {leaks(data)[:3]})")

print("--- the rights and a Seerr that is not connected ---")
check(ask(root({"library"}), "/api/v1/seerr/stats")[0] == 403, "a key without seerr: 403")
seerr.save_settings(False, "", "")
status, data = ask(everything, "/api/v1/seerr/requests")
check(status == 404 and data.get("error") == "app_not_connected", "Seerr not connected: 404 app_not_connected", f"({status} {data})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
