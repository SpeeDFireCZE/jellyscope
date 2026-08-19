# -*- coding: utf-8 -*-
r"""Síťové statistiky: kolik toho teklo, kdy byla špička, odkud.

Počítá se z `playback.bitrate` - toku, který sběrač u každého přehrávání
ukládá už dávno. Žádný nový sběr to nepotřebuje, jen jinou otázku nad
daty, která už jsou.

Nejzajímavější kus je souběžný tok: nepočítá se vzorkováním, ale
procházením událostí (každé přehrávání svůj bitrate na začátku přidá
a na konci ubere). Test proto staví situaci, kde se dvě přehrávání
překrývají jen chvíli - a kontroluje, že špička odpovídá právě tomu
překryvu, ne součtu všeho.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_sit.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sit.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)


def prehrani(jmeno: str, od: str, do: str, mbit: float, sekund: int,
             adresa: str | None, metoda: str = "DirectPlay",
             klient: str = "Jellyfin Web") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, ended_at,"
            " watched_seconds, bitrate, remote_address, play_method, client,"
            " device_name) VALUES (?, 'u1', ?, 'i1', 'Film', 'Movie',"
            " datetime('now', ?), datetime('now', ?), datetime('now', ?),"
            " ?, ?, ?, ?, ?, 'Chrome')",
            (f"s-{jmeno}-{od}", jmeno, od, do, do, sekund,
             int(mbit * 1e6), adresa, metoda, klient),
        )


# Dvě přehrávání, každé 10 Mbit/s, překryv jen hodinu uprostřed.
prehrani("Jana", "-4 hours", "-2 hours", 10, 7200, "192.168.1.5")
prehrani("Petr", "-3 hours", "-1 hours", 10, 7200, "192.168.1.6")
# Třetí je z internetu a překódované - malý tok, ale jiná kategorie.
prehrani("Eva", "-9 hours", "-8 hours", 4, 3600, "89.24.10.7", "Transcode",
         "Jellyfin Android TV")
# Čtvrté nemá adresu (z importu) ani bitrate - do součtů se počítat nesmí.
prehrani("Tomas", "-20 hours", "-19 hours", 0, 3600, None)


print("--- přehled ---")
prehled = stats.bandwidth_prehled(2)
check(prehled["prehravani"] == 3,
      f"počítá jen přehrávání, u kterých bitrate známe ({prehled['prehravani']})")
# Objem: (10 + 10) Mbit/s * 7200 s + 4 * 3600, všechno / 8 na bajty.
ocekavany = (10e6 * 7200 + 10e6 * 7200 + 4e6 * 3600) / 8
check(abs(prehled["bajtu"] - ocekavany) < 1000,
      f"objem dat sedí ({prehled['bajtu']} vs {int(ocekavany)})")
check(abs(prehled["podil_transcode"] - 9.1) < 0.5,
      f"podíl překódovaných ({prehled['podil_transcode']} %)")


print()
print("--- souběžný tok ---")
prubeh = stats.bandwidth_prubeh(2, bodu=200)
check(bool(prubeh), "křivka není prázdná")
vrchol = max(b["mbit"] for b in prubeh)
# Tady je to jádro: v překryvu tečou obě přehrávání naráz, tedy 20 Mbit/s.
# Kdyby se bralo maximum z jednotlivých přehrávání, vyšlo by 10.
check(abs(vrchol - 20.0) < 0.5, f"špička je součet v překryvu ({vrchol} Mbit/s)")
check(abs(prehled["spicka_mbit"] - vrchol) < 0.5,
      "a přehled hlásí totéž číslo")
check(all("popisek" in b for b in prubeh), "každý bod má popisek pro osu")


print()
print("--- kdo a odkud ---")
podle = stats.bandwidth_podle(2, "user_name")
check([p["label"] for p in podle][:2] == ["Jana", "Petr"]
      or [p["label"] for p in podle][:2] == ["Petr", "Jana"],
      f"nejvíc stáhli ti dva ({[p['label'] for p in podle]})")
check(all("gb" in p for p in podle), "graf dostává gigabajty, ne holé bajty")

odkud = stats.odkud_se_divaji(2)
check(odkud["skupiny"]["doma"]["plays"] == 2, "dvě přehrávání z domácí sítě")
check(odkud["skupiny"]["internet"]["plays"] == 1, "jedno z internetu")
check(odkud["skupiny"]["neznamo"]["plays"] == 1, "jedno bez adresy (import)")
check(any(not a["domaci"] for a in odkud["adresy"]),
      "veřejná adresa je v seznamu označená jako internet")

check(stats.je_domaci("192.168.1.5") and stats.je_domaci("10.0.0.1")
      and stats.je_domaci("172.20.0.9"), "privátní rozsahy se poznají")
check(not stats.je_domaci("89.24.10.7") and not stats.je_domaci(""),
      "veřejná adresa ani prázdná hodnota domácí nejsou")


print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    r = client.get("/network")
    check(r.status_code == 200, f"/network se vykreslí ({r.status_code})")
    check("Mbit/s" in r.text, "a je na ní tok")
    check("89.24.10.7" in r.text, "i tabulka adres")
    # Číslo vypadá jako měření, ale měřením není - stránka to musí říct.
    check("ne měření drátu" in r.text or "not a measurement of the wire" in r.text,
          "stránka přiznává, že je to odhad")
    check(client.get("/network?days=7").status_code == 200, "a jde přepnout období")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
