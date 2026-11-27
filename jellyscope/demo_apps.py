"""The demo's made-up apps: registering them, connecting and syncing them in the seed, deleting their data.

The demo (JELLYSCOPE_DEMO) shows the *Arr apps and Seerr with made-up data through the real code. Every app has
a made-up handler in its own package (the registry's `App.demo`, `seerr/demo.py`), Jellyfin one for what the
apps' syncs ask it (`demo_jellyfin.py`). Three steps:

* `load_fakes` registers them in the switch (demo_switch): from then on the demo's clients get them in place of
  the network. The web's start calls it before anything else, the seed too (it runs before the server);
* `seed_apps` (from demodata.seed, after the video, music and books) connects the made-up Jellyfin (Sonarr's
  sync pairs its series with Jellyfin's) and every app with a made-up one registered, and runs the apps' real
  sync and counts - the same the *Arr task does. What no sync fetches, because Jellyscope itself measured it over
  the past, its made-up module fills then (`fill_past`, when it has one): Prowlarr's samples of the indexers' status,
  so the availability has the period's history; what Seerr's earlier syncs saw (a request waiting, then approved
  or declined), so the time to the approval is known;
* `wipe_apps` (from demodata.refresh, before the made-up library goes) deletes what every connected app
  downloaded, through its own `delete_downloaded_data`.

`summary` tells the start's printout (demo.py) what the connected apps hold.

Only for the demo mode: outside it all three (and `summary`) do nothing, so a real installation never connects a
made-up app nor loses what it downloaded. A made-up module that does not exist (yet) is skipped.

Shared code: it names no app's table - it asks the registry and Seerr's door (tests/test_arr_tree.py). The
asynchronous parts run in a loop of their own (asyncio.run): the seed runs before the server (run.py, demo.py)
and in the daily refresh's thread.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from . import db, demo_switch, demo_world, seerr, services
from .arr import registry
from .config import load_config
from .jellyfin import DEMO_NAME as JELLYFIN

log = logging.getLogger(__name__)

# The made-up address of an app. Nothing is ever sent there: the switch answers inside the process.
ADDRESS = "http://{key}.demo"
# Jellyfin's key in the address and the made-up API key: http://jellyfin.demo.
JELLYFIN_KEY = "jellyfin"


def api_key(app_key: str) -> str:
    """The made-up API key of an app: of a real key's shape (32 hex characters), the same at every seed."""
    return demo_world.jellyfin_id("key-" + app_key)


@dataclass(frozen=True)
class _DemoApp:
    """One app the demo can connect - an *Arr app of the registry, or Seerr - with the functions the demo needs."""

    key: str                                            # the settings and the address: "sonarr"
    name: str                                           # the client's name = the switch's key: "Sonarr"
    stored_settings: Callable[[], dict[str, Any]]
    save_settings: Callable[[bool, str, str], str]
    synchronize: Callable[[], Awaitable[Any]]
    refresh_counts: Callable[[], Awaitable[None]] | None
    count: Callable[[], int]                            # how many records are stored
    delete_downloaded_data: Callable[[], Awaitable[int]]
    module: str                                         # its made-up module ("arr.prowlarr.demo"), for `fill_past`


def _apps() -> list[_DemoApp]:
    """Every app the demo can connect: the registry's in its order, Seerr last. Looked up at every call (tests substitute)."""
    apps = [_DemoApp(app.key, app.name, app.stored_settings, app.save_settings, app.synchronize, app.refresh_counts,
                     app.event_count, app.delete_downloaded_data, f"arr.{app.key}.demo") for app in registry.APPS.values()]
    apps.append(_DemoApp("seerr", seerr.NAME, seerr.stored_settings, seerr.save_settings, seerr.synchronize, None,
                         seerr.request_count, seerr.delete_downloaded_data, "seerr.demo"))
    return apps


def _optional(module: str) -> ModuleType | None:
    """A made-up module of the package ("demo_jellyfin", "seerr.demo"), or None while it does not exist."""
    name = f"{__package__}.{module}"
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)


def load_fakes() -> None:
    """Register every made-up app that exists in the switch: Jellyfin, every app with `demo=` in the registry, Seerr.

    Calling it again registers the same again. Outside the demo mode nothing.
    """
    if not load_config().demo_mode:
        return
    jellyfin = _optional("demo_jellyfin")
    if jellyfin is not None:
        demo_switch.register(JELLYFIN, jellyfin.handler)
    for app in registry.APPS.values():
        if app.demo is not None:
            demo_switch.register(app.name, app.demo())
    made_up_seerr = _optional("seerr.demo")
    if made_up_seerr is not None:
        demo_switch.register(seerr.NAME, made_up_seerr.handler)


def seed_apps() -> dict[str, int]:
    """Connect every app with a made-up one registered and fill it through its real code. Returns {key: records}.

    First the made-up Jellyfin, when it is registered: its address and key replace whatever is stored (an
    address from .env, or none - with none Jellyfin's client cannot even ask). Then for every app: its settings
    (on, the address http://<key>.demo, a made-up key), its sync, its slow counts (refresh_counts) and what its
    made-up module fills of the past (`fill_past`, when it has one). One app's failure is one line in the log and
    the others go on; the failed app is left out of the counts.
    Outside the demo mode nothing ({}).
    """
    if not load_config().demo_mode:
        return {}
    if demo_switch.registered(JELLYFIN):
        db.set_setting("jellyfin_url", ADDRESS.format(key=JELLYFIN_KEY))
        db.set_setting("jellyfin_api_key", api_key(JELLYFIN_KEY))
    apps = [app for app in _apps() if demo_switch.registered(app.name)]
    return asyncio.run(_seed(apps)) if apps else {}


async def _seed(apps: list[_DemoApp]) -> dict[str, int]:
    """seed_apps inside its loop: app by app."""
    counts: dict[str, int] = {}
    for app in apps:
        try:
            refused = app.save_settings(True, ADDRESS.format(key=app.key), api_key(app.key))
            if refused:
                raise services.ServiceError(refused)
            await app.synchronize()
            if app.refresh_counts is not None:
                await app.refresh_counts()
            fill_past = getattr(_optional(app.module), "fill_past", None)
            if fill_past is not None:
                await asyncio.to_thread(fill_past)
            counts[app.key] = app.count()
        except Exception as error:   # noqa: BLE001 - one made-up app must not keep the demo from starting
            log.warning("%s sync failed: %s", app.name, error)
    return counts


def summary() -> dict[str, int]:
    """What the connected made-up apps hold, for the start's printout (demo.py): {name: records}, in the seed's order.

    Read from the database, so it also tells what an earlier start made. An app whose seed failed is connected with
    what it has (often 0). Outside the demo mode nothing ({}): a real installation's apps are not the demo's.
    """
    if not load_config().demo_mode:
        return {}
    return {app.name: app.count() for app in _apps() if services.is_connected(app.stored_settings())}


def wipe_apps() -> None:
    """Delete what every connected app downloaded (its `delete_downloaded_data`). Outside the demo mode nothing.

    One app's failure is one line in the log and the others go on.
    """
    if not load_config().demo_mode:
        return
    apps = [app for app in _apps() if services.is_connected(app.stored_settings())]
    if apps:
        asyncio.run(_wipe(apps))


async def _wipe(apps: list[_DemoApp]) -> None:
    """wipe_apps inside its loop: app by app."""
    for app in apps:
        try:
            await app.delete_downloaded_data()
        except Exception as error:   # noqa: BLE001 - the others' data still go, the seed comes after
            log.warning("demo: the downloaded data of %s could not be deleted: %s", app.name, error)
