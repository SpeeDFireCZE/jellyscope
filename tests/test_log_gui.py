# -*- coding: utf-8 -*-
"""Prohlížeč logu v Nastavení.

Log je jediné místo v aplikaci, kde se můžou objevit tajemství - do hlášky
o chybě se dostane leccos, včetně API klíče nebo hesla k databázi. Zpřístupnit
ho v prohlížeči proto znamená hlídat tři věci:

  * **Vidí ho jen správce.** Čtenář se do sekce nesmí dostat ani přímou
    adresou.
  * **Tajemství se maskují.** Platí pořád, že se API klíč přes rozhraní
    nedá získat - viz stejná zásada u formuláře v sekci Jellyfin.
  * **Jméno souboru z adresy není cesta.** `?log_file=../../.env` je taky
    "jméno" a nesmí nic vydat.

Spusteni:
    .\\.venv\\Scripts\\python.exe tests\\test_log_gui.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "log.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import accounts, applog, db  # noqa: E402

failures = 0

KLIC = "SUPERTAJNYKLIC123456"
HESLO_DB = "MojeHesloKDatabazi42"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "tajneheslo1", is_admin=True)
accounts.create("ctenar", "ctenarheslo1", is_admin=False)
db.set_setting("jellyfin_url", "http://media.doma:8096")
db.set_setting("jellyfin_api_key", KLIC)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402


print("--- log se píše do souboru ---")
cesta = applog.setup()
check(cesta is not None and cesta.exists(), f"soubor vznikl ({cesta})")
check(cesta.parent.name == "logs" and cesta.parent.parent.name == "data",
      f"leží v data/logs ({cesta})")
# Domeček se ctí: jinak by test psal do ostrého logu.
check(str(cesta).startswith(_tmp), "a řídí se JELLYSCOPE_HOME")

zapisovac = logging.getLogger("jellyscope.test")
zapisovac.info("bezna hlaska o prubehu")
zapisovac.warning("neco se nepovedlo")
zapisovac.error("chyba pri synchronizaci")

vypis = applog.read_lines(limit=100)
texty = [r["text"] for r in vypis["lines"]]
check(any("bezna hlaska" in t for t in texty),
      "i hlášky na úrovni INFO se zapíšou")
check(any("chyba pri synchronizaci" in t for t in texty), "a chyby taky")
check(any(r["level"] == "ERROR" for r in vypis["lines"]),
      "u řádku se pozná úroveň (kvůli barvě a filtru)")

# Filtr na úroveň musí schovat všechno ostatní.
jen_chyby = applog.read_lines(limit=100, level="ERROR")
check(jen_chyby["lines"] and all(r["level"] == "ERROR" for r in jen_chyby["lines"]),
      f"filtr na ERROR pustí jen chyby ({len(jen_chyby['lines'])})")


print()
print("--- tajemství se ve výpisu maskují ---")
zapisovac.error("Jellyfin odmitl klic: api_key=%s na /Items", KLIC)
zapisovac.error("token: \"%s\"", KLIC)
zapisovac.error("psycopg: connection to postgres://jelly:%s@db:5432 failed", HESLO_DB)
zapisovac.error("holy klic v hlasce: %s", KLIC)

cely = "\n".join(r["text"] for r in applog.read_lines(limit=200)["lines"])
check(KLIC not in cely, "API klíč se ve výpisu neobjeví")
check(HESLO_DB not in cely, "ani heslo k databázi")
check("***" in cely, "místo nich jsou hvězdičky")
# Zbytek řádku musí zůstat čitelný - jinak by maskování zahodilo i to,
# kvůli čemu se člověk do logu dívá.
check("na /Items" in cely, "okolní text hlášky zůstane")


print()
print("--- v rozhraní ho vidí jen správce ---")
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "tajneheslo1"})
    odpoved = client.get("/settings?section=log")
    check(odpoved.status_code == 200, f"/settings?section=log -> {odpoved.status_code}")
    stranka = odpoved.text
    check("Log aplikace" in stranka, "sekce se vykreslí")
    check("chyba pri synchronizaci" in stranka, "a jsou v ní řádky logu")
    check("jellyscope.log" in stranka, "je vidět, který soubor se čte")

    # Tohle je ta samá zásada jako u formuláře v sekci Jellyfin: klíč se
    # do stránky nesmí dostat ani oklikou přes log.
    check(KLIC not in stranka, "API klíč se do stránky nedostane ani přes log")
    check(HESLO_DB not in stranka, "ani heslo k databázi")

    # Volby jsou v adrese, ať jde odkaz poslat a funguje zpětné tlačítko.
    odpoved = client.get("/settings?section=log&log_level=ERROR&log_lines=100")
    check(odpoved.status_code == 200, "filtr přes adresu funguje")
    check("bezna hlaska" not in odpoved.text,
          "a INFO řádky při filtru na ERROR zmizí")

    print()
    print("--- nesmysly z adresy nic nerozbijí ---")
    for adresa in (
        "/settings?section=log&log_lines=0",
        "/settings?section=log&log_lines=-5",
        "/settings?section=log&log_lines=999999",
        "/settings?section=log&log_level=VYMYSLENA",
        "/settings?section=log&log_file=neexistuje.log",
    ):
        odpoved = client.get(adresa)
        check(odpoved.status_code == 200, f"{adresa.split('&', 1)[1]} -> 200")

    print()
    print("--- jméno souboru z adresy není cesta ---")
    # Kdyby se jméno použilo jako cesta, tímhle by šel přečíst jakýkoliv
    # soubor na disku. Proto se bere jen holé jméno a ještě se ověří,
    # že takový soubor ve složce s logy opravdu je.
    tajny = Path(_tmp) / ".env"
    tajny.write_text("SECRET_KEY=nedostupne-tajemstvi\n", encoding="utf-8")
    for pokus in ("../../.env", "..\\..\\.env", "/etc/passwd", "../log.db"):
        vysledek = applog.read_lines(name=pokus, limit=50)
        check(not vysledek["lines"] and not vysledek["exists"],
              f"{pokus!r} nic nevydá")
        odpoved = client.get("/settings?section=log", params={"log_file": pokus})
        check(odpoved.status_code == 200 and "nedostupne-tajemstvi" not in odpoved.text,
              f"{pokus!r} ani přes rozhraní")

    print()
    print("--- čtenář se do sekce nedostane ---")
    client.post("/logout")
    client.post("/login", data={"username": "ctenar", "password": "ctenarheslo1"})
    odpoved = client.get("/settings?section=log")
    check(odpoved.status_code == 200, "stránka se čtenáři neuzavře celá")
    check("Log aplikace" not in odpoved.text,
          "ale sekci Log nevidí ani přímou adresou")
    check("chyba pri synchronizaci" not in odpoved.text, "a žádné řádky logu")
    check('section=log' not in odpoved.text, "v rozcestníku ji nemá")


print()
print("--- čte se konec souboru, ne celý ---")
# U velkého logu by načtení celého souboru do paměti bylo plýtvání.
velky = applog.log_dir() / "velky.log"
with velky.open("w", encoding="utf-8") as soubor:
    for cislo in range(50_000):
        soubor.write(f"2026-01-01 00:00:00,000 INFO    test: radek cislo {cislo}\n")

vypis = applog.read_lines(name="velky.log", limit=100)
check(len(vypis["lines"]) == 100, f"vrátí se přesně 100 řádků ({len(vypis['lines'])})")
check("radek cislo 49999" in vypis["lines"][-1]["text"],
      f"a je to konec souboru ({vypis['lines'][-1]['text'][-25:]})")
# Utržený první řádek se zahazuje - půlka hlášky mate víc než nic.
check(vypis["lines"][0]["text"].startswith("2026-01-01"),
      f"první řádek není utržený v půlce ({vypis['lines'][0]['text'][:30]})")

check(any(s["name"] == "velky.log" for s in applog.available_files()),
      "nový soubor se sám objeví v nabídce")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
