"""The "Movies & series" section: every page under /video/.

One module per page (all with their own router); this file joins them into
one `router` that the web app includes (web/__init__.py), and exposes the
names other code reads:

* `overview` - the overview of watching and its daily card.
* `compare` - two periods side by side.
* `insights` - insights, the full ranking of the most watched titles and
  the partial of the overview card.
* `languages` - languages, files without a language, the preferred language.

Without a movie or an episode in Jellyfin every page here answers 404
(web/base.require_content). Old addresses of these pages (/, /insights,
/languages…) redirect here - see jellyscope/legacy_urls.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..base import require_content
from . import compare as _compare_module
from . import insights as _insights_module
from . import languages as _languages_module
from . import overview as _overview_module
from .compare import COMPARE_A_KEY, COMPARE_B_KEY, compare
from .insights import insights, insights_top, top_items_partial
from .languages import languages, languages_preferred, languages_undetermined
from .overview import daily_partial, overview

# Without a movie or an episode in Jellyfin the section is not in the menu and its pages are 404.
router = APIRouter(dependencies=[Depends(require_content("video"))])
for _module in (_overview_module, _compare_module, _insights_module, _languages_module):
    router.include_router(_module.router)

__all__ = [
    "COMPARE_A_KEY",
    "COMPARE_B_KEY",
    "compare",
    "daily_partial",
    "insights",
    "insights_top",
    "languages",
    "languages_preferred",
    "languages_undetermined",
    "overview",
    "router",
    "top_items_partial",
]
