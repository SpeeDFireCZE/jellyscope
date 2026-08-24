# -*- coding: utf-8 -*-
r"""„Oproti předchozímu období" nesmí vyrábět nesmysly.

Skutečný případ: filtr „letos" ukázal **1 619 572 %**. Číslo bylo
spočítané správně - předchozí stejně dlouhé okno padlo do doby, kdy
Jellyscope ještě neběžel, takže se dnešek srovnával s několika vteřinami
historie. Jenže takové procento neříká nic o dnešku; říká jen „minule
tam skoro nic nebylo".

Dvě pojistky:

* Když předchozí okno začíná dřív, než sahá naše historie, srovnání se
  neukáže vůbec a místo něj se řekne proč. Tiše chybějící šipka vypadá
  jako chyba a člověk ji hledá u sebe.
* A i tam, kde data jsou, se nad tisíc procent přepne na násobek a nad
  stonásobek rovnou na slova.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_srovnani_obdobi.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "srovnani.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)
TED = datetime.now(timezone.utc).replace(tzinfo=None)


def prehrani(pred_dny: float, sekund: int, klic: str) -> None:
    kdy = TED - timedelta(days=pred_dny)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', 'Petr', 'film-1', 'Duna', 'Movie', ?, ?, ?, 0)""",
            (klic, kdy.strftime(db.TIME_FORMAT), kdy.strftime(db.TIME_FORMAT), sekund),
        )


print("--- bez dat se nesrovnává ---")
check(stats.prvni_zaznam() == "", "prázdná databáze nemá první záznam")
check(stats.lze_srovnat(30) is False, "a srovnávat není s čím")

# Historie začíná před 40 dny - přesně situace, kdy filtr sahá dál.
for den in range(0, 40, 4):
    prehrani(den, 3600, f"s{den}")

print()
print("--- srovnává se jen s obdobím, ze kterého data máme ---")
check(stats.lze_srovnat(7) is True, "týden proti týdnu ano")
check(stats.lze_srovnat(30) is False,
      "měsíc ne - předchozí měsíc sahá před začátek historie")
letos = stats.obdobi_od_do("2026-01-01", TED.date().isoformat())
check(stats.lze_srovnat(letos) is False, "a 'letos' už vůbec ne")

print()
print("--- předchozí okno navazuje, nepřekrývá se ---")
okno = stats.predchozi(7)
check(okno.do == stats._obdobi(7).od, "končí tam, kde zvolené začíná")
check(okno.dny == 7, "a je stejně dlouhé")
check(okno.relativni is False, "má pevný konec, není to 'posledních N dní'")

print()
print("--- na stránce ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402


def hero(html: str) -> str:
    m = re.search(r'class="delta[^"]*">(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)

    text = hero(client.get(f"/?od=2026-01-01&do={TED.date().isoformat()}").text)
    check("%" not in text, f"u 'letos' se procenta neukážou ({text})")
    check("historie začíná" in text, "místo nich je vysvětlení proč")

    # A hlavní číslo řekne, za jaké období vlastně je - u vlastního
    # rozmezí `day_labels` nemá co nabídnout.
    stranka = client.get(f"/?od=2026-01-01&do={TED.date().isoformat()}").text
    nadpis = re.search(r'<p class="tile-label">(.*?)</p>', stranka, re.S)
    check("2026" in re.sub(r"\s+", " ", nadpis.group(1)),
          f"nadpis ukazuje rozmezí ({re.sub(r'  +', ' ', nadpis.group(1)).strip()})")

    check("%" in hero(client.get("/?days=7").text) or "beze změny" in hero(
        client.get("/?days=7").text), "u týdne se srovnává normálně")

print()
print("--- obrovská čísla se nepíšou jako procenta ---")
# Tady jde o samotné formátování, ne o data - zkoušíme makro přímo.
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from jellyscope import formatting, i18n  # noqa: E402

prostredi = Environment(loader=FileSystemLoader(
    str(PROJECT / "jellyscope" / "templates")))
formatting.register(prostredi)
i18n.register(prostredi)
makra = prostredi.get_template("_macros.html").module


def zmena(hodnota: float) -> str:
    return re.sub(r"\s+", " ", str(makra.zmena(hodnota))).strip()


check(zmena(42.0) == "42 %", f"běžná změna zůstává v procentech ({zmena(42.0)})")
check(zmena(-42.0) == "42 %", "znaménko nese šipka, ne číslo")
check("×" in zmena(1500.0), f"nad tisíc procent se píše násobek ({zmena(1500.0)})")
check(zmena(1619572.0) == "minule skoro nic",
      f"a nad stonásobek rovnou slovy ({zmena(1619572.0)})")
# Přesně to číslo, které tohle celé odhalilo.

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
