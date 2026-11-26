# -*- coding: utf-8 -*-
r"""The guard of the *Arr apps' tree: every app in its own folder.

The *Arr page has several apps (Sonarr, Radarr, Bazarr, Prowlarr, more in time). Each has its own
package under `jellyscope/arr/` and beside it its own statistics, cards, templates and tests - whoever
fixes one app reads only its files and a small shared part. Such a split grows together over time: one
convenient import "from the neighbours" is enough. This test guards it.

A) Bazarr and Prowlarr live under `jellyscope/arr/` and no old path is left anywhere
   (`jellyscope/bazarr`, `jellyscope.prowlarr`, …).
B) The registry of apps is `jellyscope/arr/registry.py` - the only place that knows every app. The
   door `arr/__init__.py` does not import it: otherwise every single app would load all the others.
C) Every app has its own package (`arr/sonarr/`, `arr/radarr/`, …). One app's package imports neither
   another's package nor the registry, the door `arr/__init__.py` imports nothing and
   `import jellyscope.arr.<app>` in a clean process loads no other app. The shared package of Sonarr
   and Radarr (`ArrKlient`, `arr.NAZVY`, …) is gone.
D) The statistics are by app (`stats/sonarr.py`, `stats/radarr.py`, …). The shared computations
   (`stats/shared.py`) know no app nor table. One app's statistics do not reach into another's
   tables; another app's data they take only through its public function (Bazarr: the import times of
   files from Sonarr and Radarr).
E) The tables are by app (`sonarr_event`, `radarr_title`, …). The old shared `arr_event` and
   `arr_titul` are known only by the conversion at start-up (db.py) and its test. One app's tables are
   read only by its code.
F) The cards and templates are by app (`cards_sonarr.py`, `_card_sonarr_*.html`, `arr_sonarr.html`,
   …). One app's cards and templates do not know another; the web (`web/arr.py`,
   `web/settings/arr.py`) and the cards' base (`cards_base.py`) know none - they ask the registry.
G) The tests and recorded answers are by app (`tests/test_sonarr_*.py`, `tests/sonarr_responses/`, …).
   There are only three shared `test_arr_*.py`: this guard, the registry of apps (with what concerns
   several apps at once) and the conversion of the old tables. One app's test does not use another's
   helpers nor its package's modules - except the listed relations (Bazarr and Seerr take data from
   Sonarr and Radarr).
H) The guard catches what it should: over a copy of the tree into which a "growing together" is put on
   purpose (a call of another app's statistics from an app's package, another app's template, an app's
   table in shared code, another app's module in a test), it must report an error.

The old names are looked for in `jellyscope/` (code and templates), in `tests/` and in the
documentation in the project's root. Historical designs and plans (`docs/superpowers/`) are not
searched - they describe how it was.

Spec: docs/superpowers/specs/2026-11-15-arr-strom-design.md

Run:
    .venv/bin/python tests/test_arr_tree.py
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PACKAGE = PROJECT / "jellyscope"
ARR = PACKAGE / "arr"
TESTS = PROJECT / "tests"
THIS = Path(__file__).resolve()

# The guard run over a copy of the tree with an error put in (section H): it skips what the copy does not need.
OVER_COPY = os.environ.get("HLIDAC_NAD_KOPII") == "1"

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def sources(*paths: Path, suffixes: tuple[str, ...] = (".py", ".html", ".sql", ".js", ".css", ".md")) -> list[Path]:
    """Text files under the given paths (a file or a folder), without this test."""
    files: list[Path] = []
    for path in paths:
        candidates = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
        files += [f for f in candidates if f.is_file() and f.suffix in suffixes
                  and "__pycache__" not in f.parts and f.resolve() != THIS]
    return files


def occurrences(pattern: str, files: Iterable[Path]) -> list[str]:
    """The places (`file:line`) where a regular expression is found in the files."""
    wanted = re.compile(pattern)
    found = []
    for file in files:
        if not file.is_file():        # a file that is to exist and does not is a finding - not a crash of the test
            found.append(f"{file.relative_to(PROJECT)} chybí")
            continue
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if wanted.search(line):
                found.append(f"{file.relative_to(PROJECT)}:{number}")
    return found


def nowhere(pattern: str, files: Iterable[Path], label: str) -> None:
    found = occurrences(pattern, files)
    check(not found, f"{label} ({', '.join(found[:6])}{' …' if len(found) > 6 else ''})" if found else label)


# Where old names are looked for: code and templates, tests, documentation in the root.
EVERYWHERE = sources(PACKAGE, TESTS, *sorted(PROJECT.glob("*.md")))
CODE = sources(PACKAGE, suffixes=(".py",))

print("--- A) Bazarr and Prowlarr under arr/ ---")
for app in ("bazarr", "prowlarr"):
    check((ARR / app / "__init__.py").is_file(), f"{app} has the package jellyscope/arr/{app}/")
    check(not (PACKAGE / app).exists(), f"jellyscope/{app}/ is gone")
nowhere(r"jellyscope[./](bazarr|prowlarr)\b", EVERYWHERE, "no path jellyscope/bazarr or jellyscope.prowlarr is left anywhere")
nowhere(r"from jellyscope import [^#\n]*\b(bazarr|prowlarr)\b", EVERYWHERE, "nobody imports bazarr/prowlarr straight from jellyscope")
nowhere(r'"jellyscope"\s*/\s*"(bazarr|prowlarr)"', EVERYWHERE, "no path put together from \"jellyscope\" / \"bazarr\"")
# Inside the package jellyscope: `from . import bazarr` at the top level, `from .. import bazarr` a floor lower.
nowhere(r"^\s*from \. import [^#\n]*\b(bazarr|prowlarr)\b", [f for f in CODE if f.parent == PACKAGE],
        "top-level modules take bazarr/prowlarr from .arr")
nowhere(r"^\s*from \.\. import [^#\n]*\b(bazarr|prowlarr)\b", [f for f in CODE if f.parent.parent == PACKAGE and f.parent != ARR],
        "subpackages take bazarr/prowlarr from ..arr")

print("--- B) the registry of apps ---")
check((ARR / "registry.py").is_file() and not (ARR / "seznam.py").exists() and not (PACKAGE / "aplikace.py").exists(),
      "the registry of apps is jellyscope/arr/registry.py; jellyscope/arr/seznam.py and jellyscope/aplikace.py are gone")
check((TESTS / "test_arr_registry.py").is_file() and not (TESTS / "test_arr_seznam.py").exists() and not (TESTS / "test_aplikace.py").exists(),
      "its test is tests/test_arr_registry.py")
nowhere(r"jellyscope[./]aplikace\b|\baplikace\.py\b|\btest_aplikace\b|\barr[./]seznam\b|\bseznam\.py\b|\btest_arr_(seznam|strom)\b", EVERYWHERE,
        "no old name is left anywhere (jellyscope/aplikace.py, arr/seznam.py, test_arr_seznam, test_arr_strom)")
nowhere(r"^\s*from jellyscope import [^#\n]*\baplikace\b", EVERYWHERE, "nobody imports aplikace straight from jellyscope")
nowhere(r"^\s*from \.+ import [^#\n]*\baplikace\b|^\s*from \.+aplikace import", CODE, "code takes the registry from the package arr (arr.registry)")
nowhere(r"^\s*(from|import)\b[^#\n]*\bregistry\b", [ARR / "__init__.py"], "the door arr/__init__.py does not import the registry")

print("--- C) every app has its own package ---")
APPS = ("sonarr", "radarr", "lidarr", "readarr", "bazarr", "prowlarr")
under_arr = sorted(p.name for p in ARR.iterdir() if p.name != "__pycache__")
check(under_arr == sorted(["__init__.py", "registry.py", *APPS]),
      f"under jellyscope/arr/ there are only __init__.py, registry.py and the apps' folders ({under_arr})")
door_imports = [node for node in ast.walk(ast.parse((ARR / "__init__.py").read_text(encoding="utf-8")))
                if isinstance(node, (ast.Import, ast.ImportFrom)) and getattr(node, "module", "") != "__future__"]
check(not door_imports, "the door arr/__init__.py imports nothing")
for app in APPS:
    others = "|".join(a for a in APPS if a != app)
    nowhere(rf"^\s*(from|import)\b[^#\n]*\b({others}|registry)\b", sources(ARR / app, suffixes=(".py",)),
            f"the package {app} imports neither another app's package nor the registry")
    # The package may import the shared `stats` (its own statistics), but with another app's function or cards it
    # would quietly set up a relation to another app that is listed nowhere.
    nowhere(rf"\bstats\.({others}|seerr)_\w+|\bcards_({others}|seerr)\b", sources(ARR / app, suffixes=(".py",)),
            f"the package {app} calls no other app's statistics nor cards")
nowhere(r"\bArrKlient\b|\bArrError\b|\barr\.NAZVY\b|\barr\.APLIKACE\b|\bnastaveni_aplikace\b", EVERYWHERE,
        "no name of the shared package of Sonarr and Radarr is left anywhere")
# The shared package kept its state in dicts by app (`_POSLEDNI[app]`, `_TITULY[app]`). In one app's package it is
# a plain module value - a dict with the constant key `APP` would only confuse.
# (The live state's cache `live.CACHE` stays a dict: tests empty and fill it from outside.)
for app in APPS:
    nowhere(r"\bAPP\b|\b_(POSLEDNI|PRIKAZ|TITULY|LAST|LAST_COMMAND|TITLES)\b", sources(ARR / app, suffixes=(".py",)),
            f"the package {app} keeps no state in a dict with the app's single key")
    progress_file = ARR / app / "progress.py"
    if progress_file.is_file():
        # Nobody reaches the live downloads' cache from outside (tests move the clock): a plain module value.
        nowhere(r"\bprogress\.CACHE\b", sources(ARR / app, suffixes=(".py",)),
                f"nobody refers to the live downloads' cache of the app {app}")
        nowhere(r"\bCACHE\b", [progress_file], f"the live downloads of the app {app} have their cache as a plain module value")
# In a clean process: what of jellyscope.arr loads with one app.
environment = dict(os.environ, JELLYSCOPE_HOME=tempfile.mkdtemp())
for app in () if OVER_COPY else APPS:
    code = (f"import sys, jellyscope.arr.{app}; "
            f"print(sorted(m for m in sys.modules if m.startswith('jellyscope.arr.') and m.split('.')[2] != '{app}'))")
    run = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=environment, capture_output=True, text=True, timeout=180)
    output = run.stdout.strip() or (run.stderr.strip().splitlines() or ["?"])[-1]
    check(run.returncode == 0 and output == "[]", f"import jellyscope.arr.{app} loads no other app nor the registry ({output[:160]})")

print("--- D) statistics by app ---")
STATS = PACKAGE / "stats"
check(not (STATS / "arr.py").exists() and all((STATS / f"{app}.py").is_file() for app in APPS) and (STATS / "shared.py").is_file(),
      "stats/arr.py is gone; every app has stats/<app>.py and the shared computations are in stats/shared.py")
nowhere(r"\bstats\.arr_\w+|\bstats\.arr\b|\barr_(udalosti|prirustky|heatmapa|nejvetsi|rychlost|zdroje|upgrady|prvni|selhani|knihovna|detaily)\b",
        EVERYWHERE, "no shared statistics of Sonarr and Radarr (the old stats/arr.py) are left anywhere")
nowhere(r"^\s*from \.arr import\b", sources(STATS, suffixes=(".py",)), "no statistics take anything from the old stats/arr.py")
nowhere(r"(?i)sonarr|radarr|bazarr|prowlarr|seerr|lidarr|_udalost\b|_titul\b|arr_event", [STATS / "shared.py"],
        "stats/shared.py knows no app nor its table")
nowhere(r"\b(SELECT|FROM|INSERT|UPDATE|DELETE)\b", [STATS / "shared.py"], "stats/shared.py has no database query")
# Whose data an app's statistics may read through another's function (not a table): Bazarr measures the subtitles' speed from a file's import.
MAY_STATS = {"bazarr": {"sonarr", "radarr"}}
for app in APPS:
    others_list = [a for a in APPS if a != app]
    forbidden = "|".join(a for a in others_list if a not in MAY_STATS.get(app, set()))
    nowhere(rf"^\s*(from|import)\b[^#\n]*\b({forbidden})\b", [STATS / f"{app}.py"], f"stats/{app}.py does not import another app's statistics")
    nowhere(r"\b(" + "|".join(others_list) + r")_(udalost|titul|hodina|vyzadane|indexer)\b|\barr_(event|titul)\b(?=[^\n]*app = '(" + "|".join(others_list) + r")')",
            [STATS / f"{app}.py"], f"stats/{app}.py does not read another app's tables")
nowhere(r"\barr_event\b|\barr_titul\b", [STATS / "bazarr.py", STATS / "prowlarr.py", STATS / "seerr.py"],
        "the statistics of Bazarr, Prowlarr and Seerr do not read the tables of Sonarr and Radarr")

print("--- E) tables by app ---")
for schema in ("schema.sql", "schema_postgres.sql"):
    schema_text = (PACKAGE / schema).read_text(encoding="utf-8")
    missing = [t for t in ("sonarr_event", "sonarr_title", "radarr_event", "radarr_title", "lidarr_event", "lidarr_title",
                         "readarr_event", "readarr_title")
               if f"CREATE TABLE IF NOT EXISTS {t} (" not in schema_text]
    check(not missing, f"{schema} creates the tables of Sonarr, Radarr, Lidarr and Readarr ({missing})")
MAY_OLD = {PACKAGE / "db.py", TESTS / "test_arr_table_conversion.py",
           TESTS / "test_conversion_restore.py"}   # a restore drops the old names too (database step 1)
nowhere(r"\barr_event\b|\barr_titul\b", [f for f in EVERYWHERE if f not in MAY_OLD],
        "the old shared tables are known only by the conversion in db.py and its test")
APP_TABLES = {"sonarr": r"sonarr_(event|title)", "radarr": r"radarr_(event|title)", "lidarr": r"lidarr_(event|title)", "readarr": r"readarr_(event|title)", "bazarr": r"bazarr_event",
              "prowlarr": r"prowlarr_(hour|grab|indexer|sample)"}


def card_file(app: str) -> Path:
    """An app's card module: cards_<app>.py (English since step 9)."""
    return PACKAGE / f"cards_{app}.py"


def app_code(app: str) -> list[Path]:
    """The files that know something about an app: its package, statistics and cards (Seerr too, which does not live under arr/)."""
    package = PACKAGE / "seerr" if app == "seerr" else ARR / app
    return sources(package, suffixes=(".py",)) + [f for f in (STATS / f"{app}.py", card_file(app)) if f.is_file()]


for app, pattern in APP_TABLES.items():
    others_code = [f for other in (*APPS, "seerr") if other != app for f in app_code(other)]
    nowhere(rf"\b{pattern}\b", others_code, f"the tables of the app {app} are read only by its code")

print("--- F) cards and templates by app ---")
TEMPLATES = PACKAGE / "templates"


def app_templates(app: str) -> list[Path]:
    """One app's templates: its cards, slices (`_<app>_….html`) and page."""
    page = TEMPLATES / ("seerr.html" if app == "seerr" else f"arr_{app}.html")
    return sorted({*TEMPLATES.glob(f"_card_{app}_*.html"), *TEMPLATES.glob(f"_{app}_*.html"), page})


# What is neither code nor a template of any app is shared - and must not name an app's table: it asks the app's
# functions. The exceptions are db.py (the list of tables for the database conversion and the old tables' conversion at
# start-up) and schema_names.py (the map of the database's old names to the new ones names every table).
APPS_CODE = {f for app in (*APPS, "seerr") for f in (*app_code(app), *app_templates(app))}
SHARED_CODE = [f for f in sources(PACKAGE, suffixes=(".py", ".html"))
               if f not in APPS_CODE and f not in (PACKAGE / "db.py", PACKAGE / "schema_names.py")]
nowhere(r"\b(" + "|".join(APP_TABLES.values()) + r")\b", SHARED_CODE, "shared code names no app's table")
check(not (PACKAGE / "sekce_arr.py").exists() and all(card_file(app).is_file() for app in (*APPS, "seerr"))
      and not any((PACKAGE / f"sekce_{app}.py").exists() for app in (*APPS, "seerr")),
      "the old card modules are gone; every app has cards_<app>.py")
old_templates = sorted(p.name for p in TEMPLATES.glob("_sekce_arr_*.html")) + [n for n in ("arr.html", "_arr_stahuje_se_telo.html")
                                                                              if (TEMPLATES / n).exists()]
check(not old_templates, f"the shared templates of Sonarr and Radarr are gone ({old_templates[:4]})")
for app in APPS:
    check((TEMPLATES / f"arr_{app}.html").is_file() and list(TEMPLATES.glob(f"_card_{app}_*.html")),
          f"{app} has the page template arr_{app}.html and the card templates _card_{app}_*.html")
nowhere(r"(?<![\w])sekce_arr\b|_sekce_arr_\w*|(?<![\w/])arr\.html|_arr_stahuje_se_telo", EVERYWHERE,
        "no name of the shared cards and templates (sekce_arr, _sekce_arr_…, arr.html) is left anywhere")
# The web and the cards' base know no app: they take everything from the registry.
for file in (PACKAGE / "web" / "arr.py", PACKAGE / "web" / "settings" / "arr.py", PACKAGE / "cards_base.py"):
    nowhere(r"^\s*(from|import)\b[^#\n]*\b(sonarr|radarr|bazarr|prowlarr|cards_(sonarr|radarr|bazarr|prowlarr))\b", [file],
            f"{file.relative_to(PACKAGE)} imports no app nor its cards")
# One app's cards and templates do not know another. Listed relations: Bazarr measures the subtitles' speed
# from a file's import in Sonarr and Radarr and links their titles into the library.
MAY_CARDS = {"bazarr": {"sonarr", "radarr"}}
for app in APPS:
    forbidden = "|".join(a for a in APPS if a != app and a not in MAY_CARDS.get(app, set()))
    nowhere(rf"^\s*(from|import)\b[^#\n]*\b({forbidden})\b|\bstats\.({forbidden})_\w+|\bcards_({forbidden})\b",
            [card_file(app)], f"{card_file(app).name} knows no other app's cards, package nor statistics")
    # (`/arr/` without \b before the slash: there is no word boundary between a quote and a slash.)
    nowhere(rf"(\b_card_|\barr_|/arr/)({forbidden})\b|\b_?({forbidden})_\w+\.html", app_templates(app),
            f"the templates of the app {app} refer to no other app's templates nor addresses")
# The cards and templates of Sonarr and Radarr talk only about their own: Sonarr about series and episodes, Radarr about
# movies. The sentence "díl nebo film" (an episode or a movie) in a Radarr card is a leftover from when both apps had
# shared cards. (Bazarr and Seerr work with both, this does not concern them; the apps' packages may explain why the
# connection test guards against mixing them up with the neighbour.)
# The card modules are in English since step 9, the templates in Czech: both languages' words.
FOREIGN_WORDS = {"sonarr": r"\bRadarr\w*|\bLidarr\w*|\bReadarr\w*|\b[Ff]ilm(u|y|ů|em|ech|ům|ový\w*)?\b|\b[Mm]ovies?\b",
                 "radarr": r"\bSonarr\w*|\bLidarr\w*|\bReadarr\w*|\b[Dd]íl(u|y|ů|em|ech|ům)?\b|\b[Ss]eriál(u|y|ů|em|ech|ům|ový\w*)?\b|\b[Ee]pizod(a|y|u|ě|ou|ách|ám)?\b"
                           r"|\b[Ss]eries\b|\b[Ee]pisodes?\b",
                 # Lidarr: music, the unit is the album - no series, episodes, movies nor resolutions.
                 "lidarr": r"\bSonarr\w*|\bRadarr\w*|\bReadarr\w*|\b[Ss]eries\b|\b[Ss]eriál(u|y|ů|em|ech|ům|ový\w*)?\b|\b[Ee]pisodes?\b"
                           r"|\b[Ee]pizod(a|y|u|ě|ou|ách|ám)?\b|\b[Dd]íl(u|y|ů|em|ech|ům)?\b|\b[Mm]ovies?\b"
                           r"|\b[Ff]ilm(u|y|ů|em|ech|ům|ový\w*)?\b|\b[Rr]esolutions?\b|\b[Rr]ozlišení\b",
                 # Readarr: books, the unit is the book - no series, episodes, movies, resolutions, albums, tracks nor artists.
                 "readarr": r"\bSonarr\w*|\bRadarr\w*|\bLidarr\w*|\b[Ss]eries\b|\b[Ss]eriál(u|y|ů|em|ech|ům|ový\w*)?\b|\b[Ee]pisodes?\b"
                            r"|\b[Ee]pizod(a|y|u|ě|ou|ách|ám)?\b|\b[Dd]íl(u|y|ů|em|ech|ům)?\b|\b[Mm]ovies?\b"
                            r"|\b[Ff]ilm(u|y|ů|em|ech|ům|ový\w*)?\b|\b[Rr]esolutions?\b|\b[Rr]ozlišení\b"
                            r"|\b[Aa]lbums?\b|\b[Tt]racks?\b|\b[Aa]rtists?\b"}
check(not re.search(FOREIGN_WORDS["radarr"], "dílčí součet, podíl, rozdíl, oddíly stránky /arr/sonarr")
      and all(re.search(FOREIGN_WORDS["radarr"], sentence) for sentence in ("Díl vyšel", "počet dílů", "karta Sonarru", "seriálu", "epizody",
                                                                             "the series", "an episode"))
      and not re.search(FOREIGN_WORDS["sonarr"], "filmování, /arr/radarr, removes") and all(re.search(FOREIGN_WORDS["sonarr"], s) for s in ("Film", "u filmu", "Radarr", "a movie")),
      "the word check catches whole words in their forms, not random prefixes")
check(not re.search(FOREIGN_WORDS["lidarr"], "dílčí součet, albums, Lidarr, rozdíl, oddíly /arr/sonarr, removes, filmování")
      and all(re.search(FOREIGN_WORDS["lidarr"], sentence) for sentence in ("Karta Sonarru", "Radarr", "the series", "seriálu", "an episode", "díl", "a movie",
                                                                             "u filmu", "resolution", "Rozlišení")),
      "the word check for Lidarr catches the video apps' words, not the album's")
check(not re.search(FOREIGN_WORDS["readarr"], "books, Readarr, authors, files, removes, filmování, dílčí součet")
      and all(re.search(FOREIGN_WORDS["readarr"], sentence) for sentence in ("Karta Sonarru", "Lidarr", "the series", "an episode", "a movie", "an album",
                                                                              "the tracks", "an artist", "resolution")),
      "the word check for Readarr catches the other apps' words (music's too), not the book's")
for app, pattern in FOREIGN_WORDS.items():
    nowhere(pattern, [card_file(app), *app_templates(app)],
            f"the cards and templates of the app {app} do not talk about what belongs to another app")

print("--- G) tests and recorded answers by app ---")
SHARED_TESTS = {"test_arr_tree.py", "test_arr_registry.py", "test_arr_table_conversion.py"}
extra = sorted(f.name for f in TESTS.glob("test_arr_*.py") if f.name not in SHARED_TESTS)
check(not extra and all((TESTS / name).is_file() for name in SHARED_TESTS),
      f"there are only three shared *Arr tests: the tree guard, the registry of apps and the tables' conversion ({extra})")
for app in APPS:
    folder = TESTS / f"{app}_responses"
    recordings = sorted(f.name for f in folder.glob("*.json"))
    check(len(list(TESTS.glob(f"test_{app}_*.py"))) >= 4 and recordings and not [n for n in recordings if n.startswith(APPS)]
          and not (TESTS / f"{app}_odpovedi").exists(),
          f"{app} has its own tests (test_{app}_*.py) and its own recorded answers ({app}_responses/, names without an app's prefix)")
check(not (TESTS / "arr_odpovedi").exists(), "the shared folder of recorded answers (tests/arr_odpovedi) is gone")
nowhere(r"\btest_arr_(?!tree\b|registry\b|table_conversion\b)\w+", EVERYWHERE,
        "no reference to a vanished shared test (test_arr_karty, test_arr_prubeh, …) is left anywhere")
ALL_TESTS = sources(TESTS, suffixes=(".py",))
nowhere(r"\b(ARR_ODPOVEDI|ARR_CESTY|ARR_TITULY|ARR_APLIKACE|arr_obsluha|arr_klient|podvrhni_arr|pripoj_arr|napln_arr|balik_arr|karty_arr"
        r"|klient_arr|chyba_arr|nastaveni_arr|pripojene_arr)\b|\barr_odpovedi\b", [*ALL_TESTS, *sorted(PROJECT.glob("*.md"))],
        "no shared helpers of the Sonarr and Radarr tests (arr_obsluha, pripoj_arr, …) nor their folder are left anywhere")
# The recordings' folders were renamed (`<app>_odpovedi` -> `<app>_responses`, step 3 of the English code): a mention
# of the old name in a test or a document points nowhere. The pattern has no word boundary in front, so it catches
# `tests/<app>_odpovedi/` written with a placeholder too.
nowhere(r"_odpovedi\b", [*ALL_TESTS, *sorted(PROJECT.glob("*.md"))],
        "no old name of a recordings folder (<app>_odpovedi) is left in the tests and documents")
# One app's test uses no other app's helpers, cards, statistics, package nor its package's modules. Listed relations:
# Bazarr measures from a file's import in Sonarr and Radarr; Seerr takes posters and links from them.
# (Another app's addresses in a text - "/arr/radarr gives 404" - are no helpers and need not be guarded.)
WITH_TESTS = (*APPS, "seerr")
MAY_TESTS = {"bazarr": {"sonarr", "radarr"}, "seerr": {"sonarr", "radarr"}}
MODULES = r"client|settings|sync|live|progress|posters|titles|times"
for app in WITH_TESTS:
    forbidden_list = [a for a in WITH_TESTS if a != app and a not in MAY_TESTS.get(app, set())]
    lower, upper = "|".join(forbidden_list), "|".join(a.upper() for a in forbidden_list)
    # Helper names of tests/_apps.py (English since 16. 11. 2026); cards_<app> is the app's card module.
    nowhere(rf"\b(({lower})_(handler|client|history)|(connect|fill|cards)_({lower})|({upper})_(RESPONSES|PATHS))\b"
            rf"|\bstats\.({lower})_\w+|^\s*(from|import)\b[^#\n]*\b({lower})\b|\bjellyscope(\.arr)?\.({lower})\b|\b({lower})\.({MODULES})\b",
            sorted(TESTS.glob(f"test_{app}_*.py")), f"the tests of the app {app} use no other app's helpers, cards, statistics nor package")

if not OVER_COPY:
    print("--- H) the guard catches what it should ---")
    # (file, what is appended to it, a piece of the label of the check that must catch it)
    GROWTHS = (
        ("jellyscope/arr/sonarr/posters.py", "\n_cizi = lambda: stats.radarr_library(set())\n",
         "the package sonarr calls no other app's statistics nor cards"),
        ("jellyscope/arr/radarr/live.py", "\n_cizi = lambda: cards_sonarr.CARDS\n",
         "the package radarr calls no other app's statistics nor cards"),
        ("jellyscope/templates/_card_sonarr_downloading.html", '\n{% include "_radarr_downloading_body.html" %}\n',
         "the templates of the app sonarr refer to no other app's templates nor addresses"),
        ("jellyscope/templates/_radarr_downloading_body.html", '\n<a href="/arr/sonarr">vedle</a>\n',
         "the templates of the app radarr refer to no other app's templates nor addresses"),
        ("jellyscope/web/arr.py", '\n_DOTAZ = "SELECT COUNT(*) FROM sonarr_event"\n',
         "shared code names no app's table"),
        ("jellyscope/cards_base.py", '\n_DOTAZ = "SELECT 1 FROM prowlarr_hour"\n',
         "shared code names no app's table"),
        # The three forms of a shared test helper (tests/_apps.py): <app>_handler/_client/_history,
        # connect_/fill_<app>, <APP>_RESPONSES/_PATHS - and another app's package module (<app>.sync, …).
        ("tests/test_sonarr_cards.py", "\n_cizi = radarr_handler\n", "the tests of the app sonarr use no other app's helpers"),
        ("tests/test_radarr_cards.py", "\n_cizi = fill_sonarr\n", "the tests of the app radarr use no other app's helpers"),
        ("tests/test_prowlarr_cards.py", "\n_cizi = BAZARR_PATHS\n", "the tests of the app prowlarr use no other app's helpers"),
        ("tests/test_sonarr_sync.py", "\n_cizi = radarr.sync.ALREADY_RUNNING\n", "the tests of the app sonarr use no other app's helpers"),
        ("tests/test_radarr_titles.py", "\n# recordings in tests/radarr_odpovedi/\n", "no old name of a recordings folder"),
    )
    with tempfile.TemporaryDirectory() as temporary:
        copy = Path(temporary)
        skip = shutil.ignore_patterns("__pycache__", "_pg_test_*", "static", "translations")
        shutil.copytree(PACKAGE, copy / "jellyscope", ignore=skip)
        shutil.copytree(TESTS, copy / "tests", ignore=skip)
        for file, addition, label in GROWTHS:
            target = copy / file
            original = target.read_text(encoding="utf-8")
            target.write_text(original + addition, encoding="utf-8")
            run = subprocess.run([sys.executable, str(copy / "tests" / "test_arr_tree.py")], cwd=copy, capture_output=True, text=True,
                                 timeout=300, env=dict(os.environ, HLIDAC_NAD_KOPII="1"))
            target.write_text(original, encoding="utf-8")
            errors = [line for line in run.stdout.splitlines() if line.startswith("FAIL")]
            own = [line for line in errors if label in line and f"({file}:" in line]
            check(len(own) == 1 and len(errors) == 1,
                  f"a growth in {file}: the guard reports exactly that one error ({(own or errors or ['reported nothing'])[0][7:130]})")

print(f"\nDONE - failures: {failures}")
sys.exit(1 if failures else 0)
