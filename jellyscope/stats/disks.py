"""The library's disks: the pace of filling and in how many days the space runs out.

Part of the statistics package (see jellyscope/stats/__init__.py).

The pace is taken by what can be measured about the disk:

* a disk whose space Jellyfin reports - **from the drop of the real free space**. It catches what someone else writes
  to the disk too (downloads, backups), and exactly because of that the space can run out sooner than the library's
  growth alone would say;
* a disk with a manual capacity and a virtual disk - **from the growth of the library's files** on it. We do not
  know the real free space there, only what we computed.

A disk standing still or freeing up has no forecast. Until a disk has two snapshots with the same settings (its
first day, the day after its settings changed) the forecast comes from the growth of its files over the period
(files_forecast) - the caller passes stats.disk_growth, because growth.py imports from here.

An older Jellyfin (10.x) does not break the disks down: its one row "Library" has a forecast as before, from the
Library growth (see disks_overview).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from .. import content_kinds, db, formatting
from .content_history import Changes, all_changes, changes_by_disk, complete_disk_rows, kinds_of
from .periods import _local_day, _as_period

# The sources whose free space is measured, not computed.
MEASURED = ("jellyfin", "disk")
# The rows of real disks - not the one row "Library" of Jellyfin 10.x.
DISK_KINDS = ("physical", "virtual")
# The disk key of a Jellyfin without the disks broken down (scanner.disks.SINGLE_DISK; scanner imports stats).
SINGLE_DISK = "library"


def disk_snapshots(days: Any = 90) -> dict[str, list[dict[str, Any]]]:
    """The disks' snapshots in the period, by disk and by day: {disk: [rows]}."""
    period = _as_period(days)
    first_day = _local_day(period.since_local, period.since).isoformat()
    last_day = _local_day(period.until_local, period.until, end=True).isoformat() \
        if not period.relative else datetime.now(formatting.zone()).date().isoformat()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in db.query_all(
            "SELECT day, disk, name, kind, source, total, free, library, outside, fingerprint, kinds"
            "  FROM library_disk_snapshot WHERE day >= ? AND day <= ? ORDER BY day",
            (first_day, last_day)):
        result.setdefault(str(row["disk"]), []).append(dict(row))
    complete_disks(result)
    return result


def complete_disks(by_disk: dict[str, list[dict[str, Any]]]) -> None:
    """Snapshots from before music was counted get it on their disk (content_history), in place.

    Files are assigned to disks as today (scanner.file_assignment), the same knowledge the disk
    backfill has. The history of files is read only when some row needs it, and kept until the
    files change (content_history).
    """
    if all(_has_kinds(row) for rows in by_disk.values() for row in rows):
        # Nothing to complete; `kinds` the same shape as completed rows have: {kind: bytes}.
        for rows in by_disk.values():
            for row in rows:
                row["kinds"] = {key: size for key, size in (kinds_of(row.get("kinds")) or {}).items() if size}
        return
    for disk_key, rows in by_disk.items():
        if disk_key != SINGLE_DISK:
            complete_disk_rows(rows, lambda disk_key=disk_key: disk_changes(disk_key))
        else:                        # Jellyfin 10.x's one row holds every file, path or not
            complete_disk_rows(rows, all_changes)


def disk_changes(key: str) -> Changes:
    """Changes of other content kinds on one disk, by day - files assigned to disks as today."""
    # `scanner` only here: it pulls in stats itself, so at the top it would make a cycle.
    from .. import scanner

    assignment_key = json.dumps([scanner.physical_disks(), scanner.virtual_disks()], sort_keys=True, default=str)
    return changes_by_disk(scanner.file_assignment, assignment_key).get(key, {})


def _has_kinds(row: dict[str, Any]) -> bool:
    """Does the snapshot carry every content kind (no need to complete it)?"""
    kinds = row.get("kinds")
    if not kinds:
        return False
    try:
        return all(kind.key in json.loads(kinds) for kind in content_kinds.KINDS)
    except (TypeError, ValueError):
        return False


def _segment(series: list[dict[str, Any]], fingerprint: str | None = None) -> list[dict[str, Any]]:
    """The end of the series of snapshots where the disk's settings did not change (the same `fingerprint`).

    When the admin switches a disk from a manual capacity to Jellyfin, adds a virtual disk or merges two devices,
    the numbers before the change and after it cannot be compared: the difference is a jump, not a drop of space.
    `fingerprint` is today's setting (for the card); without it the last snapshot's setting holds.
    """
    if not series:
        return []
    fingerprint = series[-1].get("fingerprint") if fingerprint is None else fingerprint
    segment: list[dict[str, Any]] = []
    for row in reversed(series):
        if row.get("fingerprint") != fingerprint:
            break
        segment.insert(0, row)
    return segment


def _pace(source: str, free: Any, series: list[dict[str, Any]]) -> tuple[float | None, int | None]:
    """(bytes per day, in how many days the space runs out) from the period's first and last value."""
    if len(series) < 2:
        return None, None
    day_span = (date.fromisoformat(series[-1]["day"]) - date.fromisoformat(series[0]["day"])).days
    if day_span <= 0:
        return None, None
    if source in MEASURED:
        if series[0]["free"] is None or series[-1]["free"] is None:
            return None, None
        daily = (int(series[0]["free"]) - int(series[-1]["free"])) / day_span
    else:
        daily = (int(series[-1]["library"] or 0) - int(series[0]["library"] or 0)) / day_span
    if daily <= 0 or free is None:
        return daily, None
    return daily, int(int(free) / daily)


def files_forecast(free: Any, growth: dict[str, Any] | None) -> int | None:
    """In how many days the space runs out at the growth of the disk's files: free / bytes a day. None = none.

    The forecast while the disk's snapshots cannot give a pace (a single one - the first day of the disks, or the
    day after the disk's settings changed). `growth` is stats.disk_growth() of the disk: its daily growth over the
    period is the one the chart's curve shows. A disk that does not grow, or whose free space is unknown, has none.
    """
    if free is None or not growth or not growth.get("enough_data"):
        return None
    daily = growth.get("daily") or 0
    if daily <= 0:
        return None
    return int(int(free) / daily)


def _files_pace(days: Any, key: str, free: Any,
                disk_growth: Callable[[Any, str], dict[str, Any]]) -> tuple[float | None, int | None]:
    """(bytes per day, days left) from the disk's file growth - the fallback of _pace (see files_forecast)."""
    growth = disk_growth(days, key)
    if not growth.get("enough_data"):
        return None, None
    return growth["daily"], files_forecast(free, growth)


def disks_to_choose() -> list[dict[str, str]]:
    """The disks to choose in the Library growth: [{"key", "name", "choice"}] with the names from the Disks card.

    `choice` is what the chip's address and the session carry: a hash of the key, not the key itself. A physical
    disk's key is the device's id, i.e. the mount point on the server (`/mnt/disk1`, `D:\\`) - and the chip is seen
    by a reader too who is not to see paths.

    From the last day of the disks' snapshots (rewritten after the sync and after every change on the card), not
    from walking the files. Physical ones by key, then virtual ones by name. Without snapshots and with a Jellyfin
    that does not break the disks down (only the row "one") an empty list.
    """
    rows = [r for r in db.query_all(
        "SELECT disk, name, kind FROM library_disk_snapshot"
        " WHERE day = (SELECT MAX(day) FROM library_disk_snapshot)") if r["kind"] in DISK_KINDS]
    rows.sort(key=lambda r: (r["kind"] != "physical", str(r["disk"] if r["kind"] == "physical" else r["name"])))
    return [{"key": str(r["disk"]), "name": str(r["name"]),
             "choice": hashlib.sha256(str(r["disk"]).encode("utf-8")).hexdigest()[:12]} for r in rows]


def _outside_series(series_list: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """By day: the library's files and the data outside Jellyfin - only from disks where they can be measured."""
    by_day: dict[str, dict[str, Any]] = {}
    for series in series_list.values():
        for row in series:
            if row["outside"] is None:
                continue
            day = by_day.setdefault(row["day"], {"day": row["day"], "library": 0, "outside": 0})
            day["library"] += int(row["library"] or 0)
            day["outside"] += int(row["outside"] or 0)
    return [by_day[day] for day in sorted(by_day)]


def outside_series(days: Any = 90) -> list[dict[str, Any]]:
    """Jellyfin's data and the data outside it by day - for the Library growth chart, only from the snapshots."""
    return _outside_series(disk_snapshots(days))


def disks_overview(days: Any = 90,
                   growth: Callable[[Any], dict[str, Any]] | None = None,
                   disk_growth: Callable[[Any, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """The Disks card: today's state of the disks with the pace, the forecast and a series for the chart.

    To `scanner.disk_state()` it adds `daily` and `days_left` for every disk, and also `earliest` (the disk whose
    space runs out first, or None).

    `growth` is stats.library_growth - the caller passes it, because growth.py takes the disks' forecast from here
    and an import the other way would be a cycle. It is used only with a Jellyfin that does not break the disks
    down: its one row "Library" shows the same forecast as the Library growth, not another. `disk_growth` is
    stats.disk_growth, for the same reason: a disk whose snapshots give no pace yet gets the forecast from its
    file growth (files_forecast), the same number as the Library growth chart for that disk.
    """
    # `scanner` only here: it pulls in stats itself, so at the top it would make a cycle.
    from .. import scanner

    state = scanner.disk_state()
    series_by_disk = disk_snapshots(days)
    for row in state["disks"]:
        if row["kind"] not in DISK_KINDS:
            growth_result = growth(days) if growth else {}
            row["daily"] = growth_result.get("daily") if growth_result.get("enough_data") else None
            row["days_left"] = growth_result.get("days_left")
            continue
        daily, days_left = _pace(
            str(row["source"] or ""), row["free"], _segment(series_by_disk.get(row["key"], []), row["fingerprint"]))
        # The fallback only for a disk measured in the period: a past period before its first snapshot has no free
        # space of its own (the chart says nothing there), today's would be a number for another time.
        if daily is None and disk_growth is not None and row["free"] is not None and series_by_disk.get(row["key"]):
            daily, days_left = _files_pace(days, row["key"], row["free"], disk_growth)
        row["daily"], row["days_left"] = daily, days_left
    with_forecast = [row for row in state["disks"] if row["days_left"] is not None]
    state["earliest"] = min(with_forecast, key=lambda row: row["days_left"]) if with_forecast else None
    return state


def runs_out_first(days: Any = 90,
                   disk_growth: Callable[[Any, str], dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The disk whose space runs out first - from the disks' snapshots.

    For Library growth "All" (the tile "Fills first") and the space alert. The snapshot is written after the sync
    and after a change of capacity, so it is fresh. Only the disks of the last snapshot count - a deleted virtual
    disk plays no role any more.

    Returns {key, name, free, daily, days_left, disk_count}, or None:

    * None - the disks have nothing to say yet (no snapshots, Jellyfin 10.x without the disks broken down, or only
      one day of measuring). Then the estimate from the library's growth holds as before.
    * `days_left` None (and `key` None) - the disks are measured, but none is filling. The estimate from the
      library's growth would contradict the Disks card here.

    `disk_growth` (stats.disk_growth, passed by the caller - growth.py imports from here) gives a disk whose
    snapshots have no pace yet the forecast from its file growth; it walks the disk's files, so only for such disks.
    """
    series_by_disk = disk_snapshots(days)
    if not series_by_disk:
        return None
    last_day = max(series[-1]["day"] for series in series_by_disk.values())
    current = {disk: series for disk, series in series_by_disk.items()
               if series[-1]["day"] == last_day and series[-1]["kind"] in DISK_KINDS}
    best, measured = None, False
    for disk, series in current.items():
        last_row = series[-1]
        daily, days_left = _pace(str(last_row["source"] or ""), last_row["free"], _segment(series))
        if daily is None and disk_growth is not None and last_row["free"] is not None:
            daily, days_left = _files_pace(days, disk, last_row["free"], disk_growth)
        measured = measured or daily is not None
        if days_left is not None and (best is None or days_left < best["days_left"]):
            best = {"key": disk, "name": last_row["name"], "free": last_row["free"],
                    "daily": daily, "days_left": days_left}
    if best is None and measured:
        best = {"key": None, "name": None, "free": None, "daily": None, "days_left": None}
    if best:
        best["disk_count"] = len(current)
    return best
