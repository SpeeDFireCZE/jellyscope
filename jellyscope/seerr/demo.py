"""The demo's made-up Seerr: requests of the demo's viewers for the demo's movies and series, as Seerr 3's API answers them.

The demo (JELLYSCOPE_DEMO) shows Seerr through its real code: the seed connects it at http://seerr.demo and runs its
real sync (demo_apps.seed_apps), the page's live cards ("Waiting for approval", "Latest requests") ask it for the
pending and the newest requests. `handler` answers them inside the process (httpx.MockTransport through demo_switch)
in the shape of a real Seerr (tests/seerr_responses/, tests/_apps.py) - every path the package's client asks:

* `status`, `settings/about` - the version, the count of requests (the connection test);
* `settings/public` - it serves images ("Enable Image Caching"), no address for people;
* `request` - in pages (`take`, `skip`), `filter` (all, pending, approved, declined, processing, available,
  unavailable, completed; another one is refused), `sort` (added, modified) and `sortDirection` (desc by default);
  `request/count`; `request/{id}` - one request, 404 = Seerr does not have it (the one deleted in Seerr);
* `movie/{tmdb}`, `tv/{tmdb}` - a title's name and the path of its poster on TMDB;
* `user`, `user/{id}` - the demo's viewers as Seerr's users (Jellyfin users, the admin first).

A `POST request/{id}/approve|decline` is accepted and changes nothing; everything else is 404 - the images too
(`/imageproxy/…`): the cards draw a placeholder, as for a real Seerr that has none.

The world (`_world`) is made from the demo's library (demo_world): the viewers request the library's titles that came
after Seerr was installed (INSTALLED_DAYS before the library's newest file) - every movie of the last weeks, most of
the others, every season of a series - each some days before it came to the library; the admin approves it hours to
days later (his own requests at once), and once Jellyfin has the title Seerr marks it available (a movie with
Jellyfin's date, a series without one, as Seerr does). Who requested a title the household watched is mostly one of
those who watched it after the request (the plays of the demo's playback), sometimes someone else; a title nobody
watched stays unwatched. Six titles the library does not have: three movies coming out that Radarr waits for
(approved), two waiting for approval, one declined; and one request for a library movie its requester deleted
before somebody else requested it again. Every time is anchored to the library (its titles' arrivals and its newest
file), so the requests keep their times and ids from one seed to the next while the clock (`demo_world.now()`)
only decides what has happened by then; the ids grow with the time.

What Seerr does not store and Jellyscope caught at its hourly syncs - that a request waited, when it was approved
or declined - `fill_past` (the seed, demo_apps, after the sync) replays through the sync's own writer, then syncs
again (the deleted request is marked by the sync itself).

Registered by demo_apps.load_fakes (`seerr.demo`), in the demo mode only - the door of the package does not import
this module.
"""
from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .. import db, demo_world, stats
from ..demo_world import random_for, span, weighted
from . import sync

API = "/api/v1/"
VERSION = "3.5.2"
# Seerr came this many days before the library's newest file: titles that came before were never requested.
INSTALLED_DAYS = 700
# The share of the library's older movies somebody requested (the admin added the others in Radarr himself); every
# movie of the last weeks and every season of a series was requested.
REQUESTED_SHARE = 0.75
# How often each viewer requests (by the viewer's number, demodata.USERS): Jana most, Filip least.
USAGE = {1: 0.6, 2: 1.6, 3: 1.1, 4: 0.9, 5: 0.7, 6: 0.6, 7: 0.8, 8: 1.0, 9: 0.5, 10: 0.9, 11: 0.4}
# The share of requests made by somebody who watched the title after it came (the others: somebody else did).
WATCHER_SHARE = 0.7
# How many days before the title came it was requested: (from, to, weight); a title of the last days was requested in
# the days just before it (so the last week has its requests).
LEAD_DAYS = ((0.6, 2, 35), (2, 6, 45), (6, 14, 20))
SHORT_LEAD_DAYS = ((0.4, 1.0, 50), (1.0, 1.6, 50))
RECENT = timedelta(days=6)
# The hour of the day a request is made (UTC; mostly evenings), and how many hours later the admin decides.
HOURS = ((6, 2), (7, 2), (11, 2), (12, 2), (15, 2), (16, 3), (17, 5), (18, 7), (19, 8), (20, 7), (21, 4), (22, 2))
DECISION_HOURS = ((0.2, 1.5, 25), (1.5, 8, 40), (8, 30, 30), (30, 80, 5))
# Seerr's scan of Jellyfin sees a new title this many minutes after it came.
SCAN_MINUTES = (3, 40)
# Jellyscope's hourly sync of Seerr runs at this minute of the hour: what it saw, it knows.
SYNC_MINUTE = 17
# The titles the library does not have: (key, kind, name - None for a movie coming out (demo_world.coming_movies) -,
# about how many hours before the library's newest file it was requested (that day, at an hour people request at),
# what happened, the hours to the decision).
OUTSIDE = (
    ("demo-movie-coming-0", "movie", None, 14.5 * 24, "approved", 9.0),
    ("demo-movie-coming-1", "movie", None, 8.3 * 24, "approved", 30.0),
    ("demo-movie-coming-2", "movie", None, 31.0, "approved", 5.5),
    ("demo-seerr-0", "movie", "Kamenny most", 21.0, "pending", 0.0),
    ("demo-seerr-1", "tv", "Ostrov v mlze", 2.2, "pending", 0.0),
    ("demo-seerr-2", "movie", "Cerveny balon", 12 * 24.0, "declined", 18.0),
)
# The request's states and the title's (Seerr's MediaRequestStatus and MediaStatus).
PENDING, APPROVED, DECLINED, COMPLETED = 1, 2, 3, 5
UNKNOWN, WAITING, PROCESSING, PARTLY, AVAILABLE, GONE = 1, 2, 3, 4, 5, 7


@dataclass
class _Title:
    """A title in Seerr: a movie or a series, in the library (its Jellyfin id) or not."""

    kind: str                                   # "movie" / "tv"
    name: str
    tmdb: str
    tvdb: str = ""
    imdb: str = ""
    jellyfin_id: str = ""                       # "" outside the library
    number: int | None = None                   # the catalog's number = Radarr's / Sonarr's id
    year: int | None = None
    available: datetime | None = None           # when Seerr's scan saw it in the library
    added: datetime | None = None               # a movie's arrival in Jellyfin (mediaAddedAt)
    removed: bool = False                       # gone from the library since (the archive)
    partly: bool = False                        # a series still airing: partly available
    id: int = 0


@dataclass
class _Request:
    title: _Title
    viewer: dict[str, Any]
    created: datetime
    decided: datetime | None = None             # approved or declined (None: waiting)
    declined: bool = False
    completed: datetime | None = None           # the title available (the request done)
    deleted: datetime | None = None             # deleted in Seerr
    seasons: tuple[int, ...] = ()
    id: int = 0


@dataclass
class _World:
    titles: list[_Title] = field(default_factory=list)
    requests: list[_Request] = field(default_factory=list)
    viewers: list[dict[str, Any]] = field(default_factory=list)
    installed: datetime | None = None


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------

def _plays() -> dict[str, dict[str, str]]:
    """{item id: {viewer's id: the newest start of their play}} - who watched what and until when (the live play,
    listening and reading aside: requests are for movies and series)."""
    found: dict[str, dict[str, str]] = {}
    for row in db.query_all("SELECT item_id, user_id, MAX(started_at) AS newest FROM playback"
                            f" WHERE watched_seconds > 0 AND is_active = 0{stats.watching_only()} GROUP BY item_id, user_id"):
        found.setdefault(str(row["item_id"]), {})[str(row["user_id"])] = str(row["newest"])[:19].replace("T", " ")
    return found


def _watched(plays: dict[str, dict[str, str]], items: list[str]) -> dict[str, str]:
    """{viewer's id: the newest start of their play} over the title's items."""
    newest: dict[str, str] = {}
    for item in items:
        for viewer, started in plays.get(item, {}).items():
            newest[viewer] = max(newest.get(viewer, ""), started)
    return newest


def _when(rng: random.Random, before: datetime, lead: tuple[tuple[float, float, float], ...]) -> datetime:
    """A request's moment: `lead` days before `before`, at an hour people request at, at least an hour before it."""
    day = before - timedelta(days=span(rng, lead))
    moment = day.replace(hour=weighted(rng, HOURS), minute=rng.randrange(60), second=rng.randrange(60), microsecond=0)
    return moment - timedelta(days=1) if moment > before - timedelta(hours=1) else moment


def _requester(rng: random.Random, viewers: list[dict[str, Any]], watched: dict[str, str], created: datetime,
               admin_too: bool = True) -> dict[str, Any]:
    """Who requested it: mostly one who watched it after the request, else somebody else (by how often each requests)."""
    since = created.strftime(db.TIME_FORMAT)
    people = [v for v in viewers if admin_too or not v["is_admin"]]
    watchers = [v for v in people if watched.get(v["jellyfin_id"], "") >= since]
    others = [v for v in people if v not in watchers]
    pool = watchers if watchers and (not others or rng.random() < WATCHER_SHARE) else (others or people)
    return weighted(rng, ((v, USAGE.get(v["number"], 0.5)) for v in pool))


def _decide(rng: random.Random, request: _Request, before: datetime | None) -> None:
    """The admin approves a request hours to days after it - before the title comes; his own at once."""
    if request.viewer["is_admin"]:
        request.decided = request.created
        return
    decided = request.created + timedelta(hours=span(rng, DECISION_HOURS))
    if before is not None and decided > before - timedelta(minutes=20):
        decided = request.created + (before - timedelta(minutes=20) - request.created) * rng.uniform(0.3, 0.7)
    request.decided = decided.replace(microsecond=0)


def _scanned(rng: random.Random, arrived: datetime) -> datetime:
    return (arrived + timedelta(minutes=rng.uniform(*SCAN_MINUTES))).replace(microsecond=0)


def _movies(world: _World, plays: dict[str, dict[str, str]], anchor: datetime) -> None:
    """The library's movies that came after Seerr: requested before they came, available once Seerr saw them."""
    seen: set[str] = set()
    for movie in demo_world.movies():
        arrived = movie["added_at"]
        if not arrived or arrived <= world.installed or movie["tmdb_id"] in seen:
            continue
        seen.add(movie["tmdb_id"])
        rng = random_for(f"seerr:movie:{movie['number']}")
        if demo_world.movie_release(movie) is None and rng.random() >= REQUESTED_SHARE:
            continue
        title = _Title("movie", movie["title"], movie["tmdb_id"], imdb=demo_world.imdb_id(f"movie:{movie['jellyfin_id']}"),
                       jellyfin_id=movie["jellyfin_id"], number=movie["number"], year=movie["year"],
                       available=_scanned(rng, arrived), added=arrived.replace(microsecond=0), removed=movie["missing"])
        created = _when(rng, arrived, SHORT_LEAD_DAYS if anchor - arrived <= RECENT else LEAD_DAYS)
        request = _Request(title, _requester(rng, world.viewers, _watched(plays, [movie["jellyfin_id"]]), created), created)
        _decide(rng, request, arrived)
        request.completed = title.available
        world.titles.append(title)
        world.requests.append(request)


def _series(world: _World, plays: dict[str, dict[str, str]], anchor: datetime) -> None:
    """Every season of the library's series that came after Seerr: one request each, before its first episode came.

    A season still airing stays approved (its episodes keep coming); the others are done once the last one came.
    """
    for show in demo_world.series():
        items = [e["jellyfin_id"] for episodes in show["seasons"].values() for e in episodes]
        rng = random_for(f"seerr:tv:{show['number']}")
        title = _Title("tv", show["title"], show["tmdb_id"], tvdb=show["tvdb_id"], jellyfin_id=show["jellyfin_id"],
                       number=show["number"], year=show["year"], available=_scanned(rng, show["added_at"]),
                       partly=f"demo-series-{show['number'] - 1}" in demo_world.AIRING)
        requests = []
        for season, episodes in show["seasons"].items():
            first, last = min(e["added_at"] for e in episodes), max(e["added_at"] for e in episodes)
            if first <= world.installed:
                continue
            created = _when(rng, first, SHORT_LEAD_DAYS if anchor - first <= RECENT else LEAD_DAYS)
            request = _Request(title, _requester(rng, world.viewers, _watched(plays, items), created), created, seasons=(season,))
            _decide(rng, request, first)
            ends = demo_world.aired(show["number"], season, demo_world.SEASON_LENGTH)
            request.completed = _scanned(rng, last) if ends is None or ends <= anchor else None
            requests.append(request)
        if requests:
            world.titles.append(title)
            world.requests += requests


def _outside(world: _World, anchor: datetime) -> None:
    """The titles the library does not have: movies coming out Radarr waits for, two waiting, one declined (OUTSIDE)."""
    coming = {movie["key"]: movie for movie in demo_world.coming_movies(anchor)}
    for key, kind, name, hours, outcome, decision in OUTSIDE:
        rng = random_for(f"seerr:outside:{key}")
        movie = coming.get(key, {})
        title = _Title(kind, name or movie.get("title", ""), demo_world.tmdb_id(key),
                       tvdb=demo_world.tvdb_id(key) if kind == "tv" else "", imdb=demo_world.imdb_id(key) if kind == "movie" else "",
                       year=movie.get("released", anchor).year)
        created = _when(rng, anchor, ((hours / 24, hours / 24, 1),))
        request = _Request(title, _requester(rng, world.viewers, {}, created, admin_too=False), created,
                           seasons=(1,) if kind == "tv" else ())
        if outcome != "pending":
            request.decided, request.declined = created + timedelta(hours=decision), outcome == "declined"
        world.titles.append(title)
        world.requests.append(request)


def _deleted(world: _World) -> None:
    """The newest older movie somebody requested had an earlier request, deleted by its requester hours later."""
    found = [r for r in world.requests if r.title.kind == "movie" and r.title.added and not r.title.removed
             and demo_world.movie_release({"jellyfin_id": r.title.jellyfin_id, "added_at": r.title.added}) is None]
    if not found:
        return
    later = max(found, key=lambda r: r.title.added)
    rng = random_for(f"seerr:deleted:{later.title.tmdb}")
    created = _when(rng, later.created, ((15, 40, 1),))
    others = [v for v in world.viewers if v is not later.viewer and not v["is_admin"]]
    request = _Request(later.title, weighted(rng, ((v, USAGE.get(v["number"], 0.5)) for v in others)), created)
    request.deleted = (created + timedelta(hours=rng.uniform(5, 30))).replace(microsecond=0)
    world.requests.append(request)


def _world() -> _World:
    """Seerr's titles and requests, from the library (the same library gives the same world), ids by time."""
    anchor = demo_world.newest_arrival()
    world = _World(viewers=demo_world.viewers())
    if anchor is None or not world.viewers:
        return world
    world.installed = anchor - timedelta(days=INSTALLED_DAYS)
    plays = _plays()
    _movies(world, plays, anchor)
    _series(world, plays, anchor)
    _outside(world, anchor)
    _deleted(world)
    world.requests.sort(key=lambda r: (r.created, r.title.tmdb))
    for number, request in enumerate(world.requests, start=1):
        request.id = number
    firsts = {id(t): min(r.created for r in world.requests if r.title is t) for t in world.titles}
    for number, title in enumerate(sorted(world.titles, key=lambda t: (firsts[id(t)], t.tmdb)), start=1):
        title.id = number
    return world


# ---------------------------------------------------------------------------
# A moment of the world
# ---------------------------------------------------------------------------

def _visible(request: _Request, moment: datetime) -> bool:
    return request.created <= moment and (request.deleted is None or moment < request.deleted)


def _status(request: _Request, moment: datetime) -> int:
    if request.decided is None or moment < request.decided:
        return PENDING
    if request.declined:
        return DECLINED
    return COMPLETED if request.completed and moment >= request.completed else APPROVED


def _updated(request: _Request, moment: datetime) -> datetime:
    return max(t for t in (request.created, request.decided, request.completed) if t and t <= moment)


def _media_status(world: _World, title: _Title, moment: datetime) -> int:
    """The title's state: available once Seerr saw it in the library (gone, partly), else by its requests."""
    if title.available and moment >= title.available:
        return GONE if title.removed else PARTLY if title.partly else AVAILABLE
    states = {_status(r, moment) for r in world.requests if r.title is title and _visible(r, moment)}
    return PROCESSING if states & {APPROVED, COMPLETED} else WAITING if PENDING in states else UNKNOWN


def _iso(moment: datetime) -> str:
    """A moment as Seerr writes it: "2026-11-13T06:28:48.000Z" (UTC)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _user(world: _World, viewer: dict[str, Any], moment: datetime) -> dict[str, Any]:
    """A viewer as Seerr's user (a Jellyfin user, userType 3): the admin has permission 2, the others may request."""
    joined = _iso(world.installed + timedelta(hours=viewer["number"])) if world.installed else _iso(moment)
    return {"permissions": 2 if viewer["is_admin"] else 32, "warnings": [], "id": viewer["number"],
            "email": viewer["name"].lower(), "plexUsername": None, "jellyfinUsername": viewer["name"], "username": None,
            "recoveryLinkExpirationDate": None, "userType": 3, "plexId": None, "jellyfinUserId": viewer["jellyfin_id"],
            "avatar": f"/avatarproxy/{viewer['jellyfin_id']}?v=null", "avatarETag": None, "avatarVersion": None,
            "movieQuotaLimit": None, "movieQuotaDays": None, "tvQuotaLimit": None, "tvQuotaDays": None,
            "createdAt": joined, "updatedAt": joined,
            "requestCount": sum(r.viewer is viewer and _visible(r, moment) for r in world.requests),
            "displayName": viewer["name"]}


def _media(world: _World, title: _Title, moment: datetime) -> dict[str, Any]:
    requests = [r for r in world.requests if r.title is title and r.created <= moment]
    created = min((r.created for r in requests), default=moment)
    status = _media_status(world, title, moment)
    in_library = status in (PARTLY, AVAILABLE, GONE)
    changed = max([created, *(_updated(r, moment) for r in requests)] + ([title.available] if in_library else []))
    return {"downloadStatus": [], "downloadStatus4k": [], "id": title.id, "mediaType": title.kind,
            "tmdbId": int(title.tmdb), "tvdbId": int(title.tvdb) if title.tvdb else None, "imdbId": title.imdb or None,
            "status": status, "status4k": UNKNOWN, "createdAt": _iso(created), "updatedAt": _iso(changed),
            "lastSeasonChange": _iso(created),
            # Seerr writes the real time only for a movie its scan found; otherwise the time it made the record.
            "mediaAddedAt": _iso(title.added if in_library and title.added else created),
            "serviceId": 0 if status not in (WAITING, UNKNOWN) else None, "serviceId4k": None,
            "externalServiceId": title.number if in_library else None, "externalServiceId4k": None,
            "externalServiceSlug": title.tmdb if in_library else None, "externalServiceSlug4k": None,
            "ratingKey": None, "ratingKey4k": None,
            "jellyfinMediaId": title.jellyfin_id if in_library else None, "jellyfinMediaId4k": None}


def _record(world: _World, request: _Request, moment: datetime) -> dict[str, Any]:
    """One request as Seerr's API gives it, as it was at `moment`."""
    status, admin = _status(request, moment), next((v for v in world.viewers if v["is_admin"]), None)
    updated = _iso(_updated(request, moment))
    return {"id": request.id, "status": status, "createdAt": _iso(request.created), "updatedAt": updated,
            "type": request.title.kind, "is4k": False, "serverId": 0 if status != PENDING else None, "profileId": None,
            "rootFolder": None, "languageProfileId": None, "tags": None, "isAutoRequest": False, "ignoreQuota": False,
            "media": _media(world, request.title, moment),
            "seasons": [{"id": request.id * 10 + place, "seasonNumber": season, "status": status,
                         "createdAt": _iso(request.created), "updatedAt": updated}
                        for place, season in enumerate(request.seasons, start=1)],
            "modifiedBy": _user(world, request.viewer if request.viewer["is_admin"] else admin, moment)
            if status != PENDING and admin else None,
            "requestedBy": _user(world, request.viewer, moment), "seasonCount": len(request.seasons), "canRemove": True}


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------

def _available(world: _World, request: _Request, moment: datetime) -> bool:
    return _media_status(world, request.title, moment) in (PARTLY, AVAILABLE)


# Seerr's `filter` of the requests: which of them it lists.
FILTERS: dict[str, Callable[[_World, _Request, datetime], bool]] = {
    "all": lambda world, r, at: True,
    "pending": lambda world, r, at: _status(r, at) == PENDING,
    "approved": lambda world, r, at: _status(r, at) == APPROVED,
    "declined": lambda world, r, at: _status(r, at) == DECLINED,
    "completed": lambda world, r, at: _status(r, at) == COMPLETED,
    "processing": lambda world, r, at: _status(r, at) == APPROVED and not _available(world, r, at),
    "available": lambda world, r, at: _available(world, r, at),
    "unavailable": lambda world, r, at: _status(r, at) in (PENDING, APPROVED) and not _available(world, r, at),
}


def _whole(value: str | None, fallback: int, lowest: int = 0) -> int:
    try:
        return max(lowest, int(value or ""))
    except ValueError:
        return fallback


def _requests(world: _World, params: httpx.QueryParams, moment: datetime) -> httpx.Response:
    """`request`: one page of the requests Seerr has, filtered and sorted."""
    choose = FILTERS.get(params.get("filter") or "all")
    if choose is None:
        return httpx.Response(400, json={"message": "request.query.filter should be equal to one of the allowed values"})
    take, skip = _whole(params.get("take"), 10, 1), _whole(params.get("skip"), 0)
    found = [r for r in world.requests if _visible(r, moment) and choose(world, r, moment)]
    by = (lambda r: (_updated(r, moment), r.id)) if params.get("sort") == "modified" else (lambda r: (r.created, r.id))
    found.sort(key=by, reverse=(params.get("sortDirection") or "desc") != "asc")
    return httpx.Response(200, json={
        "pageInfo": {"pages": math.ceil(len(found) / take), "pageSize": take, "results": len(found), "page": skip // take + 1},
        "results": [_record(world, r, moment) for r in found[skip:skip + take]]})


def _count(world: _World, moment: datetime) -> dict[str, int]:
    """`request/count`: how many requests in which state."""
    visible = [r for r in world.requests if _visible(r, moment)]
    return {"total": len(visible), "movie": sum(r.title.kind == "movie" for r in visible),
            "tv": sum(r.title.kind == "tv" for r in visible)} | {
        name: sum(FILTERS[name](world, r, moment) for r in visible)
        for name in ("pending", "approved", "declined", "processing", "available", "completed")}


def _one(world: _World, number: str, moment: datetime) -> dict[str, Any] | None:
    return next((_record(world, r, moment) for r in world.requests if str(r.id) == number and _visible(r, moment)), None)


def _title(world: _World, kind: str, tmdb: str) -> dict[str, Any] | None:
    """`movie/{tmdb}`, `tv/{tmdb}`: the name TMDB gives and its poster's path (made from the id: TMDB's shape)."""
    title = next((t for t in world.titles if t.kind == kind and t.tmdb == tmdb), None)
    if title is None:
        known = demo_world.movies() if kind == "movie" else demo_world.series()
        entry = next((e for e in known if e["tmdb_id"] == tmdb), None)
        title = _Title(kind, entry["title"], tmdb, year=entry["year"]) if entry else None
    if title is None:
        return None
    poster = f"/{demo_world.jellyfin_id(f'seerr-poster:{kind}:{tmdb}')[:27]}.jpg"
    date = f"{title.year or 2000}-01-01"
    if kind == "tv":
        return {"id": int(tmdb), "name": title.name, "originalName": title.name, "firstAirDate": date, "posterPath": poster}
    return {"id": int(tmdb), "title": title.name, "originalTitle": title.name, "releaseDate": date, "posterPath": poster}


def _users(world: _World, params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    take, skip = _whole(params.get("take"), 10, 1), _whole(params.get("skip"), 0)
    users = [_user(world, v, moment) for v in world.viewers]
    return {"pageInfo": {"pages": math.ceil(len(users) / take), "pageSize": take, "results": len(users), "page": skip // take + 1},
            "results": users[skip:skip + take]}


def _about(world: _World, moment: datetime) -> dict[str, Any]:
    return {"version": VERSION, "totalMediaItems": sum(t.available is not None and t.available <= moment for t in world.titles),
            "totalRequests": sum(_visible(r, moment) for r in world.requests), "appDataPath": "/app/config"}


STATUS = {"version": VERSION, "commitTag": f"v{VERSION}", "updateAvailable": False, "commitsBehind": 0, "restartRequired": False}
PUBLIC = {"applicationTitle": "Seerr", "applicationUrl": "", "cacheImages": True, "initialized": True, "localLogin": True,
          "mediaServerType": 2}


def _get(world: _World, path: str, params: httpx.QueryParams, moment: datetime) -> httpx.Response:
    parts = path.split("/")
    found: Any = None
    if path == "request":
        return _requests(world, params, moment)
    if path == "request/count":
        found = _count(world, moment)
    elif len(parts) == 2 and parts[0] == "request":
        found = _one(world, parts[1], moment)
    elif len(parts) == 2 and parts[0] in ("movie", "tv"):
        found = _title(world, parts[0], parts[1])
    elif path == "user":
        found = _users(world, params, moment)
    elif len(parts) == 2 and parts[0] == "user":
        found = next((_user(world, v, moment) for v in world.viewers if str(v["number"]) == parts[1]), None)
    elif path == "status":
        found = STATUS
    elif path == "settings/about":
        found = _about(world, moment)
    elif path == "settings/public":
        found = PUBLIC
    return httpx.Response(200, json=found) if found is not None else httpx.Response(404, json={"message": "Not Found"})


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Seerr's answer to one request of the API (see the module's docstring)."""
    if API not in request.url.path:
        return httpx.Response(404)              # the images (/imageproxy/…): none, the cards draw a placeholder
    path = request.url.path.split(API, 1)[1].strip("/").lower()
    world, moment = _world(), demo_world.now()
    if request.method == "GET":
        return _get(world, path, request.url.params, moment)
    parts = path.split("/")
    if request.method == "POST" and len(parts) == 3 and parts[0] == "request" and parts[2] in ("approve", "decline", "pending"):
        found = _one(world, parts[1], moment)
        if found is not None:
            return httpx.Response(200, json=found)          # accepted - the demo stays as it is
    return httpx.Response(404, json={"message": "Not Found"})


# ---------------------------------------------------------------------------
# What Jellyscope caught at its earlier syncs
# ---------------------------------------------------------------------------

def _next_sync(moment: datetime) -> datetime:
    """When Jellyscope's hourly sync of Seerr ran next after `moment`."""
    tick = moment.replace(minute=SYNC_MINUTE, second=0, microsecond=0)
    return tick if tick > moment else tick + timedelta(hours=1)


def sightings(until: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What Jellyscope's hourly syncs saw before `until`: (the requests seen waiting, those seen approved), each record
    as at that sync. A request decided (or deleted) before the next sync was never seen waiting, one done before the
    next sync never approved."""
    world = _world()
    waiting, approved = [], []
    for request in world.requests:
        tick = _next_sync(request.created)
        if tick < min(t for t in (request.decided, request.deleted, until) if t):
            waiting.append(_record(world, request, tick))
        if request.decided and not request.declined:
            tick = _next_sync(request.decided)
            if tick < min(t for t in (request.completed, until) if t):
                approved.append(_record(world, request, tick))
    return waiting, approved


def fill_past(until: datetime | None = None) -> None:
    """Replay what Jellyscope's earlier syncs saw (`sightings`) through the sync's writer, then sync again.

    Seerr stores neither that a request waited nor when it was approved or declined: Jellyscope catches it while
    syncing (sync.write_records notes the approval of a request it saw waiting). The demo's seed syncs once, so it
    would know none of it - this writes the sightings in their order (the waiting ones, then the approved ones), and
    the sync of now then stores the present state over them and marks the request deleted in Seerr (it saw it
    before, Seerr no longer has it). Called by the seed (demo_apps) after the sync, in a thread of its own.
    """
    moment = until or demo_world.now()
    now = db.utcnow()
    for records in sightings(moment):
        sync.write_records(records, now)
    asyncio.run(sync.synchronize())
