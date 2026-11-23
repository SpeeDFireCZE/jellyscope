"""Bazarr's settings: on, address and API key.

Stored in the `settings` table under the same keys as for Sonarr and Radarr, only with `bazarr` in the
name: `arr_bazarr_enabled` ("1"/""), `arr_bazarr_url` and `arr_bazarr_api_key`. The key is secret
(db.SECRET_SETTINGS): it is never written into a page, only whether there is one shows - so an empty
field on save means "keep the stored one".

A "connected" Bazarr is switched on and has both an address and a key. Only such a Bazarr has a tab
on the *Arr page and only such one is synced.
"""
from __future__ import annotations

from typing import Any

import httpx

from ... import services
from ...services import address, service_settings, save_service_settings
from .client import NAME, BazarrClient, BazarrError


SETTINGS_PREFIX = "arr_bazarr"


def stored_settings() -> dict[str, Any]:
    """What is stored - without the key, only whether there is one."""
    return service_settings(SETTINGS_PREFIX)


def is_connected() -> bool:
    """Switched on, with both an address and a key."""
    return services.is_connected(stored_settings())


def save_settings(enabled: bool, url: str, key: str) -> str:
    """Store the settings. Returns an error (a Czech sentence, a translation key), or "".

    The rules (an empty key keeps the stored one, switching on only with an address and a key) are
    shared by every app - services.save_service_settings.
    """
    return save_service_settings(SETTINGS_PREFIX, enabled, url, key)


async def check_connection(url: str, key: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """Ask Bazarr for `system/status`. Returns (True, version), or (False, error).

    It also checks that Bazarr really is at the address: the status must carry `bazarr_version`.
    Another app at the same address does not pass. The error is translated into the language of
    whoever ran the test.
    """
    url, error = address(url)
    if error or not url:
        return False, BazarrError(error or "Fill in the app address first.").translated()
    try:
        async with BazarrClient(url, key, transport=transport) as client:
            state = await client.status()
    except BazarrError as api_error:
        return False, api_error.translated()
    if not state.get("bazarr_version"):
        return False, BazarrError("{name} is not answering at this address.", name=NAME).translated()
    return True, str(state["bazarr_version"])
