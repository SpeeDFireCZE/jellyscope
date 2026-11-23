"""The shared core of connections to the applications around Jellyfin (Sonarr, Radarr, Seerr).

All these applications have a REST API with the key in the `X-Api-Key` header
and differ only in the path (`/api/v3/` for *Arr, `/api/v1/` for Seerr) and
the queries. Here is what they have in common - and what is thus fixed in
one place for all of them:

* `ServiceError` - an error with the template and the values apart (like
  `accounts.AccountError`): it can be translated into the language of the
  one who reads it;
* `address()` - checking and completing an address from a form;
* `HttpClient` - one GET with all errors converted to `ServiceError`. The
  key is **only in the header** (addresses are logged, headers are not) and
  never in an error's text, not even when the application itself repeats it
  in the response body. The same holds for a password in the address
  (`http://name:password@server` for a proxy with basic authentication): an
  error's text goes to the page, the state and the log.

The application packages (`arr/`, `seerr/`) derive their own client from
`HttpClient` and their own error from `ServiceError`.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from . import db
from .i18n import TranslatableError
from .jellyfin import QUICK_TIMEOUT
from .images import MAX_BYTES

# Long messages (translation keys - each in one string, so test_translations_dictionary finds it).
INVALID_ADDRESS = "The app address is not valid."
NOT_AN_API = "There is no {name} API at this address (404 on {path}). Is the address right, including the path behind a reverse proxy?"
NOT_JSON = "The {name} API is not answering at this address ({path} did not return JSON). Is there a proxy login in front of it?"
UNEXPECTED = "The {name} API is not answering at this address ({path} returned an unexpected answer)."
INVALID_KEY = "The API key contains a character that does not belong there (a space, an accented letter or an invisible character). Copy it again."


class ServiceError(TranslatableError):
    """Anything that goes wrong while talking to an application.

    `str(error)` is the English sentence with the values filled in (for the log
    and tests), `error.translated()` the same sentence in the language of the
    one who looks - see i18n.TranslatableError.
    """


def utc_time(text: Any) -> str | None:
    """ISO 8601 from an API (even with the seven decimal places of .NET, with `Z` or an offset)
    -> UTC in db.TIME_FORMAT. Unreadable or empty = None."""
    if not text:
        return None
    try:
        time = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if time.tzinfo is None:
            time = time.replace(tzinfo=UTC)
        # OverflowError: the default date of .NET (year 1) with a positive offset is before year 1 in UTC.
        time = time.astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    return time.strftime(db.TIME_FORMAT)


def key_error(key: str) -> str:
    """Why the key cannot be used (a translation key), or "".

    The key goes into an HTTP header, and that takes only printable ASCII
    characters. An extra character gets into the field easily - an invisible
    space copied from the web, diacritics from a typo - and the httpx library
    fails on it with an exception before anything is sent. It is better to say
    so at the form.
    """
    if key and (not key.isascii() or not key.isprintable() or any(z.isspace() for z in key)):
        return INVALID_KEY
    return ""


# The "sync is already running" error of every application. A task tells by the template that
# it only met the "Sync now" button and does not report it as a failure - so the
# sentence is in one place.
SYNC_ALREADY_RUNNING = "{name} sync is already running."


def service_settings(prefix: str) -> dict[str, Any]:
    """An application's stored settings under the keys `<prefix>_enabled`, `_url` and `_api_key`.

    Without the key - only whether there is one (it is not written into the page).
    """
    return {
        "enabled": db.get_setting(f"{prefix}_enabled", "") == "1",
        "url": db.get_setting(f"{prefix}_url", ""),
        "has_key": bool(db.get_setting(f"{prefix}_api_key", "")),
    }


def is_connected(settings: Mapping[str, Any]) -> bool:
    """A "connected" application is on and has an address and a key. Only that one has a page and only that one is synchronised."""
    return bool(settings["enabled"] and settings["url"] and settings["has_key"])


def save_service_settings(prefix: str, enabled: bool, url: str, key: str,
                          before_write: Callable[[str], None] | None = None) -> str:
    """Stores an application's settings. Returns an error (a Czech sentence, a translation key), or "".

    An empty `key` keeps the stored one. Turning on works only with an address
    and a key - an application turned on without them would have no page and
    nobody would know why. A turned-off one is stored without them too and
    keeps its address and key. `before_write` gets the checked address just
    before storing (Seerr forgets by it what it knew about the old server).
    """
    url, error = address(url)
    if error:
        return error
    key = (key or "").strip()
    if key_error(key):
        return key_error(key)
    if enabled and not url:
        return "The app cannot be enabled without an address."
    if enabled and not (key or service_settings(prefix)["has_key"]):
        return "The app cannot be enabled without an API key."
    if before_write:
        before_write(url)
    db.set_setting(f"{prefix}_enabled", "1" if enabled else "")
    db.set_setting(f"{prefix}_url", url)
    if key:
        db.set_setting(f"{prefix}_api_key", key)
    return ""


def write_state(key: str, result: str, new_count: int, error: Exception | None = None) -> None:
    """Stores the state of the last sync into the setting `key` (JSON).

    The error is stored as a template and values too (ServiceError), so
    `read_state` can show it in the language of the one who looks. Shared by
    the sync of *Arr and of Seerr.
    """
    state = {"at": db.utcnow(), "result": result, "new_count": new_count, "error": str(error) if error else "",
             "template": getattr(error, "template", ""), "values": getattr(error, "values", {})}
    db.set_setting(key, json.dumps(state, ensure_ascii=False, default=str))


def read_state(key: str) -> dict[str, Any]:
    """The state of the last sync: when (UTC), the result ("ok" / "error" / ""), how many were added, the error (translated)."""
    try:
        stored = json.loads(db.get_setting(key, "") or "{}")
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    error = str(stored.get("error") or "")
    if stored.get("template"):
        try:
            error = ServiceError(str(stored["template"]), **(stored.get("values") or {})).translated()
        except (KeyError, IndexError, TypeError, ValueError):
            pass   # the template changed (a new version): the stored text stays as it was
    return {"at": str(stored.get("at") or ""), "result": str(stored.get("result") or ""),
            "new_count": integer(stored.get("new_count")), "error": error}


def integer(value: Any, default: int | None = 0) -> int | None:
    """A whole number from an application's answer; nonsense (a text, null, a list) = `default`.

    Another application's answer is an input like any other: `int("abc")`
    would bring down a page or a sync where a sensible default is enough.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):      # OverflowError: infinity (JSON allows it)
        return default


def address(url: str) -> tuple[str, str]:
    """Adjusts an address from a form: returns (address, error).

    Without `http://` it adds it (people write "nas:8989"). A scheme other
    than http(s) it refuses - `ftp://nas` would otherwise be "fixed" to
    `http://ftp://nas`. The path stays as it is: behind a reverse proxy the
    application tends to be at `/sonarr`. An empty address is fine (a
    turned-off application need not have one).
    """
    url = (url or "").strip()
    if not url:
        return "", ""
    if "://" not in url:
        url = "http://" + url
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - only a check: a non-numeric port raises ValueError
    except ValueError:
        return url, INVALID_ADDRESS
    if parts.scheme not in ("http", "https"):
        return url, "The app address must start with http:// or https://."
    if not parts.hostname or any(z.isspace() for z in url):
        return url, INVALID_ADDRESS
    try:
        httpx.URL(url)      # httpx is stricter (IDNA): a soft hyphen in the host name does not pass
    except httpx.InvalidURL:
        return url, INVALID_ADDRESS
    return url, ""


# A name without a password counts as a token only from this length (see _secrets_from_address).
SHORTEST_TOKEN = 8
_CREDENTIALS_IN_ADDRESS = re.compile(r"(?<=://)[^/@\s]*@")


def _secrets_from_address(url: str) -> list[str]:
    """What is secret in the address - as it is written, and decoded too (%40 -> @).

    For `http://name:password@server` the password. For `http://token@server`
    (a name only, without a password) that name: that is how a token is given
    that some proxies authenticate with - but only a long one
    (SHORTEST_TOKEN). A short name without a password is rather a forgotten
    password than a token, and masking it would eat ordinary words and the
    server's name out of the messages (`http://nas@nas`). Likewise a name that
    has a password is not masked. The application or the proxy may repeat the
    credential in an error answer in both forms.
    """
    try:
        parts = urlsplit(url)
        name = parts.username or ""
        secret = parts.password or (name if len(name) >= SHORTEST_TOKEN else "")
    except ValueError:
        return []
    return sorted({secret, unquote(secret)}) if secret else []


def _without_credentials(url: str) -> str:
    """The address without credentials (`http://name:password@server/…` -> `http://server/…`) - for an error's text."""
    return _CREDENTIALS_IN_ADDRESS.sub("", url)


class HttpClient:
    """The connection to one application; used as a context manager.

    `url` may have a path behind a reverse proxy too (`http://nas/sonarr`),
    `api` is the application's API path (`/api/v3/`). A derived client sets
    `error_class` to its own error class. `transport` is for tests
    (httpx.MockTransport).
    """

    error_class: type[ServiceError] = ServiceError

    def __init__(self, name: str, url: str, api: str, key: str,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: httpx.Timeout | None = None) -> None:
        self.name = name
        self._api = api
        # The application's address without the API path - for what the
        # application offers outside the API (Seerr's images at /imageproxy/).
        self._root = url.rstrip("/")
        # What must not appear in any error text: the key and the password (or token) from the address.
        # From the longest, so a shorter secret does not break the replacing of a longer one.
        self._secrets = sorted((t for t in {key, *_secrets_from_address(url)} if t), key=len, reverse=True)
        if key_error(key):
            # A key stored before the form guarded it: an error, not a failing page.
            raise self.error_class(INVALID_KEY)
        try:
            self._client = httpx.AsyncClient(
                base_url=url.rstrip("/") + api,
                headers={"X-Api-Key": key, "Accept": "application/json"},
                timeout=timeout or QUICK_TIMEOUT,
                transport=transport,
            )
        except httpx.InvalidURL:
            # An address stored before address() guarded it: an error, not a failing page.
            raise self.error_class(INVALID_ADDRESS) from None

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    def _without_key(self, text: str) -> str:
        """A safeguard: should the key or the password from the address appear in a text from
        elsewhere (an error body, a library exception's text, a redirect target), it is replaced with dots."""
        for secret in self._secrets:
            text = text.replace(secret, "•••")
        return text

    async def _get(self, path: str, params: dict[str, Any] | None = None,
                   timeout: httpx.Timeout | None = None) -> Any:
        """One GET. Converts every error to `self.error_class` without the key in the text."""
        return self._content(await self._send(path, params, timeout), path)

    async def _post(self, path: str, body: dict[str, Any],
                    timeout: httpx.Timeout | None = None) -> Any:
        """One POST with a JSON body - handled the same as GET.

        The key goes only in the header, redirects are not followed (the key
        would leave elsewhere) and every error is converted to
        `self.error_class` without the key in the text. Jellyscope sends the
        applications almost nothing: the only command is refreshing the
        downloads in Sonarr and Radarr (`refresh_downloads` of their clients).
        """
        return self._content(await self._send(path, None, timeout, body=body), path)

    async def _json_with_list(self, path: str, fields: tuple[str, ...], params: dict[str, Any] | None = None,
                              timeout: httpx.Timeout | None = None) -> Any:
        """A big answer of which only a few fields of every object are needed.

        For answers the application cannot shorten: all of Radarr's movies with
        the cast and images (tens of MB for a big library), or a queue that
        sends the whole series with every record. Two things differ from `_get`:

        * the JSON is parsed **in a thread**, not in the asyncio loop - else
          the whole web and the collector would stand still for that time;
        * of every object - nested ones too - only `fields` stay. They are
          dropped already while parsing (`object_hook`), so the whole tree of
          the answer is never in memory. What is not in `fields` is not even read.
        """
        response = await self._send(path, params, timeout)
        self._check_status(response, path)
        if not response.content:
            return None
        try:
            return await asyncio.to_thread(
                json.loads, response.content, object_hook=lambda obj: {k: obj[k] for k in fields if k in obj})
        except ValueError:
            raise self.error_class(NOT_JSON, name=self.name, path=path) from None

    async def _list(self, path: str, fields: tuple[str, ...],
                    timeout: httpx.Timeout | None = None,
                    params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """A big list of objects of which only a few fields of each are needed (see _json_with_list)."""
        data = await self._json_with_list(path, fields, params, timeout)
        return [record for record in data if isinstance(record, dict)] if isinstance(data, list) else []

    async def _send(self, path: str, params: dict[str, Any] | None = None,
                    timeout: httpx.Timeout | None = None, *, body: dict[str, Any] | None = None) -> httpx.Response:
        """Sends a GET (with `body` a POST with a JSON body) and returns the answer. Converts a connection error to `self.error_class` without the key in the text."""
        limit = timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
        try:
            if body is None:
                response = await self._client.get(path, params=params, timeout=limit)
            else:
                response = await self._client.post(path, params=params, json=body, timeout=limit)
        except httpx.TimeoutException:
            raise self.error_class("{name} did not answer in time ({path}).", name=self.name, path=path) from None
        except httpx.InvalidURL:
            raise self.error_class(INVALID_ADDRESS) from None
        except httpx.RequestError as error:
            # `from None`: the library's exception carries the request too (with
            # the headers); it does not belong in the chain of errors that may
            # be written into the log.
            raise self.error_class("{name} is not answering: {reason}", name=self.name,
                                   reason=self._without_key(str(error) or error.__class__.__name__)) from None
        return response

    def _content(self, response: httpx.Response, path: str) -> Any:
        """The answer's status -> the content, or an error with a clear message."""
        self._check_status(response, path)
        try:
            return response.json() if response.content else None
        except ValueError:
            # Typically a reverse proxy's login page: it answers 200, but HTML.
            raise self.error_class(NOT_JSON, name=self.name, path=path) from None

    def _check_status(self, response: httpx.Response, path: str) -> None:
        """Converts an answer that is not fine (a redirect, 401, 404, an error) to an error with a clear message."""
        if 300 <= response.status_code < 400:
            # A redirect is on purpose not followed: the key in the header would
            # leave to another address. Typically http:// in front of a proxy
            # with TLS -> https://. The message offers the application's address
            # (without the API path) to enter - without credentials: a relative
            # redirect would take them from our address and the message goes to
            # the page and the log.
            target = response.url.join(response.headers.get("location", ""))
            where = str(target.copy_with(userinfo=b"")).split("?")[0]
            raise self.error_class("{name} redirects elsewhere at this address. Enter the address {target} directly.",
                                   name=self.name, target=self._without_key(where.split(self._api)[0].rstrip("/")))
        if response.status_code == 401:
            raise self.error_class("{name} rejected the API key (401).", name=self.name)
        if response.status_code == 403:
            raise self.error_class("{name} denied access (403).", name=self.name)
        if response.status_code == 404:
            raise self.error_class(NOT_AN_API, name=self.name, path=path)
        if response.status_code >= 400:
            # Clean first, cut after: a key split by the boundary of two hundred
            # characters would otherwise not be replaced and its start would leak.
            reserve = max((len(secret) for secret in self._secrets), default=0)
            raise self.error_class("{name} returned error {code} on {path}: {text}", name=self.name,
                                   code=response.status_code, path=path,
                                   text=self._without_key(response.text[:200 + reserve])[:200])

    async def _image(self, url: str) -> bytes | None:
        """Downloads an image from the application. None = the application does not have it (404) or it is over the ceiling.

        `url` is a path in the API (`mediacover/8/poster-250.jpg`), or a full
        address on the same application (Seerr's images are outside the API).
        It is downloaded in pieces, so a giant body is never read into memory
        whole - past the ceiling it stops. Redirects are not followed (the key
        in the header would leave elsewhere) and it is an error like any other
        answer except 200 and 404. Whether the body really is an image is
        checked only by whoever stores it (images.save_title) - by the content,
        not by the header.
        """
        try:
            async with self._client.stream("GET", url, headers={"Accept": "image/*"}) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise self.error_class("{name} returned error {code} on {path}: {text}", name=self.name,
                                           code=response.status_code, path=self._without_key(_without_credentials(url.split("?")[0])), text="")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        return None
        except httpx.TimeoutException:
            raise self.error_class("{name} did not answer in time ({path}).", name=self.name,
                                   path=self._without_key(_without_credentials(url.split("?")[0]))) from None
        except httpx.InvalidURL:
            raise self.error_class(INVALID_ADDRESS) from None
        except httpx.RequestError as error:
            raise self.error_class("{name} is not answering: {reason}", name=self.name,
                                   reason=self._without_key(str(error) or error.__class__.__name__)) from None
        return bytes(content) or None

    async def _object(self, path: str, params: dict[str, Any] | None = None,
                      timeout: httpx.Timeout | None = None) -> dict[str, Any]:
        """A GET that must return an object (a JSON dictionary). Anything else is an error."""
        response = await self._get(path, params, timeout) or {}
        if not isinstance(response, dict):
            raise self.error_class(UNEXPECTED, name=self.name, path=path)
        return response
