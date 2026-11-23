# -*- coding: utf-8 -*-
"""Tests must not touch the live database.

A trap found only after several days of writing tests: the stored choice of
database (`data/database.json`, written by the form in Settings) **overrides**
the variable `DATABASE_PATH`. A test that set up its own database was in fact
working with the live one - and since it found a finished schema and data
there, it looked as if it passed.

Especially sneaky: it shows only when `database.json` exists. As long as
nobody had created it, the isolation was fine, and nothing suggested it stood
on sand.

`JELLYSCOPE_HOME` solves it: it switches the whole "home" (that is `.env`, the
`data/` folder and the stored choice too) to a temporary folder. This test
guards that it really works and that no test forgot about it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- JELLYSCOPE_HOME switches the stored choice of database too ---")
home = Path(tempfile.mkdtemp())
(home / "data").mkdir()
# A stored choice goes into the home - were it not read, we would notice.
(home / "data" / "database.json").write_text(
    '{"kind": "sqlite", "path": "' + str(home / "chosen.db").replace("\\", "\\\\")
    + '"}', encoding="utf-8")

code = (
    "import sys, json;"
    "from jellyscope import config, db;"
    "print(json.dumps({"
    "'base': str(config.BASE_DIR),"
    "'db': db.database_config().path}))"
)
environment = dict(os.environ,
                   JELLYSCOPE_HOME=str(home),
                   DATABASE_PATH=str(home / "from-variable.db"),
                   SECRET_KEY="x", PYTHONIOENCODING="utf-8")
result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                        text=True, cwd=str(PROJECT), env=environment)
try:
    import json
    data = json.loads(result.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    data = {}
    print("       output:", result.stdout[-300:], result.stderr[-300:])

check(data.get("base") == str(home), f"BASE_DIR follows the variable: {data.get('base')}")
check(data.get("db", "").endswith("chosen.db"),
      f"and the choice is read from the home, not from the project: {data.get('db')}")
check(str(PROJECT) not in data.get("db", "x"),
      "the path to the database does not lead into the project")


print()
print("--- without a home the test would end in the live database ---")
# A check of itself: were it all the same, this test would make no sense.
environment_without = dict(os.environ, DATABASE_PATH=str(home / "from-variable.db"),
                           SECRET_KEY="x", PYTHONIOENCODING="utf-8")
environment_without.pop("JELLYSCOPE_HOME", None)
result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                        text=True, cwd=str(PROJECT), env=environment_without)
try:
    without = json.loads(result.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    without = {}
check(without.get("base") == str(PROJECT), f"BASE_DIR is the project then: {without.get('base')}")
if (PROJECT / "data" / "database.json").exists():
    check(str(PROJECT) in without.get("db", ""),
          "and the database is taken from the project - exactly the trap this is about")
else:
    print("SKIPPED  the project has no data/database.json right now")


print()
print("--- no test forgot the home ---")
missing = []
for path in sorted((PROJECT / "tests").glob("test_*.py")):
    text = path.read_text(encoding="utf-8")
    if 'os.environ["DATABASE_PATH"]' not in text:
        continue          # the test needs no database
    if "JELLYSCOPE_HOME" not in text:
        missing.append(path.name)
    else:
        # It has to be set BEFORE DATABASE_PATH and before importing the
        # application - the configuration is read on import and never changes after.
        home_at = text.index("JELLYSCOPE_HOME")
        imports = re.search(r"^from jellyscope", text, re.M)
        if imports and home_at > imports.start():
            missing.append(f"{path.name} (only after the import)")

check(not missing, f"every test has its own home; missing: {missing}")


print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
