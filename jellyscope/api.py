"""A read-only API for tools like Grafana or Homepage.

It is **a source of numbers, not a second way into the data**. Therefore:

* **Read only.** No route under `/api/` changes anything - not as a rule kept
  in mind, but literally: there are only `GET`s here. Anything else returns
  405.
* **Ready-made overviews, not a query language.** A few addresses that answer
  the questions a person builds a dashboard for. A general API over the
  statistics would mean keeping the shape of every inner query forever.
* **The token in a header, never in the address.** An address shows up in the
  proxy's log, in the browser's history and in a link somebody passes on. A
  header does not.

Tokens
------

A **fingerprint** is stored, not the token - just like with passwords. Whoever
reads the database gets no access; whoever leaks a token revokes it in
Settings and makes a new one, without touching the others.

The fingerprint is SHA-256, not PBKDF2 as with passwords, and on purpose:
nobody makes a token up, `secrets.token_urlsafe(32)` makes it - 256 bits of
randomness. A slow fingerprint protects against guessing a weak password;
here there is nothing to guess and every request would be slower for it.

Owner and state
---------------

A user key (My account) belongs to an account and may **exactly what its
owner may** - no more. A root key (Settings → API, `account_id` is NULL)
belongs to nobody and may only what the administrator set for it
(`api_tokens.permissions`); without rights set it may everything - those are also
keys from before owners, so Grafana and Homepage do not break after an update.

The administrator can disable a key and enable it again - say when they
suspect it leaked and want to look into it first. A disabled key returns
nothing, but stays in the list. Every request with an existing key is written
to the API log (`data/logs/api.log`): when, where, from where and how it
ended. See record_use().
"""
from __future__ import annotations

import sys

import functools
import hashlib
import json
import logging
import secrets
import threading
from datetime import date, datetime, timedelta, UTC
from typing import Any

from fastapi import APIRouter, Depends, Request

from . import (access, accounts, api_endpoints, api_query, applog, db, dialect, formatting, i18n,
               insights, scanner, stats)
from .api_query import ApiError

log = logging.getLogger("jellyscope.api")

# A prefix, so it is plain at first sight what this is - in a log, in a
# dashboard's configuration and in someone else's repository it gets into by mistake.
PREFIX = "js_"

# How many characters of a token Settings show - the prefix included. The rest
# is behind asterisks and there is nowhere to take it from any more: only the
# fingerprint is stored. Five characters are two of the random part; tokens
# are told apart by their name, not by the start of the key.
PREVIEW_CHARS = 5

# How often "last used" is rewritten. Grafana asks every few seconds and a
# write at every request is needless; for the question "is this token still
# alive?" a minute is enough.
USAGE_WRITE_AFTER = timedelta(minutes=1)

# The key's states. Revoking has no state - that is deleting the row.
ACTIVE = "active"
DISABLED = "disabled"

router = APIRouter(prefix="/api/v1", tags=["api"])

# Which part of the tree "who sees what" (access.AREAS) each address shows.
# The key is the route's path as FastAPI knows it - with `{item_id}` too. None
# = anyone who passed verify_token may: the index shows no data and the
# history forces the filter on its own viewer itself, like the History page.
#
# An address missing here is closed to everyone but the administrator - so a
# new route without an area does not open by mistake. A test guards that every
# one is here. Made from the one list of addresses (api_endpoints), plus the index, which every key may
# open; read by the rights check, key_areas and the rights matrix test.
ADDRESS_AREAS: dict[str, str | None] = {
    **{path: None for path in api_endpoints.INDEX_PATHS},
    **{endpoint.path: endpoint.area for endpoint in api_endpoints.ENDPOINTS},
}


@functools.cache
def key_areas() -> frozenset[str]:
    """Areas a root key can use: those with an address above and the "...who" names under them.

    The one rule for the key's tree in Settings, for saving its rights and for reading them back
    (the table of keys, the Rights window). Music, *Arr, languages or the custom dashboard open
    nothing in the API: a key with them would hold a right that does nothing. A key saved with
    them before 16. 11. 2026 loses them once at start (drop_unusable_key_areas). Fixed for the
    life of the process, so computed once.
    """
    with_address = {area for area in ADDRESS_AREAS.values() if area}
    return frozenset(with_address | {area.key for area in access.AREAS
                                     if not area.paths and area.parent in with_address})


# Done once: the setting says the conversion below ran (any non-empty value).
CONVERSION_KEY_AREAS = "conversion_key_areas"
# The areas a root key could use when the conversion was written (16. 11. 2026, up to 1.9.x): the conversion keeps
# only these. Not key_areas(): that grows with every version that gives an area an address (languages, music, *Arr,
# Seerr on 27. 11. 2026), and an old key would get them without the administrator ever seeing them ticked.
KEY_AREAS_UNTIL_1_9 = frozenset({"charts", "library", "library_who", "network", "now_playing", "overview", "users"})


def drop_unusable_key_areas() -> bool:
    """One-off at start: stored rights of root keys keep only areas a key can use.

    Until 16. 11. 2026 the tree of a root key offered every area. Reading drops the rest
    (key_permissions), but stored they would wait: should a later version give one of them an
    address in the API (languages, say), an old key would get it without the administrator
    ever seeing the box ticked. So they go from the database too, once. True = it ran now.
    """
    if db.get_setting(CONVERSION_KEY_AREAS, ""):
        return False
    rows = db.query_all("SELECT id, permissions FROM api_tokens WHERE account_id IS NULL AND permissions IS NOT NULL")
    with db.connect() as conn:
        for row in rows:
            rights = key_permissions(row["permissions"])
            if rights is None:
                continue
            packed = pack_key_permissions(set(rights["areas"]) & KEY_AREAS_UNTIL_1_9, rights["anonymise"], rights["history"])
            if packed != row["permissions"]:
                conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?", (packed, row["id"]))
        conn.commit()
    db.set_setting(CONVERSION_KEY_AREAS, "1")
    return True


CONVERSION_NETWORK_KEYS = "conversion_network_origin_keys"


def add_network_origin_to_keys() -> bool:
    """One-off at start, after drop_unusable_key_areas: a root key with Network gets "...where they watch from" too.

    The countries of /api/v1/bandwidth belong to the sub-area since 27. 11. 2026 (access.split_network_rights does the
    groups and accounts); a key that had them keeps them until the administrator unticks it. True = it ran now.
    """
    if db.get_setting(CONVERSION_NETWORK_KEYS, ""):
        return False
    rows = db.query_all("SELECT id, permissions FROM api_tokens WHERE account_id IS NULL AND permissions IS NOT NULL")
    with db.connect() as conn:
        for row in rows:
            rights = key_permissions(row["permissions"])
            if rights is None or "network" not in rights["areas"]:
                continue
            conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?",
                         (pack_key_permissions(set(rights["areas"]) | {"network_origin"}, rights["anonymise"],
                                               rights["history"]), row["id"]))
        conn.commit()
    db.set_setting(CONVERSION_NETWORK_KEYS, "1")
    return True


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(name: str, account_id: int | None = None,
                 permissions: str | None = None) -> dict[str, Any]:
    """Makes a token. Returns it in readable form - for the last time.

    Only the fingerprint is stored, so nobody learns this value a second time.
    The page says so out loud; a token that can be read at any time is only a
    longer password in the database.

    `account_id` is the owner - a user key (My account). Without it a root key
    is made (Settings → API) with the rights `permissions` (`pack_key_permissions`);
    without rights it has everything, like the keys from before owners.
    """
    # Without a name it is stored without one: the listing shows "unnamed" in the reader's language
    # (display_name), the data carries no language.
    # Only printable characters, spaces collapsed (like applog._field): the name goes into the app's log, and a
    # line end in it (\n, \r, \x85, U+2028 …) would make a forged line of its own there - with any level and text.
    name = " ".join("".join(c if c.isprintable() else " " for c in (name or "")).split())[:60]
    token = PREFIX + secrets.token_urlsafe(32)
    with db.connect() as conn:
        token_id = conn.insert_returning_id(
            # The state written out: a database converted from 1.8.x keeps the old default of the column.
            "INSERT INTO api_tokens (name, token_hash, preview, created_at, account_id, permissions, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, _fingerprint(token), token[:PREVIEW_CHARS], db.utcnow(), account_id,
             permissions if account_id is None else None, ACTIVE),
        )
        conn.commit()
    log.info("API token %s created", name or "-")
    # "display_name" for people (the reader's language), "name" as stored (for the English log).
    return {"token": token, "display_name": display_name(name), "name": name, "id": token_id}


# The limit on keys of one's own (Settings → API): off by default, then how many ACTIVE keys one account may
# hold. A disabled key does not count and root keys are never limited - the administrator makes those.
KEY_LIMIT_ENABLED = "api_key_limit_enabled"
KEY_LIMIT = "api_key_limit"
KEY_LIMIT_DEFAULT, KEY_LIMIT_MIN, KEY_LIMIT_MAX = 10, 1, 1000
# The count and the new row together: requests sent at once must not all pass a count made before any insert.
_OWN_KEY_LOCK = threading.Lock()


def key_limit_setting() -> tuple[bool, int]:
    """The limit on keys of one's own as saved: (on, how many active keys one account may hold)."""
    return (db.get_setting(KEY_LIMIT_ENABLED, "0") == "1",
            db.get_int_setting(KEY_LIMIT, KEY_LIMIT_MIN, KEY_LIMIT_MAX, KEY_LIMIT_DEFAULT))


def create_own_token(name: str, account_id: int) -> dict[str, Any] | None:
    """A key of one's own (My account), as create_token. None = the account already holds the limit."""
    with _OWN_KEY_LOCK:
        limited, limit = key_limit_setting()
        if limited:
            row = db.query_one("SELECT COUNT(*) AS n FROM api_tokens WHERE account_id = ? AND status = ?",
                               (account_id, ACTIVE))
            if int(row["n"] if row else 0) >= limit:
                return None
        return create_token(name, account_id)


def list_tokens(account_id: int | None = None) -> list[dict[str, Any]]:
    """Tokens for the listing. Without fingerprints - they have no business there.

    Without the parameter all of them with the owner's name (for the
    administrator), with it only that account's keys (My account). `owner`
    is None for a root key.
    """
    sql = ("SELECT t.id, t.name, t.preview, t.created_at, t.last_used_at,"
           "       t.status, t.account_id, t.permissions, a.username AS owner"
           "  FROM api_tokens t LEFT JOIN accounts a ON a.id = t.account_id")
    if account_id is None:
        rows = db.query_all(sql + " ORDER BY t.id DESC")
    else:
        rows = db.query_all(sql + " WHERE t.account_id = ? ORDER BY t.id DESC", (account_id,))
    return [{**dict(row), "display_name": display_name(row["name"])} for row in rows]


def display_name(name: str | None) -> str:
    """A key's name for people: its own, or "unnamed" in the reader's language."""
    return str(name or "") or i18n.translate("unnamed")


def _size_human(size: Any) -> str:
    """A size for a person ("1,5 TB") in the notation the API always gave, whatever the caller's language.

    That is the notation of Czech: a decimal comma, a space after the thousands. The numbers on pages follow
    the page's language (formatting.NUMBER_SEPARATORS); the JSON does not, a client may read this text.
    """
    return formatting.bytes_human(size, language="cs")


def pack_key_permissions(areas: Any, anonymise: bool, history: bool) -> str:
    """A root key's rights in the form they are stored in (`api_tokens.permissions`).

    The same shape as an account's own rights (`access.pack_permissions`) plus
    the history: for an account access.sees_history governs it (a reader only
    with "...including who watched"), a root key for a library widget must not
    get it until the administrator ticks it. Only areas a key can use are kept (key_areas).
    """
    usable = key_areas()
    return json.dumps({
        "areas": sorted({str(k) for k in areas if str(k) in usable}),
        "anonymise": bool(anonymise),
        "history": bool(history),
    })


def key_permissions(stored: Any) -> dict[str, Any] | None:
    """A root key's stored rights. None = everything.

    Broken JSON (a manual change in the database) NEVER means everything: it is
    taken as "nothing" - no area, no history, names hidden. For an account it
    is the group instead (`access.own_permissions`); a key has no group and full
    rights would be exactly the hole nobody expects.
    """
    if stored is None:
        return None
    try:
        data = json.loads(stored) if isinstance(stored, str) else stored
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return {"areas": set(), "anonymise": True, "history": False}
    areas = data.get("areas")
    usable = key_areas()
    # Only real true/false: bool("false") is True, and a hand in the database
    # (text instead of a value, a missing key) must not open the history nor
    # reveal names. A doubt = no history, names hidden.
    return {
        "areas": ({str(k) for k in areas if str(k) in usable}
                    if isinstance(areas, list) else set()),
        "anonymise": data.get("anonymise") is not False,
        "history": data.get("history") is True,
    }


def save_key_permissions(token_id: int, permissions: str | None) -> dict[str, Any] | None:
    """Changes the rights of a ROOT key. Returns the name and the previous rights, otherwise None.

    A user key is not changed this way - that one follows its account's rights.
    It holds from the next request: verify_token reads the rights at each of them.
    """
    row = db.query_one("SELECT name, account_id, permissions FROM api_tokens WHERE id = ?",
                       (token_id,))
    if not row or row["account_id"] is not None:
        return None
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET permissions = ? WHERE id = ?", (permissions, token_id))
        conn.commit()
    return {"display_name": display_name(row["name"]), "name": row["name"], "before": row["permissions"]}


def permissions_for_log(permissions: str | None) -> str:
    """A root key's rights in one line for the application log."""
    unpacked = key_permissions(permissions)
    if unpacked is None:
        return "all"
    return (f"areas={','.join(sorted(unpacked['areas'])) or '-'}"
            f" history={'yes' if unpacked['history'] else 'no'}"
            f" hide_names={'yes' if unpacked['anonymise'] else 'no'}")


def _set_state(token_id: int, state: str) -> str:
    row = db.query_one("SELECT name FROM api_tokens WHERE id = ?", (token_id,))
    if not row:
        return ""
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET status = ? WHERE id = ?", (state, token_id))
        conn.commit()
    if state == DISABLED:
        log.info("API token %s disabled", row["name"] or "-")
    else:
        log.info("API token %s enabled", row["name"] or "-")
    return display_name(row["name"])


def disable_token(token_id: int) -> str:
    """Disables a key, but does not delete it. Returns its name (empty = there is none)."""
    return _set_state(token_id, DISABLED)


def enable_token(token_id: int) -> str:
    """Lets a disabled key in again. Returns its name."""
    return _set_state(token_id, ACTIVE)


def revoke_token(token_id: int) -> str:
    """Revokes a token. Returns its name, so the message has something to say."""
    row = db.query_one("SELECT name FROM api_tokens WHERE id = ?", (token_id,))
    if not row:
        return ""
    with db.connect() as conn:
        conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
        conn.commit()
    log.info("API token %s revoked", row["name"] or "-")
    return display_name(row["name"])


def _mark_use(row: dict[str, Any]) -> None:
    """Writes "last used" - but not at every request."""
    last_used = row.get("last_used_at")
    if last_used:
        try:
            when = datetime.strptime(str(last_used), db.TIME_FORMAT).replace(
                tzinfo=UTC)
            if datetime.now(UTC) - when < USAGE_WRITE_AFTER:
                return
        except ValueError:
            pass
    with db.connect() as conn:
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                     (db.utcnow(), row["id"]))
        conn.commit()


def token_from_header(request: Request) -> str:
    """The token from `Authorization: Bearer ...`, or empty.

    One header is supported, not three ways. More ways in means more places
    where a mistake can be made - and `Bearer` is what every tool can do on
    its own.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def verify_token(request: Request) -> dict[str, Any]:
    """The barrier before every address of the API.

    The order of the checks: the key exists (otherwise 401) → it is enabled →
    the owner may. As soon as the key is found, its id is written into
    `request.state`, so the request goes into the usage record - even when it
    ends in a refusal.

    Returns the key's row plus `account` (the account the rights follow: the
    owner; for a root key with chosen rights a substitute account, for a root
    key with everything None), `role` (the group) and `history` (may see the history).
    """
    token = token_from_header(request)
    if not token:
        raise ApiError(401, "missing_token",
                       "Missing token. Send the header: Authorization: Bearer <token>",
                       headers={"WWW-Authenticate": "Bearer"})

    row = db.query_one(
        "SELECT id, name, last_used_at, status, account_id, permissions"
        "  FROM api_tokens WHERE token_hash = ?",
        (_fingerprint(token),),
    )
    # An account deleted without a cascade (a manual change in the database) =
    # a key that belongs to nobody. It acts as one that does not exist, not as a root key.
    account = accounts.get(row["account_id"]) if row and row["account_id"] else None
    if not row or (row["account_id"] and not account):
        # On purpose the same sentence as for a missing token, only a different
        # first half: what exactly is wrong the attacker must not learn.
        raise ApiError(401, "invalid_token", "Invalid token.",
                       headers={"WWW-Authenticate": "Bearer"})

    # For the API log (web.ApiUsageLog): a refused request is written too
    # - exactly that one is interesting when a leak is suspected.
    request.state.api_key_in_log = {
        "id": row["id"], "name": row["name"],
        "owner": (account or {}).get("username") or ""}
    if row["status"] != ACTIVE:
        raise ApiError(403, "key_disabled", "The key is disabled.")

    # A user key: the owner's rights. An administrator's key has everything -
    # but only while they are an administrator: whoever loses the role has a
    # reader's (viewer's) rights and their key starts to follow the permission
    # like any other.
    #
    # A root key (without an owner): without rights everything (keys from
    # before owners too), with rights it counts as a reader's account with its
    # own rights - access.sees and anonymise_for then hold unchanged. It touches
    # no real account, so a change of the administrator does not affect it.
    rights_account = account
    history = True
    if account:
        role = access.role(account)
        # The key's history = the account's history (a reader only with
        # "...including who watched"). A root key has its own tick box, see below.
        history = access.sees_history(account)
    else:
        rights = key_permissions(row["permissions"])
        if rights is None:
            role = access.ADMIN
        else:
            role = access.READER
            history = rights["history"]
            rights_account = _substitute_account(rights)
    if account and not access.may_have_key(account):
        raise ApiError(403, "key_not_allowed",
                       "The key's account is not allowed a key of its own.")
    # A person from Jellyfin may come in only by Jellyfin letting them. When the
    # administrator disables them in Jellyfin, their key stops - an administrator's
    # from Jellyfin too (it used to go on: only a viewer's was checked, and by the id
    # exactly, so an id with dashes slipped by). When sign-in through Jellyfin is
    # turned off, a viewer's key stops too - otherwise a tool would go on taking
    # data, while the administrator is sure the viewers are out.
    if account and accounts.disabled_in_jellyfin(account):
        raise ApiError(403, "viewer_disabled" if role == access.VIEWER else "account_disabled",
                       "The account is disabled in Jellyfin.")
    if access.viewer_shut_out(account):
        raise ApiError(403, "jellyfin_login_off", "Sign-in through Jellyfin is off.")

    _mark_use(row)
    return {**row, "account": rights_account, "role": role, "history": history}


def _may(token: dict[str, Any], path: str) -> bool:
    """May the key's owner open an address with this route path?"""
    # An address with people's plays (api_endpoints: needs_history) needs the history: for an account
    # access.sees_history holds (computed in verify_token), a root key with rights has it only when ticked.
    endpoint = api_endpoints.by_path(path)
    if endpoint is not None and endpoint.needs_history and not token.get("history", True):
        return False
    if token["role"] == access.ADMIN:
        return True
    if path not in ADDRESS_AREAS:
        return False
    area = ADDRESS_AREAS[path]
    return area is None or access.sees(token["role"], area, token["account"])


def _substitute_account(rights: dict[str, Any]) -> dict[str, Any]:
    """A reader's account that exists only for checking a root key's rights.

    access.sees and anonymise_for then hold unchanged. It touches no real
    account, so a change of the administrator does not affect it.
    """
    return {"id": None, "username": "", "is_admin": False,
            access.ACCESS_COLUMN: pack_key_permissions(
                rights["areas"], rights["anonymise"], rights["history"])}


def root_key_paths(permissions: str | None) -> list[str]:
    """API addresses a root key with these rights really opens.

    Without the index (any key may open `/api/v1/`). An empty list = the key could open nothing -
    e.g. only "...who watched" without the Library, or a form from an older page with only
    areas that have no address in the API (key_areas drops them); the form makes no such key.
    """
    unpacked = key_permissions(permissions)
    if unpacked is None:
        token = {"role": access.ADMIN, "account": None}
    else:
        token = {"role": access.READER, "account": _substitute_account(unpacked),
                 "history": unpacked["history"]}
    return [path.replace("{item_id}", "{id}") for path in ADDRESS_AREAS
            if path not in ("/api/v1", "/api/v1/") and _may(token, path)]


def allowed_paths(account: dict[str, Any] | None) -> list[str]:
    """Addresses a key of this account may open - for the guide on My account.

    The same conditions as may_open_path(), only without a key in hand. The
    index (`/api/v1/`) is here once, even though two paths lead to it.
    """
    # The history as verify_token computes it for an account's key - otherwise the guide would offer the addresses with
    # people's plays to an account whose key gets 403 there.
    token = {"role": access.role(account), "account": account, "history": access.sees_history(account)}
    return [path.replace("{item_id}", "{id}") for path in ADDRESS_AREAS
            if path != "/api/v1" and _may(token, path)]


def may_open_path(request: Request,
                  token: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """The barrier behind verify_token: a key sees exactly what its owner does.

    One dependency for all addresses, not a check in each of them - an address
    somebody would forget is exactly the one somebody finds.
    """
    path = getattr(request.scope.get("route"), "path", "")
    if not _may(token, path):
        raise ApiError(403, "area_not_allowed",
                       "The key's owner cannot see this part of the statistics.")
    return token


def request_query(request: Request,
                  token: dict[str, Any] = Depends(may_open_path)) -> api_query.Query:
    """The address's query, read and checked (filters, sort, parameters).

    It stands behind `may_open_path`, so FastAPI runs it only after the barrier:
    whoever has no valid key gets 401/403 and does not even learn which
    parameters or fields the address knows.
    """
    path = getattr(request.scope.get("route"), "path", "")
    return api_query.parse(list(request.query_params.multi_items()),
                           KNOWN_PARAMS.get(path, set()), LISTS.get(path, {}))


def _query_days(query: api_query.Query, default: int = 30) -> int:
    return api_query.integer(query, "days", default, 1, 3650)


def _query_limit(query: api_query.Query, default: int | None, cap: int) -> int | None:
    """How many rows. Over the cap or nonsense is a 400, not a quiet cut.

    The cap is there for the server, not for the data: "give me everything"
    over a library with forty thousand titles is an answer of tens of MB and a
    dashboard asking every few seconds would keep the server busy to the full.
    """
    return api_query.integer(query, "limit", default, 1, cap)


def _parse_query(query: api_query.Query, list_name: str, limit: int | None) -> int:
    """How many rows to ask of a function that has the limit in SQL.

    With a filter or a sort the whole list - the filter must see everything,
    otherwise the rows looked for deeper in the order would drop out. Without
    them `limit` is enough as before: the whole list is expensive on a big
    library (the history of a year from 150 000 plays takes seconds) and the
    result would be the same.
    """
    if limit is None or any(query.for_list(list_name)):
        return stats.NO_LIMIT
    return limit


def _query_kind(query: api_query.Query) -> str:
    return api_query.choice(query, "kind", stats.KIND_BOTH,
                            (stats.KIND_BOTH, stats.KIND_MOVIE, stats.KIND_SERIES))


def _hidden_kinds(token: dict[str, Any]) -> frozenset[str]:
    """The kinds of plays the key may not see with the person (access.hidden_kinds).

    A root key with everything has no account to ask, but the role of an administrator.
    A root key with chosen rights has no Music or Books areas, so it gets no music and books.
    """
    return frozenset() if token["role"] == access.ADMIN else access.hidden_kinds(token.get("account"))


def _for_owner(token: dict[str, Any], data: Any) -> Any:
    """Hides other people's names, ids and addresses when the key's owner has them hidden.

    The same rule as on the pages (access.anonymise_for). What goes through
    here are **the source rows** from `stats`, not the finished answer: there
    the name is called `user` and access.anonymise() would not recognise it by the key.
    """
    return access.anonymise_for(data, token.get("account"))


def record_use(key: dict[str, Any], path: str, ip: str, status: int) -> None:
    """Writes one request into the API log (applog.write_api).

    The middleware in web/__init__.py calls it only after the answer - only
    then the result is known. `key` is what verify_token() left in the request:
    the id, the name and the owner (empty = a root key). How long the record
    is kept is governed by the Log rotation task.
    """
    applog.write_api(key["id"], key.get("name") or "", key.get("owner") or "",
                     path, ip, status)


@router.get("")
@router.get("/")
def index(token: dict[str, Any] = Depends(may_open_path),
          query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """What the API can do. So it does not have to be looked up in the documentation.

    Only the addresses this key may open - what would return 403 has no
    business here. For addresses with a list their fields and types (`lists`)
    and how to filter (`filtering`) too, so a program finds out by itself what
    it may send.
    """
    from . import __version__

    return {
        "jellyscope": __version__,
        "endpoints": {endpoint.path.replace("{item_id}", "{id}"): endpoint.summary
                      for endpoint in api_endpoints.ENDPOINTS if _may(token, endpoint.path)},
        "note": "Read-only. Authenticate with: Authorization: Bearer <token>. "
                "Every list named under lists takes filters, sort and limit (see filtering); the live lists "
                "of the *Arr apps and Seerr (short, as the app says them now) take none; "
                "a malformed request is a 400 with an error code, never default data.",
        "filtering": api_query.help_text(),
        "lists": {address: lists for address, lists in LISTS.items()
                  if _may(token, address)},
        "item_fields": "every item that is one file (least-played, unwatched, recently-added, "
                       "item, insights.oversized_rarely_watched) has path, tmdb_id, season and "
                       "episode; for an episode tmdb_id is the series' TMDB id and season + "
                       "episode say which one. Lists by title (top-items and the other insights "
                       "lists) have no single file; a playback in history has item_id for /item/{id}",
    }


def _file(row: dict[str, Any]) -> dict[str, Any]:
    """The path and the tmdb id - for every item (file) the API returns.

    For an episode `tmdb_id` is the id of the **series**: an episode itself
    usually has no id of its own in TMDB and Jellyscope stores the series' one.
    Which episode it is, the season and the episode number beside it say -
    only together they are the episode's address in TMDB. For everything else
    both are None: a music track has the track number in `index_number` and
    would pass for an episode.

    The path is whole, as the server sees it - just like in the title's
    detail. A key gets it only when its owner may open the address that
    returns it (see ADDRESS_AREAS).
    """
    is_episode = row.get("type") == "Episode"
    return {
        "path": row.get("path"),
        "tmdb_id": row.get("tmdb_id"),
        "season": row.get("parent_index_number") if is_episode else None,
        "episode": row.get("index_number") if is_episode else None,
    }


def _title(row: dict[str, Any]) -> dict[str, Any]:
    """One title from the library the way the API returns it - the same everywhere."""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "series": row.get("series_name"),
        "year": row.get("production_year"),
        "library": row.get("library_name"),
        **_file(row),
        "size_bytes": row.get("size_bytes"),
        "size_human": _size_human(row.get("size_bytes") or 0),
        "resolution": (f"{row.get('width')}x{row.get('height')}"
                       if row.get("width") and row.get("height") else None),
        "video_codec": row.get("video_codec"),
        "added_at": row.get("date_created"),
    }


# What the single addresses return - by this the filter and the sort are
# checked and the help is put together from it. A test guards that the keys of
# an answer's row are exactly these fields: so the help cannot promise a field
# that does not exist.
_T, _N, _TM, _YN = api_query.TEXT, api_query.NUMBER, api_query.TIME, api_query.YES_NO
TITLE_FIELDS: dict[str, str] = {
    "id": _T, "name": _T, "type": _T, "series": _T, "year": _N, "library": _T,
    "path": _T, "tmdb_id": _T, "season": _N, "episode": _N, "size_bytes": _N,
    "size_human": _T, "resolution": _T, "video_codec": _T, "added_at": _TM,
}
LISTS: dict[str, dict[str, dict[str, str]]] = {
    "/api/v1/least-played": {"items": {**TITLE_FIELDS, "plays": _N,
                                       "watched_seconds": _N, "last_played": _TM}},
    "/api/v1/unwatched": {"items": TITLE_FIELDS},
    "/api/v1/recently-added": {"items": TITLE_FIELDS},
    "/api/v1/top-items": {"items": {"title": _T, "is_series": _YN, "plays": _N,
                                    "watched_seconds": _N, "watched_hours": _N,
                                    "url": _T}},
    "/api/v1/users": {"users": {"id": _T, "name": _T, "is_administrator": _YN,
                                "plays": _N, "titles": _N, "watched_hours": _N,
                                "transcoded_hours": _N, "last_seen": _TM}},
    "/api/v1/history": {"playbacks": {
        "user": _T, "user_id": _T, "title": _T, "series": _T, "type": _T,
        "item_id": _T, "started_at": _TM, "watched_seconds": _N,
        "play_method": _T, "is_transcode": _YN, "client": _T, "device": _T,
        "audio_language": _T, "subtitle_language": _T}},
    "/api/v1/now-playing": {"sessions": {
        "user": _T, "title": _T, "series": _T, "library": _T, "client": _T,
        "device": _T, "play_method": _T, "is_transcode": _YN, "is_paused": _YN,
        "audio_language": _T, "bitrate": _N, "position_seconds": _N,
        "runtime_seconds": _N, "started_at": _TM}},
    "/api/v1/libraries": {"libraries": {"id": _T, "name": _T, "type": _T, "items": _N,
                                        "size_bytes": _N, "size_human": _T,
                                        "runtime_hours": _N}},
    "/api/v1/bandwidth": {"countries": {"country": _T, "code": _T, "plays": _N,
                                        "watched_seconds": _N, "bytes": _N}},
    # More lists at one address: a field in the query carries the list's prefix
    # (`never_finished.title[contains]=x`). Without a filter they are called as before.
    "/api/v1/insights": {
        "transcode_offenders": {"title": _T, "transcodes": _N, "reasons": _T,
                                "video_codec": _T, "audio_codec": _T,
                                "resolution": _T, "url": _T},
        "upgrade_candidates": {"title": _T, "plays": _N, "watched_hours": _N,
                               "resolution": _T, "bitrate": _N, "video_codec": _T,
                               "url": _T},
        "oversized_rarely_watched": {"id": _T, "title": _T, "type": _T, "path": _T,
                                     "tmdb_id": _T, "season": _N, "episode": _N,
                                     "plays": _N, "watched_hours": _N,
                                     "size_bytes": _N, "size_human": _T,
                                     "gb_per_hour": _N, "resolution": _T},
        "never_finished": {"title": _T, "attempts": _N, "best_percent": _N,
                           "runtime_minutes": _N},
    },
    "/api/v1/play-methods": {
        "methods": {"method": _T, "hours": _N},
        "clients": {"client": _T, "hours": _N, "plays": _N},
    },
}
# An address's plain parameters - what is not here (and is not a filter nor
# `sort`) is a 400 unknown_parameter.
KNOWN_PARAMS: dict[str, set[str]] = {
    "/api/v1/least-played": {"days", "limit", "min_size_mb", "order"},
    "/api/v1/unwatched": {"days", "limit"},
    "/api/v1/recently-added": {"limit"},
    "/api/v1/top-items": {"days", "limit", "kind"},
    "/api/v1/users": {"days", "limit"},
    "/api/v1/history": {"days", "limit", "user", "kind"},
    "/api/v1/now-playing": {"limit"},
    "/api/v1/libraries": {"limit"},
    "/api/v1/bandwidth": {"days", "limit"},
    "/api/v1/insights": {"days", "limit"},
    "/api/v1/play-methods": {"days", "limit"},
    "/api/v1": set(),
    "/api/v1/": set(),
    "/api/v1/summary": {"days"},
    "/api/v1/library": {"days"},
    "/api/v1/item/{item_id}": set(),
}


def path_fields(paths: list[str]) -> dict[str, dict[str, dict[str, str]]]:
    """The lists' fields of the addresses a key may open - for the "How it is used" window."""
    return {path: LISTS[path] for path in paths if path in LISTS}


@router.get("/libraries")
def libraries(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """Every library on its own - what the cards on the Library page are."""
    limit = _query_limit(query, None, 500)
    cards = [{
        "id": k["id"],
        "name": k["name"],
        "type": k.get("collection_type"),
        "items": int(k.get("item_count") or 0),
        "size_bytes": int(k.get("size_bytes") or 0),
        "size_human": _size_human(int(k.get("size_bytes") or 0)),
        "runtime_hours": round(float(k.get("hours") or 0), 1),
    } for k in stats.library_cards() if k["kind"] == "video"]     # video libraries, as the API always listed
    cards = api_query.apply(cards, *query.for_list("libraries"), limit)
    return {"count": len(cards), "libraries": cards}


@router.get("/top-items")
def most_watched(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The most watched titles over a period. A series' episodes add up."""
    days = _query_days(query)
    limit = _query_limit(query, 10, 200)
    kind = _query_kind(query)
    items = [{
        "title": r.get("label"),
        "is_series": bool(r.get("is_series")),
        "plays": int(r.get("plays") or 0),
        "watched_seconds": int(r.get("seconds") or 0),
        "watched_hours": round(float(r.get("hours") or 0), 2),
        "url": r.get("detail_url"),
    } for r in stats.top_items(days, _parse_query(query, "items", limit), kind)]
    return {
        "days": days,
        "kind": kind,
        "items": api_query.apply(items, *query.for_list("items"), limit),
    }


@router.get("/least-played")
def least_played(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """Files from the least played: zeros first, among them the biggest first.

    The play count is over the period, the last play over the whole history -
    with a zero in the period that is exactly the information a person wants.

    `order` is optional and changes only the order of titles with the same
    play count: `oldest` / `newest` by the date added instead of the size.
    Without it the order is as before - a dashboard built on the old answer
    does not change. An unknown value is a 400. `order` and `sort` at once too:
    each would ask for a different order.
    """
    days = _query_days(query, 365)
    limit = _query_limit(query, 25, 500)
    # Decimal MB are not cut (1.5 MB is 1.5 MB, not 1); a cap of 10 TB, so a
    # huge number fits into the database.
    min_bytes = round(api_query.number(query, "min_size_mb", 0, 0, 10_000_000) * 1_048_576)
    if "order" in query.values and query.sorts:
        raise api_query.bad_request("conflicting_parameters",
                                    "Use either 'order' or 'sort', not both.", "order")
    order = api_query.choice(query, "order", "size",
                             tuple(insights.LEAST_PLAYED_ORDERS))
    items = [{
        **_title(r),
        "plays": int(r.get("plays") or 0),
        "watched_seconds": int(r.get("watched_seconds") or 0),
        "last_played": r.get("last_played"),
    } for r in insights.least_played(days, _parse_query(query, "items", limit),
                                     min_bytes, order)]
    return {
        "days": days,
        # With `sort` the sort orders, not order - reporting "size" would lie.
        "order": None if query.sorts else order,
        "items": api_query.apply(items, *query.for_list("items"), limit),
    }


@router.get("/unwatched")
def unwatched(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """What was not played at all over the period - and how much space it takes.

    The most valuable number Jellyscope has: the share of the library nobody
    cares about. Titles added in the last days do not count, they have not had
    a chance yet.

    With a filter the count, the size and the share are computed only from the
    chosen rows (before the limit) - "how much space the unwatched takes in this folder".
    """
    days = _query_days(query, 365)
    limit = _query_limit(query, 25, 500)
    dead = insights.dead_storage(_parse_query(query, "items", limit), days)
    library_size = int(dead.get("library_bytes") or 0)
    selected = api_query.apply([_title(r) for r in dead.get("rows") or []],
                               *query.for_list("items"), None)
    if query.has_filter("items"):
        totals = _unused_summary(selected, library_size)
    else:
        size = int(dead.get("size_bytes") or 0)
        totals = {"count": int(dead.get("item_count") or 0), "size_bytes": size,
                  "size_human": _size_human(size),
                  "share_percent": round(float(dead.get("share") or 0), 1)}
    return {
        "days": days,
        "count": totals["count"],
        "size_bytes": totals["size_bytes"],
        "size_human": totals["size_human"],
        "library_bytes": library_size,
        "share_percent": totals["share_percent"],
        "items": selected if limit is None else selected[:limit],
    }


def _unused_summary(rows: list[dict[str, Any]], library_bytes: int) -> dict[str, Any]:
    """The count, the size and the share of the library from (filtered) rows."""
    size = sum(int(r.get("size_bytes") or 0) for r in rows)
    return {
        "count": len(rows),
        "size_bytes": size,
        "size_human": _size_human(size),
        "share_percent": round(size / library_bytes * 100, 1) if library_bytes else 0.0,
    }


@router.get("/users")
def viewers(token: dict[str, Any] = Depends(may_open_path),
            query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The viewers and how much they watched over the period.

    The names are here - just like on the Users page, and just like there a
    viewer's key or a reader's key with anonymising gets them hidden. The
    filter runs only over the hidden names, so the real name cannot be found with it.
    """
    days = _query_days(query)
    limit = _query_limit(query, None, 500)
    rows = [{
            "id": r.get("id"),
            "name": r.get("name"),
            "is_administrator": bool(r.get("is_administrator")),
            "plays": int(r.get("plays") or 0),
            "titles": int(r.get("item_count") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "transcoded_hours": round(float(r.get("transcoded_hours") or 0), 2),
            "last_seen": r.get("last_seen"),
        } for r in _for_owner(token, stats.user_table(days))]
    return {"days": days, "users": api_query.apply(rows, *query.for_list("users"), limit)}


def history_user(token: dict[str, Any], query: api_query.Query) -> str | None:
    """Whose plays a history address returns: the viewer asked for (`user`), a Jellyfin viewer's key only its own.

    One rule for every history of the API (playbacks, listens, readings). An empty `user=` the guide showed as
    "everyone" is taken as without it. With hidden names only a substitute id, as the API itself returns
    (`skryto-2`); the real id of another viewer finds nothing - otherwise it would tell who "Viewer 2" is.
    Another viewer's history for a viewer's key is a 403 - it used to be quietly rewritten to the own viewer
    and the tool believed it had someone else's data.
    """
    who = None if query.values.get("user") == "" else api_query.identifier(query, "user")
    who = access.viewer_from_address(who, token.get("account"))
    own = access.own_viewer(token.get("account"))
    if own and who and accounts.bare_id(who) != accounts.bare_id(own):
        raise ApiError(403, "own_history_only",
                       "This key sees only the history of its own account.", "user")
    return own or who


@router.get("/history")
def history(token: dict[str, Any] = Depends(may_open_path),
            query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The last plays - the newest first.

    The fields are listed, not `p.*`: a history row carries the address the
    person watched from too, and that does not belong into a summary for a
    dashboard. For the traffic over the network there is `/api/v1/bandwidth`,
    and that one without addresses.

    A viewer's key sees only its own history. Another `user` is a 403 - it
    used to be quietly rewritten to the own viewer and the tool believed it
    had someone else's data.
    """
    days = _query_days(query)
    limit = _query_limit(query, 50, 500)
    # The history listing shows everything, music listening too (like the
    # History page without a choice); "both" therefore means everything here,
    # not only watching.
    kind = _query_kind(query)
    kind = stats.KIND_ALL if kind == stats.KIND_BOTH else kind
    # The history filters by DAYS in the local time (the way the History page
    # does), not by moments - hence a date, not a timestamp.
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    user = history_user(token, query)
    # Music and books only with "who listened" and "who read", like on the History page.
    hidden = _hidden_kinds(token)
    filters, sorts = query.for_list("playbacks")
    if not (filters or sorts):
        rows = _for_owner(token, stats.history(limit=limit, user_id=user,
                                               kind=kind, since=since, hidden_kinds=hidden))
        plays = [_plays(r) for r in rows]
    else:
        # The whole history of the period in batches: in memory only what the
        # filter lets through. Without a sort the first `limit` matches are
        # enough - the order is given by the query.
        plays = []
        for batch in stats.history_for_api(user_id=user, kind=kind, since=since,
                                           conditions=_history_prefilter(filters), hidden_kinds=hidden):
            plays += api_query.apply([_plays(r) for r in _for_owner(token, batch)],
                                     filters, [], None)
            if not sorts and len(plays) >= limit:
                break
        plays = api_query.apply(plays, [], sorts, limit)
    return {"days": days, "count": len(plays), "playbacks": plays}


# History fields that can be pre-filtered in SQL, and their columns. The
# viewer's name and id and the device are NOT here: they are hidden only in
# Python (`_for_owner`), so a filter over them in SQL would run over the real
# name and give it away.
_HISTORY_COLUMNS = {
    "title": "p.item_name", "series": "p.series_name", "type": "p.item_type",
    "item_id": "p.item_id", "play_method": "p.play_method", "client": "p.client",
    "audio_language": "p.audio_language",
    "subtitle_language": "p.subtitle_language", "watched_seconds": "p.watched_seconds",
}
_COMPARISONS = {"equals": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _history_prefilter(filters: list[api_query.Filter]) -> list[tuple[str, list[Any]]]:
    """The history's filters translated into SQL as a pre-filter. Only a superset!

    The whole history of a period means on a big server moving hundreds of
    thousands of rows from the database only for Python to throw them away.
    Simple fields are therefore narrowed already in SQL. Python still decides
    exactly (`api_query.apply`), so SQL may let more through, never less:

    * text only for values in ASCII and only `equals`, `in`, `contains`,
      `starts_with`, `ends_with`; compared in lower case only in ASCII
      (`dialect.lower_ascii`) and rows with other than ASCII text always go
      through (`dialect.non_ascii`) - `casefold` in Python can do more than
      LOWER in the database (`Straße` finds `strasse`);
    * `%`, `_` and `!` in a value are ordinary characters in LIKE (ESCAPE '!' -
      a backslash would break the literal on PostgreSQL with
      standard_conforming_strings=off);
    * negations (`not`, `not_contains`) and `empty` stay with Python only.

    `is_transcode` copies the rule from `_plays` (starts with "transcode"
    whatever the case), not `stats.transcode_condition`: it must be a superset
    of what Python decides.
    """
    kind = db.current_dialect()

    def outside_ascii(column: str) -> str:
        return dialect.non_ascii(kind, column)

    conditions: list[tuple[str, list[Any]]] = []
    for flt in filters:
        if flt.field == "is_transcode":
            wants = flt.value if flt.operator == "equals" else not flt.value
            expr = (dialect.lower_ascii(kind, "COALESCE(p.play_method, '')")
                    + (" LIKE ?" if wants else " NOT LIKE ?"))
            conditions.append((f"({expr} OR {outside_ascii('p.play_method')})",
                               ["transcode%"]))
            continue
        column = _HISTORY_COLUMNS.get(flt.field)
        if not column:
            continue
        if flt.kind == api_query.NUMBER:
            if flt.operator == "in":
                conditions.append((f"{column} IN ({','.join('?' for _ in flt.value)})",
                                   list(flt.value)))
            elif flt.operator in _COMPARISONS:
                conditions.append((f"{column} {_COMPARISONS[flt.operator]} ?", [flt.value]))
            continue
        values = flt.value if flt.operator == "in" else (flt.value,)
        if (flt.operator not in ("equals", "in", "contains", "starts_with", "ends_with")
                or not all(str(h).isascii() for h in values)):
            continue
        lowered = [str(h).lower() for h in values]
        lowered_column = dialect.lower_ascii(kind, column)
        if flt.operator in ("equals", "in"):
            expr = f"{lowered_column} IN ({','.join('?' for _ in lowered)})"
            params = lowered
        else:
            bare = lowered[0].replace("!", "!!").replace("%", "!%").replace("_", "!_")
            pattern = {"contains": f"%{bare}%", "starts_with": f"{bare}%",
                       "ends_with": f"%{bare}"}[flt.operator]
            expr = f"{lowered_column} LIKE ? ESCAPE '!'"
            params = [pattern]
        conditions.append((f"({expr} OR {outside_ascii(column)})", params))
    return conditions


def _plays(r: dict[str, Any]) -> dict[str, Any]:
    """One play the way the API returns it."""
    return {
        "user": r.get("user_name"),
        "user_id": r.get("user_id"),
        "title": r.get("item_name"),
        "series": r.get("series_name"),
        "type": r.get("item_type"),
        "item_id": r.get("item_id"),
        "started_at": r.get("started_at"),
        "watched_seconds": int(r.get("watched_seconds") or 0),
        "play_method": r.get("play_method"),
        "is_transcode": str(r.get("play_method") or "").lower().startswith("transcode"),
        "client": r.get("client"),
        "device": r.get("device_name"),
        "audio_language": r.get("audio_language"),
        "subtitle_language": r.get("subtitle_language"),
    }


@router.get("/recently-added")
def recently_added(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """What came into the library last. The episodes of one batch are together.

    A series that got more episodes at once is here once - the item is the
    newest episode of the batch, so the id, path, season and episode belong to it.
    """
    limit = _query_limit(query, 18, 200)
    items = api_query.apply(
        [_title(r) for r in stats.recently_added(_parse_query(query, "items", limit))],
        *query.for_list("items"), limit)
    return {"count": len(items), "items": items}


@router.get("/play-methods")
def play_methods(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """Direct play against transcoding - and what people watch with.

    `limit` holds for both lists; without it the methods are whole and the
    clients 25 as before. All clients are counted and the filter goes over all of them.
    """
    days = _query_days(query)
    limit = _query_limit(query, None, 500)
    client_limit = 25 if limit is None else limit
    methods = [{
        "method": r.get("method"),
        "hours": round(float(r.get("hours") or 0), 2),
    } for r in stats.play_method_breakdown(days)]
    clients = [{
        "client": r.get("label"),
        "hours": round(float(r.get("hours") or 0), 2),
        "plays": int(r.get("plays") or 0),
    } for r in stats.client_breakdown(days, _parse_query(query, "clients", client_limit))]
    return {
        "days": days,
        "methods": api_query.apply(methods, *query.for_list("methods"), limit),
        "clients": api_query.apply(clients, *query.for_list("clients"), client_limit),
    }


@router.get("/insights")
def insights_report(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The Insights page in numbers - titles worth attention.

    Each list is computed whole and the filter with its prefix goes over all of
    it; `limit` comes only at the end and holds for all four.
    """
    days = _query_days(query)
    count = _query_limit(query, 15, 100)

    def fetch(name: str) -> int:
        return _parse_query(query, name, count)

    def listed(name: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return api_query.apply(items, *query.for_list(name), count)

    return {
        "days": days,
        "transcode_offenders": listed("transcode_offenders", [{
            "title": r.get("label"),
            "transcodes": int(r.get("transcodes") or 0),
            "reasons": r.get("reasons"),
            "video_codec": r.get("video_codec"),
            "audio_codec": r.get("audio_codec"),
            "resolution": _resolution(r),
            "url": r.get("detail_url"),
        } for r in insights.transcode_offenders(days, fetch("transcode_offenders"))]),
        "upgrade_candidates": listed("upgrade_candidates", [{
            "title": r.get("label"),
            "plays": int(r.get("plays") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "resolution": _resolution(r),
            "bitrate": r.get("bitrate"),
            "video_codec": r.get("video_codec"),
            "url": r.get("detail_url"),
        } for r in insights.upgrade_candidates(days, fetch("upgrade_candidates"))]),
        "oversized_rarely_watched": listed("oversized_rarely_watched", [{
            "id": r.get("id"),
            "title": r.get("label"),
            "type": r.get("type"),
            **_file(r),
            "plays": int(r.get("plays") or 0),
            "watched_hours": round(float(r.get("hours") or 0), 2),
            "size_bytes": r.get("size_bytes"),
            "size_human": _size_human(r.get("size_bytes") or 0),
            "gb_per_hour": round(float(r.get("gb_per_hour") or 0), 2),
            "resolution": _resolution(r),
        } for r in insights.oversized_rarely_watched(
            days, fetch("oversized_rarely_watched"))]),
        "never_finished": listed("never_finished", [{
            "title": r.get("label"),
            "attempts": int(r.get("attempts") or 0),
            "best_percent": round(float(r.get("best_percent") or 0), 1),
            "runtime_minutes": round(float(r.get("runtime_minutes") or 0)),
        } for r in insights.never_finished(days, fetch("never_finished"))]),
        "transcode_speed": _speed(insights.transcode_speeds(days)),
    }


def _speed(r: dict[str, Any]) -> dict[str, Any]:
    """The conversion speed for the API - the same numbers as the card on Insights."""
    def totals(s: dict[str, Any]) -> dict[str, Any]:
        return {
            "playbacks": s["playbacks"],
            "average_fps": s["average"],
            "slowest_fps": s["least"]["fps"],
            "slowest_title": s["least"]["label"],
            "fastest_fps": s["most"]["fps"],
            "fastest_title": s["most"]["label"],
            "fell_behind": s["too_slow"],
        }
    if not r.get("playbacks"):
        return {"playbacks": 0, "by_hardware": []}
    return {**totals(r),
            "by_hardware": [{"hardware": s["hw"] or "cpu", **totals(s)}
                            for s in r["by_hw"]]}


def _resolution(row: dict[str, Any]) -> str | None:
    """"1920x1080", or None when the file has no dimensions."""
    if row.get("width") and row.get("height"):
        return f"{row['width']}x{row['height']}"
    return None


@router.get("/bandwidth")
def traffic(token: dict[str, Any] = Depends(may_open_path),
            query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """How much data went out and where to - a summary, without single people's addresses.

    The filter narrows only the list of countries; the summary numbers are from all plays. Where from (home against
    internet, the countries) only with "...including where they watch from" (network_origin, 27. 11. 2026) - the card
    "Where they watch from" holds the same; without it null, and a filter on the countries is not applied (it would
    only tell what is there).
    """
    days = _query_days(query)
    where_from = token["role"] == access.ADMIN or access.sees(token["role"], "network_origin", token["account"])
    limit = _query_limit(query, None, 500)
    overview = stats.bandwidth_summary(days)
    origins = stats.watching_locations(days)["groups"]
    return {
        "days": days,
        "total_bytes": int(overview.get("byte_count") or 0),
        "total_human": _size_human(int(overview.get("byte_count") or 0)),
        "transcode_bytes": int(overview.get("byte_count_transcode") or 0),
        "transcode_share_percent": float(overview.get("transcode_share") or 0),
        "peak_mbit": round(float(overview.get("peak_mbit") or 0), 2),
        "peak_at": overview.get("peak_at"),
        "average_mbit": float(overview.get("average_mbit") or 0),
        "playbacks": int(overview.get("playbacks") or 0),
        # Home / from the internet / unknown from where - a summary, no addresses.
        "origin": None if not where_from else {
            "home": _origin(origins["at_home"]),
            "internet": _origin(origins["internet"]),
            "unknown": _origin(origins["unknown_origin"]),
        },
        "countries": None if not where_from else api_query.apply([{
            "country": z.get("label"),
            "code": z.get("code"),
            "plays": int(z.get("plays") or 0),
            "watched_seconds": int(z.get("seconds") or 0),
            "bytes": int(z.get("byte_count") or 0),
        } for z in stats.viewer_countries(days)], *query.for_list("countries"), limit),
    }


def _origin(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "plays": int(group.get("plays") or 0),
        "watched_seconds": int(group.get("seconds") or 0),
        "bytes": int(group.get("byte_count") or 0),
    }


@router.get("/item/{item_id}")
def title(item_id: str,
          query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """One title: the file, its versions and who watched it."""
    # An id from Jellyfin is hex (the demo's too), an imported one sometimes
    # with dashes - letters, digits, a dash, an underscore. Anything else is a
    # typo or an attempt and the answer is the same as for one that does not
    # exist: what exactly is wrong is not said. The id goes into the query as
    # a parameter, so this is not a defence against SQL, but against working
    # with nonsense at all.
    if not item_id or len(item_id) > 64 or not all(
            c.isalnum() or c in "-_" for c in item_id):
        raise ApiError(404, "not_found", "Item not found.")
    item = stats.item(item_id)
    if item is None:
        raise ApiError(404, "not_found", "Item not found.")
    totals = stats.item_playback_summary(item_id)
    return {
        **_title(item),
        "is_missing": bool(item.get("is_missing")),
        "container": item.get("container"),
        "bitrate": item.get("bitrate"),
        "runtime_seconds": int((item.get("runtime_ticks") or 0) / 10_000_000),
        "versions": [{
            "source_id": v.get("source_id"),
            "name": v.get("name"),
            "path": v.get("path"),
            "size_bytes": v.get("size_bytes"),
            "resolution": (f"{v.get('width')}x{v.get('height')}"
                           if v.get("width") and v.get("height") else None),
            "video_codec": v.get("video_codec"),
        } for v in stats.item_versions(item_id)],
        "plays": int(totals.get("plays") or 0),
        "watched_hours": round(float(totals.get("hours") or 0), 2),
        "viewers": int(totals.get("users") or 0),
        "last_played": totals.get("last_played"),
    }


@router.get("/summary")
def summary(token: dict[str, Any] = Depends(may_open_path),
            query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The watching over a period - what is at the top of the Overview.

    `active_now` (how much is being watched now) belongs to "Now playing" -
    without it it is missing from the answer, just like in /health and in the
    pages' panel.
    """
    days = _query_days(query)
    overview = stats.overview(days)
    watched = int(overview.get("watched_seconds") or 0)
    return {
        "days": days,
        "watched_seconds": watched,
        "watched_hours": round(watched / 3600, 2),
        "plays": int(overview.get("plays") or 0),
        "users": int(overview.get("users") or 0),
        "titles": int(overview.get("item_count") or 0),
        "transcode_share_percent": round(float(overview.get("transcode_share") or 0), 1),
        **({"active_now": stats.active_session_count()}
           if _may(token, "/api/v1/now-playing") else {}),
    }


@router.get("/now-playing")
def now_playing(token: dict[str, Any] = Depends(may_open_path),
                query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The plays running right now. An empty list is an answer too."""
    limit = _query_limit(query, None, 500)
    sessions = []
    rows = stats.active_sessions()
    if token["role"] != access.ADMIN:
        # Music and books only with "who listened" and "who read" (see _hidden_kinds).
        rows = access.visible_plays(rows, token.get("account"))
    for row in _for_owner(token, rows):
        sessions.append({
            "user": row.get("user_name"),
            "title": row.get("item_name"),
            "series": row.get("series_name"),
            "library": row.get("library_name"),
            "client": row.get("client"),
            "device": row.get("device_name"),
            "play_method": row.get("play_method"),
            "is_transcode": str(row.get("play_method") or "").startswith("Transcode"),
            "is_paused": bool(row.get("is_paused")),
            "audio_language": row.get("current_audio_language") or row.get("audio_language"),
            "bitrate": row.get("bitrate"),
            "position_seconds": int(row.get("position_seconds") or 0),
            "runtime_seconds": int(row.get("runtime_seconds") or 0),
            "started_at": row.get("started_at"),
        })
    sessions = api_query.apply(sessions, *query.for_list("sessions"), limit)
    return {"count": len(sessions), "sessions": sessions}


@router.get("/library")
def library(query: api_query.Query = Depends(request_query)) -> dict[str, Any]:
    """The library's size, the growth over the period and the free space.

    The same numbers as on the Library page - computed by the same functions,
    not by an own query. Two places that ask differently part ways sooner or later.
    """
    days = _query_days(query)
    state = stats.library_state()
    growth = stats.library_growth(days)
    free = scanner.free_space_now()
    return {
        "items": state["item_count"],
        "movies": state["movie_count"],
        "episodes": state["episode_count"],
        "size_bytes": state["size_bytes"],
        "size_human": _size_human(state["size_bytes"]),
        "items_without_size": int(stats.tech_coverage().get("without_size") or 0),
        "free_bytes": free,
        "growth": {
            "days": days,
            "enough_data": bool(growth.get("enough_data")),
            "added_bytes": int(growth.get("increase") or 0),
            "per_day_bytes": int(growth.get("daily") or 0),
            "days_until_full": growth.get("days_left"),
        },
    }


# The addresses of the other sections live in modules of their own (api.py does not grow); each brings its
# known parameters and lists and puts its routes on this router, with this module's dependencies.
from . import api_arr, api_books, api_library, api_music, api_seerr  # noqa: E402 - at the end: they use this module's dependencies

for _section in (api_music, api_books, api_library, api_arr, api_seerr):
    KNOWN_PARAMS.update(_section.PARAMS)
    LISTS.update(_section.LISTS)
    _section.install(sys.modules[__name__])

