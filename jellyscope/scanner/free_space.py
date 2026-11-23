"""Free space on the disk (from Jellyfin or from the capacity) and the library's daily snapshot.

Part of the package scanner (see jellyscope/scanner/__init__.py)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

# `stats` and `formatting` because of the library's daily snapshot (write_snapshot). Neither pulls the scanner
# back, so no cycle comes up - unlike `tasks`, which is imported only inside a function for that reason.
from .. import content_kinds, db, formatting, probe, stats

log = logging.getLogger("jellyscope.scanner")


# ---------------------------------------------------------------------------
# How much space is left
# ---------------------------------------------------------------------------
#
# Three sources, in this order - and the page then says which one it was:
#
#   1. A capacity ENTERED BY HAND. When the library lies in a cloud or on shared storage, not even Jellyfin
#      knows the size. The admin knows it and can write it; their number beats everything else.
#   2. JELLYFIN. It sits by those files, so we ask it.
#   3. THE DISK UNDER THE APP. The original way. It holds only when the data lie on the same machine -
#      otherwise we measure a completely foreign disk, and that was wrong.

CAPACITY_KEY = "library_capacity_bytes"      # entered by hand, in bytes
JF_FREE_KEY = "jellyfin_free_bytes"         # from the last sync
JF_TOTAL_KEY = "jellyfin_total_bytes"
SOURCE_KEY = "free_space_source"              # manual / jellyfin / disk


def _number(value: Any) -> int | None:
    """A number from Jellyfin's answer. None for anything that is not a number."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def space_from_jellyfin(answer: Any, path: str = "") -> dict[str, int]:
    """Free and total space from the answer of /System/Storage.

    The field names differed between Jellyfin versions, so all the usual shapes are taken. When nothing fits,
    we return emptiness - guessing a number from which "space runs out in X days" is then computed would be
    worse than admitting we do not know it.

    `path` is the library's folder. When we find it among the folders, that one holds; otherwise the one with
    the least free space is taken, because it runs out first.
    """
    if not isinstance(answer, dict):
        return {}

    # Jellyfin 12 (`/System/Info/Storage`, verified on 12.1.0) sends a dict of the server's named folders
    # (ProgramDataFolder, LogFolder, ...) and with it `Libraries`: a list of libraries, each with its list of
    # `Folders`. The libraries' folders are the ones that matter - the movies lie on them. The server's folders
    # are taken only as a fallback: when the library lies on another disk than the server, the number of a
    # server folder would lie.
    folders: list[dict[str, Any]] = []
    for library in answer.get("Libraries") or []:
        if isinstance(library, dict):
            folders.extend(s for s in library.get("Folders") or []
                           if isinstance(s, dict))
    for key in ("Folders", "folders", "StorageFolders", "Items"):
        value = answer.get(key)
        if isinstance(value, list):
            folders.extend(s for s in value if isinstance(s, dict))
    if not folders:
        folders = [v for v in answer.values()
                   if isinstance(v, dict) and "Path" in v]
    if not folders:
        # Sometimes one folder comes directly, not a list.
        folders = [answer]

    def field(folder: dict[str, Any], *names: str) -> int | None:
        for name in names:
            number = _number(folder.get(name))
            if number is not None:
                return number
        return None

    measured = []
    for folder in folders:
        free = field(folder, "FreeSpace", "freeSpace", "FreeSpaceBytes")
        total = field(folder, "TotalSpace", "totalSpace", "TotalSpaceBytes")
        # Version twelve does not send the total space - only free and used. Their sum is the same number; -1
        # means "cannot measure" and `_number` drops it.
        used = field(folder, "UsedSpace", "usedSpace")
        if total is None and free is not None and used is not None:
            total = free + used
        if free is None and total is None:
            continue
        measured.append({
            "path": str(folder.get("Path") or folder.get("path") or ""),
            "free": free, "total": total,
        })
    if not measured:
        return {}

    path = (path or "").replace("\\", "/").rstrip("/").lower()
    if path:
        for entry in measured:
            other = entry["path"].replace("\\", "/").rstrip("/").lower()
            if other and (path.startswith(other) or other.startswith(path)):
                return {k: v for k, v in entry.items()
                        if k != "path" and v is not None}

    # No folder fits the library - we take the tightest one, because space runs out on it.
    with_free = [p for p in measured if p["free"] is not None]
    tightest = min(with_free, key=lambda p: p["free"]) if with_free else measured[0]
    return {k: v for k, v in tightest.items() if k != "path" and v is not None}


def remember_space_from_jellyfin(answer: Any) -> dict[str, int]:
    """Store what Jellyfin said about the space. Called during a sync.

    It is stored in the settings, not in the snapshot: the snapshot is written only afterwards and
    `write_snapshot()` is synchronous - it cannot ask Jellyfin from there any more.
    """
    row = db.query_one(
        "SELECT path FROM items"
        " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    path = str((row or {}).get("path") or "")

    space = space_from_jellyfin(answer, path)
    db.set_setting(JF_FREE_KEY, str(space.get("free", "")))
    db.set_setting(JF_TOTAL_KEY, str(space.get("total", "")))
    return space


def _free_from_capacity() -> int | None:
    """Free space from a capacity entered by hand: the capacity minus the library's size.

    It is an estimate and the page says so: it assumes nothing else lies on that storage. The admin knows a
    number nobody else knows, though - for a library in a cloud it is the only way to say anything.
    """
    capacity = db.get_int_setting(CAPACITY_KEY, 0, 10 ** 18, 0)
    if capacity <= 0:
        return None
    size = int(db.query_value(
        "SELECT COALESCE(SUM(COALESCE(size_bytes, 0)), 0) FROM items"
        " WHERE is_missing = 0", default=0) or 0)
    # Every file Jellyfin has, music too - the Library row counts it as Jellyfin data.
    size += sum(totals["size"] for totals in content_kinds.totals().values())
    return max(0, capacity - size)


def _library_free_space() -> int | None:
    """Free space where the library lies. None when we do not know it.

    For the order of the sources see the comment at the start of this section.
    """
    manual = _free_from_capacity()
    if manual is not None:
        db.set_setting(SOURCE_KEY, "manual")
        return manual

    from_jellyfin = db.get_int_setting(JF_FREE_KEY, 0, 10 ** 18, 0)
    if from_jellyfin > 0:
        db.set_setting(SOURCE_KEY, "jellyfin")
        return from_jellyfin

    # `tasks` only here: it pulls the scanner itself, so at the top it would make a cycle.
    from .. import tasks

    row = db.query_one(
        "SELECT path FROM items"
        " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    if not row or not row.get("path"):
        db.set_setting(SOURCE_KEY, "")
        return None

    try:
        mappings = json.loads(db.get_setting("path_mappings", "") or "[]")
        if not isinstance(mappings, list):
            mappings = []
    except ValueError:
        mappings = []

    path = Path(probe.apply_path_mappings(str(row["path"]), mappings))
    # The file's folder, not the file itself - `disk_usage` wants a directory.
    free = tasks.free_space(str(path.parent))
    db.set_setting(SOURCE_KEY, "disk" if free is not None else "")
    return free


def write_snapshot(free_bytes: int | None = None) -> dict[str, Any] | None:
    """Store today's state of the library - one row a day.

    `free_bytes` is sent by whoever knows it better (disk_snapshots.write_daily_snapshots: the free space of
    the disks from the Disks card). Without it the sources described above hold.

    The same day is overwritten: the day's last known state holds, not the first. When something gets
    analysed or added during the day, the snapshot is to show it.

    The day is taken in the APP'S ZONE, so that the rows match what the charts show - not in UTC, where an
    evening sync would be written to tomorrow already.
    """
    # The same sums the end of the growth curve draws and the tile "Total size" shows - one function, so
    # those three numbers cannot drift apart. See stats.library_state().
    summary = stats.library_state()
    if not summary or not (summary.get("item_count") or summary.get("kinds")):
        return None          # an empty library (not even music): nothing to record

    day = datetime.now(formatting.zone()).strftime("%Y-%m-%d")
    row = {
        "day": day,
        "item_count": int(summary["item_count"] or 0),
        "movie_count": int(summary["movie_count"] or 0),
        "episode_count": int(summary["episode_count"] or 0),
        "size_bytes": int(summary["size_bytes"] or 0),
        "uhd": int(summary["uhd"] or 0),
        "hdr": int(summary["hdr"] or 0),
        "without_tech": int(summary["without_tech"] or 0),
        "free_space": free_bytes if free_bytes is not None else _library_free_space(),
        "written_at": db.utcnow(),
        # Parts by every content kind, zeros included (`size_bytes` above is the whole): a kind in
        # the JSON was measured, a missing one - or NULL, a snapshot written before kinds were
        # counted - is computed when read (stats/content_history.py).
        "kinds": json.dumps({kind.key: (summary.get("kinds") or {}).get(kind.key, {"items": 0, "size": 0})
                             for kind in content_kinds.KINDS}, sort_keys=True),
    }

    # The values are listed, not taken from `row.values()`: relying on the order of keys in a dict is a trap
    # that shows when a column is added by the numbers swapping places - and nobody notices.
    columns = ("day", "item_count", "movie_count", "episode_count", "size_bytes", "uhd", "hdr",
               "without_tech", "free_space", "written_at", "kinds")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO library_snapshot
                (day, item_count, movie_count, episode_count, size_bytes, uhd, hdr,
                 without_tech, free_space, written_at, kinds)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (day) DO UPDATE SET
                item_count     = excluded.item_count,
                movie_count       = excluded.movie_count,
                episode_count      = excluded.episode_count,
                size_bytes    = excluded.size_bytes,
                uhd         = excluded.uhd,
                hdr         = excluded.hdr,
                without_tech = excluded.without_tech,
                free_space = excluded.free_space,
                written_at   = excluded.written_at,
                kinds       = excluded.kinds
            """,
            tuple(row[name] for name in columns),
        )
        conn.commit()

    log.info("library snapshot %s: %s items, %s bytes",
             day, row["item_count"], row["size_bytes"])
    return row
