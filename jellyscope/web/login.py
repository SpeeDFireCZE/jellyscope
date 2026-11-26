"""The first run, sign-in and sign-out.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import access, accounts, db, i18n, navigation
from ..jellyfin import JellyfinClient, JellyfinError, QUICK_TIMEOUT
from .base import (_flash, current_account, end_session, forget_secret, hold_secret, log, peek_secret,
                   start_session, templates)

router = APIRouter()

# Jellyfin forgets a Quick Connect code after ten minutes; the status ends then, whatever it answers.
QUICK_CODE_LIFETIME = 600
# The status asks Jellyfin at most once a second per code (the page asks every two seconds), so a
# replayed cookie cannot relay requests to Jellyfin as fast as it likes. The handle of a code -> when it last asked.
QUICK_POLL_SECONDS = 1.0
_quick_polls: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Sign-in
#
# The whole app is behind the sign-in - without an account you get nowhere but
# the sign-in page. The accounts are in the table `accounts` and are managed
# in Settings; they have nothing in common with Jellyfin's users.
# ---------------------------------------------------------------------------

# ---- the first run -----------------------------------------------------

def _setup_context(**extra: Any) -> dict[str, Any]:
    """The data for the opening screen. The language is chosen here already."""
    # `ui_languages` is a global variable of the templates already (see i18n.register),
    # so it is not passed here - otherwise two lists of languages would exist
    # in time and one of them would grow old.
    return {
        "error": None,
        "ui_language": i18n.current_language(),
        **extra,
    }


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    if accounts.any_exists():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", _setup_context())


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_again: str = Form(""),
    ui_language: str = Form(i18n.DEFAULT_LANGUAGE),
):
    # Were somebody to send the form a second time, accounts exist already - nobody
    # smuggles a second "first administrator" through here.
    if accounts.any_exists():
        return RedirectResponse("/login", status_code=303)

    # A fixed list - nothing else gets into the settings from the form.
    if ui_language not in i18n.LANGUAGES:
        ui_language = i18n.DEFAULT_LANGUAGE

    try:
        account_id = accounts.create(username, password, password_again, is_admin=True)
    except accounts.AccountError as exc:
        # The language is saved even on an error. Whoever switched to English and
        # got the password wrong is to get an English message - not a Czech page again.
        db.set_setting("ui_language", ui_language)
        # The message is translated EXPLICITLY into the form's language. The saved
        # setting cannot be relied on: at the very first run it still holds the
        # default and the error would come in it, even though the person has just
        # chosen another language.
        return templates.TemplateResponse(
            request, "setup.html",
            _setup_context(error=exc.translated(ui_language), username=username,
                           ui_language=ui_language),
            status_code=400,
        )

    db.set_setting("ui_language", ui_language)
    start_session(request, accounts.get(account_id) or {"id": account_id})
    _flash(request, i18n.translate("Account created. Welcome to Jellyscope.", ui_language),
           "success", translated=True)
    return RedirectResponse("/settings", status_code=303)


# ---- sign-in and sign-out ----------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not accounts.any_exists():
        return RedirectResponse("/setup", status_code=303)
    if current_account(request) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "jellyfin_login": access.login_enabled()})


async def _sign_in_through_jellyfin(name: str, password: str) -> dict[str, Any] | None:
    """Tries the name and the password against Jellyfin. None = not signed in.

    A second way in, not a replacement: the local accounts stay, because when
    Jellyfin is off or its address changed, one has to get in all the same.
    Hence it is tried only after them.

    A connection error is **not told apart from a wrong password**. The answer "the
    server does not respond" would tell a stranger there is a Jellyfin here,
    and above all would turn the sign-in page into a tool for probing whether
    somebody else's network is reachable. It goes to the log.

    An empty password is refused before Jellyfin is asked. Jellyfin lets a user without a
    password in with an empty one, and the question comes from this server's address, so
    Jellyfin's limits on remote access do not hold here: a profile without a password would
    open Jellyscope to anybody who knows its name. Such a user signs in with a code.
    """
    if not access.login_enabled() or not password:
        return None

    url, api_key = db.jellyfin_connection()
    if not url or not api_key:
        return None

    try:
        async with JellyfinClient(url, api_key, timeout=QUICK_TIMEOUT) as jf:
            details = await jf.verify_login(name, password)
    except JellyfinError as error:
        log.warning("could not verify the Jellyfin sign-in: %s", error)
        return None

    if details is None:
        return None
    if details["is_disabled"]:
        log.warning("account %s is disabled in Jellyfin, not letting it in",
                    details["name"])
        return None
    return accounts.from_jellyfin(details)


def _brake_name(method: str, username: str) -> str:
    """The name the brake counts an attempt under: with the door. A local account and a user of Jellyfin may share
    a name - a success at one door must not forget the guesses at the other."""
    return f"{'jellyfin' if method == 'jellyfin' else 'local'}:{username}"


@router.post("/login")
async def login_submit(
    request: Request, username: str = Form(""), password: str = Form(""),
    method: str = Form("local"),
):
    # The brake against guessing passwords. The key is the address the attempt came
    # from - not the user name: by the name somebody else's account could easily be
    # locked and its owner pushed out.
    address = _client_address(request)
    remaining = accounts.block_remaining(address)
    if remaining:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _block_message(remaining), "username": username,
             "jellyfin_login": access.login_enabled()},
            status_code=429,
        )

    # Counted before the password is checked and with nothing awaited since the check above,
    # so requests sent at once cannot all pass it while the first ones wait for the answer.
    # A success forgets the attempt again.
    block = accounts.begin_attempt(address, _brake_name(method, username))

    # The door a person chose is the one knocked on. So the password of a local
    # account never leaves for Jellyfin and a password from Jellyfin is not compared
    # with the local hashes.
    if block:
        account = None
    elif method == "jellyfin":
        account = await _sign_in_through_jellyfin(username, password)
    else:
        # In a thread: the hash takes a quarter of a second and would hold up every other request.
        account = await asyncio.to_thread(accounts.authenticate, username, password)
    if account is None:
        block = block or accounts.attempt_failed(address)
        if block:
            log.warning("sign-in from %s blocked (level %s, %s)", address,
                        block["level"],
                        "permanent" if block["permanent"] else f"{block['seconds']} s")
            return templates.TemplateResponse(
                request, "login.html",
                {"error": _block_message(-1 if block["permanent"]
                                         else block["seconds"]),
                 "username": username,
                 "jellyfin_login": access.login_enabled()},
                status_code=429,
            )
        # The door from a fixed pair, not the form's text, and no name: a password typed
        # into the name field must not end up in the log.
        log.warning("sign-in failed from %s (%s)", address, "jellyfin" if method == "jellyfin" else "local")
        # On purpose it is not said whether the name or the password was wrong. Were
        # they told apart, the list of accounts could be found out this way.
        return templates.TemplateResponse(
            request, "login.html",
            {"error": i18n.translate("Wrong username or password."),
             "username": username,
             "jellyfin_login": access.login_enabled()},
            status_code=401,
        )

    accounts.forget_failures(address, _brake_name(method, username))

    start_session(request, account)
    return RedirectResponse(_landing_after_login(account), status_code=303)


def _landing_after_login(account: dict[str, Any]) -> str:
    """Where signing in with a password lands: the default page (navigation.default_page_url).

    The same rule as `/`, the logo and signing in with a code (which goes to /): the page the
    administrator chose, or the first one this account can open - never "you may not".
    """
    return navigation.default_page_url(account)


@router.post("/login/quick")
async def login_quick(request: Request):
    """Starts signing in with a code. No password is entered anywhere.

    The secret the sign-in is finished with stays **in the server's memory**
    (base.hold_secret) - the session cookie carries only a handle to it, as the
    cookie is signed, not encrypted. Only a six-digit code goes to the browser,
    and that by itself lets nobody in anywhere - until a person confirms it in
    their Jellyfin where they are signed in already, it is just a number.
    """
    if current_account(request) is not None:
        return RedirectResponse("/", status_code=303)
    if not access.login_enabled():
        return _login_error(request, "Signing in through Jellyfin is not switched on.")

    # The same brake as with a password. Without it an address blocked for guessing
    # passwords would pass through this second gate - and anybody could keep
    # creating codes in Jellyfin. Every start counts as an attempt: a code nobody
    # confirms is an unfinished attempt. A successful sign-in with a code forgets
    # these starts, not the wrong passwords (see login_quick_status). Eight codes
    # a minute from one address is not a person who wants to sign in.
    address = _client_address(request)
    remaining = accounts.block_remaining(address)
    if remaining:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _block_message(remaining),
             "jellyfin_login": access.login_enabled()},
            status_code=429)
    block = accounts.count_failure(address)
    if block:
        log.warning("sign-in with a code from %s blocked (level %s)",
                    address, block["level"])
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _block_message(-1 if block["permanent"]
                                     else block["seconds"]),
             "jellyfin_login": access.login_enabled()},
            status_code=429)

    url, api_key = db.jellyfin_connection()
    if not url or not api_key:
        return _login_error(request, "Jellyfin is not set up.")

    try:
        async with JellyfinClient(url, api_key, timeout=QUICK_TIMEOUT) as jf:
            if not await jf.quick_connect_enabled():
                return _login_error(
                    request,
                    "Quick Connect is switched off on the Jellyfin "
                    "server. It is enabled in its Dashboard.")
            started = await jf.quick_connect_start()
    except JellyfinError as error:
        log.warning("could not start quick connect: %s", error)
        return _login_error(request, "Jellyfin did not answer. Try again in a moment.")

    if not started:
        return _login_error(request, "Quick Connect could not be started.")

    # The secret goes neither into the page nor into the readable cookie: whoever has it signs in with it. It waits
    # in the server's memory as long as Jellyfin keeps the code; the session carries its handle. A code started
    # before in this browser ends.
    forget_secret(request.session.get("quick_handle"))
    request.session["quick_handle"] = hold_secret(started["secret"], QUICK_CODE_LIFETIME)
    request.session["quick_started"] = int(time.time())
    return templates.TemplateResponse(
        request, "login_quick.html", {"code_value": started["code"]})


@router.get("/login/quick/status")
async def login_quick_status(request: Request):
    """Asks Jellyfin whether somebody confirmed the code already.

    The page asks here every second. The answer is poor on purpose - only
    "not yet" or "done": more would tell what accounts there are on the
    server.
    """
    # An unknown handle (the app restarted, the code ended) ends the code like any other end: it starts again.
    handle = request.session.get("quick_handle")
    secret = peek_secret(handle) or ""
    if not secret or not access.login_enabled():
        return _end_quick(request)
    started = request.session.get("quick_started")
    if not isinstance(started, int) or time.time() - started > QUICK_CODE_LIFETIME:
        return _end_quick(request)
    if not _may_ask_jellyfin(handle):
        return {"done": False}

    url, api_key = db.jellyfin_connection()
    try:
        async with JellyfinClient(url, api_key, timeout=QUICK_TIMEOUT) as jf:
            confirmed = await jf.quick_connect_confirmed(secret)
            if confirmed is None:
                return _end_quick(request)
            if not confirmed:
                return {"done": False}
            details = await jf.quick_connect_finish(secret)
    except JellyfinError as error:
        log.warning("quick connect: the status request failed: %s", error)
        return {"done": False}

    if not details or details.get("is_disabled"):
        return _end_quick(request)

    forget_secret(handle)     # a copy of the cookie signs nobody in again
    account = accounts.from_jellyfin(details)
    start_session(request, account)
    accounts.forget_failures(_client_address(request))     # the starts of codes, not wrong passwords
    log.info("signed in through Quick Connect: %s", details.get("name"))
    return {"done": True}


def _end_quick(request: Request) -> dict[str, bool]:
    """The code is no longer valid: the secret leaves the server's memory, its handle the session, and the page says so.

    An old copy of the cookie carries the handle only, and that leads nowhere now.
    """
    forget_secret(request.session.pop("quick_handle", None))
    request.session.pop("quick_started", None)
    return {"done": False, "end": True}


def _may_ask_jellyfin(handle: str) -> bool:
    """May the status ask Jellyfin about this code now? At most once in QUICK_POLL_SECONDS."""
    now = time.monotonic()
    if now - _quick_polls.get(handle, now - QUICK_POLL_SECONDS) < QUICK_POLL_SECONDS:
        return False
    _quick_polls.pop(handle, None)
    _quick_polls[handle] = now
    while len(_quick_polls) > 1000:        # the oldest codes first, so memory stays small
        del _quick_polls[next(iter(_quick_polls))]
    return True


def _login_error(request: Request, message: str):
    """The sign-in page with an explanation why it did not work."""
    return templates.TemplateResponse(
        request, "login.html",
        {"error": i18n.translate(message),
         "jellyfin_login": access.login_enabled()},
        status_code=400)


@router.post("/logout")
def logout(request: Request):
    end_session(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block_message(remaining: int) -> str:
    """The text at a blocked sign-in.

    On purpose it does not say which block in a row it is nor after how many
    attempts it comes - those details help only whoever guesses.

    It is translated here, not in the template: the time goes into the sentence only
    after the translation, otherwise the finished sentence could not be found in the
    dictionary.
    """
    if remaining < 0:
        return i18n.translate("Signing in from this address is blocked. "
                              "An administrator can lift it in Settings.")
    minutes = remaining // 60
    if minutes >= 1:
        return i18n.translate("Too many attempts. Try again in {n} min.").format(n=minutes)
    return i18n.translate("Too many attempts. Try again in {n} s.").format(
        n=max(1, remaining))


def _client_address(request: Request) -> str:
    """Where the request came from. The key of the sign-in brake and the IP in the
    record of API key usage.

    The address is taken from `request.client` and nowhere else is looked at -
    although it used to be the other way round here.

    Behind a reverse proxy **uvicorn itself** fills in the real address: in
    run.py it is started with `proxy_headers` and with the list of trusted proxies
    (FORWARDED_ALLOW_IPS). It reads the header `X-Forwarded-For` only when
    somebody from that list sent it.

    Originally there was a second, own version of the same here - only it asked just
    "is a proxy set at all?" and then trusted the header from anybody. Whoever
    reached the app directly (HOST=0.0.0.0 with no proxy in front) sent
    `X-Forwarded-For` themselves and looked like somebody else at every password
    attempt - the brake against guessing passwords stopped holding.
    """
    return request.client.host if request.client else "?"
