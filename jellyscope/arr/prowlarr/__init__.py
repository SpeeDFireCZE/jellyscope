"""Prowlarr: indexers, queries and grabbed releases (the API client, settings, the sync, the live state).

Prowlarr is the fourth app of the *Arr page (beside Sonarr, Radarr and Bazarr): it has a card in
Settings → *Arr and a tab on the *Arr page. It is a view of the indexers - what arrived in the library
is still shown only by the Sonarr and Radarr tabs. Every app has a package of its own under `arr/`;
nothing is added to another app's package. The door of the package - whoever writes
`from jellyscope.arr import prowlarr` gets everything from here:

* `client` - the API client (`ProwlarrClient`), the error without the key in its text
  (`ProwlarrError`), `NAME`, the kinds of history events (`KINDS`). It touches nothing in the package.
  The shared core of the connection is in `jellyscope/services.py`.
* `settings` - on, address and key, "connected", the connection test.
* `sync` - Prowlarr's history into hourly sums (`prowlarr_hour`) and the list of grabbed releases
  (`prowlarr_grab`), the indexers' names, deleting the data.
* `live` - the live state: the indexers and which of them Prowlarr leaves out right now, the connected
  apps, the warnings; a one-minute cache.

Which name lives in which file is guarded by tests/test_prowlarr_package.py.
"""
from __future__ import annotations

from .client import KINDS, NAME, ProwlarrClient, ProwlarrError
from .live import live_state
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

__all__ = [
    "KINDS",
    "NAME",
    "ProwlarrClient",
    "ProwlarrError",
    "check_connection",
    "delete_data",
    "delete_downloaded_data",
    "event_count",
    "event_from_history",
    "is_connected",
    "live_state",
    "save_settings",
    "stored_settings",
    "sync_running",
    "sync_state",
    "synchronize",
]
