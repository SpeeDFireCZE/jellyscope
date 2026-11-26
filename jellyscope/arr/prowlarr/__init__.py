"""Prowlarr: indexers, queries and grabbed releases (the API client, settings, the sync, the live state).

Prowlarr is the fourth app of the *Arr page (beside Sonarr, Radarr and Bazarr): it has a card in
Settings → *Arr and a tab on the *Arr page. It is a view of the indexers - what arrived in the library
is still shown only by the Sonarr and Radarr tabs. Every app has a package of its own under `arr/`;
nothing is added to another app's package. The door of the package - whoever writes
`from jellyscope.arr import prowlarr` gets everything from here:

* `client` - the API client (`ProwlarrClient`), the error without the key in its text
  (`ProwlarrError`), `NAME`, the kinds of history events (`KINDS`). It touches nothing in the package.
  The shared core of the connection is in `jellyscope/services.py`.
* `settings` - on, address and key, "connected", the connection test; the switch and the interval of
  measuring the indexers' status in the background (`watch_settings`, `save_watch_settings`).
* `sync` - Prowlarr's history into hourly sums (`prowlarr_hour`) and the list of grabbed releases
  (`prowlarr_grab`), the indexers' names, deleting the data.
* `live` - the live state: the indexers and which of them Prowlarr leaves out right now, the connected
  apps, the warnings; a one-minute cache. From the same answers a sample of the indexers' status
  (`take_sample` after the *Arr sync task, at most every ten minutes for a page).
* `samples` - the samples of the indexers' status (`prowlarr_sample`): storing them, the history
  retention (`delete_old_samples`), the usual time between two of them (`sample_interval`), the marker
  of a stored change (`status_marker`), how the status is measured right now (`how_measured`).
* `watch` - the background loop that measures the indexers' status for as long as the app runs
  (`watch_indexers_forever`, started by web/start.py).

Which name lives in which file is guarded by tests/test_prowlarr_package.py.
"""
from __future__ import annotations

from .client import KINDS, NAME, ProwlarrClient, ProwlarrError
from .live import live_state, take_sample
from .samples import delete_old_samples, how_measured, sample_interval, status_marker
from .settings import check_connection, is_connected, save_settings, save_watch_settings, stored_settings, watch_settings
from .sync import (
    delete_data,
    delete_downloaded_data,
    event_count,
    event_from_history,
    sync_running,
    sync_state,
    synchronize,
)
from .watch import watch_indexers_forever

__all__ = [
    "KINDS",
    "NAME",
    "ProwlarrClient",
    "ProwlarrError",
    "check_connection",
    "delete_data",
    "delete_downloaded_data",
    "delete_old_samples",
    "event_count",
    "event_from_history",
    "how_measured",
    "is_connected",
    "live_state",
    "sample_interval",
    "save_settings",
    "save_watch_settings",
    "status_marker",
    "stored_settings",
    "sync_running",
    "sync_state",
    "synchronize",
    "take_sample",
    "watch_indexers_forever",
    "watch_settings",
]
