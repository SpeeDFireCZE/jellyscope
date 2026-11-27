# -*- coding: utf-8 -*-
r"""The package jellyscope/arr/prowlarr/: a structure that must not drift apart.

Every connection has its own package with the same split (client, settings, sync, live state).
Guarded:

(a) the door: `jellyscope.arr.prowlarr` offers the names of SUBMODULES and its `__all__` has them all;
(b) every name at the door is the same object as in its submodule;
(c) no cycles: dependencies between the submodules go one way, those inside functions too, and
    the package imports first in a clean process;
(d) no file has more than 600 lines;
(e) Prowlarr uses the shared core from services.py;
(f) "already running" is the very template of services.py - the registry compares it by identity;
(g) the demo's made-up Prowlarr (demo.py) lives in the package but not at the door: the registry imports it only in
    the demo mode (registry._demo), so `import jellyscope.arr.prowlarr` never loads it.

Run:
    .venv/bin/python tests/test_prowlarr_package.py
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

PACKAGE = PROJECT / "jellyscope" / "arr" / "prowlarr"

# Which name lives in which file. A new file of the package adds its line here and the test then
# guards its names too.
SUBMODULES: dict[str, list[str]] = {
    "client": ["NAME", "KINDS", "ProwlarrError", "ProwlarrClient"],
    "settings": ["stored_settings", "is_connected", "save_settings", "check_connection", "watch_settings", "save_watch_settings"],
    "sync": ["event_from_history", "synchronize", "sync_running", "sync_state", "event_count",
             "delete_data", "delete_downloaded_data"],
    "live": ["live_state", "take_sample"],
    "samples": ["delete_old_samples", "sample_interval", "status_marker", "how_measured"],
    "watch": ["watch_indexers_forever"],
}
# Files of the package whose names are not at the door: the demo's made-up Prowlarr, imported only by the registry
# (its handler) and the demo's seed (demo_apps: the samples of the past).
NOT_AT_THE_DOOR: dict[str, list[str]] = {
    "demo": ["handler", "down_at", "outages", "past_states", "fill_past"],
}
FILES = [*SUBMODULES, *NOT_AT_THE_DOOR]
MAX_LINES = 600

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


print("--- (a) the door ---")
try:
    package = importlib.import_module("jellyscope.arr.prowlarr")
except ImportError as error:
    package = None
    check(False, f"the package jellyscope.arr.prowlarr imports ({error})")

every_name = [name for names in SUBMODULES.values() for name in names]
if package is not None:
    missing = [name for name in every_name if not hasattr(package, name)]
    check(not missing, f"the door offers every name of the map ({missing})")
    check(sorted(getattr(package, "__all__", [])) == sorted(every_name),
          "__all__ is exactly the names of the map (nothing more, nothing missing)")
    check(getattr(package, "NAME", None) == "Prowlarr", "NAME: the name the app calls itself")
    check(issubclass(getattr(package, "ProwlarrError", object), RuntimeError), "ProwlarrError is a RuntimeError")

print()
print("--- (b) names live in the files of the map ---")
if package is not None:
    elsewhere = []
    for file, names in SUBMODULES.items():
        try:
            module = importlib.import_module(f"jellyscope.arr.prowlarr.{file}")
        except ImportError:
            elsewhere.append(f"{file}.py does not import")
            continue
        elsewhere += [f"{file}.{name}" for name in names if getattr(package, name, None) is not getattr(module, name, object())]
    check(not elsewhere, f"the door points at the same objects as the files ({elsewhere})")

print()
print("--- (c) no cycles ---")
if PACKAGE.is_dir():
    edges: dict[str, set[str]] = {m: set() for m in FILES}
    forbidden = []
    for m in FILES:
        if not (PACKAGE / f"{m}.py").is_file():
            forbidden.append(f"{m}.py is missing")
            continue
        # ast.walk goes into imports inside functions too - they make a cycle as well, it only shows
        # in a rare branch at run time.
        for node in ast.walk(tree(PACKAGE / f"{m}.py")):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module is None:
                    forbidden += [f"{m}: from . import {a.name}" for a in node.names if a.name not in FILES]
                    edges[m] |= {a.name for a in node.names if a.name in FILES}
                elif node.module in FILES:
                    edges[m].add(node.module)
                else:
                    forbidden.append(f"{m}: from .{node.module} import")
    check(not forbidden, f"imports in the package point only at its files ({forbidden})")
    left = {m: set(e) for m, e in edges.items()}
    while any(not e for e in left.values()):
        done = {m for m, e in left.items() if not e}
        left = {m: e - done for m, e in left.items() if m not in done}
    check(not left, f"dependencies without a cycle ({sorted(left)})")
    check(not edges["client"], "client touches nothing in the package (a leaf of the tree)")
    run = subprocess.run([sys.executable, "-c", "import jellyscope.arr.prowlarr"], cwd=PROJECT, env=os.environ.copy(),
                         capture_output=True, text=True, timeout=180)
    last = (run.stderr.strip().splitlines() or [""])[-1]
    check(run.returncode == 0, f"`import jellyscope.arr.prowlarr` first in a clean process ({last[:120]})")
else:
    check(False, "no cycles (the package prowlarr/ is missing)")

print()
print("--- (d) length of files ---")
long_files = {p.name: n for p in sorted(PACKAGE.glob("*.py"))
              if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES} if PACKAGE.is_dir() else {}
check(PACKAGE.is_dir() and not long_files, f"every file of the package has at most {MAX_LINES} lines ({long_files})")

print()
print("--- (e) the shared core ---")
try:
    services = importlib.import_module("jellyscope.services")
    check(package is not None and issubclass(getattr(package, "ProwlarrError", object), services.ServiceError),
          "ProwlarrError derives from ServiceError")
    check(package is not None and issubclass(getattr(package, "ProwlarrClient", object), services.HttpClient),
          "ProwlarrClient is an HttpClient")
    settings = importlib.import_module("jellyscope.arr.prowlarr.settings")
    check(settings.address is services.address, "prowlarr uses address() from services.py (one address check for all)")
except ImportError as error:
    check(False, f"jellyscope.services and jellyscope.arr.prowlarr.settings import ({error})")

print()
print("--- (f) already running ---")
try:
    sync = importlib.import_module("jellyscope.arr.prowlarr.sync")
    check(getattr(sync, "ALREADY_RUNNING", None) is services.SYNC_ALREADY_RUNNING,
          "sync.ALREADY_RUNNING is the template of services.py itself (the registry compares it by identity)")
except ImportError as error:
    check(False, f"jellyscope.arr.prowlarr.sync imports ({error})")

print()
print("--- (g) the made-up Prowlarr is not at the door ---")
door_source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
door_imports = {node.module for node in ast.walk(ast.parse(door_source)) if isinstance(node, ast.ImportFrom) and node.level == 1}
check(not door_imports & set(NOT_AT_THE_DOOR), f"the door imports none of {sorted(NOT_AT_THE_DOOR)} ({sorted(door_imports)})")
if package is not None:
    check(not [name for names in NOT_AT_THE_DOOR.values() for name in names if name in getattr(package, "__all__", [])],
          "their names are not in __all__")
for file, names in NOT_AT_THE_DOOR.items():
    try:
        module = importlib.import_module(f"jellyscope.arr.prowlarr.{file}")
    except ImportError as error:
        check(False, f"{file}.py imports ({error})")
        continue
    check(all(callable(getattr(module, name, None)) for name in names), f"{file}.py offers {names}")
run = subprocess.run([sys.executable, "-c", "import sys, jellyscope.arr.prowlarr; print('jellyscope.arr.prowlarr.demo' in sys.modules)"],
                     cwd=PROJECT, env=os.environ.copy(), capture_output=True, text=True, timeout=180)
check(run.returncode == 0 and run.stdout.strip() == "False",
      f"`import jellyscope.arr.prowlarr` does not load the made-up Prowlarr ({run.stdout.strip() or run.stderr.strip()[-120:]})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
