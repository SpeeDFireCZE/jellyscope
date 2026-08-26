# -*- coding: utf-8 -*-
r"""Přepínání mezi novým a klasickým vzhledem.

Nový vzhled změnil barvy i tvary; kdo je na starou podobu zvyklý, může
se k ní vrátit v Nastavení → Rozhraní. Vybraný vzhled se dosazuje do
`<html data-skin>` **na serveru**, ne až JavaScriptem: jinak by stránka
na okamžik probliknula v jednom vzhledu a přepnula se do druhého.

Celý klasický vzhled je jen CSS - grafy se kreslí přes proměnné
(--accent, --series-*, --good), takže se přebarví samy a Python o žádném
vzhledu neví. Test to hlídá: v šablonách ani v kódu grafů nesmí být
podmínka na vzhled.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_vzhled.py
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
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "vzhled.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import web  # noqa: E402

client = TestClient(web.app)
client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
            follow_redirects=False)

print("--- výchozí je nový ---")
check(db.get_setting("ui_skin", "") == "novy", "v nastavení je 'novy'")
check('data-skin="novy"' in client.get("/").text, "a stránka to říká rovnou v <html>")

print()
print("--- přepnutí na klasický ---")
client.post("/settings/interface",
            data={"ui_max_streams": "10", "ui_max_viewers": "10",
                  "ui_map_zoom": "click", "ui_skin": "klasicky"},
            follow_redirects=False)
check(db.get_setting("ui_skin", "") == "klasicky", "uložilo se")
check('data-skin="klasicky"' in client.get("/").text, "a stránka se přepnula")

print()
print("--- podvržená hodnota se neuloží ---")
# Vzhled se dosazuje do HTML, takže cokoliv odsud musí projít pevným
# seznamem - jinak by se dalo do atributu propašovat co kdo chce.
client.post("/settings/interface",
            data={"ui_max_streams": "10", "ui_max_viewers": "10",
                  "ui_map_zoom": "click", "ui_skin": '"><script>x()</script>'},
            follow_redirects=False)
check(db.get_setting("ui_skin", "") == "novy", "neznámý vzhled spadne na 'novy'")
check("<script>x()</script>" not in client.get("/").text, "a do stránky se nedostal")

print()
print("--- klasický vzhled je jen CSS ---")
# Kdyby se vzhled začal rozhodovat v šablonách nebo v grafech, přestal
# by to být přepínač a stal se z něj druhý kód, který se musí udržovat.
# Vzhled smi znat prave dve sablony: base.html ho dosadi do <html>
# a settings.html nabizi prepinac. Kdyby se objevil jinde, znamenalo by
# to, ze nekde vznika druhá podoba stranky - a ta uz se musi udrzovat.
sablony = {p.name: p.read_text(encoding="utf-8")
           for p in (PROJECT / "jellyscope" / "templates").glob("*.html")}
check(sablony["base.html"].count("ui_skin") == 1, "base.html vzhled jen dosadí")
check(sablony["settings.html"].count("ui_skin") == 4,
      f"settings.html ho nabízí k výběru ({sablony['settings.html'].count('ui_skin')}×)")
jinde = {jmeno: text.count("ui_skin") for jmeno, text in sablony.items()
         if jmeno not in ("base.html", "settings.html") and "ui_skin" in text}
check(not jinde, f"a jinde se podle něj nic nerozhoduje ({jinde})")
grafy = (PROJECT / "jellyscope" / "charts.py").read_text(encoding="utf-8")
check("skin" not in grafy and "klasick" not in grafy, "grafy o vzhledu nevědí")

styl = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
check('data-skin="klasicky"' in styl, "a celý bydlí ve style.css")
# Klasicky vzhled musi prekryt obe temata, jinak by v tmavem rezimu
# zustaly nove barvy.
check(styl.count(':root[data-skin="klasicky"]') >= 3,
      f"pro světlý i tmavý režim ({styl.count(chr(58) + 'root[data-skin=' + chr(34) + 'klasicky' + chr(34) + ']')}×)")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
