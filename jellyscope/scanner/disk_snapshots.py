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
    convert_old_capacity,
    disk_state,
    physical_disks,
    suspicions,
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
    """One number of free space for the library - for the API, the daily snapshot and Library growth "All".

    With the disks known it is the sum of the free space of every disk, physical and virtual, as the Disks card
    shows it (with manual capacities) - from the latest disk snapshot, which is rewritten after a sync and after
    every change on the card; without one from the disks themselves. A disk whose free space is unknown is left
    out, none known = None. Disks suspected of being one (Docker volumes with the same numbers) count once until
    the admin answers "different disks" - a reader must not see twice the space; the card asks the admin. Until
    23. 11. 2026 it was the disk the largest file lies on (the user: "All" is the whole storage). Without a disk
    list (Jellyfin 10.x) the earlier sources apply.
    """
    disks = physical_disks()
    if not disks:
        return _library_free_space()
    convert_old_capacity()
    rows = [{"key": row["disk"], "kind": row["kind"], "free": row["free"]} for row in db.query_all(
        "SELECT disk, kind, free FROM library_disk_snapshot WHERE day = (SELECT MAX(day) FROM library_disk_snapshot)")]
    rows = [row for row in rows if row["kind"] in ("physical", "virtual")] \
        or [row for row in disk_state()["disks"] if row["kind"] in ("physical", "virtual")]
    counted = _one_per_group([str(row["key"]) for row in rows], suspicions(disks))
    known = [int(row["free"]) for row in rows if str(row["key"]) in counted and row["free"] is not None]
    return sum(known) if known else None


def _one_per_group(keys: list[str], pairs: list[tuple[str, str]]) -> set[str]:
    """The keys to count: one key of every group joined by suspected pairs (A~B, B~C), the others are left out."""
    group = {key: key for key in keys}

    def root(key: str) -> str:
        while group[key] != key:
            key = group[key]
        return key

    for first, second in pairs:
        if first in group and second in group:
            group[root(second)] = root(first)
    return {key for key in keys if root(key) == key}


def write_daily_snapshots() -> dict[str, Any] | None:
    """After a sync: the disks' snapshot, then the library's with the same free space.

    Disks first - the library snapshot takes its free space from them, so that the numbers in
    the Disks card and in the library growth do not part.
    """
    write_disk_snapshot()
    return write_snapshot(free_space_now())
