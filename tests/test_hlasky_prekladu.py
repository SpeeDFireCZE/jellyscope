# -*- coding: utf-8 -*-
r"""Chybové hlášky u prvního spuštění a přihlášení mluví zvoleným jazykem.

Chyba, kterou to hlídá: na stránce prvního spuštění si člověk přepnul
rozhraní na angličtinu, spletl se v hesle - a dostal českou hlášku
„Heslo musí mít aspoň 8 znaků."

Jsou v tom dvě pasti a každá sama o sobě stačí:

  1. Hláška nejde přes `_flash()`, který překládá, ale rovnou do šablony.
  2. Jazyk zvolený ve formuláři **ještě není uložený** v databázi, takže
     překlad „podle nastavení" by sáhl po tom předchozím. Přeložit se
     musí výslovně do jazyka z formuláře.

Navíc se hlášky skládají z šablony a hodnoty („aspoň {n} znaků"), takže
hotová věta v překladovém slovníku není - překládá se šablona a hodnota
se dosazuje až potom. Tohle umí `accounts.AccountError.prelozena()`.

Pořadí v testu není náhodné: stránka prvního spuštění se musí vyzkoušet
dřív, než v databázi vznikne účet - potom už se jen přesměrovává na
přihlášení.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_hlasky_prekladu.py
"""
from __future__ import annotations

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

from jellyscope import accounts, db, i18n  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
# Nastavení je české - přesně jako při úplně prvním spuštění, kdy si
# člověk teprve vybírá jazyk ve formuláři.
db.set_setting("ui_language", "cs")

print("--- první spuštění: hláška v jazyce z FORMULÁŘE, ne z nastavení ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    odpoved = client.post("/setup", data={
        "username": "spravce", "password": "krat", "password_again": "krat",
        "ui_language": "en",
    })
    check(odpoved.status_code == 400, f"krátké heslo neprojde ({odpoved.status_code})")
    check("The password must be at least 8 characters." in odpoved.text,
          "a hláška přijde anglicky")
    check("Heslo musí mít aspoň" not in odpoved.text,
          "česká varianta ve stránce není")

    odpoved = client.post("/setup", data={
        "username": "spravce", "password": "kratke", "password_again": "kratke",
        "ui_language": "cs",
    })
    check("Heslo musí mít aspoň 8 znaků." in odpoved.text,
          "při české volbě zůstává čeština")

    # Teprve teď účet doopravdy založíme - další kontroly už s ním počítají.
    odpoved = client.post("/setup", data={
        "username": "spravce", "password": "dlouheheslo",
        "password_again": "dlouheheslo", "ui_language": "cs",
    }, follow_redirects=False)
    check(odpoved.status_code == 303, f"správné heslo projde ({odpoved.status_code})")


print()
print("--- hláška si pamatuje šablonu i hodnotu ---")
try:
    accounts.validate_password("krat")
    chyba = None
except accounts.AccountError as exc:
    chyba = exc

check(chyba is not None, "krátké heslo je chyba")
check(str(chyba) == "Heslo musí mít aspoň 8 znaků.",
      f"česky se nic nezměnilo ({chyba})")
check(chyba.prelozena("en") == "The password must be at least 8 characters.",
      f"anglicky i s dosazeným číslem ({chyba.prelozena('en')})")
check(chyba.prelozena("cs") == str(chyba), "čeština zůstává čeština")
# Číslo se dosazuje AŽ PO překladu - jinak by se hotová věta ve slovníku
# nenašla a zůstala by česky.
check("{n}" not in chyba.prelozena("en"), "v přeložené větě nezůstane zástupný znak")

try:
    accounts.create("spravce", "dlouheheslo")
    chyba = None
except accounts.AccountError as exc:
    chyba = exc
check(chyba is not None and "spravce" in chyba.prelozena("en"),
      f"jméno účtu se dosadí i do anglické věty ({chyba and chyba.prelozena('en')})")


print()
print("--- přihlášení ---")
check(i18n.translate("Špatné jméno nebo heslo.", "en") == "Wrong username or password.",
      f"špatné heslo ({i18n.translate('Špatné jméno nebo heslo.', 'en')})")
check(i18n.translate("Příliš mnoho pokusů. Zkus to za {n} min.", "en").format(n=5)
      == "Too many attempts. Try again in 5 min.",
      "blokace i s dosazeným časem")
# Věta o trvalé blokaci se skládá ze dvou řetězců - snadno se stane, že
# se do slovníku dostane jen půlka a překlad se pak nenajde vůbec.
dlouha = ("Přihlašování z této adresy je zablokované. "
          "Odblokovat ho může správce v Nastavení.")
check(i18n.translate(dlouha, "en").startswith("Signing in from this address"),
      "i dvouřádková věta o trvalé blokaci")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
