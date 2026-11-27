"""A client for the Jellyfin API.

The only file in the project that knows how Jellyfin talks. The rest of the
application asks this module and does not have to deal with headers, paging
or field names.

This is called **separation of concerns**: when Jellyfin changes its API in a
year, you fix one file, not twenty places scattered over the project.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from . import demo_switch, languages
from .i18n import TranslatableError

log = logging.getLogger("jellyscope.jellyfin")

# Fields we want on library items on top. Without them Jellyfin returns only
# the bare name and id.
ITEM_FIELDS = ",".join([
    "Path",
    "MediaSources",
    "MediaStreams",
    "Genres",
    "ProductionYear",
    "DateCreated",
    "ParentId",
    "Overview",
    # Identifiers from external databases (TMDB, IMDB, TVDB). Thanks to them
    # we know that a transcoded file with a new ItemId is still the same
    # movie - see scanner._merge_by_tmdb().
    "ProviderIds",
])

# The fields of music items (jellyscope/music/sync.py). Without the `Path` field and the
# overview: there tend to be tens of thousands of tracks. `MediaSources` carries the size,
# the file's path (since 16. 11. 2026 kept: music on its disk, content_kinds.py), the bitrate
# and the audio stream (codec, sample rate, bit depth); `ProviderIds` the id from
# MusicBrainz. Verified on Jellyfin 10.11 and 12.1 (see tests/music_responses/).
MUSIC_FIELDS = ",".join([
    "ProviderIds",
    "Genres",
    "DateCreated",
    "MediaSources",
    "ProductionYear",
    "PremiereDate",
    "SortName",
    "ParentId",
])

# The fields of books and audiobooks (jellyscope/books/sync.py). `MediaSources` carries the size and the file's
# path, `People` the author of an e-book (Type "Author"), `ProviderIds` the ISBN. The cover is in `ImageTags`
# (always there). No overview: only what the tables keep.
BOOK_FIELDS = ",".join([
    "ProviderIds",
    "Genres",
    "DateCreated",
    "MediaSources",
    "ProductionYear",
    "People",
    "SeriesName",
    "SortName",
    "ParentId",
    "Path",
])


class JellyfinError(TranslatableError):
    """Anything that goes wrong while talking to Jellyfin - a key and its values (i18n.TranslatableError)."""


def id_without_dashes(value: Any) -> str:
    """An id from Jellyfin in a shape it can be compared in: lower case, without dashes.

    The same id comes once as `a1b2c3d4e5f6…`, another time as
    `a1b2c3d4-e5f6-…` - depending on which endpoint or application sends it
    (Jellyfin's API without dashes; Playback Reporting, Jellystat and
    sometimes Seerr with them). It is the same number, only written
    differently. One place for the whole application: `accounts.bare_id`,
    the importers and Seerr call this.
    """
    return str(value or "").replace("-", "").strip().lower()


def digits_only(text: Any) -> bool:
    """Is it a non-empty text of digits 0-9 only? (TMDB, TVDB and other numeric ids from other applications.)

    `str.isdigit()` lets through digits outside ASCII too ("²", the Arabic
    "٣"). An id in such a shape belongs nowhere: `int("²")` fails, and in the
    database or in a file name there would lie something that only looks
    like a number. One rule for the whole application - it lives here, next
    to the other id helpers, because the image store (images.py), which is
    below the application clients (services.py), needs it too.
    """
    return isinstance(text, str) and text.isascii() and text.isdigit()


def id_forms(value: Any) -> list[str]:
    """The forms an id can be stored in the database in: without dashes and with them (8-4-4-4-12).

    For a query `column IN (...)`, which goes through an index. The opposite
    way - `REPLACE(LOWER(column), '-', '')` in the query - rules the index out
    and the database has to go through the whole table (tens of seconds for
    a big library).
    """
    bare = id_without_dashes(value)
    if len(bare) != 32:
        return [bare] if bare else []
    return [bare, f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}"]


# The server version we saw last. It is remembered in the settings, so it can
# decide already when the first query is put together - asking /System/Info
# first every time would be one more query for every call.
VERSION_KEY = "jellyfin_version"

# From this version Jellyfin **does not parse** the old authorisation headers
# (X-Emby-Token, X-Emby-Authorization, X-MediaBrowser-Token) nor the paths
# /emby/ and /mediabrowser/. Valid stays `Authorization: MediaBrowser
# Token="..."`, which is what Jellyscope has always identified itself with.
FIRST_WITHOUT_LEGACY = 12


def server_version(text: Any) -> tuple[int, int]:
    """"12.0.1" -> (12, 0). What makes no sense is (0, 0) = unknown.

    An unknown version on purpose means "behave as with an old server": send
    the old header too and try the fallback path too. On a new server that
    does no harm (it ignores the extra header), whereas the opposite choice
    would mean a refused key on an old server.
    """
    parts = str(text or "").strip().split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return (0, 0)
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        minor = 0
    return (major, minor)


LOCAL_ADDRESSES = ("localhost", "127.0.0.1", "::1", "[::1]")


def unencrypted_over_network(url: str) -> bool:
    """Does the connection to Jellyfin go over the network and without encryption?

    The key goes in a header and a viewer's password in the request body -
    both as readable text, because that is how Jellyfin takes it (and its own
    web player does it so too). Encrypting it on its behalf is not possible:
    encryption is done by `https`, not by the application above it.

    On `localhost` nothing is sent over the network - the request never leaves
    the machine -, so there is nothing to warn about there.
    """
    address = str(url or "").strip().lower()
    if not address.startswith("http://"):
        return False
    host = address[len("http://"):].split("/")[0].split(":")[0]
    return host not in LOCAL_ADDRESSES


def old_server(version: Any) -> bool:
    """Is it a server that still knows the dropped ways of signing in?

    An unknown version counts as old - see `server_version`.
    """
    major = server_version(version)[0]
    return major == 0 or major < FIRST_WITHOUT_LEGACY


def headers(api_key: str, version: Any = "") -> dict[str, str]:
    """The headers for all queries.

    `Authorization: MediaBrowser Token="..."` is the only way Jellyfin 12
    accepts - and at the same time every older version knows it, so it is
    always sent. The key belongs in a header, not in the address: the address
    is logged in a proxy and in the browser's history.

    `X-Emby-Token` is the old header. From twelve on it is not parsed, so it
    would only be a needless repetition of the key in every query; older
    servers still get it, because for some it was the only way that suited
    them.
    """
    result = {
        "Authorization": f'MediaBrowser Token="{api_key}"',
        "Accept": "application/json",
    }
    if old_server(version):
        result["X-Emby-Token"] = api_key
    return result


# The server generation chosen by hand. "auto" = tell by /System/Info.
GENERATION_KEY = "jellyfin_generation"

# What each choice means in version numbers. The manual choice thus goes the
# same way as the detected version - one decision, not two.
GENERATIONS = {
    "12": "12.0",
    "10": "10.10",
}


def _remembered_version() -> str:
    """The version that decides: the manual choice, otherwise the detected one.

    The manual choice wins, because a person entered it against what was
    detected - typically when the server behaves differently than it says
    (behind a proxy, in a test version). Empty means "I do not know", and that
    behaves as an old server; see `server_version`.

    Without the database (tests, a start before it is opened) nobody knows.
    """
    try:
        from . import db

        choice = db.get_setting(GENERATION_KEY, "auto")
        return GENERATIONS.get(choice) or db.get_setting(VERSION_KEY, "")
    except Exception:      # noqa: BLE001 - without the database nobody knows
        return ""


# One number for all phases of the connection was too little. Establishing the
# connection should be quick - when the server does not answer, waiting half a
# minute makes no sense. But the ANSWER to a query for three hundred items can
# take Jellyfin a minute to prepare for a big library, especially when it plays
# or transcodes at the same time. With a shared thirty-second ceiling the full
# sync failed on a big library - and the message claimed it could not connect,
# so the error was looked for in the address and the key, where it was not.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)

# How many times to repeat a query that ended by timing out. Repeating a GET is
# safe - it changes nothing. A short hiccup (Jellyfin is transcoding) is thus
# survived by the whole scan instead of it failing halfway.
TIMEOUT_RETRIES = 2

# A short ceiling for queries a person waits for at the screen (the button
# "Otestovat spojeni") or which repeat every few seconds (the collector).
# There long waiting does harm: instead of the answer "the server does not
# answer" a wheel would just spin and the next query would pile up on the previous.
QUICK_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)

# Images Jellyscope passes on from Jellyfin to its own address. Not "anything
# image/*": SVG can carry a script, and opened directly at Jellyscope's address
# it would be a document of Jellyscope's own origin. The CSP (script-src 'self')
# keeps a script inside it from running, but such a file has no business here
# anyway. Posters and profile photos are these raster formats.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif",
               "image/avif", "image/bmp")

# The ceiling of one image's size. Jellyfin does not shrink a profile photo (it
# returns it as somebody uploaded it), so a photo from a phone can have tens of
# MB - and all of it would go through Jellyscope's memory into a thirty-pixel circle.
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _identity(user: dict[str, Any], name: str = "") -> dict[str, Any]:
    """Who signed in - the same for a password and for Quick Connect.

    One place: were a new detail added to one path only, signing in with a
    password and through Quick Connect would each quietly behave differently.
    """
    policy = user.get("Policy") or {}
    return {
        "id": str(user["Id"]),
        "name": str(user.get("Name") or name),
        "is_admin": bool(policy.get("IsAdministrator")),
        "is_disabled": bool(policy.get("IsDisabled")),
        # The profile image's tag: with it a new image is visible right after
        # signing in (accounts.refresh_image), the image itself is not.
        "image": str(user.get("PrimaryImageTag") or ""),
    }


# The name the demo's made-up Jellyfin is registered under (demo_switch, demo_apps).
DEMO_NAME = "Jellyfin"


class JellyfinClient:
    """A thin wrapper over HTTP calls to Jellyfin.

    It is used as a context manager, so the connection is always closed properly:

        async with JellyfinClient(url, key) as jf:
            info = await jf.system_info()

    `transport` is for tests (httpx.MockTransport); without it the demo mode gives the made-up
    Jellyfin (demo_switch, under DEMO_NAME), anywhere else it is the network.
    """

    def __init__(self, base_url: str, api_key: str,
                 timeout: httpx.Timeout | float | None = None,
                 version: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # The server version: either passed (tests), or the remembered one.
        # It decides what is sent and what is tried - see `headers()` and
        # `old_server()`.
        self.version = _remembered_version() if version is None else version
        # A value stored before Settings guarded it (services.address, services.key_error): httpx refuses it
        # before anything is sent, with an error the callers do not catch - an error of the client instead.
        try:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
                headers=headers(api_key, self.version),
                transport=transport if transport is not None else demo_switch.transport(DEMO_NAME),
            )
        except UnicodeEncodeError:
            raise JellyfinError(
                "The API key contains a character that does not belong there (a space, an accented letter or an invisible character). Copy it again."
            ) from None
        except httpx.InvalidURL:
            raise JellyfinError("The app address is not valid.") from None

    async def __aenter__(self) -> JellyfinClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def _without_key(self, text: str) -> str:
        """A safeguard: should the key appear in a text from elsewhere (an error page that repeats the request's
        headers, a library exception's text), it is replaced with dots - the text goes into the side panel, the log
        and the notifications."""
        return text.replace(self.api_key, "•••") if self.api_key else text

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """One GET call. Converts all errors to JellyfinError."""
        for attempt in range(TIMEOUT_RETRIES + 1):
            try:
                response = await self._client.get(path, params=params)
                break
            except httpx.TimeoutException as exc:
                # Timing out is not the same as "I did not connect". The server
                # answers, only slowly - and that is different advice for the user.
                if attempt < TIMEOUT_RETRIES:
                    log.warning("Jellyfin did not answer %s in time, "
                                "retrying (%d/%d)", path, attempt + 1, TIMEOUT_RETRIES)
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise JellyfinError(
                    "Jellyfin did not answer {path} in time ({kind}). The server is running, but the answer "
                    "takes longer than allowed - typically with a large library or while it is transcoding.",
                    path=path, kind=exc.__class__.__name__) from exc
            except httpx.RequestError as exc:
                raise JellyfinError("Could not connect to Jellyfin: {error}", error=self._without_key(str(exc))) from exc

        if 300 <= response.status_code < 400:
            raise self._redirected(response, path)
        if response.status_code == 401:
            raise JellyfinError("Jellyfin refused the API key (401). Check it in Settings → Jellyfin.")
        if response.status_code == 403:
            raise JellyfinError("The API key has no permission for this (403).")
        if response.status_code >= 400:
            # Clean first, cut after: a key split by the cut would otherwise not be replaced and its start would leak.
            raise JellyfinError("Jellyfin returned error {status} on {path}: {body}", status=response.status_code,
                                path=path, body=self._without_key(response.text[:200 + len(self.api_key)])[:200])

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise JellyfinError("Jellyfin returned invalid JSON on {path}", path=path) from exc

    def _redirected(self, response: httpx.Response, path: str) -> JellyfinError:
        """A redirect as an error with the address to enter instead (like services.HttpClient._check_status).

        It is not followed on purpose: the key in the header would leave for another address. Typically http:// in
        front of a proxy with TLS -> https://; with an empty body it used to count as an empty answer, so the
        connection test "worked" and the collector collected nothing. The address offered is without credentials,
        the query and the API path - the message goes to the page and the log.
        """
        target = response.url.join(response.headers.get("location", ""))
        where = str(target.copy_with(userinfo=b"")).split("?")[0].split("#")[0]
        if where.endswith(path):
            where = where[:-len(path)]
        return JellyfinError("{name} redirects elsewhere at this address. Enter the address {target} directly.",
                             name="Jellyfin", target=self._without_key(where.rstrip("/")))

    @staticmethod
    def _sign_in_answer(response: httpx.Response) -> dict[str, Any]:
        """The JSON object of a sign-in answer, {} when there is none.

        A body that is no JSON (a proxy's sign-in page with 200) is an error of the client the sign-in routes
        catch - not an exception the sign-in page falls on.
        """
        try:
            data = response.json() if response.content else None
        except ValueError as exc:
            raise JellyfinError("Jellyfin returned invalid JSON on {path}", path=response.url.path) from exc
        return data if isinstance(data, dict) else {}

    # -----------------------------------------------------------------
    # Concrete queries
    # -----------------------------------------------------------------

    async def system_info(self) -> dict[str, Any]:
        """Basic info about the server. Serves mainly as a connection test.

        At the same time it **remembers the version**. It then decides what
        makes sense to send and what no longer does (see `headers`,
        `old_server`) - and the next client already knows it without having
        to ask.
        """
        from . import db

        info = await self._get("/System/Info")
        # Something other than Jellyfin answers (an empty page, foreign JSON): no working connection "? (Jellyfin ?)".
        if not isinstance(info, dict) or not (info.get("Version") or info.get("Id")):
            raise JellyfinError(
                "The {name} API is not answering at this address ({path} returned an unexpected answer).",
                name="Jellyfin", path="/System/Info")
        version = str(info.get("Version") or "").strip()
        # It is always written, even with a manual choice. It is an
        # observation of the server, not a setting: when a person chooses
        # twelve and the server reports ten, that is to be visible, not
        # overwritten. The behaviour is driven by the choice - see
        # `_remembered_version`.
        if version and version != db.get_setting(VERSION_KEY, ""):
            try:
                db.set_setting(VERSION_KEY, version)
                log.info("Jellyfin reports version %s", version)
            except Exception:   # noqa: BLE001 - without the database nothing happens
                pass
            # What decides from now on: the manual choice, otherwise this version.
            self.version = _remembered_version() or version
        return info

    async def users(self) -> list[dict[str, Any]]:
        return await self._get("/Users") or []

    async def sessions(self) -> list[dict[str, Any]]:
        """What is happening on the server right now.

        This is the source of the whole playback history. Jellyfin tells us
        only the present ("Petr is playing this episode now, he is at minute
        12"). The history is put together by us - see collector.py.
        """
        return await self._get("/Sessions") or []

    async def virtual_folders(self) -> list[dict[str, Any]]:
        """The libraries including the paths on the disk."""
        return await self._get("/Library/VirtualFolders") or []

    async def storage(self) -> dict[str, Any]:
        """How much space the server's folders have - according to Jellyfin ITSELF.

        The essential difference from `shutil.disk_usage`: that measures the
        disk JELLYSCOPE runs on. But the data tend to be elsewhere - on a NAS,
        on another machine, in another container - and then we measure a
        wholly foreign disk. Jellyfin sits by those files, so we ask it.

        The endpoint came only in a newer Jellyfin and is called
        `/System/Info/Storage` (verified on 12.1.0: the server's folders and
        `Libraries` with the libraries' folders, each with FreeSpace and
        UsedSpace). The earlier name `/System/Storage` is tried second - in
        case some version had it. On an older server both return 404 and that
        is NOT an error: we return emptiness and the caller reaches for the
        fallback way. Bringing the sync down because of it would be out of
        proportion.
        """
        for path in ("/System/Info/Storage", "/System/Storage"):
            try:
                response = await self._get(path)
            except JellyfinError as error:
                log.info("Jellyfin does not know %s (%s)", path, error)
                continue
            if response:
                return response
        log.info("Jellyfin does not report disk space; it will be measured another way")
        return {}

    async def image_bytes(
        self, item_id: str, kind: str = "Primary", max_width: int = 400
    ) -> tuple[bytes, str] | None:
        """Downloads an item's image from Jellyfin.

        Images are led through our server, even though the browser could take
        them from Jellyfin directly. The reason: this way neither Jellyfin's
        address nor the API key ever gets into the page. Were the browser to
        fetch the images itself, it would have to know about both.
        """
        return await self._download_image(f"/Items/{item_id}/Images/{kind}",
                                          {"maxWidth": max_width, "quality": 90})

    async def user_image(self, user_id: str, tag: str) -> tuple[bytes, str] | None:
        """A Jellyfin user's profile image. None = there is none or it does not work.

        `GET /UserImage?userId=&tag=` - verified live on 10.11.11, 12.0 and
        12.1 (the old path `/Users/{id}/Images/Primary` is no longer in the API
        description). Jellyfin cannot shrink it, it ignores `maxWidth` here -
        it comes as the user uploaded it; hence the ceiling MAX_IMAGE_BYTES.
        """
        return await self._download_image("/UserImage", {"userId": user_id, "tag": tag})

    async def _download_image(self, path: str,
                              params: dict[str, Any]) -> tuple[bytes, str] | None:
        """One image from Jellyfin - only an allowed type and only up to the ceiling.

        Shared by posters and profile photos, so the same rules apply:
        anything other than an image (SVG, HTML, nothing) or over the ceiling
        is None and the caller shows a substitute. It is downloaded in pieces,
        so a giant file is never read into memory whole - past the ceiling it stops.
        """
        try:
            async with self._client.stream("GET", path, params=params) as response:
                if response.status_code != 200:
                    return None
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type not in IMAGE_TYPES:
                    return None
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        return None
        except httpx.RequestError:
            return None
        return (bytes(content), content_type) if content else None

    async def verify_login(self, username: str, password: str) -> dict[str, Any] | None:
        """Checks a name and a password against Jellyfin. None = they do not match.

        This is the only place where somebody else's password is passed on -
        and it goes to the same server that account lives on, so nowhere else
        than where the person would enter it themselves. It is not stored:
        from the answer we take only the identity and the rights.

        Jellyfin answers 401 when the name or the password does not match;
        that is no connection error, so it is not turned into an exception.
        """
        # Jellyfin wants to know who asks when signing in - without this
        # header it answers 400. `DeviceId` is fixed on purpose: every
        # sign-in would otherwise create its own record in Devices and after
        # a week there would be fifty of them.
        header = self._login_header()
        body = {"Username": username, "Pw": password}
        path = "/Users/AuthenticateByName"
        try:
            response = await self._client.post(
                path, json=body,
                headers={"Content-Type": "application/json",
                         "Authorization": header})
        except httpx.RequestError as error:
            raise JellyfinError("Could not connect to Jellyfin: {error}", error=self._without_key(str(error))) from error

        if response.status_code in (400, 401, 403):
            return None
        if 300 <= response.status_code < 400:
            raise self._redirected(response, path)
        if response.status_code >= 400:
            raise JellyfinError("Jellyfin answered {status}", status=response.status_code)

        data = self._sign_in_answer(response)
        user = data.get("User") or {}
        if not user.get("Id"):
            return None

        # Signing in created an access token in Jellyfin. We do not need it
        # for anything - we only wanted to know whether the password matches -
        # and leaving it there would mean Jellyscope leaves a valid key to
        # somebody else's account hanging behind it. We revoke it at once.
        await self._revoke_token(str(data.get("AccessToken") or ""))
        return _identity(user, username)

    def _login_header(self) -> str:
        """Who asks. Without it Jellyfin refuses the sign-in calls.

        `DeviceId` is fixed and **must be the same** when starting and when
        finishing Quick Connect - Jellyfin pairs the request with the
        confirmation by it.
        """
        from . import __version__

        return ('MediaBrowser Client="Jellyscope", Device="Jellyscope",'
                f' DeviceId="jellyscope-login", Version="{__version__}",'
                f' Token="{self.api_key}"')

    async def quick_connect_enabled(self) -> bool:
        """Does the server have Quick Connect turned on at all?"""
        try:
            response = await self._client.get(
                "/QuickConnect/Enabled",
                headers={"Authorization": self._login_header()})
        except httpx.RequestError:
            return False
        if response.status_code != 200:
            return False
        return str(response.text).strip().lower() == "true"

    async def quick_connect_start(self) -> dict[str, Any] | None:
        """Asks for a code. Returns {"code", "secret"}, or None.

        `secret` (the secret) is what finishes the sign-in - it belongs on
        the server: web/login.py keeps it in the server's memory
        (web/base.py hold_secret), neither in the page nor in the cookie. Only
        `code` (the code) is shown in the window, and by itself it lets nobody
        anywhere: without the confirmation in Jellyfin it is just six digits.
        """
        try:
            response = await self._client.post(
                "/QuickConnect/Initiate",
                headers={"Authorization": self._login_header()})
        except httpx.RequestError as error:
            raise JellyfinError("Could not connect to Jellyfin: {error}", error=self._without_key(str(error))) from error
        if response.status_code != 200:
            return None
        data = self._sign_in_answer(response)
        if not data.get("Secret") or not data.get("Code"):
            return None
        return {"code": str(data["Code"]), "secret": str(data["Secret"])}

    async def quick_connect_confirmed(self, secret: str) -> bool | None:
        """Has the person confirmed it in their Jellyfin yet? None = Jellyfin does not know the code (it expired)."""
        try:
            response = await self._client.get(
                "/QuickConnect/Connect", params={"secret": secret},
                headers={"Authorization": self._login_header()})
        except httpx.RequestError:
            return False
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            return False
        return bool(self._sign_in_answer(response).get("Authenticated"))

    async def quick_connect_finish(
            self, secret: str) -> dict[str, Any] | None:
        """Exchanges a confirmed secret for an identity. Revokes the token at once."""
        try:
            response = await self._client.post(
                "/Users/AuthenticateWithQuickConnect",
                json={"Secret": secret},
                headers={"Content-Type": "application/json",
                         "Authorization": self._login_header()})
        except httpx.RequestError as error:
            raise JellyfinError("Could not connect to Jellyfin: {error}", error=self._without_key(str(error))) from error
        if response.status_code != 200:
            return None

        data = self._sign_in_answer(response)
        user = data.get("User") or {}
        if not user.get("Id"):
            return None
        await self._revoke_token(str(data.get("AccessToken") or ""))
        return _identity(user)

    async def _revoke_token(self, token: str) -> None:
        """Throws away the access token that checking the password created.

        Were it not revoked, a valid key to somebody else's account would stay
        in Jellyfin - one for every sign-in. When revoking fails, it is no
        reason not to let the person in; it is only written down.
        """
        if not token:
            return
        try:
            await self._client.post(
                "/Sessions/Logout",
                headers={"Authorization": f'MediaBrowser Token="{token}"'})
        except httpx.RequestError as error:
            log.warning("could not revoke the sign-in token: %s", error)

    async def items_page(
        self,
        start_index: int,
        limit: int,
        item_types: str = "Movie,Episode",
        user_id: str | None = None,
        parent_id: str | None = None,
        sort_by: str = "SortName",
        sort_order: str = "Ascending",
        fields: str | None = None,
    ) -> dict[str, Any]:
        """One page of library items. `fields` = other fields than ITEM_FIELDS (music).

        A library can have tens of thousands of items. Were we to ask for them
        all at once, Jellyfin and our memory would protest - so we page.
        """
        params: dict[str, Any] = {
            "Recursive": "true",
            "IncludeItemTypes": item_types,
            "Fields": fields or ITEM_FIELDS,
            "StartIndex": start_index,
            "Limit": limit,
            "EnableTotalRecordCount": "true",
            "SortBy": sort_by,
            "SortOrder": sort_order,
            # Jellyfin 12 **hides** movies that are in a collection **behind
            # that collection**: instead of three movies it sends one item of
            # the type BoxSet - and that even through the IncludeItemTypes
            # filter. The movies from collections were then missing in the
            # library and the collection stood in their place. This says "I
            # want the items, not their wrappers"; on 10.x it has no effect.
            # Verified on 12.0.0.
            "CollapseBoxSetItems": "false",
        }
        if parent_id:
            params["ParentId"] = parent_id
        path = f"/Users/{user_id}/Items" if user_id else "/Items"
        return await self._get(path, params) or {}

    async def items_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """Items by concrete ItemIds.

        Used after importing the history: neither Jellystat nor Playback
        Reporting sends the tmdb ID, but they send the ItemId. When Jellyfin
        still knows it, we ask for its ProviderIds and have something to pair
        the records with the library by.

        We ask in batches - an address with thousands of ids would hit the
        limit of the URL length, which every server has different.
        """
        found: list[dict[str, Any]] = []
        for start in range(0, len(ids), 50):
            batch = [i for i in ids[start:start + 50] if i]
            if not batch:
                continue
            data = await self._get("/Items", {
                "Ids": ",".join(batch),
                "Fields": ITEM_FIELDS,
                "Recursive": "true",
                "CollapseBoxSetItems": "false",
            }) or {}
            found.extend(data.get("Items") or [])
        return found

    async def iter_items(
        self,
        page_size: int = 300,
        item_types: str = "Movie,Episode",
        parent_id: str | None = None,
    ):
        """Goes through all items step by step (optionally in one library only).

        `yield` makes this function a **generator**: the items are handed over
        by pages, so the whole library is never in memory at once.
        """
        user_id = None
        start = 0

        while True:
            try:
                page = await self.items_page(start, page_size, item_types, user_id, parent_id)
            except JellyfinError:
                # Some older versions of Jellyfin cannot do /Items without a
                # user. We try once more under the account of the first admin.
                #
                # Not from twelve on: `/Items` is the right path there and
                # `/Users/{id}/Items` is a remnant. When the first fails, it is
                # not the version - it is something else and the fallback
                # attempt would only cover the real error with another.
                if user_id is not None or not old_server(self.version):
                    raise
                user_id = await self._first_admin_id()
                if user_id is None:
                    raise
                continue

            batch = page.get("Items") or []
            if not batch:
                return
            for item in batch:
                yield item

            start += len(batch)
            total = page.get("TotalRecordCount")
            if total is not None and start >= total:
                return

    async def recent_items(self, since: str | None, cap: int = 2000,
                           item_types: str = "Movie,Episode",
                           parent_id: str | None = None) -> list[dict[str, Any]]:
        """Items added since the given time (UTC, the shape "2026-08-14 09:00:00").

        Jellyfin cannot filter by the date added right in the query, so we ask
        for the items **sorted from the newest** and stop reading as soon as
        we come to an older one than we want. Thanks to that exactly what was
        added is downloaded, and not a single item more.

        `cap` is a safeguard for the case that a lot of titles are poured into
        the library at once (the first filling, a bulk import). Without it the
        "quick" sync would download the whole library.
        """
        found: list[dict[str, Any]] = []
        user_id = None
        start = 0
        page_size = 200

        while len(found) < cap:
            try:
                page = await self.items_page(
                    start, page_size, item_types, user_id, parent_id,
                    sort_by="DateCreated", sort_order="Descending")
            except JellyfinError:
                # The same as in `iter_items` - the fallback path only for old
                # servers.
                if user_id is not None or not old_server(self.version):
                    raise
                user_id = await self._first_admin_id()
                if user_id is None:
                    raise
                continue

            batch = page.get("Items") or []
            if not batch:
                break

            for item in batch:
                created = str(item.get("DateCreated") or "")
                # We compare texts, not dates: both shapes start with
                # "YYYY-MM-DD hh:mm", so the alphabetical order matches the
                # time order. Converting to a date would be extra work here.
                #
                # `since = None` means "take the newest, as many as fit" - used
                # on the first run, when we have nothing yet.
                if since and created and created.replace("T", " ")[:19] < since:
                    return found          # only older ones follow
                found.append(item)

            start += len(batch)
            total = page.get("TotalRecordCount")
            if total is not None and start >= total:
                break

        return found[:cap]

    async def item_count(self, item_types: str = "Movie,Episode",
                         parent_id: str | None = None) -> int:
        """How many items the library contains - without downloading them.

        Jellyfin sends `TotalRecordCount` with every page, so asking for a page
        of size zero is enough. Thanks to that it can be said already before
        the sync starts how much work it will be - and a meaningful progress
        indicator shown instead of an endless spinner.
        """
        try:
            page = await self.items_page(0, 0, item_types, None, parent_id)
        except JellyfinError:
            user_id = await self._first_admin_id()
            if user_id is None:
                return 0
            page = await self.items_page(0, 0, item_types, user_id, parent_id)
        return int(page.get("TotalRecordCount") or 0)

    async def _first_admin_id(self) -> str | None:
        for user in await self.users():
            if (user.get("Policy") or {}).get("IsAdministrator"):
                return user.get("Id")
        return None


# ---------------------------------------------------------------------------
# Pulling technical data out of Jellyfin's answers
# ---------------------------------------------------------------------------

def _dynamic_range(video: dict[str, Any]) -> str | None:
    """SDR / HDR / DOVI from what Jellyfin says about the video stream.

    `VideoRange` knows only SDR and HDR, so Dolby Vision hides under it as
    ordinary HDR. Only `VideoRangeType` tells it apart ("DOVI",
    "DOVIWithHDR10" - that is profile 8.1). Without it the overview "SDR /
    HDR / Dolby Vision" always had its third column empty, although the
    library contained DV; and what is worse, the same file counted
    differently depending on where the technical details came from - ffprobe
    tells DV apart.

    An unknown range returns None, not "SDR". Jellyfin can answer "Unknown"
    and that used to be stored as it came, so a column literally named
    "Unknown" appeared in the chart next to our "unknown".
    """
    range_type = str(video.get("VideoRangeType") or "").strip().upper()
    if range_type.startswith("DOVI"):
        return "DOVI"
    if range_type in {"HDR", "HDR10", "HDR10PLUS", "HLG"}:
        return "HDR"
    if range_type == "SDR":
        return "SDR"

    video_range = str(video.get("VideoRange") or "").strip().upper()
    return video_range if video_range in {"HDR", "SDR"} else None


def extract_tech_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Pulls the technical details of the file out of Jellyfin's answer.

    The structure is nested: item -> MediaSources -> MediaStreams -> the
    individual streams (video, audio, subtitles). We care about the first
    video stream and the first audio stream.

    Everywhere we use `.get()` with a default value. A missing field is a
    normal state in real data, not an exceptional situation - and failing
    because of it would be needless.
    """
    sources = item.get("MediaSources") or []
    if not sources:
        return {}
    return tech_from_source(sources[0])


def tech_from_source(source: dict[str, Any]) -> dict[str, Any]:
    """The technical details of one file (one version of a title).

    Separate from `extract_tech_from_item`, because a title can have more
    files - 4K next to 1080p. The item keeps the details of the first one
    (the statistics are counted by it), the others are stored apart and can
    be switched in the detail.
    """
    streams = source.get("MediaStreams") or []

    video = next((s for s in streams if s.get("Type") == "Video"), None)
    audio_streams = [s for s in streams if s.get("Type") == "Audio"]
    subtitle_streams = [s for s in streams if s.get("Type") == "Subtitle"]
    audio = audio_streams[0] if audio_streams else None

    tech: dict[str, Any] = {
        "container": source.get("Container"),
        "size_bytes": source.get("Size"),
        "bitrate": source.get("Bitrate"),
        # We unify the languages right here, so four different spellings of
        # the same language do not get into the database.
        "audio_languages": languages.pack(s.get("Language") for s in audio_streams),
        "subtitle_languages": languages.pack(s.get("Language") for s in subtitle_streams),
        "default_audio_language": languages.normalize(
            (audio or {}).get("Language")
        ) if audio_streams else None,
    }

    if video:
        tech.update({
            "video_codec": video.get("Codec"),
            "width": video.get("Width"),
            "height": video.get("Height"),
            "video_range": _dynamic_range(video),
        })
        # When the container did not give a bitrate, take at least the one of the video stream.
        if not tech.get("bitrate"):
            tech["bitrate"] = video.get("BitRate")

    if audio:
        tech.update({
            "audio_codec": audio.get("Codec"),
            "audio_channels": audio.get("Channels"),
        })

    return tech


def extract_versions(item: dict[str, Any]) -> list[dict[str, Any]]:
    """All versions of a title with their technical details.

    Jellyfin does not join two files of the same movie in a folder into two
    items - it makes one with two `MediaSources`. Without this the second one
    would get lost: the library would say 1080p even though 4K lies beside it.

    We keep the order as Jellyfin sends it - the first is the one it itself
    considers the main one, and the item keeps the same.
    """
    versions: list[dict[str, Any]] = []
    for order, source in enumerate(item.get("MediaSources") or []):
        info = tech_from_source(source)
        info.update({
            "source_id": str(source.get("Id") or "") or None,
            "name": source.get("Name") or None,
            "path": source.get("Path") or None,
            "runtime_ticks": source.get("RunTimeTicks"),
            "position": order,
        })
        if info["source_id"]:
            versions.append(info)
    return versions


def video_range_of(item: dict[str, Any]) -> str | None:
    """The dynamic range from Jellyfin's answer - even when we do not take the rest of the details.

    It is stored next to the measured range, because Dolby Vision in Matroska
    can be read only by ffmpeg 5. An older ffprobe says nothing about it (it
    has neither side data nor a codec tag) and the file looks like ordinary
    HDR - whereas Jellyfin knows it. See stats.tech.RANGE_CASE.
    """
    sources = item.get("MediaSources") or []
    streams = (sources[0].get("MediaStreams") if sources else None) or []
    video = next((s for s in streams if s.get("Type") == "Video"), None)
    return _dynamic_range(video) if video else None


def extract_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Pulls the file's individual streams into the shape we store.

    While `extract_tech_from_item` makes a summary ("which languages are
    there"), here it is about each stream apart - for the item detail, where
    a person wants to see that the second audio stream is Czech 5.1 in AC3.
    """
    sources = item.get("MediaSources") or []
    streams = (sources[0].get("MediaStreams") if sources else None) or []

    result = []
    for index, stream in enumerate(streams):
        stream_type = stream.get("Type")
        if stream_type not in ("Video", "Audio", "Subtitle"):
            continue
        result.append({
            "stream_index": stream.get("Index", index),
            "type": stream_type,
            "codec": stream.get("Codec"),
            "language": languages.normalize(stream.get("Language")),
            "title": stream.get("Title") or stream.get("DisplayTitle"),
            "channels": stream.get("Channels"),
            "channel_layout": stream.get("ChannelLayout"),
            "width": stream.get("Width"),
            "height": stream.get("Height"),
            "bitrate": stream.get("BitRate"),
            "is_default": 1 if stream.get("IsDefault") else 0,
            "is_forced": 1 if stream.get("IsForced") else 0,
            "is_external": 1 if stream.get("IsExternal") else 0,
        })
    return result


def media_streams(item: dict[str, Any]) -> list[dict[str, Any]]:
    """An item's list of streams, wherever Jellyfin sends them.

    Here is the catch: `/Items` returns the streams wrapped in `MediaSources`,
    but `/Sessions` sends them **right in the item** as `MediaStreams`. Code
    that looks only in `MediaSources` thus finds nothing for a session that is
    playing right now - and the Overview lacks the language, codec and
    bitrate. With transcoding it did not show, because there the details are
    taken from `TranscodingInfo`.
    """
    sources = item.get("MediaSources") or []
    if sources:
        streams = sources[0].get("MediaStreams") or []
        if streams:
            return streams
    return item.get("MediaStreams") or []


def source_bitrate(item: dict[str, Any]) -> int | None:
    """The source's bitrate - it can come from two places too."""
    sources = item.get("MediaSources") or []
    if sources and sources[0].get("Bitrate"):
        return sources[0]["Bitrate"]
    video = next((s for s in media_streams(item) if s.get("Type") == "Video"), {})
    return video.get("BitRate") or item.get("Bitrate")


def video_dimensions(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """The dimensions of the picture playing right now. Returns (width, height)."""
    video = next((s for s in media_streams(item) if s.get("Type") == "Video"), {})
    width = video.get("Width") or item.get("Width")
    height = video.get("Height") or item.get("Height")
    return (int(width) if width else None, int(height) if height else None)


def selected_languages(session: dict[str, Any], item: dict[str, Any]) -> dict[str, str | None]:
    """Finds out which audio stream and subtitles the viewer really played.

    The difference from `extract_tech_from_item` is essential: there we find
    out what is **available** in the file. Here, what the person **chose**.
    Only the second number says what language they really watch in.

    Jellyfin reports it as a stream number (`AudioStreamIndex`), which has to
    be found in the list of streams under the key `Index`. The order in the
    list is not the same as Index - so we look by the value, not by the position.
    """
    play_state = session.get("PlayState") or {}
    streams = media_streams(item)

    def language_of(index: Any, stream_type: str) -> str | None:
        if index is None or index < 0:
            return None
        stream = next(
            (s for s in streams if s.get("Index") == index and s.get("Type") == stream_type),
            None,
        )
        if stream is None:
            return None
        return languages.normalize(stream.get("Language"))

    audio_language = language_of(play_state.get("AudioStreamIndex"), "Audio")

    # When the player does not report the chosen stream, we take the file's
    # default stream - that is what would play by itself.
    if audio_language is None:
        audio_streams = [s for s in streams if s.get("Type") == "Audio"]
        default = next((s for s in audio_streams if s.get("IsDefault")), None)
        if default is None and audio_streams:
            default = audio_streams[0]
        if default is not None:
            audio_language = languages.normalize(default.get("Language"))

    return {
        "audio_language": audio_language,
        "subtitle_language": language_of(play_state.get("SubtitleStreamIndex"), "Subtitle"),
    }
