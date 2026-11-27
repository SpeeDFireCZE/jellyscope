"""The demo's made-up Lidarr: the demo's albums (demo_world.albums) as Lidarr v3's API answers them.

The demo (JELLYSCOPE_DEMO) shows Lidarr through its real code: the seed connects it at http://lidarr.demo and runs
its real sync and counts (demo_apps.seed_apps), the page asks for the live state and the card "Stahuje se" for the
queue. `handler` answers them inside the process (httpx.MockTransport through demo_switch) in the shape of a real
Lidarr (tests/lidarr_responses/, tests/_apps.py) - every path the package's client asks. The unit is the album:

* `system/status` - the app's name and version (the connection test, the sync's check);
* `history` (in pages, oldest first) and `history/since` - a grab per album, an import per track and one for the
  album, upgrades (the old tracks deleted, the better ones imported) and two failed downloads, with the album and the
  artist when asked. Every album of the library was imported before Jellyfin took it in (its `added_at`), in the
  quality its files have (codec, bitrate, bit depth, size); one, the household's own rip, by hand without a download.
  The albums of the last weeks came out after their artist was in Lidarr: it grabbed them after their release
  (demo_world.album_release), the older ones right after their artist was added (the back catalog). Upgrades and
  failures happen over the last year, counted back from the hour (the same answer within an hour) - one album of the
  last weeks got its better version a day after it came (demo_world.album_upgrade);
* `album` and `artist` - the library's albums and their artists with made-up MusicBrainz ids (the sync pairs them with
  the library by name, the demo's files carry no MusicBrainz ids), and the albums coming out (demo_world.coming_albums);
* `queue` - four downloads whose progress goes round with the clock (demo_world.cycle_share), one of them stuck with
  a warning; `command` (POST) is accepted and changes nothing;
* `calendar` - the albums with a release in the range; `wanted/missing` and `wanted/cutoff` - what is out and not
  downloaded, what is below the cutoff (lossless);
* `health` - one warning: an update is available.

Everything else is 404, the covers (`mediacover/...`) too: the demo has no images from the apps and the client takes
404 as "no cover", without an error. The numbers come from the catalog and a random source of its own per album
(demo_world.random_for): the same library gives the same history. The moment is `demo_world.now()`.

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

API = "/api/v1/"
VERSION = "3.1.6.5079"
UPDATE = "3.1.7.5112"         # the version its health says is available
# (Lidarr's id, name, the release's label, kb/s - by it the size of a file the demo has none of), from the worst to
# the best of the profile.
QUALITIES = (
    (0, "Unknown", "", 160), (12, "AAC-VBR", "AAC", 128), (1, "MP3-192", "MP3-192", 192), (3, "MP3-256", "MP3-256", 256),
    (2, "MP3-VBR-V0", "MP3-V0", 245), (4, "MP3-320", "MP3-320", 320), (7, "ALAC", "ALAC", 900), (6, "FLAC", "FLAC", 950),
    (21, "FLAC 24bit", "FLAC 24bit", 2300),
)
MP3_320, ALAC, FLAC, FLAC_24 = 5, 6, 7, 8
CUTOFF = ALAC         # the profile wants lossless: an album below it is in wanted/cutoff
# The version an album had before its upgrade, by the quality its files have now (one of them, by chance).
WORSE = {MP3_320: (2, 3), ALAC: (4, 5), FLAC: (4, 5, 5), FLAC_24: (5, 7)}
MANUAL_CODEC = "opus"         # the household's own rip: imported by hand, a format Lidarr has no quality for
TRACK_SECONDS = 240           # an album's track for the size of a release the library does not have
GROUPS = ("KVETEN", "DUHA", "SKRIN", "PODKROVI", "STUDNA", "VINYL")
# How a grab started (data.releaseSource), with weights: an album Lidarr waited for, one of the back catalog (searched
# for when its artist was added), an upgrade.
WAITED_SOURCES = (("Rss", 70), ("Search", 12), ("UserInvokedSearch", 8), ("InteractiveSearch", 6), ("ReleasePush", 4))
BACK_CATALOG_SOURCES = (("Search", 60), ("UserInvokedSearch", 15), ("InteractiveSearch", 25))
UPGRADE_SOURCES = (("Rss", 65), ("Search", 25), ("InteractiveSearch", 10))
DOWNLOAD = ((2, 12, 45), (12, 60, 40), (60, 300, 15))      # (from, to minutes, weight): from the grab to the import
UPGRADE_SHARE = 0.6                                        # how many albums with lossless files got them later
UPGRADE_AGE = ((0.5, 7, 15), (7, 30, 20), (30, 330, 65))   # (from, to days ago, weight)
FAILURE_AGE = ((1.5, 5), (40, 200))                        # days ago of the two failed downloads
FAILURE_MESSAGE = "Aborted, cannot be completed"
RELEASE_AGE = 26              # minutes from a release's publishing to its grab
TRACK_GAP = 3                 # seconds between the imports of an album's tracks
DELETION_AHEAD = 90           # seconds the old tracks are deleted before the better ones are imported
# Record ids: an album's number * 1000 + the record's place - a grab and the album's import or failure (SLOTS), a
# track's record adds the track's number to its block (a first import's, a deletion's, an upgrade's) - so the same
# record keeps its id at every answer.
SLOTS = {"first": (1, 2), "upgrade": (3, 4), "failed": (5, 6)}
TRACKS = {"first": 100, "deleted": 200, "upgrade": 300}
PROTOCOL_NUMBERS = {"usenet": "1", "torrent": "2"}
# Lidarr's id of the first album coming out: the library's albums have their catalog numbers, these come after them.
COMING_IDS = 101
# The queue: (key, which album, quality, the indexer's place in demo_world.INDEXERS, the cycle in minutes; 0 = it does
# not move). An album out already that the library does not have, lossless versions of the newest albums below the
# cutoff, and one more whose import is blocked.
QUEUE = (("out", "coming", FLAC, 0, 40), ("better", "cutoff", FLAC, 1, 30), ("flaky", "cutoff", FLAC_24, 2, 50),
         ("stuck", "cutoff", FLAC, 3, 0))
STUCK_MESSAGES = [{"title": "One or more albums expected in this release were not imported or missing", "messages": []},
                  {"title": "", "messages": ["Album match is not close enough: 71.4% vs 80%"]}]


@dataclass
class _Artist:
    """An artist the made-up Lidarr has: the catalog's number, name and Jellyfin id, when it was added."""

    number: int
    name: str
    jellyfin_id: str
    genres: list[str]
    added: datetime | None = None


@dataclass
class _Album:
    """An album the made-up Lidarr knows: its release, when it came to Lidarr, its files."""

    number: int                                # Lidarr's id: the catalog's number, COMING_IDS on for those coming out
    title: str
    artist: _Artist
    tracks: int
    released: datetime | None
    added: datetime | None = None              # when it came to Lidarr
    entry: dict[str, Any] | None = None        # the catalog's entry; None = the library does not have it
    file: tuple[int, int] | None = None        # (quality, size); None = no files


@dataclass
class _Download:
    """One release the made-up Lidarr took: where from, and when it was imported (or failed)."""

    kind: str                                  # "first", "upgrade" or "failed" (SLOTS)
    album: _Album
    quality: int                               # the place in QUALITIES
    grabbed: datetime | None                   # None = imported by hand, no download
    done: datetime                             # the import (its first track), or the failure
    size: int
    indexer: str = ""
    protocol: str = ""
    download_id: str | None = None
    source: str = ""
    group: str = ""
    replaced: tuple[int, int] | None = None    # the upgraded files (see _Album.file)


@dataclass
class _World:
    """The made-up Lidarr at one moment: its artists, albums and what it downloaded."""

    artists: dict[int, _Artist] = field(default_factory=dict)
    albums: list[_Album] = field(default_factory=list)
    downloads: list[_Download] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The world: what the made-up Lidarr downloaded
# ---------------------------------------------------------------------------

def _midnight(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _library_quality(entry: dict[str, Any]) -> int:
    """The quality of the library's files, by their codec, bitrate and bit depth."""
    codec, bitrate = entry["codec"], entry["bitrate"]
    if codec in ("flac", "alac"):
        return ALAC if codec == "alac" else FLAC_24 if entry["bit_depth"] >= 24 else FLAC
    if codec == "mp3":
        return MP3_320 if bitrate >= 300_000 else 3 if bitrate >= 240_000 else 2
    return 1 if codec == "aac" else 0


def _size(rng: Any, quality: int, tracks: int) -> int:
    return int(tracks * TRACK_SECONDS * QUALITIES[quality][3] * 125 * rng.uniform(0.85, 1.15))


def _download(rng: Any, kind: str, album: _Album, quality: int, grabbed: datetime, done: datetime, source: str,
              size: int = 0, indexer: str = "") -> _Download:
    """A release with its indexer (by weight, the flaky one less often), client, size and the client's download id."""
    if not indexer:
        indexer = weighted(rng, ((name, 20 if name == demo_world.FLAKY_INDEXER else 30)
                                 for name, _protocol in demo_world.INDEXERS if name != demo_world.DISABLED_INDEXER))
    protocol = dict(demo_world.INDEXERS)[indexer]
    download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
    return _Download(kind, album, quality, grabbed, done, size or _size(rng, quality, album.tracks), indexer, protocol,
                     download_id, source, rng.choice(GROUPS))


def _first(world: _World, entry: dict[str, Any]) -> _Download:
    """The first download of an album of the library: imported before Jellyfin took it in, in its files' quality.

    An album of the last weeks Lidarr waited for: it grabbed it after its release. The rest is the back catalog,
    grabbed once its artist was in Lidarr; their release is a day of their year (none for an album without one).
    """
    rng = demo_world.random_for(f"lidarr:{entry['number']}")
    released = demo_world.album_release(entry)
    if released:      # Jellyfin took it in a minute or two after the import
        done = entry["added_at"] - timedelta(minutes=rng.uniform(1, 3))
        minutes = min(span(rng, DOWNLOAD), (done - released).total_seconds() / 120)
    else:             # Jellyfin's scan took it in up to a few hours later
        done, minutes = entry["added_at"] - timedelta(minutes=rng.uniform(2, 240)), span(rng, DOWNLOAD)
        if entry["year"]:
            released = min(_midnight(datetime(entry["year"], 1, 1, tzinfo=UTC) + timedelta(days=rng.uniform(0, 360))),
                           _midnight(done - timedelta(days=30)))
    album = _Album(entry["number"], entry["title"], world.artists[entry["artist_number"]], entry["tracks"], released, entry=entry)
    if entry["codec"] == MANUAL_CODEC:
        download = _Download("first", album, 0, None, done, entry["size_bytes"])
    else:
        sources = WAITED_SOURCES if demo_world.album_release(entry) else BACK_CATALOG_SOURCES
        download = _download(rng, "first", album, _library_quality(entry), done - timedelta(minutes=minutes), done,
                             weighted(rng, sources), size=entry["size_bytes"])
    album.file = (download.quality, download.size)
    world.albums.append(album)
    return download


def _added(world: _World, firsts: list[_Download]) -> None:
    """When the artists and the albums came to Lidarr.

    An artist came before its first grab (Lidarr searched for its albums then) and long before the release of an album
    of the last weeks: such an album came to Lidarr when announced, the others with their artist.
    """
    earliest: dict[int, datetime] = {}
    for d in firsts:
        own = d.album.released - timedelta(days=30) if demo_world.album_release(d.album.entry) else (d.grabbed or d.done)
        earliest[d.album.artist.number] = min(earliest.get(d.album.artist.number, own), own)
    for number, artist in world.artists.items():
        artist.added = earliest[number] - timedelta(hours=demo_world.random_for(f"lidarr-artist:{number}").uniform(1, 3))
    for d in firsts:
        rng, album = demo_world.random_for(f"lidarr-added:{d.album.number}"), d.album
        if demo_world.album_release(album.entry):
            album.added = max(album.artist.added + timedelta(hours=1), album.released - timedelta(days=rng.uniform(5, 40)))
        else:
            album.added = album.artist.added + timedelta(seconds=rng.uniform(5, 90))


def _upgrade(first: _Download, anchor: datetime) -> _Download | None:
    """A better version later on - or None (many albums keep their first files, and none is older than its files).

    The library's files are the better ones: the first download was worse (WORSE). The album of the last weeks
    demo_world.album_upgrade names got its better version that long after it came, from the flaky indexer (one of
    its releases that did come through), the others by chance in the last year.
    """
    album = first.album
    rng = demo_world.random_for(f"lidarr-upgrade:{album.number}")
    after = demo_world.album_upgrade(album.entry)
    if first.grabbed is None or first.quality not in WORSE or (after is None and rng.random() >= UPGRADE_SHARE):
        return None
    imported = first.done + after if after else anchor - timedelta(days=span(rng, UPGRADE_AGE))
    if imported <= first.done + timedelta(hours=12):
        return None
    grabbed = imported - timedelta(minutes=min(span(rng, DOWNLOAD), (imported - first.done).total_seconds() / 120))
    better = _download(rng, "upgrade", album, first.quality, grabbed, imported, weighted(rng, UPGRADE_SOURCES), size=first.size,
                       indexer=demo_world.FLAKY_INDEXER if after else "")
    first.quality = rng.choice(WORSE[first.quality])
    first.size = int(first.size * QUALITIES[first.quality][3] / QUALITIES[better.quality][3])
    better.replaced = (first.quality, first.size)
    return better


def _failures(firsts: list[_Download], anchor: datetime) -> list[_Download]:
    """Two lossless versions that failed in the client, both from the flaky indexer: one this week, one months ago."""
    candidates = [d for d in firsts if d.grabbed and d.album.file and d.album.file[0] < CUTOFF and d.done < anchor - timedelta(days=30)]
    result = []
    for place, (low, high) in zip(sorted({1, len(candidates) // 2}), FAILURE_AGE):
        if place >= len(candidates):
            continue
        first = candidates[place]
        rng = demo_world.random_for(f"lidarr-failure:{first.album.number}")
        grabbed = anchor - timedelta(days=rng.uniform(low, high))
        if grabbed <= first.done + timedelta(days=1):
            grabbed = first.done + timedelta(days=rng.uniform(1, 5))
        failed = grabbed + timedelta(minutes=rng.uniform(10, 45))
        result.append(_download(rng, "failed", first.album, FLAC, grabbed, failed, "Rss", indexer=demo_world.FLAKY_INDEXER))
    return result


def _coming(world: _World, moment: datetime) -> None:
    """The albums coming out (demo_world.coming_albums): announced to Lidarr weeks before, without files."""
    for place, coming in enumerate(demo_world.coming_albums(moment)):
        artist = world.artists.get(coming["artist_number"])
        if artist is None:
            continue
        rng = demo_world.random_for(f"lidarr-coming:{coming['key']}")
        world.albums.append(_Album(COMING_IDS + place, coming["title"], artist, coming["tracks"], coming["released"],
                                   added=coming["released"] - timedelta(days=rng.uniform(10, 40), hours=rng.uniform(0, 20))))


def _world(moment: datetime) -> _World:
    """What the made-up Lidarr has downloaded by `moment`, from the catalog of the demo's albums."""
    anchor = moment.replace(minute=0, second=0, microsecond=0)
    world = _World()
    entries = [entry for entry in demo_world.albums() if entry["added_at"] and entry["artist_number"]]
    for entry in entries:
        world.artists.setdefault(entry["artist_number"], _Artist(entry["artist_number"], entry["artist"],
                                                                 entry["artist_jellyfin_id"], entry["genres"]))
    firsts = [_first(world, entry) for entry in entries]
    _added(world, firsts)
    world.downloads += firsts
    world.downloads += [d for d in (_upgrade(first, anchor) for first in firsts) if d is not None]
    world.downloads += _failures(firsts, anchor)
    _coming(world, moment)
    return world


# ---------------------------------------------------------------------------
# Resources: an artist, an album, a quality as Lidarr writes them
# ---------------------------------------------------------------------------

def _iso(moment: datetime | None) -> str | None:
    return demo_world.iso(moment) if moment else None


def _quality(quality: int) -> dict[str, Any]:
    number, name = QUALITIES[quality][:2]
    return {"quality": {"id": number, "name": name}, "revision": {"version": 1, "real": 0, "isRepack": False}}


def _release(album: _Album, quality: int) -> str:
    year = f" ({album.released.year})" if album.released else ""
    label = QUALITIES[quality][2]
    return f"{album.artist.name} - {album.title}{year}" + (f" [{label}]" if label else "")


def _artist_mbid(artist: _Artist) -> str:
    return demo_world.musicbrainz_id(f"artist:{artist.jellyfin_id}")


def _album_mbid(album: _Album, kind: str = "album") -> str:
    return demo_world.musicbrainz_id(f"{kind}:{album.artist.name}:{album.title}")


def _artist(artist: _Artist, full: bool = True) -> dict[str, Any]:
    """The artist as Lidarr's API sends it (`artist`; shorter embedded in the history and the queue)."""
    brief = {"id": artist.number, "artistName": artist.name, "foreignArtistId": _artist_mbid(artist), "monitored": True,
             "status": "continuing"}
    if not full:
        return brief
    return {**brief, "artistMetadataId": artist.number, "ended": False, "artistType": "Group", "disambiguation": "",
            "overview": "", "sortName": artist.name.lower(), "cleanName": "".join(c for c in artist.name.lower() if c.isalnum()),
            "genres": artist.genres, "images": [], "links": [], "path": f"/music/{artist.name}", "rootFolderPath": "/music",
            "qualityProfileId": 1, "metadataProfileId": 1, "monitorNewItems": "all", "tags": [], "added": _iso(artist.added)}


def _album(album: _Album, full: bool = True) -> dict[str, Any]:
    """The album as Lidarr's API sends it (`album`, the calendar, wanted; shorter embedded in the history and the queue)."""
    brief = {"id": album.number, "title": album.title, "artistId": album.artist.number, "foreignAlbumId": _album_mbid(album),
             "monitored": True, "albumType": "Album", "releaseDate": _iso(album.released)}
    if not full:
        return brief
    quality, size = album.file or (0, 0)
    media = [{"mediumNumber": 1, "mediumName": "", "mediumFormat": "Digital Media"}]
    duration = album.tracks * TRACK_SECONDS * 1000
    release = {"id": album.number * 10, "albumId": album.number, "foreignReleaseId": _album_mbid(album, "release"),
               "title": album.title, "status": "Official", "duration": duration, "trackCount": album.tracks, "media": media,
               "mediumCount": 1, "disambiguation": "", "country": ["Czechia"], "label": [], "format": "Digital Media",
               "monitored": True}
    files = album.tracks if album.file else 0
    return {**brief, "disambiguation": "", "overview": "", "anyReleaseOk": True, "profileId": 1, "duration": duration,
            "secondaryTypes": [], "mediumCount": 1, "ratings": {"votes": 0, "value": 0.0}, "releases": [release],
            "genres": album.entry["genres"] if album.entry else album.artist.genres, "media": media, "artist": _artist(album.artist),
            "images": [{"url": f"/MediaCover/Albums/{album.number}/cover.jpg", "coverType": "cover", "extension": ".jpg"}],
            "links": [], "added": _iso(album.added),
            "statistics": {"trackFileCount": files, "trackCount": album.tracks, "totalTrackCount": album.tracks,
                           "sizeOnDisk": size, "percentOfTracks": 100.0 if files else 0.0}}


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------

def _grab(download: _Download) -> dict[str, Any]:
    """The data of a grab: where it came from, how, how big."""
    published = download.grabbed - timedelta(minutes=RELEASE_AGE)
    indexer_id = [name for name, _protocol in demo_world.INDEXERS].index(download.indexer) + 1
    return {"indexer": download.indexer, "releaseGroup": download.group, "downloadClient": demo_world.DOWNLOAD_CLIENTS[download.protocol],
            "size": str(download.size), "protocol": PROTOCOL_NUMBERS[download.protocol], "customFormatScore": "0",
            "releaseSource": download.source, "publishedDate": demo_world.iso(published), "age": "0", "indexerId": str(indexer_id)}


def _track_sizes(size: int, tracks: int) -> list[int]:
    """An album's size spread over its tracks: every track the same, the rest of the division on the last one."""
    each = size // max(tracks, 1)
    return [each] * (tracks - 1) + [size - each * (tracks - 1)]


def _tracks(d: _Download, base: dict[str, Any]) -> list[dict[str, Any]]:
    """The track records of an import: the old files deleted a moment before an upgrade, then every track imported."""
    album, number = d.album, d.album.number * 1000
    folder, release = f"/music/{album.artist.name}/{album.title}", _release(album, d.quality)
    records = []
    if d.replaced:
        old_quality, old_size = d.replaced
        for track, size in enumerate(_track_sizes(old_size, album.tracks), start=1):
            records.append({**base, "trackId": album.number * 100 + track, "downloadId": None, "quality": _quality(old_quality),
                            "qualityCutoffNotMet": old_quality < CUTOFF, "date": demo_world.iso(d.done - timedelta(seconds=DELETION_AHEAD)),
                            "eventType": "trackFileDeleted", "id": number + TRACKS["deleted"] + track,
                            "data": {"reason": "Upgrade", "size": str(size)}})
    client = {"downloadClient": demo_world.DOWNLOAD_CLIENTS[d.protocol]} if d.grabbed else {}
    for track, size in enumerate(_track_sizes(d.size, album.tracks), start=1):
        name = f"{track:02d} {album.title} {track}"
        records.append({**base, "trackId": album.number * 100 + track, "eventType": "trackFileImported",
                        "date": demo_world.iso(d.done + timedelta(seconds=TRACK_GAP * (track - 1))),
                        "id": number + TRACKS[d.kind] + track,
                        "data": {"droppedPath": f"/downloads/complete/{release}/{name}", "importedPath": f"{folder}/{name}",
                                 **client, "releaseGroup": d.group, "size": str(size),
                                 "fileId": str(album.number * 1000 + TRACKS[d.kind] + track), "indexerFlags": "0"}})
    return records


def _records(world: _World, moment: datetime) -> list[dict[str, Any]]:
    """Every record of the history up to `moment`, oldest first - without the album and the artist (see _embedded)."""
    records = []
    for d in world.downloads:
        grab_slot, done_slot = SLOTS[d.kind]
        album, number = d.album, d.album.number * 1000
        base = {"albumId": album.number, "artistId": album.artist.number, "trackId": 0, "sourceTitle": _release(album, d.quality),
                "quality": _quality(d.quality), "qualityCutoffNotMet": d.quality < CUTOFF, "downloadId": d.download_id}
        if d.grabbed:
            records.append({**base, "date": demo_world.iso(d.grabbed), "eventType": "grabbed", "data": _grab(d), "id": number + grab_slot})
        client = {"downloadClient": demo_world.DOWNLOAD_CLIENTS.get(d.protocol, "")}
        if d.kind == "failed":
            records.append({**base, "date": demo_world.iso(d.done), "eventType": "downloadFailed", "id": number + done_slot,
                            "data": {**client, "message": FAILURE_MESSAGE, "indexer": d.indexer, "releaseGroup": d.group,
                                     "protocol": PROTOCOL_NUMBERS[d.protocol], "size": str(d.size)}})
            continue
        records += _tracks(d, base)
        if d.grabbed:      # the album's own record once its tracks are in
            records.append({**base, "date": demo_world.iso(d.done + timedelta(seconds=TRACK_GAP * album.tracks)),
                            "eventType": "downloadImported", "id": number + done_slot,
                            "data": {**client, "indexer": d.indexer, "downloadClientName": client["downloadClient"],
                                     "protocol": PROTOCOL_NUMBERS[d.protocol]}})
    records = [r for r in records if r["date"] <= demo_world.iso(moment)]
    return sorted(records, key=lambda r: (r["date"], r["id"]))


def _embedded(world: _World, records: list[dict[str, Any]], params: httpx.QueryParams) -> list[dict[str, Any]]:
    """The records with their album and artist, when the request asks for them (includeAlbum, includeArtist)."""
    albums = {album.number: _album(album, full=False) for album in world.albums}
    artists = {artist.number: _artist(artist, full=False) for artist in world.artists.values()}
    with_album, with_artist = params.get("includeAlbum") == "true", params.get("includeArtist") == "true"
    return [{**r, **({"album": albums[r["albumId"]]} if with_album else {}), **({"artist": artists[r["artistId"]]} if with_artist else {})}
            for r in records]


def _history(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    page = demo_world.page(_records(world, moment), params, "date")
    return {**page, "records": _embedded(world, page["records"], params)}


def _time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _history_since(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    since = _time(params.get("date"))
    world = _world(moment)
    records = [r for r in _records(world, moment) if since is None or r["date"] >= demo_world.iso(since)]
    return _embedded(world, records, params)


# ---------------------------------------------------------------------------
# The live state: queue, calendar, missing, cutoff, health
# ---------------------------------------------------------------------------

def _missing(world: _World, moment: datetime) -> list[_Album]:
    """What is out and has no files: the albums coming out that came out already."""
    return [a for a in world.albums if not a.file and a.released and a.released <= moment]


def _cutoff(world: _World, moment: datetime) -> list[_Album]:
    """The library's albums whose files are below the cutoff (lossless)."""
    return [a for a in world.albums if a.file and a.file[0] < CUTOFF]


def _wanted(which: Callable[[_World, datetime], list[_Album]]) -> Callable[[httpx.QueryParams, datetime], dict[str, Any]]:
    def answer(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
        found = sorted(which(_world(moment), moment), key=lambda a: (a.released or datetime.min.replace(tzinfo=UTC), a.number),
                       reverse=True)
        return demo_world.page([_album(a) for a in found], params, "albums.releaseDate", "descending")
    return answer


def _queue_album(world: _World, what: str, taken: set[int], moment: datetime) -> _Album | None:
    """The album of a download of the queue: one out already without files, or the newest below the cutoff - each
    artist once (`taken` are the artists in the queue already)."""
    if what == "coming":
        found = [a for a in _missing(world, moment) if a.entry is None]
    else:
        found = sorted((a for a in _cutoff(world, moment) if a.entry), key=lambda a: a.entry["added_at"], reverse=True)
    return next((a for a in found if a.artist.number not in taken), None)


def _queue(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    world = _world(moment)
    records: list[dict[str, Any]] = []
    taken: set[int] = set()
    for key, what, quality, indexer, minutes in QUEUE:
        album = _queue_album(world, what, taken, moment)
        if album is None:
            continue
        taken.add(album.artist.number)
        rng = demo_world.random_for(f"lidarr-queue:{key}:{album.number}")
        name, protocol = demo_world.INDEXERS[indexer]
        size, release = _size(rng, quality, album.tracks), _release(album, quality)
        if minutes:
            share = demo_world.cycle_share(moment, f"lidarr:{key}:{album.number}", minutes)
            left = timedelta(seconds=round((1 - share) * minutes * 60))
            state = {"status": "downloading", "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading",
                     "statusMessages": [], "sizeleft": int(size * (1 - share)),
                     "added": demo_world.iso(moment - timedelta(seconds=minutes * 60 * share))}
        else:
            left = timedelta(0)
            state = {"status": "completed", "trackedDownloadStatus": "warning", "trackedDownloadState": "importBlocked",
                     "statusMessages": [STUCK_MESSAGES[0], {**STUCK_MESSAGES[1], "title": release}], "sizeleft": 0,
                     "added": demo_world.iso(moment.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2))}
        download_id = f"SABnzbd_nzo_{rng.getrandbits(32):08x}" if protocol == "usenet" else f"{rng.getrandbits(160):040X}"
        records.append({"artistId": album.artist.number, "albumId": album.number, "artist": {**_artist(album.artist, full=False), "images": []},
                        "album": {**_album(album, full=False), "images": []}, "quality": _quality(quality), "customFormats": [],
                        "customFormatScore": 0, "size": size, "title": release, "timeleft": demo_world.clock(left),
                        "estimatedCompletionTime": demo_world.iso(moment + left), **state, "downloadId": download_id,
                        "protocol": protocol, "downloadClient": demo_world.DOWNLOAD_CLIENTS[protocol], "indexer": name,
                        "downloadClientHasPostImportCategory": False, "downloadForced": False, "trackFileCount": album.tracks,
                        "trackHasFileCount": 0, "id": rng.getrandbits(31)})
    for record in records:
        if params.get("includeAlbum") != "true":
            record.pop("album")
        if params.get("includeArtist") != "true":
            record.pop("artist")
    return demo_world.page(records, params, "timeleft")


def _calendar(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    """The albums with a release from `start` to `end`, the earliest first."""
    start, end = _time(params.get("start")) or moment, _time(params.get("end")) or moment + timedelta(days=7)
    found = [a for a in _world(moment).albums if a.released and start <= a.released <= end]
    return [_album(a) for a in sorted(found, key=lambda a: (a.released, a.number))]


def _health(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    return [{"source": "UpdateCheck", "type": "warning", "message": f"New update is available: {UPDATE}",
             "wikiUrl": "https://wiki.servarr.com/lidarr/system#new-update-is-available"}]


def _status(params: httpx.QueryParams, moment: datetime) -> dict[str, Any]:
    started = moment.replace(minute=0, second=0, microsecond=0) - timedelta(days=4, hours=7)
    return {"appName": NAME, "instanceName": NAME, "version": VERSION, "buildTime": "2026-10-20T10:12:31Z", "isDebug": False,
            "isProduction": True, "isAdmin": False, "isUserInteractive": False, "startupPath": "/app/lidarr/bin", "appData": "/config",
            "osName": "debian", "osVersion": "13", "isNetCore": True, "isLinux": True, "isOsx": False, "isWindows": False,
            "isDocker": True, "mode": "console", "branch": "master", "authentication": "forms", "databaseVersion": "3.46.1",
            "databaseType": "sqLite", "migrationVersion": 41, "urlBase": "", "runtimeVersion": "8.0.10", "runtimeName": ".NET",
            "startTime": demo_world.iso(started), "packageUpdateMechanism": "docker"}


def _album_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    return [_album(album) for album in sorted(_world(moment).albums, key=lambda a: a.number)]


def _artist_list(params: httpx.QueryParams, moment: datetime) -> list[dict[str, Any]]:
    world = _world(moment)
    found = []
    for artist in sorted(world.artists.values(), key=lambda a: a.number):
        own = [a for a in world.albums if a.artist is artist]
        files = sum(a.tracks for a in own if a.file)
        tracks = sum(a.tracks for a in own)
        found.append({**_artist(artist), "statistics": {
            "albumCount": len(own), "trackFileCount": files, "trackCount": tracks, "totalTrackCount": tracks,
            "sizeOnDisk": sum(a.file[1] for a in own if a.file), "percentOfTracks": round(100 * files / tracks, 1) if tracks else 0.0}})
    return found


def _command(request: httpx.Request, moment: datetime) -> dict[str, Any]:
    """A command (RefreshMonitoredDownloads) taken into the queue of commands - the made-up Lidarr does nothing with it."""
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        body = {}
    name = str(body.get("name") or "") if isinstance(body, dict) else ""
    return {"name": name, "body": {"sendUpdatesToClient": False}, "priority": "normal", "status": "queued",
            "queued": demo_world.iso(moment), "trigger": "manual", "id": int(moment.timestamp()) % 100_000}


# API path (after /api/v1/) -> its answer to a GET.
ANSWERS: dict[str, Callable[[httpx.QueryParams, datetime], Any]] = {
    "system/status": _status, "history": _history, "history/since": _history_since, "album": _album_list,
    "artist": _artist_list, "queue": _queue, "calendar": _calendar, "health": _health,
    "wanted/missing": _wanted(_missing), "wanted/cutoff": _wanted(_cutoff),
}


def handler(request: httpx.Request) -> httpx.Response:
    """The made-up Lidarr's answer to one request (httpx.MockTransport)."""
    path = request.url.path.split(API, 1)[-1].strip("/").lower()
    moment = demo_world.now()
    if request.method == "POST":
        return httpx.Response(201, json=_command(request, moment)) if path == "command" else httpx.Response(404)
    answer = ANSWERS.get(path) if request.method == "GET" else None
    if answer is None:
        return httpx.Response(404)
    return httpx.Response(200, json=answer(request.url.params, moment))
