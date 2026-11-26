"""Network: the page, GeoIP and the map (the detailed map, tiles).

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import detailed_map, formatting, geoip, scanner
from .base import _context, _flash, require_admin, require_login, spawn, templates
from .dashboards import _registry_page
from .periods import _days

router = APIRouter()

# The page's cards from the registry, in the order of network.html.
NETWORK_CARDS = ("network_numbers", "live_traffic", "peaks_by_day", "who_streamed", "by_player", "origin", "map")


@router.get("/network", response_class=HTMLResponse)
def network_page(request: Request, days: int | None = None,
                 since: str | None = None, until: str | None = None,
                 account: dict[str, Any] = Depends(require_login)):
    """Network - how much data flowed from the server to the players.

    Everything is computed from `playback.bitrate`, which the collector saves with
    every playback. It needs no new collecting - only another question over the
    data already there.
    """
    days = _days(request, days, since, until)
    # The page is put together from the Network cards: the registry draws them and computes their data.
    return templates.TemplateResponse(request, "network.html", _context(
        request, account, days=days, **_registry_page(request, account, NETWORK_CARDS, days)))


@router.post("/network/geoip")
async def network_geoip(request: Request,
                        account: dict[str, Any] = Depends(require_admin)):
    """Downloads (or refreshes) the GeoLite2 database for the map.

    The only action in the app that reaches elsewhere than Jellyfin - and only on
    a click. Hence the administrator may start it, not everybody signed in.
    """
    if not geoip.library_installed():
        _flash(request, "The maxminddb library is missing, and the database cannot be read without it.",
               "error")
        return RedirectResponse("/network#map", status_code=303)

    result = await geoip.download()
    if result.get("status") == "ok":
        _flash(request, "The GeoLite2 database has been downloaded ({size}).", "success",
               size=formatting.bytes_human(result["byte_count"]))
    else:
        _flash(request, "The download failed: {reason}", "error",
               reason=result.get("message", "?"))
    return RedirectResponse("/network#map", status_code=303)


@router.post("/network/detailed-map")
async def network_detailed_map(request: Request,
                               account: dict[str, Any] = Depends(require_admin)):
    """Downloads the detailed map (Natural Earth 1:10M) - a background task.

    It takes a few minutes (downloading about 75 MB and cutting into tiles), so it
    is only fired off and the page waits (`?wait=task`). Under the task lock - when
    another one (or this one) is running, a second does not start.
    """
    if scanner.is_scan_running():
        _flash(request, "Another task is already running, wait for it to finish.", "warning")
        return RedirectResponse("/network#map", status_code=303)
    spawn(detailed_map.start_task(), "detailed map")
    _flash(request, "The detailed map download has started. It takes a few minutes; the page refreshes by itself.",
           "info")
    return RedirectResponse("/network?wait=task#map", status_code=303)


@router.post("/network/detailed-map/delete")
def network_detailed_map_delete(request: Request,
                                account: dict[str, Any] = Depends(require_admin)):
    """Deletes the detailed map - the map goes back to the built-in outline.

    Not during a task: the download would write the map again once finished.
    """
    if scanner.is_scan_running():
        _flash(request, "Another task is already running, wait for it to finish.", "warning")
        return RedirectResponse("/network#map", status_code=303)
    detailed_map.delete()
    _flash(request, "The detailed map is deleted.", "success")
    return RedirectResponse("/network#map", status_code=303)


@router.get("/network/map/tiles/{detail}/{x}/{y}")
def network_map_tile(request: Request, detail: str, x: str, y: str,
                     account: dict[str, Any] = Depends(require_login)):
    """One tile of the detailed map - JSON with SVG paths by layers.

    The coordinates are read as text and checked by hand: nonsense (negative,
    outside the grid, not a number) is a 404, not a validation error. A valid tile
    without data (sea) is an empty object - the browser then does not ask for it again.
    The browser keeps tiles for a week; the ETag changes with every download of the map (and with
    detailed_map.LAYER_NAMES).
    """
    if not all(re.fullmatch(r"\d{1,4}", value) for value in (detail, x, y)):
        raise HTTPException(status_code=404)
    level = detailed_map.DETAIL_LEVELS.get(int(detail))
    state = detailed_map.state()
    if (level is None or not state["valid"]
            or int(x) >= level["n"] or int(y) >= level["n"]):
        raise HTTPException(status_code=404)
    tag = hashlib.sha256(f"{state['at']}|{detailed_map.LAYER_NAMES}|{detail}|{x}|{y}".encode()).hexdigest()[:20]
    headers = {"Cache-Control": "private, max-age=604800", "ETag": f'"{tag}"'}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    content = detailed_map.tile(int(detail), int(x), int(y)) or "{}"
    return Response(content=content, media_type="application/json", headers=headers)
