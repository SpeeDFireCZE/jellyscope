"""The cards of the Prowlarr tab: indexers, queries to them and grabbed releases.

Each question is a card of its own in the registry (`cards.ALL_CARDS`), as with the
other apps of the *Arr page. The Prowlarr tab (arr_prowlarr.html) and the custom
dashboards draw them, both the same way.

What it stands on:

* **The period's sums are computed once** (`stats.prowlarr_analysis`) and the cards
  are different views of them; the memo of one computation holds them.
* **Live cards** ("Teď v aplikaci") take the state from `prowlarr.live_state` (a
  one-minute cache). What did not load is None and the card says so - a zero would
  be a claim nobody checked. "V pořádku" is an indexer Prowlarr does not skip right
  now nor names in the warning about unavailable ones.
* **The texts from the app only for the administrator**: the error's detail and
  Prowlarr's warnings. The others see that something is there, not what.
* **What Prowlarr does not know is "–"**: a release it only redirected the app to the
  indexer for (always with usenet) it writes as successful and without a time.
  Failures and the time are therefore counted only from the releases it downloaded
  itself, and the card says how many those were.

stats/prowlarr.py computes the numbers, here they are only put together for the templates.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import formatting, insights, stats
from .arr import prowlarr
from .cards_base import Card, _once, current_is_admin, live
from .i18n import translate as _t

NAME = prowlarr.NAME

# The tab's sections and the names of their cards, in the order on the page. The
# heading is a Czech sentence = a translation key. The layout is in
# arr_prowlarr.html; that the template draws exactly these cards in this order is
# guarded by tests/test_prowlarr_web.py.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("In the app right now", ("live_numbers", "indexers_now", "health")),
    ("Queries", ("numbers", "by_day", "when", "who")),
    ("Indexer performance", ("indexers", "response_time", "failures")),
    ("Grabbed releases", ("grabbed_numbers", "grabbed_indexers", "grabbed_latest")),
)
NAMES: tuple[str, ...] = tuple(name for _, names in SECTIONS for name in names)

# How a grabbed release ended - labels (translation keys). "Přesměrováno" is no result:
# Prowlarr only sent the app to the indexer and does not know how the download ended.
RESULT_LABELS = {"ok": "downloaded#neuter", "failed": "failed", "redirected": "redirected", "unknown": "unknown"}
# How many rows of a long table a card gets at most (a window opens the rest).
ROWS = insights.INSIGHT_ROWS
# How many indexers the response time chart shows.
MAX_COLUMNS = 12


def card_key(name: str) -> str:
    """The card's key in the registry: `prowlarr_<name>` - stored in the layouts of dashboards (before 1.9.0 the name was
    Czech, converted by schema_names)."""
    return f"prowlarr_{name}"


def prowlarr_period(period: Any) -> Any:
    """The period of the Prowlarr cards: "the whole history" starts with Prowlarr's first stored event.

    That has nothing to do with the first playback in Jellyscope (and without a
    playback "the whole history" would be only today). It is computed once per
    computation: all cards get the same object, under which the memo of the
    computation holds their shared sums.
    """
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.prowlarr_first())
    return period


def response_time(ms: int | None) -> str:
    """The response time for people: up to a second in milliseconds ("248 ms"), then in seconds ("1,2 s"). Unknown = –."""
    if ms is None:
        return "–"
    if ms < 1000:
        return f"{formatting.number(ms)} ms"
    return f"{formatting.number_in_unit(ms / 1000)} s"


def percent(share: float | None) -> str:
    """A share (0-1) as a percentage; when there is nothing to count from, "–"."""
    return "–" if share is None else formatting.percent(share * 100)


def indexer_name(number: int, name: str | None) -> str:
    """The indexer's name; an indexer without a name (long gone from Prowlarr) is "indexer č. N".
    An event for which Prowlarr named no indexer (number 0) belongs to "an unknown indexer"."""
    if name:
        return name
    return _t("indexer no. {n}").format(n=number) if number else _t("unknown indexer")


def result_label(release: dict[str, Any]) -> str:
    """How a grabbed release ended: `ok` / `failed` (Prowlarr downloaded it itself), `redirected`, `unknownkind` (a key of RESULT_LABELS)."""
    if not release["succeeded"]:
        return "failed"
    return {"proxy": "ok", "redirect": "redirected"}.get(release["method"], "unknown")


def for_account(state: dict[str, Any], admin: bool) -> dict[str, Any]:
    """The live state as the account may see it. Returns a copy - the cache is shared.

    The texts from the app belong to the administrator: the error's text and
    Prowlarr's warnings. The others see how many warnings there are.
    """
    result = {**state, "partly_loaded": bool(state["available"] and (state["error"] or None in (
        state["indexers"], state["unavailable_count"], state["app_count"], state["health"])))}
    if not admin:
        result["error"] = ""
        result["health"] = [] if state["health"] is not None else None
    return result


# ---------------------------------------------------------------------------
# Shared intermediate results - computed once in one data_for() (_once)
# ---------------------------------------------------------------------------

def _live() -> dict[str, Any]:
    return for_account(live(prowlarr.live_state), current_is_admin())


def _analysis(period: Any) -> dict[str, Any]:
    return _once(stats.prowlarr_analysis, period)


# ---------------------------------------------------------------------------
# The data of the single cards - a dict the template reads as `d`
# ---------------------------------------------------------------------------

def _d_live_numbers() -> dict[str, Any]:
    return _once(_live)


def _d_indexers_now() -> dict[str, Any]:
    state = _once(_live)
    return {"available": state["available"], "rows": state["indexers"],
            # The indexers are there, but the blocks or warnings did not load: "OK" is then not claimed.
            "blocks_unknown": state["indexers"] is not None and state["unavailable_count"] is None}


def _d_health() -> dict[str, Any]:
    state = _once(_live)
    return {"available": state["available"], "health": state["health"], "warnings": state["warnings"]}


def _d_numbers(period: Any) -> dict[str, Any]:
    numbers = stats.prowlarr_numbers(period, analysis=_analysis(period))
    return {**numbers, "share": percent(numbers["failure_share"]), "response": response_time(numbers["response"])}


def _d_by_day(period: Any) -> dict[str, Any]:
    return {"body": stats.chart_days(stats.prowlarr_by_day(period, analysis=_analysis(period)), period, ("searches", "rss"))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.prowlarr_heatmap(period, analysis=_analysis(period))}


def _d_who(period: Any) -> dict[str, Any]:
    return {"rows": [{**r, "name": r["app"] or _t("unknown app")}
                      for r in stats.prowlarr_who(period, analysis=_analysis(period))]}


def _indexers(period: Any) -> list[dict[str, Any]]:
    return [{**r, "name": indexer_name(r["indexer_id"], r["name"])} for r in stats.prowlarr_indexers(period, analysis=_analysis(period))]


def _d_indexers(period: Any) -> dict[str, Any]:
    return {"rows": [{**r, "failure_share": percent(r["failure_share"]), "response": response_time(r["response"]),
                       "found_share": percent(r["found_share"])} for r in _once(_indexers, period)[:ROWS]]}


def _d_response_time(period: Any) -> dict[str, Any]:
    rows = sorted((r for r in _once(_indexers, period) if r["response"] is not None), key=lambda r: (-r["response"], r["name"]))
    return {"rows": [{"name": r["name"], "response": r["response"]} for r in rows[:MAX_COLUMNS]]}


def _d_failures(period: Any) -> dict[str, Any]:
    by_day = stats.prowlarr_by_day(period, analysis=_analysis(period))
    return {"body": stats.chart_days(by_day, period, ("failed_count",)), "total": sum(r["failed_count"] for r in by_day)}


def _d_grabbed_numbers(period: Any) -> dict[str, Any]:
    grabbed = stats.prowlarr_grabbed(period, analysis=_analysis(period))
    # Prowlarr knows a failure only for releases it downloaded itself; without them it is "–", not zero.
    return {**grabbed, "failed_count": formatting.number(grabbed["failed_count"]) if grabbed["being_grabbed"] else "–",
            "response": response_time(grabbed["response"])}


def _d_grabbed_indexers(period: Any) -> dict[str, Any]:
    rows = [{**r, "name": indexer_name(r["indexer_id"], r["name"]), "share": percent(r["share"]),
             "failed_count": "–" if r["failed_count"] is None else formatting.number(r["failed_count"])}
            for r in stats.prowlarr_grabbed_indexers(period, analysis=_analysis(period))]
    return {"rows": rows[:ROWS], "unknown": any(r["failed_count"] == "–" for r in rows[:ROWS])}


def _d_grabbed_latest(period: Any) -> dict[str, Any]:
    rows = []
    for r in stats.prowlarr_grabbed_latest(period, ROWS, analysis=_analysis(period)):
        how = result_label(r)
        rows.append({**r, "name": indexer_name(r["indexer_id"], r["name"]), "app": r["app"] or _t("unknown app"),
                     "result": how, "label": _t(RESULT_LABELS[how]), "response": response_time(r["response"])})
    return {"rows": rows}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _card(name: str, title: str, description: str, data: Callable[[Any], dict[str, Any]] | None = None, *,
          live_data: Callable[[], dict[str, Any]] | None = None, width: str = "full", table_window: bool = False) -> Card:
    """One Prowlarr card. `data(period)` returns a dict for the template; the registry
    gets it under the card's key. A live card (`live_data()`) reads Prowlarr's state now, the period is not its business."""
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        if data is None:
            return {key: live_data() if live_data else {}}
        return {key: data(_once(prowlarr_period, period))}

    return Card(key, title, description, f"_card_prowlarr_{name}.html", compute, uses_period=data is not None,
                width=width, area="arr", has_table_window=table_window, group=NAME, available=prowlarr.is_connected)


CARDS: tuple[Card, ...] = (
    # --- Now in the app (live) ---
    _card("live_numbers", "Numbers right now",
          "How many indexers are enabled, how many Prowlarr has temporarily disabled because of errors, how many apps are connected and how many warnings it reports.",
          live_data=_d_live_numbers),
    _card("indexers_now", "Indexers now",
          "Indexers and their state. Those Prowlarr has temporarily disabled because of errors come first.", live_data=_d_indexers_now, width="half", table_window=True),
    _card("health", "App warnings",
          "What the app itself reports as a problem. Only an administrator sees the texts.", live_data=_d_health, width="half", table_window=True),
    # --- Queries ---
    _card("numbers", "Query numbers",
          "How many searches and RSS queries went through Prowlarr, how many failed and how fast the indexers answer.", _d_numbers),
    _card("by_day", "Queries per day",
          "A curve of searches and RSS queries day by day.", _d_by_day),
    _card("when", "When searches happen",
          "A grid of days and hours: when the apps search.", _d_when, width="half"),
    _card("who", "Who asks",
          "Which apps search, read RSS and grab releases through Prowlarr.", _d_who, width="half", table_window=True),
    # --- Indexer performance ---
    _card("indexers", "Indexer performance",
          "Queries, failures, response time and search success per indexer.", _d_indexers, table_window=True),
    _card("response_time", "Indexer response time",
          "Average response time of the indexers, slowest on top.", _d_response_time),
    # The chart by day at the full width: in a half-width card its font and axes shrink too and cannot be read.
    _card("failures", "Failed queries per day",
          "How many queries to the indexers failed day by day.", _d_failures),
    # --- Grabbed releases ---
    _card("grabbed_numbers", "Grabbed release numbers",
          "How many releases the apps grabbed through Prowlarr, how many downloads failed and how long the indexer took to hand over the file.",
          _d_grabbed_numbers),
    _card("grabbed_indexers", "Grabbed releases by indexer",
          "Which indexers the apps grab releases from.", _d_grabbed_indexers, width="half", table_window=True),
    _card("grabbed_latest", "Latest grabbed releases",
          "What each app grabbed most recently and from which indexer.", _d_grabbed_latest, width="half", table_window=True),
)

# Not `assert`: Python with -O leaves it out and a difference between SECTIONS and the cards would show only on the page.
if [card.key for card in CARDS] != [card_key(name) for name in NAMES]:
    raise RuntimeError("the Prowlarr cards do not match the page's sections (cards_prowlarr.SECTIONS)")
