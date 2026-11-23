"""The "Books" section: every page under /books/.

* `overview` - the Books page (registry cards).
* `compare` - two periods of reading and listening side by side (the empty frame for now).
* `insights` - the charts and what nobody reads (registry cards).

The book page belongs to the Library (/book/<id>, web/book.py): it is attached here only so that the web package keeps
its list of modules (web/__init__.py is at its line limit) - unlike the section's pages it is not under the guard,
because the Books cards (most read, readers) and the library pages link to it (the History links tracks and titles only) (and a vanished book says so), as to an album.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import book as _book_module
from ..base import require_content
from . import compare as _compare_module
from . import insights as _insights_module
from . import overview as _overview_module
from .compare import compare
from .insights import insights
from .overview import overview

# Without a book in Jellyfin the section is not in the menu and every page of it is 404: the guard
# is on the section's router, so a page added later is covered too.
_guarded = APIRouter(dependencies=[Depends(require_content("books"))])
_guarded.include_router(_overview_module.router)
_guarded.include_router(_compare_module.router)
_guarded.include_router(_insights_module.router)
router = APIRouter()
router.include_router(_guarded)
# /book/<id> belongs to the Library area, not to this section: it is mounted here only because web/__init__.py is at
# its line limit and cannot take another module (unlike the pages above it is not under the section's guard).
router.include_router(_book_module.router)

__all__ = ["compare", "insights", "overview", "router"]
