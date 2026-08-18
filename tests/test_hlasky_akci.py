# -*- coding: utf-8 -*-
r"""Hlášky po dokončené akci mluví jazykem, který má člověk nastavený.

Chyba, kterou to hlídá: po importu nebo po doběhnutí úlohy se v anglickém
rozhraní objevila česká věta „Jellystat: naimportováno 42 záznamů…“.

Důvod byl v tom, jak se ty hlášky skládaly. `_flash()` překlad umí, jenže
dostával **hotovou větu** složenou f-řetězcem - a hotová věta s číslem
uvnitř v překladovém slovníku není a nikdy nebude. Přeložit jde jen
šablona, hodnoty se do ní dosazují až potom.

Kontrola má dvě části:

  1. **Mechanika** - `_flash()` opravdu překládá šablonu a teprve pak
     doplňuje hodnoty.
  2. **Úplnost** - každá šablona, která se ve `web.py` používá, má
     anglický protějšek. Tohle je ta část, která hlídá budoucnost:
     novou hlášku bez překladu test odhalí dřív, než ji někdo uvidí.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_hlasky_akci.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "hlasky.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, i18n  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


print("--- šablona se přeloží, teprve pak se dosadí hodnota ---")
sablona = ("Jellystat: naimportováno {n} záznamů "
           "(z {nalezeno} nalezených, {duplicit} už existovalo).")
anglicky = i18n.translate(sablona, "en").format(n=42, nalezeno=50, duplicit=8)
check(anglicky == "Jellystat: imported 42 records "
                  "(out of 50 found, 8 already existed).",
      f"import ({anglicky})")
check("{" not in anglicky, "v hotové větě nezůstane žádná značka")

cesky = i18n.translate(sablona, "cs").format(n=42, nalezeno=50, duplicit=8)
check(cesky.startswith("Jellystat: naimportováno 42 záznamů"),
      f"česky se nic nezměnilo ({cesky})")


print()
print("--- _flash: překlad a dosazení v jednom ---")
from starlette.requests import Request  # noqa: E402

from jellyscope import web  # noqa: E402


class FalesnyRequest:
    """Request potřebuje jen session - _flash nic jiného nepoužívá."""

    def __init__(self) -> None:
        self.session: dict[str, object] = {}


db.set_setting("ui_language", "en")
i18n.forget_language() if hasattr(i18n, "forget_language") else None
db.forget_settings()

zadost = FalesnyRequest()
web._flash(zadost, "Záloha {nazev} smazána.", "success", nazev="jellyscope-2026.db")
hlaska = zadost.session["flash"]["message"]
check(hlaska == "Backup jellyscope-2026.db deleted.", f"anglicky ({hlaska})")

db.set_setting("ui_language", "cs")
db.forget_settings()
zadost = FalesnyRequest()
web._flash(zadost, "Záloha {nazev} smazána.", "success", nazev="jellyscope-2026.db")
check(zadost.session["flash"]["message"] == "Záloha jellyscope-2026.db smazána.",
      "a česky pořád taky")

# Špatná značka v šabloně nesmí shodit stránku - výsledek akce je
# důležitější než dokonalá věta.
zadost = FalesnyRequest()
web._flash(zadost, "Něco s {chybnou} značkou", "info", jina=1)
check("flash" in zadost.session, "překlep ve značce hlášku nezahodí")


print()
print("--- každá hláška má anglický protějšek ---")
strom = ast.parse((PROJECT / "jellyscope" / "web.py").read_text(encoding="utf-8"))
sablony: set[str] = set()
for uzel in ast.walk(strom):
    if not (isinstance(uzel, ast.Call)
            and getattr(uzel.func, "id", "") in ("_flash", "_t")):
        continue
    if uzel.func.id == "_flash":
        arg = uzel.args[1] if len(uzel.args) >= 2 else None
    else:
        arg = uzel.args[0] if uzel.args else None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        sablony.add(arg.value)

chybi = sorted(s for s in sablony if s not in i18n.EN)
check(len(sablony) > 40, f"kontrola opravdu prošla hlášky ({len(sablony)})")
check(not chybi, f"všechny mají překlad; chybí {len(chybi)}: {chybi[:3]}")

# Značky v překladu musí sedět s originálem - jinak dosazení spadne
# přesně v tom jazyce, kterým mluví ten druhý.
import re  # noqa: E402

VZOR = re.compile(r"\{(\w+)\}")
nesedi = []
for sablona in sablony:
    preklad = i18n.EN.get(sablona)
    if preklad and set(VZOR.findall(sablona)) != set(VZOR.findall(preklad)):
        nesedi.append(sablona)
check(not nesedi, f"a stejné značky jako originál; nesedí: {nesedi[:3]}")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
