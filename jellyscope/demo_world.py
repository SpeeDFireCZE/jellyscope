"""The demo's made-up world: ids in the shape the real services give them.

The demo shows the real code at work, pairing included. That code compares Jellyfin's ids without
dashes (`jellyfin.id_without_dashes`) and takes a TMDB or TVDB id only when it is a number
(`jellyfin.digits_only`), so readable ids like "demo-movie-3" or "demo-tmdb-3" would pair with nothing.
The demo's data (`demodata`) keep working with readable keys ("demo-u1", "demo-movie-3",
"demo-series-0") and write what these functions make of them: always the same for one key - a refresh
or another machine gives the same ids, so links into the demo hold - and another for another key.

The made-up apps (*Arr, Seerr, the Jellyfin their syncs pair with) answer from the demo's library: Sonarr
its series, Radarr its movies, Lidarr its albums, Readarr its books, Seerr requests of its viewers. The
catalogs below read them from the library's shared tables (items, music_album, music_track, book, users),
every title with the number an app knows it by (Sonarr's seriesId, Radarr's movie id, ...): its readable
key's place in the demo's lists from 1 (demodata: Duna = movie 1, Chernobyl = series 1) - the same at every
seed, whatever order the rows were written in and whatever else the library holds. A row of no readable
key (one a test wrote) is in no catalog. `now()` is the made-up world's one clock: the made-up apps call it
as `demo_world.now()` (tests substitute it, the live parts then move).

What the made-up apps share is here too: the indexers their releases come from (Prowlarr's list), the download
clients, a random source of its own for every key (`random_for`: the same history at every seed), the share of a
repeating download (`cycle_share`: the live queue moves with the clock, in a cycle), the shape of an *Arr app's
paged list and queue time (`page`, `clock`), the flaky indexer's outages (`flaky_outages`, `flaky_down`: Prowlarr
leaves it out then, Sonarr's and Radarr's health warn about it), the TV schedule of the series airing now (`airings`,
`aired`): the library has their latest episodes, Sonarr grabbed them after they aired and has the next ones in its
calendar - and
the movies' releases (`movie_release`, `coming_movies`): the library has the movies of the last weeks, Radarr waited
for them and grabbed them after they came out, and knows the movies coming out, which the library does not have yet -
and the albums' releases (`album_release`, `coming_albums`) the same way for Lidarr, the books' (`book_release`,
`coming_books`) for Readarr. When the library's newest file came (`newest_arrival`) anchors what a made-up app keeps
from one seed to the next; while the demo's data are made again (`REFRESHING`) an app asked on its own keeps quiet.

Shared by everything of the demo (the data, the made-up apps), so it names no app and no app's table.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from . import db

# What every id of the demo is made from, before the key: the ids of the demo meet no other ones.
_PREFIX = "jellyscope-demo:"
# The numbers of TMDB and TVDB: seven or eight digits, so the demo's keys do not meet one another
# (test_demo_world checks it for its keys). Real ids are shorter, nothing reads their length.
_LOWEST = 1_000_000
_SPAN = 99_000_000


def _digest(key: str) -> str:
    return hashlib.sha256((_PREFIX + key).encode("utf-8")).hexdigest()


def jellyfin_id(key: str) -> str:
    """The Jellyfin id of a key: 32 lower-case hex characters, as Jellyfin's API sends them (no dashes)."""
    return _digest(key)[:32]


def _number(kind: str, key: str) -> str:
    return str(_LOWEST + int(_digest(f"{kind}:{key}")[:15], 16) % _SPAN)


def tmdb_id(key: str) -> str:
    """The TMDB id of a key: digits only, without a leading zero."""
    return _number("tmdb", key)


def tvdb_id(key: str) -> str:
    """The TVDB id of a key: digits only, without a leading zero; made apart from its TMDB id."""
    return _number("tvdb", key)


def imdb_id(key: str) -> str:
    """The IMDb id of a key: "tt" and seven or eight digits, as the apps send it; made apart from its TMDB id."""
    return "tt" + _number("imdb", key)


def goodreads_id(key: str) -> str:
    """The Goodreads id of a key (Readarr's ids of a book, an edition, an author): digits only, made apart from its others."""
    return _number("goodreads", key)


def isbn13(key: str) -> str:
    """An ISBN-13 of a key: 978-80 (a Czech publisher), seven digits and the check digit, as a book's edition carries it."""
    body = "97880" + str(int(_digest("isbn:" + key)[:15], 16) % 10_000_000).zfill(7)
    total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(body))
    return body + str((10 - total % 10) % 10)


def musicbrainz_id(key: str) -> str:
    """The MusicBrainz id of a key: a UUID in lower case (version 4), as Lidarr sends an artist's, an album's, a release's."""
    digest = _digest("musicbrainz:" + key)
    return f"{digest[:8]}-{digest[8:12]}-4{digest[13:16]}-{'89ab'[int(digest[16], 16) % 4]}{digest[17:20]}-{digest[20:32]}"


def now() -> datetime:
    """The made-up world's moment (UTC): the one clock of every made-up app. Tests substitute it."""
    return datetime.now(UTC)


# Set while the demo's data are made again (demodata.refresh: the apps' data and the library deleted, then seeded): a
# made-up app that something keeps asking on its own then stays quiet - Prowlarr does not tell the indexers' status, so
# the background loop measuring it writes nothing into tables being deleted and filled.
REFRESHING = threading.Event()


def newest_arrival() -> datetime | None:
    """When the library's newest file came (UTC): of its items, albums and books. None while it is empty (a refresh
    deletes and fills it). A made-up app anchors to it what has to hold from one seed to the next - its own history
    keeps its times and numbers while the clock moves on."""
    found = [_moment(db.query_value(sql, default="")) for sql in (
        "SELECT MAX(date_created) FROM items", "SELECT MAX(added_at) FROM music_album", "SELECT MAX(added_at) FROM book")]
    return max((moment for moment in found if moment is not None), default=None)


# ---------------------------------------------------------------------------
# What the made-up apps share
# ---------------------------------------------------------------------------

# The made-up indexers with their protocol, in Prowlarr's order (its ids from 1). Sonarr's and Radarr's releases
# come from them. FLAKY_INDEXER drops out now and then (the schedule of outages below: Prowlarr leaves it out, Sonarr's
# and Radarr's health warn about it meanwhile; their failed downloads come from it), DISABLED_INDEXER is switched off:
# nothing comes from it.
INDEXERS = (("Hvezdarna", "usenet"), ("Sklepeni", "torrent"), ("Kometa", "usenet"), ("Pavlac", "torrent"),
            ("Mlynek", "torrent"))
FLAKY_INDEXER = "Kometa"
DISABLED_INDEXER = "Mlynek"
# The made-up download clients by protocol: the name the apps know each by.
DOWNLOAD_CLIENTS = {"usenet": "SABnzbd", "torrent": "qBittorrent"}


def random_for(key: str) -> random.Random:
    """A random source of its own for `key`: the same numbers for the same key at every seed and in every process.

    A made-up app draws each title's history from it ("sonarr:10101"), so one title's numbers do not depend on
    what was drawn before it (another library, a deleted title) and the seed's own `random` is left alone.
    """
    return random.Random(int(_digest("random:" + key)[:16], 16))


def cycle_share(moment: datetime, key: str, minutes: float) -> float:
    """How far (0-1) a repeating made-up process of `minutes` is at `moment`: ((moment - its start) / length) mod 1.

    The live downloads of the made-up apps: each starts again when done, at its own phase (from `key`), so the
    queue always has something moving and the same moment gives the same share.
    """
    length = minutes * 60
    phase = int(_digest("cycle:" + key)[:8], 16) % int(length)
    return ((moment.timestamp() + phase) % length) / length


def iso(moment: datetime) -> str:
    """A moment as the *Arr apps write it: "2026-11-26T08:15:00Z" (UTC)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def weighted(rng: random.Random, choices: Iterable[tuple[Any, float]]) -> Any:
    """One of the (value, weight) pairs' values, picked by its weight."""
    values, weights = zip(*choices)
    return rng.choices(values, weights=weights)[0]


def span(rng: random.Random, ranges: Iterable[tuple[float, float, float]]) -> float:
    """A number from one of the ranges (from, to, weight), the range picked by its weight."""
    low, high = weighted(rng, ((pair[:2], pair[2]) for pair in ranges))
    return rng.uniform(low, high)


def page(records: list[dict[str, Any]], params: Mapping[str, str], sort_key: str, direction: str = "ascending") -> dict[str, Any]:
    """One page of an *Arr app's paged list (`page` from 1, `pageSize`, `sortDirection`); `records` come in `direction`."""
    def whole(value: str | None, fallback: int) -> int:
        try:
            return max(1, int(value or ""))
        except ValueError:
            return fallback

    number, size = whole(params.get("page"), 1), whole(params.get("pageSize"), 10)
    asked = params.get("sortDirection") or direction
    if asked != direction:
        records = records[::-1]
    return {"page": number, "pageSize": size, "sortKey": params.get("sortKey") or sort_key, "sortDirection": asked,
            "totalRecords": len(records), "records": records[(number - 1) * size:number * size]}


def clock(left: timedelta) -> str:
    """The time left in an *Arr app's queue: "01:02:23", with days "1.02:03:04"."""
    seconds = int(left.total_seconds())
    days, seconds = divmod(seconds, 86_400)
    text = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{days}.{text}" if days else text


# ---------------------------------------------------------------------------
# The flaky indexer's outages
# ---------------------------------------------------------------------------

# FLAKY_INDEXER drops out once in every OUTAGE_EVERY seconds from OUTAGE_EPOCH (UTC), for OUTAGE_SECONDS at a place
# of its own in that stretch: absolute time, the same at every seed and in every made-up app. An outage runs from the
# first failed query to the one that worked again - Prowlarr leaves the indexer out then, Sonarr and Radarr warn
# about it in their health.
OUTAGE_EPOCH = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp())
OUTAGE_EVERY, OUTAGE_SECONDS = 48 * 3600, (20 * 60, 40 * 60)


def _outage(block: int) -> tuple[int, int]:
    rng = random_for(f"flaky-indexer:outage:{block}")
    start = OUTAGE_EPOCH + block * OUTAGE_EVERY + rng.randint(3600, OUTAGE_EVERY - 3 * 3600)
    return start, start + rng.randint(*OUTAGE_SECONDS)


def flaky_outages(start: int, end: int) -> list[tuple[int, int]]:
    """FLAKY_INDEXER's outages (epoch seconds: from, until) that start in [start, end) or are still running at start."""
    first, last = (start - OUTAGE_EPOCH) // OUTAGE_EVERY, (end - OUTAGE_EPOCH) // OUTAGE_EVERY
    found = [_outage(block) for block in range(first, last + 1)]
    return [(a, b) for a, b in found if b > start and a < end]


def flaky_outage(seconds: int) -> tuple[int, int] | None:
    """FLAKY_INDEXER's outage running at the moment (epoch seconds), None while it works."""
    start, end = _outage((seconds - OUTAGE_EPOCH) // OUTAGE_EVERY)
    return (start, end) if start <= seconds < end else None


def flaky_down(moment: datetime) -> bool:
    """Whether FLAKY_INDEXER is out at `moment` (a time without a zone is UTC)."""
    return flaky_outage(int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp())) is not None


# ---------------------------------------------------------------------------
# The made-up world's TV schedule
# ---------------------------------------------------------------------------

# The series airing now: one episode a week at their slot, seasons of SEASON_LENGTH episodes back to back from
# AIRING_EPOCH (a Monday). {series key: (day of the week from Monday, hour UTC, the season airing at the epoch, its
# episodes aired by then)}. The library has their episodes of the last weeks (demodata), the made-up Sonarr grabbed
# them after they aired and has the next ones in its calendar. Each airs on another day and reaches the library the
# same day (demodata.AIRING_LIBRARY), so a week brings each one once - "the last 7 days" lack at most one of them.
AIRING_EPOCH = datetime(2026, 1, 5, tzinfo=UTC)
SEASON_LENGTH = 12
WEEK = timedelta(days=7)
AIRING = {"demo-series-1": (2, 2, 2, 0), "demo-series-2": (3, 1, 4, 5), "demo-series-4": (4, 1, 30, 9),
          "demo-series-5": (0, 18, 1, 6)}


def _first_airing(number: int) -> tuple[datetime, int, int] | None:
    """(the series' first airing on the schedule, its season, the episodes of it aired before), None off the air."""
    slot = AIRING.get(f"demo-series-{number - 1}")        # the catalog's number = the key's index + 1
    if slot is None:
        return None
    day, hour, season, before = slot
    return AIRING_EPOCH + timedelta(days=day, hours=hour), season, before


def airings(number: int, start: datetime, end: datetime) -> list[tuple[int, int, datetime]]:
    """When series `number` (the catalog's) airs from `start` to `end`: [(season, episode, moment)], oldest first."""
    first = _first_airing(number)
    if first is None:
        return []
    moment, season, before = first
    week = max(0, math.ceil((start - moment) / WEEK))
    found = []
    while (when := moment + week * WEEK) <= end:
        found.append((season + (before + week) // SEASON_LENGTH, (before + week) % SEASON_LENGTH + 1, when))
        week += 1
    return found


def aired(number: int, season: int, episode: int) -> datetime | None:
    """When an episode of series `number` aired (or airs) on the schedule; None off it (an older episode, a series off the air)."""
    first = _first_airing(number)
    if first is None or not 1 <= episode <= SEASON_LENGTH:
        return None
    moment, first_season, before = first
    week = (season - first_season) * SEASON_LENGTH + episode - 1 - before
    return moment + week * WEEK if week >= 0 else None


# ---------------------------------------------------------------------------
# The made-up world's movie releases
# ---------------------------------------------------------------------------

def movie_release(movie: Mapping[str, Any]) -> datetime | None:
    """When a movie of the last weeks (demodata.NEW_MOVIES; an entry of `movies()`) came out digitally, at midnight UTC.

    It reached the library its hours later (its `added_at`), so the release is that minus the hours; the made-up Radarr
    grabbed and imported it in between. None for the library's other movies (they came out years before).
    """
    from . import demodata    # here, not above: demodata imports this module

    for key, (_title, _days, hours) in demodata.NEW_MOVIES.items():
        if movie.get("jellyfin_id") == jellyfin_id(key) and movie.get("added_at"):
            return movie["added_at"] - timedelta(hours=hours)
    return None


def coming_movies(moment: datetime) -> list[dict[str, Any]]:
    """The movies the library does not have yet (demodata.COMING_MOVIES) as at `moment`, in their order.

    key, title, tmdb_id, imdb_id, year and `released` - the digital release at midnight UTC, that many days from the
    moment's day (before it for a movie out already). The made-up Radarr knows them: its calendar, missing and queue.
    """
    from . import demodata    # here, not above: demodata imports this module

    today = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    found = []
    for key, title, days in demodata.COMING_MOVIES:
        released = today + timedelta(days=days)
        found.append({"key": key, "title": title, "tmdb_id": tmdb_id(key), "imdb_id": imdb_id(key), "year": released.year,
                      "released": released})
    return found


# ---------------------------------------------------------------------------
# The made-up world's album releases
# ---------------------------------------------------------------------------

def _new_album_key(album: Mapping[str, Any]) -> str | None:
    """The key of an album of the last weeks (demodata.NEW_ALBUMS; an entry of `albums()`), None for another one."""
    from . import demodata    # here, not above: demodata imports this module

    return next((key for key in demodata.NEW_ALBUMS if album.get("jellyfin_id") == jellyfin_id(key)), None)


def album_release(album: Mapping[str, Any]) -> datetime | None:
    """When an album of the last weeks (demodata.NEW_ALBUMS; an entry of `albums()`) came out, at midnight UTC.

    It reached the library its hours later (its `added_at`), so the release is that minus the hours; the made-up Lidarr
    grabbed and imported it in between. None for the library's other albums (they came out years before).
    """
    from . import demodata    # here, not above: demodata imports this module

    key = _new_album_key(album)
    if key is None or not album.get("added_at"):
        return None
    return album["added_at"] - timedelta(hours=demodata.NEW_ALBUMS[key][-1])


def album_upgrade(album: Mapping[str, Any]) -> timedelta | None:
    """How long after its first import the made-up Lidarr took the better version of an album (demodata.UPGRADED_ALBUM).

    None for every other album: their upgrades, if any, are the made-up app's own chance.
    """
    from . import demodata    # here, not above: demodata imports this module

    key, hours = demodata.UPGRADED_ALBUM
    return timedelta(hours=hours) if _new_album_key(album) == key else None


def coming_albums(moment: datetime) -> list[dict[str, Any]]:
    """The albums the library does not have yet (demodata.COMING_ALBUMS) as at `moment`, in their order.

    key, title, artist, artist_number (the place in demodata.MUSIC_ARTISTS from 1), tracks, year and `released` - at
    midnight UTC, that many days from the moment's day (before it for an album out already). The made-up Lidarr knows
    them: its calendar, missing and queue.
    """
    from . import demodata    # here, not above: demodata imports this module

    today = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    artists = {key: (number, name) for number, (key, name) in enumerate(demodata.MUSIC_ARTISTS, start=1)}
    found = []
    for key, title, artist, tracks, days in demodata.COMING_ALBUMS:
        released = today + timedelta(days=days)
        number, name = artists[artist]
        found.append({"key": key, "title": title, "artist": name, "artist_number": number, "tracks": tracks,
                      "year": released.year, "released": released})
    return found


# ---------------------------------------------------------------------------
# The made-up world's book releases
# ---------------------------------------------------------------------------

def _book_rows(key: str, parts: int) -> list[str]:
    """The readable keys of a book's rows in the library: an audiobook in several files has one per part ("demo-ab-1-1")."""
    return [f"{key}-{part}" for part in range(1, parts + 1)] if parts > 1 else [key]


def _new_book_key(book: Mapping[str, Any]) -> str | None:
    """The key of a book of the last weeks (demodata.NEW_BOOKS; an entry of `books()`), None for another one."""
    from . import demodata    # here, not above: demodata imports this module

    return next((key for key, value in demodata.NEW_BOOKS.items()
                 if book.get("jellyfin_id") in {jellyfin_id(row) for row in _book_rows(key, value[6])}), None)


def book_release(book: Mapping[str, Any]) -> datetime | None:
    """When a book of the last weeks (demodata.NEW_BOOKS; an entry of `books()`) came out, at midnight UTC.

    It reached the library its hours later (its `added_at`), so the release is that minus the hours; the made-up Readarr
    grabbed and imported it in between. None for the library's other books (they came out years before).
    """
    from . import demodata    # here, not above: demodata imports this module

    key = _new_book_key(book)
    if key is None or not book.get("added_at"):
        return None
    return book["added_at"] - timedelta(hours=demodata.NEW_BOOKS[key][-1])


def book_upgrade(book: Mapping[str, Any]) -> timedelta | None:
    """How long after its first import the made-up Readarr took the better edition of a book (demodata.UPGRADED_BOOK).

    None for every other book: their upgrades, if any, are the made-up app's own chance.
    """
    from . import demodata    # here, not above: demodata imports this module

    key, hours = demodata.UPGRADED_BOOK
    return timedelta(hours=hours) if _new_book_key(book) == key else None


def coming_books(moment: datetime) -> list[dict[str, Any]]:
    """The books the library does not have yet (demodata.COMING_BOOKS) as at `moment`, in their order.

    key, title, author, audiobook, year and `released` - at midnight UTC, that many days from the moment's day (before it
    for a book out already). The made-up Readarr knows them: its calendar, missing and queue.
    """
    from . import demodata    # here, not above: demodata imports this module

    today = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    found = []
    for key, title, author, audiobook, days in demodata.COMING_BOOKS:
        released = today + timedelta(days=days)
        found.append({"key": key, "title": title, "author": author, "audiobook": audiobook, "year": released.year,
                      "released": released})
    return found


# ---------------------------------------------------------------------------
# The catalogs of the demo's library
# ---------------------------------------------------------------------------

def _keys() -> dict[str, dict[str, tuple[int, str]]]:
    """The readable keys of the demo's library by kind: {kind: {Jellyfin id of a row: (number, key)}}.

    The number is the key's place in demodata's lists from 1. The keys are made the way demodata.seed
    makes them (tests/test_demo_jellyfin.py checks that every row of the library is in its catalog).
    """
    from . import demodata    # here, not above: demodata imports this module

    movies = [f"demo-movie-{index}" for index in range(len(demodata.MOVIES))]
    movies += [demodata.LANGUAGE_IN_NAME, "demo-movie-dup-1080p", "demo-movie-dup-2160p", *demodata.NEW_MOVIES]
    kinds = {
        "movies": movies,
        "series": [f"demo-series-{index}" for index in range(len(demodata.SERIES) + len(demodata.NEW_SERIES))],
        "artists": [key for key, _name in demodata.MUSIC_ARTISTS],
        "albums": [album[0] for album in demodata.MUSIC_ALBUMS] + list(demodata.NEW_ALBUMS),
        "viewers": [key for key, _name, _admin in demodata.USERS],
    }
    found = {kind: {jellyfin_id(key): (number, key) for number, key in enumerate(keys, start=1)}
             for kind, keys in kinds.items()}
    # A book in several files (an audiobook in parts) has a row for each part: "demo-ab-1-1", "demo-ab-1-2"...
    books = [(book[0], book[-2]) for book in demodata.BOOKS] + [(key, value[6]) for key, value in demodata.NEW_BOOKS.items()]
    found["books"] = {jellyfin_id(row): (number, key) for number, (key, parts) in enumerate(books, start=1)
                      for row in _book_rows(key, parts)}
    return found


def _moment(value: Any) -> datetime | None:
    """A stored time (Jellyfin's "2026-11-02T10:00:00.0000000Z" or the database's "2026-11-02 10:00:00") in UTC."""
    try:
        return datetime.strptime(str(value or "")[:19].replace("T", " "), db.TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _episode_number(series_number: int, season: int, episode: int) -> int:
    """An episode's number across all series (Sonarr's episodeId): 10101 = series 1, season 1, episode 1."""
    return series_number * 10_000 + season * 100 + episode


def series() -> list[dict[str, Any]]:
    """The demo's series, by number: number, title, jellyfin_id, tmdb_id, tvdb_id, year, added_at, seasons.

    The TV library has episodes only (as Jellyscope stores them): a series is what its episodes share - the
    series' id and name, the TMDB id (Jellyfin gives an episode its series' one), the earliest year and the
    earliest arrival. The TVDB id is made from the key (no table holds one). `seasons` = {season number:
    [episode, ...]} by episode number, an episode {number, season, episode, title, jellyfin_id, added_at}.
    """
    known = _keys()["series"]
    found: dict[str, dict[str, Any]] = {}
    for row in db.query_all(
            "SELECT id, name, series_id, series_name, parent_index_number, index_number, production_year, tmdb_id,"
            " date_created FROM items WHERE type = 'Episode' AND series_id IS NOT NULL"):
        if row["series_id"] not in known:
            continue
        number, key = known[row["series_id"]]
        entry = found.setdefault(row["series_id"], {
            "number": number, "title": row["series_name"] or "", "jellyfin_id": row["series_id"],
            "tmdb_id": str(row["tmdb_id"] or ""), "tvdb_id": tvdb_id(key), "year": None, "added_at": None, "seasons": {}})
        season, episode, added = int(row["parent_index_number"] or 0), int(row["index_number"] or 0), _moment(row["date_created"])
        entry["seasons"].setdefault(season, []).append({
            "number": _episode_number(number, season, episode), "season": season, "episode": episode,
            "title": row["name"] or "", "jellyfin_id": row["id"], "added_at": added})
        if row["production_year"] and (entry["year"] is None or row["production_year"] < entry["year"]):
            entry["year"] = row["production_year"]
        if added and (entry["added_at"] is None or added < entry["added_at"]):
            entry["added_at"] = added
    for entry in found.values():
        entry["seasons"] = {season: sorted(episodes, key=lambda e: e["episode"])
                            for season, episodes in sorted(entry["seasons"].items())}
    return sorted(found.values(), key=lambda e: e["number"])


def movies() -> list[dict[str, Any]]:
    """The demo's movies, by number: number, title, jellyfin_id, tmdb_id, year, added_at, missing (in the archive),
    height and size_bytes (its file: the made-up Radarr's quality and size follow them)."""
    known = _keys()["movies"]
    found = [{"number": known[row["id"]][0], "title": row["name"] or "", "jellyfin_id": row["id"],
              "tmdb_id": str(row["tmdb_id"] or ""), "year": row["production_year"], "added_at": _moment(row["date_created"]),
              "missing": bool(row["is_missing"]), "height": int(row["height"] or 0), "size_bytes": int(row["size_bytes"] or 0)}
             for row in db.query_all("SELECT id, name, production_year, tmdb_id, date_created, is_missing, height, size_bytes"
                                     " FROM items WHERE type = 'Movie'")
             if row["id"] in known]
    return sorted(found, key=lambda e: e["number"])


def albums() -> list[dict[str, Any]]:
    """The demo's albums, by number: number, artist, title, jellyfin_id, year, added_at, tracks, artist_number,
    artist_jellyfin_id, genres (a list), and what its files are (size_bytes, codec, bitrate, bit_depth: the made-up
    Lidarr's quality and size follow them)."""
    keys = _keys()
    known, artists = keys["albums"], keys["artists"]
    found = [{"number": known[row["id"]][0], "artist": row["artist"] or "", "title": row["name"] or "",
              "jellyfin_id": row["id"], "year": row["year"], "added_at": _moment(row["added_at"]), "tracks": int(row["tracks"] or 0),
              "artist_number": artists.get(row["artist_id"], (0, ""))[0], "artist_jellyfin_id": row["artist_id"] or "",
              "genres": [g for g in str(row["genres"] or "").split("|") if g], "size_bytes": int(row["size_bytes"] or 0),
              "codec": row["codec"] or "", "bitrate": int(row["bitrate"] or 0), "bit_depth": int(row["bit_depth"] or 0)}
             for row in db.query_all(
                 "SELECT a.id, a.name, a.artist_id, a.artist, a.year, a.added_at, a.genres, COUNT(t.id) AS tracks,"
                 " SUM(t.size_bytes) AS size_bytes, MAX(t.codec) AS codec, MAX(t.bitrate) AS bitrate, MAX(t.bit_depth) AS bit_depth"
                 " FROM music_album a LEFT JOIN music_track t ON t.album_id = a.id"
                 " GROUP BY a.id, a.name, a.artist_id, a.artist, a.year, a.added_at, a.genres")
             if row["id"] in known]
    return sorted(found, key=lambda e: e["number"])


def books() -> list[dict[str, Any]]:
    """The demo's books, by number: number, author, title, jellyfin_id, audiobook, added_at, parts, and what its files
    are (format, size_bytes over its parts, isbn - empty when they carry none), year, series and genres (a list).

    An audiobook in several files is one book: its `jellyfin_id` is that of its first part by id, the one the
    pairing with the library takes (Readarr: the first row of a title by id).
    """
    known = _keys()["books"]
    found: dict[int, dict[str, Any]] = {}
    for row in db.query_all("SELECT id, kind, title, author, added_at, format, size_bytes, isbn, year, series, genres FROM book"):
        if row["id"] not in known:
            continue
        number = known[row["id"]][0]
        entry = found.setdefault(number, {
            "number": number, "author": row["author"] or "", "title": row["title"] or "", "jellyfin_id": row["id"],
            "audiobook": row["kind"] == "audiobook", "added_at": _moment(row["added_at"]), "parts": 0,
            "format": row["format"] or "", "size_bytes": 0, "isbn": row["isbn"] or "", "year": row["year"],
            "series": row["series"] or "", "genres": [g for g in str(row["genres"] or "").split("|") if g]})
        entry["parts"] += 1
        entry["size_bytes"] += int(row["size_bytes"] or 0)
        entry["jellyfin_id"] = min(entry["jellyfin_id"], row["id"])
    return [found[number] for number in sorted(found)]


def viewers() -> list[dict[str, Any]]:
    """The demo's viewers (Jellyfin's users), in the demo's order: number, jellyfin_id, name, is_admin."""
    known = _keys()["viewers"]
    found = [{"number": known[row["id"]][0], "jellyfin_id": row["id"], "name": row["name"] or "",
              "is_admin": bool(row["is_administrator"])}
             for row in db.query_all("SELECT id, name, is_administrator FROM users") if row["id"] in known]
    return sorted(found, key=lambda e: e["number"])
