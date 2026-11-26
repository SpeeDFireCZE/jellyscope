# -*- coding: utf-8 -*-
r"""Guard of the templates: references between templates, macros and comments.

Jinja checks a template only when it renders it, and a reference that points nowhere breaks only
the branch that renders it - an error page, an empty library, a viewer's page. A test of the pages
would pass. This guard reads every template of jellyscope/templates with the application's own
Jinja environment and checks, without rendering:

  (a) every template compiles;
  (b) every literal target of include / extends / import / from exists;
  (c) every template named in Python exists: a string "<name>.html" in jellyscope/, the template
      of every card (cards.ALL_CARDS), of every *Arr app (arr.registry.APPS) and of every library
      kind (web.library_kinds.KINDS);
  (d) every template is used: a literal target in a template or a name from (c);
  (e) `from "x" import a, b`: x defines the macros a and b, and the template uses both - a copied
      header must not hide which macros a template needs; every macro is called somewhere;
  (f) a call of a macro passes only its parameters (keywords) and no more positional arguments
      than it has (unless the macro reads `kwargs` / `varargs`);
  (g) the comments of the templates in ENGLISH carry no Czech (the rule of test_english_code:
      a Czech letter or a common Czech word outside quotes);
  (h) English file names: no template is named _sekce_* or lies in nastaveni/, every card's
      template is _card_*;
  (i) every name a template reads has a source: bound in a template, a Jinja global, or a name of
      the Python code (a string constant or a keyword) - a context key renamed in Python but not
      in a template is caught here when its old name is gone from the code;
  (k) every name a template comment cites in backticks exists in the code or the templates - a
      comment that documents a template's context ("Expects `x`", `d.rows`) cannot keep a renamed key;
      each part of a dotted name counts, and the maps of old names (OLD_NAME_MAPS) do not make a name known;
  (l) every `card.<name>` a template reads is a field of cards.Card - templates call a card `card`
      everywhere, and Jinja draws a missing attribute as an empty string, so a field renamed in Python
      but not in a template would only show as an empty spot on a page.

Traps: every check runs once more over a temporary folder with planted faults and has to report
each of them - a check that cannot fail would pass over anything.

Run:
    .venv/bin/python tests/test_templates.py
"""
from __future__ import annotations

import ast
import dataclasses
import fnmatch
import os
import re
import sys
import tempfile
from pathlib import Path

from jinja2 import FileSystemLoader, nodes

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "templates.db")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["JELLYSCOPE_DEMO"] = "0"

import jellyscope.web  # noqa: E402,F401 - registers the filters and globals of the environment
from jellyscope import cards  # noqa: E402
from jellyscope.arr import registry  # noqa: E402
from jellyscope.web import base, library_kinds  # noqa: E402

TEMPLATES = Path(base.TEMPLATES_DIR)
ENV = base.templates.env

# Templates whose comments are in English (fnmatch patterns over the name in the folder): all of them
# since part 10b1 of the English-code step - a new template is checked from its first commit.
ENGLISH: list[str] = ["*"]

# Cards drawn by a template that is a part of a page as well (the page includes it on its own).
CARD_TEMPLATES_OF_PAGES = {"_now_playing.html", "_recently_added.html", "_network_live.html",
                           "video/_daily_card.html", "video/_top_items.html"}

# Templates only in dev (DEV_ONLY of deploy/sync-do-produ.py): the prod tree does not have them, so a literal
# include of one is no error there - it stands under `{% if devtools_date is defined %}` and is never reached.
# In dev the set is compared with DEV_ONLY, and every include of such a template has to stand under that guard.
DEV = (PROJECT / "jellyscope" / "devtools.py").is_file()
DEV_ONLY_TEMPLATES = {"settings/_devtools_events.html"}
DEV_GUARD = "{% if devtools_date is defined %}"
# The Jinja globals only devtools.py registers (dev only): in prod a template reads them under the guard above.
DEV_ONLY_GLOBALS = {"devtools_date"}

# Templates nothing refers to, each with the reason it stays.
UNUSED_TEMPLATES: dict[str, str] = {}
# Macros nothing calls, as "template:macro", each with the reason it stays.
UNUSED_MACROS: dict[str, str] = {}

# The same rule as test_english_code.
CZECH_LETTERS = re.compile("[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")
CZECH_WORDS = re.compile(r"\b(je|se|ze|aby|kdyz|protoze|neni|jsou|nebo|jen|jak|tak)\b", re.IGNORECASE)
QUOTED = re.compile('"[^"\n]*"|`[^`\n]*`|„[^“\n]*“')
COMMENT = re.compile(r"\{#-?(.*?)-?#\}", re.DOTALL)
TEMPLATE_NAME = re.compile(r"^[\w./-]+\.html$")

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def sources(root: Path) -> dict[str, str]:
    """{name in the folder: source} of every template under root."""
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.html"))}


def parse(texts: dict[str, str]) -> tuple[dict[str, nodes.Template], list[str]]:
    """Syntax trees of the templates, and the ones that do not parse."""
    trees, errors = {}, []
    for name, text in texts.items():
        try:
            trees[name] = ENV.parse(text, name=name)
        except Exception as error:  # noqa: BLE001 - every kind of fault is a finding
            errors.append(f"{name}: {error}")
    return trees, errors


def compile_errors(root: Path, names: list[str]) -> list[str]:
    """(a) The templates the environment cannot compile (an unknown filter, a broken tag)."""
    env = ENV.overlay(loader=FileSystemLoader(str(root)))
    errors = []
    for name in names:
        try:
            env.get_template(name)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{name}: {error}")
    return errors


def literal_targets(tree: nodes.Template) -> set[str]:
    """The template names a template refers to by a literal (include, extends, import, from)."""
    found = set()
    for node in tree.find_all((nodes.Include, nodes.Extends, nodes.Import, nodes.FromImport)):
        expression = node.template
        for const in [expression, *expression.find_all(nodes.Const)]:
            if isinstance(const, nodes.Const) and isinstance(const.value, str):
                found.add(const.value)
    return found


def missing_targets(trees: dict[str, nodes.Template], allowed: set[str] = frozenset()) -> list[str]:
    """(b) Literal targets that are not templates of the folder (`allowed`: may be missing - dev-only ones in prod)."""
    return [f"{name} -> {target}" for name, tree in trees.items()
            for target in sorted(literal_targets(tree)) if target not in trees and target not in allowed]


def dev_only_listed(sync_script: str) -> set[str]:
    """The templates the DEV_ONLY set of the prod sync script names."""
    for node in ast.walk(ast.parse(sync_script)):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DEV_ONLY":
            prefix = "jellyscope/templates/"
            return {e[len(prefix):] for e in ast.literal_eval(node.value) if e.startswith(prefix)}
    return set()


def unguarded_dev_includes(texts: dict[str, str], dev_only: set[str]) -> list[str]:
    """Includes of a dev-only template that do not stand right after the dev guard on their line."""
    found = []
    for name, text in texts.items():
        for number, line in enumerate(text.splitlines(), 1):
            for target in dev_only:
                if f'"{target}"' in line and DEV_GUARD not in line.split(f'"{target}"')[0]:
                    found.append(f"{name}:{number} -> {target}")
    return found


def python_template_names(text: str) -> set[str]:
    """Template names written as a whole string in Python source ("x.html", "settings/log.html")."""
    return {node.value for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and TEMPLATE_NAME.match(node.value)}


def missing_named(in_python: dict[str, set[str]], from_registries: set[str], existing: set[str]) -> list[str]:
    """(c) Template names Python uses (strings: name -> files; registries) that are no template."""
    return sorted({f"{n} ({', '.join(sorted(where))})" for n, where in in_python.items() if n not in existing}
                  | {f"{n} (registry)" for n in from_registries if n not in existing})


def unused_templates(trees: dict[str, nodes.Template], named: set[str], allowed: dict[str, str]) -> list[str]:
    """(d) Templates no template refers to by a literal and no Python names (exceptions aside)."""
    used = set().union(*(literal_targets(tree) for tree in trees.values())) | named
    return sorted(set(trees) - used - set(allowed))


def czech_named(names: list[str], card_templates: list[str], page_cards: set[str]) -> tuple[list[str], list[str]]:
    """(h) Templates with a Czech name (_sekce_*, nastaveni/) and card templates not called _card_*."""
    czech = sorted(n for n in names if n.startswith(("_sekce_", "nastaveni/")))
    otherwise = sorted(n for n in set(card_templates) if not n.startswith("_card_") and n not in page_cards)
    return czech, otherwise


def registry_template_names() -> set[str]:
    """The templates the registries name: cards, *Arr apps (page and live slice), library kinds."""
    names = {card.template for card in cards.ALL_CARDS}
    for app in registry.APPS.values():
        names.add(app.template)
        if app.downloading_template:
            names.add(app.downloading_template)
    names |= {kind.summary_template for kind in library_kinds.KINDS.values()}
    return names


def macros_of(trees: dict[str, nodes.Template]) -> dict[tuple[str, str], nodes.Macro]:
    """{(template, macro name): macro} of every macro defined in the templates."""
    return {(name, macro.name): macro for name, tree in trees.items() for macro in tree.find_all(nodes.Macro)}


def macro_problems(trees: dict[str, nodes.Template], unused_allowed: dict[str, str]) -> list[str]:
    """(e) and (f): imports of macros that are not there, calls that do not fit, macros never called."""
    defined = macros_of(trees)
    problems, called = [], set()
    for name, tree in trees.items():
        imported: dict[str, tuple[str, str]] = {}
        for node in tree.find_all(nodes.FromImport):
            target = node.template.value if isinstance(node.template, nodes.Const) else None
            for entry in node.names:
                original, alias = (entry, entry) if isinstance(entry, str) else entry
                if target is not None and (target, original) not in defined:
                    problems.append(f"{name}: imports {original} from {target}, which does not define it")
                imported[alias] = (target, original)
        modules = {node.target: node.template.value for node in tree.find_all(nodes.Import)
                   if isinstance(node.template, nodes.Const)}
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node, nodes.Name):
                key = (name, call.node.name) if (name, call.node.name) in defined else imported.get(call.node.name)
            elif isinstance(call.node, nodes.Getattr) and isinstance(call.node.node, nodes.Name) \
                    and call.node.node.name in modules:
                key = (modules[call.node.node.name], call.node.attr)
            else:
                key = None
            if key is None or key not in defined:
                continue
            called.add(key)
            macro = defined[key]
            parameters = [argument.name for argument in macro.args]
            reads = {found.name for found in macro.find_all(nodes.Name)}
            if "kwargs" not in reads:
                for keyword in call.kwargs:
                    if keyword.key not in parameters:
                        problems.append(f"{name}: {key[1]}() has no parameter {keyword.key} ({key[0]})")
            if "varargs" not in reads and len(call.args) > len(parameters):
                problems.append(f"{name}: {key[1]}() takes {len(parameters)} arguments, gets {len(call.args)}")
    for (template, macro_name) in sorted(set(defined) - called):
        if f"{template}:{macro_name}" not in unused_allowed:
            problems.append(f"{template}: the macro {macro_name} is never called")
    return problems


def unused_imports(trees: dict[str, nodes.Template]) -> list[str]:
    """(e) Macros a template imports (`from "x" import a`) but never uses, as "template: a"."""
    found = []
    for name, tree in trees.items():
        used = {node.name for node in tree.find_all(nodes.Name) if node.ctx == "load"}
        for node in tree.find_all(nodes.FromImport):
            for entry in node.names:
                alias = entry if isinstance(entry, str) else entry[1]
                if alias not in used:
                    found.append(f"{name}: {alias}")
    return sorted(found)


def czech_comments(name: str, text: str) -> list[str]:
    """(g) Comments of a template with Czech in them (quoted text aside), as "name:line: word"."""
    found = []
    for match in COMMENT.finditer(text):
        body = QUOTED.sub("", match.group(1))
        word = CZECH_LETTERS.search(body) or CZECH_WORDS.search(body)
        if word:
            found.append(f"{name}:{text.count(chr(10), 0, match.start()) + 1}: {word.group(0)}")
    return found


# HTML attributes and events comments cite in backticks; they are no names of the code.
HTML_WORDS = {"onerror", "onchange", "onclick", "formnovalidate", "enctype", "autocomplete", "loading", "alt", "id",
              "title", "type", "name", "value", "form", "placeholder", "hidden", "selected", "checked", "disabled"}
# A name or a dotted name (`d.rows`, `content_section.data`) - every part of it is checked.
BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`")
# Modules that keep the old (Czech) names on purpose, to convert old databases and addresses: a name only they
# know is a renamed one.
OLD_NAME_MAPS = {"schema_names.py", "legacy_urls.py"}


def code_words(texts: list[str]) -> set[str]:
    """Every identifier the Python code uses (names, attributes, definitions, keywords, strings)."""
    found = set()
    for text in texts:
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, ast.Attribute):
                found.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name)
            elif isinstance(node, ast.arg):
                found.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                found.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                found.add(node.value)
    return found


def unknown_card_attributes(trees: dict[str, nodes.Template], fields: set[str]) -> list[str]:
    """(l) `card.<name>` reads in templates whose name is no field of cards.Card ("template:line card.name")."""
    return sorted(f"{name}:{node.lineno} card.{node.attr}" for name, tree in trees.items()
                  for node in tree.find_all(nodes.Getattr)
                  if isinstance(node.node, nodes.Name) and node.node.name == "card" and node.attr not in fields)


def stale_backticked(texts: dict[str, str], known: set[str]) -> list[str]:
    """(k) Names a template comment cites in backticks that exist nowhere - a renamed key left in a contract."""
    found = []
    for name, text in texts.items():
        for match in COMMENT.finditer(text):
            for word in BACKTICKED.findall(match.group(1)):
                for part in word.split("."):
                    if part not in known and part not in HTML_WORDS:
                        found.append(f"{name}: {part}")
    return sorted(set(found))


def python_names(texts: list[str]) -> set[str]:
    """Every name Python could hand to a template: identifier-like string constants and keyword names."""
    found = set()
    for text in texts:
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                found.add(node.value)
            elif isinstance(node, ast.keyword) and node.arg:
                found.add(node.arg)
    return found


def unprovided(trees: dict[str, nodes.Template], provided: set[str]) -> list[str]:
    """(i) Names a template reads that nobody provides: not bound in any template, not a Jinja global,
    not a name of Python (python_names). A key renamed in Python but not in a template ends here."""
    from jinja2 import meta
    bound = set()
    for tree in trees.values():
        bound |= {n.name for n in tree.find_all(nodes.Name) if n.ctx in ("store", "param")}
        bound |= {m.name for m in tree.find_all(nodes.Macro)}
        for node in tree.find_all(nodes.FromImport):
            bound |= {e if isinstance(e, str) else e[1] for e in node.names}
    known = (bound | provided | set(ENV.globals) | {"request", "loop", "caller", "varargs", "kwargs", "self"}
             | (set() if DEV else DEV_ONLY_GLOBALS))
    found = []
    for name, tree in trees.items():
        for free in sorted(meta.find_undeclared_variables(tree)):
            if free not in known:
                found.append(f"{name}: {free}")
    return found


def english(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in ENGLISH)


# --- the templates of the application ----------------------------------------------------------
print("--- the templates compile and their references hold ---")
texts = sources(TEMPLATES)
trees, parse_errors = parse(texts)
check(len(texts) > 150 and not parse_errors, f"all {len(texts)} templates parse ({parse_errors})")
errors = compile_errors(TEMPLATES, sorted(texts))
check(not errors, f"(a) all templates compile ({errors})")
missing = missing_targets(trees, set() if DEV else DEV_ONLY_TEMPLATES)
check(not missing, f"(b) every literal include/extends/import/from target exists ({missing})")
if DEV:
    listed = dev_only_listed((PROJECT / "deploy" / "sync-do-produ.py").read_text(encoding="utf-8"))
    check(listed == DEV_ONLY_TEMPLATES,
          f"(b) DEV_ONLY_TEMPLATES are the templates of DEV_ONLY in deploy/sync-do-produ.py ({sorted(listed)})")
    unguarded = unguarded_dev_includes(texts, DEV_ONLY_TEMPLATES)
    check(not unguarded, f"(b) a dev-only template is included only under {DEV_GUARD} ({unguarded})")

print()
print("--- the template names in Python exist ---")
in_python: dict[str, set[str]] = {}
for path in sorted((PROJECT / "jellyscope").rglob("*.py")):
    for template_name in python_template_names(path.read_text(encoding="utf-8")):
        in_python.setdefault(template_name, set()).add(path.relative_to(PROJECT).as_posix())
from_registries = registry_template_names()
check(len(in_python) > 20, f"the Python sources name templates ({len(in_python)})")
check(len(from_registries) > 100, f"the registries name templates ({len(from_registries)})")
gone = missing_named(in_python, from_registries, set(texts))
check(not gone, f"(c) every template named in Python exists ({gone})")

print()
print("--- every template is used ---")
used = set().union(*(literal_targets(tree) for tree in trees.values())) | set(in_python) | from_registries
unused = unused_templates(trees, set(in_python) | from_registries, UNUSED_TEMPLATES)
check(not unused, f"(d) every template is a target in a template or named in Python ({unused})")
stale = sorted(name for name in UNUSED_TEMPLATES if name in used or name not in texts)
check(not stale, f"no exception for an unused template is stale ({stale})")

print()
print("--- every name a template reads has a source ---")
provided = python_names([path.read_text(encoding="utf-8") for path in sorted((PROJECT / "jellyscope").rglob("*.py"))])
lost = unprovided(trees, provided)
check(not lost, f"(i) every free name of a template is bound in templates, a Jinja global or a name in Python ({lost[:8]})")

template_words = {t for tree in trees.values() for t in
                  [n.name for n in tree.find_all(nodes.Name)] + [n.attr for n in tree.find_all(nodes.Getattr)]
                  + [m.name for m in tree.find_all(nodes.Macro)] + [a.name for m in tree.find_all(nodes.Macro) for a in m.args]}
known_words = (code_words([path.read_text(encoding="utf-8") for path in sorted((PROJECT / "jellyscope").rglob("*.py"))
                            if path.name not in OLD_NAME_MAPS])
               | template_words | set(ENV.globals) | set(ENV.filters))
stale_words = stale_backticked(texts, known_words)
check(not stale_words, f"(k) every name a template comment cites in backticks exists in the code or templates ({stale_words})")
card_fields = {field.name for field in dataclasses.fields(cards.Card)}
card_reads = sum(1 for tree in trees.values() for node in tree.find_all(nodes.Getattr)
                 if isinstance(node.node, nodes.Name) and node.node.name == "card")
odd_card_reads = unknown_card_attributes(trees, card_fields)
check(card_reads >= 100 and not odd_card_reads,
      f"(l) every card.<name> in the templates is a field of Card ({card_reads} reads, unknown: {odd_card_reads[:8]})")

print()
print("--- English file names ---")
czech_files, named_otherwise = czech_named(sorted(texts), [card.template for card in cards.ALL_CARDS],
                                           CARD_TEMPLATES_OF_PAGES)
check(not czech_files, f"(h) no template is named _sekce_* or lies in nastaveni/ ({czech_files[:5]})")
check(not named_otherwise, f"(h) the template of every card is _card_*.html ({named_otherwise[:5]})")

print()
print("--- macros ---")
problems = macro_problems(trees, UNUSED_MACROS)
check(not problems, f"(e, f) imports name existing macros, calls fit, every macro is called ({problems})")
stale = sorted(entry for entry in UNUSED_MACROS
               if tuple(entry.split(":", 1)) not in macros_of(trees)
               or not macro_problems(trees, {k: v for k, v in UNUSED_MACROS.items() if k != entry}))
check(not stale, f"no exception for an unused macro is stale ({stale})")
idle_imports = unused_imports(trees)
check(not idle_imports, f"(e) every macro a template imports is used there ({idle_imports})")

print()
print("--- comments in English ---")
english_names = [name for name in texts if english(name)]
stale = [pattern for pattern in ENGLISH if not any(fnmatch.fnmatch(name, pattern) for name in texts)]
check(not stale, f"every pattern of ENGLISH matches a template ({stale})")
czech = [found for name in english_names for found in czech_comments(name, texts[name])]
check(not czech, f"(g) the comments of {len(english_names)} English templates carry no Czech ({czech[:10]})")

# --- traps --------------------------------------------------------------------------------------
print()
print("--- traps: each check reports a planted fault ---")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    planted = {
        "page.html": '{% extends "nothere.html" %}',
        "broken.html": "{% if %}",
        "filter.html": "{{ 1 | no_such_filter }}",
        "m.html": "{% macro shown(a, b=1) %}{{ a }}{% endmacro %}{% macro idle() %}{% endmacro %}",
        "imports.html": '{% from "m.html" import shown, absent %}{{ shown(1, c=2) }}{{ shown(1, 2, 3) }}',
        "orphan.html": "{# comment je česky #}",
    }
    for planted_name, text in planted.items():
        (root / planted_name).write_text(text, encoding="utf-8")
    trap_texts = sources(root)
    trap_trees, trap_parse = parse(trap_texts)
    check(any(e.startswith("broken.html") for e in trap_parse), "(a) a broken tag does not parse")
    check(any(e.startswith("filter.html") for e in compile_errors(root, ["filter.html"])),
          "(a) an unknown filter does not compile")
    check(missing_targets(trap_trees) == ["page.html -> nothere.html"], "(b) a missing extends target is found")
    check(missing_targets(trap_trees, {"nothere.html"}) == [], "(b) a target allowed to be missing (prod) is not")
    check(dev_only_listed('DEV_ONLY = {"x.md", "jellyscope/templates/dev.html"}') == {"dev.html"},
          "(b) the dev-only templates are read from DEV_ONLY")
    check(unguarded_dev_includes({"a.html": '{% include "dev.html" %}',
                                  "b.html": DEV_GUARD + '{% include "dev.html" %}{% endif %}'}, {"dev.html"})
          == ["a.html:1 -> dev.html"], "(b) a dev-only include without the guard is found")
    check(python_template_names('TemplateResponse(request, "settings/x.html", {"a": "b.css"})')
          == {"settings/x.html"}, "(c) a template name in Python is read, other strings are not")
    check(missing_named({"gone.html": {"jellyscope/web/x.py"}, "page.html": {"y.py"}}, {"lost.html"}, set(trap_texts))
          == ["gone.html (jellyscope/web/x.py)", "lost.html (registry)"],
          "(c) a template name in Python or a registry that is no template is found")
    check("orphan.html" in unused_templates(trap_trees, set(), {}), "(d) a template nobody refers to is found")
    check("orphan.html" not in unused_templates(trap_trees, {"orphan.html"}, {})
          and "orphan.html" not in unused_templates(trap_trees, set(), {"orphan.html": "planted"}),
          "(d) a template named in Python or excepted is not")
    check(czech_named(["_sekce_x.html", "nastaveni/a.html", "_card_y.html"], ["_card_y.html", "_karta_z.html", "page.html"],
                      {"page.html"}) == (["_sekce_x.html", "nastaveni/a.html"], ["_karta_z.html"]),
          "(h) a Czech template name and a card template not called _card_* are found")
    found = macro_problems(trap_trees, {})
    check(any("imports absent" in p for p in found), "(e) an import of a macro that is not there is found")
    check(any("m.html: the macro idle is never called" == p for p in found), "(e) a macro never called is found")
    check(any("has no parameter c" in p for p in found), "(f) an unknown keyword argument is found")
    check(any("takes 2 arguments, gets 3" in p for p in found), "(f) too many positional arguments are found")
    check(unused_imports(trap_trees) == ["imports.html: absent"], "(e) an imported macro the template never uses is found")
    check(not macro_problems(trap_trees, {"m.html:idle": "planted"}) == found, "(e) an exception silences one macro")
    lone = {"lone.html": ENV.parse("{{ nobody_gives_this }}{% set mine = 1 %}{{ mine }}{{ known_key }}")}
    check(unprovided(lone, python_names(['f(known_key=1)'])) == ["lone.html: nobody_gives_this"],
          "(i) a name nobody provides is found, a bound name and a Python name are not")
    check(stale_backticked({"c.html": "{# reads `gone_key` and `kept_key`, see `onerror` #}"}, {"kept_key"})
          == ["c.html: gone_key"], "(k) a backticked name that exists nowhere is found")
    check(stale_backticked({"e.html": "{# `d.radky` and `d.rows`, `content_section.data` #}"},
                           {"d", "rows", "content_section", "data"}) == ["e.html: radky"],
          "(k) a renamed part of a dotted name is found, a dotted name that exists is not")
    check(unknown_card_attributes({"d.html": ENV.parse("{{ card.key }}\n{{ card.klic }}{{ row.klic }}")}, {"key"})
          == ["d.html:2 card.klic"], "(l) a card attribute that is no field of Card is found, other objects are not")
    check(czech_comments("orphan.html", trap_texts["orphan.html"]) == ["orphan.html:1: č"],
          "(g) a Czech comment is found")
    check(not czech_comments("q.html", '{# the menu item "Přehled" and „Knihovna“ #}'),
          "(g) quoted Czech in an English comment passes")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
