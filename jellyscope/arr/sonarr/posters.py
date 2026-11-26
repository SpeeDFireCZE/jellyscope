"""Series posters from Sonarr: provisional ones for the card "Stahuje se" and for Seerr's requests.

The card "Stahuje se" (downloading) shows series with a poster. A series from the library has one
from Jellyfin (the /image/ route, see stats.sonarr_library). A series still downloading is not in
the library - Sonarr itself gives it a poster: it keeps the poster of each of its series
(`mediacover`). Jellyscope never connects anywhere outside for a poster.

The same holds as for Seerr's requests (seerr/posters.py): **one poster per title** in the image
store (images.py, a file by the kind and the TMDB id - the one Seerr would use for that series), only
a real image is stored and "none" is remembered for five minutes. A poster can be asked for only for a
series Jellyscope knows from Sonarr (the list of series, or the queue just read) - only the series'
number is taken from the request's address.

Once the series arrives in the library the card switches to the poster from Jellyfin and the next sync
deletes the provisional file (`clean_up_posters`).

`title_poster` is for Seerr's requests: the series' poster by its TVDB id, without storing it (Seerr
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
from .client import SETTINGS_PREFIX, SonarrClient
from .settings import is_connected

log = logging.getLogger("jellyscope.arr")

# How long the note "this series has no poster" holds (seconds).
MISSING_SECONDS = 300
# The kind of title in the image store (images.TITLE_KINDS).
MEDIA_TYPE = "tv"
# {series id: when its poster was last not found - time.monotonic()}. Only in memory.
_MISSING: dict[int, float] = {}
MAX_REMEMBERED = 5000


def _connection() -> tuple[str, str]:
    """Sonarr's address and key from the settings."""
    return db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")


def _tmdb_id(series_id: int) -> str:
    """The series' TMDB id: from Sonarr's list of series, else from the queue just read. "" = unknown series."""
    row = db.query_one("SELECT tmdb_id FROM sonarr_title WHERE title_id = ?", (series_id,))
    tmdb = str((row or {}).get("tmdb_id") or "") or progress.fresh_tmdb(series_id)
    return tmdb if digits_only(tmdb) else ""


def poster_missing(series_id: int | None) -> bool:
    """Is it known that the series has no poster (Sonarr did not serve it a moment ago)?

    For the card "Stahuje se": its body changes every five seconds and with every swap the browser
    would ask for the missing poster again - and the placeholder would flash in place of the poster
    every time. While the note holds, the card draws the placeholder right away; then it offers the
    poster again and it is tried once more.
    """
    return time.monotonic() - _MISSING.get(series_id, float("-inf")) < MISSING_SECONDS


async def provisional_poster(series_id: int) -> tuple[bytes, str] | None:
    """The series' provisional poster with the image type. None = there is none.

    From the disk, else from Sonarr - and what is found is stored. Only what really is an image by its
    content is stored (an app behind a proxy can send a login page instead of a poster). An unknown
    series, a series without a TMDB id and a Sonarr that is not connected end without a request
    outside.
    """
    # The database and the disk in a thread: the poster route runs in the server's loop and the loop
    # would wait for them as a whole.
    local = await asyncio.to_thread(_from_disk, series_id)
    if local is None:
        return None
    tmdb, stored, url, key = local
    if stored:
        return stored
    if poster_missing(series_id):
        return None
    try:
        async with SonarrClient(url, key) as client:
            content = await client.poster(series_id)
    except ServiceError as error:
        log.debug("the poster of title %s could not be downloaded from %s: %s", series_id, "sonarr", error)
        content = None
    stored = await asyncio.to_thread(_store, tmdb, content) if content else None
    if not stored:
        if len(_MISSING) > MAX_REMEMBERED:          # a safeguard: the memory does not grow for ever
            _MISSING.clear()
        _MISSING[series_id] = time.monotonic()
    return stored


def _from_disk(series_id: int) -> tuple[str, tuple[bytes, str] | None, str, str] | None:
    """The local part of the poster search: (TMDB id, the stored poster or None, Sonarr's address and key).

    None = there is nothing to ask (Sonarr is not connected, the series is unknown or has no TMDB id).
    """
    if not is_connected():
        return None
    tmdb = _tmdb_id(series_id)
    if not tmdb:
        return None
    return (tmdb, images.load_title(MEDIA_TYPE, tmdb), *_connection())


def _store(tmdb: str, content: bytes) -> tuple[bytes, str] | None:
    """Store a downloaded poster and return it as the store serves it. None = it was no image."""
    return images.load_title(MEDIA_TYPE, tmdb) if images.save_title(MEDIA_TYPE, tmdb, content) else None


def clean_up_posters() -> int:
    """Delete the provisional posters of series that are in the library by now. Returns the count.

    A series in the library has its poster from Jellyfin and the cards switched to it by themselves -
    the provisional file would only lie on the disk. Called by Sonarr's sync.
    """
    on_disk = sorted({tmdb for kind, tmdb in images.titles_on_disk() if kind == MEDIA_TYPE})
    if not on_disk:
        return 0
    titles = db.query_all_in("SELECT title_id, tmdb_id FROM sonarr_title WHERE tmdb_id IN ({})", on_disk)
    in_library = stats.sonarr_library({r["title_id"] for r in titles})
    deleted = sum(images.forget_title(MEDIA_TYPE, str(r["tmdb_id"])) for r in titles if r["title_id"] in in_library)
    if deleted:
        log.info("deleted %s provisional posters of %s titles that are now in the library", deleted, "sonarr")
    return deleted


def own_posters() -> set[str]:
    """The TMDB ids of the provisional posters on the disk that are Sonarr's: of series in its list of series.

    A request in Seerr for the same title has the same file (images.title_file: the kind and the TMDB id). Seerr,
    deleting its data, leaves these: the card "Stahuje se" shows them and `clean_up_posters` deletes them once the
    series is in the library.
    """
    on_disk = sorted({tmdb for kind, tmdb in images.titles_on_disk() if kind == MEDIA_TYPE})
    if not on_disk:
        return set()
    return {str(r["tmdb_id"]) for r in db.query_all_in("SELECT tmdb_id FROM sonarr_title WHERE tmdb_id IN ({})", on_disk)}


async def title_poster(tvdb_id: str) -> bytes | None:
    """The series' poster as Sonarr has it, by its TVDB id. None = Sonarr is not connected or does not know the series.

    For Seerr's requests whose series is not in Jellyfin yet. A connection error (`ServiceError`) goes
    to the caller - it decides what to do with it.
    """
    if not is_connected():
        return None
    async with SonarrClient(*_connection()) as client:
        series_id = await client.title_id(tvdb_id)
        return await client.poster(series_id) if series_id is not None else None
