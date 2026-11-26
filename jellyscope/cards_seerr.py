"""The cards of the Seerr page: requests for movies and series.

The Seerr page used to be four big cards; here each question is a card of its own
in the registry (`cards.ALL_CARDS`). The page `/seerr` (web/seerr.py, seerr.html) and
the custom dashboards draw them, both the same way - like the cards of the apps of
the *Arr page.

What it stands on:

* **The period's requests are selected once** (`stats.seerr_events`) and the cards
  are different views of that selection; the memo of one computation holds it.
* **The requesters' names only with the area "...including who requested"**
  (the area `seerr_who`). Without it the requester's name and id are removed from the rows
  already here - the template then cannot write them out, not even by mistake - and
  the "Who requests" card is not computed at all (it has that area as its own). With it
  anonymising still guards the names (the keys `user_id` and `user_name`, see
  access.anonymise).
* **A title that is in the library already is a link to its detail** (`detail`);
  where the account may not go, the template draws no link (the odkaz macro).
* **A request has a poster** (`poster`): a title that is in the library the one from
  Jellyfin - the same address, and so the same file, as "Recently added". A title
  outside the library a provisional one from the connected apps (seerr/posters.py).
* **The live card "Waiting for approval"** takes the state from `seerr.pending` (a
  one-minute cache); it leaves the error's detail only to the administrator.
* **"Latest requests" is live too**: Seerr's newest requests (`seerr.latest_requests`,
  loaded with the pending ones under the same entry of the one-minute cache) over the
  period's stored ones, so its states agree with "Waiting for approval". When Seerr does not answer, the stored requests with a note
  saying when the last sync saw them.

stats/seerr.py computes the numbers, here they are only put together for the templates.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from . import insights, seerr, stats
from .cards_base import LIVE, PLAYS, Card, _once, current_is_admin, current_sees, duration, live
from .i18n import translate as _t
from .jellyfin import digits_only

NAME = seerr.NAME

# The sections of the page /seerr and the names of their cards, in the order on the
# page. The first two cards (pending and latest requests) have no section heading -
# they have their own. The layout is in seerr.html; that they agree is guarded by
# tests/test_seerr_cards.py.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("", ("pending", "latest")),
    ("Requests in the period", ("numbers", "by_day", "when", "states", "who", "speed")),
    ("Requested and watched?", ("watching", "nobody")),
)
NAMES: tuple[str, ...] = tuple(name for _, names in SECTIONS for name in names)

# The groups of requests (stats.seerr_handling) - labels in the order they are shown in.
STATE_LABELS = {"available": "in the library", "approved": "approved, not available yet", "waiting": "waiting",
                "removed": "the title is gone from the library", "declined": "declined", "failed": "failed",
                "deleted": "deleted in Seerr"}
# The same groups briefly - the badge on a poster a hundred pixels wide.
STATE_SHORT = {"available": "in the library", "approved": "approved", "waiting": "waiting",
               "removed": "gone from the library", "declined": "declined", "failed": "failed",
               "deleted": "deleted in Seerr#neuter"}

# How many rows of a long list a card gets at most (a strip of posters, tables).
ROWS = insights.INSIGHT_ROWS
# How many pending requests the card shows as blocks. It is a list to handle, not an
# archive: the longest waiting are first and the card says how many the rest are.
MAX_PENDING = 24
# The poster's width the cards ask for - the same as "Recently added", so it is the
# same file in the image cache.
POSTER_WIDTH = 300


def card_key(name: str) -> str:
    """The card's key in the registry: `seerr_<name>` - stored in the layouts of dashboards (before 1.9.0 the name was
    Czech, converted by schema_names; `seerr_who` is also the name of a permission area)."""
    return f"seerr_{name}"


def poster(row: Mapping[str, Any]) -> str:
    """The address of the request's poster, or "" (then the substitute poster is drawn right away).

    A title from the library (`poster_id`, see stats.seerr_posters) has the poster
    from Jellyfin through /image/ - with the image's tag in the address, so after the
    poster changes in Jellyfin it is another address and nothing has to be
    invalidated. Jellyfin takes precedence: as soon as the title comes into the
    library, the address leads to it, whether a provisional poster exists or not.

    A title outside the library has a provisional poster from the route
    /seerr/poster/… (web/seerr.py, seerr/posters.py): from Radarr or Sonarr, otherwise
    from Seerr. Whether some app really has it shows only when the image loads - when
    not, the page shows a substitute.
    """
    if row.get("poster_id"):
        tag = f"&tag={row['poster_tag']}" if row.get("poster_tag") else ""
        return f"/image/{row['poster_id']}?kind=Primary&w={POSTER_WIDTH}{tag}"
    kind, tmdb_id = str(row.get("media_type") or ""), str(row.get("tmdb_id") or "")
    if kind in ("movie", "tv") and digits_only(tmdb_id):
        return f"/seerr/poster/{kind}/{tmdb_id}"
    return ""


def detail(row: Mapping[str, Any]) -> str | None:
    """The address of the requested title's detail in the library, or None (the title is not in the library).

    The same rule as for the poster: a title is in the library when it has
    `poster_id` (stats.seerr_posters) - for a movie it is the id of its item, for a
    series the series' id, as the library has them stored. A title still being
    waited for has no link: there would be nowhere to go.
    """
    if not row.get("poster_id"):
        return None
    return f"/series/{row['poster_id']}" if row.get("media_type") == "tv" else f"/item/{row['poster_id']}"


def seerr_period(period: Any) -> Any:
    """The period of the Seerr cards: "the whole history" starts with the first request.

    Seerr tends to be older than Jellyscope; with the playback history older requests
    would quietly drop out (and without a playback "the whole history" would be today).
    """
    if stats._as_period(period).whole_history:
        return stats.whole_history_period(first=stats.seerr_first())
    return period


# ---------------------------------------------------------------------------
# Shared intermediate results - computed once in one data_for() (_once)
# ---------------------------------------------------------------------------

def _events(period: Any) -> dict[str, Any]:
    return _once(stats.seerr_events, period)


def _handling(period: Any) -> dict[str, Any]:
    return stats.seerr_handling(period, analysis=_events(period))


def _watching(period: Any) -> dict[str, Any]:
    return stats.seerr_watching(period, analysis=_events(period))


def _poster_hint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The hint under a card with posters: why some are missing and where to turn it on.

    Only for the administrator (only they can do something about it - and Seerr's
    address is no business of the others), only when Seerr gives no images ("Enable
    Image Caching" off) and only when the card has at least one provisional poster. A
    card with only posters from Jellyfin needs no hint - "Nobody started" thus has it
    only exceptionally.
    """
    turned_off = (current_is_admin() and seerr.images_state() == "0"
                  and any(str(r.get("poster") or "").startswith("/seerr/poster/") for r in rows))
    return {"images_off": turned_off, "seerr_settings": seerr.settings_link() if turned_off else ""}


def _row_with_poster(row: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    """A copy of a row for the template: with the poster's address, with the address of
    the title's detail in the library (`detail_url`, None = not in the library) and -
    without the area "...including who requested" - without the requester's name and id.

    A copy, not a change in place: the rows come from shared results (the memo of the
    computation, the live card's cache) that someone else reads too.
    """
    result = {**row, "poster": poster(row), "detail_url": detail(row), **extra}
    if not current_sees("seerr_who"):
        result.pop("user_id", None)
        result.pop("user_name", None)
    return result


# ---------------------------------------------------------------------------
# The data of the single cards - a dict the template reads as `d`
# ---------------------------------------------------------------------------

def _d_pending() -> dict[str, Any]:
    """Live: the requests waiting for approval now, from the longest waiting."""
    state = live(seerr.pending)
    requests = [_row_with_poster(z, waiting=duration(z["waiting_seconds"]))
                for z in stats.seerr_with_posters(state["requests"][:MAX_PENDING])]
    return {"available": state["available"],
            # The error's detail (an internal address, a piece of Seerr's answer) belongs to the administrator.
            "error": state["error"] if current_is_admin() else "",
            "count": state["count"], "requests": requests, "more_count": max(0, state["count"] - len(requests)),
            **_poster_hint(requests)}


def _d_latest(period: Any) -> dict[str, Any]:
    """Live: Seerr's newest requests over the period's stored ones (stats.seerr_with_live) - the states are the
    present ones, the same as "Waiting for approval" shows. Seerr does not answer: the stored requests and when the
    last sync saw them (`synced_at`), for the note under the heading."""
    events = _events(period)
    fresh = live(seerr.latest_requests)
    if fresh["available"]:
        events = stats.seerr_with_live(period, fresh, analysis=events)
    rows = [_row_with_poster(r, state=_t(STATE_SHORT[r["group"]]), state_full=_t(STATE_LABELS[r["group"]]))
            for r in stats.seerr_latest(period, limit=ROWS, analysis=events)]
    return {"rows": rows, "total": len(events["requests"]), "live": fresh["available"],
            "synced_at": "" if fresh["available"] else stats.seerr_synced_at(), **_poster_hint(rows)}


def _d_numbers(period: Any) -> dict[str, Any]:
    return stats.seerr_summary(period, analysis=_events(period))


def _d_by_day(period: Any) -> dict[str, Any]:
    by_day = stats.seerr_by_day(period, analysis=_events(period))
    return {"body": stats.chart_days(by_day, period, ("movies", "series_list"))}


def _d_when(period: Any) -> dict[str, Any]:
    return {"heatmap_data": stats.seerr_heatmap(period, analysis=_events(period))}


def _d_states(period: Any) -> dict[str, Any]:
    states = _once(_handling, period)["states"]
    return {"rows": [{"label": _t(label), "count": states[group]}
                      for group, label in STATE_LABELS.items() if states[group]]}


def _d_who(period: Any) -> dict[str, Any]:
    return {"rows": stats.seerr_requesters(period, analysis=_events(period))[:ROWS]}


def _d_speed(period: Any) -> dict[str, Any]:
    handling = _once(_handling, period)
    return {part: {"median": duration(handling[part]["median"]),
                   # 90 % makes sense to write only where there is something to count it from.
                   "p90": duration(handling[part]["p90"]) if handling[part]["p90"] is not None else "",
                   "count": handling[part]["count"]}
            for part in ("to_library", "to_approve")} | {"continuation": handling["to_library"]["continuation"]}


def _d_watching(period: Any) -> dict[str, Any]:
    watching = _once(_watching, period)
    return {k: watching[k] for k in ("available_count", "requester", "other", "nobody")}


def _d_nobody(period: Any) -> dict[str, Any]:
    # A poster from the library only for the rows that are drawn - not for the whole list.
    rows = [_row_with_poster(r) for r in stats.seerr_with_posters(_once(_watching, period)["nobody_list"][:ROWS])]
    # The title need not be in Jellyscope's library here, even when Seerr reports it as
    # available (it vanished from Jellyfin, or is not scanned yet): then it has a
    # provisional poster and the administrator should learn why a substitute stands there.
    return {"rows": rows, **_poster_hint(rows)}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _card(name: str, title: str, description: str, data: Callable[[Any], dict[str, Any]] | None = None, *,
          width: str = "full", area: str = "seerr", table_window: bool = False, sources: tuple[str, ...] = ()) -> Card:
    """One Seerr card. `data(period)` returns a dict for the template; the registry
    gets it under the card's key. Without `data` it is the live card "Waiting for approval".
    `sources` only where the default (cards_base.default_sources) does not hold."""
    key = card_key(name)

    def compute(period: Any) -> dict[str, Any]:
        if data is None:
            return {key: _d_pending()}
        return {key: data(_once(seerr_period, period))}

    return Card(key, title, description, f"_card_seerr_{name}.html", compute, uses_period=data is not None,
                width=width, area=area, has_table_window=table_window, group=NAME, available=seerr.is_connected,
                sources=sources)


CARDS: tuple[Card, ...] = (
    _card("pending", "Waiting for approval",
          "Requests waiting for approval in Seerr right now, longest waiting first."),
    _card("latest", "Latest requests",
          "A strip of posters of the most recently requested titles and how each request ended.", _d_latest, table_window=True,
          # Seerr live; the stored requests when it does not answer.
          sources=(LIVE, "seerr")),
    _card("numbers", "Request numbers",
          "How many requests came in: movies, series, 4K and automatic ones from watchlists.", _d_numbers),
    _card("by_day", "Requests per day",
          "Requests for movies and series day by day.", _d_by_day),
    _card("when", "When requests are made",
          "A grid of days and hours: when most requests are made.", _d_when, width="half"),
    _card("states", "How requests ended",
          "How many requests are in the library, approved, pending and declined.", _d_states, width="half"),
    # An area of its own: the card is all about people. Whoever does not have
    # "...including who requested" does not get the card and its data are not computed.
    _card("who", "Who requests",
          "A ranking of requesters: how much they asked for, what was approved and what they watched themselves.", _d_who,
          width="half", area="seerr_who", table_window=True, sources=("seerr", PLAYS)),
    _card("speed", "How fast requests are handled",
          "How long it takes from a request to approval and to the library.", _d_speed, width="half"),
    _card("watching", "Requested and watched?",
          "Requested titles in the library: did the requester watch them, someone else, or nobody?", _d_watching,
          sources=("seerr", PLAYS)),
    _card("nobody", "Nobody started",
          "Requested titles that are in the library and nobody has started since the request.", _d_nobody, table_window=True,
          sources=("seerr", PLAYS)),
)

# Not `assert`: Python with -O leaves it out and a difference between SECTIONS and the cards would show only on the page.
if [card.key for card in CARDS] != [card_key(name) for name in NAMES]:
    raise RuntimeError("the Seerr cards do not match the page's sections (cards_seerr.SECTIONS)")
