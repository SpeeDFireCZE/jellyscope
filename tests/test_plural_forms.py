# -*- coding: utf-8 -*-
r"""Plural forms and notes after '#' in the translation runtime.

A text with a count has one form per plural category of its language
(i18next v4 / CLDR, the categories Weblate asks a translator for):

    cs  one, few, other        "1 záznam", "3 záznamy", "5 záznamů"
    en  one, other             1 record, 3 records
    de  one, other
    es  one, many, other       many = whole millions ("1 000 000 de registros")

In a file such a text is one key per form, `{n} records_one`, `{n} records_few`…
A text that serves more meanings in another language carries a note after `#`
(`Name#person`, `Name#title`); the note is never shown.

What could go wrong without anything failing:

* the wrong form for a number (the Czech "3 záznamů"),
* an empty or missing form shows nothing or the raw key `{n} records_few`,
* a count that is not a whole positive number (None from missing data, 2.0
  from SQL, a negative difference) brings the page down,
* a stored text that happens to contain '#' or to end in `_one` is cut.

Run:
    .venv/bin/python tests/test_plural_forms.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("JELLYSCOPE_HOME", tempfile.mkdtemp())

from jellyscope import formatting, i18n  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- the form for a number, per language ---")
NUMBERS = (0, 1, 2, 4, 5, 11, 21, 22, 1_000_000)
EXPECTED = {
    "cs": ("other", "one", "few", "few", "other", "other", "other", "other", "other"),   # "22 záznamů": few is 2-4 only
    "en": ("other", "one", "other", "other", "other", "other", "other", "other", "other"),
    "de": ("other", "one", "other", "other", "other", "other", "other", "other", "other"),
    "es": ("other", "one", "other", "other", "other", "other", "other", "other", "many"),
    "xx": ("other", "one", "other", "other", "other", "other", "other", "other", "other"),
}
for language, forms in EXPECTED.items():
    got = tuple(i18n.plural_tag(language, n) for n in NUMBERS)
    check(got == forms, f"{language}: {dict(zip(NUMBERS, got))}")
check(i18n.plural_tag("cs", None) == "other", "a missing count is 0 (other)")
check(i18n.plural_tag("cs", 2.0) == "few", "2.0 from SQL is 2 (few)")
check(i18n.plural_tag("cs", -3) == "few", "a negative count takes the form of its absolute value")
# A trap: a rule that returned 'other' for everything would pass the zero and the
# five above - three has to be 'few'.
check(i18n.plural_tag("cs", 3) != "other", "the Czech three is not 'other'")
check(set(i18n.PLURAL_TAGS) >= {"cs", "en", "de", "es"}, f"the four languages have their forms ({sorted(i18n.PLURAL_TAGS)})")
check(i18n.PLURAL_TAGS["cs"] == ("one", "few", "other") and i18n.PLURAL_TAGS["es"] == ("one", "many", "other"),
      "the forms Weblate asks for: cs without 'many', es with it")
# The rules are data, next to the forms: a language with forms but no rule would quietly pick
# "one"/"other" (Slovak "3 záznamov" never in its few form). Each rule returns only the forms of its
# language and reaches all of them.
SAMPLE = list(range(0, 2001)) + [10_000, 21_000, 1_000_000, 2_000_000, 1_500_000]
check(set(i18n.PLURAL_RULES) == set(i18n.PLURAL_TAGS), f"every language with forms has its rule ({sorted(i18n.PLURAL_RULES)})")
for language, tags in i18n.PLURAL_TAGS.items():
    reached = {i18n.plural_tag(language, n) for n in SAMPLE}
    check(reached == set(tags), f"{language}: the rule picks exactly its forms {tags} ({sorted(reached)})")
files = {p.stem for p in (PROJECT / "jellyscope" / "translations").glob("*.json")} \
    | {p.stem for p in (PROJECT / "jellyscope" / "translations" / "log").glob("*.json")}
check(files <= set(i18n.PLURAL_RULES), f"every translation file has its rule (missing: {sorted(files - set(i18n.PLURAL_RULES))})")

print()
print("--- the text of a count and its fallbacks ---")
saved = (i18n.SOURCE, i18n.SOURCE_LANGUAGE, i18n.TRANSLATIONS)
i18n.SOURCE = {"{n} records_one": "{n} record", "{n} records_other": "{n} records",
               "Name#person": "Name", "Name#title": "Name", "{n} tracks by {name}_one": "{n} track by {name}",
               "{n} tracks by {name}_other": "{n} tracks by {name}", "Broken {n}_one": "Broken {n}",
               "Broken {n}_other": "Broken {n}s"}
i18n.SOURCE_LANGUAGE = "en"
i18n.TRANSLATIONS = {
    "cs": {"{n} records_one": "{n} záznam", "{n} records_few": "{n} záznamy", "{n} records_other": "{n} záznamů",
           "Name#person": "Jméno", "{n} tracks by {name}_other": "{n} skladeb od {name}",
           "Broken {n}_other": "Rozbité {count}"},
    "es": {"{n} records_one": "{n} registro", "{n} records_many": "{n} de registros",
           "{n} records_other": "{n} registros"},
}
try:
    check(i18n.translate_plural("{n} records", 1, "cs") == "1 záznam", "cs one")
    check(i18n.translate_plural("{n} records", 3, "cs") == "3 záznamy", "cs few")
    check(i18n.translate_plural("{n} records", 5, "cs") == "5 záznamů", "cs other")
    check(i18n.translate_plural("{n} records", 1234, "cs") == "1 234 záznamů",
          f"the number has the thousands separator ({i18n.translate_plural('{n} records', 1234, 'cs')!r})")
    check(i18n.translate_plural("{n} records", None, "cs") == "0 záznamů", "a missing count reads 0")
    check(i18n.translate_plural("{n} records", 2.0, "cs") == "2 záznamy", "2.0 reads 2 with its form")
    check(i18n.translate_plural("{n} records", 1_000_000, "es") == "1 000 000 de registros", "es many")
    check(i18n.translate_plural("{n} records", 3, "en") == "3 records", "the source language from the template")
    check(i18n.translate_plural("{n} records", 1, "de") == "1 record", "a language without the text falls back to English")
    # A form the translator left out (or Weblate wrote as "" - not loaded) falls back to 'other'.
    check(i18n.translate_plural("{n} tracks by {name}", 3, "cs", name="Mia") == "3 skladeb od Mia",
          "a missing 'few' falls back to the language's 'other', the values are filled in")
    check(i18n.translate_plural("{n} tracks by {name}", 1, "cs", name="Mia") == "1 skladeb od Mia",
          "a missing 'one' too, never the raw key")
    check(i18n.translate_plural("Nobody knows {n}", 2, "cs") == "Nobody knows 2",
          "a text nobody knows shows itself with the number")
    broken = i18n.translate_plural("Broken {n}", 2, "cs")
    check(broken == "Broken 2s", f"a translation with a wrong placeholder falls back to English, it does not crash ({broken!r})")

    print()
    print("--- the note after '#' ---")
    check(i18n.translate("Name#person", "cs") == "Jméno", "a translated note")
    check(i18n.translate("Name#title", "cs") == "Name", "an untranslated note reads the template, without the note")
    check(i18n.translate("Name#title", "en") == "Name", "the source language without the note too")
    check(i18n.translate("chyba #3", "cs") == "chyba #3", "a text that is no key keeps its '#'")
    check(i18n.translate("x_one", "cs") == "x_one", "and its '_one'")
    check(i18n.translate_plural("{n} records", 2, "xx") == "2 records", "an unknown language reads English")
finally:
    i18n.SOURCE, i18n.SOURCE_LANGUAGE, i18n.TRANSLATIONS = saved

print()
print("--- a message by key and values, a count or not ---")
# Error classes, _flash and the parts of a task's result keep a key and its values; translate_with
# picks the form when the key is a text with a count, and only fills in the values otherwise.
saved = (i18n.SOURCE, i18n.SOURCE_LANGUAGE, i18n.TRANSLATIONS)
i18n.SOURCE = {"{n} records_one": "{n} record", "{n} records_other": "{n} records", "Hello {name}": "Hello {name}"}
i18n.SOURCE_LANGUAGE = "en"
i18n.TRANSLATIONS = {"cs": {"{n} records_one": "{n} záznam", "{n} records_few": "{n} záznamy",
                            "{n} records_other": "{n} záznamů", "Hello {name}": "Ahoj {name}"}}
try:
    check(i18n.translate_with("{n} records", "cs", n=3) == "3 záznamy", "a count gets its form")
    check(i18n.translate_with("{n} records", "en", n=1) == "1 record", "in English too")
    check(i18n.translate_with("Hello {name}", "cs", name="Eva") == "Ahoj Eva", "a plain message only fills in")
    check(i18n.translate_with("Hello {name}", "cs") == "Ahoj {name}", "without values the template stays as it is")
    check(i18n.translate_with("{n} records", "cs") == "{n} záznamů", "a count without n: its other form, unfilled")
    # A placeholder may be called language (or key): it is a value of the message, not the argument.
    check(i18n.translate_with("Hello {name}", "cs", name="Eva", language="x") == "Ahoj Eva",
          "a value called language does not clash with the language argument")
    check(i18n.translate_plural("{n} records", 2, "cs", language="x", key="y") == "2 záznamy",
          "nor in translate_plural")
finally:
    i18n.SOURCE, i18n.SOURCE_LANGUAGE, i18n.TRANSLATIONS = saved

print()
print("--- the real dictionary ---")
for key, n, language, expected in (("{n} records", 1, "cs", "1 záznam"), ("{n} records", 3, "cs", "3 záznamy"),
                                   ("{n} records", 5, "cs", "5 záznamů"), ("{n} records", 1, "en", "1 record"),
                                   ("{n} records", 2, "en", "2 records"), ("{n} episodes", 22, "cs", "22 dílů"),
                                   ("{n} people", 4, "cs", "4 lidé"), ("{n} records", 1, "es", "1 registro"),
                                   ("{n} records", 1_000_000, "es", "1 000 000 de registros"),
                                   ("{n} days ago", 1, "cs", "před 1 dnem"), ("{n} days ago", 3, "de", "vor 3 Tagen")):
    got = i18n.translate_plural(key, n, language)
    check(got == expected, f"{language} {n}: {expected!r} ({got!r})")
check(i18n.translate_plural("{name}: {n} new records", 2, "cs", name="Sonarr") == "Sonarr: 2 nové záznamy",
      "a count with another placeholder")
got = i18n.translate_plural("{n} new titles ({total} checked)", 12, "cs", total=12345)
check(got == "12 nových titulů (zkontrolováno 12 345)", f"every whole number of the sentence is written alike ({got!r})")

print()
print("--- the template and the Python helper ---")
from jellyscope import db, web  # noqa: E402

db.init_db()                      # the template asks for the server language
page = web.templates.env.from_string('{{ _n("{n} unknown things", 3) }}|{{ _n("{n} x {name}", 1, name="y") }}').render()
check(page == "3 unknown things|1 x y", f"_n in a template ({page!r})")
check(formatting.plural("{n} unknown things", 4) == "4 unknown things", "formatting.plural in Python")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
