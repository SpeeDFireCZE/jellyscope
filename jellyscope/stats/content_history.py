"""Other content kinds (music) in the history of the library: computed days and old snapshots.

Snapshots written before 16. 11. 2026 hold video only (`kinds` NULL); from then on the whole
and its parts by kind. Old ones are not rewritten - the reader completes them here with the
part computed from the days files were added (content_kinds.history), seated on the first
snapshot that measured the kind, so the curve does not jump on the day of the upgrade.

Reading the files' history is the costly part (tens of thousands of tracks): it is read once
and its changes by day - for the whole library and by disk - are kept until the files change
(content_kinds.fingerprint).

Spec: docs/superpowers/specs/2026-11-16-content-kinds-size-design.md, part 6.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from .. import content_kinds

# {kind: {day: bytes that came (+) or went (-) that day}}
Changes = dict[str, dict[str, int]]

# What was computed for one state of the files: "fingerprint" plus "*" (every file) and
# ("disks", assignment) -> {disk: Changes}. A new fingerprint starts it afresh.
_remembered: dict[Any, Any] = {}


def kind_changes(file_history: list[dict[str, Any]],
                 belongs: Callable[[str], bool] | None = None) -> Changes:
    """Changes by kind and day: a file comes on the day it was added, goes on the day it went.

    `belongs` picks files by path (one disk); a file without a path then belongs nowhere.
    """
    changes: Changes = {}
    for row in file_history:
        if belongs is not None and not (row["path"] and belongs(row["path"])):
            continue
        per_day = changes.setdefault(row["kind"], {})
        per_day[row["added"]] = per_day.get(row["added"], 0) + row["size"]
        if row["gone"]:
            per_day[row["gone"]] = per_day.get(row["gone"], 0) - row["size"]
    return changes


def series_from_changes(changes: Changes, first: str, last: str) -> dict[str, dict[str, int]]:
    """{kind: {day: bytes}} for every day first..last - the running total of files present.

    A file counts from the day it was added up to the day it went (that day no more, like the
    video backfill).
    """
    result: dict[str, dict[str, int]] = {}
    end = date.fromisoformat(last)
    for kind in content_kinds.KINDS:
        per_day = changes.get(kind.key, {})
        running = sum(size for when, size in per_day.items() if when < first)
        series: dict[str, int] = {}
        current = date.fromisoformat(first)
        while current <= end:
            key = current.isoformat()
            running += per_day.get(key, 0)
            series[key] = running
            current += timedelta(days=1)
        result[kind.key] = series
    return result


def _remember(fingerprint: str, key: Any, compute: Callable[[], Any]) -> Any:
    if _remembered.get("fingerprint") != fingerprint:
        _remembered.clear()
        _remembered["fingerprint"] = fingerprint
    if key not in _remembered:
        _remembered[key] = compute()
    return _remembered[key]


def all_changes() -> Changes:
    """Changes of every file, a path or not - the library, Jellyfin 10.x's one disk row."""
    return _remember(content_kinds.fingerprint(), "*", lambda: kind_changes(content_kinds.history()))


def changes_by_disk(assignment: Callable[[], Callable[[str], str]], assignment_key: str) -> dict[str, Changes]:
    """{disk: Changes} of files with a path, assigned to disks as `assignment()` does today.

    `assignment_key` says what the assignment depends on (the disks and virtual disks) - a new
    one computes afresh. The history read here serves all_changes too.
    """
    def compute() -> dict[str, Changes]:
        file_history = content_kinds.history()
        if "*" not in _remembered:
            _remembered["*"] = kind_changes(file_history)
        disk_of = assignment()
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in file_history:
            if row["path"]:
                buckets.setdefault(disk_of(row["path"]), []).append(row)
        return {disk: kind_changes(rows) for disk, rows in buckets.items()}

    return _remember(content_kinds.fingerprint(), ("disks", assignment_key), compute)


def kinds_of(raw: Any) -> dict[str, int] | None:
    """`kinds` of a snapshot as {kind: bytes}; None = written before kinds were counted.

    The library stores {"music": {"items", "size"}}, a disk {"music": bytes}.
    """
    if raw is None or raw == "":
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {str(key): int((value.get("size") if isinstance(value, dict) else value) or 0)
            for key, value in data.items()}


def complete_rows(rows: list[dict[str, Any]], field: str, changes: Callable[[], Changes]) -> list[int]:
    """Complete snapshot rows (one series, by day) in place; returns what was added to each.

    Every row gets `kinds` = {kind: bytes}. A kind a row does not carry is computed for its day
    and added to `field` when the row is older than the first row that measured the kind (or no
    row did): the computed part is seated on that measurement - shifted by one number, never
    below zero. A row after it without the kind measured none (the kind was gone then).
    `changes` is asked only when some row needs completing.
    """
    added = [0] * len(rows)
    if not rows:
        return added
    parts = [kinds_of(row.get("kinds")) for row in rows]
    completed: list[dict[str, int]] = [dict(p or {}) for p in parts]
    series: dict[str, dict[str, int]] | None = None
    for kind in content_kinds.KINDS:
        anchor = next((i for i, p in enumerate(parts) if p is not None and kind.key in p), None)
        todo = [i for i, p in enumerate(parts) if (p is None or kind.key not in p) and (anchor is None or i < anchor)]
        if not todo:
            continue
        if series is None:
            series = series_from_changes(changes(), str(rows[0]["day"]), str(rows[-1]["day"]))
        computed = series[kind.key]
        shift = 0
        if anchor is not None:
            shift = parts[anchor][kind.key] - computed.get(str(rows[anchor]["day"]), 0)  # type: ignore[index]
        for i in todo:
            value = max(0, computed.get(str(rows[i]["day"]), 0) + shift)
            if value:
                completed[i][kind.key] = value
                rows[i][field] = int(rows[i].get(field) or 0) + value
                added[i] += value
    for row, kinds in zip(rows, completed):
        row["kinds"] = {key: size for key, size in kinds.items() if size}
    return added


def complete_library_rows(rows: list[dict[str, Any]]) -> None:
    """Library snapshots (stats.snapshots): `size_bytes` completed with the computed kinds."""
    complete_rows(rows, "size_bytes", all_changes)


def complete_disk_rows(rows: list[dict[str, Any]], changes: Callable[[], Changes]) -> None:
    """Snapshots of one disk, by day: the kinds computed for files on it (`changes` of the disk).

    What is added to `library` (Jellyfin data) was counted in `outside` before - the disk was as
    full - so it comes off there (when measured), and with a manual capacity off the free space,
    which is that capacity minus Jellyfin data (the Disks card's row). Never below zero.
    """
    for row, extra in zip(rows, complete_rows(rows, "library", changes)):
        if not extra:
            continue
        if row.get("outside") is not None:
            row["outside"] = max(0, int(row["outside"]) - extra)
        if row.get("source") == "manual" and row.get("free") is not None:
            row["free"] = max(0, int(row["free"]) - extra)
