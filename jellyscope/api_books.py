"""Books in the API: /api/v1/books/summary, /top and /history.

Raw numbers from the same statistics the Books overview draws (stats.books_analysis and its functions):
audiobooks are listened to, e-books read - two times, `listening_seconds` and `reading_seconds`. The readings
carry people: they need the area "...who read" and the tick of the playback history (api_endpoints:
needs_history), a Jellyfin viewer's key gets only its own (api.history_user), and every row goes through
api._for_owner on its source fields (user_name, user_id) - the one rule of anonymising.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends

from . import accounts, api_query, stats

KINDS = ("books", "authors")
_T, _N, _TM = api_query.TEXT, api_query.NUMBER, api_query.TIME

PARAMS: dict[str, set[str]] = {
    "/api/v1/books/summary": {"days"},
    "/api/v1/books/top": {"days", "limit", "kind"},
    "/api/v1/books/history": {"days", "limit", "user"},
}
LISTS: dict[str, dict[str, dict[str, str]]] = {
    "/api/v1/books/top": {"items": {"id": _T, "name": _T, "author": _T, "kind": _T, "seconds": _N,
                                    "readers": _N, "works": _N}},
    "/api/v1/books/history": {"readings": {"started_at": _TM, "user": _T, "user_id": _T, "title": _T, "author": _T,
                                           "item_id": _T, "kind": _T, "read_seconds": _N, "client": _T,
                                           "device": _T, "play_method": _T}},
}


def summary(api, query: api_query.Query) -> dict[str, Any]:
    days = api._query_days(query)
    analysis = stats.books_analysis(days)
    numbers = stats.books_numbers(analysis)
    return {"days": days, "listening_seconds": int(numbers["listening"]), "reading_seconds": int(numbers["reading"]),
            "plays": len(analysis["rows"]), "readers": int(numbers["reader_count"]),
            "works": int(numbers["work_count"]), "authors": int(numbers["author_count"])}


def _top_row(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """One shape for both kinds (a book has no count of works, an author no author nor kind: None)."""
    if kind == "books":
        return {"id": row.get("id"), "name": row.get("title"), "author": row.get("author"), "kind": row.get("kind"),
                "seconds": int(row.get("seconds") or 0), "readers": int(row.get("reader_count") or 0), "works": None}
    return {"id": None, "name": row.get("name"), "author": None, "kind": None, "seconds": int(row.get("seconds") or 0),
            "readers": int(row.get("reader_count") or 0), "works": int(row.get("work_count") or 0)}


def top(api, query: api_query.Query) -> dict[str, Any]:
    days = api._query_days(query)
    limit = api._query_limit(query, 10, 200)
    kind = api_query.choice(query, "kind", "books", KINDS)
    analysis = stats.books_analysis(days)
    ranking = stats.books_works if kind == "books" else stats.books_authors
    whole = any(query.for_list("items"))
    items = [_top_row(kind, row) for row in ranking(analysis, limit=stats.NO_LIMIT if whole else limit)]
    return {"days": days, "kind": kind, "items": api_query.apply(items, *query.for_list("items"), limit)}


def _reading(row: dict[str, Any]) -> dict[str, Any]:
    return {"started_at": row.get("started_at"), "user": row.get("user_name"), "user_id": row.get("user_id"),
            "title": row.get("title") or row.get("item_name"), "author": row.get("author"),
            "item_id": row.get("item_id"), "kind": row.get("kind"), "read_seconds": int(row.get("watched_seconds") or 0),
            "client": row.get("client"), "device": row.get("device_name"), "play_method": row.get("play_method")}


def history(api, token: dict[str, Any], query: api_query.Query) -> dict[str, Any]:
    days = api._query_days(query)
    limit = api._query_limit(query, 50, 500)
    rows = list(stats.books_analysis(days)["rows"])
    user = api.history_user(token, query)
    if user is not None:            # the analysis has no filter of a viewer: the same rule as the history's
        rows = [row for row in rows if accounts.bare_id(row.get("user_id")) == accounts.bare_id(user)]
    rows.sort(key=lambda r: (r.get("started_at") or "", r.get("id") or 0), reverse=True)
    readings = api_query.apply([_reading(row) for row in api._for_owner(token, rows)], *query.for_list("readings"), limit)
    return {"days": days, "count": len(readings), "readings": readings}


def install(api) -> None:
    """Puts the routes straight on the API's router with its dependencies (see api_music.install)."""
    @api.router.get("/books/summary", name="books_summary")
    def _summary(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return summary(api, query)

    @api.router.get("/books/top", name="books_top")
    def _top(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return top(api, query)

    @api.router.get("/books/history", name="books_history")
    def _history(token: dict[str, Any] = Depends(api.may_open_path),
                 query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return history(api, token, query)
