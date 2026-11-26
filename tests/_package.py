"""Shared checks for packages with a door (stats/, web/, scanner/, importers/).

A test that substitutes a name at the door (`stats.X = replacement`) changes only the name at the
door. Code inside the package takes the name from its own submodule and never sees the
replacement - the test would quietly test something else than it thinks. The functions here spot a
substitute at the door and in a submodule however it is written: an assignment (`stats.X =`, also
with a prefix `api.`, `cards.`, `web.`...), setattr(...stats, "X", ...) and mock.patch /
patch.object.

The check (e) of the package tests uses them (`tests/test_stats_package.py`, `test_web_package.py`,
`test_scanner_package.py`, `test_importers_package.py`): `wrong_substitutes` is the whole rule,
`substitute` replaces one assignment for tests that substitute a name read by several submodules,
`flat_routes` unfolds the routes of include_router. Their check (a) asks `read_through_door` which
names the outside reads through a door, and `unread_door_names` which re-exported names nobody does.
The file starts with an underscore, so the suite does not run it as a test.
"""
from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType


def _prefix(package: str) -> str:
    """The package's name with any module prefix (`stats`, `api.stats`, `jellyscope.web`)."""
    return rf"(?<![\w.])(?:\w+\.)*{package}"


def substitute(package: ModuleType, name: str, value: object) -> Callable[[], None]:
    """Substitute a name at the package's door and in all its submodules.

    Before the split into a package one assignment was enough (`scanner.X = replacement`): all the
    code lived in one file and read the name from one place. After the split every submodule holds
    the name itself, so it must be substituted in every one that has it - exactly what this does.
    Returns a function that puts everything back (a test that does not restore drops it).

    Takes only submodules loaded already (`sys.modules`) - the package loads them itself as soon as
    its `__init__` is imported.
    """
    originals: list[tuple[ModuleType, object]] = []
    for module_name, module in list(sys.modules.items()):
        if module is not None and (module_name == package.__name__ or module_name.startswith(package.__name__ + ".")) \
                and hasattr(module, name):
            originals.append((module, getattr(module, name)))
            setattr(module, name, value)
    if not originals:
        # A typo in the name would otherwise substitute nothing quietly and the test would run
        # with the real code (the real Jellyfin client, say).
        raise AttributeError(f"neither {package.__name__} nor its submodules have {name!r}")

    def restore() -> None:
        for module, value_before in originals:
            setattr(module, name, value_before)
    return restore


def substitutes_at_door(text: str, package: str) -> set[str]:
    """Names the text substitutes at the door (<package>.X)."""
    prefix = _prefix(package)
    return (set(re.findall(prefix + r"\.(\w+)\s*=(?!=)", text))
            | set(re.findall(r"setattr\(\s*" + prefix + r"\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(r"patch\.object\(\s*" + prefix + r"\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(rf"patch\(\s*[\"']jellyscope\.{package}\.(\w+)[\"']", text)))


def wrong_substitutes(text: str, package: str, readers: dict[str, set[str]]) -> list[str]:
    """Substitutes at the door (<package>.X = ...) that do not reach the submodules' code.

    A name some submodule reads may be substituted by a plain assignment at the door only when it is
    substituted in **all** the submodules that read it too - otherwise part of the code keeps the
    original. The whole text is judged: `substitute()` elsewhere in the file does not excuse a plain
    assignment (that would switch only the door and the submodules would keep the previous
    substitute). The simplest is to call `substitute(package, "X", replacement)` instead.
    """
    errors = []
    for name in sorted(substitutes_at_door(text, package) & set(readers)):
        missing = [m for m in sorted(readers[name]) if not substitutes_in_submodule(text, package, name, {m})]
        if missing:
            errors.append(f"{package}.{name} (no substitute in {missing}; use substitute)")
    return errors


def substitutes_in_submodules(text: str, package: str) -> set[tuple[str, str]]:
    """Pairs (submodule, name) the text substitutes in submodules (<package>.<submodule>.X).

    A substitute in a submodule that neither reads nor defines the name reaches nothing - check (e)
    therefore makes sure every pair points where the name is read."""
    prefix = _prefix(package)
    return (set(re.findall(prefix + r"\.(\w+)\.(\w+)\s*=(?!=)", text))
            | set(re.findall(r"setattr\(\s*" + prefix + r"\.(\w+)\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(r"patch\.object\(\s*" + prefix + r"\.(\w+)\s*,\s*[\"'](\w+)[\"']", text))
            | set(re.findall(rf"patch\(\s*[\"']jellyscope\.{package}\.(\w+)\.(\w+)[\"']", text)))


def flat_routes(routes):
    """Every route of the application, also those inside include_router.

    FastAPI 0.162 keeps routes added through `app.include_router` as one `_IncludedRouter` without
    a path - whoever walks `app.routes` directly sees only a few paths of a web split into a
    package and quietly checks nothing.
    """
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            yield from flat_routes(route.original_router.routes)
        else:
            yield route


def substitutes_in_submodule(text: str, package: str, name: str,
                             submodules: Iterable[str] | None = None) -> bool:
    """Does the text substitute the name in a submodule too (<package>.<submodule>.X)?

    With `submodules` only a substitute in one of them counts - in the one that really reads the
    name. A substitute in another submodule does not reach the code (each module reads the name
    from its own namespace) and the test would quietly test something else.
    """
    prefix = _prefix(package)
    where = "(?:" + "|".join(sorted(map(re.escape, submodules))) + ")" if submodules else r"\w+"
    return any(re.search(pattern, text) for pattern in (
        prefix + rf"\.{where}\.{name}\s*=(?!=)",
        r"setattr\(\s*" + prefix + rf"\.{where}\s*,\s*[\"']{name}[\"']",
        r"patch\.object\(\s*" + prefix + rf"\.{where}\s*,\s*[\"']{name}[\"']",
        rf"patch\(\s*[\"']jellyscope\.{package}\.{where}\.{name}[\"']"))


def sample_movie(item_id: str = "film-zkusebni") -> None:
    """One movie in the library, for a test over a database without demo data.

    Since the menu by sections (16. 11. 2026) the section "Movies & series" exists only while
    the library has a movie or an episode (navigation.has_video): without one its pages
    (/video/overview, /video/insights, ...) answer 404 and / opens another page. A test of
    those pages over an empty database needs this one row.
    """
    from jellyscope import db

    with db.connect() as conn:
        conn.execute("INSERT INTO items (id, name, type) VALUES (?, ?, 'Movie') ON CONFLICT (id) DO NOTHING",
                     (item_id, "Zkušební film"))


def _module_of(path: Path, project: Path) -> str:
    """The dotted module of a file under jellyscope/ ("" for a test or a script outside it)."""
    try:
        parts = list(path.relative_to(project).with_suffix("").parts)
    except ValueError:
        return ""
    if not parts or parts[0] != "jellyscope":
        return ""
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imported_module(path: Path, project: Path, node: ast.ImportFrom) -> str:
    """The module an `ImportFrom` imports from, relative imports resolved."""
    if not node.level:
        return node.module or ""
    module = _module_of(path, project)
    base = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    base = base[:len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def read_through_door(package: str, sources: Mapping[Path, str], project: Path) -> set[str]:
    """The names the sources read through the door of `package` ("jellyscope.stats").

    Seen are `from <package> import X` (relative too), `<alias>.X` for every name the package goes by
    (`from jellyscope import stats as _stats`, `from .. import stats`, `import jellyscope.stats as s`),
    a name handed as a string with the package (`substitute(stats, "X", …)`, `getattr(stats, "X")`) and
    the dotted text `jellyscope.stats.X` (`mock.patch`, `import jellyscope.stats` and its attribute
    chain). A submodule read through the door (`stats.kinds.X`) reads the submodule, not X.
    """
    parent, _, last = package.rpartition(".")
    dotted = re.compile(re.escape(package) + r"\.(\w+)")
    reads: set[str] = set()
    for path, text in sources.items():
        tree = ast.parse(text)
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = _imported_module(path, project, node)
                for alias in node.names:
                    if target == package:
                        reads.add(alias.name)
                    elif target == parent and alias.name == last:
                        aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                aliases |= {alias.asname for alias in node.names if alias.name == package and alias.asname}
        reads |= set(dotted.findall(text))
        if not aliases:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                reads.add(node.attr)
            elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Name)
                  and node.args[0].id in aliases):
                reads |= {arg.value for arg in node.args[1:] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
    return reads


def outside_sources(project: Path, package_folder: Path, examples: Iterable[str] = ()) -> dict[Path, str]:
    """The code, the scripts and the tests outside a package - those that read it through its door.

    `examples` names files whose text holds made-up reads (the package tests' samples) - they are left out."""
    paths = [*project.glob("jellyscope/**/*.py"), *project.glob("tests/*.py"), *project.glob("deploy/*.py"),
             *(project / name for name in ("demo.py", "run.py", "manage.py"))]
    skipped = set(examples)
    return {path: path.read_text(encoding="utf-8") for path in sorted(paths)
            if path.is_file() and package_folder not in path.parents and "__pycache__" not in path.parts
            and path.name not in skipped}


def unread_door_names(door: Path, reads: Iterable[str]) -> tuple[set[str], list[str]]:
    """(the names the door takes from its submodules, those of them nobody reads - in the door or outside).

    A re-export nobody reads only hides where the name lives, and a check that the door has
    everything read from the outside would keep it there for ever.
    """
    tree = ast.parse(door.read_text(encoding="utf-8"))
    in_door = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    taken = {alias.asname or alias.name for node in tree.body
             if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module for alias in node.names}
    return taken, sorted(taken - in_door - set(reads))
