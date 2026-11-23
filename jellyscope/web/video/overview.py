"""The overview of movies and series: /video/overview and its daily card.

Part of the "Movies & series" section (web/video/). The page has a fixed
layout (templates/video/overview.html) and takes its cards, their
permissions and their data from the card registry (cards.py, `OVERVIEW`).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import cards, stats
from ..periods import _days, _kind
from ..dashboards import _card_choices
from ..base import _context, require_login, templates

router = APIRouter()


@router.get("/video/overview", response_class=HTMLResponse)
def overview(request: Request, days: int | None = None, kind: str | None = None,
             since: str | None = None, until: str | None = None,
             top_kind: str | None = None,
             account: dict[str, Any] = Depends(require_login)):
    """The overview: a fixed layout of registry cards.

    The registry decides which cards an account sees (cards.allowed, by the
    area of each card) and only their data is computed - the same way as in
    a custom dashboard. It used to compute all ten cards for everyone and
    guard the cards of other areas by hand; two of them slipped through.
    """
    days = _days(request, days, since, until)
    allowed = cards.allowed([cards.BY_KEY[k] for k in cards.OVERVIEW], account)
    return templates.TemplateResponse(request, "video/overview.html", _context(
        request, account,
        days=days,
        cards={card.key: card for card in allowed},
        **cards.data_for(allowed, days, _card_choices(request, kind, top_kind), account),
    ))


@router.get("/video/partials/daily", response_class=HTMLResponse)
def daily_partial(
    request: Request,
    kind: str | None = None,
    days: int | None = None,
    since: str | None = None,
    until: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Only the "Watching by day" card, without the rest of the page.

    Switching movies/series used to reload the whole page and the browser
    rewarded it with a jump to the top. Here only this card is replaced, so
    the reader stays where they were.

    It is drawn by the same template as on the overview
    (video/_daily_card.html), not by a copy: two files would drift apart.
    """
    days = _days(request, days, since, until)
    kind = _kind(request, kind)
    return templates.TemplateResponse(request, "video/_daily_card.html", _context(
        request, account,
        days=days,
        kind=kind,
        daily=stats.daily_activity_split(days),
        others=stats.others_breakdown(days),
    ))
