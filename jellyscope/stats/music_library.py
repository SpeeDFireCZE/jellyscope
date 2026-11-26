"""Statistics of the music library and the data of the artist, album and viewer pages.

Part of the statistics package (see jellyscope/stats/__init__.py), the second half of the music statistics (the
first is stats/music_listening.py - listening). jellyscope/music/sync.py fills the library. What disappeared from
Jellyfin (`missing = 1`) does not count into the library's numbers; the artist and album pages show it with a flag, so
that links from the history do not lead into emptiness.
"""
from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any

from .. import db
from .kinds import only_music
from .music_listening import PLAY_FROM_SECONDS, TICKS, music_first, music_analysis
from .periods import _as_period, whole_history_period

# Codecs of lossless sound (as Jellyfin / ffprobe report them). PCM and DSD are told by the start.
LOSSLESS = frozenset({"flac", "alac", "ape", "wavpack", "tta", "mlp", "truehd"})
# The formats' names as people know them. What is not here shows as the codec in capitals.
FORMATS = {"flac": "FLAC", "alac": "ALAC", "mp3": "MP3", "aac": "AAC", "opus": "Opus", "vorbis": "Vorbis",
           "wavpack": "WavPack", "ape": "APE", "wmav2": "WMA", "wmapro": "WMA", "ac3": "AC-3", "eac3": "E-AC-3"}
HIRES_BITS = 16           # more bits per sample = hi-res
HIRES_HZ = 48000          # more samples per second = hi-res


def _lossless(codec: Any) -> bool:
    codec = str(codec or "").lower()
    return codec in LOSSLESS or codec.startswith(("pcm_", "dsd"))


def codec_format(codec: Any) -> str:
    """The format's name by the audio stream's codec; unknown = ""."""
    codec = str(codec or "").lower()
    if codec.startswith("pcm_"):
        return "WAV/PCM"
    if codec.startswith("dsd"):
        return "DSD"
    return FORMATS.get(codec, codec.upper())


def library_tracks(library_id: str | None = None) -> list[dict[str, Any]]:
    """The library's tracks (without the vanished ones) for the library's summaries.

    The four library cards (numbers, formats, quality, genres) each compute something else from them; the page
    reads them once and hands them to all (`tracks=`). With `library_id` only the tracks of that Jellyfin library.
    """
    where, params = ("", ()) if library_id is None else (" AND library_id = ?", (library_id,))
    return db.query_all("SELECT id, album_id, artist_id, codec, bitrate, sample_rate, bit_depth, size_bytes, runtime_ticks, genres"
                        " FROM music_track WHERE missing = 0" + where, params)


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------

def music_library_summary(tracks: list[dict[str, Any]] | None = None, library_id: str | None = None) -> dict[str, int]:
    """Artists, albums, tracks, size (bytes) and the total length (seconds) - without what vanished.

    With `library_id` only that Jellyfin library: its albums, and the artists of its tracks and
    albums (an artist has no library of its own) - see library_artist_count.
    """
    tracks = library_tracks(library_id) if tracks is None else tracks
    if library_id is not None:
        artist_count = library_artist_count(tracks, library_id)
        album_count = db.query_value("SELECT COUNT(*) FROM music_album WHERE missing = 0 AND library_id = ?", (library_id,))
    else:
        artist_count = db.query_value("SELECT COUNT(*) FROM music_artist WHERE missing = 0")
        album_count = db.query_value("SELECT COUNT(*) FROM music_album WHERE missing = 0")
    return {"artist_count": int(artist_count or 0),
            "album_count": int(album_count or 0),
            "track_count": len(tracks),
            "size_bytes": sum(int(s["size_bytes"] or 0) for s in tracks),
            "seconds": sum(int(s["runtime_ticks"] or 0) for s in tracks) // TICKS}


def music_formats(tracks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Formats by codec: tracks, share (%), size. An unknown format ("") at the end."""
    tracks = library_tracks() if tracks is None else tracks
    counts: Counter = Counter()
    sizes: Counter = Counter()
    for s in tracks:
        f = codec_format(s["codec"])
        counts[f] += 1
        sizes[f] += int(s["size_bytes"] or 0)
    return [{"format": f, "track_count": n, "share": n / len(tracks) * 100, "size_bytes": sizes[f]}
            for f, n in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0] == "", pair[0].lower()))]


def music_quality(tracks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Lossless (of them hi-res), lossy by bitrate bands (below 128, 128-255, from 256 kb/s), unknown."""
    result: dict[str, Any] = {"lossless": 0, "hires": 0, "lossy": {"up_to_128": 0, "up_to_256": 0, "from_256": 0}, "unknown": 0}
    for s in library_tracks() if tracks is None else tracks:
        if _lossless(s["codec"]):
            result["lossless"] += 1
            if int(s["bit_depth"] or 0) > HIRES_BITS or int(s["sample_rate"] or 0) > HIRES_HZ:
                result["hires"] += 1
        elif not s["codec"] or not s["bitrate"]:
            result["unknown"] += 1
        else:
            kbits = int(s["bitrate"]) / 1000
            result["lossy"]["up_to_128" if kbits < 128 else "up_to_256" if kbits < 256 else "from_256"] += 1
    return result


def library_genres(tracks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Genres by the count of tracks (a track with more genres counts for each)."""
    counts: Counter = Counter()
    for s in library_tracks() if tracks is None else tracks:
        for genre in {z.strip() for z in str(s["genres"] or "").split("|") if z.strip()}:
            counts[genre] += 1
    return [{"genre": z, "track_count": n} for z, n in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))]


def music_decades(library_id: str | None = None) -> list[dict[str, Any]]:
    """Albums by the decade of release; albums without a year at the end (`decades` None). `library_id`: one library only."""
    counts: Counter = Counter()
    where, params = ("", ()) if library_id is None else (" AND library_id = ?", (library_id,))
    for a in db.query_all("SELECT year FROM music_album WHERE missing = 0" + where, params):
        year = a["year"]
        counts[year // 10 * 10 if isinstance(year, int) and year > 0 else None] += 1
    return [{"decades": d, "album_count": n} for d, n in sorted(counts.items(), key=lambda pair: (pair[0] is None, pair[0] or 0))]


# An album's track count and size: its tracks that are in Jellyfin (alias s, joined or filtered with
# s.chybi = 0). One definition for the music cards (_ALBUMS_SQL) and the album grid (_album_totals).
_ALBUM_TOTALS = "COUNT(s.id) AS track_count, COALESCE(SUM(s.size_bytes), 0) AS size_bytes"
_ALBUM_COLUMNS = f"a.id, a.name, a.artist_id, a.artist, a.year, a.cover_tag, a.added_at, {_ALBUM_TOTALS}"
_ALBUMS_SQL = (f"SELECT {_ALBUM_COLUMNS} FROM music_album a LEFT JOIN music_track s ON s.album_id = a.id AND s.missing = 0"
               " WHERE a.missing = 0 {where}"
               " GROUP BY a.id, a.name, a.artist_id, a.artist, a.year, a.cover_tag, a.added_at")


def _albums(where: str = "", order_by: str = "a.name", limit: int = 100, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Albums with the count of tracks and the size; `where` and `order_by` are pieces of SQL from this file."""
    # Finally a.id: albums of the same name with the same date or size swap places on PostgreSQL otherwise.
    return [dict(r) for r in db.query_all(_ALBUMS_SQL.format(where=where) + f" ORDER BY {order_by}, a.id LIMIT ?", (*params, limit))]


def music_newest(limit: int = 20) -> list[dict[str, Any]]:
    """The albums added last (from the newest)."""
    return _albums(order_by="a.added_at IS NULL, a.added_at DESC, a.name", limit=limit)


def music_largest(limit: int = 100, library_id: str | None = None) -> list[dict[str, Any]]:
    """The largest albums on the disk; `library_id`: of one library only (its tab in the Library)."""
    where, params = ("", ()) if library_id is None else (" AND a.library_id = ?", (library_id,))
    return _albums(where=where, order_by="size_bytes DESC, a.name", limit=limit, params=params)


def music_unplayed(limit: int = 100) -> list[dict[str, Any]]:
    """Albums without a single play in the whole history - the longest in the library first.

    The whole history, not the period: in a short period the card would list almost the whole library. A play is
    as everywhere (at least PLAY_FROM_SECONDS seconds or half the track); a skipped track does not count.
    """
    where_sql = (" AND NOT EXISTS (SELECT 1 FROM playback p JOIN music_track ps ON ps.id = p.item_id"
                 f" WHERE ps.album_id = a.id{only_music('p.')}"
                 f" AND (p.watched_seconds >= {PLAY_FROM_SECONDS}"
           # NULLIF: a track's zero length (RunTimeTicks 0) means "we do not know" - as in Python (music_analysis).
                 f" OR (COALESCE(NULLIF(ps.runtime_ticks, 0), p.media_runtime_ticks, 0) > 0"
                 f" AND p.watched_seconds * 2 * {TICKS} >= COALESCE(NULLIF(ps.runtime_ticks, 0), p.media_runtime_ticks, 0))))")
    return _albums(where=where_sql, order_by="a.added_at IS NULL, a.added_at, a.name", limit=limit)


# ---------------------------------------------------------------------------
# Artist, album, viewer
# ---------------------------------------------------------------------------

def _numbers(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"seconds": sum(x["watched_seconds"] for x in rows), "plays": sum(1 for x in rows if x["plays"]),
            "listener_count": len({x["user_id"] or x["user_name"] for x in rows})}


def _listeners(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for x in rows:
        person = people.setdefault(x["user_id"] or "name:" + str(x["user_name"]),
                                   {"user_id": x["user_id"], "user_name": x["user_name"], "seconds": 0, "plays": 0})
        person["seconds"] += x["watched_seconds"]
        person["plays"] += int(x["plays"])
    return sorted(people.values(), key=lambda c: (-c["seconds"], str(c["user_name"] or "").lower()))


def _album_listening(albums: list[str], days: Any) -> dict[str, int]:
    """The whole listening of albums in the period (seconds by album id) - of all their tracks, whoever recorded them."""
    period = _as_period(days)
    return {str(r["album_id"]): int(r["seconds"] or 0) for r in db.query_all_in(
        "SELECT s.album_id, SUM(p.watched_seconds) AS seconds FROM playback p JOIN music_track s ON s.id = p.item_id"
        f" WHERE p.started_at >= ? AND p.started_at < ? AND p.watched_seconds > 0{only_music('p.')}"
        " AND s.album_id IN ({}) GROUP BY s.album_id", albums, (period.since, period.until))}


def music_artist(artist_id: str, days: Any) -> dict[str, Any] | None:
    """The artist page: the artist's row, the period's numbers, albums, the most listened tracks, listeners."""
    artist = db.query_one("SELECT * FROM music_artist WHERE id = ?", (artist_id,))
    if artist is None:
        return None
    # The numbers, tracks and listeners are only the artist's tracks (the same thing as in the artists' ranking);
    # the albums show their whole listening (_album_listening) - on a compilation ("Various artists", "B feat. A")
    # someone else recorded tracks too and the album would show only a part on the artist page otherwise.
    rows = music_analysis(days, artist_id=artist_id)["rows"]
    tracks_by_id: dict[str, dict[str, Any]] = {}
    for x in rows:
        s = tracks_by_id.setdefault(x["item_id"], {"id": x["item_id"], "name": x["track"] or x["item_name"] or "",
                                                   "album_id": x["album_id"], "album": x["album"] or "", "seconds": 0, "plays": 0})
        s["seconds"] += x["watched_seconds"]
        s["plays"] += int(x["plays"])
    albums = _albums(where=" AND (a.artist_id = ? OR a.id IN (SELECT album_id FROM music_track WHERE artist_id = ?))",
                     order_by="a.year IS NULL, a.year DESC, a.name", limit=1000, params=(artist_id, artist_id))
    listening = _album_listening([a["id"] for a in albums], days)
    for a in albums:
        a["seconds"] = listening.get(a["id"], 0)
    return {"artist": dict(artist), "numbers": _numbers(rows), "albums": albums,
            "tracks": sorted(tracks_by_id.values(), key=lambda s: (-s["seconds"], s["name"].lower())),
            "listeners": _listeners(rows)}


def music_viewer(user_id: str, days: Any) -> dict[str, Any]:
    """One viewer's listening in the period: seconds, plays, the most listened artist."""
    rows = music_analysis(days, user_id=user_id)["rows"]
    artists: Counter = Counter()
    names: dict[str, str] = {}
    for x in rows:
        if x["artist_id"]:
            artists[x["artist_id"]] += x["watched_seconds"]
            names[x["artist_id"]] = x["artist"] or ""
    top = sorted(artists.items(), key=lambda pair: (-pair[1], names.get(pair[0], "")))
    return {"seconds": sum(x["watched_seconds"] for x in rows), "plays": sum(1 for x in rows if x["plays"]),
            "artist_id": top[0][0] if top else None, "artist": names.get(top[0][0], "") if top else ""}


# ---------------------------------------------------------------------------
# The music library in the Library (16. 11. 2026): the album grid and the album page.
# ---------------------------------------------------------------------------
ALBUM_SORTS = ("added", "name", "artist", "year", "size")
ALBUMS_PER_PAGE = 60


def _folded(text: Any) -> str:
    """Text for searching and sorting: case and diacritics do not matter ("Okrajové" = "okrajove")."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _totals_by_album(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {str(r["album_id"]): {"track_count": int(r["track_count"]), "size_bytes": int(r["size_bytes"] or 0)} for r in rows}


def _album_totals(album_ids: list[str]) -> dict[str, dict[str, int]]:
    """Track count and size of the given albums (one page of the grid)."""
    return _totals_by_album(db.query_all_in(
        f"SELECT s.album_id, {_ALBUM_TOTALS} FROM music_track s WHERE s.missing = 0 AND s.album_id IN ({{}})"
        " GROUP BY s.album_id", album_ids))


def _library_album_totals(library_id: str) -> dict[str, dict[str, int]]:
    """Track count and size of every present album of one library, in one query (sorting by size)."""
    return _totals_by_album(db.query_all(
        f"SELECT s.album_id, {_ALBUM_TOTALS} FROM music_track s JOIN music_album a ON a.id = s.album_id"
        " WHERE s.missing = 0 AND a.missing = 0 AND a.library_id = ? GROUP BY s.album_id", (library_id,)))


def library_albums(library_id: str, search: str = "", sort: str = "added", page: int = 1,
                   per_page: int = ALBUMS_PER_PAGE) -> dict[str, Any]:
    """Albums of one music library for its grid: search, sort, one page.

    Search looks into the album's name and its artist; case and diacritics do not matter and
    `%` or `_` are plain characters - the filtering is done here, not in SQL, so SQLite and
    PostgreSQL agree. Albums that disappeared from Jellyfin are not in the grid (their page
    stays). Sort: added (newest first), name, artist, year (newest first), size (largest
    first); anything else is "added". Returns {"albums", "total", "page", "pages"}.

    Only the albums' own rows are read for the whole library; track counts and sizes are
    summed for the albums of the page (for the whole library in one query when sorting by size).
    """
    albums = [dict(r) for r in db.query_all(
        "SELECT id, name, artist_id, artist, year, cover_tag, added_at FROM music_album"
        " WHERE missing = 0 AND library_id = ?", (library_id,))]
    needle = _folded(search.strip())
    if needle:
        albums = [a for a in albums if needle in _folded(a["name"]) or needle in _folded(a["artist"])]
    # By name first (stable sorts below keep it for equal keys), the id last - a fixed order everywhere.
    albums.sort(key=lambda a: (_folded(a["name"]), a["id"]))
    totals: dict[str, dict[str, int]] = {}
    if sort == "artist":
        albums.sort(key=lambda a: _folded(a["artist"]))
    elif sort == "year":
        albums.sort(key=lambda a: (a["year"] is not None, a["year"] or 0), reverse=True)
    elif sort == "size":
        totals = _library_album_totals(library_id)
        albums.sort(key=lambda a: totals.get(a["id"], {}).get("size_bytes", 0), reverse=True)
    elif sort != "name":
        albums.sort(key=lambda a: str(a["added_at"] or ""), reverse=True)       # unknown date last
    total = len(albums)
    pages = max(1, -(-total // per_page))
    page = min(max(1, int(page or 1)), pages)
    on_page = albums[(page - 1) * per_page:page * per_page]
    if sort != "size":
        totals = _album_totals([a["id"] for a in on_page])
    for album in on_page:
        album.update(totals.get(album["id"], {"track_count": 0, "size_bytes": 0}))
    return {"albums": on_page, "total": total, "page": page, "pages": pages}


def album_detail(album_id: str) -> dict[str, Any] | None:
    """The album page: the album, its tracks by disc and its listening over the whole history.

    Every track of the album is listed - one that vanished from Jellyfin with its flag - but
    only present tracks count into size and length (as everywhere in the music library).
    A play is what it is everywhere (at least PLAY_FROM_SECONDS seconds or half of the track).
    Returns None for an unknown id; a vanished album still has its page.
    """
    album = db.query_one("SELECT a.*, l.name AS library_name FROM music_album a"
                         " LEFT JOIN libraries l ON l.id = a.library_id WHERE a.id = ?", (album_id,))
    if album is None:
        return None
    rows = music_analysis(whole_history_period(first=music_first()), album_id=album_id)["rows"]
    listening: dict[str, dict[str, int]] = {}
    for row in rows:
        per_track = listening.setdefault(row["item_id"], {"seconds": 0, "plays": 0})
        per_track["seconds"] += row["watched_seconds"]
        per_track["plays"] += int(row["plays"])
    tracks = []
    for row in db.query_all("SELECT id, name, artist_id, artist, track_number, disk, runtime_ticks, codec, bitrate, sample_rate,"
                            " bit_depth, size_bytes, missing FROM music_track WHERE album_id = ?", (album_id,)):
        track = {**dict(row), "format": codec_format(row["codec"]), **listening.get(row["id"], {"seconds": 0, "plays": 0})}
        tracks.append(track)
    tracks.sort(key=lambda t: (t["disk"] or 1, t["track_number"] or 0, _folded(t["name"]), t["id"]))
    discs: dict[int, list[dict[str, Any]]] = {}
    for track in tracks:
        discs.setdefault(track["disk"] or 1, []).append(track)
    present = [t for t in tracks if not t["missing"]]
    return {"album": dict(album), "tracks": tracks,
            "discs": [{"number": number, "tracks": items} for number, items in sorted(discs.items())],
            "size": sum(int(t["size_bytes"] or 0) for t in present),
            "seconds": sum(int(t["runtime_ticks"] or 0) for t in present) // TICKS,
            "plays": sum(t["plays"] for t in tracks),
            "listeners": _listeners(rows),
            "formats": sorted({t["format"] for t in present if t["format"]})}


# ---------------------------------------------------------------------------
# A music library in the Library (16. 11. 2026): its Overview and Activity tabs, and the
# Music section of the Library page (every music library).
# ---------------------------------------------------------------------------
RECENT_LISTENS = 25
TOP_ALBUMS = 10


LIBRARY_CARDS = ("music_formats", "music_quality", "music_library_genres", "music_years", "music_largest")


def library_artist_count(tracks: list[dict[str, Any]], library_id: str | None) -> int:
    """Artists of a music library: of its present tracks and of its present albums (album artist).

    An artist has no library of its own, so the same rule serves one library and (`library_id`
    None) every music library together - the Library page and a library's Overview agree.
    """
    where, params = ("", ()) if library_id is None else (" AND library_id = ?", (library_id,))
    album_artists = {r["artist_id"] for r in db.query_all(
        "SELECT DISTINCT artist_id FROM music_album WHERE missing = 0 AND artist_id IS NOT NULL" + where, params)}
    return len({t["artist_id"] for t in tracks if t["artist_id"]} | album_artists)


def library_overview(library_id: str | None = None, cards: tuple[str, ...] = LIBRARY_CARDS) -> dict[str, Any]:
    """What a music library holds now (no period): numbers and the data of the asked library cards.

    The keys of the cards are those of the music card registry (cards_music.py), so the page
    draws the same cards as the Music page - here over one library, or every music library
    when `library_id` is None (the Music section of the Library page, which asks for two).
    """
    tracks = library_tracks(library_id)
    numbers = music_library_summary(tracks, library_id)
    numbers["artist_count"] = library_artist_count(tracks, library_id)
    builders = {"music_formats": lambda: {"rows": music_formats(tracks)},
                "music_quality": lambda: music_quality(tracks),
                "music_library_genres": lambda: {"rows": library_genres(tracks)},
                "music_years": lambda: {"rows": music_decades(library_id)},
                "music_largest": lambda: {"rows": music_largest(library_id=library_id)}}
    return {"numbers": numbers, **{key: builders[key]() for key in cards}}


def library_listening(library_id: str, days: Any) -> dict[str, Any]:
    """The Activity tab of a music library: listening of the period, and the last listens.

    `totals` (listening time, plays, listeners, albums), the most played albums and the
    listeners (both as `label`/`hours` for a bar chart; a listener row keeps `user_id`, so
    anonymisation knows the label is a name), and the last RECENT_LISTENS listens whatever the
    period. A play is what it is everywhere in music (at least PLAY_FROM_SECONDS seconds or half of
    the track).
    """
    rows = music_analysis(days, library_id=library_id)["rows"]
    albums: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row["album_id"]:
            continue
        album = albums.setdefault(row["album_id"], {"label": row["album"] or "?", "seconds": 0})
        album["seconds"] += row["watched_seconds"]
    top_albums = sorted(albums.values(), key=lambda a: (-a["seconds"], _folded(a["label"])))[:TOP_ALBUMS]
    recent = db.query_all(
        "SELECT p.started_at, p.user_id, COALESCE(u.name, p.user_name) AS user_name, p.item_id, p.play_method,"
        " p.watched_seconds, s.name AS track, s.artist, s.album_id"
        " FROM playback p JOIN music_track s ON s.id = p.item_id LEFT JOIN users u ON u.id = p.user_id"
        f" WHERE s.library_id = ? AND p.watched_seconds > 0{only_music('p.')}"
        " ORDER BY p.started_at DESC, p.id DESC LIMIT ?", (library_id, RECENT_LISTENS))
    return {"totals": {**_numbers(rows), "album_count": len(albums)},
            "top_albums": [{"label": a["label"], "hours": a["seconds"] / 3600} for a in top_albums],
            "listeners": [{"label": p["user_name"] or "?", "user_id": p["user_id"], "hours": p["seconds"] / 3600}
                          for p in _listeners(rows)],
            "recent": recent}
