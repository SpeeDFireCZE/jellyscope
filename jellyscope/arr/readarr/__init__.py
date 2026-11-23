"""Readarr (books; and its forks Chaptarr and Bookshelf): the API client, settings, the history sync, the books.

The door of the package - whoever writes `from jellyscope.arr import readarr` gets everything from
here and need not know which file a name lives in:

* `client` - the API v1 client (`ReadarrClient`), the error without the key in its text
  (`ReadarrError`), the app's name (`NAME`) and the names of the family (`FAMILY`). It touches nothing
  in the package.
* `settings` - on, address and key, a "connected" Readarr, the connection test (it takes Readarr,
  Chaptarr and Bookshelf) and the name of the app that answers.
* `sync` - the history fetched into the database (upsert, an hour of overlap), the state of the
  last sync, deleting the downloaded data.
* `live` - the state right now (queue, missing, cutoff, calendar, health), cached for a minute.
* `progress` - the live downloads by book ("Stahuje se") and their refresh rhythm.
* `posters` - the provisional covers of books still downloading, the cover from the library.
* `titles` - Readarr's list of books with the ISBNs and their pairing with the books of the library:
  by it the cards link a book into the library.

Everything that knows anything about Readarr is in its files: this package, its statistics and
cards. It touches no other app of the *Arr page (guarded by tests/test_arr_tree.py).
"""
from __future__ import annotations

from .client import FAMILY, NAME, ReadarrClient, ReadarrError
from .live import live_state
from .posters import clean_up_posters, poster_missing, provisional_poster
from .progress import download_state
from .settings import app_name, check_connection, is_connected, save_settings, stored_settings
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
    "app_name",
    "FAMILY",
    "NAME",
    "ReadarrClient",
    "ReadarrError",
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
    "title_rows",
]
