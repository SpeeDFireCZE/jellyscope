# -*- coding: utf-8 -*-
r"""A pass on a phone - what can be guarded without a browser.

Measured in headless Chrome at a width of 390 px (iPhone). No page overflowed
sideways, but four flaws were found, and this test guards that they do not
come back:

* **Text in a chart had 4.6 px.** The SVG has a fixed viewBox of 760 units and
  stretches to the width, so the text shrinks with it - on a phone the scale
  came out 0.42. So the axis label is bigger in viewBox units on a narrow screen.
* **In the Comparison the Difference column dropped out.** Four columns do not
  fit into 350 px and the horizontal scroll pushed off the screen exactly what
  a person looks at that page for. On a phone the table becomes a list of
  blocks and the column's name is carried by `data-label`.
* **The History had rows 120 px tall** and of a table 1007 px wide a third was
  visible in a 316px window. The side columns hide on a phone.
* **The menu items wrapped** onto two lines.

Run:
    .venv/bin/python tests/test_mobile.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

TEMPLATES = PROJECT / "jellyscope" / "templates"
STYLE = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def mobile_block(width: int) -> str:
    """The content of ALL `@media (max-width: <width>px)` blocks glued together.

    There are more blocks of the same width in the file - the rules stand by
    what they concern, not in one heap at the end. Were only the first taken,
    the test would report a missing rule that is a few hundred lines further down.
    """
    pieces = []
    wanted = f"@media (max-width: {width}px)"
    start = 0
    while True:
        begin = STYLE.find(wanted, start)
        if begin < 0:
            break
        depth, i = 0, STYLE.index("{", begin)
        for end in range(i, len(STYLE)):
            if STYLE[end] == "{":
                depth += 1
            elif STYLE[end] == "}":
                depth -= 1
                if depth == 0:
                    pieces.append(STYLE[i:end])
                    start = end
                    break
        else:
            break
    return "\n".join(pieces)


print("--- the axis labels in a chart ---")
# The base size applies on a desktop, where the SVG stretches OVER 760 units
# and the text grows by it instead.
base_size = re.search(r"\.axis-label\s*\{[^}]*font-size:\s*([\d.]+)px", STYLE)
check(base_size is not None, "the label's base size is in the style")

mobile = re.search(r"\.axis-label\s*\{[^}]*font-size:\s*([\d.]+)px",
                   mobile_block(860))
check(mobile is not None, "and it grows on a narrow screen")

if base_size and mobile:
    base_px, mobile_px = float(base_size.group(1)), float(mobile.group(1))
    # The scale on a 390px display came out 0.42, so for the label to get to at
    # least 9 real pixels, it must have over 21 units.
    check(mobile_px * 0.42 >= 9,
          f"and comes out at a readable {mobile_px * 0.42:.1f} px (was {base_px * 0.42:.1f})")
    check(mobile_px > base_px, f"the mobile value is bigger ({mobile_px} vs {base_px})")

# The chart's left margin must be enough for that bigger label, otherwise it is cut.
from jellyscope import charts  # noqa: E402

svg = charts.area_chart_multi(
    [{"day": f"2026-01-{d:02d}", "a": 10000.0 * d} for d in range(1, 8)],
    "den", [{"key": "a", "label": "A"}])
labels_x = [float(x) for x in re.findall(r'<text x="([\d.]+)"[^>]*class="axis-label"', svg)]
check(bool(labels_x), f"the chart has axis labels ({len(labels_x)})")
if labels_x and mobile:
    # The leftmost label is aligned right to its coordinate, so the text is
    # drawn to the left of it. On a phone a character is roughly half the
    # font size wide.
    leftmost = min(labels_x)
    longest = max(len(t) for t in re.findall(
        r'class="axis-label">([^<]*)</text>', svg))
    needed = longest * float(mobile.group(1)) * 0.5
    check(leftmost >= needed,
          f"and they fit into the left margin ({leftmost:.0f} units,"
          f" ~{needed:.0f} needed for {longest} characters)")

print()
print("--- Comparison: blocks on a phone, not a table ---")
# The table is drawn by the comparison's macros (_compare.html, shared by Movies & series and Music since 22. 11. 2026).
compare = (TEMPLATES / "_compare.html").read_text(encoding="utf-8")
# Only the cells inside the comparison table - the rankings below it are
# ordinary tables of two columns and fit on a phone.
table = compare[compare.index('<table class="compare">'):
                compare.index("</table>")]
value_cells = re.findall(r'<td class="num[^"]*"([^>]*)>', table)
check(bool(value_cells), f"the template has value cells ({len(value_cells)})")
without_label = [b for b in value_cells if "data-label" not in b]
check(not without_label,
      f"each carries data-label for the phone (without: {len(without_label)})")

mobile_compare = mobile_block(780)
check("table.compare td { display: block" in mobile_compare.replace("\n", " ")
      or "table.compare td { display: block;" in mobile_compare
      or re.search(r"table\.compare[^}]*display:\s*block", mobile_compare) is not None,
      "and the style makes blocks of the table on a phone")
check("data-label" in mobile_compare,
      "the column's label is written out from data-label on a phone")

# A trap: the first column's width is a desktop rule with the SAME
# specificity. When it stands in the file after the media query, it overrides
# it and on a phone the column stays 34 % wide - exactly that happened.
width_at = STYLE.find("table.compare th:first-child")
media_at = STYLE.find("@media (max-width: 780px)")
check(0 <= width_at < media_at,
      "the desktop width of the column stands before the mobile block, not after it")

print()
print("--- History: the side columns hide on a phone ---")
history = (TEMPLATES / "history.html").read_text(encoding="utf-8")
header = history[history.index("<thead>"):history.index("</thead>")]
# From the table's body to the end of its loop of rows: the first {% endfor %} after <tbody> - the filter above the
# table has loops of its own.
body_at = history.index("<tbody>")
body = history[body_at:history.index("{% endfor %}", body_at)]
# Counted on tags, not by occurrences of the string - otherwise the comment
# that explains it would get into the sum.
th_extra = len(re.findall(r'<th[^>]*class="[^"]*extra-column', header))
td_extra = len(re.findall(r'<td[^>]*class="[^"]*extra-column', body))
check(th_extra > 0, f"the header marks the side columns ({th_extra})")
check(th_extra == td_extra,
      f"and a row has the same number of them ({th_extra} vs {td_extra})")
# Were they not to match, the table would come apart on a phone: the header of
# one column and the cell of another would hide.
# `count("<th")` would count `<thead>` itself too.
all_columns = len(re.findall(r"<th[ >]", header))
check(all_columns - th_extra == 4,
      f"four columns of {all_columns} stay ({all_columns - th_extra})")
check(".extra-column { display: none; }" in mobile_block(720),
      "and the style hides them on a phone")

print()
print("--- the settings' signpost is a list on a phone ---")
settings = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
mobile_860 = mobile_block(860)

# Eleven tabs wrapped into six lines at 390 px: 207 px of links before a
# person sees the first setting.
check('class="tabs-select"' in settings, "the template has a drop-down list")
check(".tabs-select + .tabs { display: none; }" in mobile_860,
      "and it replaces the tabs on a phone")

# A trap: a plain `.tabs { display: none }` would hide the tabs on the library
# detail too, where there is no list - and there would be no way to switch.
check("    .tabs { display: none; }" not in mobile_860,
      "only those the list replaces hide")
detail = (TEMPLATES / "library_detail.html").read_text(encoding="utf-8")
check('class="tabs"' in detail and 'class="tabs-select"' not in detail,
      "the library detail has tabs without a list, so it depends on that")

# Both are drawn from one list - two hand-written ones would drift apart and a
# new section would be missing in one.
# Loops are counted, not occurrences of the string - otherwise a comment would get in.
loops = len(re.findall(r"for [^%]*in section_menu", settings))
check(loops == 2,
      f"the tabs and the list take the items from the same source ({loops} loops)")
check('href="/settings?section=jellyfin"' not in settings,
      "and they are not written out one by one")

# It must be possible to switch without JavaScript too - a person must not get
# stuck in the settings because of a script.
check("<noscript>" in settings.split('class="tabs-select"')[1][:600],
      "without a script a button stays")

print()
print("--- the menu is under a burger on a phone ---")
base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
mobile_menu = mobile_block(860)

# Ten items do not fit side by side: the list was 794 px wide in a 390px
# window, so two were visible and it scrolled sideways.
check('class="menu-toggle"' in base and 'class="menu-button"' in base,
      "the template has a toggle and a button")
check("menu-toggle:checked ~ .nav" in mobile_menu,
      "and it opens purely by the style")

# Without JavaScript: it is a checkbox with a label, not a <button> with a
# handler. Were it rewritten to a button, the menu would not open at all with
# the script off - and it is the only way to the other pages.
toggle = re.search(r'<input[^>]*class="menu-toggle"[^>]*>', base)
check(toggle is not None and 'type="checkbox"' in toggle.group(0),
      "the toggle is a checkbox, so the menu works without a script too")
check('for="menu-toggle"' in base, "and the label aims at it")

# The button carries the name of the open page. Were the list and that label
# put together each apart, in time the button would report another page than
# the open one - so both are drawn from one list.
# Since the menu by sections (16. 11. 2026) the one list is jellyscope/navigation.menu():
# base.html draws the entries and the burger title from the same `menu_entries`.
check("{% set menu_entries = menu()" in base and "menu_title(menu_entries)" in base,
      "the menu items and the button's label come from one list (navigation.menu)")
check(base.count('<a href="/video/insights"') == 0
      and 'href="{{ entry.url }}"' in base and 'href="{{ menu_page.url }}"' in base,
      "and the links are drawn from it by a loop, not one by one")

# A trap: `_` is the translation function and `{% set _ = ... %}` overwrites it
# with None, so the next `_("...")` fails. Exactly that happened here.
check("{% set _ = " not in base,
      "no `set _` that would override the translation function")

check(".nav a { white-space: nowrap" in mobile_menu,
      "the menu items keep on one line")
# `min(150px, 100%)`, not a bare 150: on a 320px display the column asked for
# those 150 even where less was available, and the card climbed out of the screen.
check("minmax(min(150px, 100%), 1fr)" in mobile_menu,
      "and two tiles fit side by side without overflowing")
check("minmax(min(340px, 100%), 1fr)" in STYLE,
      "the card grid does not ask for more than it has either")

print()
print("--- the selection in the Log fits the display ---")
# An option carries the file name, a label and the size
# ("jellyscope.log.3 – záloha (1.2 MB)") and the select is as wide as the
# longest of them. On a phone it thus climbed out of the screen: a label in a flex does not shrink
# below the width of its content. So on a narrow screen the label takes at most
# a whole line and the select fits into it - the option's text is shortened,
# in the open list it is whole.


def rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


label_rule = rule(mobile_860, ".log-controls label")
check("min-width: 0" in label_rule and "max-width: 100%" in label_rule,
      "the label fits into a line on a phone")
check("max-width: 100%" in rule(mobile_860, ".log-controls select"),
      "and the select in the label too")
check(".log-controls select { width: auto; }" in STYLE,
      "on a desktop the select stays as wide as the longest option")

print()
print("--- the filter examples in the API window ---")
# An address with a filter is long and without spaces ("path[starts_with]=/media/...").
# At 360 px it was cut and the label stuck right after it. It breaks anywhere
# and the label is on its own line.
example = rule(STYLE, ".api-examples code")
check("overflow-wrap: anywhere" in example and "display: block" in example,
      "the address in the example wraps and stands on its own line")
check("display: block" in rule(STYLE, ".api-examples .card-note"),
      "the example's label is under the address")
check("overflow-wrap: anywhere" in rule(STYLE, ".api-fields p"),
      "the list of fields at an address wraps")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
