# -*- coding: utf-8 -*-
r"""Translation files: no doubled keys, no dead entries, whole sentences.

The translations are in `jellyscope/translations/`, one file per language, and
even somebody who does not program may touch them - through a translation tool
or right in an editor. This test is the safeguard against what can go wrong
there without anything failing:

* **The same key twice.** JSON allows writing it and the reader takes the
  last one - silently. Back when the dictionary lived in Python, it happened
  twenty-six times, and for eight of them the two translations differed:

    * the Network page said "Last run" for a device instead of "Last seen",
    * a series said "at 3 seasons" instead of "in 3 seasons",
    * the database tables said "Lines" instead of "Rows".

  In Czech it is the same word every time, in English it is not - and one
  key cannot carry two things.

* **An entry used nowhere.** It breaks nothing, it only makes the dictionary
  a dump in which a translation is harder to find (and the translator wastes
  time on it).

* **A lost placeholder.** "{n} dílů" -> "episodes" looks like a sentence, it
  just lacks the number it came into being for.

* **A translation of a sentence the source does not know.** Typically after a
  typo is fixed in the English: the key changed and the old translation stayed
  hanging.

* **A wrong set of plural forms.** A text with a count is one key per form of
  the language (`{n} records_one`, `_few`, `_other`); Czech needs one/few/other,
  English one/other - a form outside the language's set is never shown.

The source is English: `en.json` is the template (key = text; a key with a
note after '#' has the text without the note; a text with a count has its one
and other forms), the other files - Czech included - translate it.

Run:
    .venv/bin/python tests/test_translations_dictionary.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


FOLDER = PROJECT / "jellyscope" / "translations"
FILES = sorted(FOLDER.glob("*.json")) + sorted((FOLDER / "log").glob("*.json"))


def label_of(path: Path) -> str:
    return f"{path.parent.name}/{path.name}" if path.parent.name == "log" \
        else path.name


def load_with_duplicates(path: Path) -> tuple[dict[str, str], list[str]]:
    """The file's content and the keys that were in it more than once."""
    doubled: list[str] = []
    seen: set[str] = set()

    def hook(pairs):
        for key, _value in pairs:
            if key in seen:
                doubled.append(key)
            seen.add(key)
        return dict(pairs)

    return json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=hook), doubled


print("--- the files can be read ---")
check(bool(FILES), f"translations were found ({[label_of(c) for c in FILES]})")
contents: dict[str, dict[str, str]] = {}
for path in FILES:
    try:
        data, doubled = load_with_duplicates(path)
    except ValueError as error:
        check(False, f"{label_of(path)} is valid JSON ({error})")
        continue
    contents[label_of(path)] = data
    check(not doubled,
          f"{label_of(path)}: {len(data)} sentences, duplicates: {doubled[:3] or 'none'}")

from jellyscope import i18n  # noqa: E402

FORM = re.compile(r"^(?P<unit>.+)_(?P<form>zero|one|two|few|many|other)$")
# Values from data shown through `_(value)`: the key is what the database holds, the
# English text differs ("Transcode" -> "Transcodes").
DATA_KEYS = {"Transcode"}


def units(data: dict[str, str]) -> dict[str, set[str]]:
    """The texts with a count in a file: unit -> its forms."""
    found: dict[str, set[str]] = {}
    for key in data:
        m = FORM.match(key)
        if m:
            found.setdefault(m.group("unit"), set()).add(m.group("form"))
    return found


print()
print("--- a translation knows no sentence the source does not know ---")
# When a typo is fixed in the English, the translation keeps the old key - and it
# is never used again. Weblate shows such a sentence as "outdated", here it is an
# error. A form of a count is allowed when the source has the count.
for prefix, source_name in (("", "en.json"), ("log/", "log/en.json")):
    source = contents.get(source_name, {})
    if not source:
        continue
    source_units = units(source)
    for name, data in contents.items():
        if name == source_name or not name.startswith(prefix) \
                or (prefix == "" and name.startswith("log/")):
            continue
        extra = sorted(k for k in data if k not in source
                       and not (FORM.match(k) and FORM.match(k).group("unit") in source_units))
        check(not extra,
              f"{name}: sentences outside the source: {len(extra)} {[v[:30] for v in extra[:2]]}")
        language = Path(name).stem
        allowed = set(i18n.PLURAL_TAGS.get(language, ("one", "other")))
        wrong_forms = sorted(f"{u} {sorted(f - allowed)}" for u, f in units(data).items() if f - allowed)
        check(not wrong_forms, f"{name}: forms of its language only ({sorted(allowed)}): {wrong_forms[:3]}")
        if language == "cs":
            partial = sorted(u for u, f in units(data).items() if f != set(allowed))
            check(not partial, f"{name}: every count has one, few and other ({partial[:3]})")

print()
print("--- Czech stays complete ---")
# Czech was the source until 21. 11. 2026 and could not miss a sentence; as a translation it can:
# a new English key without its Czech would show in English to every Czech user.


def missing_in(translation: dict[str, str], template: dict[str, str], language: str) -> list[str]:
    tags = i18n.PLURAL_TAGS.get(language, ("one", "other"))
    missing = []
    for key in template:
        m = FORM.match(key)
        if m:
            if m.group("form") == "other" and any(f"{m.group('unit')}_{t}" not in translation for t in tags):
                missing.append(m.group("unit"))
        elif not translation.get(key, "").strip():
            missing.append(key)
    return missing


for name, template_name in (("cs.json", "en.json"), ("log/cs.json", "log/en.json")):
    gaps = missing_in(contents.get(name, {}), contents.get(template_name, {}), "cs")
    check(not gaps, f"{name}: every sentence of the template is translated ({len(gaps)} {[g[:30] for g in gaps[:3]]})")
check(missing_in({"A": "a", "{n} x_one": "1"}, {"A": "A", "B": "B", "{n} x_one": "1", "{n} x_other": "n"}, "cs")
      == ["B", "{n} x"], "a trap: a missing sentence and a count without all its Czech forms are found")

print()
print("--- the template really is the source ---")
# In en.json the key is the text. Were they to differ, the translator would see in
# Weblate another sentence than the one in the application. Only a note after '#'
# (not shown) and the forms of a count differ.
for name in ("en.json", "log/en.json"):
    data = contents.get(name, {})
    different = [k for k, v in data.items() if not FORM.match(k) and k not in DATA_KEYS
                 and v != k.split("#", 1)[0]]
    check(not different,
          f"{name}: key = text ({len(different)} not matching {[k[:25] for k in different[:2]]})")
    forms = sorted(u for u, f in units(data).items() if f != {"one", "other"})
    check(not forms, f"{name}: every count has its one and other ({forms[:3]})")
check(not [k for k in contents.get("log/en.json", {}) if "#" in k or FORM.match(k)],
      "the log has neither notes nor counts")

from jinja2 import Environment  # noqa: E402

_JINJA = Environment()
TEMPLATES = sorted((PROJECT / "jellyscope" / "templates").rglob("*.html"))


def template_keys(text: str, call: str = "_") -> list[str]:
    """Literal keys `_("...")` / `_('...')` as Jinja will see them.

    Jinja's lexer leaves out comments {# #} and unescapes the string right away
    (\\" and \\n), so the key is exactly the one looked for in the dictionary at
    run time. A regular expression could not do it before: it overlooked a
    sentence with \\"možné\\" inside, and it stayed in Czech on the English and
    the Spanish page.
    """
    tokens = list(_JINJA.lexer.tokenize(text))
    return [c.value for a, b, c, d in zip(tokens, tokens[1:], tokens[2:], tokens[3:])
            if (a.type, a.value) == ("name", call) and b.type == "lparen"
            and c.type == "string" and d.type in ("rparen", "comma")]


def without_comments(text: str) -> str:
    # A commented-out `{# _("an old sentence") #}` is no use.
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


print()
print("--- no extra entry ---")
# Every key of the template is a literal somewhere in the code - exactly, not as a
# part of a longer sentence: a half of a split sentence ("the start " "the rest")
# whose whole is its own key is dead, and a search for a substring let fifteen
# such entries through until 21. 11. 2026. Python is read through its syntax tree
# (adjacent literals are one string there), a template through Jinja's lexer
# (every string, not only the argument of _(): keys stand in macro calls and
# lists too).
import ast  # noqa: E402

python_literals: set[str] = set()
for path in (list((PROJECT / "jellyscope").glob("**/*.py"))
             + [PROJECT / x for x in ("run.py", "manage.py", "demo.py")]):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            python_literals.add(node.value)
template_literals: set[str] = set()
for path in TEMPLATES:
    template_literals |= {t.value for t in _JINJA.lexer.tokenize(path.read_text(encoding="utf-8"))
                          if t.type == "string"}
code_literals = python_literals | template_literals


def used_keys(data: dict[str, str]) -> set[str]:
    return {FORM.match(k).group("unit") if FORM.match(k) else k for k in data}


for name, literals in (("en.json", code_literals), ("log/en.json", python_literals)):
    unused = sorted(k for k in used_keys(contents.get(name, {})) if k not in literals)
    check(not unused, f"{name}: without use {len(unused)} {[k[:35] for k in unused[:3]]}")
# A trap: adjacent literals are one key, and its half is no literal of its own.
_trial = {node.value for node in ast.walk(ast.parse('x = ("the start "\n     "the rest")'))
          if isinstance(node, ast.Constant) and isinstance(node.value, str)}
check(_trial == {"the start the rest"}, f"a split sentence is one literal, its half none ({_trial})")

print()
print("--- what the templates want translated is in the source ---")
# Otherwise the key itself, or a note after '#', appears on the page. Only literal
# keys are checked: `_(variable)` cannot be read statically. A count (_n) needs
# its forms in the source.
source = contents.get("en.json", {})
source_units = units(source)
missing = []
for path in TEMPLATES:
    text = path.read_text(encoding="utf-8")
    for key in template_keys(text):
        if len(key) >= 2 and key not in source and not key.startswith("{"):
            missing.append(f"{path.name}: {key[:40]!r}")
    for key in template_keys(text, call="_n"):
        if key not in source_units:
            missing.append(f"{path.name}: count {key[:40]!r}")
check(not missing, f"sentences missing in the source: {len(missing)} {missing[:5]}")

# A trap: the search has to handle a sentence with an escaped quote and in
# apostrophes, and must not count a comment - otherwise the check above would
# quietly miss exactly the cases it came into being for.
check(template_keys('{{ _("proto \\"možné\\".") }}{{ _(\'a\') }}{# {{ _("komentář") }} #}')
      == ['proto "možné".', "a"],
      "the search recognises a sentence with quotes inside, apostrophes and a comment")

print()
print("--- a text with a count is never looked up without its form ---")
# `_("{n} records")` would find nothing (the file has "{n} records_one", "_few", "_other")
# and show the key itself; `_n()`, `plural()`, `translate_plural()` or a message by key and
# values (_flash, the error classes, a task's parts - translate_with) pick the form.
PLAIN_LOOKUP = re.compile(r"(?<![\w.])(?:_|_t|translate|i18n\.translate)\(\s*([\"'])((?:\\.|(?!\1).)*)\1\s*[,)]")


def plain_lookups(text: str) -> list[str]:
    return [m.group(2) for m in PLAIN_LOOKUP.finditer(text) if m.group(2) in source_units]


def plain_lookups_python(source: str) -> list[str]:
    """The same in Python, through the syntax tree: a key split over adjacent literals is one string there."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value in source_units:
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in ("_t", "translate"):
                found.append(node.args[0].value)
    return found


# Python, the other way round: every literal of a count's key reaches a call that picks the form -
# translate_plural, plural, translate_with, _flash, an error class (its translated() uses
# translate_with), a task's parts (consumed by translate_with) or a helper of the import message,
# whose own lookup is one of those (checked below). A key handed on to anything else - a helper
# that looked it up plainly - wrote the English key on a Czech page (the import message, 21. 11.).
FORM_CALLS = {"translate_plural", "plural", "translate_with", "_flash", "error_class", "Message"}   # i18n.Message picks it when read
KEY_HELPERS = {"_import_message", "_sentence", "_import_from_file"}


def stray_counts(source: str) -> list[str]:
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and node.value in source_units):
            continue
        parent = parents.get(node)
        name = ""
        if isinstance(parent, ast.Call):
            name = parent.func.id if isinstance(parent.func, ast.Name) else getattr(parent.func, "attr", "")
        part = isinstance(parent, ast.Tuple) and isinstance(parents.get(parent), ast.Call) \
            and getattr(parents[parent].func, "attr", "") == "append"
        if not (name in FORM_CALLS | KEY_HELPERS or name.endswith("Error") or part):
            found.append(node.value)
    return found


python_sources = {path: path.read_text(encoding="utf-8") for path in (PROJECT / "jellyscope").glob("**/*.py")}
stray = [f"{path.name}: {k[:40]!r}" for path, text in python_sources.items() for k in stray_counts(text)]
check(not stray, f"every count's key in Python reaches a call that picks its form ({len(stray)} {stray[:3]})")
helpers_ok = []
for path, text in python_sources.items():
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in KEY_HELPERS:
            called = {c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
                      for c in ast.walk(node) if isinstance(c, ast.Call)}
            # _flash picks a form only for its own message - a helper must pick it itself or hand on.
            picks = {"translate_plural", "plural", "translate_with"} | (KEY_HELPERS - {node.name})
            helpers_ok.append(node.name if called & picks else f"{node.name}: NO")
check(sorted(helpers_ok) == sorted(KEY_HELPERS), f"and each helper hands it on to one of them ({sorted(helpers_ok)})")
check(stray_counts('x = helper("{n} records")\ny = _t("{n} records")') == ["{n} records", "{n} records"]
      and not stray_counts('plural("{n} records", 3)') if "{n} records" in source_units else True,
      "a trap: a count handed to another function is found")

wrong = []
for path in TEMPLATES:
    wrong += [f"{path.name}: {k[:40]!r}" for k in plain_lookups(path.read_text(encoding="utf-8"))]
for path in (PROJECT / "jellyscope").glob("**/*.py"):
    wrong += [f"{path.name}: {k[:40]!r}" for k in plain_lookups_python(path.read_text(encoding="utf-8"))]
check(not wrong, f"every count goes through its forms ({len(wrong)} {wrong[:3]})")
# A trap: the search finds a count looked up plainly, in a template and in Python.
check(plain_lookups('{{ _("{n} records").format(n=3) }}') == ["{n} records"]
      and plain_lookups_python('x = _t("{n} " "records").format(n=3)') == ["{n} records"],
      "the search finds `_(count)` in a template and `_t(count)` in Python, split over two literals too")

print()
print("--- the placeholders fit in every language ---")
# "{n} dílů" -> "{n} episodes". Were {n} missing in the translation, the number
# would quietly get lost: the sentence makes sense, only the figure it came
# into being for is not in it.
#
# For the log it is the same with "%s", only the order matters there too: the
# values are put in by position, so a swapped "%d ... %s" raises a TypeError at
# run time and the message gets lost from the log. Named places %(x)s need no
# order. The pattern knows %02d too (S%02dE%02d for episodes of a series).
LOG_PATTERN = re.compile(r"%(?:\(\w+\))?0?\d*[sdrf]")
UI_PATTERN = re.compile(r"\{(\w+)\}")


def mismatched(key: str, translation: str, log: bool) -> bool:
    pattern = LOG_PATTERN if log else UI_PATTERN
    in_key, in_translation = pattern.findall(key), pattern.findall(translation)
    if log and not any("(" in m for m in in_key):
        return in_key != in_translation
    return sorted(in_key) != sorted(in_translation)


def format_fails(key: str, translation: str) -> bool:
    # A template calls `_("Po {n} ...").format(n=...)` without a safeguard. A lone
    # "{" or an empty "{}" in a translation pass the pattern above, but .format()
    # fails on them and the page ends with an error 500.
    places = UI_PATTERN.findall(key)
    if not places:
        return False
    try:
        translation.format(**dict.fromkeys(places, "1"))
    except (KeyError, IndexError, ValueError):
        return True
    return False


for name, data in contents.items():
    log = name.startswith("log/")
    wrong, broken = [], []
    for key, translation in data.items():
        # An empty translation = not translated (that is how Weblate writes it);
        # it is not loaded and falls back to English, so there is nothing to compare.
        if not translation.strip():
            continue
        if mismatched(key, translation, log):
            wrong.append(f"{key[:35]!r}")
        if not log and format_fails(key, translation):
            broken.append(f"{key[:35]!r}")
    check(not wrong, f"{name}: {len(wrong)} not matching {wrong[:3]}")
    if not log:
        check(not broken, f"{name}: .format() passes for every sentence ({len(broken)} {broken[:3]})")

# A trap: the checks have to recognise a swapped order, %02d and a broken bracket.
check(mismatched("na %s (%d/%d)", "(%d/%d) na %s", log=True), "recognises a swapped %s and %d in the log")
check(mismatched("S%02dE%02d", "S%dE%d", log=True), "recognises a lost %02d")
check(format_fails("Po {n} pokusech", "Tras {n} intentos {"), "recognises a lone bracket")
check(not format_fails("Po {n} pokusech", "Tras {n} intentos"), "and a right translation passes")

print()
print("--- and a language is recognised by its file, not by the code ---")
check(set(i18n.LANGUAGES) >= {"cs", "en"},
      f"Czech and English are on offer ({sorted(i18n.LANGUAGES)})")
check(i18n.LANGUAGES["en"] == "English" and i18n.LANGUAGES["cs"] == "Čeština", "and they go by their own names")
check(list(i18n.LANGUAGES) == sorted(i18n.LANGUAGES), f"in alphabetical order ({list(i18n.LANGUAGES)})")

# A new language = one more file. It is tried on a temporary folder, so nothing
# is slipped into the package.
import tempfile  # noqa: E402

temp = Path(tempfile.mkdtemp())
(temp / "de.json").write_text('{"Overview": "Übersicht"}', encoding="utf-8")
(temp / "en.json").write_text('{"Overview": "Overview"}', encoding="utf-8")
loaded = i18n._load_folder(temp)
check(set(loaded) == {"de"},
      f"one more file = one more language, the source is not loaded ({sorted(loaded)})")

# And a broken file may bring down only itself, not the application.
(temp / "xx.json").write_text("{this is not JSON", encoding="utf-8")
loaded = i18n._load_folder(temp)
check(set(loaded) == {"de"}, "a broken translation is skipped, the application keeps running")

print()
print("--- an unfinished translation falls back to English ---")
# Whoever switches German on while the translation is half done reads the rest in
# English - the source, which nearly everybody who got as far as a media server reads.
i18n.TRANSLATIONS["trial"] = {"Overview": "Prehľad"}
try:
    check(i18n.translate("Overview", "trial") == "Prehľad",
          "a translated sentence is taken from that language")
    fallback = i18n.translate("Library", "trial")
    check(fallback == "Library", f"an untranslated one falls back to English ({fallback!r})")
    check(i18n.translate("Library", "cs") == "Knihovna", "Czech is a translation like the others")
    check(i18n.translate("Library", "en") == "Library", "English is the source")
    check(i18n.translate("Name#person", "trial") == "Name", "an untranslated note shows the text without the note")
    # A sentence nobody knows stays as it is - better than emptiness.
    check(i18n.translate("Nobody translated this", "trial") == "Nobody translated this",
          "what nobody knows stays as it is")

    # The same for the log, which is passed on with a failure.
    i18n.LOG_TRANSLATIONS["trial"] = {}
    message = "Cleared out %s playbacks older than %s days"
    check(message in i18n.LOG_EN and i18n.translate_log(message, "trial") == message,
          "the log falls back to English too")
    check(i18n.translate_log(message, "cs") == "Odklizeno %s prehravani starsich nez %s dni",
          "and the Czech log keeps its sentences")
finally:
    i18n.TRANSLATIONS.pop("trial", None)
    i18n.LOG_TRANSLATIONS.pop("trial", None)

# Weblate writes untranslated keys as an empty string. That must not be loaded
# as a translation - the page would show nothing instead of English.
import tempfile as _tf  # noqa: E402

_folder = Path(_tf.mkdtemp())
(_folder / "xx.json").write_text(
    '{"Knihovna": "", "Přehled": "   ", "Nastavení": "Ajustes"}', encoding="utf-8")
loaded_one = i18n._load(_folder / "xx.json")
check(loaded_one == {"Nastavení": "Ajustes"},
      f"an empty translation from Weblate is not loaded, only the real one ({loaded_one})")

print()
# A trial of the translation through the public interface.
sentence = i18n.translate_plural("in {n} seasons", 3, "cs")
check(sentence == "v 3 řadách" and i18n.translate_plural("in {n} seasons", 1, "cs") == "v 1 řadě",
      f"the sentence about seasons: {sentence!r}")
period = i18n.translate("over {period}", "cs").format(period="30 dní")
check(period == "za 30 dní", f"the sentence about a period: {period!r}")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
