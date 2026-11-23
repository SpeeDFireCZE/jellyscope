"""Two periods of listening side by side: /music/compare.

Laid out like the comparison of Movies & series (the user, 22. 11. 2026): the same two switches of the periods, the
same table with the difference and the charts of both periods (templates/_compare.html); the numbers from
stats.music_comparison(). The listeners' chart only with "...including who listened" (`music_who`). The page keeps
its own memory of the two periods, apart from the video comparison's.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import access, stats
from ..base import _context, _period_for_template, require_login, templates
from ..periods import DEFAULT_DAYS, _days
from ..video.compare import _period, _period_query

router = APIRouter()

# Session keys of the two periods - Music's own, so a period chosen on one comparison does not move the other.
COMPARE_A_KEY = "music_compare_a"
COMPARE_B_KEY = "music_compare_b"


@router.get("/music/compare", response_class=HTMLResponse)
def compare(request: Request,
            a_days: int | None = None, a_since: str | None = None, a_until: str | None = None,
            b_days: int | None = None, b_since: str | None = None, b_until: str | None = None,
            account: dict[str, Any] = Depends(require_login)):
    """Any two periods side by side; by default the chosen period and an equally long window before it."""
    default = _days(request, None)
    if getattr(default, "whole_history", False):
        # The whole history has no equally long window before it - it would be compared with emptiness.
        default = DEFAULT_DAYS
    first = _period(request, COMPARE_A_KEY, a_days, a_since, a_until, default=default)
    second = _period(request, COMPARE_B_KEY, b_days, b_since, b_until, default=stats.previous_period(first))
    comparison = stats.music_comparison(first, second)
    if not access.account_sees(account, "music_who"):
        # Without "...including who listened" no listener is even handed to the template.
        comparison["a"]["users"] = comparison["b"]["users"] = []
    return templates.TemplateResponse(request, "music/compare.html", _context(
        request, account,
        comparison=comparison,
        shows_listeners=access.account_sees(account, "music_who"),
        period_a=_period_for_template(first),
        period_b=_period_for_template(second),
        # The links of one switch carry the state of the other.
        query_a=_period_query("a", first),
        query_b=_period_query("b", second),
    ))
