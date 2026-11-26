"""The interface, checking for updates and the language.

A part of the subpackage web/settings (see jellyscope/web/settings/__init__.py)."""
from __future__ import annotations

import os
import time
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from typing import Any

from ... import cards, db, i18n, navigation, updates
from ..base import (
    LIMIT_MAX,
    LIMIT_MIN,
    MAP_BASEMAPS,
    SELF_REFRESH_KEY,
    SKINS,
    TABLE_LIMITS,
    ZOOM_MODES,
    _clamp,
    _flash,
    require_admin,
)
from .common import _zone_exists

router = APIRouter()


@router.post("/settings/interface")
def settings_interface(request: Request,
                       ui_max_streams: str = Form(""),
                       ui_max_streams_mobile: str = Form(""),
                       ui_max_viewers: str = Form(""),
                       ui_max_viewers_mobile: str = Form(""),
                       ui_max_rows: str = Form(""),
                       ui_max_rows_mobile: str = Form(""),
                       ui_rows_arr: str = Form(""),
                       ui_max_rows_arr: str = Form(""),
                       ui_max_rows_arr_mobile: str = Form(""),
                       ui_rows_seerr: str = Form(""),
                       ui_max_rows_seerr: str = Form(""),
                       ui_max_rows_seerr_mobile: str = Form(""),
                       ui_rows_music: str = Form(""),
                       ui_max_rows_music: str = Form(""),
                       ui_max_rows_music_mobile: str = Form(""),
                       ui_map_zoom: str = Form("click"),
                       ui_map_base: str = Form("drawn"),
                       ui_skin: str = Form("new"),
                       ui_exact_time: str = Form("0"),
                       ui_dashboard: str = Form("0"),
                       dashboard_name: str = Form(""),
                       ui_events: str = Form("1"),
                       ui_self_refresh: str = Form("1"),
                       ui_default_page: str = Form(""),
                       account: dict[str, Any] = Depends(require_admin)):
    """Settings → Interface: the limits of long lists, the map, the skin, the dashboard for everyone.

    The limits are cut to their bounds at saving already (`_clamp`) and to be safe
    again at reading (`_limits`): the form then shows exactly the number that
    holds. An empty or nonsense field means the default.

    The limits of the *Arr, Seerr and music tables are in the form only with a connected
    app (music only with music), and when they are not in it, saving anything else must not put them
    back to the default. But an empty field cannot be told from a missing one (FastAPI
    takes an empty form value as not given), so a form with the fields
    sends the marker `ui_rows_arr` / `ui_rows_seerr` / `ui_rows_music` too: without it the counts
    are left alone.
    """
    db.set_setting("ui_max_streams", str(_clamp(ui_max_streams, LIMIT_MIN,
                                                LIMIT_MAX, 10)))
    db.set_setting("ui_max_streams_mobile",
                   _clamp(ui_max_streams_mobile, LIMIT_MIN,
                          LIMIT_MAX, 3))
    db.set_setting("ui_max_viewers", str(_clamp(ui_max_viewers, LIMIT_MIN,
                                                LIMIT_MAX, 10)))
    db.set_setting("ui_max_viewers_mobile",
                   _clamp(ui_max_viewers_mobile, LIMIT_MIN, LIMIT_MAX, 5))
    db.set_setting("ui_max_rows", _clamp(ui_max_rows, LIMIT_MIN, LIMIT_MAX, 10))
    db.set_setting("ui_max_rows_mobile",
                   _clamp(ui_max_rows_mobile, LIMIT_MIN, LIMIT_MAX, 5))
    # Row counts of their own for *Arr, Seerr and music; the keys and the defaults are in web.TABLE_LIMITS.
    sent = {"arr": (ui_rows_arr, ui_max_rows_arr, ui_max_rows_arr_mobile),
            "seerr": (ui_rows_seerr, ui_max_rows_seerr, ui_max_rows_seerr_mobile),
            "music": (ui_rows_music, ui_max_rows_music, ui_max_rows_music_mobile)}
    for where, (in_form, everywhere, mobile) in sent.items():
        if in_form != "1":
            continue
        key, default, default_mobile = TABLE_LIMITS[where]
        db.set_setting(key, _clamp(everywhere, LIMIT_MIN, LIMIT_MAX, default))
        db.set_setting(key + "_mobile", _clamp(mobile, LIMIT_MIN, LIMIT_MAX, default_mobile))
    # Anything outside the known modes is a typo or a forged form -
    # in both cases the right answer is the default, not saving it.
    db.set_setting("ui_map_zoom",
                   ui_map_zoom if ui_map_zoom in ZOOM_MODES else "click")
    # The OSM basemap sends the viewers' browsers to a foreign server - only when
    # it is explicitly "osm", anything else is the drawn map.
    db.set_setting("ui_map_base",
                   ui_map_base if ui_map_base in MAP_BASEMAPS else "drawn")
    db.set_setting("ui_skin", ui_skin if ui_skin in SKINS else "new")
    db.set_setting("ui_exact_time", "1" if ui_exact_time == "1" else "0")
    # Seasonal decoration: only an explicit "0" switches it off. A form without the field
    # (another card, an older page) leaves it on - the default is on.
    db.set_setting("ui_events", "0" if ui_events == "0" else "1")
    # Cards that refresh by themselves: the same - only an explicit "0" switches them off.
    db.set_setting(SELF_REFRESH_KEY, "0" if ui_self_refresh == "0" else "1")
    # The dashboard for everyone has a name that goes into the menu. It is checked whenever
    # somebody sends it or switches the dashboard on; an invalid one is not saved and the
    # previous one holds. Without a valid name the dashboard is not switched on - there
    # would be nothing to write in the menu. The other settings are saved all the same.
    wants_dashboard = ui_dashboard == "1"
    name_error = None
    if wants_dashboard or dashboard_name.strip():
        try:
            db.set_setting(cards.NAME_KEY, cards.check_dashboard_name(dashboard_name))
        except cards.InvalidDashboardName as error:
            name_error = error
    # Switching on with an invalid name is not possible, even though a dashboard that is
    # off remembers the old one: the message says "cannot" and the dashboard is not to come
    # on silently under a name the administrator has just rewritten. One that is on stays on under the old name.
    if wants_dashboard and (not cards.dashboard_name()
                            or (name_error is not None and not cards.is_enabled())):
        wants_dashboard = False
    switching_on = wants_dashboard and not cards.is_enabled()
    db.set_setting(cards.ENABLED_KEY, "1" if wants_dashboard else "0")
    # The default page: "Automatic" clears the choice (the way of installations from before),
    # otherwise only a choice on offer now (a section with content, the custom dashboard while
    # it is on - so after the switch above). Anything else - a form without the field, an
    # unavailable or forged value - leaves the saved choice alone.
    if ui_default_page == navigation.AUTO:
        db.set_setting(navigation.DEFAULT_PAGE_KEY, "")
    elif ui_default_page in dict(navigation.default_page_options(account)):
        db.set_setting(navigation.DEFAULT_PAGE_KEY, ui_default_page)
    if name_error is not None:
        _flash(request, name_error.template, "error", **name_error.values)
        return RedirectResponse("/settings?section=interface", status_code=303)
    _flash(request, "Saved.", "success")

    # Whoever has just switched the dashboard on goes straight to building it - switching on
    # and building are two steps of the same thing, not two different places to look for.
    if switching_on and not cards.load_layout():
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/settings?section=interface", status_code=303)


@router.post("/settings/updates")
async def settings_updates(request: Request,
                           account: dict[str, Any] = Depends(require_admin)):
    """Checking for a new version by hand.

    Whether to ask regularly and when is set in Tasks like
    everything else that runs by itself - see the task "Check for updates".
    Only a one-off click is left here, which asks regardless of
    whether the task is on: that is an action, not a schedule.
    """
    result = await updates.check(forced=True)
    new_release = bool(result.get("is_newer"))
    translations = int(result.get("new_translations") or 0)
    # Four answers, each different - a person is to know from the message what is
    # ahead before opening anything. "Something is new" without details would send
    # them searching.
    if result.get("status") == "error":
        _flash(request, "The check failed: {reason}", "error",
               reason=result.get("message", "?"))
    elif new_release and translations:
        _flash(request,
               "Version {version} is available, and new translations "
               "with it ({n} files). Both come down in one go.", "success",
               version=result.get("found_one", "?"), n=translations)
    elif new_release:
        _flash(request, "Version {version} is available.", "success",
               version=result.get("found_one", "?"))
    elif translations:
        _flash(request,
               "The release is current, but new translations have landed on "
               "GitHub ({n} files).", "success", n=translations)
    else:
        _flash(request, "You have the latest version and the latest translations.", "success")

    return RedirectResponse("/settings?section=general#version", status_code=303)


@router.post("/settings/language")
def settings_language(
    request: Request,
    ui_language: str = Form(i18n.DEFAULT_LANGUAGE),
    app_timezone: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    if ui_language not in i18n.LANGUAGES:
        ui_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("ui_language", ui_language)

    # The zone is saved only when Python knows it. A typo would otherwise make
    # times be written by the system, and nobody would know why.
    zone = app_timezone.strip()
    if not zone:
        db.set_setting("app_timezone", "")
    elif _zone_exists(zone):
        db.set_setting("app_timezone", zone)
        # The process environment is rewritten right away - writing out times is
        # straight at once. The split into days in SQL only after a restart, see lifespan().
        os.environ["TZ"] = zone
        if hasattr(time, "tzset"):
            time.tzset()
    else:
        _flash(request, "I do not know the time zone {zone}, so I kept the previous one.",
               "error", zone=zone)
        return RedirectResponse("/settings?section=general", status_code=303)

    # The message in the language the page shows in right away: the newly chosen language
    # of the server, unless the administrator has a language of their own (My account) - then that.
    own = account.get("ui_language")
    message_language = own if own in i18n.LANGUAGES else ui_language
    _flash(request, i18n.translate("Save language", message_language) + " ✓", "success", translated=True)
    return RedirectResponse("/settings?section=general", status_code=303)
