"""The network: how much flowed to the players, the peaks and the live flow.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .. import db, formatting
from .viewers import VIEWER_NAME, VIEWER_GROUP
from .periods import _bounds, _as_period


# ---------------------------------------------------------------------------
# The network - how much flowed to the players
#
# The number worked with here is `playback.bitrate`: the flow of that stream in bits per second. The collector takes
# it from the session - when the server transcodes, it is the resulting (transcoded) flow, otherwise the source file's
# bitrate.
#
# It is **an estimate by the declared bitrate, not a measurement of the wire.** Seeking in the player and the buffer
# mean the bytes really transferred differ. Only a reverse proxy or the system's counters know the exact number -
# outside Jellyfin. The page says so too, so that nobody takes it for a measurement.
#
# Pauses are subtracted, though: `watched_seconds` is counted, i.e. the time of real watching. A paused play pulls
# no data, so the chart would otherwise show it as running - see bandwidth_timeline().
# ---------------------------------------------------------------------------

def _network_rows(days: int) -> list[dict[str, Any]]:
    """The period's plays with a known bitrate. The basis for everything else."""
    return db.query_all(
        """
        SELECT started_at, ended_at, last_seen_at, watched_seconds, bitrate,
               user_name, client, device_name, remote_address, play_method
          FROM playback
         WHERE started_at >= ? AND started_at < ?
           AND watched_seconds > 0
           AND bitrate IS NOT NULL AND bitrate > 0
         ORDER BY started_at
        """,
        (*_bounds(days),),
    )


def _seconds(text: Any) -> float | None:
    """A time from the database to seconds. None when it cannot be read.

    `replace(tzinfo=utc)` is not cosmetics. The database has the time in UTC and without a zone; but
    `datetime.timestamp()` takes such a time as LOCAL, so the result was shifted by the whole offset. The labels
    under the chart then showed UTC, while the rest of the app local time - the evening peak thus "came" an hour
    earlier in summer than it really was.

    The rest of the app does it the same way, see formatting._parse_any().
    """
    try:
        moment = datetime.strptime(str(text).replace("T", " ")[:19], db.TIME_FORMAT)
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=UTC).timestamp()


def bandwidth_timeline(days: int, points: int = 120) -> list[dict[str, Any]]:
    """The concurrent flow over time - how many Mbit/s flowed from the server at once.

    It is computed **by walking the events**, not by sampling: every play adds its bitrate at the start and takes
    it away at the end. Sorting the events by time gives the exact curve of the concurrent flow, whether a play
    lasted a minute or five hours.

    The chart takes the maximum of every step from it - an average would smooth the peaks, and the peak is exactly
    what interests: the line is sized by it.
    """
    rows = _network_rows(days)
    if not rows:
        return []

    events: list[tuple[float, int]] = []
    for row in rows:
        start = _seconds(row["started_at"])
        end = _seconds(row["ended_at"] or row["last_seen_at"])
        if start is None:
            continue
        if end is None or end <= start:
            end = start + float(row["watched_seconds"] or 0)
        # A pause does not flow. Without this limit the bitrate spread over the whole time from the start to the
        # end - so a movie paused overnight held the full flow in the chart until the morning, and so did a play
        # paused RIGHT NOW (its `last_seen_at` keeps running).
        #
        # When exactly it was paused the database does not know - only the sum `watched_seconds` is stored. So we
        # take the flow's length by it: the flow lasts as long as the real watching. With a pause in the middle it
        # is therefore shifted forward, but its **amount and height** fit - and the sum under the curve finally
        # matches the volume of data the page shows beside it.
        watched = float(row["watched_seconds"] or 0)
        if watched > 0:
            end = min(end, start + watched)

        events.append((start, int(row["bitrate"])))
        events.append((end, -int(row["bitrate"])))

    if not events:
        return []
    events.sort()

    first_ts, last_ts = events[0][0], events[-1][0]
    step = max((last_ts - first_ts) / max(1, points), 1.0)

    chart_points: list[dict[str, Any]] = []
    total = 0
    boundary = first_ts + step
    peak = 0
    for moment, change in events:
        while moment > boundary:
            chart_points.append({"time": boundary - step / 2, "mbit": round(peak / 1e6, 2)})
            boundary += step
            peak = total
        total += change
        peak = max(peak, total)
    chart_points.append({"time": boundary - step / 2, "mbit": round(peak / 1e6, 2)})

    for point in chart_points:
        # The same zone as for times elsewhere - see formatting.zone(). The chart's axis and the column "when" in the
        # history must say the same, otherwise a person looks for the same record at two different times.
        point["label"] = datetime.fromtimestamp(
            point["time"], formatting.zone()).strftime("%d.%m. %H:%M")
        # How long a step one point stands for. People often ask the chart "how often is it measured" - it is not
        # measured at all, the starts and ends of plays are walked and the peak of every step is taken. Without
        # this value the curve looks random; with it one sees that one peak can be the only hour out of six.
        point["step_minutes"] = round(step / 60)
    return chart_points


def flow_now() -> dict[str, Any]:
    """How much flows from the server RIGHT NOW - from the running plays.

    Unlike the curve below nothing is computed from the history here: what the collector saw at its last query is
    taken. Paused plays do not count, because nothing flows during a pause - but they are counted separately, so
    that one sees they are there.
    """
    rows = db.query_all(
        "SELECT bitrate, is_paused, play_method, user_name, item_name "
        "FROM playback WHERE is_active = 1")

    flowing = [r for r in rows if not r["is_paused"] and (r["bitrate"] or 0) > 0]
    return {
        "mbit": round(sum(int(r["bitrate"]) for r in flowing) / 1e6, 2),
        "stream_count": len(flowing),
        "paused_count": sum(1 for r in rows if r["is_paused"]),
        "without_traffic": sum(1 for r in rows
                        if not r["is_paused"] and not (r["bitrate"] or 0)),
        "recounted": sum(1 for r in flowing
                        if str(r["play_method"] or "").lower().startswith("transcode")),
    }


# The shortest window of the live curve. Going below one day makes no sense: an hour's cut says "nothing flows right
# now" even in an evening of playing all the time, when the last episode just ended.
SHORTEST_LIVE_WINDOW = 24 * 3600


# How long a step one point of the live curve stands for and how many points at most.
#
# 180 points over a week meant one point per 56 minutes - and because the step's peak is taken, a ten-minute stream
# stretched to an hour in the chart. With five minutes the curve's shape matches what really happened; the cap is
# for the picture's size.
LIVE_STEP_SECONDS = 300
# The cap of points. Originally 1500, but for a week that made fur: a peak one pixel wide that the mouse does not
# hit. Six hundred points mean a five-minute step for a day and a quarter-hour step for a week - that shows as a
# column, not as a hair. Longer periods belong to the card "Peak by day".
MAX_LIVE_POINTS = 600


def bandwidth_live(days: Any = None, points: int | None = None) -> list[dict[str, Any]]:
    """The flow over time for the live window - by the chosen period, at least 24 hours.

    Why separately and not just another range of the curve below: that one takes plays by when they STARTED, so a
    stream started the day before yesterday would not fall into the window at all, although it flows right now.
    Here plays that **overlap** the window are looked for.

    `OR is_active = 1` in the condition is no extra safety. A running play flows even when the collector last
    reported a while ago - say because Jellyfin did not answer for a moment. Without it the curve would drop to
    zero, while the number beside it would show the full flow.
    """
    now_ts = datetime.now(UTC).timestamp()

    # The window follows the page's filter. The number beside the curve is still "now", but the curve shows what the
    # person chose at the top - otherwise the filter would do nothing on that card and seem broken.
    window = SHORTEST_LIVE_WINDOW
    window_end = now_ts
    if days is not None:
        period = _as_period(days)
        start = _seconds(period.since)
        end = min(_seconds(period.until) or now_ts, now_ts)
        if start is not None and end > start:
            if period.relative:
                # "The last N days" end now and do not go below a day - see SHORTEST_LIVE_WINDOW.
                window = max(SHORTEST_LIVE_WINDOW, end - start)
            else:
                # A fixed period (clicked, or picked by dragging in a chart) is taken EXACTLY as it reads - the end in
                # the past included. If the curve still ended "now", it would show something other than the switch
                # above it says - and a second drag would then pick quite another time than the person points at.
                window, window_end = end - start, end
    window_start = window_end - window

    # The resolution by the window's length, not a fixed count of points.
    if points is None:
        points = int(min(MAX_LIVE_POINTS, max(60, window // LIVE_STEP_SECONDS)))

    rows = db.query_all(
        """
        SELECT started_at, ended_at, last_seen_at, watched_seconds, bitrate,
               is_paused, is_active
          FROM playback
         WHERE bitrate IS NOT NULL AND bitrate > 0
           AND watched_seconds > 0
           AND (COALESCE(ended_at, last_seen_at) >= ? OR is_active = 1)
         ORDER BY started_at
        """,
        (datetime.fromtimestamp(window_start, UTC).strftime(db.TIME_FORMAT),),
    )

    events: list[tuple[float, int]] = []
    for row in rows:
        start = _seconds(row["started_at"])
        if start is None:
            continue
        end = _seconds(row["ended_at"] or row["last_seen_at"]) or now_ts
        # A play paused right now does not flow - we put the end where it last played. For the others the same
        # reasoning holds as for the long curve: the flow lasts as long as the real watching.
        watched = float(row["watched_seconds"] or 0)
        if watched > 0:
            end = min(end, start + watched)
        if row["is_active"] and not row["is_paused"]:
            # A running stream flows now too, even when the collector reported a while ago.
            end = max(end, now_ts)
        if end <= window_start:
            continue
        events.append((max(start, window_start), int(row["bitrate"])))
        events.append((end, -int(row["bitrate"])))

    step = (window_end - window_start) / max(1, points)
    chart_points: list[dict[str, Any]] = []
    events.sort()
    total = 0
    streams = 0            # how many streams flow at once
    index = 0
    for i in range(points):
        boundary = window_start + (i + 1) * step
        peak = total
        peak_streams = streams
        while index < len(events) and events[index][0] <= boundary:
            total += events[index][1]
            streams += 1 if events[index][1] > 0 else -1
            if total > peak:
                peak = total
                peak_streams = streams
            index += 1
        middle = boundary - step / 2
        # For a short window the hour and minute are enough; as soon as the curve exceeds two days, the date must show
        # too - otherwise Wednesday and Thursday cannot be told apart on the axis.
        fmt = "%H:%M" if window <= 48 * 3600 else "%d.%m. %H:%M"
        chart_points.append({
            "time": middle,
            "mbit": round(max(0, peak) / 1e6, 2),
            # How many streams made that peak. In the bubble it answers the second question one has about a peak: was
            # it many people, or one movie in 4K?
            "stream_count": max(0, peak_streams),
            "label": datetime.fromtimestamp(middle, formatting.zone()).strftime(fmt),
            "step_minutes": round(step / 60) or 1,
        })
    return chart_points


def bandwidth_daily_peaks(days: int) -> list[dict[str, Any]]:
    """The highest concurrent flow of every day.

    Over a longer period drawing every minute makes no sense - there would be more points than pixels and the
    curve would look like noise. One day = one number, the highest one: the line is sized by the peak, an average
    would smooth it.
    """
    rows = _network_rows(days)
    if not rows:
        return []

    events: list[tuple[float, int]] = []
    for row in rows:
        start = _seconds(row["started_at"])
        end = _seconds(row["ended_at"] or row["last_seen_at"])
        if start is None:
            continue
        if end is None or end <= start:
            end = start + float(row["watched_seconds"] or 0)
        watched = float(row["watched_seconds"] or 0)
        if watched > 0:
            end = min(end, start + watched)
        events.append((start, int(row["bitrate"])))
        events.append((end, -int(row["bitrate"])))

    if not events:
        return []
    events.sort()

    # We walk the events and remember the highest sum of every day. The day is decided in the APP'S ZONE - otherwise a
    # peak around midnight would fall into another day than the one the person lived it in.
    #
    # A flow that runs across midnight belongs to the new day too, although nothing starts or ends there at that moment:
    # before the first event of a new day, the sum that was flowing is that day's peak so far - and the peak of every
    # whole day in between without an event (a play longer than a day). Without it a film from 23:00 to 01:00 gave the
    # second day the peak 0.
    zone = formatting.zone()
    peaks: dict[str, float] = {}
    total = 0
    current: date | None = None
    for moment, change in events:
        when = datetime.fromtimestamp(moment, zone)
        if current is not None and when.date() > current and total > 0:
            day = current + timedelta(days=1)
            while day < when.date():
                key = day.isoformat()
                peaks[key] = max(peaks.get(key, 0), total)
                day += timedelta(days=1)
            if when.replace(hour=0, minute=0, second=0, microsecond=0) < when:      # it flowed after midnight
                key = when.date().isoformat()
                peaks[key] = max(peaks.get(key, 0), total)
        current = when.date()
        total += change
        day_key = when.strftime("%Y-%m-%d")
        peaks[day_key] = max(peaks.get(day_key, 0), total)

    return [{"day": day,
             "label": datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m."),
             "mbit": round(value / 1e6, 2)}
            for day, value in sorted(peaks.items())]


def bandwidth_summary(days: int) -> dict[str, Any]:
    """The peak, the volume of data and the transcode share. The numbers above the chart."""
    rows = _network_rows(days)
    timeline = bandwidth_timeline(days, points=600)

    total_bytes = 0.0
    transcode_bytes = 0.0
    for row in rows:
        volume = float(row["bitrate"]) * float(row["watched_seconds"] or 0) / 8.0
        total_bytes += volume
        if str(row["play_method"] or "") == "Transcode":
            transcode_bytes += volume

    peak = max(timeline, key=lambda b: b["mbit"], default=None)
    return {
        "peak_mbit": peak["mbit"] if peak else 0.0,
        "peak_at": peak["label"] if peak else "",
        "byte_count": int(total_bytes),
        "byte_count_transcode": int(transcode_bytes),
        "transcode_share": round(transcode_bytes / total_bytes * 100, 1) if total_bytes else 0.0,
        "playbacks": len(rows),
        "average_mbit": (round(sum(float(r["bitrate"]) for r in rows)
                              / len(rows) / 1e6, 2) if rows else 0.0),
    }


def bandwidth_by(days: int, column: str, limit: int = 12) -> list[dict[str, Any]]:
    """The volume of data by user, client or device - from the largest.

    `column` is compared against a fixed list, so nothing from the address ever gets into the SQL.
    """
    allowed = {"user_name", "client", "device_name"}
    if column not in allowed:
        column = "user_name"
    # Viewers by viewer (see VIEWER_NAME), clients and devices by name.
    by_viewer = column == "user_name"
    label_sql = f"COALESCE({VIEWER_NAME}, '?')" if by_viewer else f"COALESCE(p.{column}, '?')"
    group_sql = VIEWER_GROUP if by_viewer else f"COALESCE(p.{column}, '?')"
    join_sql = "LEFT JOIN users u ON u.id = p.user_id" if by_viewer else ""

    rows = db.query_all(
        f"""
        SELECT {label_sql}                                       AS label,
               SUM(p.watched_seconds * p.bitrate) / 8.0        AS byte_count,
               MAX(p.bitrate)                                  AS peak,
               COUNT(*)                                        AS plays,
               -- The id of the user for the link to their detail. MIN, because
               -- PostgreSQL allows no column outside GROUP BY; for viewers
               -- user_id is in the group, so it is always the same person.
               MIN(p.user_id)                                  AS user_id
          FROM playback p
          {join_sql}
         WHERE p.started_at >= ? AND p.started_at < ?
           AND p.watched_seconds > 0
           AND p.bitrate IS NOT NULL AND p.bitrate > 0
         GROUP BY {group_sql}
         ORDER BY SUM(p.watched_seconds * p.bitrate) DESC
         LIMIT ?
        """,
        (*_bounds(days), limit),
    )
    # The chart gets gigabytes, not bytes: a column labelled "98 739 089 379 B" cannot be read, and precision to a
    # byte is of no use here.
    return [{"label": r["label"], "byte_count": int(r["byte_count"] or 0),
             "gb": round(float(r["byte_count"] or 0) / 1e9, 1),
             "peak_mbit": round(float(r["peak"] or 0) / 1e6, 2),
             "plays": int(r["plays"] or 0),
             # Only for people - a link to "Chrome" or "Shield TV" leads nowhere, we have no such page.
             "user_id": r["user_id"] if column == "user_name" else None}
            for r in rows]
