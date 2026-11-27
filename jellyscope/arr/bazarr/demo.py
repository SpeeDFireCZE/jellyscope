"""The demo's made-up Bazarr: subtitles for the demo's series and movies as Bazarr 1.6's API answers them.

The demo (JELLYSCOPE_DEMO) shows Bazarr through its real code: the seed connects it at http://bazarr.demo and runs its
real sync and counts (demo_apps.seed_apps), the page asks for the live state. `handler` answers them inside the
process (httpx.MockTransport through demo_switch) in the shape of a real Bazarr (tests/bazarr_responses/,
tests/_apps.py) - every path the package's client asks:

* `system/status` - the version, the versions of the Sonarr and Radarr it talks to, its zone (the connection test,
  the zone the sync reads the history's times in);
* `episodes/history`, `movies/history` - in pages from the newest (`start`, `length`, `total` of all): subtitles of
  the library's episodes and movies under the numbers the made-up Sonarr and Radarr give them (sonarrSeriesId,
  sonarrEpisodeId = demo_world's episode number, radarrId = the catalog's number). Bazarr looks for the languages of
  the title's profile (Czech, English - for the hearing impaired with some -, Slovak with some) as soon as the file
  comes: English is mostly there within minutes, Czech and Slovak fan subtitles of something new take hours to days,
  those of the back catalog exist already. Some are upgraded to a better match within a week, some synced, a few
  were picked by hand, uploaded as one's own file or translated. Files older than Bazarr itself got theirs in its
  first search after it was installed. The time is text in Bazarr's zone (`parsed_timestamp`, `%x %X`);
* `episodes/wanted`, `movies/wanted` - what still lacks a language of its profile (never found, or not out yet);
* `badges` - the counts of the wanted lists, of the throttled providers and of the warnings;
* `providers` - the providers, one of them throttled until the end of Bazarr's day (its daily download limit);
  `system/health` - empty: a real Bazarr reports a throttled provider in `providers` and `badges`, not as a health
  issue (tests/bazarr_responses/: two throttled, no issue).

Everything else is 404; a write (POST ...) to a path it knows is accepted with 204 and changes nothing. Every time
comes from the library (when a file came, demo_world's catalogs) and a random source of its own per item and
language (demo_world.random_for), never from the clock: the same library gives the same history, so the sync's
fingerprints (made of the time's text) never double. The moment (`demo_world.now()`) only decides what has happened
by then: the subtitles of the newest files come in the hours and days after the seed.

Registered by demo_apps.load_fakes through the registry's `App.demo`, in the demo mode only - the door of the
package does not import this module.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ... import demo_world
from ...demo_world import span, weighted
from .client import ACQUIRED

API = "/api/"
VERSION = "1.6.2"
ZONE_NAME = "Europe/Prague"
ZONE = ZoneInfo(ZONE_NAME)
TEXT_FORMAT = "%m/%d/%y %H:%M:%S"            # parsed_timestamp: strftime("%x %X") in the C locale, Bazarr's local time
SONARR_VERSION, RADARR_VERSION = "4.0.20.3014", "6.4.4.10685"     # those the made-up Sonarr and Radarr report
MAX_SCORE = {"episode": 360, "movie": 180}   # the points of a perfect match; the history writes them in percent
LANGUAGES = {"cs": ("Czech", "ces"), "en": ("English", "eng"), "sk": ("Slovak", "slk")}
# The languages profiles: the (language, for the hearing impaired) Bazarr looks for. Series by their number, movies by
# chance.
CS_EN, CS_EN_HI = (("cs", False), ("en", False)), (("cs", False), ("en", True))
CS_SK_EN, CS_SK = (("cs", False), ("sk", False), ("en", False)), (("cs", False), ("sk", False))
SERIES_PROFILES = {1: CS_EN, 2: CS_EN_HI, 3: CS_EN, 4: CS_SK_EN, 5: CS_SK, 6: CS_EN}
MOVIE_PROFILES = ((CS_EN, 60), (CS_EN_HI, 20), (CS_SK_EN, 20))
# The made-up subtitle providers: {name: (the kinds it has subtitles for, {language: weight}, the match in percent
# from-to)}.
PROVIDERS = {
    "titulkovna": (("episode", "movie"), {"cs": 45, "sk": 60, "en": 5}, (86, 100)),
    "prekladiste": (("episode", "movie"), {"cs": 25}, (80, 97)),
    "kinotitulky": (("episode", "movie"), {"cs": 20, "sk": 40, "en": 30}, (72, 96)),
    "stopar": (("episode",), {"en": 45}, (90, 100)),
    "filmovka": (("movie",), {"cs": 10, "en": 35}, (72, 95)),
}
# The provider over its daily download limit: Bazarr leaves it out until the end of its day (as it does with
# OpenSubtitles.com's limit).
THROTTLED, THROTTLE_REASON = "kinotitulky", "DownloadLimitExceeded"
UPLOADED_PROVIDER = "manual"                 # the provider Bazarr writes for a file uploaded by hand
MANUAL_SCORE = (66, 88)                      # a manual pick: one the automatic search left out for its match
# (from, to minutes, weight) from the file's arrival to the subtitles of a language: (something new, the back catalog).
# English of something new comes with the release, Czech and Slovak fan subtitles take hours to days; the back
# catalog's exist already and come with one of Bazarr's next searches, any time of the day.
DELAYS = {
    "en": (((1, 12, 65), (12, 90, 20), (90, 720, 10), (720, 4320, 5)),
           ((1, 15, 30), (15, 360, 30), (360, 1440, 35), (1440, 4320, 5))),
    "cs": (((4, 60, 15), (60, 720, 30), (720, 2880, 35), (2880, 10080, 20)),
           ((1, 15, 25), (15, 360, 30), (360, 1440, 35), (1440, 10080, 10))),
    "sk": (((60, 1440, 30), (1440, 10080, 50), (10080, 30240, 20)),
           ((5, 360, 30), (360, 2880, 40), (2880, 30240, 30))),
}
NEVER = {"en": (0.04, 0.02), "cs": (0.1, 0.04), "sk": (0.4, 0.3)}    # never found by itself: (new, back catalog)
MANUAL_SHARE = 0.6           # of the Czech and English never found by itself: picked by hand in a manual search later
PICKED_SHARE = 0.06          # of the Czech and English found: someone wanted to watch at once and picked them by hand
TRANSLATED_SHARE = 0.4       # of the Slovak never found: translated from the Czech ones
SYNC_SHARE = 0.3             # downloads whose timing Bazarr synced right after
REPLACED_SHARE = 0.03        # Czech downloads that were bad: deleted, and one's own file uploaded
UPGRADE_SHARE = 0.4          # of the downloads below UPGRADE_BELOW: replaced by a better match within a week
UPGRADE_BELOW = {"episode": 97.0, "movie": 90.0}
UPGRADE_AFTER = ((4, 14, 50), (14, 60, 35), (60, 160, 15))   # (from, to hours, weight) after the download
INSTALLED_DAYS = 420         # Bazarr came this many days before the library's newest file: the older got theirs then
FIRST_SEARCH = (5, 480)      # minutes after it came: its first search went through those older files
BATCH = timedelta(minutes=30)          # episodes of a series that came this close together: a season at once (new)
# What a history record says, by its action (Bazarr's own sentences).
DESCRIPTIONS = {
    0: "{language} subtitles deleted from disk.",
    1: "{language} subtitles downloaded from {provider} with a score of {score}.",
    2: "{language} subtitles downloaded from {provider} with a score of {score} using manual search.",
    3: "{language} subtitles upgraded from {provider} with a score of {score}.",
    4: "{language} Subtitles manually uploaded.",
    5: "{language} subtitles synchronization ended with an offset of {offset} seconds and a framerate scale factor of 1.0.",
    6: "{language} subtitles translated from Czech.",
}
# What a match is made of, from the most common; a score's share of them matched (the rest `dont_matches`).
MATCHES = {"episode": ("series", "season", "episode", "year", "source", "resolution", "video_codec", "audio_codec",
                       "release_group", "hash"),
           "movie": ("title", "year", "source", "resolution", "video_codec", "imdb_id", "audio_codec", "edition",
                     "release_group", "hash")}
# Where a write is accepted: running a task (a search, an upgrade) and the paths it answers.
WRITES = ("system/tasks",)
HEALTH: list[dict[str, str]] = []


@dataclass
class _Item:
    """An episode or a movie Bazarr knows from Sonarr or Radarr, with its file."""

    kind: str                                  # "episode" or "movie"
    number: int                                # sonarrEpisodeId or radarrId
    series: int                                # sonarrSeriesId (0 for a movie)
    title: str                                 # the series' or the movie's
    episode: tuple[int, int, str]              # (season, episode, its title); a movie (0, 0, "")
    year: int
    arrived: datetime                          # when the file came (Jellyfin's added_at)
    new: bool                                  # out just now: airing, a season at once, a movie of the last weeks
    has_file: bool                             # the archive's movies have none
    profile: tuple[tuple[str, bool], ...]


@dataclass
class _Event:
    """One record of Bazarr's history."""

    item: _Item
    action: int                                # client.ACTIONS
    at: datetime
    language: str
    hi: bool
    provider: str | None = None
    score: float | None = None                 # in percent
    subs_id: str | None = None


@dataclass
class _World:
    """The made-up Bazarr: the items it knows and every subtitle event, newest first (some may lie ahead of now)."""

    items: list[_Item] = field(default_factory=list)
    events: list[_Event] = field(default_factory=list)
    # When an item first had the subtitles of a language of its profile: (kind, number, language, hi) -> the moment.
    found: dict[tuple[str, int, str, bool], datetime] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The world: what the made-up Bazarr found, from the library
# ---------------------------------------------------------------------------

def _batched(episodes: list[dict[str, Any]]) -> set[int]:
    """The episodes of a series that came within BATCH of another one: a whole season at once."""
    times = sorted((e["added_at"], e["number"]) for e in episodes)
    return {number for place, (at, number) in enumerate(times)
            if (place and at - times[place - 1][0] <= BATCH) or (place + 1 < len(times) and times[place + 1][0] - at <= BATCH)}


def _items() -> list[_Item]:
    """The library's episodes and movies with the numbers Sonarr and Radarr know them by."""
    items = []
    for entry in demo_world.series():
        episodes = [e for season in entry["seasons"].values() for e in season if e["added_at"]]
        batch = _batched(episodes)
        for e in episodes:
            new = e["number"] in batch or demo_world.aired(entry["number"], e["season"], e["episode"]) is not None
            items.append(_Item("episode", e["number"], entry["number"], entry["title"], (e["season"], e["episode"], e["title"]),
                               entry["year"] or 0, e["added_at"], new, True, SERIES_PROFILES.get(entry["number"], CS_EN)))
    titles: set[str] = set()
    for entry in demo_world.movies():
        # A second copy of a title (the demo's duplicates) was put into the library by hand: Radarr has the title once.
        if not entry["added_at"] or entry["title"] in titles:
            continue
        titles.add(entry["title"])
        profile = weighted(demo_world.random_for(f"bazarr-profile:{entry['number']}"), MOVIE_PROFILES)
        items.append(_Item("movie", entry["number"], 0, entry["title"], (0, 0, ""), entry["year"] or 0, entry["added_at"],
                           demo_world.movie_release(entry) is not None, not entry["missing"], profile))
    return items


def _settled(moment: datetime) -> datetime:
    """The moment, out of the hour Bazarr's local clock goes through twice (summer time ends): its text would be read
    back an hour off (bazarr/times.py takes the first one)."""
    return moment + timedelta(hours=1) if moment.astimezone(ZONE).fold else moment


def _score(rng: random.Random, kind: str, bounds: tuple[float, float]) -> float:
    """A match in percent on Bazarr's scale (whole points of MAX_SCORE), from `bounds`."""
    top = MAX_SCORE[kind]
    return round(100 * min(top, round(rng.uniform(*bounds) / 100 * top)) / top, 2)


def _provider(rng: random.Random, kind: str, language: str) -> str:
    """A provider that has subtitles of the kind in the language, by its weight."""
    return weighted(rng, ((name, weights[language]) for name, (kinds, weights, _bounds) in PROVIDERS.items()
                          if kind in kinds and language in weights))


def _add(world: _World, item: _Item, action: int, at: datetime, language: str, hi: bool, rng: random.Random,
         provider: str | None = None, score: float | None = None) -> _Event:
    """One record; subtitles from a provider carry its id of them."""
    subs_id = f"{rng.getrandbits(48):012x}" if provider and provider != UPLOADED_PROVIDER else None
    event = _Event(item, action, _settled(at), language, hi, provider, score, subs_id)
    world.events.append(event)
    key = (item.kind, item.number, language, hi)
    if action in ACQUIRED and (key not in world.found or event.at < world.found[key]):
        world.found[key] = event.at
    return event


def _download(world: _World, item: _Item, language: str, hi: bool, at: datetime, rng: random.Random) -> _Event:
    """Subtitles Bazarr found by itself, and what followed: a timing sync, an upgrade to a better match within a week,
    or a bad Czech one deleted and replaced by one's own file."""
    provider = _provider(rng, item.kind, language)
    first = _add(world, item, 1, at, language, hi, rng, provider, _score(rng, item.kind, PROVIDERS[provider][2]))
    if rng.random() < SYNC_SHARE:
        _add(world, item, 5, first.at + timedelta(seconds=rng.uniform(20, 150)), language, hi, rng)
    score = first.score or 0.0
    if score < UPGRADE_BELOW[item.kind] and rng.random() < UPGRADE_SHARE:
        better = _score(rng, item.kind, (score + 3, min(100.0, score + 14)))
        _add(world, item, 3, first.at + timedelta(hours=span(rng, UPGRADE_AFTER)), language, hi, rng,
             _provider(rng, item.kind, language), better)
    elif language == "cs" and rng.random() < REPLACED_SHARE:
        deleted = first.at + timedelta(hours=rng.uniform(1, 48))
        _add(world, item, 0, deleted, language, hi, rng)
        _add(world, item, 4, deleted + timedelta(minutes=rng.uniform(3, 40)), language, hi, rng, UPLOADED_PROVIDER, 100.0)
    return first


def _language(world: _World, item: _Item, language: str, hi: bool, start: datetime, czech: datetime | None) -> datetime | None:
    """The subtitles of one language of an item, from `start` (when Bazarr could first look). Returns when it first
    had them, None = still none (it is wanted). `czech`: when it had Czech ones (a Slovak translation is made from them)."""
    rng = demo_world.random_for(f"bazarr:{item.kind}:{item.number}:{language}")
    age = 0 if item.new else 1
    if rng.random() >= NEVER[language][age]:
        at = start + timedelta(minutes=span(rng, DELAYS[language][age]))
        if language != "sk" and rng.random() < PICKED_SHARE:
            picked = _add(world, item, 2, at, language, hi, rng, _provider(rng, item.kind, language),
                          _score(rng, item.kind, MANUAL_SCORE))
            if rng.random() < 2 * SYNC_SHARE:      # a pick of a poorer match is synced more often
                _add(world, item, 5, picked.at + timedelta(seconds=rng.uniform(20, 150)), language, hi, rng)
            return picked.at
        return _download(world, item, language, hi, at, rng).at
    if language == "sk":
        if czech is not None and rng.random() < TRANSLATED_SHARE:
            return _add(world, item, 6, czech + timedelta(days=rng.uniform(1, 20)), language, hi, rng).at
        return None
    if rng.random() < MANUAL_SHARE:
        provider = _provider(rng, item.kind, language)
        return _add(world, item, 2, start + timedelta(days=rng.uniform(1, 6)), language, hi, rng, provider,
                    _score(rng, item.kind, MANUAL_SCORE)).at
    return None


def _world() -> _World:
    """Everything the made-up Bazarr has done with the library's files, newest first (some of it may lie ahead of now).

    Bazarr came INSTALLED_DAYS before the library's newest file: what was there before got its subtitles in its first
    search, everything after as it came.
    """
    world = _World(items=_items())
    if not world.items:
        return world
    newest = max(item.arrived for item in world.items)
    installed = newest.replace(hour=19, minute=40, second=0, microsecond=0) - timedelta(days=INSTALLED_DAYS)
    for item in world.items:
        start = item.arrived
        if start < installed:
            start = installed + timedelta(minutes=demo_world.random_for(f"bazarr-start:{item.kind}:{item.number}").uniform(*FIRST_SEARCH))
        czech = None
        for language, hi in item.profile:
            found = _language(world, item, language, hi, start, czech)
            czech = found if language == "cs" else czech
    world.events.sort(key=lambda e: (e.at, e.item.kind, e.item.number, e.language, e.action), reverse=True)
    return world


# ---------------------------------------------------------------------------
# The answers
# ---------------------------------------------------------------------------

def _slice(records: list[dict[str, Any]], params: httpx.QueryParams) -> dict[str, Any]:
    """A page of a paged list (`start`, `length`; none or -1 = the rest) with `total`, the count of all records."""
    try:
        start, length = max(0, int(params.get("start") or 0)), int(params.get("length") or -1)
    except ValueError:
        start, length = 0, -1
    return {"data": records[start:start + length] if length > 0 else records[start:], "total": len(records)}


def _language_of(code: str, hi: bool) -> dict[str, Any]:
    name, code3 = LANGUAGES[code]
    return {"name": name, "code2": code, "code3": code3, "forced": False, "hi": hi}


def _ago(delta: timedelta) -> str:
    """How long ago, as Bazarr writes it: "33 seconds ago", "a minute ago", "3 days ago"."""
    seconds = max(0, int(delta.total_seconds()))
    for unit, size in (("year", 31_536_000), ("month", 2_592_000), ("day", 86_400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}s ago" if count > 1 else f"{'an' if unit == 'hour' else 'a'} {unit} ago"
    return f"{seconds} seconds ago"


def _file(item: _Item, language: str, hi: bool) -> str:
    """Where the subtitles lie: beside the file, with the language in the name."""
    suffix = f".{language}{'.hi' if hi else ''}.srt"
    if item.kind == "movie":
        return f"/movies/{item.title} ({item.year})/{item.title} ({item.year}){suffix}"
    season, episode, _title = item.episode
    return f"/tv/{item.title}/Season {season}/{item.title} - S{season:02d}E{episode:02d}{suffix}"


def _titles(item: _Item) -> dict[str, Any]:
    """What a record of the item says about it: the series and the episode with Sonarr's numbers, or the movie with Radarr's."""
    if item.kind == "movie":
        return {"title": item.title, "radarrId": item.number}
    season, episode, title = item.episode
    return {"seriesTitle": item.title, "episode_number": f"{season}x{episode}", "episodeTitle": title,
            "sonarrSeriesId": item.series, "sonarrEpisodeId": item.number}


def _record(event: _Event, moment: datetime) -> dict[str, Any]:
    item = event.item
    score = f"{event.score}%" if event.score is not None else None
    # What matched: only for subtitles from a provider (an own file has a score, but nothing was compared).
    matches = MATCHES[item.kind] if event.score is not None and event.action != 4 else ()
    matched = round(len(matches) * (event.score or 0) / 100)
    language = LANGUAGES[event.language][0] + (" HI" if event.hi else "")
    offset = f"{int(event.at.timestamp()) % 397 / 100:.2f}"
    return {**_titles(item), "action": event.action, "timestamp": _ago(moment - event.at), "monitored": True,
            "description": DESCRIPTIONS[event.action].format(language=language, provider=event.provider, score=score, offset=offset),
            "language": _language_of(event.language, event.hi), "tags": [], "score": score, "subs_id": event.subs_id,
            "provider": event.provider, "subtitles_path": _file(item, event.language, event.hi),
            "upgradable": event.action == 1 and (event.score or 100) < UPGRADE_BELOW[item.kind] and moment - event.at < timedelta(days=7),
            "parsed_timestamp": event.at.astimezone(ZONE).strftime(TEXT_FORMAT), "blacklisted": False,
            "matches": list(matches[:matched]), "dont_matches": list(matches[matched:]),
            **({"path": None} if item.kind == "movie" else {})}


def _history(kind: str) -> Callable[[httpx.QueryParams, datetime], dict[str, Any]]:
    def answer(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
        events = [e for e in _world().events if e.item.kind == kind and e.at <= moment]
        return _slice([_record(e, moment) for e in events], params)
    return answer


def _missing(world: _World, item: _Item, moment: datetime) -> list[dict[str, Any]]:
    """The languages of the item's profile it has no subtitles in at `moment`."""
    return [_language_of(code, hi) for code, hi in item.profile
            if (at := world.found.get((item.kind, item.number, code, hi))) is None or at > moment]


def _wanted_items(world: _World, kind: str, moment: datetime) -> list[tuple[_Item, list[dict[str, Any]]]]:
    """Items with a file that lack a language, from the highest number (as Bazarr lists them)."""
    found = [(item, _missing(world, item, moment)) for item in world.items if item.kind == kind and item.has_file]
    return sorted(((item, missing) for item, missing in found if missing), key=lambda pair: pair[0].number, reverse=True)


def _wanted(kind: str) -> Callable[[httpx.QueryParams, datetime], dict[str, Any]]:
    def answer(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
        records = []
        for item, missing in _wanted_items(_world(), kind, moment):
            season, episode, _title = item.episode
            release = item.title.replace(" ", ".") + (f".S{season:02d}E{episode:02d}" if kind == "episode" else f".{item.year}")
            records.append({**_titles(item), "missing_subtitles": missing, "sceneName": f"{release}.1080p.WEB-DL.H.264",
                            "tags": [], **({"seriesType": "standard"} if kind == "episode" else {})})
        return _slice(records, params)
    return answer


def _badges(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    """The counts in Bazarr's header: episodes and movies without subtitles, throttled providers, warnings."""
    world = _world()
    return {"episodes": len(_wanted_items(world, "episode", moment)), "movies": len(_wanted_items(world, "movie", moment)),
            "providers": 1, "status": len(HEALTH), "sonarr_signalr": "LIVE", "radarr_signalr": "LIVE", "announcements": 0}


def _retry(moment: datetime) -> str:
    """When Bazarr tries the throttled provider again - at the end of its day -, as it writes it: "in 5 hours"."""
    midnight = datetime.combine(moment.astimezone(ZONE).date() + timedelta(days=1), datetime.min.time(), ZONE)
    seconds = int((midnight.astimezone(UTC) - moment.astimezone(UTC)).total_seconds())
    if seconds >= 7200:
        return f"in {seconds // 3600} hours"
    if seconds >= 3600:
        return "in an hour"
    return f"in {seconds // 60} minutes" if seconds >= 120 else "in a minute"


def _providers(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    return {"data": [{"name": name, "status": THROTTLE_REASON if name == THROTTLED else "Good",
                      "retry": _retry(moment) if name == THROTTLED else "-"} for name in sorted(PROVIDERS)]}


def _health(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    return {"data": HEALTH}


def _status(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    started = moment.replace(minute=0, second=0, microsecond=0) - timedelta(days=6, hours=5)
    return {"data": {"bazarr_version": VERSION, "package_version": "", "sonarr_version": SONARR_VERSION,
                     "radarr_version": RADARR_VERSION, "operating_system": "Linux-6.12.48-x86_64-with-glibc2.41",
                     "python_version": "3.13.5", "database_engine": "Sqlite 3.46.1", "database_migration": "unknown",
                     "bazarr_directory": "/app/bazarr/bin", "bazarr_config_directory": "/config",
                     "start_time": started.timestamp(), "timezone": ZONE_NAME, "cpu_cores": 4}}


# API path (after /api/) -> its answer to a GET.
ANSWERS: dict[str, Callable[[httpx.QueryParams, datetime], Any]] = {
    "system/status": _status, "badges": _badges, "providers": _providers, "system/health": _health,
    "episodes/history": _history("episode"), "movies/history": _history("movie"),
    "episodes/wanted": _wanted("episode"), "movies/wanted": _wanted("movie"),
}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Bazarr's answer to one request (httpx.MockTransport)."""
    path = request.url.path.split(API, 1)[-1].strip("/").lower()
    moment = demo_world.now()
    if request.method != "GET":
        return httpx.Response(204) if path in ANSWERS or path in WRITES else httpx.Response(404)
    answer = ANSWERS.get(path)
    if answer is None:
        return httpx.Response(404)
    return httpx.Response(200, json=answer(request.url.params, moment))
