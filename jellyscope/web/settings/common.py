"""The shared helpers of Settings: the database draft, the upload limit, time zones, odds and ends.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

import time
from fastapi import Request, UploadFile
from fastapi.responses import RedirectResponse
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ... import db, importers, jellyfin, scanner
from ...i18n import translate as _t
from ..base import BACK_TO_IMPORT, CAPACITY_UNITS, _clamp, _flash, _import_message


# The draft of the database settings.
#
# The buttons "Test connection" and "Přenést data" save nothing - after them the
# page loads again and the form would go back to what is saved. So you fill in
# the server, the port, the user and the password, test the connection, it says "works"
# - and the form is empty again and switched to SQLite. Hence the values being
# entered are held for a while.
#
# Why in memory and not in the session: the session here is a **signed cookie**, that is
# something the browser carries along and that can be read. A database password does
# not belong there. Here it stays in the process's memory and is gone after a restart.
_DB_DRAFT: dict[tuple[int, str], tuple[float, Any]] = {}

# The limit of an uploaded file when importing history.
#
# It has to agree with the limit of the reverse proxy - that refuses before the app
# does and the message then comes from nginx, not from us. The configuration examples
# in the deploy/ folder have the same value; test_deploy.py guards it.
#
# 200 MB leaves a reserve: a file this size is over a million rows of
# history, which no household collects. And all of it is read into memory,
# so a higher limit would do more harm than good on a small server.
MAX_UPLOAD_MB = 200


_DRAFT_TTL_SECONDS = 900.0   # a quarter of an hour


# The offer of time zones. The whole list has over six hundred items, so only
# those people really use are offered - any can be typed in.
COMMON_ZONES = (
    "Europe/Prague", "Europe/Bratislava", "Europe/Vienna", "Europe/Berlin",
    "Europe/Warsaw", "Europe/London", "Europe/Madrid", "Europe/Rome",
    "Europe/Kyiv", "Europe/Moscow", "UTC",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Sao_Paulo",
    "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Asia/Dubai",
    "Australia/Sydney", "Pacific/Auckland",
)


async def _load_backup(request: Request, file: UploadFile) -> bytes | None:
    """The content of the uploaded file, or None and a message why it cannot be done.

    Both places a backup is uploaded to (Playback Reporting and Jellystat)
    checked the same the same way: an empty file and the size limit.

    It is read in pieces and **the limit holds while reading**, not only after. The
    file used to be read whole and only then measured - but the limit is here exactly
    because of memory, so it asked only at the moment the memory was spent already.
    A gigabyte file thus brought the app down before it could say it is
    too large.
    """
    limit = MAX_UPLOAD_MB * 1024 * 1024
    raw = bytearray()
    while True:
        piece = await file.read(1024 * 1024)
        if not piece:
            break
        raw.extend(piece)
        if len(raw) > limit:
            _flash(request, "The file is larger than {n} MB.", "error", n=MAX_UPLOAD_MB)
            return None
    if not raw:
        _flash(request, "The file is empty.", "error")
        return None
    return bytes(raw)

def _draft_save(account: dict[str, Any], name: str, value: Any) -> None:
    _DB_DRAFT[(int(account["id"]), name)] = (time.monotonic(), value)


def _draft_read(account: dict[str, Any], name: str) -> Any:
    """The draft, unless it expired already."""
    key = (int(account["id"]), name)
    record = _DB_DRAFT.get(key)
    if record is None:
        return None
    saved_at, value = record
    if time.monotonic() - saved_at > _DRAFT_TTL_SECONDS:
        _DB_DRAFT.pop(key, None)
        return None
    return value


def _draft_clear(account: dict[str, Any], name: str) -> None:
    _DB_DRAFT.pop((int(account["id"]), name), None)


def _zone_exists(name: str) -> bool:
    """Does Python know such a zone? An empty name means "by the system"."""
    if not name:
        return True
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _tidy_counts() -> dict[str, Any]:
    """How many records of the history "Narovnání dat" has something to do with.

    It shows in two places (Tasks and History import) and the same every
    time - hence one function. Otherwise a new count would be added in two
    places and one would be forgotten sooner or later.
    """
    return {
        "duplicate_rows": importers.duplicate_playback_count(),
        "misplaced_rows": len(importers.misplaced_episode_rows()),
        "orphan_rows": importers.orphan_playback_count(),
        "orphan_items": importers.orphan_items_count(),
        "stale_name_rows": importers.stale_name_rows(),
        "import_duplicate_rows": importers.import_duplicate_count(),
        # The archive has a part of the card of its own: tidying straightens it too, and
        # without numbers it looked as if it did not deal with it.
        "archive": scanner.archive_overview(),
    }


async def _import_from_file(request: Request, file: UploadFile,
                            min_seconds: str, run_import: Any,
                            template: str) -> RedirectResponse:
    """An import from an uploaded backup - the procedure shared by both sources.

    Playback Reporting and Jellystat differ only in which function reads the
    data and what is then written into the message. The rest - loading the file, the
    size limit, the minimal length, the way back to Import - is the
    same, and when it was written out twice, it changed in only one place
    every time.
    """
    raw = await _load_backup(request, file)
    if raw is None:
        return RedirectResponse(BACK_TO_IMPORT, status_code=303)

    result = await run_import(raw, min_seconds=int(_clamp(min_seconds, 0, 3600, 60)))
    _import_message(request, result, template)
    return RedirectResponse(BACK_TO_IMPORT, status_code=303)


# Settings is split into sections. It used to be one long page
# nothing could be found on - and on top everything was computed at once at every
# opening, including the row counts of the database and the list of backups on the disk.
#
# Now only what belongs to the open section is loaded.
def _weekdays() -> list[tuple[int, str]]:
    """(number, name) for the day choice - Monday is zero, as in Python."""
    names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    return [(number, _t(name)) for number, name in enumerate(names)]


def _capacity_for_form() -> dict[str, Any]:
    """The capacity entered by hand back into the field - in the unit the person chose."""
    size = db.get_int_setting(scanner.CAPACITY_KEY, 0, 10 ** 18, 0)
    unit = db.get_setting("library_capacity_unit", "GB")
    if unit not in CAPACITY_UNITS:
        # Older settings did not know the unit - with large values
        # terabytes read better.
        unit = "TB" if size >= 1024 ** 4 else "GB"
    return {"capacity_amount": size // CAPACITY_UNITS[unit],
            "capacity_unit_name": unit}


def _generation_mismatch() -> bool:
    """Does the server report another generation than the one chosen by hand?"""
    chosen = db.get_setting(jellyfin.GENERATION_KEY, "auto")
    detected = db.get_setting(jellyfin.VERSION_KEY, "")
    if chosen not in ("12", "10") or not detected:
        return False
    return jellyfin.server_version(detected)[0] != int(chosen)


def _save_secret(key: str, value: str) -> None:
    """Saves a password/token only when something came.

    The saved value is not written into the form (see db.SECRET_SETTINGS),
    so an empty field means "leave as it was" - otherwise every save of the
    other fields would delete the password.
    """
    value = (value or "").strip()
    if value:
        db.set_setting(key, value)
