"""The library's state and growth over time (snapshots, the backfilled curve).

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from .. import content_kinds, db, formatting
from .content_history import Changes, all_changes, complete_library_rows, kind_changes, series_from_changes
from .disks import _pace, _segment, complete_disks, disk_changes, runs_out_first, disk_snapshots
from .periods import _local_day, _as_period
from .tech import RESOLUTION_CASE, RANGE_CASE


def _backfilled_curve(first: str, last: str, belongs: Callable[[str], bool] | None = None,
                      changes_by_kind: Changes | None = None) -> list[dict[str, Any]]:
    """The library's size day by day, backfilled from what is already in the data.

    The daily snapshots start only with the version that can write them. But the past CAN be computed, because two
    columns carry it:

    * `date_created` - when the item came to be,
    * `synced_at` of an item in the archive (`is_missing = 1`) - the last sync that still saw it. Items are not
      deleted, only hidden, so the drop shows too.

    An item thus counts into the library from its creation to the day the app last saw it.

    **What the backfill never gets to** (that is why the snapshots stay):

    * It describes the past with TODAY'S sizes. A movie transcoded from 30 GB to 10 GB comes out as if it had been
      10 GB forever - the snapshot of that day says 30 GB and is right.
    * Transcoding creates a new item with a new date in Jellyfin (that is why `items` has `tmdb_id` at all), so it
      reads as "added today".
    * What was deleted before the first sync is not in the data at all.
    * The free space cannot be backfilled - it is only in the snapshots.

    The day is taken from `date_created`, which is in UTC, whereas the snapshots are in the app's zone. For a curve
    over months it is a shift of hours and not worth solving; with precision to a day it would be.

    `belongs` picks items by path - the curve of one disk counts only the files lying on it (see
    disk_growth_series). Without it everything. `changes_by_kind` = changes of other content kinds already known
    for those files (a disk's, remembered in content_history); without it they are worked out here.
    """
    rows = db.query_all(
        """
        SELECT substr(date_created, 1, 10) AS created, path,
               CASE WHEN is_missing = 1 THEN substr(synced_at, 1, 10) END AS last_at,
               is_missing,
               COALESCE(size_bytes, 0) AS size_bytes,
               type
          FROM items
         WHERE date_created IS NOT NULL AND date_created != ''
        """
    )
    # No early return without items: a music-only server has its curve from the other kinds
    # (the guard after them decides).

    # The changes are collected into one dict and then walked once - one query per day would mean 365 queries for a
    # year.
    changes: dict[str, dict[str, int]] = {}

    def add_change(day: str, item_count: int, size: int, movie_count: int, episode_count: int) -> None:
        entry = changes.setdefault(day, {"item_count": 0, "size_bytes": 0,
                                         "movie_count": 0, "episode_count": 0})
        entry["item_count"] += item_count
        entry["size_bytes"] += size
        entry["movie_count"] += movie_count
        entry["episode_count"] += episode_count

    for row in rows:
        created = str(row["created"] or "")
        if len(created) != 10:
            continue
        if belongs is not None and not belongs(str(row["path"] or "")):
            continue

        # An item that is NOT in the library any more and we do not know when it went does not count at all.
        # Otherwise it would stay in the curve forever and the last point would be higher than "Total size" right
        # beside it - the chart would contradict the number on the same page. Underestimating the past is a smaller
        # evil than overwriting the present.
        last_seen = str(row["last_at"] or "")
        if int(row["is_missing"] or 0) and len(last_seen) != 10:
            continue

        size = int(row["size_bytes"] or 0)
        movie = 1 if str(row["type"] or "") == "Movie" else 0
        episode = 1 if str(row["type"] or "") == "Episode" else 0
        add_change(created, 1, size, movie, episode)

        if len(last_seen) == 10:
            # It goes on THAT day, not the next one. `synced_at` is the last sync that still saw the item - now it is
            # not in the library any more. If it were subtracted only tomorrow, an item deleted today would still
            # count into today's point and the curve's end would be higher than "Total size" right beside it.
            add_change(last_seen, -1, -size, -movie, -episode)

    # Other content kinds (music) by the same rules, from content_kinds.history; a disk counts
    # only files with a path on it (`belongs`, or its remembered `changes_by_kind`).
    if changes_by_kind is None:
        changes_by_kind = all_changes() if belongs is None else kind_changes(content_kinds.history(), belongs)
    kind_series = series_from_changes(changes_by_kind, first, last)
    first_with_kind = min((day for series in kind_series.values() for day, size in series.items() if size), default="")

    # With a filter (a disk nothing lies on) no change may be left.
    if not changes and not first_with_kind:
        return []

    # The running total from the oldest change to the end of the period.
    curve: list[dict[str, Any]] = []
    state = {"item_count": 0, "size_bytes": 0, "movie_count": 0, "episode_count": 0}
    day = date.fromisoformat(min(d for d in (min(changes) if changes else "", first_with_kind) if d))
    last_date = date.fromisoformat(last)
    first_date = date.fromisoformat(first)
    while day <= last_date:
        key = day.isoformat()
        if key in changes:
            for name, value in changes[key].items():
                state[name] += value
        if day >= first_date:
            parts = {kind: series[key] for kind, series in kind_series.items() if series.get(key)}
            curve.append({"day": key, "item_count": state["item_count"],
                          "movie_count": state["movie_count"], "episode_count": state["episode_count"],
                          "size_bytes": state["size_bytes"] + sum(parts.values()), "uhd": None, "hdr": None,
                          "without_tech": None, "free_space": None,
                          "backfilled": True, "kinds": parts})
        day += timedelta(days=1)
    return curve


def library_state() -> dict[str, Any]:
    """What the library looks like now - the same sums the daily snapshot writes.

    One function, because three places ask: the tile "Total size", writing the snapshot after the sync and the end
    of the growth curve. If each computed its own way, sooner or later they drift apart - and then one can only
    argue about which of those numbers lies.
    """
    row = db.query_one(
        f"""
        SELECT COUNT(*)                        AS item_count,
               SUM(CASE WHEN type = 'Movie'    THEN 1 ELSE 0 END) AS movie_count,
               SUM(CASE WHEN type = 'Episode'  THEN 1 ELSE 0 END) AS episode_count,
               COALESCE(SUM(COALESCE(size_bytes, 0)), 0)          AS size_bytes,
               SUM(CASE WHEN {RESOLUTION_CASE} = '4K' THEN 1 ELSE 0 END) AS uhd,
               SUM(CASE WHEN {RANGE_CASE} IN ('HDR', 'DOVI') THEN 1 ELSE 0 END) AS hdr,
               SUM(CASE WHEN tech_source IS NULL THEN 1 ELSE 0 END) AS without_tech
          FROM items
         WHERE is_missing = 0
        """
    ) or {}
    state = {name: int(row.get(name) or 0)
             for name in ("item_count", "movie_count", "episode_count", "size_bytes",
                          "uhd", "hdr", "without_tech")}
    # Other content kinds (music) count in the size; items stay movies and episodes - a track
    # does not add up with a film. `kinds` = the parts, stored with the daily snapshot.
    state["kinds"] = {key: {"items": totals["items"], "size": totals["size"]}
                      for key, totals in content_kinds.totals().items() if totals["items"]}
    state["size_bytes"] += sum(part["size"] for part in state["kinds"].values())
    return state


def snapshots(days: Any = 90) -> list[dict[str, Any]]:
    """The library's size day by day - the basis for the growth curve.

    Made of two sources, and the page is to know which is which:

    * **daily snapshots** since they started being written - what was really measured that day, the free space
      included;
    * **the backfilled past** for the time before the first snapshot, see `_backfilled_curve()`. Without it the
      curve would start on the day of installing and the question "how much did the library grow in a year" would
      have no answer.

    A snapshot always comes first when one exists for the day: it is a measurement, not an estimate.
    """
    first_day, last_day = _period_days(days)

    measured = db.query_all(
        """
        SELECT day, item_count, movie_count, episode_count, size_bytes, uhd, hdr,
               without_tech, free_space, kinds
          FROM library_snapshot
         WHERE day >= ? AND day <= ?
         ORDER BY day
        """,
        (first_day, last_day),
    )
    for row in measured:
        row["backfilled"] = False
    # Snapshots from before music was counted get it computed for their day (content_history).
    complete_library_rows(measured)

    # The backfill ends where the first snapshot AT ALL starts - not the first snapshot in the period. If the one in
    # the period were taken, for a period starting after the installation the past would be backfilled again over
    # time we have measurements of.
    measured_since = db.query_value(
        "SELECT MIN(day) FROM library_snapshot", default=None)

    boundary = str(measured_since or "")
    if boundary and boundary <= first_day:
        return measured           # we have the whole period measured

    whole = _backfilled_curve(first_day, last_day)
    backfilled = [row for row in whole if not boundary or row["day"] < boundary]
    if not backfilled:
        return measured

    # The backfill is ANCHORED on the first measured value.
    #
    # The shape of the past (when what was added) we read from the data reliably, but its HEIGHT is shifted
    # systematically: the backfill describes the past with today's sizes, whereas the snapshot knows the sizes of
    # then. Without anchoring there is a step at the joint - and "the library lost 200 GB overnight" is a claim
    # about a specific day that is simply untrue. The shift is one number for the whole backfilled part, so the
    # shape does not change.
    _anchor(backfilled, whole, next((r for r in measured if r["day"] >= boundary), None),
            ("size_bytes", "item_count", "movie_count", "episode_count"))

    # The curve must join up: the last backfilled day and the first measured day are side by side, so the line does
    # not break.
    return backfilled + measured


def _period_days(days: Any) -> tuple[str, str]:
    """The period's first and last day (the app's zone) as an ISO date - for the snapshots and the backfill."""
    period = _as_period(days)
    first_day = _local_day(period.since_local, period.since).isoformat()
    last_day = _local_day(period.until_local, period.until, end=True).isoformat() \
        if not period.relative else datetime.now(formatting.zone()).date().isoformat()
    return first_day, last_day


def _anchor(backfilled: list[dict[str, Any]], whole: list[dict[str, Any]],
            first_measured: dict[str, Any] | None, fields: tuple[str, ...]) -> None:
    """Anchors the backfilled past on the first measured value (changes `backfilled` in place).

    Why, see the comment in snapshots(). The series of one disk (disk_growth_series) does the same, hence on its
    own.
    """
    if not first_measured:
        return
    at_joint = next((r for r in whole if r["day"] == first_measured["day"]), None)
    if not at_joint:
        return
    for name in fields:
        shift = int(first_measured.get(name) or 0) - int(at_joint.get(name) or 0)
        if not shift:
            continue
        for row in backfilled:
            # A negative library does not exist; with a big shift the oldest days lean on zero.
            row[name] = max(0, int(row.get(name) or 0) + shift)


def _free_space_now(from_snapshot: Any) -> int | None:
    """How much space is left NOW, not how much there was at the last sync.

    The snapshot carries the free space from the moment it was written - and that tends to be at night. When the
    storage's capacity is entered by hand in the settings meanwhile, the estimate "the space runs out in X days"
    must be recomputed right away; waiting for the next sync would mean the entered value seemed to do nothing.

    `scanner` only here: it pulls in stats itself, so at the top it would make a cycle.
    """
    try:
        from .. import scanner

        now_free = scanner.free_space_now()
    except Exception:                     # noqa: BLE001
        # Finding out the space touches the disk and the settings - when it fails, the last known value from the
        # snapshot holds.
        return from_snapshot
    return now_free if now_free is not None else from_snapshot


def library_growth(days: Any = 90) -> dict[str, Any]:
    """Where the library is heading: how much it grew in the period and what follows from it.

    The estimate has two forms, because we know the free space only where the app sees the files:

    * if we know it, it answers "how long until it runs out";
    * otherwise it says how big the library will be in a year.

    Promising the first where only the second holds would be worse than staying silent.
    """
    rows = snapshots(days)
    result = _growth_base(rows)
    if not result["enough_data"]:
        return result
    first_row, last_row = rows[0], rows[-1]
    daily = result["daily"]
    result["item_count"] = int(last_row["item_count"]) - int(first_row["item_count"])
    result["free_space"] = _free_space_now(last_row.get("free_space"))

    # A shrinking or standing library needs no estimate - and dividing by zero even less. But the disk can fill up
    # anyway (downloads, data outside Jellyfin), so the disks' forecast is asked here too.
    if daily <= 0:
        return _by_disk(result, days)

    result["per_year"] = int(last_row["size_bytes"] + daily * 365)
    # The same value as a bit above, not again from the snapshot: otherwise a capacity entered by hand would show in
    # the row "free space", but the estimate "the space runs out in" would keep counting with what was there at
    # night.
    free = result["free_space"]
    if free:
        result["days_left"] = int(free / daily)
    return _by_disk(result, days)


def _growth_base(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the Library growth and the growth of one disk share: the period's increase.

    `rows` are the curve's points (`day`, `size_bytes`, `backfilled`). Fewer than two points = {"enough_data": False, ...}.
    """
    if len(rows) < 2:
        # One point is no growth. Until then it reports how many days we already have - so that one sees something is
        # going on and only has nothing to say yet.
        return {"enough_data": False, "days": len(rows),
                "size": rows[-1]["size_bytes"] if rows else 0}

    first_row, last_row = rows[0], rows[-1]
    day_span = max(1, (date.fromisoformat(last_row["day"])
                       - date.fromisoformat(first_row["day"])).days)
    increase = int(last_row["size_bytes"]) - int(first_row["size_bytes"])
    return {
        "enough_data": True,
        "days": day_span,
        "size": int(last_row["size_bytes"]),
        "increase": increase,
        "daily": increase / day_span,
        "per_year": None,
        "days_left": None,
        # Does a part of the period stand on the backfill instead of a measurement? The page is to say so - the
        # backfill describes the past with today's sizes, so the number is an estimate, although it looks as exact as
        # the others.
        "backfilled": any(row.get("backfilled") for row in rows),
        "backfilled_until": next((row["day"] for row in reversed(rows)
                              if row.get("backfilled")), ""),
    }


def _segment_start(key: str) -> tuple[str, int] | None:
    """(the first day, Jellyfin's data that day) of the last segment of the disk's snapshots with the same settings.

    Over the disk's whole history, not only the period. Before that segment files were assigned to the disk
    differently (a virtual disk was added or vanished, its paths changed, devices were merged) - such measurements
    cannot be joined with today's, otherwise a virtual disk's files would count for the physical disk in the past
    too. None = the disk has no snapshot.
    """
    series = db.query_all("SELECT day, library, fingerprint, kinds, source, outside, free FROM library_disk_snapshot"
                          " WHERE disk = ? ORDER BY day", (key,))
    complete_disks({key: series})     # music on the disk in snapshots from before it was counted
    segment = _segment(series)
    return (str(segment[0]["day"]), int(segment[0]["library"] or 0)) if segment else None


def disk_growth_series(days: Any, key: str) -> list[dict[str, Any]]:
    """Jellyfin's data on one disk day by day - the Library growth curve for a disk.

    Rows {"day", "size_bytes", "outside", "free", "backfilled"}. The measured days are `library` from the disk's
    snapshots - only from the last segment with the same settings (see _segment_start). The days before it are
    backfilled by _backfilled_curve() only from the files lying on the disk today (by today's folders and virtual
    disks - we have no other knowledge), and anchored on the segment's first measurement, even when it lies past
    the period's end. A virtual disk's file thus does not count for the physical disk even in the past. A disk
    without a single snapshot = an empty series.
    """
    start = _segment_start(key)
    if start is None:
        return []
    boundary, at_start = start
    first_day, last_day = _period_days(days)
    # `kinds` too: the chart's bubble says how much of a day is music (cards.growth_data).
    measured = [{"day": r["day"], "size_bytes": int(r["library"] or 0), "outside": r["outside"],
                 "free": r["free"], "backfilled": False, "kinds": r.get("kinds") or {}}
                for r in disk_snapshots(days).get(key, []) if r["day"] >= boundary]
    if boundary <= first_day:
        return measured
    # `scanner` only here: it pulls in stats itself, so at the top it would make a cycle.
    from .. import scanner

    disk_of = scanner.file_assignment()
    whole = _backfilled_curve(first_day, max(last_day, boundary), belongs=lambda path: disk_of(path) == key,
                              changes_by_kind=disk_changes(key))
    backfilled = [{"day": r["day"], "size_bytes": r["size_bytes"], "outside": None, "free": None, "backfilled": True,
                   "kinds": r.get("kinds") or {}}
                  for r in whole if r["day"] < boundary and r["day"] <= last_day]
    _anchor(backfilled, whole, {"day": boundary, "size_bytes": at_start}, ("size_bytes",))
    return backfilled + measured


def disk_growth(days: Any, key: str, series: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The Library growth tiles for one disk - like library_growth(), only for a disk.

    The increase and "daily" from the disk's curve. "The space runs out in" and the free space the same as the Disks
    card and the notifications: the pace over the last segment of snapshots with the same disk settings
    (stats.disks._pace). A period that ends before the disk's first snapshot has only the backfill: then there is
    neither the free space nor a forecast. The disks' snapshots carry no count of items, hence `item_count` None.
    `series` is a ready disk_growth_series(days, key), if the caller has it computed already (the card draws it) -
    the backfill walks the items of the whole library, twice would be a waste.
    """
    result = _growth_base(disk_growth_series(days, key) if series is None else series)
    result["disk_name"] = str(db.query_value(
        "SELECT name FROM library_disk_snapshot WHERE disk = ? ORDER BY day DESC", (key,), default="") or "")
    if not result["enough_data"]:
        return result
    snapshot_series = disk_snapshots(days).get(key, [])
    last_snapshot = snapshot_series[-1] if snapshot_series else None
    result["item_count"] = None
    result["free_space"] = last_snapshot["free"] if last_snapshot else None
    if last_snapshot:
        _daily, result["days_left"] = _pace(str(last_snapshot["source"] or ""), last_snapshot["free"],
                                               _segment(snapshot_series))
    if result["free_space"] is None and result["daily"] > 0:
        result["per_year"] = int(result["size"] + result["daily"] * 365)
    return result


def _by_disk(result: dict[str, Any], days: Any) -> dict[str, Any]:
    """When Jellyscope knows the library's disks, the forecast of the disk that runs out first holds.

    For disks Jellyfin measures it counts with the drop of the real free space - i.e. with what someone else writes
    to the disk too. Without the disks' snapshots (an old Jellyfin, the first days) the estimate from the library's
    growth stays. When the disks are measured but none is filling, there is no forecast - as on the Disks card.
    With more disks the name of the one in question is added too.
    """
    first_out = runs_out_first(days)
    if first_out is None:
        return result
    result["days_left"] = first_out["days_left"]
    if first_out["free"] is not None:
        result["free_space"] = first_out["free"]
    if first_out["key"] and first_out["disk_count"] > 1:
        result["disk_name"] = first_out["name"]
    return result
