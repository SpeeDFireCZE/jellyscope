# -*- coding: utf-8 -*-
r"""Záložka Aktivita v knihovně počítá za zvolené období.

Dřív měla napevno 90 dní a výběr období z ostatních stránek nebrala
v potaz - přitom to nikde nebylo poznat, „za posledních 90 dnů" stálo
jen pod první dlaždicí. Člověk, který měl jinde zvolených 30 dní, četl
čísla za 90 a nevěděl to.

Teď bere totéž období jako Přehled nebo Uživatelé (i vlastní rozmezí,
i zapamatované z jiné stránky) a má vlastní přepínač, který zůstane na
záložce Aktivita. Poslední přehrávání jsou dál posledních 25 bez ohledu
na období.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_knihovna_obdobi.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obdobi.db")
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


def pred(dnu: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dnu)).strftime(db.TIME_FORMAT)


# Tri filmy, kazdy prehrany jednou - pred 3, 40 a 200 dny. Podle toho,
# kolik jich do obdobi spadne, se pozna, za jake obdobi stranka pocita.
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    for item_id, jmeno, dnu, kdo in (("novy", "Nedávný film", 3, "Jana"),
                                     ("stredni", "Loňský podzim", 40, "Petr"),
                                     ("stary", "Dávný film", 200, "Eva")):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, is_missing) VALUES (?, ?, 'Movie', 'filmy', 0)",
            (item_id, jmeno))
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
            " library_id, started_at, last_seen_at, watched_seconds, is_active)"
            " VALUES (?, ?, ?, ?, ?, 'Movie', 'filmy', ?, ?, 3600, 0)",
            (f"s-{item_id}", "u-" + kdo, kdo, item_id, jmeno, pred(dnu), pred(dnu)))
    conn.commit()

klient = TestClient(web.app)
klient.post("/login", data={"username": "spravce", "password": "dlouheheslo", "zpusob": "mistni"})


def aktivita(dotaz: str = "") -> str:
    return klient.get("/library/filmy?tab=activity" + dotaz).text


def spusteni(html: str) -> int:
    """Číslo v dlaždici „Spuštění"."""
    shoda = re.search(r"Spuštění</p>\s*<div class=\"tile-value\">\s*([\d\s ]+)", html)
    if not shoda:
        # Ať to nevypadá jako chyba v počítání: změnilo se HTML dlaždice.
        raise AssertionError("dlaždice Spuštění nenalezena - změnilo se makro tile?")
    return int(re.sub(r"\D", "", shoda.group(1)))


def graf(html: str, nadpis: str) -> str:
    """Kus stránky od nadpisu karty po konec karty."""
    zacatek = html.find(f"<h2>{nadpis}</h2>")
    return html[zacatek:html.find('<div class="card', zacatek + 10)] if zacatek >= 0 else ""


print("--- Aktivita počítá za zvolené období ---")
for dny, cekam in ((7, 1), (30, 1), (90, 2), (365, 3)):
    html = aktivita(f"&days={dny}")
    check(spusteni(html) == cekam, f"za {dny} dní spuštění {cekam} ({spusteni(html)})")

html = aktivita("&days=7")
nejsledovanejsi = graf(html, "Nejsledovanější")
check("Nedávný film" in nejsledovanejsi and "Loňský podzim" not in nejsledovanejsi,
      "graf Nejsledovanější jede podle stejného období jako dlaždice")
divaci = graf(html, "Diváci")
check("Jana" in divaci and "Petr" not in divaci, "graf Diváci taky")
check("Dávný film" in html.split("Poslední přehrávání", 1)[-1],
      "poslední přehrávání jsou bez ohledu na období (i 200 dní starý)")
# „90 dnů" je na stránce dál - jako tlačítko přepínače. Hlídá se věta
# o tom, za jaké období čísla jsou.
check("sledovalo za posledních 7 dnů" in html and "za posledních 90 dnů" not in html,
      "stránka říká, za jaké období čísla jsou - zvolených 7 dní, ne napevno 90")
# „za posledních" + „rok" by česky dalo „za posledních rok" - rok má
# vlastní větu. A „za 30 dnů" bez „posledních" jde číst i jako „za 30
# dní od teď".
check("sledovalo za poslední rok" in aktivita("&days=365"), "u roku věta zní „za poslední rok“")

print()
print("--- období se pamatuje napříč stránkami ---")
klient.get("/users?days=365")
check(spusteni(aktivita()) == 3, "rok zvolený na Uživatelích platí i v Aktivitě")
klient.get("/users?days=30")
check(spusteni(aktivita()) == 1, "a přepnutí zpět na 30 dní taky")

print()
print("--- vlastní rozmezí ---")
od = (date.today() - timedelta(days=45)).isoformat()
do = (date.today() - timedelta(days=35)).isoformat()
html = aktivita(f"&od={od}&do={do}")
check(spusteni(html) == 1 and "Loňský podzim" in graf(html, "Nejsledovanější"),
      f"rozmezí {od} až {do} chytí jen přehrání před 40 dny ({spusteni(html)})")
check("sledovalo ve vybraném období." in html,
      "u vlastního rozmezí věta neopakuje data - ta jsou na tlačítku přepínače")

print()
print("--- přepínač období zůstane na Aktivitě ---")
html = aktivita("&days=30")
odkazy = re.findall(r'href="([^"]*days=\d+[^"]*)"', html)
check(odkazy and all("tab=activity" in o for o in odkazy),
      f"odkazy přepínače vedou na záložku Aktivita ({odkazy[:2]})")
check('data-okno="okno-obdobi"' in html, "a je tam i volba vlastního období")
formular = html.split('class="obdobi-form"', 1)[-1].split("</form>", 1)[0]
check('name="tab" value="activity"' in formular,
      "formulář vlastního období vrací zpátky na Aktivitu")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
