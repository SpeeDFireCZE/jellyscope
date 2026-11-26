"""The base of the card registry: what a card is and what it has at hand when it computes data.

The registry (jellyscope/cards.py) puts `ALL_CARDS` together from the cards of several
modules - its own and the cards of the apps (cards_<app>.py: the apps of the *Arr
and Seerr pages). All of them need the same: the class `Card`, the memo of one
computation, the choices from the session, the account it is computed for, a helper
for live data and a duration for people (`duration`). Were it to stay in cards.py,
the card modules would have to import it and it imports them - a circle. So the base
is here, below all of them, and knows no card module itself.

How a card computes:

* `data(period)` returns a dict for the template. Old cards put their keys straight
  into it (`{"top_users": ...}`). A card that exists more than once (Sonarr and
  Radarr have the same cards) returns everything under **its own key**
  (`{"arr_sonarr_numbers": {...}}`) and the template reads it as `d` - see
  templates/_card.html. Two apps thus overwrite nothing of each other in one dashboard.
* A card asks about the account through `current_account()`, `current_is_admin()` and
  `current_sees()`. Without an account the least rights hold: no names, no texts from
  the app.
* A card takes the live state (the app's async client) through `live()`.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

from . import access, formatting
from .i18n import translate as _t

T = TypeVar("T")

# The memo of one data_for(): cards that need the same thing (the users table for
# the chart and the table, the language colours for four language cards, the events
# of one *Arr app for twenty cards) compute it once. A context variable, not a global
# dict: data_for runs in the threads of concurrent requests and each needs its own.
_memo: ContextVar[dict[Any, Any] | None] = ContextVar("cards_memo", default=None)

# The card choices from the session (Movies/Series on Watching by day and Most
# watched) the route computed - see cards.data_for and choice().
_choices: ContextVar[Mapping[str, str] | None] = ContextVar("cards_choices", default=None)

# The account data_for is computing for right now - see current_account().
_account: ContextVar[dict[str, Any] | None] = ContextVar("cards_account", default=None)


def choice(key: str, default: str) -> str:
    """A card's choice (`kind`, `top_kind`) the route passed to data_for.

    The session remembers it (web._kind), so it holds the same on the Overview and
    in the custom dashboard - it is the same card. Outside data_for, or when the
    route passed nothing, the default.
    """
    return str((_choices.get() or {}).get(key) or default)


def _once(function: Callable[..., Any], *args: Any) -> Any:
    """`function(*args)` - computed only once within one data_for().

    The result is shared then, so it must not be changed in place. It is not:
    data_for only pours it into the context and anonymising makes copies. Outside
    data_for, or with an argument that cannot be used as a key, the function is
    simply called.
    """
    memo = _memo.get()
    key = (function, args)
    try:
        hash(key)
    except TypeError:
        memo = None
    if memo is None:
        return function(*args)
    if key not in memo:
        memo[key] = function(*args)
    return memo[key]


def current_account() -> dict[str, Any] | None:
    """The account it is computing for right now (the route passed it to data_for).

    Outside data_for, or when the route passed none, `None` - and for `None` the
    least rights hold (see current_is_admin and current_sees). A card is thus never
    computed "for everyone" only because somebody forgot the account.
    """
    return _account.get()


def current_is_admin() -> bool:
    """Is it computing for an administrator? Texts from the app (errors, warnings) are only for them."""
    return access.role(current_account()) == access.ADMIN


def current_sees(area: str) -> bool:
    """Does the account it is computing for see the area of the rights tree? Without an account no.

    For cards that show part of their content only with a sub-area (the requesters'
    names in the Seerr cards only with "...including who requested").
    """
    return access.account_sees(current_account(), area)


# From how many seconds a duration is written in days. An episode that came out years
# ago and came into the library today would otherwise stand in a tile as "136 089 h".
DAYS_FROM = 2 * 24 * 3600


def duration(seconds: float | None) -> str:
    """A duration for people: up to two days in hours and minutes, then in days. Unknown = –."""
    if seconds is None:
        return "–"
    if seconds < DAYS_FROM:
        return formatting.seconds_human(seconds)
    return _t("{n} d").format(n=formatting.number(round(seconds / 86400)))


def per_item(average: float) -> str:
    """An average per item to one decimal place ("1,5"). It tends to be between 1 and 2,
    so the `number` filter (a whole number) would always make "1" of it."""
    return formatting.number_in_unit(float(average)) if average else "0"


# A lock on each live query (function + arguments), see live(). There are few keys:
# the apps that can be connected.
_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()
# How long the lock is waited for at most (seconds). More than the time limits of the
# apps' clients, so a view usually gets its turn. Were a query to get stuck entirely,
# every further view asks by itself after this time - as without the lock.
MAX_WAIT = 30.0


def _lock(coroutine: Callable[..., Awaitable[Any]], args: tuple[Any, ...]) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault((coroutine, args), threading.Lock())


def _run(coroutine: Callable[..., Awaitable[Any]], args: tuple[Any, ...]) -> Any:
    try:
        lock = _lock(coroutine, args)
    except TypeError:               # an argument that cannot be used as a key: without the lock
        return asyncio.run(coroutine(*args))
    acquired = lock.acquire(timeout=MAX_WAIT)
    try:
        return asyncio.run(coroutine(*args))
    finally:
        if acquired:
            lock.release()


def live(coroutine: Callable[..., Awaitable[T]], *args: Any) -> T:
    """The result of an async function (`await coroutine(*args)`) for a synchronous card.

    Cards are computed in a thread (the route is a plain function, or calls data_for
    through `asyncio.to_thread`), where no asyncio loop runs - `asyncio.run` starts
    its own here. In one data_for it asks only once for the same arguments: four live
    cards of one app share one state.

    Across views only one thread asks for the same thing at a time; the others wait
    until it finishes and call the function after it. The function should therefore
    have its own cache (live_state of the *Arr apps and seerr.pending have it, for a
    failure too): the waiting ones take the finished result and an app that accepts
    the connection and does not answer does not get queries from everyone who opened
    the page meanwhile. It waits at most MAX_WAIT: a query that got stuck entirely must
    not lock the card for everyone else.

    It must not be called from an asyncio loop (`asyncio.run` does not work in it and
    waiting would stop it for everyone): that is an error at once, not a stuck page.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _once(_run, coroutine, args)
    raise RuntimeError("live() is called from a thread, not from the asyncio loop: run data_for through asyncio.to_thread")


# What a card's data come from: by a change of which the card shows something else (Card.sources). Every
# source but "live" has a marker in /health (web/status.py), so an open page knows which cards to redraw:
# "plays" moves when a play starts or ends (and once a minute while something plays), "library" with the
# library's version, a task's kind when a run of the task finishes or "Sync now" in Settings ran. "live" is
# an app's live state - it has no marker, its cache is fresh for a minute.
PLAYS, LIBRARY, LIVE = "plays", "library", "live"
TASK_SOURCES = ("arr", "seerr", "music", "books")
SOURCES = (PLAYS, LIBRARY, *TASK_SOURCES, LIVE)

# The sources of a card that names none, by the menu section of its area: (with a period, without one). A card
# with a period counts what happened in it - an app's stored history, otherwise plays; a card without a period
# shows a state - an app's live state, otherwise a library. The Library section is the library either way (its
# disks and growth have a period, the snapshots behind them are written by the library's sync).
_SOURCES_BY_SECTION = {access.ARR: ("arr", LIVE), access.SEERR: ("seerr", LIVE), access.MUSIC: (PLAYS, "music"),
                       access.BOOKS: (PLAYS, "books"), access.LIBRARY: (LIBRARY, LIBRARY)}
_SOURCES_ELSEWHERE = (PLAYS, LIBRARY)


def default_sources(area: str, uses_period: bool) -> tuple[str, ...]:
    """The sources of a card that names none - from its area's section and whether it has a period."""
    section = getattr(access.BY_KEY.get(area), "section", "")
    with_period, without = _SOURCES_BY_SECTION.get(section, _SOURCES_ELSEWHERE)
    return (with_period if uses_period else without,)


@dataclass(frozen=True)
class Card:
    """One card that can be rearranged.

    `data` gets the chosen period and returns a dict that can be poured straight
    into the template's context. The keys must differ across cards - thanks to that
    the templates need not change and work in both places.
    """

    key: str
    name: str
    description: str
    template: str
    data: Callable[[Any], dict[str, Any]]
    # Does the card react to the period filter at the top? "Now playing" and
    # "Recently added" are not about it - it is a state, not a period.
    uses_period: bool = True
    # How wide a panel: a third, a half, the full width. The registry holds what suits
    # the card; a person can switch it when editing the dashboard.
    width: str = "full"
    # Wrap the rendered template in a card? Most templates draw their card
    # themselves; a few are written as the *inside* of a card, because on their
    # original page they are put into an already open <div class="card">.
    wrap_in_card: bool = False
    # The area of the rights tree (access.AREAS) the card belongs under - that of the
    # page it comes from. Whoever does not see the area does not get the card even in
    # a dashboard (see cards.may_see). Required and keyword-only: a card without it is an
    # error already at start, not a quietly open hole.
    area: str = field(kw_only=True)
    # Does the card have a "show the whole table" button? Then the dashboard needs the
    # shared window table_window() (see _macros.html), like the Insights page.
    has_table_window: bool = field(default=False, kw_only=True)
    # The card's live wrapper - _card.html draws it, the same on the Overview and in
    # the custom dashboard. `wrapper_id` is looked for by static/js/app.js (the
    # Movies/Series switch, Recently added); a card is on a page at most once, so the
    # id stays unique. `refresh_partial` = the slice fetched again in the collector's
    # rhythm; `after_sync` = the wrapper carries the library's version and is replaced
    # after a synchronisation.
    wrapper_id: str | None = field(default=None, kw_only=True)
    refresh_partial: str | None = field(default=None, kw_only=True)
    after_sync: bool = field(default=False, kw_only=True)
    # The group of one app's cards ("Sonarr", "Radarr", "Seerr"). It is the heading in
    # the cards menu and the prefix of the name everywhere outside the app's own page
    # ("Sonarr · Indexery") - without it two cards "Indexers" would stand in a dashboard
    # and it could not be told whose is which. The app's own name, not translated.
    # Empty = a card of no app; in the menu it then stands under the name of its area.
    group: str = field(default="", kw_only=True)
    # Is what the card reads from available (a connected app)? A function without a
    # parameter, `None` = always. An unavailable card is not offered and the dashboard
    # for everyone does not draw it; the saved layout is not deleted because of it -
    # once the app is connected, the card is back (see cards.is_available).
    available: Callable[[], bool] | None = field(default=None, kw_only=True)
    # What the card's data come from (SOURCES): an open page redraws the card when one of them moved. Empty =
    # derived from the area and the period (default_sources); a card that mixes two says so itself.
    sources: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if not self.sources:
            object.__setattr__(self, "sources", default_sources(self.area, self.uses_period))
        unknown = sorted(set(self.sources) - set(SOURCES))
        if unknown:
            # Not `assert`, like the registry's other checks: a typo must stop the start, not a card's refresh.
            raise ValueError(f"the card {self.key} names unknown sources: {unknown}")
