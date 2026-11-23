"""The routes of the Settings page - all fifty-two.

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
attaches (web/__init__.py), and exposes every name
(`web_settings._update_page`, `web_settings.MAX_UPLOAD_MB`…):

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
    COMMON_ZONES,
    MAX_UPLOAD_MB,
    _DB_DRAFT,
    _DRAFT_TTL_SECONDS,
    _capacity_for_form,
    _draft_clear,
    _draft_read,
    _draft_save,
    _generation_mismatch,
    _import_from_file,
    _load_backup,
    _save_secret,
    _tidy_counts,
    _weekdays,
    _zone_exists,
)
from .page import (
    settings_page,
    settings_save,
)
from .accounts import (
    KEY_OPTIONS,
    _key_permissions_from_form,
    _key_tree,
    _root_key_permissions,
    _save_tree,
    account_create,
    account_delete,
    account_password,
    account_role,
    api_token_disable,
    api_token_enable,
    api_token_new,
    api_token_permissions,
    api_token_revoke,
    blocks_permanent,
    blocks_unblock,
    settings_account_permissions,
    settings_keys,
    settings_readers,
    settings_viewers,
)
from .connection import (
    settings_connection,
    settings_database,
)
from .notifications import (
    settings_notification_events,
    settings_notification_test,
    settings_notification_texts,
    settings_notifications,
)
from .interface import (
    settings_interface,
    settings_language,
    settings_updates,
)
from .updates import (
    _schedule_restart,
    _update_page,
    backup_delete,
    backup_download,
    backup_restore,
    settings_restart,
    settings_update,
)
from .tasks import (
    log_download,
    settings_scan,
    settings_stop,
    settings_sync,
    tasks_run,
    tasks_save,
)
from .history import (
    history_assign,
    history_forget,
    history_orphans,
    history_restore,
    history_tidy,
    import_detect,
    import_jellystat,
    import_playback_reporting,
    import_playback_reporting_file,
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
    "COMMON_ZONES",
    "JellyfinClient",
    "KEY_OPTIONS",
    "MAX_UPLOAD_MB",
    "_DB_DRAFT",
    "_DRAFT_TTL_SECONDS",
    "_capacity_for_form",
    "_draft_clear",
    "_draft_read",
    "_draft_save",
    "_generation_mismatch",
    "_import_from_file",
    "_key_permissions_from_form",
    "_key_tree",
    "_load_backup",
    "_root_key_permissions",
    "_save_secret",
    "_save_tree",
    "_schedule_restart",
    "_tidy_counts",
    "_update_page",
    "_weekdays",
    "_zone_exists",
    "account_create",
    "account_delete",
    "account_password",
    "account_role",
    "api_token_disable",
    "api_token_enable",
    "api_token_new",
    "api_token_permissions",
    "api_token_revoke",
    "backup_delete",
    "backup_download",
    "backup_restore",
    "blocks_permanent",
    "blocks_unblock",
    "history_assign",
    "history_forget",
    "history_orphans",
    "history_restore",
    "history_tidy",
    "import_detect",
    "import_jellystat",
    "import_playback_reporting",
    "import_playback_reporting_file",
    "log_download",
    "router",
    "settings_account_permissions",
    "settings_connection",
    "settings_database",
    "settings_interface",
    "settings_keys",
    "settings_language",
    "settings_notification_events",
    "settings_notification_test",
    "settings_notification_texts",
    "settings_notifications",
    "settings_page",
    "settings_readers",
    "settings_restart",
    "settings_save",
    "settings_scan",
    "settings_stop",
    "settings_sync",
    "settings_update",
    "settings_updates",
    "settings_viewers",
    "tasks_run",
    "tasks_save",
]
