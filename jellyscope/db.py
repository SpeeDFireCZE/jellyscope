"""Working with the database.

The application can run on **SQLite** (the default, nothing to install) as well
as on **PostgreSQL** (when you already have one at home). The rest of the program
does not know about it, though - a query is written once in the SQLite dialect and
`dialect.translate()` translates it when needed. See [dialect.py](dialect.py).

There is no ORM here on purpose (a library that writes SQL for you). Jellyscope
is ninety per cent "ask the database a clever question and draw the answer" -
and that is exactly what SQL is good at. Learn it here, and you will know it
everywhere.

The whole module stands on one function: `connect()`. It opens a connection,
gives it to you, and when the block ends it closes it and saves the changes
itself (or rolls them back on an error).
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from . import config as app_config
from . import dialect
from .i18n import SOURCE_LANGUAGE, TranslatableError, error_text, translate_with
from .config import BASE_DIR, load_config

log = logging.getLogger("jellyscope.db")

SCHEMA_SQLITE = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_POSTGRES = Path(__file__).resolve().parent / "schema_postgres.sql"

# One time format in the whole application (see utcnow below).
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

DEFAULT_SETTINGS: dict[str, str] = {
    # --- the connection to Jellyfin -------------------------------------------
    # It used to be in .env. Now it is set from the web, so the user does not
    # have to touch a file and restart. The values from .env only serve as the
    # first filling at the very first start (see seed_from_env).
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    # Interface language - English for a new installation. An existing one has its choice
    # stored already (the defaults below fill in only what is missing).
    "ui_language": "en",
    # 'jellyfin' = take technical data from the Jellyfin API (always works, fewer details)
    # 'ffprobe'  = analyse the files on the disk directly (more accurate, needs access)
    "tech_source": "jellyfin",
    # How often to ask Jellyfin what is playing right now (in seconds).
    "poll_interval": "10",
    # When the library is synchronised every day (local time "HH:MM").
    #
    # A time, not an interval: it is a night task that goes through the whole
    # library. Were it counted from the last run, every manual run would move
    # the schedule, and over time the task would drift into the afternoon,
    # when people watch. See tasks.py.
    "library_sync_time": "03:30",
    # How often to look for newly added titles. The task costs a few calls to
    # Jellyfin (one per library), so it can run often.
    #
    # It sets the border of "what is new" by itself from the last known
    # title, not from this interval - see scanner.sync_recent(). The interval
    # only says HOW OFTEN to ask, not HOW FAR back to look.
    "recent_sync_minutes": "15",
    "task_recent_enabled": "1",
    # Path to the ffprobe program. Empty = look in PATH.
    "ffprobe_path": "",
    # Rewriting paths, when Jellyfin sees the files elsewhere than Jellyscope.
    "path_mappings": "[]",
    # How many files to analyse at once.
    "ffprobe_concurrency": "3",

    # --- scheduled tasks (see tasks.py) ---------------------------------------
    # The Jellyfin version we saw last, and the manual choice of the
    # generation ("auto" / "12" / "10"). They decide what makes sense to
    # send to the server - see jellyfin.headers().
    "jellyfin_version": "",
    "jellyfin_generation": "auto",
    "task_sync_enabled": "1",
    "task_backup_enabled": "0",
    # Deleting data never starts on its own - see purge.py.
    "task_purge_enabled": "0",
    "history_retention_days": "365",
    # The backup is a night task too - the same reason as for the sync.
    # A little after it, so they do not get into each other's lock.
    # Checking for a new version is switched OFF by default: it is an
    # outgoing connection, and that is for whoever runs the server to decide.
    # See updates.py.
    # How many items a card lists right away before hiding the rest in a window.
    # A higher number = more to see without clicking, but the card grows and
    # pushes everything below it down. See web._context() and the templates
    # _now_playing.html and languages.html.
    "ui_max_streams": "10",
    "ui_max_viewers": "10",
    # And the same for a narrow screen - less fits there, see web._limits().
    "ui_max_streams_mobile": "3",
    "ui_max_viewers_mobile": "5",
    # Table rows on the Insights page (unused space, transcodes...).
    # They used to have 15 to 25 rows fixed, and the card next to them stayed empty.
    "ui_max_rows": "10",
    "ui_max_rows_mobile": "5",
    # Tables on the *Arr and Seerr pages have their own counts (web.TABLE_LIMITS).
    "ui_max_rows_arr": "10",
    "ui_max_rows_arr_mobile": "5",
    "ui_max_rows_seerr": "10",
    "ui_max_rows_seerr_mobile": "5",
    "ui_max_rows_music": "10",
    "ui_max_rows_music_mobile": "5",
    # How the map on the Network page zooms. "click" as the default on
    # purpose: the wheel over the map would otherwise stop the page from
    # scrolling and you would get stuck at the map. See web._limits() and base.html.
    "ui_map_zoom": "click",
    "ui_map_base": "drawn",
    # The look of the application: "new" (Jellyfin's colours) or "classic" (the
    # original blue on a neutral background). It is not just another palette - the
    # classic one also brings back flat cards, dots on badges and the number above
    # the curve instead of next to it. See the end of style.css and web._context().
    "ui_skin": "new",
    # The default page (navigation.default_page_url): "video", "music" or "dashboard".
    # Empty = never chosen (an installation from before the menu by sections): the custom
    # dashboard when it has something to show, otherwise Movies & series - as before.
    "ui_default_page": "",
    # The application's time zone (e.g. "Europe/Prague"). Empty = the system's
    # zone. It decides the time the data are shown in and how days are split
    # in the charts - see formatting.zone() and web.lifespan().
    "app_timezone": "",
    "task_tidy_enabled": "1",
    # Synchronising the *Arr history (Sonarr, Radarr): on, so a connected
    # app is downloaded by itself. Without a connected app it does nothing.
    "task_arr_enabled": "1",
    # Synchronising requests from Seerr: the same - without a connected Seerr it does nothing.
    "task_seerr_enabled": "1",
    "task_music_enabled": "1",
    "task_books_sync_enabled": "1",
    # Between the sync (03:30) and the backup (04:30): tidying works with
    # what the sync has just downloaded, and the backup should already store
    # the tidied data.
    "task_tidy_time": "04:00",
    # Rotating the logs: every day at this time it checks whether
    # `logrotate_days` has passed; it keeps `logrotate_count` old logs (applog.rotate).
    "task_logrotate_enabled": "1",
    "task_logrotate_time": "00:05",
    "logrotate_days": "7",
    "logrotate_count": "4",
    "task_backup_time": "04:30",
    "task_purge_time": "04:45",
    "backup_path": "",
    "backup_keep": "7",
    # Empty = Jellyscope finds pg_dump itself and picks a version that is
    # enough for the server. See tasks._choose_pg_dump().
    "pg_dump_path": "",
}


# Settings the application no longer uses.
#
# A new value is added at start by itself (see init_db), but the old one
# stays lying in the table forever - and whoever looks into the database
# searches in vain for why nothing follows it. So they are deleted at start.
#
# A key belongs on the list only once it is nowhere in the code any more.
DROPPED_SETTINGS = (
    # The log file is always English and the Log page shows it in the reader's language (21. 11. 2026).
    "log_language",
    # Analysing files stopped being a task of its own - it is part of the
    # sync and follows the source of data. See tasks.py.
    "task_tech_enabled",
    "task_tech_minutes",
    # The library sync and the backup are planned for a time, not an interval.
    "library_sync_minutes",
    "task_backup_minutes",
    # Checking for a new version is a task like any other - it is switched on in Tasks.
    # The value is carried over to `task_updates_enabled` at start, see init_db.
    "update_check_enabled",
)


def utcnow() -> str:
    """The current time in UTC as text.

    We always store time in UTC and always in the same format. Once you start
    mixing time zones or formats, sorting by time stops making sense and you
    spend a week looking for why.

    The format "2026-08-12 09:47:00" (with a space, not a T) is no accident -
    both SQLite and PostgreSQL understand it when the text is cast to a time.
    """
    return datetime.now(UTC).strftime(TIME_FORMAT)


# ---------------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------------

_db_config: dialect.DatabaseConfig | None = None


def database_config(reload: bool = False) -> dialect.DatabaseConfig:
    """Which database we stick to. Read once at start."""
    global _db_config
    if _db_config is None or reload:
        _db_config = dialect.load_config(
            BASE_DIR, str(load_config().database_path)
        )
    return _db_config


def current_dialect() -> str:
    return dialect.POSTGRES if database_config().is_postgres else dialect.SQLITE


# The columns of a table on PostgreSQL (name and type, in the table's order). One query for every place
# that asks about columns - `Connection.table_columns`, `columns_of` and `_column_types`.
# Only in the schema Jellyscope creates its tables in (`current_schema()`): a database can have more
# schemas, and a table of the same name in another one is not ours. Without that condition the columns
# of both would merge, and a table that is only elsewhere would look as if it existed.
_POSTGRES_COLUMNS = ("SELECT column_name, data_type FROM information_schema.columns"
                     " WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position")


# ---------------------------------------------------------------------------
# A common interface over both databases
# ---------------------------------------------------------------------------

class Connection:
    """A wrapper that behaves the same outwardly for SQLite and PostgreSQL.

    It does three things:
      1. translates SQL into the dialect of the target database,
      2. unifies the interface (psycopg has no `executemany` on the connection),
      3. returns rows so they can be read by the column's name.
    """

    def __init__(self, raw: Any, kind: str) -> None:
        self._raw = raw
        self.kind = kind

    @property
    def is_postgres(self) -> bool:
        return self.kind == dialect.POSTGRES

    def execute(self, sql: str, params: Any = ()) -> Any:
        query = dialect.translate(sql, self.kind)
        if self.is_postgres:
            cursor = self._raw.cursor()
            # An empty tuple, not None - even when the query has no parameters.
            #
            # The translation doubles percent signs (`LIKE '%cs%'` -> `LIKE '%%cs%%'`),
            # because psycopg reads `%` as the start of a placeholder. Back to one
            # percent sign they are put only by psycopg - but it does that only
            # when it gets some parameters. With `None` the query would go out
            # with the doubled percent signs and look for the text "%cs%" literally.
            cursor.execute(query, tuple(params))
            return cursor
        return self._raw.execute(query, params)

    def executemany(self, sql: str, rows: Any) -> Any:
        query = dialect.translate(sql, self.kind)
        if self.is_postgres:
            cursor = self._raw.cursor()
            cursor.executemany(query, [tuple(r) for r in rows])
            return cursor
        return self._raw.executemany(query, rows)

    def executescript(self, script: str) -> None:
        """Runs a whole schema file."""
        if self.is_postgres:
            # psycopg handles several statements in one call when there are
            # no parameters - and the schema has none.
            self._raw.cursor().execute(script)
        else:
            self._raw.executescript(script)

    def insert_returning_id(self, sql: str, params: Any = ()) -> int:
        """Inserts a row and returns its new id.

        SQLite has `cursor.lastrowid` for it, PostgreSQL `RETURNING id`.
        The difference is hidden here, so the rest of the program does not have to know about it.
        """
        if self.is_postgres:
            cursor = self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
            row = cursor.fetchone()
            if row is None:
                return 0
            # The branch is for PostgreSQL only, and that returns a dict -
            # no reason to read by position here.
            return int(row["id"])

        cursor = self.execute(sql, params)
        return int(cursor.lastrowid or 0)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def table_columns(self, table: str) -> set[str]:
        """Names of the table's columns. Used in migrations.

        On PostgreSQL only in our schema, see `_POSTGRES_COLUMNS`.
        """
        if self.is_postgres:
            cursor = self.execute(_POSTGRES_COLUMNS, (table,))
            return {row["column_name"] if isinstance(row, dict) else row[0]
                    for row in cursor.fetchall()}

        rows = self._raw.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}


def _lock_against_1_8(conn: Any) -> None:
    """The view that stops a start of 1.8.x on this database (schema_names.LOCK_VIEW says why).

    Made only when nothing of that name is there: a table of that name would be an unconverted bin, and
    the conversion before init_db has renamed it by then.
    """
    from . import schema_names

    name = schema_names.LOCK_VIEW
    if conn.is_postgres:
        found = conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                             " WHERE c.relname = ? AND n.nspname = current_schema()", (name,)).fetchone()
    else:
        found = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    if not found:
        conn.execute(f"CREATE VIEW {name} AS SELECT CAST('Jellyscope 1.9.0 or newer' AS TEXT) AS database_of")


# ---------------------------------------------------------------------------
# Connection pool (PostgreSQL only)
# ---------------------------------------------------------------------------
#
# SQLite is a file on the disk - opening it costs a fraction of a millisecond,
# so it is opened and closed again every time, and that is fine.
#
# PostgreSQL is a server. Every connection means a TCP handshake, a sign-in and,
# on the server's side, a new process - single milliseconds on localhost, easily
# tens over a network. One Jellyscope page asks for data about twelve times, so
# this price would be paid twelve times for every load.
#
# A pool keeps a handful of connections open and lends them. The price of
# connecting is paid once at start, not with every query.
#
# What it costs: every idle connection holds a process on the server (in the
# order of single MB of memory). Hence min_size = 1 - at rest just one hangs
# there, the rest is connected only when really needed, and disappears again
# after a while of idleness.

_pool: Any = None
_pool_key: tuple | None = None

# The ceiling is 8: the collector in the background + a few concurrent requests
# from the web. A higher number would not help - the requests are served by a
# limited number of threads anyway - and would only hold processes on the
# database server for nothing.
POOL_MAX_SIZE = 8


def _pool_identity(config: dialect.DatabaseConfig) -> tuple:
    """What tells us the pool belongs to another server than now."""
    return (config.host, config.port, config.database, config.user, config.password)


def close_pool() -> None:
    """Closes the pool. Called when the application shuts down and before a restart.

    Without this, after `os.execv` connections would stay hanging that the new
    process knows nothing about, and PostgreSQL would hold them until its own
    timeout.
    """
    global _pool, _pool_key
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001 - an error does not matter while shutting down
            pass
    _pool = None
    _pool_key = None


def _get_pool(config: dialect.DatabaseConfig) -> Any:
    """Returns the pool for the given connection, or None.

    None means "go the old way, connection by connection". It happens in two
    cases, and neither is an error:

      * the user switched the pool off in Settings,
      * the psycopg_pool library is not available and cannot be installed.

    In both cases the application keeps working, only it connects anew for
    every query. On the same network the difference is small, over a WAN
    noticeable - but it has to work always.
    """
    global _pool, _pool_key

    if not config.use_pool:
        close_pool()
        return None

    identity = _pool_identity(config)
    if _pool is not None and _pool_key == identity:
        return _pool

    close_pool()

    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError:
        return None

    pool = ConnectionPool(
        conninfo="",
        kwargs={
            "host": config.host,
            "port": config.port,
            "dbname": config.database,
            "user": config.user,
            "password": config.password,
            "row_factory": dict_row,
            "connect_timeout": 10,
        },
        min_size=1,
        max_size=POOL_MAX_SIZE,
        # When every connection is lent out, wait at most 10 s, then fail.
        # Without a limit the request would get stuck forever.
        timeout=10.0,
        max_idle=300.0,
        open=True,
        name="jellyscope",
    )
    _pool, _pool_key = pool, identity
    return pool


class DatabaseUnavailable(TranslatableError):
    """The database cannot be opened - and we know why.

    A type of its own, so it can be caught in the launcher and printed without
    a stack trace: to somebody whose container did not start, twenty lines
    from the guts of sqlite3 are of no use.

    It is made of several sentences, each a key and its values (`lines`).
    `str(error)` is English (the console), `translated(language)` any language -
    the outage page shows the one the browser asks for. The interface language
    cannot be used: it is a setting stored in the database that could not be opened.
    """

    def __init__(self, lines: list[tuple[str, dict[str, Any]]]) -> None:
        self.lines = lines
        super().__init__(lines[0][0], **lines[0][1])

    def translated(self, language: str | None = None) -> str:
        return "\n".join(translate_with(template, language, **values) for template, values in self.lines)

    def __str__(self) -> str:
        return self.translated(SOURCE_LANGUAGE)


class ConversionFailed(DatabaseUnavailable):
    """The database could not be converted to the English names (jellyscope.conversion) - nothing changed.

    A kind of `DatabaseUnavailable`, so the launcher shows the outage page with the reason and what to do.
    """


# What runs a container's application and how it is fixed: on the host, only the data folder.
CONTAINER_ADVICE = ("In a container this is nearly always the owner of the mounted folder: inside runs a user "
                    "with UID 10001, while on the host the folder belongs to somebody else - typically root, "
                    "when Docker created it itself. Fix it on the host, in the folder next to "
                    "docker-compose.yml - and only the data folder, not the whole project:")


def _why_unopenable(path: Path, error: Exception) -> list[tuple[str, dict[str, Any]]]:
    """Why the database file cannot be opened - and what to do about it: sentences as keys and values.

    It is written here because only this moment knows the answer: the path,
    the permissions of the folder and whether we run in a container. A bit
    further on, all that is left of it is "unable to open database file".
    """
    folder = path.parent
    if not folder.is_dir():
        lines = [("The database {path} cannot be opened: the folder {folder} does not exist and cannot be created.",
                  {"path": path, "folder": folder})]
    elif not os.access(folder, os.W_OK | os.X_OK):
        lines = [("The database {path} cannot be opened: the folder {folder} cannot be written to.",
                  {"path": path, "folder": folder})]
    elif path.exists() and not os.access(path, os.W_OK):
        lines = [("The database {path} cannot be opened: the file is read-only.", {"path": path})]
    else:
        lines = [("The database {path} cannot be opened: {error}.", {"path": path, "error": error})]

    # Who we are - without it the folder's owner cannot be compared with anything.
    if hasattr(os, "getuid"):
        lines.append(("The application runs as UID {uid}:{gid}.", {"uid": os.getuid(), "gid": os.getgid()}))

    # The same question as elsewhere in the application, not an own copy of the
    # condition: it recognises Podman too, and a test can substitute it.
    if app_config._in_container():
        lines += [(CONTAINER_ADVICE, {}), ("    sudo chown -R 10001:10001 data", {}), ("    docker compose restart", {})]
    else:
        lines.append(("Check that the folder exists and that the user the application runs as is allowed to "
                      "write into it.", {}))
    return lines


def _open_raw(config: dialect.DatabaseConfig) -> Any:
    if config.is_postgres:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on the installation
            # Two things this message used to break on:
            #
            # 1. It advised `psycopg[binary,pool]`, so it looked as if it
            #    enforced the connection pool - even when the user had it
            #    switched off. The pool is an extra; without psycopg you do
            #    not connect to PostgreSQL at all, pool on or not.
            #
            # 2. It advised a bare `pip`, but the application runs in a
            #    virtual environment. Installing into the system python
            #    would be pointless (and newer Ubuntu would refuse it
            #    outright). sys.executable is the right python - the one
            #    this is running with right now.
            raise TranslatableError(
                "PostgreSQL needs the psycopg library. Install it into the same environment the application "
                "runs in:\n    {python} -m pip install \"psycopg[binary]\"\nThen restart the application. (The "
                "connection pool has nothing to do with it - it is switched on separately and adds the extra "
                "\"pool\".)", python=sys.executable) from exc

        raw_conn = psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=config.password,
            row_factory=dict_row,
            connect_timeout=10,
        )

        # The connection's time zone has to be the one the application counts in.
        #
        # Without it, "local time" is counted in TWO places differently:
        #   * in SQL (date(x, 'localtime')) by the zone of the database server,
        #   * when printing times in Python by the zone of the application's server.
        #
        # When they differ - and they often do, PostgreSQL usually runs in UTC -
        # a day in a chart does not end where the listing ends it. A click on
        # the day then shows records too that belong elsewhere by the time shown.
        #
        # TZ is set by the service configurations in deploy/ (Europe/Prague).
        zone = os.environ.get("TZ", "").strip()
        if zone:
            try:
                with raw_conn.cursor() as cursor:
                    # The zone name cannot be passed as a parameter, so it is
                    # checked before it is inserted - only what looks like a
                    # zone name may go into SQL.
                    if re.fullmatch(r"[A-Za-z0-9_+\-/]{1,64}", zone):
                        cursor.execute(f"SET TIME ZONE '{zone}'")
                raw_conn.commit()
            except Exception:  # noqa: BLE001 - a wrong zone must not bring the start down
                log.warning("time zone %r could not be set", zone)

        return raw_conn

    path = Path(config.path)
    if not path.is_absolute():
        path = BASE_DIR / path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=15.0)
        # row_factory makes the results come out as dicts (row["name"])
        # instead of anonymous tuples (row[3]). The code is readable then.
        connection.row_factory = sqlite3.Row
        # WAL = Write-Ahead Logging. It lets one process write while another
        # reads at the same time. Exactly our case: the collector writes in the
        # background, the web reads.
        #
        # Inside the `try` on purpose: SQLite can open a file read-only, so
        # the connection comes into being and only this PRAGMA fails on
        # "attempt to write a readonly database". From outside it is the same
        # failure as "unable to open database file", and the same answer
        # belongs to it.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
    except (OSError, sqlite3.Error) as error:
        # SQLite has a single sentence for this: "unable to open database file"
        # or "attempt to write a readonly database". There is no path in it,
        # no reason, nothing about what to do - and in a container it is the
        # most common error at the first start of all.
        raise DatabaseUnavailable(_why_unopenable(path, error)) from error
    return connection


@contextmanager
def connect(config: dialect.DatabaseConfig | None = None) -> Iterator[Connection]:
    """Opens a connection to the database as a "context manager" (used with `with`).

        with db.connect() as conn:
            conn.execute("INSERT ...")
        # here the change is saved and the connection closed

    When an error happens inside the block, the changes are rolled back - so
    the database never stays in the middle of a half-done operation.
    """
    config = config or database_config()

    # On PostgreSQL we borrow the connection from the pool and give it back at
    # the end - we do not close it. `pool.connection()` itself rolls back when
    # the block ends with an exception, and tidies the connection up before
    # lending it to the next one.
    pool = _get_pool(config) if config.is_postgres else None
    if pool is not None:
        with pool.connection() as raw:
            connection = Connection(raw, config.kind)
            yield connection
            connection.commit()
        return

    connection = Connection(_open_raw(config), config.kind)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def compact() -> bool:
    """Rewrites the database so that no trace is left after deleted rows.

    Without this, "deleted" is just an entry in a list: the row is no longer
    found, but its bytes stay in the file until something overwrites them.
    Whoever has access to the file can read them.

    It takes time in proportion to the size of the database, because the file
    really is rewritten. So it runs only after operations that delete on
    purpose - not after every write.

    SQLite needs both: `VACUUM` rewrites the database and `wal_checkpoint`
    throws away the journal next to it too, where the trace would otherwise
    stay as well. PostgreSQL can do it with `VACUUM FULL`, but that locks the
    table for the time - so just that one table, not the whole database.

    Returns whether it worked. When not, the data are deleted anyway; only
    something of them is left in the file, and that shows in the log.
    """
    config = database_config()
    try:
        if config.is_postgres:
            with connect(config) as conn:
                # VACUUM must not run in a transaction, and psycopg opens one
                # by itself. Hence autocommit - and back at the end, so the
                # next loan from the pool does not get a changed connection.
                raw = conn._raw
                previous = raw.autocommit
                raw.rollback()
                raw.autocommit = True
                try:
                    with raw.cursor() as cursor:
                        cursor.execute("VACUUM (FULL, ANALYZE) playback")
                finally:
                    raw.autocommit = previous
        else:
            raw_conn = _open_raw(config)
            try:
                # `isolation_level = None` means "no transaction".
                raw_conn.isolation_level = None
                raw_conn.execute("VACUUM")
                raw_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                raw_conn.close()
    except Exception as error:      # noqa: BLE001 - tidying up must not bring the action down
        log.warning("the database could not be rewritten: %s", error)
        return False
    log.info("database rewritten, no trace left of the deleted rows")
    return True


def _importable(name: str) -> bool:
    """Is the library installed?

    We ask by trying to import it, not by looking for a file: a broken
    installation (a missing system library psycopg builds on) is on the disk
    but cannot be imported - and for us that is the same as if it were not
    there.
    """
    try:
        importlib.import_module(name)
    except ImportError:
        return False
    return True


def psycopg_available() -> bool:
    """Is the PostgreSQL driver available?

    Used by Settings: when the driver is there (as it usually is after an
    installation by the script), it makes no sense to show the user how to
    install it. Advice you do not need only takes up space and confuses.
    """
    return _importable("psycopg")


def pool_available() -> bool:
    """Is the library for the connection pool available?"""
    return _importable("psycopg_pool")


def test_connection(config: dialect.DatabaseConfig) -> tuple[bool, str]:
    """Tries to connect. Returns (success, a message for the user).

    The connection is made **directly** here, outside the pool. We test
    whether the server can be reached, not the pool - and were the test to
    borrow a connection, it would create a pool for a server the application
    has not been switched to yet.
    """
    raw = None
    try:
        raw = _open_raw(config)
        Connection(raw, config.kind).execute("SELECT 1 AS ok").fetchone()
    except Exception as exc:  # noqa: BLE001 - the message is shown to the user
        return False, f"{type(exc).__name__}: {error_text(exc)}"
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:  # noqa: BLE001
                pass

    # A pool switched on without the library is not an error, but the user
    # should know - otherwise they would wait for a speed-up that never comes.
    if config.is_postgres and config.use_pool and not pool_available():
        return True, translate_with(
            "The connection works: {database} - but the connection pool cannot be switched on, the library "
            "psycopg_pool is missing. The application will go on, it will only open connections one by one.",
            None, database=config.describe())
    return True, translate_with("The connection works: {database}", None, database=config.describe())


# ---------------------------------------------------------------------------
# Creating and maintaining the schema
# ---------------------------------------------------------------------------

# Columns added to the project only later.
#
# Why it is needed: `CREATE TABLE IF NOT EXISTS` does not add a new column to
# a table that already exists - the statement is simply skipped. Whoever ran
# the application before would have the old table after an update, and the
# application would report "no such column". So at every start we check that
# all the expected columns exist, and add the missing ones.
#
# This is called a **database migration**. Big projects have whole libraries
# for it; this dictionary is enough for us.
MIGRATIONS: dict[str, dict[str, str]] = {
    "scan_log": {
        # A task's message as an English key with its values apart (21. 11. 2026), so each reader gets
        # it in their language - i18n.message_parts / stored_text. Empty in older rows.
        "message_values": "TEXT",
    },
    "libraries": {
        # Every Jellyfin library is stored since 16. 11. 2026 (the Library page takes them all
        # over); a library missing from two sync runs in a row is hidden (stats.library_cards).
        "missing_runs": "INTEGER NOT NULL DEFAULT 0",
    },
    "seerr_request": {
        # The title's id in Jellyfin (media.jellyfinMediaId): a movie = items.id,
        # a series = items.series_id of its episodes. Episodes carry no TMDB id
        # (Jellyfin 12 does not send SeriesProviderIds for them), so by TMDB the
        # series does not pair with the library. The table came into being on
        # 13. 11. 2026 without it.
        "jellyfin_id": "TEXT NOT NULL DEFAULT ''",
        # Posters of requests (13. 11. 2026). `tvdb_id`: Sonarr looks a series
        # up by its TVDB id (media.tvdbId). `poster`: the poster path from Seerr
        # (posterPath) for titles that are not in Jellyfin; '' = not looked up
        # yet (the next sync fills it in), '-' = Seerr has none.
        "tvdb_id": "TEXT NOT NULL DEFAULT ''",
        "poster": "TEXT NOT NULL DEFAULT ''",
    },
    "music_track": {
        # The track's file path (16. 11. 2026): music on its disk in the Disks card, in the total
        # size and in the library growth (content_kinds.py). NULL = the sync has not asked yet.
        "path": "TEXT",
    },
    "library_disk_snapshot": {
        # A fingerprint of the disk settings (the source of capacity, virtual
        # disks, merged devices). The pace is counted only across snapshots
        # with the same fingerprint - see stats.disks._segment().
        "fingerprint": "TEXT NOT NULL DEFAULT ''",
        # Bytes by content kind, JSON (16. 11. 2026, content_kinds.py); NULL = a snapshot written
        # before, video only - completed when read (stats/content_history.py).
        "kinds": "TEXT",
    },
    "library_snapshot": {
        # Parts by content kind, JSON {"music": {"items", "size"}} (16. 11. 2026); `size_bytes` is the
        # whole from then on. NULL = written before, video only - completed when read.
        "kinds": "TEXT",
    },
    "items": {
        # The values of an analysis error's key (21. 11. 2026, i18n.message_parts) - the item's page reads
        # it in the reader's language.
        "tech_error_values": "TEXT",
        # Copies of the same movie in one library: on a secondary copy the id
        # of the main one, otherwise empty. Recounted by scanner.recount_copies().
        "main_id": "TEXT",
        # A fingerprint of the image from Jellyfin (ImageTags.Primary). Another
        # image = another fingerprint, so it tells us the cached one is no
        # longer valid. See web.item_image() and scanner._forget_images().
        "image_tag": "TEXT",
        # A fingerprint of the SERIES poster (Jellyfin reports it with every
        # episode as SeriesPrimaryImageTag). We have no item for the series, so
        # without it there was nothing to compare its poster with - and an
        # image once downloaded stayed there forever.
        "series_image_tag": "TEXT",
        "audio_languages": "TEXT",
        "subtitle_languages": "TEXT",
        "default_audio_language": "TEXT",
        # What the file name promises but cannot be assigned to a particular
        # stream. Added to the language summaries - see scanner._recount_item_languages.
        "audio_from_name": "TEXT",
        "subtitle_from_name": "TEXT",
        # An identifier from TMDB. It survives re-encoding the file, unlike
        # Jellyfin's ItemId - see scanner._merge_by_tmdb().
        "tmdb_id": "TEXT",
        # Genres from Jellyfin, separated by a vertical bar. Used in the user's
        # detail - "what does this person actually watch".
        "genres": "TEXT",
        # The dynamic range as Jellyfin reports it (SDR/HDR/DOVI).
        # Next to what ffprobe measured: Dolby Vision in Matroska can be read
        # only by ffmpeg 5, so an older ffprobe reports just "HDR" and the DV
        # gets lost. Jellyfin knows it, though. See stats.RANGE_CASE.
        "video_range_reported": "TEXT",
    },
    "dashboard_layout": {
        # The width of a card in the custom dashboard. Empty = as the card
        # has it in the registry; a person can switch it while editing the dashboard.
        "width": "TEXT",
    },
    "item_streams": {
        # Where the stream's language comes from. Empty = from the source of
        # technical data, "jellyfin" = the file did not state it and it was
        # filled in from the library.
        "language_source": "TEXT",
    },
    "accounts": {
        # Which Jellyfin viewer the account belongs to. Empty = a local
        # account that has nothing to do with Jellyfin. This id tells which
        # playbacks are "mine" - it is the same id as in the table `users`.
        "jellyfin_user_id": "TEXT",
        # The account's own permissions as JSON, or NULL = the group's settings
        # apply (readers / viewers). See access.own_permissions().
        "permissions": "TEXT",
        # The interface language the account chose on the My account page.
        # NULL = the server's language. See i18n.current_language().
        "ui_language": "TEXT",
        # May the account make its own API key? "1" / "0", NULL = by the
        # group. See access.may_have_key().
        "may_have_key": "TEXT",
        # May the seasonal decoration move? "0" = the account switched the
        # animations off on the My account page, NULL = they move. See seasonal.motion().
        "events_motion": "TEXT",
    },
    "users": {
        # A fingerprint of the profile picture from Jellyfin (PrimaryImageTag
        # from /Users). NULL = no picture. The fingerprint also tells when it
        # changes - it is in the address the browser remembers. See web.my_image().
        "image_tag": "TEXT",
    },
    "api_tokens": {
        # Whom the key belongs to. NULL = a root key (without an owner, keys
        # from the time before owners included). A deleted account takes its
        # keys with it. See api.verify_token().
        "account_id": "INTEGER REFERENCES accounts(id) ON DELETE CASCADE",
        # Permissions of a root key: NULL = everything (keys from before owners too - so
        # Grafana does not break after an update), otherwise JSON from api.pack_key_permissions.
        "permissions": "TEXT",
        # active / disabled. Only an admin may switch it off and on;
        # revoking is deleting the row.
        "status": "TEXT NOT NULL DEFAULT 'active'",
    },
    "playback": {
        "audio_language": "TEXT",
        "subtitle_language": "TEXT",
        # Running, or paused? Added at run time to older databases too - see
        # the migration below.
        "is_paused": "INTEGER NOT NULL DEFAULT 0",
        # Dimensions of the picture the session is playing right now. They used
        # to be taken from the table `items`, so for an episode we had not
        # synchronised yet the resolution was missing altogether. The session
        # knows it itself.
        "video_width": "INTEGER",
        "video_height": "INTEGER",
        # The language playing RIGHT NOW. Next to it stands `audio_language`,
        # which keeps the first one found. Each answers a different question -
        # see collector._store_sessions().
        # The length of the programme as the session itself reports it. It used
        # to be taken from the table `items` only, so for an episode we had not
        # synchronised yet the progress could not be counted - and the
        # indicator was missing.
        # BIGINT, not INTEGER: one tick is 100 nanoseconds, so a quarter-hour
        # episode has over 9 billion - that does not fit into a 4-byte INTEGER
        # in PostgreSQL ("integer out of range").
        "media_runtime_ticks": "BIGINT",
        "current_audio_language": "TEXT",
        "current_subtitle_language": "TEXT",
        # Since when the current combination of streams plays and whether it has
        # been counted in the statistics yet. See collector.MIN_LANGUAGE_SECONDS.
        "language_since": "TEXT",
        "language_confirmed": "INTEGER NOT NULL DEFAULT 0",
        # What exactly happens in a transcode - see collector._describe_stream().
        # A bare "transcode" does not say whether the server re-encodes the
        # picture (expensive), only the sound (cheap), or burns in subtitles.
        "transcode_video_direct": "INTEGER",
        "transcode_audio_direct": "INTEGER",
        "transcode_hw": "TEXT",
        # How fast the conversion runs - now, and the average through the sum
        # and the number of samples (whole numbers: decimals make no difference
        # to the question "does it keep up?"). `video_fps` is the frame rate of
        # the video itself, which the speed of the conversion is compared with.
        # See collector._describe_stream().
        "transcode_fps": "INTEGER",
        "transcode_fps_sum": "INTEGER NOT NULL DEFAULT 0",
        "transcode_fps_samples": "INTEGER NOT NULL DEFAULT 0",
        "video_fps": "INTEGER",
    },
}


# Columns whose original type turned out not to be enough.
#
# The migrations above can only **add** a column. When it turns out later
# that it should have had another type, that does not help the tables that
# already exist - and the error shows only at run time ("integer out of range").
#
# It concerns PostgreSQL only: INTEGER is four bytes there. SQLite sizes it by
# the value, so there is nothing to overflow.
TYPE_FIXES: dict[tuple[str, str], str] = {
    # One tick is 100 nanoseconds, so a quarter-hour episode has over
    # 9 billion - it does not fit into a four-byte INTEGER.
    ("playback", "media_runtime_ticks"): "BIGINT",
}


def _migrate(conn: Connection) -> list[str]:
    """Adds the columns the database does not have yet. Returns what it added."""
    added: list[str] = []
    for table, columns in MIGRATIONS.items():
        existing = conn.table_columns(table)
        if not existing:
            continue  # the table does not exist yet, the schema creates it
        for column, column_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                added.append(f"{table}.{column}")

    if conn.is_postgres:
        for (table, column), new_type in TYPE_FIXES.items():
            if column in conn.table_columns(table):
                # Idempotent: when the type is right already, PostgreSQL does
                # nothing. So it does not have to be recorded anywhere.
                conn.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"
                )
    return added


# Until 15. 11. 2026 Sonarr and Radarr had shared tables `arr_event` and `arr_titul` with a column
# `app`. Now every app has its own (see schema.sql). What is copied from an old table where:
# {old table: {app: (new table, columns)}} - the columns under their names in the OLD table (that
# shape is fixed for good); they land in the new table's columns through schema_names (the names
# were Czech until 1.9.0 - such a database goes straight into the English ones). A movie has no
# episode, TVDB id or Jellyfin series id - Radarr never filled those columns, and its tables do not
# have them.
_EVENT_COLUMNS = ("arr_id", "typ", "datum", "titul", "epizoda", "polozka", "titul_id", "kvalita", "rozliseni", "velikost",
                  "indexer", "release_group", "klient", "protokol", "zdroj", "duvod", "download_id", "vydano", "cf_skore")
_TITLE_COLUMNS = ("titul_id", "nazev", "tmdb_id", "tvdb_id", "imdb_id", "jellyfin_id", "pridano", "synchronizovano")
_OLD_ARR_TABLES: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "arr_event": {"sonarr": ("sonarr_event", _EVENT_COLUMNS),
                  "radarr": ("radarr_event", tuple(s for s in _EVENT_COLUMNS if s != "epizoda"))},
    "arr_titul": {"sonarr": ("sonarr_title", _TITLE_COLUMNS),
                  "radarr": ("radarr_title", tuple(s for s in _TITLE_COLUMNS if s not in ("tvdb_id", "jellyfin_id")))},
}


def _row_count(conn: Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    """The number of the table's rows (the name and the condition are constants of the code, values go as parameters)."""
    return int(dict(conn.execute(f"SELECT COUNT(*) AS count FROM {table}{where}", params).fetchone())["count"] or 0)


def _convert_table(conn: Connection, old: str, targets: dict[str, tuple[str, tuple[str, ...]]]) -> int:
    """Rows of one old shared table into the apps' tables; then deletes the old one. Returns how many it really inserted."""
    from . import schema_names

    has = conn.table_columns(old)
    found = inserted = 0
    for app, (new_table, columns) in targets.items():
        # Only the columns the old table really has: the titles table came into
        # being on 13. 11. 2026 without the time added (`pridano`), and not every one got it in time.
        present = [s for s in columns if s in has]
        names = next((t.columns for t in schema_names.TABLES if t.new == new_table), {})
        shared, renamed = ", ".join(present), ", ".join(names.get(s, s) for s in present)
        found += _row_count(conn, old, " WHERE app = ?", (app,))
        # ON CONFLICT DO NOTHING: what is in the new table already stands - nothing is doubled or overwritten.
        # How many really got inserted is told by the row count of the new table before and after (it runs in
        # the start's transaction, nobody else writes into it) - the driver's `rowcount` is not relied on here.
        before = _row_count(conn, new_table)
        conn.execute(f"INSERT INTO {new_table} ({renamed}) SELECT {shared} FROM {old} WHERE app = ?"
                     " ON CONFLICT DO NOTHING", (app,))
        inserted += _row_count(conn, new_table) - before
    if found != inserted:
        log.info("table %s: %s rows were already in the new tables and stay unchanged", old, found - inserted)
    total = _row_count(conn, old)
    if total != found:
        # A row of an app Jellyscope does not know has nowhere to go. It is a copy of someone else's history - it can be downloaded again.
        log.warning("table %s: %s rows of an unknown app were not converted", old, total - found)
    conn.execute(f"DROP TABLE {old}")
    return inserted


def _convert_arr_tables(conn: Connection) -> int:
    """Converts a database with the shared tables of Sonarr and Radarr to tables per app.

    Called at start right after the tables are created. A database in the new
    shape does not have the old tables, and nothing happens. Otherwise the rows
    are copied by app into the new tables and the old ones are deleted - all in
    the start's transaction: an error in the middle leaves the database as it
    was, and the next start tries again. The sync marks are in the settings and
    do not change, so nothing is downloaded twice after the conversion. Returns
    the number of converted rows.
    """
    converted, old_count = 0, 0
    for old, targets in _OLD_ARR_TABLES.items():
        if conn.table_columns(old):
            old_count += 1
            converted += _convert_table(conn, old, targets)
    if old_count:
        # Even with zero: an old table was deleted, and that must not happen without a word.
        log.info("Sonarr and Radarr tables converted to per-app tables: %s rows", converted)
    return converted


def _is_index(statement: str) -> bool:
    """Does this statement create an index? Leading comments are skipped."""
    for line in statement.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        return stripped.upper().startswith(("CREATE INDEX", "CREATE UNIQUE INDEX"))
    return False


def _split_indexes(script: str) -> tuple[str, str]:
    """Splits the schema into two parts: tables and indexes.

    Why: an index refers to a column, and `CREATE TABLE IF NOT EXISTS` does
    not add a new column to a table that already exists. Whoever ran the
    application before would therefore have an old table without `tmdb_id`,
    but the schema would try to create `idx_items_tmdb` right away - and the
    whole start would fail on "no such column: tmdb_id".

    So the right order is: **tables, then migrations, only then indexes.**
    The split goes by lines - a statement ends with a line ending in a
    semicolon. That is enough for us here; more complex SQL (say triggers
    with a nested `BEGIN ... END;`) the schema does not have.
    """
    tables: list[str] = []
    indexes: list[str] = []
    statement: list[str] = []

    for line in script.splitlines(keepends=True):
        statement.append(line)
        if line.rstrip().endswith(";"):
            text = "".join(statement)
            (indexes if _is_index(text) else tables).append(text)
            statement = []

    if statement:
        # The rest without a semicolon - typically a comment at the end of the file.
        tables.append("".join(statement))

    return "".join(tables), "".join(indexes)


def init_db(config: dialect.DatabaseConfig | None = None) -> list[str]:
    """Creates the tables, adds the missing columns and sets the default values.

    When it turns out meanwhile that the database cannot be written to,
    `DatabaseUnavailable` is raised with an understandable explanation - see
    `_open_raw`. It need not show when opening: a read-only file opens fine,
    and only the first write tells it will not work.
    """
    config = config or database_config()
    schema_file = SCHEMA_POSTGRES if config.is_postgres else SCHEMA_SQLITE
    tables, indexes = _split_indexes(schema_file.read_text(encoding="utf-8"))

    with _guarded_writes(config):
        # The old names first: the schema's CREATE TABLE IF NOT EXISTS would make the new ones empty beside them.
        from . import conversion
        conversion.convert(config)
        return _init_db(config, tables, indexes)


@contextmanager
def _guarded_writes(config: dialect.DatabaseConfig) -> Iterator[None]:
    """A write that failed for lack of permissions turned into `DatabaseUnavailable`."""
    try:
        yield
    except sqlite3.Error as error:
        if config.is_postgres:
            raise
        path = Path(config.path)
        if not path.is_absolute():
            path = BASE_DIR / path
        raise DatabaseUnavailable(_why_unopenable(path, error)) from error


def _init_db(config: dialect.DatabaseConfig, tables: str,
             indexes: str) -> list[str]:
    with connect(config) as conn:
        conn.executescript(tables)
        added = _migrate(conn)
        _convert_arr_tables(conn)
        _ensure_playback_bin(conn)
        _lock_against_1_8(conn)
        if indexes.strip():
            conn.executescript(indexes)
        for key, value in DEFAULT_SETTINGS.items():
            # ON CONFLICT DO NOTHING is understood by both databases - unlike
            # SQLite's construct INSERT OR IGNORE, which is its own only.
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO NOTHING",
                (key, value),
            )
        # Checking for a new version moved among the tasks. Whoever had it
        # switched on should keep it on - otherwise it would quietly stop
        # working after an update, with no way of telling why.
        old_check = conn.execute(
            "SELECT value FROM settings WHERE key = 'update_check_enabled'"
        ).fetchone()
        # By the column's name, not by position: PostgreSQL returns a dict and
        # `row[0]` is a KeyError on it. SQLite handles both, so it does not
        # show during development - it shows only at start for people with
        # PostgreSQL, and in such a way that the application does not start at all.
        if old_check is not None and str(dict(old_check).get("value")) == "1":
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('task_updates_enabled', '1')"
                " ON CONFLICT (key) DO NOTHING")

        for key in DROPPED_SETTINGS:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    forget_settings()   # the schema may have changed

    # The first filling from .env is done only for the database the
    # application really uses.
    #
    # `init_db()` is called on a **foreign** database too - when data are
    # moved elsewhere in Settings, the schema is created there by the same
    # function. But seed_from_env() reads and writes through the default
    # connection, so it would touch a database that is not the point here at
    # all (and when moving into a database not created yet, it would fail
    # outright).
    if config is database_config():
        seed_from_env()
    return added


# Every table of the schema, in the order they can be filled in:
# `item_streams` refers to `items`, `dashboard_layout` and `api_tokens` to
# `accounts`. They are to be deleted in the reverse order.
#
# The single list for the backup by own export (tasks.BACKED_UP_TABLES)
# and for the conversion SQLite <-> PostgreSQL (dbmigrate.TABLES). The
# conversion used to have its own, hand-written list - and with new tables
# it silently left out API keys, dashboards or file versions. Tests guard it
# against both schemas (test_own_export_pg, test_database_conversion): a
# table added to schema.sql has to be added here too.
TABLES = ("libraries", "users", "items", "item_versions", "item_streams",
          "accounts", "dashboard_layout", "library_snapshot", "library_disk_snapshot",
          "settings", "scan_log", "api_tokens", "login_blocks",
          "forgettings", "playback", "detailed_map_tile", "sonarr_event", "sonarr_title", "radarr_event", "radarr_title",
          "lidarr_event", "lidarr_title", "readarr_event", "readarr_title", "seerr_request", "bazarr_event",
          "prowlarr_hour", "prowlarr_grab", "prowlarr_indexer",
          "music_artist", "music_album", "music_track", "book")


# The bin of forgotten viewers. On forgetting, rows are **moved** here, so
# they can be brought back until night; at night the bin is emptied for real.
PLAYBACK_BIN = "playback_bin"


def columns_of(conn: Any, table: str) -> list[str]:
    """Names of the table's columns. An empty list = there is no such table.

    On PostgreSQL only in our schema, see `_POSTGRES_COLUMNS`.
    """
    if conn.kind == dialect.POSTGRES:
        rows = conn.execute(_POSTGRES_COLUMNS, (table,)).fetchall()
        return [str(dict(r)["column_name"]) for r in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(dict(r)["name"]) for r in rows]


def _column_types(conn: Any, table: str) -> dict[str, str]:
    if conn.kind == dialect.POSTGRES:
        rows = conn.execute(_POSTGRES_COLUMNS, (table,)).fetchall()
        return {str(dict(r)["column_name"]): str(dict(r)["data_type"])
                for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(dict(r)["name"]): str(dict(r)["type"] or "TEXT")
            for r in rows}


def _ensure_playback_bin(conn: Any) -> None:
    """Creates the bin and evens its columns up with `playback`.

    The bin is **not** in schema.sql on purpose: were it written by hand,
    after a column was added to `playback` it would silently lose a part of
    the data - and that would show only when somebody wanted to bring a
    forgotten viewer back, that is exactly when it matters. This way it is
    made from its shape.

    `event_id` in addition: it tells which forgetting a row belongs to, and a
    whole group is brought back at once.
    """
    if not columns_of(conn, PLAYBACK_BIN):
        # The shape is taken from an empty select, not from `CREATE TABLE (LIKE ...)`:
        # both databases do this the same - and above all, the word LIKE does not
        # pass this way, which our dialect translator changes to ILIKE (see
        # dialect.translate) and PostgreSQL then refuses the DDL.
        conn.execute(f"CREATE TABLE IF NOT EXISTS {PLAYBACK_BIN} AS"
                     " SELECT * FROM playback WHERE 1=0")
        conn.execute(f"ALTER TABLE {PLAYBACK_BIN} ADD COLUMN event_id INTEGER")
    # On every start, not only with a new bin: the bin is not in schema.sql, and the conversion to
    # English names on SQLite (which cannot rename an index) drops its old index.
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_playback_bin_event"
                 f" ON {PLAYBACK_BIN} (event_id)")

    # Columns added to `playback` only after the bin was created.
    types = _column_types(conn, "playback")
    has = set(columns_of(conn, PLAYBACK_BIN))
    for column, column_type in types.items():
        if column not in has:
            conn.execute(f"ALTER TABLE {PLAYBACK_BIN} ADD COLUMN {column} {column_type}")
            log.info("bin: column %s added", column)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# Settings are read very often - and that used to mean a new connection to the
# database every time. It showed worst in translation: every `_("text")` in a
# template called `get_setting("ui_language")`, so one page opened over a
# hundred connections and spent most of its time doing it.
#
# So the whole table is kept in memory. It is small (a few dozen rows) and
# changes rarely.
#
# The validity is limited to a few seconds. On a write the memory is thrown
# away at once, so within the own process a change is immediate; the short
# validity is only a safeguard in case the application one day runs in more
# processes over a shared PostgreSQL and the other one changed a setting.
_settings_cache: dict[str, str] | None = None
_settings_cached_at: float = 0.0
SETTINGS_CACHE_SECONDS = 5.0


def _stored_settings() -> dict[str, str]:
    """What really is in the settings table (without the default values)."""
    global _settings_cache, _settings_cached_at

    now = time.monotonic()
    if _settings_cache is not None and (now - _settings_cached_at) < SETTINGS_CACHE_SECONDS:
        return _settings_cache

    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()

    _settings_cache = {row["key"]: row["value"] for row in rows}
    _settings_cached_at = now
    return _settings_cache


def forget_settings() -> None:
    """Throws the remembered settings away. Called after every write."""
    global _settings_cache
    _settings_cache = None


def get_setting(key: str, default: str | None = None) -> str:
    stored = _stored_settings()
    if key in stored and stored[key] is not None:
        return stored[key]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


# Settings that must never get into a page or a template.
#
# The API key for Jellyfin is a password: whoever has it can do anything with
# someone else's server that its owner can. So it does not belong in the
# browser, not even by mistake. The SMTP password, the Discord webhook and the
# Telegram token are access credentials like the Jellyfin key - whoever has
# them can send messages in your name.
SECRET_SETTINGS = ("jellyfin_api_key", "notify_smtp_password",
                   "notify_discord_webhook", "notify_telegram_token",
                   # The keys of the *Arr apps (jellyscope/arr/). Listed: db must not
                   # reach for the apps' packages (a cycle) - a new app adds its
                   # row, guarded by tests/test_arr_registry.py.
                   "arr_sonarr_api_key", "arr_radarr_api_key", "arr_lidarr_api_key", "arr_readarr_api_key", "arr_bazarr_api_key", "arr_prowlarr_api_key",
                   # The Seerr key (jellyscope/seerr/).
                   "seerr_api_key")


def prefill_backup_folder() -> str:
    """In OUR image, sets the backup folder when none is set.

    Without it the backup task ended on "Neni nastavena cesta pro zalohy" -
    and whoever filled the path in easily hit a place outside the mounted
    folder. Such a backup is stored inside the container and disappears with
    the next build; meanwhile the task reports for months that it ran.

    So next to the database there is `backups/`, that is inside /app/data - a
    folder our compose always mounts outside, so the backup can be reached
    from the host too.

    **Only for our image.** It used to be enough to be "in any container",
    and that was guessing: in someone else's container (LXC, a foreign image)
    we know nothing about a mounted volume, so `data/backups` is not a clever
    default but an unrequested change of settings. And on an own machine even
    less: there the sensible place for backups is another disk or the
    network, not a folder next to the database.
    """
    from .config import load_config

    config = load_config()
    if not config.our_image or get_setting("backup_path", "").strip():
        return ""

    path = config.database_path.parent / "backups"
    set_setting("backup_path", str(path))
    log.info("backups will be written to %s", path)
    return str(path)


def get_settings() -> dict[str, str]:
    """All the settings at once - the default values overridden by what is in the DB."""
    values = dict(DEFAULT_SETTINGS)
    for key, value in _stored_settings().items():
        values[key] = value if value is not None else ""
    return values


def get_public_settings() -> dict[str, str]:
    """The settings without secrets - this may be passed to a template.

    Templates print the API key nowhere today, but the whole dictionary used
    to be passed to them, the key included. One `{{ settings.jellyfin_api_key }}`
    would do (say while debugging) and the key would end up in the page
    source. It is cheaper not to let it in here at all than to watch that
    nobody prints it.
    """
    values = get_settings()
    for key in SECRET_SETTINGS:
        values.pop(key, None)
    return values


# What is written into the log by name only. A password, a token or a key
# have no business there - it is enough to know they changed.
SECRET_PARTS = ("heslo", "password", "token", "webhook", "api_key", "secret")

# Operational records, not settings: they rewrite themselves, and nothing but
# noise would be left of them in the log. An underscore at the start marks the
# collector's records - the time of the last query is rewritten with every
# query to Jellyfin.
UNLOGGED_SETTINGS = ("_", "jellyfin_version", "notify_state_", "db_compaction_pending",
                     # The state and the mark of the *Arr sync change with every run (once
                     # an hour) - in the log they would be just noise. Listed per app: db
                     # must not reach for the arr package (a cycle); a new app adds its two rows.
                     "arr_sonarr_state", "arr_sonarr_last_sync", "arr_radarr_state", "arr_radarr_last_sync",
                     "arr_lidarr_state", "arr_lidarr_last_sync", "arr_lidarr_titles", "arr_lidarr_titles_version",
                     "arr_readarr_state", "arr_readarr_last_sync", "arr_readarr_titles", "arr_readarr_titles_version", "arr_readarr_app_name",
                     "arr_bazarr_state", "arr_bazarr_last_sync",
                     "arr_prowlarr_state", "arr_prowlarr_last_id", "arr_prowlarr_last_event_at", "arr_prowlarr_counted",
                     "arr_sonarr_titles", "arr_radarr_titles", "arr_sonarr_titles_version", "arr_radarr_titles_version",
                     # The counts of missing and cutoff and Bazarr's languages, stored with their time by every sync.
                     "arr_sonarr_counts", "arr_radarr_counts", "arr_lidarr_counts", "arr_readarr_counts",
                     "arr_bazarr_missing_languages",
                     "seerr_state", "seerr_last_sync",
                     # What Jellyscope finds out about Seerr by itself with every sync.
                     "seerr_images", "seerr_web", "seerr_posters_retry",
                     # The state of the music sync (every hour).
                     "music_state", "music_full_pass", "music_vanished_libraries",
                     # The same for the books sync.
                     "books_state", "books_full_pass", "books_vanished_libraries")


# Name endings whose value may be printed. Numbers, switches, the server's
# address - none of it says anything about anybody.
SAFE_SUFFIXES = ("enabled", "host", "port", "tls", "time", "interval",
                 "minutes", "days", "keep", "concurrency", "zoom",
                 "language", "skin", "source", "generation", "capacity",
                 "unit", "path", "mappings", "timezone")


def _is_secret(key: str) -> bool:
    return any(part in key for part in SECRET_PARTS)


def _value_may_be_logged(key: str) -> bool:
    """May the value be printed for this setting too?

    The list is **inverted** on purpose: it allows, it does not forbid.
    Forbidding means having to know in advance every secret that ever comes,
    and the one that gets forgotten is printed into the log in full. This way
    a new setting stays silent until somebody consciously says it may be shown.

    It is not only about passwords. `notify_smtp_recipient` is an e-mail address and
    `notify_smtp_user` an account name - neither belongs in a log that gets
    forwarded when something goes wrong.
    """
    if _is_secret(key):
        return False
    if key in DEFAULT_SETTINGS:
        return True
    # Notification keys come into being only at run time (notify_<channel>_<field>),
    # so they are recognised by the end of the name.
    return key.rsplit("_", 1)[-1] in SAFE_SUFFIXES


def _log_change(key: str, old: str, new: str) -> None:
    """A line in the log about a changed setting. Silent without a change."""
    if old == new or any(key.startswith(p) or key == p for p in UNLOGGED_SETTINGS):
        return
    if not _value_may_be_logged(key):
        log.info("setting %s changed", key)
        return
    log.info("setting %s: %s -> %s", key, old or "(empty)",
             new or "(empty)")


def set_setting(key: str, value: str) -> None:
    # The old value is read before the write - otherwise there is nothing to
    # compare with, and the log would report a change even where nothing changed.
    old = get_setting(key, "")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
    forget_settings()
    _log_change(key, str(old or ""), str(value or ""))


def seed_from_env() -> None:
    """The first filling of the Jellyfin connection from .env.

    The address and the key used to be read from .env at every start. Now
    they live in the database and are changed from the web - but whoever used
    the application already still has them only in .env. So they are copied
    over once at the first start.

    The condition "only when the value in the database is empty" matters:
    without it .env would overwrite at every start what the user set on the web.
    """
    import os

    for setting_key, env_key in (("jellyfin_url", "JELLYFIN_URL"),
                                 ("jellyfin_api_key", "JELLYFIN_API_KEY")):
        if get_setting(setting_key, "").strip():
            continue
        value = os.environ.get(env_key, "").strip().rstrip("/")
        if value and not value.startswith("sem-vloz"):
            set_setting(setting_key, value)


def jellyfin_connection() -> tuple[str, str]:
    """Jellyfin's address and API key - the one place to take them from."""
    return (
        get_setting("jellyfin_url", "").strip().rstrip("/"),
        get_setting("jellyfin_api_key", "").strip(),
    )


def get_int_setting(key: str, minimum: int, maximum: int, fallback: int) -> int:
    """A number from the settings, clamped to sensible limits.

    The user can write anything into the form. Never trust input - not even
    your own.
    """
    try:
        value = int(float(get_setting(key)))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, value))


# ---------------------------------------------------------------------------
# Small helpers, so the code does not repeat the same over and over
# ---------------------------------------------------------------------------

def query_all_in(sql: str, values: Any, before: tuple = (), batch: int = 400) -> list[dict[str, Any]]:
    """A query with a list of values in `IN (...)`: `sql` has one `{}` in place of the question marks.

    A long list is sent in batches - databases have a ceiling on the number
    of parameters of one query (older SQLite 999). `before` are the
    parameters that stand in the query before the list. An empty list = no
    query.

        db.query_all_in("SELECT id FROM items WHERE type = ? AND id IN ({})", ids, ("Movie",))
    """
    values = list(values)
    result: list[dict[str, Any]] = []
    for start in range(0, len(values), batch):
        chunk = values[start:start + batch]
        result += query_all(sql.format(", ".join("?" * len(chunk))), (*before, *chunk))
    return result


def query_all(sql: str, params: Any = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def query_iter(sql: str, params: Any = ()) -> Iterator[dict[str, Any]]:
    """Like query_all, only row by row: a big result is not held in memory twice (rows and dicts).

    For queries with hundreds of thousands of rows (the analysis of listening over the whole history). The
    connection stays open until the result is read to the end - so read it right away, do not keep the
    generator for later.
    """
    with connect() as conn:
        for row in conn.execute(sql, params):
            yield dict(row)


def query_one(sql: str, params: Any = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def query_value(sql: str, params: Any = (), default: Any = 0) -> Any:
    """The first column of the first row - typically for `SELECT COUNT(*) ...`."""
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return default

    # PostgreSQL returns a dict, SQLite a row that can be read by position too.
    # We take the first value regardless of what the column is called.
    value = next(iter(dict(row).values()))
    return default if value is None else value
