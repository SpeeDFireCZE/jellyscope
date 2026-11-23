"""Accounts, permissions, readers, viewers, address blocks and API keys.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import access, accounts, api, db
from ..base import _flash, _form_number, log, require_admin, require_login

router = APIRouter()


def _save_tree(form: Any, who: str) -> None:
    """Saves the ticked tree of one group.

    It is read from the form directly, not through named parameters: areas
    keep coming and signing for each separately means one gets
    forgotten - and that one can then only be switched off, not on.
    """
    db.set_setting(f"{who}_anonymise",
                   "1" if form.get("anonymise") else "0")
    for area in access.AREAS:
        db.set_setting(access.area_key(who, area),
                       "1" if form.get(f"sees_{area.key}") else "0")


@router.post("/settings/accounts/keys")
def settings_keys(request: Request, reader_may_have_key: str = Form(""),
                  viewer_may_have_key: str = Form(""),
                  account: dict[str, Any] = Depends(require_admin)):
    """Who may make an API key of their own - the groups' default.

    The browser does not send an unticked box at all, hence empty = no.
    The exceptions of single accounts (the Permissions window) keep holding.
    """
    for who, value in ((access.READER, reader_may_have_key),
                       (access.VIEWER, viewer_may_have_key)):
        db.set_setting(access.MAY_HAVE_KEY_SETTING.format(who=who), "1" if value else "0")
    db.forget_settings()
    _flash(request, "Saved. Accounts with their own exception are not affected.", "success")
    return RedirectResponse("/settings?section=accounts#keys", status_code=303)


# The choice "Own API keys" in the Permissions window -> the value of the column accounts.may_have_key.
KEY_OPTIONS = {"group": None, "yes": "1", "no": "0"}


@router.post("/settings/accounts/permissions")
async def settings_account_permissions(request: Request,
                                       account: dict[str, Any] = Depends(require_admin)):
    """The own permissions of one account - or the way back to the group.

    The tick boxes are read from the form directly, for the same reason as
    with the groups (see `_save_tree`). An administrator's account has no own
    permissions: the administrator sees everything and a tree would only confuse.
    """
    form = await request.form()
    try:
        account_id = int(str(form.get("account_id") or 0))
    except ValueError:
        account_id = 0
    target = accounts.get(account_id)
    if target is None or target.get("is_admin"):
        _flash(request, "This account has no rights of its own.", "error")
        return RedirectResponse("/settings?section=accounts", status_code=303)

    # The own API key is saved in both modes - it is another question than
    # the tree of pages. A form without that choice (an older page in an open
    # tab) leaves the exception as it was.
    key_choice = str(form.get("may_have_key") or "")
    if key_choice in KEY_OPTIONS:
        accounts.save_may_have_key(account_id, KEY_OPTIONS[key_choice])

    if str(form.get("mode") or "") == "custom":
        permissions = access.pack_permissions(
            (area.key for area in access.AREAS
             if form.get(f"sees_{area.key}")),
            bool(form.get("anonymise")))
        accounts.save_permissions(account_id, permissions)
        _flash(request, "Saved. The account {user} has rights of its own; the group settings no longer apply to it.",
               "success", user=target["username"])
    else:
        accounts.save_permissions(account_id, None)
        _flash(request, "Saved. The account {user} follows its group settings again.",
               "success", user=target["username"])
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/readers")
async def settings_readers(request: Request,
                           account: dict[str, Any] = Depends(require_admin)):
    """What the local reader accounts see."""
    form = await request.form()
    _save_tree(form, access.READER)
    db.forget_settings()
    _flash(request, "Saved. This is what reader accounts see.",
           "success")
    return RedirectResponse("/settings?section=accounts#readers",
                            status_code=303)


@router.post("/settings/viewers")
async def settings_viewers(request: Request,
                           account: dict[str, Any] = Depends(require_admin)):
    """Saves who from Jellyfin may come in and what they see.

    The tick boxes are read from the form directly, not through named
    parameters: areas keep coming and signing for each separately means
    one gets forgotten - and that one can then only be switched off, not on.
    """
    form = await request.form()
    db.set_setting(access.LOGIN_KEY,
                   "1" if form.get("jellyfin_login") else "0")
    _save_tree(form, access.VIEWER)
    db.forget_settings()

    if not access.login_enabled():
        _flash(request, "Saved. Jellyfin accounts cannot come in; only local "
                        "accounts can.", "success")
    else:
        _flash(request, "Saved. Viewers from Jellyfin sign in with their own password.",
               "success")
    return RedirectResponse("/settings?section=jellyfin#viewers",
                            status_code=303)


@router.post("/settings/blocks/unblock")
def blocks_unblock(request: Request, ip: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Lifts the sign-in block of one address."""
    if accounts.unblock(ip.strip()):
        _flash(request, "Address {ip} has been unblocked.", "success", ip=ip.strip())
    else:
        _flash(request, "There is no such block in the list.", "warning")
    return RedirectResponse("/settings?section=blocks", status_code=303)


@router.post("/settings/blocks/permanent")
def blocks_permanent(request: Request, ip: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Blocks an address permanently - until the administrator lets it in."""
    address = ip.strip()
    if not address:
        _flash(request, "The address is missing.", "error")
    else:
        accounts.block_permanently(address)
        _flash(request, "Address {ip} has been blocked permanently.", "success",
               ip=address)
    return RedirectResponse("/settings?section=blocks", status_code=303)


# ---------------------------------------------------------------------------
# Managing accounts
#
# Note that every action has an address of its own and is sent by POST.
# Deleting through GET (say by the link /delete?id=3) is a classic mistake: the
# browser may load such an address by itself, say when preloading links,
# and the account vanishes without anybody clicking anything.
# ---------------------------------------------------------------------------

@router.post("/settings/accounts/create")
def account_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_again: str = Form(""),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.create(username, password, password_again, is_admin=bool(is_admin))
        _flash(request, "Account '{user}' created.", "success", user=username)
    except accounts.AccountError as exc:
        _flash(request, exc.translated(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/accounts/password")
def account_password(
    request: Request,
    account_id: int = Form(...),
    password: str = Form(""),
    password_again: str = Form(""),
    account: dict[str, Any] = Depends(require_login),
):
    """Changing the password of another account (the administrator).

    One's own password does not belong here: it is changed on the My account page,
    where the current password is asked for. Here it is not - the administrator
    helps this way whoever forgot the password - so were one's own allowed too, the
    demand for the current password would be bypassed (and an administrator from
    Jellyfin would set a local password).
    """
    if account_id == account["id"]:
        _flash(request, "Your own password is changed on the My account page.", "info")
        return RedirectResponse("/account", status_code=303)
    if not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Only an administrator may change someone else's password.")

    try:
        accounts.set_password(account_id, password, password_again)
        _flash(request, "Password changed.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.translated(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@router.post("/settings/accounts/role")
def account_role(
    request: Request,
    account_id: int = Form(...),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.set_admin(account_id, bool(is_admin))
        _flash(request, "Permissions changed.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.translated(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


def _key_tree(areas: Any, history: bool, anonymisation: bool,
              everything: bool) -> dict[str, Any]:
    """The permissions of a root key for the page: the state of the Permissions window and the list for the table.

    access.tree draws the tree the same as for an account with own permissions -
    only from an account that exists just for this moment.
    """
    # Only areas a key can use (api.key_areas, the same rule as when saving) - the tree and the
    # summary in the table alike, so neither names a right the Rights window would drop.
    usable = api.key_areas()
    enabled = set(areas) & usable
    tree = [row for row in access.tree(access.READER, {
                access.ACCESS_COLUMN: access.pack_permissions(enabled, anonymisation)})
            if row["key"] in usable]
    return {
        "all": everything,
        "history": history,
        "anonymizace": anonymisation,
        "tree": tree,
        "areas": [area for area in access.AREAS if area.key in enabled],
    }


def _root_key_permissions(permissions: str | None) -> dict[str, Any]:
    """What one root key may (the column Type and permissions, the Permissions window).

    With "everything" the whole tree is ticked: that is exactly what the key may, and
    whoever switches to "only chosen" takes away from what holds already - not starting from zero.
    """
    unpacked = api.key_permissions(permissions)
    if unpacked is None:
        return _key_tree(access.BY_KEY, history=True, anonymisation=False, everything=True)
    return _key_tree(unpacked["areas"], unpacked["history"],
                     unpacked["anonymise"], everything=False)


def _key_permissions_from_form(form: Any) -> tuple[str | None, str]:
    """The permissions of a root key from the form: (permissions, error). An empty error = fine.

    `mode` is "all" (permissions NULL - everything, areas added later too),
    or "selected". Anything else - also a form without it from an older page
    in an open tab - is an error: guessing what the administrator meant would mean
    giving a key to the data more than was wanted. Unknown areas and areas a key cannot use (a form
    from an older page) are dropped by api.pack_key_permissions.
    """
    mode = str(form.get("mode") or "")
    if mode == "all":
        return None, ""
    if mode != "selected":
        return None, "Cannot tell what the key should be allowed to do. Reload the page and choose it."
    areas = [area.key for area in access.AREAS
             if form.get(f"sees_{area.key}")]
    history = bool(form.get("history"))
    if not areas and not history:
        return None, "Tick what the key may do - at least one area or the playback history."
    permissions = api.pack_key_permissions(areas, bool(form.get("anonymise")), history)
    # Nothing left that the API opens (only "...who watched" without the Library, or an older
    # form with only areas the API has no address for): such a key could do nothing and nobody
    # would know why.
    if not api.root_key_paths(permissions):
        return None, ("The ticked areas open nothing in the API. Add an area that has "
                      "an API address (for example the Library or Users), or the "
                      "playback history.")
    return permissions, ""


@router.post("/settings/api/token")
async def api_token_new(request: Request,
                        account: dict[str, Any] = Depends(require_admin)):
    """Makes a root key with the permissions from the form and shows it once.

    A root key belongs to no account - not even to the administrator who made it. It may
    only what the administrator sets here, and changing or deleting the administrator's
    account does not affect it. A key of one's own (with the account's rights) is made in My account.
    """
    form = await request.form()
    permissions, error = _key_permissions_from_form(form)
    if error:
        _flash(request, error, "error")
        return RedirectResponse("/settings?section=api", status_code=303)
    # The name only as text: a file sent under this field would otherwise
    # become the key's name ("UploadFile(...)").
    name = form.get("name")
    result = api.create_token(name if isinstance(name, str) else "", None, permissions)
    log.info("Admin %s created root key %s (%s)", account["username"],
             result["name"] or "-", api.permissions_for_log(permissions))
    # Into the session, not into the address: the address shows in the proxy's log and in the
    # browser's history, and this is the only moment the token is readable.
    request.session["new_api_token"] = {"id": result["id"], "token": result["token"]}
    _flash(request, 'Key "{user}" created.', "success",
           user=result["display_name"])
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/permissions")
async def api_token_permissions(request: Request,
                                account: dict[str, Any] = Depends(require_admin)):
    """Changes the permissions of a root key. It holds from the next query with it.

    Only a root key: a user's key follows the rights of its account and cannot be
    changed this way (api.save_key_permissions refuses it). Who, what and how it was
    before goes to the app's log - the table of keys does not keep it.
    """
    form = await request.form()
    permissions, error = _key_permissions_from_form(form)
    if error:
        _flash(request, error, "error")
        return RedirectResponse("/settings?section=api", status_code=303)
    number = _form_number(form.get("token_id"))
    change = api.save_key_permissions(number, permissions) if number else None
    if change is None:
        _flash(request, "There is no such root key. A user key follows the rights of its account.",
               "error")
        return RedirectResponse("/settings?section=api", status_code=303)
    log.info("Admin %s changed the rights of root key %s: %s -> %s", account["username"],
             change["name"] or "-", api.permissions_for_log(change["before"]),
             api.permissions_for_log(permissions))
    _flash(request, 'Rights of the key "{user}" saved. They apply from the next request.',
           "success", user=change["display_name"])
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/revoke")
def api_token_revoke(request: Request, token_id: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Revokes one token - somebody else's too. The others are not touched."""
    number = _form_number(token_id)
    name = api.revoke_token(number) if number else ""
    if name:
        _flash(request, 'Key "{user}" no longer works.', "success",
               user=name)
    else:
        _flash(request, "There is no such key here.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/disable")
def api_token_disable(request: Request, token_id: str = Form(""),
                      account: dict[str, Any] = Depends(require_admin)):
    """Disables a key, but does not delete it.

    For the moment a key is suspected to have leaked: it stops working at once,
    but stays with its usage records, so one can find out who asked with it and
    from where - and when it was a false alarm, enable it again.
    """
    number = _form_number(token_id)
    name = api.disable_token(number) if number else ""
    if name:
        _flash(request, 'Key "{user}" is disabled. Until you enable it, the API returns nothing to it.',
               "success", user=name)
    else:
        _flash(request, "There is no such key here.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/api/token/enable")
def api_token_enable(request: Request, token_id: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Lets a disabled key to the data again."""
    number = _form_number(token_id)
    name = api.enable_token(number) if number else ""
    if name:
        _flash(request, 'Key "{user}" works again.', "success", user=name)
    else:
        _flash(request, "There is no such key here.", "info")
    return RedirectResponse("/settings?section=api", status_code=303)


@router.post("/settings/accounts/delete")
def account_delete(
    request: Request,
    account_id: int = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    if account_id == account["id"]:
        _flash(request, "You cannot delete your own account.", "warning")
        return RedirectResponse("/settings?section=accounts", status_code=303)

    try:
        accounts.delete(account_id)
        _flash(request, "Account deleted.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.translated(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)
