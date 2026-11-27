"""The demo's made-up Prowlarr: the indexers of the demo's apps as Prowlarr 2.7's API answers them.

The demo (JELLYSCOPE_DEMO) shows Prowlarr through its real code: the seed connects it at http://prowlarr.demo and runs
its real sync and sample (demo_apps.seed_apps), the page asks for the live state, the background loop for the
indexers' status. `handler` answers them inside the process (httpx.MockTransport through demo_switch) in the shape of
a real Prowlarr (tests/prowlarr_responses/, tests/_apps.py) - every path the package's client asks:

* `system/status` - the version (the connection test);
* `indexer` - demo_world.INDEXERS in Prowlarr's order (ids from 1), the ones the made-up apps grab from; the
  switched-off one (DISABLED_INDEXER) is not enabled;
* `indexerstatus` - who Prowlarr leaves out right now: the flaky indexer (FLAKY_INDEXER) during its outages
  (`down_at`), with since when it fails, its last failure and until when it is skipped. Not answered while the demo's
  data are made again (demo_world.REFRESHING): the loop's round then measures nothing (see `fill_past`);
* `applications` - Sonarr, Radarr, Lidarr and Readarr; `health` - an update is available, and while the flaky
  indexer is out the warning naming it;
* `history` - in pages from the newest (`page`, `pageSize`, `sortDirection`, `totalRecords`): the apps' RSS every few
  hours to their indexers, a few searches a day by the household (in the apps and in Prowlarr itself), and the
  grabs of the library's titles - shortly before each came into the library (a season that came at once is one
  pack), some upgraded later - each after a search of all the app's indexers. The flaky indexer's queries fail when
  its outage starts and at every retry, in between Prowlarr leaves it out; nothing is grabbed from it then, a
  release it fetches itself fails now and then (the app takes the next one). The switched-off indexer found little
  and has had no query since it was switched off. The numbers grow with the time, one by one.

Everything else is 404; a `command` (POST) is accepted with 201 and changes nothing. The outages follow demo_world's
schedule (one in every two days, 20-40 minutes, `outages`), the same at every seed and the one Sonarr's and Radarr's
health warn by. The history is anchored to
the library (demo_world.newest_arrival: Prowlarr came INSTALLED_DAYS before its newest file, the switched-off indexer
was switched off SWITCHED_OFF_DAYS before it), so it keeps its times and numbers from one seed to the next while the
clock (`demo_world.now()`) only decides what has happened by then.

Jellyscope measured the indexers' status all that time: `fill_past` (the seed, demo_apps, after the sync) writes the
samples the background loop would have taken, through the real writer (samples.store_watched, extend_watched) - a
sample at every change of the state (`past_states`), measured up to one round before the next - and the demo's loop
measures on from there (web/start.py).

Registered by demo_apps.load_fakes through the registry's `App.demo`, in the demo mode only - the door of the
package does not import this module.
"""
from __future__ import annotations

import re
import time
import unicodedata
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from typing import Any

import httpx

from ... import db, demo_world
from ...demo_world import random_for, span, weighted
from .samples import MOVED, delete_old_samples, extend_watched, store_watched
from .settings import watch_settings

API = "/api/v1/"
VERSION = "2.7.0.5723"
UPDATE = "2.8.1.5790"            # the version its update check offers (a harmless warning)
NUMBERS = {name: number for number, (name, _protocol) in enumerate(demo_world.INDEXERS, start=1)}
FLAKY, DISABLED = NUMBERS[demo_world.FLAKY_INDEXER], NUMBERS[demo_world.DISABLED_INDEXER]
# Each indexer: (privacy, Prowlarr only redirects the app to it, its answer in ms, the share of searches it finds nothing
# for). The flaky one is fetched through Prowlarr (redirect off), so a failed fetch is known; the switched-off one found little.
TRAITS = {"Hvezdarna": ("private", True, (180, 450), 0.06), "Sklepeni": ("private", False, (320, 900), 0.1),
          "Kometa": ("private", False, (700, 2400), 0.2), "Pavlac": ("public", False, (260, 750), 0.12),
          "Mlynek": ("public", False, (900, 3200), 0.75)}
# The apps: (name, RSS every N hours, at minute M of the grid, its indexers, the query type of its search, categories).
APPS = (("Sonarr", 3, 7, ("Hvezdarna", "Sklepeni", "Kometa", "Pavlac", "Mlynek"), "tvsearch", "5000,5030,5040"),
        ("Radarr", 4, 23, ("Hvezdarna", "Sklepeni", "Kometa", "Pavlac", "Mlynek"), "movie", "2000,2040,2045"),
        ("Lidarr", 8, 41, ("Hvezdarna", "Pavlac"), "music", "3000,3010,3040"),
        ("Readarr", 8, 53, ("Hvezdarna", "Sklepeni"), "book", "7000,7020,3030"),
        ("Prowlarr", 0, 0, ("Hvezdarna", "Sklepeni", "Kometa", "Pavlac", "Mlynek"), "search", ""))
BY_NAME = {app[0]: app for app in APPS}
# Which indexer an app grabs from (never the switched-off one, the flaky one less).
GRAB_WEIGHTS = {"Hvezdarna": 35, "Sklepeni": 30, "Kometa": 12, "Pavlac": 23}
# A day's searches by the household: how many, at which hour (UTC; evenings), in which app.
SEARCH_COUNTS = ((2, 25), (3, 30), (4, 25), (5, 15), (6, 5))
SEARCH_HOURS = tuple((hour, weight) for hour, weight in enumerate(
    (1, 1, 1, 1, 1, 1, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 6, 8, 9, 10, 9, 7, 4, 2)))
SEARCHERS = (("Sonarr", 40), ("Radarr", 30), ("Lidarr", 10), ("Readarr", 8), ("Prowlarr", 12))
# How long before a title came into the library it was grabbed (seconds, weighted ranges).
DOWNLOADS = ((180, 1500, 40), (1500, 7200, 40), (7200, 28800, 20))
UPGRADE_SHARE, FETCH_FAILS = 0.3, 0.3   # titles upgraded later; releases the flaky indexer fails to hand over
PACK_GAP = 30 * 60                      # episodes that came within this of each other were one season pack
VIDEO = ("DVDRip.XviD", "720p.WEB-DL.DD5.1.H.264", "1080p.WEB-DL.DDP5.1.H.264", "1080p.BluRay.x264",
         "2160p.WEB-DL.DDP5.1.HDR.H.265")
GROUPS = ("CZDAB", "NOCNI", "KVALITA", "HRNEK", "SUMAVA", "VLTAVA")
INSTALLED_DAYS = 370                    # Prowlarr came this many days before the library's newest file
SWITCHED_OFF_DAYS = 12                  # the switched-off indexer was switched off this many days before it
MEASURED_AFTER = 2 * 3600               # Jellyscope began measuring the indexers' status this long after Prowlarr came
HORIZON = 6 * 3600                      # the history is made this far ahead and served up to the moment
# In the flaky indexer's outages (demo_world.flaky_outages) Prowlarr leaves it out from the first failure; it retries it
# after each block (RETRIES after the start) until a query works again.
RETRIES = (0, 5 * 60, 15 * 60, 30 * 60)
GRID_EPOCH = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp())   # the apps' RSS grid counts from here
FAILED_MS = (15_000, 30_000)
END = "9999-12-31 23:59:59"
Row = tuple[int, str | None, str | None, str | None]


# ---------------------------------------------------------------------------
# The schedule of outages and the frame of the history
# ---------------------------------------------------------------------------

def _seconds(moment: datetime) -> int:
    return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp())


def _moment(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _text(seconds: int) -> str:
    return _moment(seconds).strftime(db.TIME_FORMAT)


def outages(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """The flaky indexer's outages as Prowlarr leaves it out, from the first failure to the query that worked again:
    those that start in [start, end) or still run at `start` (demo_world's schedule)."""
    return [(_moment(a), _moment(b)) for a, b in demo_world.flaky_outages(_seconds(start), _seconds(end))]


def down_at(indexer_id: int, moment: datetime) -> bool:
    """Whether Prowlarr leaves the indexer out at `moment` because of failures: only the flaky one, in its outages."""
    return indexer_id == FLAKY and demo_world.flaky_outage(_seconds(moment)) is not None


def _block(seconds: int) -> tuple[int, int, int] | None:
    """The flaky indexer's block at the moment: (failing since, its most recent failure, skipped until), or None."""
    outage = demo_world.flaky_outage(seconds)
    if outage is None:
        return None
    start, end = outage
    points = [start + retry for retry in RETRIES if start + retry < end]
    last = max(point for point in points if point <= seconds)
    later = [point for point in points if point > last]
    return start, last, later[0] if later else end


@dataclass(frozen=True)
class _Frame:
    installed: int          # Prowlarr came: its history starts
    measured_from: int      # Jellyscope's loop began measuring
    switched_off: int       # the switched-off indexer was switched off


def _after_outage(seconds: int) -> int:
    """The moment, or the end of the flaky indexer's outage running then: a change of the frame never falls inside one."""
    outage = demo_world.flaky_outage(seconds)
    return outage[1] if outage else seconds


def _frame(anchor: datetime) -> _Frame:
    """The history's frame for the library whose newest file came at `anchor` (the same from one seed to the next)."""
    day = (anchor - timedelta(days=INSTALLED_DAYS)).date()
    installed = _seconds(datetime.combine(day, clock_time(18, 12), UTC))
    off_day = (anchor - timedelta(days=SWITCHED_OFF_DAYS)).date()
    return _Frame(installed, _after_outage(installed + MEASURED_AFTER),
                  _after_outage(_seconds(datetime.combine(off_day, clock_time(20, 37, 14), UTC))))


def _on(name: str, seconds: int, frame: _Frame) -> bool:
    """Whether Prowlarr asks the indexer at the moment: switched on and not left out."""
    if name == demo_world.DISABLED_INDEXER:
        return seconds < frame.switched_off
    return name != demo_world.FLAKY_INDEXER or demo_world.flaky_outage(seconds) is None


# ---------------------------------------------------------------------------
# The history: RSS, searches, grabs
# ---------------------------------------------------------------------------

@dataclass
class _World:
    anchor: datetime
    frame: _Frame
    horizon: int
    events: list[tuple[int, int, int, str, bool, dict[str, Any]]] = field(default_factory=list)
    times: list[int] = field(default_factory=list)

    def add(self, seconds: int, order: int, name: str, kind: str, successful: bool, data: dict[str, Any]) -> None:
        if self.frame.installed <= seconds <= self.horizon:
            self.events.append((seconds, order, NUMBERS[name], kind, successful, data))


def _query(rng: Any, app: str, name: str, *, results: int | None = None, query: str = "", rss: bool = False) -> dict[str, Any]:
    """The `data` of one answered query: how long it took, what it found (`results`, else drawn from the indexer's traits)."""
    _privacy, _redirect, (fastest, slowest), nothing = TRAITS[name]
    _app, _every, _minute, _indexers, query_type, categories = BY_NAME[app]
    elapsed = rng.randint(fastest, slowest)
    found = results if results is not None else (0 if rng.random() < nothing else rng.randint(1, 60))
    head = {"limit": "100", "offset": "0"} if rss else {}
    return {**head, "elapsedTime": str(elapsed), "query": query, "queryType": "search" if rss else query_type,
            "categories": categories, "source": app, "host": f"{app.lower()}.demo", "queryResults": str(found), "cached": "0"}


def _rss(world: _World, start: int, end: int) -> None:
    """Every app's RSS to each of its indexers at its grid (a few seconds apart), day by day from a source of its own."""
    for app, every, minute, indexers, _type, _categories in APPS:
        if not every:
            continue
        step, offset = every * 3600, GRID_EPOCH + minute * 60
        for day in range(start // 86_400, end // 86_400 + 1):
            rng = random_for(f"prowlarr:rss:{app}:{day}")
            slot = offset + -(-(day * 86_400 - offset) // step) * step
            while slot < (day + 1) * 86_400:
                for place, name in enumerate(indexers):
                    found = rng.randint(0, 15) if name == demo_world.DISABLED_INDEXER else rng.randint(60, 100)
                    data = _query(rng, app, name, results=found, rss=True)
                    moment = slot + 2 * place
                    if _on(name, moment, world.frame):
                        world.add(moment, NUMBERS[name], name, "indexerRss", True, data)
                slot += step


def _failures(world: _World, start: int, end: int) -> None:
    """The flaky indexer's failed queries: when its outage starts and at every retry within it."""
    for outage_start, outage_end in demo_world.flaky_outages(start, end):
        rng = random_for(f"prowlarr:failures:{outage_start}")
        for place, retry in enumerate(RETRIES):
            if outage_start + retry >= outage_end:
                break
            app = weighted(rng, (("Sonarr", 60), ("Radarr", 40)))
            data = {**_query(rng, app, demo_world.FLAKY_INDEXER, results=0, rss=place == 0),
                    "elapsedTime": str(rng.randint(*FAILED_MS))}
            world.add(outage_start + retry, 10 + FLAKY, demo_world.FLAKY_INDEXER, "indexerRss" if place == 0 else "indexerQuery",
                      False, data)


def _search(world: _World, rng: Any, app: str, at: int, order: int, *, query: str = "", hit: str = "") -> list[tuple[str, dict[str, Any]]]:
    """One search of the app: a query to each of its indexers Prowlarr asks then, a second apart. `hit` surely finds something."""
    asked = []
    for place, name in enumerate(BY_NAME[app][3]):
        data = _query(rng, app, name, query=query, results=rng.randint(3, 40) if name == hit else None)
        if _on(name, at + place, world.frame):
            world.add(at + place, order + NUMBERS[name], name, "indexerQuery", True, data)
            asked.append((name, data))
    return asked


def _searches(world: _World, start: int, end: int, titles: list[str]) -> None:
    """The household's searches, day by day: in the apps and in Prowlarr itself, some repeated from Prowlarr's cache."""
    for day in range(start // 86_400, end // 86_400 + 1):
        rng = random_for(f"prowlarr:searches:{day}")
        for _ in range(weighted(rng, SEARCH_COUNTS)):
            at = day * 86_400 + weighted(rng, SEARCH_HOURS) * 3600 + rng.randint(0, 3599)
            app = weighted(rng, SEARCHERS)
            query = rng.choice(titles) if titles and app == "Prowlarr" else ""
            asked = _search(world, rng, app, at, 20, query=query)
            if rng.random() < 0.1:
                again = at + rng.randint(60, 240)
                for place, (name, data) in enumerate(asked):
                    world.add(again + place, 30 + NUMBERS[name], name, "indexerQuery", True, {**data, "elapsedTime": "0", "cached": "1"})


@dataclass(frozen=True)
class _Unit:
    """One title an app grabbed: who, its own random source, the release's name without the quality, when the library
    got it (epoch seconds), the quality of the library's file (VIDEO; -1 = music or a book) and whether it may be upgraded."""
    app: str
    key: str
    name: str
    arrival: int
    quality: int
    upgradable: bool


def _plain(text: str) -> str:
    """A title in a release's name: ASCII words between dots."""
    letters = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return ".".join(re.findall(r"[A-Za-z0-9]+", letters))


def _episode_units() -> list[_Unit]:
    """The series' episodes in the order they came: those that came within PACK_GAP of each other were one season pack."""
    units = []
    for series in demo_world.series():
        episodes = sorted((e for season in series["seasons"].values() for e in season if e["added_at"]), key=lambda e: e["added_at"])
        group: list[dict[str, Any]] = []
        for episode in [*episodes, None]:
            if episode is not None and group and (episode["added_at"] - group[-1]["added_at"]).total_seconds() <= PACK_GAP:
                group.append(episode)
                continue
            if group:
                first = group[0]
                label = f"S{first['season']:02d}" if len(group) > 1 else f"S{first['season']:02d}E{first['episode']:02d}"
                units.append(_Unit("Sonarr", f"episode:{first['number']}", f"{_plain(series['title'])}.{label}",
                                   _seconds(first["added_at"]), 3 if len(group) > 1 else 2, len(group) == 1))
            group = [episode] if episode is not None else []
    return units


def _units() -> list[_Unit]:
    """Every title the apps grabbed: the library's episodes, movies (a title once: a second copy came by hand), albums, books."""
    units = _episode_units()
    seen: set[str] = set()
    for movie in demo_world.movies():
        if movie["added_at"] is None or movie["title"] in seen:
            continue
        seen.add(movie["title"])
        height = movie["height"]
        quality = 4 if height >= 2160 else 3 if height >= 1080 and movie["size_bytes"] > 9 * 1024 ** 3 else 2 if height >= 1080 \
            else 1 if height >= 720 else 0
        units.append(_Unit("Radarr", f"movie:{movie['number']}", f"{_plain(movie['title'])}.{movie['year']}",
                           _seconds(movie["added_at"]), quality, quality > 0))
    for album in demo_world.albums():
        if album["added_at"] is not None:
            kind = "FLAC" if album["codec"] == "flac" else "MP3 320"
            units.append(_Unit("Lidarr", f"album:{album['number']}", f"{album['artist']} - {album['title']}{_year(album)} [{kind}]",
                               _seconds(album["added_at"]), -1, False))
    for book in demo_world.books():
        if book["added_at"] is not None:
            kind = "M4B" if book["audiobook"] else (book["format"] or "epub").upper()
            units.append(_Unit("Readarr", f"book:{book['number']}", f"{book['author']} - {book['title']}{_year(book)} {kind}",
                               _seconds(book["added_at"]), -1, False))
    return units


def _year(title: dict[str, Any]) -> str:
    return f" ({title['year']})" if title.get("year") else ""


def _release(unit: _Unit, quality: int, rng: Any) -> str:
    return unit.name if unit.quality < 0 else f"{unit.name}.{VIDEO[quality]}-{rng.choice(GROUPS)}"


def _grab(world: _World, rng: Any, unit: _Unit, at: int, quality: int) -> None:
    """The app's search of all its indexers, then the grab from one of them a little later. A release the flaky indexer
    fails to hand over: the app takes the next one from another indexer."""
    candidates = [(name, GRAB_WEIGHTS[name]) for name in BY_NAME[unit.app][3] if name in GRAB_WEIGHTS and _on(name, at, world.frame)]
    chosen = weighted(rng, candidates)
    _search(world, rng, unit.app, at - 40, 40, hit=chosen)
    title = _release(unit, quality, rng)
    for attempt in range(2):
        _privacy, redirect, (fastest, slowest), _nothing = TRAITS[chosen]
        fails = chosen == demo_world.FLAKY_INDEXER and attempt == 0 and rng.random() < FETCH_FAILS
        data = {"source": unit.app, "host": f"{unit.app.lower()}.demo", "grabMethod": "Redirect" if redirect else "Proxy",
                "grabTitle": title}
        if not redirect:
            data["elapsedTime"] = str(rng.randint(fastest, slowest) * (8 if fails else 1))
        world.add(at, 50 + NUMBERS[chosen], chosen, "releaseGrabbed", not fails, data)
        if not fails:
            return
        at += rng.randint(90, 300)
        chosen = weighted(rng, [(name, weight) for name, weight in candidates if name != chosen])


def _grabs(world: _World, units: list[_Unit]) -> None:
    """Every title grabbed shortly before the library got it; some upgraded later (a worse release first)."""
    for unit in units:
        rng = random_for(f"prowlarr:{unit.key}")
        at = unit.arrival - int(span(rng, DOWNLOADS))
        upgraded = unit.upgradable and rng.random() < UPGRADE_SHARE
        later = unit.arrival + rng.randint(2 * 86_400, 40 * 86_400)
        if at <= world.frame.installed + 3600:
            continue
        _grab(world, rng, unit, at, unit.quality - 1 if upgraded else unit.quality)
        if upgraded:
            _grab(world, rng, unit, later, unit.quality)


def _build(anchor: datetime, horizon: int) -> _World:
    """Prowlarr's whole history up to `horizon`, oldest first; an event's number is its place (from 1)."""
    world = _World(anchor, _frame(anchor), horizon)
    start = world.frame.installed
    units = _units()
    _rss(world, start, horizon)
    _failures(world, start, horizon)
    _searches(world, start, horizon, sorted({unit.name.rsplit(".", 1)[0].replace(".", " ") for unit in units if unit.app == "Radarr"}))
    _grabs(world, units)
    world.events.sort(key=lambda event: (event[0], event[1]))
    world.times = [event[0] for event in world.events]
    return world


_MEMORY: dict[str, _World] = {}   # the last history made (rebuilt for another library or past its horizon)


def _world(moment: datetime) -> _World | None:
    """The history that holds for the library now (None while it is empty), made at most once for a library and horizon."""
    anchor = demo_world.newest_arrival()
    if anchor is None:
        return None
    world = _MEMORY.get("history")
    if world is None or world.anchor != anchor or world.horizon < _seconds(moment):
        world = _MEMORY["history"] = _build(anchor, _seconds(moment) + HORIZON)
    return world


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------

def _whole(value: str | None, fallback: int) -> int:
    try:
        return max(1, int(value or ""))
    except ValueError:
        return fallback


def _history(params: dict[str, str], moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    count = bisect_right(world.times, _seconds(moment)) if world else 0
    page, size = _whole(params.get("page"), 1), _whole(params.get("pageSize"), 10)
    direction = params.get("sortDirection") or "descending"
    places = range(count - 1 - (page - 1) * size, max(-1, count - 1 - page * size), -1) if direction == "descending" \
        else range((page - 1) * size, min(count, page * size))
    records = []
    for place in places:
        seconds, _order, number, kind, successful, data = world.events[place]
        records.append({"indexerId": number, "date": demo_world.iso(_moment(seconds)), "successful": successful,
                        "eventType": kind, "data": data, "id": place + 1})
    return {"page": page, "pageSize": size, "sortKey": params.get("sortKey") or "date", "sortDirection": direction,
            "totalRecords": count, "records": records}


def _indexers(_params: dict[str, str], moment: datetime) -> list[dict[str, Any]]:
    anchor = demo_world.newest_arrival() or moment
    added = demo_world.iso(_moment(_frame(anchor).installed + 600))
    listed = []
    for number, (name, protocol) in enumerate(demo_world.INDEXERS, start=1):
        privacy, redirect, _ms, _nothing = TRAITS[name]
        kind = "Newznab" if protocol == "usenet" else "Torznab"
        address = f"https://{name.lower()}.demo"
        listed.append({
            "indexerUrls": [address], "legacyUrls": [], "definitionName": kind,
            "description": "Newznab is an API search specification for Usenet" if kind == "Newznab" else "A Newznab-like api for torrents.",
            "language": "cs-CZ", "encoding": "Unicode (UTF-8)", "enable": number != DISABLED, "redirect": redirect,
            "supportsRss": True, "supportsSearch": True, "supportsRedirect": True, "supportsPagination": True,
            "appProfileId": 1, "protocol": protocol, "privacy": privacy, "priority": 25, "downloadClientId": 0, "added": added,
            "sortName": name.lower(), "name": name,
            "fields": [{"order": 0, "name": "baseUrl", "label": "Url", "value": address, "type": "textbox", "advanced": False,
                        "privacy": "normal", "isFloat": False}],
            "implementationName": kind, "implementation": kind, "configContract": f"{kind}Settings",
            "infoLink": f"https://wiki.servarr.com/prowlarr/supported-indexers#{kind.lower()}", "tags": [], "id": number})
    return listed


def _status(_params: dict[str, str], moment: datetime) -> list[dict[str, Any]] | httpx.Response:
    if demo_world.REFRESHING.is_set():
        return httpx.Response(503)
    block = _block(_seconds(moment))
    if block is None:
        return []
    since, last, until = (demo_world.iso(_moment(seconds)) for seconds in block)
    return [{"indexerId": FLAKY, "disabledTill": until, "mostRecentFailure": last, "initialFailure": since}]


def _applications(_params: dict[str, str], _moment_now: datetime) -> list[dict[str, Any]]:
    return [{"syncLevel": "fullSync", "enable": True, "name": name, "implementationName": name, "implementation": name,
             "configContract": f"{name}Settings", "infoLink": f"https://wiki.servarr.com/prowlarr/supported#{name.lower()}",
             "tags": [], "fields": [{"order": 0, "name": "prowlarrUrl", "value": "http://prowlarr.demo"},
                                    {"order": 1, "name": "baseUrl", "value": f"http://{name.lower()}.demo"}],
             "id": number} for number, (name, *_rest) in enumerate(APPS[:4], start=1)]


def _health(_params: dict[str, str], moment: datetime) -> list[dict[str, Any]]:
    found = [{"source": "UpdateCheck", "type": "warning", "message": f"New update is available: v{UPDATE}",
              "wikiUrl": "https://wiki.servarr.com/prowlarr/system#new-update-is-available"}]
    if demo_world.flaky_outage(_seconds(moment)):
        found.append({"source": "IndexerStatusCheck", "type": "warning",
                      "message": f"Indexers unavailable due to failures: {demo_world.FLAKY_INDEXER}",
                      "wikiUrl": "https://wiki.servarr.com/prowlarr/system#indexers-are-unavailable-due-to-failures"})
    return found


def _system(_params: dict[str, str], moment: datetime) -> dict[str, Any]:
    started = moment.replace(hour=4, minute=12, second=9) - timedelta(days=3)
    return {"appName": "Prowlarr", "instanceName": "Prowlarr", "version": VERSION, "buildTime": "2026-10-26T18:06:59Z",
            "isDebug": False, "isProduction": True, "isAdmin": False, "isUserInteractive": True, "startupPath": "/app/prowlarr/bin",
            "appData": "/config", "osName": "ubuntu", "osVersion": "24.04", "isNetCore": True, "isLinux": True, "isOsx": False,
            "isWindows": False, "isDocker": True, "isContainerized": True, "mode": "console", "branch": "master",
            "databaseType": "sqLite", "databaseVersion": "3.53.4", "authentication": "forms", "migrationVersion": 44,
            "urlBase": "", "runtimeVersion": "8.0.27", "runtimeName": "netcore", "startTime": demo_world.iso(started),
            "packageUpdateMechanism": "docker"}


ANSWERS: dict[str, Callable[[dict[str, str], datetime], Any]] = {
    "system/status": _system, "indexer": _indexers, "indexerstatus": _status, "applications": _applications,
    "health": _health, "history": _history}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Prowlarr's answer to one request (see the module's docstring)."""
    path = request.url.path.split(API, 1)[-1].strip("/").lower()
    if request.method != "GET":
        if request.method == "POST" and path == "command":
            return httpx.Response(201, json={"name": "ApplicationIndexerSync", "status": "queued", "id": 1})
        return httpx.Response(404)
    answer = ANSWERS.get(path)
    if answer is None:
        return httpx.Response(404)
    body = answer(dict(request.url.params), demo_world.now().astimezone(UTC).replace(microsecond=0))
    return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)


# ---------------------------------------------------------------------------
# The indexers' status Jellyscope measured over the past
# ---------------------------------------------------------------------------

def _rows(seconds: int) -> list[Row]:
    block = _block(seconds)
    return [] if block is None else [(FLAKY, _text(block[0]), _text(block[2]), _text(block[1]))]


def past_states(until: datetime) -> list[tuple[datetime, list[Row], list[int]]]:
    """What the background loop saw from when Jellyscope began measuring until `until`: at every change of the state
    (when it began, every outage's start and end, the indexer switched off) the moment, the indexers Prowlarr left out
    then as a sample's rows (number, failing since, skipped until, last failure) and the indexers on. [] while the
    library is empty."""
    anchor = demo_world.newest_arrival()
    if anchor is None:
        return []
    frame, end = _frame(anchor), _seconds(until)
    marks = {frame.measured_from} | {moment for start, stop in demo_world.flaky_outages(frame.measured_from, end)
                                     for moment in (start, stop)}
    marks.add(frame.switched_off)
    return [(_moment(moment), _rows(moment), [n for n in NUMBERS.values() if n != DISABLED or moment < frame.switched_off])
            for moment in sorted(marks) if frame.measured_from <= moment < end]


def _again(write: Callable[[], bool]) -> bool:
    """A write on the side meets another writer now and then (the live stream's every ten seconds): a few tries."""
    for attempt in range(3):
        if write():
            return True
        time.sleep(0.05 * (attempt + 1))
    return False


def fill_past(until: datetime | None = None) -> int:
    """Store the samples the background loop would have taken over the past, up to `until` (now): one at every change
    of the state (`past_states`), measured up to one round before the next, the last up to `until`. Returns how many.

    What samples there are go first: the one the seed's counts took (take_sample, when no loop runs yet) and a round
    of the loop whose write met the refresh's deletion (demodata.refresh) - the past is the schedule's alone. Through the real
    writer, with the loop's interval (Settings), as the loop would have stored them; the last one moves the marker of
    a stored change, so open pages redraw the availability.
    """
    end = (until or demo_world.now()).astimezone(UTC).replace(microsecond=0)
    states = past_states(end)
    if not states:
        return 0
    delete_old_samples(END)
    interval = int(watch_settings()["interval"])
    stored = 0
    for place, (moment, rows, enabled) in enumerate(states):
        last = place + 1 == len(states)
        upto = (end if last else states[place + 1][0] - timedelta(seconds=interval)).strftime(db.TIME_FORMAT)
        when = moment.strftime(db.TIME_FORMAT)
        if not _again(lambda: store_watched(when, rows, enabled, interval, last)):
            continue
        stored += 1
        if upto > when:
            _again(lambda: extend_watched(when, upto) == MOVED)
    return stored
