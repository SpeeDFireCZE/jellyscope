# -*- coding: utf-8 -*-
r"""The package jellyscope/stats/: a structure that must not drift apart.

The statistical queries live in the package `jellyscope/stats/` with its door (`stats/__init__.py`), so nothing
changes from outside. Guarded:

(a) the door has everything that is read from outside as `stats.X` or `from jellyscope.stats import X`, and its
    `__all__` is exactly the list of what it imports;
(b) every name lives in its submodule by the map SUBMODULES, the map covers every top-level name of the
    submodules, and the door points at the same objects;
(c) no cycles, the leaves of the graph reach nothing else in the package, and the package as well as its partners
    (scanner, importers, web) import first in a clean process;
(d) no submodule over 600 lines;
(e) tests substitute where it takes effect (tests/_package.py);
(f) the old stats.py is gone, and tests that read the sources see the package too (a glob of jellyscope/*.py
    without subfolders would quietly stop checking the statistics; an exception only with the marker
    "top level only" and a reason on the same line);
(h) the logger is called as before (`jellyscope.stats`).

Run:
    .venv/bin/python tests/test_stats_package.py
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
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "stats_package.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import substitute, substitutes_in_submodules, wrong_substitutes  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


PACKAGE = PROJECT / "jellyscope" / "stats"
OLD_FILE = PROJECT / "jellyscope" / "stats.py"
# Files whose text holds examples like stats.X; this test also by its own name (a PostgreSQL run is a copy under
# another name, tests/_pg_<name>.py).
WITH_EXAMPLES = {Path(__file__).name, "test_stats_package.py", "test_web_package.py", "_package.py"}

# Which top-level name lives in which submodule. A new name of the package adds itself here.
SUBMODULES: dict[str, list[str]] = {
    "periods": ["Period", "END_OF_TIME", "NO_LIMIT", "OLDEST_HISTORY", "period_of_days", "whole_history_period",
                "_local_time", "period_between", "period_from_instants", "_as_period", "_bounds", "previous_period",
                "first_record", "can_compare", "transcode_condition", "_local_day", "chart_days"],
    "kinds": ["VIDEO_LIBRARY_TYPES", "is_video_library", "video_library_sql", "KIND_MOVIE", "KIND_SERIES",
              "KIND_OTHER", "KIND_BOTH", "ALLOWED_KINDS", "SERIES_TYPES", "_KNOWN_TYPES", "_SERIES_TYPES_SQL",
              "_KNOWN_TYPES_SQL", "KIND_MUSIC", "KIND_ALL", "MUSIC_TYPES", "_MUSIC_TYPES_SQL", "HISTORY_KINDS",
              "KIND_BOOKS", "BOOK_TYPES", "_BOOK_TYPES_SQL", "_NOT_WATCHING_SQL",
              "watching_only", "by_titles", "only_music", "only_books", "_kind_condition", "ITEM_TYPE_LABELS",
              "UNKNOWN_TYPE",
              "type_label"],
    "shared": ["UPGRADE_PAIRING_WINDOW", "GRAB_WINDOW", "BUCKETS", "UNKNOWN", "_time", "_local", "local_fast", "_shift",
               "_quantile", "_pair_grabs", "_pair_upgrades", "_ranked"],
    "playback": ["active_session_count", "transcode_speed", "transcode_description", "active_sessions"],
    "tech": ["RESOLUTION_CASE", "RESOLUTION_ORDER", "tech_coverage", "_video_coverage", "codec_breakdown",
             "resolution_breakdown", "RANGE_CASE", "item_range", "video_range_breakdown"],
    "recent": ["_KEY", "_NEWEST_GROUPS", "recently_added", "_sort_episodes", "_episode_preference", "BATCH_HOURS",
               "_hours_back", "_in_same_batch"],
    "content_history": ["Changes", "_remembered", "kind_changes", "series_from_changes", "_remember", "all_changes",
                        "changes_by_disk", "kinds_of", "complete_rows", "complete_library_rows", "complete_disk_rows"],
    "titles": ["_SERIES_FROM_NAME", "series_from_name", "_episodes_by_name", "_series_names", "extra_copies",
               "title_key", "TITLE_GROUP_KEY", "TITLE_GROUP_COLUMNS", "_merge_titles", "top_items"],
    "viewers": ["VIEWER_NAME", "VIEWER_GROUP", "VIEWER_ORDER", "top_users", "user_table", "_user_table",
                "_with_links", "user_genres", "user_detail"],
    "item_detail": ["item", "item_versions", "title_items", "title_files", "item_streams", "item_playback",
                    "item_playback_summary", "sibling_episodes", "delete_item", "_delete_item", "item_thumbnail",
                    "file_name", "item_label"],
    "play_history": ["PLAY_METHODS", "_safety_bounds", "_history_filter", "history", "history_for_api",
                     "history_count", "filter_values"],
    "archive": ["archived_count", "only_archived", "delete_from_archive", "_ALL_PLAYS", "archived_items",
                "_EPISODE_CODE", "_ASCII_PART", "_WORD_EDGE", "CANDIDATE_LIMIT", "_for_search", "_episode_mark",
                "_standalone", "_like_anywhere", "find_existing", "AssignmentError", "assign_to_other"],
    "locations": ["HOME_PREFIXES", "is_home", "_traffic_by_address", "watching_locations", "places", "hotspots",
                  "viewer_countries"],
    "network": ["_network_rows", "_seconds", "bandwidth_timeline", "flow_now", "SHORTEST_LIVE_WINDOW",
                "LIVE_STEP_SECONDS", "MAX_LIVE_POINTS", "bandwidth_live", "bandwidth_daily_peaks",
                "bandwidth_summary", "bandwidth_by"],
    "disks": ["MEASURED", "DISK_KINDS", "SINGLE_DISK", "disk_snapshots", "complete_disks", "disk_changes", "_has_kinds", "_segment",
              "_pace", "disks_to_choose", "_outside_series", "outside_series", "disks_overview", "runs_out_first"],
    "growth": ["_backfilled_curve", "library_state", "snapshots", "_period_days", "_anchor", "_free_space_now",
               "library_growth", "_growth_base", "_segment_start", "disk_growth_series", "disk_growth", "_by_disk"],
    "sonarr": ["TRIGGERS", "_COLUMNS", "_is_import", "_is_upgrade", "_analysis", "sonarr_events", "sonarr_additions",
               "sonarr_heatmap", "sonarr_largest", "sonarr_speed", "sonarr_sources", "_trigger", "sonarr_upgrades",
               "sonarr_first", "sonarr_failures", "sonarr_library", "sonarr_detail_links", "sonarr_imports"],
    "lidarr": ["TRIGGERS", "SAME_IMPORT", "ALBUM_UPGRADE_WINDOW", "ALBUM_UPGRADE_SLACK", "_COLUMNS", "_is_import", "_is_upgrade", "_import_key", "_album_imports", "_mark_upgrades",
               "_analysis", "lidarr_events", "lidarr_additions", "lidarr_heatmap", "lidarr_largest", "lidarr_speed",
               "lidarr_sources", "_trigger", "lidarr_upgrades", "lidarr_first", "lidarr_failures", "lidarr_library"],
    "readarr": ["TRIGGERS", "SAME_IMPORT", "BOOK_UPGRADE_WINDOW", "BOOK_UPGRADE_SLACK", "_COLUMNS", "_is_import", "_is_upgrade", "_import_key", "_book_imports", "_mark_upgrades",
                "_analysis", "readarr_events", "readarr_additions", "readarr_heatmap", "readarr_largest", "readarr_speed",
                "readarr_sources", "_trigger", "readarr_upgrades", "readarr_first", "readarr_failures", "readarr_library"],
    "radarr": ["TRIGGERS", "_COLUMNS", "_is_import", "_is_upgrade", "_analysis", "radarr_events", "radarr_additions",
               "radarr_heatmap", "radarr_largest", "radarr_speed", "radarr_sources", "_trigger", "radarr_upgrades",
               "radarr_first", "radarr_failures", "radarr_library", "radarr_detail_links", "radarr_imports"],
    "bazarr": ["ACTIONS", "ACQUIRED", "UPGRADE", "OTHER", "ACTION_ORDER", "BUCKETS", "SCORE_BUCKETS", "IMPORTS",
               "_COLUMNS", "_ACQUIRED_SQL", "_order", "_mean", "_median", "_acquired", "_action_counts", "_earlier",
               "_imports", "bazarr_first", "bazarr_analysis", "_Never", "_after_import", "bazarr_numbers",
               "bazarr_by_day", "bazarr_heatmap", "bazarr_actions", "_groups", "_score", "bazarr_languages",
               "bazarr_providers", "bazarr_scores", "bazarr_upgrades", "bazarr_changed", "bazarr_speed"],
    "prowlarr": ["QUERY_KINDS", "_IN_PERIOD", "_BY_HOUR", "_BY_INDEXER", "_BY_APP", "_GRABBED_BY_INDEXER",
                 "_GRABBED_BY_APP", "_LATEST", "_whole", "_share", "_mean", "prowlarr_first",
                 "prowlarr_indexer_names", "prowlarr_analysis", "prowlarr_numbers", "prowlarr_by_day",
                 "prowlarr_heatmap", "prowlarr_who", "prowlarr_indexers", "prowlarr_grabbed",
                 "prowlarr_grabbed_indexers", "prowlarr_grabbed_latest"],
    "seerr": ["AVAILABLE", "DELETED", "APPROVED", "DECLINED", "_COLUMNS", "_time", "_local", "_in_library",
              "_items_by_id", "_title_items", "_watching", "_posters_by_id", "_ids_from_other_requests",
              "seerr_posters", "seerr_with_posters", "seerr_events", "_requests", "seerr_summary", "seerr_by_day",
              "seerr_heatmap", "seerr_requesters", "_group", "seerr_handling", "seerr_watching", "seerr_latest",
              "seerr_first"],
    "music_comparison": ["TOP", "_added", "_genre_rows", "_hours", "_side", "music_comparison"],
    "music_listening": ["PLAY_FROM_SECONDS", "TICKS", "_SHARED_TEXT_COLUMNS", "music_analysis",
                        "music_first", "music_numbers", "music_by_day", "music_heatmap", "music_listeners",
                        "_ranking", "music_artists", "music_albums", "music_tracks", "music_genres", "_is_transcode",
                        "music_play_method", "music_apps", "music_network"],
    "music_library": ["LOSSLESS", "FORMATS", "HIRES_BITS", "HIRES_HZ", "_lossless", "codec_format", "library_tracks",
                      "music_library_summary", "music_formats", "music_quality", "library_genres", "music_decades",
                      "_ALBUM_TOTALS", "_ALBUM_COLUMNS", "_ALBUMS_SQL", "_albums", "music_newest", "music_largest",
                      "music_unplayed", "_numbers", "_listeners", "_album_listening", "music_artist", "music_viewer",
                      "ALBUM_SORTS", "ALBUMS_PER_PAGE", "_folded", "_totals_by_album", "_album_totals",
                      "_library_album_totals", "library_albums", "album_detail", "RECENT_LISTENS", "TOP_ALBUMS",
                      "LIBRARY_CARDS", "library_artist_count", "library_overview", "library_listening"],
    "books_comparison": ["TOP", "_added", "_genre_rows", "_hours", "_side", "books_comparison"],
    "books_listening": ["TICKS", "READ_FROM_SECONDS", "AUDIOBOOK", "BOOK", "_SHARED_TEXT_COLUMNS", "work_key",
                        "_split", "books_works_index", "books_analysis", "books_first", "reading_reported", "books_numbers",
                        "books_by_day", "books_heatmap", "books_readers", "_work_info", "books_works", "books_authors",
                        "books_genres", "books_play_method", "books_apps"],
    "books_library": ["WORK_SORTS", "WORKS_PER_PAGE", "RECENT_LISTENS", "TOP_WORKS", "_natural", "books_library_works",
                      "books_library_summary", "books_formats", "books_decades", "books_library_genres", "books_newest",
                      "books_largest", "books_unread", "library_book_works", "_readers", "book_detail",
                      "books_library_overview", "books_library_listening"],
    "library_page": ["_GROUP", "_GROUP_COLUMNS", "_PLAYS", "_VERSIONS", "_library_filter", "library_rows",
                     "library_rows_count", "series_detail", "_common_folder", "LIBRARY_TYPE_LABELS",
                     "TICKS_PER_SECOND", "_MUSIC_ALBUMS", "_MUSIC_TRACKS", "_BOOK_WORK", "_BOOKS", "_BOOK_COVERS", "_MUSIC_COVERS", "library_cards",
                     "library", "library_overview", "library_activity"],
    "overview_page": ["overview", "previous_overview", "length_seconds", "_overlap", "_row", "DELIVERY_ROLES",
                      "_language_rows", "comparison", "others_breakdown", "daily_activity_split",
                      "play_method_breakdown", "client_breakdown", "hourly_heatmap"],
}
FILES = list(SUBMODULES)
# The leaves of the graph: these submodules reach nothing else in the package.
LEAVES = {"periods", "kinds", "shared", "playback", "tech", "recent", "content_history"}


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


stats = importlib.import_module("jellyscope.stats")

print("--- (f) the old file and tests that read the sources ---")
check(is_package() and not OLD_FILE.exists(), "stats.py is gone, the package stats/ is there")
SHALLOW = re.compile(r'\(PROJECT / "jellyscope"\)\.glob\("\*\.py"\)')
TOP_LEVEL_MARKS = ("top level only",)
shallow = [f"{f.name}:{i}" for f in sorted(PROJECT.glob("tests/*.py")) if f.name not in WITH_EXAMPLES
           for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
           if SHALLOW.search(line) and not any(mark in line for mark in TOP_LEVEL_MARKS)]
check(not shallow, f"no test reads the sources without subfolders ({shallow})")

print()
print("--- (a) the door has everything that is read from outside ---")
NAME = re.compile(r"(?<![\w.])stats\.([A-Za-z_]\w*)")
FROM = re.compile(r"from\s+(?:\.|jellyscope\.)stats\s+import\s+\(?([^)\n]+)\)?")
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
missing = sorted(f"{n} ({where})" for n, where in used.items() if n not in FILES and not hasattr(stats, n))
check(len(used) > 50 and not missing, f"all {len(used)} names go through the door (missing {missing[:6]})")
door_imports = {(a.asname or a.name) for node in tree(PACKAGE / "__init__.py").body
                if isinstance(node, ast.ImportFrom) and node.module != "__future__" for a in node.names}
all_names = set(getattr(stats, "__all__", []))
check(all_names == door_imports and len(all_names) == len(stats.__all__),
      f"__all__ is exactly what the door imports, each once (only in __all__ {sorted(all_names - door_imports)[:6]},"
      f" only imported {sorted(door_imports - all_names)[:6]})")

print()
print("--- (b) every name lives in its submodule ---")
if is_package():
    defined_names = {m: defined(tree(PACKAGE / f"{m}.py")) - {"log"} for m in FILES if (PACKAGE / f"{m}.py").is_file()}
    on_map = sorted(n for names in SUBMODULES.values() for n in names)
    in_files = [n for names in defined_names.values() for n in names]
    check(sorted(set(PACKAGE.glob("*.py")) - {PACKAGE / "__init__.py"}) == sorted(PACKAGE / f"{m}.py" for m in FILES),
          f"the package has exactly the submodules of the map ({sorted(p.name for p in PACKAGE.glob('*.py'))})")
    check(sorted(on_map) == sorted(in_files),
          f"the map covers every top-level name of the submodules ({len(on_map)} names; not on the map "
          f"{sorted(set(in_files) - set(on_map))[:6]}, not defined {sorted(set(on_map) - set(in_files))[:6]})")
    missing_in = []
    owners: dict[str, list[object]] = {}
    for m, names in SUBMODULES.items():
        there = defined_names.get(m, set())
        missing_in += [f"{m}.{n}" for n in names if n not in there]
        if (PACKAGE / f"{m}.py").is_file():
            module = importlib.import_module(f"jellyscope.stats.{m}")
            for n in names:
                if n in there:
                    owners.setdefault(n, []).append(getattr(module, n))
    # A name defined in two submodules (library_overview: the video and the music library) is offered once - the
    # door must point at one of them.
    elsewhere = sorted(n for n in stats.__all__ if n in owners and not any(getattr(stats, n) is o for o in owners[n]))
    check(not missing_in, f"the names are in the submodules by the map ({missing_in[:6]})")
    check(not elsewhere, f"the door points at the same objects as the submodules ({elsewhere[:6]})")
else:
    check(False, "the names are in the submodules by the map (the package stats/ is missing)")

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
    # per package covers them all. The partners (the scanner takes stats at the top, web and importers too) are
    # other ways in.
    for name in ["jellyscope.stats", "jellyscope.scanner", "jellyscope.importers", "jellyscope.web"]:
        run = subprocess.run([sys.executable, "-c", f"import {name}"], cwd=PROJECT, env=os.environ.copy(),
                             capture_output=True, text=True, timeout=180, check=False)
        error = (run.stderr.strip().splitlines() or [""])[-1]
        check(run.returncode == 0, f"`import {name}` first in a clean process ({error[:120]})")
else:
    check(False, "dependencies without a cycle (the package stats/ is missing)")

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
original_door, original_module = stats.recently_added, stats.recent.recently_added


def replacement(*a, **k):
    return []


restore = substitute(stats, "recently_added", replacement)
check(stats.recently_added is replacement and stats.recent.recently_added is replacement,
      "substitute sets the name in the door and in the submodule")
restore()
check(stats.recently_added is original_door and stats.recent.recently_added is original_module,
      "and restoring gives both back")
try:
    substitute(stats, "recently_added_typo", replacement)
    check(False, "substitute with a typo in the name fails (it would quietly substitute nothing otherwise)")
except AttributeError:
    check(True, "substitute with a typo in the name fails (it would quietly substitute nothing otherwise)")
# The check of the check (e): the whole file is judged - a plain assignment beside substitute is not let off.
TWO = {"sonarr_detail_links": {"sonarr", "bazarr"}}
check(bool(wrong_substitutes('substitute(stats, "sonarr_detail_links", F)\nstats.sonarr_detail_links = G', "stats", TWO)),
      "(e) a plain assignment beside substitute is not let off")
check(bool(wrong_substitutes("stats.sonarr_detail_links = F\nstats.sonarr.sonarr_detail_links = F", "stats", TWO)),
      "(e) a substitute in only one of two readers is not enough")
check(not wrong_substitutes('substitute(stats, "sonarr_detail_links", F)', "stats", TWO)
      and not wrong_substitutes("stats.sonarr_detail_links = F\nstats.sonarr.sonarr_detail_links = F\n"
                                "stats.bazarr.sonarr_detail_links = F", "stats", TWO),
      "(e) substitute, or a substitute in every reader, is enough")
names_in = {m: defined(tree(PACKAGE / f"{m}.py")) | loaded(tree(PACKAGE / f"{m}.py"))
            for m in FILES if (PACKAGE / f"{m}.py").is_file()}
wrong, outside = [], []
for path in sorted(PROJECT.glob("tests/*.py")):
    if path.name in WITH_EXAMPLES:
        continue
    text = path.read_text(encoding="utf-8")
    wrong += [f"{path.name}: {error}" for error in wrong_substitutes(text, "stats", readers)]
    for m, name in sorted(substitutes_in_submodules(text, "stats")):
        if m in names_in and name not in names_in[m]:
            outside.append(f"{path.name}: stats.{m}.{name}")
check(not wrong, f"names read in submodules are substituted through substitute or in all their readers ({wrong})")
check(not outside, f"every substitute in a submodule aims where the name is read ({outside})")

print()
print("--- (h) the logger ---")
# Every call, not one per file - a second getLogger would otherwise override the first.
loggers = [(p.name, v) for p in sorted(PACKAGE.glob("*.py"))
           for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
wrong_loggers = [(f, v) for f, v in loggers if v != '"jellyscope.stats"']
check(loggers and not wrong_loggers, f"the package logs as jellyscope.stats ({wrong_loggers or len(loggers)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
