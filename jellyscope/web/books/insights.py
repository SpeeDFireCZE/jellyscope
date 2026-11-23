"""Insights of Books: /books/insights.

Laid out like the insights of Music: the full charts of books, authors and genres and what nobody reads - the cards of
`cards_books.PAGES["insights"]`, area `books_insights`. The overview keeps a short chart. Like the overview, the page
is drawn from the card registry, so the same cards can go to a custom dashboard.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import cards, cards_books
from ..base import _context, require_login, templates
from ..periods import _days
from .overview import page_cards

router = APIRouter()


@router.get("/books/insights", response_class=HTMLResponse)
async def insights(request: Request, days: int | None = None, since: str | None = None, until: str | None = None,
                   account: dict[str, Any] = Depends(require_login)):
    """The charts and what nobody reads: registry cards in a fixed layout (books/insights.html)."""
    days = cards_books.books_period(_days(request, days, since, until))
    allowed_cards = cards.allowed(page_cards("insights"), account)
    # The cards are synchronous (database queries), so in a thread: the web keeps running.
    data = await asyncio.to_thread(cards.data_for, allowed_cards, days, None, account)
    return templates.TemplateResponse(request, "books/insights.html", _context(
        request, account, days=days,
        cards={card.key: card for card in allowed_cards},
        page_card_keys=[cards_books.card_key(name) for name in cards_books.PAGES["insights"]],
        **data))
