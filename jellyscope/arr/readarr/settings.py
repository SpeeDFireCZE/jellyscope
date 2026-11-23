"""Readarr's settings: on, address and API key.

Stored in the `settings` table under the keys `arr_readarr_enabled` ("1"/""), `arr_readarr_url` and
`arr_readarr_api_key`. The key is secret (db.SECRET_SETTINGS): it is never written into a page, only
whether there is one shows - so an empty field on saving means "keep the stored one", as with
Jellyfin's key.

One connection serves the whole family: Readarr and the forks that continue it (Chaptarr, Bookshelf)
answer to the same API. The name the app gives is stored (`arr_readarr_app_name`, not a secret) and
the tab shows it - when the connection test (of the saved address) or the sync hears it.

A "connected" Readarr is on and has an address and a key. Only then it has a tab on the *Arr page and
only then the sync fetches from it.
"""
from __future__ import annotations

from typing import Any

import httpx

from ... import db, services
from ...services import address, service_settings, save_service_settings   # rules shared by every app
from .client import FAMILY, NAME, SETTINGS_PREFIX, ReadarrClient, ReadarrError

APP_NAME_KEY = f"{SETTINGS_PREFIX}_app_name"


def stored_settings() -> dict[str, Any]:
    """What is stored - without the key, only whether there is one."""
    return service_settings(SETTINGS_PREFIX)


def is_connected() -> bool:
    """Is Readarr on, with an address and a key?"""
    return services.is_connected(stored_settings())


def save_settings(enabled: bool, url: str, key: str) -> str:
    """Save Readarr's settings. Returns an error (a sentence, a translation key), or "".

    The rules (an empty key keeps the stored one, it can be switched on only with an address and a
    key) are shared by every app - services.save_service_settings.
    """
    return save_service_settings(SETTINGS_PREFIX, enabled, url, key)


def app_name() -> str:
    """The name of the app that answered the connection test (Readarr, Chaptarr or Bookshelf); Readarr until it did."""
    return family_name(db.get_setting(APP_NAME_KEY, "")) or NAME


def family_name(reported: Any) -> str:
    """The name of an app of the Readarr family as it is spelled here ("chaptarr" -> "Chaptarr"), else empty."""
    text = str(reported or "").strip().lower()
    return next((name for name in FAMILY if name.lower() == text), "")


def store_app_name(reported: Any) -> None:
    """Remember which app of the family answers (the tab's subtitle). Another app's name is not stored."""
    name = family_name(reported)
    if name and db.get_setting(APP_NAME_KEY, "") != name:
        db.set_setting(APP_NAME_KEY, name)


async def check_connection(url: str, key: str,
                           transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """Ask the app for `system/status`. Returns (True, version), or (False, error).

    It also checks that Readarr (or Chaptarr, or Bookshelf) really is at the address: the other *Arr
    apps answer the same question, so an address that leads to one of them by mistake would "work" -
    and the sync would then fetch something else as books. The error is translated into the language
    of whoever ran the test.
    """
    url, error = address(url)
    if error or not url:
        return False, ReadarrError(error or "Fill in the app address first.").translated()
    try:
        async with ReadarrClient(url, key, transport=transport) as client:
            state = await client.status()
    except ReadarrError as api_error:
        return False, api_error.translated()
    name = family_name(state.get("appName"))
    if not name:
        return False, ReadarrError("{other} answers at this address, not {name}.",
                                   other=str(state.get("appName") or "") or "?", name=NAME).translated()
    # Only the saved address names the app of the tab: trying another address (not saved) changes nothing stored.
    if url.rstrip("/") == str(db.get_setting(f"{SETTINGS_PREFIX}_url", "")).rstrip("/"):
        store_app_name(name)
    return True, str(state.get("version") or "?")
