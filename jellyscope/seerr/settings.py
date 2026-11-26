"""Seerr's settings: on, address and API key.

Stored in the `settings` table under the keys `seerr_enabled` ("1"/""), `seerr_url` and
`seerr_api_key`. The key is secret (db.SECRET_SETTINGS): it is never written into a page, only
whether there is one shows - an empty field on saving therefore means "keep the stored one", as with
the keys of Jellyfin and the *Arr apps.

A "connected" Seerr is on and has an address and a key. Only that one has the Seerr page in the menu
and only that one the sync fetches from.

Plus two things Jellyscope finds out about Seerr itself from its public settings
(posters.check_images) and only remembers here:

* `seerr_images` - does Seerr serve images ("Enable Image Caching")? "1" yes, "0" no, "" unknown.
  Without them a pending or declined request has no poster.
* `seerr_web` - the address people open Seerr at (its "Application URL"), when set. The link
  "Open Seerr's settings" leads there; the address Jellyscope knows tends to be one from the server's
  inner network.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import db
from .. import services
from ..services import address, service_settings, save_service_settings
from .client import NAME, SeerrClient, SeerrError


def stored_settings() -> dict[str, Any]:
    """What is stored - without the key, only whether there is one."""
    return service_settings("seerr")


def is_connected() -> bool:
    """On, with an address and a key."""
    return services.is_connected(stored_settings())


IMAGES_KEY = "seerr_images"
WEB_KEY = "seerr_web"


def images_state() -> str:
    """Does Seerr serve images ("Enable Image Caching")? "1" yes, "0" no, "" unknown."""
    value = db.get_setting(IMAGES_KEY, "") or ""
    return value if value in ("0", "1") else ""


def write_images_state(enabled: bool | None) -> bool:
    """Remember whether Seerr serves images (None = unknown). Returns whether the state changed.

    Only a change is written: the state is read on every sync and writing the same value would only
    be a needless write to the database.
    """
    new = "" if enabled is None else ("1" if enabled else "0")
    if new == images_state():
        return False
    db.set_setting(IMAGES_KEY, new)
    return True


def _without_credentials(url: str) -> str:
    """An http(s) address without credentials and without a query, or "" (not an address for a link).

    A password from the address (`http://name:password@server`) does not belong in a link on a page,
    and it must be nothing but http or https - the value may come from another app's answer and
    `javascript:` in a link would be code.
    """
    text = str(url or "").strip()
    if not text or any(char.isspace() or ord(char) < 32 for char in text):
        return ""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}{f':{port}' if port else ''}{parts.path.rstrip('/')}"


def write_web_address(value: Any) -> None:
    """Remember Seerr's "Application URL" - only a valid http(s) address without credentials."""
    text = str(value or "").strip() if isinstance(value, str) else ""
    web_address = _without_credentials(text) if "@" not in text else ""
    if web_address != (db.get_setting(WEB_KEY, "") or ""):
        db.set_setting(WEB_KEY, web_address)


def settings_link() -> str:
    """Where the link "Open Seerr's settings" leads (its Settings → General), or "".

    To the address Seerr has set for people; otherwise to the one Jellyscope knows - without a
    password, if it has one. The latter tends to be an address from the server's inner network and
    a browser may not open it; the page says so at the link.
    """
    base = (db.get_setting(WEB_KEY, "") or "") or _without_credentials(db.get_setting("seerr_url", "") or "")
    return f"{base}/settings/main" if base else ""


def save_settings(enabled: bool, url: str, key: str) -> str:
    """Save the settings. Returns an error (a sentence, a translation key), or "".

    The rules (an empty key keeps the stored one, it can be switched on only with an address and a
    key) are shared by all apps - services.save_service_settings.
    """
    def other_address(new: str) -> None:
        if new.rstrip("/") != (db.get_setting("seerr_url", "") or "").rstrip("/"):
            # Another address may be another Seerr: what was known about the old one (it serves
            # images, the address for people) does not hold for the new one.
            write_images_state(None)
            write_web_address("")

    return save_service_settings("seerr", enabled, url, key, before_write=other_address)


async def check_connection(url: str, key: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """Ask Seerr for `settings/about`. Returns (True, version), or (False, error).

    It also checks that Seerr really is at the address: the answer must have `totalRequests`
    (Overseerr and Jellyseerr have it too - the same API). Sonarr or another app at the same address
    does not pass. The error is translated into the language of whoever ran the test.
    """
    url, error = address(url)
    if error or not url:
        return False, SeerrError(error or "Fill in the app address first.").translated()
    try:
        async with SeerrClient(url, key, transport=transport) as client:
            about = await client.about()
    except SeerrError as api_error:
        return False, api_error.translated()
    if "totalRequests" not in about:
        return False, SeerrError("{name} is not answering at this address.", name=NAME).translated()
    return True, str(about.get("version") or "?")
