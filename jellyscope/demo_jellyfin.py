"""The demo's made-up Jellyfin: only what the made-up apps' syncs ask it.

The demo's library (video, music, books) is written straight into the database (demodata) - no Jellyfin is
asked for it. Sonarr's sync, though, pairs its series with Jellyfin's by their TVDB and TMDB ids
(arr/sonarr/titles.py), and in the demo it is to do so through its real code. So this answers from the
demo's library (demo_world.series):

* `GET /Items` with `IncludeItemTypes=Series` - the series with their `Id` and `ProviderIds` (Tvdb, Tmdb),
  paged by `StartIndex` and `Limit`, with `TotalRecordCount` and `StartIndex`;
* `GET /System/Info` and `/System/Info/Public` - the server's info, Jellyfin 10.11;
* `POST /Users/AuthenticateByName` - 401, Jellyfin's answer to a wrong name or password: nobody signs in through
  the made-up Jellyfin, and the sign-in page says only that (an error of Jellyfin would be a warning in the log).

Everything else is 404, other writes too: a library sync asking for movies or episodes fails instead of reading
an empty library (it would take the demo's library for deleted).

demo_apps.load_fakes registers it in the switch under jellyfin.DEMO_NAME and the seed connects it
(demo_apps.seed_apps); outside the demo mode it is never used (demo_switch). It names no app.
"""
from __future__ import annotations

from typing import Any

import httpx

from . import demo_world

# What the made-up server says it is: the latest 10.x (10.11), the generation the demo's code paths know best.
VERSION = "10.11.11"
SERVER_NAME = "Jellyfin"
SERVER_ID = demo_world.jellyfin_id("jellyfin-server")
# Jellyfin's paths do not care about case (ASP.NET): compared in lower case.
_ITEMS = "/items"
_INFO = "/system/info"
_PUBLIC_INFO = "/system/info/public"
_SIGN_IN = "/users/authenticatebyname"


def _whole(value: str | None, fallback: int | None) -> int | None:
    """A non-negative whole number from the query, else `fallback`."""
    try:
        number = int(value or "")
    except ValueError:
        return fallback
    return number if number >= 0 else fallback


def _info(public: bool) -> dict[str, Any]:
    """The server's info. The public part is what a client sees before signing in."""
    info: dict[str, Any] = {"ServerName": SERVER_NAME, "Version": VERSION, "ProductName": "Jellyfin Server",
                            "OperatingSystem": "", "Id": SERVER_ID, "StartupWizardCompleted": True}
    if not public:
        info.update({"OperatingSystem": "Linux", "OperatingSystemDisplayName": "Linux", "HasPendingRestart": False,
                     "IsShuttingDown": False, "SupportsLibraryMonitor": True})
    return info


def _series_page(params: httpx.QueryParams) -> dict[str, Any]:
    """One page of the demo's series, as Jellyfin's /Items gives it."""
    start = _whole(params.get("StartIndex"), 0) or 0
    limit = _whole(params.get("Limit"), None)
    catalog = demo_world.series()
    page = catalog[start:] if limit is None else catalog[start:start + limit]
    items = [{"Name": entry["title"], "ServerId": SERVER_ID, "Id": entry["jellyfin_id"], "Type": "Series",
              "IsFolder": True, "ProductionYear": entry["year"],
              "ProviderIds": {"Tvdb": entry["tvdb_id"], "Tmdb": entry["tmdb_id"]}}
             for entry in page]
    return {"Items": items, "TotalRecordCount": len(catalog), "StartIndex": start}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Jellyfin's answer to one request (httpx.MockTransport)."""
    path = request.url.path.rstrip("/").lower()
    if request.method == "POST" and path == _SIGN_IN:
        return httpx.Response(401)
    if request.method == "GET" and path in (_INFO, _PUBLIC_INFO):
        return httpx.Response(200, json=_info(path == _PUBLIC_INFO))
    if request.method == "GET" and path == _ITEMS:
        kinds = {kind.strip().lower() for kind in request.url.params.get("IncludeItemTypes", "").split(",") if kind.strip()}
        if kinds == {"series"}:
            return httpx.Response(200, json=_series_page(request.url.params))
    return httpx.Response(404)
