# -*- coding: utf-8 -*-
r"""Draws a chart with the colours it has - not the fallback grey.

This test exists because of a bug that happened: colours started to be let
into an attribute only after a check (so no code could be made of them), but
the checker got a finished `linear-gradient(...)` instead of a colour, which
the chart puts together itself. A gradient is no colour, so it did not pass -
and **all bars in all charts turned grey**. The security tests were silent,
because they guard that no code comes out of a chart; that the right colour
comes out of it too, nobody guarded.

From that follows the rule this file holds: the colour is checked **at the
input**, and what the application puts together from it is not checked any more.

Run:
    .venv/bin/python tests/test_chart_colours.py
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
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def colours_in(html: str) -> list[str]:
    """Everything that ended in the HTML as a colour in a style or an attribute."""
    return (re.findall(r'background:\s*([^";]+)', html)
            + re.findall(r'stop-color="([^"]+)"', html)
            + re.findall(r'stroke="([^"]+)"', html)
            + re.findall(r'fill="([^"]+)"', html))


print("--- the bars have a gradient, not the fallback colour ---")
CODECS = [{"label": "H264", "value": 51, "slot": 1},
          {"label": "HEVC", "value": 20, "slot": 2},
          {"label": "MPEG4", "value": 7, "slot": 3}]

with_slots = charts.hbar_chart(CODECS, "label", "value")
fills = re.findall(r'background:\s*([^"]+)', with_slots)
check(len(fills) == 3, f"three bars, three fills ({len(fills)})")
for number, fill in enumerate(fills, start=1):
    check(fill.startswith("linear-gradient("),
          f"bar {number} is a gradient ({fill[:28]})")
    check(f"var(--series-{number})" in fill,
          f"bar {number} carries its series' colour")
    check(charts.FALLBACK_COLOUR not in fill,
          f"bar {number} is not the fallback grey")

# A chart without a slot is one-coloured - the brand's gradient, still not grey.
without_slots = charts.hbar_chart([{"label": "A", "value": 5},
                                   {"label": "B", "value": 3}],
                                  "label", "value")
# The data colour = the application's colour, which the seasonal decoration does not change (style.css).
for fill in re.findall(r'background:\s*([^"]+)', without_slots):
    check(fill == "linear-gradient(90deg, var(--data-color-2), var(--data-color))",
          f"a one-coloured chart has the brand's gradient ({fill[:34]})")

print()
print("--- the fallback colour appears in no chart ---")
# Ordinary data, no attack. Whenever the fallback colour appears here, it
# means the checker threw away something it was not to throw away.
rows = [{"label": "Petr", "value": 83, "hours": 83, "item_count": 51,
         "id": "u1", "user_id": "u1", "percent": 40, "gb": 12,
         "code": "cs", "slot": 1},
        {"label": "Jana", "value": 80, "hours": 80, "item_count": 20,
         "id": "u2", "user_id": "u2", "percent": 35, "gb": 9,
         "code": "en", "slot": 2},
        {"label": "Tereza", "value": 68, "hours": 68, "item_count": 7,
         "id": "u3", "user_id": "u3", "percent": 25, "gb": 4,
         "code": "sk", "slot": 3}]
roles = [{"label": "Direct play", "value": 54, "percent": 54, "role": "dobre"},
         {"label": "Transcodes", "value": 29, "percent": 29, "role": "spatne"},
         {"label": "Remux", "value": 17, "percent": 17, "role": "jinak"}]

charts_drawn = {
    "hbar_chart": charts.hbar_chart(rows, "label", "value"),
    "hbar_chart with a link": charts.hbar_chart(
        rows, "label", "value", link_prefix="/users/", link_key="user_id"),
    "hbar_chart as a ranking": charts.hbar_chart(
        rows, "label", "value", ranked=True),
    "legend": charts.legend(rows),
    "legend with roles": charts.legend(roles),
    "stacked_bar": charts.stacked_bar(rows),
    "stacked_bar with roles": charts.stacked_bar(roles),
    "donut_chart": charts.donut_chart(rows),
    "heatmap": charts.heatmap([[h + d for h in range(24)] for d in range(7)]),
    "sparkline": charts.sparkline([{"day": "2026-09-01", "hours": 1},
                                   {"day": "2026-09-02", "hours": 2}]),
    "area_chart_multi": charts.area_chart_multi(
        [{"day": "2026-09-01", "gb": 1}, {"day": "2026-09-02", "gb": 2}],
        "den", [{"key": "gb", "label": "GB", "slot": 1}], selectable=True),
}

for name, html in charts_drawn.items():
    found = [b for b in colours_in(html) if charts.FALLBACK_COLOUR in b]
    check(not found, f"{name}: no fallback colour {found[:2] if found else ''}")
    check(bool(colours_in(html)), f"{name}: there is some colour in it at all")

print()
print("--- the caller's own colour passes unchanged ---")
# A colour the application gives is to be used - the checker is a barrier
# against nonsense, not against own colours.
for colour in ("var(--accent)", "#0af", "#00aaff", "tomato", "rgb(1, 2, 3)",
               "hsl(210, 50%, 40%)", "var(--seq-450)"):
    own = charts.legend([{"label": "X", "value": 1, "color": colour}])
    check(colour in own, f"{colour!r} got into the chart")

print()
print("--- and the barrier still holds ---")
# Were this test ever to go out, it would mean that fixing the grey bars
# opened that hole back up.
for nonsense in ('" onmouseover="alert(1)', "</svg><script>x</script>",
                 "url(javascript:1)", "expression(alert(1))"):
    check(charts._colour(nonsense) == charts.FALLBACK_COLOUR,
          f"{nonsense[:26]!r} is replaced")
    bad = charts.legend([{"label": "X", "value": 1, "color": nonsense}])
    check(nonsense not in bad, f"{nonsense[:26]!r} does not get into the chart")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
