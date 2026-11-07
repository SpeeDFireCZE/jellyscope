# -*- coding: utf-8 -*-
r"""Tlačítko nesmí volat funkci, která neexistuje.

Chyba, kterou to hlídá, opravdu nastala a stála data: tlačítko
„Zapomenout" volalo v `onclick` funkci, která v šabloně ještě nebyla.
Prohlížeč na takový `onclick` vyhodí chybu, **přeskočí ho** a provede
výchozí akci - formulář odešel a historie diváka zmizela bez jediné
otázky.

To je na tom to zákeřné: rozbitá pojistka se nechová jako rozbitá. Chová
se, jako by tam nebyla, a pozná se to až na datech, která už nejsou.

Kontrola je proto statická. Od přestavby na CSP bez vloženého kódu
(docs/superpowers/specs/2026-11-05-csp-skripty-design.md) obsluhu nesou
atributy data-…: projde šablony a ověří, že každý použitý atribut a každá
akce `data-akce` má obsluhu ve static/js.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_obsluha_tlacitek.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SABLONY = PROJECT / "jellyscope" / "templates"

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


# Obsluha prvků je od přestavby na CSP bez vloženého kódu deklarativní:
# šablona napíše atribut data-…, skript ve static/js ví, co s ním (viz
# tests/test_bez_vlozeneho_js.py - onclick= tam už být nesmí). Past je
# tatáž jako dřív: atribut, který žádný skript neobsluhuje, se chová, jako
# by tam nebyl. U data-potvrdit to znamená smazání bez jediné otázky.
#
# Atribut -> čím ho skript pozná (jméno atributu, nebo klíč v dataset).
DEKLARATIVNI = {
    "data-potvrdit": "potvrdit",
    "data-pri-chybe": "priChybe",
    "data-zavrit-okno": "data-zavrit-okno",
    "data-zavrit-hlasku": "data-zavrit-hlasku",
    "data-prepnout-motiv": "data-prepnout-motiv",
    "data-otevrit-okno": "otevritOkno",
    "data-odeslat-pri-zmene": "data-odeslat-pri-zmene",
    "data-nazev-souboru": "data-nazev-souboru",
}
SKRIPTY = "\n".join(s.read_text(encoding="utf-8")
                    for s in (PROJECT / "jellyscope" / "static" / "js").glob("*.js"))
sablony = {s: s.read_text(encoding="utf-8") for s in SABLONY.rglob("*.html")}

pouzite = set()
for atribut, znacka in DEKLARATIVNI.items():
    kde = sorted(s.relative_to(SABLONY).as_posix() for s, text in sablony.items()
                 if re.search(rf"\s{re.escape(atribut)}[\s=>]", text))
    if not kde:
        continue
    pouzite.add(atribut)
    # Celé jméno, ne kus: "potvrdit" by se našlo i v "potvrditX".
    vzor = (rf"dataset\.{znacka}\b" if not znacka.startswith("data-")
            else rf"[\"\[]{re.escape(znacka)}[\"\]=]")
    check(bool(re.search(vzor, SKRIPTY)), f"{atribut} (v {', '.join(kde[:3])}) má obsluhu ve static/js")

# data-akce="…" nese jméno akce - každé musí skript znát.
for sablona, text in sorted(sablony.items()):
    for akce in sorted(set(re.findall(r'data-akce="([^"]+)"', text))):
        pouzite.add(f"data-akce={akce}")
        check(f'"{akce}"' in SKRIPTY,
              f"{sablona.relative_to(SABLONY).as_posix()}: akce {akce} má obsluhu ve static/js")

# Past: kdyby kontrola nic nenasla, jeji ticho by nic neznamenalo.
check(len(pouzite) >= 5, f"a kontrola opravdu nějaké atributy našla ({len(pouzite)})")
check("data-potvrdit" in pouzite, "včetně potvrzení před nevratnou akcí")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
