"""Radarr (movies): the API client, settings, the history sync, the live state.

The door of the package - whoever writes `from jellyscope.arr import radarr` gets everything from
here and need not know which file a name lives in:

* `client` - the API v3 client (`RadarrClient`), the error without the key in its text
  (`RadarrError`), the app's name (`NAME`). It touches nothing in the package.
* `settings` - on, address and key, a "connected" Radarr, the connection test.
* `sync` - the history fetched into the database (upsert, an hour of overlap), the state of the
  last sync, deleting the downloaded data.
* `live` - the live state (queue, missing, cutoff, calendar, health) with the one-minute cache
  `live.CACHE`.
* `progress` - the live downloads for the card "Stahuje se" (downloading): the queue by movies with
  progress, the refresh command only while something downloads, a short cache.
* `posters` - the provisional poster of a movie that is still downloading (from Radarr, through
  the image store), the clean-up once the movie arrives in the library and the poster for Seerr's
  requests (`title_poster`).
* `titles` - Radarr's list of movies with the ids of the title databases (TMDB, IMDb): by the TMDB
  id the cards link a movie into the library.

Everything that knows anything about Radarr is in its files: this package, its statistics and
cards. It touches no other app of the *Arr page (guarded by tests/test_arr_tree.py); which name
lives in which file is guarded by tests/test_radarr_package.py.
"""
from __future__ import annotations

from .client import NAME, RadarrClient, RadarrError
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
    "RadarrClient",
    "RadarrError",
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
