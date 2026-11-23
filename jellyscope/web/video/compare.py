"""Two periods side by side: /video/compare.

Part of the "Movies & series" section (web/video/).

The page keeps its own memory of the two periods, not the shared period
filter: it has two periods and the shared filter carries one. Taking the
shared one would switch the period on every other page too - and back.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import stats
from ..base import _context, _last_day, _period_for_template, require_login, templates
from ..periods import ALLOWED_DAYS, DEFAULT_DAYS, _date_from_text, _days, _period_to_session

router = APIRouter()

# Session keys of the two periods. The values stay as before the move, so
# a period chosen before an upgrade is remembered after it.
COMPARE_A_KEY = "compare_a"
COMPARE_B_KEY = "compare_b"


def _period(request: Request, key: str, days: int | None,
            start: str | None, end: str | None, default: Any) -> Any:
    """One of the two periods of the page.

    Behaves like `_days()`, only remembers under its own key and has no
    drag-to-select in a chart - there is no chart to drag on this page.
    """
    if start or end:
        period = stats.period_between(_date_from_text(start), _date_from_text(end))
        if period is not None:
            request.session[key] = _period_to_session(period)
            return period

    if days in ALLOWED_DAYS:
        request.session[key] = int(days)
        return int(days)

    remembered = request.session.get(key)
    if isinstance(remembered, dict):
        period = stats.period_between(remembered.get("since"), remembered.get("until"))
        if period is not None:
            return period
    if remembered in ALLOWED_DAYS:
        return int(remembered)

    return default


def _period_query(prefix: str, chosen: Any) -> dict[str, str]:
    """A period as the pair for an address, so the links of one keep the other.

    Without it every switch would fall back to what is in the session and
    the choice of the other period would jump under the reader's hands.

    A dict, not a ready string: the template needs it twice - in the link
    address (`| urlencode`) and as hidden fields of the form.
    """
    if isinstance(chosen, stats.Period):
        start = (chosen.since_local or chosen.since)[:10]
        end = _last_day(chosen.until_local or chosen.until)
        return {f"{prefix}_since": start, f"{prefix}_until": end}
    return {f"{prefix}_days": str(int(chosen))}


@router.get("/video/compare", response_class=HTMLResponse)
def compare(request: Request,
            a_days: int | None = None, a_since: str | None = None,
            a_until: str | None = None,
            b_days: int | None = None, b_since: str | None = None,
            b_until: str | None = None,
            account: dict[str, Any] = Depends(require_login)):
    """Any two periods side by side.

    The default pair is what the reader knows from the overview: the chosen
    period and an equally long window before it. The page shows something
    at once; only who wants "August versus December" touches the switches.
    """
    default = _days(request, None)
    if getattr(default, "whole_history", False):
        # The whole history has no equally long window before it - there
        # is nothing before the first playback and it would be compared
        # with emptiness.
        default = DEFAULT_DAYS
    first = _period(request, COMPARE_A_KEY, a_days, a_since, a_until, default=default)
    second = _period(request, COMPARE_B_KEY, b_days, b_since, b_until, default=stats.previous_period(first))

    return templates.TemplateResponse(request, "video/compare.html", _context(
        request, account,
        comparison=stats.comparison(first, second),
        period_a=_period_for_template(first),
        period_b=_period_for_template(second),
        # The links of one switch carry the state of the other.
        query_a=_period_query("a", first),
        query_b=_period_query("b", second),
    ))
