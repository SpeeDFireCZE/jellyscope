# Translating Jellyscope

The application is written in English, and **English is the source
language**: the templates carry whole English sentences, not keys like
`dashboard.title`. A translation is a file that maps each of those
sentences to your language. Czech is a translation like any other.

All you need is a text editor. There is no build step and no compiler, and
**a half-finished translation is fine**: a sentence you have not translated
yet shows in **English**, never as a blank space or a key.

```
jellyscope/translations/
├── en.json          the template: every sentence of the application, in English
├── cs.json          Czech
├── de.json          German
├── es.json          Spanish
└── log/
    ├── en.json      messages written into the application log (the template)
    ├── cs.json      the same in Czech
    ├── de.json      the same in German
    └── es.json      the same in Spanish
```

The two folders are separate on purpose. `log/` holds technical sentences
for whoever is looking into a problem. They do not belong among button
labels, and translating them is optional. The log file itself is always
written in English; Settings → Log shows each line in the reader's language
with these translations.

**The Spanish and German translations have not been proofread** by the
maintainer, who does not speak those languages. If something reads oddly or
is simply wrong, a correction is welcome, through Weblate or a pull request.

## Three shapes of a key

**A sentence.** Most keys are the English sentence itself:

```json
"Overview": "Übersicht"
```

**A sentence with a note.** Where one English word means two things in other
languages, the key carries a note after `#`. The note is never shown, it only
tells you which meaning is meant:

```json
"Name#person": "Jméno",
"Name#title": "Název",
"On#feminine": "Zapnutá"
```

**A text with a count.** A sentence with a number has one key per plural form
of your language, the way the [CLDR plural rules] name them (the i18next v4
format):

```json
"{n} records_one": "{n} záznam",
"{n} records_few": "{n} záznamy",
"{n} records_other": "{n} záznamů"
```

English and German have `one` and `other`; Czech `one`, `few` and `other`;
Spanish `one`, `many` (whole millions: "1 000 000 de registros") and `other`.
The application picks the form by the number. A form you leave out falls
back to your `other`, then to English.

[CLDR plural rules]: https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html

## The easy way: Weblate

**<https://translate.jellyscope.cz/>** runs a Weblate instance for this
project. It shows the English sentence with a box for yours (one box per
plural form), the note after `#` as the key, checks the placeholders as you
type, and sends the result to the repository on its own. You need no git,
no JSON and no pull request.

Everything below describes the same job done by hand. Both work, so pick
whichever suits you.

## Adding a language by hand

1. Copy `jellyscope/translations/en.json` to `<code>.json`. The code is
   the usual two letters (`pl`, `sk`, `fr`), or carries a region where it
   matters (`pt-br`).
2. Replace the **values** with your language. Leave the keys alone: the key
   is what the application looks the sentence up by.
3. For a text with a count, write the forms your language has
   (`_one`, `_few`, `_many`, `_other` - see the CLDR rules above) and delete
   the ones it does not.
4. A sentence you have not translated yet can be deleted or set to `""`;
   either way it falls back to English.
5. Start the app. **The language appears in Settings → General by itself.**

Every language also needs its plural rule in `jellyscope/i18n.py`: the
forms in `PLURAL_TAGS` and the function that picks them in `PLURAL_RULES`
(most Slavic languages differ from English). The test suite asks for both and
checks that the rule picks exactly the forms listed; the maintainer adds them
with your file. To
have the language shown under its own name rather than as `PL`, add it to
`LANGUAGE_NAMES` there too.

The same goes for `log/` if you want Settings → Log in your language too.

## Two things that break a translation

**A placeholder that goes missing.** Some sentences carry values:

```json
"in {n} seasons_other": "in {n} Staffeln",
"Cleared out %s playbacks older than %s days": "%s Wiedergaben älter als %s Tage entfernt"
```

`{n}`, `{name}` and `%s` are where numbers and names go in. Drop one and the
sentence still reads fine, but it no longer says what it is there to say. The
test suite refuses a translation whose placeholders do not match the source;
in the log the order of `%s` and `%d` matters too.

**A key that no longer exists.** The key is an English sentence, so fixing
a typo in the English creates a *new* key and orphans the old translation.
The tests report those as well (Weblate shows them as outdated).

## Checking your work

```bash
.venv/bin/python tests/test_translations_dictionary.py
```

It reads every file in `translations/` and refuses invalid JSON, a key used
twice, a translation of a sentence the source does not have, plural forms
your language does not have, and mismatched placeholders. It needs no
Jellyfin and no database.

Then look at the application: **Settings → General → Language and time**.

## Sending it in

Through **<https://translate.jellyscope.cz/>** this happens on its own. By
hand, open a pull request against `main` with the file you changed. If you
would rather not use git, open an issue and attach the file.

The maintainer does not proofread translations, since they do not speak
your language. What matters is that the file passes the tests and that you
stand behind the wording.

## For the maintainer: the Weblate component

Since 1.9.0 the files are in the **i18next JSON file v4** format with
`en.json` as the monolingual base. The switch is done once, together with the
release that brings the new files:

1. In Weblate, settle the suggestions, the "needs editing" marks and the
   comments, commit and push everything pending, and **lock** the component.
   Suggestions and comments live only in Weblate's database and hang on the old
   keys, which disappear; the history stays in git, and the translation memory
   offers the old texts as suggestions.
2. If prod's translation files changed since dev last took them (Weblate, a
   pull request), bring them into dev first:
   `deploy/convert-translations.py merge deploy/translation-keys-map.json --prod <prod> --dev <dev>`
   (three-way against the recorded baselines; a conflict writes nothing and
   lists what to decide), commit in dev, and only then sync and push.
3. In the component's settings (the interface and the log): file format
   "i18next JSON file v4"; monolingual base file
   `jellyscope/translations/en.json` (`…/log/en.json` for the log); source
   language English; template for new translations `en.json`; check flag
   `python-brace-format` (for `{n}`, `{name}`).
4. Pull the changes from the repository, check the counts of translated
   strings and, on one text with a count (`{n} records`), the forms: Czech 3,
   Spanish 3, German and English 2. Unlock the component.

A translation that arrives in the old shape later (Czech sentences as keys) is
converted with `deploy/convert-translations.py convert` (see its docstring).
