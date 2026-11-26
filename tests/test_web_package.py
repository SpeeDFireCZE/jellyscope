# -*- coding: utf-8 -*-
r"""The rules of the web/ package - the web split by areas.

`jellyscope/web/` is the application: `__init__.py` founds it (middleware, error handling,
Jinja globals) and is the door the outside reads; the routes live in modules by areas, the
Settings page in a subpackage of its own. From the outside nothing may change, so the test
guards:

(a) the door has everything the outside reads as `web.X` or `from jellyscope.web import X`
    - and nobody takes other modules through it;
(b) the map names every module of the package and the door points at the very objects of
    the submodules;
(c) no cycles: no submodule reaches for `__init__`, dependencies go one way, the package
    (and `jellyscope.devtools`) imports first in a clean process;
(d) the web's files (subpackages included) have up to 600 lines, except base.py;
(e) tests substitute where it takes effect (tests/_package.py);
(f) no test reads the web's sources shallowly or walks `app.routes` directly;
(g) routes, middleware, error handling and the keys of the Jinja globals and filters are
    what the tables here say;
(h) templates and static files are looked for in their place;
(i) the loggers are named `jellyscope.web`.

Spec: docs/superpowers/specs/2026-11-06-web-balik-design.md (the split),
docs/superpowers/specs/2026-11-16-english-code-design.md (the names)

Run:
    .venv/bin/python tests/test_web_package.py
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

import jinja2.defaults

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "web_package.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import substitutes_in_submodule, substitutes_in_submodules, substitutes_at_door, wrong_substitutes, flat_routes  # noqa: E402
from starlette.routing import Match  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


PACKAGE = PROJECT / "jellyscope" / "web"
# The modules of the package (a subpackage under its folder's name).
MODULES = ["__init__", "start", "base", "settings", "periods", "login", "my_account", "dashboards",
           "library", "users", "history", "network", "status", "disks", "arr", "seerr", "video", "legacy",
           "music", "books", "album", "book", "music_library", "books_library", "library_kinds", "body_limit"]
SETTINGS = "settings"               # the subpackage of the Settings page
# Subpackages: their entry point (__init__.py) exposes the names of their modules.
SUBPACKAGES = (SETTINGS, "video", "music", "books")
# The modules whose names the door re-exports for reading; the base and the settings took their
# names themselves even before the split, a substitute at the door never reached them.
ROUTE_MODULES = [m for m in MODULES if m not in ("base", SETTINGS)]
# The developer tools are dev only (DEV_ONLY) - prod and demo have no devtools.py and so no route
# of it either. The test runs there too (prod's CI).
DEV = (PROJECT / "jellyscope" / "devtools.py").is_file()
# Files with examples of substitutes and names in strings - those are no real uses.
EXAMPLE_FILES = {Path(__file__).name, "test_stats_package.py", "test_settings_package.py", "_package.py"}

# Method, path and the name of the function of every route of the application.
ROUTES = sorted('''
GET / home
GET /account my_account
GET /account/dashboard my_dashboard
GET /account/image my_image
GET /account/prehled legacy_redirect
GET /album/{album_id} album_detail
GET /api/now-playing api_now_playing
GET /api/v1 index
GET /api/v1/ index
GET /api/v1/bandwidth traffic
GET /api/v1/history history
GET /api/v1/insights insights_report
GET /api/v1/item/{item_id} title
GET /api/v1/least-played least_played
GET /api/v1/libraries libraries
GET /api/v1/library library
GET /api/v1/now-playing now_playing
GET /api/v1/play-methods play_methods
GET /api/v1/recently-added recently_added
GET /api/v1/summary summary
GET /api/v1/top-items most_watched
GET /api/v1/unwatched unwatched
GET /api/v1/users viewers
GET /archive/search archive_search
GET /arr arr_index
GET /arr/{app} arr_page
GET /arr/{app}/poster/{title_id} arr_poster
GET /dashboard custom_dashboard
GET /health health
GET /history history
GET /history/new history_new
GET /hudba legacy_redirect
GET /hudba/album/{album_id} legacy_redirect
GET /hudba/interpret/{artist_id} legacy_redirect
GET /image/{item_id} item_image
GET /insights legacy_redirect
GET /item/{item_id} item_detail
GET /languages legacy_redirect
GET /languages/bez-jazyka legacy_redirect
GET /library library_index
GET /library/partials/growth growth_partial
GET /library/{library_id} library_detail
GET /login login_form
GET /login/quick/status login_quick_status
GET /music section_root
GET /music/artist/{artist_id} artist
GET /music/overview overview
GET /music/compare compare
GET /music/insights insights
GET /book/{book_id} book_detail
GET /books section_root
GET /books/overview overview
GET /books/compare compare
GET /books/insights insights
GET /network network_page
GET /network/map/tiles/{detail}/{x}/{y} network_map_tile
GET /partials/arr/{app}/downloading arr_downloading
GET /partials/cards cards_partial
GET /partials/network-live network_live_partial
GET /partials/now-playing now_playing_partial
GET /partials/recently-added recently_added_partial
GET /seerr seerr_page
GET /seerr/poster/{kind}/{tmdb_id} seerr_poster
GET /series/{series_id} series_detail
GET /settings settings_page
GET /settings/backup/download backup_download
GET /settings/history/orphans history_orphans
GET /settings/log/download log_download
GET /setup setup_form
GET /srovnani legacy_redirect
GET /top-items legacy_redirect
GET /users users
GET /users/{user_id} user_detail
GET /video section_root
GET /video/compare compare
GET /video/insights insights
GET /video/insights/top insights_top
GET /video/languages languages
GET /video/languages/undetermined languages_undetermined
GET /video/overview overview
GET /video/partials/daily daily_partial
GET /video/partials/top-items top_items_partial
MOUNT /static static
POST /account/dashboard my_dashboard_save
POST /account/event-motion my_account_event_motion
POST /account/key my_account_key
POST /account/key/revoke my_account_key_revoke
POST /account/language my_account_language
POST /account/password my_account_password
POST /dashboard/layout custom_dashboard_save
POST /item/{item_id}/assign item_assign
POST /item/{item_id}/delete item_delete
POST /item/{item_id}/refresh item_refresh
POST /library/disks/capacity disks_capacity
POST /library/disks/decision/undo disks_decision_undo
POST /library/disks/same disks_same
POST /library/disks/virtual disks_virtual_add
POST /library/disks/virtual/delete disks_virtual_delete
POST /library/disks/virtual/edit disks_virtual_edit
POST /library/{library_id}/archive/assign archive_assign
POST /library/{library_id}/archive/delete archive_delete
POST /login login_submit
POST /login/quick login_quick
POST /logout logout
POST /network/detailed-map network_detailed_map
POST /network/detailed-map/delete network_detailed_map_delete
POST /network/geoip network_geoip
POST /series/{series_id}/refresh series_refresh
POST /settings settings_save
POST /settings/accounts/create account_create
POST /settings/accounts/delete account_delete
POST /settings/accounts/keys settings_keys
POST /settings/accounts/password account_password
POST /settings/accounts/permissions settings_account_permissions
POST /settings/accounts/role account_role
POST /settings/api/key-limit api_key_limit
POST /settings/api/token api_token_new
POST /settings/api/token/disable api_token_disable
POST /settings/api/token/enable api_token_enable
POST /settings/api/token/permissions api_token_permissions
POST /settings/api/token/revoke api_token_revoke
POST /settings/arr/{app} settings_arr
POST /settings/arr/{app}/delete settings_arr_delete
POST /settings/arr/{app}/sync settings_arr_sync
POST /settings/backup/delete backup_delete
POST /settings/backup/restore backup_restore
POST /settings/blocks/permanent blocks_permanent
POST /settings/blocks/unblock blocks_unblock
POST /settings/connection settings_connection
POST /settings/database settings_database
POST /settings/dev/event-date set_date
POST /settings/history/assign history_assign
POST /settings/history/forget history_forget
POST /settings/history/restore history_restore
POST /settings/history/tidy history_tidy
POST /settings/import/detect import_detect
POST /settings/import/jellystat import_jellystat
POST /settings/import/playback-reporting import_playback_reporting
POST /settings/import/playback-reporting-file import_playback_reporting_file
POST /settings/interface settings_interface
POST /settings/language settings_language
POST /settings/notifications settings_notifications
POST /settings/notifications/events settings_notification_events
POST /settings/notifications/forget settings_notification_forget
POST /settings/notifications/test settings_notification_test
POST /settings/notifications/texts settings_notification_texts
POST /settings/readers settings_readers
POST /settings/restart settings_restart
POST /settings/scan settings_scan
POST /settings/seerr settings_seerr
POST /settings/seerr/delete settings_seerr_delete
POST /settings/seerr/sync settings_seerr_sync
POST /settings/stop settings_stop
POST /settings/sync settings_sync
POST /settings/tasks tasks_save
POST /settings/conversion-notice/close conversion_notice_close
POST /settings/tasks/run tasks_run
POST /settings/update settings_update
POST /settings/updates settings_updates
POST /settings/viewers settings_viewers
POST /setup setup_submit
POST /video/languages/preferred languages_preferred
'''.strip().splitlines())
MIDDLEWARE = [('BaseHTTPMiddleware', 'security_headers'), ('BaseHTTPMiddleware', 'legacy_query_middleware'),
              ('ApiUsageLog', ''), ('SessionMiddleware', ''), ('LegacySession', ''), ('BodyLimit', ''),
              ('BaseHTTPMiddleware', 'account_language_middleware'), ('BaseHTTPMiddleware', 'viewer_scope_middleware'),
              ('BaseHTTPMiddleware', 'demo_mode_middleware')]
ERROR_HANDLERS = ['fastapi.exceptions.HTTPException', 'fastapi.exceptions.RequestValidationError',
                  'fastapi.exceptions.WebSocketRequestValidationError', 'jellyscope.api_query.ApiError',
                  'jellyscope.web.legacy.StaleForm', 'starlette.exceptions.HTTPException']
# The keys the templates call - Jinja's own and Starlette's `url_for` aside. A renamed key is an
# UndefinedError on the page that uses it. `devtools_date` comes from devtools.py (dev only).
OWN_GLOBALS = ['_', '_n', 'allowed_days', 'arr_connected', 'asset_version', 'card_data', 'charts', 'check_card', 'day_labels',
               'demo_mode', 'devtools_date', 'has_music', 'lang', 'seasonal_event', 'seasonal_motion', 'seasonal_year',
               'seerr_connected', 'stored_text', 'ui_languages']
OWN_FILTERS = ['bitrate', 'bytes', 'czech_date', 'datetime', 'decimal', 'episode_code', 'hours', 'local_datetime',
               'number', 'percent', 'relative', 'resolution', 'seconds', 'step_label', 'ticks', 'timecode',
               'transcode_reasons', 'type_name', 'video_range']


def defined(tree: ast.Module) -> set[str]:
    """The names defined at the top level (def, class, assignment)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def file_of(m: str) -> Path:
    if m in SUBPACKAGES:
        return PACKAGE / m / "__init__.py"
    return PACKAGE / ("__init__.py" if m == "__init__" else f"{m}.py")


def tree_of(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def outside_sources() -> list[Path]:
    """The code and the tests that use the web from the outside (without the package itself)."""
    everything = [*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
                  *(PROJECT / name for name in ("demo.py", "run.py", "manage.py"))]
    return sorted(p for p in everything if p.is_file() and PACKAGE not in p.parents and p.name not in EXAMPLE_FILES)


print("--- (b) the map names every module ---")
present = ({p.stem for p in PACKAGE.glob("*.py")} | {p.parent.name for p in PACKAGE.glob("*/__init__.py")})
check(present == set(MODULES) and len(MODULES) == len(set(MODULES)),
      f"the map has exactly the modules of the package (missing {sorted(set(MODULES) - present)}, extra {sorted(present - set(MODULES))})")
check(all((PACKAGE / m / "__init__.py").is_file() for m in SUBPACKAGES), f"the subpackages are folders ({SUBPACKAGES})")
# The door's docstring is the reader's map of the package: it names every module too.
door_doc = ast.get_docstring(tree_of(PACKAGE / "__init__.py")) or ""
unnamed = [m for m in MODULES if f"`{m}`" not in door_doc]
check(not unnamed, f"the docstring of the door names every module ({unnamed})")
if present != set(MODULES):
    print()
    print(f"DONE - failures: {failures}")
    sys.exit(1)

web = importlib.import_module("jellyscope.web")
ROOT_MODULES = ({p.stem for p in (PROJECT / "jellyscope").glob("*.py")}       # top level only: the names of the modules
                | {p.parent.name for p in (PROJECT / "jellyscope").glob("*/__init__.py")}) - {"web", "__init__"}
mismatched = []
for m in MODULES[1:]:
    module = importlib.import_module(f"jellyscope.web.{m}")
    for name in defined(tree_of(file_of(m))):
        if name in MODULES:
            continue             # a route named like its module (history.history): web.<name> is the module
        if hasattr(web, name) and getattr(web, name) is not getattr(module, name):
            mismatched.append(f"{m}.{name}")
check(not mismatched, f"the door points at the same objects as the submodules ({mismatched[:6]})")

print()
print("--- (f) nobody reads the web shallowly ---")
# The web has subpackages: glob("*.py") over web/ does not see them and a test would quietly leave
# the Settings out. rglob is the way; a file named like the settings subpackage does not exist.
SHALLOW = re.compile("|".join((
    r"""["'](?:jellyscope/)?web["']\)\.glob\(["']\*\.py["']\)""",      # (… / "web").glob("*.py")
    r"""glob\(["']jellyscope/web/\*\.py["']\)""",                     # PROJECT.glob("jellyscope/web/*.py")
    rf"""(?:web/|["']web["']\s*/\s*["']){SETTINGS}\.py["']""",            # the subpackage read as one file
    rf"""getsource\(\s*(?:web_)?{SETTINGS}\s*\)""",                     # getsource(module) = only its __init__
)))
SETTINGS_ALIAS = re.compile(
    rf"(?:from\s+jellyscope\.web\s+import\s+{SETTINGS}|import\s+jellyscope\.web\.{SETTINGS})\s+as\s+(\w+)")


def shallow_lines(text: str) -> list[int]:
    """The numbers of a test's lines that read the web shallowly or the settings subpackage by a file path.

    `getsource` of a subpackage returns only its __init__ - also when it goes by an alias of its
    own (`from jellyscope.web import <settings> as _wn`).
    """
    aliases = SETTINGS_ALIAS.findall(text)
    through_alias = (re.compile(rf"getsource\(\s*(?:{'|'.join(map(re.escape, aliases))})\s*\)")
                     if aliases else None)
    return [i for i, line in enumerate(text.splitlines(), 1)
            if SHALLOW.search(line) or (through_alias and through_alias.search(line))]


shallow = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py"))
           if s.name not in EXAMPLE_FILES and s.name != "test_sync_to_prod.py"      # file names in a fixture
           for i in shallow_lines(s.read_text(encoding="utf-8"))]
check(not shallow, f"no test reads the web shallowly or the settings subpackage by a file path ({shallow})")
# A check of the check: it knows other spellings of a shallow read too.
for sample in ('sorted((PROJECT / "jellyscope" / "web").glob("*.py"))', 'PROJECT.glob("jellyscope/web/*.py")',
               '(PROJECT / "jellyscope/web").glob("*.py")', f'PROJECT / "jellyscope/web/{SETTINGS}.py"',
               f'"web" / "{SETTINGS}.py"', f"inspect.getsource(web_{SETTINGS})", f"inspect.getsource({SETTINGS})"):
    check(bool(SHALLOW.search(sample)), f"the guard knows a shallow read: {sample}")
check(not SHALLOW.search('sorted((PROJECT / "jellyscope" / "web").rglob("*.py"))'), "the guard lets rglob through")
check(shallow_lines(f"from jellyscope.web import {SETTINGS} as _wn\nx = inspect.getsource(_wn)\n") == [2],
      "the guard knows getsource through an alias of the subpackage too (as _wn)")
# app.routes hides the routes of include_router (_IncludedRouter) - a test that walks it directly
# sees only a few addresses after the split and quietly guards nothing.
DIRECT = re.compile(r"\bapp\.(?:router\.)?routes\b")
direct = [f"{s.name}:{i}" for s in sorted(PROJECT.glob("tests/*.py")) if s.name not in EXAMPLE_FILES
          for i, line in enumerate(s.read_text(encoding="utf-8").splitlines(), 1)
          if DIRECT.search(line) and "flat_routes(" not in line]
check(not direct, f"the application's routes are walked through flat_routes ({direct})")

print()
print("--- (a) the door has everything read from the outside ---")
WEB_NAME = re.compile(r"(?<![\w.])_?web\.([A-Za-z_]\w*)")
FROM_WEB = re.compile(r"from\s+jellyscope\.web\s+import\s+\(?([^)\n]+)\)?")
used: dict[str, str] = {}
for path in outside_sources():
    text = path.read_text(encoding="utf-8")
    for name in WEB_NAME.findall(text):
        used.setdefault(name, path.name)
    for group in FROM_WEB.findall(text):
        for piece in group.split("#")[0].split(","):
            name = piece.split(" as ")[0].strip()
            if name:
                used.setdefault(name, path.name)
used.pop("py", None)                         # "web.py" in comments
through_door = sorted(f"{n} ({where})" for n, where in used.items() if n in ROOT_MODULES)
missing = sorted(f"{n} ({where})" for n, where in used.items()
                 if n not in ROOT_MODULES and n not in MODULES and not hasattr(web, n))
check(len(used) > 15 and not missing, f"all {len(used)} names go through the door (missing {missing[:6]})")
check(not through_door, f"nobody takes other modules through web - import the module directly ({through_door})")
# The other way round: a name the door takes from a submodule is read - in the door itself or from the outside.
# A re-export nobody reads only hides where the name lives, and (a) above would keep it there for ever.
door_tree = tree_of(PACKAGE / "__init__.py")
door_reads = {n.id for n in ast.walk(door_tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
re_exported = {alias.asname or alias.name for node in door_tree.body
               if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module for alias in node.names}
unread = sorted(name for name in re_exported if name not in door_reads and name not in used)
check(len(re_exported) > 15 and not unread,
      f"every name the door takes from a submodule is read in the door or from the outside ({len(unread)} unread: {unread})")

print()
print("--- (c) no cycles ---")
edges: dict[str, set[str]] = {m: set() for m in MODULES}
forbidden = []
for m in MODULES[1:]:
    if m == SETTINGS:
        continue                 # the subpackage has rules of its own (test_settings_package)
    # A subpackage (web/video/) is checked file by file: an import inside it
    # (`from .compare`) is its own business, `from ..base` is a dependency on
    # a neighbour like any other - one level deeper.
    depth = 1 if m in SUBPACKAGES else 0
    paths = sorted((PACKAGE / m).glob("*.py")) if m in SUBPACKAGES else [file_of(m)]
    for node in (n for path in paths for n in ast.walk(tree_of(path))):
        if isinstance(node, ast.ImportFrom) and 0 < node.level <= depth:
            continue
        if isinstance(node, ast.ImportFrom) and node.level > depth:
            node = ast.ImportFrom(module=node.module, names=node.names, level=node.level - depth)
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module is None:
                for a in node.names:
                    (edges[m].add(a.name) if a.name in MODULES else forbidden.append(f"{m}: from . import {a.name}"))
            elif node.level == 1:
                target = (node.module or "").split(".")[0]      # .music.details -> the subpackage music
                (edges[m].add(target) if target in MODULES
                 else forbidden.append(f"{m}: from .{node.module}"))
            elif node.level == 2 and node.module is None and any(a.name == "web" for a in node.names):
                forbidden.append(f"{m}: from .. import web")
            elif node.level == 0 and (node.module or "").startswith("jellyscope.web"):
                forbidden.append(f"{m}: from {node.module}")
        elif isinstance(node, ast.Import) and any(a.name.startswith("jellyscope.web") for a in node.names):
            forbidden.append(f"{m}: import jellyscope.web")
check(not forbidden and not any("__init__" in e for e in edges.values()),
      f"no submodule reaches for __init__ ({forbidden[:6]})")
left = {m: set(e) for m, e in edges.items()}
while any(not e for e in left.values()):
    done = {m for m, e in left.items() if not e}
    left = {m: e - done for m, e in left.items() if m not in done}
check(not left, f"dependencies without a cycle ({sorted(left)})")
check(not edges["base"] and edges["periods"] <= {"base"},
      f"base reaches for no neighbour, periods only for base ({edges['base']}, {edges['periods']})")
FLATTEN = """
def flat(routes):
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from flat(r.original_router.routes)
        else:
            yield r
"""
# Importing a submodule always runs the package's __init__ first (and that loads every submodule) -
# one check per package covers them all; devtools.py is another entry (a cycle through web.base).
if not DEV:
    print("       jellyscope/devtools.py is not here (prod, demo) - its import is skipped")
for name in ["jellyscope.web", *(["jellyscope.devtools"] if DEV else [])]:
    code = f"import {name}\n"
    if name == "jellyscope.devtools":
        code += FLATTEN + ("import jellyscope.web as w\n"
                           "print('DEV' if any(getattr(r, 'path', '') == '/settings/dev/event-date'"
                           " for r in flat(w.app.router.routes)) else 'NO DEV')\n")
    run = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=os.environ.copy(),
                         capture_output=True, text=True, timeout=180)
    error = (run.stderr.strip().splitlines() or [""])[-1]
    check(run.returncode == 0 and (name != "jellyscope.devtools" or run.stdout.strip().endswith("DEV")
                                   and "NO DEV" not in run.stdout),
          f"`import {name}` first in a clean process ({run.stdout.strip()[-20:]} {error[:120]})")

print()
print("--- (d) size ---")
# The whole tree of the web (subpackages included); the only exception is base.py - moved as it was.
large = [(str(p.relative_to(PACKAGE)), n) for p in sorted(PACKAGE.rglob("*.py")) if p != PACKAGE / "base.py"
         and (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
check(not large, f"the web's files up to 600 lines, except base.py ({large})")

print()
print("--- (e) tests substitute where it takes effect ---")
# A name read in a route module (outside __init__) is not reached by a substitute at the door.
# Who reads which name: the substitute has to be in the submodule where it is read.
readers: dict[str, set[str]] = {}
for m in ROUTE_MODULES[1:]:
    for name in {n.id for n in ast.walk(tree_of(file_of(m))) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
        readers.setdefault(name, set()).add(m)
for sample, expected in (("web.JellyfinClient = x", {"JellyfinClient"}),
                         ('mock.patch("jellyscope.web.JellyfinClient", x)', {"JellyfinClient"}),
                         (f"web_{SETTINGS}.JellyfinClient = x", set())):
    check(substitutes_at_door(sample, "web") == expected, f"(e) knows a substitute at the door: {sample!r}")
check(substitutes_in_submodule("web.library.JellyfinClient = x", "web", "JellyfinClient")
      and not substitutes_in_submodule("web.JellyfinClient = x", "web", "JellyfinClient"),
      "(e) knows a substitute in a submodule (web.library.X), a substitute at the door not")
# A substitute counts only in the submodule that really reads the name: /image takes
# JellyfinClient from the library module, a substitute in the account module would miss it.
check(substitutes_in_submodule("web.library.JellyfinClient = x", "web", "JellyfinClient", {"library"})
      and not substitutes_in_submodule("web.my_account.JellyfinClient = x", "web", "JellyfinClient", {"library"}),
      "(e) a substitute in the wrong submodule does not count")
check(substitutes_in_submodules("web.my_account.JellyfinClient = x\nmock.patch('jellyscope.web.network.geoip', y)", "web")
      == {("my_account", "JellyfinClient"), ("network", "geoip")}, "(e) knows substitutes in submodules")
wrong = []
for path in sorted(PROJECT.glob("tests/*.py")):
    if path.name in EXAMPLE_FILES:
        continue
    text = path.read_text(encoding="utf-8")
    wrong += [f"{path.name}: {problem}" for problem in wrong_substitutes(text, "web", readers)]
check(not wrong, f"names read in a submodule are substituted in the submodule ({wrong})")
# And the other way round: a substitute in a submodule that neither reads nor defines the name hits nothing.
names_in: dict[str, set[str]] = {}
for m in MODULES[1:]:
    tree = tree_of(file_of(m))
    names_in[m] = defined(tree) | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
astray = []
for path in sorted(PROJECT.glob("tests/*.py")):
    if path.name in EXAMPLE_FILES:
        continue
    for m, name in sorted(substitutes_in_submodules(path.read_text(encoding="utf-8"), "web")):
        if m in names_in and name not in names_in[m]:
            astray.append(f"{path.name}: web.{m}.{name}")
check(not astray, f"every substitute in a submodule aims where the name is read ({astray})")

print()
print("--- (g) routes, middleware, error handling, Jinja keys ---")
routes = list(flat_routes(web.app.router.routes))
table = sorted(f"MOUNT {r.path} {r.name}" if type(r).__name__ == "Mount"
               else f"{','.join(sorted(getattr(r, 'methods', None) or []))} {r.path} {r.name}" for r in routes)
expected_routes = [t for t in ROUTES if DEV or " /settings/dev/" not in t]
extra, missing_routes = sorted(set(table) - set(expected_routes)), sorted(set(expected_routes) - set(table))
check(table == expected_routes,
      f"all {len(expected_routes)} routes as the table says (extra {extra[:4]}, missing {missing_routes[:4]})")
shadowed = []
for r in routes:
    for method in sorted(getattr(r, "methods", None) or []):
        scope = {"type": "http", "path": re.sub(r"\{[^}]+\}", "x1", r.path), "method": method, "root_path": ""}
        others = [s.path for s in routes if s is not r and type(s).__name__ != "Mount"
                  and method in (getattr(s, "methods", None) or set()) and s.matches(scope)[0] == Match.FULL]
        if others:
            shadowed.append(f"{method} {r.path} ~ {others}")
check(not shadowed, f"no route shadows another ({shadowed[:4]})")
middleware = [(m.cls.__name__, getattr(m.kwargs.get("dispatch"), "__name__", "")) for m in web.app.user_middleware]
check(middleware == MIDDLEWARE, f"middleware in the same order ({middleware})")
handlers = sorted(f"{k.__module__}.{k.__name__}" for k in web.app.exception_handlers)
check(handlers == ERROR_HANDLERS, f"error handling the same ({handlers})")
own_globals = sorted(set(web.templates.env.globals) - set(jinja2.defaults.DEFAULT_NAMESPACE) - {"url_for"})
expected_globals = [g for g in OWN_GLOBALS if DEV or g != "devtools_date"]
check(own_globals == expected_globals,
      f"the keys of the Jinja globals as the table says ({sorted(set(own_globals) ^ set(expected_globals))})")
own_filters = sorted(set(web.templates.env.filters) - set(jinja2.defaults.DEFAULT_FILTERS))
check(own_filters == OWN_FILTERS, f"the keys of the Jinja filters as the table says ({sorted(set(own_filters) ^ set(OWN_FILTERS))})")

print()
print("--- (h) paths ---")
base = importlib.import_module("jellyscope.web.base")
templates_dir, static_dir = Path(base.TEMPLATES_DIR), Path(web.STATIC_DIR)
check(templates_dir == PROJECT / "jellyscope" / "templates" and templates_dir.is_dir(),
      f"templates in jellyscope/templates ({templates_dir})")
check(static_dir == PROJECT / "jellyscope" / "static" and static_dir.is_dir(),
      f"static files in jellyscope/static ({static_dir})")

print()
print("--- (i) loggers ---")
# Every call, not one per file - a second getLogger would otherwise beat the first.
loggers = [(p.name, v) for p in sorted(PACKAGE.rglob("*.py"))
           for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
wrong_loggers = [(s, v) for s, v in loggers if v != '"jellyscope.web"']
check(loggers and not wrong_loggers,
      f"the package logs as jellyscope.web, not under a file's name ({wrong_loggers or len(loggers)})")

print()
print("--- (j) the shared context of every page ---")
# The keys _context() hands to every template (the menu, permissions, the account, today, the limits of
# long tables). Templates read them by these names, so a rename has to go to both sides at once.
from starlette.requests import Request  # noqa: E402

from jellyscope import db as _db  # noqa: E402
from jellyscope.web import base as web_base_module  # noqa: E402

_db.init_db()
_request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [],
                    "session": {}, "app": web.app, "router": web.app.router})
shared = set(web_base_module._context(_request, None))
BASE_CONTEXT = {"collector_status", "collector_error", "last_poll", "flash", "active_count", "account", "own_viewer",
                "is_admin", "account_image", "may_open", "menu", "menu_title", "sees", "ui_language",
                "server_language", "today", "today_text", "quick_periods", "wait_for", "table_limits", "ui_self_refresh"}
check(BASE_CONTEXT <= shared, f"_context() gives every template these keys (missing {sorted(BASE_CONTEXT - shared)})")
old_czech = {"muj_divak", "je_spravce", "obrazek_uctu", "smi", "vidi", "jazyk_serveru", "dnes", "dnes_text",
             "rychla_obdobi", "stropy_tabulek"}
check(not (old_czech & shared), f"and none of the old Czech keys ({sorted(old_czech & shared)})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
