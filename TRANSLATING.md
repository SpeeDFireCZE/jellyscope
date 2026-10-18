# Translating Jellyscope

The application is written in Czech, and **Czech is the source language**:
the templates carry whole Czech sentences, not keys like `dashboard.title`.
A translation is a file that maps each of those sentences to your language.

All you need is a text editor. There is no build step and no compiler, and
**a half-finished translation is fine**: a sentence you have not translated
yet falls back to **English**, never to a blank space or a key. Czech shows
only where even the English is missing, and the tests do not allow that.

```
jellyscope/translations/
├── cs.json          the source: key and value are the same Czech sentence
├── en.json          English
├── es.json          Spanish
└── log/
    ├── cs.json      messages written into the application log (source)
    ├── en.json      the same in English
    └── es.json      the same in Spanish
```

The two folders are separate on purpose. `log/` holds technical sentences
for whoever is looking into a problem. They do not belong among button
labels, and translating them is optional.

**The Spanish translation has not been checked.** I do not speak Spanish,
so nobody has proofread it. If something reads oddly or is simply wrong,
a correction is welcome, through Weblate or a pull request.

## The easy way: Weblate

**<https://translate.jellyscope.cz/>** runs a Weblate instance for this
project. It shows the Czech sentence with a box for yours, checks the
placeholders as you type, and sends the result to the repository on its
own. You need no git, no JSON and no pull request.

Everything below describes the same job done by hand. Both work, so pick
whichever suits you.

## Adding a language by hand

1. Copy `jellyscope/translations/cs.json` to `<code>.json`. The code is
   the usual two letters (`de`, `pl`, `sk`), or carries a region where it
   matters (`pt-br`).
2. Replace the **values** with your language. Leave the keys alone: the key
   is what the application looks the sentence up by.

   ```json
   {
     "Přehled": "Übersicht",
     "Knihovna": "Bibliothek"
   }
   ```
3. A sentence you have not translated yet can be deleted or set to `""`;
   either way it falls back to English. A line you leave as it was in
   `cs.json` shows Czech.
4. Start the app. **The language appears in Settings → General by itself**;
   nothing in the code has to change.

To have the language shown under its own name rather than as `DE`, add it
to `JMENA_JAZYKU` in `jellyscope/i18n.py`. It is a nicety, not a
requirement.

The same goes for `log/` if you want the log in your language too.

## Two things that break a translation

**A placeholder that goes missing.** Some sentences carry values:

```json
"v {n} řadách": "in {n} seasons",
"Odklizeno %s prehravani starsich nez %s dni": "Cleared out %s playbacks older than %s days"
```

`{n}` and `%s` are where numbers and names go in. Drop one and the sentence
still reads fine, but it no longer says what it is there to say. The test
suite refuses a translation whose placeholders do not match the source.

**A key that no longer exists.** The key is a Czech sentence, so fixing a
typo in the Czech creates a *new* key and orphans the old translation. The
tests report those as well. The fix is to rename the key in every language
file.

## Checking your work

```bash
.venv/bin/python tests/test_slovnik.py
```

It reads every file in `translations/` and refuses invalid JSON, a key used
twice, a translation of a sentence the source does not have, and
mismatched placeholders. It needs no Jellyfin and no database.

Then look at the application: **Settings → General → Language and time**.

## Sending it in

Through **<https://translate.jellyscope.cz/>** this happens on its own. By
hand, open a pull request against `main` with the file you changed. If you
would rather not use git, open an issue and attach the file.

The maintainer does not proofread translations, since they do not speak
your language. What matters is that the file passes the tests and that you
stand behind the wording.
