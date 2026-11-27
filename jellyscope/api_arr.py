"""The *Arr apps in the API: /api/v1/arr and /arr/{app}/queue, /calendar, /health.

Generic over the registry of apps (arr.registry): the shared code names no app - each app gives its live state
(`App.live_state`: queue, calendar, health; 3 s, a minute's memory, the same the cards read) and its downloads
(`App.downloading`). An app that does not answer is a 200 with `available: false` and a reason, never a 500; an
app that is not connected, or an address the app does not have, a 404 with a code.

The apps' own texts - health messages, the messages at queue items, error texts - only for the administrator,
as on the pages (cards_<app>.for_account): they carry addresses and paths of the internal network. The others
get the count and the type, the message is null.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends

from . import access, api_query, db
from .api_query import ApiError

# The addresses an app has (api_endpoints.Endpoint.apps tells the same to the documentation).
DOWNLOADERS = ("sonarr", "radarr", "lidarr", "readarr")
HAS = {"queue": DOWNLOADERS, "calendar": DOWNLOADERS, "indexers": ("prowlarr",)}

PARAMS: dict[str, set[str]] = {
    "/api/v1/arr": set(),
    "/api/v1/arr/{app}/queue": set(),
    "/api/v1/arr/{app}/calendar": {"days"},
    "/api/v1/arr/{app}/health": set(),
    "/api/v1/arr/{app}/stats": {"days"},
    "/api/v1/arr/{app}/indexers": {"days"},
}
LISTS: dict[str, dict[str, dict[str, str]]] = {}


# ---------------------------------------------------------------------------
# The statistics of a download app (Sonarr, Radarr, Lidarr, Readarr) - one shape for the four. Each app's package
# (arr/<app>/api_data.py) hands over its own statistics functions, so this names no app nor its tables; the
# period's events are picked and paired once (`events`), every other statistic takes them as `analysis`, as the
# app's page does. Times in seconds; missing and cutoff from the live state (what the last sync counted).
# ---------------------------------------------------------------------------

def _whole_seconds(value: Any) -> int | None:
    return None if value is None else int(round(float(value)))


def download_stats(days: int, state: dict[str, Any], *, events: Callable[..., dict[str, Any]],
                   speed: Callable[..., dict[str, Any]], upgrades: Callable[..., dict[str, Any]],
                   failures: Callable[..., int], reliability: Callable[..., list[dict[str, Any]]]) -> dict[str, Any]:
    analysis = events(days)
    pace = speed(days, analysis=analysis)
    since_release, downloads = pace.get("since_release") or {}, pace.get("downloads") or {}
    return {
        "days": days,
        "grabbed": len(analysis.get("grabs_list") or []),
        "imported": len(analysis.get("imports") or []),
        "upgrades": int((upgrades(days, analysis=analysis) or {}).get("count") or 0),
        "failed": int(failures(days) or 0),
        "release_to_download_median_seconds": _whole_seconds(since_release.get("median")),
        "release_to_download_p90_seconds": _whole_seconds(since_release.get("p90")),
        "grab_to_import_median_seconds": _whole_seconds(downloads.get("median")),
        "missing": state.get("missing"),
        "cutoff_unmet": state.get("cutoff"),
        "indexers": [{"name": row.get("name"), "grabbed": int(row.get("downloads") or 0),
                      "imported": int(row.get("imported") or 0), "failed": int(row.get("failed") or 0),
                      "success_share": round(float(row.get("success") or 0), 4)}
                     for row in reliability(days, analysis=analysis)],
    }


def _registry():
    from .arr import registry
    return registry


def _app(key: str, part: str):
    """The app of the address, or the 404 that says why there is none."""
    registry = _registry()
    app = registry.APPS.get(key)
    if app is None:
        raise ApiError(404, "not_found", f"There is no app '{key}'.")
    if part in HAS and key not in HAS[part]:
        raise ApiError(404, "not_available_for_app", f"{app.name} has no {part}.")
    if key not in registry.connected():
        raise ApiError(404, "app_not_connected", f"{app.name} is not connected.")
    return app


def _admin(token: dict[str, Any]) -> bool:
    return token["role"] == access.ADMIN


def _text(value: Any, admin: bool) -> str | None:
    return (str(value) if value else None) if admin else None


def _unavailable(part: str) -> dict[str, Any]:
    return {"available": False, "reason": "app_not_answering", part: None}


async def overview(token: dict[str, Any]) -> dict[str, Any]:
    registry = _registry()
    connected = set(registry.connected())
    apps = list(registry.APPS.items())
    # In the server's own event loop: the live states share their memory and locks with the cards.
    live = await asyncio.gather(*(app.live_state() if key in connected else asyncio.sleep(0, None)
                                  for key, app in apps))
    rows = []
    for (key, app), state in zip(apps, live):
        sync = app.sync_state() if key in connected else {}
        health = (state or {}).get("health")
        rows.append({"app": key, "name": app.name, "connected": key in connected,
                     "warnings": len(health) if isinstance(health, list) else None,
                     "last_sync": sync.get("at") or None, "last_sync_result": sync.get("result") or None})
    return {"apps": rows}


async def queue(token: dict[str, Any], key: str) -> dict[str, Any]:
    app = _app(key, "queue")
    admin = _admin(token)
    state, downloads = await asyncio.gather(app.live_state(), app.downloading(False))
    if not (state.get("available") and downloads.get("available")):
        return {**_unavailable("queue"), "errors": None}
    items = []
    for block in downloads.get("blocks") or []:
        size, grabbed = int(block.get("size_bytes") or 0), int(block.get("grabbed") or 0)
        items.append({"title": block.get("title"), "detail": block.get("description") or None,
                      "status": block.get("state"),
                      "progress_share": round(min(1.0, grabbed / size), 4) if size else round((block.get("percent") or 0) / 100, 4),
                      "size_bytes": size, "left_bytes": max(0, size - grabbed),
                      "seconds_left": block.get("seconds_left"), "client": block.get("client"),
                      "protocol": block.get("protocol")})
    errors = [{"title": p.get("title"), "detail": p.get("episode") or None, "status": p.get("state"),
               "message": _text(p.get("message"), admin)}
              for p in ((state.get("queue") or {}).get("with_error") or [])]
    return {"available": True, "reason": None, "queue": items, "errors": errors}


async def calendar(token: dict[str, Any], key: str, days: int) -> dict[str, Any]:
    app = _app(key, "calendar")
    state = await app.live_state()
    if not state.get("available") or state.get("calendar") is None:
        return _unavailable("calendar")
    until = (datetime.now(UTC) + timedelta(days=days)).strftime(db.TIME_FORMAT)
    return {"available": True, "reason": None, "days": days,
            "calendar": [{"title": c.get("title"), "detail": c.get("episode") or None, "release": c.get("at"),
                          "has_file": bool(c.get("has_file"))}
                         for c in state["calendar"] if (c.get("at") or "") <= until]}


async def health(token: dict[str, Any], key: str) -> dict[str, Any]:
    app = _app(key, "health")
    admin = _admin(token)
    state = await app.live_state()
    if not state.get("available") or state.get("health") is None:
        return _unavailable("health")
    return {"available": True, "reason": None,
            "health": [{"type": h.get("type") or None, "message": _text(h.get("message"), admin)} for h in state["health"]]}


async def statistics(key: str, days: int, part: str = "stats") -> dict[str, Any]:
    """The app's statistics of the period (the database) with what its live state knows (missing, providers,
    indexers now); the database's work in a thread, so the server does not stop for it."""
    app = _app(key, part)
    state = await app.live_state()
    function = app.api_indexers if part == "indexers" else app.api_stats
    return await asyncio.to_thread(function, days, state)


def install(api) -> None:
    """Puts the routes straight on the API's router with its dependencies (see api_music.install)."""
    @api.router.get("/arr", name="arr_overview")
    async def _overview(token: dict[str, Any] = Depends(api.may_open_path),
                        query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await overview(token)

    @api.router.get("/arr/{app}/queue", name="arr_queue")
    async def _queue(app: str, token: dict[str, Any] = Depends(api.may_open_path),
                     query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await queue(token, app)

    @api.router.get("/arr/{app}/calendar", name="arr_calendar")
    async def _calendar(app: str, token: dict[str, Any] = Depends(api.may_open_path),
                        query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await calendar(token, app, api_query.integer(query, "days", 14, 1, 90))

    @api.router.get("/arr/{app}/stats", name="arr_stats")
    async def _stats(app: str, token: dict[str, Any] = Depends(api.may_open_path),
                     query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await statistics(app, api._query_days(query))

    @api.router.get("/arr/{app}/indexers", name="arr_indexers")
    async def _indexers(app: str, token: dict[str, Any] = Depends(api.may_open_path),
                        query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await statistics(app, api._query_days(query), "indexers")

    @api.router.get("/arr/{app}/health", name="arr_health")
    async def _health(app: str, token: dict[str, Any] = Depends(api.may_open_path),
                      query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await health(token, app)
