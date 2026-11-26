# -*- coding: utf-8 -*-
r"""The package jellyscope/seerr/ (requests from Seerr): its door, files and dependencies.

The package holds everything that talks to Seerr: the API v1 client, the settings, the sync of
requests, the live "pending" card and the provisional posters. The shared core of the client
(errors without the key, the address check) is in jellyscope/services.py, and the arr/ package stands
on it too - guarded by (e).

The same rules as for the other packages (scanner/, stats/, importers/, music/):

(a) the door: `jellyscope.seerr` offers the names of SUBMODULES and its `__all__` has them all -
    whoever writes `from jellyscope import seerr` need not know which file a name lives in;
(b) every name at the door is the same object as in its submodule (a copy would drift apart
    when a test substitutes it or it changes at run time);
(c) no cycles: dependencies between the submodules go one way, those inside functions too, and
    the package imports first in a clean process;
(d) no file has more than 600 lines;
(e) Seerr and *Arr use the shared core from services.py;
(f) "already running" is the very template of services.py - tasks.py compares it by identity.

Run:
    .venv/bin/python tests/test_seerr_package.py
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

PACKAGE = PROJECT / "jellyscope" / "seerr"

# Which name lives in which file. A new file of the package adds its line here and the test then
# guards its names too.
SUBMODULES: dict[str, list[str]] = {
    "client": ["NAME", "SeerrError", "SeerrClient"],
    "settings": ["stored_settings", "is_connected", "save_settings", "check_connection", "images_state", "settings_link"],
    "sync": ["normalize_id", "request_row", "synchronize", "sync_state", "delete_data", "delete_downloaded_data",
             "request_count", "sync_running"],
    # The pending requests' rows, the shared cache and titles - read by now.py, nothing at the door.
    "live": [],
    # The page of Seerr's newest requests for "Latest requests" (26. 11. 2026) - read by now.py.
    "latest": [],
    # Both live cards loaded at once under one entry of the cache.
    "now": ["pending", "latest_requests"],
    # Provisional posters of titles that are not in Jellyfin (13. 11. 2026).
    "posters": ["poster_path", "provisional_poster", "clean_up_posters", "forget_missing", "check_images"],
}
FILES = list(SUBMODULES)
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
    package = importlib.import_module("jellyscope.seerr")
except ImportError as error:
    package = None
    check(False, f"the package jellyscope.seerr imports ({error})")

every_name = [name for names in SUBMODULES.values() for name in names]
if package is not None:
    missing = [name for name in every_name if not hasattr(package, name)]
    check(not missing, f"the door offers every name of the map ({missing})")
    check(sorted(getattr(package, "__all__", [])) == sorted(every_name),
          "__all__ is exactly the names of the map (nothing more, nothing missing)")
    check(getattr(package, "NAME", None) == "Seerr", "NAME: the name the app calls itself")
    check(issubclass(getattr(package, "SeerrError", object), RuntimeError), "SeerrError is a RuntimeError")

print()
print("--- (b) names live in the files of the map ---")
if package is not None:
    elsewhere = []
    for file, names in SUBMODULES.items():
        try:
            module = importlib.import_module(f"jellyscope.seerr.{file}")
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
    run = subprocess.run([sys.executable, "-c", "import jellyscope.seerr"], cwd=PROJECT, env=os.environ.copy(),
                         capture_output=True, text=True, timeout=180)
    last = (run.stderr.strip().splitlines() or [""])[-1]
    check(run.returncode == 0, f"`import jellyscope.seerr` first in a clean process ({last[:120]})")
else:
    check(False, "no cycles (the package seerr/ is missing)")

print()
print("--- (d) length of files ---")
long_files = {p.name: n for p in sorted(PACKAGE.glob("*.py"))
              if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES} if PACKAGE.is_dir() else {}
check(PACKAGE.is_dir() and not long_files, f"every file of the package has at most {MAX_LINES} lines ({long_files})")

print()
print("--- (e) the shared core ---")
try:
    services = importlib.import_module("jellyscope.services")
    check(package is not None and issubclass(getattr(package, "SeerrError", object), services.ServiceError),
          "SeerrError derives from ServiceError")
    check(package is not None and issubclass(getattr(package, "SeerrClient", object), services.HttpClient),
          "SeerrClient is an HttpClient")
    settings = importlib.import_module("jellyscope.seerr.settings")
    check(settings.address is services.address, "seerr uses address() from services.py (one address check for all)")
except ImportError as error:
    check(False, f"jellyscope.services and jellyscope.seerr.settings import ({error})")

print()
print("--- (f) already running ---")
try:
    sync = importlib.import_module("jellyscope.seerr.sync")
    check(getattr(sync, "ALREADY_RUNNING", None) is services.SYNC_ALREADY_RUNNING,
          "sync.ALREADY_RUNNING is the template of services.py itself (tasks.py compares it by identity)")
except ImportError as error:
    check(False, f"jellyscope.seerr.sync imports ({error})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
