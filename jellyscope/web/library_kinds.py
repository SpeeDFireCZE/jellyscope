"""Libraries that are not video: what the Library shows for each kind of content.

Part of the web package (see jellyscope/web/__init__.py). The Library takes over every Jellyfin
library (16. 11. 2026). A video library has its own detail in web/library.py (Overview, Media,
Activity); every other kind of content Jellyscope collects registers here once, with

* its page - /library/<id> of a library of that kind (tabs of its own), and
* its section on the Library page, next to the video statistics (only while Jellyscope has
  content of that kind).

Music is the first kind, books the second (a module each: web/music_library.py, web/books_library.py).
A type nobody registered (photos, music videos…) has a tile without a link and no page.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import access
from ..books import has_books
from ..music import has_music
from . import books_library, music_library


@dataclass(frozen=True)
class LibraryKind:
    # Jellyfin's CollectionType of the libraries of this kind.
    collection_type: str
    # Does Jellyscope have content of this kind (then the Library page shows its section)?
    available: Callable[[], bool]
    # The permission area of its statistics (the Library page section; its own page decides
    # its tabs by it too). The grid of its items stays in the Library area.
    area: str
    # The page of one library of this kind: (request, library row, account).
    page: Callable[[Request, dict[str, Any], dict[str, Any]], HTMLResponse]
    # The data of its section on the Library page (the template reads it as `content_section.data`).
    summary: Callable[[], dict[str, Any]]
    # The template of that section.
    summary_template: str


KINDS: dict[str, LibraryKind] = {
    "music": LibraryKind("music", has_music, music_library.AREA, music_library.library_page, music_library.library_summary,
                         "library/_music_summary.html"),
    "books": LibraryKind("books", has_books, books_library.AREA, books_library.library_page, books_library.library_summary,
                         "library/_books_summary.html"),
}


def summaries(account: dict[str, Any]) -> list[dict[str, Any]]:
    """The sections of the Library page: [{"template", "data"}], each kind's data under its own key.

    Only kinds with content, and only for an account with the kind's area.
    """
    return [{"template": kind.summary_template, "data": kind.summary()}
            for kind in KINDS.values() if kind.available() and access.account_sees(account, kind.area)]
