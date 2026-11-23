"""The library's disks: what the admin changes in the Disks card.

Part of the package scanner (see jellyscope/scanner/__init__.py). The capacity of a physical disk, virtual
disks and the answer to "maybe the same disk". It checks the input, stores it in the settings and returns a
message to show; the routes in web/disks.py only read the form. What follows from the settings (the card's
rows, snapshots) is computed by disks.py.

Every change is read - modify - write over one JSON in the settings. Two admins saving at the same moment
would otherwise overwrite each other's change (both would see "saved", only one would stay). That is why it
runs under the shared lock `LOCK` - Jellyscope is one process.
"""
from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from typing import Any

from .. import db
from .disks import (
    DIFFERENT_KEY,
    DISK_CAPACITY_KEY,
    DISK_NAMES_KEY,
    LOCK,
    SAME_KEY,
    VIRTUAL_KEY,
    _int,
    _json,
    disk_paths,
    is_inside,
    physical_disks,
    suspicions,
    virtual_disks,
)


def _save(key: str, value: Any) -> None:
    db.set_setting(key, json.dumps(value, ensure_ascii=False))


def _kept(stored: Any, capacity: int, keep_within: int) -> int:
    """The stored capacity when the new one differs only by the rounding of the form (`keep_within` bytes).

    The disk's window shows a capacity rounded to four places: saving the window for a new name would otherwise
    store the rounded number - a new capacity and a new fingerprint, and the pace would start over.
    """
    stored = _int(stored)
    return stored if stored > 0 and abs(stored - int(capacity)) <= keep_within else int(capacity)


def set_capacity(disk: str, source: str, capacity: int, keep_within: int = 0) -> str:
    """The source of a physical disk's capacity. Returns an error to show, or "".

    "manual" stores the capacity (in bytes), "jellyfin" goes back to what Jellyfin reports; another word (a tab
    left open across the update to 1.9.0 sends the old one) changes nothing. A manual capacity within
    `keep_within` bytes of the stored one keeps the stored one (see _kept).
    """
    found = next((d for d in physical_disks() if d["key"] == disk), None)
    if found is None:
        return "No such disk here."
    if source not in ("manual", "jellyfin"):
        return "This page is from an older version of the app. Reload it (F5) and try again - nothing was saved."
    if source == "manual" and capacity <= 0:
        return "Enter a capacity greater than zero."
    with LOCK:
        capacities = _json(DISK_CAPACITY_KEY, {})
        stored = next((setting.get("capacity") for device in [disk, *found["device"]]
                       if isinstance(setting := capacities.get(device), dict) and setting.get("source") == "manual"),
                      0)
        capacity = _kept(stored, capacity, keep_within)
        # Away with everything that belongs to the disk - the capacity stored at a device that joined it by a
        # merge too.
        for device in [disk, *found["device"]]:
            capacities.pop(device, None)
        if source == "manual":
            capacities[disk] = {"source": "manual", "capacity": int(capacity)}
        _save(DISK_CAPACITY_KEY, capacities)
    return ""


def set_disk_name(disk: str, name: str) -> str:
    """The name of a physical disk; empty = the default name after its libraries. Returns an error to show, or "".

    At most 60 characters, like a virtual disk's name. A name stored at a device that joined the disk by a merge
    goes too, so that the disk has one name.
    """
    found = next((d for d in physical_disks() if d["key"] == disk), None)
    if found is None:
        return "No such disk here."
    name = str(name or "").strip()[:60]
    with LOCK:
        names = _json(DISK_NAMES_KEY, {})
        for device in [disk, *found["device"]]:
            names.pop(device, None)
        if name:
            names[disk] = name
        _save(DISK_NAMES_KEY, names)
    return ""


def _check_virtual(name: str, paths: str | Sequence[str], capacity: int,
                   except_id: str = "") -> tuple[str, dict[str, Any], str, list[str]]:
    """The check of a virtual disk's values - the same when adding and when editing.

    Returns (error, values for the message, name, paths); an empty error = in order. Paths see disk_paths():
    empty fields and repeats are dropped, at least one must stay. Every path must lead into a library folder
    Jellyfin reports: lie in it, or contain it (`/media/cloud` above `/media/cloud/filmy` and
    `/media/cloud/serialy`). Otherwise no file would lead to it. And none may overlap with a path of
    **another** virtual disk - one file belongs to one disk. Paths of the same disk may overlap (the file belongs
    to it all the same). `except_id` is the id of the disk being edited. Called under the lock, so that nothing
    changes between the check and the save.
    """
    name, path_list = str(name or "").strip()[:60], disk_paths(paths)
    if not name:
        return "Enter a name for the disk.", {}, name, path_list
    if not path_list:
        return "Enter at least one path.", {}, name, path_list
    if capacity <= 0:
        return "Enter a capacity greater than zero.", {}, name, path_list
    folders = [folder for disk in physical_disks() for folder in disk["folders"]]
    others = [other for other in virtual_disks() if other["id"] != except_id]
    for path in path_list:
        if not any(is_inside(path, folder) or is_inside(folder, path) for folder in folders):
            return ("The path {path} does not lead to any library folder Jellyfin reports.",
                    {"path": path}, name, path_list)
        for other in others:
            if any(is_inside(path, its) or is_inside(its, path) for its in other["paths"]):
                return "The path overlaps the virtual disk “{name}”.", {"name": other["name"]}, name, path_list
    return "", {}, name, path_list


def add_virtual(name: str, paths: str | Sequence[str], capacity: int) -> tuple[str, dict[str, Any]]:
    """Add a virtual disk. Returns (error, values for the message); an empty error = added.

    The rules for the values see _check_virtual().
    """
    with LOCK:
        error, values, name, path_list = _check_virtual(name, paths, capacity)
        if error:
            return error, values
        disks = _json(VIRTUAL_KEY, [])
        disks.append({"id": secrets.token_hex(4), "name": name, "paths": path_list, "capacity": int(capacity)})
        _save(VIRTUAL_KEY, disks)
    return "", {}


def edit_virtual(disk_id: str, name: str, paths: str | Sequence[str],
                 capacity: int, keep_within: int = 0) -> tuple[str, dict[str, Any]]:
    """Change the name, path and capacity of a virtual disk. Returns (error, values for the message).

    The id stays, so the disk's history of snapshots goes on. When the path changes, the fingerprint of the
    disk's settings changes too and the pace is computed again from today (the numbers before and after the
    change cannot be compared). The rules for the values as when adding, see _check_virtual(). A capacity
    within `keep_within` bytes of the stored one keeps the stored one (see _kept).
    """
    with LOCK:
        disks = _json(VIRTUAL_KEY, [])
        edited = next((disk for disk in disks
                       if isinstance(disk, dict) and str(disk.get("id")) == str(disk_id)), None)
        if edited is None:
            return "No such disk here.", {}
        capacity = _kept(edited.get("capacity"), capacity, keep_within)
        error, values, name, path_list = _check_virtual(name, paths, capacity, except_id=str(disk_id))
        if error:
            return error, values
        # The older shape with one path goes: from now on only the list holds.
        edited.pop("path", None)
        edited.update({"name": name, "paths": path_list, "capacity": int(capacity)})
        _save(VIRTUAL_KEY, disks)
    return "", {}


def delete_virtual(disk_id: str) -> bool:
    """Delete a virtual disk. Its old snapshots stay in the history."""
    with LOCK:
        disks = _json(VIRTUAL_KEY, [])
        left = [disk for disk in disks if not (isinstance(disk, dict) and str(disk.get("id")) == str(disk_id))]
        if len(left) == len(disks):
            return False
        _save(VIRTUAL_KEY, left)
    return True


def decide_same(a: str, b: str, same: bool) -> bool:
    """The admin's answer to a suspicion: one disk (True), or two different ones.

    Only for a pair that really is under suspicion - otherwise any two disks could be glued together through
    the address.
    """
    if (a, b) not in suspicions(physical_disks()) and (b, a) not in suspicions(physical_disks()):
        return False
    key = SAME_KEY if same else DIFFERENT_KEY
    with LOCK:
        _save(key, [*_json(key, []), sorted([a, b])])
    return True


def undo_decision(disk: str) -> bool:
    """Take back the answers "the same disk" and "different disks" that concern the disk.

    For a mistaken click, and for the case where a volume moves to another disk and keeps the same path in the
    container (and so the same device id). A merged disk falls apart into devices again and the suspicion shows
    again if the numbers still fit. Returns whether there was anything to take back.
    """
    found = next((d for d in physical_disks() if d["key"] == disk), None)
    if found is None:
        return False
    its = {disk, *found["device"]}
    with LOCK:
        same = _json(SAME_KEY, [])
        different = _json(DIFFERENT_KEY, [])
        same_left = [s for s in same if not (isinstance(s, list) and its & {str(x) for x in s})]
        different_left = [r for r in different if not (isinstance(r, list) and its & {str(x) for x in r})]
        if len(same_left) == len(same) and len(different_left) == len(different):
            return False
        _save(SAME_KEY, same_left)
        _save(DIFFERENT_KEY, different_left)
    return True
