# -*- coding: utf-8 -*-
r"""The smooth curve in the charts must not draw values that did not happen.

Area charts interpolate the measured points with a curve. Were a free spline
used, it would "swing out" between two days: it would shoot above the highest
measured value and below the lowest. The chart would show a peak that never
was - and below zero even negative viewing.

`charts._path()` therefore uses a monotone cubic (Fritsch-Carlson). This test
checks that guarantee by really evaluating the curve: it takes the control
points from the drawn path and walks every stretch in steps.

Run:
    .venv/bin/python tests/test_curve.py
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
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def stretch_y(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """The value of a cubic Bezier curve at the time t."""
    u = 1 - t
    return (u ** 3 * p0 + 3 * u ** 2 * t * p1 + 3 * u * t ** 2 * p2 + t ** 3 * p3)


def curve_range(points: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """For every stretch returns (the lowest y on the curve, the highest y, the bound from the points)."""
    path = charts._path(points)
    numbers = [float(x) for x in re.findall(r"-?\d+\.?\d*", path)]
    # M x y, then by sixes C x1 y1 x2 y2 x y
    result = []
    zy = numbers[1]   # the start's y (M x y)
    i = 2
    while i + 5 < len(numbers) + 1 and i + 5 <= len(numbers):
        _, y1, _, y2, _, y = numbers[i:i + 6]
        samples = [stretch_y(zy, y1, y2, y, k / 40) for k in range(41)]
        result.append((min(samples), max(samples), zy))
        zy = y
        i += 6
    return result


print("--- the curve stays between the measured points ---")
# A sharp peak in the middle and a zero right next to it - exactly there a free
# spline overshoots. The coordinates are in pixels, so a smaller y = a higher value.
points = [(0.0, 200.0), (100.0, 40.0), (200.0, 200.0), (300.0, 120.0), (400.0, 200.0)]
stretches = curve_range(points)
check(len(stretches) == 4, f"the path has four stretches ({len(stretches)})")

overshoots = []
for index, (lowest, highest, _) in enumerate(stretches):
    a, b = points[index][1], points[index + 1][1]
    lower, upper = min(a, b), max(a, b)
    if lowest < lower - 0.01 or highest > upper + 0.01:
        overshoots.append((index, round(lowest, 1), round(highest, 1), lower, upper))
check(not overshoots, f"no stretch climbs out of its two points ({overshoots})")

print()
print("--- zero stays zero ---")
# A day without watching must not drop below the base; otherwise the chart
# would paint negative viewing.
points = [(0.0, 40.0), (100.0, 200.0), (200.0, 40.0)]
lowest_point = max(u[1] for u in curve_range(points))
check(lowest_point <= 200.01, f"the curve does not get below the zero line ({lowest_point:.1f})")

print()
print("--- the chart really uses the curve ---")
data = [{"d": f"2026-08-{day:02d}", "a": value}
        for day, value in enumerate([1, 3, 0, 2, 1], start=1)]
svg = charts.area_chart_multi(data, "d", [{"key": "a", "label": "A", "slot": 1}])
check("<polyline" not in svg, "the line is no longer broken (no polyline)")
check(svg.count("C") >= 4, "the path is made of cubic arcs")

mini = charts.sparkline(data, "d", "a")
check("<polyline" not in mini and " C" in mini, "the same holds for the sparkline")

# One point has nothing to interpolate - it must not fail.
check(charts._path([(1.0, 2.0)]) == "M1.0,2.0", "a single point passes unchanged")
check(charts._path([]) == "", "an empty series too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
