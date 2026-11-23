# -*- coding: utf-8 -*-
r"""The package jellyscope/music/ (music libraries from Jellyfin): its door, files and imports.

Guards the same rules as the other packages (seerr/, arr/<app>/, stats/):

(a) door: `jellyscope.music` offers the names of the SUBMODULES map and lists them all in
    `__all__`;
(b) every name at the door is the same object as in its submodule;
(c) no cycles: imports reach only files of the package, and `import jellyscope.music` works
    first in a clean process without loading another app's package;
(d) no file has more than 600 lines;
(e) the music error is ServiceError (translated like the errors of the other services).

Run:
    .venv/bin/python tests/test_music_package.py
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

PACKAGE = PROJECT / "jellyscope" / "music"

# Which name lives in which file. A new file of the package adds its line here.
SUBMODULES: dict[str, list[str]] = {
    "sync": ["NAME", "ALREADY_RUNNING", "MusicError", "has_music", "sync_state", "sync_running", "synchronize"],
}
FILES = list(SUBMODULES)
MAX_LINES = 600

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- (a) door ---")
package = importlib.import_module("jellyscope.music")
every_name = [name for names in SUBMODULES.values() for name in names]
check(not [name for name in every_name if not hasattr(package, name)], "the door offers every name of the map")
check(sorted(getattr(package, "__all__", [])) == sorted(every_name), "__all__ is exactly the names of the map")
check(package.NAME == "Music", "NAME (a translation key, English since the keys are English)")

print()
print("--- (b) names live in the files of the map ---")
elsewhere = [f"{module}.{name}" for module, names in SUBMODULES.items() for name in names
             if getattr(package, name, None) is not getattr(importlib.import_module(f"jellyscope.music.{module}"), name, object())]
check(not elsewhere, f"the door points at the same objects as the files ({elsewhere})")
extra = sorted(p.stem for p in PACKAGE.glob("*.py") if p.stem != "__init__" and p.stem not in FILES)
check(not extra, f"every file of the package is in the map ({extra})")

print()
print("--- (c) no cycles ---")
forbidden = []
for module in FILES:
    for node in ast.walk(ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module not in FILES:
            forbidden.append(f"{module}: from .{node.module or ''} import")
        if isinstance(node, ast.ImportFrom) and node.level == 2 and node.module and node.module.split(".")[0] in ("arr", "seerr", "web"):
            forbidden.append(f"{module}: from ..{node.module} import")
check(not forbidden, f"imports reach only the package's files and shared modules, not other apps or web ({forbidden})")
code = ("import sys, jellyscope.music; "
        "print(sorted(m for m in sys.modules if m.startswith(('jellyscope.arr', 'jellyscope.seerr', 'jellyscope.web'))))")
run = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=os.environ.copy(), capture_output=True, text=True, timeout=180)
output = run.stdout.strip() or (run.stderr.strip().splitlines() or ["?"])[-1]
check(run.returncode == 0 and output == "[]",
      f"`import jellyscope.music` first in a clean process, without other apps or web ({output[:120]})")

print()
print("--- (d) file length ---")
long_files = {p.name: n for p in sorted(PACKAGE.glob("*.py")) if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES}
check(not long_files, f"every file of the package has at most {MAX_LINES} lines ({long_files})")

print()
print("--- (e) shared core ---")
services = importlib.import_module("jellyscope.services")
check(issubclass(package.MusicError, services.ServiceError), "MusicError derives from ServiceError")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
