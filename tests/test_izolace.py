# -*- coding: utf-8 -*-
"""Testy nesmí sahat do ostré databáze.

Past, na kterou se přišlo až po několika dnech psaní testů: uložený výběr
databáze (`data/database.json`, píše ho formulář v Nastavení) **přebíjí**
proměnnou `DATABASE_PATH`. Test, který si nastavil vlastní databázi, tak
ve skutečnosti pracoval s tou ostrou — a protože v ní našel hotové schéma
i data, tvářil se, že prošel.

Zvlášť zákeřné je, že se to projeví jen tehdy, když `database.json`
existuje. Dokud ho nikdo nevytvořil, byla izolace v pořádku a nic
nenasvědčovalo tomu, že stojí na vodě.

Řeší to `JELLYSCOPE_HOME`: přepne celý „domeček" (tedy `.env`, složku
`data/` i ten uložený výběr) do dočasné složky. Tenhle test hlídá, že to
opravdu funguje a že na to žádný test nezapomněl.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


print("--- JELLYSCOPE_HOME přepíná i uložený výběr databáze ---")
domecek = Path(tempfile.mkdtemp())
(domecek / "data").mkdir()
# Do domečku dáme uložený výběr - kdyby se nectil, poznáme to.
(domecek / "data" / "database.json").write_text(
    '{"kind": "sqlite", "path": "' + str(domecek / "vybrana.db").replace("\\", "\\\\")
    + '"}', encoding="utf-8")

kod = (
    "import sys, json;"
    "from jellyscope import config, db;"
    "print(json.dumps({"
    "'base': str(config.BASE_DIR),"
    "'db': db.database_config().path}))"
)
prostredi = dict(os.environ,
                 JELLYSCOPE_HOME=str(domecek),
                 DATABASE_PATH=str(domecek / "z-promenne.db"),
                 SECRET_KEY="x", PYTHONIOENCODING="utf-8")
vysledek = subprocess.run([sys.executable, "-c", kod], capture_output=True,
                          text=True, cwd=str(PROJECT), env=prostredi)
try:
    import json
    data = json.loads(vysledek.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    data = {}
    print("       výstup:", vysledek.stdout[-300:], vysledek.stderr[-300:])

check(data.get("base") == str(domecek), f"BASE_DIR jde za proměnnou: {data.get('base')}")
check(data.get("db", "").endswith("vybrana.db"),
      f"a čte se výběr z domečku, ne z projektu: {data.get('db')}")
check(str(PROJECT) not in data.get("db", "x"),
      "cesta k databázi nevede do projektu")


print()
print("--- bez domečku by test skončil v ostré databázi ---")
# Kontrola sama sebe: kdyby to bylo jedno, neměl by tenhle test smysl.
prostredi_bez = dict(os.environ, DATABASE_PATH=str(domecek / "z-promenne.db"),
                     SECRET_KEY="x", PYTHONIOENCODING="utf-8")
prostredi_bez.pop("JELLYSCOPE_HOME", None)
vysledek = subprocess.run([sys.executable, "-c", kod], capture_output=True,
                          text=True, cwd=str(PROJECT), env=prostredi_bez)
try:
    bez = json.loads(vysledek.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    bez = {}
check(bez.get("base") == str(PROJECT), f"BASE_DIR je pak projekt: {bez.get('base')}")
if (PROJECT / "data" / "database.json").exists():
    check(str(PROJECT) in bez.get("db", ""),
          "a databáze se vezme z projektu - přesně ta past, kvůli které to je")
else:
    print("PRESKOCENO  projekt zrovna nemá data/database.json")


print()
print("--- na domeček nezapomněl žádný test ---")
chybejici = []
for soubor in sorted((PROJECT / "tests").glob("test_*.py")):
    text = soubor.read_text(encoding="utf-8")
    if 'os.environ["DATABASE_PATH"]' not in text:
        continue          # test databázi nepotřebuje
    if "JELLYSCOPE_HOME" not in text:
        chybejici.append(soubor.name)
    else:
        # Musí být nastavený DŘÍV než DATABASE_PATH i než import aplikace -
        # konfigurace se načte při importu a pak už se nemění.
        home = text.index("JELLYSCOPE_HOME")
        importy = re.search(r"^from jellyscope", text, re.M)
        if importy and home > importy.start():
            chybejici.append(f"{soubor.name} (až po importu)")

check(not chybejici, f"všechny testy mají vlastní domeček; chybí: {chybejici}")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
