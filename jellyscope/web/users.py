"""Users: the list and the detail.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import access, stats
from .base import _context, require_login, templates
from .periods import _days

router = APIRouter()


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, days: int | None = None,
          since: str | None = None, until: str | None = None,
          account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, since, until)
    return templates.TemplateResponse(request, "users.html", _context(
        request, account,
        days=days,
        rows=stats.user_table(days),
    ))


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    request: Request, user_id: str, days: int | None = None,
    since: str | None = None, until: str | None = None,
    account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days, since, until)
    # With hidden names the link leads to a placeholder id (skryto-2) and the real
    # id of somebody else finds nothing - see access.viewer_from_address.
    detail = stats.user_detail(access.viewer_from_address(user_id, account), days)
    if not detail:
        raise HTTPException(status_code=404, detail="User not found.")
    # Listening to music does not count as watching - it has a row of its own, and only
    # for whoever may open the Music page (the artist comes from it).
    music_listening = (stats.music_viewer(detail["user"]["id"], days)
                       if access.account_sees(account, "music") else None)

    return templates.TemplateResponse(request, "user_detail.html", _context(
        request, account, days=days, music_listening=music_listening, **detail
    ))
