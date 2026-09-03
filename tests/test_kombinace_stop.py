# -*- coding: utf-8 -*-
"""Okno se všemi kombinacemi stop.

Karta „Kombinace stop" ukazuje čtyři nejčastější a zbytek shrne do řádku
„Ostatní (N dalších kombinací)". Ten řádek dosud jen konstatoval, že se
něco skrývá, a nedal to jak zobrazit — u knihovny, kde v „Ostatní" leží
třeba čtvrtina titulů, je to slepá ulička.

Na čem to stojí:

* **Okno má úplný seznam**, ne jen ten schovaný zbytek: člověk chce
  vidět celý žebříček, ne dva odtržené kusy.
* **Procenta se počítají ze stejného celku** jako na kartě. Kdyby se
  v okně počítala jen z vypsaných řádků, znamenalo by „56 %" na každém
  místě něco jiného a nešlo by to poznat.
* **Když karta nic neskrývá, okno se nekreslí.** Bylo by druhou kopií
  téže tabulky a tlačítko by nemělo co otevřít.

Spuštění:
    .\\.venv\\Scripts\\python.exe tests\\test_kombinace_stop.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kombinace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from jellyscope import db, langstats  # noqa: E402

failures = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        failures += 1


def polozka(cislo: int, jazyky: str, velikost: int = 1024 ** 3) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, type, audio_languages, size_bytes,"
            " is_missing) VALUES (?,?,?,?,?,0)",
            (f"i{cislo}", f"Film {cislo}", "Movie", jazyky, velikost))
        conn.commit()


db.init_db()

# Knihovna s dvanácti kombinacemi: ctyri velke a osm drobnych. Presne
# ten pripad, kvuli kteremu radek "Ostatni" vznikl.
VELKE = [("cs,en", 40), ("en", 30), ("cs", 20), ("cs,en,sk", 10)]
DROBNE = ["de", "fr", "en,fr", "cs,de", "pl", "en,pl", "cs,en,de", "hu"]

cislo = 0
for jazyky, kolik in VELKE:
    for _ in range(kolik):
        cislo += 1
        polozka(cislo, jazyky)
for jazyky in DROBNE:
    for _ in range(2):
        cislo += 1
        polozka(cislo, jazyky)

CELKEM_KOMBINACI = len(VELKE) + len(DROBNE)
CELKEM_TITULU = sum(kolik for _, kolik in VELKE) + len(DROBNE) * 2

print("--- data ---")
karta = langstats.language_combinations()
vsechny = langstats.vsechny_kombinace()
check(len(karta) == 5, f'karta ukazuje čtyři a „Ostatní“ ({len(karta)} řádků)')
check(len(vsechny) == CELKEM_KOMBINACI,
      f"okno má všechny kombinace ({len(vsechny)} z {CELKEM_KOMBINACI})")

# Souctem to musi sedet - jinak by okno mluvilo o jine knihovne nez karta.
check(sum(int(r["item_count"]) for r in vsechny) == CELKEM_TITULU,
      f"a všechny tituly ({CELKEM_TITULU})")
check(sum(int(r["item_count"]) for r in karta) == CELKEM_TITULU,
      "karta jich má se souhrnem stejně")

# Procenta z tehoz celku: prvni radek musi vyjit stejne na obou mistech.
check(abs(karta[0]["percent"] - vsechny[0]["percent"]) < 0.001,
      f"procenta sedí ({karta[0]['percent']:.1f} % vs {vsechny[0]['percent']:.1f} %)")
check(abs(sum(r["percent"] for r in vsechny) - 100) < 0.01,
      "a v okně dávají dohromady sto")

# Radek "Ostatni" hlasi, kolik kombinaci se schovalo.
skrytych = karta[-1].get("combinations")
check(skrytych == len(DROBNE), f"souhrnný řádek hlásí {skrytych} skrytých kombinací")

print()
print("--- stránka ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402

accounts.create("spravce", "dlouheheslo", is_admin=True)
with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/languages")
    check(stranka.status_code == 200, "Jazyky se načtou")
    html = stranka.text

    # Radek "Ostatni" je tlacitko, ne mrtvy text.
    check('data-okno="okno-kombinace"' in html, "„Ostatní“ je tlačítko do okna")
    check('id="okno-kombinace"' in html, "a okno na stránce opravdu je")

    # V okne musi byt i ty drobne kombinace - kvuli nim se otevira.
    okno = html.split('id="okno-kombinace"', 1)[1]
    chybi = [j for j in DROBNE
             if langstats.languages.combination_label(j) not in okno]
    check(not chybi, f"v okně jsou i drobné kombinace (chybí: {chybi})")

    # A soucet, aby bylo videt, z ceho se procenta pocitaji.
    check("</tfoot>" in okno, "okno má souhrnný řádek")

    # Past: kdyby se okno naplnilo tymiz radky jako karta, vsechny testy
    # vyse by presto prosly. Karta ty drobne kombinace mit NESMI - jinak
    # se nic neschovava a okno neni k cemu.
    karta_html = html.split('id="okno-kombinace"', 1)[0]
    navic = [j for j in DROBNE
             if langstats.languages.combination_label(j) in karta_html]
    check(not navic, f"a na kartě samotné nejsou (přebývá: {navic})")

print()
print("--- když není co skrývat, okno se nekreslí ---")
with db.connect() as conn:
    conn.execute("DELETE FROM items WHERE audio_languages NOT IN"
                 " ('cs,en', 'en', 'cs', 'cs,en,sk')")
    conn.commit()

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    html = client.get("/languages").text
    check(len(langstats.language_combinations()) == 4, "zbyly čtyři kombinace")
    check('id="okno-kombinace"' not in html, "a okno se nekreslí")
    check('data-okno="okno-kombinace"' not in html, "ani tlačítko do něj")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
