"""Lidarr's albums and their counterparts in the music library.

The history Jellyscope fetches from Lidarr knows an album only by its id in Lidarr and by its name.
So that the cards can link an album into the library, the sync also fetches Lidarr's list of albums
and artists - with the MusicBrainz ids the albums and artists are known by (the release group, the
releases, the artist).

Pairing is with the music library Jellyscope already has (`music_album`, `music_artist`), so no
request to Jellyfin is made here:

* an album pairs by a **MusicBrainz release id** - any of the releases Lidarr knows for the album
  may be the one the library's album carries (`music_album.musicbrainz_id`, Jellyfin's release id);
* an album without a shared id pairs by its **name and its artist's name**, case and diacritics
  do not matter (the library has the names the files are tagged with). The name alone is not
  enough: two artists have an album called "Greatest Hits";
* an artist pairs by the MusicBrainz artist id, else by its name.

An album or an artist that is not in the library (or went missing from it) has an empty
counterpart: its card shows the name without a link. Pairing is recomputed on every sync - a local
query, and an album that left the library is not to keep a link that leads nowhere.

Lidarr cannot page the list of albums (a big library has tens of MB of it), so it is not fetched
every time: only the first time, when the history brought an album Jellyscope does not know yet,
and otherwise once a day (`REFRESH`).

None of this is a condition of the history sync: when the lists or the pairing fail, the history is
fetched anyway and the links are filled in next time.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from ... import db
from ...services import utc_time, integer
from .client import NAME, SETTINGS_PREFIX, LidarrClient, LidarrError

log = logging.getLogger("jellyscope.arr")

COLUMNS = ("title_id", "name", "artist", "artist_id", "mb_release_group", "mb_releases", "mb_artist", "track_count",
           "released_at", "added_at", "synced_at")
# `jellyfin_id` and `jellyfin_artist_id` are left out of the upsert on purpose: only the pairing
# with the library writes them, so a list from the app does not delete a pairing already done.
UPSERT = (f"INSERT INTO lidarr_title ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
          " ON CONFLICT (title_id) DO UPDATE SET " + ", ".join(f"{s} = excluded.{s}" for s in COLUMNS[1:]))
# A MusicBrainz id is a UUID. Anything else from the app is not stored (it would never pair anyway).
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# How often the list of albums is refreshed even when the history brought nothing new: because of
# corrected ids and albums that have no history yet.
REFRESH = timedelta(days=1)
# The version of what is stored from the list of albums. A list fetched by an older version is fetched
# again once at once, not after a day.
LIST_VERSION = "2"      # 2: the time the artist came into Lidarr (`added_at`)
# A release date before this year is no date: .NET sends year 1 for an empty one.
OLDEST_YEAR = 1000
# The MusicBrainz ids of an album's releases are joined by this in one column.
SEPARATOR = "|"

# The settings key with the time of the last fetched list of albums (UTC); the list's version is
# under MARK_KEY + "_version".
MARK_KEY = f"{SETTINGS_PREFIX}_titles"


def _needed(seen: set[int]) -> bool:
    """Is the list of albums to be fetched? The first time, after a day, after an upgrade, or when the history brought an unknown album.

    `seen` are the album ids from the history just fetched. Only they are asked about, not the whole
    history: an album Lidarr no longer has would be missing from the list for ever and the list would
    be fetched on every sync because of it.
    """
    if (db.get_setting(MARK_KEY + "_version", "") or "") != LIST_VERSION:
        return True
    last = db.get_setting(MARK_KEY, "") or ""
    try:
        age = datetime.strptime(db.utcnow(), db.TIME_FORMAT) - datetime.strptime(last, db.TIME_FORMAT)
    except ValueError:
        return True                 # never yet (or a mark that is no time)
    if age >= REFRESH or age < timedelta(0):
        return True
    known = {r["title_id"] for r in db.query_all_in(
        "SELECT title_id FROM lidarr_title WHERE title_id IN ({})", sorted(seen))}
    return bool(seen - known)


def _folded(text: Any) -> str:
    """A name for comparing: case and diacritics do not matter ("Kryštof" = "KRYSTOF")."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _mbid(value: Any) -> str:
    """A MusicBrainz id in lowercase, else empty."""
    text = str(value if value is not None else "").strip().lower()
    return text if _MBID.fullmatch(text) else ""


def _release_date(value: Any) -> str | None:
    released = utc_time(value)
    # The year is read from the number before the first dash: `strftime` does not pad years under 1000 with zeros.
    if released and not OLDEST_YEAR <= int(released.split("-", 1)[0]) <= 9000:
        return None
    return released


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def title_rows(records: Any, artists: Any = None) -> list[dict[str, Any]]:
    """Lidarr's list of albums as rows of the table. A broken record is skipped.

    `artists` is Lidarr's list of artists: it gives the albums the artist's name and MusicBrainz id
    (the artist embedded in an album is the fallback). Another app's answer is input like any other:
    a record without a numeric id is not used and an id that does not look like a MusicBrainz id is
    stored empty. Only a few fields are taken - the rest is let go at once.
    """
    by_artist = {integer(a.get("id"), None): a for a in artists if isinstance(a, dict)} if isinstance(artists, list) else {}
    rows = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        album_id = integer(record.get("id"), None)
        if album_id is None:
            continue
        artist_id = integer(record.get("artistId"), None)
        artist = _dict(by_artist.get(artist_id)) or _dict(record.get("artist"))
        releases = record.get("releases")
        release_ids = [_mbid(r.get("foreignReleaseId")) for r in releases if isinstance(r, dict)] if isinstance(releases, list) else []
        added = _release_date(artist.get("added"))        # an empty .NET date (year 1) is no time here either
        rows.append({"title_id": album_id, "name": str(record.get("title") or ""), "artist": str(artist.get("artistName") or ""),
                     "artist_id": artist_id, "mb_release_group": _mbid(record.get("foreignAlbumId")),
                     "mb_releases": SEPARATOR.join(dict.fromkeys(i for i in release_ids if i)),
                     "mb_artist": _mbid(artist.get("foreignArtistId")),
                     "track_count": integer(_dict(record.get("statistics")).get("trackCount")),
                     "released_at": _release_date(record.get("releaseDate")), "added_at": added})
    return rows


def _store(rows: list[dict[str, Any]]) -> None:
    """Upsert the albums. An album Lidarr no longer has stays in the table - the history still points at it."""
    now = db.utcnow()
    with db.connect() as conn:
        conn.executemany(UPSERT, [tuple({**r, "synced_at": now}[s] for s in COLUMNS) for r in rows])
        conn.commit()


def _pair_albums() -> int:
    """Give every Lidarr album its album and artist in the music library (or empty). Returns the count of paired albums.

    It walks every stored album, those Lidarr no longer has too: an album that left the library
    loses its pairing - the link would lead nowhere. A library album that went missing is not a
    counterpart. Where several library albums fit, the first by id holds (the choice is the same on
    every run).
    """
    by_mbid: dict[str, str] = {}
    by_name: dict[tuple[str, str], str] = {}
    for r in db.query_all("SELECT id, name, artist, musicbrainz_id FROM music_album WHERE missing = 0 ORDER BY id"):
        if _mbid(r["musicbrainz_id"]):
            by_mbid.setdefault(_mbid(r["musicbrainz_id"]), r["id"])
        if r["name"] and r["artist"]:
            by_name.setdefault((_folded(r["name"]), _folded(r["artist"])), r["id"])
    artists_by_mbid: dict[str, str] = {}
    artists_by_name: dict[str, str] = {}
    for r in db.query_all("SELECT id, name, musicbrainz_id FROM music_artist WHERE missing = 0 ORDER BY id"):
        if _mbid(r["musicbrainz_id"]):
            artists_by_mbid.setdefault(_mbid(r["musicbrainz_id"]), r["id"])
        if r["name"]:
            artists_by_name.setdefault(_folded(r["name"]), r["id"])
    changes, paired = [], 0
    for r in db.query_all("SELECT title_id, name, artist, mb_releases, mb_artist, jellyfin_id, jellyfin_artist_id FROM lidarr_title"):
        album = next((by_mbid[i] for i in str(r["mb_releases"]).split(SEPARATOR) if i in by_mbid), "")
        if not album and r["name"] and r["artist"]:
            album = by_name.get((_folded(r["name"]), _folded(r["artist"])), "")
        artist = artists_by_mbid.get(r["mb_artist"], "") or (artists_by_name.get(_folded(r["artist"]), "") if r["artist"] else "")
        paired += bool(album)
        if (album, artist) != (r["jellyfin_id"] or "", r["jellyfin_artist_id"] or ""):
            changes.append((album, artist, r["title_id"]))
    if changes:
        with db.connect() as conn:
            conn.executemany("UPDATE lidarr_title SET jellyfin_id = ?, jellyfin_artist_id = ? WHERE title_id = ?", changes)
            conn.commit()
    return paired


async def _fetch_list(client: LidarrClient) -> int | None:
    """Fetch and store Lidarr's list of albums. Returns their count, None = it failed.

    Lidarr's error deletes nothing and does not move the fetch mark: the next sync tries again. The
    list of artists only names the albums: without it (it failed) the albums are stored with the
    names embedded in them.
    """
    try:
        records = await client.titles()
    except LidarrError as error:
        log.info("could not download the title list of %s, will retry next time: %s", NAME, error)
        return None
    try:
        artists = await client.artists()
    except LidarrError as error:
        log.info("could not download the artist list of %s, the albums keep the names they came with: %s", NAME, error)
        artists = []
    # Conversion and writing in a thread, so the web runs meanwhile (as with the history).
    rows = await asyncio.to_thread(title_rows, records, artists)
    del records
    await asyncio.to_thread(_store, rows)
    db.set_setting(MARK_KEY, db.utcnow())
    db.set_setting(MARK_KEY + "_version", LIST_VERSION)
    return len(rows)


async def sync_titles(client: LidarrClient, seen: Iterable[Any] = ()) -> int:
    """Refresh Lidarr's list of albums (if needed) and pair them with the music library.

    Called by the history sync with its open client; `seen` are the album ids from the history just
    fetched (see `_needed`). Returns the count of fetched albums (0 = the list was not fetched this
    time or it failed). Neither Lidarr's error nor the library's brings the history sync down: the
    links are extra, the numbers do not depend on them.
    """
    fetched = 0
    if await asyncio.to_thread(_needed, {i for i in seen if isinstance(i, int)}):
        fetched = await _fetch_list(client) or 0
    paired = await asyncio.to_thread(_pair_albums)
    log.debug("%s titles: %s albums matched to the music library", NAME, paired)
    return fetched


def delete_titles() -> None:
    """Delete Lidarr's stored albums (together with the downloaded history)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM lidarr_title")
        conn.commit()
    db.set_setting(MARK_KEY, "")
    db.set_setting(MARK_KEY + "_version", "")
