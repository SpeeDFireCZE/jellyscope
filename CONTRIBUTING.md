# Contributing

Thanks for looking. This is a small project with one author, so the rules are
short.

## Before you start

**Open an issue first** for anything bigger than a bug fix. It costs you five
minutes and can save you an afternoon of work on something I would not merge —
the project deliberately stays small (see *Scope* below).

## Running it

```bash
python3 -m venv .venv                              # Python 3.10 or newer
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

You do not need a Jellyfin server to develop: `demo.py` fills the database with
made-up data and starts the app on <http://127.0.0.1:8097>.

```bash
.venv/bin/python demo.py
```

## Tests

There is no pytest. The tests are plain scripts — each one sets up its own
temporary database, prints `OK` / `CHYBA` per check and exits non-zero when
something fails:

```bash
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done
```

They need no Jellyfin, no network and no ffmpeg, and they must **all** pass
before a pull request is merged. The same loop runs in GitHub Actions on
Python 3.10 and 3.13.

**A new test has to be able to fail.** Write it, then break the fix on purpose
and check that the test goes red. A test that passes both ways guards nothing —
this is the single most useful habit in the project.

## Style

- **Comments explain *why*, not *what*.** The code says what it does; the
  comment says which mistake would happen without it. If you fix a bug, leave
  a sentence about what went wrong — that is the part nobody can reconstruct
  later.
- **Plain SQL, no ORM.** The queries are meant to be readable by someone
  learning SQL. Anything that comes from the browser goes in as a parameter
  (`?`), never glued into the string.
- **No JavaScript frameworks, no CDN.** Pages are rendered on the server; the
  bit of JavaScript that exists is in `base.html` and loads from nowhere. The
  app must work without an internet connection.
- **New settings** belong in the database (Settings page), not in `.env`.
  `.env` only holds what the app needs *before* it can open the database.
- Both SQLite and PostgreSQL have to work. `dialect.py` translates the SQLite
  dialect to PostgreSQL, so avoid SQLite-only functions where you can, and
  when you cannot, check `dialect.py` knows how to translate it.

## A note on the language

The user interface and the documentation are in **English and Czech**; the
**comments in the source are Czech only**. The author is Czech and the
comments are long, explanatory, and written to teach — translating them would
cost more than it would add, and machine translation reads them fine.

Pull requests with English comments are welcome and will not be rewritten.
A mix is better than silence.

## Scope

Jellyscope connects *what people watch* with *the technical state of the
library*. Things that serve that idea are welcome. Things that turn it into a
media manager (editing metadata, moving files, talking back to Jellyfin) are
not: **Jellyscope only ever reads from Jellyfin**, and a test guards that
promise (`tests/test_readonly.py`). A pull request that writes to Jellyfin
will not be merged.
