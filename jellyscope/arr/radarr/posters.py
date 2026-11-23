"""Movie posters from Radarr: provisional ones for the card "Stahuje se" and for Seerr's requests.

The card "Stahuje se" (downloading) shows movies with a poster. A movie from the library has one
from Jellyfin (the /image/ route, see stats.radarr_library). A movie still downloading is not in
the library - Radarr itself gives it a poster: it keeps the poster of each of its movies
(`mediacover`). Jellyscope never connects anywhere outside for a poster.

The same holds as for Seerr's requests (seerr/posters.py): **one poster per title** in the image
store (images.py, a file by the kind and the TMDB id - the one Seerr would use for that movie), only
a real image is stored and "none" is remembered for five minutes. A poster can be asked for only for a
movie Jellyscope knows from Radarr (the list of movies, or the queue just read) - only the movie's
number is taken from the request's address.

Once the movie arrives in the library the card switches to the poster from Jellyfin and the next sync
deletes the provisional file (`clean_up_posters`).

`title_poster` is for Seerr's requests: the movie's poster by its TMDB id, without storing it (Seerr
takes care of the store).
"""
from __future__ import annotations

import asyncio
import logging
import time

from ... import db, images, stats
from ...jellyfin import digits_only
from ...services import ServiceError
from . import progress
from .client import SETTINGS_PREFIX, RadarrClient
from .settings import is_connected

log = logging.getLogger("jellyscope.arr")

# How long the note "this movie has no poster" holds (seconds).
MISSING_SECONDS = 300
# The kind of title in the image store (images.TITLE_KINDS).
MEDIA_TYPE = "movie"
# {movie id: when its poster was last not found - time.monotonic()}. Only in memory.
_MISSING: dict[int, float] = {}
MAX_REMEMBERED = 5000


def _connection() -> tuple[str, str]:
    """Radarr's address and key from the settings."""
    return db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")


def _tmdb_id(movie_id: int) -> str:
    """The movie's TMDB id: from Radarr's list of movies, else from the queue just read. "" = unknown movie."""
    row = db.query_one("SELECT tmdb_id FROM radarr_title WHERE title_id = ?", (movie_id,))
    tmdb = str((row or {}).get("tmdb_id") or "") or progress.fresh_tmdb(movie_id)
    return tmdb if digits_only(tmdb) else ""


def poster_missing(movie_id: int | None) -> bool:
    """Is it known that the movie has no poster (Radarr did not serve it a moment ago)?

    For the card "Stahuje se": its body changes every five seconds and with every swap the browser
    would ask for the missing poster again - and the placeholder would flash in place of the poster
    every time. While the note holds, the card draws the placeholder right away; then it offers the
    poster again and it is tried once more.
    """
    return time.monotonic() - _MISSING.get(movie_id, float("-inf")) < MISSING_SECONDS


async def provisional_poster(movie_id: int) -> tuple[bytes, str] | None:
    """The movie's provisional poster with the image type. None = there is none.

    From the disk, else from Radarr - and what is found is stored. Only what really is an image by its
    content is stored (an app behind a proxy can send a login page instead of a poster). An unknown
    movie, a movie without a TMDB id and a Radarr that is not connected end without a request
    outside.
    """
    # The database and the disk in a thread: the poster route runs in the server's loop and the loop
    # would wait for them as a whole.
    local = await asyncio.to_thread(_from_disk, movie_id)
    if local is None:
        return None
    tmdb, stored, url, key = local
    if stored:
        return stored
    if poster_missing(movie_id):
        return None
    try:
        async with RadarrClient(url, key) as client:
            content = await client.poster(movie_id)
    except ServiceError as error:
        log.debug("the poster of title %s could not be downloaded from %s: %s", movie_id, "radarr", error)
        content = None
    stored = await asyncio.to_thread(_store, tmdb, content) if content else None
    if not stored:
        if len(_MISSING) > MAX_REMEMBERED:          # a safeguard: the memory does not grow for ever
            _MISSING.clear()
        _MISSING[movie_id] = time.monotonic()
    return stored


def _from_disk(movie_id: int) -> tuple[str, tuple[bytes, str] | None, str, str] | None:
    """The local part of the poster search: (TMDB id, the stored poster or None, Radarr's address and key).

    None = there is nothing to ask (Radarr is not connected, the movie is unknown or has no TMDB id).
    """
    if not is_connected():
        return None
    tmdb = _tmdb_id(movie_id)
    if not tmdb:
        return None
    return (tmdb, images.load_title(MEDIA_TYPE, tmdb), *_connection())


def _store(tmdb: str, content: bytes) -> tuple[bytes, str] | None:
    """Store a downloaded poster and return it as the store serves it. None = it was no image."""
    return images.load_title(MEDIA_TYPE, tmdb) if images.save_title(MEDIA_TYPE, tmdb, content) else None


def clean_up_posters() -> int:
    """Delete the provisional posters of movies that are in the library by now. Returns the count.

    A movie in the library has its poster from Jellyfin and the cards switched to it by themselves -
    the provisional file would only lie on the disk. Called by Radarr's sync.
    """
    on_disk = sorted({tmdb for kind, tmdb in images.titles_on_disk() if kind == MEDIA_TYPE})
    if not on_disk:
        return 0
    titles = db.query_all_in("SELECT title_id, tmdb_id FROM radarr_title WHERE tmdb_id IN ({})", on_disk)
    in_library = stats.radarr_library({r["title_id"] for r in titles})
    deleted = sum(images.forget_title(MEDIA_TYPE, str(r["tmdb_id"])) for r in titles if r["title_id"] in in_library)
    if deleted:
        log.info("deleted %s provisional posters of %s titles that are now in the library", deleted, "radarr")
    return deleted


async def title_poster(tmdb_id: str) -> bytes | None:
    """The movie's poster as Radarr has it, by its TMDB id. None = Radarr is not connected or does not know the movie.

    For Seerr's requests whose movie is not in Jellyfin yet. A connection error (`ServiceError`) goes
    to the caller - it decides what to do with it.
    """
    if not is_connected():
        return None
    async with RadarrClient(*_connection()) as client:
        movie_id = await client.title_id(tmdb_id)
        return await client.poster(movie_id) if movie_id is not None else None
