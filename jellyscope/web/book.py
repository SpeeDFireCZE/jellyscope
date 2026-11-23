"""A book: /book/<id> - part of the Library, built like an album.

The page is about the book, not a period: the cover, the author, tiles (files, length, size, time listened and read
over the whole history), "About the book" and the files of the book (the parts of an audiobook) with their format,
length and size. Any file of a multi-file audiobook leads to the same book. "Who read" only with the area
"...including who read and listened" - without it the names are not sent to the template at all. An unknown id is
404, a book that vanished from Jellyfin keeps its page.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import access, cards_books, stats
from ..stats import reading_reported
from ..stats.books_library import book_detail as book_data
from .base import _context, check_id, require_login, templates

router = APIRouter()


@router.get("/book/{book_id}", response_class=HTMLResponse)
async def book_detail(request: Request, book_id: str, account: dict[str, Any] = Depends(require_login)):
    """A book: its files with format and size, who read it (with the area)."""
    check_id(book_id)
    data = await asyncio.to_thread(book_data, book_id)
    if data is None:
        raise HTTPException(status_code=404)
    if not access.account_sees(account, "books_who"):
        data["readers"] = None
    work = data["work"]
    return templates.TemplateResponse(request, "library/book.html", _context(
        request, account, d=data, genre_list=[g.strip() for g in str(work["genres"]).split("|") if g.strip()],
        cover=cards_books.cover(work["id"], work["cover_tag"]),
        # The library tile it belongs to (it may be gone from Jellyfin: then just its name).
        library=stats.library(work["library_id"]) if work["library_id"] else None,
        reading_reported=reading_reported(),
        formats=sorted(f.upper() for f in work["formats"] if f)))
