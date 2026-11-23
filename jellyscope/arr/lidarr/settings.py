"""Lidarr's settings: on, address and API key.

Stored in the `settings` table under the keys `arr_lidarr_enabled` ("1"/""), `arr_lidarr_url` and
`arr_lidarr_api_key`. The key is secret (db.SECRET_SETTINGS): it is never written into a page, only
whether there is one shows - so an empty field on saving means "keep the stored one", as with
Jellyfin's key.

A "connected" Lidarr is on and has an address and a key. Only then it has a tab on the *Arr page and
only then the sync fetches from it.
"""
from __future__ import annotations

from typing import Any

import httpx

from ... import services
from ...services import address, service_settings, save_service_settings   # rules shared by every app
from .client import NAME, SETTINGS_PREFIX, LidarrClient, LidarrError


def stored_settings() -> dict[str, Any]:
    """What is stored - without the key, only whether there is one."""
    return service_settings(SETTINGS_PREFIX)


def is_connected() -> bool:
    """Is Lidarr on, with an address and a key?"""
    return services.is_connected(stored_settings())


def save_settings(enabled: bool, url: str, key: str) -> str:
    """Save Lidarr's settings. Returns an error (a sentence, a translation key), or "".

    The rules (an empty key keeps the stored one, it can be switched on only with an address and a
    key) are shared by every app - services.save_service_settings.
    """
    return save_service_settings(SETTINGS_PREFIX, enabled, url, key)


async def check_connection(url: str, key: str,
                           transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """Ask the app for `system/status`. Returns (True, version), or (False, error).

    It also checks that Lidarr really is at the address: the other *Arr apps answer the same
    question, so an address that leads to one of them by mistake would "work" - and the sync would
    then fetch something else as albums. The error is translated into the language of whoever ran
    the test.
    """
    url, error = address(url)
    if error or not url:
        return False, LidarrError(error or "Fill in the app address first.").translated()
    try:
        async with LidarrClient(url, key, transport=transport) as client:
            state = await client.status()
    except LidarrError as api_error:
        return False, api_error.translated()
    name = str(state.get("appName") or "")
    if name.lower() != NAME.lower():
        return False, LidarrError("{other} answers at this address, not {name}.",
                                  other=name or "?", name=NAME).translated()
    return True, str(state.get("version") or "?")
