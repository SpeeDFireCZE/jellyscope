"""The connection to Jellyfin and to the database.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import db, dbmigrate, dialect, i18n, jellyfin, services
from ...config import BASE_DIR as PROJECT_DIR
from ...jellyfin import JellyfinClient, JellyfinError, QUICK_TIMEOUT
from ..base import _clamp, _flash, log, require_admin
from .common import _draft_clear, _draft_read, _draft_save

router = APIRouter()


@router.post("/settings/connection")
async def settings_connection(
    request: Request,
    jellyfin_url: str = Form(""),
    jellyfin_api_key: str = Form(""),
    jellyfin_generation: str = Form("auto"),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Saves or tests Jellyfin's address and the API key.

    The key is never written into the form - only whether there is one already
    shows. So an empty field means "keep the existing one", not "delete it".
    Without it saving the form by mistake would be enough for the connection to stop working.

    Testing is a part of **the same** form, not of a separate button
    beside it. It used to be apart and sent nothing, so it tested the saved
    settings instead of the filled-in ones: you filled in the address, clicked
    "Otestovat spojeni" and got an error about a missing http:// - because
    the empty saved value was tested. And the filled-in values were thrown away
    on top, because sending one form throws away the content of the other.
    """
    # The same check as for the *Arr apps: a port that is no number or a soft hyphen in the host name makes
    # httpx fail before anything is sent - said at the form instead of a page that falls with a 500.
    url, address_error = services.address(jellyfin_url.strip().rstrip("/"))

    # The server's generation. Neither the address nor the key is thrown away - those are
    # the same for both generations and only how the queries are sent differs.
    #
    # But **the detected version** is thrown away, and only at a real change of the choice:
    # it belongs to what was chosen before, so two numbers that contradict each
    # other would stay on the page. An empty one is detected again at the next
    # connection test or synchronisation.
    choice = jellyfin_generation if jellyfin_generation in ("auto", "12", "10") \
        else "auto"
    if choice != db.get_setting(jellyfin.GENERATION_KEY, "auto"):
        db.set_setting(jellyfin.GENERATION_KEY, choice)
        db.set_setting(jellyfin.VERSION_KEY, "")

    # An empty key means "keep the existing one". At a test the draft one is taken too,
    # so that several attempts in a row can be tested without typing it again. Both only
    # for the server they were given for: another server needs the key typed again.
    draft = _draft_read(account, "jellyfin") or {}
    typed = jellyfin_api_key.strip() or services.draft_key(url, draft)
    saved_url, saved_key = db.jellyfin_connection()
    refused = services.key_refused(url, typed, saved_url, saved_key)
    key = typed or ("" if refused else saved_key)

    # Neither tested nor saved: a stored key with an invisible character would bring down every sign-in through
    # Jellyfin and every poster. The address stays in the draft, a key that does not pass does not (see settings_arr).
    error = address_error or refused or services.key_error(key)
    if error:
        _draft_save(account, "jellyfin", {"url": url, "api_key": "" if services.key_error(key) else key})
        _flash(request, error, "error")
        return RedirectResponse("/settings?section=jellyfin", status_code=303)

    if action == "test":
        # The draft is held, so that the form does not empty itself after the test.
        _draft_save(account, "jellyfin", {"url": url, "api_key": key})

        if not url:
            _flash(request, "Fill in the server address first.", "error")
        else:
            try:
                async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                    info = await client.system_info()
                _flash(
                    request,
                    "Connection works: {server} (Jellyfin {version})",
                    "success",
                    server=info.get("ServerName", "?"),
                    version=info.get("Version", "?"),
                )
            except JellyfinError as exc:
                _flash(request, "The connection failed: {reason}", "error", reason=i18n.error_text(exc))
        return RedirectResponse("/settings?section=jellyfin", status_code=303)

    db.set_setting("jellyfin_url", url)
    if key:
        db.set_setting("jellyfin_api_key", key)
    _draft_clear(account, "jellyfin")

    # The version is detected right at the save, not only at a connection test or
    # the nightly synchronisation: whoever switches the generation and clicks Save is
    # to see at once what the server reports - and above all it shows at once when it
    # contradicts the choice by hand.
    #
    # A short limit and a swallowed error: the save must not fail because
    # the server is down right now. The version then stays empty and is filled in
    # at the next occasion.
    if url and key:
        try:
            async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                await client.system_info()
        except JellyfinError as error:
            log.info("the Jellyfin version could not be detected while saving: %s",
                     error)

    _flash(request, "Connection saved.", "success")
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@router.post("/settings/database")
def settings_database(
    request: Request,
    kind: str = Form("sqlite"),
    sqlite_path: str = Form("data/jellyscope.db"),
    pg_host: str = Form("localhost"),
    pg_port: str = Form("5432"),
    pg_database: str = Form("jellyscope"),
    pg_user: str = Form("jellyscope"),
    pg_password: str = Form(""),
    # An unticked box is not sent in an HTML form at all, hence the
    # default "" = off. That is also why Form(True) cannot go
    # here - it could be switched on, but never off again.
    pg_use_pool: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """The database settings: save, test, or move the data.

    This one setting cannot be in the database - it is needed to connect
    to it at all. So it is saved into the file data/database.json.
    """
    current = db.database_config()
    # The password is never written into the form, so an empty field means
    # "keep what is known already". The draft comes first in that order:
    # after testing the connection exactly what was tested is to be saved.
    draft = _draft_read(account, "database")

    candidate = dialect.DatabaseConfig(
        kind=kind if kind in (dialect.SQLITE, dialect.POSTGRES) else dialect.SQLITE,
        path=sqlite_path.strip() or "data/jellyscope.db",
        host=pg_host.strip() or "localhost",
        port=int(_clamp(pg_port, 1, 65535, 5432)),
        database=pg_database.strip() or "jellyscope",
        user=pg_user.strip() or "jellyscope",
        password=pg_password or (draft.password if draft else "")
                 or current.password,
        use_pool=bool(pg_use_pool),
    )

    # Neither the test nor the move saves anything, but the draft values are held -
    # otherwise the user would fill them in again after every click.
    if action in ("test", "migrate"):
        _draft_save(account, "database", candidate)

    if action == "test":
        ok, message = db.test_connection(candidate)
        _flash(request, message, "success" if ok else "error")
        return RedirectResponse("/settings?section=database", status_code=303)

    if action == "migrate":
        ok, message = db.test_connection(candidate)
        if not ok:
            _flash(request, "The target database is not reachable: {reason}", "error",
                   reason=message)
            return RedirectResponse("/settings?section=database", status_code=303)

        result = dbmigrate.copy_all(current, candidate)
        if result.get("table"):
            _flash(request, "The transfer stopped at table {table}, nothing was changed: {error}",
                   "error", table=result["table"], error=result.get("error", ""))
        elif result.get("status") != "ok":
            _flash(request, result.get("message", "Transfer failed."), "error")
        else:
            _flash(
                request,
                "Transferred {n} rows. Save the settings and restart "
                "so the application switches to the new database.",
                "success",
                n=result["total"],
            )
        return RedirectResponse("/settings?section=database", status_code=303)

    ok, message = db.test_connection(candidate)
    if not ok:
        _flash(request, "Not saving, the connection does not work: {reason}", "error",
               reason=message)
        return RedirectResponse("/settings?section=database", status_code=303)

    dialect.save_config(PROJECT_DIR, candidate)
    _draft_clear(account, "database")   # saved, the draft is of no use any more
    _flash(
        request,
        "Database settings saved. The change takes effect after a restart.",
        "success",
    )
    return RedirectResponse("/settings?section=database", status_code=303)
