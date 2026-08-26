# -*- coding: utf-8 -*-
r"""Popisky pod grafem sítě ukazují místní čas, ne UTC.

V databázi jsou časy v UTC a bez zóny. `datetime.timestamp()` ale takový
čas považuje za **místní**, takže výsledek byl posunutý o celý offset -
a graf souběžného toku popisoval osu v UTC, zatímco zbytek aplikace psal
místní čas. V létě to znamenalo, že večerní špička podle grafu nastávala
o hodinu dřív, než doopravdy byla.

Test běží v pevné zóně (Europe/Prague), aby výsledek nezávisel na tom,
kde se pouští.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_cas_v_grafu.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cas.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
# Pevna zona, at test rika totez na kazdem stroji. `tzset` je jen na
# unixu; na Windows si zonu bere Python z prostredi az pri startu, takze
# se tam spolehneme na to, ze offset spocitame ze systemu (nize).
os.environ["TZ"] = "Europe/Prague"
if hasattr(time, "tzset"):
    time.tzset()

from jellyscope import db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()

# Prehravani, ktere v UTC zacalo v 18:00 vcera.
zacatek = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
    hour=18, minute=0, second=0, microsecond=0)
konec = zacatek + timedelta(hours=1)

with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
        " started_at, last_seen_at, ended_at, watched_seconds, paused_seconds,"
        " bitrate, is_paused, is_active, play_method, remote_address, client, device_name)"
        " VALUES ('s1','u1','Pepa','film','Duna',?,?,?,3600,0,20000000,0,0,"
        "'DirectPlay','10.0.0.5','Web','Chrome')",
        (zacatek.strftime(db.TIME_FORMAT), konec.strftime(db.TIME_FORMAT),
         konec.strftime(db.TIME_FORMAT)))

mistni = zacatek.astimezone()

print("--- osa grafu je v místním čase ---")
print(f"       v databázi (UTC): {zacatek.strftime('%d.%m. %H:%M')}")
print(f"       místní čas:       {mistni.strftime('%d.%m. %H:%M')}")

krivka = stats.bandwidth_prubeh(7, bodu=20)
check(bool(krivka), "graf má body")
if krivka:
    popisek = krivka[0]["popisek"]
    print(f"       popisek v grafu:  {popisek}")
    check(popisek.startswith(mistni.strftime("%d.%m. %H:")),
          f"první bod sedí s místním časem ({popisek})")
    # A hlavne: NESMI to byt UTC. Kdyz je zona posunuta, musi se to poznat.
    if mistni.utcoffset() != timedelta(0):
        check(not popisek.startswith(zacatek.strftime("%d.%m. %H:")),
              "a není to UTC")
    else:
        print("       (zóna bez posunu - rozdíl by nebyl vidět)")

print()
print("--- totéž platí pro čas špičky nad grafem ---")
prehled = stats.bandwidth_prehled(7)
check(prehled["spicka_kdy"].startswith(mistni.strftime("%d.%m. %H:")),
      f"špička v místním čase ({prehled['spicka_kdy']})")

print()
print("--- převod sám o sobě ---")
# Bez zony by se cas povazoval za mistni a timestamp by byl posunuty.
sekundy = stats._sekundy("2026-08-25 18:00:00")
ocekavane = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc).timestamp()
check(sekundy == ocekavane, f"čas z databáze se čte jako UTC ({sekundy} vs {ocekavane})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
