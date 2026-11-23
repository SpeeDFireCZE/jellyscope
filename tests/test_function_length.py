r"""Function length - no function of the app is to be longer than a smaller module.

Rebuild 3f took the four longest functions (Settings, the collector, the file
analysis, the Jellystat import) apart into named steps: the main function reads
like a table of contents, the details are a bit higher up, each under its own name. The test watches
that this does not quietly come back:

(a) no function or method in jellyscope/ above LIMIT lines, except the exceptions
    with a reason written down;
(b) every exception exists and still exceeds the limit - otherwise it belongs off
    the list (the list may only shrink);
(c) the four main functions taken apart have at most MAIN_LIMIT lines;
(d) the comments that were in those functions are still in their file -
    none got lost when taking them apart (importers/jellystat.py left the
    watch with the translation into English in step 4, scanner/tech_scan.py in step 5
    and collector.py in step 8 - the comments are translated);
(e) checking the check: on an artificially long function the limit really bites.

Spec: docs/superpowers/specs/2026-11-06-slozite-funkce-design.md

Run:
    .venv/bin/python tests/test_function_length.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# The test only reads sources, but let nobody run it against the live database.
_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "delka.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

PACKAGE = PROJECT / "jellyscope"
LIMIT = 150
MAIN_LIMIT = 90

# The key: the path under jellyscope/ and the dotted name (class.method, outer.nested).
EXCEPTIONS = {
    "demodata.py:seed": "the generator of sample data - made-up traffic, not the app's logic",
    "charts.py:area_chart_multi": "drawing SVG layer by layer; taking it apart is outside rebuild 3f",
    "stats/titles.py:_merge_titles": "putting titles together; taking it apart is outside rebuild 3f",
}
MAIN = {
    "web/settings/page.py:settings_page",
    "collector.py:_store_sessions",
    "scanner/tech_scan.py:run_tech_scan",
    "importers/jellystat.py:import_jellystat_json",
}

# The comments from the function bodies before taking them apart (commit 011ca3b) watched four files:
# importers/jellystat.py, scanner/tech_scan.py, collector.py and the Settings page. Since steps 4, 5, 8 and
# 10a of the English step all of them are translated, so the table is empty.
COMMENTS: dict[str, list[str]] = {}

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def lengths(source: str) -> dict[str, int]:
    """The dotted name of every function (methods and nested ones too) → the number of lines."""
    result: dict[str, int] = {}

    def walk(node: ast.AST, path: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = path + (child.name,)
                if not isinstance(child, ast.ClassDef):
                    result[".".join(name)] = child.end_lineno - child.lineno + 1
                walk(child, name)
            else:
                walk(child, path)

    walk(ast.parse(source), ())
    return result


everything: dict[str, int] = {}
for path in sorted(PACKAGE.rglob("*.py")):
    rel = path.relative_to(PACKAGE).as_posix()
    for name, lines in lengths(path.read_text(encoding="utf-8")).items():
        everything[f"{rel}:{name}"] = lines

print("--- (a) no function above the limit ---")
check(len(everything) > 500, f"the functions were found ({len(everything)})")
for key, lines in sorted(everything.items()):
    if lines > LIMIT and key not in EXCEPTIONS:
        check(False, f"{key} has {lines} lines (limit {LIMIT}) - take it apart into steps")
check(all(r <= LIMIT for k, r in everything.items() if k not in EXCEPTIONS),
      f"apart from the exceptions every function has at most {LIMIT} lines")

print()
print("--- (b) the exceptions hold ---")
for key, reason in EXCEPTIONS.items():
    check(bool(reason.strip()), f"{key}: the exception has a reason")
    if key not in everything:
        check(False, f"{key}: the exception points at a function that does not exist - delete it from EXCEPTIONS")
    else:
        check(everything[key] > LIMIT,
              f"{key}: still exceeds the limit ({everything[key]}), otherwise delete it from EXCEPTIONS")

print()
print("--- (c) the main functions taken apart are short ---")
for key in sorted(MAIN):
    lines = everything.get(key)
    check(lines is not None and lines <= MAIN_LIMIT,
          f"{key} has at most {MAIN_LIMIT} lines ({lines})")

print()
print("--- (d) the comments did not get lost ---")
for file, original in COMMENTS.items():
    now = Counter(r.strip() for r in (PACKAGE / file).read_text(encoding="utf-8").splitlines()
                  if r.strip().startswith("#"))
    missing = sorted((Counter(original) - now).elements())
    check(not missing, f"{file}: all {len(original)} comments are in place (missing {missing[:3]})")

print()
print("--- (e) checking the check ---")
long_one = "def long_one():\n" + "    x = 1\n" * LIMIT
exact = "def exact():\n" + "    x = 1\n" * (LIMIT - 1)
check(lengths(long_one) == {"long_one": LIMIT + 1}, "a function a line longer is recognised")
check(lengths(exact)["exact"] <= LIMIT, "a function exactly at the limit passes")
check(lengths("class A:\n    def m(self):\n        def v():\n            pass\n")
      == {"A.m": 3, "A.m.v": 2}, "methods and nested functions have a dotted name")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
