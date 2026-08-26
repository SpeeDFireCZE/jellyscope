# -*- coding: utf-8 -*-
r"""Pozastavené přehrávání netahá data - a graf sítě to musí vědět.

Pauza žádná data nepřenáší: server nic neposílá a přehrávač nic nežádá.
Graf souběžného toku to ale nebral v potaz - bral bitrate a rozprostřel
ho přes celý čas od začátku do konce relace. Film pozastavený přes noc
tak držel plný tok do rána a přehrávání pozastavené **právě teď** taky,
protože `last_seen_at` běží dál i během pauzy.

Bylo to vidět i na tom, že dvě čísla na jedné stránce si odporovala:
plocha pod křivkou vycházela mnohonásobně větší než objem dat vedle ní,
který se odjakživa počítá z `watched_seconds`.

Kdy přesně se pauzovalo, databáze neví - drží jen součty. Tok proto trvá
tak dlouho, jak dlouho se doopravdy sledovalo; u pauzy uprostřed je
posunutý dopředu, ale výška i množství sedí.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_sit_pauza.py
"""
from __future__ import annotations

import asyncio
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sit.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import collector, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
TED = datetime.now(timezone.utc).replace(tzinfo=None)


def prehravani(klic: str, pred_minutami: int, odsledovano_s: int,
               pauza_s: int = 0, mbit: int = 20, aktivni: int = 1) -> None:
    zacatek = TED - timedelta(minutes=pred_minutami)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, started_at, last_seen_at, watched_seconds, paused_seconds,"
            " bitrate, is_paused, is_active, play_method, remote_address, client,"
            " device_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (klic, "u1", "Pepa", "film", "Duna",
             zacatek.strftime(db.TIME_FORMAT), TED.strftime(db.TIME_FORMAT),
             odsledovano_s, pauza_s, mbit * 1_000_000,
             1 if pauza_s else 0, aktivni, "DirectPlay", "10.0.0.5", "Web", "Chrome"))


def minut_v_grafu(bodu: int = 600) -> float:
    krivka = stats.bandwidth_prubeh(7, bodu=bodu)
    if not krivka:
        return 0.0
    return (krivka[-1]["cas"] - krivka[0]["cas"]) / 60


print("--- film pozastavený skoro dvě hodiny ---")
# Sledovalo se deset minut, zbytek stojí pauza. A stojí PRÁVĚ TEĎ.
prehravani("s1", pred_minutami=120, odsledovano_s=600, pauza_s=6600)
minut = minut_v_grafu()
check(9 <= minut <= 11, f"graf pokrývá zhruba deset minut, ne dvě hodiny ({minut:.1f})")

prehled = stats.bandwidth_prehled(7)
check(prehled["spicka_mbit"] == 20.0, f"špička zůstává 20 Mbit/s ({prehled['spicka_mbit']})")
check(abs(prehled["bajtu"] - 20e6 * 600 / 8) < 1e6,
      f"objem odpovídá deseti minutám ({prehled['bajtu'] / 1e9:.2f} GB)")
# Tohle je ta věta, kvůli které to celé vzniklo: plocha pod křivkou
# a objem dat vedle ní musí říkat totéž.
plocha_gb = prehled["spicka_mbit"] * 1e6 * (minut * 60) / 8 / 1e9
check(abs(plocha_gb - prehled["bajtu"] / 1e9) < 0.2,
      f"plocha pod křivkou sedí s objemem ({plocha_gb:.2f} GB vs "
      f"{prehled['bajtu'] / 1e9:.2f} GB)")

print()
print("--- kdo se dívá bez pauzy, tomu se nic neubírá ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
prehravani("s2", pred_minutami=60, odsledovano_s=3600)
minut = minut_v_grafu()
check(59 <= minut <= 61, f"graf pokrývá celou hodinu ({minut:.1f})")

print()
print("--- souběh se pořád sčítá ---")
# Dva lidé zároveň = součet toků. Kdyby oprava rozbila procházení
# událostí, spadlo by právě tohle.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
prehravani("s3", pred_minutami=30, odsledovano_s=1800, mbit=20)
prehravani("s4", pred_minutami=30, odsledovano_s=1800, mbit=12)
check(stats.bandwidth_prehled(7)["spicka_mbit"] == 32.0,
      f"špička je součet obou ({stats.bandwidth_prehled(7)['spicka_mbit']})")

print()
print("--- a co statistika jazyků: pauza se do sledovaného času nepočítá ---")
# Odpověď na obavu, jestli pauza nezkresluje i jazyky a titulky. Nezkresluje:
# sběrač přičítá k `watched_seconds` jen tehdy, když se doopravdy hraje.
with db.connect() as conn:
    conn.execute("DELETE FROM playback")

TIK = 10_000_000


class FalesnyKlient:
    def __init__(self) -> None:
        self.relace: list[dict] = []

    async def sessions(self) -> list[dict]:
        return self.relace


def relace(pozice_s: int, pauza: bool) -> dict:
    return {
        "Id": "s5", "UserId": "u1", "UserName": "Pepa",
        "Client": "Jellyfin Web", "DeviceName": "Chrome",
        "PlayState": {"PositionTicks": pozice_s * TIK, "IsPaused": pauza,
                      "PlayMethod": "DirectPlay"},
        "NowPlayingItem": {"Id": "film-1", "Name": "Duna", "Type": "Movie",
                           "RunTimeTicks": 7200 * TIK},
    }


klient = FalesnyKlient()


def snimek() -> None:
    asyncio.run(collector.poll_once(klient, max_gap_seconds=30))


def posun_hodiny(o_sekund: int) -> None:
    """Předstírá, že mezi snímky uběhl čas.

    Sběrač počítá přírůstek z rozdílu proti `last_seen_at`. Dva snímky
    hned po sobě mají rozdíl nula, takže by test neměřil nic.
    """
    with db.connect() as conn:
        conn.execute("UPDATE playback SET last_seen_at = ?",
                     ((datetime.now(timezone.utc).replace(tzinfo=None)
                       - timedelta(seconds=o_sekund)).strftime(db.TIME_FORMAT),))


def stav() -> tuple[int, int]:
    r = db.query_one("SELECT watched_seconds, paused_seconds FROM playback")
    return int(r["watched_seconds"] or 0), int(r["paused_seconds"] or 0)


klient.relace = [relace(60, pauza=False)]
snimek()

posun_hodiny(20)
klient.relace = [relace(70, pauza=True)]
snimek()
odsledovano, pauza = stav()
check(pauza >= 20, f"pauza se počítá zvlášť ({pauza} s)")
check(odsledovano == 0, f"a do sledovaného času se nepřičte ({odsledovano} s)")

posun_hodiny(20)
snimek()
odsledovano2, pauza2 = stav()
check(odsledovano2 == odsledovano, f"ani při druhém snímku ({odsledovano2} s)")
check(pauza2 > pauza, f"pauza mezitím roste ({pauza2} s)")

# A protože stojící číslo může znamenat i rozbité počítání, ještě jednou
# se rozjedeme: sledovaný čas se musí pohnout.
posun_hodiny(20)
klient.relace = [relace(90, pauza=False)]
snimek()
odsledovano3, _ = stav()
check(odsledovano3 >= 20, f"po rozjetí zase přibývá ({odsledovano3} s)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
