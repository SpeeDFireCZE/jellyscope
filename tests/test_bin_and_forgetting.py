# -*- coding: utf-8 -*-
r"""A forgotten viewer goes to the bin - until the night they can be brought back, then they are gone.

Deleting a viewer's history cannot be undone, and it is easy to click on it by mistake.
That is why the rows are not deleted straight away: **they move into the bin**. The viewer
disappears from the statistics at once (that is what is expected after forgetting), but until
the scheduler empties the bin at night, one button is enough and they are back.

What is checked here:

* after forgetting the viewer is gone from the statistics, but in the bin,
* bringing them back returns all their rows - and cancels the ordered rewrite of the file,
  so the database is not locked because of a deletion that did not happen,
* emptying at night is final: the rows disappear from the bin too,
* then the list offers only a backup, and when that disappears too, the row leaves
  the list as well,
* the bin has the same columns as `playback` - otherwise part of the data would be
  lost on bringing back,
* `to_bin=False` (the way for whoever does not want to wait) deletes straight away.

Run:
    .venv/bin/python tests/test_bin_and_forgetting.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
DB = Path(_tmp) / "kos.db"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(DB)
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, purge, tasks, web  # noqa: E402

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
BACKUPS = Path(_tmp) / "backups"
db.set_setting("backup_path", str(BACKUPS))
db.forget_settings()


def fill(user_id: str, name: str, count: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)", (user_id, name, db.utcnow()))
        for n in range(count):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, item_name, started_at, last_seen_at,"
                " watched_seconds, is_active) VALUES (?,?,?,?,?,?,?,?,0)",
                (f"{user_id}-{n}", user_id, name, f"i-{n}",
                 f"Titul {n}", "2024-01-01 10:00:00",
                 "2024-01-01 10:20:00", 1200))
        conn.commit()


def in_history(user_id: str) -> int:
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE user_id = ?", (user_id,)) or 0)


def in_bin(user_id: str) -> int:
    return int(db.query_value(
        f"SELECT COUNT(*) FROM {db.PLAYBACK_BIN} WHERE user_id = ?", (user_id,)) or 0)


def forget(user_id: str) -> str:
    response = client.post("/settings/history/forget",
                           data={"user_id": user_id}, follow_redirects=True)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.text))


def where_it_returns(path: str, data: dict) -> str:
    return client.post(path, data=data, follow_redirects=False).headers.get(
        "location", "")


print("--- the bin has the shape of the history ---")
with db.connect() as conn:
    playback_columns = db.columns_of(conn, "playback")
    bin_columns = db.columns_of(conn, db.PLAYBACK_BIN)
check(not [s for s in playback_columns if s not in bin_columns],
      f"the bin has all the history's columns ({len(playback_columns)})")
check("event_id" in bin_columns, "and on top knows which forgetting it belongs to")

print()
print("--- forgetting: out of the statistics at once, but into the bin ---")
fill("u-mirek", "Mirek", 40)
text = forget("u-mirek")
check(in_history("u-mirek") == 0, "nothing of them is in the history")
check(in_bin("u-mirek") == 40, f"but all 40 are in the bin ({in_bin('u-mirek')})")
check("jde vrátit zpět" in text, "the message says it can be brought back")
check(purge.compaction_pending() != "", "and the rewrite of the file is ordered for the night")
listing = purge.forgettings()
check(len(listing) == 1 and listing[0]["in_bin"] and listing[0]["name"] == "Mirek",
      f"the list of the forgotten knows them ({listing})")

print()
print("--- the list is behind the Restore button, not scattered in the card ---")
page = client.get("/settings?section=tasks").text
check('data-modal="modal-restore"' in page, "the Restore button is seen")
check('<dialog id="modal-restore"' in page, "and opens a dialog with the list")
dialog_at = page.index('<dialog id="modal-restore"')
check(page.index("Mirek", dialog_at) > dialog_at, "the viewer's name is inside the dialog")
template = (PROJECT / "jellyscope" / "templates" / "settings"
            / "tasks.html").read_text(encoding="utf-8")
button_at = template.index('data-modal="modal-restore"')
check('type="button"' in template[button_at - 200:button_at],
      "the button sends nothing - it only opens the dialog")

print()
print("--- the page stays by the card, it does not jump up ---")
# After a click the page loads again; without an anchor it would start from the top
# and a person would look for where it was. The message is a floating box at the top,
# so they do not miss it that way.
# The position is held by `data-keep-scroll` (the script in static/js/app.js remembers it on
# the click and brings it back after loading). The anchor in the address is only a rescue in case the
# script did not finish - without it a person would end up at the top of the page.
# Measured in a browser through CDP: 1644 px before and after forgetting,
# 3355 px before and after bringing back.
card_template = (PROJECT / "jellyscope" / "templates" / "settings"
                 / "tasks.html").read_text(encoding="utf-8")
confirm_at = card_template.index('form="forget"',
                                 card_template.index("modal-forget"))
check("data-keep-scroll" in card_template[confirm_at:confirm_at + 200],
      "the confirmation of forgetting keeps the page's position")
restore_at = card_template.index('_("Bring back")')
check("data-keep-scroll" in card_template[restore_at - 200:restore_at],
      "and the Bring back button too")
check("[data-keep-scroll]" in (PROJECT / "jellyscope" / "static" / "js"
                               / "app.js").read_text(encoding="utf-8"),
      "the handler that brings the position back is on the page")
check(where_it_returns("/settings/history/forget", {"user_id": "u-nikdo"})
      .endswith("#purge"), "and without the script it at least jumps to the card")
check('id="purge"' in client.get("/settings?section=tasks").text,
      "the anchor is on the page")

print()
print("--- bringing back ---")
check(where_it_returns("/settings/history/restore", {"event": "0"})
      .endswith("#purge"), "bringing back too")
response = client.post("/settings/history/restore",
                       data={"event": str(listing[0]["id"])},
                       follow_redirects=True)
text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.text))
check(in_history("u-mirek") == 40, f"the history is back ({in_history('u-mirek')})")
check(in_bin("u-mirek") == 0, "and the bin is empty")
check("je zpátky" in text, "the message confirms it")
check(purge.compaction_pending() == "",
      "the ordered rewrite was cancelled - it has nothing to tidy")
check(purge.forgettings() == [], "and they disappeared from the list")
row = db.query_one("SELECT item_name, watched_seconds FROM playback"
                   " WHERE user_id = 'u-mirek' ORDER BY session_key LIMIT 1")
check(dict(row or {}).get("item_name") == "Titul 0"
      and dict(row or {}).get("watched_seconds") == 1200,
      f"with the content too, not just the count ({dict(row or {})})")

print()
print("--- emptying at night is final ---")
forget("u-mirek")
check(in_bin("u-mirek") == 40, "in the bin again")
old = datetime.now(timezone.utc) - timedelta(days=2)
db.set_setting(purge.DEFERRED_KEY, old.strftime(db.TIME_FORMAT))
db.forget_settings()
asyncio.run(tasks._finish_deferred_compaction())
check(in_bin("u-mirek") == 0, "the bin is empty after the night")
check(in_history("u-mirek") == 0, "and nothing in the history either")
listing = purge.forgettings()
check(len(listing) == 1 and not listing[0]["in_bin"] and listing[0]["has_backup"],
      f"the list offers a backup ({listing})")
check(listing[0]["backup_file"].startswith("jellyscope-"),
      f"and names it ({listing[0]['backup_file']})")
result = purge.restore_from_bin(listing[0]["id"])
check(result["returned"] == 0, "bringing back from an empty bin is not possible")

print()
print("--- when the backup disappears too, the row leaves the list ---")
for file in BACKUPS.glob("jellyscope-*"):
    file.unlink()
check(purge.forgettings() == [], "the list is empty")
check(db.query_value("SELECT COUNT(*) FROM forgettings") == 0,
      "and not even a row of it was left in the database")

print()
print("--- to_bin=False deletes straight away ---")
fill("u-jana", "Jana", 15)
result = purge.forget_user("u-jana")
check(result["deleted"] == 15 and in_history("u-jana") == 0, "deleted")
check(in_bin("u-jana") == 0, "and nothing in the bin - this way does not use the bin")
check(purge.forgettings() == [], "nor in the list")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
