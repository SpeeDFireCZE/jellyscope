# -*- coding: utf-8 -*-
r"""The package jellyscope/scanner/: a structure that must not drift apart.

The library sync, refreshing items, merging, writing, disks and free space, stream languages, the tech scan,
copies and the state of running tasks live in the package `jellyscope/scanner/` with its door
(`scanner/__init__.py`), so nothing changes from outside. Guarded:

(a) the door has everything that is read from outside as `scanner.X` or `from jellyscope.scanner import X`,
    and its `__all__` is exactly the list of what it imports;
(b) every name lives in its submodule by the map SUBMODULES, the map covers every top-level name of the
    submodules, and the door points at the same objects;
(c) no cycles, the leaves of the graph reach nothing else in the package, and the package as well as its
    partners in import cycles (importers, tasks, stats) import first in a clean process;
(d) no submodule over 600 lines;
(e) tests substitute where it takes effect (tests/_package.py);
(f) the state of a running task is one: `_stop_requested` is read and rewritten only in task_state and is not
    in the door (an import would make a copy and the Stop button would stop working), the lock and the
    progress are the same object in every submodule;
(g) the old scanner.py is gone and no test reads it by path;
(h) the logger is called as before (`jellyscope.scanner`).

Run:
    .venv/bin/python tests/test_scanner_package.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "scanner_package.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import (outside_sources, read_through_door, substitute, substitutes_in_submodules,  # noqa: E402
                      unread_door_names, wrong_substitutes)

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


PACKAGE = PROJECT / "jellyscope" / "scanner"
OLD_FILE = PROJECT / "jellyscope" / "scanner.py"
# Files whose text holds examples like scanner.X; this test also by its own name (a PostgreSQL run is a copy
# under another name, tests/_pg_<name>.py).
WITH_EXAMPLES = {Path(__file__).name, "test_scanner_package.py", "test_importers_package.py", "test_stats_package.py",
                 "test_web_package.py", "_package.py"}

# Which top-level name lives in which submodule. A new name of the package adds itself here.
SUBMODULES: dict[str, list[str]] = {
    "item_types": ["MANAGED_TYPES", "WRAPPER_TYPES", "NOT_YET_CLASSIFIED", "WRITTEN_TYPES", "is_wrapper"],
    "task_state": ["_scan_lock", "is_scan_running", "task_lock", "_stop_requested", "request_stop",
                   "stop_requested", "_progress", "progress", "_start_progress", "_add_progress", "_clear_progress",
                   "_clear_stop", "start_task_log", "versions", "library_version", "tasks_version",
                   "finish_task_log", "RECORDS_PER_KIND", "_thin_records", "close_interrupted_task_logs", "last_scan"],
    "free_space": ["CAPACITY_KEY", "JF_FREE_KEY", "JF_TOTAL_KEY", "SOURCE_KEY", "_number", "space_from_jellyfin",
                   "remember_space_from_jellyfin", "_free_from_capacity", "_library_free_space", "write_snapshot"],
    "merging": ["_old_to_merge", "_merge_by_tmdb", "duplicates_of_new", "_by_tmdb", "_by_name", "_select",
                "move_history", "_equal_or_empty", "archive"],
    "copies": ["_order", "_expected", "recount_copies", "recount_copies_after_sync"],
    "disks": ["FOLDERS_KEY", "FOLDERS_TIME_KEY", "DISK_CAPACITY_KEY", "DISK_NAMES_KEY", "SAME_KEY", "DIFFERENT_KEY",
              "VIRTUAL_KEY", "SINGLE_DISK", "CONVERTED_KEY", "TOLERANCE", "MAX_AGE_DAYS", "LOCK", "_json", "_int", "_space",
              "FINGERPRINT_WORDS", "_old", "_fingerprint", "normalize_path", "is_inside", "folders_from_jellyfin", "remember_folders", "stale_since",
              "_same_groups", "physical_disks", "suspicions", "disk_paths", "virtual_disks", "_ancestors",
              "_assigner", "file_assignment", "files_by_disk", "_kinds_remembered", "_kinds_by_disk",
              "_manual_capacity", "_custom_name", "_physical_row", "_single_state", "disk_state", "_disk_for_old_capacity",
              "convert_old_capacity"],
    "disk_snapshots": ["write_disk_snapshot", "free_space_now", "_one_per_group", "write_daily_snapshots"],
    "disk_settings": ["_save", "_kept", "set_capacity", "set_disk_name", "_check_virtual", "add_virtual", "edit_virtual",
                      "delete_virtual", "decide_same", "undo_decision"],
    "writing": ["write_versions", "_write_batch", "_write_items", "_ID_COLUMN", "_SERIES_COLUMN",
                "_SERIES_IMAGE_TAG_COLUMN", "_IMAGE_TAG_COLUMN", "_items_with_new_image", "_forget_images",
                "save_streams", "_unconfirmed_libraries", "_library_names", "_mark_missing", "_archive_pairs", "merge_archive_into_live", "archive_overview",
                "clean_up_phantoms"],
    "sync": ["sync_library", "_sync_users", "EMPTY_ANSWER_COUNTS_AFTER", "_only_libraries", "_sync_libraries", "tmdb_id_of",
             "item_identity", "_item_row", "_already_known", "_live_among_old",
             "_save_new_items", "_sync_items_of_library"],
    "refresh": ["RECENT_OVERLAP_MINUTES", "REVIEW_CAP", "_last_added", "refresh_item", "refresh_series",
                "_recent_message", "sync_recent", "_review_recent", "_successors_by_path",
                "straighten_after_new"],
    "stream_languages": ["fill_languages_from_jellyfin", "_fill_item_languages",
                         "candidates_for_language_from_name", "fill_languages_from_name", "_fill_item_from_name",
                         "_split_by_name", "_pair_streams", "_recount_item_languages"],
    "tech_scan": ["_path_mappings", "_analysis_query", "_analyse_files", "_additions_after_analysis", "_additions_text",
                  "run_tech_scan", "_missing_language", "candidates_for_range_from_jellyfin",
                  "fill_range_from_jellyfin", "_save_probe_result", "_save_tech", "_save_tech_error"],
}
FILES = list(SUBMODULES)
# State that is rewritten (a bool): the door would hold only a copy from the time of the import.
NOT_IN_DOOR = {"_stop_requested"}
# The leaves of the graph: these submodules reach nothing else in the package.
LEAVES = {"item_types", "task_state", "free_space", "merging", "stream_languages", "copies"}


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


scanner = importlib.import_module("jellyscope.scanner")

print("--- (g) the old file ---")
check(is_package() and not OLD_FILE.exists(), "scanner.py is gone, the package scanner/ is there")
BY_PATH = re.compile(r"""["']scanner\.py["']""")
by_path = [f"{f.name}:{i}" for f in sorted(PROJECT.glob("tests/*.py"))
           if f.name not in WITH_EXAMPLES and f.name != "test_sync_to_prod.py"
           for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if BY_PATH.search(line)]
check(not by_path, f"no test reads scanner.py by path ({by_path[:6]})")

print()
print("--- (a) the door has everything that is read from outside ---")
NAME = re.compile(r"(?<![\w.])scanner\.([A-Za-z_]\w*)")
FROM = re.compile(r"from\s+jellyscope\.scanner\s+import\s+\(?([^)\n]+)\)?")
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
missing = sorted(f"{n} ({where})" for n, where in used.items()
                 if n not in FILES and n not in NOT_IN_DOOR and not hasattr(scanner, n))
check(len(used) > 50 and not missing, f"all {len(used)} names go through the door (missing {missing[:6]})")
door_imports = {(a.asname or a.name) for node in tree(PACKAGE / "__init__.py").body
                if isinstance(node, ast.ImportFrom) and node.module != "__future__" for a in node.names}
all_names = set(getattr(scanner, "__all__", []))
check(all_names == door_imports and len(all_names) == len(scanner.__all__),
      f"__all__ is exactly what the door imports, each once (only in __all__ {sorted(all_names - door_imports)[:6]},"
      f" only imported {sorted(door_imports - all_names)[:6]})")
# The other way round (audit CL-10): a name the door takes from a submodule is read - in the door itself or from
# the outside. A re-export nobody reads only hides where the name lives, and the check above keeps it for ever.
# What is read through an alias or by a string (`substitute(scanner, "X")`) counts too.
door_reads = read_through_door("jellyscope.scanner", outside_sources(PROJECT, PACKAGE, WITH_EXAMPLES), PROJECT)
re_exported, unread = unread_door_names(PACKAGE / "__init__.py", set(used) | door_reads)
check(len(re_exported) > 50 and not unread,
      f"every name the door takes from a submodule is read in the door or from the outside ({len(unread)} unread: {unread})")
check(not (door_imports & NOT_IN_DOOR) and not hasattr(scanner, "_stop_requested"),
      "_stop_requested is not in the door (an import would make a copy that never changes)")

print()
print("--- (b) every name lives in its submodule ---")
if is_package():
    defined_names = {m: defined(tree(PACKAGE / f"{m}.py")) - {"log"} for m in FILES if (PACKAGE / f"{m}.py").is_file()}
    on_map = sorted(n for names in SUBMODULES.values() for n in names)
    check(sorted(set(PACKAGE.glob("*.py")) - {PACKAGE / "__init__.py"}) == sorted(PACKAGE / f"{m}.py" for m in FILES),
          f"the package has exactly the submodules of the map ({sorted(p.name for p in PACKAGE.glob('*.py'))})")
    check(len(on_map) == len(set(on_map)) and set(on_map) == {n for names in defined_names.values() for n in names},
          f"the map covers every top-level name of the submodules, each once ({len(on_map)} names; not on the map "
          f"{sorted({n for names in defined_names.values() for n in names} - set(on_map))[:6]})")
    missing_in, elsewhere = [], []
    for m, names in SUBMODULES.items():
        there = defined_names.get(m, set())
        missing_in += [f"{m}.{n}" for n in names if n not in there]
        if (PACKAGE / f"{m}.py").is_file():
            module = importlib.import_module(f"jellyscope.scanner.{m}")
            elsewhere += [f"{m}.{n}" for n in names if n in there and hasattr(scanner, n)
                          and getattr(scanner, n) is not getattr(module, n)]
    check(not missing_in, f"the names are in the submodules by the map ({missing_in[:6]})")
    check(not elsewhere, f"the door points at the same objects as the submodules ({elsewhere[:6]})")
    jellyfin = importlib.import_module("jellyscope.jellyfin")
    check(getattr(scanner, "JellyfinClient", None) is jellyfin.JellyfinClient,
          "scanner.JellyfinClient is the client from jellyfin.py (tests substitute it)")
else:
    check(False, "the names are in the submodules by the map (the package scanner/ is missing)")

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
    left = {m: set(e) for m, e in edges.items()}
    while any(not e for e in left.values()):
        done = {m for m, e in left.items() if not e}
        left = {m: e - done for m, e in left.items() if m not in done}
    check(not left, f"dependencies without a cycle ({sorted(left)})")
    check(all(not edges[m] for m in LEAVES),
          f"the leaves reach nothing else in the package ({ {m: sorted(edges[m]) for m in LEAVES if edges[m]} })")
    # Importing a submodule always runs the package's __init__ first (and that loads every submodule) - one check
    # per package covers them all. The partners in import cycles (importers, tasks and stats take the scanner
    # lazily, the scanner takes them) are other ways in.
    for name in ["jellyscope.scanner", "jellyscope.importers", "jellyscope.tasks", "jellyscope.stats"]:
        run = subprocess.run([sys.executable, "-c", f"import {name}"], cwd=PROJECT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=180)
        error = (run.stderr.strip().splitlines() or [""])[-1]
        check(run.returncode == 0, f"`import {name}` first in a clean process ({error[:120]})")
else:
    check(False, "dependencies without a cycle (the package scanner/ is missing)")

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
# The check of the check: substitute sets the name in the door and in its readers and gives back both.
original_door, original_state = scanner.is_scan_running, scanner.task_state.is_scan_running


def replacement(*a, **k):
    return False


restore = substitute(scanner, "is_scan_running", replacement)
check(scanner.is_scan_running is replacement and scanner.task_state.is_scan_running is replacement,
      "substitute sets the name in the door and in the submodule")
restore()
check(scanner.is_scan_running is original_door and scanner.task_state.is_scan_running is original_state,
      "and restoring gives both back")
try:
    substitute(scanner, "is_scan_running_typo", replacement)
    check(False, "substitute with a typo in the name fails (it would quietly substitute nothing otherwise)")
except AttributeError:
    check(True, "substitute with a typo in the name fails (it would quietly substitute nothing otherwise)")
# The check of the check (e): the whole file is judged - a plain assignment beside substitute is not let off.
TWO = {"JellyfinClient": {"refresh", "sync"}}
check(bool(wrong_substitutes('substitute(scanner, "JellyfinClient", F)\nscanner.JellyfinClient = G', "scanner", TWO)),
      "(e) a plain assignment beside substitute is not let off")
check(bool(wrong_substitutes("scanner.JellyfinClient = F\nscanner.sync.JellyfinClient = F", "scanner", TWO)),
      "(e) a substitute in only one of two readers is not enough")
check(not wrong_substitutes('substitute(scanner, "JellyfinClient", F)', "scanner", TWO)
      and not wrong_substitutes("scanner.JellyfinClient = F\nscanner.refresh.JellyfinClient = F\n"
                                "scanner.sync.JellyfinClient = F", "scanner", TWO),
      "(e) substitute, or a substitute in every reader, is enough")
names_in = {m: defined(tree(PACKAGE / f"{m}.py")) | loaded(tree(PACKAGE / f"{m}.py"))
            for m in FILES if (PACKAGE / f"{m}.py").is_file()}
wrong, outside = [], []
for path in sorted(PROJECT.glob("tests/*.py")):
    if path.name in WITH_EXAMPLES:
        continue
    text = path.read_text(encoding="utf-8")
    wrong += [f"{path.name}: {error}" for error in wrong_substitutes(text, "scanner", readers)]
    for m, name in sorted(substitutes_in_submodules(text, "scanner")):
        if m in names_in and name not in names_in[m]:
            outside.append(f"{path.name}: scanner.{m}.{name}")
check(not wrong, f"names read in submodules are substituted through substitute or in all their readers ({wrong})")
check(not outside, f"every substitute in a submodule aims where the name is read ({outside})")

print()
print("--- (f) the state of a running task is one ---")
if is_package():
    stop_in = sorted({p.stem for p in PACKAGE.glob("*.py")
                      for n in ast.walk(tree(p))
                      if (isinstance(n, ast.Name) and n.id == "_stop_requested")
                      or (isinstance(n, ast.Global) and "_stop_requested" in n.names)
                      or (isinstance(n, ast.alias) and n.name == "_stop_requested")})
    check(stop_in == ["task_state"], f"only task_state reads and rewrites _stop_requested ({stop_in})")
    state = importlib.import_module("jellyscope.scanner.task_state")
    copies = [f"{m}.{n}" for m in FILES for n in ("_scan_lock", "_progress")
              if hasattr(module := importlib.import_module(f"jellyscope.scanner.{m}"), n)
              and getattr(module, n) is not getattr(state, n)]
    check(not copies, f"the lock and the progress are the same object in every submodule as in task_state ({copies})")
else:
    check(False, "the state of a running task (the package scanner/ is missing)")

print()
print("--- (h) the logger ---")
# Every call, not one per file - a second getLogger would otherwise override the first.
loggers = [(p.name, v) for p in sorted(PACKAGE.glob("*.py"))
           for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
wrong_loggers = [(f, v) for f, v in loggers if v != '"jellyscope.scanner"']
check(loggers and not wrong_loggers, f"the package logs as jellyscope.scanner ({wrong_loggers or len(loggers)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
