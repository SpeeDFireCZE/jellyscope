# -*- coding: utf-8 -*-
r"""The rules of the subpackage web/settings/ - the routes of the Settings page by areas.

Every file with routes has its own `router` and `settings/__init__.py` joins them into one
`router` the web attaches. The door exposes every name, so tests read the subpackage as
`from jellyscope.web import settings as web_settings`. The test guards:

(a) the door has everything read from the outside (`web_settings.X`, `web.settings.X`,
    `from jellyscope.web.settings import X`);
(b) the map names every module of the subpackage, the door points at the very objects of
    the files and `settings.router` has all the routes;
(c) no cycles and the right number of dots in relative imports (`..base`, `...` = the
    package jellyscope); the subpackage imports first in a clean process; the door imports
    no root module named like one of its files;
(d) no file over 600 lines;
(e) tests substitute where it takes effect (through the alias web_settings too);
(f) the subpackage has no logger of its own (it logs through `log` of web/base.py).

Spec: docs/superpowers/specs/2026-11-06-nastaveni-balik-design.md (the split),
docs/superpowers/specs/2026-11-16-english-code-design.md (the names)

Run:
    .venv/bin/python tests/test_settings_package.py
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import jinja2.nodes as jinja2_nodes

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "settings_package.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _package import substitutes_in_submodules, substitutes_at_door, flat_routes, wrong_substitutes  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


PACKAGE = PROJECT / "jellyscope" / "web" / "settings"
FULL_NAME = "jellyscope.web.settings"
EXAMPLE_FILES = {Path(__file__).name, "test_stats_package.py", "test_web_package.py", "test_scanner_package.py",
                 "test_importers_package.py", "_package.py"}

# The files of the subpackage.
SUBMODULES = ["common", "page", "accounts", "connection", "notifications", "interface", "updates", "tasks", "arr",
              "seerr", "history"]
WITH_ROUTES = [m for m in SUBMODULES if m != "common"]
# The dependency graph: who may reach for whom.
MAY = {"common": set(), "page": {"common", "accounts"}, "accounts": set(), "connection": {"common"},
       "notifications": {"common"}, "interface": {"common"}, "updates": set(), "tasks": set(),
       "history": {"common"}, "arr": {"common"}, "seerr": {"common"}}
ROUTES = 53   # 46 of the page + three routes /settings/arr/{app}… (*Arr) + three /settings/seerr… (Seerr)
              # + closing the stripe about the backup before the conversion (tasks)


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


def tree_of(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def read_names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


ALIAS = re.compile(r"(?:from\s+jellyscope\.web\s+import\s+settings|import\s+jellyscope\.web\.settings)\s+as\s+(\w+)")
BARE = re.compile(r"from\s+jellyscope\.web\s+import\s+(?:[\w ,]*,\s*)?settings\b(?!\s+as\b)")


def prefixes(text: str) -> list[str]:
    """The names a test knows the subpackage by, as regular expressions: web_settings, the dotted
    `web.settings`, its own aliases (`as _wn`), and `settings` only when the test imports it under
    that bare name (many modules are called settings)."""
    return sorted({"web_settings", r"web\.settings", *ALIAS.findall(text), *(["settings"] if BARE.search(text) else [])})


def has_name(module: str, name: str) -> bool:
    """Has the module the attribute `name`, or (a package) a submodule of that name?"""
    m = importlib.import_module(module)
    if hasattr(m, name):
        return True
    return hasattr(m, "__path__") and importlib.util.find_spec(f"{module}.{name}") is not None


def imports_of_file(tree: ast.Module, m: str) -> tuple[list[str], set[str]]:
    """The wrong imports of a file of the subpackage (nested in a function too) and its dependencies inside it.

    Every import is turned into its target module - counting dots is not enough:
    `from ...base` (one dot too many) would pass and fail only in a rare branch. The
    target has to exist and must not be the web or another part of it than base and
    the subpackage itself.
    """
    forbidden: list[str] = []
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [(a.name, None) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            target_base = (importlib.util.resolve_name("." * node.level + (node.module or ""), FULL_NAME)
                           if node.level else node.module)
            targets = [(target_base, a.name) for a in node.names]
        else:
            continue
        for target, name in targets:
            if target != "jellyscope" and not target.startswith("jellyscope."):
                continue                 # stdlib, fastapi...
            # `from ... import web` aims at the submodule web, not at a name in the package
            # jellyscope - so the target is that submodule (and is guarded as such).
            if (name and importlib.util.find_spec(target) is not None
                    and hasattr(importlib.import_module(target), "__path__")
                    and importlib.util.find_spec(f"{target}.{name}") is not None):
                target, name = f"{target}.{name}", None
            if target == "jellyscope.web" or (target.startswith("jellyscope.web.") and target != "jellyscope.web.base"
                                              and not target.startswith(FULL_NAME)):
                forbidden.append(f"{m}: {target} (only web.base and the subpackage)")
            elif importlib.util.find_spec(target) is None:
                forbidden.append(f"{m}: {target} does not exist")
            elif name and not has_name(target, name):
                forbidden.append(f"{m}: {target} has no {name}")
            if target.startswith(FULL_NAME + "."):
                edges.add(target.rsplit(".", 1)[1])
            elif target == FULL_NAME and name in SUBMODULES:
                edges.add(name)
    return forbidden, edges


def routes_of(r) -> list[str]:
    return sorted(f"{','.join(sorted(getattr(x, 'methods', None) or []))} {x.path}" for x in flat_routes(r.routes))


print("--- (b) the map names every module, the router has every route ---")
present = {p.stem for p in PACKAGE.glob("*.py")} - {"__init__"}
check(present == set(SUBMODULES) and len(SUBMODULES) == len(set(SUBMODULES)),
      f"the map has exactly the files of the subpackage (missing {sorted(set(SUBMODULES) - present)}, extra {sorted(present - set(SUBMODULES))})")
if present != set(SUBMODULES):
    print()
    print(f"DONE - failures: {failures}")
    sys.exit(1)

settings = importlib.import_module(FULL_NAME)
check(len(routes_of(settings.router)) == ROUTES, f"settings.router has all {ROUTES} routes ({len(routes_of(settings.router))})")
mismatched = []
exported = 0
for m in SUBMODULES:
    module = importlib.import_module(f"{FULL_NAME}.{m}")
    for name in defined(tree_of(PACKAGE / f"{m}.py")):
        if name == "router" or name in SUBMODULES or not hasattr(settings, name):
            continue                 # every file has a router of its own; a submodule's name is the submodule
        exported += 1
        if getattr(settings, name) is not getattr(module, name):
            mismatched.append(f"{m}.{name}")
check(exported > 40 and not mismatched, f"the door points at the same objects as the files ({exported} names; {mismatched[:6]})")
# __all__ holds names as strings - a rename does not reach them by itself.
stale = sorted(n for n in settings.__all__ if not hasattr(settings, n))
check(len(settings.__all__) > 40 and not stale and list(settings.__all__) == sorted(settings.__all__),
      f"every name of __all__ exists and the list is sorted (missing {stale[:6]})")
jellyfin = importlib.import_module("jellyscope.jellyfin")
check(getattr(settings, "JellyfinClient", None) is jellyfin.JellyfinClient,
      "web_settings.JellyfinClient is the client of jellyfin.py (tests substitute it)")
from_files = sorted(r for m in WITH_ROUTES for r in routes_of(importlib.import_module(f"{FULL_NAME}.{m}").router))
check(from_files == routes_of(settings.router), "settings.router joins exactly the routes of the files")

print()
print("--- (a) the door has everything read from the outside ---")
PATTERNS = (re.compile(r"(?<![\w.])web_settings\.([A-Za-z_]\w*)"),
            re.compile(r"(?<![\w.])(?:jellyscope\.)?web\.settings\.([A-Za-z_]\w*)"))
FROM = re.compile(r"from\s+jellyscope\.web\.settings\s+import\s+\(?([^)\n]+)\)?")
used: dict[str, str] = {}
for path in sorted([*PROJECT.glob("jellyscope/**/*.py"), *PROJECT.glob("tests/*.py"),
                    *(PROJECT / j for j in ("demo.py", "run.py", "manage.py"))]):
    if not path.is_file() or PACKAGE in path.parents or path.name in EXAMPLE_FILES:
        continue
    text = path.read_text(encoding="utf-8")
    patterns = [*PATTERNS, *(re.compile(rf"(?<![\w.]){re.escape(a)}\.([A-Za-z_]\w*)") for a in ALIAS.findall(text))]
    for pattern in patterns:
        for name in pattern.findall(text):
            used.setdefault(name, path.name)
    for group in FROM.findall(text):
        for piece in group.split("#")[0].split(","):
            if piece.strip():
                used.setdefault(piece.split(" as ")[0].strip(), path.name)
used.pop("py", None)
missing = sorted(f"{n} ({where})" for n, where in used.items() if n not in SUBMODULES and not hasattr(settings, n))
check(len(used) > 5 and not missing, f"all {len(used)} names go through the door (missing {missing[:6]})")

print()
print("--- (c) no cycles, relative imports with the right number of dots ---")
# Every import (nested in a function too) is turned into its target module - counting
# dots is not enough: `from ...base` (one dot too many) would pass and fail only
# in a rare branch. The target has to exist and must not be the web or another part of it
# than base and the subpackage itself.
edges: dict[str, set[str]] = {m: set() for m in SUBMODULES}
forbidden = []
for m in SUBMODULES:
    problems, edges[m] = imports_of_file(tree_of(PACKAGE / f"{m}.py"), m)
    forbidden += problems
# A check of the check: the samples show what is to pass and what is not.
for sample, passes in (("def f():\n    from ... import web\n", False),
                       ("def f():\n    from ... import notifications_typo\n", False),
                       ("def f():\n    from ...base import _flash\n", False),
                       ("from jellyscope.web import app\n", False),
                       ("def f():\n    from ... import notifications\n", True),
                       ("from ..base import _flash\n", True),
                       ("from .. import base\n", True)):
    check((not imports_of_file(ast.parse(sample), "accounts")[0]) == passes,
          f"the import check {'lets through' if passes else 'catches'}: {sample.strip().splitlines()[-1].strip()}")
check(not forbidden, f"the imports aim at existing allowed modules ({forbidden[:6]})")
beyond = {m: sorted(edges[m] - MAY[m]) for m in SUBMODULES if edges[m] - MAY[m]}
check(not beyond, f"dependencies by the graph (beyond it {beyond})")
# The door imports its files by name (`from . import accounts, tasks…`): a root module of the same
# name imported there too would be shadowed, or would shadow the file.
ROOT_MODULES = ({p.stem for p in (PROJECT / "jellyscope").glob("*.py")}       # top level only: the names of the modules
                | {p.parent.name for p in (PROJECT / "jellyscope").glob("*/__init__.py")})
door_imports = []
for node in ast.walk(tree_of(PACKAGE / "__init__.py")):
    if isinstance(node, ast.ImportFrom) and node.level >= 2 and node.module is None:
        door_imports += [a.asname or a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "jellyscope":
        door_imports += [a.asname or a.name for a in node.names]
clashes = sorted(set(door_imports) & set(SUBMODULES))
check(len(set(SUBMODULES) & ROOT_MODULES) >= 4 and not clashes,
      f"the door imports no root module named like one of its files ({clashes})")
# Importing a submodule always runs the package's __init__ first (and that loads every
# submodule) - one check per package covers them all.
run = subprocess.run([sys.executable, "-c", f"import {FULL_NAME}"], cwd=PROJECT, env=os.environ.copy(),
                     capture_output=True, text=True, timeout=180)
error = (run.stderr.strip().splitlines() or [""])[-1]
check(run.returncode == 0, f"`import {FULL_NAME}` first in a clean process ({error[:120]})")

print()
print("--- (d) size ---")
large = [(p.name, n) for p in sorted(PACKAGE.glob("*.py")) if (n := len(p.read_text(encoding="utf-8").splitlines())) > 600]
check(not large, f"no file over 600 lines ({large})")

print()
print("--- (e) tests substitute where it takes effect ---")
readers: dict[str, set[str]] = {}
for m in SUBMODULES:
    for name in read_names(tree_of(PACKAGE / f"{m}.py")):
        readers.setdefault(name, set()).add(m)
check(substitutes_at_door("updates.update, web_settings._schedule_restart = a, b", "web_settings")
      == {"_schedule_restart"}, "(e) a substitute in a pair is recognised")
check(prefixes("from jellyscope.web import settings as _wn\n") == ["_wn", r"web\.settings", "web_settings"]
      and prefixes("from jellyscope.web import app, settings\n") == ["settings", r"web\.settings", "web_settings"]
      and prefixes("from jellyscope.arr.sonarr import settings\n") == [r"web\.settings", "web_settings"],
      "(e) the subpackage's names in a test: an alias, the bare name only when imported from the web")
check(any(substitutes_in_submodules("web.settings.connection.JellyfinClient = Fake\n", prefix) == {("connection", "JellyfinClient")}
          for prefix in prefixes("import jellyscope.web as web\n"))
      and any(substitutes_at_door("web.settings._schedule_restart = f\n", prefix) == {"_schedule_restart"}
              for prefix in prefixes("import jellyscope.web as web\n")),
      "(e) a substitute written through web.settings (a file's name, the door) is seen too")
names_in = {m: defined(tree_of(PACKAGE / f"{m}.py")) | read_names(tree_of(PACKAGE / f"{m}.py")) for m in SUBMODULES}
wrong, astray = [], []
for path in sorted(PROJECT.glob("tests/*.py")):
    if path.name in EXAMPLE_FILES:
        continue
    text = path.read_text(encoding="utf-8")
    for prefix in prefixes(text):
        wrong += [f"{path.name}: {problem}" for problem in wrong_substitutes(text, prefix, readers)]
        for m, name in sorted(substitutes_in_submodules(text, prefix)):
            if m in names_in and name not in names_in[m]:
                astray.append(f"{path.name}: {prefix.replace(chr(92), '')}.{m}.{name}")
check(not wrong, f"names read in the files are substituted through substitute or in every reader ({wrong})")
check(not astray, f"every substitute in a file aims where the name is read ({astray})")

print()
print("--- (f) logger ---")
loggers = [(p.name, v) for p in sorted(PACKAGE.glob("*.py"))
           for v in re.findall(r"getLogger\(([^)]*)\)", p.read_text(encoding="utf-8"))]
check(not loggers, f"the subpackage has no logger of its own, it logs through log of web/base.py ({loggers})")

print()
print("--- (s) every name a section's template reads, the Settings route gives ---")
# A section template (settings/<key>.html) is included by settings.html and reads the context the route put
# together: the shared _context(), the page's own keys and its section's helper (_section_<key>). A key
# renamed on one side only would render empty without an error - so every section is rendered here as
# an admin, its context captured, and the template's free names compared with it.
from fastapi.testclient import TestClient  # noqa: E402
from jinja2 import meta  # noqa: E402

from jellyscope import accounts, db  # noqa: E402
from jellyscope.web import app  # noqa: E402
from jellyscope.web import base as web_base_module  # noqa: E402
from jellyscope.web.settings import page as settings_page_module  # noqa: E402

db.init_db()
accounts.create("spravce_s", "longpassword", "longpassword", is_admin=True)
env = web_base_module.templates.env
templates_dir = PROJECT / "jellyscope" / "templates"
frame = env.parse((templates_dir / "settings.html").read_text(encoding="utf-8"))
frame_names = {n.name for n in frame.find_all(jinja2_nodes.Name) if n.ctx in ("store", "param")}
captured: dict[str, set[str]] = {}
_original_response = web_base_module.templates.TemplateResponse


def _capture(request, name, *args, **kwargs):
    context = args[0] if args else kwargs.get("context", {})
    captured.setdefault(name, set()).update(context)
    return _original_response(request, name, *args, **kwargs)


# Globals only devtools.py registers (dev only, DEV_ONLY of the prod sync): read under `is defined` in prod.
DEV_ONLY_GLOBALS = set() if (PROJECT / "jellyscope" / "devtools.py").is_file() else {"devtools_date"}


def unprovided(template_text: str, provided: set[str]) -> list[str]:
    """Free names of a section template that neither the route nor the page frame nor Jinja gives."""
    tree = env.parse(template_text)
    bound = {n.name for n in tree.find_all(jinja2_nodes.Name) if n.ctx in ("store", "param")}
    return sorted(meta.find_undeclared_variables(tree) - provided - bound - frame_names - set(env.globals)
                  - DEV_ONLY_GLOBALS)


# Sections with more than one view: the log shows the app's log or the API log (the address parameter
# log_kind); the template reads the keys of both.
SECTION_VIEWS = {"log": ["", "&log_kind=api"]}


def section_context(client: TestClient, key: str) -> set[str]:
    keys = {"request"}
    for view in SECTION_VIEWS.get(key, [""]):
        captured.clear()
        client.get(f"/settings?section={key}{view}")
        keys |= captured.get("settings.html", set())
    return keys


web_base_module.templates.TemplateResponse = _capture
try:
    with TestClient(app) as client:
        client.post("/login", data={"username": "spravce_s", "password": "longpassword"}, follow_redirects=False)
        missing = {}
        for key, _label, _admin_only in web_base_module.SETTINGS_SECTIONS:
            path = templates_dir / "settings" / f"{key}.html"
            lost = unprovided(path.read_text(encoding="utf-8"), section_context(client, key))
            if lost:
                missing[key] = lost
        check(not missing, f"every name a section template reads is in its context ({missing})")
        # Trap: a section helper that hands its template a renamed key is found.
        original_helper = settings_page_module._section_blocks
        settings_page_module._section_blocks = lambda: {
            ("renamed_" + k if i == 0 else k): v for i, (k, v) in enumerate(original_helper().items())}
        try:
            lost = unprovided((templates_dir / "settings" / "blocks.html").read_text(encoding="utf-8"),
                              section_context(client, "blocks"))
        finally:
            settings_page_module._section_blocks = original_helper
        check(bool(lost), f"trap: a key renamed in the section helper only is found ({lost})")
finally:
    web_base_module.templates.TemplateResponse = _original_response

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
