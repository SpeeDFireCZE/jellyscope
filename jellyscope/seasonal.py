"""Seasonal decoration: which holiday it is today.

Around Christmas, Halloween, New Year's Eve and Easter Jellyscope decorates
itself (ornaments by the logo, a holiday colour, an animation across the page
- see `static/events/`). Here it is only decided **whether and which**
holiday it is; how it looks is up to the templates and CSS.

An admin may switch the decoration off (`ui_events`), not start or change it.
Whoever wants to see it out of season gives the server a date through the
environment variable `JELLYSCOPE_EVENT_DATE=2026-12-24` (before 1.9.0
`JELLYSCOPE_UDALOST_DATUM`, which still works).
"""
from __future__ import annotations

import functools
import logging
import os
import re
from datetime import date, timedelta
from typing import Any
from collections.abc import Callable

from . import db

log = logging.getLogger("jellyscope.seasonal")

EVENTS: tuple[str, ...] = ("christmas", "halloween", "newyear", "easter")

# Where to take "today" from even before the environment variable. In prod it
# stays None; only the development tool sets it (jellyscope/devtools.py, not
# copied to prod), so the date can be switched in Settings in dev.
DATE_SOURCE: Callable[[], date | None] | None = None

VARIABLE = "JELLYSCOPE_EVENT_DATE"
# The name released before 1.9.0 (TODO.md item 0: kept for a while, like the old Czech addresses).
OLD_VARIABLE = "JELLYSCOPE_UDALOST_DATUM"
# Only YYYY-MM-DD: fromisoformat would take "20261224" or a time after the date too.
_DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# Nonsense in the variable is reported once per run, not with every page.
_reported = False


@functools.lru_cache(maxsize=64)
def easter(year: int) -> date:
    """Easter Sunday for any year (Gregorian calendar).

    The anonymous Gregorian algorithm (Meeus/Jones/Butcher): the first Sunday
    after the first spring full moon, in integers and without a table.
    Checked against `dateutil.easter` for the years 1583-4099.
    """
    a = year % 19                          # where the year is in the Moon's 19-year cycle
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30    # days from 21 March to the full moon
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - days from the full moon to Sunday
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    day = (h + l - 7 * m + 33 * month + 19) % 32
    return date(year, month, day)


def event_for(day: date) -> str | None:
    """The holiday's key for the given day, or None. Both limits of the windows count.

    New Year's Eve starts only on 27 December: a week before New Year (25
    December) would override Christmas. The windows do not overlap - Easter
    always lies between 15 March and 26 April.
    """
    m, d = day.month, day.day
    if m == 10 and 24 <= d <= 31:
        return "halloween"
    if m == 12 and 17 <= d <= 26:
        return "christmas"
    if (m == 12 and d >= 27) or (m == 1 and d == 1):
        return "newyear"
    sunday = easter(day.year)
    if sunday - timedelta(days=7) <= day <= sunday + timedelta(days=1):
        return "easter"
    return None


def today() -> date:
    """Today's date for the decoration.

    The order: the development hook (`DATE_SOURCE`), the environment variable,
    the real local date of the server (the same as History). The variable is
    read every time, so it can be changed without a restart in tests.
    """
    global _reported
    if DATE_SOURCE is not None:
        substituted = DATE_SOURCE()
        if substituted is not None:
            return substituted
    name = VARIABLE if os.environ.get(VARIABLE, "").strip() else OLD_VARIABLE
    text = os.environ.get(name, "").strip()
    if text:
        if _DATE_FORMAT.fullmatch(text):
            try:
                return date.fromisoformat(text)
            except ValueError:      # 2026-02-30
                pass
        if not _reported:
            _reported = True
            log.warning("invalid %s %r, using today's date", name, text)
    return date.today()


def enabled() -> bool:
    """Has the admin the decoration switched on? By default yes."""
    return (db.get_setting("ui_events", "1") or "1").strip() != "0"


def active() -> str | None:
    """The holiday to show now - or None (switched off, out of season)."""
    if not enabled():
        return None
    return event_for(today())


def celebrated_year(day: date | None = None) -> int:
    """The year whose coming New Year's Eve celebrates (the game's finale): in December the next one."""
    day = day or today()
    return day.year + 1 if day.month == 12 else day.year


def year_for_template() -> str:
    """The celebrated year for a template (the global `seasonal_year()` in Jinja).

    Like `for_template`: the development date source reads the database, and
    when it does not answer, the real date of today applies - the page does
    not fail.
    """
    try:
        return str(celebrated_year())
    except Exception:  # noqa: BLE001 - the decoration is extra, the page comes first
        return str(celebrated_year(date.today()))


def motion(account: dict[str, Any] | None) -> bool:
    """May the decoration move? Not only when the account switched it off.

    The system's "reduce motion" is handled by CSS and JS themselves
    (prefers-reduced-motion); this is only about the choice on the My account
    page. An unknown value = motion.
    """
    return not (account and account.get("events_motion") == "0")


def for_template() -> str:
    """The holiday's key for a template (the global `seasonal_event()` in Jinja), otherwise "".

    The decoration must not bring the page down: when the settings cannot be
    read (a database outage), it simply does not show.
    """
    try:
        return active() or ""
    except Exception:  # noqa: BLE001 - the decoration is extra, the page comes first
        return ""
