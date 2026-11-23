"""Lidarr (music): the API client, settings, the history sync, the albums.

The door of the package - whoever writes `from jellyscope.arr import lidarr` gets everything from
here and need not know which file a name lives in:

* `client` - the API v1 client (`LidarrClient`), the error without the key in its text
  (`LidarrError`), the app's name (`NAME`). It touches nothing in the package.
* `settings` - on, address and key, a "connected" Lidarr, the connection test.
* `sync` - the history fetched into the database (upsert, an hour of overlap), the state of the
  last sync, deleting the downloaded data.
* `live` - the state right now (queue, missing, cutoff, calendar, health), cached for a minute.
* `progress` - the live downloads by album ("Stahuje se") and their refresh rhythm.
* `posters` - the provisional covers of albums still downloading, the cover from the library.
* `titles` - Lidarr's list of albums with the MusicBrainz ids and their pairing with the albums and
  artists of the music library: by it the cards link an album into the library.

Everything that knows anything about Lidarr is in its files: this package, its statistics and
cards. It touches no other app of the *Arr page (guarded by tests/test_arr_tree.py).
"""
from __future__ import annotations

from .client import NAME, LidarrClient, LidarrError
from .live import live_state
from .posters import clean_up_posters, poster_missing, provisional_poster
from .progress import download_state
from .settings import check_connection, is_connected, save_settings, stored_settings
from .sync import (
    delete_data,
    delete_downloaded_data,
    event_count,
    event_from_history,
    refresh_counts,
    sync_running,
    sync_state,
    synchronize,
)
from .titles import sync_titles, title_rows

__all__ = [
    "NAME",
    "LidarrClient",
    "LidarrError",
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
    "refresh_counts",
    "save_settings",
    "stored_settings",
    "sync_running",
    "sync_state",
    "sync_titles",
    "synchronize",
    "title_rows",
]
