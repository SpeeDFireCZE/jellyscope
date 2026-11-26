"""Support for two databases: SQLite and PostgreSQL.

The application is written in the **SQLite dialect** - that is the source form,
just as Czech is the source language of the interface. When it runs on
PostgreSQL, every query goes through `translate()` and is translated.

## Why translate instead of writing both dialects by hand

The project has over a hundred SQL queries. Writing them twice means twice as
many places for a mistake, and the certainty that one day only one version gets
fixed. Translating keeps **one source of truth**.

For that to be safe, one thing has to hold: what is translated is a **closed
set** of patterns we write ourselves. It is not a general SQL converter and
never will be - it is a dictionary for a few specific constructs we know we
use.

Constructs the project does **not use**, because they are not portable:
`GROUP_CONCAT` (called through `group_concat()` below), `MAX(a, b)` as a scalar
function (through `greatest()`), a column alias in `HAVING` and
`INSERT OR REPLACE` (we write `ON CONFLICT`, which both understand).

## What is translated

| SQLite                              | PostgreSQL                          |
|-------------------------------------|-------------------------------------|
| `?`                                 | `%s`                                |
| `datetime('now', ?)`                | `now() + interval`                  |
| `date(x, 'localtime')`              | time zone conversion + `::date`     |
| `strftime('%H', x, 'localtime')`    | `EXTRACT(HOUR FROM …)`              |
| `julianday(a) - julianday(b)`       | `EXTRACT(EPOCH FROM …)`             |
| `LIKE`                              | `ILIKE` (SQLite searches case-insensitively) |
| `char(10)`                          | `chr(10)`                           |
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Iterator

log = logging.getLogger(__name__)

SQLITE = "sqlite"
POSTGRES = "postgres"


# ---------------------------------------------------------------------------
# Database settings
#
# This one setting cannot live in the database - we need it to connect to
# the database in the first place. The classic chicken and egg. It is solved
# with a small file next to the database, written by the form in Settings.
# ---------------------------------------------------------------------------

@dataclass
class DatabaseConfig:
    kind: str = SQLITE
    # SQLite
    path: str = "data/jellyscope.db"
    # PostgreSQL
    host: str = "localhost"
    port: int = 5432
    database: str = "jellyscope"
    user: str = "jellyscope"
    password: str = field(default="", repr=False)
    # Connection pool. Switched on it is faster, but it needs the extra
    # library psycopg_pool - and that cannot always be installed. Whoever
    # lacks it switches this off and the application opens connections one
    # at a time as before.
    use_pool: bool = True

    @property
    def is_postgres(self) -> bool:
        return self.kind == POSTGRES

    def describe(self) -> str:
        """A description for the user - never holds the password."""
        if self.is_postgres:
            return f"PostgreSQL {self.user}@{self.host}:{self.port}/{self.database}"
        return f"SQLite {self.path}"

    def to_dict(self, include_password: bool = True) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "path": self.path,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "use_pool": self.use_pool,
        }
        if include_password:
            data["password"] = self.password
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatabaseConfig:
        kind = str(data.get("kind", SQLITE)).lower()
        return cls(
            kind=kind if kind in (SQLITE, POSTGRES) else SQLITE,
            path=str(data.get("path") or "data/jellyscope.db"),
            host=str(data.get("host") or "localhost"),
            port=int(data.get("port") or 5432),
            database=str(data.get("database") or "jellyscope"),
            user=str(data.get("user") or "jellyscope"),
            password=str(data.get("password") or ""),
            # An old file does not have this key - there it means on. Were
            # the library missing, the application falls back to one
            # connection at a time anyway, so the default "on" breaks
            # nothing for anyone.
            use_pool=bool(data.get("use_pool", True)),
        )


def config_path(base_dir: Path) -> Path:
    return base_dir / "data" / "database.json"


def load_config(base_dir: Path, fallback_sqlite_path: str) -> DatabaseConfig:
    """Reads the database settings from the file; without one, SQLite."""
    path = config_path(base_dir)
    if path.exists():
        try:
            return DatabaseConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            # A damaged file must not mean the application does not start.
            # We fall back to SQLite, where the data almost certainly are.
            pass
    return DatabaseConfig(kind=SQLITE, path=fallback_sqlite_path)


def write_private(path: Path, text: str) -> None:
    """Writes a file only its owner may read (permissions 600) - from the first moment.

    `Path.write_text` creates a file with the default permissions (usually 644, readable by everyone) and a
    `chmod` after it leaves a moment when the file is readable - and where `chmod` does nothing it stays so. Opened
    with mode 600 the file is private as it is born; the `chmod` after it narrows a file that existed before. On
    Windows the file is protected by access to the folder.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def save_config(base_dir: Path, config: DatabaseConfig) -> None:
    """Saves the choice of database to `data/database.json`.

    The file gets permissions 600, because for PostgreSQL it holds the
    **database password**, readable - otherwise the application could not
    sign in with it. The same reasoning as for `data/secret_key` in
    config.py: what is sensitive is readable by its owner only. On Windows
    `chmod` cannot do that; there the file is protected by access to the
    folder.
    """
    path = config_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
    # The password is not in the log, of course - only which database the
    # application switches to after a restart.
    log.info("database selected: %s (%s)", config.kind,
             f"{config.host}:{config.port}/{config.database}"
             if config.is_postgres else config.path)


# ---------------------------------------------------------------------------
# Helpers for writing portable SQL
# ---------------------------------------------------------------------------

def group_concat(dialect: str, expression: str, separator: str = "','") -> str:
    """Values of a group joined into one string.

    SQLite calls it GROUP_CONCAT, PostgreSQL string_agg, and they differ in
    the order of the arguments too. We do not want to translate it with a
    regular expression - the expression inside can hold more brackets, and
    that is hard to keep track of. So this function is called while the
    query is being built.
    """
    if dialect == POSTGRES:
        return f"string_agg({expression}, {separator})"
    return f"GROUP_CONCAT({expression}, {separator})"


def group_concat_distinct(dialect: str, expression: str) -> str:
    if dialect == POSTGRES:
        return f"string_agg(DISTINCT {expression}, ',')"
    return f"GROUP_CONCAT(DISTINCT {expression})"


def greatest(dialect: str, first: str, second: str) -> str:
    """The greater of two values.

    SQLite uses the two-argument MAX(), PostgreSQL GREATEST(). Translating
    it is not possible: `MAX` is an aggregate function in both as well, and
    telling them apart by the number of arguments would take a parser.
    """
    if dialect == POSTGRES:
        return f"GREATEST({first}, {second})"
    return f"MAX({first}, {second})"


def short_lock_wait(dialect: str, milliseconds: int) -> str:
    """The statement after which this connection waits for another's lock `milliseconds` at most, then fails.

    For a write that may be skipped (web_sessions). SQLite's connection otherwise waits out its busy timeout
    (db._open_raw), PostgreSQL without a limit; there only for the transaction (SET LOCAL), so a connection
    the pool lends again does not keep it.
    """
    if dialect == POSTGRES:
        return f"SET LOCAL lock_timeout = '{int(milliseconds)}ms'"
    return f"PRAGMA busy_timeout = {int(milliseconds)}"


# ---------------------------------------------------------------------------
# Translating SQL
# ---------------------------------------------------------------------------

def lower_ascii(dialect: str, column: str) -> str:
    """The column in lower case - but in ASCII only, the same in both databases.

    SQLite's LOWER changes ASCII only. PostgreSQL follows the database's
    locale, and a Turkish one turns "I" into "ı" - the API's prefilter
    (api._history_prefilter) would then drop the row "INFUSE" that Python
    lets through. Collation "C" changes ASCII only; that holds for ILIKE as
    well, which LIKE is translated to in PostgreSQL.
    """
    if dialect == POSTGRES:
        return f'LOWER({column} COLLATE "C")'
    return f"LOWER({column})"


def non_ascii(dialect: str, column: str) -> str:
    """The condition "the text holds something other than printable ASCII".

    The prefilter always lets such rows through: Python's `casefold` does
    more than the database's LOWER (`Straße` -> `strasse`), so Python has to
    decide about them.
    """
    if dialect == POSTGRES:
        return f"{column} ~ '[^ -~]'"
    return f"{column} GLOB '*[^ -~]*'"


# A text column converted to local time. Times are stored as text in UTC,
# so the column has to be cast first and then shifted into the server's zone.
def _local(column: str) -> str:
    return (f"(({column})::timestamp AT TIME ZONE 'UTC' "
            f"AT TIME ZONE current_setting('TIMEZONE'))")


_COLUMN = r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"

# The format times are stored in. The result of a conversion has to be
# **text** again, because the columns are text. Were the translation to
# return a `timestamp`, PostgreSQL would refuse the comparison
# `text >= timestamp` - unlike SQLite, which guesses the types.
_TS = "'YYYY-MM-DD HH24:MI:SS'"

# These rules apply to the WHOLE query, not only outside string literals.
# It has to be so: the pattern `datetime('now', ?)` holds the literal
# `'now'` itself, so a search "outside literals only" would never find it.
# It is safe because we never write these particular shapes inside data.
_FUNCTION_RULES: list[tuple[re.Pattern[str], Any]] = [
    # datetime('now', ?) -> the current UTC time shifted by an interval, as text
    (re.compile(r"datetime\(\s*'now'\s*,\s*\?\s*\)"),
     lambda m: f"to_char((now() AT TIME ZONE 'UTC') + (?)::interval, {_TS})"),

    # date('now', '-60 days') -> the same, but the date only and with a fixed interval
    (re.compile(r"\bdate\(\s*'now'\s*,\s*'([^']*)'\s*\)"),
     lambda m: ("to_char((now() AT TIME ZONE 'UTC') + interval "
                f"'{m.group(1)}', 'YYYY-MM-DD')")),

    # (julianday('now') - julianday(?)) * 24 * 60  -> minutes
    (re.compile(r"\(\s*julianday\(\s*'now'\s*\)\s*-\s*julianday\(\s*\?\s*\)\s*\)"
                r"\s*\*\s*24\s*\*\s*60"),
     lambda m: "(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - (?)::timestamp)) / 60)"),

    # strftime('%w' / '%H', column, 'localtime')
    (re.compile(r"strftime\(\s*'%w'\s*,\s*" + _COLUMN + r"\s*,\s*'localtime'\s*\)"),
     lambda m: f"EXTRACT(DOW FROM {_local(m.group(1))})"),
    (re.compile(r"strftime\(\s*'%H'\s*,\s*" + _COLUMN + r"\s*,\s*'localtime'\s*\)"),
     lambda m: f"EXTRACT(HOUR FROM {_local(m.group(1))})"),

    # date(column, 'localtime')
    (re.compile(r"\bdate\(\s*" + _COLUMN + r"\s*,\s*'localtime'\s*\)"),
     lambda m: f"{_local(m.group(1))}::date"),

    (re.compile(r"\bchar\(\s*10\s*\)"), lambda m: "chr(10)"),
]

# This one, on the other hand, is a single word without quotes, so it is
# looked for outside literals only - not to be rewritten inside text, should
# there ever be some.
#
# Why at all: SQLite's LIKE ignores case, PostgreSQL's does not. For
# searching to behave the same, we use ILIKE there.
_LIKE = re.compile(r"\bLIKE\b")


def _split_literals(sql: str) -> Iterator[tuple[bool, str]]:
    """Splits SQL into the parts outside and inside text literals.

    Without this split, the replacing would touch the text inside quotes
    too - and `'%cs%'` would turn into nonsense. A doubled quote inside a
    literal (`''`) is an escape in SQL, so it is skipped.
    """
    index = 0
    length = len(sql)
    while index < length:
        start = sql.find("'", index)
        if start == -1:
            yield False, sql[index:]
            return

        yield False, sql[index:start]

        end = start + 1
        while end < length:
            if sql[end] == "'":
                if end + 1 < length and sql[end + 1] == "'":
                    end += 2
                    continue
                break
            end += 1

        yield True, sql[start:end + 1]
        index = end + 1


def translate(sql: str, dialect: str) -> str:
    """Translates a query from our SQLite dialect into the target database.

    The order of the steps is not arbitrary and is worth reading carefully -
    twice here a mistake can be made that shows only at run time.
    """
    if dialect != POSTGRES:
        return sql

    # 1. Functions. On the whole query, because the patterns themselves hold quotes.
    for pattern, replacement in _FUNCTION_RULES:
        sql = pattern.sub(replacement, sql)

    # 2. LIKE -> ILIKE, but outside string literals only.
    sql = "".join(
        chunk if is_literal else _LIKE.sub("ILIKE", chunk)
        for is_literal, chunk in _split_literals(sql)
    )

    # 3. Percent signs. psycopg reads `%` as the start of a placeholder, so
    #    every real percent sign (say in `LIKE '%cs%'`) has to be doubled.
    #    It MUST happen before the question marks become `%s` - otherwise
    #    we would double the new ones too.
    sql = sql.replace("%", "%%")

    # 4. Question marks to %s - again outside literals only, so a question
    #    mark inside text is not rewritten.
    return "".join(
        chunk if is_literal else chunk.replace("?", "%s")
        for is_literal, chunk in _split_literals(sql)
    )
