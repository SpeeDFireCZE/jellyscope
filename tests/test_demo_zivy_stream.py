# -*- coding: utf-8 -*-
r"""V ukázce pořád něco běží: vymyšlený stream se drží naživu a hraje dál.

Karta „Právě se hraje" je první, co návštěvník ukázky uvidí. Seed do ní
dá jedno přehrávání, jenže to stálo na místě - pozice z okamžiku seedu,
„naposledy viděno" taky. Stačilo, aby ho cokoliv uzavřelo (restart se
sběračem nad touž databází), a karta hlásila, že se nikdo nedívá.

Teď ho úloha ukázky každých pár sekund oživí a posune pozici o čas, který
uplynul. Na konci filmu začne znovu od začátku.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_demo_zivy_stream.py
"""
from __future__ import annotations

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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "demo.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "1"

from jellyscope import collector, config, db, demodata, stats  # noqa: E402

config.load_config(reload=True)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def bezi() -> list[dict]:
    return [r for r in stats.active_sessions() if r["session_key"] == demodata.ZIVY_KLIC]


db.init_db()
demodata.pripravit(tichy=True)

print("--- po seedu stream běží ---")
check(len(bezi()) == 1, "v Právě se hraje je vymyšlený stream")

print()
print("--- uzavřený stream oživne ---")
# Tak to dělá sběrač při startu: co zůstalo viset, uzavře.
collector.close_orphans()
check(bezi() == [], "uzavřený z karty zmizí (výchozí stav chyby)")
ted = datetime(2026, 10, 17, 18, 0, 0, tzinfo=timezone.utc)
demodata.udrz_zive(ted)
radky = bezi()
check(len(radky) == 1, "udrz_zive ho vrátí do karty")
check(bool(radky) and radky[0]["last_seen_at"] == ted.strftime(db.TIME_FORMAT)
      and radky[0]["ended_at"] is None, "naposledy viděn teď, bez konce")

print()
print("--- hraje dál ---")
pred = radky[0] if radky else {}
demodata.udrz_zive(ted + timedelta(seconds=30))
po = (bezi() or [{}])[0]
posun = (po.get("position_seconds") or 0) - (pred.get("position_seconds") or 0)
check(29 <= posun <= 31, f"pozice se posune o uplynulý čas ({posun:.1f} s)")
check((po.get("watched_seconds") or 0) - (pred.get("watched_seconds") or 0) == 30,
      "odsledovaný čas taky")
check(po.get("progress") is not None and 0 < po["progress"] < 100,
      f"ukazatel postupu je vidět ({po.get('progress')} %)")

print()
print("--- na konci filmu začne znovu ---")
delka = po.get("runtime_seconds") or 0
check(delka > 0, f"film má délku ({delka:.0f} s)")
for minut in (90, 160, 400, 1000):
    demodata.udrz_zive(ted + timedelta(minutes=minut))
    radek = (bezi() or [{}])[0]
    pozice = radek.get("position_seconds") or 0
    check(0 < pozice < delka and radek.get("watched_seconds", 0) <= delka,
          f"po {minut} min je pozice uvnitř filmu ({pozice:.0f} s z {delka:.0f})")

print()
print("--- živý tok ho vidí ---")
check(stats.tok_ted()["streamu"] == 1, "Právě teče počítá jeden stream")

print()
print("--- na pozadí to hlídá úloha ukázky ---")
zdroj = (PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8")
check("demodata.udrzuj_zive()" in zdroj, "web.py ji v ukázkovém režimu spouští")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
