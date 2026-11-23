"""Album covers from Lidarr: provisional ones for the card "Stahuje se".

The card "Stahuje se" (downloading) shows albums with a cover. An album from the music library has
its cover from Jellyfin (the /image/ route, put together by the cards). An album still downloading is not in
the library - Lidarr itself gives it a cover: it keeps the cover of each of its albums
(`mediacover`). Jellyscope never connects anywhere outside for a cover.

**One cover per album** in the image store (images.py, a file by the kind `album` and the album's
numeric id in Lidarr), only a real image is stored and "none" is remembered for five minutes. A cover
can be asked for only for an album Jellyscope knows from Lidarr (the list of albums, or the queue
just read) and that is not in the library - only the album's number is taken from the request's
address.

Once the album arrives in the library the card switches to the cover from Jellyfin and the next sync
deletes the provisional file (`clean_up_posters`).
"""
from __future__ import annotations

import asyncio
import logging
import time

from ... import db, images
from ...services import ServiceError
from . import progress
from .client import SETTINGS_PREFIX, LidarrClient
from .settings import is_connected

log = logging.getLogger("jellyscope.arr")

# How long the note "this album has no cover" holds (seconds).
MISSING_SECONDS = 300
# The kind of title in the image store (images.TITLE_KINDS).
MEDIA_TYPE = "album"
# {album id: when its cover was last not found - time.monotonic()}. Only in memory.
_MISSING: dict[int, float] = {}
MAX_REMEMBERED = 5000


def _connection() -> tuple[str, str]:
    """Lidarr's address and key from the settings."""
    return db.get_setting(f"{SETTINGS_PREFIX}_url", ""), db.get_setting(f"{SETTINGS_PREFIX}_api_key", "")


def poster_missing(album_id: int | None) -> bool:
    """Is it known that the album has no cover (Lidarr did not serve it a moment ago)?

    For the card "Stahuje se": its body changes every five seconds and with every swap the browser
    would ask for the missing cover again - and the placeholder would flash in place of the cover
    every time. While the note holds, the card draws the placeholder right away; then it offers the
    cover again and it is tried once more.
    """
    return time.monotonic() - _MISSING.get(album_id, float("-inf")) < MISSING_SECONDS


async def provisional_poster(album_id: int) -> tuple[bytes, str] | None:
    """The album's provisional cover with the image type. None = there is none.

    From the disk, else from Lidarr - and what is found is stored. Only what really is an image by its
    content is stored (an app behind a proxy can send a login page instead of a cover). An unknown
    album, an album already in the library and a Lidarr that is not connected end without a request
    outside.
    """
    # The database and the disk in a thread: the cover route runs in the server's loop and the loop
    # would wait for them as a whole.
    local = await asyncio.to_thread(_from_disk, album_id)
    if local is None:
        return None
    stored, url, key = local
    if stored:
        return stored
    if poster_missing(album_id):
        return None
    try:
        async with LidarrClient(url, key) as client:
            content = await client.cover(album_id)
    except ServiceError as error:
        log.debug("the poster of title %s could not be downloaded from %s: %s", album_id, "lidarr", error)
        content = None
    stored = await asyncio.to_thread(_store, album_id, content) if content else None
    if not stored:
        if len(_MISSING) > MAX_REMEMBERED:          # a safeguard: the memory does not grow for ever
            _MISSING.clear()
        _MISSING[album_id] = time.monotonic()
    return stored


def _from_disk(album_id: int) -> tuple[tuple[bytes, str] | None, str, str] | None:
    """The local part of the cover search: (the stored cover or None, Lidarr's address and key).

    None = there is nothing to ask (Lidarr is not connected, the album is unknown or already in the library).
    """
    if not is_connected():
        return None
    known = db.query_one("SELECT jellyfin_id FROM lidarr_title WHERE title_id = ?", (album_id,))
    if known is None and not progress.in_queue(album_id):
        return None
    if known and known["jellyfin_id"]:
        return None
    return (images.load_title(MEDIA_TYPE, str(album_id)), *_connection())


def _store(album_id: int, content: bytes) -> tuple[bytes, str] | None:
    """Store a downloaded cover and return it as the store serves it. None = it was no image."""
    return images.load_title(MEDIA_TYPE, str(album_id)) if images.save_title(MEDIA_TYPE, str(album_id), content) else None


def clean_up_posters() -> int:
    """Delete the provisional covers of albums that are in the library by now. Returns the count.

    An album in the library has its cover from Jellyfin and the cards switched to it by themselves -
    the provisional file would only lie on the disk. Called by Lidarr's sync.
    """
    on_disk = sorted({album for kind, album in images.titles_on_disk() if kind == MEDIA_TYPE})
    if not on_disk:
        return 0
    albums = db.query_all_in("SELECT title_id FROM lidarr_title WHERE jellyfin_id <> '' AND title_id IN ({})",
                             [int(album) for album in on_disk])
    deleted = sum(images.forget_title(MEDIA_TYPE, str(r["title_id"])) for r in albums)
    if deleted:
        log.info("deleted %s provisional posters of %s titles that are now in the library", deleted, "lidarr")
    return deleted
