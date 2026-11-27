# -*- coding: utf-8 -*-
"""Music in the API: /api/v1/music/summary, /top and /history.

Over the demo's made-up music: the shapes and raw numbers, the rights (music for the summary, music_insights for
the charts, music_who and the history tick for the listens), the anonymising of names - a root key with hidden
names, a Jellyfin viewer's own key (sees only its own listens, under its own name) and a reader with hidden names
see no other person's name or id -, and the 400 of a malformed request.

Run:
    .venv/bin/python tests/test_api_music.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "api_music.db")
os.environ["SECRET_KEY"] = "api-music-test-key-long-enough-0123456789"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, api, db, demo_world, demodata, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail if not condition else ''}")
    if not condition:
        failures += 1


db.init_db()
demodata.seed()
client = TestClient(web.app)


def ask(token: str, path: str) -> tuple[int, dict]:
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {}


def root(areas: set[str], anonymise: bool = False, history: bool = False) -> str:
    return api.create_token("root", permissions=api.pack_key_permissions(areas, anonymise, history))["token"]


everything = api.create_token("everything")["token"]
people = db.query_all("SELECT id, name FROM users")
names = {row["name"] for row in people}
ids = {row["id"] for row in people}

print("--- /api/v1/music/summary ---")
status, summary = ask(everything, "/api/v1/music/summary?days=30")
numbers = stats.music_numbers(stats.music_analysis(30))
check(status == 200 and set(summary) == {"days", "listening_seconds", "plays", "listeners", "artists", "albums", "tracks"},
      "the summary has its fields", f"({status} {sorted(summary)})")
check(summary.get("days") == 30 and summary.get("listening_seconds") == numbers["seconds"]
      and summary.get("plays") == numbers["plays"] and summary.get("listeners") == numbers["listener_count"]
      and isinstance(summary.get("albums"), int) and summary["albums"] > 0,
      "raw numbers, the same as the Music overview's", f"({summary})")

print("--- /api/v1/music/top ---")
for kind, extra in (("artists", {"artist", "album"}), ("albums", {"artist", "album"}), ("tracks", {"artist", "album"})):
    status, top = ask(everything, f"/api/v1/music/top?days=30&kind={kind}&limit=5")
    items = top.get("items") or []
    check(status == 200 and top.get("kind") == kind and 0 < len(items) <= 5
          and all({"id", "name", "plays", "listening_seconds", "listeners"} | extra <= set(i) for i in items)
          and [i["listening_seconds"] for i in items] == sorted((i["listening_seconds"] for i in items), reverse=True),
          f"top {kind}: fields and the most listened first (by listening time, as the Music page)", f"({status} {items[:1]})")
    check(all((i["artist"] is None) == (kind == "artists") and (i["album"] is None) == (kind != "tracks") for i in items),
          f"top {kind}: artist and album filled exactly where they mean something")
status, sorted_top = ask(everything, "/api/v1/music/top?days=30&kind=albums&sort=name&limit=3")
check(status == 200 and [i["name"] for i in sorted_top["items"]] == sorted(i["name"] for i in sorted_top["items"]),
      "the list takes the API's sort")

print("--- /api/v1/music/history ---")
status, history = ask(everything, "/api/v1/music/history?days=30&limit=20")
listens = history.get("listens") or []
check(status == 200 and 0 < len(listens) <= 20 and all(
    {"started_at", "user", "user_id", "track", "album", "artist", "item_id", "listened_seconds", "client", "device",
     "play_method"} <= set(r) for r in listens), "the listens have their fields", f"({status} {listens[:1]})")
check(all(isinstance(r["listened_seconds"], int) for r in listens) and any(r["user"] in names for r in listens),
      "a key with everything sees the real names")
check([r["started_at"] for r in listens] == sorted((r["started_at"] for r in listens), reverse=True), "the newest first")

print("--- the rights ---")
only_music = root({"music"})
check(ask(only_music, "/api/v1/music/summary")[0] == 200, "music opens the summary")
check(ask(only_music, "/api/v1/music/top")[0] == 403, "the charts need music_insights")
check(ask(only_music, "/api/v1/music/history")[0] == 403, "the listens need music_who")
who_without_history = root({"music", "music_who"})
check(ask(who_without_history, "/api/v1/music/history")[0] == 403, "music_who without the history tick is not enough")
check(ask(root({"library"}), "/api/v1/music/summary")[0] == 403, "a key without music gets nothing of it")

print("--- anonymising: no other person's name or id ---")


def leaks(data: dict, allowed_name: str = "", allowed_id: str = "") -> list[str]:
    text = json.dumps(data, ensure_ascii=False)
    return sorted({n for n in names if n != allowed_name and f'"{n}"' in text} |
                  {i for i in ids if i != allowed_id and i in text})


hidden = root({"music", "music_who", "music_insights"}, anonymise=True, history=True)
status, data = ask(hidden, "/api/v1/music/history?days=365&limit=200")
check(status == 200 and data.get("listens") and not leaks(data), "a root key with hidden names", f"({status} {leaks(data)[:3]})")
# A Jellyfin viewer with a key of their own: only their own listens, under their own name.
viewer_id = demo_world.jellyfin_id("demo-u2")
viewer_name = next(row["name"] for row in people if row["id"] == viewer_id)
viewer = accounts.from_jellyfin({"id": viewer_id, "name": viewer_name, "is_admin": False})
db.set_setting(access.LOGIN_KEY, "1")     # a viewer's key works only while sign-in through Jellyfin is on
for area in ("music", "music_who", "music_insights"):
    db.set_setting(access.area_key(access.VIEWER, access.BY_KEY[area]), "1")
db.forget_settings()
accounts.save_may_have_key(viewer["id"], "1")
own = api.create_own_token("mine", viewer["id"])
status, data = ask(own["token"], "/api/v1/music/history?days=365&limit=200")
check(status == 200 and data.get("listens") and all(r["user_id"] == viewer_id for r in data["listens"])
      and not leaks(data, viewer_name, viewer_id), "a viewer's own key: only their own listens", f"({status} {leaks(data, viewer_name, viewer_id)[:3]})")
other = next(i for i in ids if i != viewer_id)
check(ask(own["token"], f"/api/v1/music/history?user={other}")[0] == 403, "and another viewer's listens are a 403")
reader_id = accounts.create("reader", "readerpassword")
for area in ("music", "music_who", "music_insights"):
    db.set_setting(access.area_key(access.READER, access.BY_KEY[area]), "1")
db.set_setting("reader_anonymise", "1")
for area in ("library", "library_who"):    # History of a reader opens with "...who watched" (access.sees_history)
    db.set_setting(access.area_key(access.READER, access.BY_KEY[area]), "1")
db.forget_settings()
accounts.save_may_have_key(reader_id, "1")
reader_key = api.create_own_token("reader", reader_id)
status, data = ask(reader_key["token"], "/api/v1/music/history?days=365&limit=200")
check(status == 200 and data.get("listens") and not leaks(data), "a reader with hidden names", f"({status} {leaks(data)[:3]})")

print("--- a malformed request ---")
for path in ("/api/v1/music/summary?days=abc", "/api/v1/music/top?kind=films", "/api/v1/music/history?nothing[equals]=1"):
    status, data = ask(everything, path)
    check(status == 400 and data.get("error"), f"{path} is a 400 with an error code", f"({status} {data})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
