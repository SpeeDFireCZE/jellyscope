"""Subtitle statistics from Bazarr over the downloaded history (the table bazarr_event).

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/arr/bazarr/sync.py downloads the
history into the database.

All functions take `days` - anything `_as_period` takes (a count of days or a ready Period) - and compute in the
app's zone. A page picks the events once (`bazarr_analysis`) and hands them to every statistic as `analysis=`.

Notions:

* **Acquired subtitles** - an action by which subtitles came to be or were replaced (`ACQUIRED`): a download, a
  manual pick, an upgrade, uploading one's own file, a translation, extracting from the file. Syncing the timing and
  deleting not.
* **An upgrade's predecessor** - the last acquired subtitles of the same item, language and flags (hearing
  impaired, forced) before the upgrade, maybe even from before the period. It gets "replaced" credited at the
  provider. The score's improvement is measured against the last such subtitles **that had a score** - translated
  ones or ones extracted from the file have none.
* **The first subtitles after an import** - the first acquired subtitles of an item in a language since Sonarr or
  Radarr imported its file. A file replaced by a better version is a new import: subtitles Bazarr finds for it are
  measured from the replacement. Further subtitles for the same file (another flag) are no longer first and **an
  upgrade is never first** - it says there were subtitles already (maybe left over after an import during which
  Bazarr searched for nothing). The speed counts those that fell into the period. The imports are looked up only
  when the speed card asks for them - the shared analysis does not need them.

Only the rows of acquired subtitles are loaded into memory. What brought no subtitles (syncing the timing tends to
come with every downloaded subtitles, deleting) is only counted in the database - the cards need the count of it
and the analysis is half the size.

A record without a date (`happened_at` NULL - the time could not be read at the first sync) belongs only to the period
"whole history" and never to the charts by day and hour. It is taken as the oldest: it comes from the first run. A
record with the time of first sight (`time_exact` = 0) has the day right, the seconds not - so it does not count
into the speed.

The actions and their names are copied here from the client (jellyscope/arr/bazarr/client.py): the app packages
import the statistics, the statistics do not import the app packages - otherwise there would be a cycle.
tests/test_bazarr_stats.py guards that they match.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from typing import Any

from .. import db
from .periods import OLDEST_HISTORY, Period, _as_period
from .radarr import radarr_imports
from .shared import _local, _quantile, _time
from .sonarr import sonarr_imports

ACTIONS = {0: "deleted", 1: "grabbed", 2: "manual", 3: "upgrade", 4: "uploaded", 5: "synced",
           6: "translated", 7: "extracted"}
ACQUIRED = (1, 2, 3, 4, 6, 7)
UPGRADE = 3
OTHER = "other"
ACTION_ORDER = (*ACQUIRED, 5, 0)
BUCKETS = (("10m", 600), ("1h", 3600), ("1d", 24 * 3600), ("7d", 7 * 24 * 3600))
SCORE_BUCKETS = (("90", 90.0), ("80", 80.0), ("70", 70.0))
# Who knows when an item's file was imported: episodes Sonarr, movies Radarr. It asks their public statistics - no
# other app's tables are touched from here.
IMPORTS = (("episode", sonarr_imports), ("movie", radarr_imports))

_COLUMNS = ("fingerprint, kind, title, episode, title_id, item_id, action, language, hi, forced, provider, score,"
            " happened_at, time_exact")
_ACQUIRED_SQL = ", ".join(str(a) for a in ACQUIRED)


def _order(event: dict[str, Any]) -> tuple[bool, str, str]:
    """The order from the oldest; records without a date first (they are from the first sync)."""
    return event["happened_at"] is not None, event["happened_at"] or "", event["fingerprint"]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _median(values: list[float]) -> float | None:
    """The median to one decimal place (scores are percentages with decimals). Empty = None."""
    if not values:
        return None
    ordered = sorted(values)
    half = len(ordered) // 2
    return round(ordered[half] if len(ordered) % 2 else (ordered[half - 1] + ordered[half]) / 2, 1)


def _acquired(period: Period) -> list[dict[str, Any]]:
    """The period's acquired subtitles from the oldest; in the whole history those without a date too."""
    rows = db.query_all(f"SELECT {_COLUMNS} FROM bazarr_event WHERE happened_at >= ? AND happened_at < ? AND action IN ({_ACQUIRED_SQL})"
                        " ORDER BY happened_at, fingerprint", (period.since, period.until))
    if period.whole_history:
        rows = db.query_all(f"SELECT {_COLUMNS} FROM bazarr_event WHERE happened_at IS NULL AND action IN ({_ACQUIRED_SQL})"
                            " ORDER BY fingerprint") + rows
    return rows


def _action_counts(period: Period) -> Counter[int]:
    """How many events of which action the period had - all of them, those that brought no subtitles too."""
    counts: Counter[int] = Counter()
    queries = [("happened_at >= ? AND happened_at < ?", (period.since, period.until))]
    if period.whole_history:
        queries.append(("happened_at IS NULL", ()))
    for condition, params in queries:
        for row in db.query_all(f"SELECT action, COUNT(*) AS count FROM bazarr_event WHERE {condition} GROUP BY action", params):
            counts[row["action"]] += int(row["count"])
    return counts


def _earlier(events: list[dict[str, Any]], period: Period) -> list[dict[str, Any]]:
    """Acquired subtitles of the period's items from the time before it (those without a date too): the upgrades' predecessors and "it had subtitles already"."""
    if period.whole_history:
        return []
    result: list[dict[str, Any]] = []
    for kind, _ in IMPORTS:
        items = {u["item_id"] for u in events if u["kind"] == kind and u["item_id"] is not None}
        result += db.query_all_in(
            f"SELECT {_COLUMNS} FROM bazarr_event WHERE kind = ? AND (happened_at < ? OR happened_at IS NULL)"
            f" AND action IN ({_ACQUIRED_SQL}) AND item_id IN ({{}})", items, (kind, period.since))
    return result


def _imports(acquired: list[dict[str, Any]]) -> dict[tuple[str, Any], list[str]]:
    """When Sonarr or Radarr imported the files of the items that got subtitles in the period:
    {(kind, item): times from the oldest}."""
    result: dict[tuple[str, Any], list[str]] = defaultdict(list)
    for kind, imports_of in IMPORTS:
        items = {u["item_id"] for u in acquired if u["kind"] == kind and u["item_id"] is not None}
        for item, times in imports_of(items).items():
            result[(kind, item)] = times
    return result


def bazarr_first() -> str:
    """The time of the first subtitle event (UTC), empty without a history or without a single date.

    The start of "the whole history" on the Bazarr tab and also the moment since which Bazarr provably ran (see
    `bazarr_speed`). A nonsense date (before OLDEST_HISTORY) does not count, the same as with plays.
    """
    return str(db.query_value("SELECT MIN(happened_at) FROM bazarr_event WHERE happened_at >= ?", (OLDEST_HISTORY,),
                              default="") or "")


def bazarr_analysis(days: Any = 30) -> dict[str, Any]:
    """The period's events picked and paired once - for a page that wants more statistics.

    `action` the counts of all events by action code, `obtained` the rows of acquired subtitles, `predecessors` {the
    upgrade's fingerprint: the subtitles it replaced}, `predecessor_score` {the upgrade's fingerprint: the last
    previous subtitles with a score}, `earlier` acquired subtitles of the same items from before the period (for
    the speed) and `start` the time of Bazarr's first event.
    """
    period = _as_period(days)
    acquired = _acquired(period)
    earlier = _earlier(acquired, period)
    last: dict[tuple, dict[str, Any]] = {}
    last_with_score: dict[tuple, dict[str, Any]] = {}
    predecessors: dict[str, dict[str, Any]] = {}
    score_predecessors: dict[str, dict[str, Any]] = {}
    for u in sorted([*earlier, *acquired], key=_order):
        if u["item_id"] is None:
            continue
        key = (u["kind"], u["item_id"], u["language"], u["hi"], u["forced"])
        if u["action"] == UPGRADE and u["happened_at"]:
            if key in last:
                predecessors[u["fingerprint"]] = last[key]
            if key in last_with_score:
                score_predecessors[u["fingerprint"]] = last_with_score[key]
        last[key] = u
        if u["score"] is not None:
            last_with_score[key] = u
    return {"action": _action_counts(period), "obtained": acquired, "earlier": earlier, "predecessors": predecessors,
            "predecessor_score": score_predecessors, "start": bazarr_first()}


class _Never:
    """A mark "the item has not got subtitles in this language yet" (None means "it got them, nobody knows when")."""


def _after_import(analysis: dict[str, Any]) -> list[tuple[dict[str, Any], str | None]]:
    """The first subtitles of an item in a language after its file's import: pairs (subtitles, the import's time).

    The import's time is None for the very first subtitles whose file has no import in the history. Not counted:

    * subtitles before which the item already got subtitles in the language since the same import (the same time
      as the import is taken as "after it");
    * an upgrade - see the module's introduction;
    * subtitles after a record without a date or with the time of first sight that does not lie before the import:
      nobody knows whether that record was before the import or after it, and a wrongly measured time is worse than
      none;
    * subtitles without an exact time.
    """
    acquired = analysis["obtained"]
    in_period = {u["fingerprint"] for u in acquired}
    import_times = _imports(acquired)
    last_time: dict[tuple, Any] = {}
    result: list[tuple[dict[str, Any], str | None]] = []
    for u in sorted([*analysis["earlier"], *acquired], key=_order):
        if u["item_id"] is None:
            continue
        key = (u["kind"], u["item_id"], u["language"])
        if u["fingerprint"] in in_period and u["happened_at"] and u["time_exact"] and u["action"] != UPGRADE:
            times = import_times.get(key[:2], [])
            import_index = bisect_right(times, u["happened_at"])
            previous = last_time.get(key, _Never)
            if not import_index:
                if previous is _Never:
                    result.append((u, None))
            elif previous is _Never or (previous is not None and previous < times[import_index - 1]):
                result.append((u, times[import_index - 1]))
        last_time[key] = u["happened_at"]
    return result


def bazarr_numbers(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, int]:
    """The period's numbers: acquired subtitles and of them downloaded by themselves, manually (a pick, one's own file) and by an upgrade; syncs, deletes."""
    r = analysis or bazarr_analysis(days)
    action_counts = r["action"]
    return {"obtained_count": len(r["obtained"]), "automatic": action_counts[1], "manual": action_counts[2] + action_counts[4], "upgrade_count": action_counts[UPGRADE],
            "sync_count": action_counts[5], "deleted": action_counts[0]}


def bazarr_by_day(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Acquired subtitles by day (in the app's zone), episodes and movies separately. Only days when something was."""
    by_day: dict[str, dict[str, Any]] = {}
    for u in (analysis or bazarr_analysis(days))["obtained"]:
        if u["happened_at"]:
            day = _local(u["happened_at"]).date().isoformat()
            row = by_day.setdefault(day, {"day": day, "episodes": 0, "movies": 0})
            row["episodes" if u["kind"] == "episode" else "movies"] += 1
    return [by_day[day] for day in sorted(by_day)]


def bazarr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Acquired subtitles by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0] * 24 for _ in range(7)]
    for u in (analysis or bazarr_analysis(days))["obtained"]:
        if u["happened_at"]:
            moment = _local(u["happened_at"])
            grid[moment.weekday()][moment.hour] += 1
    return grid


def bazarr_actions(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What happened with subtitles: counts by action (names from `ACTIONS`, an unknown code "other"), acquired ones first. Only non-zero."""
    counts: Counter[str] = Counter()
    for code, count in (analysis or bazarr_analysis(days))["action"].items():
        counts[ACTIONS.get(code, OTHER)] += count
    return [{"action": name, "count": counts[name]} for name in (*(ACTIONS[a] for a in ACTION_ORDER), OTHER) if counts[name]]


def _groups(acquired: list[dict[str, Any]], by: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """Acquired subtitles by groups (language, provider) from the largest, on a tie by name."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u in acquired:
        groups[u[by]].append(u)
    return sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))


def _score(events: list[dict[str, Any]]) -> list[float]:
    return [float(u["score"]) for u in events if u["score"] is not None]


def bazarr_languages(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The languages of acquired subtitles: count, share in percent, how many for the hearing impaired and forced, the mean score."""
    acquired = (analysis or bazarr_analysis(days))["obtained"]
    return [{"language": language, "count": len(s), "share": round(100 * len(s) / len(acquired), 1),
             "hi": sum(1 for u in s if u["hi"]), "forced": sum(1 for u in s if u["forced"]), "score": _mean(_score(s))}
            for language, s in _groups(acquired, "language")]


def bazarr_providers(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The providers of acquired subtitles: count, share, the mean score and `replaced`.

    `replaced` = how many upgrades in the period replaced this provider's subtitles (by the upgrade's predecessor,
    maybe even from before the period). Subtitles without a provider (a translation) have an empty name.
    """
    r = analysis or bazarr_analysis(days)
    acquired = r["obtained"]
    replaced = Counter(r["predecessors"][u["fingerprint"]]["provider"] for u in acquired if u["fingerprint"] in r["predecessors"])
    return [{"provider": name, "count": len(s), "share": round(100 * len(s) / len(acquired), 1),
             "score": _mean(_score(s)), "replaced": replaced[name]}
            for name, s in _groups(acquired, "provider")]


def bazarr_scores(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The match of acquired subtitles: the median, how many have a score and the distribution (below 70, 70-80, 80-90, from 90)."""
    values = _score((analysis or bazarr_analysis(days))["obtained"])
    buckets = {"under_70": 0} | {name: 0 for name, _ in reversed(SCORE_BUCKETS)}
    for value in values:
        buckets[next((name for name, bound in SCORE_BUCKETS if value >= bound), "under_70")] += 1
    return {"median": _median(values), "count": len(values), "buckets": buckets}


def bazarr_upgrades(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Subtitle upgrades: how many there were, how many have the score known before and after (`paired`) and the median improvement in points.

    "Before" are the last previous subtitles that had a score - not necessarily those the upgrade replaced
    (translated ones and ones extracted from the file have no score).
    """
    r = analysis or bazarr_analysis(days)
    upgrades = [u for u in r["obtained"] if u["action"] == UPGRADE]
    differences = [float(u["score"]) - float(previous["score"]) for u in upgrades
                   if (previous := r["predecessor_score"].get(u["fingerprint"])) and u["score"] is not None]
    return {"count": len(upgrades), "paired": len(differences), "improvements": _median(differences)}


def bazarr_changed(days: Any = 30, limit: int = 10, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Episodes and movies whose subtitles in one language were acquired more than once in the period, from the most frequent.

    Another language or another flag (hearing impaired, forced) is not summed - those are other subtitles, not a
    replacement.
    """
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for u in (analysis or bazarr_analysis(days))["obtained"]:
        if u["item_id"] is not None:
            groups[(u["kind"], u["item_id"], u["language"], u["hi"], u["forced"])].append(u)
    rows = [{"kind": s[-1]["kind"], "title": s[-1]["title"], "episode": s[-1]["episode"], "title_id": s[-1]["title_id"],
             "language": s[-1]["language"], "hi": s[-1]["hi"], "forced": s[-1]["forced"], "count": len(s)}
            for s in groups.values() if len(s) >= 2]
    rows.sort(key=lambda row: (-row["count"], row["title"], row["episode"], row["language"]))
    return rows[:max(0, int(limit))]


def bazarr_speed(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """From the file's import to the first subtitles: the median and 90 % in seconds, buckets, and what did not count.

    The first subtitles of an item in a language after its file's import (in the period) minus that import - an
    episode in Sonarr's history, a movie in Radarr's. It counts after every import: a file replaced by a better
    version is a new import and its subtitles are measured from the replacement. Two things do not count and the
    result says how many:

    * `not_imported` - there is no import in the history (a file added otherwise, Sonarr or Radarr not connected).
    * `before_bazarr` - the file was in the library before Bazarr's first event. It got subtitles at the first bulk
      search; "three years after the file" would say nothing about Bazarr's speed.
    """
    r = analysis or bazarr_analysis(days)
    seconds: list[float] = []
    without_import = before_bazarr = 0
    for u, file_time in _after_import(r):
        if file_time is None:
            without_import += 1
        elif file_time < r["start"]:
            before_bazarr += 1
        else:
            seconds.append((_time(u["happened_at"]) - _time(file_time)).total_seconds())
    buckets = {name: 0 for name, _ in BUCKETS} | {"longer": 0}
    for s in seconds:
        buckets[next((name for name, bound in BUCKETS if s <= bound), "longer")] += 1
    return {"median": _quantile(seconds, 0.5), "p90": _quantile(seconds, 0.9), "count": len(seconds),
            "not_imported": without_import, "before_bazarr": before_bazarr, "buckets": buckets}
