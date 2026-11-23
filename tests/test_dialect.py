r"""A test of translating SQL to PostgreSQL.

The translation is the riskiest piece of the whole project: it runs on every
query, and a mistake in it shows only at run time, on a particular page. So it
has a test of its own that checks the single patterns as well as real queries
of the application.

What this test **cannot** do: check that PostgreSQL really accepts the
translated query. That takes a running server. The shape is checked, not the
execution.

Run:
    .venv/bin/python tests/test_dialect.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import dialect  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def pg(sql: str) -> str:
    return dialect.translate(sql, dialect.POSTGRES)


print("--- SQLite stays unchanged ---")
for sql in [
    "SELECT * FROM items WHERE id = ?",
    "SELECT datetime('now', ?) FROM playback",
    "SELECT name FROM items WHERE name LIKE ?",
]:
    check(dialect.translate(sql, dialect.SQLITE) == sql, f"unchanged: {sql[:40]}")

print("--- placeholders ---")
check(pg("SELECT * FROM items WHERE id = ?") == "SELECT * FROM items WHERE id = %s",
      "question mark to %s")
check("?" not in pg("SELECT ? , ? , ?"), "every question mark translated")

# A question mark inside text must not be rewritten - it is data, not a placeholder.
translated = pg("SELECT * FROM items WHERE name = 'kdo?' AND id = ?")
check("'kdo?'" in translated and translated.endswith("= %s"),
      f"the question mark inside text stayed: {translated}")

print("--- percent signs ---")
translated = pg("SELECT * FROM items WHERE audio_languages NOT LIKE '%cs%'")
check("'%%cs%%'" in translated, f"percent signs doubled: {translated}")
check(translated.count("%s") == 0, "no false placeholder came into being")

translated = pg("SELECT * FROM items WHERE name LIKE ? AND path NOT LIKE '%tmp%'")
check("%s" in translated and "'%%tmp%%'" in translated,
      f"a parameter and a literal at once: {translated}")

print("--- time ---")
translated = pg("WHERE started_at >= datetime('now', ?)")
check("to_char(" in translated, "datetime is converted back to text")
check("::interval" in translated, "the parameter is read as an interval")
check("%s" in translated, "the parameter stayed a parameter")
# The columns are text. Were the translation to return a timestamp, PostgreSQL
# would refuse the comparison text >= timestamp.
check("'YYYY-MM-DD HH24:MI:SS'" in translated, "the result has our time format")

translated = pg("WHERE substr(date_created, 1, 10) < date('now', '-60 days')")
check("interval '-60 days'" in translated, f"a fixed interval: {translated}")
check("'YYYY-MM-DD'" in translated, "for date() the date without the time is enough")

translated = pg("SELECT (julianday('now') - julianday(?)) * 24 * 60 AS minutes")
check("EXTRACT(EPOCH FROM" in translated and "/ 60" in translated,
      f"julianday to minutes: {translated}")
check("julianday" not in translated, "no julianday stayed anywhere")

print("--- local time ---")
translated = pg("SELECT date(started_at, 'localtime') AS day")
check("::date" in translated and "current_setting('TIMEZONE')" in translated,
      f"date(x,'localtime'): {translated}")

translated = pg("SELECT CAST(strftime('%H', started_at, 'localtime') AS INTEGER)")
check("EXTRACT(HOUR FROM" in translated, f"hour: {translated}")
check("strftime" not in translated, "no strftime stayed anywhere")

translated = pg("SELECT CAST(strftime('%w', p.started_at, 'localtime') AS INTEGER)")
check("EXTRACT(DOW FROM" in translated, "day of the week")
check("p.started_at" in translated, "a column name with a dot survived")

print("--- LIKE and ILIKE ---")
check("ILIKE" in pg("WHERE name LIKE ?"), "LIKE to ILIKE")
check("NOT ILIKE" in pg("WHERE name NOT LIKE ?"), "NOT LIKE to NOT ILIKE")
# Inside text the word LIKE must not be rewritten.
check("'I LIKE IT'" in pg("SELECT 'I LIKE IT' AS x"), "LIKE inside text stayed")

print("--- helpers for non-portable constructs ---")
check(dialect.group_concat(dialect.SQLITE, "path", "','") == "GROUP_CONCAT(path, ',')",
      "GROUP_CONCAT for SQLite")
check(dialect.group_concat(dialect.POSTGRES, "path", "','") == "string_agg(path, ',')",
      "string_agg for PostgreSQL")
check(dialect.greatest(dialect.SQLITE, "a", "b") == "MAX(a, b)", "MAX for SQLite")
check(dialect.greatest(dialect.POSTGRES, "a", "b") == "GREATEST(a, b)",
      "GREATEST for PostgreSQL")

print("--- real queries of the application ---")
# We go through queries as the application really sends them and check that
# no shape PostgreSQL would not understand stayed in the translation.
from jellyscope import db  # noqa: E402

REAL_QUERIES = [
    """
    SELECT COUNT(*) AS plays, COALESCE(SUM(watched_seconds), 0) AS watched_seconds
    FROM playback
    WHERE started_at >= datetime('now', ?) AND watched_seconds > 0
    """,
    """
    SELECT date(started_at, 'localtime') AS day, SUM(watched_seconds) / 3600.0 AS hours
    FROM playback WHERE started_at >= datetime('now', ?) GROUP BY day ORDER BY day
    """,
    """
    SELECT CAST(strftime('%w', started_at, 'localtime') AS INTEGER) AS weekday,
           CAST(strftime('%H', started_at, 'localtime') AS INTEGER) AS hour
    FROM playback WHERE started_at >= datetime('now', ?)
    """,
    """
    SELECT p.* FROM playback p LEFT JOIN items i ON i.id = p.item_id
    WHERE p.watched_seconds > 0 AND (p.item_name LIKE ? OR p.series_name LIKE ?)
    ORDER BY p.started_at DESC LIMIT ? OFFSET ?
    """,
    """
    SELECT COUNT(*) FROM items
    WHERE is_missing = 0 AND audio_languages NOT LIKE '%cs%'
    """,
    "SELECT (julianday('now') - julianday(?)) * 24 * 60",
    # The daily library snapshot: writing through ON CONFLICT and reading for a period.
    """
    INSERT INTO library_snapshot
        (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr,
         without_tech, free_space, written_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT (day) DO UPDATE SET
        item_count = excluded.item_count, size_bytes = excluded.size_bytes
    """,
    """
    SELECT day, item_count, size_bytes, free_space FROM library_snapshot
    WHERE day >= ? AND day <= ? ORDER BY day
    """,
    # The condition "this is a transcode" - it looks by the start of the name,
    # so it carries a percent sign inside the string. See stats.transcode_condition().
    """
    SELECT COALESCE(SUM(CASE WHEN play_method LIKE 'Transcode%'
                        THEN watched_seconds ELSE 0 END), 0) AS transcoded
    FROM playback WHERE started_at >= ? AND started_at < ?
    """,
    # The library's past worked out backwards - see stats._backfilled_curve().
    """
    SELECT substr(date_created, 1, 10) AS created,
           CASE WHEN is_missing = 1 THEN substr(synced_at, 1, 10) END AS last_at,
           COALESCE(size_bytes, 0) AS size,
           type
      FROM items
     WHERE date_created IS NOT NULL AND date_created != ''
    """,
]

BANNED = ["datetime('now'", "julianday", "strftime", "'localtime'",
          "GROUP_CONCAT", "INSERT OR "]

for index, query in enumerate(REAL_QUERIES):
    translated = pg(query)
    leftovers = [word for word in BANNED if word in translated]
    check(not leftovers, f"query {index + 1}: nothing non-portable stayed {leftovers}")
    check("?" not in translated, f"query {index + 1}: no question mark stayed")

print("--- connection settings ---")
config = dialect.DatabaseConfig(kind="postgres", host="db", port=5433,
                                database="js", user="u", password="secret")
check(config.is_postgres, "recognises PostgreSQL")
check("secret" not in config.describe(), f"the description holds no password: {config.describe()}")
check("secret" not in str(config.to_dict(include_password=False)),
      "the export without the password really does not hold it")

restored = dialect.DatabaseConfig.from_dict(config.to_dict())
check(restored.to_dict() == config.to_dict(), "saving and reading give the same")

# An unknown kind of database has to fall back to SQLite, not break the start.
odd = dialect.DatabaseConfig.from_dict({"kind": "oracle"})
check(odd.kind == dialect.SQLITE, "an unknown kind of database -> SQLite")


print("--- the message when psycopg is missing ---")
# It used to advise `pip install "psycopg[binary,pool]"`, so it looked as if
# the application enforced the connection pool - even when the user had it
# switched off. And a bare `pip` would install outside the virtual environment.
import builtins  # noqa: E402

_real_import = builtins.__import__


def _without_psycopg(name, *args, **kwargs):
    if name.startswith("psycopg"):
        raise ImportError("missing on purpose")
    return _real_import(name, *args, **kwargs)


no_pool = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="x", database="d",
                                 user="u", password="p", use_pool=False)
builtins.__import__ = _without_psycopg
try:
    db._open_raw(no_pool)
    message = ""
except RuntimeError as exc:
    message = str(exc)
except Exception as exc:  # noqa: BLE001
    message = f"another exception: {type(exc).__name__}"
finally:
    builtins.__import__ = _real_import

check("psycopg[binary]\"" in message,
      "the message advises the basic psycopg[binary], not the pool extra right away")
check("psycopg[binary,pool]" not in message,
      "the message does not enforce the pool when it is switched off")
check(sys.executable in message,
      "the message shows the python of the virtual environment, not a general 'pip'")
check("-m pip install" in message, "the message uses python -m pip")


print("--- the choice of pool is saved ---")
# The pool can be switched off. That has to survive saving to the file and
# reading it back, otherwise it would switch itself on again after a restart.
off = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", database="js",
                             user="u", password="p", use_pool=False)
check(off.to_dict()["use_pool"] is False, "a switched-off pool is in the export")
check(dialect.DatabaseConfig.from_dict(off.to_dict()).use_pool is False,
      "a switched-off pool survives saving and reading")

# An old file does not have this key - on has to apply there.
check(dialect.DatabaseConfig.from_dict({"kind": "postgres"}).use_pool is True,
      "an old file without the key = on")

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    dialect.save_config(base, off)
    loaded = dialect.load_config(base, "data/jellyscope.db")
    check(loaded.use_pool is False, "the choice survives writing to the disk and reading")
    check(loaded.host == "db", "the rest of the settings did not get lost on the way")


print("--- the connection pool ---")
# The pool is for PostgreSQL only. SQLite must not even touch it - opening a
# file is cheap, and sharing one connection between threads would, on the
# contrary, make trouble with sqlite3.
from jellyscope import db  # noqa: E402

sqlite_config = dialect.DatabaseConfig(kind=dialect.SQLITE, path="data/x.sqlite3")
check(db._get_pool.__module__ == "jellyscope.db", "the pool is in db.py")

with db.connect(sqlite_config) as conn:
    conn.execute("SELECT 1")
check(db._pool is None, "SQLite does not create a pool at all")

# Closing an empty pool must not fail - it is called at every shutdown, even
# when PostgreSQL was never touched.
db.close_pool()
check(db._pool is None, "close_pool() on an empty pool passes")

# When psycopg_pool is missing, _get_pool returns None and the application
# goes the old way. We simulate it by making the import of the library
# impossible for a while.
import builtins  # noqa: E402

real_import = builtins.__import__


def _blocked(name, *args, **kwargs):
    if name.startswith("psycopg"):
        raise ImportError("missing on purpose")
    return real_import(name, *args, **kwargs)


pg_config = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", port=5432,
                                   database="js", user="u", password="p")
builtins.__import__ = _blocked
try:
    check(db._get_pool(pg_config) is None, "without psycopg_pool it returns None instead of failing")
finally:
    builtins.__import__ = real_import
    db.close_pool()

# The pool belongs to a particular server. Changing the target has to throw it
# away, otherwise the new settings would query the old database.
other_config = dialect.DatabaseConfig(kind=dialect.POSTGRES, host="other", port=5432,
                                      database="js", user="u", password="p")
check(db._pool_identity(pg_config) != db._pool_identity(other_config),
      "another server = another identity of the pool")
check(db._pool_identity(pg_config) == db._pool_identity(
          dialect.DatabaseConfig(kind=dialect.POSTGRES, host="db", port=5432,
                                 database="js", user="u", password="p")),
      "the same server = the same identity of the pool")

# When psycopg_pool is available, we really try to build the pool. No server
# is needed for it - the connections are made in the background. It checks
# that the library really accepts the arguments we pass to it.
try:
    import psycopg_pool  # noqa: F401
except ImportError:
    print("SKIPPED  psycopg_pool is not installed")
else:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pool = db._get_pool(pg_config)
        check(pool is not None, "the pool was built")
        check(pool.max_size == db.POOL_MAX_SIZE, f"the ceiling is {db.POOL_MAX_SIZE}")
        check(db._get_pool(pg_config) is pool, "a second call returns the same pool")
        check(db._get_pool(other_config) is not pool, "another server = a new pool")
        db.close_pool()
        check(db._pool is None, "close_pool() tidied the pool away")

        # A switched-off choice means "no pool", even when the library is there.
        check(db._get_pool(off) is None, "a switched-off choice builds no pool")
        check(db._pool is None, "a switched-off choice leaves nothing behind")

# ---------------------------------------------------------------------------
# Percent signs in a query without parameters
# ---------------------------------------------------------------------------
#
# The translation doubles percent signs (`LIKE '%cs%'` -> `LIKE '%%cs%%'`),
# because psycopg reads `%` as the start of a placeholder. Back to one percent
# sign they are put only by psycopg - but it does that only when it gets some
# parameters.
#
# So a query without parameters has to get an empty tuple, not None. With None
# it would go out with the doubled percent signs, and `LIKE '%%cs%%'` would look
# for the text "%cs%" literally - the filter would quietly stop filtering.

print()
print("--- percent signs in a query without parameters ---")


class FakeCursor:
    def __init__(self):
        self.seen = None

    def execute(self, query, params=None):
        self.seen = (query, params)
        return self


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()

    def cursor(self):
        return self.fake_cursor


raw = FakeConnection()
connection = db.Connection(raw, dialect.POSTGRES)
connection.execute("SELECT COUNT(*) FROM items WHERE audio_languages NOT LIKE '%cs%'")
query, params = raw.fake_cursor.seen

check(params is not None, "a query without parameters gets a tuple, not None")
check(params == (), f"and it is empty: {params!r}")

# And now for real: what would psycopg put together from the given query?
try:
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer
except ImportError:
    print("SKIPPED  psycopg is not installed")
else:
    built = PostgresQuery(Transformer())
    built.convert(query, params)
    sent = built.query.decode()
    check("'%cs%'" in sent, f"one percent sign goes to the server: {sent}")
    check("%%" not in sent, "no doubled percent sign stayed")

# ---------------------------------------------------------------------------
# Ticks do not fit into a four-byte INTEGER
# ---------------------------------------------------------------------------
#
# Jellyfin measures time in "ticks" of 100 nanoseconds, so a quarter-hour
# episode has over 9 billion. In SQLite it does not matter (the size follows
# the value), but PostgreSQL has a four-byte INTEGER and fails on "integer out
# of range".
#
# The error shows only at run time and only with one of the databases -
# exactly the one development on SQLite never shows. So it is guarded here.

print()
print("--- columns with ticks have to be BIGINT ---")

schema_pg = (PROJECT / "jellyscope" / "schema_postgres.sql").read_text(encoding="utf-8")
wrong = [
    line.strip()
    for line in schema_pg.splitlines()
    if re.search(r"^\s*\w*ticks\w*\s+", line) and "BIGINT" not in line.upper()
]
check(not wrong, f"in schema_postgres.sql; wrong: {wrong}")

found_count = len(re.findall(r"(?mi)^\s*\w*ticks\w*\s+BIGINT", schema_pg))
check(found_count >= 3, f"and there are enough of them ({found_count})")

# The same holds for bytes. INTEGER is four bytes in PostgreSQL, so 2.1 GB fit
# into it - neither the library nor the free space fit there. SQLite does not
# have this trap (INTEGER is eight bytes there), so it shows only on Postgres.
BYTE_COLUMNS = ("size_bytes", "free_space", "bitrate", "total", "free", "library", "outside")
wrong_bytes = [
    line.strip()
    for line in schema_pg.splitlines()
    if re.match(r"^\s*(" + "|".join(BYTE_COLUMNS) + r")\s+", line)
    and "BIGINT" not in line.upper()
]
check(not wrong_bytes, f"the byte columns are BIGINT; wrong: {wrong_bytes}")
check(len([r for r in schema_pg.splitlines()
           if re.match(r"^\s*(" + "|".join(BYTE_COLUMNS) + r")\s+", r)]) >= 6,
      "and enough of them are looked for")

wrong_migrations = [
    f"{table}.{column}"
    for table, columns in db.MIGRATIONS.items()
    for column, column_type in columns.items()
    if "ticks" in column and "BIGINT" not in column_type.upper()
]
check(not wrong_migrations, f"in the migrations too; wrong: {wrong_migrations}")

# A column once created is not fixed by a migration - TYPE_FIXES are for that.
check(("playback", "media_runtime_ticks") in db.TYPE_FIXES,
      "an existing column is fixed through TYPE_FIXES")
check(all("BIGINT" in column_type.upper() or "TEXT" in column_type.upper()
          for column_type in db.TYPE_FIXES.values()),
      "and the fixes aim at sensible types")

# ---------------------------------------------------------------------------
# PostgreSQL wants a truth value in WHEN, not a number
# ---------------------------------------------------------------------------
#
# `CASE WHEN ? THEN ...` with a parameter 1/0 works on SQLite (non-zero = true),
# but PostgreSQL refuses: "argument of CASE/WHEN must be type boolean, not
# type smallint". Another trap development on SQLite does not show.
#
# The solution is not writing `? = 1`, but deciding in Python and sending the
# resulting value to SQL right away - SQL is to store, not to decide.

print()
print("--- CASE WHEN with a parameter is not used ---")
import ast as _ast  # noqa: E402

found = []
for path in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    source = path.read_text(encoding="utf-8")
    for node in _ast.walk(_ast.parse(source)):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, _ast.JoinedStr):
            text = "".join(c.value if isinstance(c, _ast.Constant) else "{X}"
                           for c in node.values)
        else:
            continue
        # A comment may write about it; what matters is a real query.
        without_comments = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("--"))
        if re.search(r"CASE\s+WHEN\s*\?", without_comments, re.I):
            found.append(f"{path.name}:{node.lineno}")

check(not found, f"nowhere in the project; found: {found}")

# A check of itself - were the search to stop searching, it would stay silent.
sample = "UPDATE t SET a = CASE WHEN ? THEN ? ELSE a END"
check(bool(re.search(r"CASE\s+WHEN\s*\?", sample, re.I)),
      "the check really recognises such a query")

print()
print("--- `IS ?` is not used ---")
#
# SQLite takes `column IS ?` as a comparison that handles NULL too.
# PostgreSQL knows only `IS NULL` / `IS NOT NULL`, so `IS %s` is a syntax
# error - and development on SQLite does not show it.
#
# A real case: the custom dashboard's layout is read with every request
# (because of the tab in the menu), so on PostgreSQL it brought down not one
# page but the whole application.
#
# The solution is to decide in Python: "account_id IS NULL" without a
# parameter, or "account_id = ?" with it.
found_is = []
for path in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    source = path.read_text(encoding="utf-8")
    for node in _ast.walk(_ast.parse(source)):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, _ast.JoinedStr):
            text = "".join(c.value if isinstance(c, _ast.Constant) else "{X}"
                           for c in node.values)
        else:
            continue
        # A docstring may write about it; what matters is a real query.
        if "SELECT" not in text.upper() and "DELETE" not in text.upper()                 and "UPDATE" not in text.upper():
            continue
        if re.search(r"\bIS\s+\?", text, re.I):
            found_is.append(f"{path.name}:{node.lineno}")

check(not found_is, f"nowhere in the project; found: {found_is}")
check(bool(re.search(r"\bIS\s+\?", "DELETE FROM t WHERE a IS ?", re.I)),
      "the check really recognises such a query")

print()
print("--- a row is not read by position ---")
#
# PostgreSQL returns rows as dicts, SQLite as `sqlite3.Row`, which can be read
# both ways. So `row[0]` works during development and fails for a user with
# PostgreSQL on `KeyError: 0`.
#
# A real case: carrying over one setting at start. Because of it the
# application did not start at all - not just a page, the whole process.


def _read_by_position(source: str) -> list[int]:
    """Lines where data from fetchone() are read by position."""
    found = []
    for node in _ast.walk(_ast.parse(source)):
        # Within one function only - the same name elsewhere is not the same thing.
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        from_row = set()
        for sub in _ast.walk(node):
            if (isinstance(sub, _ast.Assign) and isinstance(sub.value, _ast.Call)
                    and isinstance(sub.value.func, _ast.Attribute)
                    and sub.value.func.attr == "fetchone"):
                from_row.update(target.id for target in sub.targets
                                if isinstance(target, _ast.Name))
        for sub in _ast.walk(node):
            if not (isinstance(sub, _ast.Subscript)
                    and isinstance(sub.slice, _ast.Constant)
                    and isinstance(sub.slice.value, int)):
                continue
            direct = (isinstance(sub.value, _ast.Call)
                      and isinstance(sub.value.func, _ast.Attribute)
                      and sub.value.func.attr == "fetchone")
            through_name = (isinstance(sub.value, _ast.Name)
                            and sub.value.id in from_row)
            if direct or through_name:
                found.append(sub.lineno)
    return sorted(set(found))


by_position = []
for path in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    for line in _read_by_position(path.read_text(encoding="utf-8")):
        by_position.append(f"{path.name}:{line}")

check(not by_position, f"nowhere in the project; found: {by_position}")
WRONG = """def f(c):
    row = c.fetchone()
    return row[0]
"""
RIGHT = """def f(c):
    row = c.fetchone()
    return row["value"]
"""

# A check of itself - were the search to stop searching, it would stay silent.
check(_read_by_position(WRONG) == [3],
      "the check really recognises such code")
check(_read_by_position(RIGHT) == [],
      "and leaves reading by name alone")

# ---------------------------------------------------------------------------
# The collector's writes: do the parameter counts fit and do they pass psycopg?
# ---------------------------------------------------------------------------
#
# The collector's INSERT has over thirty columns, and with every extension it
# is added to by hand in three places (the list of columns, the row of question
# marks, the tuple of values). When one of them is forgotten, both SQLite and
# PostgreSQL notice - but only at run time, that is with the user. Here it is
# noticed right away.

print()
print("--- the collector's writes ---")

collector_source = (PROJECT / "jellyscope" / "collector.py").read_text(encoding="utf-8")
writes = 0
for node in _ast.walk(_ast.parse(collector_source)):
    if not (isinstance(node, _ast.Call) and getattr(node.func, "attr", "") == "execute"):
        continue
    if not node.args or not isinstance(node.args[0], _ast.Constant):
        continue
    sql = node.args[0].value
    if not re.search(r"\b(INSERT|UPDATE)\b", sql, re.I):
        continue
    if len(node.args) < 2 or not isinstance(node.args[1], _ast.Tuple):
        continue          # a query without parameters

    writes += 1
    placeholders = sql.count("?")
    values_count = len(node.args[1].elts)
    kind = "INSERT" if re.search(r"\bINSERT\b", sql, re.I) else "UPDATE"
    check(placeholders == values_count,
          f"{kind} on line {node.lineno}: {placeholders} placeholders, {values_count} values")

    # And whether psycopg accepts the translated shape at all - a badly
    # bracketed translation or a forgotten percent sign shows there.
    try:
        from psycopg._queries import PostgresQuery
        from psycopg.adapt import Transformer
    except ImportError:
        continue
    built = PostgresQuery(Transformer())
    try:
        built.convert(dialect.translate(sql, dialect.POSTGRES),
                      tuple([None] * placeholders))
        passed = True
    except Exception as error:            # noqa: BLE001
        passed = False
        print("       ", error)
    check(passed, f"{kind} on line {node.lineno} passes psycopg")

check(writes >= 2, f"both of the collector's writes were found ({writes})")


# ---------------------------------------------------------------------------
# Descending order by a column that may be NULL
# ---------------------------------------------------------------------------
# SQLite takes NULL for the smallest value, so with DESC it ends up last.
# PostgreSQL has NULLS FIRST as the default with DESC and puts them at the
# start. For a query that takes ONE row this is no cosmetics - another row is
# returned. Exactly like this the free space would be measured on the disk of
# the first unmeasured file.

def _nullable_columns() -> set[str]:
    """Columns of the schema that may be NULL."""
    text = (PROJECT / "jellyscope" / "schema.sql").read_text(encoding="utf-8")
    text = re.sub(r"--[^\n]*", "", text)          # comments away
    nullable: set[str] = set()
    for body in re.findall(r"CREATE TABLE[^(]*\((.*?)\n\);", text, re.S):
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            m = re.match(r"^([a-z_][a-z0-9_]*)\s+(TEXT|INTEGER|REAL|BLOB|BIGINT)",
                         line, re.I)
            if m and not re.search(r"NOT NULL|PRIMARY KEY", line, re.I):
                nullable.add(m.group(1).lower())
    return nullable


def _terms(expression: str) -> list[str]:
    """Splits ORDER BY into terms; commas inside brackets do not split."""
    terms: list[str] = []
    depth = 0
    chunk = ""
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            terms.append(chunk)
            chunk = ""
        else:
            chunk += char
    terms.append(chunk)
    return [t.strip() for t in terms if t.strip()]


def _sql_of_call(node: ast.Call) -> str:
    """The query's text from the first argument - even when it is put together from pieces."""
    parts: list[str] = []
    for arg in node.args[:1]:
        for n in ast.walk(arg):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                parts.append(n.value)
    return " ".join(parts)


def _risky_orderings(source: str) -> list[str]:
    """Queries for one row ordered descending by a nullable column."""
    nullable = _nullable_columns()
    findings: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function_name = (node.func.attr if isinstance(node.func, ast.Attribute)
                         else getattr(node.func, "id", ""))
        if function_name not in ("query_one", "query_value"):
            continue
        sql = _sql_of_call(node)
        for expression in re.findall(r"ORDER BY\s+(.+?)(?:\s+LIMIT\b|$)", sql, re.I | re.S):
            for term in _terms(expression):
                if not re.search(r"\bDESC\b", term, re.I):
                    continue
                if re.search(r"COALESCE|NULLS", term, re.I):
                    continue
                column = re.sub(r"\bDESC\b", "", term, flags=re.I).strip()
                column = column.split(".")[-1].strip().lower()
                if column in nullable:
                    findings.append(f"line {node.lineno}: ORDER BY {term.strip()}")
    return findings


print()
print('--- the condition "this is a transcode" passes the translation ---')
# It carries a percent sign inside the string. Were it not doubled, psycopg
# would read it as the start of a placeholder and the query would fail - and it
# would fail only on PostgreSQL, nothing happens on SQLite.
from jellyscope import stats as _stats  # noqa: E402

for column in ("play_method", "p.play_method"):
    translated_condition = pg(f"SELECT 1 WHERE {_stats.transcode_condition(column)}")
    check("'Transcode%%'" in translated_condition,
          f"the percent sign is doubled ({column}): {translated_condition[-24:]}")
    check("ILIKE" in translated_condition, f"and LIKE was translated to ILIKE ({column})")

# A trap: were the condition written as a fixed text, this test would guard nothing.
check(_stats.transcode_condition().startswith("play_method LIKE 'Transcode"),
      "the condition has the shape the test relies on")
# And above all: a transcode must never be looked for by an exact match. The
# delivery chart looks for it by the start (because of the import
# "Transcode (v:h264 a:direct)"), and a second rule would mean two different
# numbers on one page.
exact_match = []
for path in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"play_method\s*=\s*'Transcode'", line):
            exact_match.append(f"{path.name}:{number}")
check(not exact_match, f"nowhere is it looked for by an exact match: {exact_match}")

print()
print("--- one row is not taken by a column that may be NULL ---")
wrong: list[str] = []
for path in sorted((PROJECT / "jellyscope").glob("**/*.py")):
    for finding in _risky_orderings(path.read_text(encoding="utf-8")):
        wrong.append(f"{path.name} {finding}")
check(not wrong, "no risky ordering " + (", ".join(wrong) or "(clean)"))

# A trap: without this the test would pass even over broken code.
trap = _risky_orderings(
    'db.query_one("SELECT path FROM items ORDER BY size_bytes DESC")')
check(len(trap) == 1, f"and it can find the mistake ({len(trap)})")
clean = _risky_orderings(
    'db.query_one("SELECT path FROM items ORDER BY COALESCE(size_bytes, 0) DESC")')
check(not clean, "COALESCE passes")
key_column = _risky_orderings('db.query_one("SELECT id FROM items ORDER BY id DESC")')
check(not key_column, "a column that must not be NULL passes too")

print()
print("--- prefilter: lower case in ASCII only, non-ASCII rows always through ---")
# The API's history prefilter (api._history_prefilter) may let more through in
# SQL, never less than the exact filter in Python. But PostgreSQL's LOWER folds
# letters by the database's locale - a Turkish one turns an ASCII "I" into "ı",
# and the row "INFUSE" would get lost. Collation "C" changes ASCII only, the
# same as SQLite.
check(dialect.lower_ascii(dialect.SQLITE, "p.client") == "LOWER(p.client)",
      "SQLite: LOWER changes ASCII only by itself")
check(dialect.lower_ascii(dialect.POSTGRES, "p.client") == 'LOWER(p.client COLLATE "C")',
      "PostgreSQL: LOWER with collation C")
check(dialect.non_ascii(dialect.SQLITE, "p.client") == "p.client GLOB '*[^ -~]*'",
      "SQLite: a row with a non-ASCII character through GLOB")
check(dialect.non_ascii(dialect.POSTGRES, "p.client") == "p.client ~ '[^ -~]'",
      "PostgreSQL: through a regular expression")

print()
print("--- an apostrophe and a question mark in an SQL comment ---")
# The translation for PostgreSQL (dialect._split_literals) looks for text
# literals by apostrophes and knows nothing about `--` comments. An apostrophe
# in a comment ("the file's codecs") thus "opens" a literal up to the next
# apostrophe, and the question marks after it are not turned into `%s` - the
# query fails on PostgreSQL ("the query has 0 placeholders") and passes on
# SQLite. It happened after the comments were translated to English
# (17. 11. 2026).


def _comments_with_quote(source: str) -> list[str]:
    findings = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts = [node.value]
        elif isinstance(node, ast.JoinedStr):
            texts = ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)]
        else:
            continue
        # Every string, not just the one with SELECT: a piece of SQL is put together from constants without it
        # too. A comment starts with `--` at the start of a line or after a space (not `--headless` in command
        # options).
        for text in texts:
            for line in text.splitlines():
                comment = re.search(r"(?:^|\s)(--(?:\s.*|$))", line)
                if comment and (comment.group(1).count("'") % 2 or "?" in comment.group(1)):
                    findings.append(comment.group(1).strip())
    return findings


with_quote = []
for path in sorted((PROJECT / "jellyscope").rglob("*.py")):
    with_quote += [f"{path.relative_to(PROJECT)}: {r[:60]}"
                   for r in _comments_with_quote(path.read_text(encoding="utf-8"))]
check(not with_quote, f"no SQL comment has an odd apostrophe or a question mark ({with_quote[:4]})")
check(_comments_with_quote('q = """SELECT 1\n  -- the file\'s codec\n WHERE x = ?"""')
      == ["-- the file's codec"], "the check finds an apostrophe in a comment")
check(not _comments_with_quote('q = """SELECT 1 -- a \'x\' b\n WHERE x = ?"""'),
      "and leaves a pair of apostrophes (a closed literal) alone")
# A question mark in a comment is turned into `%s` as well - psycopg then counts one placeholder too many. And a
# piece of SQL put into a query elsewhere (the group's columns, a piece of WHERE) need not hold SELECT (review of step 6).
check(_comments_with_quote('q = """SELECT 1 -- why?\n WHERE x = ?"""') == ["-- why?"],
      "the check finds a question mark in a comment too")
check(_comments_with_quote('COLUMNS = """\n  COUNT(*) AS n,\n  -- the group\'s files\n  MAX(x) AS m\n"""')
      == ["-- the group's files"], "and a piece of SQL without SELECT (columns put into a query elsewhere)")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
