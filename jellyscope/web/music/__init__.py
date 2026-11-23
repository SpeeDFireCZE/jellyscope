"""The "Music" section: every page under /music/.

* `overview` - the Music page (registry cards).
* `compare` - two periods of listening side by side (like /video/compare).
* `insights` - the charts and what nobody listens to (registry cards).
* `artist` - an artist's page.
* `details` - helpers shared with the album page (web/album.py).

The album page belongs to the Library (/album/<id>).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..base import require_content
from . import artist as _artist_module
from . import compare as _compare_module
from . import insights as _insights_module
from . import overview as _overview_module
from .artist import artist
from .compare import compare
from .details import check_id, load_detail, split_genres
from .insights import insights
from .overview import overview

# Without a track in Jellyfin the section is not in the menu and its pages are 404: every page of
# the section goes under the guard, a new one included. The exception is a detail page other
# pages link to - an artist's page stays, like an album's page in the Library (album and viewer
# pages link to it, and a vanished artist says so).
_guarded = APIRouter(dependencies=[Depends(require_content("music"))])
_guarded.include_router(_overview_module.router)
_guarded.include_router(_compare_module.router)
_guarded.include_router(_insights_module.router)
router = APIRouter()
router.include_router(_guarded)
router.include_router(_artist_module.router)

__all__ = ["artist", "check_id", "compare", "insights", "load_detail", "overview", "router", "split_genres"]
