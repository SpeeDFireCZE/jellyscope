"""The update, the restart and backups (download, delete, restore).

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py). The waiting
page after an update is put together here in Python; its inline script and styles are text of
that page (they go with the templates and scripts, parts 10b and 10c of the English-code step)."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from html import escape
from typing import Any

from ... import __version__, db, i18n, scanner, tasks, updates
from ..base import STARTED_AT, _flash, log, require_admin, spawn

router = APIRouter()


@router.post("/settings/update")
async def settings_update(request: Request,
                          account: dict[str, Any] = Depends(require_admin)):
    """Downloads the new version and restarts the app.

    It does the same as `deploy/update.sh`, only without the last step:
    `git pull`, installing dependencies and then **a restart of its own
    process** (`os.execv`), that is the same restart as the button in Settings.
    No service manager is needed for it.

    The app adds missing database columns by itself at start, so
    after the restart it is done. When the update fails, nothing is
    restarted and the old version keeps running - that is the safer order.
    """
    result = await updates.update()
    if result.get("status") != "ok":
        _flash(request, result.get("message", "The update did not go through."), "error",
               **result.get("parameters", {}))
        return RedirectResponse("/settings?section=general", status_code=303)

    _schedule_restart()
    # On purpose NOT a redirect to a page of the app.
    #
    # Templates are read from files at every request, the app's code lives
    # in the process's memory. So between the download and the restart OLD CODE runs
    # over NEW TEMPLATES - and once a new template wants a variable the old
    # code does not know, it ends in an error. Exactly this made the "Internal
    # Server Error" after an update from the browser.
    #
    # This page is put together here in Python: no template, no
    # context, nothing that could drift apart. It waits for the new process and only
    # then lets the person on. Its script is inline (it must not go to static/js -
    # that changes with the version), hence a one-time nonce into the CSP header.
    nonce = secrets.token_urlsafe(18)
    request.state.csp_nonce = nonce
    return HTMLResponse(_update_page(nonce))


def _update_page(nonce: str) -> str:
    """The waiting room for the restart after an update - without a template, on purpose.

    It is the only page the **old** version of the app has to render
    at a moment when the new one lies on the disk already. So it must not touch
    anything that changes with the version: neither a template, nor a style, nor the context.
    Hence its script is inline and passes CSP only with the `nonce` from the header
    (web.csp) - the only such page in the app.
    """
    # The page is put together in Python, so Jinja's autoescape does not protect it.
    # Translations are written by other people through Weblate: a tag in them has to show
    # as text and an apostrophe ("It's taking longer") must not break the JS string,
    # otherwise the function attempt() would not arise and the waiting room would never let anybody on.
    heading = escape(i18n.translate("Updating…"))
    description = escape(i18n.translate("The new version is downloaded. The app is restarting now. As soon as it is "
                                        "up, you will be let through."))
    too_long = escape(i18n.translate("This is taking longer than it should."))
    retry = escape(i18n.translate("Try again"))
    slow_note = json.dumps(f'{too_long} <a href="/">{retry}</a>')
    started_at = int(STARTED_AT)
    current_version = __version__
    return f"""<!doctype html>
<html lang="{i18n.current_language()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading} &middot; Jellyscope</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #131312; color: #eceae4; font-size: 15px;
         font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .box {{ max-width: 420px; padding: 28px 30px; border-radius: 14px;
          border: 1px solid rgba(255,255,255,.10); background: #1b1b1a;
          box-shadow: 0 24px 60px rgb(0 0 0 / 45%); text-align: center; }}
  h1 {{ font-size: 19px; margin: 0 0 10px; }}
  p  {{ color: #b6b4ac; line-height: 1.5; margin: 0; }}
  .note:not(:empty) {{ margin-top: 14px; font-size: 14px; }}
  .note a {{ color: #6aa9ee; }}
  .pulse-dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%;
            background: #3987e5; margin-bottom: 14px;
            animation: pulse 1.4s ease-in-out infinite; }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .25; }} }}
  @media (prefers-reduced-motion: reduce) {{ .pulse-dot {{ animation: none; }} }}
</style>
</head>
<body>
<div class="box">
  <span class="pulse-dot"></span>
  <h1>{heading}</h1>
  <p>{description}</p>
  <p class="note" id="note"></p>
</div>
<script nonce="{nonce}">
  // We ask /health until the new process answers - it is recognised by
  // `started_at`, which changes when the process is swapped. Only then is
  // the page let through; until then the old version would draw it over the
  // new templates, which is exactly what we avoid here.
  //
  // What we wait from is written here by the server - the start of the process
  // that drew this page. Finding it out from the first response did not
  // work: the restart comes within a second, so the first response already
  // belonged to the new process, the waiting page took it as the initial state
  // and waited for a change that never came. The app had long been up.
  var startedAt = {started_at};        // the start of the process that drew this page
  var version = "{current_version}";     // and the version it had
  var attempts = 0;
  var GIVE_UP_AFTER = 150;              // ~5 minutes

  function attempt() {{
    if (attempts++ > GIVE_UP_AFTER) {{
      document.getElementById("note").innerHTML =
        {slow_note};
      return;
    }}
    fetch("/health", {{ cache: "no-store", credentials: "same-origin" }})
      .then(function (r) {{ return r.json(); }})
      .then(function (data) {{
        // Two signals, because each of them alone fails somewhere:
        //   * `version` is exactly the question we wait on - but it is
        //     reported only to someone signed in,
        //   * `started_at` answers anybody, it just says less
        //     ("something restarted").
        // It is enough when one of them changes.
        if (data.started_at !== startedAt) {{ location.href = "/"; }}
        else if (data.version && data.version !== version) {{ location.href = "/"; }}
        else {{ setTimeout(attempt, 2000); }}
      }})
      .catch(function () {{
        // The app is down right now - exactly what we expect.
        setTimeout(attempt, 2000);
      }});
  }}

  attempt();
</script>
</body>
</html>"""


@router.post("/settings/restart")
async def settings_restart(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Restarts **Jellyscope**, not Jellyfin.

    Its own process is replaced, nothing else. The media server is not
    touched - a running playback is not affected. The only thing that happens
    is that this app stops answering for a few seconds.

    Most settings take effect at once, because they are read from the database at every
    use. A restart is needed for things that are loaded once at start -
    mainly a change of the database.

    The restart is done by the process replacing itself (`os.execv`). Once the response
    reaches the browser, the app comes up again.
    """
    log.info("restart requested by account %s", account["username"])
    _flash(request, "The application is restarting. The page refreshes itself once it is up.", "info")
    _schedule_restart()
    # `wait=restart`: the page waits by itself until the new process comes up,
    # and loads again. The user used to have to guess and refresh by hand.
    return RedirectResponse("/settings?section=general&wait=restart", status_code=303)


def _schedule_restart() -> None:
    """In a moment replaces the process with itself.

    A function of its own, because several places need a restart - the button by
    hand and restoring a backup. Two copies of this logic would drift in time and one
    of them would forget to close the connections.
    """
    async def _restart_soon() -> None:
        # A moment for sending the response - otherwise the browser would get
        # a broken connection instead of the redirect.
        await asyncio.sleep(1.0)
        log.info("restart requested by the user")
        # execv replaces the process, so nobody does any cleanup afterwards -
        # the connections have to be closed now, while still running.
        db.close_pool()
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            # Should execv not go through, at least end politely - the service manager
            # (or the user) starts the app again.
            log.exception("restart via execv failed, shutting down")
            os._exit(1)

    spawn(_restart_soon(), "restart")


@router.get("/settings/backup/download")
def backup_download(name: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Downloads one backup of the database.

    The name is checked against what really lies in the backup folder -
    see `tasks.backup_file()`. Without it any file of the machine
    could be downloaded through the address.
    """
    path = tasks.backup_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="There is no such backup here.")

    # `media_type` is octet-stream on purpose: the browser saves the file instead
    # of trying to show SQL as text in a page.
    return FileResponse(path, filename=path.name,
                        media_type="application/octet-stream")


@router.post("/settings/backup/delete")
def backup_delete(request: Request, name: str = Form(""),
                  account: dict[str, Any] = Depends(require_admin)):
    """Deletes one backup."""
    if tasks.delete_backup(name):
        _flash(request, "Backup {name} deleted.", "success", name=name)
    else:
        _flash(request, "That backup could not be found.", "error")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@router.post("/settings/backup/restore")
async def backup_restore(request: Request, name: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Restores the database from a backup and restarts the app.

    The restart is not cosmetic: the app has in memory settings and open
    connections to the database that no longer hold after the restore.
    """
    # A running sync or analysis would write into the restored database after it (its batches wait for the lock),
    # until the restart - the sync's marking of vanished titles over nearly the whole restored library. Imports,
    # backups and the clean-up take no lock, they are counted (tasks.data_job). The restore then holds the lock
    # itself, so no task the scheduler starts meanwhile writes into the database being restored.
    if scanner.is_scan_running() or tasks.data_job_running():
        _flash(request, "Another task is already running, wait for it to finish.", "warning")
        return RedirectResponse("/settings?section=tasks", status_code=303)
    async with scanner.task_lock():
        result = await asyncio.to_thread(tasks.restore_backup, name)
    if result.get("status") != "ok":
        _flash(request, result.get("message", "The restore failed."), "error")
        return RedirectResponse("/settings?section=tasks", status_code=303)

    _flash(request,
           "The database has been restored from backup {name}. The state before the "
           "restore was saved as {backup}. The application is restarting.",
           "success", name=name, backup=result["safety"])
    _schedule_restart()
    return RedirectResponse("/settings?section=tasks&wait=restart", status_code=303)
