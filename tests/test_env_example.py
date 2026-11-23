# -*- coding: utf-8 -*-
r"""What the application reads from the environment is described in .env.example.

A variable written about nowhere is a variable nobody finds - except by
reading the source. And they pile up quietly: writing os.environ.get(...) is
enough, and the documentation goes stale without anybody deleting anything.

So the test compares both and guards the opposite direction too:
JELLYSCOPE_DOCKER is only described in .env.example, never set. Were somebody
to add an active line there, everyone who copies the file would thereby turn
off updating from the browser on an ordinary machine.

Run:
    .venv/bin/python tests/test_env_example.py
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


# The source is read, not the running application: a variable can be read in
# code that never runs in a normal run (and exactly that one would be forgotten).
READING = re.compile(
    r"""os\.environ\.get\(\s*["']([A-Z_]+)["']"""
    r"""|os\.getenv\(\s*["']([A-Z_]+)["']"""
    r"""|os\.environ\[\s*["']([A-Z_]+)["']\s*\]"""
    r"""|_flag\(\s*["']([A-Z_]+)["']"""
)

# Variables the operating system or Python sets by itself, which do not
# belong to the application's configuration.
FOREIGN = {"PATH", "PYTHONIOENCODING", "TEMP", "TMP", "USERPROFILE", "HOME"}

read: set[str] = set()
for path in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    for match in READING.finditer(path.read_text(encoding="utf-8")):
        name = next(g for g in match.groups() if g)
        if name not in FOREIGN:
            read.add(name)

example = (PROJECT / ".env.example").read_text(encoding="utf-8")

print("--- every variable is described in .env.example ---")
check(len(read) >= 10, f"variables found in the code ({len(read)})")
for name in sorted(read):
    check(re.search(rf"\b{name}\b", example) is not None, name)

print()
print("--- JELLYSCOPE_DOCKER is only described ---")
# An active line = it does not start with a hash. A commented-out description is fine.
active = [
    line for line in example.splitlines()
    if line.strip().startswith("JELLYSCOPE_DOCKER")
]
check(not active, f"no active line ({active})")
check("JELLYSCOPE_DOCKER" in example, "but it is mentioned there")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
