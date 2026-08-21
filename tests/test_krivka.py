# -*- coding: utf-8 -*-
r"""Plynulá křivka v grafech nesmí kreslit hodnoty, které nenastaly.

Plošné grafy prokládají naměřené body křivkou. Kdyby se použil volný
spline, mezi dvěma dny by se "rozmáchl": vyletěl by nad nejvyšší
naměřenou hodnotu a pod nejnižší. Graf by ukazoval špičku, která nikdy
nebyla - a pod nulou dokonce zápornou sledovanost.

`charts._cesta()` proto používá monotonní kubiku (Fritsch-Carlson).
Tenhle test tu záruku ověřuje tak, že křivku skutečně vyčíslí: vezme
řídicí body z vykreslené cesty a projde každý úsek po krocích.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_krivka.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import charts  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'CHYBA '} {label}")
    if not condition:
        failures += 1


def usek_y(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Hodnota kubické Bézierovy křivky v čase t."""
    u = 1 - t
    return (u ** 3 * p0 + 3 * u ** 2 * t * p1 + 3 * u * t ** 2 * p2 + t ** 3 * p3)


def rozsah_krivky(body: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Pro každý úsek vrátí (nejnižší y na křivce, nejvyšší y, mez z bodů)."""
    cesta = charts._cesta(body)
    cisla = [float(x) for x in re.findall(r"-?\d+\.?\d*", cesta)]
    # M x y, pak po šesticích C x1 y1 x2 y2 x y
    vysledek = []
    zx, zy = cisla[0], cisla[1]
    i = 2
    while i + 5 < len(cisla) + 1 and i + 5 <= len(cisla):
        _, y1, _, y2, x, y = cisla[i:i + 6]
        vzorky = [usek_y(zy, y1, y2, y, k / 40) for k in range(41)]
        vysledek.append((min(vzorky), max(vzorky), zy))
        zx, zy = x, y
        i += 6
    return vysledek


print("--- křivka zůstává mezi naměřenými body ---")
# Ostrá špička uprostřed a nula hned vedle - právě tam volný spline
# přestřelí. Souřadnice jsou v pixelech, takže menší y = vyšší hodnota.
body = [(0.0, 200.0), (100.0, 40.0), (200.0, 200.0), (300.0, 120.0), (400.0, 200.0)]
useky = rozsah_krivky(body)
check(len(useky) == 4, f"cesta má čtyři úseky ({len(useky)})")

prestreleni = []
for index, (nej_niz, nej_vys, _) in enumerate(useky):
    a, b = body[index][1], body[index + 1][1]
    dolni, horni = min(a, b), max(a, b)
    if nej_niz < dolni - 0.01 or nej_vys > horni + 0.01:
        prestreleni.append((index, round(nej_niz, 1), round(nej_vys, 1), dolni, horni))
check(not prestreleni, f"žádný úsek nevyleze mimo své dva body ({prestreleni})")

print()
print("--- nula zůstane nulou ---")
# Den bez sledování se nesmí propadnout pod základnu; jinak by graf
# maloval zápornou sledovanost.
body = [(0.0, 40.0), (100.0, 200.0), (200.0, 40.0)]
nejniz = max(u[1] for u in rozsah_krivky(body))
check(nejniz <= 200.01, f"pod nulovou čáru se křivka nedostane ({nejniz:.1f})")

print()
print("--- graf křivku opravdu používá ---")
data = [{"d": f"2026-08-{den:02d}", "a": hodnota}
        for den, hodnota in enumerate([1, 3, 0, 2, 1], start=1)]
svg = charts.area_chart_multi(data, "d", [{"key": "a", "label": "A", "slot": 1}])
check("<polyline" not in svg, "čára už není lomená (žádný polyline)")
check(svg.count("C") >= 4, "cesta je složená z kubických oblouků")

mini = charts.sparkline(data, "d", "a")
check("<polyline" not in mini and " C" in mini, "totéž platí pro minigraf")

# Jeden bod nemá co proložit - nesmí to spadnout.
check(charts._cesta([(1.0, 2.0)]) == "M1.0,2.0", "jediný bod projde beze změny")
check(charts._cesta([]) == "", "prázdná řada taky")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
