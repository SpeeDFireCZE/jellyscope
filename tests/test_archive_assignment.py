# -*- coding: utf-8 -*-
r"""Assigning an archived item to another title by hand.

Jellyfin identified a title wrongly (as "Behind the scenes", say), after the fix
it created it anew - and neither the name, the tmdb nor the id match. The automatics have nothing
to pair by, a person knows. The assignment:

* moves the history (from the bin of forgotten viewers too) to the chosen live title
  and rewrites its name, kind, series and library,
* deletes the archived item with its streams and versions,
* makes a backup of the database beforehand.

It works from the item's detail (one) and from the library's archive (in bulk - what has no
chosen target stays). Deleting from the archive is confirmed by a dialog of its own and is also
backed up before. From the library's archive it is possible to delete in bulk too - the ticked
items, never a title that is in the library, nor another library's archive.

Run:
    .venv/bin/python tests/test_archive_assignment.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "archiv.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword", is_admin=False)
when = (datetime.now(timezone.utc) - timedelta(days=3)).strftime(db.TIME_FORMAT)
# From the folder the application really uses - not from the test's variable.
BACKUPS = Path(os.environ["JELLYSCOPE_HOME"]) / "data" / "backups"


def add_item(item_id, name, kind="Movie", archive=0, series=None, season=None, episode=None,
             path=None, lib="filmy"):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, series_id, series_name,"
            " parent_index_number, index_number, production_year, path, is_missing)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2021, ?, ?)",
            (item_id, name, kind, lib, series and "s-" + series, series, season, episode,
             path or f"/media/{item_id}.mkv", archive))
        conn.commit()


def add_plays(item_id, name, count, kind="Movie", table="playback"):
    with db.connect() as conn:
        for i in range(count):
            conn.execute(
                f"INSERT INTO {table} (session_key, user_id, user_name, item_id, item_name,"
                " item_type, library_id, started_at, last_seen_at, watched_seconds, is_active)"
                " VALUES (?, 'u1', 'Jana', ?, ?, ?, 'filmy', ?, ?, 1800, 0)",
                (f"{item_id}-{table}-{i}", item_id, name, kind, when, when))
        conn.commit()


with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES"
                 " ('filmy', 'Filmy', 'movies'), ('serialy', 'Seriály', 'tvshows')")
    conn.commit()

add_item("duna", "Duna", path="/media/filmy/Duna (2021)/Duna.mkv")
add_item("bts", "Behind the scenes", kind="Video", archive=1,
         path="/media/filmy/Duna (2021)/Duna.mkv")
add_plays("bts", "Behind the scenes", 3, kind="Video")
with db.connect() as conn:
    conn.execute("INSERT INTO item_streams (item_id, stream_index, type)"
                 " VALUES ('bts', 0, 'Video')")
    conn.execute("INSERT INTO item_versions (item_id, source_id, position, name)"
                 " VALUES ('bts', 'zdroj', 0, '1080p')")
    db._ensure_playback_bin(conn)
    conn.commit()
add_plays("bts", "Behind the scenes", 1, kind="Video", table=db.PLAYBACK_BIN)

add_item("amelie", "Amélie")
add_item("stary-dil", "Pilot (2)", kind="Episode", archive=1, series="Mikeš", season=1, episode=1,
         lib="serialy")
add_plays("stary-dil", "Pilot (2)", 2, kind="Episode")
add_item("novy-dil", "Pilot", kind="Episode", series="Mikeš", season=1, episode=1, lib="serialy")
add_item("zbytek", "Upoutávka", kind="Video", archive=1)
add_plays("zbytek", "Upoutávka", 1, kind="Video")
add_item("jiny-archiv", "Ztracený film", archive=1)

admin = TestClient(web.app)
admin.post("/login", data={"username": "admin", "password": "longpassword",
                           "method": "local"})
reader = TestClient(web.app)
reader.post("/login", data={"username": "reader", "password": "longpassword",
                            "method": "local"})

print("--- the file name in the archive ---")
# Python computes it, not the template: a Windows path (\) gives only the file name too.
add_item("win-cesta", "Stary film", archive=1, path="D:\\media\\stare\\Stary.Film.2001.mkv")
archive = {r["id"]: r for r in stats.archived_items(None)}
check(archive["win-cesta"].get("file") == "Stary.Film.2001.mkv",
      f"an archived item has only the file name ({archive['win-cesta'].get('file')})")
template = (PROJECT / "jellyscope" / "templates" / "library_detail.html").read_text(encoding="utf-8")
check(".split('/')" not in template and "p.file" in template, "the template only writes the file name out")
with db.connect() as conn:
    conn.execute("DELETE FROM items WHERE id = 'win-cesta'")
# The helper item belongs to this section only - left behind, it would be one more archived title
# in the films' library for the sections below.
check(stats.item("win-cesta") is None, "the helper item with the Windows path is cleaned up")

print()
print("--- searching for the target ---")
found = admin.get("/archive/search?q=Du").json()["items"]
check([p["id"] for p in found] == ["duna"], f"only the live Duna, not the archive ({[p['id'] for p in found]})")
check(found[0]["file"] == "Duna.mkv" and "/media" not in str(found),
      "the result has the file name, not the whole path on the disk")
check(admin.get("/archive/search?q=Duna.mkv").json()["items"][0]["id"] == "duna",
      "it is found by the file name too")
check(admin.get("/archive/search?q=D").json()["items"] == [], "one letter searches nothing")
check(found[0]["thumbnail"].startswith("/image/duna?kind=Primary&w=80"),
      f"the result has the poster's address ({found[0]['thumbnail']})")
episode = admin.get("/archive/search?q=Mike").json()["items"][0]
check(episode["thumbnail"].startswith("/image/s-Mikeš") or episode["thumbnail"].startswith("/image/s-Mike"),
      f"an episode has the series' poster, not a still from the episode ({episode['thumbnail']})")
check(admin.get("/archive/search?q=Mike").json()["items"][0]["description"]
      == "Mikeš S01E01 – Pilot", "an episode is described by the series and the number")
check(reader.get("/archive/search?q=Du", follow_redirects=False).status_code == 403,
      "a reader may not search")

print()
print("--- searching by words (series + S06E07) ---")
# A Czech series name, an English file - as with the user: "Zelenáč" is
# The Rookie, the episode's number is in the metadata and in the file name.
for season in range(1, 7):
    for number in range(1, 23):
        title = "Rozdrcený" if (season, number) == (6, 7) else f"Díl {season}-{number}"
        add_item(f"zel-{season}-{number}", title, kind="Episode", series="Zelenáč", season=season, episode=number,
                 lib="serialy",
                 path=f"/media/serialy/The Rookie/Season {season:02d}/"
                      f"The Rookie - S{season:02d}E{number:02d} - Episode WEBDL-1080p.mkv")
add_item("car-1-1", "Začátek", kind="Episode", series="Čarodějky", season=1, episode=1, lib="serialy",
         path="/media/serialy/Charmed/Season 01/Charmed - S01E01.mkv")


def search(query):
    return admin.get("/archive/search", params={"q": query}).json()


def ids(query):
    return [p["id"] for p in search(query)["items"]]


for query in ("Zelenáč S06E07", "zelenáč s06e07", "S06E07 Zelenáč", "Zelenáč Rozdrcený",
              "Zelenáč S06E07 – Rozdrcený", "  Zelenáč   S06E07  "):
    check(ids(query) == ["zel-6-7"], f"“{query.strip()}” finds only S06E07 ({ids(query)[:3]})")
season6 = ids("Zelenáč S06")
check(season6 == [f"zel-6-{c}" for c in range(1, 23)],
      f"“Zelenáč S06” = the whole 6th season in order ({len(season6)} episodes)")
check(ids("čarodějky s01e01") == ["car-1-1"], "lower case with diacritics finds “Čarodějky”")
check(ids("Zelenáč S07E01") == [], "an episode that does not exist finds nothing")
check(ids("The Rookie S06E07") == ["zel-6-7"], "it works by the file name word by word too")
everything = search("Zelenáč")
check(len(everything["items"]) == 50 and everything.get("next") is True,
      f"more than 50 results: 50 show and it is known there are more ({len(everything['items'])}, {everything.get('next')})")
check(search("Zelenáč S06").get("next") is False, "when all fit, there are no more")
print()
print("--- searching: edges from the review ---")
add_item("zona-2012", "Zóna 2012", path="/media/filmy/Zona/Zona.mkv")
add_item("ctyri", "4x4", path="/media/filmy/4x4.mkv")
add_item("bez-cisel", "Pilot", kind="Episode", series="Show", lib="serialy",
         path="/media/serialy/Show/Show 6x07.mkv")
add_item("bez-dilu", "Neúplný", kind="Episode", series="Bez čísla", season=6, lib="serialy",
         path="/media/serialy/Bez cisla/neco.mkv")
check(ids("Zelenáč, S06E07.") == ["zel-6-7"], "a comma and a full stop by a word do not matter")
check(ids("Zelenáč S06E07") == ["zel-6-7"], "a query in decomposed form (NFD) finds the same")
check(set(ids("4x4")) == {"ctyri", "zel-4-4"},
      f"“4x4” finds the movie of that name and the episode S04E04 ({ids('4x4')})")
check(ids("Show 6x07") == ["bez-cisel"], f"an episode without numbers in the metadata by the file name 6x07 ({ids('Show 6x07')})")
check(set(ids("S06E07")) == {"zel-6-7", "bez-dilu"},
      f"the code alone finds an episode missing its episode number too ({ids('S06E07')})")
add_item("sherlock", "Шерлок", path="/media/filmy/Sherlock/Sherlock.mkv")
for number in (1, 3):
    add_item(f"garaz-{number}", f"Motor {number}", kind="Episode", series="4x4 Garage", season=1, episode=number,
             lib="serialy", path=f"/media/serialy/4x4 Garage/4x4 Garage - S01E{number:02d}.mkv")
add_item("bass", "Basový koncert", path="/media/filmy/Bass06/koncert.mkv")
check(ids("4x4 Garage") == ["garaz-1", "garaz-3"],
      f"“4x4” in a series' name is searched as text too ({ids('4x4 Garage')})")
check("bass" not in ids("S06"), f"the code S06 is not a piece of another word (Bass06) ({ids('S06')})")
check(ids("Show 6x07") == ["bez-cisel"], "6x07 as a standalone word in the file still matches")
# In the submodule where it lives - find_existing reads it there (tests/test_stats_package.py, check e).
limit = stats.archive.CANDIDATE_LIMIT
stats.archive.CANDIDATE_LIMIT = 10
try:
    check(ids("шерлок") == ["sherlock"], f"a word without ASCII letters (Cyrillic) is found beyond the limit too ({ids('шерлок')})")
    check(ids("2012") == ["zona-2012"], f"numbers in the name find the movie beyond the candidate limit too ({ids('2012')})")
    result = search("Zelenáč")
    check(result["next"] is True,
          f"when SQL hits the candidate limit, it is known there are more ({len(result['items'])}, {result['next']})")
    check(ids("Zelenáč Rozdrcený") == ["zel-6-7"], "all words are filtered already in SQL, the limit does not matter")
finally:
    stats.archive.CANDIDATE_LIMIT = limit

dialog = admin.get("/item/bts").text
check('data-next="' in dialog and "S06E07" in dialog.split('data-next="')[1].split('"')[0],
      "the target picker has advice how to narrow down when the results do not fit")
# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
script = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
          + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
check("data.next" in script and "box.dataset.next" in script, "the menu shows the advice when next comes")
check("if (!entries.length && !next)" in script,
      "“Nic nenalezeno” only when there are no further results either (otherwise the messages would contradict each other)")

print()
print("--- assignment from the detail ---")
detail = admin.get("/item/bts").text
check('id="modal-assign"' in detail and 'id="modal-delete-item"' in detail,
      "an archived item has the assignment dialog and the delete confirmation")
check("confirm(" not in detail, "the browser's confirm() is no longer there")
dialog = detail[detail.index('id="modal-assign"'):detail.index("</dialog>", detail.index('id="modal-assign"'))]
# An archived item's image is no longer in Jellyfin - an empty box would be left.
check("/image/bts" not in dialog and 'class="poster-small"><img' not in dialog,
      "an archived item has no poster in the dialog")
check("data-target-picker" in dialog, "only the chosen target has a poster (the picker is in the dialog)")
# Part 10d3 review: the field's name comes from the macro target_picker(...) - the guard of the templates
# cannot see it in the page, so the page itself is checked. The route reads `target`.
check(re.search(r'<input type="hidden" name="target" value="">', dialog) is not None,
      "the assignment dialog sends the target in the field target (the name the route reads)")
check('id="modal-assign"' not in reader.get("/item/bts").text, "a reader does not see them")
check('id="modal-assign"' not in admin.get("/item/duna").text,
      "a live item has no assignment dialog")

check(reader.post("/item/bts/assign", data={"target": "duna"},
                  follow_redirects=False).status_code == 403, "a reader may not assign")
for target, what in (("jiny-archiv", "into the archive"), ("bts", "onto itself"),
                     ("neexistuje", "that does not exist"), ("", "an empty target")):
    admin.post("/item/bts/assign", data={"target": target}, follow_redirects=False)
    check(stats.item("bts") is not None, f"the target {what} is refused and nothing changes")
admin.post("/item/duna/assign", data={"target": "amelie"}, follow_redirects=False)
check(stats.item("duna") is not None, "a live item cannot be assigned (it would be deleted from the library)")
check(not list(BACKUPS.glob("jellyscope-*")) if BACKUPS.exists() else True,
      "for the refused ones there was not even a backup")

response = admin.post("/item/bts/assign", data={"target": "duna"}, follow_redirects=False)
check(response.status_code == 303 and response.headers["location"] == "/item/duna",
      f"after the assignment it goes to the new title ({response.headers.get('location')})")
check(stats.item("bts") is None, "the archived item is deleted")
rows = db.query_all("SELECT item_name, item_type FROM playback WHERE item_id = 'duna'")
check(len(rows) == 3, f"all three plays moved to Duna ({len(rows)})")
check(all(r["item_name"] == "Duna" and r["item_type"] == "Movie" for r in rows),
      "and they carry the corrected name and kind")
check(db.query_value(f"SELECT COUNT(*) FROM {db.PLAYBACK_BIN} WHERE item_id = 'duna'") == 1,
      "the row in the bin of forgotten viewers too (restoring brings it back to the right title)")
check(db.query_value("SELECT COUNT(*) FROM item_streams WHERE item_id = 'bts'") == 0
      and db.query_value("SELECT COUNT(*) FROM item_versions WHERE item_id = 'bts'") == 0,
      "the streams and versions of the wrongly identified item are gone")
check(len(list(BACKUPS.glob("jellyscope-*"))) == 1, "and a backup was made beforehand")
check("Duna" in admin.get("/history").text
      and "Behind the scenes" not in admin.get("/history").text,
      "the history shows the corrected title")

print()
print("--- in bulk from the archive ---")
archive = admin.get("/library/serialy?tab=media&archived=1").text
check('id="modal-assignment"' in archive and "Mikeš S01E01 – Pilot (2)" in archive,
      "the library's archive has a dialog with the archived episodes one by one")
check('class="poster-small"><img' not in archive,
      "the archive's rows have no posters")
check('id="modal-assignment"' not in reader.get("/library/serialy?tab=media&archived=1").text,
      "a reader does not see it")
check('id="modal-assignment"' not in admin.get("/library/serialy?tab=media").text,
      "the live library has no dialog")
dialog = archive.split('id="modal-assignment"', 1)[1].split("</dialog>", 1)[0]
check('data-step="confirm" hidden' in dialog and "data-recap-list" in dialog,
      "the dialog has a step with the recap, hidden at the start")
form = dialog.split('class="modal-steps"', 1)[1]
check(re.search(r'name="target_[\w.-]+"', dialog) is not None and 'name="cil' not in dialog,
      "the archive's rows send the targets in fields target_<id> (the prefix the route reads)")
check(form.count('type="submit"') == 1
      and 'type="submit" name="confirmed" value="1" data-recap-confirm' in form,
      "only confirming the recap submits (the first button only switches the step)")

# Without confirming the recap nothing changes - not even a backup is made.
backups_before = len(list(BACKUPS.glob("jellyscope-*")))
response = admin.post("/library/filmy/archive/assign", data={"target_stary-dil": "novy-dil"},
                      follow_redirects=False)
check(response.status_code == 303 and stats.item("stary-dil") is not None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'stary-dil'") == 2,
      "without confirming the recap the archive stays as it was")
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before, "and no backup is made")
check(admin.post("/library/filmy/archive/assign",
                 data={"target_stary-dil": "novy-dil", "confirmed": "ano"},
                 follow_redirects=False).status_code == 303
      and stats.item("stary-dil") is not None, "confirmed must be exactly 1")

response = admin.post("/library/filmy/archive/assign", data={
    "confirmed": "1",
    "target_stary-dil": "novy-dil",
    "target_zbytek": "",                       # without a target - nothing happens
    "target_jiny-archiv": "neexistuje",        # a wrong target - must not stop the others
}, follow_redirects=False)
check(response.status_code == 303, f"saved ({response.status_code})")
check(stats.item("stary-dil") is None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'novy-dil'") == 2,
      "an episode with a target: the history moved, it is gone from the archive")
check(stats.item("zbytek") is not None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'zbytek'") == 1,
      "an item without a target stayed as it was")
check(stats.item("jiny-archiv") is not None, "an item with a wrong target too")
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before + 1, "one backup for the whole batch")
check(reader.post("/library/filmy/archive/assign",
                  data={"confirmed": "1", "target_zbytek": "amelie"},
                  follow_redirects=False).status_code == 403, "a reader may not do it in bulk")
backups_before = len(list(BACKUPS.glob("jellyscope-*")))
admin.post("/library/filmy/archive/assign", data={"confirmed": "1", "target_zbytek": ""})
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before, "without a single target there is not even a backup")

print()
print("--- deleting from the archive ---")
backups_before = len(list(BACKUPS.glob("jellyscope-*")))
check(reader.post("/item/zbytek/delete", follow_redirects=False).status_code == 403,
      "a reader may not delete")
admin.post("/item/zbytek/delete", follow_redirects=False)
check(stats.item("zbytek") is None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'zbytek'") == 0,
      "the title and the history are deleted")
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before + 1, "and a backup was made beforehand")

check(stats.item("amelie") is not None
      and admin.post("/item/amelie/delete", follow_redirects=False).status_code == 303
      and stats.item("amelie") is not None, "a title that is in the library cannot be deleted")
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before + 1, "and no backup is made because of it")

print()
print("--- deleting from the archive in bulk ---")
add_item("smaz-1", "Starý film", archive=1)
add_plays("smaz-1", "Starý film", 2)
add_plays("smaz-1", "Starý film", 1, table=db.PLAYBACK_BIN)
add_item("smaz-2", "Druhý starý", archive=1)
add_plays("smaz-2", "Druhý starý", 1)
add_item("cizi-archiv", "Díl odjinud", kind="Episode", archive=1, series="Jiný", season=1, episode=1,
         lib="serialy")
add_plays("cizi-archiv", "Díl odjinud", 1, kind="Episode")

archive = admin.get("/library/filmy?tab=media&archived=1").text
check('id="modal-delete"' in archive and "data-select-all" in archive,
      "the archive has a delete dialog with \"Vybrat vše\"")
dialog = archive.split('id="modal-delete"', 1)[1].split("</dialog>", 1)[0]
row = dialog.split('value="smaz-1"', 1)[1].split("</li>", 1)[0]
check("Starý film" in row and "2 přehrávání" in row,
      "a row has a tick box, the name and the number of plays")
check('value="amelie"' not in dialog and 'value="cizi-archiv"' not in dialog,
      "it offers only this library's archive")
check(dialog.split('class="modal-steps"', 1)[1].count('type="submit"') == 1
      and 'type="submit" name="confirmed" value="1"' in dialog,
      "only the confirmation button deletes")
check('id="modal-delete"' not in reader.get("/library/filmy?tab=media&archived=1").text,
      "a reader does not see the dialog")
check('id="modal-delete"' not in admin.get("/library/filmy?tab=media").text,
      "the live library has no dialog")

backups_before = len(list(BACKUPS.glob("jellyscope-*")))
admin.post("/library/filmy/archive/delete", data={"delete": ["smaz-1", "smaz-2"]})
check(stats.item("smaz-1") is not None and stats.item("smaz-2") is not None,
      "without confirming nothing is deleted")
admin.post("/library/filmy/archive/delete",
           data={"confirmed": "1", "delete": ["amelie", "cizi-archiv", "neexistuje"]})
check(stats.item("amelie") is not None and stats.item("cizi-archiv") is not None,
      "it deletes neither a live title nor another library's archive")
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before,
      "and when there is nothing to delete, no backup is made")
check(reader.post("/library/filmy/archive/delete",
                  data={"confirmed": "1", "delete": ["smaz-1"]},
                  follow_redirects=False).status_code == 403
      and stats.item("smaz-1") is not None, "a reader may not delete")
# "Vybrat vše" with a full dialog sends a thousand fields; Starlette lets through only 1000 of them
# without raising the limit and answers with an error 400.
response = admin.post("/library/filmy/archive/delete", follow_redirects=False, data={
    "confirmed": "1", "delete": [f"x{i}" for i in range(web.ARCHIVE_DELETE_LIMIT)]})
check(response.status_code == 303, f"a full list passes too ({response.status_code})")

response = admin.post("/library/filmy/archive/delete", follow_redirects=False, data={
    "confirmed": "1",
    "delete": ["smaz-1", "smaz-2", "smaz-1", "amelie", "cizi-archiv", "neexistuje"]})
check(response.status_code == 303, f"deleted ({response.status_code})")
check(stats.item("smaz-1") is None and stats.item("smaz-2") is None,
      "the ticked items are gone from the archive")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE item_id IN ('smaz-1', 'smaz-2')") == 0,
      "with the history too")
check(db.query_value(f"SELECT COUNT(*) FROM {db.PLAYBACK_BIN} WHERE item_id = 'smaz-1'") == 0,
      "in the bin of forgotten viewers too")
check(stats.item("amelie") is not None and stats.item("cizi-archiv") is not None
      and db.query_value("SELECT COUNT(*) FROM playback WHERE item_id = 'cizi-archiv'") == 1,
      "the live title and another library's archive stayed with their history")
check(stats.item("jiny-archiv") is not None, "an unticked item stayed")
check(len(list(BACKUPS.glob("jellyscope-*"))) == backups_before + 1, "one backup for the whole batch")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
