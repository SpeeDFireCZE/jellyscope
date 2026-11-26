# -*- coding: utf-8 -*-
r"""The package jellyscope/importers/: a structure that must not drift apart.

The history import (Playback Reporting, Jellystat), linking the imported records to items, the guards
against duplicates, the history clean-up, orphan records and the whole tidy live in the package
`jellyscope/importers/` with its door (`importers/__init__.py`), so nothing changes from outside. Guarded:

(a) the door has everything that is read from outside as `importers.X` or
    `from jellyscope.importers import X`, and its `__all__` is exactly the list of what it imports;
(b) every name lives in its submodule by the map SUBMODULES, the map covers every top-level name of the
    submodules, and the door points at the same objects;
(c) no cycles: dependencies one way by the graph MAY, no `from . import X` that would aim into the package,
    and the package as well as its partners in the import cycle (scanner, tasks) import first in a clean
    process;
(d) no submodule over 600 lines;
(e) tests substitute where it takes effect (tests/_package.py);
(f) the old importers.py is gone and no test reads it by path (the guard "we only read from Jellyfin" in
    test_readonly must see the whole import);
(h) the logger is called as before (`jellyscope.importers`).

Run:
    .venv/bin/python tests/test_importers_package.py
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "importers_package.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import (outside_sources, read_through_door, substitutes_in_submodules, unread_door_names,  # noqa: E402
                      wrong_substitutes)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


PACKAGE = PROJECT / "jellyscope" / "importers"
OLD_FILE = PROJECT / "jellyscope" / "importers.py"
# Files whose text holds examples like importers.X; this test also by its own name (a PostgreSQL run is a copy
# under another name, tests/_pg_<name>.py).
WITH_EXAMPLES = {Path(__file__).name, "test_importers_package.py", "test_stats_package.py", "test_web_package.py",
                 "test_scanner_package.py", "_package.py"}

# Which top-level name lives in which submodule. A new name of the package adds itself here.
SUBMODULES: dict[str, list[str]] = {
    "base": ["NOT_LISTENING_OR_READING", "ImportError_", "_parse_time", "_existing_keys", "FORBIDDEN_WORDS", "read_only", "_normalize_id",
             "_insert", "LANGUAGE_COLUMNS", "_language_from_source", "_build_row", "_EPISODE_TITLE", "split_episode_title"],
    "matching": ["_epoch_slow", "_epoch", "_playback_index", "_already_there", "_remember", "_known_users", "_known_items"],
    "creation": ["create_from_jellyfin", "_episodes_under_series", "_libraries_by_path", "_library_for",
                 "_episode_in_library"],
    "linking": ["_unify_ids", "refresh_playback_metadata", "_orphan_item_ids", "_relink",
                "_link_by_tmdb", "_orphan_records", "_link_by_name", "_link_by_episode_number",
                "link_imported_history"],
    "pbr": ["PBR_QUERY", "PBR_QUERY_WITHOUT_ROWID", "_body_without_key", "playback_reporting_available",
            "import_playback_reporting", "PBR_TSV_COLUMNS", "_tsv_columns", "import_playback_reporting_tsv",
            "_process_pbr"],
    "jellystat": ["_jellystat_item_type", "JELLYSTAT_TABLES", "_find_jellystat_rows", "_pick", "_by_source",
                  "_read_record", "_fix_redirected", "_row_from_record", "import_jellystat_json"],
    "cleanup": ["CAP_MULTIPLE", "cap_nonsense_durations", "duplicate_playback_groups", "merge_duplicate_playback",
                "duplicate_playback_count"],
    "duplicates": ["LENGTH_TOLERANCE_S", "MAX_GAP_S", "_title_for_comparison", "_import_exists",
                   "import_duplicate_groups", "_belong_together", "_completeness", "merge_import_duplicates",
                   "import_duplicate_count", "misplaced_episode_rows"],
    "orphans": ["orphan_playback_count", "stale_name_rows", "unify_names", "orphan_items_count", "LOOKUP_LIMIT",
                "look_up_orphans_in_jellyfin", "REASON_ID", "REASON_ONE_MATCH", "REASON_SEVERAL_MATCHES",
                "REASON_SERIES_EXISTS", "REASON_NO_SERIES", "REASON_KNOWS_SERIES", "REASON_NOTHING", "REASON_LABELS",
                "ANALYSIS_LIMIT", "analyse_orphans", "candidates_for_orphan", "assign_manually", "relink_orphans",
                "repair_episode_links"],
    "tidy": ["tidy_data", "import_summary"],
}
FILES = list(SUBMODULES)
# The dependency graph: who may reach whom.
MAY = {
    "base": set(), "matching": {"base"}, "creation": {"base"}, "linking": {"base", "creation"},
    "pbr": {"base", "matching", "linking"}, "jellystat": {"base", "matching", "linking"},
    "cleanup": {"base", "matching"}, "duplicates": {"base", "matching"},
    "orphans": {"base", "creation", "linking", "duplicates"}, "tidy": {"linking", "cleanup", "duplicates", "orphans"},
}


def defined(tree: ast.Module) -> set[str]:
    """Names defined at the top level (def, class, assignment)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def is_package() -> bool:
    return (PACKAGE / "__init__.py").is_file()


def loaded(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


importers = importlib.import_module("jellyscope.importers")

print("--- (f) the old file ---")
check(is_package() and not OLD_FILE.exists(), "importers.py is gone, the package importers/ is there")
BY_PATH = re.compile(r"""["']importers\.py["']""")
by_path = [f"{f.name}:{i}" for f in sorted(PROJECT.glob("tests/*.py"))
           if f.name not in WITH_EXAMPLES and f.name != "test_sync_to_prod.py"
           for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if BY_PATH.search(line)]
check(not by_path, f"no test reads importers.py by path ({by_path[:6]})")

print()
print("--- (a) the door has everything that is read from outside ---")
NAME = re.compile(r"(?<![\w.])importers\.([A-Za-z_]\w*)")
FROM = re.compile(r"from\s+jellyscope\.importers\s+import\s+\(?([^)\n]+)\)?")
used: dict[str, str] = {}
for path in sorted([*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
                    *(PROJECT / n for n in ("demo.py", "run.py", "manage.py"))]):
    if not path.is_file() or PACKAGE in path.parents or path == OLD_FILE or path.name in WITH_EXAMPLES:
        continue
    text = path.read_text(encoding="utf-8")
    for name in NAME.findall(text):
        used.setdefault(name, path.name)
    for group in FROM.findall(text):
        for piece in group.split("#")[0].split(","):
            if piece.strip():
                used.setdefault(piece.split(" as ")[0].strip(), path.name)
used.pop("py", None)
missing = sorted(f"{n} ({where})" for n, where in used.items() if n not in FILES and not hasattr(importers, n))
check(len(used) > 20 and not missing, f"all {len(used)} names go through the door (missing {missing[:6]})")
door_imports = {(a.asname or a.name) for node in tree(PACKAGE / "__init__.py").body
                if isinstance(node, ast.ImportFrom) and node.module != "__future__" for a in node.names}
all_names = set(getattr(importers, "__all__", []))
check(all_names == door_imports and len(all_names) == len(importers.__all__),
      f"__all__ is exactly what the door imports, each once (only in __all__ {sorted(all_names - door_imports)[:6]},"
      f" only imported {sorted(door_imports - all_names)[:6]})")
# The other way round (audit CL-10): a name the door takes from a submodule is read - in the door itself or from
# the outside. A re-export nobody reads only hides where the name lives, and the check above keeps it for ever.
# What is read through an alias or by a string (`substitute(importers, "X")`) counts too.
door_reads = read_through_door("jellyscope.importers", outside_sources(PROJECT, PACKAGE, WITH_EXAMPLES), PROJECT)
# Read only by a dev tool outside the repository (.superpowers/tools/compare_import.py, the comparison of step 4):
# kept in the door while that tool may still be run. A name gone from the door leaves the list.
READ_BY_DEV_TOOLS = {"duplicate_playback_groups"}
check(READ_BY_DEV_TOOLS <= set(importers.__all__), f"the names kept for the dev tool are in the door ({sorted(READ_BY_DEV_TOOLS)})")
re_exported, unread = unread_door_names(PACKAGE / "__init__.py", set(used) | door_reads | READ_BY_DEV_TOOLS)
check(len(re_exported) > 25 and not unread,
      f"every name the door takes from a submodule is read in the door or from the outside ({len(unread)} unread: {unread})")

print()
print("--- (b) every name lives in its submodule ---")
if is_package():
    defined_names = {m: defined(tree(PACKAGE / f"{m}.py")) - {"log"} for m in FILES if (PACKAGE / f"{m}.py").is_file()}
    on_map = sorted(n for names in SUBMODULES.values() for n in names)
    check(sorted(set(PACKAGE.glob("*.py")) - {PACKAGE / "__init__.py"}) == sorted(PACKAGE / f"{m}.py" for m in FILES),
          f"the package has exactly the submodules of the map ({sorted(p.name for p in PACKAGE.glob('*.py'))})")
    check(len(on_map) == len(set(on_map)) and set(on_map) == {n for names in defined_names.values() for n in names},
          f"the map covers every top-level name of the submodules, each once ({len(on_map)} names)")
    missing_in, elsewhere = [], []
    for m, names in SUBMODULES.items():
        there = defined_names.get(m, set())
        missing_in += [f"{m}.{n}" for n in names if n not in there]
        if (PACKAGE / f"{m}.py").is_file():
            module = importlib.import_module(f"jellyscope.importers.{m}")
            elsewhere += [f"{m}.{n}" for n in names if n in there and hasattr(importers, n)
                          and getattr(importers, n) is not getattr(module, n)]
    check(not missing_in, f"the names are in the submodules by the map ({missing_in[:6]})")
    check(not elsewhere, f"the door points at the same objects as the submodules ({elsewhere[:6]})")
    jellyfin = importlib.import_module("jellyscope.jellyfin")
    check(getattr(importers, "JellyfinClient", None) is jellyfin.JellyfinClient,
          "importers.JellyfinClient is the client from jellyfin.py (tests substitute it)")
else:
    check(False, "the names are in the submodules by the map (the package importers/ is missing)")

print()
print("--- (c) no cycles ---")
if is_package():
    edges: dict[str, set[str]] = {m: set() for m in FILES}
    forbidden = []
    for m in FILES:
        if not (PACKAGE / f"{m}.py").is_file():
            continue
        for node in ast.walk(tree(PACKAGE / f"{m}.py")):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module is None:
                    forbidden += [f"{m}: from . import {a.name}" for a in node.names if a.name not in FILES]
                    edges[m] |= {a.name for a in node.names if a.name in FILES}
                elif node.module in FILES:
                    edges[m].add(node.module)
                else:
                    forbidden.append(f"{m}: from .{node.module} import")
    check(not forbidden, f"no import in a submodule aims into the package by mistake ({forbidden[:6]})")
    extra = {m: sorted(edges[m] - MAY[m]) for m in FILES if edges[m] - MAY[m]}
    check(not extra, f"dependencies by the graph MAY (extra {extra})")
    # Importing a submodule always runs the package's __init__ first (and that loads every submodule) - one check
    # per package covers them all. The partners in the import cycle (scanner and tasks take importers lazily) are
    # another way in.
    for name in ["jellyscope.importers", "jellyscope.scanner", "jellyscope.tasks"]:
        run = subprocess.run([sys.executable, "-c", f"import {name}"], cwd=PROJECT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=180)
        error = (run.stderr.strip().splitlines() or [""])[-1]
        check(run.returncode == 0, f"`import {name}` first in a clean process ({error[:120]})")
else:
    check(False, "dependencies without a cycle (the package importers/ is missing)")

print()
print("--- (d) size ---")
large = [(p.name, n) for p in sorted(PACKAGE.glob("*.py")) if (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
check(not large, f"no submodule over 600 lines ({large})")

print()
print("--- (e) tests substitute where it takes effect ---")
readers: dict[str, set[str]] = {}
for m in FILES:
    if (PACKAGE / f"{m}.py").is_file():
        for name in loaded(tree(PACKAGE / f"{m}.py")):
            readers.setdefault(name, set()).add(m)
names_in = {m: defined(tree(PACKAGE / f"{m}.py")) | loaded(tree(PACKAGE / f"{m}.py"))
            for m in FILES if (PACKAGE / f"{m}.py").is_file()}
wrong, outside = [], []
for path in sorted(PROJECT.glob("tests/*.py")):
    if path.name in WITH_EXAMPLES:
        continue
    text = path.read_text(encoding="utf-8")
    wrong += [f"{path.name}: {error}" for error in wrong_substitutes(text, "importers", readers)]
    for m, name in sorted(substitutes_in_submodules(text, "importers")):
        if m in names_in and name not in names_in[m]:
            outside.append(f"{path.name}: importers.{m}.{name}")
check(not wrong, f"names read in submodules are substituted through substitute or in all their readers ({wrong})")
check(not outside, f"every substitute in a submodule aims where the name is read ({outside})")

print()
print("--- (h) the logger ---")
# Every call, not one per file - a second getLogger would otherwise override the first.
loggers = [(p.name, v) for p in sorted(PACKAGE.glob("*.py"))
           for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
wrong_loggers = [(f, v) for f, v in loggers if v != '"jellyscope.importers"']
check(loggers and not wrong_loggers, f"the package logs as jellyscope.importers ({wrong_loggers or len(loggers)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
