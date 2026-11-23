"""Insights and the most watched titles: /video/insights, /video/insights/top.

Part of the "Movies & series" section (web/video/). The insights page is
made of the cards of the "Zjištění" area (cards.area_data); the most
watched titles have a full ranking page and a partial for the overview card.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import cards, stats
from ..base import _context, require_login, templates
from ..periods import TOP_KIND_SESSION_KEY, _days, _kind

router = APIRouter()


@router.get("/video/insights", response_class=HTMLResponse)
def insights(request: Request, days: int | None = None,
             since: str | None = None, until: str | None = None,
             account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, since, until)
    # The page is made of the insight cards and takes their data from the registry.
    return templates.TemplateResponse(request, "video/insights.html", _context(
        request, account, days=days, **cards.area_data("charts", days)))


@router.get("/video/partials/top-items", response_class=HTMLResponse)
def top_items_partial(
    request: Request,
    kind: str | None = None,
    days: int | None = None,
    since: str | None = None,
    until: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Only the "Most watched titles" card.

    Switching the filter replaces this one card, so the page stays where it
    is. It used to reload whole and scroll to the top - and this card is in
    the second half of the overview.
    """
    kind = _kind(request, kind, TOP_KIND_SESSION_KEY)
    return templates.TemplateResponse(request, "video/_top_items.html", _context(
        request, account,
        top_kind=kind,
        top_items=stats.top_items(_days(request, days, since, until), kind=kind),
    ))


@router.get("/video/insights/top", response_class=HTMLResponse)
def insights_top(
    request: Request,
    kind: str | None = None,
    days: int | None = None,
    since: str | None = None,
    until: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """The full ranking of the most watched titles - the overview chart shows ten.

    One route, two shapes: for a `fetch` from the window over the overview
    it returns only the table, for a plain visit the whole page. Without
    JavaScript nobody loses the list and both come from the same template.

    The limit of a thousand rows is a safety net, not a restriction: series
    are summed, so a thousand rows means a thousand different titles.
    """
    kind = _kind(request, kind, TOP_KIND_SESSION_KEY)
    context = _context(
        request, account,
        top_kind=kind,
        top_items=stats.top_items(_days(request, days, since, until), limit=1000, kind=kind),
    )
    if request.headers.get("x-requested-with") == "fetch":
        return templates.TemplateResponse(request, "video/_top_items_all.html", context)
    return templates.TemplateResponse(request, "video/insights_top.html", context)
