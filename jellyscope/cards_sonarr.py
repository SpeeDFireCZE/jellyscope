"""The Sonarr cards: the Sonarr tab of the *Arr page and the custom dashboards.

Each question is a card of its own in the registry (`cards.ALL_CARDS`): the page
`/arr/sonarr` (web/arr.py, arr_sonarr.html) and the custom dashboards draw them,
both the same way. Everything the cards know about Sonarr is here and in the
templates `_card_sonarr_*.html`; the other apps have their own.

What it stands on:

* **The cards' keys** are `arr_sonarr_<name>` (`arr_sonarr_queue`). A card returns
  its data under its key and the template reads them as `d` (see cards_base.py) -
  the cards of several apps overwrite nothing of each other in one dashboard.
* **A card of a disconnected Sonarr is not available** (`available`): it is neither
  offered nor computed. After connecting it is back.
* **The period's events are selected once.** Twenty cards are only different views
  of the same selection (`stats.sonarr_events`); the memo of one computation
  (`_once`) holds it, so the page and the dashboard make one query.
* **The live state** (queue, calendar, health) comes from `sonarr.live_state` with a
  one-minute cache; a card takes it through `live()` and leaves the texts from the
  app only to the administrator (`for_account`).
* **A series that is in the library is a link to its detail** (`with_details`). It
  is paired by id (stats.sonarr_detail_links), not by name; a series Sonarr is only
  downloading stays without a link.

stats/sonarr.py computes the numbers, here they are only put together for the
templates. Adding a card means adding a line to `CARDS` and its name to `SECTIONS`.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any

from . import formatting, i18n, insights, stats
from .arr import sonarr
from .cards_base import Card, _once, current_is_admin, duration, live, per_item
from .i18n import translate as _t

# The sections of the page /arr/sonarr and the names of their cards, in the order on
# the page. The heading is an English sentence = a translation key. The layout (pairs
# side by side) is in arr_sonarr.html; that the template draws exactly these
# cards in this order is guarded by tests/test_sonarr_cards.py.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("In the app right now", ("live_numbers", "downloading", "queue", "calendar", "health")),
    ("What was added", ("numbers", "by_day", "when", "best_days", "largest")),
    ("Speed", ("downloads", "after_release")),
    ("Sources and quality", ("indexer_reliability", "indexers", "groups", "clients", "protocols", "qualities", "resolutions")),
    ("Upgrades", ("upgrades", "how_all", "how_upgrades", "transitions", "upgraded")),
)

# How a grab started - the labels of the groups from stats.sonarr_upgrades (`how`, `how_all`).
HOW_LABELS = {
    "automatic": "automatic (RSS and automatic search)",
    "user": "search started by a user",
    "manual": "manual release pick",
    "external": "pushed from outside (Prowlarr, autobrr)",
    "unknown": "unknown#neuter",
}
# The buckets "from release to import" (stats.sonarr_speed) and their labels.
BUCKET_LABELS = {"1h": "within an hour", "6h": "within 6 hours", "1d": "within a day", "7d": "within a week", "longer": "longer"}
BEST_DAYS = 5
# Download states (arr/sonarr/progress.py) for the "Downloading" card: the label and the badge's colour.
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
# The poster's width - the same as "Recently added" and the Seerr cards, so it is the same file in the image cache.
POSTER_WIDTH = 300
# How many rows of a long table a card gets at most. How many show right away is
# governed by Settings → Interface → Long lists; a window opens the rest.
ROWS = insights.INSIGHT_ROWS


def card_key(name: str) -> str:
    """The card's key in the registry: `arr_sonarr_<name>` - stored in the layouts of dashboards (before 1.9.0 the name
    was Czech, converted by schema_names)."""
    return f"arr_sonarr_{name}"


def for_account(state: dict[str, Any], admin: bool) -> dict[str, Any]:
    """The live state as the account may see it. Returns a copy - the cache is shared.

    The texts from the app belong to the administrator, who can do something with
    them: the error's text, the health messages ("Unable to communicate with
    qBittorrent at http://192.168.1.5:8080") and the messages at queue items (paths
    on disk). The others see that something is there - the count of warnings, the
    series and the state in the queue - but not the addresses and paths from the
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

def sonarr_period(period: Any) -> Any:
    """The period of the Sonarr cards: "the whole history" starts with its first event.

    Sonarr's history usually reaches far before the first playback in Jellyscope (and
    without a playback "the whole history" would be only today). It is computed once
    per computation and all cards get the same object - it is the key under which the
    memo of the computation holds their shared intermediate results.
    """
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.sonarr_first())
    return period


def with_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with the address of the series' detail in the library (`detail_url`, None = not in the library).

    Returns copies: the rows come from shared results (the memo of the computation,
    the cache of the live state). Where the link leads stats.sonarr_detail_links
    knows; whether the account may open it the template decides (the odkaz macro).
    """
    # Only the series of these rows: Sonarr's library can have thousands of series
    # and the card draws a few dozen.
    links = stats.sonarr_detail_links({row.get("title_id") for row in rows})
    return [{**row, "detail_url": links.get(row.get("title_id"))} for row in rows]


def success_rate(share: float | None) -> str:
    """The success rate (0-1) as a percentage with one decimal, rounded down: an indexer with one failure in a thousand
    does not read "100,0 %". Nothing finished that can be matched = "–"."""
    if share is None:
        return "–"
    return formatting.percent(math.floor(share * 1000 + 1e-9) / 10, 1)


def episode_count(count: int) -> str:
    """"1 díl", "3 díly", "5 dílů"."""
    return formatting.plural("{n} episodes", count)


def _downloads_for_template(item: dict[str, Any]) -> dict[str, Any]:
    """A block or a download with texts for the template: the state, the episode count and the details under the progress bar.

    `details` are finished pieces of the line under the bar (how much is downloaded,
    how much is left, what downloads it) - the template only joins them with a
    separator, so the line does not start with a separator even when one is missing
    (a torrent only fetching metadata has neither a size nor a percentage).
    """
    label, css_class = DOWNLOAD_STATES.get(item["state"], (item["state"], "neutral"))
    details = []
    if item["percent"] is not None:
        size = formatting.bytes_human(item["size_bytes"])
        # What has not started yet writes only the size: "0 B z 2 GB" says nothing.
        details.append(_t("{downloaded} of {total}").format(downloaded=formatting.bytes_human(item["grabbed"]), total=size)
                       if item["grabbed"] else size)
    if item["seconds_left"] is not None:
        details.append(f"{_t('left')} {duration(item['seconds_left'])}")
    if item["client"]:
        details.append(item["client"] + (f" ({item['protocol']})" if item["protocol"] else ""))
    return {**item, "state_text": _t(label) if item["state"] in DOWNLOAD_STATES else label, "state_class": css_class,
            "details": details, "episode_count_text": episode_count(item["episode_count"]) if item["episode_count"] > 1 else ""}


def downloading(state: dict[str, Any], admin: bool) -> dict[str, Any]:
    """The data of the body of the "Stahuje se" card from the queue's state (sonarr.download_state).

    Blocks get a link into the library and a poster: a series from the library has
    the one from Jellyfin (/image/, the same file as "Recently added"), the others a
    provisional one from Sonarr (/arr/sonarr/poster/<id>); a download Sonarr did not
    pair, none. A block with more downloads gets the id of a window with the
    breakdown. The error's detail (the app's internal address) only for the
    administrator - it is called from the card and from the slice, so who it is put
    together for is said from outside.
    """
    library = stats.sonarr_library({block["title_id"] for block in state["blocks"]})
    blocks = []
    for block in state["blocks"]:
        in_library = library.get(block["title_id"])
        if in_library:
            tag = f"&tag={in_library['poster_tag']}" if in_library["poster_tag"] else ""
            poster = f"/image/{in_library['poster_id']}?kind=Primary&w={POSTER_WIDTH}{tag}"
        elif block["title_id"] is not None and block["tmdb_id"] and not sonarr.poster_missing(block["title_id"]):
            poster = f"/arr/sonarr/poster/{block['title_id']}"
        else:               # a download Sonarr did not pair, or a poster known not to exist: a substitute
            poster = ""
        several = block["count"] > 1 and block["title_id"] is not None
        # What downloads in the block when there is more of it: a series' episodes, otherwise the count of downloads.
        count_text = (episode_count(block["episode_count"]) if block["episode_count"] else i18n.translate_plural("{n} downloads", block["count"])) if several else ""
        blocks.append({**_downloads_for_template(block), "poster": poster, "detail_url": in_library["detail_url"] if in_library else None,
                       "window": f"modal-downloading-sonarr-{block['title_id']}" if several else "", "count_text": count_text,
                       "downloads": [_downloads_for_template(s) for s in block["downloads"]]})
    more = formatting.plural("And {n} more titles in the queue.", state["more_count"]) if state["more_count"] else ""
    return {"app": "sonarr", "available": state["available"], "error": state["error"] if admin else "", "blocks": blocks,
            "more_count": more, "incomplete": state["incomplete"], "refresh": state["refresh"]}


async def _right_now() -> tuple[dict[str, Any], dict[str, Any]]:
    """The live state and the downloads at once: a page waits for the slower of the two, not for both in a row.

    Both keep their own caches and in-flight guards; the cards read them through one live() call (cards_base: one
    computation per page).
    """
    state, downloads = await asyncio.gather(sonarr.live_state(), sonarr.download_state())
    return state, downloads


def _d_downloading() -> dict[str, Any]:
    return downloading(live(_right_now)[1], current_is_admin())


def _live() -> dict[str, Any]:
    """Sonarr's live state for the account it is computed for - series with a link into the library."""
    state = for_account(live(_right_now)[0], current_is_admin())
    if state["queue"] is not None:
        state["queue"] = {**state["queue"], "with_error": with_details(state["queue"]["with_error"])}
    if state["calendar"] is not None:
        state["calendar"] = with_details(state["calendar"])
    return state


def _events(period: Any) -> dict[str, Any]:
    return _once(stats.sonarr_events, period)


def _additions(period: Any) -> list[dict[str, Any]]:
    return stats.sonarr_additions(period, analysis=_events(period))


def _speed(period: Any) -> dict[str, Any]:
    return stats.sonarr_speed(period, analysis=_events(period))


def _sources(period: Any) -> dict[str, list[dict[str, Any]]]:
    return stats.sonarr_sources(period, analysis=_events(period))


def _upgrades(period: Any) -> dict[str, Any]:
    return stats.sonarr_upgrades(period, analysis=_events(period), titles=ROWS)


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
            "failures": stats.sonarr_failures(period)}


def _d_by_day(period: Any) -> dict[str, Any]:
    return {"body": stats.chart_days(_once(_additions, period), period, ("new_count", "upgrade_count"))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.sonarr_heatmap(period, analysis=_events(period))}


def _d_best_days(period: Any) -> dict[str, Any]:
    additions = _once(_additions, period)
    return {"rows": sorted(additions, key=lambda r: (-r["count"], -r["size_bytes"], r["day"]))[:BEST_DAYS]}


def _d_largest(period: Any) -> dict[str, Any]:
    return {"rows": with_details(stats.sonarr_largest(period, limit=ROWS))}


def _d_downloads(period: Any) -> dict[str, Any]:
    downloads = _once(_speed, period)["downloads"]
    return {"median": duration(downloads["median"]), "p90": duration(downloads["p90"]), "count": downloads["count"]}


def _d_after_release(period: Any) -> dict[str, Any]:
    since_release = _once(_speed, period)["since_release"]
    return {"median": duration(since_release["median"]), "p90": duration(since_release["p90"]), "count": since_release["count"],
            "before_release": since_release["before_release"],
            # Sentences under the tiles: how many imported ones are not counted and why. A title added only after
            # the release and a title for which it is not known when it came are two different things.
            "not_counted": formatting.plural("{n} imported files are not counted: their title was added to the app after the release.", since_release["not_counted"]) if since_release["not_counted"] else "",
            "unknown": formatting.plural("For {n} imported files it is not known when their title was added to the app; they are not counted either.", since_release["unknown"]) if since_release["unknown"] else "",
            "buckets": [{"label": _t(BUCKET_LABELS[k]), "count": n} for k, n in since_release["buckets"].items()]}


def _d_indexer_reliability(period: Any) -> dict[str, Any]:
    rows = stats.sonarr_indexer_reliability(period, analysis=_events(period))
    # Whether any grab cannot be matched (the Blackhole client): then the card says why.
    return {"rows": [{**r, "success": success_rate(r["success"])} for r in rows[:ROWS]],
            "unmatched": sum(r["unmatched"] for r in rows)}


def _d_source(group: str) -> Callable[[Any], dict[str, Any]]:
    """The data of one of the six source cards (indexers, groups, clients…) - the same template, another group."""
    def data(period: Any) -> dict[str, Any]:
        rows = _once(_sources, period)[group]
        # The share is of the count of imported files; each file is in the group exactly once.
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
    """One Sonarr card.

    `data(period)` returns a dict for the template; the registry gets it under the
    card's key. Without `data` the card is live: it reads Sonarr's state now, the
    period is not its business - the shared live state (`_live`), or its own
    (`live_data()`, the "Stahuje se" card). The template is
    `_card_sonarr_<name>.html`, unless said otherwise (the six source cards and the
    two "how it was downloaded" cards share one each).
    """
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        if data is None:
            # Once per computation: four live cards read the same state
            # (and the same lookup of the queue's and the calendar's series in the library).
            return {key: _once(live_data or _live)}
        return {key: data(_once(sonarr_period, period))}

    return Card(key, title, description, template or f"_card_sonarr_{name}.html", compute,
                uses_period=data is not None, width=width, area="arr", has_table_window=table_window,
                group=sonarr.NAME, available=lambda: sonarr.is_connected())


def _source(name: str, title: str, description: str) -> Card:
    return _card(name, title, description, _d_source(name), template="_card_sonarr_source.html", width="third", table_window=True)


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
          "How many files were imported into the library, how many new, how many upgrades and how many downloads failed.", _d_numbers),
    _card("by_day", "Imports per day",
          "New files and upgrades day by day.", _d_by_day),
    _card("when", "When imports happen",
          "A grid of days and hours: when files arrive in the library.", _d_when, width="half"),
    _card("best_days", "Best days",
          "The days when the most files arrived in the library.", _d_best_days, width="half"),
    _card("largest", "Largest imported files",
          "The largest files imported in the selected period.", _d_largest, table_window=True),
    # --- Speed ---
    _card("downloads", "How long downloads take",
          "Time from sending to the client until the import into the library: median and 90%.", _d_downloads, width="half"),
    _card("after_release", "How soon after release",
          "How long after it aired an episode is in the library.", _d_after_release, width="half"),
    # --- Sources and quality ---
    _card("indexer_reliability", "Indexer reliability",
          "How many episode downloads from each indexer were imported into the library and how many failed. The most failures come first.",
          _d_indexer_reliability, table_window=True),
    _source("indexers", "Indexers", "Which indexers the imported files came from."),
    _source("groups", "Release groups", "Which release groups are the most common among imported files."),
    _source("clients", "Download clients", "Which client downloaded the imported files."),
    _source("protocols", "Usenet and torrent", "The share of Usenet and torrents among imported files."),
    _source("qualities", "Quality of imported files", "The quality in which files arrive in the library."),
    _source("resolutions", "Resolution of imported files", "The resolution in which files arrive in the library."),
    # --- Upgrades ---
    _card("upgrades", "Upgrade numbers",
          "How many upgrades happened, how many items they concerned and how many there are per item.", _d_upgrades),
    _card("how_all", "How everything was grabbed",
          "Who started the download: automation, a search, a manual pick or another app.", _d_how("how_all"),
          template="_card_sonarr_how.html", width="half"),
    _card("how_upgrades", "How upgrades were grabbed",
          "Who started the downloads that brought a better version of a file.", _d_how("how"),
          template="_card_sonarr_how.html", width="half"),
    _card("transitions", "From which quality to which",
          "The most common quality changes on upgrade.", _d_transitions, width="half", table_window=True),
    _card("upgraded", "Most upgraded titles",
          "Titles whose files were replaced most often.", _d_upgraded, width="half", table_window=True),
)

# Not `assert`: Python with -O leaves it out and a difference between SECTIONS and the cards would show only on the page.
if [k.key for k in CARDS] != [card_key(n) for _, names in SECTIONS for n in names]:
    raise RuntimeError("the Sonarr cards do not match the page's sections (cards_sonarr.SECTIONS)")
