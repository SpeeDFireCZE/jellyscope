# -*- coding: utf-8 -*-
"""Import do prázdné knihovny - stav, ve kterém je člověk hned po instalaci.

Tenhle test vznikl z konkrétní chyby: po importu z Jellystatu do ještě
nepřipojeného Jellyfinu zůstal graf sledovanosti prázdný, u uživatelů byly
otazníky a knihovna byla prázdná — přestože souhrnná čísla seděla.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "empty.db")

from jellyscope import db, importers, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

# --- záloha Jellystatu tak, jak vypadá doopravdy --------------------------
# Tabulka jf_playback_activity nemá sloupec s typem položky; film od epizody
# se pozná podle vyplněného SeriesName / EpisodeId.
now = datetime.now(timezone.utc)


def zaznam(index: int, nazev: str, serial: str | None, uzivatel: str,
           user_id: str, hodin: float, pred_dny: int) -> dict:
    return {
        "Id": f"jst-{index}",
        "NowPlayingItemId": f"item-{index}",
        "NowPlayingItemName": nazev,
        "SeriesName": serial,
        "EpisodeId": f"ep-{index}" if serial else None,
        "UserId": user_id,
        "UserName": uzivatel,
        "Client": "Jellyfin Web",
        "DeviceName": "Chrome",
        "PlayMethod": "DirectPlay",
        "PlaybackDuration": int(hodin * 3600),
        "ActivityDateInserted": (now - timedelta(days=pred_dny)).strftime("%Y-%m-%d %H:%M:%S"),
    }


zaloha = {"jf_playback_activity": [
    zaznam(1, "Duna", None, "Tomáš", "u-1", 2.0, 1),
    zaznam(2, "Matrix", None, "Tomáš", "u-1", 2.5, 2),
    zaznam(3, "1. díl", "Kancelář", "Jana", "u-2", 0.5, 1),
    zaznam(4, "2. díl", "Kancelář", "Jana", "u-2", 0.5, 3),
]}

vysledek = asyncio.run(importers.import_jellystat_json(
    json.dumps(zaloha).encode("utf-8"), min_seconds=60
))
check(vysledek["status"] == "ok", f"import proběhl: {vysledek.get('message', '')}")
check(vysledek["imported"] == 4, f"naimportovaly se 4 záznamy (je {vysledek['imported']})")


print()
print("--- jméno uživatele ze zálohy, ne otazník ---")
jmena = {row["user_name"] for row in db.query_all("SELECT DISTINCT user_name FROM playback")}
check("?" not in jmena, f"v historii nejsou otazníky: {sorted(jmena)}")
check(jmena == {"Tomáš", "Jana"}, f"jména sedí: {sorted(jmena)}")


print()
print("--- typ položky: film vs epizoda ---")
typy = {row["item_name"]: row["item_type"]
        for row in db.query_all("SELECT item_name, item_type FROM playback")}
check(typy.get("Duna") == "Movie", f"Duna je film (je: {typy.get('Duna')!r})")
check(typy.get("Matrix") == "Movie", f"Matrix je film (je: {typy.get('Matrix')!r})")
check(typy.get("1. díl") == "Episode", f"epizoda je Episode (je: {typy.get('1. díl')!r})")
# Tohle byla ta chyba: do item_type se ukládal název titulu.
check(not any(t in ("Duna", "Matrix", "1. díl") for t in typy.values()),
      f"v item_type nejsou názvy titulů: {sorted(set(typy.values()))}")


print()
print("--- graf sledovanosti po dnech ---")
denni = stats.daily_activity_split(30)
celkem = sum(row["hours"] for row in denni)
check(abs(celkem - 5.5) < 0.05, f"souhrn hodin sedí: {celkem:.2f} (čekáno 5,5)")

filmy = sum(row["movie_hours"] for row in denni)
serialy = sum(row["series_hours"] for row in denni)
ostatni = sum(row["other_hours"] for row in denni)
check(abs(filmy - 4.5) < 0.05, f"filmy: {filmy:.2f} (čekáno 4,5)")
check(abs(serialy - 1.0) < 0.05, f"seriály: {serialy:.2f} (čekáno 1,0)")
check(ostatni == 0, f"nic nespadlo do 'ostatní': {ostatni:.2f}")
check(abs((filmy + serialy + ostatni) - celkem) < 0.05,
      "rozpad se sečte na celek")


print()
print("--- neznámý typ se neztratí ---")
# Kdyby přišel záznam, u kterého typ nepoznáme, nesmí z grafu zmizet.
with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, item_id, item_name, item_type,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('x-1', 'zahadka', 'Koncert', 'MusicVideo', ?, ?, 3600, 0)""",
        (db.utcnow(), db.utcnow()),
    )
denni = stats.daily_activity_split(30)
check(abs(sum(row["other_hours"] for row in denni) - 1.0) < 0.05,
      "neznámý typ spadl do 'ostatní'")
check(abs(sum(row["hours"] for row in denni) - 6.5) < 0.05,
      f"a započítal se do celku: {sum(row['hours'] for row in denni):.2f} (čekáno 6,5)")


print()
print("--- doplnění po připojení Jellyfinu ---")
# Simulujeme, že se knihovna nasynchronizovala až teď.
with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name) VALUES ('u-3', 'Petr')")
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, series_name, is_missing, synced_at)"
        " VALUES ('item-9', 'Pozdní film', 'Movie', 'lib-1', NULL, 0, ?)",
        (db.utcnow(),),
    )
    conn.execute("INSERT INTO libraries (id, name) VALUES ('lib-1', 'Filmy')")
    conn.execute(
        """INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,
                                 item_type, started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('pozdni', 'u-3', '?', 'item-9', 'Pozdní film', 'Pozdní film', ?, ?, 3600, 0)""",
        (db.utcnow(), db.utcnow()),
    )

zmeny = importers.refresh_playback_metadata()
check(zmeny["user_names"] == 1, f"doplnilo se jméno uživatele ({zmeny['user_names']})")
check(zmeny["item_types"] >= 1, f"opravil se typ položky ({zmeny['item_types']})")
check(zmeny["libraries"] == 1, f"doplnila se knihovna ({zmeny['libraries']})")

radek = db.query_one("SELECT * FROM playback WHERE session_key = 'pozdni'")
check(radek["user_name"] == "Petr", f"jméno je Petr (je: {radek['user_name']!r})")
check(radek["item_type"] == "Movie", f"typ je Movie (je: {radek['item_type']!r})")
check(radek["library_id"] == "lib-1", f"knihovna je lib-1 (je: {radek['library_id']!r})")

# Opakované spuštění už nemá co dělat - a nesmí přepsat jména, která sedí.
zmeny = importers.refresh_playback_metadata()
check(zmeny["user_names"] == 0, "druhý průchod už jména nepřepisuje")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
