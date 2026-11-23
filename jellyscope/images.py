"""One store of images: the folder `data/imagecache` and everything in it.

Images are led into the pages through Jellyscope (so Jellyfin's address and
the keys do not get into the browser) and each is downloaded only once - the
second time it already lies here on the disk. The folder and the file names
used to be known by the /image/ route and by the cleanup in the scanner, two
places that had to agree. Now only this module knows it; whoever wants to
store, read or delete an image goes through it.

There are two kinds of images in the store:

* **An image of a Jellyfin item** (a movie, an episode, a series, a library):
  `<item id>-<kind>-<width>[-<tag>].img`. The tag (ImageTags from Jellyfin)
  is part of the name, so nothing needs invalidating: another image has
  another tag, so another address and another file. The names are the same
  as before the module existed - the cache from earlier stays valid.
* **A provisional poster of a title not in Jellyfin yet** (requests from
  Seerr): `title-<kind>-<tmdb id>.img` (`titul-…` before 1.9.0, still read). A title only ever has one; as soon
  as it is added to Jellyfin, the poster from Jellyfin applies and the
  provisional one is deleted (see jellyscope/seerr/).

Only checked input goes into a file name (an id of letters, digits, a hyphen
and an underscore; the kind `movie`/`tv`/`album`/`book`; an id of digits) - nothing to
step out of the folder with.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import config, db
from .jellyfin import JellyfinClient, digits_only

# The ceiling of a provisional poster. A poster a few hundred pixels wide has
# tens of kilobytes; whatever is bigger is no poster (and the disk is no dump).
MAX_BYTES = 5 * 1024 * 1024

FOLDER = "imagecache"
SUFFIX = ".img"
# `album` is a Lidarr album and `book` a Readarr book: the key is the numeric id in that app, not a TMDB id.
TITLE_KINDS = ("movie", "tv", "album", "book")
# `[0-9]`, not `\d`: that takes digits outside ASCII too ("٣"). The same rule as jellyfin.digits_only.
TITLE_PREFIX = "title-"
# Provisional posters stored before 1.9.0 (titul-<kind>-<tmdb id>.img): read, replaced and deleted like the new ones.
OLD_TITLE_PREFIX = "titul-"
_TITLE_FILE = re.compile(r"(?:title|titul)-(movie|tv|album|book)-([0-9]+)\.img")
_SAFE = re.compile(r"[A-Za-z0-9_-]+")


def folder() -> Path:
    """The folder with the images - next to the database, created on first use."""
    path = config.load_config().database_path.parent / FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_type(content: bytes) -> str | None:
    """The image's type by its first bytes: JPEG, PNG or WebP. Anything else is None.

    By the content, not by what the answer claims about itself: an
    application behind a reverse proxy can send a login page with the code
    200 instead of an image, and that must not get onto the disk as a poster.
    """
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _check(value: str, what: str) -> str:
    if not _SAFE.fullmatch(value or ""):
        raise ValueError(f"{what} does not belong in a file name: {value!r}")
    return value


def item_file(item_id: str, kind: str, width: int, tag: str = "") -> Path:
    """Where a Jellyfin item's image lies: `<id>-<kind>-<width>[-<tag>].img`."""
    name = f"{_check(item_id, 'item id')}-{_check(kind, 'image kind')}-{int(width)}"
    if tag:
        name += f"-{_check(tag, 'image tag')}"
    return folder() / (name + SUFFIX)


def title_file(kind: str, tmdb_id: str) -> Path:
    """Where a title's provisional poster lies: `title-<kind>-<id>.img`.

    The id is a TMDB id for `movie`/`tv` and Lidarr's album id for `album`, Readarr's book id for `book`; both are digits.
    """
    if kind not in TITLE_KINDS or not digits_only(str(tmdb_id)):
        raise ValueError(f"a provisional poster is only for movie/tv/album/book and a numeric id: {kind!r}, {tmdb_id!r}")
    return folder() / f"{TITLE_PREFIX}{kind}-{tmdb_id}{SUFFIX}"


def _old_title_file(kind: str, tmdb_id: str) -> Path:
    """The same poster under its name from before 1.9.0 (title_file checked the arguments)."""
    return folder() / f"{OLD_TITLE_PREFIX}{kind}-{tmdb_id}{SUFFIX}"


def _write(path: Path, content: bytes) -> None:
    """Writes a file at once: first beside it under a temporary name, then renames it.

    A page with fifty posters reads the folder at the moment another request
    writes into it. Writing straight to the place could hand it half an
    image; renaming is one step - the file either is not there yet or is whole.
    """
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".writing-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Images of Jellyfin items
# ---------------------------------------------------------------------------

async def item_image(item_id: str, kind: str, width: int, tag: str = "") -> tuple[bytes, str] | None:
    """An item's image with its type: from the disk, otherwise from Jellyfin (and it is stored). None = there is none.

    Not every item has every kind of image. A backdrop tends to be on a
    series, not on an episode; a logo is often missing altogether. Almost
    everything has a poster, so it is taken as a substitute - better than an
    empty space.

    The type of a file from the disk is told by the content; an old file for
    which that does not work is sent as JPEG - the way everything was sent
    before anybody told the types apart.
    """
    path = item_file(item_id, kind, width, tag)
    if path.exists():
        content = path.read_bytes()
        return content, image_type(content) or "image/jpeg"
    async with JellyfinClient(*db.jellyfin_connection()) as client:
        result = await client.image_bytes(item_id, kind, width)
        if result is None and kind != "Primary":
            result = await client.image_bytes(item_id, "Primary", width)
    if result is None:
        return None
    _write(path, result[0])
    return result


def forget_items(ids: list[str]) -> int:
    """Deletes the images of these items - all kinds, widths and tags. Returns the count.

    It is not just cleaning: as long as a file lies here under an address
    without a tag, the old image is sent and a person has no way to get to
    the new one. Called by the scanner when an item's image changed in
    Jellyfin. The name starts with the id and a hyphen, so the item `abc`
    deletes neither the images of the item `abcd` nor the provisional posters.
    """
    where = folder()
    deleted = 0
    for item_id in ids:
        if not _SAFE.fullmatch(item_id or ""):
            continue
        for path in where.glob(f"{item_id}-*{SUFFIX}"):
            try:
                path.unlink()
                deleted += 1
            except OSError:  # noqa: PERF203 - one locked file must not stop the rest
                continue
    return deleted


# ---------------------------------------------------------------------------
# Provisional posters of titles that are not in Jellyfin
# ---------------------------------------------------------------------------

def load_title(kind: str, tmdb_id: str) -> tuple[bytes, str] | None:
    """The stored provisional poster with its type, or None."""
    try:
        path = title_file(kind, tmdb_id)
    except ValueError:
        return None
    if not path.exists():
        path = _old_title_file(kind, tmdb_id)
    if not path.exists():
        return None
    content = path.read_bytes()
    content_type = image_type(content)
    return (content, content_type) if content_type else None


def save_title(kind: str, tmdb_id: str, content: bytes) -> bool:
    """Stores a provisional poster. False = it was not stored (not an image, too big, an invalid title).

    The content comes from another application (Radarr, Sonarr, Seerr), so it
    is not trusted: it must be JPEG, PNG or WebP by its first bytes and fit
    into MAX_BYTES.
    """
    if not content or len(content) > MAX_BYTES or image_type(content) is None:
        return False
    try:
        path = title_file(kind, tmdb_id)
    except ValueError:
        return False
    _write(path, content)
    _old_title_file(kind, tmdb_id).unlink(missing_ok=True)      # one poster per title
    return True


def forget_title(kind: str, tmdb_id: str) -> bool:
    """Deletes a title's provisional poster. True = there was one."""
    try:
        paths = (title_file(kind, tmdb_id), _old_title_file(kind, tmdb_id))
    except ValueError:
        return False
    deleted = False
    for path in paths:
        try:
            path.unlink()
            deleted = True
        except OSError:
            continue
    return deleted


def titles_on_disk() -> list[tuple[str, str]]:
    """The titles that have a provisional poster: [(kind, tmdb id)], sorted."""
    found = (_TITLE_FILE.fullmatch(path.name) for prefix in (TITLE_PREFIX, OLD_TITLE_PREFIX)
             for path in folder().glob(f"{prefix}*{SUFFIX}"))
    return sorted({(n.group(1), n.group(2)) for n in found if n})


def forget_titles() -> int:
    """Deletes all provisional posters (leaves the images from Jellyfin). Returns the count."""
    return sum(forget_title(kind, tmdb_id) for kind, tmdb_id in titles_on_disk())


def forget_kind(kind: str) -> int:
    """Deletes the provisional posters of one kind of title (`album`, `book`, …), no other. Returns the count."""
    return sum(forget_title(k, title_id) for k, title_id in titles_on_disk() if k == kind)
