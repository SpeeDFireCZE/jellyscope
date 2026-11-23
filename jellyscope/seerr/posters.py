"""Provisional posters of requests: a title that is not in Jellyfin yet.

A title from the library has its poster from Jellyfin (the /image/ route). A requested title that is
not in the library cannot have one from there - and Jellyscope never connects anywhere outside on
its own for it. It asks only the apps it has connected, in this order; the first find is stored on
disk (images.save_title) and the next time it goes nowhere:

1. **Radarr** (a movie) or **Sonarr** (a series): they keep the poster of every title that reached
   them. Those are the approved requests.
2. **Seerr**: it passes TMDB images on at `/imageproxy/tmdb/…`, but only with "Enable Image Caching"
   on. It is the only source for **pending and declined** requests - those never reached Radarr or
   Sonarr.
3. Nothing: the title has no poster. That is remembered for five minutes (`MISSING_SECONDS`), so the
   apps are not asked on every page load.

A poster can be asked for only for a title somebody requested (it has a row in `seerr_request`, or the
live card's memory has it) - so the route cannot be used to question the apps about any id. The
address is never built from what came in the request: the TVDB id and the poster path are from the
database and the path must have the shape of a poster path (client.POSTER_PATH_PATTERN).

As soon as the title arrives in the library, the cards switch to the poster from Jellyfin
(cards_seerr.poster) and the next sync deletes the provisional file (`clean_up_posters`).
"""
from __future__ import annotations

import logging
import time

import httpx

from .. import db, images, stats
from ..arr import radarr, sonarr
from ..jellyfin import digits_only
from ..services import ServiceError
from . import live
from .client import POSTER_PATH_PATTERN, SeerrClient, SeerrError
from .settings import images_state, is_connected, write_images_state, write_web_address

log = logging.getLogger("jellyscope.seerr")

# How long the note "this title has no poster" holds (seconds).
MISSING_SECONDS = 300
PATH_PATTERN = POSTER_PATH_PATTERN
# Which *Arr app has the poster of which kind of title.
APPS = {"movie": "radarr", "tv": "sonarr"}
# {(media type, TMDB id): when it was last not found - time.monotonic()}. Only in memory.
_MISSING: dict[tuple[str, str], float] = {}
MAX_REMEMBERED = 5000


def forget_missing() -> None:
    """Drop the notes "no poster" - the next request asks the apps again.

    Called when something may have changed: after a sync, after the connection test and after saving
    the settings (somebody switched "Enable Image Caching" on in Seerr).
    """
    _MISSING.clear()


async def check_images(transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Ask Seerr whether it serves images and remember it. Returns the state ("1" / "0" / "").

    It reads Seerr's public settings: `cacheImages` is its "Enable Image Caching" (Settings →
    General). Without it Seerr serves no posters and pending and declined requests have only a
    placeholder. On the way it remembers the "Application URL" too (the address for the link to
    Seerr's settings).

    Called after a sync, after "Test connection" and after saving the settings. Every time it drops
    the notes "no poster": whoever just switched the option on in Seerr is to see posters at once,
    not in five minutes. An unavailable Seerr does not change the state - that it does not answer
    says nothing about what it has set.
    """
    forget_missing()
    if not is_connected():
        return images_state()
    try:
        async with SeerrClient(db.get_setting("seerr_url", ""), db.get_setting("seerr_api_key", ""),
                               transport=transport) as client:
            public = await client.public_settings()
    except SeerrError as error:
        log.debug("could not read Seerr's public settings: %s", error)
        return images_state()
    serves = public.get("cacheImages")
    write_images_state(serves if isinstance(serves, bool) else None)
    write_web_address(public.get("applicationUrl"))
    return images_state()


def _requested_title(media_type: str, tmdb_id: str) -> dict[str, str] | None:
    """What is stored about a requested title: the TVDB id and the poster path. None = nobody requested it.

    A title can have several requests (two people, 4K separately) and not every one has everything
    filled in: the first non-empty TVDB id is taken and the first path with the shape of a poster
    path. The choice is made here, not with MAX() in the query - the order of texts like `'-'` and
    `'/abc.jpg'` depends on the database's collation.
    """
    rows = db.query_all("SELECT tvdb_id, poster FROM seerr_request WHERE media_type = ? AND tmdb_id = ? ORDER BY id",
                        (media_type, tmdb_id))
    if rows:
        return {"tvdb_id": next((str(r["tvdb_id"]) for r in rows if r["tvdb_id"]), ""),
                "poster": next((str(r["poster"]) for r in rows if PATH_PATTERN.fullmatch(str(r["poster"] or ""))), "")}
    fresh = live.fresh_poster(media_type, tmdb_id)
    return None if fresh is None else {"tvdb_id": "", "poster": fresh}


def poster_path(media_type: str, tmdb_id: str) -> str:
    """The poster path of a title for Seerr, or "" (an unknown title, no poster, an invalid path).

    From the database (the sync stored it), for a fresh pending request from the live card's memory.
    What does not have the shape of a poster path is not used - not even when the database says so.
    """
    title = _requested_title(media_type, tmdb_id)
    path = title["poster"] if title else ""
    return path if PATH_PATTERN.fullmatch(path) else ""


async def _from_arr(media_type: str, tmdb_id: str, tvdb_id: str) -> bytes | None:
    """A poster from Radarr (a movie, by TMDB id) or Sonarr (a series, by TVDB id), if the app is connected and knows the title."""
    try:
        return await (radarr.title_poster(tmdb_id) if media_type == "movie" else sonarr.title_poster(tvdb_id))
    except ServiceError as error:
        log.debug("could not download the poster of %s %s from %s: %s", media_type, tmdb_id, APPS[media_type], error)
        return None


async def _from_seerr(path: str) -> bytes | None:
    """A poster from Seerr by its TMDB path. Without a connected Seerr or without a path nothing."""
    if not path or not is_connected():
        return None
    try:
        async with SeerrClient(db.get_setting("seerr_url", ""), db.get_setting("seerr_api_key", "")) as client:
            return await client.tmdb_poster(path)
    except ServiceError as error:
        log.debug("could not download poster %s from Seerr: %s", path, error)
        return None


async def provisional_poster(media_type: str, tmdb_id: str) -> tuple[bytes, str] | None:
    """The provisional poster of a requested title with the image type. None = there is none.

    From the disk, else from Radarr or Sonarr, else from Seerr - and what is found is stored. Every
    source is only a candidate: only what really is an image by its content is stored (an app behind
    a proxy can send a login page instead of a poster); otherwise the next one is tried.
    """
    if media_type not in APPS or not digits_only(tmdb_id):
        return None
    stored = images.load_title(media_type, tmdb_id)
    if stored:
        return stored
    title = _requested_title(media_type, tmdb_id)
    if title is None:
        return None
    key = (media_type, tmdb_id)
    if time.monotonic() - _MISSING.get(key, float("-inf")) < MISSING_SECONDS:
        return None
    content = await _from_arr(media_type, tmdb_id, title["tvdb_id"])
    if not (content and images.save_title(media_type, tmdb_id, content)):
        content = await _from_seerr(title["poster"] if PATH_PATTERN.fullmatch(title["poster"]) else "")
        if not (content and images.save_title(media_type, tmdb_id, content)):
            if len(_MISSING) > MAX_REMEMBERED:        # a safeguard: the memory does not grow for ever
                _MISSING.clear()
            _MISSING[key] = time.monotonic()
            return None
        # Seerr served the image, so it serves images: the state is fixed at once, without waiting for
        # a sync (somebody just switched the option on in Seerr).
        write_images_state(True)
    return images.load_title(media_type, tmdb_id)


def clean_up_posters() -> int:
    """Delete the provisional posters of titles that are in the library by now. Returns the count.

    A title in the library has its poster from Jellyfin and the cards switched to it by themselves
    (cards_seerr.poster) - the provisional file would only lie on the disk. Called by the sync; "in
    the library" is told the same way as for the cards (stats.seerr_posters).
    """
    on_disk = images.titles_on_disk()
    if not on_disk:
        return 0
    requests = db.query_all_in("SELECT DISTINCT media_type, jellyfin_id, tmdb_id FROM seerr_request WHERE tmdb_id IN ({})",
                               sorted({tmdb_id for _, tmdb_id in on_disk}))
    in_library = {(media_type, tmdb_id) for media_type, _, tmdb_id in
                  stats.seerr_posters((r["media_type"], r["jellyfin_id"], r["tmdb_id"]) for r in requests)}
    deleted = sum(images.forget_title(media_type, tmdb_id) for media_type, tmdb_id in on_disk
                  if (media_type, tmdb_id) in in_library)
    if deleted:
        log.info("deleted %s provisional posters of titles that are now in the library", deleted)
    return deleted
