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
`flat_routes` unfolds the routes of include_router. The file starts with an underscore, so the
suite does not run it as a test.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable
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
