"""Users: the list and the detail.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import access, accounts, stats
from .base import _context, require_login, templates
from .dashboards import _registry_page
from .periods import _days

router = APIRouter()

# The list's cards from the registry, in the order of users.html.
USERS_CARDS = ("users_chart", "users_table")


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, days: int | None = None,
          since: str | None = None, until: str | None = None,
          account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, since, until)
    # Both cards read one users table (computed once for the two).
    return templates.TemplateResponse(request, "users.html", _context(
        request, account, days=days, **_registry_page(request, account, USERS_CARDS, days)))


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
    # Listening to music does not count as watching - it has a row of its own. Somebody else's
    # listening is "...including who listened" (the Listeners card's area); the own one needs
    # only the Music page (the artist comes from it).
    own = access.own_viewer(account)
    own_page = bool(own) and accounts.bare_id(own) == accounts.bare_id(str(detail["user"]["id"]))
    music_listening = (stats.music_viewer(detail["user"]["id"], days)
                       if access.account_sees(account, "music" if own_page else "music_who") else None)
    # What the person watched - the most watched titles, the devices, the genres - is "who watched what": somebody
    # else's only with "...including who watched it", the gate of History too (access.sees_history). The totals stay,
    # the list of users shows them as well.
    shows_watched = own_page or access.account_sees(account, "library_who")
    if not shows_watched:
        detail = {**detail, "top_items": [], "devices": [], "genres": []}

    return templates.TemplateResponse(request, "user_detail.html", _context(
        request, account, days=days, music_listening=music_listening, shows_watched=shows_watched, **detail
    ))
