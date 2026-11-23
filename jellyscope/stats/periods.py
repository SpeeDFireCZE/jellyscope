"""The time windows of the statistics: the period, its bounds and the previous period for comparing.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, UTC
from typing import Any

from .. import db, formatting


@dataclass(frozen=True)
class Period:
    """The window the statistics are computed over.

    Only a count of days used to be passed and every query put in `datetime('now', '-30 days')`. The window
    thus always ended now, so "the whole of last December" could not be expressed at all. Hence a pair of
    bounds.

    `since` is inclusive, `until` exclusive - the query is then `>= since AND < until` and midnight does not
    belong to two days at once.
    """

    since: str
    until: str
    days: int
    # "The last N days" (relative), or a fixed range from-to? It decides two things: how the period is named in
    # the switch and from which day the calendar in the chart by days starts.
    relative: bool = True
    # The same bounds, but in the APP'S ZONE.
    #
    # `since`/`until` are in UTC, because the history is stored in UTC. The form and the labels want what the person
    # wrote, though - when they pick 20 August, 20 August is to stand there, not 19 August 22:00.
    #
    # Both pairs behave the same: the lower bound INCLUSIVE, the upper EXCLUSIVE. No "sometimes inclusive,
    # sometimes not" - that breeds off-by-one-day bugs which are hard to find, because half a year (in winter)
    # they do not show.
    since_local: str = ""
    until_local: str = ""
    # Did the person enter whole days, or an exact time? It decides only how the period is written:
    # "20.8.2026" for a whole day, "20.8.2026 21:30" for a stretch picked by dragging in a chart.
    whole_day: bool = True
    # The whole history (whole_history_period): computed as "the last N days" from the first play, only named
    # differently.
    whole_history: bool = False

    @property
    def custom(self) -> bool:
        """Did the person pick the period themselves?"""
        return not self.relative


# The upper bound of "the last N days". A far future on purpose, not "now": time is stored rounded to seconds,
# so a play written in the very second we ask would no longer fit in the window - and that is exactly the
# record the person is looking for right now. A custom period sets its upper bound itself.
END_OF_TIME = "9999-12-31 23:59:59"
# "Practically everything" for functions that put `limit` into SQL (`LIMIT ?`). The API filters over the whole
# list, so it needs it whole; the number fits into int64 in SQLite and PostgreSQL even after `limit * 6` in
# recently_added().
NO_LIMIT = 1_000_000_000
# How far back the history can reach at all. Jellyfin came to be in 2018, Emby in 2013 - an older play is an
# import's mistake (Playback Reporting can send DateTime.MinValue, i.e. 0001-01-01) and does not decide the
# start of the history.
OLDEST_HISTORY = "2000-01-01 00:00:00"


def period_of_days(days: int) -> Period:
    """The last N days. The end is open - see END_OF_TIME."""
    day_count = max(1, int(days))
    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=day_count)
    return Period(since=start.strftime(db.TIME_FORMAT), until=END_OF_TIME, days=day_count)


def whole_history_period(first: str | None = None) -> Period:
    """The whole history: from the day of the first play until now, without a time limit.

    Computed as "the last N days", where N reaches back to the first play: the end is open (END_OF_TIME), so a
    remembered choice includes what is added tomorrow too, and the length, the previous window and the chart's
    calendar work just like with the other periods. Without a history it is today.

    The start is first_record(), not a bare MIN: a nonsense date from an import would otherwise overflow when
    converted to the zone and a remembered choice would bring down every page.

    `first` is the start of another history than plays (the *Arr page: Sonarr's first event, which tends to be
    long before the first play in Jellyscope).
    """
    zone = formatting.zone()
    today = datetime.now(zone).replace(tzinfo=None)
    first = first_record() if first is None else first
    try:
        start = (datetime.strptime(str(first)[:19], db.TIME_FORMAT)
                 .replace(tzinfo=UTC).astimezone(zone).replace(tzinfo=None))
    except (TypeError, ValueError):
        start = today
    day = min(start, today).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return Period(
        since=day.replace(tzinfo=zone).astimezone(UTC).strftime(db.TIME_FORMAT),
        until=END_OF_TIME, days=(today.date() - day.date()).days + 1,
        since_local=day.strftime(db.TIME_FORMAT), until_local=tomorrow.strftime(db.TIME_FORMAT),
        whole_history=True)


def _local_time(text: Any) -> tuple[datetime, bool] | None:
    """Text from the address to a time in the app's zone. Returns (time, is_a_date).

    We take two shapes: "2026-08-20" (a whole day) and "2026-08-20 21:30" (an exact moment). The second is used
    by picking a range by dragging in a chart - there rounding to a whole day would throw away exactly what the
    person picked.
    """
    raw = str(text or "").strip().replace("T", " ")
    for fmt, is_date in (("%Y-%m-%d %H:%M:%S", False),
                         ("%Y-%m-%d %H:%M", False),
                         ("%Y-%m-%d", True)):
        try:
            return datetime.strptime(raw, fmt), is_date
        except ValueError:
            continue
    return None


def period_between(start_text: Any, end_text: Any) -> Period | None:
    """A period from two bounds. None when they make no sense.

    The bounds come in the APP'S ZONE - that is how the person wrote them and how they see them in the chart.
    The history is stored in UTC, though, so they are converted; without that "20 August" in central Europe in
    summer meant 20 August from two in the morning until two in the morning of the next day.

    For a whole day the end is the last day the person wants to see - so the query gets the midnight of the next
    day, otherwise everything after 00:00 would fall out of the last day. An exact time is taken as it came.
    """
    start = _local_time(start_text)
    end = _local_time(end_text)
    if start is None or end is None:
        return None

    start_time, _ = start
    end_time, end_is_date = end
    if end_is_date:
        end_time += timedelta(days=1)
    if end_time <= start_time:
        return None

    zone = formatting.zone()
    def _utc(moment: datetime) -> str:
        return moment.replace(tzinfo=zone).astimezone(UTC).strftime(db.TIME_FORMAT)

    return Period(since=_utc(start_time),
                  until=_utc(end_time),
                  days=max(1, round((end_time - start_time).total_seconds() / 86400)),
                  relative=False,
                  since_local=start_time.strftime(db.TIME_FORMAT),
                  until_local=end_time.strftime(db.TIME_FORMAT),
                  whole_day=start[1] and end_is_date)


def period_from_instants(start_ts: Any, end_ts: Any) -> Period | None:
    """A period from two instants in seconds since the epoch.

    That is how picking by dragging in a chart comes. The browser sends numbers, not text: if it converted by
    itself, it would use the COMPUTER'S zone, whereas the app has its own (Settings -> General). So the server
    does the conversion.
    """
    try:
        start = float(start_ts)
        end = float(end_ts)
    except (TypeError, ValueError):
        return None
    if not (end > start):
        return None

    zone = formatting.zone()
    fmt = "%Y-%m-%d %H:%M"
    try:
        return period_between(
            datetime.fromtimestamp(start, zone).strftime(fmt),
            datetime.fromtimestamp(end, zone).strftime(fmt),
        )
    except (OverflowError, OSError, ValueError):
        # An instant outside the calendar's range. It comes from the address, so slipping in a link with
        # `since_ts=-1e308` is enough for the page to fail with 500 - and `0` brings Windows down already when
        # converting to the local zone. A nonsense period is no server error; there simply is no period.
        return None


def _as_period(spec: Any) -> Period:
    """Takes a number as well as a ready Period. Thanks to that the signatures did not have to change."""
    return spec if isinstance(spec, Period) else period_of_days(int(spec or 30))


def _bounds(spec: Any) -> tuple[str, str]:
    """A pair for a query: (od, do). Unpacked into the parameters with a star."""
    period = _as_period(spec)
    return period.since, period.until


def previous_period(spec: Any) -> Period:
    """An equally long window right before the chosen one - for comparing.

    "Equally long" is measured from the window's bounds, not from the count of days. For whole days both come
    out the same, for a stretch picked by dragging in a chart no more: a two-hour pick has `days == 1`, so it
    would be compared with a whole previous day - and every arrow "compared with the previous period" would
    show a drop that never happened.
    """
    period = _as_period(spec)
    try:
        end = datetime.strptime(period.since, db.TIME_FORMAT)
        # "The last N days" has an open end (END_OF_TIME), so the length cannot be measured from the bounds -
        # there the count of days holds.
        length = (timedelta(days=period.days) if period.relative
                  else datetime.strptime(period.until, db.TIME_FORMAT) - end)
    except ValueError:
        return period
    start = end - length
    # `relative=False`: the previous window has a fixed end (the start of the chosen one), so it is no longer
    # "the last N days".
    zone = formatting.zone()

    def _local(moment: datetime) -> str:
        return (moment.replace(tzinfo=UTC).astimezone(zone)
                .strftime(db.TIME_FORMAT))

    return Period(since=start.strftime(db.TIME_FORMAT), until=period.since,
                  days=period.days, relative=False,
                  since_local=_local(start), until_local=_local(end),
                  whole_day=period.whole_day)


def first_record() -> str:
    """Since when we have any history at all. Empty when we have nothing yet.

    It serves the comparison with the previous period: when the previous window starts earlier than our data
    reach, it is not a comparison of two periods but of a period with emptiness. The percentages come out
    arbitrarily large and say nothing.

    A record older than OLDEST_HISTORY does not count - see there.
    """
    return str(db.query_value(
        "SELECT MIN(started_at) FROM playback"
        " WHERE watched_seconds > 0 AND started_at >= ?",
        (OLDEST_HISTORY,), default="") or "")


def can_compare(spec: Any) -> bool:
    """Does the previous period have any data to compare with at all?"""
    start = first_record()
    if not start:
        return False
    return previous_period(spec).since >= start


def transcode_condition(column: str = "play_method") -> str:
    """The condition "this is a transcode" - one place for all queries.

    It is looked for by the START of the name, not by an exact match. Imported history (Playback Reporting)
    carries more detailed values - say "Transcode (v:h264 a:direct)" - and with an exact match they would fall
    through.

    Until this function it was computed twice differently: the delivery chart by the start long ago (see
    `_role`), whereas the transcode share by an exact match. One page could thus say "Transcode: 1 h" and right
    beside it "Transcode share: 0 %".
    """
    return f"{column} LIKE 'Transcode%'"


def _local_day(local_text: str, utc: str, end: bool = False):
    """The date of a bound in the app's zone.

    The upper bound is always exclusive (the midnight of the day after the last), so for the end a second back
    is returned - otherwise the calendar would have a day extra.
    """
    if local_text:
        moment = datetime.strptime(local_text, db.TIME_FORMAT)
    else:
        moment = (datetime.strptime(utc, db.TIME_FORMAT)
                  .replace(tzinfo=UTC).astimezone(formatting.zone())
                  .replace(tzinfo=None))
    return (moment - timedelta(seconds=1)).date() if end else moment.date()


def chart_days(by_day: list[dict[str, Any]], days: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """The points of a chart by days, with the days when nothing happened too (zero, not a gap).

    `by_day` are rows only for the days when something was (`day` = YYYY-MM-DD in the app's zone, with the
    numbers under `keys`). The chart starts at the period's start (for the whole history at the first day with
    data) and ends at its end, for "the last N days" today. Shared by the *Arr and Seerr cards
    (sekce_<app>.py) - hence here, not with the pages in web/.
    """
    if not by_day:
        return []
    period = _as_period(days)
    first = date.fromisoformat(by_day[0]["day"])
    start = first if period.whole_history else min(first, _local_day(period.since_local, period.since))
    end = datetime.now(formatting.zone()).date() if period.relative \
        else _local_day(period.until_local, period.until, end=True)
    end = max(end, date.fromisoformat(by_day[-1]["day"]))
    by_date = {r["day"]: r for r in by_day}
    points, day = [], start
    while day <= end:
        row = by_date.get(day.isoformat(), {})
        points.append({"day": day.isoformat(), **{k: row.get(k, 0) for k in keys}})
        day += timedelta(days=1)
    return points
