"""The demo's made-up Sonarr: the demo's series (demo_world.series) as Sonarr v4's API answers them.

The demo (JELLYSCOPE_DEMO) shows Sonarr through its real code: the seed connects it at http://sonarr.demo and runs
its real sync and counts (demo_apps.seed_apps), the page asks for the live state and the card "Stahuje se" for the
queue. `handler` answers them inside the process (httpx.MockTransport through demo_switch) in the shape of a real
Sonarr (tests/sonarr_responses/, tests/_apps.py) - every path the package's client asks:

* `system/status` - the app's name and version (the connection test, the sync's check);
* `history` (in pages, oldest first) and `history/since` - grabs, imports, upgrades and two failed downloads.
  Every episode of the library was imported before Jellyfin took it in (its `added_at`), grabbed a while before
  that, after it aired; episodes that arrived together came as a season pack. Upgrades and failures happen over
  the last year, counted back from the hour (the same answer within an hour);
* `series` (also `?tvdbId=`) - the demo's series with their TVDB and TMDB ids, by which the sync pairs them with
  the library (through the made-up Jellyfin, demo_jellyfin);
* `queue` - four downloads whose progress goes round with the clock (demo_world.cycle_share), one of them stuck
  with a warning; `command` (POST) is accepted and changes nothing;
* `calendar` - the series airing now (the world's TV schedule, demo_world.airings) air an episode every week; their
  latest episodes are in the library, grabbed and imported after they aired; `wanted/missing` and `wanted/cutoff` -
  what aired and is not downloaded, what is below the cutoff;
* `health` - an update is available; while the indexer that drops out now and then (demo_world.FLAKY_INDEXER)
  is out - demo_world's schedule, the one Prowlarr leaves it out by - also the warning naming it.

Everything else is 404, the posters (`mediacover/...`) too: the demo has no images from the apps and the client
takes 404 as "no poster", without an error. The numbers come from the catalog and a random source of its own per
title (demo_world.random_for): the same library gives the same history. The moment is `demo_world.now()`.

Registered by demo_apps.load_fakes through the registry's `App.demo`, in the demo mode only - the door of the
package does not import this module.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ... import demo_world
from ...demo_world import span, weighted
from .client import NAME

API = "/api/v3/"
VERSION = "4.0.20.3014"
UPDATE = "4.0.21.3027"  # the version its health says is available
GB = 1024 ** 3
# (Sonarr's id, name, source, resolution, the release's label, an episode's size in GB from-to, the custom format score)
QUALITIES = (
    (4, "HDTV-720p", "television", 720, "720p.HDTV.x264", (0.5, 1.1), 0),
    (5, "WEBDL-720p", "web", 720, "720p.WEB-DL.DD5.1.H.264", (0.7, 1.4), 0),
    (9, "HDTV-1080p", "television", 1080, "1080p.HDTV.x264", (1.2, 2.4), 0),
    (3, "WEBDL-1080p", "web", 1080, "1080p.WEB-DL.DDP5.1.H.264", (1.5, 3.2), 10),
    (7, "Bluray-1080p", "bluray", 1080, "1080p.BluRay.x264", (3.0, 6.0), 25),
    (18, "WEBDL-2160p", "web", 2160, "2160p.WEB-DL.DDP5.1.HDR.H.265", (5.0, 11.0), 50),
)
BEST = len(QUALITIES) - 1
FIRST_QUALITIES = ((0, 8), (1, 20), (2, 10), (3, 37), (4, 15), (5, 10))     # (quality, weight) of a first download
CUTOFF = 3            # WEBDL-1080p: an episode below it is in wanted/cutoff
GROUPS = ("CZDAB", "NOCNI", "KVALITA", "HRNEK", "SUMAVA", "VLTAVA")
LANGUAGES = [{"id": 1, "name": "English"}]
# How a grab started (data.releaseSource), with weights: a first download, an upgrade.
FIRST_SOURCES = (("Rss", 62), ("Search", 18), ("UserInvokedSearch", 8), ("InteractiveSearch", 8), ("ReleasePush", 4))
UPGRADE_SOURCES = (("Rss", 70), ("Search", 20), ("InteractiveSearch", 10))
# (from, to minutes, weight): from airing to the grab, from the grab to the import.
AIRED_TO_GRAB = ((5, 40, 30), (40, 300, 30), (300, 1440, 20), (1440, 10080, 13), (10080, 40000, 7))
DOWNLOAD = ((3, 20, 50), (20, 90, 35), (90, 480, 15))
# Episodes Jellyfin took in within this time of each other came in one release (a season pack).
PACK_GAP = timedelta(minutes=30)
UPGRADE_SHARE = 0.35                                       # how many single episodes got a better version later
UPGRADE_AGE = ((0.2, 7, 15), (7, 30, 20), (30, 330, 65))   # (from, to days ago, weight)
FAILURE_AGE = ((1.5, 5), (40, 200))                        # days ago of the two failed downloads
FAILURE_MESSAGE = "Aborted, cannot be completed"
RELEASE_AGE = 17                             # minutes from a release's publishing to its grab
# Record ids: an episode's number * 10 + the record's place (a first grab, its import, an upgrade's grab, the old
# file's deletion, its import, a failed grab, its failure) - the same record keeps its id at every answer.
SLOTS = {"first": (1, 2), "upgrade": (3, 5), "failed": (6, 7)}
DELETION_SLOT = 4
CLIENT_TYPES = {"usenet": "SABnzbd", "torrent": "QBittorrent"}       # data.downloadClient: the client's implementation
PROTOCOL_NUMBERS = {"usenet": "1", "torrent": "2"}
# The series by their number in the catalog (demodata.SERIES from 1). Which air now and when is the world's TV schedule
# (demo_world.airings, aired): their library episodes were grabbed after they aired. One is an old series that came to
# Sonarr years after its episodes aired (4 Pratele - "how soon after release" does not count them), two lack episodes
# of their first season Sonarr knows ({number: how many}). An episode beyond the library has no title yet ("TBA").
LATE_SERIES = 4
MISSING_EPISODES = {3: 2, 4: 4}
WEEK = demo_world.WEEK
MISSING_WEEKS = 3                            # how far back an aired, not downloaded episode is missing
# The queue: (key, series number, what, quality, the indexer's place in demo_world.INDEXERS, the cycle in minutes;
# 0 = it does not move). A season pack better than the library's files, this week's episodes of two airing series
# (still downloading, or a better version of the one the library has) and a missing episode whose import is blocked.
QUEUE = (("pack", 2, "pack", 4, 0, 55), ("aired", 3, "aired", 3, 1, 25), ("aired", 5, "aired", 1, 2, 40),
         ("stuck", 4, "missing", 1, 3, 0))
STUCK_MESSAGES = [{"title": "One or more episodes expected in this release were not imported or missing from the release",
                   "messages": []}, {"title": "", "messages": ["No audio tracks detected"]}]


@dataclass
class _Download:
    """One release the made-up Sonarr grabbed: its episodes, where from, and when each was imported (or failed)."""

    kind: str                                  # "first", "upgrade" or "failed" (SLOTS)
    series: dict[str, Any]
    episodes: list[dict[str, Any]]
    quality: int                               # the place in QUALITIES
    grabbed: datetime
    done: list[datetime]                       # every episode's import, or the failure
    sizes: list[int]
    indexer: str
    protocol: str
    download_id: str
    source: str
    group: str
    replaced: list[tuple[int, int, str, int]] = field(default_factory=list)   # the upgraded files (see _World.files)


@dataclass
class _World:
    """The made-up Sonarr at one moment: the catalog, what it downloaded and what it knows of every episode."""

    series: list[dict[str, Any]]
    added: dict[int, datetime] = field(default_factory=dict)       # series number -> when it came to Sonarr
    aired: dict[int, datetime] = field(default_factory=dict)       # episode number -> when it aired
    files: dict[int, tuple[int, int, str, int]] = field(default_factory=dict)  # episode -> (quality, size, group, file id)
    downloads: list[_Download] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The world: what the made-up Sonarr downloaded
# ---------------------------------------------------------------------------

def _download(rng: Any, kind: str, series: dict[str, Any], episodes: list[dict[str, Any]], quality: int,
              grabbed: datetime, done: list[datetime], source: str, indexer: str = "") -> _Download:
    """A release with its indexer (by weight, the flaky one less often), client, sizes and the client's download id."""
    if not indexer:
        indexer = weighted(rng, ((name, 20 if name == demo_world.FLAKY_INDEXER else 30)
                                 for name, _protocol in demo_world.INDEXERS if name != demo_world.DISABLED_INDEXER))
    protocol = dict(demo_world.INDEXERS)[indexer]
    low, high = QUALITIES[quality][5]
    download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
    return _Download(kind, series, episodes, quality, grabbed, done, [int(rng.uniform(low, high) * GB) for _ in episodes],
                     indexer, protocol, download_id, source, rng.choice(GROUPS))


def _batches(episodes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The library's episodes as Sonarr downloaded them: those that arrived together came as one release."""
    batches: list[list[dict[str, Any]]] = []
    for episode in sorted(episodes, key=lambda e: (e["added_at"], e["number"])):
        last = batches[-1][-1] if batches else None
        if last and episode["season"] == last["season"] and episode["added_at"] - last["added_at"] <= PACK_GAP:
            batches[-1].append(episode)
        else:
            batches.append([episode])
    return batches


def _first(world: _World, series: dict[str, Any], batch: list[dict[str, Any]]) -> _Download:
    """The first download of episodes of the library: imported before Jellyfin took them in, grabbed after they aired."""
    rng = demo_world.random_for(f"sonarr:{batch[0]['number']}")
    pack = len(batch) > 1
    scheduled = None if pack else demo_world.aired(series["number"], batch[0]["season"], batch[0]["episode"])
    if pack:     # a season pack: imported in a row, Jellyfin a minute or two after each; the season came out at once
        done = [e["added_at"] - timedelta(minutes=rng.uniform(1, 3)) for e in batch]
        quality, minutes, delay, source = rng.choice((3, 4)), rng.uniform(25, 70), timedelta(hours=rng.uniform(6, 72)), "Search"
    elif scheduled:   # a series airing now: grabbed after it aired, Jellyfin took it in right after the import
        done = [batch[0]["added_at"] - timedelta(minutes=rng.uniform(1, 3))]
        quality, source = weighted(rng, FIRST_QUALITIES), weighted(rng, FIRST_SOURCES)
        minutes, delay = min(span(rng, DOWNLOAD), (done[0] - scheduled).total_seconds() / 120), None
    else:        # Jellyfin's scheduled scan took it in up to a day later
        done = [batch[0]["added_at"] - timedelta(minutes=rng.uniform(5, 1200))]
        quality, minutes = weighted(rng, FIRST_QUALITIES), span(rng, DOWNLOAD)
        delay, source = timedelta(minutes=span(rng, AIRED_TO_GRAB)), weighted(rng, FIRST_SOURCES)
    download = _download(rng, "first", series, batch, quality, min(done) - timedelta(minutes=minutes), done, source)
    for episode in batch:
        world.aired[episode["number"]] = scheduled or download.grabbed - delay
    for episode, size in zip(batch, download.sizes):
        world.files[episode["number"]] = (quality, size, download.group, episode["number"] * 10 + 1)
    return download


def _upgrade(world: _World, first: _Download, anchor: datetime, recent: bool = False) -> _Download | None:
    """A better version of a single episode later on - or None (most episodes keep their first file).

    `recent`: the newest episode of a series airing now gets one half-way between its import and now, so the last
    days have an upgrade too.
    """
    episode = first.episodes[0]
    rng = demo_world.random_for(f"sonarr-upgrade:{episode['number']}")
    if (rng.random() >= UPGRADE_SHARE or first.quality >= BEST) and not recent:
        return None
    imported = anchor - timedelta(days=span(rng, UPGRADE_AGE))
    if recent or imported <= first.done[0] + timedelta(hours=12):      # not before the file it replaces: after it
        imported = first.done[0] + (anchor - first.done[0]) * (0.5 if recent else rng.uniform(0.2, 0.9))
    quality = min(BEST, first.quality + rng.choice((1, 1, 2)))
    grabbed = imported - timedelta(minutes=min(span(rng, DOWNLOAD), (imported - first.done[0]).total_seconds() / 120))
    download = _download(rng, "upgrade", first.series, [episode], quality, grabbed, [imported], weighted(rng, UPGRADE_SOURCES))
    download.replaced = [world.files[episode["number"]]]
    world.files[episode["number"]] = (quality, download.sizes[0], download.group, episode["number"] * 10 + 2)
    return download


def _failures(singles: list[_Download], anchor: datetime) -> list[_Download]:
    """Two upgrades that failed in the client, both from the flaky indexer: one this week, one months ago."""
    result = []
    for place, (low, high) in zip(sorted({1, len(singles) // 2}), FAILURE_AGE):
        if place >= len(singles):
            continue
        first = singles[place]
        rng = demo_world.random_for(f"sonarr-failure:{first.episodes[0]['number']}")
        grabbed = anchor - timedelta(days=rng.uniform(low, high))
        if grabbed <= first.done[0] + timedelta(days=1):
            grabbed = first.done[0] + timedelta(days=rng.uniform(1, 5))
        failed = grabbed + timedelta(minutes=rng.uniform(10, 45))
        result.append(_download(rng, "failed", first.series, first.episodes, min(BEST, first.quality + 1), grabbed, [failed],
                                "Rss", indexer=demo_world.FLAKY_INDEXER))
    return result


def _world(moment: datetime) -> _World:
    """What the made-up Sonarr has downloaded by `moment`, from the catalog of the demo's series."""
    anchor = moment.replace(minute=0, second=0, microsecond=0)
    world = _World(demo_world.series())
    for series in world.series:
        episodes = [e for season in series["seasons"].values() for e in season if e["added_at"]]
        world.downloads += [_first(world, series, batch) for batch in _batches(episodes)]
        if series["number"] == LATE_SERIES:      # an old series: its season aired weekly in its year, long before
            start = datetime(series["year"] or 2000, 9, 22, 2, tzinfo=UTC)
            world.aired.update({e["number"]: start + (e["episode"] - 1) * WEEK for e in episodes})
    singles = sorted((d for d in world.downloads if len(d.episodes) == 1), key=lambda d: d.episodes[0]["number"])
    airing = [d for d in singles if d.quality < BEST and d.done[0] <= anchor - timedelta(hours=12)
              and demo_world.aired(d.series["number"], d.episodes[0]["season"], d.episodes[0]["episode"])]
    recent = max(airing, key=lambda d: d.done[0], default=None)
    world.downloads += [d for d in (_upgrade(world, first, anchor, first is recent) for first in singles) if d is not None]
    world.downloads += _failures(singles, anchor)
    for series in world.series:
        rng = demo_world.random_for(f"sonarr-series:{series['number']}")
        own = [d for d in world.downloads if d.series is series]
        aired = [world.aired[e["number"]] for d in own for e in d.episodes if e["number"] in world.aired]
        if series["number"] == LATE_SERIES and own:
            world.added[series["number"]] = min(d.grabbed for d in own) - timedelta(hours=rng.uniform(1, 3))
        else:
            world.added[series["number"]] = min(aired, default=series["added_at"] or anchor) - timedelta(days=rng.uniform(3, 60))
    return world


# ---------------------------------------------------------------------------
# Resources: a series, an episode, a quality as Sonarr writes them
# ---------------------------------------------------------------------------

def _quality(quality: int) -> dict[str, Any]:
    number, name, source, resolution = QUALITIES[quality][:4]
    return {"quality": {"id": number, "name": name, "source": source, "resolution": resolution},
            "revision": {"version": 1, "real": 0, "isRepack": False}}


def _release(title: str, label: str, quality: int, group: str) -> str:
    return f"{'.'.join(title.split())}.{label}.{QUALITIES[quality][4]}-{group}"


def _runtime(number: int) -> int:
    return demo_world.random_for(f"sonarr-runtime:{number}").choice((22, 30, 45, 55))


def _series(world: _World, series: dict[str, Any], moment: datetime) -> dict[str, Any]:
    number, title = series["number"], series["title"]
    week = demo_world.airings(number, moment - WEEK, moment)
    last_season = max([*series["seasons"], *(season for season, _e, _a in week)], default=1)
    return {"title": title, "sortTitle": title.lower(), "status": "continuing" if week else "ended", "ended": not week,
            "airTime": f"{week[-1][2].hour if week else 21:02d}:00",
            "images": [{"coverType": "poster", "url": f"/MediaCover/{number}/poster.jpg"}],
            "originalLanguage": LANGUAGES[0], "seasons": [{"seasonNumber": s, "monitored": True} for s in range(1, last_season + 1)],
            "year": series["year"] or 0, "path": f"/tv/{title}", "qualityProfileId": 1, "seasonFolder": True, "monitored": True,
            "monitorNewItems": "all", "useSceneNumbering": False, "runtime": _runtime(number),
            "tvdbId": int(series["tvdb_id"] or 0), "tmdbId": int(series["tmdb_id"] or 0), "seriesType": "standard",
            "cleanTitle": "".join(c for c in title.lower() if c.isalnum()), "titleSlug": "-".join(title.lower().split()),
            "genres": [], "tags": [], "added": demo_world.iso(world.added.get(number, moment)), "languageProfileId": 1, "id": number}


def _episode(series: dict[str, Any], season: int, episode: int, title: str, aired: datetime, file_id: int = 0) -> dict[str, Any]:
    number = series["number"] * 10_000 + season * 100 + episode     # = demo_world's episode numbers
    return {"seriesId": series["number"], "tvdbId": int(demo_world.tvdb_id(f"sonarr-episode:{number}")), "episodeFileId": file_id,
            "seasonNumber": season, "episodeNumber": episode, "title": title, "airDate": aired.date().isoformat(),
            "airDateUtc": demo_world.iso(aired), "runtime": _runtime(series["number"]), "hasFile": bool(file_id),
            "monitored": True, "unverifiedSceneNumbering": False, "id": number}


def _library_episode(world: _World, series: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    file_id = world.files.get(episode["number"], (0, 0, "", 0))[3]
    return _episode(series, episode["season"], episode["episode"], episode["title"],
                    world.aired.get(episode["number"], episode["added_at"]), file_id)


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------

def _grab(download: _Download) -> dict[str, Any]:
    """The data of a grab: where it came from, how, how big - the same for every episode of a pack."""
    published = download.grabbed - timedelta(minutes=RELEASE_AGE)
    return {"indexer": download.indexer, "releaseGroup": download.group, "age": "0", "ageHours": f"{RELEASE_AGE / 60:.4f}",
            "ageMinutes": str(RELEASE_AGE), "publishedDate": demo_world.iso(published), "downloadClient": CLIENT_TYPES[download.protocol],
            "downloadClientName": demo_world.DOWNLOAD_CLIENTS[download.protocol], "size": str(sum(download.sizes)),
            "tvdbId": str(download.series["tvdb_id"]), "tvRageId": "0", "imdbId": None,
            "protocol": PROTOCOL_NUMBERS[download.protocol], "customFormatScore": str(QUALITIES[download.quality][6]),
            "seriesMatchType": "Id", "releaseSource": download.source, "indexerFlags": "0",
            "releaseType": "SeasonPack" if len(download.episodes) > 1 else "SingleEpisode"}


def _records(world: _World, moment: datetime) -> list[dict[str, Any]]:
    """Every record of the history up to `moment`, oldest first - without the series and the episode (see _embedded)."""
    records = []
    for d in world.downloads:
        grab_slot, done_slot = SLOTS[d.kind]
        pack = len(d.episodes) > 1
        title = d.series["title"]
        for place, (episode, done, size) in enumerate(zip(d.episodes, d.done, d.sizes)):
            label = f"S{episode['season']:02d}E{episode['episode']:02d}"
            release = _release(title, f"S{episode['season']:02d}" if pack else label, d.quality, d.group)
            base = {"episodeId": episode["number"], "seriesId": d.series["number"], "sourceTitle": release, "languages": LANGUAGES,
                    "quality": _quality(d.quality), "customFormats": [], "customFormatScore": QUALITIES[d.quality][6],
                    "qualityCutoffNotMet": d.quality < CUTOFF, "downloadId": d.download_id}
            client = {"downloadClient": CLIENT_TYPES[d.protocol], "downloadClientName": demo_world.DOWNLOAD_CLIENTS[d.protocol]}
            records.append({**base, "date": demo_world.iso(d.grabbed), "eventType": "grabbed", "data": _grab(d),
                            "id": episode["number"] * 10 + grab_slot})
            if d.kind == "failed":
                records.append({**base, "date": demo_world.iso(done), "eventType": "downloadFailed", "id": episode["number"] * 10 + done_slot,
                                "data": {**client, "message": FAILURE_MESSAGE, "releaseGroup": d.group, "size": str(size)}})
                continue
            if d.replaced:
                old_quality, old_size, old_group, _file = d.replaced[place]
                records.append({**{k: v for k, v in base.items() if k != "downloadId"}, "quality": _quality(old_quality),
                                "sourceTitle": _release(title, label, old_quality, old_group), "date": demo_world.iso(done),
                                "eventType": "episodeFileDeleted", "id": episode["number"] * 10 + DELETION_SLOT,
                                "data": {"reason": "Upgrade", "releaseGroup": old_group, "size": str(old_size), "indexerFlags": "0",
                                         "releaseType": "SingleEpisode"}})
            file_name = _release(title, label, d.quality, d.group) + ".mkv"
            records.append({**base, "date": demo_world.iso(done), "eventType": "downloadFolderImported",
                            "id": episode["number"] * 10 + done_slot,
                            "data": {"fileId": str(episode["number"] * 10 + (2 if d.kind == "upgrade" else 1)),
                                     "droppedPath": f"/downloads/complete/{release}/{file_name}",
                                     "importedPath": f"/tv/{title}/Season {episode['season']}/{file_name}", **client,
                                     "releaseGroup": d.group, "customFormatScore": str(QUALITIES[d.quality][6]), "size": str(size),
                                     "indexerFlags": "0", "releaseType": "SeasonPack" if pack else "SingleEpisode"}})
    records = [r for r in records if r["date"] <= demo_world.iso(moment)]
    return sorted(records, key=lambda r: (r["date"], r["id"]))


def _embedded(world: _World, records: list[dict[str, Any]], params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    """The records with their series and episode, when the request asks for them (includeSeries, includeEpisode)."""
    with_series, with_episode = params.get("includeSeries") == "true", params.get("includeEpisode") == "true"
    if not (with_series or with_episode):
        return records
    series = {s["number"]: s for s in world.series}
    shown = {number: _series(world, s, moment) for number, s in series.items()} if with_series else {}
    episodes = {e["number"]: _library_episode(world, s, e) for s in world.series for season in s["seasons"].values()
                for e in season} if with_episode else {}
    return [{**r, **({"series": shown[r["seriesId"]]} if with_series else {}),
             **({"episode": episodes[r["episodeId"]]} if with_episode else {})} for r in records]


def _history(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    page = demo_world.page(_records(world, moment), params, "date")
    return {**page, "records": _embedded(world, page["records"], params, moment)}


def _history_since(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    try:
        since = demo_world.iso(datetime.strptime(str(params.get("date") or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC))
    except ValueError:
        since = ""
    world = _world(moment)
    return _embedded(world, [r for r in _records(world, moment) if r["date"] >= since], params, moment)


# ---------------------------------------------------------------------------
# The live state: queue, calendar, missing, cutoff, health
# ---------------------------------------------------------------------------

def _library(world: _World) -> dict[int, dict[str, Any]]:
    """The library's episodes by their number."""
    return {e["number"]: e for s in world.series for season in s["seasons"].values() for e in season}


def _gaps(world: _World, series: dict[str, Any]) -> list[dict[str, Any]]:
    """The episodes Sonarr knows after the library's last one of the first season (MISSING_EPISODES), a week apart."""
    episodes = series["seasons"].get(min(series["seasons"], default=0), [])
    last = max(episodes, key=lambda e: e["episode"], default=None)
    if last is None:
        return []
    aired = world.aired.get(last["number"], last["added_at"])
    return [_episode(series, last["season"], last["episode"] + step, "", aired + step * WEEK)
            for step in range(1, MISSING_EPISODES.get(series["number"], 0) + 1)]


def _missing(world: _World, moment: datetime) -> list[dict[str, Any]]:
    """What aired and has no file: the gaps after the library's episodes, the last weeks' airings it does not have."""
    found, have = [], _library(world)
    for series in world.series:
        found += _gaps(world, series)
        found += [e for e in (_episode(series, season, episode, "", aired) for season, episode, aired
                              in demo_world.airings(series["number"], moment - MISSING_WEEKS * WEEK, moment)) if e["id"] not in have]
    return sorted(found, key=lambda e: (e["airDateUtc"], e["id"]), reverse=True)


def _cutoff(world: _World) -> list[dict[str, Any]]:
    """The library's episodes whose file is below the cutoff (WEBDL-1080p)."""
    found = [_library_episode(world, series, e) for series in world.series for season in series["seasons"].values()
             for e in season if world.files.get(e["number"], (BEST,))[0] < CUTOFF]
    return sorted(found, key=lambda e: (e["airDateUtc"], e["id"]), reverse=True)


def _wanted(which: Callable[[_World, datetime], list[dict[str, Any]]]) -> Callable[[httpx.QueryParams, datetime], dict[str, Any]]:
    def answer(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
        return demo_world.page(which(_world(moment), moment), params, "episodes.airDateUtc", "descending")
    return answer


def _queue_episodes(world: _World, series: dict[str, Any], what: str, moment: datetime) -> tuple[list[dict[str, Any]], int | None]:
    """The episodes of one download of the queue and its quality, when not the spec's.

    The library's whole first season; this week's airing - itself while the library does not have it, else a better
    version of it; the first missing episode.
    """
    if what == "pack":
        return [_library_episode(world, series, e) for e in series["seasons"].get(1, [])], None
    if what == "aired":
        week = demo_world.airings(series["number"], moment - WEEK, moment)
        if not week:
            return [], None
        newest = _episode(series, *week[-1][:2], "", week[-1][2])
        own = _library(world).get(newest["id"])
        if own is None:
            return [newest], None
        return [_library_episode(world, series, own)], min(BEST, world.files[own["number"]][0] + 1)
    return _gaps(world, series)[:1], None


def _queue(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    by_number = {s["number"]: s for s in world.series}
    records = []
    for key, number, what, quality, indexer, minutes in QUEUE:
        series = by_number.get(number)
        episodes, better = _queue_episodes(world, series, what, moment) if series else ([], None)
        if not episodes:
            continue
        quality = quality if better is None else better
        rng = demo_world.random_for(f"sonarr-queue:{key}:{number}")
        name, protocol = demo_world.INDEXERS[indexer]
        size = int(sum(rng.uniform(*QUALITIES[quality][5]) for _ in episodes) * GB)
        first = episodes[0]
        label = f"S{first['seasonNumber']:02d}" + ("" if what == "pack" else f"E{first['episodeNumber']:02d}")
        release = _release(series["title"], label, quality, rng.choice(GROUPS))
        if minutes:
            share = demo_world.cycle_share(moment, f"sonarr:{key}:{number}", minutes)
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
        for episode in episodes:
            records.append({"seriesId": number, "episodeId": episode["id"], "seasonNumber": episode["seasonNumber"],
                            "series": _series(world, series, moment), "episode": episode, "languages": LANGUAGES,
                            "quality": _quality(quality), "customFormats": [], "customFormatScore": QUALITIES[quality][6],
                            "size": size, "title": release, "estimatedCompletionTime": demo_world.iso(moment + left), **state,
                            "downloadId": download_id, "protocol": protocol, "downloadClient": demo_world.DOWNLOAD_CLIENTS[protocol],
                            "downloadClientHasPostImportCategory": False, "indexer": name, "episodeHasFile": episode["hasFile"],
                            "timeleft": demo_world.clock(left), "id": rng.getrandbits(31)})
    for record in records:
        for embedded, asked in (("series", "includeSeries"), ("episode", "includeEpisode")):
            if params.get(asked) != "true":
                record.pop(embedded)
    return demo_world.page(records, params, "timeleft")


def _calendar(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    try:
        start, end = (datetime.strptime(str(params.get(name) or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
                      for name in ("start", "end"))
    except ValueError:
        start, end = moment, moment + WEEK
    world = _world(moment)
    found, have = [], _library(world)
    for series in world.series:
        for season, episode, aired in demo_world.airings(series["number"], start, end):
            entry = _episode(series, season, episode, "", aired)
            entry = _library_episode(world, series, have[entry["id"]]) if entry["id"] in have else entry
            found.append({**entry, **({"series": _series(world, series, moment)} if params.get("includeSeries") == "true" else {})})
    return sorted(found, key=lambda e: (e["airDateUtc"], e["id"]))


def _health(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    found = [{"source": "UpdateCheck", "type": "warning", "message": f"New update is available: {UPDATE}",
              "wikiUrl": "https://wiki.servarr.com/sonarr/system#new-update-is-available"}]
    if demo_world.flaky_down(moment):
        found.append({"source": "IndexerStatusCheck", "type": "warning",
                      "message": f"Indexers unavailable due to failures: {demo_world.FLAKY_INDEXER}",
                      "wikiUrl": "https://wiki.servarr.com/sonarr/system#indexers-are-unavailable-due-to-failures"})
    return found


def _status(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    started = moment.replace(minute=0, second=0, microsecond=0) - timedelta(days=6, hours=5)
    return {"appName": NAME, "instanceName": NAME, "version": VERSION, "buildTime": "2026-09-16T16:37:42Z", "isDebug": False,
            "isProduction": True, "isAdmin": False, "isUserInteractive": False, "startupPath": "/app/sonarr/bin", "appData": "/config",
            "osName": "debian", "osVersion": "12", "isNetCore": True, "isLinux": True, "isOsx": False, "isWindows": False,
            "isDocker": True, "mode": "console", "branch": "main", "authentication": "forms", "migrationVersion": 217,
            "urlBase": "", "runtimeVersion": "6.0.36", "runtimeName": ".NET", "startTime": demo_world.iso(started),
            "packageUpdateMechanism": "docker", "databaseVersion": "3.46.1", "databaseType": "sqLite"}


def _series_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    world = _world(moment)
    found = [_series(world, series, moment) for series in world.series]
    wanted = params.get("tvdbId")
    return [s for s in found if str(s["tvdbId"]) == wanted] if wanted else found


def _command(request: httpx.Request, moment: datetime) -> dict[str, Any]:
    """A command (RefreshMonitoredDownloads) taken into the queue of commands - the made-up Sonarr does nothing with it."""
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        body = {}
    name = str(body.get("name") or "") if isinstance(body, dict) else ""
    return {"name": name, "body": {"sendUpdatesToClient": False}, "priority": "normal", "status": "queued",
            "queued": demo_world.iso(moment), "trigger": "manual", "id": int(moment.timestamp()) % 100_000}


# API path (after /api/v3/) -> its answer to a GET.
ANSWERS: dict[str, Callable[[httpx.QueryParams, datetime], Any]] = {
    "system/status": _status, "history": _history, "history/since": _history_since, "series": _series_list,
    "queue": _queue, "calendar": _calendar, "health": _health,
    "wanted/missing": _wanted(_missing), "wanted/cutoff": _wanted(lambda world, _moment: _cutoff(world)),
}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Sonarr's answer to one request (httpx.MockTransport)."""
    path = request.url.path.split(API, 1)[-1].strip("/").lower()
    moment = demo_world.now()
    if request.method == "POST":
        return httpx.Response(201, json=_command(request, moment)) if path == "command" else httpx.Response(404)
    answer = ANSWERS.get(path) if request.method == "GET" else None
    if answer is None:
        return httpx.Response(404)
    return httpx.Response(200, json=answer(request.url.params, moment))
