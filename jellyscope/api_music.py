"""Music in the API: /api/v1/music/summary, /top and /history.

Raw numbers from the same statistics the Music overview draws (stats.music_analysis and its functions), not the
cards' formatted texts. The listens carry people: they need the area "...who listened" and the tick of the
playback history (api_endpoints: needs_history), a Jellyfin viewer's key gets only its own listens, and every
row goes through api._for_owner on its source fields (user_name, user_id) - the one rule of anonymising.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends

from . import api_query, stats

KINDS = ("artists", "albums", "tracks")
_T, _N, _TM = api_query.TEXT, api_query.NUMBER, api_query.TIME

PARAMS: dict[str, set[str]] = {
    "/api/v1/music/summary": {"days"},
    "/api/v1/music/top": {"days", "limit", "kind"},
    "/api/v1/music/history": {"days", "limit", "user"},
}
LISTS: dict[str, dict[str, dict[str, str]]] = {
    "/api/v1/music/top": {"items": {"id": _T, "name": _T, "artist": _T, "album": _T, "plays": _N,
                                    "listening_seconds": _N, "listeners": _N}},
    "/api/v1/music/history": {"listens": {"started_at": _TM, "user": _T, "user_id": _T, "track": _T, "album": _T,
                                          "artist": _T, "item_id": _T, "listened_seconds": _N, "client": _T,
                                          "device": _T, "play_method": _T}},
}


def _api():
    """The API module (its dependencies and helpers); imported late - it includes this router at its end."""
    from . import api
    return api


def summary(query: api_query.Query) -> dict[str, Any]:
    days = _api()._query_days(query)
    analysis = stats.music_analysis(days)
    numbers = stats.music_numbers(analysis)
    return {"days": days, "listening_seconds": int(numbers["seconds"]), "plays": int(numbers["plays"]),
            "listeners": int(numbers["listener_count"]), "artists": int(numbers["artist_count"]),
            "albums": len({row.get("album_id") for row in analysis["rows"] if row.get("album_id")}),
            "tracks": int(numbers["track_count"])}


def _top_row(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    # Always the same fields, whatever the kind (an artist has no artist nor album: None) - one shape for a tool.
    return {"id": row.get("id"), "name": row.get("name"),
            "artist": row.get("artist") if kind in ("albums", "tracks") else None,
            "album": row.get("album") if kind == "tracks" else None,
            "plays": int(row.get("plays") or 0), "listening_seconds": int(row.get("seconds") or 0),
            "listeners": int(row.get("listener_count") or 0)}


def top(query: api_query.Query) -> dict[str, Any]:
    api = _api()
    days = api._query_days(query)
    limit = api._query_limit(query, 10, 200)
    kind = api_query.choice(query, "kind", "artists", KINDS)
    analysis = stats.music_analysis(days)
    ranking = {"artists": stats.music_artists, "albums": stats.music_albums, "tracks": stats.music_tracks}[kind]
    items = [_top_row(kind, row) for row in ranking(analysis, limit=stats.NO_LIMIT if any(query.for_list("items")) else limit)]
    return {"days": days, "kind": kind, "items": api_query.apply(items, *query.for_list("items"), limit)}


def _listen(row: dict[str, Any]) -> dict[str, Any]:
    return {"started_at": row.get("started_at"), "user": row.get("user_name"), "user_id": row.get("user_id"),
            "track": row.get("track") or row.get("item_name"), "album": row.get("album"), "artist": row.get("artist"),
            "item_id": row.get("item_id"), "listened_seconds": int(row.get("watched_seconds") or 0),
            "client": row.get("client"), "device": row.get("device_name"), "play_method": row.get("play_method")}


def history(token: dict[str, Any], query: api_query.Query) -> dict[str, Any]:
    api = _api()
    days = api._query_days(query)
    limit = api._query_limit(query, 50, 500)
    rows = stats.music_analysis(days, user_id=api.history_user(token, query))["rows"]
    rows = sorted(rows, key=lambda r: (r.get("started_at") or "", r.get("id") or 0), reverse=True)
    listens = [_listen(row) for row in api._for_owner(token, rows)]
    return {"days": days, "count": None, "listens": api_query.apply(listens, *query.for_list("listens"), limit)}


def install(api) -> None:
    """Puts the routes straight on the API's router, with its dependencies (called once by api at its end).

    Straight, not through include_router: an included router keeps its routes' own paths (FastAPI 0.165), and the
    rights check and the known parameters go by the route's whole path (/api/v1/music/summary).
    """
    @api.router.get("/music/summary", name="music_summary")
    def _summary(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return summary(query)

    @api.router.get("/music/top", name="music_top")
    def _top(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return top(query)

    @api.router.get("/music/history", name="music_history")
    def _history(token: dict[str, Any] = Depends(api.may_open_path),
                 query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        data = history(token, query)
        data["count"] = len(data["listens"])
        return data
