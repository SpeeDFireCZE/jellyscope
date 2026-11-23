"""Helpers shared by the artist page and the album page.

An id from the address is used only in the checked shape; the data of the
page are computed in a thread; listener names are sent to the template only
to an account with the area "...including who listened" (`music_who`).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from ... import access, cards_music
from ..base import check_id
from ..periods import _days


async def load_detail(request: Request, days: int | None, start: str | None, end: str | None,
                      account: dict[str, Any], loader: Callable[[str, Any], dict[str, Any] | None],
                      item_id: str) -> tuple[Any, dict[str, Any]]:
    """Checked id, period, data (None = 404); listener names only with `music_who`."""
    check_id(item_id)
    period = cards_music.music_period(_days(request, days, start, end))
    data = await asyncio.to_thread(loader, item_id, period)
    if data is None:
        raise HTTPException(status_code=404)
    if not access.account_sees(account, "music_who"):
        data["listeners"] = None
    return period, data


def split_genres(text: Any) -> list[str]:
    """Genres as Jellyfin's list joined by `|`."""
    return [genre.strip() for genre in str(text or "").split("|") if genre.strip()]
