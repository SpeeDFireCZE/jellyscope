"""Synchronisation, stopping, the technical scan, scheduled tasks and the log.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from typing import Any

from ... import applog, conversion, db, i18n, purge, scanner, stats, tasks
from ..base import _clamp, _flash, require_admin, spawn

router = APIRouter()


@router.post("/settings/sync")
async def settings_sync(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Starts the library synchronisation in the background.

    It is not run right in this function - with a large library the browser
    would wait for the response for minutes and it would look frozen.
    The task is fired off and the answer comes at once.
    """
    if scanner.is_scan_running():
        _flash(request, "Another task is already running, wait for it to finish.", "warning")
    else:
        spawn(scanner.sync_library(), "library sync")
        _flash(request, "Library sync started.", "info")
        # `wait=task` lets the page wait and refreshes it when done -
        # otherwise a person looks at "running" with no idea when it is finished.
        return RedirectResponse("/settings?section=jellyfin&wait=task",
                                status_code=303)
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@router.post("/settings/stop")
async def settings_stop(
    request: Request,
    back: str = Form("tasks"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Asks the running task to end.

    The task is not interrupted in the middle of work - it finishes the item in hand and only
    then ends. Hence nothing is waited for here either: only the instruction is passed on.
    """
    if scanner.request_stop():
        _flash(
            request,
            "The stop request has been passed on. The task will finish the item it is on and stop. The page "
            "then refreshes itself.",
            "info",
        )
    else:
        _flash(request, "No task is running right now.", "warning")

    section = back if back in ("tasks", "jellyfin", "data") else "tasks"
    # `wait=task` tells the page to wait and refresh itself once
    # the task really ends. So the user need not guess when it is time.
    wait = "&wait=task" if scanner.stop_requested() else ""
    return RedirectResponse(f"/settings?section={section}{wait}", status_code=303)


@router.post("/settings/scan")
async def settings_scan(
    request: Request,
    mode: str = Form("missing"),
    library_id: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Starts the technical analysis of files through ffprobe.

    `library_id` is sent by the button right at the note on a library's detail -
    then only that library is analysed and the person comes back to it.
    """
    # Where to return after starting. Only the id of a library that really exists;
    # a foreign value from the form must not get into the redirect's address.
    # Without a library back among Tasks - that is where the buttons are.
    back_url = "/settings?section=tasks"
    if library_id and stats.library(library_id):
        back_url = f"/library/{library_id}"
    else:
        library_id = ""

    if db.get_setting("tech_source") != "ffprobe":
        _flash(
            request,
            "The technical data source is set to Jellyfin. "
            "Switch it to ffprobe and save the settings.",
            "warning",
        )
        return RedirectResponse(back_url, status_code=303)

    if scanner.is_scan_running():
        _flash(request, "Another task is already running, wait for it to finish.", "warning")
    else:
        spawn(scanner.run_tech_scan(only_missing=(mode == "missing"), library_id=library_id or None), "file analysis")
        _flash(request, "File analysis started.", "info")
        separator = "&" if "?" in back_url else "?"
        return RedirectResponse(f"{back_url}{separator}wait=task", status_code=303)

    return RedirectResponse(back_url, status_code=303)


@router.post("/settings/conversion-notice/close")
def conversion_notice_close(account: dict[str, Any] = Depends(require_admin)):
    """Closes the stripe about the backup before the conversion (the backup itself stays its 30 days)."""
    db.set_setting(conversion.NOTICE_CLOSED_SETTING, "1")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.post("/settings/tasks")
async def tasks_save(
    request: Request,
    backup_path: str = Form(""),
    backup_keep: str = Form(""),
    pg_dump_path: str = Form(""),
    history_retention_days: str = Form(""),
    logrotate_days: str = Form(""),
    logrotate_count: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Saves the settings of all tasks at once.

    Tick boxes are sent in a form only when ticked - hence they are
    read straight from the request's body, not as named parameters.
    An unticked field does not show in the data at all.
    """
    form = await request.form()

    for task in tasks.TASKS.values():
        enabled = "1" if form.get(f"enabled_{task.key}") else "0"
        db.set_setting(task.enabled_setting, enabled)

        if task.is_daily:
            # The hour and the minute come from two separate fields, here "HH:MM"
            # is put together from them again. `_clamp` guards the range and pads with a zero
            # (typed "3" and "5" is "03:05"), `valid_time` is the last
            # safeguard: what still would not fit the shape is saved as
            # the default, not as a broken schedule.
            #
            # The fallback for nonsense is what is saved now: so a cleared
            # field does not change the schedule. Zero would silently turn it into midnight.
            current = tasks.daily_time(task).split(":")
            hour = _clamp(str(form.get(f"time_{task.key}_h", "")),
                          0, 23, int(current[0]))
            minute = _clamp(str(form.get(f"time_{task.key}_m", "")),
                            0, 59, int(current[1]))
            time_text = f"{int(hour):02d}:{int(minute):02d}"
            db.set_setting(task.time_setting,
                           tasks.valid_time(time_text, task.default_time))
        else:
            # The same for an interval: a cleared field keeps the saved one.
            db.set_setting(
                task.interval_setting,
                _clamp(str(form.get(f"minutes_{task.key}", "")),
                       0, 10080, tasks.interval_minutes(task)),
            )

    # How much history is kept. It is saved even when purging is off -
    # once it is switched on, the number the person saw on the page is to hold.
    # A cleared or missing field keeps the saved number, never the default: with the clean-up
    # on, 365 in place of ten years deleted the older history the next night.
    db.set_setting(purge.RETENTION_KEY,
                   _clamp(history_retention_days, purge.MIN_DAYS,
                          purge.MAX_DAYS, purge.retention_days()))
    db.set_setting("backup_path", backup_path.strip())
    # The same for the number of backups kept: the default pruned the kept backups at the next run.
    db.set_setting("backup_keep", _clamp(backup_keep, 1, 365, db.get_int_setting("backup_keep", 1, 365, 7)))
    db.set_setting("pg_dump_path", pg_dump_path.strip())
    # Log rotation. A cleared field is not to break the schedule: the fallback is the saved
    # value, not zero.
    db.set_setting(applog.DAYS_KEY, _clamp(logrotate_days, 1, applog.MAX_DAYS,
                                           tasks.rotation_days()))
    db.set_setting(applog.COUNT_KEY, _clamp(logrotate_count, 1, applog.MAX_COUNT,
                                            tasks.rotation_count()))
    applog.set_count(tasks.rotation_count())

    _flash(request, "Task settings saved.", "success")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.get("/settings/log/download")
def log_download(file: str = "",
                 account: dict[str, Any] = Depends(require_admin)):
    """Downloads a log - a rotated backup too, until the rotation deletes it.

    The name is checked against the log folder (applog.downloadable), otherwise any
    file could be downloaded through the address. Secret values are replaced
    with asterisks the same as in the listing on the page.
    """
    path = applog.downloadable(file)
    if path is None:
        raise HTTPException(status_code=404, detail="There is no such log here.")
    return StreamingResponse(
        applog.masked_lines(path), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"',
                 "Cache-Control": "no-store"})


@router.post("/settings/tasks/run")
async def tasks_run(
    request: Request,
    key: str = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    """Running a task by hand.

    The task is fired off in the background and the answer comes at once - with a large
    library the browser would otherwise wait for minutes and it would look frozen.
    """
    task = tasks.TASKS.get(key)
    if task is None:
        _flash(request, "Unknown task.", "error")
    elif scanner.is_scan_running() and key in ("sync", "recent", "tech"):
        # "recent" used to be missing here, so it could be started alongside a scan -
        # and the second task then only hit the lock and ended silently.
        _flash(request, "Another task is already running, wait for it to finish.", "warning")
    else:
        spawn(tasks.run_now(key), f"task {key}")
        # The task's name is a Czech constant of tasks.py - in an English
        # interface it has to be translated like the rest of the message.
        _flash(request, "{task}: {state}", "info",
               task=i18n.translate(task.name),
               state=i18n.translate("started."))
        # The page waits and refreshes itself when done - the same as
        # with the library synchronisation.
        return RedirectResponse("/settings?section=tasks&wait=task", status_code=303)

    return RedirectResponse("/settings?section=tasks", status_code=303)
