# Contributing

Thanks for looking. This is a small project with one author, so the rules are
short.

## Before you start

**Open an issue first** for anything bigger than a bug fix. It costs you five
minutes and can save you an afternoon spent on something I would not merge,
because the project deliberately stays small (see *Scope* below).

## Running it

```bash
python3 -m venv .venv                              # Python 3.11 or newer
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

You do not need a Jellyfin server to develop: `demo.py` fills the database with
made-up data and starts the app on <http://127.0.0.1:8098>.

```bash
.venv/bin/python demo.py
```

## Tests

There is no pytest. The tests are plain scripts. Each one sets up its own
temporary database, prints `OK` or `FAIL` per check, ends with
`DONE - failures: 0` when everything passed and exits non-zero when
something fails:

```bash
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done
```

They need no Jellyfin, no network and no ffmpeg, and they must **all** pass
before a pull request is merged. Sonarr, Radarr, Lidarr, Readarr, Bazarr,
Prowlarr and Seerr are not needed either: their tests replay responses
recorded from real instances (`tests/<app>_responses/`). The same loop runs
in GitHub Actions on Python 3.11 and 3.13.

**A new test has to be able to fail.** Write it, then break the fix on purpose
and check that the test goes red. A test that passes both ways guards nothing.
Of all the habits in this project, this one helps most.

## Translations

This is the one contribution that needs no Python, and through
**<https://translate.jellyscope.cz/>** no git either. `jellyscope/translations/`
holds one JSON file per language; English (`en.json`) is the source and the
template. Copy it, translate the values, run
`tests/test_translations_dictionary.py` and open a pull request; the language
shows up in Settings by itself (the maintainer adds its plural rule to
`jellyscope/i18n.py`). **[TRANSLATING.md](TRANSLATING.md)** has the details,
including the two things that break a translation.

## Style

- **Comments explain *why*, not *what*.** The code says what it does; the
  comment says which mistake would happen without it. If you fix a bug, leave
  a sentence about what went wrong, because nobody can reconstruct that part
  later.
- **Plain SQL, no ORM.** The queries are meant to be readable by someone
  learning SQL. Anything that comes from the browser goes in as a parameter
  (`?`), never glued into the string.
- **No JavaScript frameworks, no CDN.** Pages are rendered on the server, and
  the little JavaScript there is lives in `jellyscope/static/js/` and loads
  from nowhere else. The pages carry no inline script and no `on…=`
  handlers, because the Content-Security-Policy allows scripts only from the
  app itself; `tests/test_no_inline_js.py` guards that. The app must work
  without an internet connection.
- **New settings** belong in the database (the Settings page), not in `.env`.
  `.env` only holds what the app needs *before* it can open the database.
- Both SQLite and PostgreSQL have to work. `dialect.py` translates the SQLite
  dialect to PostgreSQL, so avoid SQLite-only functions where you can, and
  when you cannot, check that `dialect.py` knows how to translate them.

## A note on the language

The user interface is in **English, Czech, German and Spanish**, and the
documentation is in English. Since 1.9.0 the code is English too: names,
comments, the database and the log file. The author is Czech, and the
comments are long and written to teach.

Please keep it that way: new names, comments and log messages in English,
and texts shown to people as English sentences wrapped for translation, so
they reach the translation files.

## Scope

Jellyscope connects *what people watch* with *the technical state of the
library*. Things that serve that idea are welcome. Things that turn it into a
media manager (editing metadata, moving files, talking back to Jellyfin) are
not. **Jellyscope only ever reads from Jellyfin**, and a test guards that
promise (`tests/test_readonly.py`). A pull request that writes to Jellyfin
will not be merged. The same goes for the apps it connects to (Sonarr,
Radarr, Lidarr, Readarr, Bazarr, Prowlarr, Seerr): it reads from them, with
one documented exception, asking an \*Arr app to refresh its download list
while the Downloading card is open.
