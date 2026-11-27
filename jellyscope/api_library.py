"""Languages and disks in the API: /api/v1/languages and /api/v1/disks.

The same numbers as the Languages page (langstats) and the Disks card (stats.disks_overview), turned into the
API's raw units: seconds instead of hours, shares 0-1 instead of percents, bytes, and `full_at` - the day a disk
runs full at its growth (null while the capacity or the growth is unknown, never a made-up number).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import Depends

from . import api_query, langstats, stats
from . import languages as languages_names

_T, _N = api_query.TEXT, api_query.NUMBER

PARAMS: dict[str, set[str]] = {
    "/api/v1/languages": {"days", "limit"},
    "/api/v1/disks": {"days"},
}
LISTS: dict[str, dict[str, dict[str, str]]] = {
    "/api/v1/languages": {"audio": {"language": _T, "name": _T, "plays": _N, "watched_seconds": _N, "viewers": _N,
                                    "titles": _N, "share": _N}},
    "/api/v1/disks": {"disks": {"id": _T, "name": _T, "kind": _T, "capacity_bytes": _N, "free_bytes": _N,
                                "library_bytes": _N, "outside_bytes": _N, "free_share": _N,
                                "daily_growth_bytes": _N, "days_left": _N, "full_at": _T}},
}


def _seconds(hours: Any) -> int:
    return round(float(hours or 0) * 3600)


def _share(percent: Any) -> float:
    return round(float(percent or 0) / 100, 6)


def languages(api, query: api_query.Query) -> dict[str, Any]:
    days = api._query_days(query)
    limit = api._query_limit(query, None, 500)
    watched = langstats.watched_languages(days, langstats.colour_map())
    subtitles = langstats.subtitle_usage(days)
    # The name in English whatever the server's language (the page's label is translated): an integration must not
    # break when the administrator switches the language.
    audio = [{"language": row.get("code"), "name": languages_names.english_name(row.get("code")), "plays": int(row.get("plays") or 0),
              "watched_seconds": _seconds(row.get("hours")), "viewers": int(row.get("users") or 0),
              "titles": int(row.get("item_count") or 0), "share": _share(row.get("percent"))}
             for row in watched.get("rows") or []]
    return {"days": days, "known_share": _share(watched.get("known_share")),
            "watched_seconds": _seconds(watched.get("total_hours")),
            "audio": api_query.apply(audio, *query.for_list("audio"), limit),
            "subtitles": {"with_seconds": _seconds(subtitles.get("with_hours")),
                          "without_seconds": _seconds(subtitles.get("without_hours")),
                          "share": _share(subtitles.get("percent")),
                          "languages": [{"language": row.get("code"), "name": languages_names.english_name(row.get("code")),
                                         "plays": int(row.get("plays") or 0), "watched_seconds": _seconds(row.get("hours"))}
                                        for row in subtitles.get("rows") or []]}}


def _whole(value: Any) -> int | None:
    return None if value is None else int(value)


def _disk(row: dict[str, Any]) -> dict[str, Any]:
    total, free, days_left = row.get("total"), row.get("free"), row.get("days_left")
    return {"id": row.get("fingerprint") or row.get("key"), "name": row.get("name"), "kind": row.get("kind"),
            "capacity_bytes": _whole(total), "free_bytes": _whole(free), "library_bytes": _whole(row.get("library")),
            "outside_bytes": _whole(row.get("outside")),
            "free_share": round(free / total, 6) if total and free is not None else None,
            "daily_growth_bytes": _whole(row.get("daily")), "days_left": _whole(days_left),
            "full_at": _full_at(days_left)}


def _full_at(days_left: Any) -> str | None:
    """The day the disk runs full; None when unknown or past any calendar (a quiet archive disk: 1e8 days)."""
    if days_left is None:
        return None
    today = date.today()
    return None if int(days_left) > (date.max - today).days else (today + timedelta(days=int(days_left))).isoformat()


def disks(api, query: api_query.Query) -> dict[str, Any]:
    days = api._query_days(query)
    rows = stats.disks_overview(days, stats.library_growth, stats.disk_growth).get("disks") or []
    return {"days": days, "disks": api_query.apply([_disk(row) for row in rows], *query.for_list("disks"), None)}


def install(api) -> None:
    """Puts the routes straight on the API's router with its dependencies (see api_music.install)."""
    @api.router.get("/languages", name="audio_languages")
    def _languages(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return languages(api, query)

    @api.router.get("/disks", name="disks")
    def _disks(query: api_query.Query = Depends(api.request_query)) -> dict[str, Any]:
        return disks(api, query)
