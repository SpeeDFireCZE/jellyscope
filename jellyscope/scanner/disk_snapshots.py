"""Daily snapshots of the disks and the one free-space number of the library.

Moved out of the disks module (16. 11. 2026) when it reached its 600 lines; the names are read across the
application through the door (scanner.write_disk_snapshot and the others).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .. import content_kinds, db, formatting
from .disks import (
    _assigner,
    convert_old_capacity,
    disk_state,
    physical_disks,
    virtual_disks,
)
from .free_space import _library_free_space, write_snapshot


def write_disk_snapshot(day: str | None = None) -> int:
    """Store today's state of the disks - a row per disk, the same day is overwritten.

    Written with the library snapshot after a sync and after every change of capacity in the
    Disks card: the forecast is computed from snapshots, and waiting for the nightly sync after
    a capacity was entered would look as if the change did nothing. Returns the rows written.

    A disk that has gone meanwhile (a deleted virtual one, merged devices) is deleted from the
    same day's snapshot: otherwise until midnight the tiles and alerts would name a disk that is
    no more. Older days stay.
    """
    day = day or datetime.now(formatting.zone()).strftime("%Y-%m-%d")
    rows = disk_state()["disks"]
    # A kind is measured on a disk only once every path of it is known; Jellyfin 10.x's one row
    # counts every file without paths, so it measures every kind.
    measured = content_kinds.kinds_with_paths()
    keys = [row["key"] for row in rows]
    with db.connect() as conn:
        conn.execute(f"DELETE FROM library_disk_snapshot WHERE day = ? AND disk NOT IN ({', '.join('?' * len(keys))})",
                     (day, *keys))
        for row in rows:
            conn.execute(
                """
                INSERT INTO library_disk_snapshot
                    (day, disk, name, kind, source, total, free, library, outside, fingerprint, written_at, kinds)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (day, disk) DO UPDATE SET
                    name = excluded.name, kind = excluded.kind, source = excluded.source,
                    fingerprint = excluded.fingerprint,
                    total = excluded.total, free = excluded.free,
                    library = excluded.library, outside = excluded.outside,
                    written_at = excluded.written_at, kinds = excluded.kinds
                """,
                (day, row["key"], row["name"], row["kind"], row["source"] or "",
                 row["total"], row["free"], row["library"], row["outside"], row["fingerprint"],
                 # `library` is the whole; its parts by every measured content kind, 0 included: a kind
                 # in the JSON was measured, a missing one is computed when read (stats/content_history.py).
                 db.utcnow(), json.dumps({kind.key: int((row.get("kinds") or {}).get(kind.key, 0))
                                          for kind in content_kinds.KINDS
                                          if kind.key in measured or row["kind"] == "single"}, sort_keys=True)),
            )
        conn.commit()
    return len(rows)


def free_space_now() -> int | None:
    """One number of free space for the library - for the API, the daily snapshot and the growth.

    With the disks known, it is the free space of the disk the largest file lies on (the same
    file as always), as the Disks card shows it - that is with a manual capacity. Otherwise,
    after converting the old manual capacity to a disk, it would return the number Jellyfin
    reports for the mount, the one the administrator entered a capacity because of. Without a
    disk list (Jellyfin 10.x) the earlier sources apply.
    """
    disks = physical_disks()
    if not disks:
        return _library_free_space()
    convert_old_capacity()
    row = db.query_one(
        "SELECT path FROM items WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    key = _assigner(disks, virtual_disks())(str((row or {}).get("path") or ""))
    if not key:
        return _library_free_space()     # the largest file lies on no known disk
    # From the snapshot when there is one: it is rewritten after a sync and after every change
    # in the card, and one number is not worth going through the whole library.
    snapshot = db.query_one(
        "SELECT free FROM library_disk_snapshot WHERE disk = ? ORDER BY day DESC", (key,))
    if snapshot:
        return int(snapshot["free"]) if snapshot["free"] is not None else None
    return next((r["free"] for r in disk_state()["disks"] if r["key"] == key), None)


def write_daily_snapshots() -> dict[str, Any] | None:
    """After a sync: the disks' snapshot, then the library's with the same free space.

    Disks first - the library snapshot takes its free space from them, so that the numbers in
    the Disks card and in the library growth do not part.
    """
    write_disk_snapshot()
    return write_snapshot(free_space_now())
