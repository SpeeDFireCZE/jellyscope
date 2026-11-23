# -*- coding: utf-8 -*-
r"""The contrast of colours in all four looks.

Colours are not written directly in the application, they are variables in
`style.css` (--text-muted, --series-1, ...) and every theme overrides them.
Contrast is badly estimated by eye: the grey helper text had 3.5 : 1 in the
light theme and 4.3 : 1 in the dark one, while ordinary text needs at least
4.5 : 1 (WCAG AA). White percentages on the turquoise bar had 1.9 : 1.

This test computes the contrast from the values in the CSS, so a change of the
palette cannot quietly break it:

* text (primary, secondary, helper) on the page's background and both surfaces,
* text on every colour that can be a background of text (the bars' parts,
  the red button) - through the --ink-* variables,
* that the bar really takes its text from --ink-* and the delete button too,
* text smaller than 12 px only in the charts' labels.

The themes are composed the way the browser composes them: dark overrides
light, the classic look overrides the new one and classic dark goes last.

Run:
    .venv/bin/python tests/test_contrast.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "kontrast.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import charts  # noqa: E402

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


CSS = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")


def block(selector: str, start: int = 0) -> dict[str, str]:
    """The variables of one block `selector { ... }`."""
    i = CSS.index(selector + " {", start)
    end = CSS.index("}", i)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", CSS[i:end]))


BASE = block(":root")
DARK = block(':root[data-theme="dark"]')
CLASSIC = block(':root[data-skin="classic"]')
CLASSIC_DARK = block(':root[data-skin="classic"][data-theme="dark"]')
DARK_BY_SYSTEM = block(':root:not([data-theme="light"])')
CLASSIC_BY_SYSTEM = block(':root[data-skin="classic"]:not([data-theme="light"])')

THEMES = {
    "new light": [BASE],
    "new dark": [BASE, DARK],
    "classic light": [BASE, CLASSIC],
    "classic dark": [BASE, DARK, CLASSIC, CLASSIC_DARK],
}


def compose(blocks: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for b in blocks:
        result.update(b)
    return result


def value_of(variables: dict[str, str], name: str) -> str:
    v = variables[name].strip()
    m = re.match(r"var\((--[\w-]+)\)$", v)
    return value_of(variables, m.group(1)) if m else v


def luminance(colour: str) -> float:
    h = colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


print("--- the dark theme by the system = the dark theme switched by hand ---")
# The dark values are in the CSS twice: once for "by the system", once for
# switching by hand. When only one copy is fixed, half of the people see the
# old colour.
check(DARK_BY_SYSTEM == DARK, "the new look: both dark copies match",
      str(sorted(set(DARK_BY_SYSTEM.items()) ^ set(DARK.items())))[:120])
check(CLASSIC_BY_SYSTEM == CLASSIC_DARK, "the classic look: both dark copies match",
      str(sorted(set(CLASSIC_BY_SYSTEM.items()) ^ set(CLASSIC_DARK.items())))[:120])

print()
print("--- text on a background: at least 4.5 : 1 ---")
for theme, blocks in THEMES.items():
    p = compose(blocks)
    for text in ("--text-primary", "--text-secondary", "--text-muted"):
        worst = min((contrast(value_of(p, text), value_of(p, background)), background)
                    for background in ("--page", "--surface-1", "--surface-2"))
        check(worst[0] >= 4.5, f"{theme}: {text}",
              f"{worst[0]:.2f} : 1 on {worst[1]}")

print()
print("--- text on a coloured background: at least 4.5 : 1 ---")
# The split bar's parts carry percentages, the red button carries "Smazat".
# Every colour that can be a background of text has its own --ink-*.
# The application's colour has had its ink since ever: --accent-ink (the Save
# button, a selected chip). A second token for the same would drift apart in time.
BACKGROUNDS = [f"--series-{i}" for i in range(1, 9)] + [f"--line-{i}" for i in range(1, 4)] + [
    "--good", "--accent", "--warning", "--serious", "--critical", "--text-muted"]


def ink_for(background: str) -> str:
    return "--accent-ink" if background == "--accent" else "--ink-" + background[2:]


for theme, blocks in THEMES.items():
    p = compose(blocks)
    bad = []
    for background in BACKGROUNDS:
        ink_name = ink_for(background)
        if ink_name not in p:
            bad.append(f"{ink_name} missing")
            continue
        ratio = contrast(value_of(p, background), value_of(p, ink_name))
        if ratio < 4.5:
            bad.append(f"{background} {ratio:.2f}")
    check(not bad, f"{theme}: text on all colours", str(bad))

print()
print("--- the bar and the button really take their text from --ink-* ---")
bar = charts.stacked_bar([
    {"label": "a", "value": 5},
    {"label": "b", "value": 4, "role": "good"},
    {"label": "c", "value": 3, "color": "var(--series-7)"},
    {"label": "d", "value": 2, "role": "info"},
])
parts = re.findall(r'<div class="stack-seg" style="([^"]*)"', bar)
check(len(parts) == 4, f"the bar has four parts ({len(parts)})")
for style in parts:
    background = re.search(r"background: var\((--[\w-]+)\)", style)
    ink_name = re.search(r"color: var\((--[\w-]+)(?:, #[0-9a-f]+)?\)", style)
    check(bool(background and ink_name and ink_for(background.group(1)) == ink_name.group(1)),
          "a part has the text for its colour", style[:90])
check("color: #fff" not in CSS[CSS.index(".stack-seg span"):CSS.index(".stack-seg span") + 200],
      "the percentages in the bar do not have hard-coded white")

button = CSS[CSS.index(".btn.danger {"):CSS.index("}", CSS.index(".btn.danger {"))]
check("var(--ink-critical)" in button, "the red button takes its text from --ink-critical")

# Hard-coded white on a theme colour. The selected day in the calendar (white
# on --series-1) had 2.2 : 1 in the dark theme - the token check above did not
# see it, because white is no token.
hard_coded = []
for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
    body = rule.group(2)
    background = re.search(r"background(?:-color)?:\s*var\((--[\w-]+)\)", body)
    if (background and background.group(1) in BACKGROUNDS
            and re.search(r"(?<![-\w])color:\s*(#fff\b|#ffffff|white)", body)):
        hard_coded.append(" ".join(rule.group(1).split()).split("*/")[-1].strip())
check(not hard_coded, f"no hard-coded white on a theme colour ({hard_coded})")

print()
print("--- text below 12 px only in the charts' labels ---")
# The labels of axes, the heat map and the calendar are part of a chart and
# have their own scale. Elsewhere (badges, tile labels, posters) 12 px is the minimum.
CHART_SELECTORS = {".axis-label", ".heatmap-hours span", ".heatmap-day", ".donut-unit"}
small = []
for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
    selector = " ".join(rule.group(1).split()).split("*/")[-1].strip()
    size = re.search(r"font-size:\s*([\d.]+)px", rule.group(2))
    if size and float(size.group(1)) < 12 and selector not in CHART_SELECTORS:
        small.append(f"{selector} {size.group(1)}px")
check(not small, f"no tiny text outside charts ({len(small)}: {small[:4]})")

print()
print("--- the accents of the seasonal decoration ---")
# A holiday overrides --accent, --accent-2 and --accent-ink. The accent is a
# text colour too (links, active elements), so it must be readable on the
# background, and the text on a button in the accent must be readable on it.
for key in ("christmas", "halloween", "newyear", "easter"):
    path = PROJECT / "jellyscope" / "static" / "events" / f"{key}.css"
    css = path.read_text(encoding="utf-8") if path.exists() else ""

    def holiday_block(selector: str) -> dict[str, str]:
        i = css.find(selector + " {")
        if i < 0:
            return {}
        return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", css[i:css.index("}", i)]))

    light = holiday_block(f':root[data-event="{key}"]')
    dark = holiday_block(f':root[data-event="{key}"][data-theme="dark"]')
    by_system = holiday_block(f':root[data-event="{key}"]:not([data-theme="light"])')
    check(bool(light) and dark == by_system and set(dark) == set(light),
          f"{key}: a light block and two identical dark copies with the same variables",
          str(sorted(set(dark.items()) ^ set(by_system.items())))[:100])
    # A holiday may change only the application's colour and the colours of its
    # effects - not the data colour (--data-color*: the "info" state, one-coloured
    # charts, the water on the map).
    foreign = sorted(k for k in set(light) | set(dark)
                     if k not in ("--accent", "--accent-2", "--accent-ink")
                     and not k.startswith("--event-"))
    check(not foreign, f"{key}: changes only the application's colour and its effects ({foreign})")
    for theme, blocks in THEMES.items():
        p = compose(blocks + ([light, dark] if "dark" in theme else [light]))
        try:
            ink = contrast(value_of(p, "--accent"), value_of(p, "--accent-ink"))
            # Buttons, chips and the logo in the menu are a gradient --accent-2 ->
            # --accent with --accent-ink text: it must be readable at both ends.
            ink2 = contrast(value_of(p, "--accent-2"), value_of(p, "--accent-ink"))
            text = min(contrast(value_of(p, "--accent"), value_of(p, background))
                       for background in ("--page", "--surface-1"))
        except (KeyError, ValueError):
            ink = ink2 = text = 0.0
        check(ink >= 4.5 and ink2 >= 4.5 and text >= 4.5, f"{key} / {theme}: the accent",
              f"text on the accent {ink:.2f}, on accent 2 {ink2:.2f}, the accent on the background {text:.2f}")

print()
print("--- the data colour = the application's colour (as long as it is no holiday) ---")
# The data colour is a copy of the application's colour that a holiday does not
# change. Outside a holiday it must be the same, otherwise the charts and states
# would change even without the decoration.
for theme, blocks in THEMES.items():
    p = compose(blocks)
    try:
        same = all(value_of(p, data_var) == value_of(p, app_var) for data_var, app_var in (
            ("--data-color", "--accent"), ("--data-color-2", "--accent-2"),
            ("--ink-data-color", "--accent-ink")))
    except KeyError as missing:
        same, missing_text = False, str(missing)
    else:
        missing_text = ""
    check(same, f"{theme}: --data-color* = --accent*", missing_text)

print()
print("--- the keyboard: a visible focus ---")
check(re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)", CSS) is not None,
      "links and buttons have one focus frame in the application's colour")
# A link in a bar chart's label sits in .hbar-label with overflow: hidden.
# A frame drawn outwards would be cut and the focus would not be visible.
check(re.search(r"\.hbar-link:focus-visible\s*\{[^}]*outline-offset:\s*-", CSS) is not None,
      "in a chart's label the frame is drawn inwards, so it is not cut")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
