"""The Seerr page: requests for movies and series.

A part of the web package (see jellyscope/web/__init__.py). The page is drawn
from the card registry (jellyscope/cards_seerr.py) - the same cards can be added
to a custom dashboard. The route only picks the cards the account may see and lets
the registry compute their data: stats/seerr.py computes the numbers over the saved
requests, the live card "Čeká na vyřízení" comes from seerr.pending with a one-minute
cache. A Seerr that is not connected has no page (404, missing in the menu).

The requesters' names are only with the area "...including who requested" (`seerr_who`):
without it the card "Kdo žádá" is not among the allowed ones and cards_seerr.py
removes the names from the other cards - the template then cannot write them, not even
by mistake. With it anonymising guards them (`_context`).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import seerr as seerr_package
from .. import cards, cards_seerr
from .base import _context, require_login, templates
from .periods import _days

router = APIRouter()


@router.get("/seerr", response_class=HTMLResponse)
async def seerr_page(request: Request, days: int | None = None, since: str | None = None, until: str | None = None,
                     account: dict[str, Any] = Depends(require_login)):
    """The statistics of requests: cards from the registry in a fixed layout (seerr.html)."""
    if not seerr_package.is_connected():
        raise HTTPException(status_code=404)
    # "The whole history" means the history of requests here, not playback: Seerr
    # tends to be older than Jellyscope. So the period switch at the top shows the same
    # range the cards compute with.
    days = cards_seerr.seerr_period(_days(request, days, since, until))
    allowed_cards = cards.allowed(list(cards_seerr.CARDS), account)
    visible = {card.key for card in allowed_cards}
    # The cards are synchronous (database queries, the live state through a loop of
    # their own - cards_base.live), hence in a thread: the web keeps running meanwhile.
    data = await asyncio.to_thread(cards.data_for, allowed_cards, days, None, account)
    return templates.TemplateResponse(request, "seerr.html", _context(
        request, account, days=days,
        cards={card.key: card for card in allowed_cards},
        # The keys the template may ask for (the macro show_card() refuses others - a typo).
        page_card_keys=[card.key for card in cards_seerr.CARDS],
        # A section's heading only where the account sees at least one card.
        sections_shown={title for title, names in cards_seerr.SECTIONS
                        if any(cards_seerr.card_key(name) in visible for name in names)},
        **data))


@router.get("/seerr/poster/{kind}/{tmdb_id}")
async def seerr_poster(kind: str, tmdb_id: str, account: dict[str, Any] = Depends(require_login)):
    """The provisional poster of a requested title that is not in Jellyfin.

    A title from the library has its poster from /image/; this one is for the others. Where
    to get it seerr/posters.py knows: from the disk, otherwise from Radarr or Sonarr, otherwise
    from Seerr - and only for a title somebody requested. From the address only
    the checked type and the numeric TMDB id are used; the download address is not put
    together from it. Only an account with the Seerr area may open the path (the middleware
    guards it by the prefix /seerr, like the page).

    When there is no poster, it is a 404 without details and without saving
    in the browser: after switching "Enable Image Caching" on in Seerr the image is to
    show at the next load of the page, not only when its cache expires.
    """
    # provisional_poster checks the kind of the title and the shape of the id itself (one rule in one place).
    poster = await seerr_package.provisional_poster(kind, tmdb_id)
    if poster is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    content, media_type = poster
    # `private`: the poster is behind the sign-in. A day, not a week as with /image/ -
    # a provisional poster has no tag in its address that would change with the image.
    return Response(content, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})
