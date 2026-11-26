"""Prowlarr's settings: on, address and API key; measuring the indexers' status in the background.

Stored in the `settings` table under the same keys as for the other apps of the *Arr page, only with
`prowlarr` in the name: `arr_prowlarr_enabled` ("1"/""), `arr_prowlarr_url` and `arr_prowlarr_api_key`.
The key is secret (db.SECRET_SETTINGS): it is never written into a page, only whether there is one
shows - so an empty field on save means "keep the stored one".

A "connected" Prowlarr is switched on and has both an address and a key. Only such a Prowlarr has a
tab on the *Arr page and only such one is synced. The saving rules are shared by every app
(services.py).

Prowlarr's own: the background loop that measures the indexers' status (watch.py) - switched on by
default (`arr_prowlarr_watch_enabled`), every `arr_prowlarr_watch_interval` seconds (15-600, 60 by
default). Saved from Prowlarr's form in Settings → *Arr.
"""
from __future__ import annotations

from typing import Any

import httpx

from ... import db, services
from ...services import address, service_settings, save_service_settings
from .client import NAME, ProwlarrClient, ProwlarrError

SETTINGS_PREFIX = "arr_prowlarr"
# Measuring the indexers' status in the background (watch.py): the switch, the interval in seconds and its bounds. Under
# a minute adds little - Prowlarr itself leaves a failing indexer out for at least a few minutes.
WATCH_ENABLED, WATCH_INTERVAL = f"{SETTINGS_PREFIX}_watch_enabled", f"{SETTINGS_PREFIX}_watch_interval"
WATCH_DEFAULT, WATCH_MINIMUM, WATCH_MAXIMUM = 60, 15, 600


def stored_settings() -> dict[str, Any]:
    """What is stored - without the key, only whether there is one."""
    return service_settings(SETTINGS_PREFIX)


def is_connected() -> bool:
    """Switched on, with both an address and a key."""
    return services.is_connected(stored_settings())


def save_settings(enabled: bool, url: str, key: str) -> str:
    """Store the settings. Returns an error (a sentence, a translation key), or "".

    The rules (an empty key keeps the stored one, switching on only with an address and a key) are
    shared by every app - services.save_service_settings.
    """
    return save_service_settings(SETTINGS_PREFIX, enabled, url, key)


def watch_settings() -> dict[str, Any]:
    """Measuring the indexers' status in the background: {"enabled", "interval" (seconds), "minimum", "maximum"}."""
    return {"enabled": db.get_setting(WATCH_ENABLED, "1") == "1",
            "interval": db.get_int_setting(WATCH_INTERVAL, WATCH_MINIMUM, WATCH_MAXIMUM, WATCH_DEFAULT),
            "minimum": WATCH_MINIMUM, "maximum": WATCH_MAXIMUM}


def save_watch_settings(enabled: bool, interval: str) -> None:
    """Store the switch and the interval from the form: a cleared or unreadable interval keeps the stored one, one
    outside the bounds is moved to the nearest bound."""
    try:
        seconds = int(float(interval))
    except (TypeError, ValueError, OverflowError):     # empty, text, 1e400, nan
        seconds = watch_settings()["interval"]
    db.set_setting(WATCH_ENABLED, "1" if enabled else "0")
    db.set_setting(WATCH_INTERVAL, str(max(WATCH_MINIMUM, min(WATCH_MAXIMUM, seconds))))


async def check_connection(url: str, key: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """Ask Prowlarr for `system/status`. Returns (True, version), or (False, error).

    It also checks that Prowlarr really is at the address: Sonarr and Radarr have an API that looks the
    same, so an address leading to them by mistake would otherwise "work" - and the sync would then
    read another app's history. The error is translated into the language of whoever ran the test.
    """
    url, error = address(url)
    if error or not url:
        return False, ProwlarrError(error or "Fill in the app address first.").translated()
    try:
        async with ProwlarrClient(url, key, transport=transport) as client:
            state = await client.status()
    except ProwlarrError as api_error:
        return False, api_error.translated()
    name = str(state.get("appName") or "")
    if name.lower() != NAME.lower():
        return False, ProwlarrError("{other} answers at this address, not {name}.", other=name or "?", name=NAME).translated()
    return True, str(state.get("version") or "?")
