"""The *Arr page: a sub-tab for every connected app (Sonarr, Radarr, Lidarr, Readarr, Bazarr, Prowlarr).

A part of the web package (see jellyscope/web/__init__.py). The page is drawn
from the card registry (every app has cards of its own) - the same cards can be added
to a custom dashboard. The route only picks the cards of the shown app that the
account may see and lets the registry compute their data; the numbers are computed by
the app's statistics over the downloaded history, the live state (queue, calendar,
health) comes from the app's package with a one-minute cache. An app that is not
connected has no page: /arr/<app> returns 404 and it is neither in the menu nor in the sub-tabs.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import cards
from ..arr import registry
from .base import _context, require_login, templates
from .periods import _days

router = APIRouter()


@router.get("/arr")
def arr_index(account: dict[str, Any] = Depends(require_login)):
    """The signpost: the first connected app. Without a connected app there is no page."""
    connected = registry.connected()
    if not connected:
        raise HTTPException(status_code=404)
    return RedirectResponse(f"/arr/{connected[0]}", status_code=303)


@router.get("/arr/{app}", response_class=HTMLResponse)
async def arr_page(request: Request, app: str, days: int | None = None,
                   since: str | None = None, until: str | None = None,
                   account: dict[str, Any] = Depends(require_login)):
    """The statistics of one app: cards from the registry in a fixed layout (the app's template).

    Which app has which cards, sections and template is told by the list of apps
    (jellyscope/arr/registry.py) - this route is the same for all.
    """
    connected = registry.connected()
    if app not in connected:
        raise HTTPException(status_code=404)
    shown = registry.APPS[app]
    # "The whole history" means the app's history here, not playback: Sonarr
    # was downloading long before the first play in Jellyscope. So the period switch
    # at the top shows the same range the cards compute with.
    days = shown.period(_days(request, days, since, until))
    all_cards = shown.cards()
    allowed_cards = cards.allowed(list(all_cards), account)
    visible = {card.key for card in allowed_cards}
    # The cards are synchronous (database queries, the live state through a loop of
    # their own - cards_base.live), hence in a thread: the web keeps running meanwhile.
    data = await asyncio.to_thread(cards.data_for, allowed_cards, days, None, account)
    return templates.TemplateResponse(request, shown.template, _context(
        request, account, days=days, app=app,
        # The app that answers (Chaptarr in place of Readarr), else its name - the heading, the title and the subtitle.
        app_name=shown.shown_name(), detected_name=shown.shown_name(),
        cards={card.key: card for card in allowed_cards},
        # The keys the template may ask for (the macro show_card() refuses others - a typo).
        page_card_keys=[card.key for card in all_cards],
        # A section's heading only where the account sees at least one card.
        sections_shown={title for title, names in shown.sections
                        if any(shown.card_key(name) in visible for name in names)},
        **data))


@router.get("/arr/{app}/poster/{title_id}")
async def arr_poster(app: str, title_id: int, account: dict[str, Any] = Depends(require_login)):
    """The provisional poster of a title the app is only downloading (the "Stahuje se" card).

    A title from the library has its poster from /image/; this one is for the others. Where
    to get it the app's package knows (posters.py): from the disk, otherwise from the app -
    and only for a title Jellyscope knows from the app. An app that does not download
    (Bazarr, Prowlarr) has no posters. From the address only the name of a
    connected app and the title's number are used. Only an account with the *Arr area
    may open the path (the middleware guards it by the prefix /arr, like the page).

    When there is no poster, it is a 404 without details and without saving
    in the browser: the image is to show as soon as the app has it.
    """
    shown = registry.APPS.get(app)
    poster = await shown.provisional_poster(title_id) if shown is not None and shown.provisional_poster is not None else None
    if poster is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    content, media_type = poster
    # `private`: the poster is behind the sign-in. A day as with the posters of Seerr's requests -
    # a provisional poster has no tag in its address that would change with the image.
    return Response(content, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/partials/arr/{app}/downloading", response_class=HTMLResponse)
async def arr_downloading(request: Request, app: str, account: dict[str, Any] = Depends(require_login)):
    """The body of the "Stahuje se" card - a partial the card fetches by itself.

    The same template and the same data as when the page is drawn (the list of apps
    gives them), so there is no second place that would drift in time. The header
    `X-Jellyscope-Refresh` tells the browser in how many seconds to ask again:
    5 while downloading, otherwise 60. Only an account with the *Arr area may open the path
    (the middleware, the prefix /partials/arr/).
    """
    shown = registry.APPS.get(app)
    if shown is None or shown.downloading is None or app not in registry.connected():
        raise HTTPException(status_code=404)
    data = await shown.downloading(bool(account.get("is_admin")))
    response = templates.TemplateResponse(request, shown.downloading_template, _context(request, account, d=data))
    response.headers["X-Jellyscope-Refresh"] = str(data["refresh"])
    response.headers["Cache-Control"] = "no-store"
    return response
