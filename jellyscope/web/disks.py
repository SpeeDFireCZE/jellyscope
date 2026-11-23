"""The Disks card on the Library page: disk capacity, virtual disks, "the same disk?".

A part of the web package (see jellyscope/web/__init__.py). Only for the administrator - it
changes the server's settings. `scanner` (disks.py) checks and saves, here the
form is only read and the message shown. After every change today's disk
snapshot is rewritten: the forecast "space runs out in" is computed from the
snapshots, and without it a new capacity would show only after the nightly synchronisation.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import scanner
from .base import CAPACITY_UNITS, _flash, log, require_admin

router = APIRouter()

BACK = "/library#disks"
# A million units (TB) at most: more is a typo, not a disk.
MAX_UNITS = 10 ** 6


def _capacity(number: str, unit: str) -> int:
    """The capacity from the form in bytes. 0 = empty, not a number or nonsense."""
    try:
        value = float(str(number or "").replace(",", ".").replace(" ", "").strip())
    except ValueError:
        return 0
    if not 0 < value <= MAX_UNITS:
        return 0
    return int(value * CAPACITY_UNITS.get(unit, CAPACITY_UNITS["GB"]))


def _rounding(number: int, unit: str) -> int:
    """How far a capacity shown in a disk's window may be from the stored one only by its rounding.

    The window shows four decimal places of the unit, in at most six significant digits ("%g") - half of the last
    shown digit either way. Saving the window unchanged must not change the capacity (see _kept in scanner/disk_settings.py).
    """
    return max(CAPACITY_UNITS.get(unit, CAPACITY_UNITS["GB"]) // 20000, int(number) // 100000)


def _back_after_change() -> RedirectResponse:
    """Rewrites today's disk snapshot and returns to the card."""
    try:
        scanner.write_disk_snapshot()
    except Exception:  # noqa: BLE001 - the setting is saved, the snapshot is written at synchronisation
        log.exception("the disk snapshot after a settings change was not written")
    return RedirectResponse(BACK, status_code=303)


@router.post("/library/disks/capacity")
def disks_capacity(request: Request, disk: str = Form(""), source: str = Form("jellyfin"),
                   capacity: str = Form(""), unit: str = Form("GB"), name: str | None = Form(None),
                   name_field: str = Form(""), account: dict[str, Any] = Depends(require_admin)):
    """The disk's window: its name, and the capacity Jellyfin reports or the administrator enters by hand.

    `name_field` is sent by a window that has the Name field: then an empty `name` (FastAPI makes a form's "" its
    default, None) means the default name after the libraries. Without it the form is from a tab opened before
    the field existed, and the name stays.
    """
    number = _capacity(capacity, unit)
    error = scanner.set_capacity(disk, source, number, _rounding(number, unit))
    if not error and name_field:
        error = scanner.set_disk_name(disk, name or "")
    if error:
        _flash(request, error, "error")
        return RedirectResponse(BACK, status_code=303)
    _flash(request, "Disk saved.", "success")
    return _back_after_change()


@router.post("/library/disks/virtual")
def disks_virtual_add(request: Request, name: str = Form(""), path: list[str] = Form([]),
                      capacity: str = Form(""), unit: str = Form("GB"),
                      account: dict[str, Any] = Depends(require_admin)):
    """Adds a virtual disk: the files under the path belong to it, not to the physical disk."""
    error, values = scanner.add_virtual(name, path, _capacity(capacity, unit))
    if error:
        _flash(request, error, "error", **values)
        return RedirectResponse(BACK, status_code=303)
    _flash(request, "Virtual disk “{name}” added.", "success", name=name.strip()[:60])
    return _back_after_change()


@router.post("/library/disks/virtual/delete")
def disks_virtual_delete(request: Request, id: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Deletes a virtual disk. Its files count for the physical disk again."""
    if not scanner.delete_virtual(id):
        _flash(request, "No such disk here.", "error")
        return RedirectResponse(BACK, status_code=303)
    _flash(request, "Virtual disk deleted.", "success")
    return _back_after_change()


@router.post("/library/disks/same")
def disks_same(request: Request, a: str = Form(""), b: str = Form(""), answer: str = Form(""),
               account: dict[str, Any] = Depends(require_admin)):
    """The answer to the suspicion "maybe the same disk": yes = merge, no = do not ask again."""
    if answer not in ("yes", "no") or not scanner.decide_same(a, b, answer == "yes"):
        _flash(request, "These two disks cannot be compared - reload the page.", "error")
        return RedirectResponse(BACK, status_code=303)
    _flash(request, "Disks merged." if answer == "yes" else "Noted: these are different disks.", "success")
    return _back_after_change()


@router.post("/library/disks/decision/undo")
def disks_decision_undo(request: Request, disk: str = Form(""),
                          account: dict[str, Any] = Depends(require_admin)):
    """Takes back the answer "the same disk" / "different disks" - for a mistake and for a moved volume."""
    if not scanner.undo_decision(disk):
        _flash(request, "There is nothing to withdraw for this disk.", "error")
        return RedirectResponse(BACK, status_code=303)
    _flash(request, "Answer withdrawn - disks are told apart by device id again.", "success")
    return _back_after_change()


@router.post("/library/disks/virtual/edit")
def disks_virtual_edit(request: Request, id: str = Form(""), name: str = Form(""),
                       path: list[str] = Form([]),
                       capacity: str = Form(""), unit: str = Form("GB"),
                       account: dict[str, Any] = Depends(require_admin)):
    """Changes the name, the path and the capacity of a virtual disk. The id and the history stay."""
    number = _capacity(capacity, unit)
    error, values = scanner.edit_virtual(id, name, path, number, _rounding(number, unit))
    if error:
        _flash(request, error, "error", **values)
        return RedirectResponse(BACK, status_code=303)
    _flash(request, "Virtual disk “{name}” saved.", "success", name=name.strip()[:60])
    return _back_after_change()
