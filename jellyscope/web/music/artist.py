"""An artist: /music/artist/<id> - numbers of the period, albums, most played tracks, who listened."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ... import cards_music, stats
from ..base import _context, require_login, templates
from .details import load_detail, split_genres

router = APIRouter()


@router.get("/music/artist/{artist_id}", response_class=HTMLResponse)
async def artist(request: Request, artist_id: str, days: int | None = None, since: str | None = None,
                 until: str | None = None, account: dict[str, Any] = Depends(require_login)):
    """An artist: numbers of the period, albums, most played tracks, who listened."""
    period, data = await load_detail(request, days, since, until, account, stats.music_artist, artist_id)
    row = data["artist"]
    data["albums"] = [{**a, "cover": cards_music.cover(a["id"], a["cover_tag"]), "link": cards_music.album_link(a["id"])}
                    for a in data["albums"]]
    data["tracks"] = [{**s, "link": cards_music.album_link(s["album_id"], s["id"]),
                        "album_link": cards_music.album_link(s["album_id"])} for s in data["tracks"]]
    return templates.TemplateResponse(request, "music/artist.html", _context(
        request, account, days=period, d=data, genre_list=split_genres(row["genres"]),
        image=cards_music.cover(row["id"], row["image_tag"])))
