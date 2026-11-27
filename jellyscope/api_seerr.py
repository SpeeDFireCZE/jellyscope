"""Seerr in the API: /api/v1/seerr/requests, /pending (live) and /stats.

The requests from the same statistics the Seerr page draws (stats.seerr_latest - the stored requests), the
waiting ones live from Seerr (seerr.pending: 3 s, a minute's memory, shared with the card); the statistics as
seconds and counts. The requester only with "...including who requested" (seerr_who), as on the page
(cards_seerr: without it the name and id go), and never past the anonymising (api._for_owner on the source rows).
Seerr's own texts (its error) stay out - only a reason code when it does not answer.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends

from . import access, api_query, stats
from .api_query import ApiError

STATUSES = ("waiting", "approved", "available", "declined", "deleted", "failed", "removed")
PARAMS: dict[str, set[str]] = {
    "/api/v1/seerr/requests": {"days", "limit", "status"},
    "/api/v1/seerr/pending": set(),
    "/api/v1/seerr/stats": {"days"},
}
_T, _N, _TM, _YN = api_query.TEXT, api_query.NUMBER, api_query.TIME, api_query.YES_NO
_REQUEST = {"id": _N, "kind": _T, "title": _T, "tmdb_id": _T, "status": _T, "requested_at": _TM, "is_4k": _YN,
            "requester": _T, "requester_id": _T}
LISTS: dict[str, dict[str, dict[str, str]]] = {"/api/v1/seerr/requests": {"requests": _REQUEST}}


def _connected() -> None:
    from . import seerr
    if not seerr.is_connected():
        raise ApiError(404, "app_not_connected", "Seerr is not connected.")


def _sees_who(token: dict[str, Any]) -> bool:
    return token["role"] == access.ADMIN or access.sees(token["role"], "seerr_who", token.get("account"))


def _request(row: dict[str, Any], who: bool, status: str | None = None) -> dict[str, Any]:
    return {"id": row.get("request_id"), "kind": row.get("media_type"), "title": row.get("title"),
            "tmdb_id": row.get("tmdb_id") or None, "status": status or row.get("group"),
            "requested_at": row.get("created_at"), "is_4k": bool(row.get("is_4k")),
            "requester": row.get("user_name") if who else None, "requester_id": row.get("user_id") if who else None}


def requests(api, token: dict[str, Any], query: api_query.Query) -> dict[str, Any]:
    _connected()
    days = api._query_days(query)
    limit = api._query_limit(query, 100, 500)
    wanted = None if query.values.get("status") in (None, "") else api_query.choice(query, "status", STATUSES[0], STATUSES)
    rows = [row for row in stats.seerr_latest(days, limit=stats.NO_LIMIT) if wanted is None or row.get("group") == wanted]
    who = _sees_who(token)
    items = [_request(row, who) for row in api._for_owner(token, rows)]
    return {"days": days, "requests": api_query.apply(items, *query.for_list("requests"), limit)}


async def pending(api, token: dict[str, Any]) -> dict[str, Any]:
    _connected()
    from . import seerr
    state = await seerr.pending()
    if not state.get("available"):
        return {"available": False, "reason": "app_not_answering", "requests": None}
    who = _sees_who(token)
    rows = api._for_owner(token, list(state.get("requests") or []))
    return {"available": True, "reason": None,
            "requests": [{**_request(row, who, "waiting"), "waiting_seconds": int(row.get("waiting_seconds") or 0)}
                         for row in rows]}


def statistics(api, query: api_query.Query) -> dict[str, Any]:
    _connected()
    days = api._query_days(query)
    analysis = stats.seerr_events(days)
    summary = stats.seerr_summary(days, analysis=analysis)
    handling = stats.seerr_handling(days, analysis=analysis)
    watching = stats.seerr_watching(days, analysis=analysis)
    states = handling.get("states") or {}

    def median(part: str) -> int | None:
        value = (handling.get(part) or {}).get("median")
        return None if value is None else int(value)

    return {"days": days, "requests": int(summary.get("count") or 0), "movies": int(summary.get("movies") or 0),
            "series": int(summary.get("series_list") or 0), "waiting": int(states.get("waiting") or 0),
            "approved": int(states.get("approved") or 0), "available": int(states.get("available") or 0),
            "declined": int(states.get("declined") or 0), "approval_median_seconds": median("to_approve"),
            "to_library_median_seconds": median("to_library"),
            "watched_by_requester": int(watching.get("requester") or 0),
            "watched_by_others": int(watching.get("other") or 0), "unwatched": int(watching.get("nobody") or 0)}


def install(api) -> None:
    """Puts the routes straight on the API's router with its dependencies (see api_music.install)."""
    @api.router.get("/seerr/requests", name="seerr_requests")
    def _requests(token: dict[str, Any] = Depends(api.may_open_path),
                  query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return requests(api, token, query)

    @api.router.get("/seerr/pending", name="seerr_pending")
    async def _pending(token: dict[str, Any] = Depends(api.may_open_path),
                       query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return await pending(api, token)

    @api.router.get("/seerr/stats", name="seerr_stats")
    def _stats(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return statistics(api, query)
