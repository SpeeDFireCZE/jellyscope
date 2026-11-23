"""The cards of the Bazarr tab: subtitles for series and movies.

Each question is a card of its own in the registry (`cards.ALL_CARDS`), as with the
other apps. The Bazarr tab of the *Arr page (arr_bazarr.html) and the custom
dashboards draw them, both the same way. There is one Bazarr, so there is one set
of cards too.

What it stands on:

* **The period's events are selected once** (`stats.bazarr_analysis`) and the cards
  are different views of that selection; the memo of one computation holds it.
* **Live cards** ("Teď v aplikaci") take the state from `bazarr.live_state` (a
  one-minute cache). What did not load is None and the card says so - a zero would
  be a claim nobody checked.
* **The texts from the app only for the administrator**: the error's detail and
  Bazarr's warnings (paths on disk tend to be in them). The others see that
  something is there, not what.
* **A title that is in the library is a link to its detail** - it is looked up
  through Sonarr and Radarr (`stats.sonarr_detail_links`, `stats.radarr_detail_links`):
  Bazarr knows their title numbers. Whoever Jellyscope does not know stays plain text.
* **The speed card only with Sonarr or Radarr connected**: it counts from a file's
  import, and that is in their history.

stats/bazarr.py computes the numbers, here they are only put together for the templates.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import formatting, insights, languages, stats
from .arr import bazarr, radarr, sonarr
from .cards_base import Card, _once, current_is_admin, duration, live
from .i18n import translate as _t

NAME = bazarr.NAME

# The tab's sections and the names of their cards, in the order on the page. The
# heading is a Czech sentence = a translation key. The layout is in arr_bazarr.html;
# that the template draws exactly these cards in this order is guarded by
# tests/test_bazarr_web.py.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("In the app right now", ("live_numbers", "missing_languages", "providers_now", "health")),
    ("What was downloaded", ("numbers", "by_day", "when", "how")),
    ("Languages and providers", ("languages", "providers")),
    ("How good they are", ("score", "upgrades", "changed")),
    ("How fast they arrive", ("speed",)),
)
NAMES: tuple[str, ...] = tuple(name for _, names in SECTIONS for name in names)

# What happened to the subtitles (stats.bazarr_actions) - labels. The last three are not acquired subtitles.
ACTION_LABELS = {
    "grabbed": "downloaded automatically",
    "manual": "manual pick",
    "upgrade": "upgrade to better",
    "uploaded": "own file",
    "translated": "translated",
    "extracted": "extracted from the file",
    "synced": "timing sync",
    "deleted": "deleted",
    "other": "other#alt",
}
NOT_ACQUIRED = ("synced", "deleted")
# The buckets "from the file to the subtitles" (stats.bazarr_speed) and of the match (stats.bazarr_scores), in the chart's order.
BUCKET_LABELS = {"10m": "within 10 minutes", "1h": "within an hour", "1d": "within a day", "7d": "within a week", "longer": "longer"}
SCORE_LABELS = {"under_70": "under 70%", "70": "70–80%", "80": "80–90%", "90": "90% and more"}
# The provider under which Bazarr keeps a file uploaded by hand.
MANUAL_PROVIDER = "manual"
# When Bazarr tries a limited provider again: it writes in English "in 12 hours", but also
# "in a minute", "in an hour", "tomorrow" (common with a daily limit), "next week"…
_RETRY_IN = re.compile(r"in (\d+|an?) (second|minute|hour|day|week|month)s?")
RETRY_LABELS = {"second": "in {n} s", "minute": "in {n} min", "hour": "in {n} h", "day": "in {n} d",
                "week": "in {n} wk", "month": "in {n} mo"}
RETRY_WORDS = {"tomorrow": "tomorrow", "day after": "the day after tomorrow", "next week": "next week", "next month": "next month"}
# Language codes Bazarr has of its own (they are not in the common lists).
CUSTOM_CODES = {"pb": "Portuguese (Brazil)", "zt": "Chinese (Traditional)"}
# How many rows of a long table a card gets at most (a window opens the rest).
ROWS = insights.INSIGHT_ROWS


def card_key(name: str) -> str:
    """The card's key in the registry: `bazarr_<name>` - stored in the layouts of dashboards (before 1.9.0 the name was
    Czech, converted by schema_names)."""
    return f"bazarr_{name}"


def bazarr_period(period: Any) -> Any:
    """The period of the Bazarr cards: "the whole history" starts with the first subtitle event.

    Bazarr's history tends to be older than the first playback in Jellyscope (and
    without a playback "the whole history" would be only today). It is computed once
    per computation: all cards get the same object, under which the memo of the
    computation holds their shared selection of events.
    """
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.bazarr_first())
    return period


def has_imports() -> bool:
    """Is Sonarr or Radarr connected? Without their history there is nothing to count the speed from."""
    return bazarr.is_connected() and (sonarr.is_connected() or radarr.is_connected())


def language(code: str, hi: Any = False, forced: Any = False) -> str:
    """The subtitles' language for people: "čeština", "angličtina · pro neslyšící", "angličtina · vynucené"."""
    parts = [_t(CUSTOM_CODES[code]) if code in CUSTOM_CODES else languages.display(code)]
    if hi:
        parts.append(_t("hearing impaired"))
    if forced:
        parts.append(_t("forced"))
    return " · ".join(parts)


def retry_in(text: str) -> str:
    """When Bazarr tries again, in the interface's language. A shape that is not recognised stays as Bazarr wrote it."""
    text = text.strip()
    if text in RETRY_WORDS:
        return _t(RETRY_WORDS[text])
    match = _RETRY_IN.fullmatch(text)
    if not match:
        return text
    return _t(RETRY_LABELS[match.group(2)]).format(n=match.group(1) if match.group(1).isdigit() else "1")


def percent(value: float | None, signed: bool = False) -> str:
    """The match score to one decimal place ("86,5 %"); with a sign for an improvement ("+20,0 %"). Unknown = –."""
    if value is None:
        return "–"
    text = formatting.percent(abs(value), 1)
    return text if not signed else ("−" if value < 0 else "+") + text


def for_account(state: dict[str, Any], admin: bool) -> dict[str, Any]:
    """The live state as the account may see it. Returns a copy - the cache is shared.

    The texts from the app belong to the administrator: the error's text and
    Bazarr's warnings (paths on disk tend to be in them). The others see how many
    warnings there are.
    """
    result = {**state, "partly_loaded": bool(state["available"] and (state["error"] or None in (
        state["episode_count"], state["movie_count"], state["limited_count"], state["warnings"], state["providers"])))}
    if not admin:
        result["error"] = ""
        result["health"] = [] if state["health"] is not None else None
    return result


# ---------------------------------------------------------------------------
# Shared intermediate results - computed once in one data_for() (_once)
# ---------------------------------------------------------------------------

def _live() -> dict[str, Any]:
    return for_account(live(bazarr.live_state), current_is_admin())


def _analysis(period: Any) -> dict[str, Any]:
    return _once(stats.bazarr_analysis, period)


def _with_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with the address of the title's detail in the library (`detail_url`, None = Jellyscope does not know it in the library).

    An episode is looked up among Sonarr's series, a movie among Radarr's movies -
    only in connected apps and only the titles of these rows.
    """
    links = {kind: look_up({r["title_id"] for r in rows if r["kind"] == kind}) if package.is_connected() else {}
             for kind, package, look_up in (("episode", sonarr, stats.sonarr_detail_links), ("movie", radarr, stats.radarr_detail_links))}
    return [{**r, "detail_url": links.get(r["kind"], {}).get(r["title_id"])} for r in rows]


# ---------------------------------------------------------------------------
# The data of the single cards - a dict the template reads as `d`
# ---------------------------------------------------------------------------

def _d_live_numbers() -> dict[str, Any]:
    return _once(_live)


def _d_missing_languages() -> dict[str, Any]:
    state = _once(_live)
    rows = state["languages"]
    # The languages come from the sync (state["languages_at"]), also when Bazarr does not answer now.
    return {"available": state["available"], "incomplete_list": state["languages_incomplete"], "most": bazarr.live.MISSING_LIMIT,
            "counted_at": state["languages_at"],
            "rows": None if rows is None else [{**r, "label": language(r["language"], r["hi"], r["forced"])} for r in rows]}


def _d_providers_now() -> dict[str, Any]:
    state = _once(_live)
    rows = state["providers"]
    return {"available": state["available"],
            "rows": None if rows is None else [{**p, "again": retry_in(p["again"])} for p in rows]}


def _d_health() -> dict[str, Any]:
    state = _once(_live)
    return {"available": state["available"], "health": state["health"], "warnings": state["warnings"]}


def _d_numbers(period: Any) -> dict[str, Any]:
    return stats.bazarr_numbers(period, analysis=_analysis(period))


def _d_by_day(period: Any) -> dict[str, Any]:
    return {"body": stats.chart_days(stats.bazarr_by_day(period, analysis=_analysis(period)), period, ("episodes", "movies"))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.bazarr_heatmap(period, analysis=_analysis(period))}


def _d_how(period: Any) -> dict[str, Any]:
    rows = [{"action": r["action"], "label": _t(ACTION_LABELS[r["action"]]), "count": r["count"]}
            for r in stats.bazarr_actions(period, analysis=_analysis(period))]
    return {"rows": [r for r in rows if r["action"] not in NOT_ACQUIRED],
            "outside": ", ".join(f"{r['label']} {formatting.number(r['count'])}" for r in rows if r["action"] in NOT_ACQUIRED)}


def _d_languages(period: Any) -> dict[str, Any]:
    rows = stats.bazarr_languages(period, analysis=_analysis(period))[:ROWS]
    return {"rows": [{**r, "name": language(r["language"]), "score": percent(r["score"])} for r in rows]}


def _d_providers(period: Any) -> dict[str, Any]:
    rows = stats.bazarr_providers(period, analysis=_analysis(period))[:ROWS]
    return {"rows": [{**r, "score": percent(r["score"]),
                       "name": _t("own file") if r["provider"] == MANUAL_PROVIDER else r["provider"] or "–"}
                      for r in rows]}


def _d_score(period: Any) -> dict[str, Any]:
    score = stats.bazarr_scores(period, analysis=_analysis(period))
    return {"median": percent(score["median"]), "count": score["count"],
            "buckets": [{"label": _t(label), "count": score["buckets"][bucket]} for bucket, label in SCORE_LABELS.items()]}


def _d_upgrades(period: Any) -> dict[str, Any]:
    upgrades = stats.bazarr_upgrades(period, analysis=_analysis(period))
    return {"count": upgrades["count"], "paired": upgrades["paired"], "improvements": percent(upgrades["improvements"], signed=True)}


def _d_changed(period: Any) -> dict[str, Any]:
    rows = _with_details(stats.bazarr_changed(period, ROWS, analysis=_analysis(period)))
    return {"rows": [{**r, "language_name": language(r["language"], r["hi"], r["forced"])} for r in rows]}


def _d_speed(period: Any) -> dict[str, Any]:
    speed = stats.bazarr_speed(period, analysis=_analysis(period))
    return {"median": duration(speed["median"]), "p90": duration(speed["p90"]), "count": speed["count"],
            "buckets": [{"label": _t(label), "count": speed["buckets"][bucket]} for bucket, label in BUCKET_LABELS.items()],
            # What is not counted (the sentences are in the template): no known import and a file older than Bazarr.
            "not_imported": speed["not_imported"], "before_bazarr": speed["before_bazarr"]}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _card(name: str, title: str, description: str, data: Callable[[Any], dict[str, Any]] | None = None, *,
          live_data: Callable[[], dict[str, Any]] | None = None, width: str = "full", table_window: bool = False,
          available: Callable[[], bool] = bazarr.is_connected) -> Card:
    """One Bazarr card. `data(period)` returns a dict for the template; the registry
    gets it under the card's key. A live card (`live_data()`) reads Bazarr's state now, the period is not its business."""
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        if data is None:
            return {key: live_data() if live_data else {}}
        return {key: data(_once(bazarr_period, period))}

    return Card(key, title, description, f"_card_bazarr_{name}.html", compute, uses_period=data is not None,
                width=width, area="arr", has_table_window=table_window, group=NAME, available=available)


CARDS: tuple[Card, ...] = (
    # --- Now in the app (live) ---
    _card("live_numbers", "Numbers right now",
          "How many episodes and movies have no subtitles, how many providers are throttled and how many warnings Bazarr reports.",
          live_data=_d_live_numbers),
    _card("missing_languages", "Missing subtitles by language",
          "How many episodes and movies are waiting for subtitles in which language.", live_data=_d_missing_languages, width="half"),
    _card("providers_now", "Providers now",
          "Enabled subtitle providers and their state. Throttled ones come first, with the reason.", live_data=_d_providers_now, width="half", table_window=True),
    _card("health", "App warnings",
          "What the app itself reports as a problem. Only an administrator sees the texts.", live_data=_d_health, table_window=True),
    # --- What was downloaded ---
    _card("numbers", "Subtitle numbers",
          "How many subtitles were obtained and how many of them automatically, manually and by upgrade.", _d_numbers),
    _card("by_day", "Subtitles per day",
          "Subtitles obtained for episodes and for movies, day by day.", _d_by_day),
    _card("when", "When subtitles arrive",
          "A grid of days and hours: when subtitles are obtained.", _d_when, width="half"),
    _card("how", "How subtitles were obtained",
          "Download, manual pick, upgrade, own file, translation or extraction from the file.", _d_how, width="half"),
    # --- Languages and providers ---
    _card("languages", "Subtitle languages",
          "Which languages subtitles were obtained in and how well they matched.", _d_languages, width="half", table_window=True),
    _card("providers", "Subtitle providers",
          "Where subtitles come from, how well they matched and how many an upgrade later replaced.", _d_providers,
          width="half", table_window=True),
    # --- How good they are ---
    _card("score", "Subtitle match",
          "How well subtitles fit the file: the median match and its distribution.", _d_score, width="half"),
    _card("upgrades", "Subtitle upgrades",
          "How many times Bazarr replaced subtitles with better ones and by how much the match improved.", _d_upgrades, width="half"),
    _card("changed", "Subtitles obtained repeatedly",
          "Episodes and movies whose subtitles in one language were obtained more than once.", _d_changed, table_window=True),
    # --- How fast they come ---
    _card("speed", "How fast subtitles arrive",
          "How long after the file is imported an episode or movie gets its first subtitles.", _d_speed, available=has_imports),
)

# Not `assert`: Python with -O leaves it out and a difference between SECTIONS and the cards would show only on the page.
if [card.key for card in CARDS] != [card_key(name) for name in NAMES]:
    raise RuntimeError("the Bazarr cards do not match the page's sections (cards_bazarr.SECTIONS)")
