"""Music sync: artists, albums and tracks of Jellyfin's music libraries.

Part of the music package (see jellyscope/music/__init__.py). Listening itself is not here - the
collector writes it into the playback history (`playback`, type "Audio"). This only fetches what
the music libraries hold, so that listening has an artist, an album, a format and a size.

Two modes in one task (`synchronize`):

* **incremental** - items from the newest added, read only up to the first page where everything
  is known already. A few requests every hour;
* **full pass** - when none has run yet or FULL_PASS_EVERY has passed since the last one. Goes
  through everything, fixes changed names, and what Jellyfin did not show gets `missing = 1` (the
  row stays - history and charts would lose their names). When reading ends with an error,
  nothing is marked missing.

After every run, imported history without a type whose item is a known track is marked as
listening (`item_type = 'Audio'`).

What Jellyfin returns about music (checked on 10.11 and 12.1, recorded in
tests/music_responses/): artists through `IncludeItemTypes=MusicArtist` (`/Artists` reports
`TotalRecordCount` 0 on 10.11); a track's artist need not be in the list ("A; B" in one field) -
it is added from the track; a track without an album has the album of its folder; the format is
the codec of the audio stream (the m4a container is "mov,mp4,m4a,3gp,3g2,mj2"); the MusicBrainz
id comes with tracks and albums, not with artists in the list.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db, i18n
from ..jellyfin import MUSIC_FIELDS, JellyfinClient, JellyfinError
from ..services import ServiceError, utc_time, integer, read_state, write_state

log = logging.getLogger("jellyscope.music")

NAME = "Music"                                  # a translation key - the name shown in the UI
STATE_KEY = "music_state"                        # the result of the last run (JSON, services.write_state)
FULL_PASS_KEY = "music_full_pass"            # when the last full pass ran (UTC)
GONE_LIBRARIES_KEY = "music_vanished_libraries"   # music libraries with stored music the last run did not find in Jellyfin (JSON)
PAGE_SIZE = 200                                 # items per request
FULL_PASS_EVERY = timedelta(hours=24)
TABLES_BY_TYPE = (("MusicArtist", "music_artist"), ("MusicAlbum", "music_album"), ("Audio", "music_track"))
# A sentence of its own, not services.SYNC_ALREADY_RUNNING: "Synchronizace {nazev}" would read
# "Synchronizace Hudba" here.
ALREADY_RUNNING = "Music sync is already running."

# Is a sync running? A set of at most one element: it changes without `global` and tests set it
# from outside.
_RUNNING: set[str] = set()


class MusicError(ServiceError):
    """The music sync failed (Jellyfin did not answer, or it is running already)."""


# ---------------------------------------------------------------------------
# Jellyfin items to rows
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _genres(item: dict[str, Any]) -> str:
    genres = item.get("Genres")
    return "|".join(_text(g) for g in genres if _text(g)) if isinstance(genres, list) else ""


def _first(entries: Any) -> dict[str, Any]:
    """The first {"Id", "Name"} of a list of artists, or an empty dict."""
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and _text(entry.get("Id")):
                return entry
    return {}


def _provider_id(item: dict[str, Any], key: str) -> str:
    ids = item.get("ProviderIds")
    return _text(ids.get(key)) if isinstance(ids, dict) else ""


def _image_tag(item: dict[str, Any]) -> str:
    tags = item.get("ImageTags")
    return _text(tags.get("Primary")) if isinstance(tags, dict) else ""


def _artist_row(item: dict[str, Any], now: str) -> dict[str, Any]:
    return {"id": _text(item.get("Id")), "name": _text(item.get("Name")), "genres": _genres(item),
            "musicbrainz_id": _provider_id(item, "MusicBrainzArtist"), "image_tag": _image_tag(item),
            "synced_at": now, "missing": 0}


def _album_row(item: dict[str, Any], library: str, now: str) -> dict[str, Any]:
    artist = _first(item.get("AlbumArtists"))
    return {"id": _text(item.get("Id")), "name": _text(item.get("Name")),
            "artist_id": _text(artist.get("Id")) or None,
            "artist": _text(item.get("AlbumArtist")) or _text(artist.get("Name")),
            "year": integer(item.get("ProductionYear"), None), "genres": _genres(item),
            "musicbrainz_id": _provider_id(item, "MusicBrainzAlbum"), "cover_tag": _image_tag(item),
            "added_at": utc_time(item.get("DateCreated")), "library_id": library, "synced_at": now, "missing": 0}


def _audio(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(source, audio stream) of a track; whatever is missing is an empty dict."""
    sources = item.get("MediaSources")
    source = sources[0] if isinstance(sources, list) and sources and isinstance(sources[0], dict) else {}
    streams = source.get("MediaStreams")
    stream = next((s for s in streams if isinstance(s, dict) and s.get("Type") == "Audio"), {}) if isinstance(streams, list) else {}
    return source, stream


def _track_row(item: dict[str, Any], library: str, now: str, albums: dict[str, str]) -> dict[str, Any]:
    source, stream = _audio(item)
    artist = _first(item.get("ArtistItems")) or _first(item.get("AlbumArtists"))
    artists = [_text(a.get("Name")) for a in item.get("ArtistItems") or [] if isinstance(a, dict) and _text(a.get("Name"))]
    album_id = _text(item.get("AlbumId")) or None
    return {"id": _text(item.get("Id")), "name": _text(item.get("Name")),
            # A track without an album in its tags has the album of its folder: its `Album` is empty,
            # the album has the name.
            "album_id": album_id, "album": _text(item.get("Album")) or albums.get(album_id or "", ""),
            "artist_id": _text(artist.get("Id")) or None, "artist": _text(artist.get("Name")),
            "artists": "|".join(artists), "track_number": integer(item.get("IndexNumber"), None),
            "disk": integer(item.get("ParentIndexNumber"), None), "runtime_ticks": integer(item.get("RunTimeTicks"), None),
            "year": integer(item.get("ProductionYear"), None), "genres": _genres(item),
            "container": _text(item.get("Container")), "codec": _text(stream.get("Codec")).lower(),
            "bitrate": integer(stream.get("BitRate"), None) or integer(source.get("Bitrate"), None),
            "sample_rate": integer(stream.get("SampleRate"), None), "bit_depth": integer(stream.get("BitDepth"), None),
            "channels": integer(stream.get("Channels"), None), "size_bytes": integer(source.get("Size"), None),
            # The file path puts the track on its disk (content_kinds.py); '' = Jellyfin gave none.
            "path": _text(source.get("Path")),
            "added_at": utc_time(item.get("DateCreated")), "library_id": library, "synced_at": now, "missing": 0}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _write(table: str, rows: list[dict[str, Any]]) -> None:
    """Upsert by id; an item that is back gets `missing` reset."""
    if not rows:
        return
    columns = list(rows[0])
    statement = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
                 f" ON CONFLICT (id) DO UPDATE SET {', '.join(_set_clause(table, c) for c in columns[1:])}")
    with db.connect() as conn:
        conn.executemany(statement, [tuple(r[c] for c in columns) for r in rows])
        conn.commit()


def _set_clause(table: str, column: str) -> str:
    """The SET part of the upsert. An artist's MusicBrainz id is not overwritten with an empty value:
    the list of artists does not have it (it comes from tracks), and an incremental run would erase it
    for every artist it read. Albums and tracks have their own - when somebody deletes it in Jellyfin,
    it goes here too."""
    if column == "musicbrainz_id" and table == "music_artist":
        return (f"musicbrainz_id = CASE WHEN excluded.musicbrainz_id <> '' THEN excluded.musicbrainz_id"
                f" ELSE {table}.musicbrainz_id END")
    return f"{column} = excluded.{column}"


def _gone_libraries(current: set[str]) -> int:
    """Music libraries with stored music that Jellyfin no longer has: their albums and tracks are missing.

    A library is gone only when Jellyfin leaves it out **two runs in a row**: at its start (or with a
    token without rights) it returns an empty or incomplete list of libraries, and music would vanish
    from the menu for an hour. When all of them are gone, artists are missing too (they have no
    library). Rows stay - history and charts still know the names. The record of the last full pass
    is cleared: should a library come back, the next run is a full pass (an incremental one would
    stop at the first known page). Returns the number of tracks.
    """
    stored = {str(r["library_id"]) for table in ("music_track", "music_album")
              for r in db.query_all(f"SELECT DISTINCT library_id FROM {table} WHERE missing = 0 AND library_id IS NOT NULL")}
    try:
        last_time = set(json.loads(db.get_setting(GONE_LIBRARIES_KEY, "") or "[]"))
    except (TypeError, ValueError):
        last_time = set()
    gone = stored - current
    confirmed = sorted(gone & last_time)
    db.set_setting(GONE_LIBRARIES_KEY, json.dumps(sorted(gone - set(confirmed))))
    if not confirmed:
        return 0
    placeholders = ", ".join("?" * len(confirmed))
    count = int(db.query_value(f"SELECT COUNT(*) FROM music_track WHERE missing = 0 AND library_id IN ({placeholders})",
                               tuple(confirmed)) or 0)
    with db.connect() as conn:
        for table in ("music_track", "music_album"):
            conn.execute(f"UPDATE {table} SET missing = 1 WHERE missing = 0 AND library_id IN ({placeholders})", tuple(confirmed))
        if not current:
            conn.execute("UPDATE music_artist SET missing = 1 WHERE missing = 0")
        conn.commit()
    db.set_setting(FULL_PASS_KEY, "")
    log.info("music: libraries disappeared from Jellyfin (%s), %s tracks missing", len(confirmed), count)
    return count


def _known_ids(table: str, ids: list[str]) -> set[str]:
    return {r["id"] for r in db.query_all_in(f"SELECT id FROM {table} WHERE id IN ({{}})", ids)} if ids else set()


def _collect_artists(tracks: list[dict[str, Any]], names: dict[str, str], mbids: dict[str, str]) -> None:
    """From a page of tracks, collect artist names (also of artists missing from the list) and MusicBrainz ids.

    Called page by page: the raw Jellyfin answers (about 8 KB per track) are not kept until the end
    of the run - for a big library a full pass would take hundreds of MB. Only these two small dicts
    stay.
    """
    for track in tracks:
        album_artist = _first(track.get("AlbumArtists"))
        for artist in [*(track.get("ArtistItems") or []), *(track.get("AlbumArtists") or [])]:
            if isinstance(artist, dict) and _text(artist.get("Id")):
                names.setdefault(_text(artist["Id"]), _text(artist.get("Name")))
        if _text(album_artist.get("Id")) and _provider_id(track, "MusicBrainzAlbumArtist"):
            mbids.setdefault(_text(album_artist["Id"]), _provider_id(track, "MusicBrainzAlbumArtist"))
        only = [a for a in track.get("ArtistItems") or [] if isinstance(a, dict) and _text(a.get("Id"))]
        if len(only) == 1 and _provider_id(track, "MusicBrainzArtist"):
            mbids.setdefault(_text(only[0]["Id"]), _provider_id(track, "MusicBrainzArtist"))


def _add_artists(names: dict[str, str], mbids: dict[str, str], now: str) -> None:
    """An artist found only on a track (not in the list of artists) gets a row; MusicBrainz ids from tracks."""
    unknown = sorted(set(names) - _known_ids("music_artist", sorted(names)))
    with db.connect() as conn:
        conn.executemany("INSERT INTO music_artist (id, name, synced_at, missing) VALUES (?, ?, ?, 0)"
                         " ON CONFLICT (id) DO NOTHING", [(i, names[i], now) for i in unknown])
        # An artist this run showed only on a track is in Jellyfin.
        conn.executemany("UPDATE music_artist SET synced_at = ?, missing = 0 WHERE id = ?", [(now, i) for i in names])
        conn.executemany("UPDATE music_artist SET musicbrainz_id = ? WHERE id = ? AND musicbrainz_id = ''",
                         [(m, i) for i, m in mbids.items()])
        conn.commit()


# ---------------------------------------------------------------------------
# Reading from Jellyfin
# ---------------------------------------------------------------------------

async def _walk(client: Any, item_type: str, table: str, library: str, full: bool, now: str,
                albums: dict[str, str], artists: tuple[dict[str, str], dict[str, str]]) -> tuple[int, int]:
    """Walk one item type of one library page by page. Returns (read, new).

    `artists` = (names, MusicBrainz ids) of artists from tracks, filled page by page (_collect_artists).
    """
    read = new = 0
    start = 0
    while True:
        page = await client.items_page(start, PAGE_SIZE, item_type, None, library, sort_by="DateCreated,SortName",
                                       sort_order="Descending", fields=MUSIC_FIELDS)
        items = [i for i in page.get("Items") or [] if isinstance(i, dict) and _text(i.get("Id"))]
        if not items:
            return read, new
        # The database in a thread: the task runs in the server's main loop, a big library means
        # hundreds of writes.
        known = await asyncio.to_thread(_known_ids, table, [_text(i["Id"]) for i in items])
        if item_type == "MusicArtist":
            rows = [_artist_row(i, now) for i in items]
        elif item_type == "MusicAlbum":
            rows = [_album_row(i, library, now) for i in items]
            albums.update({r["id"]: r["name"] for r in rows})
        else:
            rows = [_track_row(i, library, now, albums) for i in items]
            _collect_artists(items, *artists)
        await asyncio.to_thread(_write, table, rows)
        read += len(items)
        new += len(items) - len(known)
        start += len(items)
        total = integer(page.get("TotalRecordCount"), None)
        # An incremental run ends on the first page where everything was known already.
        if (not full and len(known) == len(items)) or (total is not None and start >= total):
            return read, new


def _full_pass_due() -> bool:
    # Tracks synced before the path was kept (16. 11. 2026) have none, and an incremental sync
    # stops at the first known page: a full one runs while a present track lacks it.
    if db.query_value("SELECT 1 FROM music_track WHERE missing = 0 AND path IS NULL LIMIT 1", default=None):
        return True
    last = db.get_setting(FULL_PASS_KEY, "")
    if not last:
        return True
    try:
        when = datetime.strptime(last, db.TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return True
    return datetime.now(UTC) - when >= FULL_PASS_EVERY


def _mark_missing(run: str) -> int:
    """After a full pass: what Jellyfin did not show gets `missing = 1`. Returns the number of tracks.

    What this run saw has `synced_at` equal to `run` (the run's start time with microseconds -
    two runs in one second do not mix).
    """
    before = int(db.query_value("SELECT COUNT(*) FROM music_track WHERE missing = 1") or 0)
    with db.connect() as conn:
        for _, table in TABLES_BY_TYPE:
            conn.execute(f"UPDATE {table} SET missing = 1 WHERE synced_at IS NULL OR synced_at <> ?", (run,))
        conn.commit()
    return int(db.query_value("SELECT COUNT(*) FROM music_track WHERE missing = 1") or 0) - before


# Ids of imported history (Playback Reporting, Jellystat) with dashes and capitals that match a known
# track once normalised. The import normalises ids only against items (importers/linking.py), where
# music is not.
_NORMALISED_ID = "LOWER(REPLACE(item_id, '-', ''))"
_ID_WITH_DASHES = f"item_id LIKE '%-%' AND {_NORMALISED_ID} IN (SELECT id FROM music_track)"


def _pair_history() -> int:
    """Imported history without a type whose item is a known track is listening. Returns the number of rows.

    First it normalises track ids written with dashes to the form music has - otherwise the listening
    would not pair with its track (no artist, no album). It changes only rows that would not pair.
    """
    with db.connect() as conn:
        conn.execute(f"UPDATE playback SET item_id = {_NORMALISED_ID} WHERE {_ID_WITH_DASHES}")
        conn.commit()
    where = "(item_type IS NULL OR item_type = '') AND item_id IN (SELECT id FROM music_track)"
    count = int(db.query_value(f"SELECT COUNT(*) FROM playback WHERE {where}") or 0)
    if count:
        with db.connect() as conn:
            conn.execute(f"UPDATE playback SET item_type = 'Audio' WHERE {where}")
            conn.commit()
    return count


async def _fetch(full: bool | None) -> dict[str, Any]:
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        libraries = [lib for lib in await client.virtual_folders() if isinstance(lib, dict) and lib.get("CollectionType") == "music"]
        gone = await asyncio.to_thread(_gone_libraries, {_text(lib.get("ItemId")) for lib in libraries})
        if not libraries:
            return {"library_count": 0, "missing": gone} if gone else {"library_count": 0}
        # After the gone libraries: when one is gone, the record of the full pass is cleared and this
        # run is a full one. In a thread: it looks through the tracks for one without a path.
        full = await asyncio.to_thread(_full_pass_due) if full is None else full
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        albums = {r["id"]: r["name"] for r in db.query_all("SELECT id, name FROM music_album")}
        artists: tuple[dict[str, str], dict[str, str]] = ({}, {})
        # The keys of the result are shown in Settings > Tasks and stored with the task log.
        result: dict[str, Any] = {"library_count": len(libraries), "full": full, "artist_count": 0, "album_count": 0, "track_count": 0, "new_count": 0}
        for library in libraries:
            for item_type, table in TABLES_BY_TYPE:
                read, new = await _walk(client, item_type, table, _text(library.get("ItemId")), full, now, albums, artists)
                result[{"MusicArtist": "artist_count", "MusicAlbum": "album_count", "Audio": "track_count"}[item_type]] += read
                result["new_count"] += new
    # Work over whole tables (and the whole history) in a thread: the task runs in the server's main
    # loop and the web and the collector would stand still meanwhile.
    await asyncio.to_thread(_add_artists, *artists, now)
    result["missing"] = gone
    if full:
        result["missing"] += await asyncio.to_thread(_mark_missing, now)
        db.set_setting(FULL_PASS_KEY, now[:19])
    result["paired"] = await asyncio.to_thread(_pair_history)
    return result


async def synchronize(full: bool | None = None) -> dict[str, Any]:
    """Fetch the artists, albums and tracks of Jellyfin's music libraries.

    `full` = force (True) or forbid (False) a full pass; without it the time since the last one
    decides. Without a music library it returns {"library_count": 0} (and `missing` when that confirmed a
    library is gone - see _gone_libraries). A Jellyfin error is written into the state and raises
    MusicError.
    """
    if NAME in _RUNNING:
        raise MusicError(ALREADY_RUNNING)
    _RUNNING.add(NAME)
    try:
        result = await _fetch(full)
    except JellyfinError as error:
        log.warning("music sync failed: %s", error)
        write_state(STATE_KEY, "error", 0, error)
        raise MusicError("{reason}", reason=i18n.error_text(error)) from error
    finally:
        _RUNNING.discard(NAME)
    # The state even without a music library: the administrator sees in Settings > Tasks that the
    # run happened (and why music vanished).
    write_state(STATE_KEY, "ok", int(result.get("new_count", 0)))
    if result.get("library_count"):
        log.info("music: %s new items (artists %s, albums %s, tracks %s)",
                 result["new_count"], result["artist_count"], result["album_count"], result["track_count"])
    return result


def sync_running() -> bool:
    return NAME in _RUNNING


def sync_state() -> dict[str, Any]:
    """The state of the last run (when, result, how many came, error) and the time of the last full pass."""
    return {**read_state(STATE_KEY), "full": db.get_setting(FULL_PASS_KEY, "")}


def has_music() -> bool:
    """Has Jellyscope anything to show on the Music page? At least one track that is in Jellyfin.

    A music library alone is not enough (the user, 16. 11. 2026): an empty library would give a tab
    with nothing in it. The menu entry, the cards of the dashboards and the field in Settings follow
    this condition too.
    """
    return bool(db.query_value("SELECT 1 FROM music_track WHERE missing = 0 LIMIT 1"))
