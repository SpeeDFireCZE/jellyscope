# -*- coding: utf-8 -*-
r"""Tlačítko nesmí volat funkci, která neexistuje.

Chyba, kterou to hlídá, opravdu nastala a stála data: tlačítko
„Zapomenout" volalo v `onclick` funkci, která v šabloně ještě nebyla.
Prohlížeč na takový `onclick` vyhodí chybu, **přeskočí ho** a provede
výchozí akci - formulář odešel a historie diváka zmizela bez jediné
otázky.

To je na tom to zákeřné: rozbitá pojistka se nechová jako rozbitá. Chová
se, jako by tam nebyla, a pozná se to až na datech, která už nejsou.

Kontrola je proto statická - projde šablony, najde každé volání funkce
v `onclick`/`onsubmit`/`onchange` a ověří, že ta funkce je ve stejné
šabloně (nebo ve společném základu) opravdu definovaná.

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


# Vestavene funkce prohlizece, ktere se nikde definovat nemusi - a `_`,
# coz je preklad v sablone. Ten Jinja dosadi jeste na serveru, takze do
# prohlizece se dostane hotovy text, ne volani.
VESTAVENE = {"confirm", "alert", "prompt", "print", "open", "reload",
             "submit", "focus", "blur", "preventDefault", "returnValue",
             "_",
             # Klicova slova JS - vypadaji jako volani, ale nejsou.
             "if", "for", "while", "switch", "return", "catch", "typeof"}

# `onclick="return neco(...)"` i `onclick="neco(...)"`. Tečka před jménem
# znamená metodu na objektu (`this.closest(...)`) - tu prohlížeč zná sám
# a definovat se nemusí.
VOLANI = re.compile(r'on(?:click|submit|change|input)="[^"]*?'
                    r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(')

zaklad = (SABLONY / "base.html").read_text(encoding="utf-8")

for soubor in sorted(SABLONY.glob("*.html")):
    text = soubor.read_text(encoding="utf-8")
    volane = {m for m in VOLANI.findall(text) if m not in VESTAVENE}
    if not volane:
        continue
    for jmeno in sorted(volane):
        # Funkce muze byt v teze sablone nebo ve spolecnem zakladu.
        vzor = re.compile(rf"(?:function\s+{re.escape(jmeno)}\s*\(|"
                          rf"\b{re.escape(jmeno)}\s*=\s*(?:function|\())")
        je = bool(vzor.search(text)) or bool(vzor.search(zaklad))
        check(je, f"{soubor.name}: {jmeno}() je definovaná")

# Past: kdyby kontrola nic nenasla, jeji ticho by nic neznamenalo.
vsechna = set()
for soubor in SABLONY.glob("*.html"):
    vsechna |= {m for m in VOLANI.findall(
        soubor.read_text(encoding="utf-8")) if m not in VESTAVENE}
check(len(vsechna) >= 1,
      f"a kontrola opravdu nějaká volání našla ({len(vsechna)})")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
