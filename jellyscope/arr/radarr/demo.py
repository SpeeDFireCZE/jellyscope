"""The demo's made-up Radarr: the demo's movies (demo_world.movies) as Radarr v6's API answers them.

The demo (JELLYSCOPE_DEMO) shows Radarr through its real code: the seed connects it at http://radarr.demo and runs
its real sync and counts (demo_apps.seed_apps), the page asks for the live state and the card "Stahuje se" for the
queue. `handler` answers them inside the process (httpx.MockTransport through demo_switch) in the shape of a real
Radarr (tests/radarr_responses/, tests/_apps.py) - every path the package's client asks:

* `system/status` - the app's name and version (the connection test, the sync's check);
* `history` (in pages, oldest first) and `history/since` - grabs, imports, upgrades and two failed downloads.
  Every movie of the library was imported before Jellyfin took it in (its `added_at`), grabbed a while before that,
  in the quality its file has (its height and size). The movies of the last weeks Radarr knew before they came out:
  it grabbed them after their digital release (demo_world.movie_release), the older ones mostly right after they
  were added to it (the back catalog). Upgrades and failures happen over the last year, counted back from the hour
  (the same answer within an hour);
* `movie` (also `?tmdbId=`) - the library's movies with their TMDB ids, by which the sync pairs them with the library
  (a second copy of a title in the library was put there by hand: Radarr has the title once; the archive's movies are
  unmonitored without a file), and the movies coming out (demo_world.coming_movies), which the library does not have;
* `queue` - four downloads whose progress goes round with the clock (demo_world.cycle_share), one of them stuck
  with a warning; `command` (POST) is accepted and changes nothing;
* `calendar` - the movies with a release (in cinemas, digital, physical) in the range; `wanted/missing` and
  `wanted/cutoff` - what is out and not downloaded, what is below the cutoff;
* `health` - an update is available; while the indexer that drops out now and then (demo_world.FLAKY_INDEXER)
  is out - demo_world's schedule, the one Prowlarr leaves it out by - also the warning naming it.

Everything else is 404, the posters (`mediacover/...`) too: the demo has no images from the apps and the client
takes 404 as "no poster", without an error. The numbers come from the catalog and a random source of its own per
movie (demo_world.random_for): the same library gives the same history. The moment is `demo_world.now()`.

Registered by demo_apps.load_fakes through the registry's `App.demo`, in the demo mode only - the door of the
package does not import this module.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import demo_world
from ...demo_world import span, weighted
from .client import NAME

API = "/api/v3/"
VERSION = "6.4.4.10685"
UPDATE = "6.4.5.10712"  # the version its health says is available
GB = 1024 ** 3
# (Radarr's id, name, source, resolution, modifier, the release's label, a movie's size in GB from-to, the custom
# format score), from the worst to the best of the profile.
QUALITIES = (
    (2, "DVD", "dvd", 480, "none", "DVDRip.x264", (1.2, 4.4), 0),
    (4, "HDTV-720p", "tv", 720, "none", "720p.HDTV.x264", (2.0, 4.5), 0),
    (5, "WEBDL-720p", "webdl", 720, "none", "720p.WEB-DL.DD5.1.H.264", (2.5, 5.0), 0),
    (3, "WEBDL-1080p", "webdl", 1080, "none", "1080p.WEB-DL.DDP5.1.H.264", (4.0, 9.0), 10),
    (7, "Bluray-1080p", "bluray", 1080, "none", "1080p.BluRay.DTS.x264", (8.0, 16.0), 25),
    (18, "WEBDL-2160p", "webdl", 2160, "none", "2160p.WEB-DL.DDP5.1.HDR.H.265", (14.0, 30.0), 40),
    (31, "Remux-2160p", "bluray", 2160, "remux", "2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1", (45.0, 70.0), 60),
)
BEST = len(QUALITIES) - 1
CUTOFF = 3            # WEBDL-1080p: a movie below it is in wanted/cutoff
BLURAY = 4            # Bluray-1080p: not before the physical release
UHD = 5               # WEBDL-2160p
GROUPS = ("CZDAB", "NOCNI", "KVALITA", "HRNEK", "SUMAVA", "VLTAVA")
LANGUAGES = [{"id": 1, "name": "English"}]
# How a grab started (data.releaseSource), with weights: a movie Radarr waited for, one of the back catalog (searched
# for when it was added), an upgrade.
WAITED_SOURCES = (("Rss", 66), ("Search", 14), ("UserInvokedSearch", 8), ("InteractiveSearch", 8), ("ReleasePush", 4))
BACK_CATALOG_SOURCES = (("Search", 55), ("UserInvokedSearch", 20), ("InteractiveSearch", 25))
UPGRADE_SOURCES = (("Rss", 70), ("Search", 20), ("InteractiveSearch", 10))
# (from, to minutes, weight): from the release to the grab of an older movie Radarr waited for, from the grab to the
# import.
RELEASE_TO_GRAB = ((10, 60, 20), (60, 360, 25), (360, 1440, 25), (1440, 10080, 20), (10080, 40000, 10))
DOWNLOAD = ((4, 25, 45), (25, 120, 40), (120, 600, 15))
# An older movie that came out at most this long before its grab was waited for (it was in Radarr before it came out).
WAITED_WINDOW = timedelta(days=120)
UPGRADE_SHARE = 0.35                                       # how many movies got a better version later
# The version a movie had before its upgrade, by the quality its file has now (one of them, by chance).
WORSE = {1: (0,), 2: (0, 1), 3: (2,), 4: (3, 3, 2), 5: (3, 4), 6: (4, 5)}
UPGRADE_AGE = ((0.2, 7, 15), (7, 30, 20), (30, 330, 65))   # (from, to days ago, weight)
FAILURE_AGE = ((1.5, 5), (40, 200))                        # days ago of the two failed downloads
FAILURE_MESSAGE = "Aborted, cannot be completed"
RELEASE_AGE = 26                             # minutes from a release's publishing to its grab
# Record ids: a movie's number * 10 + the record's place (a first grab, its import, an upgrade's grab, the old file's
# deletion, its import, a failed grab, its failure) - the same record keeps its id at every answer.
SLOTS = {"first": (1, 2), "upgrade": (3, 5), "failed": (6, 7)}
DELETION_SLOT = 4
CLIENT_TYPES = {"usenet": "SABnzbd", "torrent": "QBittorrent"}       # data.downloadClient: the client's implementation
PROTOCOL_NUMBERS = {"usenet": "1", "torrent": "2"}
# Radarr's id of the first movie coming out: the library's movies have their catalog numbers, these come after them.
COMING_IDS = 101
# The queue: (key, which movie, quality, the indexer's place in demo_world.INDEXERS, the cycle in minutes; 0 = it does
# not move). A movie out already that the library does not have, a Blu-ray of the newest movie below the cutoff, a 4K
# version of the newest movie of the last weeks, and a Blu-ray of the next one below the cutoff whose import is blocked.
QUEUE = (("out", "coming", CUTOFF, 0, 45), ("better", "cutoff", BLURAY, 1, 25), ("uhd", "new", UHD, 2, 35),
         ("stuck", "cutoff", BLURAY, 3, 0))
STUCK_MESSAGES = [{"title": "One or more movies expected in this release were not imported or missing", "messages": []},
                  {"title": "", "messages": ["No audio tracks detected"]}]


@dataclass
class _Movie:
    """A movie the made-up Radarr knows: its releases, when it came to Radarr, its file."""

    number: int                                # Radarr's id: the catalog's number, COMING_IDS on for those coming out
    title: str
    year: int
    tmdb_id: str
    imdb_id: str
    cinemas: datetime
    digital: datetime
    physical: datetime
    added: datetime                            # when it came to Radarr
    entry: dict[str, Any] | None = None        # the catalog's entry; None = the library does not have it
    file: tuple[int, int, str, int] | None = None    # (quality, size, group, file id); None = no file
    monitored: bool = True


@dataclass
class _Download:
    """One release the made-up Radarr grabbed: where from, and when it was imported (or failed)."""

    kind: str                                  # "first", "upgrade" or "failed" (SLOTS)
    movie: _Movie
    quality: int                               # the place in QUALITIES
    grabbed: datetime
    done: datetime                             # the import, or the failure
    size: int
    indexer: str
    protocol: str
    download_id: str
    source: str
    group: str
    replaced: tuple[int, int, str, int] | None = None   # the upgraded file (see _Movie.file)


@dataclass
class _World:
    """The made-up Radarr at one moment: its movies and what it downloaded."""

    movies: list[_Movie] = field(default_factory=list)
    downloads: list[_Download] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The world: what the made-up Radarr downloaded
# ---------------------------------------------------------------------------

def _midnight(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _library_quality(rng: Any, entry: dict[str, Any]) -> int:
    """The quality of the library's file, by its height and size."""
    height, size = entry["height"], entry["size_bytes"] / GB
    if height >= 2000:
        return BEST if size >= 40 else UHD
    if height >= 1000:
        return BLURAY if size > 9 else CUTOFF
    return rng.choice((1, 2)) if height >= 700 else 0


def _download(rng: Any, kind: str, movie: _Movie, quality: int, grabbed: datetime, done: datetime, source: str,
              size: int = 0, indexer: str = "") -> _Download:
    """A release with its indexer (by weight, the flaky one less often), client, size and the client's download id."""
    if not indexer:
        indexer = weighted(rng, ((name, 20 if name == demo_world.FLAKY_INDEXER else 30)
                                 for name, _protocol in demo_world.INDEXERS if name != demo_world.DISABLED_INDEXER))
    protocol = dict(demo_world.INDEXERS)[indexer]
    low, high = QUALITIES[quality][6]
    download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
    return _Download(kind, movie, quality, grabbed, done, size or int(rng.uniform(low, high) * GB), indexer, protocol,
                     download_id, source, rng.choice(GROUPS))


def _first(world: _World, entry: dict[str, Any]) -> _Download:
    """The first download of a movie of the library: imported before Jellyfin took it in, in its file's quality.

    A movie of the last weeks, or an older one that came out shortly before its grab: Radarr knew it before it came
    out and grabbed it after. The rest is the back catalog, added to Radarr right before its grab.
    """
    rng = demo_world.random_for(f"radarr:{entry['number']}")
    released = demo_world.movie_release(entry)
    quality = _library_quality(rng, entry)
    if released:      # Jellyfin took it in a minute or two after the import
        done = entry["added_at"] - timedelta(minutes=rng.uniform(1, 3))
        minutes = min(span(rng, DOWNLOAD), (done - released).total_seconds() / 120)
    else:             # Jellyfin's scan took it in up to a few hours later
        done, minutes = entry["added_at"] - timedelta(minutes=rng.uniform(2, 240)), span(rng, DOWNLOAD)
    grabbed = done - timedelta(minutes=minutes)
    digital = _midnight(datetime(entry["year"] or grabbed.year - 3, 1, 1, tzinfo=UTC) + timedelta(days=rng.uniform(20, 340)))
    if released is None and digital >= grabbed - WAITED_WINDOW:      # it came out shortly before: Radarr waited for it
        released = _midnight(grabbed - timedelta(minutes=span(rng, RELEASE_TO_GRAB)))
    digital = released or digital
    cinemas = _midnight(digital - timedelta(days=rng.uniform(30, 90)))
    physical = _midnight(digital + timedelta(days=rng.uniform(20, 60)))
    added = (digital - timedelta(days=rng.uniform(5, 60), hours=rng.uniform(0, 20)) if released
             else grabbed - timedelta(hours=rng.uniform(1, 3)))
    movie = _Movie(entry["number"], entry["title"], entry["year"] or digital.year, entry["tmdb_id"],
                   demo_world.imdb_id(f"movie:{entry['jellyfin_id']}"), cinemas, digital, physical, added, entry=entry,
                   monitored=not entry["missing"])
    download = _download(rng, "first", movie, quality, grabbed, done,
                         weighted(rng, WAITED_SOURCES if released else BACK_CATALOG_SOURCES), size=entry["size_bytes"])
    if not entry["missing"]:      # the archive's movies: deleted from the disk and unmonitored
        movie.file = (quality, download.size, download.group, movie.number * 10 + 1)
    world.movies.append(movie)
    return download


def _upgrade(first: _Download, anchor: datetime, recent: bool = False) -> _Download | None:
    """A better version later on - or None (most movies keep their first file, and none is older than its file).

    The library's file is the better one: the first download was worse (WORSE; no Blu-ray before the physical
    release). `recent`: the newest movie gets one half-way between its import and now, so the last days have one too.
    """
    movie = first.movie
    rng = demo_world.random_for(f"radarr-upgrade:{movie.number}")
    if first.quality == 0 or (rng.random() >= UPGRADE_SHARE and not recent):
        return None
    imported = anchor - timedelta(days=span(rng, UPGRADE_AGE))
    if recent:
        imported = first.done + (anchor - first.done) * 0.5
    elif imported <= first.done + timedelta(hours=12):
        return None
    grabbed = imported - timedelta(minutes=min(span(rng, DOWNLOAD), (imported - first.done).total_seconds() / 120))
    better = _download(rng, "upgrade", movie, first.quality, grabbed, imported, weighted(rng, UPGRADE_SOURCES), size=first.size)
    first.quality = rng.choice(WORSE[first.quality])
    if first.quality == BLURAY and first.done < movie.physical:
        first.quality = CUTOFF
    first.size = int(rng.uniform(*QUALITIES[first.quality][6]) * GB)
    better.replaced = (first.quality, first.size, first.group, movie.number * 10 + 1)
    if movie.file:
        movie.file = (better.quality, better.size, better.group, movie.number * 10 + 2)
    return better


def _failures(firsts: list[_Download], anchor: datetime) -> list[_Download]:
    """Two upgrades that failed in the client, both from the flaky indexer: one this week, one months ago."""
    candidates = [d for d in firsts if d.movie.file and d.movie.file[0] < BEST and d.done < anchor - timedelta(days=30)]
    result = []
    for place, (low, high) in zip(sorted({1, len(candidates) // 2}), FAILURE_AGE):
        if place >= len(candidates):
            continue
        first = candidates[place]
        rng = demo_world.random_for(f"radarr-failure:{first.movie.number}")
        grabbed = anchor - timedelta(days=rng.uniform(low, high))
        if grabbed <= first.done + timedelta(days=1):
            grabbed = first.done + timedelta(days=rng.uniform(1, 5))
        failed = grabbed + timedelta(minutes=rng.uniform(10, 45))
        result.append(_download(rng, "failed", first.movie, first.movie.file[0] + 1, grabbed, failed, "Rss",
                                indexer=demo_world.FLAKY_INDEXER))
    return result


def _coming(world: _World, moment: datetime) -> None:
    """The movies coming out (demo_world.coming_movies): in cinemas now, added to Radarr before, without a file."""
    for place, coming in enumerate(demo_world.coming_movies(moment)):
        rng = demo_world.random_for(f"radarr-coming:{coming['key']}")
        digital = coming["released"]
        cinemas = _midnight(digital - timedelta(days=rng.uniform(30, 50)))
        world.movies.append(_Movie(COMING_IDS + place, coming["title"], coming["year"], coming["tmdb_id"], coming["imdb_id"],
                                   cinemas, digital, _midnight(digital + timedelta(days=rng.uniform(25, 60))),
                                   cinemas - timedelta(days=rng.uniform(10, 40), hours=rng.uniform(0, 20))))


def _world(moment: datetime) -> _World:
    """What the made-up Radarr has downloaded by `moment`, from the catalog of the demo's movies."""
    anchor = moment.replace(minute=0, second=0, microsecond=0)
    world, firsts = _World(), []
    for entry in demo_world.movies():
        # A second copy of a title (the demo's duplicates) was put into the library by hand: Radarr has the title once.
        if entry["added_at"] and entry["title"] not in {m.title for m in world.movies}:
            firsts.append(_first(world, entry))
    newest = max((d for d in firsts if d.quality > 0 and d.done <= anchor - timedelta(hours=12)), key=lambda d: d.done, default=None)
    world.downloads += firsts
    world.downloads += [d for d in (_upgrade(first, anchor, first is newest) for first in firsts) if d is not None]
    world.downloads += _failures(firsts, anchor)
    _coming(world, moment)
    return world


# ---------------------------------------------------------------------------
# Resources: a movie, a quality as Radarr writes them
# ---------------------------------------------------------------------------

def _quality(quality: int) -> dict[str, Any]:
    number, name, source, resolution, modifier = QUALITIES[quality][:5]
    return {"quality": {"id": number, "name": name, "source": source, "resolution": resolution, "modifier": modifier},
            "revision": {"version": 1, "real": 0, "isRepack": False}}


def _release(movie: _Movie, quality: int, group: str) -> str:
    return f"{'.'.join(re.findall(r'[A-Za-z0-9]+', movie.title))}.{movie.year}.{QUALITIES[quality][5]}-{group}"


def _folder(movie: _Movie) -> str:
    return f"/movies/{movie.title} ({movie.year})"


def _resource(movie: _Movie, moment: datetime) -> dict[str, Any]:
    """The movie as Radarr's API sends it (`movie`, embedded in the history and the queue, the calendar, wanted)."""
    number, title = movie.number, movie.title
    quality, size, group, file_id = movie.file or (0, 0, "", 0)
    status = "released" if movie.digital <= moment else "inCinemas" if movie.cinemas <= moment else "announced"
    files = {"movieFileCount": 1, "sizeOnDisk": size, "releaseGroups": [group],
             "movieFileQualities": [_quality(quality)["quality"]]} if movie.file else {
             "movieFileCount": 0, "sizeOnDisk": 0, "releaseGroups": [], "movieFileQualities": []}
    return {"title": title, "originalTitle": title, "originalLanguage": LANGUAGES[0], "alternateTitles": [],
            "secondaryYearSourceId": 0, "sortTitle": title.lower(), "sizeOnDisk": size, "status": status,
            "inCinemas": demo_world.iso(movie.cinemas), "physicalRelease": demo_world.iso(movie.physical),
            "digitalRelease": demo_world.iso(movie.digital), "releaseDate": demo_world.iso(min(movie.digital, movie.physical)),
            "images": [{"coverType": "poster", "url": f"/MediaCover/{number}/poster.jpg"}], "website": "", "year": movie.year,
            "youTubeTrailerId": "", "studio": "", "path": _folder(movie), "qualityProfileId": 1, "hasFile": bool(movie.file),
            "movieFileId": file_id, "monitored": movie.monitored, "minimumAvailability": "released",
            "isAvailable": movie.digital <= moment, "folderName": _folder(movie),
            "runtime": demo_world.random_for(f"radarr-runtime:{number}").randint(88, 165),
            "cleanTitle": "".join(c for c in title.lower() if c.isalnum()), "imdbId": movie.imdb_id,
            "tmdbId": int(movie.tmdb_id or 0), "titleSlug": str(movie.tmdb_id), "rootFolderPath": "/movies", "genres": [],
            "tags": [], "added": demo_world.iso(movie.added), "statistics": files, "id": number}


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------

def _grab(download: _Download) -> dict[str, Any]:
    """The data of a grab: where it came from, how, how big."""
    published = download.grabbed - timedelta(minutes=RELEASE_AGE)
    indexer_id = [name for name, _protocol in demo_world.INDEXERS].index(download.indexer) + 1
    return {"indexer": download.indexer, "releaseGroup": download.group, "age": "0", "ageHours": f"{RELEASE_AGE / 60:.4f}",
            "ageMinutes": str(RELEASE_AGE), "publishedDate": demo_world.iso(published), "downloadClient": CLIENT_TYPES[download.protocol],
            "downloadClientName": demo_world.DOWNLOAD_CLIENTS[download.protocol], "size": str(download.size),
            "tmdbId": str(download.movie.tmdb_id), "imdbId": download.movie.imdb_id, "protocol": PROTOCOL_NUMBERS[download.protocol],
            "customFormatScore": str(QUALITIES[download.quality][7]), "movieMatchType": "Id", "releaseSource": download.source,
            "indexerFlags": "0", "indexerId": str(indexer_id)}


def _records(world: _World, moment: datetime) -> list[dict[str, Any]]:
    """Every record of the history up to `moment`, oldest first - without the movie (see _embedded)."""
    records = []
    for d in world.downloads:
        grab_slot, done_slot = SLOTS[d.kind]
        number, release = d.movie.number, _release(d.movie, d.quality, d.group)
        base = {"movieId": number, "sourceTitle": release, "languages": LANGUAGES, "quality": _quality(d.quality),
                "customFormats": [], "customFormatScore": QUALITIES[d.quality][7], "qualityCutoffNotMet": d.quality < CUTOFF,
                "downloadId": d.download_id}
        client = {"downloadClient": CLIENT_TYPES[d.protocol], "downloadClientName": demo_world.DOWNLOAD_CLIENTS[d.protocol]}
        records.append({**base, "date": demo_world.iso(d.grabbed), "eventType": "grabbed", "data": _grab(d), "id": number * 10 + grab_slot})
        if d.kind == "failed":
            records.append({**base, "date": demo_world.iso(d.done), "eventType": "downloadFailed", "id": number * 10 + done_slot,
                            "data": {**client, "message": FAILURE_MESSAGE, "releaseGroup": d.group, "size": str(d.size),
                                     "indexer": d.indexer}})
            continue
        if d.replaced:
            old_quality, old_size, old_group, _file = d.replaced
            old = _release(d.movie, old_quality, old_group)
            records.append({**{k: v for k, v in base.items() if k != "downloadId"}, "quality": _quality(old_quality),
                            "customFormatScore": QUALITIES[old_quality][7], "qualityCutoffNotMet": old_quality < CUTOFF,
                            "sourceTitle": f"{_folder(d.movie)}/{old}.mkv", "date": demo_world.iso(d.done),
                            "eventType": "movieFileDeleted", "id": number * 10 + DELETION_SLOT,
                            "data": {"reason": "Upgrade", "releaseGroup": old_group, "size": str(old_size), "indexerFlags": "0"}})
        records.append({**base, "date": demo_world.iso(d.done), "eventType": "downloadFolderImported", "id": number * 10 + done_slot,
                        "data": {"fileId": str(number * 10 + (2 if d.kind == "upgrade" else 1)),
                                 "droppedPath": f"/downloads/complete/{release}/{release}.mkv",
                                 "importedPath": f"{_folder(d.movie)}/{release}.mkv", **client, "releaseGroup": d.group,
                                 "customFormatScore": str(QUALITIES[d.quality][7]), "size": str(d.size), "indexerFlags": "0"}})
    records = [r for r in records if r["date"] <= demo_world.iso(moment)]
    return sorted(records, key=lambda r: (r["date"], r["id"]))


def _embedded(world: _World, records: list[dict[str, Any]], params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    """The records with their movie, when the request asks for it (includeMovie)."""
    if params.get("includeMovie") != "true":
        return records
    shown = {movie.number: _resource(movie, moment) for movie in world.movies}
    return [{**r, "movie": shown[r["movieId"]]} for r in records]


def _history(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    page = demo_world.page(_records(world, moment), params, "date")
    return {**page, "records": _embedded(world, page["records"], params, moment)}


def _time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _history_since(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    since = _time(params.get("date"))
    world = _world(moment)
    records = [r for r in _records(world, moment) if since is None or r["date"] >= demo_world.iso(since)]
    return _embedded(world, records, params, moment)


# ---------------------------------------------------------------------------
# The live state: queue, calendar, missing, cutoff, health
# ---------------------------------------------------------------------------

def _missing(world: _World, moment: datetime) -> list[_Movie]:
    """What is out and has no file: the movies coming out that came out already (the archive is unmonitored)."""
    return [m for m in world.movies if m.monitored and not m.file and m.digital <= moment]


def _cutoff(world: _World, moment: datetime) -> list[_Movie]:
    """The library's movies whose file is below the cutoff (WEBDL-1080p)."""
    return [m for m in world.movies if m.monitored and m.file and m.file[0] < CUTOFF]


def _wanted(which: Callable[[_World, datetime], list[_Movie]]) -> Callable[[httpx.QueryParams, datetime], dict[str, Any]]:
    def answer(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
        found = sorted(which(_world(moment), moment), key=lambda m: m.title.lower(), reverse=True)
        return demo_world.page([_resource(m, moment) for m in found], params, "movieMetadata.sortTitle", "descending")
    return answer


def _queue_movie(world: _World, what: str, taken: set[int], moment: datetime) -> _Movie | None:
    """The movie of a download of the queue: one out already without a file, the newest below the cutoff, or the
    newest of the last weeks below 4K - each in the queue once."""
    library = sorted((m for m in world.movies if m.entry and m.file and m.number not in taken),
                     key=lambda m: m.entry["added_at"], reverse=True)
    if what == "coming":
        found = [m for m in _missing(world, moment) if m.entry is None]
    elif what == "cutoff":
        found = [m for m in library if m.file[0] < CUTOFF]
    else:
        found = [m for m in library if m.file[0] < UHD and demo_world.movie_release(m.entry)]
    return next((m for m in found if m.number not in taken), None)


def _queue(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    records: list[dict[str, Any]] = []
    taken: set[int] = set()
    for key, what, quality, indexer, minutes in QUEUE:
        movie = _queue_movie(world, what, taken, moment)
        if movie is None:
            continue
        taken.add(movie.number)
        rng = demo_world.random_for(f"radarr-queue:{key}:{movie.number}")
        name, protocol = demo_world.INDEXERS[indexer]
        size = int(rng.uniform(*QUALITIES[quality][6]) * GB)
        release = _release(movie, quality, rng.choice(GROUPS))
        if minutes:
            share = demo_world.cycle_share(moment, f"radarr:{key}:{movie.number}", minutes)
            left = timedelta(seconds=round((1 - share) * minutes * 60))
            state = {"status": "downloading", "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading",
                     "statusMessages": [], "sizeleft": int(size * (1 - share)),
                     "added": demo_world.iso(moment - timedelta(seconds=minutes * 60 * share))}
        else:
            left = timedelta(0)
            state = {"status": "completed", "trackedDownloadStatus": "warning", "trackedDownloadState": "importBlocked",
                     "statusMessages": [STUCK_MESSAGES[0], {**STUCK_MESSAGES[1], "title": release + ".mkv"}], "sizeleft": 0,
                     "added": demo_world.iso(moment.replace(minute=0, second=0, microsecond=0) - timedelta(hours=3))}
        download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
        records.append({"movieId": movie.number, "movie": _resource(movie, moment), "languages": LANGUAGES,
                        "quality": _quality(quality), "customFormats": [], "customFormatScore": QUALITIES[quality][7],
                        "size": size, "title": release, "estimatedCompletionTime": demo_world.iso(moment + left), **state,
                        "downloadId": download_id, "protocol": protocol, "downloadClient": demo_world.DOWNLOAD_CLIENTS[protocol],
                        "downloadClientHasPostImportCategory": False, "indexer": name, "timeleft": demo_world.clock(left),
                        "id": rng.getrandbits(31)})
    if params.get("includeMovie") != "true":
        for record in records:
            record.pop("movie")
    return demo_world.page(records, params, "timeleft")


def _calendar(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    """The monitored movies with a release (in cinemas, digital, physical) from `start` to `end`, the earliest first."""
    start, end = _time(params.get("start")) or moment, _time(params.get("end")) or moment + timedelta(days=7)
    found = []
    for movie in _world(moment).movies:
        dates = [at for at in (movie.cinemas, movie.digital, movie.physical) if start <= at <= end]
        if dates and movie.monitored:
            found.append((min(dates), movie.number, movie))
    return [_resource(movie, moment) for _at, _number, movie in sorted(found, key=lambda f: f[:2])]


def _health(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    found = [{"source": "UpdateCheck", "type": "warning", "message": f"New update is available: {UPDATE}",
              "wikiUrl": "https://wiki.servarr.com/radarr/system#new-update-is-available"}]
    if demo_world.flaky_down(moment):
        found.append({"source": "IndexerStatusCheck", "type": "warning",
                      "message": f"Indexers unavailable due to failures: {demo_world.FLAKY_INDEXER}",
                      "wikiUrl": "https://wiki.servarr.com/radarr/system#indexers-are-unavailable-due-to-failures"})
    return found


def _status(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    started = moment.replace(minute=0, second=0, microsecond=0) - timedelta(days=6, hours=5)
    return {"appName": NAME, "instanceName": NAME, "version": VERSION, "buildTime": "2026-09-14T15:54:37Z", "isDebug": False,
            "isProduction": True, "isAdmin": False, "isUserInteractive": False, "startupPath": "/app/radarr/bin", "appData": "/config",
            "osName": "debian", "osVersion": "13", "isNetCore": True, "isLinux": True, "isOsx": False, "isWindows": False,
            "isDocker": True, "isContainerized": True, "mode": "console", "branch": "master", "databaseType": "sqLite",
            "databaseVersion": "3.53.4", "authentication": "forms", "migrationVersion": 242, "urlBase": "", "runtimeVersion": "8.0.27",
            "runtimeName": "netcore", "startTime": demo_world.iso(started), "packageUpdateMechanism": "docker"}


def _movie_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    found = [_resource(movie, moment) for movie in sorted(_world(moment).movies, key=lambda m: m.number)]
    wanted = params.get("tmdbId")
    return [m for m in found if str(m["tmdbId"]) == wanted] if wanted else found


def _command(request: httpx.Request, moment: datetime) -> dict[str, Any]:
    """A command (RefreshMonitoredDownloads) taken into the queue of commands - the made-up Radarr does nothing with it."""
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        body = {}
    name = str(body.get("name") or "") if isinstance(body, dict) else ""
    return {"name": name, "body": {"sendUpdatesToClient": False}, "priority": "normal", "status": "queued",
            "queued": demo_world.iso(moment), "trigger": "manual", "id": int(moment.timestamp()) % 100_000}


# API path (after /api/v3/) -> its answer to a GET.
ANSWERS: dict[str, Callable[[httpx.QueryParams, datetime], Any]] = {
    "system/status": _status, "history": _history, "history/since": _history_since, "movie": _movie_list,
    "queue": _queue, "calendar": _calendar, "health": _health,
    "wanted/missing": _wanted(_missing), "wanted/cutoff": _wanted(_cutoff),
}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Radarr's answer to one request (httpx.MockTransport)."""
    path = request.url.path.split(API, 1)[-1].strip("/").lower()
    moment = demo_world.now()
    if request.method == "POST":
        return httpx.Response(201, json=_command(request, moment)) if path == "command" else httpx.Response(404)
    answer = ANSWERS.get(path) if request.method == "GET" else None
    if answer is None:
        return httpx.Response(404)
    return httpx.Response(200, json=answer(request.url.params, moment))
