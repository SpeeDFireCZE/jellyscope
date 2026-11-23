"""The choice of the period and of the kind of content, shared by the pages (from the address, from the session, defaults).

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import Request

from .. import stats
from .base import _last_day


# The periods a user can choose. A fixed list - a number straight from the
# address never gets into SQL.
ALLOWED_DAYS = (7, 30, 90, 365)
# `?days=0` = the whole history (stats.whole_history_period). It is not in the switch,
# it is a button in the custom period window.
WHOLE_HISTORY = 0
DAY_LABELS = {7: "7 days", 30: "30 days", 90: "90 days", 365: "1 year"}

# One default period for the whole app. Every page used to have its own
# (30 days here, a year there) and the number changed unexpectedly when
# switching tabs. Now it is shared and the chosen value holds across pages.
DEFAULT_DAYS = 30
DAYS_SESSION_KEY = "days"

# The shape of a date for the history filter (a click-through from the Overview's table).
_VALID_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _days(request: Request, value: int | None,
          since: str | None = None, until: str | None = None) -> Any:
    """The chosen period - shared by all pages.

    Returns either a number of days (7, 30, 90, 365), or a `stats.Period` with
    bounds of its own. The statistics take both - see stats._bounds().

    When the address carries a valid value, it is used and remembered. When the
    address carries nothing, the last chosen one is taken. So switching to "a year"
    on the Overview holds after moving to Languages too - the window is in sync.
    A custom period is remembered the same way, only as a pair of dates.

    The number is compared against a fixed list and the dates go through
    `strptime`, so text straight from the address never gets into SQL.
    """
    # A selection dragged in a chart sends instants, not dates - see
    # stats.period_from_instants(). It comes first: whoever has just dragged
    # the mouse wants to see exactly that.
    since_ts = request.query_params.get("since_ts")
    until_ts = request.query_params.get("until_ts")
    if since_ts and until_ts:
        period = stats.period_from_instants(since_ts, until_ts)
        if period is not None:
            request.session[DAYS_SESSION_KEY] = _period_to_session(period)
            return period

    if since or until:
        # A date is written the Czech way (1.8.2026), YYYY-MM-DD is taken too - the same
        # parser as in the history filter, so that it behaves the same everywhere.
        period = stats.period_between(_date_from_text(since), _date_from_text(until))
        if period is not None:
            request.session[DAYS_SESSION_KEY] = _period_to_session(period)
            return period

    if value in ALLOWED_DAYS:
        request.session[DAYS_SESSION_KEY] = int(value)
        return int(value)
    if value == WHOLE_HISTORY:
        # The choice is remembered, not the bounds: tomorrow it is to include tomorrow too.
        request.session[DAYS_SESSION_KEY] = WHOLE_HISTORY
        return stats.whole_history_period()

    remembered = request.session.get(DAYS_SESSION_KEY)
    if remembered == WHOLE_HISTORY and not isinstance(remembered, bool):
        return stats.whole_history_period()
    if isinstance(remembered, dict):
        period = stats.period_between(remembered.get("since"), remembered.get("until"))
        if period is not None:
            return period
    if remembered in ALLOWED_DAYS:
        return int(remembered)

    return DEFAULT_DAYS


def _period_to_session(period: stats.Period) -> dict[str, str]:
    """What to remember of a custom period between pages.

    It is saved in LOCAL time. Were the bounds of the query (UTC) saved, the
    next step would read them as local again - and the period would shift back
    with every move elsewhere, often by a whole day.

    The shape carries a meaning and stats.period_between() reads it the same way:
    a date alone is a whole day, a date with a time is an exact span selected by
    dragging in a chart.
    """
    if period.whole_day:
        return {"since": period.since_local[:10], "until": _last_day(period.until_local)}
    return {"since": period.since_local[:16], "until": period.until_local[:16]}


KIND_SESSION_KEY = "kind"
# A memory of its own for the filter of the most watched titles - see _kind().
TOP_KIND_SESSION_KEY = "top_kind"


# The most watched titles do not offer "Other": they are put together from
# titles' names, while "Other" is a bag for records nothing is known about the
# title of. The list would come out empty or full of "episode 6" without a series.
TOP_ALLOWED_KINDS = (stats.KIND_BOTH, stats.KIND_MOVIE, stats.KIND_SERIES)


GROWTH_DISK_SESSION_KEY = "growth_disk"


def _growth_disk(request: Request, value: str | None) -> str:
    """The disk chosen above the Library growth chart: its `choice` (a fingerprint of the key), or "all".

    Like `_kind`: the value from the address is saved into the session, so it holds
    on the Library and in a custom dashboard and after coming back to the page. It is
    compared with the list of today's disks - nothing from the address gets into SQL.
    An unknown value from the address means "everything" (and is saved so).

    The list of disks is read only when the address carries the choice: the
    remembered value is checked by cards.growth_data() (a disk that vanished
    meanwhile is "everything" there). Otherwise every page with a dashboard would
    ask for the disks, even one that has no Library growth card at all.
    """
    if value:
        valid = {"all"} | {disk["choice"] for disk in stats.disks_to_choose()}
        chosen = value if value in valid else "all"
        request.session[GROWTH_DISK_SESSION_KEY] = chosen
        return chosen
    remembered = request.session.get(GROWTH_DISK_SESSION_KEY)
    return str(remembered) if isinstance(remembered, str) and remembered else "all"


def _kind(request: Request, value: str | None,
          session_key: str = KIND_SESSION_KEY) -> str:
    """Mix / movies / series / other - the same principle as with the period.

    The choice is remembered in the session, so that it stays after clicking
    elsewhere and back. It is compared against a fixed list, so nothing from the
    address ever gets into SQL.

    `session_key` tells the single filters apart. The most watched titles have
    their own - were they to share the choice with the watching by days, switching
    at one card would silently redraw the other too. They also have a narrower
    list of allowed values (see TOP_ALLOWED_KINDS), so "Ostatní" does not get
    into them through the address either.
    """
    allowed = (TOP_ALLOWED_KINDS if session_key == TOP_KIND_SESSION_KEY
               else stats.ALLOWED_KINDS)
    if value in allowed:
        request.session[session_key] = value
        return value

    remembered = request.session.get(session_key)
    if remembered in allowed:
        return str(remembered)

    return stats.KIND_BOTH


def _date_from_text(text: str | None) -> str | None:
    """A date written the Czech way (1.8.2026) into the shape for the database.

    The shape YYYY-MM-DD is taken too - that is how a click-through from the
    Overview's table comes and how somebody may save it in bookmarks.
    """
    text = (text or "").strip()
    if not text:
        return None
    # "2026-08-20 21:30" - that is how a saved selection from a chart comes.
    exact = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", text)
    if exact:
        return f"{exact.group(1)} {exact.group(2)}"
    if _VALID_DAY.match(text):
        return text
    match = re.match(r"^(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{4})$", text)
    if not match:
        return None
    day, month, year = (int(c) for c in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        # The thirtieth of February. Rather nothing than a date that silently shows emptiness.
        return None
