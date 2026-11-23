"""The Settings page (all sections) and saving the general form.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py). What
each section needs (the template's context) is put together by its helper `_section_…`
right above `settings_page`; the page itself only picks the section and calls the helper."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Any

from ... import (
    access,
    accounts,
    api,
    api_query,
    applog,
    db,
    dbmigrate,
    dialect,
    formatting,
    importers,
    jellyfin,
    navigation,
    purge,
    scanner,
    seerr,
    stats,
    tasks,
)
from ...arr import registry
from ...config import BASE_DIR as PROJECT_DIR
from ...i18n import translate as _t
from ..base import (
    CAPACITY_UNITS,
    SETTINGS_SECTIONS,
    _clamp,
    _context,
    _flash,
    _form_number,
    _new_key,
    _no_cache,
    config,
    require_admin,
    require_login,
    templates,
)
from .accounts import KEY_OPTIONS, _key_tree, _root_key_permissions
from .common import (
    COMMON_ZONES,
    _capacity_for_form,
    _draft_read,
    _generation_mismatch,
    _tidy_counts,
    _weekdays,
)

router = APIRouter()


def _section_jellyfin(account: dict[str, Any]) -> dict[str, Any]:
    """The Jellyfin section: the connection, the server's generation and the viewers' sign-in."""
    context: dict[str, Any] = {}
    # When the user only tested the connection, the settings were not saved -
    # but the form is to hold what was filled in, not the saved value.
    # Otherwise the address would be filled in again after every test.
    draft = _draft_read(account, "jellyfin") or {}
    saved_key = db.get_setting("jellyfin_api_key", "").strip()
    context.update(
        jellyfin_url=draft.get("url") or db.get_setting("jellyfin_url", ""),
        has_api_key=bool(draft.get("api_key") or saved_key),
        # The server's generation: what is chosen and what was last detected.
        generation=db.get_setting(jellyfin.GENERATION_KEY, "auto"),
        detected_version=db.get_setting(jellyfin.VERSION_KEY, ""),
        # The choice by hand against what the server reports. When they disagree,
        # it is no error (that is what the choice is for), but it is to show -
        # otherwise you wonder why the app sends other queries than you expect.
        generation_mismatch=_generation_mismatch(),
        jellyfin_draft=bool(draft),
        last_library_scan=scanner.last_scan("library"),
        scan_running=scanner.is_scan_running(),
        stop_pending=scanner.stop_requested(),
        # Signing viewers in with their Jellyfin password belongs here: it is
        # about Jellyfin and its accounts, not about Jellyscope's accounts.
        # The key and the viewers' passwords go to Jellyfin readable - only
        # https encrypts. When the address starts with http:// and does not lead
        # to this machine, it is to show.
        unencrypted=jellyfin.unencrypted_over_network(
            draft.get("url") or db.get_setting("jellyfin_url", "")),
        viewer_login=access.login_enabled(),
        viewer_tree=access.tree(access.VIEWER),
        viewer_anonymise=access.anonymising_enabled(access.VIEWER),
    )
    return context


def _section_notifications() -> dict[str, Any]:
    """The Notifications section: the channels, the saved secrets (only whether they exist) and the weekdays."""
    context: dict[str, Any] = {}
    from ... import notifications

    # The saved secrets are not written into the page (db.SECRET_SETTINGS),
    # but the form is to know that something IS saved already - otherwise
    # an empty field would look like not set.
    context.update(
        notification_state=notifications.state(),
        has_smtp_password=bool(db.get_setting(
            notifications.setting_key("smtp", "password"), "").strip()),
        has_discord=bool(db.get_setting(
            notifications.setting_key("discord", "webhook"), "").strip()),
        has_telegram=bool(db.get_setting(
            notifications.setting_key("telegram", "token"), "").strip()),
        channel_names={"smtp": _t("e-mail"), "discord": "Discord",
                       "telegram": "Telegram"},
        weekdays=_weekdays(),
    )
    return context


def _section_arr(account: dict[str, Any]) -> dict[str, Any]:
    """The *Arr section: a card of every app of the list (jellyscope/arr/registry.py).

    After a connection test (or a failed save) the form shows what was
    tried, not what is saved - otherwise the filled-in address would be lost.
    The key is never returned, only whether there is one.
    """
    app_cards = []
    connected = registry.connected()
    for app, a in registry.APPS.items():
        saved = a.stored_settings()
        draft = _draft_read(account, f"arr_{app}") or {}
        app_cards.append({
            "app": app,
            "name": a.shown_name(),
            "enabled": draft.get("enabled", saved["enabled"]),
            "url": draft.get("url", saved["url"]),
            "has_key": saved["has_key"] or bool(draft.get("api_key")),
            "connected": app in connected,
            "draft": bool(draft),
            "state": a.sync_state(),
            "count": a.event_count(),
            "example_url": a.sample_url,
            "key_hint_text": a.key_hint,
            "deletion_note": a.delete_note,
        })
    return {"arr_apps": app_cards}


def _section_seerr(account: dict[str, Any]) -> dict[str, Any]:
    """The Seerr section: a card with a form. After a connection test it shows the tried
    values (the draft), the key never - only whether there is one."""
    saved = seerr.stored_settings()
    draft = _draft_read(account, "seerr") or {}
    return {"seerr": {
        "enabled": draft.get("enabled", saved["enabled"]),
        "url": draft.get("url", saved["url"]),
        "has_key": saved["has_key"] or bool(draft.get("api_key")),
        "connected": seerr.is_connected(),
        "draft": bool(draft),
        "state": seerr.sync_state(),
        "count": seerr.request_count(),
        # The row "Request posters": does Seerr hand out images ("1" / "0" / "" not
        # known) and where the link to its settings leads. `public_url` = the link
        # goes to the address Seerr has set for people; otherwise it is the one
        # Jellyscope knows, and it need not open from the browser.
        "images": seerr.images_state(),
        "settings_url": seerr.settings_link(),
        "public_url": bool(db.get_setting(seerr.settings.WEB_KEY, "")),
    }}


def _section_data() -> dict[str, Any]:
    """The Data section: ffprobe and where the free space is taken from."""
    context: dict[str, Any] = {}
    from ... import probe  # only here, so that nothing holds the app's start up
    # The card with the file analysis moved among Tasks - this
    # section only picks the source of the data now, so it needs neither the
    # coverage nor the state of the last run.
    source = db.get_setting(scanner.SOURCE_KEY, "")
    context.update(
        ffprobe_found=probe.find_ffprobe(db.get_setting("ffprobe_path")),
        # The capacity is saved in bytes, but entered in the unit the person
        # chose - for a twenty-terabyte array "30000 GB" is a needlessly
        # long number.
        **_capacity_for_form(),
        # Did Jellyfin break the libraries' disks down? Then the capacity is set
        # at the disk in the Disks card and here is only a link.
        disk_breakdown=bool(scanner.physical_disks()),
        space_source=source,
        source_label={
            "manual": _t("free space is worked out from the capacity you entered"),
            "jellyfin": _t("free space is reported by Jellyfin"),
            "disk": _t("free space is read from the disk the application runs on"),
        }.get(source, ""),
    )
    return context


def _section_tasks() -> dict[str, Any]:
    """The Tasks section: scheduled tasks, backups, the file analysis and purging history."""
    context: dict[str, Any] = {}
    context.update(
        # The "Data tidy-up" card moved here from Import, so the
        # numbers it shows belong here too.
        **_tidy_counts(),
        # When a task finishes while a person is on this section, the page
        # loads by itself - otherwise the strip "a task is running" would stay
        # hanging here and the result would not show. See the watcher in static/js/app.js.
        reload_on_task=True,
        task_list=tasks.all_statuses(),
        logrotate_days=tasks.rotation_days(),
        logrotate_count=tasks.rotation_count(),
        logrotate_days_max=applog.MAX_DAYS,
        logrotate_count_max=applog.MAX_COUNT,
        backups=tasks.list_backups(),
        backup_free=tasks.free_space(db.get_setting("backup_path", "")),
        fallback_backup_folder=tasks.shown_folder(tasks.fallback_backup_folder()),
        # Which pg_dump is on the machine and which server. Without it a
        # version mismatch is looked for only by the error message after
        # a backup fails - see tasks._choose_pg_dump().
        pg_dumps=(tasks.available_pg_dumps() if db.database_config().is_postgres
                  else []),
        pg_server=(tasks.server_version() if db.database_config().is_postgres
                   else 0),
        scan_running=scanner.is_scan_running(),
        stop_pending=scanner.stop_requested(),
        last_library_scan=scanner.last_scan("library"),
        last_tech_scan=scanner.last_scan("tech"),
        coverage=stats.tech_coverage(),
        # Purging history. The numbers are computed even when it is off -
        # by them a person decides whether to switch it on.
        retention_days=purge.retention_days(),
        retention_min=purge.MIN_DAYS,
        retention_max=purge.MAX_DAYS,
        purge=purge.overview(),
        viewers=purge.viewers(),
        compaction_at=tasks.deferred_compaction_time(),
        forgotten=purge.forgettings(),
    )
    return context


def _section_api(request: Request, account: dict[str, Any]) -> dict[str, Any]:
    """The API section: the keys, their permissions, the guide and the key just made."""
    context: dict[str, Any] = {}
    tokens = api.list_tokens()
    context.update(
        api_tokens=tokens,
        # Root keys: what they may (the Type column and the permissions) and the
        # pre-filled Permissions window. A new key starts with nothing ticked - its
        # permissions are set knowingly.
        api_key_permissions={t["id"]: _root_key_permissions(t["permissions"])
                             for t in tokens if t["account_id"] is None},
        api_new_key=_key_tree([], history=False, anonymisation=False, everything=False),
        # The window "How to use it" (_api_guide.html) - the administrator gets
        # all the addresses, but the list is always passed explicitly.
        api_guide_paths=api.allowed_paths(account),
        api_guide_fields=api.path_fields(api.allowed_paths(account)),
        api_guide_operators=api_query.OPERATORS,
        # A newly made key shows only once, right after the
        # redirect; so it is held in the session, not in the address - and at
        # the next load of the page nobody can write it out any more. It shows
        # in the row of its key (the macro bunka_klice).
        new_token=_new_key(request, "new_api_token"),
    )
    return context


def _section_blocks() -> dict[str, Any]:
    """The Blocks section: the blocked addresses and the blocking rules."""
    context: dict[str, Any] = {}
    context.update(
        blocks=accounts.list_blocks(),
        block_levels=accounts.BLOCK_STEPS,
        block_attempts=accounts.ATTEMPTS_BEFORE_BLOCK,
        block_forget_hours=accounts.FORGET_AFTER_HOURS,
    )
    return context


def _section_import() -> dict[str, Any]:
    """The Import section: the summary of the imported history and the counts for tidying."""
    context: dict[str, Any] = {}
    context.update(
        import_stats=importers.import_summary(),
        last_import=scanner.last_scan("import"),
        # How many duplicate and wrongly assigned records the history holds.
        # Shown in advance, so that it is clear whether the cleanup makes sense at all.
        **_tidy_counts(),
    )
    return context


def _section_database(account: dict[str, Any]) -> dict[str, Any]:
    """The Database section: the saved and the running configuration, the draft form."""
    context: dict[str, Any] = {}
    # Two different configurations, easy to mix up:
    #
    #   saved    = what is in data/database.json, that is what is used
    #              after a restart. This belongs in the form.
    #   running  = what the app uses now. A change of the database takes effect
    #              only by a restart, so until the restart these two differ.
    #
    # The running configuration used to go into the form - and since that one
    # is cached in memory, after saving PostgreSQL the form
    # switched back to SQLite. It looked as if the save had failed,
    # though the file was written right.
    saved = dialect.load_config(PROJECT_DIR, str(config.database_path))
    running = db.database_config()
    # When the user has a draft (tested the connection, but did not save
    # yet), the form shows that - otherwise the filled-in values would be
    # lost at every test.
    draft = _draft_read(account, "database")
    context.update(
        database=draft or saved,
        draft_pending=draft is not None,
        running_database=running,
        restart_pending=saved.to_dict() != running.to_dict(),
        database_counts=dbmigrate.summarise(running),
        # The path to the python the app runs with right now. So the guide to
        # installing psycopg shows a command that can be
        # copied - not a general "pip install", which in the case of a
        # virtual environment would install somewhere else.
        python_path=sys.executable,
        psycopg_available=db.psycopg_available(),
        pool_available=db.pool_available(),
    )
    return context


def _section_accounts() -> dict[str, Any]:
    """The Accounts section: the permissions of the groups and of every account without the administrator's rights."""
    context: dict[str, Any] = {}
    context.update(
        reader_tree=access.tree(access.READER),
        reader_anonymise=access.anonymising_enabled(access.READER),
        reader_may_have_key=access.group_may_have_key(access.READER),
        viewer_may_have_key=access.group_may_have_key(access.VIEWER),
    )
    everyone = accounts.all_accounts()
    context.update(
        all_accounts=everyone,
        # How many administrators there are - by that the Delete button is not
        # shown at the last of them. See accounts.delete().
        admin_count=accounts.admin_count(),
        local_admin_count=accounts.local_admin_count(),
        # The own permissions of every account without the administrator's rights: the
        # tree as it holds for it (its own, or inherited from the group), and
        # whether it is its own. An administrator has no tree - sees everything.
        account_permissions={
            account["id"]: {
                "own": access.own_permissions(account) is not None,
                "group": access.role(account),
                "tree": access.tree(access.role(account), account),
                "anonymizace": access.anonymising_enabled(
                    access.role(account), account),
                # The exception for an API key of one's own: group / yes / no.
                # It is apart from the tree and holds in both modes. The choice
                # is read back from KEY_OPTIONS, so that both sides are one.
                "may_have_key": {value: option for option, value
                                 in KEY_OPTIONS.items()}.get(
                    account.get("may_have_key"), "group"),
                "may_have_key_group": access.group_may_have_key(access.role(account)),
            }
            for account in everyone if not account.get("is_admin")
        },
    )
    return context


def _section_log(log_file: str, log_lines: int, log_level: str, log_kind: str,
                 log_account: str, log_key: str, log_ip: str) -> dict[str, Any]:
    """The Log section: the app's log, or the API log with the filters from the address."""
    context: dict[str, Any] = {}
    # Never trust input from the address - not even your own link. The file's
    # name is checked by applog itself (see _safe_path), here the
    # number of lines, the level and the filters are enough.
    lines = log_lines if log_lines > 0 else applog.DEFAULT_LINES
    context.update(log_kind="api" if log_kind == "api" else "app", log_lines=lines)
    if context["log_kind"] == "api":
        keys = api.list_tokens()
        key_filter = _form_number(log_key) or ""
        context.update(
            api_log=applog.read_api(lines, log_account.strip(), key_filter,
                                    log_ip.strip()[:64]),
            # The offer of filters: the keys that exist, and their owners
            # ("-" = a root key). A deleted key stays in the log, it only cannot
            # be chosen by.
            api_log_keys=keys,
            api_log_owners=sorted({k["owner"] or "-" for k in keys}),
            log_account=log_account.strip(), log_key=key_filter,
            log_ip=log_ip.strip()[:64],
            log_backups_list=applog.backups(applog.API_NAME),
        )
    else:
        level = log_level if log_level in applog.levels() else ""
        listing = applog.read_lines(log_file, lines, level)
        context.update(
            log=listing,
            log_level=level,
            log_levels=applog.levels(),
            log_backups_list=applog.backups(listing["name"]),
        )
    return context


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    section: str = "jellyfin",
    # The choices of the log viewer. They are in the address, not in the settings: it is
    # a view, not a choice of the server - two administrators can each look at something else.
    log_file: str = "",
    log_lines: int = 0,
    log_level: str = "",
    # Which log: the app's ("app"), or the API's. The API log's filters: the key's
    # owner, the key and the IP. Text, not a number - nonsense in the address is to
    # give the whole log, not a 422.
    log_kind: str = "",
    log_account: str = "",
    log_key: str = "",
    log_ip: str = "",
    account: dict[str, Any] = Depends(require_login),
):
    allowed = {key for key, _name, admin_only in SETTINGS_SECTIONS
               if account["is_admin"] or not admin_only}
    if section not in allowed:
        # A reader has only one section - the one where the password is changed.
        section = "jellyfin" if account["is_admin"] else "accounts"

    # The template gets the settings WITHOUT secrets - an API key must not
    # get into the page, not even by mistake.
    # The offer of sections is drawn TWICE - as tabs on a desktop and as
    # a drop-down list on a phone - and so it is put together here, once.
    # Two lists written by hand would drift in time and a new
    # section would be missing in one.
    context: dict[str, Any] = {
        "section": section,
        "settings": db.get_public_settings(),
        "section_menu": [(key, _t(name)) for key, name, admin_only
                         in SETTINGS_SECTIONS
                         if account["is_admin"] or not admin_only],
    }

    if section == "jellyfin":
        context.update(_section_jellyfin(account))
    elif section == "notifications":
        context.update(_section_notifications())
    elif section == "arr":
        context.update(_section_arr(account))
    elif section == "seerr":
        context.update(_section_seerr(account))
    elif section == "data":
        context.update(_section_data())
    elif section == "tasks":
        context.update(_section_tasks())
    elif section == "api":
        context.update(_section_api(request, account))
    elif section == "blocks":
        context.update(_section_blocks())
    elif section == "import":
        context.update(_section_import())
    elif section == "database":
        context.update(_section_database(account))
    elif section == "accounts":
        context.update(_section_accounts())
    elif section == "interface":
        # The default page: what is on offer now and what opens instead of an unavailable choice.
        context["default_page"] = navigation.default_page_field(account)
    elif section == "log":
        context.update(_section_log(log_file, log_lines, log_level, log_kind,
                                    log_account, log_key, log_ip))

    # The offer of zones and the current time in the chosen one - so that after a save it
    # shows that something really changed. Without it a person would look at a field
    # with text and have to guess whether the zone holds at all.
    context.setdefault("time_zones", COMMON_ZONES)
    context.setdefault("zone_now",
                       datetime.now(formatting.zone()).strftime("%d.%m.%Y %H:%M"))

    response = templates.TemplateResponse(
        request, "settings.html", _context(request, account, **context)
    )
    if context.get("new_token"):
        _no_cache(response)
    return response


@router.post("/settings")
def settings_save(
    request: Request,
    tech_source: str = Form("jellyfin"),
    poll_interval: str = Form("10"),
    # The capacity field is in the form only with a Jellyfin without the disk breakdown -
    # otherwise the capacity is set at the disk in the Disks card. So a missing field
    # must not overwrite the capacity with zero.
    library_capacity_gb: str | None = Form(None),
    library_capacity_unit: str = Form("GB"),
    ffprobe_path: str = Form(""),
    ffprobe_concurrency: str = Form("3"),
    path_mappings: str = Form("[]"),
    account: dict[str, Any] = Depends(require_admin),
):
    # Input from a form is never saved without a check. A user can
    # (by mistake or on purpose) send anything.
    if tech_source not in ("jellyfin", "ffprobe"):
        tech_source = "jellyfin"

    db.set_setting("tech_source", tech_source)
    db.set_setting("poll_interval", _clamp(poll_interval, 2, 300, 10))
    # The time of the library synchronisation is not written here any more - it belongs
    # to the scheduled tasks and is changed in a form of its own. Were both to save it,
    # they would overwrite each other's value.
    # The capacity is entered in GB or TB. Zero means "find out yourself".
    # With the disk breakdown the capacity is set at the disk. A form opened
    # before the first synchronisation has the field - and saved after it would
    # revive the old capacity the conversion has just moved to the disk.
    if library_capacity_gb is not None and not scanner.physical_disks():
        unit = library_capacity_unit if library_capacity_unit in CAPACITY_UNITS else "GB"
        db.set_setting("library_capacity_unit", unit)
        db.set_setting(scanner.CAPACITY_KEY,
                       str(int(_clamp(library_capacity_gb, 0, 10 ** 6, 0))
                           * CAPACITY_UNITS[unit]))
    db.set_setting("ffprobe_concurrency", _clamp(ffprobe_concurrency, 1, 16, 3))
    db.set_setting("ffprobe_path", ffprobe_path.strip())

    try:
        parsed = json.loads(path_mappings or "[]")
        if not isinstance(parsed, list):
            raise ValueError
        db.set_setting("path_mappings", json.dumps(parsed))
    except ValueError:
        _flash(request, "The path mapping is not valid JSON, so I kept the original value.", "warning")
        return RedirectResponse("/settings?section=data", status_code=303)

    _flash(request, "Settings saved.", "success")
    return RedirectResponse("/settings?section=data", status_code=303)
