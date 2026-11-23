"""Shared helpers of the tests of the *Arr page's apps and of Seerr.

The apps do not run in the tests: their answers are recorded from a real Sonarr, Radarr, Bazarr,
Prowlarr and Seerr (tests/<app>_responses/) and the client is substituted with one using
httpx.MockTransport. Every app has its own part here with the same four helpers:
`<app>_handler` (the recorded app), `<app>_client` (a class in place of the client),
`connect_<app>` and `fill_<app>`. A test of one app takes only the helpers of its app (guarded by
tests/test_arr_tree.py); what is above them is for all.

The file starts with an underscore, so the suite does not run it as a test. The `jellyscope`
package is imported only inside functions: the test sets up its environment (database, session
key) and must manage that before the first import.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

PROJECT = Path(__file__).resolve().parent.parent
SONARR_RESPONSES = PROJECT / "tests" / "sonarr_responses"
RADARR_RESPONSES = PROJECT / "tests" / "radarr_responses"
LIDARR_RESPONSES = PROJECT / "tests" / "lidarr_responses"
READARR_RESPONSES = PROJECT / "tests" / "readarr_responses"
SEERR_RESPONSES = PROJECT / "tests" / "seerr_responses"
BAZARR_RESPONSES = PROJECT / "tests" / "bazarr_responses"
PROWLARR_RESPONSES = PROJECT / "tests" / "prowlarr_responses"

Handler = Callable[[httpx.Request], httpx.Response]

# Requests that went to the client of an app the test did not fake (see no_network). Must stay empty.
UNFAKED_REQUESTS: list[str] = []
_REAL_EXIT = sys.exit         # `sys.exit` from before no_network(), see _exit_with_check


def recorded_response(folder: Path, name: str) -> Any:
    """A recorded answer of an app (JSON from the folder of answers)."""
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


def fake_jellyfin_series(series: list[dict[str, Any]], calls: list[str] | None = None, error: Exception | None = None) -> type:
    """A class in place of JellyfinClient that answers a request for series with a prepared list.

    `series` are items as Jellyfin sends them: `{"Id": ..., "ProviderIds": {"Tvdb": ..., "Tmdb":
    ..., "Imdb": ...}}`. *Arr pairs its series with the library by them. `calls` collects the item
    types somebody asked for; `error` is raised instead of an answer (Jellyfin unavailable).
    """
    class Client:
        last_timeout: Any = None      # the timeout it was created with last

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            Client.last_timeout = kwargs.get("timeout")

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def iter_items(self, page_size: int = 300, item_types: str = "Movie,Episode", parent_id: str | None = None):
            if calls is not None:
                calls.append(item_types)
            if error is not None:
                raise error
            for one in series if item_types == "Series" else []:
                yield one
    return Client


def not_responding(attempts: list[str] | None = None) -> Handler:
    """An app that does not accept the connection. `attempts` collects the paths tried."""
    def handler(request: httpx.Request) -> httpx.Response:
        if attempts is not None:
            attempts.append(request.url.path.rsplit("/api/", 1)[-1].split("/", 1)[-1])
        raise httpx.ConnectError("Connection refused", request=request)
    return handler


def unfaked(app: str) -> Handler:
    """A handler for an app the test did not fake: the first request is the test's error, not a quiet way to the network."""
    def handler(request: httpx.Request) -> httpx.Response:
        UNFAKED_REQUESTS.append(f"{app} {request.method} {request.url.path}")
        raise AssertionError(f"the test did not fake the client of {app}: {request.method} {request.url.path}")
    return handler


def _exit_with_check(code: Any = 0) -> None:
    """The test's `sys.exit` that first reports requests to apps the test did not fake.

    Such a request fails with AssertionError by itself, but code sometimes swallows it on purpose
    (the sync of all apps must not stop because of one) and the test would reach "DONE - failures:
    0" (or "HOTOVO - chyb: 0"). Every test ends with `sys.exit(...)`, so it is caught here: a FAIL
    line (on the output and on the error output) and exit code 1. Exiting is still a plain
    `sys.exit` - cleanup registered with atexit runs as always. Whoever tries an unfaked request on
    purpose clears the list after it.
    """
    if UNFAKED_REQUESTS:
        message = f"FAIL   the test asked an app it did not fake: {UNFAKED_REQUESTS[:5]}"
        print(message, flush=True)
        print(message, file=sys.stderr, flush=True)
        code = code or 1
    _REAL_EXIT(code)


def _fake_client(client_class: type, handler: Handler) -> type:
    """A class in place of an app's client that talks to the handler instead of the network.

    It always derives from the real client, even when another fake is substituted in the package
    at the moment: a fake of a fake would talk to the older one's handler.
    """
    real = getattr(client_class, "_real", client_class)

    class Client(real):       # type: ignore[misc, valid-type]
        _real = real

        def __init__(self, url: str, key: str, transport: Any = None, timeout: Any = None) -> None:
            # A transport the test passed itself (a connection test with its own answer) wins.
            super().__init__(url, key, transport=transport or httpx.MockTransport(handler), timeout=timeout)
    return Client


def no_network() -> None:
    """The clients of all apps fail on their first request until the test fakes them its own way.

    A test of one app thus never talks to the real network just because the code unexpectedly asked
    another app. It is called before the test's own fakes; what went wrong is in
    `UNFAKED_REQUESTS` - and should anything stay there at the end of the test, the test ends with
    an error (`_exit_with_check`).
    """
    import importlib

    sys.exit = _exit_with_check

    from _package import substitute

    for path, class_name in (("jellyscope.arr.sonarr", "SonarrClient"), ("jellyscope.arr.radarr", "RadarrClient"),
                             ("jellyscope.arr.lidarr", "LidarrClient"), ("jellyscope.arr.readarr", "ReadarrClient"),
                             ("jellyscope.arr.bazarr", "BazarrClient"), ("jellyscope.arr.prowlarr", "ProwlarrClient"),
                             ("jellyscope.seerr", "SeerrClient")):
        package = importlib.import_module(path)
        substitute(package, class_name, _fake_client(getattr(package, class_name), unfaked(path.rsplit(".", 1)[-1])))


def login(name: str, password: str = "longpassword") -> Any:
    """A signed-in TestClient of a local account."""
    from fastapi.testclient import TestClient

    from jellyscope import web

    client = TestClient(web.app)
    client.post("/login", data={"username": name, "password": password, "method": "local"})
    return client


def card_html(html: str, key: str) -> str:
    """The cut of a page with one card: from its mark to the next card's mark.

    _card.html writes the mark `<!-- card: key -->` before every card. The cut ends at the next
    card, at the latest at the first dialog (`<dialog`) or the end of the content - otherwise the
    last card would "contain" the offer of cards in the layout dialog too and a test would find
    anything in it.
    """
    start = html.find(f"<!-- card: {key} -->")
    if start < 0:
        return ""
    ends = [html.find(mark, start + 1) for mark in ("<!-- card: ", "<dialog", "</main>")]
    return html[start:min([e for e in ends if e > 0], default=len(html))]


def tile(html: str, label: str) -> str | None:
    """The value of the tile with the given label (the tile macro), or None when it is not there."""
    found = re.search(re.escape(label) + r'</p>\s*<div class="tile-value">([^<]+)</div>', html)
    return found.group(1).strip() if found else None


# ---------------------------------------------------------------------------
# Sonarr
# ---------------------------------------------------------------------------

# API v3 path -> name of the recorded Sonarr answer (tests/sonarr_responses/).
SONARR_PATHS = {"history/since": "history_since", "queue": "queue", "wanted/missing": "wanted_missing",
                "wanted/cutoff": "wanted_cutoff", "calendar": "calendar", "health": "health",
                "system/status": "system_status", "series": "series"}


def sonarr_handler(requests: list[str] | None = None, instead: dict[str, httpx.Response] | None = None) -> Handler:
    """The recorded Sonarr as an httpx.MockTransport handler.

    Every API path gets the real recorded answer. `requests` collects the paths asked for (to count
    how often); `instead` is {path: answer} for paths that should answer otherwise (error 500, empty
    health). The whole queue for the "Stahuje se" card (`queue` with `pageSize=200`) gets the
    recording with download progress, named `queue/cela` in `requests`.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v3/", 1)[-1]
        # The queue is read by two parts: the minute live state (tiles, a queue with an error) and the
        # live "Stahuje se" card - it asks for the whole queue in one page of up to 200 records. It has
        # its own name in `requests`, so the tests of the live state's cache still count only theirs.
        whole_queue = path == "queue" and request.url.params.get("pageSize") == "200"
        if requests is not None:
            requests.append("queue/cela" if whole_queue else path)
        if instead and path in instead:
            return instead[path]
        if whole_queue:
            return httpx.Response(200, json=recorded_response(SONARR_RESPONSES, "queue_stahuje"))
        if request.method == "POST" and path == "command":      # the command to refresh downloads
            return httpx.Response(201, json={"id": 1, "name": "RefreshMonitoredDownloads", "status": "queued"})
        if path == "history":          # the first sync goes page by page (all of it on one page)
            history = recorded_response(SONARR_RESPONSES, "history_since")
            return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": len(history),
                                             "records": history})
        if path not in SONARR_PATHS:
            return httpx.Response(404)
        # The whole list of series even when the request asks for one id (`series?tvdbId=`): older
        # versions of Sonarr answer so and the client picks the right record itself.
        return httpx.Response(200, json=recorded_response(SONARR_RESPONSES, SONARR_PATHS[path]))
    return handler


def sonarr_client(handler: Handler) -> type:
    """A class in place of sonarr.SonarrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(sonarr, "SonarrClient", sonarr_client(...))`.
    """
    from jellyscope.arr.sonarr import client

    return _fake_client(client.SonarrClient, handler)


def connect_sonarr(key: str = "tajny-klic") -> None:
    """Switch Sonarr on in Settings (address http://sonarr.test)."""
    from jellyscope.arr import sonarr

    error = sonarr.save_settings(True, "http://sonarr.test", key)
    if error:
        raise RuntimeError(error)


def fill_sonarr() -> None:
    """Fetch Sonarr's history into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope.arr import sonarr

    asyncio.run(sonarr.synchronize())


# ---------------------------------------------------------------------------
# Radarr
# ---------------------------------------------------------------------------

# API v3 path -> name of the recorded Radarr answer (tests/radarr_responses/).
RADARR_PATHS = {"history/since": "history_since", "queue": "queue", "wanted/missing": "wanted_missing",
                "wanted/cutoff": "wanted_cutoff", "calendar": "calendar", "health": "health",
                "system/status": "system_status", "movie": "movie"}


def radarr_handler(requests: list[str] | None = None, instead: dict[str, httpx.Response] | None = None) -> Handler:
    """The recorded Radarr as an httpx.MockTransport handler.

    Every API path gets the real recorded answer. `requests` collects the paths asked for (to count
    how often); `instead` is {path: answer} for paths that should answer otherwise (error 500, empty
    health). The whole queue for the "Stahuje se" card (`queue` with `pageSize=200`) gets the
    recording with download progress, named `queue/cela` in `requests`.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v3/", 1)[-1]
        # The queue is read by two parts: the minute live state (tiles, a queue with an error) and the
        # live "Stahuje se" card - it asks for the whole queue in one page of up to 200 records. It has
        # its own name in `requests`, so the tests of the live state's cache still count only theirs.
        whole_queue = path == "queue" and request.url.params.get("pageSize") == "200"
        if requests is not None:
            requests.append("queue/cela" if whole_queue else path)
        if instead and path in instead:
            return instead[path]
        if whole_queue:
            return httpx.Response(200, json=recorded_response(RADARR_RESPONSES, "queue_stahuje"))
        if request.method == "POST" and path == "command":      # the command to refresh downloads
            return httpx.Response(201, json={"id": 1, "name": "RefreshMonitoredDownloads", "status": "queued"})
        if path == "history":          # the first sync goes page by page (all of it on one page)
            history = recorded_response(RADARR_RESPONSES, "history_since")
            return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": len(history),
                                             "records": history})
        if path not in RADARR_PATHS:
            return httpx.Response(404)
        # The whole list of movies even when the request asks for one id (`movie?tmdbId=`): older
        # versions of Radarr answer so and the client picks the right record itself.
        return httpx.Response(200, json=recorded_response(RADARR_RESPONSES, RADARR_PATHS[path]))
    return handler


def radarr_client(handler: Handler) -> type:
    """A class in place of radarr.RadarrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(radarr, "RadarrClient", radarr_client(...))`.
    """
    from jellyscope.arr.radarr import client

    return _fake_client(client.RadarrClient, handler)


def connect_radarr(key: str = "tajny-klic") -> None:
    """Switch Radarr on in Settings (address http://radarr.test)."""
    from jellyscope.arr import radarr

    error = radarr.save_settings(True, "http://radarr.test", key)
    if error:
        raise RuntimeError(error)


def fill_radarr() -> None:
    """Fetch Radarr's history into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope.arr import radarr

    asyncio.run(radarr.synchronize())


# ---------------------------------------------------------------------------
# Lidarr
# ---------------------------------------------------------------------------

# API v1 path -> name of the recorded Lidarr answer (tests/lidarr_responses/).
LIDARR_PATHS = {"history/since": "history_since", "system/status": "system_status", "album": "album", "artist": "artist",
                "queue": "queue", "wanted/missing": "wanted_missing", "wanted/cutoff": "wanted_cutoff",
                "calendar": "calendar", "health": "health"}


def lidarr_handler(requests: list[str] | None = None, instead: dict[str, httpx.Response] | None = None) -> Handler:
    """The recorded Lidarr as an httpx.MockTransport handler.

    Every API path gets its recorded answer. `requests` collects the paths asked for (to count how
    often); `instead` is {path: answer} for paths that should answer otherwise (error 500, an empty
    list). The first sync reads the history page by page (`history`): it gets all of it on one page.
    The whole queue for the "Stahuje se" card (`queue` with `pageSize=200`) gets the recording with
    download progress and is named `queue/whole` in `requests`, so the tests of the live state's cache
    count only their own reads.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v1/", 1)[-1]
        whole_queue = path == "queue" and request.url.params.get("pageSize") == "200"
        if requests is not None:
            requests.append("queue/whole" if whole_queue else path)
        if instead and path in instead:
            return instead[path]
        if whole_queue:
            return httpx.Response(200, json=recorded_response(LIDARR_RESPONSES, "queue_downloading"))
        if request.method == "POST" and path == "command":      # the command to refresh downloads
            return httpx.Response(201, json={"id": 1, "name": "RefreshMonitoredDownloads", "status": "queued"})
        if path == "history":
            history = recorded_response(LIDARR_RESPONSES, "history_since")
            return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": len(history),
                                             "records": history})
        if path not in LIDARR_PATHS:
            return httpx.Response(404)
        return httpx.Response(200, json=recorded_response(LIDARR_RESPONSES, LIDARR_PATHS[path]))
    return handler


def lidarr_client(handler: Handler) -> type:
    """A class in place of lidarr.LidarrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(lidarr, "LidarrClient", lidarr_client(...))`.
    """
    from jellyscope.arr.lidarr import client

    return _fake_client(client.LidarrClient, handler)


def connect_lidarr(key: str = "tajny-klic") -> None:
    """Switch Lidarr on in Settings (address http://lidarr.test)."""
    from jellyscope.arr import lidarr

    error = lidarr.save_settings(True, "http://lidarr.test", key)
    if error:
        raise RuntimeError(error)


def fill_lidarr() -> None:
    """Fetch Lidarr's history into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope.arr import lidarr

    asyncio.run(lidarr.synchronize())


# ---------------------------------------------------------------------------
# Readarr
# ---------------------------------------------------------------------------

# API v1 path -> name of the recorded Readarr answer (tests/readarr_responses/).
READARR_PATHS = {"history/since": "history_since", "system/status": "system_status", "book": "book", "author": "author", "edition": "edition",
                 "queue": "queue", "wanted/missing": "wanted_missing", "wanted/cutoff": "wanted_cutoff",
                 "calendar": "calendar", "health": "health"}


def readarr_handler(requests: list[str] | None = None, instead: dict[str, httpx.Response] | None = None) -> Handler:
    """The recorded Readarr as an httpx.MockTransport handler.

    Every API path gets its recorded answer. `requests` collects the paths asked for (to count how
    often); `instead` is {path: answer} for paths that should answer otherwise (error 500, an empty
    list). The first sync reads the history page by page (`history`): it gets all of it on one page.
    The whole queue for the "Stahuje se" card (`queue` with `pageSize=200`) gets the recording with
    download progress and is named `queue/whole` in `requests`, so the tests of the live state's cache
    count only their own reads.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v1/", 1)[-1]
        whole_queue = path == "queue" and request.url.params.get("pageSize") == "200"
        if requests is not None:
            requests.append("queue/whole" if whole_queue else path)
        if instead and path in instead:
            return instead[path]
        if whole_queue:
            return httpx.Response(200, json=recorded_response(READARR_RESPONSES, "queue_downloading"))
        if request.method == "POST" and path == "command":      # the command to refresh downloads
            return httpx.Response(201, json={"id": 1, "name": "RefreshMonitoredDownloads", "status": "queued"})
        if path == "history":
            history = recorded_response(READARR_RESPONSES, "history_since")
            return httpx.Response(200, json={"page": 1, "pageSize": 1000, "totalRecords": len(history),
                                             "records": history})
        if path not in READARR_PATHS:
            return httpx.Response(404)
        answer = recorded_response(READARR_RESPONSES, READARR_PATHS[path])
        if path == "edition":     # only the editions of the books asked about (`bookId` is repeated)
            wanted = request.url.params.get_list("bookId")
            answer = [e for e in answer if str(e["bookId"]) in wanted]
        return httpx.Response(200, json=answer)
    return handler


def readarr_client(handler: Handler) -> type:
    """A class in place of readarr.ReadarrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(readarr, "ReadarrClient", readarr_client(...))`.
    """
    from jellyscope.arr.readarr import client

    return _fake_client(client.ReadarrClient, handler)


def connect_readarr(key: str = "tajny-klic") -> None:
    """Switch Readarr on in Settings (address http://readarr.test)."""
    from jellyscope.arr import readarr

    error = readarr.save_settings(True, "http://readarr.test", key)
    if error:
        raise RuntimeError(error)


def fill_readarr() -> None:
    """Fetch Readarr's history into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope.arr import readarr

    asyncio.run(readarr.synchronize())


# ---------------------------------------------------------------------------
# Seerr
# ---------------------------------------------------------------------------

def seerr_handler(requests: list[str] | None = None,
                  instead: dict[str, httpx.Response] | None = None) -> Handler:
    """The recorded Seerr as an httpx.MockTransport handler.

    `request` returns the recorded requests (with `filter=pending` only the pending ones, newest
    first like Overseerr, which ignores `sortDirection`), `movie/<id>` and `tv/<id>` the title
    ("Film 603", "Seriál 70523"). `requests` collects paths as "path?filter", `instead` is
    {path: answer} for paths that should answer otherwise.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v1/", 1)[-1]
        if requests is not None:
            requests.append(f"{path}?{request.url.params.get('filter', '')}")
        if instead and path in instead:
            return instead[path]
        if path == "request":
            if request.url.params.get("filter") == "pending":
                pending = recorded_response(SEERR_RESPONSES, "request_pending")
                return httpx.Response(200, json={**pending, "results": pending["results"][::-1]})
            return httpx.Response(200, json=recorded_response(SEERR_RESPONSES, "request"))
        if path.startswith(("movie/", "tv/")):
            number = path.split("/")[1]
            return httpx.Response(200, json={"title": f"Film {number}", "name": f"Seriál {number}"})
        if path == "settings/about":
            return httpx.Response(200, json=recorded_response(SEERR_RESPONSES, "settings_about"))
        return httpx.Response(404)
    return handler


def seerr_client(handler: Handler) -> type:
    """A class in place of seerr.SeerrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(seerr, "SeerrClient", seerr_client(...))`.
    """
    from jellyscope.seerr import client

    return _fake_client(client.SeerrClient, handler)


def connect_seerr(key: str = "tajny-klic") -> None:
    """Switch Seerr on in Settings (address http://seerr.test)."""
    from jellyscope import seerr

    error = seerr.save_settings(True, "http://seerr.test", key)
    if error:
        raise RuntimeError(error)


def fill_seerr() -> None:
    """Fetch requests into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope import seerr

    asyncio.run(seerr.synchronize())


# ---------------------------------------------------------------------------
# Bazarr
# ---------------------------------------------------------------------------

BAZARR_PATHS = {"system/status": "system_status", "badges": "badges", "providers": "providers", "system/health": "system_health",
                "episodes/history": "episodes_history", "movies/history": "movies_history",
                "episodes/wanted": "episodes_wanted", "movies/wanted": "movies_wanted"}


def bazarr_handler(requests: list[str] | None = None, instead: dict[str, Any] | None = None) -> Handler:
    """The recorded Bazarr as an httpx.MockTransport handler.

    History and wanted are paged as in the real Bazarr (`start`, `length`; `total` is always the
    whole count). `requests` collects "path?start", `instead` is {path: an httpx.Response or data}
    for paths that should answer otherwise (data is paged the same as the recorded).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/", 1)[-1]
        if requests is not None:
            requests.append(f"{path}?{request.url.params.get('start', '')}")
        replacement = (instead or {}).get(path)
        if isinstance(replacement, httpx.Response):
            return replacement
        if path not in BAZARR_PATHS:
            return httpx.Response(404)
        data = replacement if replacement is not None else recorded_response(BAZARR_RESPONSES, BAZARR_PATHS[path])
        if path.endswith(("/history", "/wanted")):
            start, length = int(request.url.params.get("start", 0)), int(request.url.params.get("length", -1))
            rows = data["data"][start:start + length] if length > 0 else data["data"]
            data = {"data": rows, "total": data.get("total", len(data["data"]))}
        return httpx.Response(200, json=data)
    return handler


def bazarr_client(handler: Handler) -> type:
    """A class in place of bazarr.BazarrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(bazarr, "BazarrClient", bazarr_client(...))`.
    """
    from jellyscope.arr.bazarr import client

    return _fake_client(client.BazarrClient, handler)


def connect_bazarr(key: str = "tajny-klic") -> None:
    """Switch Bazarr on in Settings (address http://bazarr.test)."""
    from jellyscope.arr import bazarr

    error = bazarr.save_settings(True, "http://bazarr.test", key)
    if error:
        raise RuntimeError(error)


def fill_bazarr() -> None:
    """Fetch the subtitle history into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope.arr import bazarr

    asyncio.run(bazarr.synchronize())


# ---------------------------------------------------------------------------
# Prowlarr
# ---------------------------------------------------------------------------

PROWLARR_PATHS = {"system/status": "system_status", "indexer": "indexer", "indexerstatus": "indexerstatus",
                  "applications": "applications", "health": "health"}


def prowlarr_history() -> list[dict[str, Any]]:
    """The whole recorded history of Prowlarr (both pages), from the oldest event."""
    return [*recorded_response(PROWLARR_RESPONSES, "history_strana1")["records"],
            *recorded_response(PROWLARR_RESPONSES, "history_strana2")["records"]]


def prowlarr_handler(requests: list[str] | None = None, instead: dict[str, Any] | None = None) -> Handler:
    """The recorded Prowlarr as an httpx.MockTransport handler.

    `history` is paged as in the real Prowlarr: `page`, `pageSize`, sorted by date in the direction
    `sortDirection` (by id when the time is the same), `totalRecords` is the whole count and a page
    past the end is empty. `requests` collects "path?page", `instead` is {path: an httpx.Response or
    data} - under the key `history` a list of events that replaces the recorded history (read again
    on every request, a test may change it between pages).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/api/v1/", 1)[-1]
        params = request.url.params
        if requests is not None:
            requests.append(f"{path}?{params.get('page') or ''}")
        replacement = (instead or {}).get(path)
        if isinstance(replacement, httpx.Response):
            return replacement
        if path == "history":
            direction = str(params.get("sortDirection") or "descending")
            events = sorted(prowlarr_history() if replacement is None else replacement, key=lambda e: (str(e["date"]), e["id"]),
                            reverse=direction == "descending")
            page, size = int(params.get("page", 1)), int(params.get("pageSize", 10))
            return httpx.Response(200, json={"page": page, "pageSize": size, "sortKey": "date", "sortDirection": direction,
                                             "totalRecords": len(events), "records": events[(page - 1) * size:page * size]})
        if path not in PROWLARR_PATHS:
            return httpx.Response(404)
        return httpx.Response(200, json=replacement if replacement is not None else recorded_response(PROWLARR_RESPONSES, PROWLARR_PATHS[path]))
    return handler


def prowlarr_client(handler: Handler) -> type:
    """A class in place of prowlarr.ProwlarrClient that talks to the handler instead of the network.

    Substituted through `_package.substitute(prowlarr, "ProwlarrClient", prowlarr_client(...))`.
    """
    from jellyscope.arr.prowlarr import client

    return _fake_client(client.ProwlarrClient, handler)


def connect_prowlarr(key: str = "tajny-klic") -> None:
    """Switch Prowlarr on in Settings (address http://prowlarr.test)."""
    from jellyscope.arr import prowlarr

    error = prowlarr.save_settings(True, "http://prowlarr.test", key)
    if error:
        raise RuntimeError(error)


def fill_prowlarr() -> None:
    """Fetch Prowlarr's history into the database (a sync through the faked client)."""
    import asyncio

    from jellyscope.arr import prowlarr

    asyncio.run(prowlarr.synchronize())
