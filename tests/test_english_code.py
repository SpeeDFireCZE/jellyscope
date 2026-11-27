# -*- coding: utf-8 -*-
r"""Translated code stays in English: no Czech left in comments, docstrings or names.

The English-code step (spec: docs/superpowers/specs/2026-11-16-english-code-design.md) moves the
code to English package by package. Every finished package is listed in FINISHED, and every test
(tests/*.py) is checked - all of them are English since part 10f. Every other Python file of jellyscope/
is checked too (25. 11. 2026), so a new module is checked from its first commit.
In them a comment or a docstring with a Czech letter (diacritics) or a common Czech word fails,
and so does a name that is not ASCII. Strings are not checked: translation keys (`_("...")`) and
log texts stay Czech on purpose, and SQL names Czech tables and columns until the database step.
For the same reason quoted text inside a comment or a docstring ("Stahuje se", `nazev`, „...")
is skipped: it names a UI text, a column or data, which stay Czech.

Run:
    .venv/bin/python tests/test_english_code.py
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tempfile
import tokenize
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent

# Files and folders already in English. Each step of the spec added its package here. Tests are not listed:
# since part 10f (21. 11. 2026) every tests/*.py is checked (finished_files), a new test from its first commit;
# the same holds for every jellyscope/**/*.py since 25. 11. 2026 (the books modules had slipped through the list).
FINISHED = [
    # Step 1 (16. 11. 2026): the music package and its tests.
    "jellyscope/music",
    # Step 2 (17. 11. 2026): the seerr package and its tests.
    "jellyscope/seerr",
    # Step 3 (17. 11. 2026): the arr package, app by app, and its tests.
    "jellyscope/arr/sonarr",
    "jellyscope/arr/radarr",
    "jellyscope/arr/bazarr",
    "jellyscope/arr/prowlarr",
    "jellyscope/arr",
    # Step 4 (17. 11. 2026): the importers package and its tests.
    "jellyscope/importers",
    # Step 5 (17. 11. 2026): the scanner package and its tests.
    "jellyscope/scanner",
    # Step 6 (17. 11. 2026): the stats package and its tests.
    "jellyscope/stats",
    # Step 7 (17. 11. 2026): the root infrastructure.
    "jellyscope/dialect.py",
    "jellyscope/config.py",
    "jellyscope/dbmigrate.py",
    "jellyscope/db.py",
    "jellyscope/i18n.py",
    "jellyscope/formatting.py",
    "jellyscope/applog.py",
    "jellyscope/accounts.py",
    "jellyscope/access.py",
    # Step 8 (18. 11. 2026): the rest of the root.
    "jellyscope/languages.py",
    "jellyscope/probe.py",
    "jellyscope/geometry.py",
    "jellyscope/worldmap.py",
    "jellyscope/map_clusters.py",
    "jellyscope/seasonal.py",
    "jellyscope/outage.py",
    "jellyscope/api_query.py",
    "jellyscope/geoip.py",
    "jellyscope/jellyfin.py",
    "jellyscope/images.py",
    "jellyscope/services.py",
    "jellyscope/updates.py",
    "jellyscope/release_notes.py",
    "jellyscope/charts.py",
    "jellyscope/detailed_map.py",
    "jellyscope/langstats.py",
    "jellyscope/insights.py",
    "jellyscope/collector.py",
    "jellyscope/notifications.py",
    "jellyscope/demodata.py",
    "jellyscope/purge.py",
    "jellyscope/api.py",
    "jellyscope/tasks.py",
    "run.py",
    "manage.py",
    "demo.py",
    "jellyscope/cards_base.py",
    "jellyscope/cards_sonarr.py",
    "jellyscope/cards_radarr.py",
    "jellyscope/cards_bazarr.py",
    "jellyscope/cards_prowlarr.py",
    "jellyscope/cards_seerr.py",
    "jellyscope/cards_music.py",
    "jellyscope/cards.py",
    "jellyscope/web/base.py",
    "jellyscope/web/periods.py",
    "jellyscope/web/__init__.py",
    "jellyscope/web/status.py",
    "jellyscope/web/start.py",
    "jellyscope/web/legacy.py",
    "jellyscope/web/library.py",
    "jellyscope/web/disks.py",
    "jellyscope/web/network.py",
    "jellyscope/web/history.py",
    "jellyscope/web/users.py",
    "jellyscope/web/dashboards.py",
    "jellyscope/web/login.py",
    "jellyscope/web/my_account.py",
    "jellyscope/web/arr.py",
    "jellyscope/web/seerr.py",
    "jellyscope/web/album.py",
    "jellyscope/web/music_library.py",
    "jellyscope/web/library_kinds.py",
    "jellyscope/web/video",
    "jellyscope/web/music",
    "jellyscope/web/settings",
    "jellyscope/__init__.py",
    "jellyscope/content_kinds.py",
    "jellyscope/legacy_urls.py",
    "jellyscope/navigation.py",
]

# Finished files that exist only in dev (DEV_ONLY of deploy/sync-do-produ.py): checked when they are
# there, in prod they are missing on purpose.
FINISHED_DEV_ONLY = [
    "jellyscope/devtools.py",
    "deploy/sync-do-produ.py",
    "deploy/convert-translations.py",
]

# Text files already in English (not Python). No Czech letter or common Czech word anywhere in them:
# comments, echo texts and messages alike.
FINISHED_TEXT = [
    # Docker (16. 11. 2026, asked by the user).
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    "deploy/docker-entrypoint.sh",
]

CZECH_LETTERS = re.compile("[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")
CZECH_WORDS = re.compile(r"\b(je|se|ze|aby|kdyz|protoze|neni|jsou|nebo|jen|jak|tak)\b", re.IGNORECASE)
# Quoted text: "...", `...` and Czech quotes - a UI text, a column or data named in English prose.
QUOTED = re.compile('"[^"\n]*"|`[^`\n]*`|„[^“\n]*“')

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def finished_files(root: Path = PROJECT) -> tuple[list[Path], list[str]]:
    """The Python files of FINISHED (and of FINISHED_DEV_ONLY where present), every other file of jellyscope/, every test,
    and the FINISHED entries that do not exist. `root` is the project (a made-up one for the check of the check)."""
    files: list[Path] = []
    missing: list[str] = []
    for entry in FINISHED + FINISHED_DEV_ONLY:
        path = root / entry
        if path.is_dir():
            files += sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
        elif path.is_file():
            files.append(path)
        elif entry in FINISHED:
            missing.append(entry)
    files += [path for path in sorted((root / "jellyscope").rglob("*.py"))
              if "__pycache__" not in path.parts and path not in files]
    files += [path for path in sorted((root / "tests").glob("*.py")) if path not in files]
    return files, missing


def tokens(source: str) -> list[tokenize.TokenInfo]:
    """The source's tokens, once per file (comments and names both read them)."""
    return list(tokenize.generate_tokens(io.StringIO(source).readline))


def comments(toks: list[tokenize.TokenInfo]) -> Iterator[tuple[int, str]]:
    for token in toks:
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string


def docstrings(tree: ast.AST) -> Iterator[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            text = ast.get_docstring(node, clean=False)
            if text:
                yield (node.body[0].lineno, text)


def names(toks: list[tokenize.TokenInfo], tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Every identifier of the source: the NAME tokens, so definitions and uses, import and except
    aliases, keyword argument names, global/nonlocal and match captures alike."""
    for token in toks:
        if token.type == tokenize.NAME:
            yield token.start[0], token.string
    # Python 3.11 keeps a whole f-string in one STRING token, so the names inside it come from the tree.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute):
            yield node.lineno, node.attr
        elif isinstance(node, ast.keyword) and node.arg:
            yield node.lineno, node.arg
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg


# Czech words of names without diacritics (part 10e): `odpoved`, `spravce_k`, `prihlas()` are ASCII and
# would pass the check above. Only names the file binds count - an attribute of an object from elsewhere
# or a keyword argument of a call (a translation placeholder, `nazev=`) is not its own.
CZECH_NAME_WORDS = frozenset("""
spravce odpoved ctenar jmeno stranka klic cesta stav adresa karta ucet vysledek puvodni podminka den radek radky bez
polozka jmena dotazy karty chybi prihlas divak nazev hodnota spatne posli kod okno nastav ted cislo vsechny prehled
posledni slozka zadost kolik chyba soubor vse misto odeslane poradi historie zacatek sablona nalez hra odkazy cas
dil jazyky heslo jen cil prehrani kdo kdy odkaz zavolej oblast pole ctenare velikost jazyk nacti rust muj klice vrat
stranky uzel hlaska shoda dnes pocet podle utok rada cizi hlavni zalohy klik obsah pozadavek serial ulozene dotaz
vychozi dotazu seznam sablony zaznam vlastni zkontroluj zadosti mrtve serveru knihovna prohlizec anonym adresy
novacek souhrn telo sit prava dny obrazku zive nastaveni texty sirka typ oblasti znacka zachyt kde druhy hledani
navod ceka zarizeni cely vyraz ucty veta celkem cisla cele knihovny klient chyb kontrola zaloha
""".split())


def bound_names(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """The names a file binds itself: assignments, definitions, parameters, aliases, captures."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.lineno, node.id
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.lineno, node.name
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg
        elif isinstance(node, ast.alias) and node.asname:
            yield getattr(node, "lineno", 0), node.asname
        elif isinstance(node, ast.ExceptHandler) and node.name:
            yield node.lineno, node.name
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            yield from ((node.lineno, name) for name in node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            yield node.lineno, node.name



def czech_name_words(name: str) -> set[str]:
    return set(name.lower().strip("_").split("_")) & CZECH_NAME_WORDS


def non_ascii_names(toks: list[tokenize.TokenInfo], tree: ast.AST) -> list[tuple[int, str]]:
    """Identifiers of the source that are not ASCII, with their line (each once)."""
    return sorted({(line, name) for line, name in names(toks, tree) if not name.isascii()})


def sample_names(source: str) -> set[str]:
    """The non-ASCII names of a sample source."""
    return {name for _, name in non_ascii_names(tokens(source), ast.parse(source))}


def czech_in_text_line(line: str) -> str:
    """What makes a line of a finished text file Czech ("" = nothing): a Czech letter or word anywhere
    on it - a comment, an echo or a message alike, quoted text included."""
    found = CZECH_LETTERS.search(line) or CZECH_WORDS.search(line)
    return found.group(0) if found else ""


def czech_text(text: str) -> str:
    """What makes a text Czech ("" = nothing): the first Czech letter or word outside quotes."""
    text = QUOTED.sub("", text)
    letter = CZECH_LETTERS.search(text)
    if letter:
        return letter.group(0)
    word = CZECH_WORDS.search(text)
    return word.group(0) if word else ""


files, missing = finished_files()
check(not missing, f"every entry of FINISHED exists ({missing})")
# Files only in dev (DEV_ONLY of deploy/sync-do-produ.py) never reach prod, but this test does: in
# FINISHED they would fail "exists" there and with it prod's CI and the release (review of step 8).
SYNC_SCRIPT = PROJECT / "deploy" / "sync-do-produ.py"
if SYNC_SCRIPT.is_file():
    dev_only: set[str] = set()
    for node in ast.parse(SYNC_SCRIPT.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEV_ONLY" for t in node.targets):
            dev_only = set(ast.literal_eval(node.value))
    check(bool(dev_only) and not set(FINISHED) & dev_only,
          f"no entry of FINISHED is dev only - those go to FINISHED_DEV_ONLY ({sorted(set(FINISHED) & dev_only)})")
    check(set(FINISHED_DEV_ONLY) <= dev_only,
          f"FINISHED_DEV_ONLY holds only dev-only files ({sorted(set(FINISHED_DEV_ONLY) - dev_only)})")
check(len(files) >= len(FINISHED) - len(missing), f"FINISHED holds {len(files)} Python files")

def czech_in_file(path: Path, root: Path = PROJECT) -> list[str]:
    """What is Czech in one Python file: comments, docstrings, non-ASCII names and names with a Czech word."""
    found: list[str] = []
    source = path.read_text(encoding="utf-8")
    toks, tree = tokens(source), ast.parse(source)
    where = path.relative_to(root)
    for line, text in comments(toks):
        if czech_text(text):
            found.append(f"{where}:{line}: comment {czech_text(text)!r}: {text.strip()[:80]}")
    for line, text in docstrings(tree):
        if czech_text(text):
            found.append(f"{where}:{line}: docstring {czech_text(text)!r}: {text.strip()[:80]}")
    for line, name in non_ascii_names(toks, tree):
        found.append(f"{where}:{line}: name {name!r} is not ASCII")
    for line, name in sorted(set(bound_names(tree))):
        if czech_name_words(name):
            found.append(f"{where}:{line}: name {name!r} has a Czech word {sorted(czech_name_words(name))}")
    return found


found = [item for path in files for item in czech_in_file(path)]
for item in found[:40]:
    print(f"    {item}")
check(not found, f"no Czech in comments, docstrings or names of finished code ({len(found)} found)")
# The check of the check: a module FINISHED does not name - a new one, in a new subfolder - is checked from its
# first commit. (Comparing the files with the same rglob that collected them would always pass.)
sample_root = Path(tempfile.mkdtemp())
(sample_root / "jellyscope" / "brand_new").mkdir(parents=True)
(sample_root / "tests").mkdir()
(sample_root / "jellyscope" / "brand_new" / "module.py").write_text("# kdyz se to nepovede, zkus to znovu\nx = 1\n",
                                                                   encoding="utf-8")
(sample_root / "tests" / "brand_new_check.py").write_text("def over_odpoved():\n    pass\n", encoding="utf-8")
sample_files, _ = finished_files(sample_root)
sample_found = [item.split(":")[0] for path in sample_files for item in czech_in_file(path, sample_root)]
check(sample_found == ["jellyscope/brand_new/module.py", "tests/brand_new_check.py"],
      f"a new module in a new folder and a new test that FINISHED does not name are checked ({sample_found})")

check(czech_name_words("spravce_k") == {"spravce"} and czech_name_words("odpoved") == {"odpoved"}
      and not czech_name_words("admin_client") and czech_name_words("ui_cas_presne") == {"cas"}
      and {name for _, name in bound_names(ast.parse("def prihlas(jmeno):\n    odpoved = 1\nx.atribut = 2\nf(nazev=1)\n"))}
      == {"prihlas", "jmeno", "odpoved"},
      "a Czech word in a name the file binds is found (definitions, parameters, assignments); "
      "an attribute and a keyword argument of a call are not the file's own; an old form field's name is no exception")
# Every kind of name counts, not only variables and definitions.
SAMPLE = ("from json import dumps as výpis\n"
          "try:\n    pass\nexcept ValueError as chyba_ž:\n    pass\n"
          "print('x', ž=1)\n"
          "def f():\n    global počet\n"
          "match 1:\n    case [ř]:\n        pass\n"
          "print(x.atribut_ž)\n"
          'print(f"{v_fstringu_š}")\n')
check(sample_names(SAMPLE) == {"výpis", "chyba_ž", "ž", "počet", "ř", "atribut_ž", "v_fstringu_š"},
      f"a name in import-as, except-as, a keyword argument, global, a match capture, an attribute and "
      f"an f-string is found too ({sorted(sample_names(SAMPLE))})")


def as_python_311(toks: list[tokenize.TokenInfo]) -> list[tokenize.TokenInfo]:
    """The tokens without what Python 3.11 does not split out: there an f-string is one STRING token
    (PEP 701 split it from 3.12 on). On 3.11 itself nothing is left out."""
    start, end = getattr(tokenize, "FSTRING_START", None), getattr(tokenize, "FSTRING_END", None)
    kept, depth = [], 0
    for token in toks:
        if token.type == start:
            depth += 1
        elif token.type == end:
            depth -= 1
        elif depth == 0:
            kept.append(token)
    return kept


# The prod CI runs the tests on Python 3.11 too: a name inside an f-string must be found there as well.
FSTRING = 'print(f"{v_fstringu_š}")\n'
on_311 = {name for _, name in non_ascii_names(as_python_311(tokens(FSTRING)), ast.parse(FSTRING))}
check(on_311 == {"v_fstringu_š"}, f"a name inside an f-string is found with Python 3.11 tokens too ({sorted(on_311)})")

# A text file is Czech by any line, not only by its comments - an echo or a compose message counts.
CZECH_LINES = ['echo "Jellyscope: slozka $DATA neni prazdna"',
               'SECRET_KEY: "${SECRET_KEY:?nastav to v .env, jinak se nespusti}"']
ENGLISH_LINES = ['[ $# -eq 0 ] && exec "$@"   # nothing else to run', 'rest="${path#/app/}"']
check(all(czech_in_text_line(line) for line in CZECH_LINES),
      f"a Czech line of a text file is found outside comments too ({[czech_in_text_line(x) for x in CZECH_LINES]})")
check(not any(czech_in_text_line(line) for line in ENGLISH_LINES),
      f"an English line with # outside a comment passes ({[czech_in_text_line(x) for x in ENGLISH_LINES]})")

text_found: list[str] = []
missing_text = [entry for entry in FINISHED_TEXT if not (PROJECT / entry).is_file()]
check(not missing_text, f"every entry of FINISHED_TEXT exists ({missing_text})")
for entry in FINISHED_TEXT:
    path = PROJECT / entry
    if not path.is_file():
        continue
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if czech_in_text_line(line):
            text_found.append(f"{entry}:{number}: {czech_in_text_line(line)!r}: {line.strip()[:80]}")
for item in text_found[:40]:
    print(f"    {item}")
check(not text_found, f"no Czech in the finished text files ({len(text_found)} found)")

print()
print("--- the templates call charts.X(...) by names that exist ---")
# Templates call the chart functions by name, with keyword arguments. A function or a parameter
# renamed in Python and not in a template is a 500 or an empty chart, found only when the page
# is opened - so every call is read with Jinja's own parser and checked against the signature.
import inspect  # noqa: E402

from jinja2 import Environment, nodes  # noqa: E402

sys.path.insert(0, str(PROJECT))
from jellyscope import charts  # noqa: E402


def chart_call_problems(source: str, name: str) -> list[str]:
    """Calls of charts.X(...) in a template whose function or keyword argument does not exist."""
    problems: list[str] = []
    for call in Environment().parse(source).find_all(nodes.Call):
        target = call.node
        if not (isinstance(target, nodes.Getattr) and isinstance(target.node, nodes.Name)
                and target.node.name == "charts"):
            continue
        function = getattr(charts, target.attr, None)
        if not callable(function):
            problems.append(f"{name}:{call.lineno}: charts.{target.attr} does not exist")
            continue
        parameters = inspect.signature(function).parameters
        any_keyword = any(p.kind is p.VAR_KEYWORD for p in parameters.values())
        for keyword in call.kwargs:
            if keyword.key not in parameters and not any_keyword:
                problems.append(f"{name}:{call.lineno}: charts.{target.attr}() has no parameter {keyword.key}")
    return problems


chart_calls = 0
chart_problems: list[str] = []
for template in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    source = template.read_text(encoding="utf-8")
    chart_calls += source.count("charts.")
    chart_problems += chart_call_problems(source, str(template.relative_to(PROJECT)))
for item in chart_problems[:20]:
    print(f"    {item}")
check(chart_calls >= 60 and not chart_problems,
      f"every charts call in the templates names a function and its parameters ({chart_calls} calls, "
      f"{len(chart_problems)} wrong)")
check(len(chart_call_problems("{{ charts.hbar_chart(rows, no_such_argument=1) }}"
                              "{{ charts.no_such_chart(rows) }}{{ other.hbar_chart(x=1) }}", "sample")) == 2,
      "a call with an unknown argument and a call of an unknown function are found, other objects are not")

# The fields of the classes templates read (step 10b3): exactly these, in English. A template reads
# them with a dot (`card.key`, `period.since`), so a Czech field would keep Czech in the templates too.
# The names inside the scripts (step 10c2): a variable, a function, a parameter or an object key must not
# hold a Czech word. A name is split at "_" and at a lower-to-upper case change; the words are those of the
# names step 10c2 renamed; every script under static/ is checked.
JS_FINISHED = sorted(path.relative_to(PROJECT).as_posix()
                     for path in (PROJECT / "jellyscope" / "static").rglob("*.js"))
# Names that stay Czech in a script on purpose, with the reason.
JS_KEPT: dict[str, str] = {}     # none left: the JSON the scripts read is English since the database step 6
JS_CZECH_PARTS: set[str] = {
    "anonym", "bezi", "cely", "cesta", "chci", "cisla", "dlazdice", "dlazdici", "dokonci", "hodnota", "hotovo",
    "hranice", "klic", "klice", "krok", "mapa", "misto", "mysi", "nacteno", "nahrada", "nejvic", "obal",
    "obrazek", "odeber", "odpoved", "okno", "omez", "po", "pocet", "pod", "podrobna", "podrobne", "podrobnost",
    "pohled", "pole", "polomer", "pomer", "poradi", "posluchac", "posunuto", "precti", "prepni", "priblizeni",
    "priblizit", "prisla", "prvek", "pustit", "rezim", "se", "sekundy", "sila", "sirka", "site", "skupina",
    "stav", "stred", "strom", "svet", "tahne", "tecka", "tecky", "tlacitko", "uklid", "uloz", "uroven",
    "urovne", "velikost", "verze", "vlastni", "vloz", "vrstva", "vrstvy", "vyber", "vybrane", "vychozi",
    "vykresli", "vyrez", "vyrizeno", "zakladni", "zamceno", "zaznam", "zeptej", "zmene",
    "animace", "atributy", "barva", "barvy", "beh", "bez", "bod", "cas", "casovace", "chyba", "cil", "citac",
    "darek", "delka", "dira", "dopad", "dotazeno", "drahy", "druha", "dyne", "fokusovatelne", "hladce",
    "hori", "hrana", "hry", "jedna", "jeviste", "jinde", "jiskra", "jiskry", "kazdych", "klepni", "klicka",
    "klid", "klidne", "kliku", "kolik", "konec", "konfet", "konfety", "koren", "kresba", "kresby", "krivka",
    "kus", "laloky", "malo", "meze", "mezi", "mimo", "na", "nacita", "nahodne", "napoveda", "netopyr",
    "netopyri", "odemkni", "odkud", "odlet", "odpocty", "odskok", "ohyb", "orez", "otevreni", "otevreno",
    "otevreny", "otevri", "ozdoba", "pad", "pavouk", "pevne", "plamen", "plamene", "platno", "pohybu", "posl",
    "posledni", "posun", "posuvnik", "pozice", "prani", "praskliny", "pred", "preklop", "presun", "prodleva",
    "promenne", "pruh", "pruhy", "prvky", "prvni", "pust", "rachejtli", "raketa", "registruj", "rodic", "rok",
    "roku", "rolovani", "rozbal", "rozbusky", "rozsvitit", "rozvazani", "rychlost", "sada", "scena", "seznam",
    "sfouknout", "skript", "skryta", "smer", "snesl", "snih", "snimek", "snimky", "spicka", "spust", "stara",
    "stopa", "svatek", "sviti", "svitit", "tady", "tahle", "telo", "textu", "texty", "tlacitka", "trida",
    "trvani", "tvar", "uhel", "ukazatel", "ukoly", "usek", "uzke", "vajec", "vajicek", "vajicko", "vejce",
    "velka", "ven", "vicka", "vlocka", "vpredu", "vrch", "vsvg", "vybuch", "vybuchni", "vycisti", "vyklubni",
    "vypln", "vyrezy", "vyrizni", "vyska", "vyskok", "vystup", "vzdal", "zajic", "zamek", "zamkni", "zapal",
    "zapalovac", "zare", "zavri", "zavrit", "zbyva", "zdroj", "znovu", "zpomaluje", "zrus", "zvetsi",
    "adresa", "aktivni", "bublinu", "bunka", "byl", "casovac", "casti", "chyb", "cip", "co", "cti", "dal",
    "dalsi", "datum", "den", "dlouho", "dnes", "dnu", "dny", "dobehla", "dotaz", "duvod", "dva", "fokus",
    "fokusem", "formular", "graf", "grafu", "hlaska", "hlava", "hledej", "jazyk", "je", "jmeno", "kam",
    "karta", "kde", "klient", "kod", "kolem", "kopie", "krizek", "kroky", "mesic", "mesice", "mista", "mrizka",
    "nabidka", "nabidku", "nacti", "nadpis", "nahled", "nahradni", "nakresli", "napis", "napln", "nastroje",
    "nazev", "nejlepsi", "nejmin", "neplati", "neuspechu", "nic", "nova", "nove", "novy", "obnov", "obnovit",
    "obnovy", "obsah", "odebrat", "odkaz", "ohlas", "okna", "okraj", "otevrene", "oznam", "parametr", "pas",
    "plakat", "plakatek", "podil", "pojistka", "pokusu", "policka", "policko", "polohu", "polozka", "polozky",
    "popis", "posuvy", "potvrdit", "potvrzeni", "pouzite", "povedlo", "povoleno", "pozn", "poznamka",
    "precisluj", "prehravani", "prepinac", "prepinatelna", "pridat", "pryc", "pul", "puvodni", "radek",
    "radku", "radky", "ramecek", "rekap", "rozsah", "rytmus", "sekund", "serveru", "shoda", "sipka", "sirky",
    "skryte", "sobe", "souhrn", "spocti", "spoustec", "srovnej", "stare", "stejne", "stejny", "stranku",
    "tabulka", "tah", "tazena", "tazeni", "tento", "tiku", "titulu", "trva", "udalost", "ukaz", "ukazka",
    "ulohy", "ulozeno", "uzel", "ve", "vic", "videno", "volba", "vpred", "vrat", "vse", "vsechny", "vstup",
    "vybrano", "vymen", "vzdalenost", "zacatek", "zadani", "zapamatuj", "zbytek", "zkontroluj", "zkratka",
    "zkratky", "zkus", "zmena", "znacky", "znama", "zname", "zpet", "zrusit", "zvolene",
    "od", "za", "kr"
}
JS_TOKEN = re.compile(r"""//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`""", re.S)


def js_czech_names(source: str) -> list[str]:
    """Names of a script (not after a dot) with a word of JS_CZECH_PARTS."""
    code = JS_TOKEN.sub(lambda m: " " * len(m.group()), source)
    found = set()
    for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)", code):
        name = m.group(1)
        words = {w.lower() for w in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", name) if w}
        if words & JS_CZECH_PARTS and name not in JS_KEPT:
            found.add(name)
    return sorted(found)


# The closing tag may carry whitespace and attributes ("</script >") - the browser ends the script there too.
INLINE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\b[^>]*>", re.S | re.I)


def inline_scripts(text: str) -> list[str]:
    """Bodies of the inline scripts in a Python file or a template (data blocks of type application/json aside)."""
    return [body for attrs, body in INLINE_SCRIPT.findall(text)
            if body.strip() and "application/json" not in attrs and "src=" not in attrs]


print()
for script in JS_FINISHED:
    czech_js = js_czech_names((PROJECT / script).read_text(encoding="utf-8"))
    check(not czech_js, f"{script}: the names are English ({czech_js[:8]})")
inline = {path.relative_to(PROJECT).as_posix(): inline_scripts(path.read_text(encoding="utf-8"))
          for path in sorted((PROJECT / "jellyscope").rglob("*")) if path.suffix in (".py", ".html")}
inline = {where: bodies for where, bodies in inline.items() if bodies}
for where, bodies in inline.items():
    czech_js = sorted({name for body in bodies for name in js_czech_names(body)})
    check(not czech_js, f"{where}: the names of its inline script are English ({czech_js[:8]})")
check("jellyscope/web/settings/updates.py" in inline,
      "the inline scripts are found (the update page's waiting script among them)")
check(inline_scripts('<script nonce="{n}">var zkus;</script><script type="application/json" id="t">{"zkus": 1}</script>')
      == ["var zkus;"], "an inline script is read, a JSON data block is not")
check(inline_scripts("<script>var a;</script >\n<SCRIPT>var b;</SCRIPT\t data-x>") == ["var a;", "var b;"],
      "a closing tag with whitespace, attributes or in capitals ends the script too")


# Comments of static files (part 10c3) and of the inline scripts and styles in Python. Old comments there
# were often Czech without diacritics (an old cp1250 habit), so besides czech_text() a block fails on a
# common Czech word without diacritics: the most frequent ones of the old static comments that never occur
# in the project's English prose.
STATIC_CZECH_WORDS = re.compile(r"\b(" + "|".join(sorted({
    "atribut", "az", "barve", "barvu", "bila", "bile", "blok", "bloku", "bublina", "bubliny", "byly", "byt", "cary",
    "chlup", "cislo", "clovek", "displej", "displeji", "dokumentu", "dole", "doprava", "dostat", "drzi", "dve",
    "dvojice", "fialova", "fokus", "fokusu", "grafem", "grafy", "hodnotou", "hrana", "hranu", "hrany", "informaci",
    "javascriptu", "kartou", "kartu", "kazdy", "kdyz", "klavesnici", "kontrast", "kousek", "krabice", "kresli",
    "krivka", "kroky", "ktera", "ktere", "ktery", "lehce", "logiky", "maji", "makro", "mela", "mezera", "mezery",
    "modra", "motiv", "motivu", "mrizka", "musel", "musi", "muze", "mysi", "nabidka", "nadpisem", "nahore",
    "nahoru", "najeti", "nastaveni", "neco", "nedalo", "nevejde", "nutnost", "obrazovky", "obsahem", "odkazy",
    "odsazeni", "odstin", "odstup", "oknem", "okno", "okraj", "okraje", "okrajem", "okraji", "omezit", "ozdobu",
    "pas", "pismo", "plamen", "plati", "plna", "plocha", "plochu", "plochy", "podrobnost", "pohybu", "poloha",
    "polohu", "popisek", "popisku", "posouvat", "posuv", "pozornost", "prechod", "prehledu", "prehravani", "pres",
    "pri", "procesu", "prohlizec", "protoze", "prouzek", "prst", "pruhem", "prvek", "prvku", "prvky", "rachejtle",
    "radek", "rakety", "ramecek", "rezimu", "rohu", "rolovat", "roste", "rozdil", "rozsahu", "roztahuje", "rysky",
    "rytmu", "rytmus", "serie", "sirku", "sirky", "skript", "skriptem", "sloupec", "slovo", "sousedni", "spadne",
    "stavove", "stejne", "stejny", "stoji", "stran", "strance", "stranku", "stranky", "strany", "strom", "strop",
    "stuha", "sve", "svetlejsi", "svetlem", "svetlosti", "svisle", "svym", "tabem", "tah", "takze", "tataz",
    "tlacitka", "tmave", "tmaveho", "tmavem", "tmavsi", "trefit", "ukazatel", "ukazovat", "uz", "ven", "videt",
    "viewboxu", "viset", "vlastni", "vlevo", "volby", "vrstva", "vybrana", "vybrany", "vykoukne", "vypada",
    "vypadalo", "vypadat", "zadny", "zalomit", "zamerne", "zemi", "zmizela", "zustava",
})) + r")\b", re.IGNORECASE)
STATIC_ENGLISH = sorted(path.relative_to(PROJECT).as_posix() for path in (PROJECT / "jellyscope" / "static").rglob("*")
                        if path.suffix in (".js", ".css", ".svg"))   # every static file (part 10c3)
INLINE_ENGLISH = sorted(path.relative_to(PROJECT).as_posix() for path in (PROJECT / "jellyscope").rglob("*.py")
                        if "<script" in path.read_text(encoding="utf-8") or "<style" in path.read_text(encoding="utf-8"))
CSS_TOKEN = re.compile(r"""/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'""", re.S)


def static_comments(text: str, kind: str) -> list[tuple[int, str]]:
    """(line, text without markers) of every comment of a script ("js"), a stylesheet ("css") or markup
    ("markup", <!-- -->); consecutive // lines make one comment, strings are skipped."""
    pattern = JS_TOKEN if kind == "js" else CSS_TOKEN if kind == "css" else re.compile(r"<!--.*?-->", re.S)
    out: list[tuple[int, str]] = []
    line_end = -1                       # where the previous // comment ended (-1: the previous was no //)
    for m in pattern.finditer(text):
        body = m.group()
        if not body.startswith(("//", "/*", "<!--")):
            continue
        line = text.count("\n", 0, m.start()) + 1
        if body.startswith("//"):
            if out and line_end >= 0 and re.fullmatch(r"[ \t]*\n[ \t]*", text[line_end:m.start()]):
                out[-1] = (out[-1][0], out[-1][1] + "\n" + body[2:].strip())
            else:
                out.append((line, body[2:].strip()))
            line_end = m.end()
        else:
            out.append((line, (body[4:-3] if body.startswith("<!--") else body[2:-2]).strip()))
            line_end = -1
    return out


def static_czech(text: str) -> str:
    """What makes a comment Czech ("" = nothing): czech_text() or a common Czech word without diacritics.
    A line with an unbalanced quote fails too: QUOTED pairs quotes within a line, so a quote broken
    across a line end would pair a stray " with the next one on the line and hide what lies between."""
    for line in text.split("\n"):
        if line.count('"') % 2 or line.count("„") != line.count("“"):
            return "a quote broken across a line end"
    found = czech_text(text)
    if found:
        return found
    word = STATIC_CZECH_WORDS.search(QUOTED.sub("", text))
    return word.group(0) if word else ""


def inline_styles(text: str) -> list[str]:
    return [body for body in re.findall(r"<style\b[^>]*>(.*?)</style>", text, re.S) if body.strip()]


print()
check(len(STATIC_ENGLISH) >= 19 and "jellyscope/web/settings/updates.py" in INLINE_ENGLISH,
      f"the comment check covers every static file and the update page ({len(STATIC_ENGLISH)} files)")
for rel in STATIC_ENGLISH:
    kind = "js" if rel.endswith(".js") else "css" if rel.endswith(".css") else "markup"
    czech_comments = [f"{line}: {static_czech(c)!r} {c[:50]!r}"
                      for line, c in static_comments((PROJECT / rel).read_text(encoding="utf-8"), kind)
                      if static_czech(c)]
    check(not czech_comments, f"{rel}: comments are English ({len(czech_comments)}: {czech_comments[:3]})")
for rel in INLINE_ENGLISH:
    text = (PROJECT / rel).read_text(encoding="utf-8")
    czech_comments = [c[:60] for body in inline_scripts(text) for _, c in static_comments(body, "js") if static_czech(c)]
    czech_comments += [c[:60] for body in inline_styles(text) for _, c in static_comments(body, "css") if static_czech(c)]
    check(not czech_comments, f"{rel}: comments of its inline scripts and styles are English ({czech_comments[:3]})")
check(static_comments('var a = "http://x"; // one\n    // two\n/* three\n   four */ b = 1 / 2;', "js")
      == [(1, "one\ntwo"), (3, "three\n   four")]
      and static_comments('a { content: "/*"; } /* real */', "css") == [(1, "real")]
      and static_comments("<svg>\n<!-- uvnitř --></svg>", "markup") == [(2, "uvnitř")],
      "comments of a script, a stylesheet and markup are found, not inside strings")
check(static_czech("Plochy a pismo") and static_czech("Tečky se zmenší") and not static_czech("Charts")
      and not static_czech('"Stahuje se" is the label of `pri`'),
      "a Czech comment with or without diacritics is found, English with quoted Czech is not")
check(static_czech('A link ("zobrazit jako\ntabulku"): nikam nevede, <a href="#">')
      and static_czech("„Přidat cestu\" clones a row") and not static_czech('Quoted "Přidat cestu" on one line'),
      "a quote broken across a line end fails (it could hide Czech between two quotes), one on a line passes")
check(js_czech_names('var precti = 1; x.precti(); /* precti */ "precti"; function zeptejSe(okno) {}')
      == ["okno", "precti", "zeptejSe"] or not {"precti", "okno"} <= JS_CZECH_PARTS,
      "a Czech variable, function and parameter are found, a property, a comment and a string are not")


CLASS_FIELDS: dict[str, tuple[str, set[str]]] = {
    "Card": ("jellyscope/cards_base.py", {
        "key", "name", "description", "template", "data", "uses_period", "width", "wrap_in_card", "area",
        "has_table_window", "wrapper_id", "refresh_partial", "after_sync", "group", "available", "sources"}),
    "App": ("jellyscope/arr/registry.py", {
        "key", "name", "stored_settings", "save_settings", "check_connection", "synchronize", "sync_running",
        "sync_state", "event_count", "delete_downloaded_data", "cards", "card_key", "sections", "template",
        "period", "sample_url", "key_hint", "already_running", "delete_note", "provisional_poster",
        "downloading", "downloading_template", "detected_name", "refresh_counts", "watch_settings", "save_watch_settings",
        "marker", "demo", "shown_name", "live_state", "api_stats", "api_indexers"}),
    "Area": ("jellyscope/access.py", {"key", "name", "description", "paths", "default", "parent", "section",
                                      "heading"}),
    "Message": ("jellyscope/notifications.py", {"key", "name", "subject", "text", "variables"}),
    "Period": ("jellyscope/stats/periods.py", {"since", "until", "days", "relative", "since_local", "until_local",
                                               "whole_day", "whole_history", "custom"}),
}


def class_fields(source: str, name: str) -> set[str]:
    """Fields of class `name` in `source`: class attributes (annotated or not), properties and other
    methods (a template may call them), and what __init__/__post_init__ set on self - `self.x = …`,
    `self.x: T = …`, `self.a, self.b = …`, `object.__setattr__(self, "x", …)`. Dunder names are left out."""

    def self_targets(target: ast.expr) -> set[str]:
        if isinstance(target, (ast.Tuple, ast.List)):
            return set().union(*(self_targets(t) for t in target.elts))
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            return {target.attr}
        return set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            found: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    found.add(item.target.id)
                elif isinstance(item, ast.Assign):
                    found |= {t.id for t in item.targets if isinstance(t, ast.Name)}
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in ("__init__", "__post_init__"):
                        for n in ast.walk(item):
                            if isinstance(n, ast.Assign):
                                found |= set().union(*(self_targets(t) for t in n.targets))
                            elif isinstance(n, ast.AnnAssign):
                                found |= self_targets(n.target)
                            elif (isinstance(n, ast.Call) and ast.unparse(n.func) == "object.__setattr__"
                                  and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)):
                                found.add(n.args[1].value)
                    elif not (item.name.startswith("__") and item.name.endswith("__")):
                        found.add(item.name)
            return {f for f in found if not (f.startswith("__") and f.endswith("__"))}
    return set()


print()
for class_name, (path, expected) in CLASS_FIELDS.items():
    actual = class_fields((PROJECT / path).read_text(encoding="utf-8"), class_name)
    check(actual == expected, f"{class_name} has exactly its English fields (Czech or unexpected: "
          f"{sorted(actual - expected)}, missing: {sorted(expected - actual)})")
sample = ("from dataclasses import dataclass\n@dataclass\nclass Card:\n    key: str\n    klic: str\n"
          "    @property\n    def vlastni(self): return 1\nclass Area:\n    def __init__(self, k):\n"
          "        self.pod = k\n")
check(class_fields(sample, "Card") == {"key", "klic", "vlastni"} and class_fields(sample, "Area") == {"pod"},
      "a Czech field, property or attribute set in __init__ is found")
hidden = ("from dataclasses import dataclass\n@dataclass\nclass Card:\n    key: str\n    nazev = 'x'\n"
          "    def spocti(self): return 1\n    def __post_init__(self):\n        object.__setattr__(self, 'obal', 1)\n"
          "class Area:\n    def __init__(self, k):\n        self.klic: str = k\n        self.a, self.pod = k, k\n")
check(class_fields(hidden, "Card") == {"key", "nazev", "spocti", "obal"}
      and class_fields(hidden, "Area") == {"klic", "a", "pod"},
      f"also an unannotated class attribute, a method, object.__setattr__, an annotated or unpacked self.x "
      f"({sorted(class_fields(hidden, 'Card'))}, {sorted(class_fields(hidden, 'Area'))})")

print()
print("--- the internal keys in the strings of the code are English ---")
# Strings are not read by the checks above, so Czech lived on in keys only the code itself reads: a group
# key ("serial:<id>"), a dict key ("_skryto"), an SVG id ("prechod-3"), a reason ("jedna_shoda"), a task's
# name, a script's error (audit CL-7). A Python string shaped like a name (no spaces - a sentence is a UI
# text or a log key) and every string of a script must not hold a Czech word. What a database, a saved
# file, an address or the API carries stays as it was: those are listed with the reason.
KEY_WORDS = CZECH_NAME_WORDS | {"prechod", "vydani", "jedna", "shod", "zna", "nic", "nejsilnejsi", "zastupci",
                                "skryto", "uctu", "obnova", "zivy", "relace", "je", "neni", "vic"}
KEY_SHAPE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*[:-]?")
# The old names mapped to the new ones - Czech on purpose.
KEYS_OF_OLD_NAMES = {"schema_names.py", "legacy_urls.py", "conversion.py"}
KEPT_KEYS: dict[tuple[str, str], str] = {
    ("jellyscope/access.py", "skryto-"): "the placeholder id of a hidden viewer: in addresses, bookmarks and API answers",
    ("jellyscope/accounts.py", "relace:"): "the prefix of the stored sessions: another one would sign everybody out",
    ("jellyscope/arr/bazarr/sync.py", "dil"): "a part of the fingerprint stored with Bazarr's records",
    **{("jellyscope/db.py", column): "a column of the old Czech *Arr tables a 1.8.x database converts from"
       for column in ("typ", "polozka", "velikost", "klient", "nazev")},
    ("jellyscope/db.py", "heslo"): "keeps the value of an old Czech setting key (smtp_heslo) out of the log",
    ("jellyscope/demodata.py", "Historie"): "a genre of the demo library (data)",
    ("jellyscope/tasks.py", "zalohy"): "the backup folder older installations have on disk",
}


def czech_key_words(value: str) -> set[str]:
    return set(re.split(r"[^a-z]+", value.lower())) & KEY_WORDS


def czech_keys(source: str) -> list[tuple[int, str]]:
    """(line, value) of the strings of a Python source shaped like a name with a Czech word."""
    return sorted({(node.lineno, node.value) for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and KEY_SHAPE.fullmatch(node.value) and czech_key_words(node.value)})


def czech_script_strings(source: str) -> list[str]:
    """The strings of a script with a Czech word or letter (comments are checked elsewhere)."""
    return [m.group()[1:-1] for m in JS_TOKEN.finditer(source) if m.group()[:1] in "\"'`"
            and (czech_key_words(m.group()) or CZECH_LETTERS.search(m.group()))]


def czech_sql_comments(source: str) -> list[tuple[int, str]]:
    """(line, comment) of the SQL comments (-- ...) inside the strings of a Python source that are Czech."""
    return sorted({(node.lineno, comment.strip()) for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   for comment in re.findall(r"--[^\n]*", node.value) if czech_text(comment)})


czech_key_list = [f"{path.relative_to(PROJECT)}:{line}: {value!r}"
                  for path in sorted((PROJECT / "jellyscope").rglob("*.py"))
                  if "__pycache__" not in path.parts and path.name not in KEYS_OF_OLD_NAMES
                  for line, value in czech_keys(path.read_text(encoding="utf-8"))
                  if (path.relative_to(PROJECT).as_posix(), value) not in KEPT_KEYS]
for item in czech_key_list[:40]:
    print(f"    {item}")
check(not czech_key_list, f"no Czech word in the keys of the code ({len(czech_key_list)} found)")
# The header of the backups before 1.9.0 (conversion.py writes it on purpose, for going back to 1.8.x).
KEPT_SQL_COMMENTS = {("jellyscope/tasks.py", "-- Záloha Jellyscope"): "the mark of a backup before 1.9.0 - it restores as well"}
sql_comments = [f"{path.relative_to(PROJECT)}:{line}: {comment[:60]!r}"
                for path in sorted((PROJECT / "jellyscope").rglob("*.py"))
                if "__pycache__" not in path.parts and path.name not in KEYS_OF_OLD_NAMES
                for line, comment in czech_sql_comments(path.read_text(encoding="utf-8"))
                if (path.relative_to(PROJECT).as_posix(), comment) not in KEPT_SQL_COMMENTS]
check(not sql_comments, f"the SQL comments inside strings are English ({sql_comments[:6]})")
check([c for _, c in czech_sql_comments('q = """SELECT 1\n  -- kdyz Jellyfin nic neposle\n  -- the stored one stays\n"""\n')]
      == ["-- kdyz Jellyfin nic neposle"], "a Czech SQL comment inside a string is found, an English one is not")
script_strings = [f"{script}: {value!r}" for script in JS_FINISHED
                  for value in czech_script_strings((PROJECT / script).read_text(encoding="utf-8"))]
check(not script_strings, f"no Czech in the strings of the scripts ({script_strings[:6]})")
check([value for _, value in czech_keys('k = f"serial:{x}"\nd["_skryto"] = 1\nm = "Jen text s mezerou"\nok = "series:"\n')]
      == ["serial:", "_skryto"]
      and czech_script_strings('throw new Error("bez karty"); var a = "no card"; // bez karty\n') == ["bez karty"],
      "a Czech key in an f-string or a dict and a Czech string of a script are found, a sentence and a comment are not")
stale_kept = sorted(f"{path}: {value}" for path, value in KEPT_KEYS
                    if value not in {v for _, v in czech_keys((PROJECT / path).read_text(encoding="utf-8"))})
check(not stale_kept, f"every kept key is still in its file - a gone one leaves the list ({stale_kept})")

print()
print("--- every test's check() takes the condition first ---")
# All tests write check(condition, label). A helper with the label first (part 10f translated
# zkontroluj(popis, podminka) word for word) turns a check written the usual way into
# check(condition="label"): a non-empty string, true forever.


def label_first_checks(sources: dict[str, str]) -> list[str]:
    """The files whose function check() does not take the condition (condition / ok) first."""
    found = []
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "check"
                    and node.args.args and node.args.args[0].arg not in ("condition", "ok")):
                found.append(f"{name}: check({', '.join(a.arg for a in node.args.args)})")
    return found


test_sources = {path.name: path.read_text(encoding="utf-8") for path in sorted((PROJECT / "tests").glob("*.py"))}
check(not label_first_checks(test_sources), f"every check() of the tests takes the condition first ({label_first_checks(test_sources)})")
check(label_first_checks({"a.py": "def check(desc, condition):\n    pass\n", "b.py": "def check(condition, label):\n    pass\n"})
      == ["a.py: check(desc, condition)"], "a trap: a check() with the label first is found, the usual one is not")

print()
print("--- every path to a test named in the repo leads to a file ---")
# Part 10e renamed half of the tests: a comment, TODO.md or DEV_ONLY that still names the old file sends
# the reader nowhere, and DEV_ONLY would quietly copy a dev test into prod. Plans and specs are history.
TEST_PATH = re.compile(r"tests/((?:test_|_)\w+)\.py")


def dead_test_paths(text: str, allowed_missing: set[str]) -> set[str]:
    """Paths tests/<name>.py in the text that lead to no file (minus the ones allowed to be missing)."""
    found = {f"tests/{name}.py" for name in TEST_PATH.findall(text)}
    return {path for path in found if not (PROJECT / path).is_file()} - allowed_missing


trap = "see " + "tests/" + "test_neexistuje.py and tests/_package.py"
check(dead_test_paths(trap, set()) == {"tests/" + "test_neexistuje.py"},
      f"a path to a test that does not exist is found, an existing one passes ({sorted(dead_test_paths(trap, set()))})")
# Dev-only files are missing in prod on purpose (FINISHED_DEV_ONLY names them). In dev every path must lead
# to a file - DEV_ONLY of the sync script included: a stale entry there would quietly ship a renamed dev test to prod.
allowed_missing = set() if SYNC_SCRIPT.is_file() else set(FINISHED_DEV_ONLY)
import subprocess  # noqa: E402

try:
    listed = subprocess.run(["git", "ls-files"], cwd=PROJECT, capture_output=True, text=True)
except OSError:                     # no git at all (the Docker image ships tests/ without .git)
    listed = subprocess.CompletedProcess(["git", "ls-files"], 1, "", "")
if listed.returncode != 0:
    print("       not a git checkout - the paths to tests are not checked")
else:
    dead: list[str] = []
    for name in listed.stdout.split():
        if name.startswith("docs/superpowers/") or name == "CHANGELOG.md":
            continue
        path = PROJECT / name
        if not path.is_file() or path.suffix not in (".py", ".md", ".txt", ".sh", ".html", ".js", ".css", ".yml",
                                                       ".yaml", ".toml", ".cfg", ".json", ""):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        dead += [f"{name}: {p}" for p in sorted(dead_test_paths(text, allowed_missing))]
    check(not dead, f"every path tests/<name>.py in the repo leads to a test that exists ({dead[:5]})")

print()
print("--- what Python hands to a translation is a key of the English template ---")
# Since the translation keys step English is the source: a literal passed to _t(), translate(),
# translate_plural(), plural(), translate_with(), _flash() or as a log message must be a key of
# en.json (a count by its unit) or log/en.json. A Czech sentence written there by habit would be
# looked up, not found and shown in Czech to everybody; the templates' _() and _n() are watched by
# test_translations_dictionary, the log's keys by test_translation_and_refresh.
import json as _json  # noqa: E402

_UI = set(_json.loads((PROJECT / "jellyscope/translations/en.json").read_text(encoding="utf-8")))
_UI |= {k.rsplit("_", 1)[0] for k in _UI if k.rsplit("_", 1)[-1] in ("one", "other")}
_LOG = set(_json.loads((PROJECT / "jellyscope/translations/log/en.json").read_text(encoding="utf-8")))
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_UI_CALLS = {"_t", "translate", "translate_plural", "plural", "translate_with", "Message"}


def foreign_keys(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        # A stored message given as text (`finish_task_log(..., message="...")`, `store_error("...")`) is read as
        # a key when shown, so it must be one.
        stored = [k.value for k in node.keywords if k.arg == "message"] if name == "finish_task_log" else \
            node.args[:1] if name == "store_error" else []
        for value in stored:
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value not in _UI:
                found.append(f"{name} {value.value[:40]!r}")
        if not node.args:
            continue
        is_log = isinstance(node.func, ast.Attribute) and name in _LOG_METHODS and "log" in ast.unparse(node.func.value).lower()
        argument = node.args[1] if name == "_flash" and len(node.args) > 1 else node.args[0]
        if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
            continue
        if is_log and argument.value not in _LOG:
            found.append(f"log {argument.value[:40]!r}")
        elif name == "Message" and not (isinstance(node.func, ast.Attribute) and ast.unparse(node.func.value) == "i18n"):
            continue   # notifications.Message is a notification, not a translation
        elif (name in _UI_CALLS or name == "_flash") and not is_log and argument.value not in _UI:
            found.append(f"{name} {argument.value[:40]!r}")
    return found


not_keys = [f"{path.relative_to(PROJECT)}: {item}" for path in sorted((PROJECT / "jellyscope").rglob("*.py"))
            for item in foreign_keys(path.read_text(encoding="utf-8"))]
check(not not_keys, f"every literal handed to a translation or to the log is an English key ({len(not_keys)} {not_keys[:3]})")
# A trap: a Czech sentence in _t(), in _flash(), in a stored Message and in a log message is found.
_planted = foreign_keys('_t("Přehled")\n_flash(request, "Uloženo.", "success")\nlog.info("hotovo %s", x)\n_t("Overview")\n'
                        'i18n.Message("Hotovo {n}", n=1)\nscanner.finish_task_log(1, "ok", message="Hotovo.")\n'
                        'collector.store_error("Chyba.")\nscanner.finish_task_log(1, "ok", message="The file is empty.")')
check(len(_planted) == 6, f"the guard finds a Czech key in _t(), _flash(), Message(), a stored message and log ({_planted})")

print()
print("--- no Czech text in Python's strings, only data ---")
# A message built in Python (an import's result, a backup's error, the outage page) is an English key
# translated for the reader, like the templates' texts. A Czech literal was shown in Czech to everybody:
# about a hundred of them were left after the translation keys step (21. 11. 2026). What stays Czech is
# data, each with its reason; docstrings are watched by the check of comments above.
CZECH_DATA = {
    "jellyscope/demodata.py": None,   # the sample library's names - data, like titles from Jellyfin
    "jellyscope/languages.py": {      # recognising Czech and Slovak dubbing in a file's name
        r"^(cz|sk|en|de|hu|pl|ru)[-_.]?dab(ing|ovan[eyá])?$", "[^0-9A-Za-zÀ-ž]+"},
    "jellyscope/i18n.py": {"Čeština", "Slovenčina"},   # a language under its own name
    "jellyscope/tasks.py": {"-- Záloha Jellyscope"},  # the mark of a backup written before 1.9.0
    # the header of the backup before the conversion, so 1.8.x restores it (the way back)
    "jellyscope/conversion.py": {"-- Záloha Jellyscope (vlastní export)"},
}


# Czech without diacritics ("Nepodarilo se spojit s Jellyfinem") passes the letters: a sentence is Czech by its
# words too. The words are those of the Czech translation, without accents, that no English text has; a string
# counts when it is prose (a space, no SQL). SQL keeps its Czech columns until the database step.
import unicodedata as _unicodedata  # noqa: E402


def _fold(word: str) -> str:
    return "".join(c for c in _unicodedata.normalize("NFD", word) if _unicodedata.category(c) != "Mn").lower()


_CS = _json.loads((PROJECT / "jellyscope/translations/cs.json").read_text(encoding="utf-8"))
_EN = _json.loads((PROJECT / "jellyscope/translations/en.json").read_text(encoding="utf-8"))
_ENGLISH_WORDS = {w.lower() for t in [*_EN, *_EN.values()] for w in re.findall(r"[A-Za-z]+", t)} | {"stale"}
CZECH_ASCII_WORDS = {_fold(w) for t in _CS.values() for w in re.findall(r"[^\W\d_]+", t) if len(w) >= 3} - _ENGLISH_WORDS
SQL_LIKE = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE|WHERE|CREATE|JOIN|FROM|SET|AND|OR|IN|AS|ON|BY|IS|NULL|NOT|"
                      r"DESC|ASC|THEN|ELSE|END|CASE|COALESCE|NULLIF|DEFAULT|TEXT|INTEGER|GROUP|ORDER)\b")
COLUMN_LIST = re.compile(r"[\w.]+(\s*,\s*[\w.]+)+,?\s*")
# SQL fragments with Czech names that the patterns above do not tell from prose - until the database step.
SQL_KEPT: dict[str, set[str]] = {}      # none left after the database step 6 (the subquery alias of library_page is groups)


def czech_literals(source: str, allowed: set[str] | None = frozenset()) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs or node.value in allowed:
            continue
        text = node.value
        prose = " " in text.strip() and not SQL_LIKE.search(text) and not COLUMN_LIST.fullmatch(text.strip())
        if CZECH_LETTERS.search(text) or (prose and any(w.lower() in CZECH_ASCII_WORDS for w in re.findall(r"[A-Za-z]+", text))):
            found.append((node.lineno, text))
    return found


czech_found = []
_python = [*sorted((PROJECT / "jellyscope").rglob("*.py")), PROJECT / "run.py", PROJECT / "manage.py", PROJECT / "demo.py"]
for path in _python:
    relative = path.relative_to(PROJECT).as_posix()
    allowed = CZECH_DATA.get(relative, frozenset())
    if allowed is None:
        continue
    allowed = set(allowed) | SQL_KEPT.get(relative, set())
    czech_found += [f"{relative}:{line}: {text[:50]!r}" for line, text in czech_literals(path.read_text(encoding="utf-8"), allowed)]
check(not czech_found, f"no Czech string in Python outside the data listed ({len(czech_found)}: {czech_found[:3]})")
# Every exception still exists: one the code no longer has is to go from the list (the database step).
_stale = [f"{f}: {t!r}" for f, texts in {**{k: v for k, v in CZECH_DATA.items() if v}, **SQL_KEPT}.items()
          for t in texts if repr(t)[1:-1] not in (PROJECT / f).read_text(encoding="utf-8") and t not in (PROJECT / f).read_text(encoding="utf-8")]
check(not _stale, f"every allowed Czech string is still in its file ({_stale})")
# A trap: a Czech message, also inside an f-string and without accents, is found; a docstring, SQL with a
# Czech column, an English sentence and an allowed literal are not.
_planted = czech_literals('"""Modul."""\nx = "Záloha selhala."\ny = f"Zbylo {n} souborů"\nz = "Čeština"\n'
                          'a = "Nepodarilo se spojit s Jellyfinem"\nb = "SELECT nazev FROM items"\nc = "Could not connect"\n',
                          {"Čeština"})
check(sorted(line for line, _ in _planted) == [2, 3, 5], f"the guard finds Czech with and without accents ({_planted})")

print()
print("--- no Czech text in the templates and the static files ---")
# A template's texts go through _() as English keys; a word typed straight into the HTML (`<em>jméno</em>`)
# was Czech in every language. Comments may quote a Czech text (step 10c3) - they are left out.
_COMMENTS = {".html": re.compile(r"\{#.*?#\}|<!--.*?-->", re.S), ".css": re.compile(r"/\*.*?\*/", re.S),
             ".js": re.compile(r"/\*.*?\*/|(?<![:\\\w\"'])//[^\n]*", re.S)}


def visible_czech(text: str, suffix: str) -> list[tuple[int, str]]:
    text = _COMMENTS[suffix].sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return [(number, line.strip()[:60]) for number, line in enumerate(text.splitlines(), start=1)
            if CZECH_LETTERS.search(line)]


_web = [*sorted((PROJECT / "jellyscope/templates").rglob("*.html")), *sorted((PROJECT / "jellyscope/static").rglob("*.js")),
        *sorted((PROJECT / "jellyscope/static").rglob("*.css"))]
web_found = [f"{path.relative_to(PROJECT)}:{number}: {line}" for path in _web
             for number, line in visible_czech(path.read_text(encoding="utf-8"), path.suffix)]
check(not web_found, f"no Czech letter outside comments in a template, a script or a style ({len(web_found)}: {web_found[:3]})")
# A trap: a Czech word in the HTML or in a script's string is found, one in a comment is not.
_planted = [len(visible_czech('<em>jméno</em>\n{# komentář\nčeský #}\n<!-- poznámka -->', ".html")),
            len(visible_czech('const a = "http://x"; // poznámka\n/* další\nřádek */', ".js")),
            len(visible_czech('const b = "Uloženo";', ".js")),
            len(visible_czech('a { b: c } /* barva tlačítka */', ".css"))]
check(_planted == [1, 0, 1, 0], f"the web check finds visible Czech only ({_planted})")

print()
print("--- the database: old names of the converted groups are gone from the code ---")
# The database step (docs/superpowers/specs/2026-11-21-database-english-design.md) converts group by group;
# schema_names.DONE lists the groups done. Their old compound names - tables, indexes, setting keys, stored
# values like a card key - may stay only in the map itself and in legacy_urls.py (old names of form fields,
# accepted from an old tab). The bare old words are not guarded: a column word (`nazev`) is still the name in
# other tables, and `hudba` is still the key of the Music area of permissions (step 5).
from jellyscope import schema_names  # noqa: E402

OLD_NAMES_ALLOWED = {"jellyscope/schema_names.py", "jellyscope/legacy_urls.py"}


def done_old_names(map_module: Any = schema_names) -> set[str]:
    """The old compound names of the groups in DONE (a group's entries are those whose new name has it)."""
    found: set[str] = set()
    for group in map_module.DONE:
        for table in map_module.TABLES:
            if group in table.new:          # a table that keeps its name (only its columns change) is no old name
                found |= {*([table.old] if table.old != table.new else []),
                          *(old for old, new in table.indexes.items() if group in new)}
        found |= {old for old, new in map_module.SETTING_KEYS.items() if group in new}
        found |= {old for mapping in map_module.VALUES.values() for old, new in mapping.items() if group in new}
        found |= {old for mapping in getattr(map_module, "JSON_VALUES", {}).values() for old, new in mapping.items()
                  if group in new}
    return {name for name in found if "_" in name}




def old_names_in(root: Path, names: set[str]) -> list[str]:
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b")
    hits = []
    for path in sorted((root / "jellyscope").rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.suffix not in (".py", ".html", ".js", ".css", ".sql") or relative in OLD_NAMES_ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            hits += [f"{relative}:{number}: {m.group(1)}" for m in pattern.finditer(line)]
    return hits


_old = done_old_names()
check({"hudba_skladba", "idx_hudba_skladba_chybi", "task_hudba_enabled", "hudba_alba"} <= _old and "hudba" not in _old
      and "nazev" not in _old, f"the music group's old names ({len(_old)})")
_old_found = old_names_in(PROJECT, _old)
check(not _old_found, f"no old name of a converted group in jellyscope/ ({len(_old_found)}: {_old_found[:3]})")
with tempfile.TemporaryDirectory() as _trap:
    (Path(_trap) / "jellyscope").mkdir()
    (Path(_trap) / "jellyscope" / "a.py").write_text('SQL = "SELECT 1 FROM hudba_skladba"\nb = "hudba_skladba_x"\n', encoding="utf-8")
    (Path(_trap) / "jellyscope" / "b.html").write_text('{{ show_card("hudba_alba") }}\n', encoding="utf-8")
    (Path(_trap) / "jellyscope" / "legacy_urls.py").write_text('OLD = "ui_max_rows_hudba"\n', encoding="utf-8")
    (Path(_trap) / "jellyscope" / "c.py").write_text('Area("seerr_kdo", "who")\nAREA = "knihovna_kdo"\n', encoding="utf-8")
    _planted = old_names_in(Path(_trap), _old)
check(_planted == ["jellyscope/a.py:1: hudba_skladba", "jellyscope/b.html:1: hudba_alba", "jellyscope/c.py:1: seerr_kdo",
                   "jellyscope/c.py:2: knihovna_kdo"],
      f"the guard finds an old name in code and a template, an old permission area (JSON_VALUES too), not a longer "
      f"name or an allowed file ({_planted})")

print()
print("--- no Czech key of a computed dict, a template or a script (database step 6) ---")
# The keys of the dicts the code computes (summaries, a card's rows, template contexts, the JSON for the scripts)
# copied the database's Czech names (`d.pocet`) until the database step 6. A key is Czech when a part of it is a
# Czech word: those of the guards above and of the step's map (old -> new, next to its plan). Data stays Czech:
# the map files, the old addresses and the sample library (as in CZECH_DATA), and the words languages.py recognises
# in file names.
STEP6_MAP = _json.loads((PROJECT / "tests/conversion_step6_keys.json").read_text(encoding="utf-8"))
KEY_WORDS = (CZECH_NAME_WORDS | CZECH_ASCII_WORDS | {part for key in STEP6_MAP for part in key.split("_") if len(part) > 1 and part.isalpha()}
             ) - _ENGLISH_WORDS - {"tech", "hw"}   # tech (a file's technique) and hw (hardware) are English
KEY_DATA_FILES = {"jellyscope/schema_names.py", "jellyscope/legacy_urls.py", "jellyscope/demodata.py"}
# languages.py: the words a file's name or a track's title is recognised by ("cesky", "cestina") - data.
KEYS_KEPT = {("jellyscope/languages.py", word) for word in ("anglicky", "cesky", "cestina", "nemecky", "slovensky", "madarsky",
                                                         "polsky", "rusky", "francouzsky", "italsky", "spanelsky")}
JS_KEY_SCRIPTS = ("jellyscope/static/js/app.js", "jellyscope/static/js/quick-login.js")
_JINJA_CODE = re.compile(r"({{|{%)(.*?)(}}|%})", re.S)
_JINJA_STRING = re.compile(r"""(['"])(?:(?!\1).)*\1""", re.S)


def czech_key(key: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", key)) and (key in STEP6_MAP or any(p in KEY_WORDS for p in key.split("_")))


# Data under these names stays: the old shared *Arr tables and their columns (db.py), the old words fingerprints are
# computed from (scanner/disks.py, arr/bazarr/sync.py), the words a subtitle track is recognised by (languages.py).
KEY_DATA_NAMES = {"_EVENT_COLUMNS", "_TITLE_COLUMNS", "_OLD_ARR_TABLES", "FINGERPRINT_WORDS", "FINGERPRINT_KIND", "_SUBTITLES"}


def python_keys(source: str) -> list[tuple[int, str]]:
    found = []
    tree = ast.parse(source)
    kept = {id(c) for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name) and t.id in KEY_DATA_NAMES
            for c in ast.walk(n.value)}
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
            ast.AsyncFunctionDef)) and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        keys = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in STEP6_MAP \
                and id(node) not in kept and id(node) not in docs:
            keys.append((node.lineno, node.value))     # a key passed by name, a computed value
        if isinstance(node, ast.Dict):
            keys += [(k.lineno, k.value) for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
                     and id(k) not in kept]
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            keys += [(node.lineno, node.slice.value)]
        elif isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop", "setdefault", "group") and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys += [(node.lineno, node.args[0].value)]
            keys += [(node.lineno, kw.arg) for kw in node.keywords if kw.arg]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            keys += [(node.lineno, m) for m in re.findall(r"\(\?P<(\w+)>", node.value)]
            keys += [(node.lineno, m) for m in re.findall(r"\bAS\s+([a-z_]+)\b", node.value)]
        found += [(line, key) for line, key in keys if czech_key(key)]
    return list(dict.fromkeys(found))       # a key both at its place and as an exact string counts once


def template_keys(text: str) -> list[tuple[int, str]]:
    found = []
    for m in _JINJA_CODE.finditer(text):
        code = re.sub(r"_\((['\"]).*?\1", "", m.group(2), flags=re.S)
        line = text.count("\n", 0, m.start()) + 1
        keyed = re.findall(r"""['"]([a-z_]+)['"]""", code)      # a key by name, a computed value
        names = re.findall(r"(?<![\w|])([a-z_][a-z0-9_]*)", _JINJA_STRING.sub("", code))
        found += [(line, key) for key in keyed + names if czech_key(key)]
    return found


def script_keys(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        code = re.sub(r"//.*$", "", line)
        found += [(number, k) for k in re.findall(r"\b(?:data|row|p|entry)\.([a-z_]+)\b", code) + re.findall(r"[{,]\s*([a-z_]+)\s*:", code)
                  if czech_key(k)]
    return found


czech_keys = []
for path in sorted((PROJECT / "jellyscope").rglob("*")):
    relative = path.relative_to(PROJECT).as_posix()
    if relative in KEY_DATA_FILES or "/translations/" in relative:
        continue
    if path.suffix == ".py":
        hits = python_keys(path.read_text(encoding="utf-8"))
    elif path.suffix == ".html":
        hits = template_keys(path.read_text(encoding="utf-8"))
    elif relative in JS_KEY_SCRIPTS:
        hits = script_keys(path.read_text(encoding="utf-8"))
    else:
        continue
    czech_keys += [f"{relative}:{line}: {key}" for line, key in hits if (relative, key) not in KEYS_KEPT]
check(not czech_keys, f"no Czech key in the code, the templates and the scripts ({len(czech_keys)}: {czech_keys[:4]})")
_planted = (python_keys('d = {"pocet": 1}\nx = r["count"]\ny = m.group("dil")\n')
            + template_keys('{{ d.pocet }} {{ _("The count") }} {{ d.count }}')
            + script_keys('if (data.hotovo) {}\nvar x = {items: []};'))
check([k for _, k in _planted] == ["pocet", "dil", "pocet", "hotovo"],
      f"the guard finds a Czech key in a dict, .group(), a template and a script, not an English one ({_planted})")

print()
print("--- the tests' accounts and passwords are English (database step 6) ---")
# A test's local account is test data like any name in the code: "spravce" / "ctenar" / "divak" and passwords
# like "dlouheheslo" were Czech until the database step 6. Left out: the old shape the conversion tests spell out
# (test_conversion_*, _old_*) and the guards whose word lists and traps hold the Czech words on purpose.
ACCOUNT_WORDS = {"spravce", "ctenar", "divak"}
CZECH_PASSWORD = re.compile(r"[a-z]+heslo[a-z0-9]*|heslo[a-z0-9]+")
ACCOUNTS_KEPT = {"test_addresses.py", "test_web_hooks.py", "test_english_code.py"}


def czech_accounts(source: str) -> list[str]:
    """String constants equal to a Czech account name or password; a dict's key, a subscript and .get("…") are keys of
    computed dicts (the names of Jellyfin's details, task 4), not accounts."""
    tree = ast.parse(source)
    keys = {id(k) for node in ast.walk(tree) if isinstance(node, ast.Dict) for k in node.keys if k is not None}
    keys |= {id(node.slice) for node in ast.walk(tree) if isinstance(node, ast.Subscript)}
    keys |= {id(node.args[0]) for node in ast.walk(tree) if isinstance(node, ast.Call) and node.args
             and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop", "setdefault")}
    return [f"{node.lineno}: {node.value}" for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in keys
            and (node.value in ACCOUNT_WORDS or CZECH_PASSWORD.fullmatch(node.value))]


czech_test_accounts = {path.name: hits for path in sorted((PROJECT / "tests").glob("*.py"))
                       if not path.name.startswith(("test_conversion_", "_old_")) and path.name not in ACCOUNTS_KEPT
                       and (hits := czech_accounts(path.read_text(encoding="utf-8")))}
check(not czech_test_accounts, f"no test signs in as spravce / ctenar / divak or with a Czech password "
                               f"({len(czech_test_accounts)} files: {list(czech_test_accounts)[:4]})")
_planted = sorted(czech_accounts('accounts.create("ctenar", "tajneheslo1")\nlogin("reader", "readerpass")\nPARTS = {"heslo"}\n'
                                 'if name == "divak":\n    pass\nd = {"spravce": False}\n'))
check(_planted == ["1: ctenar", "1: tajneheslo1", "4: divak"],
      f"the guard finds a Czech account and password, a comparison too - not the bare word nor a dict's key ({_planted})")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
