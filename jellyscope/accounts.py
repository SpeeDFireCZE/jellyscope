"""Accounts for Jellyscope - signing in and managing users.

Mind a difference that is easy to overlook:

  * **users**    = Jellyfin's users. We only read them, to know who watched
                   what. Nobody signs in anywhere with them.
  * **accounts** = accounts for this application. Those are ours to look after.

They are two independent things and are kept apart on purpose: when you give
somebody access to Jellyscope, you do not give them access to Jellyfin by it,
and the other way round.

## How a password is stored

Never, under any circumstances, is a password stored as the user wrote it.
Were somebody to get to the database, they would have all the passwords at
once - and since people use the same password in several places, they would
get to their e-mails too.

Instead of the password its **hash** is stored: the result of a computation
that can be done in one direction only. A hash is easy to compute from a
password, a password cannot be found from a hash. When signing in, the hash of
the entered password is computed and compared with the stored one.

We use PBKDF2 - it is right in Python, it needs no extra library, and it has
two important properties:

  * **a salt** - a random string stored next to the hash. Thanks to it two
    users with the same password have different hashes, so the hash does not
    reveal that they have the same password.
  * **the number of iterations** - the computation is repeated hundreds of
    thousands of times, so trying passwords one after another takes an
    attacker unbearably long.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import time
from datetime import datetime, timedelta, UTC
from typing import Any

from . import db
from .i18n import TranslatableError
from .jellyfin import id_without_dashes

log = logging.getLogger(__name__)

# The more iterations, the slower trying passwords is - but also the slower
# signing in. 600 000 is OWASP's recommendation for PBKDF2 with SHA-256.
ITERATIONS = 600_000
ALGORITHM = "pbkdf2_sha256"

MIN_PASSWORD_LENGTH = 8
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")


class AccountError(TranslatableError, ValueError):
    """An error that has to be shown to a person - and so translated too (see i18n.TranslatableError).

    It keeps the template ("The password must be at least {n} characters.") and the values apart, not a
    finished sentence: only the template can be translated. A ValueError as well - callers catch that.
    """


# ---------------------------------------------------------------------------
# A brake on guessing passwords
# ---------------------------------------------------------------------------
#
# The hashing itself is slow on purpose already (600 000 iterations take
# about a quarter of a second), so passwords cannot be tried by the thousand
# a second. A dictionary attack overnight would still work out, though - and
# every attempt costs a quarter of a second of the processor, so the
# application can be flooded this way too.
#
# The brake has two floors:
#
#   1. **Counting attempts** is kept in the process's memory. It is a record
#      of the last hour, and writing every wrong password into the database
#      would be exactly what an attacker wants.
#   2. **A block** does belong in the database: it has to survive a restart,
#      otherwise waiting for an update would do. And above all it escalates -
#      whoever comes for the eighth time is obviously not trying their own
#      password.
#
# The steps are short at the start on purpose and hard at the end. Whoever
# mistypes their password waits a minute and swears; whoever guesses hits a
# wall after a quarter of an hour.
ATTEMPTS_BEFORE_BLOCK = 8
BLOCK_STEPS = (60, 120, 300, 900)      # 1 min, 2 min, 5 min, 15 min
# After four blocks it is permanent - an admin has to unblock it in Settings.

# Eight attempts a minute catch a burst, not a patient script: seven a minute
# are ten thousand guesses a day. So the last hour counts too - twenty attempts
# in it are a block as well, the same steps.
LONG_WINDOW_SECONDS = 3600
ATTEMPTS_IN_LONG_WINDOW = 20
# The addresses kept in memory. Above it the one quiet for the longest is
# forgotten, so memory cannot be filled by changing addresses - and a burst
# from thousands of them does not wipe the count of the others.
MAX_COUNTED_ADDRESSES = 5000

# When an address behaves for a long time, it starts again from the first step.
# Without it, a person who mistypes their password once every half a year
# would get blocked permanently after a few years.
FORGET_AFTER_HOURS = 24

# The attempts of an address: (when, a mark of the door and name aimed at). None = no name - a code (Quick Connect).
_attempts: dict[str, list[tuple[float, str | None]]] = {}


def _target(name: str | None) -> str | None:
    """A short mark of the name an attempt aimed at ("local:petr"), never the name itself.

    The name comes straight from the sign-in form and may be as long as the form lets it be (a megabyte):
    kept whole, a few addresses with a few attempts each filled the memory without ever meeting a block.
    """
    if name is None:
        return None
    return hashlib.sha256(name.strip().lower().encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _brake_key(ip: str) -> str:
    """The address the brake counts: an IPv6 one as its /64 network, an IPv4-mapped one as the IPv4 address.

    One host gets a whole /64 and can take a new address of it for every few guesses - counted one by
    one, it never met a block. Anything that is no address (a network from the list in Settings) stays.
    """
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _block_row(ip: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM login_blocks WHERE ip = ?", (ip,))


def block_remaining(ip: str) -> int:
    """How many seconds the address still has to wait. 0 = it may try, -1 = permanently."""
    row = _block_row(_brake_key(ip))
    if row is None:
        return 0
    if row["permanent"]:
        return -1
    if not row["blocked_until"]:
        return 0

    remaining = _seconds_until(str(row["blocked_until"]))
    return max(0, remaining)


def _seconds_until(utc_time: str) -> int:
    """How many seconds are left until the given time in UTC."""
    try:
        end = datetime.strptime(utc_time[:19].replace("T", " "), db.TIME_FORMAT)
    except (TypeError, ValueError):
        return 0
    return int((end - datetime.now(UTC).replace(tzinfo=None)).total_seconds())


def _recent_attempts(ip: str) -> list[tuple[float, str | None]]:
    now = time.monotonic()
    return [a for a in _attempts.get(ip, []) if now - a[0] < LONG_WINDOW_SECONDS]


def _used_up(attempts: list[tuple[float, str | None]]) -> bool:
    """Has the address no attempt left - eight in the last minute, or twenty in the last hour?"""
    now = time.monotonic()
    in_minute = sum(1 for a in attempts if now - a[0] < BLOCK_STEPS[0])
    return in_minute >= ATTEMPTS_BEFORE_BLOCK or len(attempts) >= ATTEMPTS_IN_LONG_WINDOW


def begin_attempt(ip: str, name: str | None = None) -> dict[str, Any] | None:
    """Counts an attempt aimed at `name` (None = a code) before its password is checked.

    Called with nothing awaited since the check of the block (block_remaining): counted only
    after the answer, requests sent at once all passed the check while the first ones waited
    for Jellyfin or the hash, and every one of them got its guess. The attempt counts as
    failed until a success forgets it (forget_failures); attempt_failed() after a wrong
    password makes the block at the last allowed one, as before.

    The attempts of all names count together - the name only tells which of them a successful
    sign-in forgets. Returns a block when the address has used up its attempts already
    (checked or still waiting for their answer) - the password is then not checked at all.
    """
    ip = _brake_key(ip)
    attempts = _recent_attempts(ip)
    _attempts.pop(ip, None)
    if _used_up(attempts):
        return _block(ip)
    attempts.append((time.monotonic(), _target(name)))
    _attempts[ip] = attempts       # at the end again: the most recent address

    while len(_attempts) > MAX_COUNTED_ADDRESSES:
        del _attempts[next(iter(_attempts))]
    return None


def attempt_failed(ip: str) -> dict[str, Any] | None:
    """The attempt begun by begin_attempt failed. Returns the block when the address has just used up its attempts."""
    ip = _brake_key(ip)
    if not _used_up(_recent_attempts(ip)):
        return None
    _attempts.pop(ip, None)
    return _block(ip)


def count_failure(ip: str, name: str | None = None) -> dict[str, Any] | None:
    """Records a failed attempt (the start of a code). When there are too many already, creates a block.

    Returns a description of the block when it has just come into being - None otherwise.
    """
    return begin_attempt(ip, name) or attempt_failed(ip)


def _block(ip: str) -> dict[str, Any]:
    """Creates or tightens the block of an address. A block that runs already is returned as it is."""
    row = _block_row(ip)
    if row and (row["permanent"] or _seconds_until(str(row["blocked_until"] or "")) > 0):
        # Failures counted during a block (a burst that passed the check at once) do not climb a level each.
        return {"ip": ip, "level": int(row["level"]), "permanent": bool(row["permanent"]),
                "seconds": 0 if row["permanent"] else _seconds_until(str(row["blocked_until"]))}
    level = int(row["level"]) if row else 0
    failures = int(row["failures"] or 0) if row else 0

    # Quiet for a long time? We start from the beginning again.
    if row and row["last_failure"]:
        since_last = -_seconds_until(str(row["last_failure"]))
        if since_last > FORGET_AFTER_HOURS * 3600:
            level = 0

    level += 1
    permanent = level > len(BLOCK_STEPS)
    seconds = 0 if permanent else BLOCK_STEPS[level - 1]
    until = None if permanent else _in_seconds(seconds)

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO login_blocks (ip, level, blocked_until, permanent,
                                      failures, last_failure)
                 VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (ip) DO UPDATE SET
                level         = excluded.level,
                blocked_until = excluded.blocked_until,
                permanent     = excluded.permanent,
                failures      = excluded.failures,
                last_failure  = excluded.last_failure
            """,
            (ip, level, until, 1 if permanent else 0,
             failures + ATTEMPTS_BEFORE_BLOCK, db.utcnow()),
        )
    return {"ip": ip, "level": level, "seconds": seconds, "permanent": permanent}


def _in_seconds(seconds: int) -> str:
    return (datetime.now(UTC).replace(tzinfo=None)
            + timedelta(seconds=seconds)).strftime(db.TIME_FORMAT)


def forget_failures(ip: str, name: str | None = None) -> None:
    """After a successful sign-in the failures aimed at that name (None = a code) are forgotten.

    Only those: whoever knows their own password is not the owner of the other
    accounts. When a success forgot every failure of the address, anybody with an
    account guessed the administrator's password seven times, signed in as
    themselves and went on - a block never came. For the same reason the record in
    the database, the block's level, stays; it is forgotten after FORGET_AFTER_HOURS
    of quiet (see _block and clean_up_blocks).
    """
    ip, target = _brake_key(ip), _target(name)
    left = [a for a in _attempts.pop(ip, []) if a[1] != target]
    if left:
        _attempts[ip] = left


def list_blocks() -> list[dict[str, Any]]:
    """Blocks for the listing in Settings - the freshest first."""
    rows = db.query_all(
        "SELECT * FROM login_blocks ORDER BY last_failure DESC, ip")
    for row in rows:
        row["permanent"] = bool(row["permanent"])
        row["seconds_left"] = (0 if row["permanent"]
                               else max(0, _seconds_until(str(row["blocked_until"] or ""))))
        row["active"] = row["permanent"] or row["seconds_left"] > 0
    return rows


def unblock(ip: str) -> bool:
    """Removes the block of an address, including the counter of steps."""
    ip = _brake_key(ip)
    with db.connect() as conn:
        cursor = conn.execute("DELETE FROM login_blocks WHERE ip = ?", (ip,))
    _attempts.pop(ip, None)
    removed = bool(cursor.rowcount)
    if removed:
        log.info("block lifted for address %s", ip)
    return removed


def convert_block_keys() -> int:
    """Moves blocks stored under a whole address under the key the brake counts today. Returns how many rows moved.

    Since 24. 11. 2026 an IPv6 address counts as its /64 and an IPv4-mapped one as IPv4 (_brake_key). A row an
    older version stored under the whole address was found by nothing: a permanent block silently stopped
    holding and could not be lifted. Rows that meet under one key join: the higher level, permanent when
    any was, the later times. Run at every start; with nothing old it changes nothing.
    """
    moved = 0
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM login_blocks").fetchall()]
        by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = _brake_key(str(row["ip"]))
            moved += key != row["ip"]
            kept = by_key.get(key)
            if kept is None:
                by_key[key] = {**row, "ip": key}
                continue
            kept["level"] = max(int(kept["level"] or 0), int(row["level"] or 0))
            kept["permanent"] = 1 if kept["permanent"] or row["permanent"] else 0
            for column in ("blocked_until", "last_failure"):
                kept[column] = max(str(kept[column] or ""), str(row[column] or "")) or None
        if moved:
            conn.execute("DELETE FROM login_blocks")
            for row in by_key.values():
                columns = list(row)
                conn.execute(f"INSERT INTO login_blocks ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                             tuple(row[c] for c in columns))
            conn.commit()
    return moved


def block_permanently(ip: str) -> None:
    """A manual permanent block from the list in Settings."""
    ip = _brake_key(ip)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO login_blocks (ip, level, blocked_until, permanent,
                                      failures, last_failure)
                 VALUES (?, ?, NULL, 1, 0, ?)
            ON CONFLICT (ip) DO UPDATE SET
                permanent = 1, blocked_until = NULL,
                level = login_blocks.level + 1
            """,
            (ip, len(BLOCK_STEPS) + 1, db.utcnow()),
        )
    log.info("address %s blocked permanently", ip)


def clean_up_blocks() -> int:
    """Deletes records that expired long ago. Called at start."""
    cutoff = (datetime.now(UTC).replace(tzinfo=None)
              - timedelta(hours=FORGET_AFTER_HOURS)).strftime(db.TIME_FORMAT)
    with db.connect() as conn:
        cursor = conn.execute(
            "DELETE FROM login_blocks WHERE permanent = 0 AND last_failure < ?",
            (cutoff,),
        )
    return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Makes the hash of a password in the shape  algorithm$iterations$salt$hash."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Checks a password against the stored hash."""
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False

    # compare_digest compares in constant time. A plain "==" stops at the first
    # differing byte, and the hash could be guessed step by step from the time of the answer.
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------------------
# Checks of the input
# ---------------------------------------------------------------------------

def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_PATTERN.match(username):
        raise AccountError(
            "The name must be 3 to 32 characters and may contain only "
            "unaccented letters, digits, a dot, a hyphen and an underscore."
        )
    return username


def validate_password(password: str, again: str | None = None) -> str:
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError("The password must be at least {n} characters.",
                           n=MIN_PASSWORD_LENGTH)
    if again is not None and password != again:
        raise AccountError("The passwords do not match.")
    return password


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM accounts"))


def any_exists() -> bool:
    """Is there at least one account yet? When not, the application offers the first setup."""
    return count() > 0


def has_local_password(username: str) -> bool:
    """Does an account of this name have a password here? Then signing in checks it here only (web/login.py).

    An account from Jellyfin has none (from_jellyfin stores no password), so its name goes to Jellyfin.
    """
    account = get_by_name(username or "")
    return bool(account and account.get("password_hash"))


def get_by_name(username: str) -> dict[str, Any] | None:
    # Comparing through LOWER() makes "Petr" and "petr" the same account - and
    # works the same in SQLite and PostgreSQL. Relying on COLLATE NOCASE would
    # work in SQLite only.
    return db.query_one(
        "SELECT * FROM accounts WHERE LOWER(username) = LOWER(?)",
        (username.strip(),),
    )


def get(account_id: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))


def all_accounts() -> list[dict[str, Any]]:
    # `jellyfin_user_id` and `permissions` are here because of Settings: the first
    # tells a viewer from Jellyfin, the second own permissions; `may_have_key` is
    # the exception for an own API key (the Permissions window).
    return [dict(r) for r in db.query_all(
        "SELECT id, username, is_admin, created_at, last_login,"
        " jellyfin_user_id, permissions, may_have_key"
        " FROM accounts ORDER BY is_admin DESC, LOWER(username)"
    )]


def save_permissions(account_id: int, permissions: str | None) -> None:
    """The account's own permissions; None = back to the group."""
    account = get(account_id)
    if account is None:
        raise AccountError("The account does not exist.")
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET permissions = ? WHERE id = ?",
                     (permissions, account_id))
        conn.commit()
    log.info("account %s: rights %s", account["username"],
             "own" if permissions else "by group")


def admin_count() -> int:
    return int(db.query_value("SELECT COUNT(*) FROM accounts WHERE is_admin = 1"))


def local_admin_count() -> int:
    """Administrators that are accounts of Jellyscope, not from Jellyfin.

    One of them must always stay (the user, 22. 11. 2026): an account from Jellyfin depends on Jellyfin - when it
    is down, or the person is disabled or demoted there, only a local account gets into Settings. So the last
    local administrator can be neither deleted nor demoted (local accounts have no "disabled"). An older
    installation may have none left; Settings → Accounts warns then, and the last administrator from Jellyfin
    keeps the old safeguards (not removed, demoted or shut out by Jellyfin).
    """
    return int(db.query_value("SELECT COUNT(*) FROM accounts WHERE is_admin = 1"
                              " AND (jellyfin_user_id IS NULL OR jellyfin_user_id = '')"))


def is_last_local_admin(account: dict[str, Any]) -> bool:
    return bool(account.get("is_admin")) and not account.get("jellyfin_user_id") and local_admin_count() <= 1


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------

def create(username: str, password: str, again: str | None = None, is_admin: bool = False) -> int:
    username = validate_username(username)
    password = validate_password(password, again)

    if get_by_name(username) is not None:
        raise AccountError("An account named '{user}' already exists.", user=username)

    with db.connect() as conn:
        account_id = conn.insert_returning_id(
            "INSERT INTO accounts (username, password_hash, is_admin, created_at)"
            " VALUES (?,?,?,?)",
            (username, hash_password(password), 1 if is_admin else 0, db.utcnow()),
        )
    log.info("account %s created (%s)", username,
             "admin" if is_admin else "reader")
    return account_id


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Returns the account when both the name and the password fit. None otherwise."""
    account = get_by_name(username or "")
    if account is None or not account["password_hash"]:
        # Even when the account does not exist - or comes from Jellyfin and has
        # no password here - the hash is computed for nothing. Without it the
        # answer would come noticeably faster, and it would tell which names
        # exist on the server.
        hash_password(password or "")
        return None

    if not verify_password(password or "", account["password_hash"]):
        return None

    with db.connect() as conn:
        conn.execute("UPDATE accounts SET last_login = ? WHERE id = ?",
                     (db.utcnow(), account["id"]))
    return account


# Set once the local passwords of accounts from Jellyfin were cleared (clear_jellyfin_passwords).
JELLYFIN_PASSWORDS_KEY = "conversion_jellyfin_passwords"


def clear_jellyfin_passwords() -> int:
    """Clears the local passwords an older version let an administrator give to accounts from Jellyfin, once.

    Such an account is opened through Jellyfin only (from_jellyfin stores no password). Until 24. 11. 2026
    "change another account's password" took these accounts too; that password then opened the account at the
    local door for ever - with signing in through Jellyfin switched off, after the password changed in
    Jellyfin - and it can no longer be changed or removed (set_password refuses). Once per database: after
    it the mark is set and nothing is touched again. Returns how many accounts lost such a password.
    """
    if db.get_setting(JELLYFIN_PASSWORDS_KEY, "") == "1":
        return 0
    with db.connect() as conn:
        cursor = conn.execute("UPDATE accounts SET password_hash = '' WHERE COALESCE(jellyfin_user_id, '') <> ''"
                              " AND COALESCE(password_hash, '') <> ''")
        conn.commit()
    db.set_setting(JELLYFIN_PASSWORDS_KEY, "1")
    return int(cursor.rowcount or 0)


def by_jellyfin_id(jellyfin_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM accounts WHERE jellyfin_user_id = ?",
                        (str(jellyfin_id or ""),)) if jellyfin_id else None


def from_jellyfin(details: dict[str, Any]) -> dict[str, Any]:
    """The account of a viewer who signed in with their Jellyfin password.

    When they have none yet, one is created; when they have one, it is evened
    up with what Jellyfin has just said - the name and the permissions. The
    permissions are taken over at **every** sign-in on purpose: whoever has them
    taken away in Jellyfin should not have them here either, and right away,
    not once somebody notices.

    No password is stored here. Such an account can be opened only through
    Jellyfin - the stored `password_hash` is empty, and authenticate() never accepts one.
    """
    jellyfin_id = str(details["id"])
    account = by_jellyfin_id(jellyfin_id)
    now = db.utcnow()
    if "image" in details:
        refresh_image(jellyfin_id, details["image"])

    if account is None:
        # The name may belong to a local account already. We must not
        # overwrite it, so the viewer gets a name with a distinction - they
        # sign in by what they have in Jellyfin anyway.
        name = _free_name(details["name"])
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO accounts (username, password_hash, is_admin,"
                " created_at, last_login, jellyfin_user_id)"
                " VALUES (?,?,?,?,?,?)",
                (name, "", 1 if details["is_admin"] else 0, now, now,
                 jellyfin_id))
            conn.commit()
        log.info("account created from Jellyfin: %s (%s)", name,
                 "admin" if details["is_admin"] else "viewer")
        return by_jellyfin_id(jellyfin_id) or {}

    keeps_rights = bool(account["is_admin"]) and not details["is_admin"] and admin_count() <= 1
    if keeps_rights:
        # No local administrator is left (an older installation): the last one is not demoted by a sign-in.
        log.warning("account %s is no administrator in Jellyfin any more, but it is the last "
                    "administrator here, so it keeps the rights", account["username"])
    with db.connect() as conn:
        conn.execute(
            "UPDATE accounts SET is_admin = ?, last_login = ? WHERE id = ?",
            (1 if details["is_admin"] or keeps_rights else 0, now, account["id"]))
        conn.commit()
    if bool(account["is_admin"]) != bool(details["is_admin"]) and not keeps_rights:
        log.info("account %s: rights taken from Jellyfin (%s)", account["username"],
                 "admin" if details["is_admin"] else "viewer")
    return db.query_one("SELECT * FROM accounts WHERE id = ?",
                        (account["id"],)) or {}


def refresh_image(jellyfin_id: str, tag: str) -> bool:
    """Writes the fingerprint of a viewer's profile picture when it changed. True = changed.

    Called at every sign-in through Jellyfin: the fingerprint comes with the
    identity, so a new picture shows right away, not only after the night's
    sync. The picture itself is not downloaded - the address in the My account
    button carries the fingerprint, so the browser fetches a new one by itself
    and keeps an unchanged one in its cache.

    A viewer Jellyscope does not know yet is not created: Jellyfin sends the id
    once with dashes, once without, and a double would come into being. The
    sync adds them.

    A database error (locked, a restored backup without the column) is only
    written to the log: signing in must not fail because of a picture.
    """
    bare = bare_id(str(jellyfin_id or ""))
    if not bare:
        return False
    try:
        with db.connect() as conn:
            changed = conn.execute(
                "UPDATE users SET image_tag = ? WHERE REPLACE(LOWER(id), '-', '') = ?"
                " AND COALESCE(image_tag, '') <> ?",
                (tag or None, bare, tag or "")).rowcount
            conn.commit()
    except Exception:  # noqa: BLE001 - the picture is cosmetics, signing in does not fail because of it
        log.warning("profile picture tag cannot be written", exc_info=True)
        return False
    return bool(changed)


def remove_without_jellyfin(live_ids: set[str] | list[str]) -> list[str]:
    """Deletes the sign-in accounts of viewers Jellyfin no longer knows.

    An account from Jellyfin comes into being by itself at the first sign-in -
    and it should disappear by itself the same way when an admin removes that
    person in Jellyfin. Otherwise a row that leads nowhere stays in Settings →
    Accounts, and worse: were that person to come back to Jellyfin under the
    same id, they would inherit the old account together with permissions
    nobody looked at meanwhile.

    **Only the account** is deleted, that is only the sign-in. The row in
    `users`, the history and the statistics stay: who watched what does not
    change by removing an account, and the bin and forgetting are for that,
    not this.

    Two safeguards:

    * An empty list from Jellyfin is **not taken as "nobody"**. An answer
      without a single user is nearly certainly an error (a wrong key, another
      server), and deleting every account by it would be an error squared.
    * The last admin stays, whatever Jellyfin says - otherwise nobody could
      get into Settings any more.
    """
    live = {bare_id(jid) for jid in live_ids if jid}
    if not live:
        return []

    deleted: list[str] = []
    for account in db.query_all(
            "SELECT id, username, is_admin, jellyfin_user_id FROM accounts"
            " WHERE jellyfin_user_id IS NOT NULL AND jellyfin_user_id <> ''"):
        account = dict(account)
        if bare_id(str(account["jellyfin_user_id"])) in live:
            continue
        if account["is_admin"] and admin_count() <= 1:
            log.warning("account %s no longer exists in Jellyfin, but it is the last "
                        "administrator, so it stays", account["username"])
            continue
        with db.connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account["id"],))
            conn.commit()
        log.info("account %s deleted: the user no longer exists in Jellyfin",
                 account["username"])
        deleted.append(str(account["username"]))
    return deleted


def follow_jellyfin_rights(admins: dict[str, bool]) -> list[str]:
    """The administrator's rights of accounts from Jellyfin follow Jellyfin at every users' sync.

    `admins` is {Jellyfin id: IsAdministrator} of the users whose Policy came in the answer (a user without it
    changes nothing). from_jellyfin() takes the rights over at a sign-in too - but a browser signed in before
    used to keep them for as long as its cookie lived. The last administrator keeps them, as when the person
    is removed in Jellyfin (remove_without_jellyfin): otherwise nobody could get into Settings any more.
    Returns the names of the accounts that changed.
    """
    wanted_by_id = {bare_id(jellyfin_id): bool(is_admin) for jellyfin_id, is_admin in admins.items() if jellyfin_id}
    changed: list[str] = []
    for account in db.query_all(
            "SELECT id, username, is_admin, jellyfin_user_id FROM accounts"
            " WHERE jellyfin_user_id IS NOT NULL AND jellyfin_user_id <> ''"):
        account = dict(account)
        wanted = wanted_by_id.get(bare_id(str(account["jellyfin_user_id"])))
        if wanted is None or wanted == bool(account["is_admin"]):
            continue
        if not wanted and admin_count() <= 1:
            log.warning("account %s is no administrator in Jellyfin any more, but it is the last "
                        "administrator here, so it keeps the rights", account["username"])
            continue
        with db.connect() as conn:
            conn.execute("UPDATE accounts SET is_admin = ? WHERE id = ?", (1 if wanted else 0, account["id"]))
            conn.commit()
        log.info("account %s: rights taken from Jellyfin (%s)", account["username"],
                 "admin" if wanted else "viewer")
        changed.append(str(account["username"]))
    return changed


def disabled_in_jellyfin(account: dict[str, Any]) -> bool:
    """Is this an account from Jellyfin whose person is disabled there?

    The sign-in asks Jellyfin and refuses such a person - but a browser signed in before, and an own API key,
    used to go on working. The state comes with the users' sync (every quick refresh, 15 minutes by default):
    such an account opens no page and no API until it is enabled in Jellyfin again. The account is not deleted,
    so its settings stay. A person the sync has not seen yet is not taken for disabled.
    """
    jellyfin_id = str(account.get("jellyfin_user_id") or "")
    if not jellyfin_id:
        return False
    # By every form of the id (Jellyfin sends it with dashes and without) - on the primary key, this runs on
    # every request; were there two rows, the one synced last counts (COALESCE: NULL sorts last on both databases).
    bare = bare_id(jellyfin_id)
    dashed = f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}" if len(bare) == 32 else bare
    row = db.query_one("SELECT is_disabled FROM users WHERE id IN (?, ?, ?)"
                       " ORDER BY COALESCE(synced_at, '') DESC LIMIT 1", (jellyfin_id, bare, dashed))
    if not (row and dict(row).get("is_disabled")):
        return False
    # No local administrator is left (an older installation): the last administrator is not shut out.
    return not (account.get("is_admin") and admin_count() <= 1)


def bare_id(jellyfin_id: str) -> str:
    """The id without dashes and in lower case.

    Jellyfin sends the same id once with dashes, once without - depending on
    which endpoint answers. It has to be compared cleaned, otherwise this
    tidying up would delete an account whose person is in Jellyfin just fine.
    The one function for the whole application is `jellyfin.id_without_dashes`.
    """
    return id_without_dashes(jellyfin_id)


def _free_name(base: str) -> str:
    """A name nobody has yet. To a taken one it adds a number."""
    base = (base or "jellyfin").strip() or "jellyfin"
    if get_by_name(base) is None:
        return base
    for number in range(2, 100):
        candidate = f"{base} ({number})"
        if get_by_name(candidate) is None:
            return candidate
    return f"{base} ({db.utcnow()})"


def set_password(account_id: int, password: str, again: str | None = None) -> None:
    """A new password of a local account. An account from Jellyfin has its password in Jellyfin.

    A local one set here would be a second door to it: one its owner can neither change nor
    see, and one that ignores sign-in through Jellyfin switched off.
    """
    account = get(account_id)
    if account is None:
        raise AccountError("The account does not exist.")
    if account.get("jellyfin_user_id"):
        raise AccountError("The password of a Jellyfin account is changed in Jellyfin.")
    password = validate_password(password, again)
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?",
                     (hash_password(password), account_id))
    # The password itself does not get into the log, only that it changed and whose.
    log.info("password changed for account %s", account.get("username") or account_id)


class WrongCurrentPassword(AccountError):
    """A wrong current password on the My account page.

    Separate from the other errors, because it counts towards the brake on
    guessing (the same as at sign-in). A new password that is too short is no
    guessing.
    """


def session_fingerprint(account: dict[str, Any]) -> str:
    """A short fingerprint of the password for the session (cookie).

    It is stored at sign-in and compared with every request. When the password
    changes, the fingerprint stops matching and the old session ends. It is a
    hash of the hash, neither the password nor its stored hash: the cookie is
    signed, but readable. An account from Jellyfin has an empty password hash,
    so this never signs it out - and the hash alone would be the same for all of
    them: once an id is handed out again (a restored backup, a copy to another
    database sets the counter to the highest id), a deleted account's cookie would
    open the new one. So its Jellyfin id and creation time are in it too. A local
    account keeps the old form, its sessions hold over an upgrade.
    """
    source = "relace:" + str(account.get("password_hash") or "")
    jellyfin_id = str(account.get("jellyfin_user_id") or "")
    if jellyfin_id:
        source += "|" + bare_id(jellyfin_id) + "|" + str(account.get("created_at") or "")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def change_own_password(account_id: int, current: str, password: str,
                        again: str | None = None) -> None:
    """Changing one's own password from the My account page - only with the current password.

    An admin changes passwords in Settings → Accounts without the old one: a
    forgotten password is exactly why one goes there. Not here - whoever sits
    down at someone else's signed-in browser must not be able to overwrite the
    password and lock the owner out by it. An account from Jellyfin has no
    password here at all, that belongs to Jellyfin.
    """
    account = get(account_id)
    if account is None:
        raise AccountError("The account does not exist.")
    if account.get("jellyfin_user_id"):
        raise AccountError("The password of a Jellyfin account is changed in Jellyfin.")
    if not verify_password(current or "", str(account.get("password_hash") or "")):
        raise WrongCurrentPassword("The current password is wrong.")
    set_password(account_id, password, again)


def save_may_have_key(account_id: int, value: str | None) -> None:
    """The exception to allowing an own API key: "1", "0", or None = the group.

    Anything else is stored as None - only three states belong in the column.
    """
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET may_have_key = ? WHERE id = ?",
                     (value if value in ("1", "0") else None, account_id))


def set_language(account_id: int, language: str | None) -> None:
    """The account's interface language (My account). Empty = the server's language.

    The route guards that the code is valid - here it is only stored.
    """
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET ui_language = ? WHERE id = ?",
                     (language or None, account_id))


def set_event_motion(account_id: int, motion: bool) -> None:
    """Does the seasonal decoration move for the account? (My account.) On is stored as
    NULL - the same as an account that never saw the choice."""
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET events_motion = ? WHERE id = ?",
                     (None if motion else "0", account_id))


def set_admin(account_id: int, is_admin: bool) -> None:
    """Changes the permissions. Demoting the last admin is not allowed."""
    account = get(account_id)
    if account is None:
        raise AccountError("The account does not exist.")
    if account.get("jellyfin_user_id"):
        # They follow Jellyfin at every users' sync - a change here would be undone within minutes.
        raise AccountError("The rights of an account from Jellyfin are set in Jellyfin.")
    if account["is_admin"] and not is_admin and is_last_local_admin(account):
        raise AccountError("You cannot remove privileges from the last local administrator.")
    if account["is_admin"] and not is_admin and admin_count() <= 1:
        raise AccountError("You cannot remove privileges from the last administrator.")

    with db.connect() as conn:
        if is_admin:
            conn.execute("UPDATE accounts SET is_admin = 1 WHERE id = ?", (account_id,))
        # The checks above and the write are two steps: two administrators demoting each other at once both
        # passed them. The write checks again in the same statement, so one of them is refused.
        elif not _write_keeping_admin(conn, _DEMOTE_KEEPING_LOCAL, account_id):
            raise AccountError("You cannot remove privileges from the last local administrator.")
    log.info("account %s is now %s", account["username"],
             "admin" if is_admin else "reader")


def delete(account_id: int) -> None:
    """Deletes an account. The last admin stays - otherwise nobody could get in any more."""
    account = get(account_id)
    if account is None:
        raise AccountError("The account does not exist.")
    if is_last_local_admin(account):
        raise AccountError("The last local administrator cannot be deleted: one must always stay, "
                           "to get in when Jellyfin is not reachable.")
    if account["is_admin"] and admin_count() <= 1:
        raise AccountError("The last administrator cannot be deleted.")

    local = not account.get("jellyfin_user_id")
    with db.connect() as conn:
        # Checked again in the write: somebody else may have changed the administrators since the checks above.
        if not _write_keeping_admin(conn, _DELETE_KEEPING_LOCAL if local else _DELETE_KEEPING_ANY, account_id):
            if local:
                raise AccountError("The last local administrator cannot be deleted: one must always stay, "
                                   "to get in when Jellyfin is not reachable.")
            raise AccountError("The last administrator cannot be deleted.")
    log.info("account %s deleted", account["username"])


# The write happens only when the account is no administrator or another (local) one stays. A local
# administrator needs another local one, an administrator from Jellyfin any other administrator (an older
# installation may have no local one).
_OTHER_LOCAL_ADMIN = (" AND (is_admin = 0 OR (SELECT COUNT(*) FROM accounts WHERE is_admin = 1 AND id <> ?"
                      " AND COALESCE(jellyfin_user_id, '') = '') >= 1)")
_DEMOTE_KEEPING_LOCAL = "UPDATE accounts SET is_admin = 0 WHERE id = ?" + _OTHER_LOCAL_ADMIN
_DELETE_KEEPING_LOCAL = "DELETE FROM accounts WHERE id = ?" + _OTHER_LOCAL_ADMIN
_DELETE_KEEPING_ANY = ("DELETE FROM accounts WHERE id = ? AND (is_admin = 0"
                       " OR (SELECT COUNT(*) FROM accounts WHERE is_admin = 1 AND id <> ?) >= 1)")


def _write_keeping_admin(conn: Any, sql: str, account_id: int) -> bool:
    """Runs one of the writes above, whose condition keeps an administrator. Returns whether it wrote.

    The condition is part of the statement, so on SQLite the check and the write run under one write lock.
    On PostgreSQL the administrators' rows are locked first: two such writes at once take turns, and the
    second one sees the first one's result.
    """
    if conn.is_postgres:
        conn.execute("SELECT id FROM accounts WHERE is_admin = 1 ORDER BY id FOR UPDATE")
    return bool(conn.execute(sql, (account_id, account_id)).rowcount)
