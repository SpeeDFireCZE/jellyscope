"""My account: the page about one account, the image, the language, the API key and the password.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import access, accounts, api, api_query, db, i18n
from ..jellyfin import JellyfinClient, QUICK_TIMEOUT
from .base import (
    _context,
    _flash,
    _form_number,
    _new_key,
    _no_cache,
    account_image_tag,
    log,
    require_login,
    templates,
)
from .login import _block_message, _client_address

router = APIRouter()


# ---------------------------------------------------------------------------
# My account - a page about one account, not a shrunk Settings
# ---------------------------------------------------------------------------
#
# The account is always taken from the session, never from a form: nothing of
# somebody else's can be changed from here, even if a foreign id were slipped into a form.

@router.get("/account", response_class=HTMLResponse)
def my_account(request: Request, account: dict[str, Any] = Depends(require_login)):
    paths = api.allowed_paths(account)
    fresh = _new_key(request, "new_account_key")
    response = templates.TemplateResponse(request, "my_account.html", _context(
        request, account,
        account_language=account.get("ui_language") or "",
        from_jellyfin=bool(account.get("jellyfin_user_id")),
        may_have_key=access.may_have_key(account),
        api_keys=api.list_tokens(account["id"]),
        # The window "How to use it" (_api_guide.html) shows only these addresses.
        api_guide_paths=paths,
        api_guide_fields=api.path_fields(paths),
        api_guide_operators=api_query.OPERATORS,
        # The example in the guide is to work at once: the first address with data
        # the account may open (not a fixed /summary - a viewer may not by default).
        api_sample=next((a for a in paths if a != "/api/v1/" and "{" not in a),
                        "/api/v1/"),
        # A new key only once, right after the redirect - in the row of its key
        # (see bunka_klice). Under a name of its own in the session: a key made
        # here is not to show in Settings and the other way round.
        fresh_key=fresh,
    ))
    return _no_cache(response) if fresh else response


def _no_image() -> Response:
    """There is no image (or Jellyfin does not answer) - the letter stays.

    A bare 404 without an error page: <img> would not show it anyway. And for five
    minutes the browser does not ask again, so that not every page tries a Jellyfin
    that is down right now.
    """
    return Response(status_code=404, headers={"Cache-Control": "private, max-age=300"})


@router.get("/account/image")
async def my_image(request: Request, account: dict[str, Any] = Depends(require_login)):
    """The profile image of the signed-in account from Jellyfin - only one's own.

    There is no id in the address: whoever asks gets their own image, nobody
    else's - extra parameters are ignored. `?v=` is only the tag, so that a new
    image in Jellyfin means a new address.

    It is not saved on the server's disk (saves space). It is downloaded through the
    server, so that neither Jellyfin's address nor the key gets into the page. The
    browser keeps it, but asks before every use (no-cache + ETag): the server
    checks the sign-in and returns 304 without a query to Jellyfin. So on a shared
    computer nobody else gets somebody's photo from the cache.
    """
    viewer = str(account.get("jellyfin_user_id") or "")
    tag = await asyncio.to_thread(account_image_tag, account) if viewer else ""
    if not tag:
        return _no_image()
    headers = {"Cache-Control": "private, no-cache", "ETag": f'"{tag}"'}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    connection = await asyncio.to_thread(db.jellyfin_connection)
    # A short limit: a person at a page waits for it, not a background task.
    async with JellyfinClient(*connection, timeout=QUICK_TIMEOUT) as client:
        result = await client.user_image(viewer, tag)
    if result is None:
        return _no_image()
    content, media_type = result
    return Response(content, media_type=media_type, headers=headers)


@router.post("/account/language")
def my_account_language(request: Request, ui_language: str = Form(""),
                        account: dict[str, Any] = Depends(require_login)):
    """The interface language of the account. Empty = the server's language."""
    if ui_language and ui_language not in i18n.LANGUAGES:
        _flash(request, "That language is not available.", "error")
        return RedirectResponse("/account", status_code=303)
    accounts.set_language(account["id"], ui_language or None)
    # The message is in the newly chosen language already - the page it returns to
    # will be in it too. It is translated straight away (`_flash` leaves a finished sentence alone).
    _flash(request, i18n.translate("Language saved.", ui_language or i18n.server_language()),
           "success", translated=True)
    return RedirectResponse("/account", status_code=303)


@router.post("/account/event-motion")
def my_account_event_motion(request: Request, motion: str = Form("1"),
                            account: dict[str, Any] = Depends(require_login)):
    """Does the seasonal decoration move for me? Only an explicit "0" switches it off."""
    accounts.set_event_motion(account["id"], motion != "0")
    _flash(request, "Saved.", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/key")
def my_account_key(request: Request, name: str = Form(""),
                   account: dict[str, Any] = Depends(require_login)):
    """An API key of one's own - only with the administrator's permission.

    The key belongs to the account from the session, not from the form, and may
    exactly what the account itself may (see api.verify_token and api.may_open_path).
    """
    if not access.may_have_key(account):
        _flash(request, "The admin has not allowed you your own API keys.", "error")
        return RedirectResponse("/account#keys", status_code=303)
    result = api.create_token(name, account["id"])
    request.session["new_account_key"] = {"id": result["id"], "token": result["token"]}
    _flash(request, 'Key "{user}" created.', "success", user=result["display_name"])
    return RedirectResponse("/account#keys", status_code=303)


@router.post("/account/key/revoke")
def my_account_key_revoke(request: Request, token_id: str = Form(""),
                          account: dict[str, Any] = Depends(require_login)):
    """Revokes one's own key. Even without the permission - an old key has to be disposable.

    The id from the form is checked against the keys of the account from the session:
    somebody else's key cannot be revoked this way, whatever is written into the form.
    """
    number = _form_number(token_id)
    own = {key["id"] for key in api.list_tokens(account["id"])}
    name = api.revoke_token(number) if number in own else ""
    if name:
        _flash(request, 'Key "{user}" no longer works.', "success", user=name)
    else:
        _flash(request, "There is no such key here.", "info")
    return RedirectResponse("/account#keys", status_code=303)


@router.post("/account/password")
def my_account_password(request: Request, current_password: str = Form(""),
                        password: str = Form(""), password_again: str = Form(""),
                        account: dict[str, Any] = Depends(require_login)):
    """One's own password, only with the current password. A Jellyfin account has none here.

    The brake against guessing is the same as at sign-in and with the same counter
    (the client's address). Without it a signed-in session - say in somebody else's
    browser - would be an unlimited oracle for the password, and every attempt on
    top costs a quarter of a second of PBKDF2.
    """
    back = RedirectResponse("/account", status_code=303)
    address = _client_address(request)
    remaining = accounts.block_remaining(address)
    if remaining:
        _flash(request, _block_message(remaining), "error")
        return back
    try:
        accounts.change_own_password(account["id"], current_password,
                                     password, password_again)
    except accounts.WrongCurrentPassword as exc:
        block = accounts.count_failure(address)
        if block:
            log.warning("password change from %s blocked (level %s)", address, block["level"])
            _flash(request, _block_message(-1 if block["permanent"]
                                           else block["seconds"]), "error")
        else:
            _flash(request, exc.translated(), "error")
        return back
    except accounts.AccountError as exc:
        _flash(request, exc.translated(), "error")
        return back
    accounts.forget_failures(address)
    # This session stays (a new fingerprint), the account's other sessions end -
    # see current_account.
    request.session["fingerprint"] = accounts.session_fingerprint(accounts.get(account["id"]) or {})
    _flash(request, "Password changed.", "success")
    return back
