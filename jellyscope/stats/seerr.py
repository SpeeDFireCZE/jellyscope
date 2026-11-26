"""Seerr's statistics over the stored requests (the table seerr_request).

Part of the statistics package (see jellyscope/stats/__init__.py). jellyscope/seerr/sync.py downloads the requests
into the database.

All functions take `(days, analysis=None)`: `days` is anything `_as_period` takes, and a request counts into the
period by its date in the app's zone. `analysis` is a ready result of `seerr_events` - the page computes it once and
the cards are only different views of it.

The requesters' names: who pairs with a Jellyscope viewer (the Jellyfin id without dashes and in lower case) has
`user_id` and `user_name` from Jellyscope - so the link to the viewer's detail and the anonymising work for them
(`access.anonymise` looks for exactly these keys). The others have an empty `user_id` and the name from Seerr.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from .. import db, formatting
from ..jellyfin import id_without_dashes, id_forms
from .periods import OLDEST_HISTORY, _as_period
from .shared import _quantile

AVAILABLE = (4, 5)          # the title's state: partly available, available
DELETED = 7             # the title's state: it disappeared from the library (Seerr: DELETED)
APPROVED = (2, 4, 5)      # the request's state: approved, failed (after approval), done
DECLINED = 3

_COLUMNS = ("id, media_type, tmdb_id, jellyfin_id, title, is_4k, auto_request, status, media_status, requester_seerr_id, requester_name,"
            " requester_jellyfin_id,"
            " created_at, available_at, seen_pending, approved_at, deleted_at")


def _time(text: str) -> datetime:
    return datetime.strptime(text, db.TIME_FORMAT)


def _local(text: str) -> datetime:
    """A time from the database (UTC) in the app's zone."""
    return _time(text).replace(tzinfo=UTC).astimezone(formatting.zone())


def _in_library(request: dict[str, Any]) -> bool:
    """The request's title is in the library and the request led to it.

    A declined request does not count, even when the title is in the library: all requests for it share the title's
    state, so a declined one would be "fulfilled" by someone else's request.
    """
    return request["media_status"] in AVAILABLE and request["status"] != DECLINED


def _items_by_id(ids: Iterable[str]) -> dict[tuple[str, str], list[str]]:
    """The library's items by the title's id in Jellyfin: {(type, id without dashes): [item ids]}.

    A movie = `items.id` and with it its copies in the same library (scanner/copies.py): Seerr points at one item,
    but the viewer could have played any of them. A series = episodes with the same `series_id`. It looks up via
    `IN (...)` over the stored forms of the id (jellyfin.id_forms), through the index.
    """
    forms = sorted({form for jellyfin_id in ids for form in id_forms(jellyfin_id)})
    by_id: dict[tuple[str, str], list[str]] = defaultdict(list)
    movies = db.query_all_in("SELECT id, main_id FROM items WHERE type = 'Movie' AND id IN ({})", forms)
    main_ids = sorted({str(r["main_id"] or r["id"]) for r in movies})
    copies: dict[str, list[str]] = defaultdict(list)
    for r in db.query_all_in("SELECT id, main_id FROM items WHERE type = 'Movie' AND is_missing = 0 AND main_id IN ({})", main_ids):
        copies[str(r["main_id"])].append(r["id"])
    for r in movies:
        group_id = str(r["main_id"] or r["id"])
        others = [i for i in [group_id, *copies[group_id]] if i != r["id"]] if copies[group_id] else []
        by_id[("movie", id_without_dashes(r["id"]))] = [r["id"], *others]
    for r in db.query_all_in("SELECT id, series_id FROM items WHERE type = 'Episode' AND series_id IN ({})", forms):
        by_id[("tv", id_without_dashes(r["series_id"]))].append(r["id"])
    return by_id


def _title_items(requests: list[dict[str, Any]]) -> dict[int, list[str]]:
    """The library's items of every request: {request id: [item ids]}.

    First by the title's id in Jellyfin (Seerr's `jellyfinMediaId`): a movie = `items.id`, a series = episodes with
    the same `series_id`. When nothing is found by it - the id is missing, or outdated (the title was deleted and
    added anew in Jellyfin) - the TMDB id is tried for a **movie**. Not for a series: an episode can carry its own
    TMDB id (the episode's id, another number space) and with the same number the request would get the watching of
    someone else's series credited.

    What is not found even so is tried via the ids from other requests for the same title
    (_ids_from_other_requests) - the same rule as for the posters (seerr_posters). Otherwise a request without an id
    of its own would have a poster and a link into the library, but its plays would not be found.
    """
    by_id = _items_by_id(z["jellyfin_id"] for z in requests)
    remaining = [z for z in requests if not by_id.get((z["media_type"], z["jellyfin_id"]))]
    by_tmdb: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in db.query_all_in("SELECT id, tmdb_id FROM items WHERE type = 'Movie' AND tmdb_id IN ({})",
                             sorted({z["tmdb_id"] for z in remaining if z["tmdb_id"] and z["media_type"] == "movie"})):
        by_tmdb[("movie", r["tmdb_id"])].append(r["id"])
    result = {z["id"]: by_id.get((z["media_type"], z["jellyfin_id"])) or by_tmdb.get((z["media_type"], z["tmdb_id"])) or []
              for z in requests}
    without = [z for z in requests if not result[z["id"]] and z["tmdb_id"]]
    elsewhere = _ids_from_other_requests((z["media_type"], z["jellyfin_id"], z["tmdb_id"]) for z in without) if without else {}
    # Only ids not tried yet - what was not found a moment ago will not be found now either.
    new_ids = {i for ids in elsewhere.values() for i in ids} - {z["jellyfin_id"] for z in requests}
    if new_ids:
        by_id = {**by_id, **_items_by_id(new_ids)}
    for z in without:
        result[z["id"]] = next((by_id[(z["media_type"], i)] for i in elsewhere.get((z["media_type"], z["tmdb_id"]), [])
                                if by_id.get((z["media_type"], i))), [])
    return result


def _watching(requests: list[dict[str, Any]]) -> dict[int, str]:
    """"Requested and watches?" for requests with the title in the library: {id: zadatel/jiny/nikdo}.

    A play counts with `watched_seconds > 0` (as with the viewers' statistics) and only after the request.
    """
    in_library = [z for z in requests if _in_library(z) and (z["jellyfin_id"] or z["tmdb_id"])]
    items = _title_items(in_library)
    plays: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in db.query_all_in("SELECT item_id, user_id, started_at FROM playback WHERE watched_seconds > 0"
                             " AND item_id IN ({})", sorted({i for id_list in items.values() for i in id_list})):
        plays[r["item_id"]].append((id_without_dashes(r["user_id"]), str(r["started_at"])[:19].replace("T", " ")))
    result = {}
    for z in in_library:
        viewers = {who for i in items[z["id"]] for who, when in plays.get(i, []) if when >= z["created_at"]}
        result[z["id"]] = "requester" if z["requester_jellyfin_id"] and z["requester_jellyfin_id"] in viewers \
            else "other" if viewers else "nobody"
    return result


def _posters_by_id(ids: Iterable[str]) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Posters by the title's id in Jellyfin: (movies, series), each {id without dashes: (id for /image/, tag)}.

    A movie has its own poster (`items.id`, `image_tag`), a series the series' poster (`series_id` and
    `series_image_tag` of its episodes). Only items that are still in Jellyfin (`is_missing = 0`): nobody would give
    out the picture of one that disappeared.
    """
    forms = sorted({form for jellyfin_id in ids for form in id_forms(jellyfin_id)})
    movies = {id_without_dashes(r["id"]): (str(r["id"]), str(r["image_tag"] or "")) for r in db.query_all_in(
        "SELECT id, image_tag FROM items WHERE type = 'Movie' AND is_missing = 0 AND id IN ({})", forms)}
    series = {id_without_dashes(r["series_id"]): (str(r["series_id"]), str(r["fingerprint"] or "")) for r in db.query_all_in(
        "SELECT series_id, MAX(series_image_tag) AS fingerprint FROM items"
        " WHERE type = 'Episode' AND is_missing = 0 AND series_id IN ({}) GROUP BY series_id", forms)}
    return movies, series


def _ids_from_other_requests(titles: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str], list[str]]:
    """The title's Jellyfin ids from other requests for the same title: {(type, TMDB id): [ids]}.

    A request for the 4K version carries the 4K copy's id - empty until there is a 4K copy - although the regular
    copy of the title is in the library and the regular request for it has its id. The same for a request Seerr has
    not filled the id in yet.
    """
    elsewhere: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in db.query_all_in("SELECT DISTINCT media_type, tmdb_id, jellyfin_id FROM seerr_request"
                             " WHERE jellyfin_id <> '' AND tmdb_id IN ({}) ORDER BY jellyfin_id",
                             sorted({tmdb for _, _, tmdb in titles if tmdb})):
        elsewhere[(str(r["media_type"]), str(r["tmdb_id"]))].append(str(r["jellyfin_id"]))
    return elsewhere


def seerr_posters(titles: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str, str], tuple[str, str]]:
    """A poster from Jellyfin for the titles that are in the library.

    `titles` are triples (type, the title's id in Jellyfin, TMDB id) - as a request carries them. Returns {triple:
    (item id for /image/, the picture's tag)} only for titles in the library; the others are not in the result and
    the card is left with a provisional poster. A movie has its own poster, a series the series' poster - the same
    as "Recently added", so it is the same file in the image cache.

    It pairs as with "Requested and watches?" (_title_items): first by the id from Jellyfin, a movie then also by
    the TMDB id; a series not by the items' TMDB id (an episode can carry the episode's id). When a TMDB id fits
    several movies, the main copy is taken (an empty `main_id`, scanner/copies.py), among several main ones the one
    with the lower id. What is not found is also tried via the ids from other requests for the same title
    (_ids_from_other_requests) - otherwise a request for the 4K version of a series that is in the library would be
    left without a poster from Jellyfin and without a link.
    """
    titles = set(titles)
    movies, series = _posters_by_id(jellyfin_id for _, jellyfin_id, _ in titles)
    by_tmdb: dict[str, tuple[str, str]] = {}
    candidates = db.query_all_in(
        "SELECT id, tmdb_id, image_tag, main_id FROM items WHERE type = 'Movie' AND is_missing = 0 AND tmdb_id IN ({})",
        sorted({tmdb for media_type, jellyfin_id, tmdb in titles if media_type == "movie" and tmdb and jellyfin_id not in movies}))
    for r in sorted(candidates, key=lambda r: (r["main_id"] is not None, str(r["id"]))):
        by_tmdb.setdefault(str(r["tmdb_id"]), (str(r["id"]), str(r["image_tag"] or "")))
    result = {}
    for title in titles:
        media_type, jellyfin_id, tmdb = title
        found = series.get(jellyfin_id) if media_type == "tv" else movies.get(jellyfin_id) or by_tmdb.get(tmdb)
        if found:
            result[title] = found
    remaining = [title for title in titles if title not in result and title[2]]
    elsewhere = _ids_from_other_requests(remaining) if remaining else {}
    # Only ids not tried yet - what was not found a moment ago will not be found now either.
    new_ids = {i for ids in elsewhere.values() for i in ids} - {jellyfin_id for _, jellyfin_id, _ in titles}
    if new_ids:
        more_movies, more_series = _posters_by_id(new_ids)
        movies, series = {**movies, **more_movies}, {**series, **more_series}
    for title in remaining:
        where_from = series if title[0] == "tv" else movies
        found = next((where_from[i] for i in elsewhere.get((title[0], title[2]), []) if i in where_from), None)
        if found:
            result[title] = found
    return result


def seerr_with_posters(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copies of request rows with a poster from the library: `poster_id` and `poster_tag`, outside the library both empty.

    Only the rows that are really drawn need a poster - at most a few dozen of all the period's requests. That is
    why it is not looked up when picking the period (seerr_events), but only here, for the rows that ask for it. A
    row must carry `media_type`, `jellyfin_id` and `tmdb_id`.
    """
    rows = list(rows)
    posters = seerr_posters((r["media_type"], r["jellyfin_id"], r["tmdb_id"]) for r in rows)
    result = []
    for r in rows:
        poster_id, poster_tag = posters.get((r["media_type"], r["jellyfin_id"], r["tmdb_id"]), ("", ""))
        result.append({**r, "poster_id": poster_id, "poster_tag": poster_tag})
    return result


def seerr_events(days: Any = 30) -> dict[str, Any]:
    """The period's requests (those deleted in Seerr too) with the local time, the requester's name and the result
    of "Requested and watches?" - for all cards at once.

    There is no poster from the library here: only the rows that are drawn need it (seerr_with_posters)."""
    period = _as_period(days)
    requests = [dict(r) for r in db.query_all(
        f"SELECT {_COLUMNS} FROM seerr_request WHERE created_at >= ? AND created_at < ? ORDER BY created_at, id",
        (period.since, period.until))]
    viewers = {id_without_dashes(r["id"]): (str(r["id"]), str(r["name"])) for r in db.query_all("SELECT id, name FROM users")}
    watching = _watching(requests)
    for z in requests:
        z["local"] = _local(z["created_at"])
        z["user_id"], z["user_name"] = viewers.get(z["requester_jellyfin_id"]) or ("", z["requester_name"])
        z["watching"] = watching.get(z["id"])
    return {"requests": requests}


def _requests(days: Any, analysis: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (analysis or seerr_events(days))["requests"]


def seerr_summary(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, int]:
    """How many requests, of them movies, series, 4K and automatic ones (from the watchlist)."""
    z = _requests(days, analysis)
    return {"count": len(z), "movies": sum(r["media_type"] == "movie" for r in z), "series_list": sum(r["media_type"] == "tv" for r in z),
            "four_k": sum(bool(r["is_4k"]) for r in z), "automatic": sum(bool(r["auto_request"]) for r in z)}


def seerr_by_day(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Requests by day in the app's zone (movies and series separately), only days with a request."""
    by_day: dict[str, dict[str, Any]] = {}
    for z in _requests(days, analysis):
        day = z["local"].date().isoformat()
        row = by_day.setdefault(day, {"day": day, "movies": 0, "series_list": 0})
        row["series_list" if z["media_type"] == "tv" else "movies"] += 1
    return [by_day[d] for d in sorted(by_day)]


def seerr_heatmap(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[list[int]]:
    """Requests by day of the week and hour: 7 × 24, row 0 = Monday (charts.heatmap)."""
    grid = [[0] * 24 for _ in range(7)]
    for z in _requests(days, analysis):
        grid[z["local"].weekday()][z["local"].hour] += 1
    return grid


def seerr_requesters(days: Any = 30, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The requesters' ranking: how many they requested, how many approved / declined / available and how many of their own they watched."""
    people: dict[tuple[Any, ...], dict[str, Any]] = {}
    for z in _requests(days, analysis):
        # A Jellyscope viewer is one row, even if they had two names in Seerr. Who is not a viewer is told by the user's
        # id in Seerr - two people of the same name do not merge; without it (a requester deleted in Seerr) the name
        # is left.
        key = (z["user_id"],) if z["user_id"] else ("", z["requester_seerr_id"] if z["requester_seerr_id"] is not None
                                                    else z["user_name"])
        row = people.setdefault(key, {"user_id": z["user_id"], "user_name": z["user_name"], "count": 0,
                                      "approved": 0, "declined": 0, "available": 0, "watched_it_self": 0})
        row["count"] += 1
        row["approved"] += z["status"] in APPROVED
        row["declined"] += z["status"] == DECLINED
        row["available"] += _in_library(z)
        row["watched_it_self"] += z["watching"] == "requester"
    return sorted(people.values(), key=lambda r: (-r["count"], r["user_name"]))


def _group(request: dict[str, Any]) -> str:
    """One group per request, in the order: deleted in Seerr, declined, failed, waiting, available, the title vanished
    from the library, approved (not available yet)."""
    if request["deleted_at"]:
        return "deleted"
    if request["status"] == DECLINED:
        return "declined"
    if request["status"] == 4:
        return "failed"
    if request["status"] == 1:
        return "waiting"
    if request["media_status"] in AVAILABLE:
        return "available"
    # A handled request whose title someone deleted from the library: "not available yet" would lie.
    return "removed" if request["media_status"] == DELETED else "approved"


def seerr_handling(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The requests' states and how fast they are handled (seconds, the median and p90).

    From the request to the library: only where the time of arrival is known. When the title was in the library
    before the request already (typically another season of a series), the time cannot be measured - the request
    counts as "a continuation". From the request to the approval: only requests Jellyscope saw waiting (otherwise
    we do not know the time of the approval).
    """
    requests = _requests(days, analysis)
    states = dict.fromkeys(("deleted", "declined", "failed", "waiting", "available", "approved", "removed"), 0)
    to_library, continuations, to_approval = [], 0, []
    for z in requests:
        states[_group(z)] += 1
        if z["available_at"] and z["status"] != DECLINED:
            seconds = (_time(z["available_at"]) - _time(z["created_at"])).total_seconds()
            if seconds < 0:
                continuations += 1
            else:
                to_library.append(seconds)
        if z["seen_pending"] and z["approved_at"]:
            to_approval.append(max(0.0, (_time(z["approved_at"]) - _time(z["created_at"])).total_seconds()))
    return {
        "states": states,
        "to_library": {"median": _quantile(to_library, 0.5), "p90": _quantile(to_library, 0.9),
                        "count": len(to_library), "continuation": continuations},
        "to_approve": {"median": _quantile(to_approval, 0.5), "p90": _quantile(to_approval, 0.9),
                         "count": len(to_approval)},
    }


def seerr_watching(days: Any = 30, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """"Requested and watches?": for requests with the title in the library who played it, and a list of the unplayed.

    The list's rows carry what the poster is looked up by (`media_type`, `jellyfin_id`, `tmdb_id`); seerr_with_posters adds
    the poster itself to the rows that are drawn."""
    done = [z for z in _requests(days, analysis) if z["watching"]]
    nobody = sorted((z for z in done if z["watching"] == "nobody"), key=lambda z: (z["available_at"] or "9", z["id"]))
    return {
        "available_count": len(done),
        "requester": sum(z["watching"] == "requester" for z in done),
        "other": sum(z["watching"] == "other" for z in done),
        "nobody": len(nobody),
        "nobody_list": [{"title": z["title"], "available_at": z["available_at"], "user_id": z["user_id"],
                          "user_name": z["user_name"], "media_type": z["media_type"], "tmdb_id": z["tmdb_id"],
                          "jellyfin_id": z["jellyfin_id"]} for z in nobody],
    }


def seerr_latest(days: Any = 30, limit: int = 100, analysis: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The period's requests from the newest - for the strip of posters "Latest requests".

    With each how it ended (`group`, the same groups as seerr_handling), who requested and the poster from the
    library if the title is in it (looked up only for the returned rows, not for all the period's requests). On the
    same time the request's id decides (higher = newer), so that the order is the same every time.

    The request's id is under the key `request_id`, not `id`: the anonymising takes the `id` of a row with a name for
    a viewer's id (access.anonymise) and for a requester who is not a Jellyscope viewer it would make a link to a
    page that does not exist.
    """
    requests = sorted(_requests(days, analysis), key=lambda z: (z["created_at"], z["id"]), reverse=True)
    return seerr_with_posters(
        {"request_id": z["id"], "media_type": z["media_type"], "tmdb_id": z["tmdb_id"], "jellyfin_id": z["jellyfin_id"],
         "title": z["title"], "group": _group(z), "created_at": z["created_at"], "is_4k": bool(z["is_4k"]),
         "user_id": z["user_id"], "user_name": z["user_name"]}
        for z in requests[:max(0, int(limit))])


def seerr_with_live(days: Any, fresh: Mapping[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """The period's requests with Seerr's newest requests read right now in place of the stored ones.

    For "Latest requests": `fresh` is the live state of seerr.latest_requests - `requests` (rows in the shape of the
    table's rows), `seen` (the ids the page held) and `span_from` (from which id on the page holds every request,
    None = unknown). A live row takes the place of the stored one: its state is the present one, the same as
    "Waiting for approval" shows. A live row of the period the table does not know yet (newer than the last sync) is
    added. A stored request the page should hold and does not is gone from Seerr - deleted in Seerr, as the next sync
    marks it. The page speaks only for the ids from `span_from` up to its highest one: a request stored after the page
    went into the cache (a sync within its minute) is newer than all of it and is not gone, and an empty page marks
    nothing. The older stored requests stay as they are. Returns `{"requests": [...]}`, an analysis seerr_latest
    takes; the other cards keep the stored requests. Nothing is changed in place: both inputs are shared.
    """
    period = _as_period(days)
    live = {r["id"]: r for r in fresh["requests"]}
    seen, span_from, now = fresh["seen"], fresh["span_from"], db.utcnow()
    highest = max(seen, default=None)
    merged = []
    for z in _requests(days, analysis):
        if z["id"] in live:
            continue
        gone = (span_from is not None and highest is not None and span_from <= z["id"] <= highest
                and z["id"] not in seen and not z["deleted_at"])
        merged.append({**z, "deleted_at": now} if gone else z)
    merged += [r for r in live.values() if period.since <= r["created_at"] < period.until]
    return {"requests": merged}


def seerr_first() -> str:
    """The time of the first stored request (UTC) - the start of "the whole history" of the Seerr page."""
    return str(db.query_value("SELECT MIN(created_at) FROM seerr_request WHERE created_at >= ?",
                              (OLDEST_HISTORY,), default="") or "")


def seerr_synced_at() -> str:
    """When the last sync saw a request (UTC), "" = never - how old the stored requests are."""
    return str(db.query_value("SELECT MAX(synced_at) FROM seerr_request", default="") or "")
