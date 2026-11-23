# -*- coding: utf-8 -*-
r"""The buttons in Settings leave the page where it is.

Actions in Settings are done in the middle of a long page and each of them
loads it again. Without help it would start from the top every time, so every
save was followed by looking for the place where a person was.

`data-keep-scroll` (the handler is in `static/js/app.js`) remembers the position on the click
and brings it back after loading. This test watches that the attribute is on
**every** button after which the Settings page loads again - otherwise
one would be forgotten at the next addition and would behave differently
from the others.

The dialogs' closing buttons (`method="dialog"`) do not belong on the list: they load
nothing, so the attribute would be useless to them.

That it really works is checked in a browser through CDP - not by this
test. This one only watches that the attribute is missing nowhere.

Run:
    .venv/bin/python tests/test_page_position.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


SECTIONS = PROJECT / "jellyscope" / "templates" / "settings"
# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
BASE = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
        + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))

print("--- the handler is on the page ---")
check("[data-keep-scroll]" in BASE, "the scripts listen to data-keep-scroll")
check("sessionStorage" in BASE and "scrollTo" in BASE,
      "it stores the position and brings it back after loading")
check("10000" in BASE, "and throws it away after ten seconds (in case the page "
                       "did not load again)")

print()
print("--- even when a script loads the page, not a person ---")
# A task run by hand, a background task finishing, closing the dialog
# with the layout, a drag over a chart: everywhere a script loads the page, so
# a click to remember the position is not enough.
by_script = [m.start() for m in re.finditer(
    r"window\.location\.(reload\(\)|replace\(|href = )", BASE)]
without_memory = [i for i in by_script
                  if "rememberPosition" not in BASE[max(0, i - 400):i]]
check(len(by_script) >= 4, f"the script loads the page in {len(by_script)} places")
check(not without_memory,
      f"and all of them remember the position ({len(without_memory)} without)")

print()
print("--- every button after which Settings load again ---")
missing: list[str] = []
dialogs = 0
for template in sorted(SECTIONS.glob("*.html")):
    text = template.read_text(encoding="utf-8")
    forms = {}
    for match in re.finditer(r"<form[^>]*>", text):
        form_id = re.search(r'id="([^"]+)"', match.group(0))
        action = re.search(r'action="([^"]+)"', match.group(0))
        if form_id:
            forms[form_id.group(1)] = action.group(1) if action else ""

    for match in re.finditer(r"<button[^>]*>", text):
        tag = match.group(0)
        if 'type="submit"' not in tag:
            continue
        form_ref = re.search(r'form="([^"]+)"', tag)
        if form_ref:
            action = forms.get(form_ref.group(1), "")
        else:
            before = text[:match.start()]
            start = before.rfind("<form")
            head = text[start:text.find(">", start) + 1]
            action_m = re.search(r'action="([^"]+)"', head)
            method = re.search(r'method="([^"]+)"', head)
            if method and method.group(1) == "dialog":
                dialogs += 1
                continue
            action = action_m.group(1) if action_m else ""
        if not action.startswith("/settings"):
            continue
        if "data-keep-scroll" not in tag:
            line = text[:match.start()].count("\n") + 1
            missing.append(f"{template.name}:{line} -> {action}")

check(not missing, f"all have data-keep-scroll ({len(missing)} without: {missing[:3]})")
check(dialogs >= 4,
      f"the dialogs' closing buttons do not count ({dialogs} skipped)")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
