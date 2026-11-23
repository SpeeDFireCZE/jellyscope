"""Sonarr (series): the API client, settings, the history sync, the live state.

The door of the package - whoever writes `from jellyscope.arr import sonarr` gets everything from
here and need not know which file a name lives in:

* `client` - the API v3 client (`SonarrClient`), the error without the key in its text
  (`SonarrError`), the app's name (`NAME`). It touches nothing in the package.
* `settings` - on, address and key, a "connected" Sonarr, the connection test.
* `sync` - the history fetched into the database (upsert, an hour of overlap), the state of the
  last sync, deleting the downloaded data.
* `live` - the live state (queue, missing, cutoff, calendar, health) with the one-minute cache
  `live.CACHE`.
* `progress` - the live downloads for the card "Stahuje se" (downloading): the queue by series with
  progress, the refresh command only while something downloads, a short cache.
* `posters` - the provisional poster of a series that is still downloading (from Sonarr, through
  the image store), the clean-up once the series arrives in the library and the poster for Seerr's
  requests (`title_poster`).
* `titles` - Sonarr's list of series with the ids of the title databases and their pairing with
  Jellyfin's series: by it the cards link a series into the library.

Everything that knows anything about Sonarr is in its files: this package, its statistics and
cards. It touches no other app of the *Arr page (guarded by tests/test_arr_tree.py); which name
lives in which file is guarded by tests/test_sonarr_package.py.
"""
from __future__ import annotations

from .client import NAME, SonarrClient, SonarrError
from .live import live_state
from .posters import clean_up_posters, poster_missing, provisional_poster, title_poster
from .progress import download_state
from .settings import check_connection, is_connected, save_settings, stored_settings
from .sync import (
    delete_data,
    delete_downloaded_data,
    event_count,
    event_from_history,
    sync_running,
    sync_state,
    synchronize,
)
from .titles import sync_titles, title_rows

__all__ = [
    "NAME",
    "SonarrClient",
    "SonarrError",
    "check_connection",
    "clean_up_posters",
    "delete_data",
    "delete_downloaded_data",
    "download_state",
    "event_count",
    "event_from_history",
    "is_connected",
    "live_state",
    "poster_missing",
    "provisional_poster",
    "save_settings",
    "stored_settings",
    "sync_running",
    "sync_state",
    "sync_titles",
    "synchronize",
    "title_poster",
    "title_rows",
]
