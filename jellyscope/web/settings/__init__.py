"""The routes of the Settings page - all of them.

They live apart because they are nearly a third of the whole web and have nothing
in common with the other pages. Whoever looks for why something was not saved is to
look here, not among the charts.

`APIRouter` is used, not `@app.post(...)`. So the module need not know about `app`
at all - `web/__init__.py` only picks the router up and attaches it. Without it the two
modules would have to import each other.

What every page needs (templates, context, flash messages, the sign-in guards)
is in `base.py`.

A subpackage by areas (jellyscope/web/settings/). Every file with routes
has its own router; this file joins them into one `router` the web
attaches (web/__init__.py), and exposes the names code and tests outside read
(`web_settings._update_page`, `web_settings.MAX_UPLOAD_MB`…) - a name only the subpackage
uses stays in its file (both ways guarded by tests/test_settings_package.py, check a):

* `common` - the shared helpers of Settings: the database draft, the upload limit, time zones, odds and ends.
* `page` - the Settings page (all sections) and saving the general form.
* `accounts` - accounts, permissions, readers, viewers, address blocks and API keys.
* `connection` - the connection to Jellyfin and to the database.
* `notifications` - notifications: channels, events, texts, the test message.
* `interface` - the interface, checking for updates and the language.
* `updates` - the update, the restart and backups (download, delete, restore).
* `tasks` - synchronisation, stopping, the technical scan, scheduled tasks and the log.
* `history` - importing history, tidying data, orphans, forgetting a viewer.
* `arr` - the *Arr apps (Sonarr, Radarr…): saving, the connection test, synchronisation, deleting data.
* `seerr` - Seerr (requests): saving, the connection test, synchronisation, deleting data.

Tests that substitute a name and expect the effect inside the subpackage use
substitute() of tests/_package.py (tests/test_settings_package.py, check e).
"""
from __future__ import annotations

from fastapi import APIRouter

from ...jellyfin import JellyfinClient  # noqa: F401 - tests substitute it as web_settings.JellyfinClient
from .common import (
    MAX_UPLOAD_MB,
    _draft_clear,
    _draft_read,
    _draft_save,
    _generation_mismatch,
    _load_backup,
)
from .updates import (
    _schedule_restart,
    _update_page,
)
from . import (accounts, arr, connection, history, interface, notifications, page, seerr, tasks, updates)

# One router for the whole Settings - web/__init__.py attaches it. The order does
# not matter (no two routes have the same address with the same method).
router = APIRouter()
router.include_router(page.router)
router.include_router(tasks.router)
router.include_router(accounts.router)
router.include_router(connection.router)
router.include_router(notifications.router)
router.include_router(interface.router)
router.include_router(updates.router)
router.include_router(history.router)
router.include_router(arr.router)
router.include_router(seerr.router)

__all__ = [
    "JellyfinClient",
    "MAX_UPLOAD_MB",
    "_draft_clear",
    "_draft_read",
    "_draft_save",
    "_generation_mismatch",
    "_load_backup",
    "_schedule_restart",
    "_update_page",
    "router",
]
