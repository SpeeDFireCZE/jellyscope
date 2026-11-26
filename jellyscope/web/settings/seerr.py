"""Settings → Seerr: saving, the connection test, synchronisation and deleting the saved requests.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py).
What is saved and how is in the package jellyscope/seerr/ - here is only the form.
The section stands in Settings right after Jellyfin (Seerr does not belong to *Arr).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ... import db, i18n, seerr, services, tasks
from ..base import _flash, log, require_admin
from .common import _draft_clear, _draft_read, _draft_save

router = APIRouter()

DRAFT_NAME = "seerr"      # the name of the draft form (see _draft_save)
# The warning after an address change when requests are saved (a translation key - one string).
ADDRESS_CHANGE = "The {name} address changed and the downloaded history is from the old address. If it is another instance, use Delete downloaded data, otherwise the two histories mix."


@router.post("/settings/seerr")
async def settings_seerr(
    request: Request,
    enabled: str = Form(""),
    url: str = Form(""),
    api_key: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Saves or tests Seerr - by the button the form was sent with.

    Both buttons are in the same form, so the test works with what is filled in,
    not with what is saved. After the test what was filled in is held as a draft (with the
    key, in memory): the form is drawn again with it and the following Save need not
    get the key again - it does not go back into the page.
    """
    draft = _draft_read(account, DRAFT_NAME) or {}
    key = api_key.strip() or services.draft_key(url, draft)
    # A key that does not pass the check (a character outside ASCII) is not put into the
    # draft: otherwise the next Test or Save with an empty field would try it again
    # instead of the saved one and the administrator would get the same error until the draft expires.
    filled = {"enabled": enabled == "1", "url": url.strip(), "api_key": "" if services.key_error(key) else key}

    if action == "test":
        _draft_save(account, DRAFT_NAME, filled)
        # The saved key goes only to its own server (a Save is refused the same way, by save_settings).
        refused = services.key_refused(url, key, seerr.stored_settings()["url"], db.get_setting("seerr_api_key", ""))
        if refused:
            _flash(request, refused, "error")
            return RedirectResponse("/settings?section=seerr", status_code=303)
        ok, text = await seerr.check_connection(url, key or db.get_setting("seerr_api_key", ""))
        if ok:
            _flash(request, "Connection works: {name} {version}", "success", name=seerr.NAME, version=text)
        else:
            _flash(request, "The connection failed: {reason}", "error", reason=text)
        # The test is also the moment to find out whether Seerr hands out images ("Enable
        # Image Caching") - whoever has just switched the option on in Seerr reaches for it
        # first. The saved Seerr is asked (only that one has requests and posters).
        await seerr.check_images()
        return RedirectResponse("/settings?section=seerr", status_code=303)

    previous_url = seerr.stored_settings()["url"]
    error = seerr.save_settings(enabled == "1", url, key)
    if error:
        # A failed save must not throw away what the person filled in.
        _draft_save(account, DRAFT_NAME, filled)
        _flash(request, error, "error")
    else:
        _draft_clear(account, DRAFT_NAME)
        _flash(request, "{name} settings saved.", "success", name=seerr.NAME)
        # A freshly saved Seerr: find out right away whether it hands out images.
        await seerr.check_images()
        # Another address and saved requests: it may be another Seerr (request ids
        # start from 1 again, an upsert would mix both histories). It must not delete by
        # itself - it may be just a new name of the same machine; it only says so.
        new_url = seerr.stored_settings()["url"]
        if previous_url and new_url.rstrip("/") != previous_url.rstrip("/") and seerr.request_count():
            _flash(request, ADDRESS_CHANGE, "warning", name=seerr.NAME)
    return RedirectResponse("/settings?section=seerr", status_code=303)


@router.post("/settings/seerr/sync")
async def settings_seerr_sync(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Synchronise now: downloads the requests at once (within the request, so that the result is in the message)."""
    if not seerr.is_connected():
        _flash(request, "Enable the app and save its address and API key first.", "error")
        return RedirectResponse("/settings?section=seerr", status_code=303)
    try:
        result = await seerr.synchronize()
    except seerr.SeerrError as error:
        _flash(request, "Sync failed: {reason}", "error", reason=error.translated())
    except Exception as error:   # noqa: BLE001 - the error is shown, the page must not fall
        log.exception("%s sync failed", seerr.NAME)
        _flash(request, "Sync failed: {reason}", "error", reason=i18n.error_text(error))
    else:
        _flash(request, "{n} requests added, {total} in Seerr, {deleted} deleted.", "success",
               n=result["new_count"], total=result["total"], deleted=result["deleted_count"])
    return RedirectResponse("/settings?section=seerr", status_code=303)


@router.post("/settings/seerr/delete")
async def settings_seerr_delete(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Deletes the saved requests - after a database backup, not without it; never during a synchronisation."""
    if seerr.sync_running():
        _flash(request, "{name} sync is running right now, the downloaded data cannot be deleted yet. Try again in a moment.",
               "error", name=seerr.NAME)
        return RedirectResponse("/settings?section=seerr", status_code=303)
    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request, "The downloaded data was not deleted because the backup before it failed: {reason}", "error",
               reason=backup.get("message") or "?")
        return RedirectResponse("/settings?section=seerr", status_code=303)
    try:
        count = await seerr.delete_downloaded_data()        # in a thread, under the synchronisation lock
    except seerr.SeerrError as error:      # a synchronisation started during the backup
        _flash(request, error.translated(), "error")
        return RedirectResponse("/settings?section=seerr", status_code=303)
    _flash(request, "Deleted {n} downloaded records from {name}.", "success", n=count, name=seerr.NAME)
    return RedirectResponse("/settings?section=seerr", status_code=303)
