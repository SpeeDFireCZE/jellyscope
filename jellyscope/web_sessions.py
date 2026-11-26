"""Sign-in sessions kept on the server: signing out ends every copy of the cookie.

The session cookie is Starlette's - signed, so nobody forges one, but a copy anybody who saw it may keep (a
shared computer, a plain-http network, a backup of the browser's profile). The server used to keep no list of
sessions, so a copy made before signing out went on opening the account, and an account from Jellyfin, whose
fingerprint never changes, had no way to end it short of being deleted.

Now the cookie carries a random token and the table `web_sessions` a record of it - only its hash, so whoever
reads the database (or a backup) signs in with nothing. web.base.current_account accepts a session only while
its record exists and the fingerprint still fits (both must hold); signing out deletes the record, and so do
the checks that end a session (a changed password, an account disabled in Jellyfin, Jellyfin sign-in switched
off for viewers). A deleted account takes its records with it (ON DELETE CASCADE).

A record lives as long as the cookie did: LIFETIME_SECONDS since it was last used. Every request looks it up
once by the primary key and writes nothing - the time of the last use moves at most every TOUCH_SECONDS:
SQLite has a single writer, and an image or a style is a request too. Expired records are cleaned at start and
by the quick refresh (tasks._run_recent); one that nobody cleaned yet is refused all the same.

Those writes on the side (the touch, ending a session a check refused, the clean-up) run on the event loop and
may be skipped, so they wait for another writer (a VACUUM, a restore, a long import) only briefly and give up
quietly: the next request or refresh tries again, and a check that refused a session refuses its copies again.
Signing out does wait - it must not be skipped.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from . import db, dialect

# The key of the token in the session cookie.
COOKIE_KEY = "session_token"
# The cookie's life (web/__init__.py gives it to SessionMiddleware) and the record's: unused this long, over.
LIFETIME_SECONDS = 14 * 24 * 3600
# The time of the last use moves at most this often.
TOUCH_SECONDS = 300
# How long a write on the side waits for another writer's lock before it is skipped.
SIDE_WRITE_WAIT_MS = 200


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cutoff(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime(db.TIME_FORMAT)


def _lock_errors() -> tuple[type[Exception], ...]:
    """What the database answers when its lock could not be had in time: an operational error of its driver."""
    if db.current_dialect() == dialect.POSTGRES:
        import psycopg

        return (sqlite3.OperationalError, psycopg.OperationalError)
    return (sqlite3.OperationalError,)


def _write(sql: str, params: Any, best_effort: bool = False) -> int:
    """One write. Returns how many rows it changed.

    best_effort: a write on the side (see the module's docstring) - it waits for the lock SIDE_WRITE_WAIT_MS at
    most and is skipped (0) when it cannot have it.
    """
    try:
        with db.connect() as conn:
            if best_effort:
                conn.execute(dialect.short_lock_wait(conn.kind, SIDE_WRITE_WAIT_MS))
            cursor = conn.execute(sql, params)
            conn.commit()
    except _lock_errors():
        if not best_effort:
            raise
        return 0
    return int(cursor.rowcount or 0)


def create(account_id: int) -> str:
    """A record of a new sign-in. Returns the token for the cookie - the only place it is kept."""
    token = secrets.token_urlsafe(32)
    now = db.utcnow()
    with db.connect() as conn:
        conn.execute("INSERT INTO web_sessions (token_hash, account_id, created_at, last_used_at) VALUES (?, ?, ?, ?)",
                     (_hash(token), account_id, now, now))
        conn.commit()
    return token


def account_of(token: Any) -> dict[str, Any] | None:
    """The account whose live record the token is - one lookup by the primary key. None = no such session.

    The account's row comes in the same query, so a request asks the database once. An expired record is
    deleted here; a used one is touched only when TOUCH_SECONDS passed since the last time.
    """
    if not isinstance(token, str) or not token:
        return None
    token_hash = _hash(token)
    row = db.query_one("SELECT s.last_used_at AS session_last_used_at, a.* FROM web_sessions s"
                       " JOIN accounts a ON a.id = s.account_id WHERE s.token_hash = ?", (token_hash,))
    if row is None:
        return None
    last_used = str(row.pop("session_last_used_at") or "")
    if last_used < _cutoff(LIFETIME_SECONDS):
        end(token, best_effort=True)
        return None
    if last_used < _cutoff(TOUCH_SECONDS):
        _write("UPDATE web_sessions SET last_used_at = ? WHERE token_hash = ?", (db.utcnow(), token_hash),
               best_effort=True)
    return row


def end(token: Any, best_effort: bool = False) -> None:
    """Ends one session: every copy of its cookie stops opening the account.

    best_effort for a session a check refused (web.base.current_account), never for signing out.
    """
    if isinstance(token, str) and token:
        _write("DELETE FROM web_sessions WHERE token_hash = ?", (_hash(token),), best_effort)


def end_all(account_id: int, best_effort: bool = False) -> int:
    """Ends every session of the account. Returns how many there were (0 when a best-effort one was skipped)."""
    return _write("DELETE FROM web_sessions WHERE account_id = ?", (account_id,), best_effort)


# A statement of the sessions' table or its index in a schema file (reset): CREATE ... web_sessions ... ;
_SCHEMA_STATEMENT = re.compile(r"^CREATE [^;(]*\bweb_sessions\b[^;]*;", re.M)


def _schema_statements(kind: str) -> list[str]:
    """The table and its index as the dialect's schema file makes them - one source with init_db."""
    schema = db.SCHEMA_POSTGRES if kind == dialect.POSTGRES else db.SCHEMA_SQLITE
    return _SCHEMA_STATEMENT.findall(schema.read_text(encoding="utf-8"))


def reset() -> None:
    """After a restored backup everybody signs in again.

    The backup's records must not come back - a copy of a cookie signed out since the backup was made would open
    the account again - and a backup from before the sessions has no table for them: every request with a cookie
    and every sign-in would fail until the restart. So the table is made when it is missing, and emptied.
    """
    with db.connect() as conn:
        for statement in _schema_statements(conn.kind):
            conn.execute(statement)
        conn.execute("DELETE FROM web_sessions")
        conn.commit()


def clean_up() -> int:
    """Deletes the records unused for longer than their life - on the side, skipped under a lock. Returns how many."""
    return _write("DELETE FROM web_sessions WHERE last_used_at < ?", (_cutoff(LIFETIME_SECONDS),), best_effort=True)
