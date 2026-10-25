# -*- coding: utf-8 -*-
r"""Geometrie pro podrobnou mapu: zjednodušení a řezání do dlaždic.

Podrobná mapa (Natural Earth 1:10M) je na celou stránku moc velká, a tak
se při stažení rozřeže na dlaždice v síti n × n přes svět 360 × 360
(Web Mercator). Hlídá se tu:

* kusy leží ve své dlaždici a plochy jsou uzavřené;
* plocha, která pokrývá celou dlaždici (vnitrozemí - žádná hrana do
  dlaždice nevede), dá výplň celého čtverce, ne prázdno;
* čára se rozřeže na navazující kusy;
* zjednodušení nespadne na hloubce rekurze a obrys se 100 000 body se
  rozřeže v rozumném čase.

Spuštění:
    .\.venv\Scripts\python.exe tests\test_geometrie.py
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import geometrie  # noqa: E402

failures = 0


def check(podminka: bool, popis: str) -> None:
    global failures
    print(f"{'OK    ' if podminka else 'CHYBA '} {popis}")
    if not podminka:
        failures += 1


def uvnitr(kus, x, y, velikost) -> bool:
    e = 1e-9
    return all(x * velikost - e <= bx <= (x + 1) * velikost + e
               and y * velikost - e <= by <= (y + 1) * velikost + e for bx, by in kus)


def plocha(kus) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(kus, kus[1:] + kus[:1]))) / 2


print("--- plochy ---")
ctverec = [(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0), (10.0, 10.0)]
kusy = geometrie.rozrez_plochu(ctverec, 16)
check(set(kusy) == {(0, 0), (1, 0), (0, 1), (1, 1)}, f"čtverec přes hranu dlaždic ({sorted(kusy)})")
check(all(len(k) >= 3 and uvnitr(k, x, y, 22.5) for (x, y), seznam in kusy.items() for k in seznam),
      "každý kus je uzavřený a leží ve své dlaždici")
check(abs(sum(plocha(k) for seznam in kusy.values() for k in seznam) - 400.0) < 1e-6,
      "kusy dohromady mají plochu celého čtverce")

svet = [(-10.0, -10.0), (370.0, -10.0), (370.0, 370.0), (-10.0, 370.0)]
cely = geometrie.rozrez_plochu(svet, 16)
dlazdice = cely.get((5, 5), [])
check(len(cely) == 256 and len(dlazdice) == 1 and abs(plocha(dlazdice[0]) - 22.5 ** 2) < 1e-6,
      "plocha přes celý svět vyplní každou dlaždici celou (vnitrozemí)")

print("--- čáry ---")
cara = geometrie.rozrez_caru([(0.0, 5.0), (50.0, 5.0)], 16)
check(set(cara) == {(0, 0), (1, 0), (2, 0)}, f"čára přes tři dlaždice ({sorted(cara)})")
konce = [(cara[(i, 0)][0][0][0], cara[(i, 0)][0][-1][0]) for i in range(3)] if len(cara) == 3 else []
check(konce == [(0.0, 22.5), (22.5, 45.0), (45.0, 50.0)], f"kusy na sebe navazují ({konce})")
cik = geometrie.rozrez_caru([(10.0, 5.0), (30.0, 5.0), (30.0, 6.0), (10.0, 6.0)], 16)
check(len(cik.get((0, 0), [])) == 2 and len(cik.get((1, 0), [])) == 1,
      "čára, která z dlaždice odejde a vrátí se, dá v ní dva kusy")

print("--- zjednodušení ---")
nahoda = random.Random(3)
sum_cara = [(i / 10, nahoda.uniform(-0.001, 0.001)) for i in range(101)]
check(geometrie.zjednodus(sum_cara, 0.02) == [sum_cara[0], sum_cara[-1]],
      "přímka se šumem pod tolerancí = jen krajní body")
check(geometrie.zjednodus([(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)], 0.02) == [(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)],
      "zlom nad tolerancí zůstane")
dlouha = [(i * 0.001, math.sin(i * 0.01)) for i in range(200_000)]
try:
    vysledek = geometrie.zjednodus(dlouha, 0.0001)
    check(vysledek[0] == dlouha[0] and vysledek[-1] == dlouha[-1] and len(vysledek) < len(dlouha),
          "200 000 bodů bez pádu na hloubce rekurze")
except RecursionError:
    check(False, "200 000 bodů bez pádu na hloubce rekurze")

print("--- rychlost ---")
kruh = [(180 + 100 * math.cos(i * 2 * math.pi / 100_000), 180 + 100 * math.sin(i * 2 * math.pi / 100_000))
        for i in range(100_000)]
start = time.perf_counter()
rozrezany = geometrie.rozrez_plochu(kruh, 128)
trvalo = time.perf_counter() - start
check(trvalo < 5 and len(rozrezany) > 1000, f"obrys se 100 000 body do sítě 128 × 128: {trvalo:.2f} s")

print()
print("HOTOVO - chyb:", failures)
sys.exit(1 if failures else 0)
