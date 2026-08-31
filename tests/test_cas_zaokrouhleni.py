# -*- coding: utf-8 -*-
r"""Zaokrouhlovani casu v grafech - prepinac v Nastaveni -> Rozhrani.

Popisky os utinaji nad deset hodin desetinna mista, protoze na ose je
"40" citelnejsi nez "39,6". U konkretni hodnoty v bubline to ale znamena
az pulhodinovy rozdil - a "39,6 h" stejne nikomu pulhodinu nerekne,
kdezto "39:38" ano.

Vychozi zustava zaokrouhleni; kdo chce presnost, prepne si to.

Spusteni:
    .\.venv\Scripts\python.exe tests\test_cas_zaokrouhleni.py
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
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "cas.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, charts, db, formatting  # noqa: E402

chyb = 0


def check(podminka: bool, popis: str, detail: str = "") -> None:
    global chyb
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis} {detail}")
    if not podminka:
        chyb += 1


def prepni(presne: bool) -> None:
    db.set_setting("ui_cas_presne", "1" if presne else "0")
    db.forget_settings()


db.init_db()
accounts.create("spravce", "dlouheheslo", is_admin=True)

print("--- převod hodin na hodiny a minuty ---")
check(formatting.hodiny_hhmm(12.4) == "12:24", "12,4 h je 12:24")
check(formatting.hodiny_hhmm(34.52) == "34:31", "34,52 h je 34:31",
      f"({formatting.hodiny_hhmm(34.52)})")
check(formatting.hodiny_hhmm(0.83) == "0:50", "necelá hodina taky")
check(formatting.hodiny_hhmm(40) == "40:00", "celá hodina má nuly")
check(formatting.hodiny_hhmm(0) == "0:00", "nula")
check(formatting.hodiny_hhmm("nesmysl") == "-", "nesmysl nespadne")

print()
print("--- výchozí stav je zaokrouhlení ---")
check(formatting.presny_cas() is False, "bez nastavení se zaokrouhluje")
check(charts._udaj(34.52, "h") == "35 h", "a 34,52 h se hlásí jako 35 h",
      f"({charts._udaj(34.52, 'h')})")

print()
print("--- po přepnutí se čte na minuty ---")
prepni(True)
check(formatting.presny_cas() is True, "nastavení platí")
check(charts._udaj(34.52, "h") == "34:31", "hodnota je přesná",
      f"({charts._udaj(34.52, 'h')})")

# Prepinac se tyka casu, ne vseho ostatniho: Mbit/s ani pocty prehrani
# se na hodiny a minuty prevest nedaji.
check(charts._udaj(139.77, "Mbit/s") == "140 Mbit/s", "Mbit/s zůstává",
      f"({charts._udaj(139.77, 'Mbit/s')})")
check(charts._udaj(12.0, "") == "12", "číslo bez jednotky zůstává")

print()
print("--- v grafu to opravdu je ---")
body = [{"day": "2026-08-30", "movie_hours": 34.52},
        {"day": "2026-08-31", "movie_hours": 12.4}]
serie = [{"key": "movie_hours", "label": "Filmy"}]
prepni(True)
presny = charts.area_chart_multi(body, "day", serie)
check("34:31" in presny, "bublina ukazuje 34:31")
check("35 h" not in presny, "a už ne 35 h")

prepni(False)
kulaty = charts.area_chart_multi(body, "day", serie)
check("35 h" in kulaty, "po vypnutí zase 35 h")
check("34:31" not in kulaty, "a ne 34:31")

# Osa je meritko, ne udaj - tam zaokrouhleni zustava vzdycky.
prepni(True)
osa = re.findall(r'class="axis-label">([^<]+)<', presny)
check(all(":" not in p for p in osa), "popisky os zůstávají bez dvojtečky",
      f"({', '.join(osa[:6])})")

print()
print("--- pruhy pod grafem taky ---")
prepni(True)
pruhy = charts.hbar_chart([{"label": "Tereza", "value": 34.52}],
                                 "label", "value", unit="h")
check("34:31" in pruhy, "hodnota u pruhu je přesná")
prepni(False)
check("35 h" in charts.hbar_chart([{"label": "Tereza", "value": 34.52}],
                                 "label", "value", unit="h"),
      "a po vypnutí zaokrouhlená")

print()
print("--- nastavení jde přepnout ze stránky ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "spravce", "password": "dlouheheslo"},
                follow_redirects=False)
    stranka = client.get("/settings?section=interface").text
    check('name="ui_cas_presne"' in stranka, "přepínač je v Rozhraní")
    check(stranka.count('name="ui_cas_presne"') == 2, "má obě volby")
    # Prepinac se tyka jen zapisu - at je z nastaveni videt, ze data
    # a vypocty zustavaji stejne, at si clovek vybere cokoliv.
    check("no calculation" in stranka or "žádná data ani výpočty" in stranka,
          "a je u něj řečeno, že výpočty se nemění")

    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "click", "ui_skin": "novy",
                      "ui_cas_presne": "1"}, follow_redirects=False)
    db.forget_settings()
    check(formatting.presny_cas() is True, "uložení zapne přesný čas")

    po = client.get("/settings?section=interface").text
    check('value="1"\n                           checked' in po
          or re.search(r'name="ui_cas_presne" value="1"\s+checked', po) is not None,
          "a formulář si to pamatuje")

    client.post("/settings/interface",
                data={"ui_max_streams": "10", "ui_max_viewers": "10",
                      "ui_map_zoom": "click", "ui_skin": "novy",
                      "ui_cas_presne": "0"}, follow_redirects=False)
    db.forget_settings()
    check(formatting.presny_cas() is False, "a zase vypne")

print()
print(f"HOTOVO - chyb: {chyb}")
sys.exit(1 if chyb else 0)
