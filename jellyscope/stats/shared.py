"""Calculations that know no app: time in the zone, the quantile, pairing grabs with imports.

Part of the statistics package (see jellyscope/stats/__init__.py). The statistics of the single apps
(stats/<app>.py) take from here what they share. What knows names from some app's API, its table, or what is an
episode and what a movie does not belong here - that is in the app's file (tests/test_arr_tree.py guards it).

Pairing works with rows as dicts: grabs and imports have the keys `arr_id` (the record's number in the app's
history), `item_id` (an episode, a movie), `download_id` and `happened_at`. It is a row's shape, not a table.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db, formatting

# Within how many minutes of a file deleted with the reason "upgrade" the import that caused it is looked for.
UPGRADE_PAIRING_WINDOW = timedelta(minutes=10)
# How far before the period's start to look for the grab of an import in the period. A grab imported only after a
# month is a fault, not a statistic.
GRAB_WINDOW = timedelta(days=30)
# Buckets "how long after the release": the upper bound in seconds (inclusive).
BUCKETS = (("1h", 3600), ("6h", 6 * 3600), ("1d", 24 * 3600), ("7d", 7 * 24 * 3600))
UNKNOWN = "–"


def _time(text: str) -> datetime:
    return datetime.strptime(text, db.TIME_FORMAT)


def _local(text: str) -> datetime:
    """A time from the database (UTC) in the app's zone."""
    return _time(text).replace(tzinfo=UTC).astimezone(formatting.zone())


def local_fast(text: Any, zone: Any) -> datetime:
    """Like `_local`, only faster: fromisoformat instead of strptime and the zone computed in advance."""
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC).astimezone(zone)
    except (TypeError, ValueError):
        return _local(text)


def _shift(text: str, by: timedelta) -> str:
    """A period's bound shifted by `by`. The infinite end (END_OF_TIME) is not shifted."""
    try:
        return (_time(text) + by).strftime(db.TIME_FORMAT)
    except (OverflowError, ValueError):
        return text


def _quantile(values: list[float], q: float) -> int | None:
    """A quantile by linear interpolation by rank (like numpy's "linear"). Empty = None."""
    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (position - low) * (ordered[high] - ordered[low]))


def _pair_grabs(grabs: list[dict[str, Any]], imports: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """For every import its grab: {arr_id of the import: the grab's row}.

    With the same `download_id` **the last grab before the import** is taken. One `download_id` can repeat: it
    is a torrent's hash or an id in the client, and the same release grabbed again (after the file was deleted)
    gets the same one. The first grab in the window would then pair a grab weeks old with the new import. A pack
    of several items keeps working - one grab, many imports after it.

    Without `download_id` (a client that does not report it with the grab) the last not yet paired grab of the
    same item before the import is taken.
    """
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    without_id: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for g in grabs:                       # sorted by time
        (by_id[g["download_id"]] if g["download_id"] else without_id[g["item_id"]]).append(g)
    used: set[int] = set()
    result: dict[int, dict[str, Any]] = {}
    for i in imports:
        same_id = by_id.get(i["download_id"], []) if i["download_id"] else []
        before_import = [k for k in same_id if k["happened_at"] <= i["happened_at"]]
        # None before the import (a clock off): at least the first with the same id - the source is right anyway.
        g = before_import[-1] if before_import else (same_id[0] if same_id else None)
        if g is None:
            candidates = [k for k in without_id.get(i["item_id"], [])
                          if k["happened_at"] <= i["happened_at"] and k["arr_id"] not in used]
            g = candidates[-1] if candidates else None
            if g is not None:
                used.add(g["arr_id"])
        if g is not None:
            result[i["arr_id"]] = g
    return result


def _pair_upgrades(upgrades: list[dict[str, Any]], imports: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """For every upgrade (a file deleted) the import that caused it: {arr_id of the deletion: the import}."""
    by_item: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for i in imports:
        by_item[i["item_id"]].append(i)
    used: set[int] = set()
    result: dict[int, dict[str, Any]] = {}
    for d in upgrades:
        moment = _time(d["happened_at"])
        near = [(abs(_time(i["happened_at"]) - moment), i) for i in by_item.get(d["item_id"], [])
                if i["arr_id"] not in used and abs(_time(i["happened_at"]) - moment) <= UPGRADE_PAIRING_WINDOW]
        if near:
            _, nearest = min(near, key=lambda pair: (pair[0], pair[1]["arr_id"]))
            used.add(nearest["arr_id"])
            result[d["arr_id"]] = nearest
    return result


def _ranked(counts: Counter, sizes: Counter) -> list[dict[str, Any]]:
    """[{name, pocet, size_bytes}] by count, on a tie by size (descending), then alphabetically."""
    return [{"name": n, "count": counts[n], "size_bytes": sizes[n]}
            for n in sorted(counts, key=lambda n: (-counts[n], -sizes[n], n))]
