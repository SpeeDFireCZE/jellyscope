# -*- coding: utf-8 -*-
r"""Zapomenutí diváka: ptá se, a pak smaže přesně to, co má.

Dvě věci, a jedna z nich už jednou stála data:

1. **Klik na tlačítko sám o sobě nic nesmaže.** Potvrzení bylo dřív
   v pojmenované funkci, která se jmenovala stejně jako `<select>` pod
   ní. Prvek s `id` je vlastnost `window` a funkci přebil - volání spadlo
   na „is not a function“, prohlížeč `onclick` přeskočil a udělal výchozí
   akci. Formulář se odeslal a historie zmizela bez jediné otázky.
   Rozbitá pojistka se nechovala jako rozbitá; chovala se, jako by tam
   nebyla.

   Proto je spouštěcí tlačítko `type="button"` a k formuláři se nehlásí:
   **odeslat neumí, ať se se skriptem stane cokoliv**. Otevřít okno je
   jediné, co dokáže; odeslat umí až potvrzení uvnitř něj. Tohle je ta
   podstatná část a test ji hlídá jako první.

2. **Smaže jen toho jednoho.** Ostatní diváci se nesmí hnout a účet
   samotný zůstává: je v Jellyfinu, odsud smazat nejde, a příští
   synchronizace by ho stejně vrátila.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_zapomenuti_divaka.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "zapomenuti.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"

from jellyscope import db, odklizeni  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


db.init_db()


def divak(user_id: str, jmeno: str, kolik: int, aktivni: int = 0) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, name, is_administrator, is_disabled,"
            " synced_at) VALUES (?,?,0,0,?)",
            (user_id, jmeno, db.utcnow()))
        for poradi in range(kolik):
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name,"
                " item_id, started_at, last_seen_at, watched_seconds,"
                " is_active) VALUES (?,?,?,?,?,?,?,?)",
                (f"{user_id}-{poradi}", user_id, jmeno, f"item-{poradi}",
                 "2026-01-01 10:00:00", "2026-01-01 10:20:00", 1200,
                 aktivni if poradi == 0 else 0))
        conn.commit()


divak("u-mirek", "Mirek", 5, aktivni=1)
divak("u-jana", "Jana", 3)

print("--- smaže se jen ten jeden ---")
vysledek = odklizeni.zapomen_uzivatele("u-mirek")
check(vysledek["smazano"] == 5, f"vrátil počet smazaných ({vysledek['smazano']})")
check(vysledek["jmeno"] == "Mirek", f"a jméno ({vysledek['jmeno']!r})")

zbylo = db.query_value(
    "SELECT COUNT(*) FROM playback WHERE user_id = ?", ("u-mirek",))
check(zbylo == 0, f"po Mirkovi nezbyl žádný záznam ({zbylo})")
check(db.query_value("SELECT COUNT(*) FROM playback WHERE user_id = ?",
                     ("u-jana",)) == 3, "Jana se nehnula")

# I zrovna bezici prehravani odejde - jinak by po zapomenutem divakovi
# zustal zaznam, ktery se za chvili zase zapise.
check(db.query_value("SELECT COUNT(*) FROM playback WHERE is_active = 1") == 0,
      "zmizelo i právě běžící přehrávání")

# Ucet zustava: je v Jellyfinu a pristi synchronizace by ho vratila.
check(db.query_value("SELECT COUNT(*) FROM users WHERE id = ?",
                     ("u-mirek",)) == 1, "účet v seznamu zůstal")

print()
print("--- kdo tam není, toho nejde zapomenout ---")
prazdno = odklizeni.zapomen_uzivatele("u-neexistuje")
check(prazdno["smazano"] == 0, "neznámý divák nic nesmaže")
prazdno = odklizeni.zapomen_uzivatele("")
check(prazdno["smazano"] == 0, "a prázdné id taky ne")
check(db.query_value("SELECT COUNT(*) FROM playback") == 3,
      "ostatním se přitom nic nestalo")

print()
print("--- ptá se ve vlastním okně, a klik sám o sobě nic nesmaže ---")
sablona = (PROJECT / "jellyscope" / "templates"
           / "settings.html").read_text(encoding="utf-8")

spoustec = re.search(
    r'<button class="btn danger"([^>]*?)onclick="(.*?)"\s*>\s*'
    r'\{\{ _\("Zapomenout"\) \}\}', sablona, re.S)
check(spoustec is not None, "spouštěcí tlačítko se našlo")
if spoustec:
    atributy, obsluha = spoustec.group(1), spoustec.group(2)
    # Tohle je ta podstatna pojistka: tlacitko, ktere neumi odeslat,
    # nemuze pri chybe skriptu nic smazat. Driv umelo - a smazalo.
    check('type="button"' in atributy,
          "je type=button, takže samo nic neodešle")
    check('form="zapomenut"' not in atributy,
          "a nehlásí se k formuláři")
    check("showModal()" in obsluha, "otevírá vlastní okno")

okno = re.search(r'<dialog id="okno-zapomenut".*?</dialog>', sablona, re.S)
check(okno is not None, "okno s dotazem je v šabloně")
if okno:
    text = okno.group(0)
    check('id="koho-zapomenout"' in text, "okno jmenuje konkrétního diváka")
    check('form="zapomenut"' in text, "potvrzení v okně odesílá formulář")
    check('method="dialog"' in text, "a jde ho zavřít bez následku")
    # Fokus po otevreni stoji na Zrusit - Enter ze zvyku nic nesmaze.
    check(text.index("autofocus") < text.index('form="zapomenut"'),
          "fokus stojí na Zrušit, ne na mazání")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
