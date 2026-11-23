"""The time of events in Bazarr's history.

Bazarr returns an event's time only as text: `parsed_timestamp` is `strftime('%x %X')`, that is the
date and the time in the format of the system Bazarr runs on, and in its time zone. It stores the
local time **without a zone** (`datetime.now()`): the text of an old event does not change after
Bazarr's zone changes, only the zone Bazarr reports does - and with it the conversion to UTC. So a
time once converted is never converted again and a record is recognised by its text, not by the
conversion (see sync.py). In reality Bazarr sets no locale, so it sends the format of the C locale
(`11/14/26 08:31:35` - month/day/year). That cannot be relied on, though - another way of starting it
is enough - so the format is recognised from the records themselves (`detect_format`).

`05/06/26` may be the 5th of June or the 6th of May. The whole page of history decides: Bazarr returns
it newest first, so in the right format the times do not go backwards and none is in the future. One
broken record (an empty time, nonsense, a clock thrown into the future) must not take the date away
from the whole page, though: the format is recognised from the rest and only that one record stays
without a date.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ... import db, formatting

# The known formats, the most likely first. The first one is what Bazarr sends.
FORMATS = (
    "%m/%d/%y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%y %H:%M:%S",
    "%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%y %I:%M:%S %p", "%d/%m/%Y %I:%M:%S %p",
)
# How far a time may be "in the future" (the clocks of Bazarr and Jellyscope, another zone) and how far
# a record may run ahead of the one before it (two records of the same second in reverse order).
FUTURE_MARGIN = timedelta(days=1)
ORDER_MARGIN = timedelta(minutes=1)
# One record in how many of a page may be unreadable for the format still to be recognised (always at least one).
BAD_ONE_IN = 20


def _parse(text: str, time_format: str) -> datetime | None:
    try:
        return datetime.strptime(text, time_format)
    except ValueError:
        return None


def detect_format(texts: list[Any], now: datetime, preferred: str | None = None) -> str | None:
    """The time format `texts` are in (a page of history, newest first). None = not recognised.

    A format in which almost everything can be read (at most a twentieth of the texts may be broken,
    always at least one) and the times read do not rise in the order of the list. An empty text, an
    unreadable text and a time after `now` (Bazarr's local time is taken as a plain time without a
    zone, hence a margin of a day) are "broken": such a record stays without a date by itself (see
    sync), it does not cost the page its format. There must not be more of them - a format that reads
    only part of the page is most likely a wrong reading (day/month read as month/day "fits" for the
    days up to the twelfth) and its times would be stored as exact for ever.

    The order is a reliable guide: Bazarr sorts the history by the same stored value the text comes
    from, so in the right format the times do not rise, not even around a clock change.

    When several formats fit equally well (the day and the month up to twelve and both readings give
    falling times), `preferred` wins - the format recognised on the previous page - and then the
    earlier one in the list.
    """
    non_empty = [text.strip() for text in texts if isinstance(text, str) and text.strip()]
    best, most = None, 0
    for time_format in dict.fromkeys([*([preferred] if preferred else []), *FORMATS]):
        times = [time for time in (_parse(text, time_format) for text in non_empty)
                 if time is not None and time <= now + FUTURE_MARGIN]
        if not times or len(non_empty) - len(times) > max(1, len(non_empty) // BAD_ONE_IN):
            continue
        if any(later > earlier + ORDER_MARGIN for earlier, later in zip(times, times[1:])):
            continue
        if len(times) > most:
            best, most = time_format, len(times)
    return best


def to_utc(text: str, time_format: str, zone: str) -> str | None:
    """A time from Bazarr's history in UTC (`db.TIME_FORMAT`). None = the text cannot be read in the format.

    `zone` is Bazarr's time zone (`timezone` from its status, say `Europe/Prague`). Unknown or empty =
    the app's zone, and when that is not set either, the system's zone.
    """
    try:
        time = datetime.strptime(str(text).strip(), time_format)
    except ValueError:
        return None
    try:
        tz = ZoneInfo(zone) if zone else formatting.zone()
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: a name that is a folder in the zone database ("America") does not fail as an unknown zone.
        tz = formatting.zone()
    # Without a zone (`None`) astimezone takes the time as the system's local time.
    time = time.replace(tzinfo=tz) if tz is not None else time.astimezone()
    return time.astimezone(UTC).strftime(db.TIME_FORMAT)
