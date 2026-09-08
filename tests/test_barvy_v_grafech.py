# -*- coding: utf-8 -*-
r"""Vykreslí graf barvami, které má - ne náhradní šedou.

Tenhle test existuje kvůli chybě, která se stala: barvy se začaly do
atributu pouštět jen po ověření (aby se z nich nemohl stát kód), jenže
ověřovač dostal místo barvy hotový `linear-gradient(...)`, který si graf
skládá sám. Přechod barvou není, tak neprošel - a **všechny pruhy ve
všech grafech zešedly**. Bezpečnostní testy mlčely, protože ty hlídají,
že z grafu nevyleze kód; že z něj vyleze i ta správná barva, nehlídal
nikdo.

Odsud plyne pravidlo, které tenhle soubor drží: ověřuje se barva **na
vstupu**, a co si z ní aplikace složí, se už neověřuje.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_barvy_v_grafech.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "barvy.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

from jellyscope import charts, db  # noqa: E402

db.init_db()

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def barvy_v(html: str) -> list[str]:
    """Všechno, co v HTML skončilo jako barva ve stylu nebo v atributu."""
    return (re.findall(r'background:\s*([^";]+)', html)
            + re.findall(r'stop-color="([^"]+)"', html)
            + re.findall(r'stroke="([^"]+)"', html)
            + re.findall(r'fill="([^"]+)"', html))


print("--- pruhy mají přechod, ne náhradní barvu ---")
KODEKY = [{"label": "H264", "value": 51, "slot": 1},
          {"label": "HEVC", "value": 20, "slot": 2},
          {"label": "MPEG4", "value": 7, "slot": 3}]

se_sloty = charts.hbar_chart(KODEKY, "label", "value")
vyplne = re.findall(r'background:\s*([^"]+)', se_sloty)
check(len(vyplne) == 3, f"tři pruhy, tři výplně ({len(vyplne)})")
for cislo, vypln in enumerate(vyplne, start=1):
    check(vypln.startswith("linear-gradient("),
          f"pruh {cislo} je přechod ({vypln[:28]})")
    check(f"var(--series-{cislo})" in vypln,
          f"pruh {cislo} nese barvu své série")
    check(charts.NAHRADNI_BARVA not in vypln,
          f"pruh {cislo} není náhradní šedá")

# Graf bez slotu je jednobarevny - prechod znacky, porad ne sed.
bez_slotu = charts.hbar_chart([{"label": "A", "value": 5},
                               {"label": "B", "value": 3}],
                              "label", "value")
for vypln in re.findall(r'background:\s*([^"]+)', bez_slotu):
    check(vypln == "linear-gradient(90deg, var(--accent-2), var(--accent))",
          f"jednobarevný graf má přechod značky ({vypln[:34]})")

print()
print("--- náhradní barva se neobjeví v žádném grafu ---")
# Bezna data, zadny utok. Kdykoliv se tu objevi nahradni barva, znamena
# to, ze overovac zahodil neco, co zahazovat nemel.
radky = [{"label": "Petr", "value": 83, "hours": 83, "item_count": 51,
          "id": "u1", "user_id": "u1", "percent": 40, "gb": 12,
          "code": "cs", "slot": 1},
         {"label": "Jana", "value": 80, "hours": 80, "item_count": 20,
          "id": "u2", "user_id": "u2", "percent": 35, "gb": 9,
          "code": "en", "slot": 2},
         {"label": "Tereza", "value": 68, "hours": 68, "item_count": 7,
          "id": "u3", "user_id": "u3", "percent": 25, "gb": 4,
          "code": "sk", "slot": 3}]
role = [{"label": "Direct play", "value": 54, "percent": 54, "role": "dobre"},
        {"label": "Transcodes", "value": 29, "percent": 29, "role": "spatne"},
        {"label": "Remux", "value": 17, "percent": 17, "role": "jinak"}]

grafy = {
    "hbar_chart": charts.hbar_chart(radky, "label", "value"),
    "hbar_chart s odkazem": charts.hbar_chart(
        radky, "label", "value", link_prefix="/users/", link_key="user_id"),
    "hbar_chart se žebříčkem": charts.hbar_chart(
        radky, "label", "value", poradi=True),
    "legend": charts.legend(radky),
    "legend s rolemi": charts.legend(role),
    "stacked_bar": charts.stacked_bar(radky),
    "stacked_bar s rolemi": charts.stacked_bar(role),
    "donut_chart": charts.donut_chart(radky),
    "heatmap": charts.heatmap([[h + d for h in range(24)] for d in range(7)]),
    "sparkline": charts.sparkline([{"day": "2026-09-01", "hours": 1},
                                   {"day": "2026-09-02", "hours": 2}]),
    "area_chart_multi": charts.area_chart_multi(
        [{"den": "2026-09-01", "gb": 1}, {"den": "2026-09-02", "gb": 2}],
        "den", [{"key": "gb", "label": "GB", "slot": 1}], vyber=True),
}

for jmeno, html in grafy.items():
    nasel = [b for b in barvy_v(html) if charts.NAHRADNI_BARVA in b]
    check(not nasel, f"{jmeno}: žádná náhradní barva {nasel[:2] if nasel else ''}")
    check(bool(barvy_v(html)), f"{jmeno}: nějaká barva v něm vůbec je")

print()
print("--- vlastní barva od volajícího projde beze změny ---")
# Barva, kterou zada aplikace, se ma pouzit - overovac je zavora proti
# nesmyslum, ne proti vlastnim barvam.
for barva in ("var(--accent)", "#0af", "#00aaff", "tomato", "rgb(1, 2, 3)",
              "hsl(210, 50%, 40%)", "var(--seq-450)"):
    vlastni = charts.legend([{"label": "X", "value": 1, "barva": barva}])
    check(barva in vlastni, f"{barva!r} se dostala do grafu")

print()
print("--- a závora pořád drží ---")
# Kdyby tenhle test kdykoliv zhasl, znamenalo by to, ze se opravou
# sedivych pruhu otevrela ta dira zpatky.
for nesmysl in ('" onmouseover="alert(1)', "</svg><script>x</script>",
                "url(javascript:1)", "expression(alert(1))"):
    check(charts._barva(nesmysl) == charts.NAHRADNI_BARVA,
          f"{nesmysl[:26]!r} se nahradí")
    spatny = charts.legend([{"label": "X", "value": 1, "barva": nesmysl}])
    check(nesmysl not in spatny, f"{nesmysl[:26]!r} se do grafu nedostane")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
