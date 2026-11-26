"""Seerr: requests for movies and series (API client, settings, sync, live card, posters).

Seerr is not part of *Arr: it has its own page in the menu and its own section in Settings right
after Jellyfin. The door of the package - whoever writes `from jellyscope import seerr` gets
everything from here:

* `client` - the API v1 client (`SeerrClient`), the error without the key in its text
  (`SeerrError`), `NAME`. It touches nothing in the package. The shared core of the connection
  (errors, the key's protection, the address check) is in `jellyscope/services.py`.
* `settings` - on, address and key, "connected", the connection test; what is known about Seerr
  (does it serve images? the address for the link to its settings).
* `sync` - all requests into the table `seerr_request` (upsert, times of state changes, deleted in
  Seerr, the titles' names), the state, deleting the stored data.
* `live` - the live card "Waiting for approval" (the pending requests' rows) and what both live
  cards share: the one-minute cache `live.CACHE`, the names of titles the table does not know yet.
* `latest` - the page of Seerr's newest requests for "Latest requests" and its rows
  (stats.seerr_with_live lays them over the stored requests).
* `now` - both live cards loaded at once under one entry of the cache (`pending`,
  `latest_requests`): one wait for a hanging Seerr, the same moment in both cards.
* `posters` - the provisional poster of a title that is not in Jellyfin: from Radarr or Sonarr,
  else from Seerr; the clean-up once the title arrives in the library; finding out whether Seerr
  serves images ("Enable Image Caching").

The statistics over the stored requests are in `stats/seerr.py`, the pages in `web/`. Which name
lives in which file is guarded by tests/test_seerr_package.py.
"""
from __future__ import annotations

from .client import NAME, SeerrClient, SeerrError
from .now import latest_requests, pending
from .posters import check_images, clean_up_posters, forget_missing, poster_path, provisional_poster
from .settings import check_connection, images_state, is_connected, save_settings, settings_link, stored_settings
from .sync import (
    delete_data,
    delete_downloaded_data,
    normalize_id,
    request_count,
    request_row,
    sync_running,
    sync_state,
    synchronize,
)

__all__ = [
    "NAME",
    "SeerrClient",
    "SeerrError",
    "check_connection",
    "check_images",
    "clean_up_posters",
    "delete_data",
    "delete_downloaded_data",
    "forget_missing",
    "images_state",
    "is_connected",
    "latest_requests",
    "normalize_id",
    "pending",
    "poster_path",
    "provisional_poster",
    "request_count",
    "request_row",
    "save_settings",
    "settings_link",
    "stored_settings",
    "sync_running",
    "sync_state",
    "synchronize",
]
