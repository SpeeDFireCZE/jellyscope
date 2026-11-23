# -*- coding: utf-8 -*-
r"""A button must not call a function that does not exist.

The bug this watches really happened and cost data: the button
"Zapomenout" called in its `onclick` a function that was not in the template yet.
The browser throws an error on such an `onclick`, **skips it** and does
the default action - the form was sent and the viewer's history disappeared without a single
question.

That is the treacherous part: a broken safeguard does not behave as broken. It behaves
as if it were not there, and it shows only on data that no longer exist.

The check is therefore static. Since the rebuild for CSP without inline code
(docs/superpowers/specs/2026-11-05-csp-skripty-design.md) the handling is carried by
data-… attributes: it goes through the templates and checks that every attribute used and every
action `data-action` has a handler in static/js.

Run:
    .venv/bin/python tests/test_button_handlers.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT / "jellyscope" / "templates"

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# Since the rebuild for CSP without inline code the elements' handling is declarative:
# the template writes a data-… attribute, a script in static/js knows what to do with it (see
# tests/test_no_inline_js.py - onclick= must not be there any more). The trap is
# the same as before: an attribute no script handles behaves as if it
# were not there. With data-confirm that means deleting without a single question.
#
# The attribute -> how the script recognises it (the attribute's name, or the key in dataset).
DECLARATIVE = {
    "data-confirm": "confirm",
    "data-on-error": "onError",
    "data-close-modal": "data-close-modal",
    "data-close-flash": "data-close-flash",
    "data-toggle-theme": "data-toggle-theme",
    "data-open-modal": "openModal",
    "data-submit-on-change": "data-submit-on-change",
    "data-file-name": "data-file-name",
}
SCRIPTS = "\n".join(s.read_text(encoding="utf-8")
                    for s in (PROJECT / "jellyscope" / "static" / "js").glob("*.js"))
templates = {s: s.read_text(encoding="utf-8") for s in TEMPLATES.rglob("*.html")}

used = set()
for attribute, mark in DECLARATIVE.items():
    where = sorted(s.relative_to(TEMPLATES).as_posix() for s, text in templates.items()
                   if re.search(rf"\s{re.escape(attribute)}[\s=>]", text))
    if not where:
        continue
    used.add(attribute)
    # The whole name, not a piece: "potvrdit" would be found in "potvrditX" too.
    pattern = (rf"dataset\.{mark}\b" if not mark.startswith("data-")
               else rf"[\"\[]{re.escape(mark)}[\"\]=]")
    check(bool(re.search(pattern, SCRIPTS)), f"{attribute} (in {', '.join(where[:3])}) has a handler in static/js")

# data-action="…" carries the action's name - the script must know every one.
for template, text in sorted(templates.items()):
    for action in sorted(set(re.findall(r'data-action="([^"]+)"', text))):
        used.add(f"data-action={action}")
        check(f'"{action}"' in SCRIPTS,
              f"{template.relative_to(TEMPLATES).as_posix()}: the action {action} has a handler in static/js")

# A trap: were the check to find nothing, its silence would mean nothing.
check(len(used) >= 5, f"and the check really found some attributes ({len(used)})")
check("data-confirm" in used, "the confirmation before an irreversible action included")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
