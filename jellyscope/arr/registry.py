"""The apps of the *Arr page in one place.

The *Arr page has a tab for every connected app, Settings → *Arr a card for every app, and the task
"Synchronizace *Arr" syncs them all. The web and the task used to have Sonarr and Radarr written in;
now they ask this registry. Another app is only added here (Bazarr is the third, Prowlarr the fourth)
- with functions that do the same for it: save the settings, test the connection, sync, tell the
state, delete the downloaded data, give the cards.

Every app is an `App`: a few facts and functions without the app argument (it is filled in already).
The order in the dict `APPS` is the order of the tabs and of the cards in Settings.

The functions are looked up only when called (`_later`), not on import: tests substitute the apps'
package functions (`_package.substitute`) and a reference stored on import would point at the
original.

The registry is the only place that knows every app. Hence a module of its own that the door of the
package `arr` does not import: whoever takes one app (`from jellyscope.arr import bazarr`) does not
load the others with it.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .. import cards_bazarr, cards_lidarr, cards_prowlarr, cards_radarr, cards_readarr, cards_sonarr
from ..cards_base import Card
from ..services import SYNC_ALREADY_RUNNING, ServiceError, is_connected
from . import bazarr, lidarr, prowlarr, radarr, readarr, sonarr

log = logging.getLogger("jellyscope.arr")

# The error "the sync is already running" (shared by every app, services.py) - the task does not take it as an error.
ALREADY_RUNNING = SYNC_ALREADY_RUNNING


@dataclass(frozen=True)
class App:
    """One app of the *Arr page: what the web and the task need to know about it and to be able to do."""

    key: str                                                # part of the address and of the settings keys: "sonarr"
    name: str                                               # "Sonarr" (not translated)
    stored_settings: Callable[[], dict[str, Any]]           # {"enabled", "url", "has_key"}
    save_settings: Callable[[bool, str, str], str]          # (enabled, address, key) -> an error, or ""
    check_connection: Callable[[str, str], Awaitable[tuple[bool, str]]]  # (address, key) -> (ok, the version or an error)
    synchronize: Callable[[], Awaitable[dict[str, Any]]]    # {"new_count": n, …}; an error = ServiceError
    sync_running: Callable[[], bool]                        # is a sync running right now?
    sync_state: Callable[[], dict[str, Any]]                # the state of the last sync
    event_count: Callable[[], int]                          # how many downloaded records are in the database
    delete_downloaded_data: Callable[[], Awaitable[int]]    # deletes the downloaded data, returns the count
    cards: Callable[[], tuple[Card, ...]]                   # the app's cards from the registry
    card_key: Callable[[str], str]                          # a card's name -> its key in the card registry
    sections: tuple[tuple[str, tuple[str, ...]], ...]       # the headings of the page's sections and their cards' names
    template: str                                           # the template of the app's page
    period: Callable[[Any], Any]                            # the cards' period ("the whole history" of the app)
    sample_url: str                                         # the hint in the address field
    key_hint: str                                           # where to find the key in the app (a translation key)
    already_running: str = ALREADY_RUNNING                  # the template of the error "the sync is already running"
    # What else to say in the dialog "Delete downloaded data" (delete downloaded data; a translation key); empty = nothing.
    delete_note: str = ""
    # Only apps that download (the card "Downloading"); the others do not have them and the web answers 404.
    # The provisional poster of a title being downloaded: (title id) -> (content, image type), or None.
    provisional_poster: Callable[[int], Awaitable[tuple[bytes, str] | None]] | None = None
    # The data of the slice of the card "Downloading" for an admin (True) or the others (False), with `refresh`.
    downloading: Callable[[bool], Awaitable[dict[str, Any]]] | None = None
    downloading_template: str = ""                          # the template of that slice
    # The name of the app that really answers when it can differ from `name` (Readarr, Chaptarr or Bookshelf).
    detected_name: Callable[[], str] | None = None

    def shown_name(self) -> str:
        """The name people see - in the *Arr menu, on the tab and in Settings: the app that answers, else `name`
        (the user, 23. 11. 2026: "Readarr = readarr, Chaptarr = chaptarr")."""
        return (self.detected_name() if self.detected_name else "") or self.name


def _later(package: Any, name: str, *bound: Any) -> Callable[..., Any]:
    """A package function looked up only when called, with the `bound` arguments filled in."""
    def call(*args: Any, **kwargs: Any) -> Any:
        return getattr(package, name)(*bound, *args, **kwargs)
    return call


def _slice_data(package: Any, cards: Any) -> Callable[[bool], Awaitable[dict[str, Any]]]:
    """The data of one app's slice of the card "Stahuje se": the queue's state (the package) and the template's data from it (the cards)."""
    async def data(admin: bool) -> dict[str, Any]:
        state = await package.download_state()
        # Looking up titles in the library is a database query - in a thread, so the web runs meanwhile.
        return await asyncio.to_thread(cards.downloading, state, admin)
    return data


def _sonarr_app() -> App:
    """Sonarr: series. A package, tables and cards of its own."""
    return App(
        key="sonarr", name=sonarr.NAME,
        stored_settings=_later(sonarr, "stored_settings"),
        save_settings=_later(sonarr, "save_settings"),
        check_connection=_later(sonarr, "check_connection"),
        synchronize=_later(sonarr, "synchronize"),
        sync_running=_later(sonarr, "sync_running"),
        sync_state=_later(sonarr, "sync_state"),
        event_count=_later(sonarr, "event_count"),
        delete_downloaded_data=_later(sonarr, "delete_downloaded_data"),
        cards=lambda: cards_sonarr.CARDS,
        card_key=cards_sonarr.card_key,
        sections=cards_sonarr.SECTIONS,
        template="arr_sonarr.html",
        period=cards_sonarr.sonarr_period,
        sample_url="http://localhost:8989",
        key_hint="In the app: Settings → General → Security → API Key.",
        provisional_poster=_later(sonarr, "provisional_poster"),
        downloading=_slice_data(sonarr, cards_sonarr),
        downloading_template="_sonarr_downloading_body.html",
    )


def _radarr_app() -> App:
    """Radarr: movies. A package, tables and cards of its own."""
    return App(
        key="radarr", name=radarr.NAME,
        stored_settings=_later(radarr, "stored_settings"),
        save_settings=_later(radarr, "save_settings"),
        check_connection=_later(radarr, "check_connection"),
        synchronize=_later(radarr, "synchronize"),
        sync_running=_later(radarr, "sync_running"),
        sync_state=_later(radarr, "sync_state"),
        event_count=_later(radarr, "event_count"),
        delete_downloaded_data=_later(radarr, "delete_downloaded_data"),
        cards=lambda: cards_radarr.CARDS,
        card_key=cards_radarr.card_key,
        sections=cards_radarr.SECTIONS,
        template="arr_radarr.html",
        period=cards_radarr.radarr_period,
        sample_url="http://localhost:7878",
        key_hint="In the app: Settings → General → Security → API Key.",
        provisional_poster=_later(radarr, "provisional_poster"),
        downloading=_slice_data(radarr, cards_radarr),
        downloading_template="_radarr_downloading_body.html",
    )


def _lidarr_app() -> App:
    """Lidarr: music, the unit is the album. A package, tables and cards of its own."""
    return App(
        key="lidarr", name=lidarr.NAME,
        stored_settings=_later(lidarr, "stored_settings"),
        save_settings=_later(lidarr, "save_settings"),
        check_connection=_later(lidarr, "check_connection"),
        synchronize=_later(lidarr, "synchronize"),
        sync_running=_later(lidarr, "sync_running"),
        sync_state=_later(lidarr, "sync_state"),
        event_count=_later(lidarr, "event_count"),
        delete_downloaded_data=_later(lidarr, "delete_downloaded_data"),
        cards=lambda: cards_lidarr.CARDS,
        card_key=cards_lidarr.card_key,
        sections=cards_lidarr.SECTIONS,
        template="arr_lidarr.html",
        period=cards_lidarr.lidarr_period,
        sample_url="http://localhost:8686",
        key_hint="In the app: Settings → General → Security → API Key.",
        provisional_poster=_later(lidarr, "provisional_poster"),
        downloading=_slice_data(lidarr, cards_lidarr),
        downloading_template="_lidarr_downloading_body.html",
    )


def _readarr_app() -> App:
    """Readarr: books, the unit is the book (and its forks Chaptarr and Bookshelf). A package, tables and cards of its own."""
    return App(
        key="readarr", name=readarr.NAME,
        stored_settings=_later(readarr, "stored_settings"),
        save_settings=_later(readarr, "save_settings"),
        check_connection=_later(readarr, "check_connection"),
        synchronize=_later(readarr, "synchronize"),
        sync_running=_later(readarr, "sync_running"),
        sync_state=_later(readarr, "sync_state"),
        event_count=_later(readarr, "event_count"),
        delete_downloaded_data=_later(readarr, "delete_downloaded_data"),
        cards=lambda: cards_readarr.CARDS,
        card_key=cards_readarr.card_key,
        sections=cards_readarr.SECTIONS,
        template="arr_readarr.html",
        period=cards_readarr.readarr_period,
        sample_url="http://localhost:8787",
        key_hint="In the app: Settings → General → Security → API Key.",
        provisional_poster=_later(readarr, "provisional_poster"),
        downloading=_slice_data(readarr, cards_readarr),
        downloading_template="_readarr_downloading_body.html",
        detected_name=_later(readarr, "app_name"),
    )


def _bazarr_app() -> App:
    """Bazarr: a package, a table and cards of its own - for the web and the task the same functions as Sonarr's."""
    return App(
        key="bazarr", name=bazarr.NAME,
        stored_settings=_later(bazarr, "stored_settings"),
        save_settings=_later(bazarr, "save_settings"),
        check_connection=_later(bazarr, "check_connection"),
        synchronize=_later(bazarr, "synchronize"),
        sync_running=_later(bazarr, "sync_running"),
        sync_state=_later(bazarr, "sync_state"),
        event_count=_later(bazarr, "event_count"),
        delete_downloaded_data=_later(bazarr, "delete_downloaded_data"),
        cards=lambda: cards_bazarr.CARDS,
        card_key=cards_bazarr.card_key,
        sections=cards_bazarr.SECTIONS,
        template="arr_bazarr.html",
        period=cards_bazarr.bazarr_period,
        sample_url="http://localhost:6767",
        key_hint="In Bazarr: Settings → General → Security → API Key.",
    )


def _prowlarr_app() -> App:
    """Prowlarr: a package, tables and cards of its own - for the web and the task the same functions as the others'.

    Unlike them it keeps its history only for a limited time: after the downloaded data is deleted,
    only what it still has comes back from it. The deletion dialog says so.
    """
    return App(
        key="prowlarr", name=prowlarr.NAME,
        stored_settings=_later(prowlarr, "stored_settings"),
        save_settings=_later(prowlarr, "save_settings"),
        check_connection=_later(prowlarr, "check_connection"),
        synchronize=_later(prowlarr, "synchronize"),
        sync_running=_later(prowlarr, "sync_running"),
        sync_state=_later(prowlarr, "sync_state"),
        event_count=_later(prowlarr, "event_count"),
        delete_downloaded_data=_later(prowlarr, "delete_downloaded_data"),
        cards=lambda: cards_prowlarr.CARDS,
        card_key=cards_prowlarr.card_key,
        sections=cards_prowlarr.SECTIONS,
        template="arr_prowlarr.html",
        period=cards_prowlarr.prowlarr_period,
        sample_url="http://localhost:9696",
        key_hint="In the app: Settings → General → Security → API Key.",
        delete_note=("Prowlarr keeps its history only for a limited time (30 days by default). "
                     "Anything older will not come back after deleting."),
    )


APPS: dict[str, App] = {
    "sonarr": _sonarr_app(),
    "radarr": _radarr_app(),
    "lidarr": _lidarr_app(),
    "readarr": _readarr_app(),
    "bazarr": _bazarr_app(),
    "prowlarr": _prowlarr_app(),
}


def connected() -> list[str]:
    """The keys of the switched-on apps with both an address and a key, in the registry's order."""
    return [key for key, app in APPS.items() if is_connected(app.stored_settings())]


async def synchronize_all() -> dict[str, dict[str, Any]]:
    """Sync every connected app. An error of one does not stop the others.

    Returns {key: {"new_count", …}}, {key: {"error": the error}} (a ServiceError, kept as a key and values for
    the task log; another failure as its text), or {key: {"running": True}} when a
    sync of the same app is already running (the button in Settings and the task met - that is no
    error).
    """
    results: dict[str, dict[str, Any]] = {}
    for key in connected():
        app = APPS[key]
        try:
            results[key] = await app.synchronize()
        except ServiceError as error:
            if error.template == app.already_running:
                results[key] = {"running": True}
                continue
            log.warning("%s sync failed: %s", app.name, error)
            results[key] = {"error": error}
        except Exception as error:   # noqa: BLE001 - one app must not bring the others down
            log.warning("%s sync failed: %s", app.name, error)
            results[key] = {"error": str(error)}
    return results
