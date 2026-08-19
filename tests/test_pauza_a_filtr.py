# -*- coding: utf-8 -*-
r"""Pauza není nové spuštění; a filtr v historii se dá skládat.

**Pauza.** Některé přehrávače při pauze z `/Sessions` zmizí. Sběrač
takový záznam uzavře - a když se film rozjede dál, založil nový. Jedno
sledování pak bylo v historii dvakrát a ve statistikách jako dvě
spuštění. Nově se na přerušené přehrávání naváže, pokud jde o tentýž
titul, uplynula chvíle a **nezačalo se od začátku**: kdo film pustí
znovu od nuly, ten se dívá podruhé.

**Filtr.** Uživatel, druh, období, způsob přehrání, přehrávač a jazyk se
musí dát kombinovat - a počet nahoře musí sedět s tím, co je vypsané,
jinak stránkování lže.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_pauza_a_filtr.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "pauza.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import collector, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

TIK = 10_000_000  # jedna vteřina v tikách Jellyfinu


class FalesnyKlient:
    """Vrací to, co bychom dostali z /Sessions."""

    def __init__(self) -> None:
        self.relace: list[dict] = []

    async def sessions(self) -> list[dict]:
        return self.relace


def relace(session_id: str, pozice_s: int, pauza: bool = False) -> dict:
    return {
        "Id": session_id,
        "UserId": "u1",
        "UserName": "Jana",
        "Client": "Jellyfin Web",
        "DeviceName": "Chrome",
        "PlayState": {"PositionTicks": pozice_s * TIK, "IsPaused": pauza,
                      "PlayMethod": "DirectPlay"},
        "NowPlayingItem": {"Id": "film-1", "Name": "Duna", "Type": "Movie",
                           "RunTimeTicks": 7200 * TIK},
    }


klient = FalesnyKlient()


def snimek() -> None:
    asyncio.run(collector.poll_once(klient, max_gap_seconds=30))


print("--- pauza a znovuspuštění je JEDNO přehrávání ---")
klient.relace = [relace("s1", 600)]
snimek()
check(db.query_value("SELECT COUNT(*) FROM playback") == 1, "začalo se dívat")

# Přehrávač zmizí z /Sessions - pauza, uspaná televize, zavřená záložka.
klient.relace = []
snimek()
check(db.query_value("SELECT COUNT(*) FROM playback WHERE is_active = 1") == 0,
      "záznam se uzavřel")

# A za chvíli je zpátky - jiné id relace, protože klient se připojil znovu.
klient.relace = [relace("s2", 1200)]
snimek()
pocet = db.query_value("SELECT COUNT(*) FROM playback")
check(pocet == 1, f"pořád jedno přehrávání, ne dvě ({pocet})")
radek = db.query_one("SELECT is_active, ended_at, session_key FROM playback")
check(radek["is_active"] == 1, "a je zase aktivní")
check(radek["ended_at"] is None, "konec se zrušil")
check(radek["session_key"].startswith("s2::"), "klíč se přepsal na novou relaci")


print()
print("--- ale spuštění od začátku je nové přehrávání ---")
klient.relace = []
snimek()
# Tentýž film, ale přehrávač je na nule - někdo si ho pustil znovu.
klient.relace = [relace("s3", 5)]
snimek()
pocet = db.query_value("SELECT COUNT(*) FROM playback")
check(pocet == 2, f"tohle je druhé sledování ({pocet})")


print()
print("--- a po půl hodině se nenavazuje ---")
with db.connect() as conn:
    conn.execute("UPDATE playback SET is_active = 0,"
                 " last_seen_at = datetime('now', '-2 hours'),"
                 " ended_at = datetime('now', '-2 hours')")
klient.relace = [relace("s4", 900)]
snimek()
check(db.query_value("SELECT COUNT(*) FROM playback") == 3,
      "po dvou hodinách je to nová podívaná")


print()
print("--- filtr v historii se dá skládat ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    vzorky = [
        ("a", "u1", "Jana", "Movie", "Transcode", "Jellyfin Web", "cs", 3600),
        ("b", "u1", "Jana", "Episode", "DirectPlay", "Infuse", "en", 1800),
        ("c", "u2", "Petr", "Movie", "DirectPlay", "Jellyfin Web", "cs", 2400),
        ("d", "u2", "Petr", "Movie", "Transcode", "Infuse", None, 1200),
    ]
    for klic, uzivatel, jmeno, typ, metoda, klient_, jazyk, sekund in vzorky:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, play_method, client, audio_language,"
            " started_at, last_seen_at, ended_at, watched_seconds)"
            " VALUES (?,?,?,?,?,?,?,?,?, datetime('now','-1 day'),"
            " datetime('now','-1 day'), datetime('now','-1 day'), ?)",
            (klic, uzivatel, jmeno, f"i-{klic}", f"Titul {klic}", typ,
             metoda, klient_, jazyk, sekund),
        )

check(stats.history_count() == 4, "bez filtru jsou tam všechny")
check(stats.history_count(user_id="u1") == 2, "podle uživatele")
check(stats.history_count(method="Transcode") == 2, "podle způsobu")
check(stats.history_count(client="Infuse") == 2, "podle přehrávače")
check(stats.history_count(language="cs") == 2, "podle jazyka")
check(stats.history_count(language="und") == 1, "„neuvedený“ chytí i prázdnou hodnotu")
check(stats.history_count(kind=stats.KIND_MOVIE) == 3, "podle druhu")

# Tohle je ta pointa: kombinace, ne jen jedna volba.
check(stats.history_count(user_id="u2", kind=stats.KIND_MOVIE,
                          method="Transcode") == 1,
      "uživatel + druh + způsob naráz")
check(stats.history_count(user_id="u1", method="DirectPlay",
                          client="Infuse") == 1, "a další kombinace")
check(stats.history_count(od="2000-01-01", do="2000-01-02") == 0,
      "období mimo rozsah nevrátí nic")

# Počet nad výpisem musí sedět s výpisem, jinak stránkování lže.
for filtr in ({}, {"user_id": "u1"}, {"method": "Transcode"},
              {"kind": stats.KIND_MOVIE, "client": "Jellyfin Web"}):
    check(len(stats.history(limit=500, **filtr)) == stats.history_count(**filtr),
          f"počet sedí s výpisem {filtr or '(bez filtru)'}")

nabidka = stats.hodnoty_filtru()
check(set(nabidka["zpusoby"]) == {"Transcode", "DirectPlay"},
      f"nabídka způsobů podle dat ({nabidka['zpusoby']})")
check("Infuse" in nabidka["klienti"], "a nabídka přehrávačů taky")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
