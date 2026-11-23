"""The Readarr cards: the Readarr tab of the *Arr page and the custom dashboards.

Each question is a card of its own in the registry (`cards.ALL_CARDS`): the page
`/arr/readarr` (web/arr.py, arr_readarr.html) and the custom dashboards draw them,
both the same way. Everything the cards know about Readarr is here and in the
templates `_card_readarr_*.html`; the other apps have their own.

What it stands on:

* **The unit is the book.** Readarr writes an import per file; the statistics put the
  files of one download together (stats/readarr.py), so a card counts books and says
  the files beside them.
* **The cards' keys** are `arr_readarr_<name>` (`arr_readarr_queue`). A card returns
  its data under its key and the template reads them as `d` (see cards_base.py) -
  the cards of several apps overwrite nothing of each other in one dashboard.
* **A card of a disconnected Readarr is not available** (`available`): it is neither
  offered nor computed. After connecting it is back.
* **The period's events are selected once.** Twenty-odd cards are only different views
  of the same selection (`stats.readarr_events`); the memo of one computation
  (`_once`) holds it, so the page and the dashboard make one query.
* **The live state** (queue, calendar, health) comes from `readarr.live_state` with a
  one-minute cache; a card takes it through `live()` and leaves the texts from the
  app only to the administrator (`for_account`).
* **A book that is in the books library is a link to its page** (`with_details`). It is paired by id
  (stats.readarr_library, by the ISBN or the title and the author - arr/readarr/titles.py), not by name, and the
  address is put together by the same helpers the books cards use (`cards_books.book_link`, `cover`). A book Readarr
  is only downloading (or the library has no books at all) stays without a link; whether the account may open the
  address is decided by the template (the `link` macro): a book (`/book/<id>`) belongs to the Library area, so
  without it the book is a text. The author is always a text - there is no author page.
* **The app's name** (Readarr, Chaptarr or Bookshelf - whichever answered the connection test) is in the tab's
  subtitle; the cards say "the app".

stats/readarr.py computes the numbers, here they are only put together for the
templates. Adding a card means adding a line to `CARDS` and its name to `SECTIONS`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from . import cards_books, formatting, i18n, insights, stats
from .arr import readarr
from .cards_base import Card, _once, current_is_admin, duration, live, per_item
from .i18n import translate as _t

# The sections of the page /arr/readarr and the names of their cards, in the order on
# the page. The heading is an English sentence = a translation key. The layout (pairs,
# triples side by side) is in arr_readarr.html; that the template draws exactly these
# cards in this order is guarded by tests/test_readarr_cards.py.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("In the app right now", ("live_numbers", "downloading", "queue", "calendar", "health")),
    ("What was added", ("numbers", "by_day", "when", "best_days", "largest")),
    ("Speed", ("downloads", "after_release")),
    ("Sources and quality", ("indexers", "groups", "clients", "protocols", "qualities")),
    ("Upgrades", ("upgrades", "how_all", "how_upgrades", "transitions", "upgraded")),
)

# How a grab started - the labels of the groups from stats.readarr_upgrades (`how`, `how_all`).
HOW_LABELS = {
    "automatic": "automatic (RSS and automatic search)",
    "user": "search started by a user",
    "manual": "manual release pick",
    "external": "pushed from outside (Prowlarr, autobrr)",
    "unknown": "unknown#neuter",
}
# The buckets "from release to import" (stats.readarr_speed) and their labels.
BUCKET_LABELS = {"1h": "within an hour", "6h": "within 6 hours", "1d": "within a day", "7d": "within a week", "longer": "longer"}
BEST_DAYS = 5
# Download states (arr/readarr/progress.py) for the "Downloading" card: the label and the badge's colour.
DOWNLOAD_STATES = {
    "downloading": ("downloading", "good"),
    "waiting_client": ("queued in the client", "neutral"),
    "paused": ("paused", "neutral"),
    "postponed": ("delayed", "neutral"),
    "client_not_responding": ("the download client is not responding", "warning"),
    "waiting_import": ("downloaded, waiting to be imported", "info"),
    "importing": ("being imported into the library", "info"),
    "stuck": ("import failed", "warning"),
    "failed": ("failed", "serious"),
    "warnings": ("warning", "warning"),
}
# How many rows of a long table a card gets at most. How many show right away is
# governed by Settings → Interface → Long lists; a window opens the rest.
ROWS = insights.INSIGHT_ROWS


def card_key(name: str) -> str:
    """The card's key in the registry: `arr_readarr_<name>` - stored in the layouts of dashboards."""
    return f"arr_readarr_{name}"


def for_account(state: dict[str, Any], admin: bool) -> dict[str, Any]:
    """The live state as the account may see it. Returns a copy - the cache is shared.

    The texts from the app belong to the administrator, who can do something with
    them: the error's text, the health messages ("Unable to communicate with
    qBittorrent at http://192.168.1.5:8080") and the messages at queue items (paths
    on disk). The others see that something is there - the count of warnings, the
    books and the state in the queue - but not the addresses and paths from the
    internal network.
    """
    health = state["health"]
    # The counts of missing and cutoff come from the sync (None = not counted yet): no part of the live load.
    result = {**state, "health_count": len(health) if health is not None else None,
              "partly_loaded": bool(state["available"] and (state["error"] or None in (
                  state["queue"], state["calendar"], health)))}
    if admin:
        return result
    result["error"] = ""
    result["health"] = [] if health is not None else None
    if state["queue"] is not None:
        result["queue"] = {**state["queue"], "with_error": [{**p, "message": ""} for p in state["queue"]["with_error"]]}
    return result


# ---------------------------------------------------------------------------
# Shared intermediate results - computed once in one data_for() (_once)
# ---------------------------------------------------------------------------

def readarr_period(period: Any) -> Any:
    """The period of the Readarr cards: "the whole history" starts with its first event.

    Readarr's history usually reaches far before the first playback in Jellyscope (and
    without a playback "the whole history" would be only today). It is computed once
    per computation and all cards get the same object - it is the key under which the
    memo of the computation holds their shared intermediate results.
    """
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.readarr_first())
    return period


def with_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with the address of the book's page in the books library (`detail_url`; None = not there).

    Returns copies: the rows come from shared results (the memo of the computation,
    the cache of the live state). Where the link leads stats.readarr_library knows;
    whether the account may open them the template decides (the link macro).
    """
    # Only the books of these rows: Readarr's library can have thousands of books
    # and the card draws a few dozen.
    library = stats.readarr_library({row.get("title_id") for row in rows})
    return [{**row, "detail_url": cards_books.book_link(library.get(row.get("title_id"), {}).get("book_id"))} for row in rows]


def file_count(count: int) -> str:
    """"1 file", "3 files"."""
    return formatting.plural("{n} files", count)


def _downloads_for_template(item: dict[str, Any]) -> dict[str, Any]:
    """A block or a download with texts for the template: the state and the details under the progress bar.

    `details` are finished pieces of the line under the bar (how much is downloaded,
    how much is left, what downloads it) - the template only joins them with a
    separator, so the line does not start with a separator even when one is missing
    (a torrent only fetching metadata has neither a size nor a percentage).
    """
    label, css_class = DOWNLOAD_STATES.get(item["state"], (item["state"], "neutral"))
    details = []
    if item["percent"] is not None:
        size = formatting.bytes_human(item["size_bytes"])
        # What has not started yet writes only the size: "0 B of 2 GB" says nothing.
        details.append(_t("{downloaded} of {total}").format(downloaded=formatting.bytes_human(item["grabbed"]), total=size)
                       if item["grabbed"] else size)
    if item["seconds_left"] is not None:
        details.append(f"{_t('left')} {duration(item['seconds_left'])}")
    if item["client"]:
        details.append(item["client"] + (f" ({item['protocol']})" if item["protocol"] else ""))
    return {**item, "state_text": _t(label) if item["state"] in DOWNLOAD_STATES else label, "state_class": css_class,
            "details": details}


def downloading(state: dict[str, Any], admin: bool) -> dict[str, Any]:
    """The data of the body of the "Downloading" card from the queue's state (readarr.download_state).

    Blocks get a link into the library and a cover: a book from the library has
    the one from Jellyfin (/image/, the same file as the books pages), the others a
    provisional one from Readarr (/arr/readarr/poster/<id>); a download Readarr did not
    pair, none. A block with more downloads gets the id of a window with the
    breakdown. The error's detail (the app's internal address) only for the
    administrator - it is called from the card and from the slice, so who it is put
    together for is said from outside.
    """
    library = stats.readarr_library({block["title_id"] for block in state["blocks"]})
    blocks = []
    for block in state["blocks"]:
        in_library = library.get(block["title_id"]) or {}
        cover = cards_books.cover(in_library.get("book_id"), in_library.get("cover_tag"))
        if cover:
            poster = cover
        elif block["title_id"] is not None and not readarr.poster_missing(block["title_id"]):
            poster = f"/arr/readarr/poster/{block['title_id']}"
        else:               # a download Readarr did not pair, or a cover known not to exist: a substitute
            poster = ""
        several = block["count"] > 1 and block["title_id"] is not None
        count_text = i18n.translate_plural("{n} downloads", block["count"]) if several else ""
        shared = i18n.translate_plural("part of a download of {n} books", block["download_books"]) if block["download_books"] > 1 else ""
        blocks.append({**_downloads_for_template(block), "poster": poster, "shared_text": shared, "detail_url": cards_books.book_link(in_library.get("book_id")),
                       "window": f"modal-downloading-readarr-{block['title_id']}" if several else "", "count_text": count_text,
                       "downloads": [_downloads_for_template(s) for s in block["downloads"]]})
    more = formatting.plural("And {n} more titles in the queue.", state["more_count"]) if state["more_count"] else ""
    return {"app": "readarr", "available": state["available"], "error": state["error"] if admin else "", "blocks": blocks,
            "more_count": more, "incomplete": state["incomplete"], "refresh": state["refresh"]}


async def _right_now() -> tuple[dict[str, Any], dict[str, Any]]:
    """The live state and the downloads at once: a page waits for the slower of the two, not for both in a row.

    Both keep their own caches and in-flight guards; the cards read them through one live() call (cards_base: one
    computation per page).
    """
    state, downloads = await asyncio.gather(readarr.live_state(), readarr.download_state())
    return state, downloads


def _d_downloading() -> dict[str, Any]:
    return downloading(live(_right_now)[1], current_is_admin())


def _live() -> dict[str, Any]:
    """Readarr's live state for the account it is computed for - books with a link into the library."""
    state = for_account(live(_right_now)[0], current_is_admin())
    if state["queue"] is not None:
        state["queue"] = {**state["queue"], "with_error": with_details(state["queue"]["with_error"])}
    if state["calendar"] is not None:
        state["calendar"] = with_details(state["calendar"])
    return state


def _events(period: Any) -> dict[str, Any]:
    return _once(stats.readarr_events, period)


def _additions(period: Any) -> list[dict[str, Any]]:
    return stats.readarr_additions(period, analysis=_events(period))


def _speed(period: Any) -> dict[str, Any]:
    return stats.readarr_speed(period, analysis=_events(period))


def _sources(period: Any) -> dict[str, list[dict[str, Any]]]:
    return stats.readarr_sources(period, analysis=_events(period))


def _upgrades(period: Any) -> dict[str, Any]:
    return stats.readarr_upgrades(period, analysis=_events(period), titles=ROWS)


def _how_rows(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Rows for the bar chart "how it was downloaded", in a fixed order of the groups."""
    return [{"label": _t(label), "count": counts.get(how, 0)}
            for how, label in HOW_LABELS.items() if counts.get(how)]


# ---------------------------------------------------------------------------
# The data of the single cards: period -> a dict the template reads as `d`
# ---------------------------------------------------------------------------

def _d_numbers(period: Any) -> dict[str, Any]:
    additions = _once(_additions, period)
    return {"count": sum(r["count"] for r in additions), "new_count": sum(r["new_count"] for r in additions),
            "upgrade_count": sum(r["upgrade_count"] for r in additions), "size_bytes": sum(r["size_bytes"] for r in additions),
            "files": file_count(sum(r["file_count"] for r in additions)), "failures": stats.readarr_failures(period)}


def _d_by_day(period: Any) -> dict[str, Any]:
    return {"body": stats.chart_days(_once(_additions, period), period, ("new_count", "upgrade_count"))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.readarr_heatmap(period, analysis=_events(period))}


def _d_best_days(period: Any) -> dict[str, Any]:
    additions = _once(_additions, period)
    return {"rows": sorted(additions, key=lambda r: (-r["count"], -r["size_bytes"], r["day"]))[:BEST_DAYS]}


def _d_largest(period: Any) -> dict[str, Any]:
    return {"rows": with_details(stats.readarr_largest(period, limit=ROWS, analysis=_events(period)))}


def _d_downloads(period: Any) -> dict[str, Any]:
    downloads = _once(_speed, period)["downloads"]
    return {"median": duration(downloads["median"]), "p90": duration(downloads["p90"]), "count": downloads["count"]}


def _d_after_release(period: Any) -> dict[str, Any]:
    since_release = _once(_speed, period)["since_release"]
    return {"median": duration(since_release["median"]), "p90": duration(since_release["p90"]), "count": since_release["count"],
            "before_release": since_release["before_release"],
            # Sentences under the tiles: how many imported books are not counted and why. A book added after its
            # release and a book for which it is not known when it was added are two different things.
            "not_counted": formatting.plural(
                "{n} imported books are not counted: they were added to the app after their release.",
                since_release["not_counted"]) if since_release["not_counted"] else "",
            "unknown": formatting.plural(
                "For {n} imported books it is not known when they were added to the app; they are not counted either.",
                since_release["unknown"]) if since_release["unknown"] else "",
            "buckets": [{"label": _t(BUCKET_LABELS[k]), "count": n} for k, n in since_release["buckets"].items()]}


def _d_source(group: str) -> Callable[[Any], dict[str, Any]]:
    """The data of one of the five source cards (indexers, groups, clients…) - the same template, another group."""
    def data(period: Any) -> dict[str, Any]:
        rows = _once(_sources, period)[group]
        # The share is of the count of imported books; each book is in the group exactly once.
        return {"rows": rows[:ROWS], "import_count": sum(r["count"] for r in rows)}
    return data


def _d_upgrades(period: Any) -> dict[str, Any]:
    upgrades = _once(_upgrades, period)
    return {"count": upgrades["count"], "item_count": upgrades["item_count"], "per_item": per_item(upgrades["per_item"])}


def _d_how(which: str) -> Callable[[Any], dict[str, Any]]:
    """The data of the card "how it was downloaded": `how_all` all grabs, `how` only those that brought an upgrade."""
    def data(period: Any) -> dict[str, Any]:
        return {"rows": _how_rows(_once(_upgrades, period)[which])}
    return data


def _d_transitions(period: Any) -> dict[str, Any]:
    return {"rows": _once(_upgrades, period)["transitions"][:ROWS]}


def _d_upgraded(period: Any) -> dict[str, Any]:
    return {"rows": with_details(_once(_upgrades, period)["titles"])}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _card(name: str, title: str, description: str,
          data: Callable[[Any], dict[str, Any]] | None = None, *,
          template: str | None = None, width: str = "full", table_window: bool = False,
          live_data: Callable[[], dict[str, Any]] | None = None) -> Card:
    """One Readarr card.

    `data(period)` returns a dict for the template; the registry gets it under the
    card's key. Without `data` the card is live: it reads Readarr's state now, the
    period is not its business - the shared live state (`_live`), or its own
    (`live_data()`, the "Downloading" card). The template is
    `_card_readarr_<name>.html`, unless said otherwise (the five source cards and the
    two "how it was downloaded" cards share one each).
    """
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        if data is None:
            # Once per computation: four live cards read the same state
            # (and the same lookup of the queue's and the calendar's books in the library).
            return {key: _once(live_data or _live)}
        return {key: data(_once(readarr_period, period))}

    return Card(key, title, description, template or f"_card_readarr_{name}.html", compute,
                uses_period=data is not None, width=width, area="arr", has_table_window=table_window,
                group=readarr.NAME, available=lambda: readarr.is_connected())


def _source(name: str, title: str, description: str) -> Card:
    return _card(name, title, description, _d_source(name), template="_card_readarr_source.html", width="third", table_window=True)


CARDS: tuple[Card, ...] = (
    # --- Now in the app (live) ---
    _card("live_numbers", "Numbers right now",
          "Queue, missing, cutoff unmet and warnings: the state of the app right now."),
    _card("downloading", "Downloading",
          "What the app is downloading right now and how far it is. It refreshes by itself, without reloading the page.", live_data=_d_downloading),
    _card("queue", "Queue items with an error or warning",
          "What got stuck while downloading or importing into the library.", width="half", table_window=True),
    _card("calendar", "Coming in the next 7 days",
          "The app's calendar: what comes out this week and which of it is already in the library.", width="half", table_window=True),
    _card("health", "App warnings",
          "What the app itself reports as a problem. Only an administrator sees the texts.", table_window=True),
    # --- What was added ---
    _card("numbers", "Numbers for the period",
          "How many books were imported into the library, how many new, how many upgrades and how many downloads failed.", _d_numbers),
    _card("by_day", "Imports per day",
          "New books and upgrades day by day.", _d_by_day),
    _card("when", "When imports happen",
          "A grid of days and hours: when books arrive in the library.", _d_when, width="half"),
    _card("best_days", "Best days",
          "The days when the most books arrived in the library.", _d_best_days, width="half"),
    _card("largest", "Largest imported books",
          "The largest books imported in the selected period.", _d_largest, table_window=True),
    # --- Speed ---
    _card("downloads", "How long downloads take",
          "Time from sending to the client until the import into the library: median and 90%.", _d_downloads, width="half"),
    _card("after_release", "How soon after release",
          "How long after its release a book is in the library.", _d_after_release, width="half"),
    # --- Sources and quality ---
    _source("indexers", "Indexers", "Which indexers the imported books came from."),
    _source("groups", "Release groups", "Which release groups are the most common among imported books."),
    _source("clients", "Download clients", "Which client downloaded the imported books."),
    _source("protocols", "Usenet and torrent", "The share of Usenet and torrents among imported books."),
    _source("qualities", "Quality of imported books", "The quality in which books arrive in the library."),
    # --- Upgrades ---
    _card("upgrades", "Upgrade numbers",
          "How many upgrades happened, how many books they concerned and how many there are per book.", _d_upgrades),
    _card("how_all", "How everything was grabbed",
          "Who started the download: automation, a search, a manual pick or another app.", _d_how("how_all"),
          template="_card_readarr_how.html", width="half"),
    _card("how_upgrades", "How upgrades were grabbed",
          "Who started the downloads that brought a better version of a book.", _d_how("how"),
          template="_card_readarr_how.html", width="half"),
    _card("transitions", "From which quality to which",
          "The most common quality changes on upgrade.", _d_transitions, width="half", table_window=True),
    _card("upgraded", "Most upgraded books",
          "Books whose files were replaced most often.", _d_upgraded, width="half", table_window=True),
)

# Not `assert`: Python with -O leaves it out and a difference between SECTIONS and the cards would show only on the page.
if [k.key for k in CARDS] != [card_key(n) for _, names in SECTIONS for n in names]:
    raise RuntimeError("the Readarr cards do not match the page's sections (cards_readarr.SECTIONS)")
