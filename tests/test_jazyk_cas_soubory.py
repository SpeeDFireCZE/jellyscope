# -*- coding: utf-8 -*-
"""Odklad u jazyka, místní čas v den-filtru a seznam souborů bez jazyka.

Tři nezávislé věci, každá s vlastní pastí:

  * **Odklad u jazyka.** Začátek filmu jsou loga a znělky a divák během
    nich stopu teprve hledá. Prvních pár minut se proto při rozhodování
    ignoruje — ale odsledovaný čas se k jazyku připočítá celý.

  * **Místní čas.** Den v grafu musí končit tam, kde ho končí výpis času.
    Když se „místní čas" počítá na dvou místech z různých zdrojů, proklik
    na den ukáže i záznamy, které podle zobrazeného času patří jinam.

  * **Soubory bez jazyka.** Seznam existuje kvůli jediné věci — cestě
    k souboru. Bez ní by řekl „něco je špatně" a hledání nechal na
    uživateli.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "jazyk.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import collector, db, langstats, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

STOPY = [
    {"Type": "Video", "Index": 0, "Codec": "h264", "Width": 1920, "Height": 1080},
    {"Type": "Audio", "Index": 1, "Codec": "aac", "Language": "eng", "IsDefault": True},
    {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "cze"},
]


def snimek(stopa: int) -> dict[str, Any]:
    return {
        "Id": "relace", "UserId": "u1", "UserName": "Karel",
        "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False,
                      "AudioStreamIndex": stopa},
        "NowPlayingItem": {"Id": "film", "Name": "Film", "Type": "Movie",
                           "RunTimeTicks": 72_000_000_000, "MediaStreams": STOPY},
    }


def zestarni(sekund: int) -> None:
    """Posune relaci do minulosti - test nemůže čekat pět minut."""
    with db.connect() as conn:
        conn.execute(
            """UPDATE playback
                  SET last_seen_at   = datetime(last_seen_at, ?),
                      language_since = datetime(language_since, ?)""",
            (f"-{sekund} seconds", f"-{sekund} seconds"),
        )


def relace() -> dict[str, Any]:
    return stats.active_sessions()[0]


print("--- první minuty se při rozhodování ignorují ---")
check(collector.LANGUAGE_GRACE_SECONDS == 240, "úvod je čtyři minuty")
check(collector.MIN_LANGUAGE_SECONDS == 60, "a pak minuta na ustálení")

# Loga hrají anglicky, ve 2:30 divák přepne na češtinu.
collector._store_sessions([snimek(1)], max_gap_seconds=600)
for krok in range(1, 40):
    zestarni(10)
    collector._store_sessions([snimek(1 if krok < 15 else 2)], max_gap_seconds=600)

    r = relace()
    odsledovano = int(r["watched_seconds"])
    if odsledovano < 300:
        if r["audio_language"] is not None:
            check(False, f"jazyk se zapsal moc brzy (v {odsledovano} s)")
            break
    else:
        break

r = relace()
check(r["audio_language"] == "cs",
      f"po pěti minutách je zapsaná čeština ({r['audio_language']})")
check(int(r["watched_seconds"]) >= 300,
      f"a ne dřív než v páté minutě ({r['watched_seconds']} s)")
check(r["current_audio_language"] == "cs", "karta ukazuje totéž")

# Angličtina z úvodu se do statistik nedostala vůbec - o to celé jde.
check(r["audio_language"] != "en", "angličtina z log se nezapsala")

# Odsledovaný čas se ale nekrátí: k jazyku patří celé přehrávání
# včetně těch prvních minut.
check(int(r["watched_seconds"]) >= collector.LANGUAGE_GRACE_SECONDS,
      f"odsledovaný čas obsahuje i úvod ({r['watched_seconds']} s)")


print()
print("--- hranice pro statistiky na to navazuje ---")
# Kdyby zůstala na dvou minutách, tříminutové přehrávání by do statistik
# vstoupilo bez jazyka - a přibylo by "Neuvedeno" tam, kde jsme se odpověď
# jen ještě nestihli dozvědět.
check(langstats.MIN_PLAY_SECONDS >= collector.LANGUAGE_GRACE_SECONDS
      + collector.MIN_LANGUAGE_SECONDS,
      f"do statistik jde jen přehrávání, u kterého se jazyk stihl určit "
      f"({langstats.MIN_PLAY_SECONDS} s)")


print()
print("--- filtr na den počítá stejný místní čas jako výpis ---")
# Uložený čas je vždycky UTC. Den se musí počítat v místním čase - a to
# na obou stranách stejně, jinak proklik na den ukáže cizí záznamy.
zdroj = (PROJECT / "jellyscope" / "stats.py").read_text(encoding="utf-8")
check(zdroj.count("date(p.started_at, 'localtime') = ?") == 1,
      "historie filtruje den v místním čase")
check("date(started_at, 'localtime')" in zdroj,
      "graf po dnech seskupuje taky v místním čase")

# U PostgreSQL se "místní" řídí zónou spojení - a tu musíme nastavit sami,
# jinak by SQL počítalo podle serveru s databází a Python podle serveru
# s aplikací.
zdroj_db = (PROJECT / "jellyscope" / "db.py").read_text(encoding="utf-8")
check("SET TIME ZONE" in zdroj_db,
      "spojení do PostgreSQL dostane časovou zónu aplikace")
check('os.environ.get("TZ"' in zdroj_db, "a bere ji z TZ, kterou nastavuje služba")

# Do SQL se název zóny vkládá do řetězce, takže musí projít kontrolou.
check("re.fullmatch" in zdroj_db, "název zóny se před vložením ověřuje")

with db.connect() as conn:
    conn.execute(
        """INSERT INTO playback (session_key, user_id, item_id, item_name,
                                 started_at, last_seen_at, watched_seconds, is_active)
           VALUES ('den-test', 'u1', 'film', 'Film',
                   '2026-08-11 22:30:00', '2026-08-11 23:00:00', 1800, 0)""")

# Ať je zóna serveru jakákoliv, obě strany se musí shodnout: den, do kterého
# záznam spadne podle filtru, musí odpovídat dni v jeho vypsaném čase.
den = db.query_one(
    "SELECT date(started_at, 'localtime') AS den FROM playback"
    " WHERE session_key = 'den-test'")["den"]
from jellyscope.formatting import datetime_human  # noqa: E402
vypsany = datetime_human("2026-08-11 22:30:00")
check(vypsany.startswith(f"{den[8:10]}.{den[5:7]}."),
      f"den z filtru ({den}) sedí s vypsaným časem ({vypsany})")


print()
print("--- seznam souborů bez jazyka ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('l','Filmy',?)",
                 (db.utcnow(),))
    for ident, jazyky, cesta in (
        ("ok", "cs,en", "/media/Matrix.mkv"),
        ("prazdne", None, "/media/Duna.mkv"),
        ("und", "und", "/media/Neznamy.mp4"),
        ("bez-textu", "", "/media/serial/S01E01.mkv"),
    ):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, audio_languages,
                                  path, size_bytes, is_missing, synced_at)
               VALUES (?,?,'Movie','l',?,?,3000000000,0,?)""",
            (ident, ident, jazyky, cesta, db.utcnow()),
        )

check(langstats.undefined_language_count() == 3,
      f"tři soubory bez jazyka ze čtyř ({langstats.undefined_language_count()})")
soubory = langstats.undefined_language_files(10, 0)
check({r["id"] for r in soubory} == {"prazdne", "und", "bez-textu"},
      "prázdné, 'und' i prázdný řetězec se počítají")
check(all(r["path"] for r in soubory), "u každého je cesta k souboru")
check(langstats.undefined_language_count(search="serial") == 1,
      "hledat jde i podle cesty")
check(langstats.undefined_language_count(search="Neznamy") == 1, "i podle názvu")

# Archivované soubory sem nepatří - ty už na disku nejsou, není co opravovat.
with db.connect() as conn:
    conn.execute("UPDATE items SET is_missing = 1 WHERE id = 'und'")
check(langstats.undefined_language_count() == 2,
      f"archivované se nenabízejí k opravě ({langstats.undefined_language_count()})")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
