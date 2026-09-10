# -*- coding: utf-8 -*-
r"""Kontrola aktualizací si všimne překladů, které přibyly na `main`.

Překlady z Weblate přicházejí slučováním, ne vydáním, takže kontrola
podle čísla verze je nikdy nezachytí. Aplikace se proto GitHubu ptá
zvlášť: co se od mého commitu na `main` změnilo **ve složce s překlady**?

Tři odpovědi a každá znamená něco jiného:

* jen soubory ve složce s překlady -> „nové překlady", tlačítko stáhnout,
* cokoliv jinde (kód, dokumentace) -> nic; `git pull` by to stáhl taky,
  a na to je vydání,
* GitHub commit nezná (404)     -> „nevím", zůstane, co bylo.

GitHub se podstrkuje přes `httpx.MockTransport`, takže test běží bez
sítě a odpověď se dá napsat přesně.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_preklady_na_main.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "preklady.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from jellyscope import db, updates  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def github(soubory: list[str] | None, stav: int = 200):
    """Podvržený GitHub: vrátí seznam změněných souborů, nebo 404."""
    def odpoved(zadost: httpx.Request) -> httpx.Response:
        if stav != 200:
            return httpx.Response(stav, json={"message": "Not Found"})
        return httpx.Response(200, json={
            "ahead_by": len(soubory or []),
            "files": [{"filename": s} for s in (soubory or [])],
        })

    def tovarna(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(odpoved),
                                 **kwargs)
    return tovarna


# Aplikace v testu nebezi z gitu jako produkce, tak se commit podstrci.
async def _falesny_commit():
    return "abc123"


updates._muj_commit = _falesny_commit
updates.duvod_bez_aktualizace = lambda: ""       # jako by slo aktualizovat


def spocitej(soubory, stav=200):
    return asyncio.run(updates._novych_prekladu(
        client_factory=github(soubory, stav)))


print("--- co se počítá ---")
check(spocitej(["jellyscope/translations/es.json",
                "jellyscope/translations/log/es.json"]) == 2,
      "dva překladové soubory a nic jiného = 2")
check(spocitej(["jellyscope/translations/es.json",
                "jellyscope/translations/log/es.json",
                "README.md"]) == 0,
      "překlady + cokoliv mimo složku = 0 (pull by stáhl i to ostatní)")
check(spocitej(["jellyscope/web.py", "CHANGELOG.md", "README.md"]) == 0,
      "kód a dokumentace = 0 (na to je vydání)")
# Tag pushnuty, CI spadlo, Release nevznikl: na main je kod i preklady.
# `git pull` by stahl obojí, tak se preklady nehlasi - prijdou s vydanim.
check(spocitej(["jellyscope/translations/cs.json",
                "jellyscope/translations/en.json",
                "jellyscope/web.py", "CHANGELOG.md"]) == 0,
      "překlady vedle kódu = 0 (rozdělané vydání, pull by stáhl i kód)")
check(spocitej([]) == 0, "nic změněného = 0")
check(spocitej(None, stav=404) is None,
      "neznámý commit = None (ne nula: „nevím“ není „nic“)")
check(spocitej(None, stav=500) is None, "chyba serveru = None")

print()
print("--- když aktualizovat nejde, neptá se ---")
updates.duvod_bez_aktualizace = lambda: "Tohle je ukázka"
check(spocitej(["jellyscope/translations/es.json"]) is None,
      "v ukázce nebo v obrazu se GitHub neobtěžuje")
updates.duvod_bez_aktualizace = lambda: ""

print()
print("--- stav pro stránky ---")
db.set_setting(updates.NOVE_PREKLADY, "3")
db.forget_settings()
stav = updates.stav()
check(stav["nove_preklady"] == 3, f"stav() nese počet ({stav['nove_preklady']})")
check(stav["je_novejsi"] is False, "a bez nového vydání je_novejsi=False")

db.set_setting(updates.NOVE_PREKLADY, "")
db.forget_settings()
check(updates.stav()["nove_preklady"] == 0, "prázdno = 0")

print()
print("--- aktualizace bez vydání stáhne jen překlady ---")
# git se podstrci: co by pull prinesl, rika podvrzeny `diff --name-only`.
VOLANI: list[tuple] = []


def podvrzeny_git(*argumenty):
    async def beh():
        VOLANI.append(argumenty)
        if argumenty[0] == "diff" and "--name-only" in argumenty:
            return {"kod": 0, "vystup": chr(10).join(NA_DALCE)}
        return {"kod": 0, "vystup": "Already up to date."}
    return beh()


updates._git = podvrzeny_git
updates._spust = lambda *a: podvrzeny_git("pip")
db.set_setting(updates.NALEZENA_VERZE, "")          # zadne vydane nove vydani
db.forget_settings()

NA_DALCE = ["jellyscope/translations/es.json", "jellyscope/translations/log/es.json"]
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "ok", f"jen překlady na dálce -> pull proběhl ({vysledek['status']})")
check(any(v[0] == "fetch" for v in VOLANI), "před tím se udělal fetch")
check(any(v[0] == "pull" for v in VOLANI), "a pak pull")

NA_DALCE = ["jellyscope/translations/es.json", "jellyscope/web.py"]
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "error", "překlady + kód bez vydání -> nestáhne se nic")
check("víc než překlady" in vysledek.get("message", ""), "a hláška říká proč")
check(not any(v[0] == "pull" for v in VOLANI), "pull se nevolal")

db.set_setting(updates.NALEZENA_VERZE, "99.0.0")     # vydani je venku
db.forget_settings()
VOLANI.clear()
vysledek = asyncio.run(updates.aktualizuj())
check(vysledek["status"] == "ok" and any(v[0] == "pull" for v in VOLANI),
      "s vydaným vydáním se stáhne všechno (to je vydání)")
db.set_setting(updates.NALEZENA_VERZE, "")
db.forget_settings()

print()
print("--- v šabloně má překlad přednost jen bez vydání ---")
sablona = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(
    encoding="utf-8")
i_verze = sablona.find("{% if verze.je_novejsi %}")
i_preklady = sablona.find("{% elif verze.nove_preklady %}")
check(0 < i_verze < i_preklady,
      "patička: nejdřív nové vydání, teprve pak překlady")
check("{% if verze.je_novejsi or verze.nove_preklady %}" in sablona,
      "okno se otevře i jen kvůli překladům")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
