"""Prowlarr's statistics: queries to the indexers, the indexers' performance and grabbed releases.

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/arr/prowlarr/sync.py puts the data
into the database: queries as **hourly sums** (`prowlarr_hour`), grabbed releases one by one
(`prowlarr_grab`), the indexers' names in `prowlarr_indexer`.

All functions take `days` - anything `_as_period` takes (a count of days or a ready Period). The database sums; only
finished sums go into memory (by hour, by indexer, by app). A page asks for them once (`prowlarr_analysis`) and hands
them to every statistic as `analysis=`.

Notions:

* **A search** (`query`) is a query for a specific title, **RSS** the regular query "what is new". **Signing in**
  (`auth`) to an indexer does not belong among the queries: the indexer says separately how many times it failed.
* **The mean response** is the sum of responses divided by their count over the whole period (not a mean of the
  hourly means) and only from successful queries that did not come from Prowlarr's cache.
* **Found something** - the share of searches that returned at least one release out of all searches the indexer
  answered. A failed search says nothing about the indexer's content and has its own column.
* **A grabbed release** Prowlarr either downloads itself (`proxy` - it knows the result and the time), or only
  redirects the app to the indexer (`redirect`, always with usenet - it does not know the result and writes it
  as successful). Failures and the time are therefore counted only from the downloaded ones; where there was none,
  it is None.

The hours are stored in UTC. An hour belongs to the period - and to the day in the app's zone - where its start
belongs: the sum of one hour cannot be split any more. With zones shifted by half an hour the day thus ends half an
hour earlier.

Where there is nothing to compute from (no query, no response), it is None, not zero.

**The indexers' availability** (`prowlarr_availability`) comes from the samples of their status
(`prowlarr_sample`, written by jellyscope/arr/prowlarr/samples.py): now and then Jellyscope notes which indexers
Prowlarr leaves out because of failures, which indexers were there and enabled, and the usual interval between
samples in force then. An indexer's time is **measured** only between two samples it was there and enabled in - an
indexer added later, removed or switched off by hand is not measured where it was not there - and only when they
are at most twice the larger of their two stored intervals apart (`GAP_TIMES`): a longer gap (Jellyscope did not
run, Prowlarr did not answer) says nothing either way, and changing the task's interval today changes no past gap.
An **outage** starts at the indexer's "failing since", but not before the last sample without it, and ends at the
first later sample without it (or without the indexer); while the last sample is younger than its longest gap, the
time up to now is measured too (the next sample is not late yet) and an outage seen in it is still running. The
availability is the share of the indexer's measured time outside outages, the outages' lengths count only measured
time, and an outage crossing the period's edge counts only within the period.

**The background loop's samples** (arr/prowlarr/watch.py) are one moment and a stretch: the loop asks every interval
and stores a new sample only when the state changes (and every ten minutes); until then it moves the sample's
`measured_until` to its last round. The whole stretch up to it is measured - the loop saw the same state at every
round - and the step from it to the loop's next sample counts only when it is one round long (`LOOP_GAP_TIMES` the
interval): a round Prowlarr did not answer makes it two, the app down longer still. Other samples may fall inside a
stretch - the loop of a second app process on the same database, the task's or a page's sample: every sample's
indexers are left out up to its own last round, and a step inside any loop sample's stretch is measured, once. The
availability also says the intervals the measured time was measured with (`intervals`).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple

from .. import db, formatting
from .periods import END_OF_TIME, OLDEST_HISTORY, _as_period, _local_day
from .shared import _local, _time

QUERY_KINDS = ("query", "rss")
_IN_PERIOD = "hour >= ? AND hour < ?"
_BY_HOUR = ("SELECT hour, kind, SUM(event_count) AS event_count, SUM(failed_count) AS failed_count FROM prowlarr_hour"
            f" WHERE {_IN_PERIOD} AND kind IN ('query', 'rss') GROUP BY hour, kind ORDER BY hour")
_BY_INDEXER = ("SELECT indexer_id, kind, SUM(event_count) AS event_count, SUM(failed_count) AS failed_count, SUM(response_sum) AS response_sum,"
               " SUM(response_count) AS response_count, SUM(with_results) AS with_results FROM prowlarr_hour"
               f" WHERE {_IN_PERIOD} GROUP BY indexer_id, kind")
_BY_APP = ("SELECT app, kind, SUM(event_count) AS event_count FROM prowlarr_hour"
           f" WHERE {_IN_PERIOD} AND kind IN ('query', 'rss') GROUP BY app, kind")
# `being_grabbed` = Prowlarr knows the result: it downloaded itself, or the grab failed (only a downloaded one can fail).
_GRABBED_BY_INDEXER = ("SELECT indexer_id, COUNT(*) AS grab_count,"
                       " SUM(CASE WHEN method = 'proxy' OR succeeded = 0 THEN 1 ELSE 0 END) AS being_grabbed,"
                       " SUM(CASE WHEN method = 'redirect' AND succeeded = 1 THEN 1 ELSE 0 END) AS redirected,"
                       " SUM(CASE WHEN succeeded = 0 THEN 1 ELSE 0 END) AS failed_count,"
                       " COALESCE(SUM(response), 0) AS response_sum, COUNT(response) AS response_count"
                       " FROM prowlarr_grab WHERE happened_at >= ? AND happened_at < ? GROUP BY indexer_id")
_GRABBED_BY_APP = "SELECT app, COUNT(*) AS grab_count FROM prowlarr_grab WHERE happened_at >= ? AND happened_at < ? GROUP BY app"
_LATEST = ("SELECT id, happened_at, indexer_id, app, title, method, succeeded, response, client FROM prowlarr_grab"
           " WHERE happened_at >= ? AND happened_at < ? ORDER BY happened_at DESC, id DESC")


def _whole(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sums from the database as whole numbers (PostgreSQL returns SUM as a Decimal, an empty sum is NULL)."""
    return [{key: value if isinstance(value, str) else int(value or 0) for key, value in row.items()} for row in rows]


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _mean(total: int, count: int) -> int | None:
    return round(total / count) if count else None


def prowlarr_first() -> str:
    """The time of the first stored event (UTC) - the start of "the whole history" on the Prowlarr tab. Empty without data.

    For queries it is the start of the hour. A nonsense date (before OLDEST_HISTORY) does not count, the same as with
    plays.
    """
    first_times = [str(moment) for moment in (
        db.query_value("SELECT MIN(hour) FROM prowlarr_hour WHERE hour >= ?", (OLDEST_HISTORY,), default=""),
        db.query_value("SELECT MIN(happened_at) FROM prowlarr_grab WHERE happened_at >= ?", (OLDEST_HISTORY,), default="")) if moment]
    return min(first_times) if first_times else ""


def prowlarr_indexer_names() -> dict[int, dict[str, Any]]:
    """The indexers' names: {number: {"name", "protocol", "removed"}}. Those no longer in Prowlarr too."""
    return {int(r["id"]): {"name": r["name"], "protocol": r["protocol"], "removed": bool(r["removed"])}
            for r in db.query_all("SELECT id, name, protocol, removed FROM prowlarr_indexer")}


def prowlarr_analysis(days: Any = 30) -> dict[str, Any]:
    """The period's sums all statistics compute from: by hour, by indexer, by app, grabbed releases by indexer and by
    app, and the indexers' names. Five queries with sums, no single events."""
    period = _as_period(days)
    bounds = (period.since, period.until)
    return {"hours": _whole(db.query_all(_BY_HOUR, bounds)), "indexers": _whole(db.query_all(_BY_INDEXER, bounds)),
            "app": _whole(db.query_all(_BY_APP, bounds)), "grabbed_by_app": _whole(db.query_all(_GRABBED_BY_INDEXER, bounds)),
            "requested_app": _whole(db.query_all(_GRABBED_BY_APP, bounds)), "names": prowlarr_indexer_names()}


def prowlarr_numbers(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The period's numbers: searches, RSS queries, how many failed (count and share) and the mean response in ms."""
    queries = [x for x in (analysis or prowlarr_analysis(days))["indexers"] if x["kind"] in QUERY_KINDS]
    searches = sum(x["event_count"] for x in queries if x["kind"] == "query")
    rss = sum(x["event_count"] for x in queries if x["kind"] == "rss")
    failed = sum(x["failed_count"] for x in queries)
    return {"searches": searches, "rss": rss, "failed_count": failed, "failure_share": _share(failed, searches + rss),
            "response": _mean(sum(x["response_sum"] for x in queries), sum(x["response_count"] for x in queries))}


def prowlarr_by_day(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Searches, RSS queries and failed queries by day (in the app's zone). Only days when something was."""
    by_day: dict[str, dict[str, Any]] = {}
    for x in (analysis or prowlarr_analysis(days))["hours"]:
        day = _local(x["hour"]).date().isoformat()
        row = by_day.setdefault(day, {"day": day, "searches": 0, "rss": 0, "failed_count": 0})
        row["searches" if x["kind"] == "query" else "rss"] += x["event_count"]
        row["failed_count"] += x["failed_count"]
    return [by_day[day] for day in sorted(by_day)]


def prowlarr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Searches by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap). RSS runs the same all the time, it does not belong here."""
    grid = [[0] * 24 for _ in range(7)]
    for x in (analysis or prowlarr_analysis(days))["hours"]:
        if x["kind"] == "query":
            moment = _local(x["hour"])
            grid[moment.weekday()][moment.hour] += x["event_count"]
    return grid


def prowlarr_who(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Who asks: by app the searches, RSS queries and grabbed releases, from the most active. An unknown app is ""."""
    r = analysis or prowlarr_analysis(days)
    by_app: dict[str, dict[str, Any]] = {}
    for x in r["app"]:
        row = by_app.setdefault(x["app"], {"app": x["app"], "searches": 0, "rss": 0, "grabs": 0})
        row["searches" if x["kind"] == "query" else "rss"] += x["event_count"]
    for x in r["requested_app"]:
        by_app.setdefault(x["app"], {"app": x["app"], "searches": 0, "rss": 0, "grabs": 0})["grabs"] += x["grab_count"]
    return sorted(by_app.values(), key=lambda x: (-(x["searches"] + x["rss"] + x["grabs"]), x["app"]))


def prowlarr_indexers(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The indexers' performance, from the one with the most queries: queries (searches and RSS), how many failed and
    what share that is, the mean response in ms, how many answered searches found something, grabbed releases - and
    separately the failed sign-ins. The failure share counts from the queries standing beside it; sign-ins do not
    belong among them.

    `name` is None when the indexer has no name (it left Prowlarr long ago) - the card puts together the text
    "indexer no. N".
    """
    r = analysis or prowlarr_analysis(days)
    grabbed = {x["indexer_id"]: x["grab_count"] for x in r["grabbed_by_app"]}
    by_indexer: dict[int, list[dict[str, Any]]] = {number: [] for number in grabbed}
    for x in r["indexers"]:
        by_indexer.setdefault(x["indexer_id"], []).append(x)
    result = []
    for number, rows in by_indexer.items():
        queries = [x for x in rows if x["kind"] in QUERY_KINDS]
        searches = [x for x in rows if x["kind"] == "query"]
        failed = sum(x["failed_count"] for x in queries)
        names_entry = r["names"].get(number, {})
        result.append({
            "indexer_id": number, "name": names_entry.get("name"), "removed": bool(names_entry.get("removed")),
            "query_count": sum(x["event_count"] for x in queries), "failed_count": failed, "failure_share": _share(failed, sum(x["event_count"] for x in queries)),
            "login_failed": sum(x["failed_count"] for x in rows if x["kind"] == "auth"),
            "response": _mean(sum(x["response_sum"] for x in queries), sum(x["response_count"] for x in queries)),
            "found_share": _share(sum(x["with_results"] for x in searches), sum(x["event_count"] - x["failed_count"] for x in searches)),
            "grabs": grabbed.get(number, 0)})
    return sorted(result, key=lambda x: (-x["query_count"], -x["grabs"], x["indexer_id"]))


def prowlarr_grabbed(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The period's grabbed releases: how many, how many of them Prowlarr downloaded itself and how many it only
    redirected, how many failed and the mean time in ms until the indexer gave out the file (both only from the
    downloaded ones)."""
    rows = (analysis or prowlarr_analysis(days))["grabbed_by_app"]
    return {"grab_count": sum(x["grab_count"] for x in rows), "being_grabbed": sum(x["being_grabbed"] for x in rows),
            "redirected": sum(x["redirected"] for x in rows), "failed_count": sum(x["failed_count"] for x in rows),
            "response": _mean(sum(x["response_sum"] for x in rows), sum(x["response_count"] for x in rows))}


def prowlarr_grabbed_indexers(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Grabbed releases by indexer, from the most frequent: count, share of all, how many Prowlarr downloaded itself
    and how many failed. `failed_count` is None for an indexer where Prowlarr only redirected - it does not know the
    result."""
    r = analysis or prowlarr_analysis(days)
    total = sum(x["grab_count"] for x in r["grabbed_by_app"])
    return sorted(({"indexer_id": x["indexer_id"], "name": r["names"].get(x["indexer_id"], {}).get("name"), "grab_count": x["grab_count"],
                    "share": _share(x["grab_count"], total), "being_grabbed": x["being_grabbed"],
                    "failed_count": x["failed_count"] if x["being_grabbed"] else None} for x in r["grabbed_by_app"]),
                  key=lambda x: (-x["grab_count"], x["indexer_id"]))


def prowlarr_grabbed_latest(days: Any = 30, limit: int | None = 10, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The period's last grabbed releases from the newest: when (UTC), the release's name, the indexer, who took it,
    how (`method`), whether it succeeded and the time in ms. `limit` None = all."""
    period = _as_period(days)
    names = analysis["names"] if analysis else prowlarr_indexer_names()
    if limit is None:
        rows = db.query_all(_LATEST, (period.since, period.until))
    else:
        rows = db.query_all(_LATEST + " LIMIT ?", (period.since, period.until, max(0, int(limit))))
    return [{"happened_at": x["happened_at"], "title": x["title"], "indexer_id": int(x["indexer_id"]), "name": names.get(int(x["indexer_id"]), {}).get("name"),
             "app": x["app"], "method": x["method"], "succeeded": bool(x["succeeded"]), "response": x["response"], "client": x["client"]}
            for x in rows]


# ---------------------------------------------------------------------------
# The indexers' availability - from the samples of their status (prowlarr_sample)
# ---------------------------------------------------------------------------

GAP_TIMES = 2      # a gap longer than this many usual intervals (stored with its samples) is not measured time
# Between two of the background loop's samples a step longer than this many intervals is not measured: the loop's next
# sample comes one round after its last (its interval and the time of a request), a round without an answer makes two.
LOOP_GAP_TIMES = 1.5
# A day of the strip: an outage in its measured time, failed queries (the hourly history), measured and fine, nothing measured.
FINE = "fine"
FAILED = "failed"
OUTAGE = "outage"
UNMEASURED = "unmeasured"
# The number of a sample's own row (arr/prowlarr/samples.RUN - the statistics do not import the app's package). In the
# hourly history the same 0 is an event without an indexer: neither has an availability.
_RUN = 0
_FIRST_SAMPLE = "SELECT MIN(sampled_at) FROM prowlarr_sample WHERE indexer_id = ?"
_SAMPLE_BEFORE = "SELECT MAX(sampled_at) FROM prowlarr_sample WHERE indexer_id = ? AND sampled_at < ?"
_SAMPLE_FROM = "SELECT MIN(sampled_at) FROM prowlarr_sample WHERE indexer_id = ? AND sampled_at >= ?"
_SAMPLES = ("SELECT sampled_at, indexer_id, failing_since, last_failure, enabled_ids, interval_seconds, measured_until"
            " FROM prowlarr_sample WHERE sampled_at >= ? AND sampled_at <= ? ORDER BY sampled_at, indexer_id")
_FAILED_HOURS = f"SELECT DISTINCT hour, indexer_id FROM prowlarr_hour WHERE {_IN_PERIOD} AND kind IN ('query', 'rss') AND failed_count > 0"
_ENABLED = "SELECT id FROM prowlarr_indexer WHERE enabled = 1 AND removed = 0"
Stretch = tuple[datetime, datetime]
Outage = tuple[datetime, datetime, list[dict[str, Any]]]
# A moment of the samples: (when, the indexers there and enabled, the usual interval, the background loop's).
_Point = tuple[datetime, frozenset[int], timedelta, bool]


class Samples(NamedTuple):
    """The samples a period needs, in parallel lists by time, and {indexer: {time: its row}} of those left out.

    A sample of the background loop is two moments: when it was taken and its last round (`measured_until`), with
    the same state. Other samples may lie between them - another loop's (two app processes on one database), the
    task's, a page's: a step inside any loop sample's own stretch is `joined` - measured whole."""
    times: list[datetime]
    present: list[frozenset[int]]        # the indexers there and enabled at each sample
    intervals: list[timedelta]           # the usual interval in force at each sample
    watched: list[bool]                  # the moment is the background loop's
    joined: list[bool]                   # from this moment to the next lies inside a loop sample's own stretch
    left_out: dict[int, dict[datetime, dict[str, Any]]]


def _moment(text: Any) -> datetime | None:
    """A stored time (UTC, db.TIME_FORMAT) as a datetime without a zone; empty or unreadable = None."""
    if not text:
        return None
    try:
        return _time(str(text)[:19])
    except ValueError:
        return None


def _seconds(length: timedelta) -> int:
    return int(length.total_seconds())


def _ids(text: Any, known: dict[str, frozenset[int]]) -> frozenset[int]:
    """The numbers of a sample's list of enabled indexers (",1,3," - samples.id_list). The same text gives the same
    set: samples one after another mostly share it, and `_measured` then joins them without comparing."""
    key = str(text or "")
    if key not in known:
        numbers = set()
        for part in key.split(","):
            try:
                numbers.add(int(part))
            except ValueError:          # the empty text between the commas
                continue
        known[key] = frozenset(numbers)
    return known[key]


def _gap_times(first_watched: bool, second_watched: bool) -> float:
    """How many usual intervals a step between two moments may be long and still be measured time."""
    return LOOP_GAP_TIMES if first_watched and second_watched else GAP_TIMES


def _points(row: Any, lists: dict[str, frozenset[int]]) -> tuple[list[_Point], datetime | None]:
    """The moments of a sample's own row: one, or two for the loop's sample seen again after it was taken - and then
    its own last round (`measured_until`), else None."""
    moment, until = _moment(row["sampled_at"]), _moment(row["measured_until"])
    if moment is None:
        return [], None
    there, interval = _ids(row["enabled_ids"], lists), timedelta(seconds=max(0, int(row["interval_seconds"] or 0)))
    if until is not None and until > moment:
        return [(moment, there, interval, True), (until, there, interval, True)], until
    return [(moment, there, interval, until is not None)], None


def _joined(times: list[datetime], stretches: dict[datetime, datetime]) -> list[bool]:
    """For every moment whether the step from it to the next one lies inside a loop sample's own stretch (from when
    it was taken to its last round; `stretches` = {taken: last round}). Overlapping stretches - two app processes on
    one database run a loop each - are joined first: a step inside them is measured once, whichever sample it is in.
    Every stretch starts and ends at a moment, so a step is inside a joined stretch whole or not at all."""
    merged: list[list[datetime]] = []
    for start, until in sorted(stretches.items()):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], until)
        else:
            merged.append([start, until])
    joined, j = [], 0
    for k, moment in enumerate(times):
        while j < len(merged) and merged[j][1] <= moment:
            j += 1
        joined.append(k + 1 < len(times) and j < len(merged) and merged[j][0] <= moment and times[k + 1] <= merged[j][1])
    return joined


def _samples(since: str, end: str, now: datetime) -> Samples:
    """The samples a period needs: from the last sample before the period's start to the first one at or after its
    end - the stretches across the edges are known then. When the last of them is the newest of all and younger than
    twice its interval (one and a half for the loop's), now is one more sample with the same state: the next one is
    not late yet.
    """
    before = db.query_value(_SAMPLE_BEFORE, (_RUN, since), default="")
    after = db.query_value(_SAMPLE_FROM, (_RUN, end), default="")
    points: list[_Point] = []
    stretches: dict[datetime, datetime] = {}         # a loop sample's own stretch: {taken: its last round}
    left_out: dict[int, dict[datetime, dict[str, Any]]] = {}
    lists: dict[str, frozenset[int]] = {}
    for row in db.query_all(_SAMPLES, (before or since, after or END_OF_TIME)):
        if int(row["indexer_id"]) == _RUN:
            own, until = _points(row, lists)
            points += own
            if until is not None:
                stretches[own[0][0]] = until
        elif (moment := _moment(row["sampled_at"])) is not None:
            left_out.setdefault(int(row["indexer_id"]), {})[moment] = row
    points.sort(key=lambda point: point[0])
    # A loop sample's indexers left out are left out at its own last round too (a sample taken at that very moment
    # keeps its own rows).
    for rows in left_out.values():
        for moment, row in list(rows.items()):
            if moment in stretches:
                rows.setdefault(stretches[moment], row)
    if points and not after and timedelta(0) < now - points[-1][0] <= points[-1][2] * _gap_times(points[-1][3], points[-1][3]):
        for rows in left_out.values():
            if points[-1][0] in rows:
                rows[now] = rows[points[-1][0]]
        points.append((now, *points[-1][1:]))
    times = [p[0] for p in points]
    return Samples(times, [p[1] for p in points], [p[2] for p in points], [p[3] for p in points],
                   _joined(times, stretches), left_out)


def _step_measured(samples: Samples, k: int) -> bool:
    """Whether the time from the moment k to the next one is measured: a loop sample's own stretch, or a step at most
    the allowed count of the larger of their intervals long."""
    if samples.joined[k]:
        return True
    allowed = _gap_times(samples.watched[k], samples.watched[k + 1]) * max(samples.intervals[k], samples.intervals[k + 1])
    return samples.times[k + 1] - samples.times[k] <= allowed


def _measured(samples: Samples, start: datetime, end: datetime) -> tuple[dict[int, list[Stretch]], list[int]]:
    """Every indexer's measured stretches within [start, end), joined: from one sample to the next when the indexer
    was there and enabled in both and the step between them is measured (`_step_measured`). And the intervals (in
    seconds, from the shortest) of the samples some indexer's measured time lies between - inside a loop sample's
    stretch only the loop's (a sample of the task or a page there measured nothing the loop did not), never 0 (a
    sample of an early build has no interval stored).

    The stretches go first into pieces with the same indexers (mostly a few long ones) and only then to the indexers.
    """
    times, present, intervals = samples.times, samples.present, samples.intervals
    pieces: list[tuple[datetime, datetime, frozenset[int]]] = []
    used: set[int] = set()
    for k in range(len(times) - 1):
        if not _step_measured(samples, k):
            continue
        a, b = max(times[k], start), min(times[k + 1], end)
        both = present[k] if present[k] is present[k + 1] else present[k] & present[k + 1]
        if b <= a or not both:
            continue
        ends = [n for n in (k, k + 1) if samples.watched[n]] if samples.joined[k] else [k, k + 1]
        used |= {_seconds(intervals[n]) for n in ends} - {0}
        if pieces and pieces[-1][1] == a and (pieces[-1][2] is both or pieces[-1][2] == both):
            pieces[-1] = (pieces[-1][0], b, pieces[-1][2])
        else:
            pieces.append((a, b, both))
    stretches: dict[int, list[Stretch]] = {}
    for a, b, both in pieces:
        for number in both:
            own = stretches.setdefault(number, [])
            if own and own[-1][1] == a:
                own[-1] = (own[-1][0], b)
            else:
                own.append((a, b))
    return stretches, sorted(used)


def _pieces(stretches: list[Stretch], start: datetime, end: datetime) -> list[Stretch]:
    """The parts of the stretches within [start, end)."""
    return [(max(a, start), min(b, end)) for a, b in stretches if a < end and b > start]


def _outages(samples: Samples, number: int) -> tuple[list[Outage], bool]:
    """One indexer's outages over the samples: [(start, end, its rows)] and whether the last one is still open.

    An outage starts at the indexer's "failing since" - not before the last sample without it (Prowlarr may keep the
    time of a failure long gone), not after the sample that saw it - and ends at the first later sample without it.
    A sample the indexer was not there or not enabled in is a sample without it. One still open at the last sample
    ends there.
    """
    left_out = samples.left_out.get(number, {})
    if not left_out:                     # never left out: nothing to walk through
        return [], False
    outages: list[Outage] = []
    current: tuple[datetime, list[dict[str, Any]]] | None = None
    clean: datetime | None = None
    for moment, there in zip(samples.times, samples.present, strict=True):
        row = left_out.get(moment) if number in there else None
        if row is None:
            if current is not None:
                outages.append((current[0], moment, current[1]))
                current = None
            clean = moment
        elif current is None:
            since = _moment(row["failing_since"]) or moment
            current = (min(max(since, clean) if clean else since, moment), [row])
        else:
            current[1].append(row)
    if current is not None:
        outages.append((current[0], samples.times[-1], current[1]))
    return outages, current is not None


def _strip_days(period: Any, now: datetime) -> list[date]:
    """The period's days in the app's zone, at most up to today."""
    today = now.replace(tzinfo=UTC).astimezone(formatting.zone()).date()
    first = _local_day(period.since_local, period.since)
    try:
        last = min(today, _local_day(period.until_local, period.until, end=True))
    except (OverflowError, ValueError):     # an open end without its local form (END_OF_TIME does not shift into a zone)
        last = today
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _day_bounds(days: list[date]) -> list[datetime]:
    """The start of every day in UTC (the local midnight - a day is not always 24 hours) and the end of the last one."""
    zone = formatting.zone()
    edges = [*days, days[-1] + timedelta(days=1)] if days else []
    return [datetime.combine(day, datetime.min.time()).replace(tzinfo=zone).astimezone(UTC).replace(tzinfo=None) for day in edges]


def _days_hit(stretches: list[Stretch], bounds: list[datetime]) -> set[int]:
    """Which days (their numbers; `bounds` from _day_bounds) the stretches reach into."""
    hit: set[int] = set()
    for a, b in stretches:
        hit.update(range(max(0, bisect_right(bounds, a) - 1), min(len(bounds) - 2, bisect_left(bounds, b) - 1) + 1))
    return hit


def _failed_days(period: Any, days: list[date]) -> dict[int, set[int]]:
    """{indexer: the numbers of the days with a failed query} - an hour belongs to the day its start falls into."""
    numbers = {day.isoformat(): n for n, day in enumerate(days)}
    failed: dict[int, set[int]] = {}
    for row in db.query_all(_FAILED_HOURS, (period.since, period.until)):
        number = numbers.get(_local(str(row["hour"])).date().isoformat())
        if number is not None:
            failed.setdefault(int(row["indexer_id"]), set()).add(number)
    return failed


def _availability_of(outages: list[Outage], still_open: bool, running_now: bool, measured: list[Stretch], end: str,
                     bounds: list[datetime]) -> dict[str, Any]:
    """One indexer's outages within the measured time: how long it was out, how often, the longest outage, the last
    failure (not after the period's end `end`), whether the last outage still runs and the days it reached into."""
    down, longest, count, last_counted = timedelta(0), timedelta(0), 0, False
    failures: list[str] = []
    hit: set[int] = set()
    for start, finish, rows in outages:
        pieces = _pieces(measured, start, finish)
        length = sum((b - a for a, b in pieces), timedelta(0))
        last_counted = bool(length)
        if not length:
            continue
        count, down, longest = count + 1, down + length, max(longest, length)
        failures += [str(t) for row in rows for t in (row["last_failure"], row["failing_since"]) if t and str(t) <= end]
        hit |= _days_hit(pieces, bounds)
    return {"down": _seconds(down), "outages": count, "longest": _seconds(longest) if count else None,
            "last_failure": max(failures) if failures else None, "running": still_open and running_now and last_counted, "hit": hit}


def _strip(outage: set[int], failed: set[int], measured: set[int], count: int) -> list[str]:
    """The states of the strip's days: an outage in the measured time, else failed queries, else measured, else nothing measured."""
    return [OUTAGE if n in outage else FAILED if n in failed else FINE if n in measured else UNMEASURED for n in range(count)]


def _troublesome(row: dict[str, Any]) -> tuple[Any, ...]:
    """From the most troublesome: the lowest availability, the highest share of failed queries, the most outages."""
    availability = 1.0 if row["availability"] is None else row["availability"]
    return availability, -(row["failure_share"] or 0.0), -row["outages"], (row["name"] or "").casefold(), row["indexer_id"]


def prowlarr_availability(days: Any = 30, analysis: dict[str, Any] | None = None, *, now: str | None = None) -> dict[str, Any]:
    """The indexers' availability in the period (see the module's docstring): for every indexer the share of its
    measured time it was not left out by Prowlarr (`availability`, None = nothing measured), how many seconds were
    measured and how many it was out, the count of outages, the longest one in seconds, the last failure (UTC),
    whether an outage still runs, the share of failed queries from the hourly history and one state per day of the
    period (`days`: FINE, FAILED, OUTAGE, UNMEASURED - a day it was not there or not enabled is UNMEASURED).

    The usual interval between samples is the one stored with them, not today's setting. The indexers are those
    Prowlarr has enabled now and those with queries or an outage in the period, from the most troublesome.
    `first_sample` is the time of the first sample of all ("" = none yet: only the failed queries mean anything),
    `measured_since` the same when it falls after the period's start, `intervals` the usual intervals (in seconds, from
    the shortest) of the samples the period's measured time lies between - [] = nothing measured. `now` (UTC) is for
    tests.
    """
    period = _as_period(days)
    r = analysis or prowlarr_analysis(period)
    moment = _moment(now or db.utcnow()) or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    start, end = _time(period.since), min(_moment(period.until) or moment, moment)
    end_text = end.strftime(db.TIME_FORMAT)
    samples = _samples(period.since, end_text, moment)
    measured, intervals = _measured(samples, start, end)
    strip_days = _strip_days(period, moment)
    bounds = _day_bounds(strip_days)
    failed = _failed_days(period, strip_days)
    queries = {x["indexer_id"]: x for x in prowlarr_indexers(period, analysis=r)}
    enabled = {int(x["id"]) for x in db.query_all(_ENABLED)}
    running_now = bool(samples.times) and samples.times[-1] == moment
    rows = []
    for number in sorted((enabled | set(queries) | set(samples.left_out)) - {_RUN}):
        own = measured.get(number, [])
        total = sum((b - a for a, b in own), timedelta(0))
        found = _availability_of(*_outages(samples, number), running_now, own, end_text, bounds)
        asked, named = queries.get(number, {}), r["names"].get(number, {})
        if number in enabled or asked.get("query_count") or found["outages"]:
            hit = found.pop("hit")
            rows.append({"indexer_id": number, "name": named.get("name"), "removed": bool(named.get("removed")),
                         "availability": 1 - found["down"] / total.total_seconds() if total else None, "measured": _seconds(total),
                         **found, "query_count": asked.get("query_count", 0), "failed_count": asked.get("failed_count", 0),
                         "failure_share": asked.get("failure_share"),
                         "days": _strip(hit, failed.get(number, set()), _days_hit(own, bounds), len(strip_days))})
    first = str(db.query_value(_FIRST_SAMPLE, (_RUN,), default="") or "")
    return {"first_sample": first, "measured_since": first if first > period.since else "", "intervals": intervals,
            "days": [day.isoformat() for day in strip_days], "indexers": sorted(rows, key=_troublesome)}
