# -*- coding: utf-8 -*-
r"""Geometry for the detailed map: simplifying and cutting into tiles.

The detailed map (Natural Earth 1:10M) is too big for the whole page, so when
it is downloaded it is cut into tiles in an n × n grid over the world of
360 × 360 (Web Mercator). Guarded here:

* pieces lie in their tile and polygons are closed;
* a polygon that covers a whole tile (inland - no edge leads into the tile)
  gives a fill of the whole square, not emptiness;
* a line is cut into pieces that join up;
* simplifying does not fail on the recursion depth and an outline with
  100 000 points is cut in a reasonable time.

Run:
    .venv/bin/python tests/test_geometry.py
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import geometry  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def inside(piece, x, y, size) -> bool:
    e = 1e-9
    return all(x * size - e <= bx <= (x + 1) * size + e
               and y * size - e <= by <= (y + 1) * size + e for bx, by in piece)


def area(piece) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(piece, piece[1:] + piece[:1]))) / 2


print("--- polygons ---")
square = [(10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0), (10.0, 10.0)]
pieces = geometry.cut_polygon(square, 16)
check(set(pieces) == {(0, 0), (1, 0), (0, 1), (1, 1)}, f"a square across a tile edge ({sorted(pieces)})")
check(all(len(k) >= 3 and inside(k, x, y, 22.5) for (x, y), group in pieces.items() for k in group),
      "every piece is closed and lies in its tile")
check(abs(sum(area(k) for group in pieces.values() for k in group) - 400.0) < 1e-6,
      "the pieces together have the area of the whole square")

world = [(-10.0, -10.0), (370.0, -10.0), (370.0, 370.0), (-10.0, 370.0)]
whole = geometry.cut_polygon(world, 16)
tile = whole.get((5, 5), [])
check(len(whole) == 256 and len(tile) == 1 and abs(area(tile[0]) - 22.5 ** 2) < 1e-6,
      "a polygon over the whole world fills every tile whole (inland)")

print("--- lines ---")
line = geometry.cut_line([(0.0, 5.0), (50.0, 5.0)], 16)
check(set(line) == {(0, 0), (1, 0), (2, 0)}, f"a line across three tiles ({sorted(line)})")
ends = [(line[(i, 0)][0][0][0], line[(i, 0)][0][-1][0]) for i in range(3)] if len(line) == 3 else []
check(ends == [(0.0, 22.5), (22.5, 45.0), (45.0, 50.0)], f"the pieces join up ({ends})")
zigzag = geometry.cut_line([(10.0, 5.0), (30.0, 5.0), (30.0, 6.0), (10.0, 6.0)], 16)
check(len(zigzag.get((0, 0), [])) == 2 and len(zigzag.get((1, 0), [])) == 1,
      "a line that leaves a tile and comes back gives two pieces in it")

print("--- simplifying ---")
rng = random.Random(3)
noisy_line = [(i / 10, rng.uniform(-0.001, 0.001)) for i in range(101)]
check(geometry.simplify(noisy_line, 0.02) == [noisy_line[0], noisy_line[-1]],
      "a straight line with noise below the tolerance = only the end points")
check(geometry.simplify([(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)], 0.02) == [(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)],
      "a bend above the tolerance stays")
long_line = [(i * 0.001, math.sin(i * 0.01)) for i in range(200_000)]
try:
    result = geometry.simplify(long_line, 0.0001)
    check(result[0] == long_line[0] and result[-1] == long_line[-1] and len(result) < len(long_line),
          "200 000 points without failing on the recursion depth")
except RecursionError:
    check(False, "200 000 points without failing on the recursion depth")

print("--- speed ---")
circle = [(180 + 100 * math.cos(i * 2 * math.pi / 100_000), 180 + 100 * math.sin(i * 2 * math.pi / 100_000))
          for i in range(100_000)]
start = time.perf_counter()
cut = geometry.cut_polygon(circle, 128)
took = time.perf_counter() - start
check(took < 5 and len(cut) > 1000, f"an outline with 100 000 points into a 128 × 128 grid: {took:.2f} s")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
