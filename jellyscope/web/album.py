"""An album: /album/<id> - part of the Library, built like a series.

The page is about the album, not a period: the cover, the artist (a link to their page
in Music), tiles (tracks, discs, length, size, plays over the whole history), "About the
album" and the tracks by disc with their format, bitrate and plays. "Who listened" only
with the area "...including who listened" - without it the names are not sent to the
template at all. A track has no page of its own; links to a track lead to its row here
(#track-<id>). An unknown id is 404, an album that vanished from Jellyfin keeps its page.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import access, cards_music, stats
from ..stats.music_library import album_detail as album_data
from .music.details import check_id, split_genres
from .base import _context, require_login, templates

router = APIRouter()


@router.get("/album/{album_id}", response_class=HTMLResponse)
async def album_detail(request: Request, album_id: str, account: dict[str, Any] = Depends(require_login)):
    """An album: its tracks by disc with format and plays, who listened (with the area)."""
    check_id(album_id)
    data = await asyncio.to_thread(album_data, album_id)
    if data is None:
        raise HTTPException(status_code=404)
    if not access.account_sees(account, "music_who"):
        data["listeners"] = None
    row = data["album"]
    return templates.TemplateResponse(request, "library/album.html", _context(
        request, account, d=data, genre_list=split_genres(row["genres"]),
        cover=cards_music.cover(row["id"], row["cover_tag"]),
        artist_link=cards_music.artist_link(row["artist_id"]),
        # The library tile it belongs to (it may be gone from Jellyfin: then just its name).
        library=stats.library(row["library_id"]) if row["library_id"] else None,
        # A disc heading only for an album of several discs.
        several_discs=len(data["discs"]) > 1))
