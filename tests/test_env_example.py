# -*- coding: utf-8 -*-
r"""Co aplikace čte z prostředí, to je popsané v .env.example.

Proměnná, o které se nikde nepíše, je proměnná, kterou nikdo nenajde -
leda čtením zdrojáku. A přibývají tiše: stačí napsat os.environ.get(...)
a dokumentace zestárne, aniž by kdokoliv něco smazal.

Test proto porovnává obojí a hlídá i opačný směr: JELLYSCOPE_DOCKER se
v .env.example jenom popisuje, nikdy nenastavuje. Kdyby tam někdo přidal
činný řádek, každý, kdo si soubor zkopíruje, by si tím na běžném stroji
zakázal aktualizaci z prohlížeče.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_env_example.py
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


# Čteme zdroják, ne běžící aplikaci: proměnná se může číst v kódu, který
# se za normálního běhu nikdy nespustí (a právě ta by se zapomněla).
CTENI = re.compile(
    r"""os\.environ\.get\(\s*["']([A-Z_]+)["']"""
    r"""|os\.getenv\(\s*["']([A-Z_]+)["']"""
    r"""|os\.environ\[\s*["']([A-Z_]+)["']\s*\]"""
    r"""|_flag\(\s*["']([A-Z_]+)["']"""
)

# Proměnné, které si nastavuje operační systém nebo Python sám a do
# konfigurace aplikace nepatří.
CIZI = {"PATH", "PYTHONIOENCODING", "TEMP", "TMP", "USERPROFILE", "HOME"}

ctene: set[str] = set()
for soubor in sorted((PROJECT / "jellyscope").glob("*.py")):
    for nalez in CTENI.finditer(soubor.read_text(encoding="utf-8")):
        jmeno = next(g for g in nalez.groups() if g)
        if jmeno not in CIZI:
            ctene.add(jmeno)

priklad = (PROJECT / ".env.example").read_text(encoding="utf-8")

print("--- každá proměnná je v .env.example popsaná ---")
check(len(ctene) >= 10, f"našly se proměnné v kódu ({len(ctene)})")
for jmeno in sorted(ctene):
    check(re.search(rf"\b{jmeno}\b", priklad) is not None, jmeno)

print()
print("--- JELLYSCOPE_DOCKER se jenom popisuje ---")
# Činný řádek = nezačíná mřížkou. Zakomentovaný popis je v pořádku.
cinne = [
    radek for radek in priklad.splitlines()
    if radek.strip().startswith("JELLYSCOPE_DOCKER")
]
check(not cinne, f"žádný činný řádek ({cinne})")
check("JELLYSCOPE_DOCKER" in priklad, "ale zmíněný tam je")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
