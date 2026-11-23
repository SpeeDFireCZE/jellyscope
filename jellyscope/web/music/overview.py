"""The Music page: /music/overview.

Drawn from the card registry (jellyscope/cards_music.py) - the same cards
can go to a custom dashboard. The route picks the cards an account may see
and lets the registry compute their data: the listening of the period is
selected once (stats.music_analysis in the memory of one computation) and the
library cards read the stored tracks. Without music there is no page (404
from the section's guard in web/music/__init__.py, no menu item).

Listener names need the area "...including who listened" (`music_who`):
without it the "Listeners" card is not among the allowed ones and no other
card carries names; with it anonymisation guards them (`_context`).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import cards, cards_music
from ..base import _context, require_login, templates
from ..periods import _days

router = APIRouter()


@router.get("/music/overview", response_class=HTMLResponse)
async def overview(request: Request, days: int | None = None, since: str | None = None, until: str | None = None,
                   account: dict[str, Any] = Depends(require_login)):
    """Music statistics: registry cards in a fixed layout (music/overview.html)."""
    # "Whole history" starts with the first listen: the period switch at the
    # top shows the same range the cards compute with.
    days = cards_music.music_period(_days(request, days, since, until))
    allowed_cards = cards.allowed(page_cards("overview"), account)
    # The cards are synchronous (database queries), so in a thread: the web keeps running.
    data = await asyncio.to_thread(cards.data_for, allowed_cards, days, None, account)
    return templates.TemplateResponse(request, "music/overview.html", _context(
        request, account, days=days,
        cards={card.key: card for card in allowed_cards},
        # Keys the template may ask for (the show_card() macro refuses others - a typo).
        page_card_keys=[cards_music.card_key(name) for name in cards_music.PAGES["overview"]],
        **data))


def page_cards(page: str) -> list[Any]:
    """The registry's cards of one page of Music (cards_music.PAGES), in the page's order."""
    by_key = {card.key: card for card in cards_music.CARDS}
    return [by_key[cards_music.card_key(name)] for name in cards_music.PAGES[page]]
