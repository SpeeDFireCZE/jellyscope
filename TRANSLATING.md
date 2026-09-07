# Translating Jellyscope

The application is written in Czech and **Czech is the source language**:
the templates carry whole Czech sentences, not keys like `dashboard.title`.
A translation is a file that maps each of those sentences to your language.

Nothing is required of you but a text editor. There is no build step, no
compiler, and **a half-finished translation is fine** — a sentence without
a translation is shown in Czech rather than as a blank space or a key.

```
jellyscope/translations/
├── cs.json          the source: key and value are the same Czech sentence
├── en.json          English
└── log/
    ├── cs.json      messages written into the application log (source)
    └── en.json      the same in English
```

The two folders are separate on purpose. `log/` holds technical sentences
for whoever is looking into a problem; they do not belong among button
labels, and translating them is optional.

## The easy way: Weblate

**<https://translate.jellyscope.cz/>** runs a Weblate instance for this project. It shows the
Czech sentence and a box to write yours in, checks the placeholders as you
type, and sends the result to the repository itself — no git, no JSON, no
pull request.

Everything below describes the same job done by hand. Both work; pick
whichever suits you.

## Adding a language by hand

1. Copy `jellyscope/translations/cs.json` to `<code>.json` — the code is
   the usual two letters (`de`, `pl`, `sk`), or with a region where it
   matters (`pt-br`).
2. Replace the **values** with your language. Leave the keys alone: the key
   is what the application looks the sentence up by.

   ```json
   {
     "Přehled": "Übersicht",
     "Knihovna": "Bibliothek"
   }
   ```
3. Delete the lines you have not translated yet, or leave them as they are —
   both mean "not translated" and both show Czech.
4. Start the app. **The language appears in Settings → General by itself**;
   nothing has to be changed in the code.

To have the language shown under its own name rather than as `DE`, add it
to `JMENA_JAZYKU` in `jellyscope/i18n.py`. That is a nicety, not a
requirement.

The same goes for `log/` if you want the log in your language too.

## The two things that break a translation

**A placeholder that goes missing.** Some sentences carry values:

```json
"v {n} řadách": "in {n} seasons",
"Odklizeno %s prehravani starsich nez %s dni": "Cleared out %s playbacks older than %s days"
```

`{n}` and `%s` are where numbers and names are inserted. Drop one and the
sentence still reads fine — it just no longer says the thing it exists to
say. The test suite refuses a translation where the placeholders do not
match the source.

**A key that no longer exists.** The key is a Czech sentence, so fixing a
typo in the Czech creates a *new* key and orphans the old translation. The
tests report those as well; the fix is to rename the key in every language
file.

## Checking your work

```bash
.venv/bin/python tests/test_slovnik.py
```

It reads every file in `translations/`, refuses invalid JSON, a key used
twice, a translation of a sentence the source does not have, and mismatched
placeholders. It needs no Jellyfin and no database.

Then look at the application: **Settings → General → Language and time**.

## Sending it in

Through **<https://translate.jellyscope.cz/>** this happens on its own. By hand: open a pull
request against `main` with the file you changed. If you would rather not
use git, open an issue and attach the file — that works too.

Translations are not proofread by the maintainer (who does not speak your
language); what matters is that the file passes the tests and that you
stand behind the wording.
