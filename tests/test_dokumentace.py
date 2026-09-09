# -*- coding: utf-8 -*-
r"""Dokumentace proti skutečnosti.

Dokumentace stárne tiše. Nic nespadne, testy jsou zelené a README pořád
tvrdí, že úlohy jsou čtyři a ukázka běží na 8097 — jen to už rok neplatí.
Tenhle test hlídá právě ty věty, které se dají ověřit strojově:

* **počet a názvy naplánovaných úloh** proti `tasks.TASKS`,
* **port ukázky** proti tomu, co nastavuje `demo.py`,
* **sekce Nastavení**, na které se dokumentace odkazuje, proti tomu, jak
  se doopravdy jmenují,
* **soubory v „Project structure"** proti tomu, co v balíčku leží,
* **odkazy na soubory v repozitáři** (`deploy/...`, `docs/...`) proti
  tomu, co existuje.

Co ověřit nejde: jestli je text pravdivý. To za nikoho neudělá nikdo.

Spuštění:
    .\\.venv\\Scripts\\python.exe tests\\test_dokumentace.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "dokumentace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import tasks, web  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


DOKUMENTY = [cesta for cesta in
             (PROJECT / "README.md", PROJECT / "DEPLOY.md",
              PROJECT / "CONTRIBUTING.md", PROJECT / "SECURITY.md")
             if cesta.is_file()]
check(bool(DOKUMENTY), f"dokumenty se našly ({[d.name for d in DOKUMENTY]})")

TEXTY = {cesta.name: cesta.read_text(encoding="utf-8") for cesta in DOKUMENTY}

print()
print("--- kolik je naplánovaných úloh ---")
# Pocet uloh je nejvic zradna veta v celem README: pribyde uloha a nikdo
# si nevzpomene, ze je vypsana i v textu.
CISLOVKY = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
            6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
kolik = len(tasks.TASKS)
spravne = CISLOVKY[kolik]
for jmeno, text in TEXTY.items():
    nalezene = re.findall(r"has (\w+) tasks", text)
    for nalez in nalezene:
        check(nalez == spravne,
              f"{jmeno}: počet úloh sedí (píše {nalez!r}, je jich {kolik})")

print()
print("--- názvy úloh, které dokumentace vypisuje ---")
# Kdyz se uloha prejmenuje, tabulka v README zustane stat.
from jellyscope.i18n import EN  # noqa: E402

for task in tasks.TASKS.values():
    anglicky = EN.get(task.name, task.name)
    kde = [jmeno for jmeno, text in TEXTY.items() if anglicky in text]
    check(bool(kde), f"{anglicky!r} je v dokumentaci ({kde or 'nikde'})")

print()
print("--- port ukázky ---")
zdroj_dema = (PROJECT / "demo.py").read_text(encoding="utf-8")
port = re.search(r'setdefault\("PORT", "(\d+)"\)', zdroj_dema)
check(port is not None, "demo.py svůj port nastavuje")
if port:
    cislo = port.group(1)
    for jmeno, text in TEXTY.items():
        # Vety, ktere mluvi o ukazce a zaroven o adrese.
        for radek in text.splitlines():
            if "demo" in radek.lower() and "127.0.0.1:" in radek:
                check(f"127.0.0.1:{cislo}" in radek,
                      f"{jmeno}: adresa ukázky sedí", radek.strip()[:60])

print()
print("--- sekce Nastavení, na které se odkazuje ---")
# "Settings → Neco" musi byt sekce, ktera existuje. Preklad bereme
# z i18n, at se hlida i to, ze se sekce nepresune jinam.
nazvy = {EN.get(nazev, nazev) for _klic, nazev, _spravce in web.SETTINGS_SECTIONS}
# Nazvy karet uvnitr sekci - na ne se dokumentace odkazuje taky.
#
# Sekce Nastaveni jsou rozdelene do `templates/nastaveni/`, takze se
# hleda do hloubky. Cist jen `settings.html` by znamenalo, ze test po
# rozdeleni sablony tise prestane karty videt a zacne hlasit, ze
# dokumentace odkazuje nekam, kam ve skutecnosti odkazuje spravne.
for sablona in (PROJECT / "jellyscope" / "templates").rglob("*.html"):
    for nalez in re.findall(r'<h2>\{\{ _\("([^"]+)"\) \}\}</h2>',
                            sablona.read_text(encoding="utf-8")):
        nazvy.add(EN.get(nalez, nalez))

spatne = []
for jmeno, text in TEXTY.items():
    # Zalomeni radku uprostred nazvu ("Settings -> Data\ncollection") neni
    # jina sekce - text se proto nejdriv slepi do jedne rady.
    jedna_rada = " ".join(text.split())
    for nalez in re.findall(r"Settings → ([A-Z][A-Za-z ]+?)(?:\*|,|\.|:|\)|<| →|$)",
                            jedna_rada):
        cil = nalez.strip()
        if cil and cil not in nazvy:
            spatne.append(f"{jmeno}: {cil!r}")
check(not spatne, f"všechny odkazy míří na existující sekci ({spatne})")

print()
print("--- soubory vypsané ve struktuře projektu ---")
readme = TEXTY.get("README.md", "")
zminene = set(re.findall(r"[├└]──\s+(\w+\.py)", readme))
skutecne = {p.name for p in (PROJECT / "jellyscope").glob("*.py")
            if p.name != "__init__.py"}
chybi = sorted(skutecne - zminene)
navic = sorted(zminene - skutecne - {"run.py", "demo.py", "manage.py"})
check(not chybi, f"struktura zná všechny moduly (chybí: {chybi})")
check(not navic, f"a nevypisuje neexistující (přebývá: {navic})")

print()
print("--- odkazy na soubory v repozitáři ---")
chybejici = []
for jmeno, text in TEXTY.items():
    for odkaz in re.findall(r"`(deploy/[\w.\-]+|docs/[\w./\-]+)`", text):
        if not (PROJECT / odkaz).exists():
            chybejici.append(f"{jmeno}: {odkaz}")
    for odkaz in re.findall(r"\]\((docs/[\w./\-]+)\)", text):
        if not (PROJECT / odkaz).exists():
            chybejici.append(f"{jmeno}: {odkaz}")
check(not chybejici, f"odkazované soubory existují ({chybejici})")

# Past: bez teto kontroly by test prosel i nad vymyslenym odkazem.
check(not (PROJECT / "deploy/neexistuje.sh").exists(),
      "a kontrola pozná soubor, který tam není")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
