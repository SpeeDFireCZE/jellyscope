"""The period's summary: the overview, comparing two periods, watching by day, play methods, players, the heatmap.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db, formatting
from ..i18n import translate as _t
from .viewers import top_users
from .kinds import KIND_BOTH, KIND_OTHER, SERIES_TYPES, _kind_condition, watching_only, type_label
from .periods import Period, _bounds, _local_day, _as_period, transcode_condition, previous_period
from .growth import library_growth
from .network import bandwidth_summary
from .titles import extra_copies, top_items


# ---------------------------------------------------------------------------
# The overview (dashboard)
# ---------------------------------------------------------------------------

def overview(days: int) -> dict[str, Any]:
    """The main numbers of the chosen period. Different titles: a movie's copy is one title (extra_copies)."""
    row = db.query_one(
        f"""
        SELECT
            COUNT(*)                                        AS plays,
            COALESCE(SUM(watched_seconds), 0)               AS watched_seconds,
            COUNT(DISTINCT user_id)                         AS users,
            COUNT(DISTINCT item_id)                         AS item_count,
            COALESCE(SUM(CASE WHEN {transcode_condition()}
                              THEN watched_seconds ELSE 0 END), 0) AS transcoded_seconds
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        """,
        (*_bounds(days),),
    ) or {}

    watched = row.get("watched_seconds") or 0
    transcoded = row.get("transcoded_seconds") or 0
    row["transcode_share"] = (transcoded / watched * 100) if watched else 0.0
    row["item_count"] = (row.get("item_count") or 0) - extra_copies(_bounds(days), "p.watched_seconds > 0")
    return row


def previous_overview(days: int) -> dict[str, Any]:
    """The same numbers for the previous equally long period - for comparing.

    Without this function a stat "142 hours" would say nothing. Beside "+18 % against the previous period" it is
    information.
    """
    return db.query_one(
        f"""
        SELECT
            COUNT(*)                          AS plays,
            COALESCE(SUM(watched_seconds), 0) AS watched_seconds,
            COUNT(DISTINCT user_id)           AS users
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        """,
        _bounds(previous_period(days)),
    ) or {}



# ---------------------------------------------------------------------------
# Two periods side by side
# ---------------------------------------------------------------------------

def length_seconds(spec: Any) -> float:
    """How long the period really is - in seconds.

    It is measured from the bounds, not from the count of days. For whole days both come out the same, for a stretch
    picked by dragging in a chart no more: a two-hour pick has `days == 1` and the daily average would come out twelve
    times smaller than it is.

    "The last N days" has an open end (END_OF_TIME), so the length cannot be measured from the bounds - there the
    count of days holds.
    """
    period = _as_period(spec)
    if period.relative:
        return period.days * 86400.0
    try:
        start = datetime.strptime(period.since, db.TIME_FORMAT)
        end = datetime.strptime(period.until, db.TIME_FORMAT)
    except ValueError:
        return max(1, period.days) * 86400.0
    return max(1.0, (end - start).total_seconds())


def _overlap(first: Period, second: Period) -> bool:
    """Do the periods share a piece of time?

    Two overlapping periods are not "August versus December": part of the data is in both columns and the difference
    between them measures nothing. Saying it out loud is more honest than computing it and staying silent.
    """
    return first.since < second.until and second.since < first.until


def _row(label: str, fmt: str, value_a: Any, value_b: Any) -> dict[str, Any]:
    """One row of the comparison table with the difference.

    `relative` (by how many percent) is not computed for percentage values: "from 12 % to 18 %" is +6 PERCENTAGE
    POINTS, not +50 %. The second number is formally right and confusing in practice, so it is not given at all.
    """
    a = float(value_a or 0)
    b = float(value_b or 0)
    return {"label": label, "form": fmt,
            "a": value_a or 0, "b": value_b or 0,
            "difference": a - b,
            "relative": None if fmt == "percent" or not b else (a - b) / b * 100}


# The delivery method's role -> the label in the table. It sums by ROLE, not by the exact name: imported history
# carries values like "Transcode (v:h264 a:direct)" and with a match on the exact string they would fall through, so
# the transcode from an import would be missing from the comparison.
DELIVERY_ROLES: tuple[tuple[str, str], ...] = (
    ("good", "Direct play"),
    ("info", "Remux (direct stream)"),
    ("serious", "Transcode"),
)


def _language_rows(first: Any, second: Any) -> list[dict[str, Any]]:
    """How much was watched in which language - in both periods.

    `langstats` only here, not at the top: stats is the lowest floor and is not to tie itself to anything high. It
    would not make a cycle today, but the order of imports will never have to be solved because of it.

    The rows are the UNION of both periods. A language that is only in one must show - "in December watching in
    Slovak started" is exactly the answer a person looks at the comparison for. With an intersection it would quietly
    drop out of the table.

    Beside the hours goes the share too. With periods of different length the share is the only honest number: a
    year has more hours of anything watched than August, but the ratio of Czech and English does not change by it.
    """
    from .. import langstats

    colours = langstats.colour_map()
    a = {r["code"]: r for r in langstats.watched_languages(first, colours)["rows"]}
    b = {r["code"]: r for r in langstats.watched_languages(second, colours)["rows"]}

    codes = sorted(set(a) | set(b),
                   key=lambda k: -max(float((a.get(k) or {}).get("hours") or 0),
                                      float((b.get(k) or {}).get("hours") or 0)))

    rows = []
    for code in codes:
        first_row, second_row = a.get(code) or {}, b.get(code) or {}
        row = _row(str(first_row.get("label") or second_row.get("label") or code),
                   "hours",
                   first_row.get("hours") or 0, second_row.get("hours") or 0)
        row["share_a"] = float(first_row.get("percent") or 0)
        row["share_b"] = float(second_row.get("percent") or 0)
        rows.append(row)
    return rows


def comparison(first_spec: Any, second_spec: Any) -> dict[str, Any]:
    """The same statistics for two arbitrary periods, side by side.

    "Against the previous period" the app has known for long, but a person does not choose the previous period - it
    is the window right before the chosen one. It does not answer the question "was August better than December?".

    Two things that are easy to keep quiet about here, and must not be:

    * **Periods of different length.** By the sum the longer period wins almost always. So the daily average is right
      under the sum and the page says the lengths differ.
    * **An overlap.** When the periods share a piece, part of the data is in both columns and the difference measures
      nothing.
    """
    first_period, second_period = _as_period(first_spec), _as_period(second_spec)
    seconds_a, seconds_b = length_seconds(first_period), length_seconds(second_period)

    overview_a, overview_b = overview(first_period), overview(second_period)
    network_a, network_b = bandwidth_summary(first_period), bandwidth_summary(second_period)

    def hours_of(summary: dict[str, Any]) -> float:
        return float(summary.get("watched_seconds") or 0) / 3600.0

    def daily(summary: dict[str, Any], seconds: float) -> float:
        return hours_of(summary) / max(1.0, seconds / 86400.0)

    def by_role(spec: Any) -> dict[str, float]:
        totals: dict[str, float] = {}
        for row in play_method_breakdown(spec):
            role = str(row.get("role") or "")
            totals[role] = totals.get(role, 0.0) + float(row.get("hours") or 0)
        return totals

    role_a, role_b = by_role(first_period), by_role(second_period)

    groups = [
        {
            "heading": _t("Watching"),
            "rows": [
                _row(_t("Watched"), "hours", hours_of(overview_a), hours_of(overview_b)),
                _row(_t("Daily average"), "hours",
                     daily(overview_a, seconds_a), daily(overview_b, seconds_b)),
                _row(_t("Plays#starts"), "number",
                     overview_a.get("plays"), overview_b.get("plays")),
                _row(_t("Viewers"), "number",
                     overview_a.get("users"), overview_b.get("users")),
                _row(_t("Distinct titles"), "number",
                     overview_a.get("item_count"), overview_b.get("item_count")),
            ],
        },
        {
            "heading": _t("How the server delivered the content"),
            "rows": [
                _row(_t(label), "hours",
                     role_a.get(role, 0.0), role_b.get(role, 0.0))
                for role, label in DELIVERY_ROLES
            ] + [
                _row(_t("Transcode share"), "percent",
                     overview_a.get("transcode_share"),
                     overview_b.get("transcode_share")),
            ],
        },
        {
            "heading": _t("Network"),
            "rows": [
                _row(_t("Transferred"), "bytes",
                     network_a.get("byte_count"), network_b.get("byte_count")),
                _row(_t("Peak"), "mbit",
                     network_a.get("peak_mbit"), network_b.get("peak_mbit")),
            ],
        },
    ]

    # The languages have a varying count of rows, so the group is added only when there is something to show - an empty
    # table with a heading claims nobody watched, which is other information than "we do not know the stream's
    # language".
    languages = _language_rows(first_period, second_period)
    if languages:
        groups.append({"heading": _t("Which language was watched in"),
                       "rows": languages, "with_share": True})

    # The library stands on the daily snapshots, and those start only with the version that can write them. So they
    # are not there for older periods - and instead of a table of zeros it says why. "Nothing was added" and "we do
    # not know" are two different things.
    growth_a, growth_b = library_growth(first_period), library_growth(second_period)
    library_known = bool(growth_a.get("enough_data") and growth_b.get("enough_data"))
    library_backfilled = bool(growth_a.get("backfilled") or growth_b.get("backfilled"))
    if library_known:
        groups.append({
            "heading": _t("Library"),
            "rows": [
                _row(_t("Items added"), "number",
                     growth_a.get("item_count"), growth_b.get("item_count")),
                _row(_t("Data added"), "bytes",
                     growth_a.get("increase"), growth_b.get("increase")),
                _row(_t("Size at the end of the period"), "bytes",
                     growth_a.get("size"), growth_b.get("size")),
            ],
        })

    return {
        "a": {"period": first_period, "days": seconds_a / 86400.0,
              "top": top_items(first_period, limit=5, kind=KIND_BOTH),
              "users": top_users(first_period, limit=5)},
        "b": {"period": second_period, "days": seconds_b / 86400.0,
              "top": top_items(second_period, limit=5, kind=KIND_BOTH),
              "users": top_users(second_period, limit=5)},
        "groups": groups,
        # A difference of lengths up to one percent is rounding, not a difference.
        "same_length": abs(seconds_a - seconds_b) <= max(seconds_a, seconds_b) * 0.01,
        "overlap": _overlap(first_period, second_period),
        # Is there anything to compare at all? Two empty periods side by side are a table of zeros and it is better to
        # say why.
        "has_data": bool((overview_a.get("plays") or 0) or (overview_b.get("plays") or 0)),
        # When we know nothing about the library for some period, the page says so instead of the group quietly missing.
        "library_known": library_known,
        # And when a part stands on the backfill from the items' creation dates, it says that too: the backfill
        # describes the past with today's sizes.
        "library_backfilled": library_backfilled,
    }


def others_breakdown(days: int) -> list[dict[str, Any]]:
    """What "Other" is made of - by kind, from the largest.

    Without this "Other" is a blind spot in the chart: a person sees hours, but has no way to find out what it was. It
    is used as the label under the chart and in the legend's bubble.
    """
    rows = db.query_all(
        f"""
        SELECT item_type,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {_kind_condition(KIND_OTHER)}
        GROUP BY item_type
        ORDER BY SUM(watched_seconds) DESC
        """,
        (*_bounds(days),),
    )
    # We merge by the label, not by the raw value: an empty string and NULL are two different groups for SQL, but one
    # and the same thing for a person - a record whose kind we do not know. Without it the unknown-type label would
    # show twice in a row under the chart with different numbers.
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        label = type_label(r["item_type"])
        merged_row = merged.setdefault(
            label, {"item_type": r["item_type"], "label": label,
                    "hours": 0.0, "plays": 0})
        merged_row["hours"] += float(r["hours"] or 0)
        merged_row["plays"] += int(r["plays"] or 0)

    for merged_row in merged.values():
        merged_row["hours"] = round(merged_row["hours"], 2)
    return sorted(merged.values(), key=lambda p: p["hours"], reverse=True)


def daily_activity_split(days: int) -> list[dict[str, Any]]:
    """Watching by day split into movies and series.

    Returns one row per day with four numbers: hours and plays separately for movies and for series. The template
    makes the sum - if it were sent precomputed, the data would have to be pulled again when switching the filter.

    Why a split by the item's type and not by library: everyone names a library differently ("Movies", "Films",
    "4K"), Jellyfin reports the item's type always the same.
    """
    rows = db.query_all(
        f"""
        SELECT date(started_at, 'localtime') AS day,
               item_type,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*)                      AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        GROUP BY day, item_type
        ORDER BY day
        """,
        (*_bounds(days),),
    )

    # "other" is the third bag for everything that is neither a movie nor an episode: concerts, home videos - and above
    # all imported history we have no item in the library for yet, so we do not know the type.
    #
    # Without it such records got lost completely: the sum was computed as movies + series, so after an import into an
    # empty library the chart stayed empty, although the hours in the summary above it fit.
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        entry = by_day.setdefault(
            str(row["day"]),
            {"movie_hours": 0.0, "movie_plays": 0, "series_hours": 0.0,
             "series_plays": 0, "other_hours": 0.0, "other_plays": 0},
        )
        prefix = ("movie" if row["item_type"] == "Movie"
                  else "series" if row["item_type"] in SERIES_TYPES
                  else "other")
        entry[f"{prefix}_hours"] += float(row["hours"] or 0)
        entry[f"{prefix}_plays"] += int(row["plays"] or 0)

    # We draw the whole calendar, so that the chart shows a day when nobody watched too.
    #
    # For "the last N days" the last day is today and the first the N-th back - i.e. N rows, not N+1. A custom period
    # starts and ends where the person said.
    window = _as_period(days)
    # All data here are already converted to the app's zone (see 'localtime' in the query above) - so the calendar must
    # be built from the same clock. The window's bounds are in UTC; if the day were taken from them directly, the chart
    # would start a day earlier in summer, because midnight in Prague is 22:00 of the previous day in UTC.
    today = datetime.now(formatting.zone()).date()
    if window.relative:
        first_day, last_day = today - timedelta(days=window.days - 1), today
    else:
        first_day = _local_day(window.since_local, window.since)
        last_day = min(today, _local_day(window.until_local, window.until, end=True))
    calendar = [first_day + timedelta(days=offset)
                for offset in range(max(1, (last_day - first_day).days + 1))]

    result = []
    for day in calendar:
        entry = by_day.get(day.isoformat(), {})
        movie_hours = round(float(entry.get("movie_hours") or 0.0), 2)
        series_hours = round(float(entry.get("series_hours") or 0.0), 2)
        other_hours = round(float(entry.get("other_hours") or 0.0), 2)
        movie_plays = int(entry.get("movie_plays") or 0)
        series_plays = int(entry.get("series_plays") or 0)
        other_plays = int(entry.get("other_plays") or 0)
        result.append({
            "day": day.isoformat(),
            "movie_hours": movie_hours,
            "series_hours": series_hours,
            "other_hours": other_hours,
            "movie_plays": movie_plays,
            "series_plays": series_plays,
            "other_plays": other_plays,
            # The sum of all three, so that "Total" in the table matches reality even when we do not know the type.
            "hours": round(movie_hours + series_hours + other_hours, 2),
            "plays": movie_plays + series_plays + other_plays,
        })
    return result


def play_method_breakdown(days: int) -> list[dict[str, Any]]:
    """How much time played directly and how much had to be transcoded."""
    rows = db.query_all(
        f"""
        SELECT COALESCE(play_method, 'unknown') AS method,
               SUM(watched_seconds) / 3600.0    AS hours
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        GROUP BY method
        ORDER BY hours DESC
        """,
        (*_bounds(days),),
    )
    # The labels go through `_t()`, because they are shown to the user - in the legend as well as right in the chart's
    # bar.
    labels = {
        "DirectPlay": _t("Direct play"),
        "DirectStream": _t("Remux (direct stream)"),
        "Transcode": _t("Transcode"),
        "unknown": _t("Unknown"),
    }
    # The colour by what it costs the server - not by the order in the list. These three values are not arbitrary
    # categories but a scale: direct play is no work, remuxing almost none, a transcode loads the CPU or the graphics
    # card. The chart thus says the same as the badge at a play and the amber piece at the end is a warning.
    # Exactly the same roles the badge at a play carries - see the macro `method_badge`. When they differed (an orange
    # badge, an amber chart), it looked like two different things; yet it is the same value once as a state and once
    # as a share.
    # The role is looked for by the START of the name, not by an exact match. Imported history (Playback Reporting)
    # carries more detailed values - say "Transcode (v:h264 a:direct)" - and with an exact match they would fall into
    # "muted", so they would grey out in the chart although they are a transcode.
    def _role(method: str) -> str:
        m = (method or "").strip().lower()
        if m.startswith("transcode"):
            return "serious"
        if m.startswith("directstream") or m.startswith("remux"):
            return "info"
        if m.startswith("directplay") or m.startswith("direct play"):
            return "good"
        return "muted"

    # Several transcode variants one under another would have one colour and could not be told apart. The first keeps
    # the role's orange - so that one sees at first sight it is a transcode -, the next take colours from the series
    # palette, i.e. the same one "who watches in which language" uses.
    #
    # Shades of one colour were here before and did not work: the parts tend to be a few pixels narrow and three shades
    # of brown blend on such an area.
    MORE_COLOURS = ("var(--series-5)", "var(--series-7)", "var(--series-4)",
                    "var(--series-8)", "var(--series-2)", "var(--series-6)")
    order_count: dict[str, int] = {}
    for row in rows:
        row["label"] = labels.get(row["method"], row["method"])
        row["role"] = _role(row["method"])
        order_count[row["role"]] = order_count.get(row["role"], -1) + 1
        index = order_count[row["role"]]
        if index:
            row["color"] = MORE_COLOURS[(index - 1) % len(MORE_COLOURS)]
        # The split bar chart expects the key "value" - we prepare it here, so that the template does not have to compute
        # anything.
        row["value"] = row["hours"]
    return rows


def client_breakdown(days: int, limit: int = 8) -> list[dict[str, Any]]:
    return db.query_all(
        f"""
        SELECT COALESCE(client, 'unknown') AS label,
               SUM(watched_seconds) / 3600.0 AS hours,
               COUNT(*) AS plays
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        GROUP BY label
        ORDER BY hours DESC, label
        LIMIT ?
        """,
        (*_bounds(days), limit),
    )


def hourly_heatmap(days: int) -> list[list[float]]:
    """A grid of 7 days x 24 hours with the count of watched hours.

    Returns a list of seven rows (Monday to Sunday), each of 24 values.
    """
    rows = db.query_all(
        f"""
        SELECT CAST(strftime('%w', started_at, 'localtime') AS INTEGER) AS weekday,
               CAST(strftime('%H', started_at, 'localtime') AS INTEGER) AS hour,
               SUM(watched_seconds) / 3600.0 AS hours
        FROM playback
        WHERE started_at >= ? AND started_at < ?
          AND watched_seconds > 0
          {watching_only()}
        GROUP BY weekday, hour
        """,
        (*_bounds(days),),
    )

    grid = [[0.0 for _ in range(24)] for _ in range(7)]
    for row in rows:
        # SQLite counts the week from Sunday (0). We want it from Monday.
        weekday = (int(row["weekday"]) + 6) % 7
        grid[weekday][int(row["hour"])] += float(row["hours"] or 0)
    return grid
