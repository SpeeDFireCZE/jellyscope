"""The library's disks: what the files lie on and how much space is left there.

Part of the package scanner (see jellyscope/scanner/__init__.py).

Jellyfin 12 sends the free and used space and the device's id (`DeviceId`) with every library folder. Folders
with the same id lie on the same disk - that tells whether movies and series are on one disk or on two. On top
of that the admin can enter a **virtual disk**: a path and a capacity, say for a cloud mount that reports
nonsense about itself. The files under that path belong to it and the physical disk does not count them.

Jellyfin is not asked here: the answer is stored during a sync (`remember_folders`) and only read here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import content_kinds, db
from .free_space import CAPACITY_KEY, JF_TOTAL_KEY, SOURCE_KEY, _library_free_space

log = logging.getLogger("jellyscope.scanner")

FOLDERS_KEY = "jf_library_folders"      # library folders from the last sync
FOLDERS_TIME_KEY = "jf_library_folders_at"  # when Jellyfin last sent them (UTC, ISO)
DISK_CAPACITY_KEY = "disks_capacity"  # {DeviceId: {"source": "jellyfin"|"manual", "capacity": bytes}}
SAME_KEY = "disks_same"              # groups of DeviceIds the admin confirmed as one disk
DIFFERENT_KEY = "disks_different"    # pairs they confirmed as different disks
VIRTUAL_KEY = "disks_virtual"        # [{"id", "name", "paths", "capacity"}]
SINGLE_DISK = "library"              # the disk key of the one row of a Jellyfin without the disks broken down
CONVERTED_KEY = "disks_converted"    # "1" = the earlier manual capacity is at a disk already

# How close the numbers of two disks must be for a suspicion of the same disk: 0.1 % of the capacity. Docker
# volumes on one disk report the same numbers, only something may get written between two folders of the
# answer.
TOLERANCE = 0.001

# After how many days without a new answer from Jellyfin its numbers about space stop being taken as today's.
# The full sync runs daily, so three days are two missed syncs and a margin.
MAX_AGE_DAYS = 3

# The disks' settings are a JSON that is read, modified and stored whole. Two writes at the same moment would
# overwrite each other's change - hence one lock for all (Jellyscope is one process). disk_settings.py uses it
# too.
LOCK = threading.RLock()


def _json(key: str, default: Any) -> Any:
    """A setting stored as JSON. Broken or of another shape = the default value."""
    try:
        value = json.loads(db.get_setting(key, "") or "null")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _int(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _space(value: Any) -> int | None:
    """Space in bytes from Jellyfin's answer. None when Jellyfin does not know it.

    It sends an unknown value as -1 (an unreachable network disk, a mount that cannot be measured). If it
    became zero, the disk would look full and the alarm "space runs out in 0 days" would come.
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


# The fingerprint of a disk's settings is computed from the words stored before 1.9.0 (Czech): the snapshots of
# 1.8.x carry fingerprints of them, and other input would cut the forecast of space off at the conversion.
FINGERPRINT_WORDS = {"physical": "fyzicky", "virtual": "virtualni", "single": "jedno", "manual": "rucne"}


def _old(word: Any) -> Any:
    return FINGERPRINT_WORDS.get(word, word) if isinstance(word, str) else word


def _fingerprint(*parts: Any) -> str:
    """A short fingerprint of a disk's settings. Snapshots with the same fingerprint can be compared.

    The pace is the difference of two snapshots. When where the numbers come from changes between them (a
    manual capacity instead of Jellyfin, a virtual disk that took files from the physical one, two devices
    merged), the difference is not a loss of space but a jump - and the forecast would make an alarm of it.
    """
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def normalize_path(path: str) -> str:
    """A path for comparing: slashes `/`, without a trailing slash.

    A path with a drive letter (`C:`) and a network path (`\\\\NAS\\…`) are from Windows, where the case of
    letters does not matter, so it is compared in lower case.
    """
    normalized = str(path or "").replace("\\", "/")
    while len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    if (len(normalized) >= 2 and normalized[1] == ":") or normalized.startswith("//"):
        normalized = normalized.lower()
    return normalized


def is_inside(path: str, folder: str) -> bool:
    """Does `path` lie in `folder` (or is it the folder itself)? By folders, not by characters.

    So `/media/filmy2` does not lie in `/media/filmy`, although it starts that way.
    """
    path, folder = normalize_path(path), normalize_path(folder)
    if not folder:
        return False
    if folder == "/":
        return path.startswith("/")
    return path == folder or path.startswith(folder + "/")


def folders_from_jellyfin(answer: Any) -> list[dict[str, Any]]:
    """Library folders from the answer of `/System/Info/Storage`. Empty for anything else."""
    if not isinstance(answer, dict):
        return []
    folders: list[dict[str, Any]] = []
    for library in answer.get("Libraries") or []:
        if not isinstance(library, dict):
            continue
        for folder in library.get("Folders") or []:
            if not isinstance(folder, dict) or not folder.get("Path"):
                continue
            folders.append({
                "library": str(library.get("Name") or "?"),
                "path": normalize_path(folder["Path"]),
                # Without a device id every folder is a disk of its own - we know nothing better about it.
                "device": str(folder.get("DeviceId") or folder["Path"]),
                "free": _space(folder.get("FreeSpace")),
                "used": _space(folder.get("UsedSpace")),
                "type": str(folder.get("StorageType") or ""),
            })
    return folders


def remember_folders(answer: Any) -> int:
    """Store the library folders from Jellyfin's answer. Called during a sync.

    An empty answer (an older Jellyfin without the endpoint, an outage) does not delete the previous folders:
    the card shows the last known state, not emptiness.
    """
    folders = folders_from_jellyfin(answer)
    if folders:
        db.set_setting(FOLDERS_KEY, json.dumps(folders, ensure_ascii=False))
        db.set_setting(FOLDERS_TIME_KEY, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return len(folders)


def stale_since() -> str:
    """When Jellyfin last sent the space on the disks (UTC, ISO) - only if it was long ago.

    When the request fails during syncs for days (a key without admin rights, an outage), the numbers stored
    from the last success stay. Passing them off as today's would mean a standing free space (pace 0, the
    forecast quietly disappears). An empty string = fresh, or we do not know the time (stored by an older
    version - it did not write the time).
    """
    time_text = db.get_setting(FOLDERS_TIME_KEY, "") or ""
    try:
        when = datetime.fromisoformat(time_text)
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return time_text if datetime.now(timezone.utc) - when > timedelta(days=MAX_AGE_DAYS) else ""


def _same_groups() -> dict[str, str]:
    """DeviceId -> the disk's key by the groups the admin confirmed as one disk.

    Groups with a shared disk join: when the admin confirmed A = C and then C = E, it is one disk A, C, E.
    """
    groups: list[set[str]] = []
    for group in _json(SAME_KEY, []):
        if not isinstance(group, list) or len(group) < 2:
            continue
        new = {str(device) for device in group}
        for old in [s for s in groups if s & new]:
            new |= old
            groups.remove(old)
        groups.append(new)
    disk_key: dict[str, str] = {}
    for group in groups:
        for device in group:
            disk_key[device] = min(group)
    return disk_key


def physical_disks() -> list[dict[str, Any]]:
    """Physical disks from the library folders: by DeviceId and the confirmed groups."""
    confirmed = _same_groups()
    disks: dict[str, dict[str, Any]] = {}
    for folder in _json(FOLDERS_KEY, []):
        if not isinstance(folder, dict) or not folder.get("path"):
            continue
        device = str(folder.get("device") or folder["path"])
        key = confirmed.get(device, device)
        disk = disks.setdefault(key, {
            "key": key, "device": [], "libraries": [], "folders": [],
            "free": None, "used": None, "type": str(folder.get("type") or ""),
        })
        # The numbers are for the whole disk, so those of the first folder that knows both are enough. None =
        # Jellyfin could not measure the space.
        free, used = _space(folder.get("free")), _space(folder.get("used"))
        if disk["free"] is None and free is not None and used is not None:
            disk["free"], disk["used"] = free, used
        for field, value in (("device", device), ("libraries", str(folder.get("library") or "?")),
                             ("folders", normalize_path(folder["path"]))):
            if value not in disk[field]:
                disk[field].append(value)
    for disk in disks.values():
        disk["device"].sort()
        disk["libraries"].sort()
    return sorted(disks.values(), key=lambda disk: disk["key"])


def suspicions(disks: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Pairs of disks that are maybe one and the same (the same capacity and free space).

    Only a suggestion: two different disks may match by chance at the same moment, so the admin decides. A
    pair they marked as different disks is not offered - even after a merge: when C ≠ D and B = C, then B ≠ D
    too.
    """
    different = {frozenset(str(x) for x in pair) for pair in _json(DIFFERENT_KEY, [])
                 if isinstance(pair, list) and len(pair) == 2}
    result: list[tuple[str, str]] = []
    measured = [disk for disk in disks if disk["free"] is not None]
    for i, first in enumerate(measured):
        total = first["free"] + first["used"]
        if not total:
            continue
        for second in measured[i + 1:]:
            if any(frozenset((x, y)) in different for x in first["device"] for y in second["device"]):
                continue
            if (abs(total - (second["free"] + second["used"])) <= total * TOLERANCE
                    and abs(first["free"] - second["free"]) <= total * TOLERANCE):
                result.append((first["key"], second["key"]))
    return result


def disk_paths(paths: Any) -> list[str]:
    """A list of paths from the form or from the settings: normalised, without empty ones and repeats.

    A string is one path - that is how a virtual disk looked before it could have more paths (the key
    `path`), and that is how tests still call it. The order stays as entered.
    """
    if isinstance(paths, str):
        paths = [paths]
    result: list[str] = []
    for path in paths if isinstance(paths, (list, tuple)) else []:
        normalized = normalize_path(str(path or "").strip())
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def virtual_disks() -> list[dict[str, Any]]:
    """Virtual disks entered by the admin (only those with meaningful values).

    A disk has a list `paths`. The older shape with one path (`path`) is read as a list with one path -
    nothing needs converting, it is written the new way at the next save.
    """
    result = []
    for disk in _json(VIRTUAL_KEY, []):
        if not isinstance(disk, dict):
            continue
        paths = disk_paths(disk["paths"] if "paths" in disk else disk.get("path"))
        if disk.get("id") and paths and _int(disk.get("capacity")) > 0:
            result.append({"id": str(disk["id"]), "name": str(disk.get("name") or paths[0]),
                           "paths": paths, "capacity": _int(disk["capacity"])})
    return result


def _ancestors(path: str) -> list[str]:
    """The normalised path and every folder above it, the longest first: /a/b/c, /a/b, /a, /.

    The same paths for which `is_inside(path, folder)` holds - only every library folder is not tried
    separately, they are looked up in a dict.
    """
    result: list[str] = []
    rest = path
    while rest:
        result.append(rest)
        slash = rest.rfind("/")
        if slash <= 0:
            break
        rest = rest[:slash]
    if path.startswith("/") and "/" not in result:
        result.append("/")
    return result


def _assigner(disks: list[dict[str, Any]], virtual: list[dict[str, Any]]) -> Callable[[str], str]:
    """A function: a file's path -> the key of the disk it lies on ("" = outside all).

    A virtual disk first, then a library folder; in both the longest path wins. A file is not tried against
    every folder (with a big library and dozens of folders that was millions of comparisons on every load of
    the Library page): only the folders above it are gone through and the result for its directory is
    remembered - the episodes of one season share a directory.
    """
    virt = {path: f"virt:{v['id']}" for v in virtual for path in v["paths"]}
    folders = {folder: disk["key"] for disk in disks for folder in disk["folders"]}
    dirs: dict[str, tuple[str, str]] = {}

    def above(directory: str) -> tuple[str, str]:
        if directory not in dirs:
            ancestors = _ancestors(directory)
            dirs[directory] = (next((virt[c] for c in ancestors if c in virt), ""),
                               next((folders[c] for c in ancestors if c in folders), ""))
        return dirs[directory]

    def disk_of_file(path: str) -> str:
        path = normalize_path(path)
        slash = path.rfind("/")
        directory = path[:slash] if slash > 0 else ("/" if slash == 0 else "")
        above_it = above(directory) if directory else ("", "")
        return virt.get(path) or above_it[0] or folders.get(path) or above_it[1]

    return disk_of_file


def file_assignment() -> Callable[[str], str]:
    """A file's path -> the disk's key by today's folders and virtual disks ("" = none).

    For the backfill of one disk's growth (stats.disk_growth_series).
    """
    return _assigner(physical_disks(), virtual_disks())


def files_by_disk(disks: list[dict[str, Any]],
                  virtual: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """How many bytes and files lie on which disk: {key: {"bytes", "file_count", "kinds"}}.

    Virtual disks first (the longest path wins), then the library folders (the longest too). A file outside
    everything has the key "" and is added nowhere.
    Files of other content kinds (music; content_kinds.py) count too, by the same assignment;
    `kinds` = their bytes by kind ({"music": b}), only kinds with some bytes on the disk.
    """
    disk_of_file = _assigner(disks, virtual)
    result: dict[str, dict[str, Any]] = {}
    # Rows straight from the cursor, not through query_all: with a hundred thousand files converting every row
    # to a dict shows.
    with db.connect() as conn:
        for row in conn.execute(
                "SELECT path, COALESCE(size_bytes, 0) AS size FROM items"
                " WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"):
            file_sums = result.setdefault(disk_of_file(str(row["path"])), {"bytes": 0, "file_count": 0, "kinds": {}})
            file_sums["bytes"] += int(row["size"] or 0)
            file_sums["file_count"] += 1
    for key, kind_sums in _kinds_by_disk(disk_of_file, disks, virtual).items():
        sums = result.setdefault(key, {"bytes": 0, "file_count": 0, "kinds": {}})
        sums["bytes"] += kind_sums["bytes"]
        sums["file_count"] += kind_sums["file_count"]
        for kind, size in kind_sums["kinds"].items():
            sums["kinds"][kind] = sums["kinds"].get(kind, 0) + size
    return result


# Files of other content kinds by disk for one state of the files and disks (see below).
_kinds_remembered: dict[str, Any] = {}


def _kinds_by_disk(disk_of_file: Callable[[str], str], disks: list[dict[str, Any]],
                   virtual: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{disk: {"bytes", "file_count", "kinds"}} of other content kinds - kept until files or disks change.

    Tens of thousands of tracks made every view of the Disks card go through every path; the
    files' fingerprint (content_kinds) and the disks themselves say when to count again.
    """
    key = content_kinds.fingerprint() + json.dumps([disks, virtual], sort_keys=True, default=str)
    if _kinds_remembered.get("key") != key:
        by_disk: dict[str, dict[str, Any]] = {}
        for kind, path, size in content_kinds.current_files():
            sums = by_disk.setdefault(disk_of_file(path), {"bytes": 0, "file_count": 0, "kinds": {}})
            sums["bytes"] += size
            sums["file_count"] += 1
            if size:
                sums["kinds"][kind] = sums["kinds"].get(kind, 0) + size
        _kinds_remembered.clear()
        _kinds_remembered.update(key=key, by_disk=by_disk)
    return _kinds_remembered["by_disk"]


def _manual_capacity(disk: dict[str, Any], capacities: dict[str, Any]) -> int:
    """A disk's manual capacity in bytes, 0 = none.

    It is looked up under the disk's key and under every one of its devices: after two devices are merged
    into one disk, the disk has the key of only one of them, and a capacity set earlier at the other must not
    quietly disappear.
    """
    for device in [disk["key"], *disk["device"]]:
        setting = capacities.get(device)
        if isinstance(setting, dict) and setting.get("source") == "manual" and _int(setting.get("capacity")) > 0:
            return _int(setting["capacity"])
    return 0


def _physical_row(disk: dict[str, Any], files: dict[str, dict[str, int]], capacities: dict[str, Any],
                  suspected: list[tuple[str, str]], virtual: list[dict[str, Any]],
                  stale: bool = False) -> dict[str, Any]:
    """The card's row for a physical disk: a manual capacity, or the numbers from Jellyfin.

    `stale` = the numbers from Jellyfin are of an older date (see stale_since()): then they are not taken as
    today's, a manual capacity still holds.
    """
    on_disk = files.get(disk["key"], {"bytes": 0, "file_count": 0})
    library = on_disk["bytes"]
    manual = _manual_capacity(disk, capacities)
    if manual:
        total, free, outside, source = manual, max(0, manual - library), None, "manual"
    elif disk["free"] is None or stale:
        # Jellyfin could not measure the space, or it was long ago: rather nothing than zero or a number that
        # poses as today's.
        total, free, outside, source = None, None, None, ""
    else:
        total = disk["free"] + disk["used"]
        free, outside, source = disk["free"], max(0, disk["used"] - library), "jellyfin"
    return {
        "fingerprint": _fingerprint(_old("physical"), _old(source), manual, disk["device"],
                              sorted(path for v in virtual for path in v["paths"])),
        "key": disk["key"], "name": ", ".join(disk["libraries"]), "kind": "physical", "type": disk["type"],
        "source": source, "total": total, "free": free, "library": library, "outside": outside,
        "libraries": disk["libraries"], "paths": disk["folders"], "device": disk["device"],
        "suspicions": [b if a == disk["key"] else a for a, b in suspected if disk["key"] in (a, b)],
        "without_file": on_disk["file_count"] == 0, "kinds": on_disk.get("kinds", {}),
        # What Jellyfin reports, even when a manual capacity holds - the disk's settings window shows it at the
        # option for Jellyfin's numbers, so that there is something to compare with.
        "jellyfin_total": None if disk["free"] is None or stale else disk["free"] + disk["used"],
    }


def _single_state() -> dict[str, Any]:
    """Jellyfin without a list of disks (10.x): one row, the earlier behaviour."""
    library = int(db.query_value(
        "SELECT COALESCE(SUM(COALESCE(size_bytes, 0)), 0) FROM items WHERE is_missing = 0",
        default=0) or 0)
    # Other content kinds (music) count whole - without a disk list, a path is not needed.
    kinds = {key: totals["size"] for key, totals in content_kinds.totals().items() if totals["size"]}
    library += sum(kinds.values())
    free = _library_free_space()
    source = db.get_setting(SOURCE_KEY, "")
    manual = db.get_int_setting(CAPACITY_KEY, 0, 10 ** 18, 0)
    total = manual or db.get_int_setting(JF_TOTAL_KEY, 0, 10 ** 18, 0) or None
    outside = None
    if source == "jellyfin" and total and free is not None:
        outside = max(0, total - free - library)
    row = {"fingerprint": _fingerprint(_old("single"), _old(source), manual),
           "key": SINGLE_DISK, "name": "Library", "kind": "single", "type": "", "source": source,
           "total": total, "free": free, "library": library, "outside": outside, "libraries": [],
           "paths": [], "device": [], "suspicions": [], "without_file": library == 0, "kinds": kinds}
    return {"mode": "single", "disks": [row], "suspicions": [], "outdated_list": ""}


def disk_state() -> dict[str, Any]:
    """The library's disks today: {"mode", "disks": [the card's rows], "suspicions": [pairs]}.

    `mode` is "disks" when Jellyfin sent the list, otherwise "single" (one row as before). A row carries the
    capacity, the free space, the size of the library's files on the disk (`library`) and data outside Jellyfin
    (`outside`, None where they cannot be measured).
    """
    disks = physical_disks()
    if not disks:
        return _single_state()
    convert_old_capacity()
    virtual = virtual_disks()
    files = files_by_disk(disks, virtual)
    capacities = _json(DISK_CAPACITY_KEY, {})
    suspected = suspicions(disks)
    stale = stale_since()
    rows = [_physical_row(disk, files, capacities, suspected, virtual, bool(stale)) for disk in disks]
    # The admin can take back the answer to "the same disk?" - the button only where there is some answer (a
    # merged disk, or "different disks").
    answered = {str(x) for pair in _json(DIFFERENT_KEY, []) if isinstance(pair, list) for x in pair}
    for row in rows:
        row["answered"] = len(row["device"]) > 1 or bool(answered & set(row["device"]))
    for disk in virtual:
        key = f"virt:{disk['id']}"
        on_disk = files.get(key, {"bytes": 0, "file_count": 0, "kinds": {}})
        rows.append({
            "fingerprint": _fingerprint(_old("virtual"), sorted(disk["paths"]), disk["capacity"]),
            "key": key, "name": disk["name"], "kind": "virtual", "type": "", "source": "manual",
            "total": disk["capacity"], "free": max(0, disk["capacity"] - on_disk["bytes"]),
            "library": on_disk["bytes"], "outside": None, "libraries": [], "paths": disk["paths"],
            "device": [], "suspicions": [], "without_file": on_disk["file_count"] == 0,
            "answered": False, "kinds": on_disk.get("kinds", {}),
        })
    return {"mode": "disks", "disks": rows, "suspicions": suspected, "outdated_list": stale}


def _disk_for_old_capacity(disks: list[dict[str, Any]]) -> str:
    """The disk the earlier single manual capacity belongs to. "" = none.

    The one with the largest file - the free space was taken by the same file until now. When the largest file
    lies outside all library folders (an old path, a file outside the libraries), then the disk most of the
    library lies on.
    """
    row = db.query_one(
        "SELECT path FROM items WHERE is_missing = 0 AND path IS NOT NULL AND path != ''"
        " ORDER BY COALESCE(size_bytes, 0) DESC")
    key = _assigner(disks, [])(str((row or {}).get("path") or ""))
    if key:
        return key
    by_disk = {k: v["bytes"] for k, v in files_by_disk(disks, []).items() if k and v["file_count"]}
    return max(by_disk, key=lambda k: by_disk[k]) if by_disk else ""


def convert_old_capacity() -> bool:
    """Move the earlier manual capacity (one for everything) to the library's disk. Once.

    The disk see _disk_for_old_capacity(): nothing changes, it only shows at the right disk. When none fits
    yet (nothing lies on the disks), the capacity stays where it was and the conversion is tried next time -
    earlier the conversion was marked as done at that moment and the capacity kept holding hidden (the field in
    Settings is gone). Without a list of disks (Jellyfin 10.x) nothing is converted: there the manual capacity
    still holds the old way.
    """
    if db.get_setting(CONVERTED_KEY, "") == "1":
        return False
    disks = physical_disks()
    if not disks:
        return False
    with LOCK:
        capacity = db.get_int_setting(CAPACITY_KEY, 0, 10 ** 18, 0)
        if capacity <= 0 or _json(DISK_CAPACITY_KEY, {}):
            db.set_setting(CONVERTED_KEY, "1")
            return False
        key = _disk_for_old_capacity(disks)
        if not key:
            return False
        db.set_setting(DISK_CAPACITY_KEY, json.dumps({key: {"source": "manual", "capacity": capacity}}))
        # Moved, not copied: otherwise it would still hold the old way too (the daily snapshot, alerts) and the
        # numbers would part.
        db.set_setting(CAPACITY_KEY, "0")
        db.set_setting(CONVERTED_KEY, "1")
    log.info("manual capacity of %s B moved to disk %s", capacity, key)
    return True
