# -*- coding: utf-8 -*-
r"""Dlouhé tabulky na stránce Zjištění se zkrátí podle nastavení.

Tabulka „Místo, které nikdo nevyužívá" měla 25 řádků, „Nejčastěji
transcodované soubory" 15 - a karta vedle nich („Proč se transcoduje")
měla pod sebou prázdnou plochu na výšku dvaceti řádků. Kolik řádků karta
ukáže rovnou, teď určuje Nastavení → Rozhraní → Dlouhé seznamy, zvlášť
pro širokou obrazovku a pro mobil, stejně jako u streamů a diváků.
Zbytek otevře tlačítko „zobrazit všech N" v okně.

Hlídá se:

* výchozí strop (10, na mobilu 5) - řádky nad ním jsou schované,
  tlačítko je na stránce a okno pro celou tabulku taky,
* vyšší strop všechno ukáže a tlačítko zmizí,
* nastavení se dá uložit a ořízne se do mezí,
* pole jsou ve formuláři Rozhraní.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_zjisteni_strop.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "strop.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

# Patnact nikdy nehranych filmu s velikosti - vsechny skonci v tabulce
# "Misto, ktere nikdo nevyuziva".
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for i in range(15):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, is_missing, size_bytes)"
            " VALUES (?, ?, 'Movie', 'filmy', 0, ?)",
            (f"f{i}", f"Film {i:02d}", (20 - i) * 1_000_000_000))
    conn.commit()

klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})


def karta_mista(html: str) -> str:
    """Celá karta „Místo, které nikdo nevyužívá" i s tlačítkem pod tabulkou."""
    zacatek = html.index('id="nevyuzite-misto"')
    return html[zacatek:html.index('<div class="grid-2', zacatek)]


def radky(html: str) -> list[str]:
    return re.findall(r"<tr[^>]*>\s*<td", html)


print("--- výchozí strop: 10 rovnou, 5 na mobilu ---")
html = klient.get("/insights").text
karta = karta_mista(html)
vsechny = radky(karta)
check(len(vsechny) == 15, f"v tabulce je všech 15 řádků ({len(vsechny)})")
navic = [r for r in vsechny if "radek-navic" in r]
jen_desktop = [r for r in vsechny if "jen-desktop" in r]
check(len(navic) == 5, f"5 řádků nad stropem je schovaných ({len(navic)})")
check(len(jen_desktop) == 5, f"5 dalších se schová jen na mobilu ({len(jen_desktop)})")
check("data-okno-tabulka" in karta, "pod tabulkou je tlačítko na celou tabulku")
check('id="okno-tabulka"' in html, "a okno, do kterého se tabulka otevře")

print()
print("--- vyšší strop ukáže všechno ---")
odpoved = klient.post("/settings/interface", data={
    "ui_max_streams": "10", "ui_max_streams_mobile": "3",
    "ui_max_viewers": "10", "ui_max_viewers_mobile": "5",
    "ui_max_rows": "20", "ui_max_rows_mobile": "20",
    "ui_map_zoom": "click", "ui_skin": "novy", "ui_cas_presne": "0", "ui_dashboard": "0",
}, follow_redirects=False)
check(odpoved.status_code == 303, f"nastavení se uložilo ({odpoved.status_code})")
check(db.get_setting("ui_max_rows") == "20", "strop řádků je v databázi")
db.forget_settings()
karta = karta_mista(klient.get("/insights").text)
check(not any("radek-navic" in r or "jen-desktop" in r for r in radky(karta)),
      "nic není schované")
check("data-okno-tabulka" not in karta, "a tlačítko na celou tabulku zmizelo")

print()
print("--- tlačítko je všude, kde je něco schované ---")


def uloz_stropy(desktop: str, mobil: str) -> None:
    klient.post("/settings/interface", data={"ui_max_rows": desktop, "ui_max_rows_mobile": mobil})
    db.forget_settings()


def tlacitko(karta: str) -> str | None:
    """Třídy řádku s tlačítkem na celou tabulku, nebo None, když tam není."""
    m = re.search(r'<div class="(btn-row[^"]*)"[^>]*>\s*<button[^>]*data-okno-tabulka', karta)
    return m.group(1) if m else None


# Mobil 20, desktop 12: radky 13-15 jsou schovane vsude. Tlacitko na ne
# musi byt i tak - driv se kreslilo jen nad mobilnim stropem a tri radky
# zmizely bez moznosti je zobrazit.
uloz_stropy("12", "20")
karta = karta_mista(klient.get("/insights").text)
check(sum("radek-navic" in r for r in radky(karta)) == 3, "obrácené stropy: 3 řádky schované")
check(tlacitko(karta) == "btn-row", f"a tlačítko na ně je všude ({tlacitko(karta)})")

# Desktop 20, mobil 5: na sirokem je videt vsech 15, na mobilu jen 5.
# Tlacitko je jen na mobilu.
uloz_stropy("20", "5")
karta = karta_mista(klient.get("/insights").text)
check(tlacitko(karta) == "btn-row jen-mobil", f"schované jen na mobilu: tlačítko jen na mobilu ({tlacitko(karta)})")

print()
print("--- meze a formulář ---")
klient.post("/settings/interface", data={"ui_max_rows": "99999", "ui_max_rows_mobile": "0"})
db.forget_settings()
check(int(db.get_setting("ui_max_rows")) <= web.STROP_MAX, "příliš velké číslo se ořízne")
check(int(db.get_setting("ui_max_rows_mobile")) >= 1, "nula se nepřijme")
formular = klient.get("/settings?section=interface").text
check('name="ui_max_rows"' in formular and 'name="ui_max_rows_mobile"' in formular,
      "obě pole jsou v Nastavení → Rozhraní → Dlouhé seznamy")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
