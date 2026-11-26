"""Importing history, tidying data, orphans, forgetting a viewer.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Any

from ... import i18n, importers, purge, tasks
from ..base import (
    BACK_TO_IMPORT,
    _clamp,
    _context,
    _flash,
    _form_number,
    _import_message,
    require_admin,
    templates,
)
from .common import _import_from_file

router = APIRouter()


@router.post("/settings/history/forget")
async def history_forget(request: Request, user_id: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Deletes the history of one viewer. The account stays in Jellyfin.

    A route of its own, not a part of the tasks form: it is a one-off change of
    the data, not a setting, and is not to happen by the way while saving something
    else.

    The order is fixed: the backup first, then deleting. When the backup fails,
    nothing is deleted - whoever clicked the wrong name has something to come back from.
    """
    user_id = (user_id or "").strip()
    if not purge.session_count(user_id):
        _flash(request, "There was nothing to forget; we have nothing about that viewer.",
               "info")
        return RedirectResponse("/settings?section=tasks#purge",
                            status_code=303)

    backup = await tasks.backup_before_delete()
    if backup.get("status") != "ok":
        _flash(request,
               "History was not deleted because the backup that comes first failed: {reason}",
               "error", reason=backup.get("message") or "?")
        return RedirectResponse("/settings?section=tasks#purge",
                            status_code=303)

    # Into the bin, not straight away: the viewer is gone from the statistics at once, but
    # until the night it can be taken back - see purge.restore_from_bin(). The move runs
    # in a thread, so that a large history does not stop serving the other pages.
    result = await asyncio.to_thread(
        purge.forget_user, user_id, True, str(backup.get("file") or ""))
    # `purge.forget_user()` wrote the log already. A second line
    # about the same action would only be in the way in the log.
    when = tasks.deferred_compaction_time()
    _flash(request,
           "Viewer {user}'s history is gone from the statistics ({n} plays). You can bring it back "
           "until {when}; after that it is deleted for good.",
           "success",
           user=result["name"] or user_id, n=result["deleted"],
           when=when.strftime("%d.%m. %H:%M") if when else "?")
    return RedirectResponse("/settings?section=tasks#purge",
                            status_code=303)


@router.post("/settings/history/restore")
async def history_restore(request: Request, event: str = Form(""),
                          account: dict[str, Any] = Depends(require_admin)):
    """Puts a viewer who is in the bin back into the history.

    It is no dangerous action - nothing is deleted, only what was cleared away a
    moment ago comes back - so it asks nothing and has no window of its own.
    """
    result = await asyncio.to_thread(purge.restore_from_bin, _form_number(event))
    if not result["returned"]:
        _flash(request,
               "Could not bring it back; there is no such thing in the bin any more.", "error")
    else:
        _flash(request, "Viewer {user}'s history is back ({n} plays).",
               "success", user=result["name"], n=result["returned"])
    return RedirectResponse("/settings?section=tasks#purge",
                            status_code=303)


@router.post("/settings/import/detect")
async def import_detect(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Finds out whether Jellyfin has the Playback Reporting plugin."""
    available, message = await importers.playback_reporting_available()
    _flash(request, message, "success" if available else "warning")
    return RedirectResponse(BACK_TO_IMPORT, status_code=303)


@router.post("/settings/import/playback-reporting")
async def import_playback_reporting(
    request: Request,
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    with tasks.data_job():       # no backup is restored under an import
        result = await importers.import_playback_reporting(
            min_seconds=int(_clamp(min_seconds, 0, 3600, 60))
        )
    _import_message(
        request, result,
        "Playback Reporting: imported {n} records "
        "(out of {found} found, {duplicates} already existed).")

    return RedirectResponse(BACK_TO_IMPORT, status_code=303)


@router.post("/settings/import/playback-reporting-file")
async def import_playback_reporting_file(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """An import from a backup of the Playback Reporting plugin (a TSV file).

    A fallback for when the plugin does not work through the API - see
    importers.import_playback_reporting_tsv().
    """
    return await _import_from_file(
        request, backup, min_seconds, importers.import_playback_reporting_tsv,
        "Playback Reporting (backup file): imported {n} records "
        "(out of {found} found, {duplicates} already existed).")


@router.post("/settings/history/tidy")
async def history_tidy(request: Request,
                       account: dict[str, Any] = Depends(require_admin)):
    """Tidying data - one button for the whole cleanup of the history.

    These used to be two actions and a person had to know which to run first.
    Yet the order is not a choice: looking up in Jellyfin makes the links
    everything else works with. Hence one route - and the scheduled task
    runs the same function, so the run by hand and the automatic one cannot
    drift apart.

    It can be run repeatedly; the second time it finds nothing.
    """
    result = await importers.tidy_data()

    jf = result.get("jellyfin") or {}
    if jf.get("status") == "error":
        # Jellyfin is only the first step. The rest ran, so it is no error of
        # the whole action - but it has to show, otherwise a person would wait in vain
        # for the orphaned records to be found.
        _flash(request, "Jellyfin did not answer ({reason}), the rest went through.",
               "warning", reason=jf.get("message", "?"))
    elif not result["parts"]:
        _flash(request, "There was nothing to tidy, the history is in order.", "success")
    else:
        sentences = [i18n.translate_with(template, None, **values)
                     for template, values in result["parts"]]
        message = ", ".join(sentences)
        if result["left"]:
            message += ". " + i18n.translate_plural("{n} records remain unassigned.", result["left"])
        _flash(request, "Data tidy-up: {what}", "success", what=message)

    return RedirectResponse("/settings?section=tasks#cleanup", status_code=303)


@router.get("/settings/history/orphans", response_class=HTMLResponse)
def history_orphans(request: Request, assign: str = "", q: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """The list of records that could not be placed - with the reason.

    Nothing is saved anywhere, the list is computed anew every time. A saved one
    would show the state after the last cleanup, that is something that need not hold
    any more - a synchronisation or another import may have run meanwhile.
    """
    orphaned = importers.analyse_orphans()
    chosen = None
    if assign:
        chosen = next((o for o in orphaned if str(o["item_id"]) == assign), None)

    return templates.TemplateResponse(request, "orphans.html", _context(
        request, account,
        orphans=orphaned,
        reason_labels=importers.REASON_LABELS,
        chosen=chosen,
        search_text=q,
        candidates=importers.candidates_for_orphan(q) if chosen else [],
    ))


@router.post("/settings/history/assign")
def history_assign(request: Request, item_id: str = Form(""),
                   target_id: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Assigning orphaned records to an item of the library by hand."""
    result = importers.assign_manually(item_id.strip(), target_id.strip())
    if result.get("status") != "ok":
        _flash(request, result.get("message", "It did not work."), "error")
    else:
        _flash(request, 'Assigned to "{name}": {n} records.', "success",
               name=result["name"], n=result["rows"])
    return RedirectResponse("/settings/history/orphans", status_code=303)


@router.post("/settings/import/jellystat")
async def import_jellystat(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """An import from an uploaded JSON file with a Jellystat backup."""
    return await _import_from_file(
        request, backup, min_seconds, importers.import_jellystat_json,
        "Jellystat: imported {n} records "
        "(out of {found} found, {duplicates} already existed).")
