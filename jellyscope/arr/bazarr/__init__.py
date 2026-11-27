"""Bazarr: subtitles for series and movies (the API client, settings, history, the live state).

Bazarr is the third app of the *Arr page (beside Sonarr and Radarr): it has a card in Settings → *Arr
and a tab on the *Arr page. It has a package of its own like every app under `arr/`: Sonarr and Radarr
stand on the pair grab → import of a file, Bazarr knows the language, the provider and the match
score. The door of the package - whoever writes `from jellyscope.arr import bazarr` gets everything
from here:

* `client` - the API client (`BazarrClient`), the error without the key in its text (`BazarrError`),
  `NAME`, the codes of history actions (`ACTIONS`, `ACQUIRED`). It touches nothing in the package. The
  shared core of the connection is in `jellyscope/services.py`.
* `settings` - on, address and key, "connected", the connection test.
* `times` - the time of events: Bazarr returns it only as text in the system's format,
  `detect_format` recognises the format from a page of history and `to_utc` converts it.
* `sync` - the subtitle history into the table `bazarr_event` (page by page from the newest, an
  upsert by the fingerprint), the state, deleting the data.
* `live` - the live state for the cards "In the app right now" (what is missing, providers,
  warnings), a one-minute cache.

The statistics are in `stats/bazarr.py`, the cards in `cards_bazarr.py`. Which name lives in which
file is guarded by tests/test_bazarr_package.py.
"""
from __future__ import annotations

from .client import ACQUIRED, ACTIONS, NAME, BazarrClient, BazarrError
from .api_data import api_stats
from .live import live_state
from .settings import check_connection, is_connected, save_settings, stored_settings
from .sync import (
    delete_data,
    delete_downloaded_data,
    event_count,
    event_from_history,
    fingerprint,
    refresh_counts,
    sync_running,
    sync_state,
    synchronize,
)
from .times import FORMATS, detect_format, to_utc

__all__ = [
    "ACQUIRED",
    "ACTIONS",
    "FORMATS",
    "NAME",
    "BazarrClient",
    "BazarrError",
    "check_connection",
    "delete_data",
    "delete_downloaded_data",
    "detect_format",
    "event_count",
    "event_from_history",
    "fingerprint",
    "is_connected",
    "live_state",
    "refresh_counts",
    "save_settings",
    "stored_settings",
    "sync_running",
    "sync_state",
    "synchronize",
    "to_utc",
]
