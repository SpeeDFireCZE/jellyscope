# -*- coding: utf-8 -*-
r"""Tlačítka v Nastavení nechávají stránku tam, kde je.

Akce v Nastavení se dělají uprostřed dlouhé stránky a každá z nich ji
načte znovu. Bez pomoci by pokaždé začínala od začátku, takže po každém
uložení následovalo hledání místa, kde člověk byl.

`data-keep-scroll` (obsluha je v `base.html`) si při kliknutí polohu
zapamatuje a po načtení ji vrátí. Tenhle test hlídá, že ten atribut má
**každé** tlačítko, po kterém se stránka Nastavení načte znovu - jinak
by se na jedno při příštím přidávání zapomnělo a chovalo by se jinak
než ostatní.

Zavírací tlačítka oken (`method="dialog"`) v seznamu být nemají: nic
nenačítají, takže by jim atribut byl k ničemu.

Že to doopravdy funguje, je ověřeno v prohlížeči přes CDP - ne tímhle
testem. Ten hlídá jen to, aby atribut nikde nechyběl.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_poloha_stranky.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


SEKCE = PROJECT / "jellyscope" / "templates" / "nastaveni"
BASE = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(
    encoding="utf-8")

print("--- obsluha na stránce je ---")
check("[data-keep-scroll]" in BASE, "base.html poslouchá na data-keep-scroll")
check("sessionStorage" in BASE and "scrollTo" in BASE,
      "polohu si ukládá a po načtení ji vrací")
check("10000" in BASE, "a po deseti vteřinách ji zahodí (kdyby se stránka "
                       "znovu nenačetla)")

print()
print("--- i když stránku načte skript, ne člověk ---")
# Rucne spustena uloha, dobehnuti ulohy na pozadi, zavreni okna
# s rozvrzenim, tah po grafu: vsude to stranku nacte skript, takze
# kliknuti k zapamatovani polohy nestaci.
samo = [m.start() for m in re.finditer(
    r"window\.location\.(reload\(\)|replace\(|href = )", BASE)]
bez_pameti = [i for i in samo
              if "zapamatujPolohu" not in BASE[max(0, i - 400):i]]
check(len(samo) >= 4, f"skript stránku načítá na {len(samo)} místech")
check(not bez_pameti,
      f"a všechna si polohu zapamatují ({len(bez_pameti)} bez toho)")

print()
print("--- každé tlačítko, po kterém se Nastavení načte znovu ---")
chybi: list[str] = []
dialogy = 0
for sablona in sorted(SEKCE.glob("*.html")):
    text = sablona.read_text(encoding="utf-8")
    formulare = {}
    for shoda in re.finditer(r"<form[^>]*>", text):
        idcko = re.search(r'id="([^"]+)"', shoda.group(0))
        akce = re.search(r'action="([^"]+)"', shoda.group(0))
        if idcko:
            formulare[idcko.group(1)] = akce.group(1) if akce else ""

    for shoda in re.finditer(r"<button[^>]*>", text):
        znacka = shoda.group(0)
        if 'type="submit"' not in znacka:
            continue
        odkaz = re.search(r'form="([^"]+)"', znacka)
        if odkaz:
            akce = formulare.get(odkaz.group(1), "")
        else:
            pred = text[:shoda.start()]
            zacatek = pred.rfind("<form")
            hlavicka = text[zacatek:text.find(">", zacatek) + 1]
            akce_m = re.search(r'action="([^"]+)"', hlavicka)
            metoda = re.search(r'method="([^"]+)"', hlavicka)
            if metoda and metoda.group(1) == "dialog":
                dialogy += 1
                continue
            akce = akce_m.group(1) if akce_m else ""
        if not akce.startswith("/settings"):
            continue
        if "data-keep-scroll" not in znacka:
            radek = text[:shoda.start()].count("\n") + 1
            chybi.append(f"{sablona.name}:{radek} -> {akce}")

check(not chybi, f"všechna mají data-keep-scroll ({len(chybi)} bez: {chybi[:3]})")
check(dialogy >= 4,
      f"zavírací tlačítka oken se nepočítají ({dialogy} přeskočeno)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
