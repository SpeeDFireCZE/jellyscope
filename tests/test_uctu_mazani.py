# -*- coding: utf-8 -*-
"""Mazání účtů — a hlavně to, že vždycky zbude aspoň jeden správce.

Pravidlo je jednoduché a jeho porušení má jediný, zato nepříjemný
následek: **nikdo se už do Jellyscope nedostane**. Databázi by pak musel
zachraňovat člověk s přístupem na server.

Testuje se na třech místech, protože každé chrání něco jiného:

  * `accounts.delete()` — samotné pravidlo. Platí i pro kód, který by
    tuhle funkci zavolal odjinud než z webu.
  * routa `/settings/accounts/delete` — přidává „vlastní účet ne" a
    kontroluje, že mazat smí jen správce.
  * šablona — tlačítko se u posledního správce vůbec neukáže. To je
    pohodlí, ne ochrana; ta zůstává v accounts.delete().
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
# Vlastní domeček: bez něj by DATABASE_PATH přebil uložený výběr
# databáze v data/database.json a test by běžel proti ostré databázi.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "ucty.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0
HESLO = "dlouheheslo"


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def prihlas(jmeno: str) -> TestClient:
    klient = TestClient(app)
    klient.post("/login", data={"username": jmeno, "password": HESLO})
    return klient


def hlaska(odpoved: Any) -> str:
    m = re.search(r'<div class="flash \w+">.*?<span>([^<]*)</span>', odpoved.text, re.S)
    return " ".join(m.group(1).split()) if m else ""


db.init_db()

print("--- samotné pravidlo (accounts.delete) ---")
jediny = accounts.create("spravce1", HESLO, is_admin=True)
try:
    accounts.delete(jediny)
    check(False, "poslední správce NEŠEL smazat")
except accounts.AccountError as chyba:
    check("posledního správce" in str(chyba).lower(),
          f"poslední správce je chráněný: {chyba}")

druhy = accounts.create("spravce2", HESLO, is_admin=True)
accounts.delete(jediny)
check(accounts.admin_count() == 1, "při dvou správcích jde jeden smazat")
try:
    accounts.delete(druhy)
    check(False, "a ten poslední NEŠEL smazat")
except accounts.AccountError:
    check(True, "a ten poslední už zase ne")


print()
print("--- přes rozhraní ---")
sprava = accounts.create("spravce", HESLO, is_admin=True)
ctenar = accounts.create("ctenar", HESLO, is_admin=False)
klient = prihlas("spravce")

pred = accounts.count()
odpoved = klient.post("/settings/accounts/delete",
                      data={"account_id": ctenar}, follow_redirects=True)
check(accounts.count() == pred - 1, f"čtenáře jde smazat ({hlaska(odpoved)})")

odpoved = klient.post("/settings/accounts/delete",
                      data={"account_id": sprava}, follow_redirects=True)
check(accounts.get(sprava) is not None, "vlastní účet smazat nejde")
check("vlastní účet" in hlaska(odpoved).lower(), f"a řekne proč: {hlaska(odpoved)}")

# Druhý správce: jeden z nich smazat jde, dokud jsou dva.
kolega = accounts.create("kolega", HESLO, is_admin=True)
check(accounts.admin_count() == 3, f"teď jsou tři správci ({accounts.admin_count()})")
klient.post("/settings/accounts/delete", data={"account_id": kolega},
            follow_redirects=True)
check(accounts.get(kolega) is None, "správce jde smazat, když nezůstane sám")


print()
print("--- kdo smí mazat ---")
divak = accounts.create("divak", HESLO, is_admin=False)
klient_divak = prihlas("divak")
odpoved = klient_divak.post("/settings/accounts/delete",
                            data={"account_id": divak}, follow_redirects=False)
check(odpoved.status_code == 403, f"čtenář mazat nesmí ({odpoved.status_code})")
check(accounts.get(divak) is not None, "a účet zůstal")

hoste = TestClient(app)
odpoved = hoste.post("/settings/accounts/delete",
                     data={"account_id": divak}, follow_redirects=False)
check(odpoved.status_code in (302, 303, 307), "nepřihlášený se ani nedostane k formuláři")
check(accounts.get(divak) is not None, "a účet zůstal i tak")


print()
print("--- co je vidět na stránce ---")
# Zůstal jediný správce - u něj se tlačítko nemá ukázat vůbec.
for ucet in accounts.all_accounts():
    if ucet["is_admin"] and ucet["username"] != "spravce":
        accounts.delete(ucet["id"])
check(accounts.admin_count() == 1, f"zbyl jeden správce ({accounts.admin_count()})")

# Poslední správce je vždycky ten přihlášený - kdyby jím byl někdo jiný,
# byli by správci dva. Vysvětlení proto patří k VLASTNÍMU řádku.
html = klient.get("/settings?section=accounts").text
radek_spravce = next(r for r in re.findall(r"<tr>.*?</tr>", html, re.S)
                     if "spravce" in r and "(" in r)
check("poslední správce" in radek_spravce,
      "u posledního správce je vysvětlení místo tlačítek")
check("/settings/accounts/delete" not in radek_spravce,
      "a formulář pro smazání tam není")
check("/settings/accounts/role" not in radek_spravce,
      "ani pro odebrání práv")

# U čtenáře tlačítko být musí - a červeně, protože je to nevratné.
ctenar2 = accounts.create("ctenar2", HESLO, is_admin=False)
html = klient.get("/settings?section=accounts").text
radek_ctenar = next(r for r in re.findall(r"<tr>.*?</tr>", html, re.S)
                    if "ctenar2" in r)
check("/settings/accounts/delete" in radek_ctenar, "u čtenáře tlačítko je")
check('class="btn danger"' in radek_ctenar, "a je červené (nevratná akce)")
check("confirm(" in radek_ctenar and "nejde vzít zpět" in radek_ctenar,
      "s otázkou, která říká, co se stane")


print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
