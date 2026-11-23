# -*- coding: utf-8 -*-
r"""An episode that exists in the library again must not stay hanging in the archive.

What happened: in Jellyfin the file of one episode was replaced (another quality,
a new rip). Jellyfin does not take that as a change - it creates **a new item with a new
ItemId** and the old one disappears. Jellyscope therefore marks it as "missing" during the
sync, and the series' detail shows the message "v archivu je navíc 3 dílů", although
those episodes are in the library as usual.

The merging during the sync did not catch it: it compares the stored `tmdb_id`,
and older records (from a history import or from before that column)
have none. `scanner.merge_archive_into_live()` therefore takes the identity
from elsewhere - the series plus the season and episode number.

Run:
    .venv/bin/python tests/test_series_archive.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "archiv.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, scanner, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)


def write(sql: str, params: tuple = ()) -> None:
    with db.connect() as conn:
        conn.execute(sql, params)


def item(item_id: str, season, episode, missing: int, name: str,
         tmdb: str | None = None, series: str | None = "serial-1",
         series_name: str = "Kancelar") -> None:
    write(
        """INSERT INTO items (id, name, type, series_id, series_name,
                              parent_index_number, index_number, library_id,
                              is_missing, tmdb_id)
           VALUES (?,?,'Episode',?,?,?,?,'lib-tv',?,?)""",
        (item_id, name, series, series_name, season, episode, missing, tmdb),
    )


def play(item_id: str, seconds: int, key: str) -> None:
    write(
        """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                 item_name, item_type, started_at, last_seen_at,
                                 watched_seconds, is_active)
           VALUES (?,'u1','Petr',?,'Karamboly','Episode',?,?,?,0)""",
        (key, item_id, db.utcnow(), db.utcnow(), seconds),
    )


write("INSERT INTO libraries (id, name, collection_type)"
      " VALUES ('lib-tv','Serialy','tvshows')")

# A live episode (the new file) and two old items of the same episode in the archive -
# exactly what was seen on the series' page: S04E04 twice.
item("novy-s4e4", 4, 4, 0, "Karamboly s karambity")
item("stary-s4e4-a", 4, 4, 1, "Karamboly s karambity")
item("stary-s4e4-b", 4, 4, 1, "Karamboly s karambity")
# An episode that really is no longer in Jellyfin - that one IS to stay in the archive.
item("stary-s4e9", 4, 9, 1, "Posledni dil")
# And an episode of another series with the same numbers. It must not get mixed in.
item("cizi-s4e4", 4, 4, 1, "Uplne jiny serial", series="serial-2",
     series_name="Uplne jiny serial")

play("stary-s4e4-a", 1200, "sess-a")
play("stary-s4e4-b", 300, "sess-b")

print("--- the starting state ---")
series = stats.series_detail("serial-1")
check(series["archived_count"] == 3,
      f"three episodes hang in the archive ({series['archived_count']})")

print()
print("--- the Tidy data card knows beforehand what awaits the archive ---")
# The card used to count only the history: for the archive it showed zeros, even though
# episodes waited in it - and it looked as if tidying did not deal with the archive.
overview = scanner.archive_overview()
check(overview["to_return"] == 2, f"two episodes go back to the live ones ({overview['to_return']})")
check(overview["left"] == 2, f"two items have no counterpart ({overview['left']})")
check(overview["libraries"] == [{"id": "lib-tv", "name": "Serialy", "count": 2}],
      f"and those are by library, so one can go and assign them ({overview['libraries']})")

from fastapi.testclient import TestClient  # noqa: E402
from jellyscope import accounts, web  # noqa: E402

accounts.create("admin", "longpassword", is_admin=True)
client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})


def tidy_card() -> str:
    page = client.get("/settings?section=tasks").text
    return page[page.index('id="cleanup"'):page.index('/settings/history/tidy')]


card = tidy_card()
check("Historie" in card and "Archiv knihovny" in card,
      "the card has the history and the library archive apart")
check(">2<" in card.split("Díly, které se vrátí k živým")[1].split("</tr>")[0],
      "for the archive it shows how many episodes go back (2)")
remaining_row = card.split("V archivu bez protějšku")[1].split("</tr>")[0]
check(">2<" in remaining_row and "/library/lib-tv?tab=media&archived=1" in remaining_row,
      "and how many stay without a counterpart, with a link to the library's archive")

print()
print("--- merging the archive with the live episodes ---")
merged = scanner.merge_archive_into_live()
check(merged == 2, f"two old items were merged ({merged})")
check(merged == overview["to_return"], "exactly as many as the card promised")
after = scanner.archive_overview()
check(after["to_return"] == 0 and after["left"] == 2,
      f"after tidying nothing to bring back, two stay without a counterpart ({after['to_return']}, {after['left']})")
check(">0<" in tidy_card().split("Díly, které se vrátí k živým")[1].split("</tr>")[0],
      "and the card shows it")

series = stats.series_detail("serial-1")
check(series["archived_count"] == 1,
      f"only the episode that really is not in Jellyfin is left in the archive "
      f"({[d['name'] for d in series['archived']]})")

check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'cizi-s4e4'") == 1,
      "the other series with the same numbers stayed untouched")

print()
print("--- the history moved to the live episode ---")
seconds = db.query_value(
    "SELECT SUM(watched_seconds) FROM playback WHERE item_id = 'novy-s4e4'")
check(seconds == 1500, f"both plays sit on the new episode ({seconds})")
check(db.query_value(
    "SELECT COUNT(*) FROM playback WHERE item_id IN ('stary-s4e4-a','stary-s4e4-b')") == 0,
    "and nothing hangs on the old items any more")

print()
print("--- a series that got a new id in Jellyfin ---")
# Somebody in Jellyfin deleted and added again the whole folder of the series. The episodes have
# new series_ids, so by them the old item is never found -
# so the pairing goes by the series' name too.
item("stary-jiny-id", 1, 1, 1, "Prvni dil", series="serial-9",
     series_name="Pratele")
item("novy-jiny-id", 1, 1, 0, "Prvni dil", series="serial-10",
     series_name="Pratele")
play("stary-jiny-id", 600, "sess-c")

check(scanner.merge_archive_into_live() == 1, "it merges across another series id too")
check(db.query_value(
    "SELECT SUM(watched_seconds) FROM playback WHERE item_id = 'novy-jiny-id'") == 600,
    "and the history goes with it")

print()
print("--- but not when that series still exists ---")
# A live series missing one episode. That episode really is missing - and a match by
# name would send its history to somebody else's series of the same name.
item("zivy-jinde", 2, 2, 0, "Dvojka", series="serial-11",
     series_name="Duna")
item("zivy-jinde-b", 2, 3, 0, "Trojka", series="serial-11",
     series_name="Duna")
item("chybi-dil", 2, 2, 1, "Dvojka", series="serial-11",
     series_name="Duna")
check(scanner.merge_archive_into_live() == 1,
      "an episode with a live twin under the same series is merged")

# Two series of the same name (The Office US vs. UK). The name and the numbers match,
# but the tmdb_id differs - that is the proof it is another show.
item("cizi-stejny-nazev", 5, 5, 1, "Pátý", tmdb="111", series="serial-12",
     series_name="Duna")
item("duna-5-5", 5, 5, 0, "Pátý", tmdb="222", series="serial-11",
     series_name="Duna")
check(scanner.merge_archive_into_live() == 0,
      "but a series with another tmdb_id is not merged, even when it has the same name")

print()
print("--- a second run has nothing to do ---")
check(scanner.merge_archive_into_live() == 0, "repeating changes nothing")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
