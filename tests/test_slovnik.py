# -*- coding: utf-8 -*-
r"""Překladový slovník: bez dvojích klíčů a bez mrtvých hesel.

Slovník je obyčejný Python dict, takže **stejný klíč napsaný dvakrát
tiše přebije ten dřívější** - a nikde se to neohlásí. Zrovna to se stalo
šestadvacetkrát a u osmi z nich se ty dva překlady lišily, takže si
aplikace vybírala ten pozdější:

  * na Síti stálo u zařízení „Last run" místo „Last seen",
  * u seriálu „at 3 seasons" místo „in 3 seasons",
  * u tabulek databáze „Lines" místo „Rows".

Česky je to pokaždé stejné slovo, anglicky ne - a jeden klíč dvě věci
neunese. Řešení je pojmenovat obojí zvlášť (například „Naposledy" proti
„Naposledy běželo"), ne jeden překlad umazat.

Druhá půlka testu hlídá opačný směr: heslo, které se nikde nepoužívá.
Těch se našlo 43 - zbytky po přepsaných hláškách a zrušených tlačítkách.
Nic nerozbijí, jen dělají ze slovníku smetiště, ve kterém se překlad
hledá hůř.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_slovnik.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


I18N = PROJECT / "jellyscope" / "i18n.py"
slovniky: dict[str, list[tuple[str, int]]] = {}
for uzel in ast.parse(I18N.read_text(encoding="utf-8")).body:
    if isinstance(uzel, ast.AnnAssign) and isinstance(uzel.value, ast.Dict):
        jmeno = getattr(uzel.target, "id", "")
        if jmeno in ("EN", "LOG_EN"):
            slovniky[jmeno] = [(k.value, k.lineno) for k in uzel.value.keys
                               if isinstance(k, ast.Constant)]

print("--- žádný klíč dvakrát ---")
check(set(slovniky) == {"EN", "LOG_EN"}, f"oba slovníky se našly ({list(slovniky)})")
for jmeno, klice in slovniky.items():
    videno: dict[str, int] = {}
    dvojmo = []
    for klic, radek in klice:
        if klic in videno:
            dvojmo.append(f"{klic[:40]!r} (řádky {videno[klic]} a {radek})")
        videno[klic] = radek
    check(not dvojmo, f"{jmeno}: {len(klice)} klíčů, duplicity: {dvojmo[:3] or 'žádné'}")

print()
print("--- žádné heslo navíc ---")
# Klíč se v kódu může objevit rozdělený přes dva řádky ("začátek "
# "pokračování"), takže se hledá i ve slepené podobě. Bez toho by test
# hlásil jako nepoužité skoro každou delší větu.
zdroj = ""
for cesta in (list((PROJECT / "jellyscope").glob("*.py"))
              + list((PROJECT / "jellyscope" / "templates").glob("*.html"))
              + [PROJECT / x for x in ("run.py", "manage.py", "demo.py")]):
    if cesta.name == "i18n.py":
        continue
    zdroj += cesta.read_text(encoding="utf-8") + "\n"

slepeny = re.sub(r'"\s*\n\s*"', "", zdroj)
slepeny = re.sub(r"'\s*\n\s*'", "", slepeny)
slepeny = re.sub(r"\s*\n\s*", " ", slepeny)
slepeny = re.sub(r'"\s*"', "", slepeny)

for jmeno, klice in slovniky.items():
    nepouzite = [k for k, _ in klice
                 if k not in zdroj and re.sub(r"\s+", " ", k) not in slepeny]
    check(not nepouzite,
          f"{jmeno}: bez použití {len(nepouzite)} {[k[:35] for k in nepouzite[:3]]}")

print()
print("--- co šablony chtějí přeložit, to ve slovníku je ---")
# Jinak se na anglické stránce objeví české slovo. Kontrolují se jen
# doslovné klíče: `_(promenna)` se staticky přečíst nedá.
EN = dict(slovniky["EN"])
chybi = []
for cesta in sorted((PROJECT / "jellyscope" / "templates").glob("*.html")):
    text = cesta.read_text(encoding="utf-8")
    for nalez in re.finditer(r'_\(\s*"([^"]{2,})"\s*\)', text):
        klic = nalez.group(1)
        if klic not in EN and not klic.startswith("{"):
            chybi.append(f"{cesta.name}: {klic[:40]!r}")
check(not chybi, f"nepřeložených klíčů v šablonách: {len(chybi)} {chybi[:3]}")

print()
print("--- dosazovaná místa sedí v obou jazycích ---")
# "{n} dílů" -> "{n} episodes". Kdyby v překladu {n} chybělo, číslo se
# tiše ztratí: věta dává smysl, jen v ní není údaj, kvůli kterému vznikla.
from jellyscope import i18n  # noqa: E402

spatne = []
for klic, anglicky in i18n.EN.items():
    v_klici = set(re.findall(r"\{(\w+)\}", klic))
    v_prekladu = set(re.findall(r"\{(\w+)\}", anglicky))
    if v_klici != v_prekladu:
        spatne.append(f"{klic[:40]!r}: {sorted(v_klici)} -> {sorted(v_prekladu)}")
check(not spatne, f"nesedících: {len(spatne)} {spatne[:3]}")

# A jedna věta na zkoušku: přesně ta, kvůli které vznikl klíč s frází.
veta = i18n.translate("v {n} řadách", "en").format(n=3)
check(veta == "in 3 seasons", f"věta o řadách: {veta!r}")
obdobi = i18n.translate("za {obdobi}", "en").format(obdobi="30 days")
check(obdobi == "over 30 days", f"věta o období: {obdobi!r}")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
