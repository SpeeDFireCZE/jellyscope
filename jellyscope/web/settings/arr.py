"""Settings → *Arr: saving, the connection test, synchronisation and deleting downloaded data.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py).
The routes are the same for all the apps of the *Arr page: what each can do and where it
saves is told by the list of apps (jellyscope/arr/registry.py) - here is only the form.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ... import db, i18n, services, tasks
from ...arr import registry
from ..base import _flash, log, require_admin
from .common import _draft_clear, _draft_read, _draft_save

router = APIRouter()

# The warning after an address change of an app with downloaded data (a translation key - one string).
ADDRESS_CHANGE = "The {name} address changed and the downloaded history is from the old address. If it is another instance, use Delete downloaded data, otherwise the two histories mix."


def draft_name(app: str) -> str:
    """The name the app's draft form is held under (see _draft_save)."""
    return f"arr_{app}"


@router.post("/settings/arr/{app}")
async def settings_arr(
    request: Request,
    app: str,
    enabled: str = Form(""),
    url: str = Form(""),
    api_key: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Saves or tests one app - by the button the form was sent with.

    Both buttons are in the same form, so the test works with what is
    filled in, not with what is saved (the same as with Jellyfin). After the test
    what was filled in is held as a draft: the form is drawn again with it
    and the key that was tried holds for the following Save too - it does not go
    back into the page, so a person would otherwise have to type it again.
    """
    if app not in registry.APPS:
        raise HTTPException(status_code=404)
    chosen = registry.APPS[app]
    name = draft_name(app)
    draft = _draft_read(account, name) or {}
    key = api_key.strip() or draft.get("api_key", "")
    # A key that does not pass the check (a character outside ASCII) is not put into the
    # draft: otherwise the next Test or Save with an empty field would try it again
    # instead of the saved one and the administrator would get the same error until the draft expires.
    filled = {"enabled": enabled == "1", "url": url.strip(), "api_key": "" if services.key_error(key) else key}

    if action == "test":
        _draft_save(account, name, filled)
        ok, text = await chosen.check_connection(url, key or _saved_key(app))
        if ok:
            _flash(request, "Connection works: {name} {version}", "success", name=chosen.shown_name(), version=text)
        else:
            _flash(request, "The connection failed: {reason}", "error", reason=text)
        return RedirectResponse("/settings?section=arr", status_code=303)

    previous_url = chosen.stored_settings()["url"]
    error = chosen.save_settings(enabled == "1", url, key)
    if error:
        # A failed save must not throw away what the person filled in.
        _draft_save(account, name, filled)
        _flash(request, error, "error")
    else:
        _draft_clear(account, name)
        _flash(request, "{name} settings saved.", "success", name=chosen.shown_name())
        # Another address and downloaded data: it may be another instance (HD and 4K,
        # a reinstall). Its record ids start from 1 again and an upsert would silently
        # mix both histories. It must not delete by itself (it may be just a new name
        # of the same machine) - it only says so.
        new_url = chosen.stored_settings()["url"]
        if previous_url and new_url.rstrip("/") != previous_url.rstrip("/") and chosen.event_count():
            _flash(request, ADDRESS_CHANGE, "warning", name=chosen.shown_name())
    return RedirectResponse("/settings?section=arr", status_code=303)


@router.post("/settings/arr/{app}/sync")
async def settings_arr_sync(request: Request, app: str, account: dict[str, Any] = Depends(require_admin)):
    """Synchronise now: downloads the new history of one app at once.

    It runs within the request (not in the background), so that the result shows right
    in the message. With a home library it is a few seconds; the first synchronisation
    of a huge history may take longer - then the task does it for the person.
    """
    if app not in registry.APPS:
        raise HTTPException(status_code=404)
    chosen = registry.APPS[app]
    app_name = chosen.shown_name()
    if app not in registry.connected():
        _flash(request, "Enable the app and save its address and API key first.", "error")
        return RedirectResponse("/settings?section=arr", status_code=303)
    try:
        result = await chosen.synchronize()
    except services.ServiceError as error:
        _flash(request, "Sync failed: {reason}", "error", reason=error.translated())
    except Exception as error:   # noqa: BLE001 - the error is shown, the page must not fall
        log.exception("%s sync failed", app_name)
        _flash(request, "Sync failed: {reason}", "error", reason=i18n.error_text(error))
    else:
        _flash(request, "{name}: {n} history records added.", "success", name=app_name, n=result["new_count"])
    return RedirectResponse("/settings?section=arr", status_code=303)


@router.post("/settings/arr/{app}/delete")
async def settings_arr_delete(request: Request, app: str, account: dict[str, Any] = Depends(require_admin)):
    """Deletes the downloaded history of one app - after a database backup, not without it."""
    if app not in registry.APPS:
        raise HTTPException(status_code=404)
    chosen = registry.APPS[app]
    if chosen.sync_running():
        _flash(request, "{name} sync is running right now, the downloaded data cannot be deleted yet. Try again in a moment.",
               "error", name=chosen.shown_name())
        return RedirectResponse("/settings?section=arr", status_code=303)
    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request, "The downloaded data was not deleted because the backup before it failed: {reason}", "error",
               reason=backup.get("message") or "?")
        return RedirectResponse("/settings?section=arr", status_code=303)
    try:
        count = await chosen.delete_downloaded_data()      # in a thread, under the synchronisation lock
    except services.ServiceError as error:      # a synchronisation started during the backup
        _flash(request, error.translated(), "error")
        return RedirectResponse("/settings?section=arr", status_code=303)
    _flash(request, "Deleted {n} downloaded records from {name}.", "success", n=count, name=chosen.shown_name())
    return RedirectResponse("/settings?section=arr", status_code=303)


def _saved_key(app: str) -> str:
    """The saved key - only for the connection test, it is written out nowhere."""
    return db.get_setting(f"arr_{app}_api_key", "")
