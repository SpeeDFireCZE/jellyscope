# -*- coding: utf-8 -*-
r"""Sign-in sessions kept on the server: signing out ends every copy of the cookie.

The session cookie is signed, so nobody forges one - but it is a copy anybody who saw it may keep (a shared
computer, a plain-http network, a backup of the browser's profile). The server used to keep no list of
sessions: a copy made before signing out went on opening the account, and an account from Jellyfin (its
fingerprint never changes) had no way to end it short of being deleted (audit SA-8, SS-9). Guarded here:

* signing out ends that session for every copy of its cookie, and only that session;
* the server keeps one record per sign-in with the token's hash, never the token;
* a cookie without a record (forged, from before this version, expired, signed out) opens nothing;
* a request writes at most every few minutes (SQLite has a single writer);
* the checks that end a session (a changed password, an account deleted or disabled in Jellyfin, Jellyfin
  sign-in switched off for viewers) end its record too;
* expired records are cleaned at start and by the quick refresh;
* a write on the side of a request (the touch, ending a refused session, the clean-up) never waits long for
  another writer and never fails the request: under a held write lock the request answers quickly.

Run:
    .venv/bin/python tests/test_web_sessions.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "web_sessions.db")
os.environ["SECRET_KEY"] = "test-signing-key-long-enough-for-sessions"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402

from _apps import login  # noqa: E402
from _package import substitute  # noqa: E402
from jellyscope import access, accounts, db, jellyfin, scanner, tasks, web, web_sessions  # noqa: E402
from jellyscope.config import load_config  # noqa: E402
from jellyscope.web import start as web_start  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


COOKIE = "jellyscope_session"
SIGNER = TimestampSigner(str(load_config().secret_key))

db.init_db()
admin_id = accounts.create("admin", "longpassword", is_admin=True)
reader_id = accounts.create("reader", "readerpass", is_admin=False)


def session_of(client: TestClient) -> dict[str, Any]:
    raw = client.cookies.get(COOKIE) or ""
    return json.loads(base64.b64decode(SIGNER.unsign(raw.encode("utf-8")))) if raw else {}


def copy_of(client: TestClient) -> TestClient:
    """Another browser holding the same cookie (copied before anything happened to the session)."""
    other = TestClient(web.app)
    other.cookies.set(COOKIE, client.cookies.get(COOKIE) or "")
    return other


def forged(data: dict[str, Any]) -> TestClient:
    """A cookie signed the way Starlette signs it - with whatever is wanted in it."""
    other = TestClient(web.app)
    other.cookies.set(COOKIE, SIGNER.sign(base64.b64encode(json.dumps(data).encode("utf-8"))).decode("utf-8"))
    return other


def opens(client: TestClient, path: str = "/account") -> bool:
    return client.get(path, follow_redirects=False).status_code == 200


def records(account_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in db.query_all("SELECT * FROM web_sessions WHERE account_id = ?", (account_id,))]


def ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime(db.TIME_FORMAT)


print("--- signing out ends every copy of the cookie (audit SA-8, SS-9) ---")
admin = login("admin")
copy = copy_of(admin)
check(opens(admin, "/settings") and opens(copy, "/settings"), "signed in, a copy of the cookie opens the account too")
other_browser = login("admin")
admin.post("/logout", follow_redirects=False)
check(not opens(admin), "the browser that signed out is out")
check(not opens(copy, "/settings"), "a copy of its cookie made before signing out no longer opens the account")
check(opens(other_browser, "/settings"), "another sign-in of the same account stays")

print("--- the server keeps a record of each sign-in, the token only as a hash ---")
check(len(records(admin_id)) == 1, f"one record for the one session still signed in ({len(records(admin_id))})")
token = str(session_of(other_browser).get("session_token") or "")
stored = json.dumps(records(admin_id))
check(len(token) >= 32 and token not in stored, "the cookie carries a long random token, the database never holds it")
row = records(admin_id)[0] if records(admin_id) else {}
check(bool(row.get("created_at")) and bool(row.get("last_used_at")), f"the record knows when it was made and last used ({row})")

print("--- a cookie without its record opens nothing ---")
fingerprint = accounts.session_fingerprint(accounts.get(admin_id) or {})
check(not opens(forged({"account_id": admin_id, "fingerprint": fingerprint}), "/settings"),
      "a signed cookie with the right fingerprint but no token (a cookie from before this version) signs in once more")
check(not opens(forged({"account_id": admin_id, "fingerprint": fingerprint, "session_token": "made-up-" * 6}), "/settings"),
      "a made-up token opens nothing")
check(not opens(forged({"account_id": reader_id, "fingerprint": accounts.session_fingerprint(accounts.get(reader_id) or {}),
                        "session_token": token}), "/account"),
      "another account's token does not open this account")

print("--- a record ends after the cookie's life unused ---")
stale = login("reader", "readerpass")
with db.connect() as conn:
    conn.execute("UPDATE web_sessions SET last_used_at = ? WHERE account_id = ?", (ago(15 * 24 * 3600), reader_id))
    conn.commit()
check(not opens(stale), "a session unused for fifteen days is over")
check(not records(reader_id), "and its record is gone")

print("--- a request writes at most every few minutes ---")
fresh = login("reader", "readerpass")
minute_ago = ago(60)
with db.connect() as conn:
    conn.execute("UPDATE web_sessions SET last_used_at = ? WHERE account_id = ?", (minute_ago, reader_id))
    conn.commit()
opens(fresh)
check([r["last_used_at"] for r in records(reader_id)] == [minute_ago], "a request a minute after the last one writes nothing")
long_ago = ago(20 * 60)
with db.connect() as conn:
    conn.execute("UPDATE web_sessions SET last_used_at = ? WHERE account_id = ?", (long_ago, reader_id))
    conn.commit()
opens(fresh)
check([r["last_used_at"] > long_ago for r in records(reader_id)] == [True], "after twenty minutes the time of the last use moves")
fresh.post("/logout", follow_redirects=False)

print("--- the checks that end a session end its record too ---")
first, second = login("reader", "readerpass"), login("reader", "readerpass")
first.post("/account/password", data={"current_password": "readerpass", "password": "brandnewpass1",
                                      "password_again": "brandnewpass1"})
check(opens(first) and not opens(second), "the password changed in one session: the other one ends")
check(len(records(reader_id)) == 1, f"and only the record of the session that changed it is left ({len(records(reader_id))})")
first.post("/logout", follow_redirects=False)

doomed_id = accounts.create("doomed", "doomedpass", is_admin=False)
doomed = login("doomed", "doomedpass")
check(len(records(doomed_id)) == 1, "a reader about to be deleted is signed in")
accounts.delete(doomed_id)
check(not records(doomed_id) and not opens(doomed), "a deleted account takes its records with it")

JELLYFIN_ID = "0123456789abcdef0123456789abcdef"


async def jellyfin_answer(self: Any, username: str, password: str) -> dict[str, Any] | None:
    if username == "mirek" and password == "mirekpass":
        return {"id": JELLYFIN_ID, "name": "mirek", "is_admin": False, "is_disabled": False}
    return None


db.set_setting("jellyfin_url", "http://jellyfin.test")
db.set_setting("jellyfin_api_key", "sessions-test-key")
db.set_setting(access.LOGIN_KEY, "1")
db.forget_settings()
original_verify_login = jellyfin.JellyfinClient.verify_login
jellyfin.JellyfinClient.verify_login = jellyfin_answer  # type: ignore[method-assign]
try:
    def viewer_sign_in() -> TestClient:
        client = TestClient(web.app)
        client.post("/login", data={"username": "mirek", "password": "mirekpass", "method": "jellyfin"})
        return client

    viewer = viewer_sign_in()
    viewer_copy = copy_of(viewer)
    viewer_id = int((accounts.by_jellyfin_id(JELLYFIN_ID) or {}).get("id") or 0)
    check(opens(viewer) and opens(viewer_copy), "a viewer from Jellyfin is signed in")
    viewer.post("/logout", follow_redirects=False)
    check(not opens(viewer_copy), "a viewer from Jellyfin signs out for every copy too (the fingerprint never changes)")

    one, two = viewer_sign_in(), viewer_sign_in()
    with db.connect() as conn:
        conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES (?, 'mirek', 0, 1)", (JELLYFIN_ID,))
        conn.commit()
    check(not opens(one), "disabled in Jellyfin: the session ends")
    check(not records(viewer_id), "and every record of the account with it, not only the one that asked")
    with db.connect() as conn:
        conn.execute("UPDATE users SET is_disabled = 0 WHERE id = ?", (JELLYFIN_ID,))
        conn.commit()
    check(not opens(two), "enabled again: the other browser has to sign in again")

    three = viewer_sign_in()
    db.set_setting(access.LOGIN_KEY, "0")
    db.forget_settings()
    check(not opens(three) and not records(viewer_id), "Jellyfin sign-in switched off: the viewer's records end")
finally:
    jellyfin.JellyfinClient.verify_login = original_verify_login  # type: ignore[method-assign]
    db.set_setting(access.LOGIN_KEY, "0")
    db.forget_settings()

print("--- a write on the side never holds a request behind another writer ---")
# The lock is SQLite's (one writer for the whole file). On PostgreSQL the test runs through a copy without the
# file (.superpowers/tools/test_on_pg.py), and these checks have nothing to hold there.
SQLITE_FILE = os.environ.get("DATABASE_PATH", "")


@contextmanager
def write_lock():
    """Another connection holds the database's write lock (a VACUUM, a restore, a long import)."""
    holder = sqlite3.connect(SQLITE_FILE, timeout=1.0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def timed(client: TestClient, path: str = "/account") -> tuple[int, float]:
    started = time.monotonic()
    try:
        status = client.get(path, follow_redirects=False).status_code
    except Exception:  # noqa: BLE001 - the test client raises what the server answers with 500
        status = 500
    return status, time.monotonic() - started


def token_of(client: TestClient) -> str:
    return str(session_of(client).get(web_sessions.COOKIE_KEY) or "")


if SQLITE_FILE:
    touched = login("reader", "brandnewpass1")
    ten_minutes_ago = ago(10 * 60)
    with db.connect() as conn:
        conn.execute("UPDATE web_sessions SET last_used_at = ? WHERE account_id = ?", (ten_minutes_ago, reader_id))
        conn.commit()
    with write_lock():
        status, took = timed(touched)
    check(status == 200 and took < 3, f"a touch due under another writer's lock: the request answers 200 at once ({status}, {took:.1f} s)")
    check([r["last_used_at"] for r in records(reader_id)] == [ten_minutes_ago], "the touch is skipped, nothing is half-written")
    opens(touched)
    check([r["last_used_at"] > ten_minutes_ago for r in records(reader_id)] == [True], "the next request touches it")
    touched.post("/logout", follow_redirects=False)

    refused = forged({"account_id": admin_id, "fingerprint": "an-old-password", "session_token": web_sessions.create(admin_id)})
    with write_lock():
        status, took = timed(refused)
    check(status == 303 and took < 3, f"a session a changed password ends, under the lock: refused at once, no error ({status}, {took:.1f} s)")

    expired_token = web_sessions.create(admin_id)
    with db.connect() as conn:
        conn.execute("UPDATE web_sessions SET last_used_at = ? WHERE token_hash = ?",
                     (ago(15 * 24 * 3600), web_sessions._hash(expired_token)))
        conn.commit()
    expired = forged({"account_id": admin_id, "fingerprint": fingerprint, "session_token": expired_token})
    with write_lock():
        status, took = timed(expired)
    check(status == 303 and took < 3, f"an expired session under the lock: refused at once, no error ({status}, {took:.1f} s)")

    shut_out = login("admin")
    restore = substitute(access, "viewer_shut_out", lambda account: True)
    try:
        with write_lock():
            status, took = timed(shut_out)
    finally:
        restore()
    check(status == 303 and took < 3, f"every session of a shut-out account ends, under the lock: refused at once ({status}, {took:.1f} s)")
    check(not opens(shut_out), "the refused browser is signed out all the same, its records go at the next check")
else:
    print("(skipped: another writer's lock is a matter of SQLite)")

print("--- expired records are cleaned at start and by the quick refresh ---")


def plant(token_hash: str, last_used: str) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO web_sessions (token_hash, account_id, created_at, last_used_at) VALUES (?, ?, ?, ?)",
                     (token_hash, admin_id, last_used, last_used))
        conn.commit()


def hashes() -> set[str]:
    return {r["token_hash"] for r in records(admin_id)}


plant("expired-at-start", ago(15 * 24 * 3600))
plant("alive-at-start", ago(3600))


async def start_up() -> None:
    async with web_start.lifespan(web.app):
        pass


asyncio.run(start_up())
check("expired-at-start" not in hashes() and "alive-at-start" in hashes(), f"the start cleans the expired ones only ({sorted(hashes())})")


async def no_refresh() -> dict[str, Any]:
    return {"status": "ok"}


plant("expired-by-task", ago(15 * 24 * 3600))
restore = substitute(scanner, "sync_recent", no_refresh)
try:
    asyncio.run(tasks.TASKS["recent"].runner())
finally:
    restore()
check("expired-by-task" not in hashes() and "alive-at-start" in hashes(), f"so does the quick refresh ({sorted(hashes())})")

plant("expired-under-lock", ago(15 * 24 * 3600))
restore = substitute(scanner, "sync_recent", no_refresh)
try:
    with write_lock():
        started = time.monotonic()
        try:
            outcome: Any = asyncio.run(tasks.TASKS["recent"].runner())
        except Exception as exc:  # noqa: BLE001 - what the test looks at
            outcome = exc
        took = time.monotonic() - started
finally:
    restore()
check(outcome == {"status": "ok"} and took < 3,
      f"the quick refresh under another writer's lock: the clean-up is skipped, the refresh runs ({outcome!r}, {took:.1f} s)")


print("--- a restored backup brings no session back, on either database ---")
# tasks.restore_backup empties the table after a restore, and makes it when the backup is from before it - by the
# statements of the dialect's own schema file (PostgreSQL cannot be run here, its statements are looked at).
from jellyscope import dialect  # noqa: E402

for kind, column in ((dialect.SQLITE, "INTEGER"), (dialect.POSTGRES, "BIGINT")):
    statements = web_sessions._schema_statements(kind)
    check(len(statements) == 2 and statements[0].startswith("CREATE TABLE IF NOT EXISTS web_sessions (")
          and f"account_id    {column} NOT NULL REFERENCES accounts(id) ON DELETE CASCADE" in statements[0]
          and statements[1] == "CREATE INDEX IF NOT EXISTS idx_web_sessions_account ON web_sessions (account_id);",
          f"{kind}: the table and its index as its schema makes them ({[s[:60] for s in statements]})")
check(dialect.translate(web_sessions._schema_statements(dialect.POSTGRES)[0], dialect.POSTGRES)
      == web_sessions._schema_statements(dialect.POSTGRES)[0], "the translation for PostgreSQL leaves them as they are")
web_sessions.reset()
check(not db.query_all("SELECT token_hash FROM web_sessions"), "reset() empties the table")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
