"""The play history.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import access, db, i18n, stats
from .base import _context, _czech_date, _self_refresh, own_viewer, require_login, templates
from .periods import _VALID_DAY, _date_from_text

router = APIRouter()


# The periods a person chooses in the history. The key goes into the address, so
# a link to "the last 7 days" holds tomorrow too - unlike fixed dates.
PERIOD_CHOICES = {
    "all": ("all", None),
    "today": ("today", 0),
    "7": ("last 7 days", 7),
    "30": ("last 30 days", 30),
    "90": ("last 90 days", 90),
    "365": ("last year", 365),
    "custom": ("custom…", -1),
}


# The offer of players, languages and methods counts the whole history (three GROUP BYs - with a hundred thousand
# plays most of the page's time), so the page keeps it: until a new play arrives (the highest row number changes)
# or FILTER_OPTIONS_TTL seconds pass - a deleted or rewritten play leaves the offer at the latest then.
FILTER_OPTIONS_TTL = 60.0
_FILTER_OPTIONS: dict[str, Any] = {"at": None, "key": None, "options": None}


def _filter_options() -> dict[str, list[Any]]:
    key = db.query_value("SELECT MAX(id) FROM playback")
    now = time.monotonic()
    if (_FILTER_OPTIONS["at"] is None or _FILTER_OPTIONS["key"] != key
            or now - _FILTER_OPTIONS["at"] > FILTER_OPTIONS_TTL):
        _FILTER_OPTIONS.update(at=now, key=key, options=stats.filter_values())
    return _FILTER_OPTIONS["options"]


def _resolved_filters(
    account: dict[str, Any] | None,
    user_id: str | None,
    search: str | None,
    day: str | None,
    kind: str | None,
    period: str | None,
    since: str | None,
    until: str | None,
    method: str | None,
    client: str | None,
    language: str | None,
    options: dict[str, list[Any]] | None = None,
) -> tuple[dict[str, Any], frozenset[str], str]:
    """What the filter in the address finds for the account: the filters of stats.history and history_count,
    the kinds left out (access.hidden_kinds) and the period chosen.

    One place for the page and for the count of its new plays (history_new): were the count to build the filter
    itself, the number in the bar would drift from what loading the page again shows. `options` = the filter's
    offer (_filter_options) when the caller has it already; otherwise it is read only when a method, a player or a
    language is chosen - only those are checked against it, and it counts the whole history.
    """
    # A viewer signed in with their Jellyfin account sees only their own history -
    # whatever they write into the address. So the filter is forced on them
    # here, not in the template: the template decides what is visible, not
    # what is loaded.
    viewer = own_viewer(account)
    if viewer:
        user_id = viewer
    else:
        # With hidden names a placeholder id comes from the offer; the real id
        # of somebody else finds nothing (access.viewer_from_address).
        user_id = access.viewer_from_address(user_id, account)

    # The day comes by a click-through from the Overview's table; it is let into
    # SQL only in the shape YYYY-MM-DD. It goes there as a parameter, so it would be
    # safe anyway - this is extra, against typos that would silently return
    # an empty list.
    valid_day = day if day and _VALID_DAY.match(day) else None

    period = period if period in PERIOD_CHOICES else ("custom" if (since or until) else "all")
    if period == "custom":
        since_iso, until_iso = _date_from_text(since), _date_from_text(until)
    else:
        days = PERIOD_CHOICES[period][1]
        # "The last 7 days" are today and the six before it - as `days=7` in the
        # API (api.history) and the seven rows of a chart by days; "today" is 0.
        since_iso = ((date.today() - timedelta(days=max(0, days - 1))).isoformat()
                     if days is not None and days >= 0 else None)
        until_iso = None

    # The kind is sent here by a click-through in a table, not from the session - a
    # filter in the history is not to overwrite the choice on the Overview. Without a
    # choice it is absolutely everything, music too. "both" (watching, without music)
    # is not in the switch, but comes with a click-through from a day on the Overview:
    # the list then has to show the very numbers the person clicked.
    # Music and books only for whoever may see who listened and who read (access.hidden_kinds) - neither in the
    # list nor in the switch.
    hidden = access.hidden_kinds(account)
    kind = (kind if (kind in stats.HISTORY_KINDS or kind == stats.KIND_BOTH) and kind not in hidden
            else stats.KIND_ALL)

    # The play method is compared against a fixed list and against what the
    # history holds (imported variants such as "Transcode (v:h264 a:direct)");
    # players and languages against what really is in the history. The offer is
    # built from the data, so it never holds a choice that would find nothing.
    # Without a method outside the fixed list, a player or a language other than "und" chosen, an empty offer
    # decides the same as the whole one - and the count of new plays, asked often, need not read it.
    if options is None:
        options = (_filter_options()
                   if client or (language and language != "und") or (method and method not in stats.PLAY_METHODS)
                   else {"methods": [], "clients": [], "languages": []})
    method = method if method in stats.PLAY_METHODS or method in options["methods"] else None
    client = client if client in options["clients"] else None
    language = language if (language == "und"
                            or language in options["languages"]) else None

    filters = {
        "user_id": user_id or None,
        "search": (search or "").strip() or None,
        "day": valid_day,
        "kind": kind,
        "since": since_iso,
        "until": until_iso,
        "method": method,
        "client": client,
        "language": language,
    }
    return filters, hidden, period


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    page: int = 1,
    search: str | None = None,
    user_id: str | None = None,
    day: str | None = None,
    kind: str | None = None,
    period: str | None = None,
    since: str | None = None,
    until: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """The play history with a filter that can be combined.

    All the choices are in the address, not in the session: a filter in the history
    is a one-off question ("who watched movies last week"), not a setting that is
    to survive moving elsewhere. So the result can be sent as a link.
    """
    page = max(1, page)
    per_page = 50

    options = _filter_options()
    filters, hidden, period = _resolved_filters(account, user_id, search, day, kind, period, since, until,
                                                method, client, language, options)
    user_id, valid_day, kind = filters["user_id"], filters["day"], filters["kind"]
    since_iso, until_iso = filters["since"], filters["until"]

    total = stats.history_count(**filters, hidden_kinds=hidden)
    page_count = max(1, -(-total // per_page))
    # The newest play the filter finds: History's bar of new plays counts from it (history_new).
    last_id = stats.history_last_id(**filters, hidden_kinds=hidden) if _self_refresh() else 0
    page = min(page, page_count)

    return templates.TemplateResponse(request, "history.html", _context(
        request, account,
        rows=stats.history(limit=per_page, offset=(page - 1) * per_page, **filters, hidden_kinds=hidden),
        hidden_kinds=hidden,
        # `is_disabled` only so that anonymising knows the row is a viewer (a
        # library or a title has a bare id and a name too) - otherwise the
        # offer would show real names and ids. With hidden names by the id,
        # that is by the number of "Viewer N": the order by name would give them away.
        users=db.query_all(
            "SELECT id, name, is_disabled FROM users ORDER BY id"
            if access.hides_names(account)
            else "SELECT id, name, is_disabled FROM users ORDER BY name"),
        choices=options,
        total=total,
        last_id=last_id,
        page=page,
        pages=page_count,
        period=period,
        period_choices=[(key, i18n.translate(label))
                        for key, (label, _span) in PERIOD_CHOICES.items()],
        # The form gets the date the Czech way, the address gets ISO.
        since_text=_czech_date(since_iso),
        until_text=_czech_date(until_iso),
        **filters,
        filter_active=(any(value for key, value in filters.items()
                           if key != "kind")
                       or kind != stats.KIND_ALL or period != "all"),
        # For the chips above the table: how many choices are on and what they
        # are called. A number on the button is clear at a glance, a list of ids
        # is not. The number on the button has to agree with the count of chips
        # under it. So the dates are not counted: they are only another shape of
        # the choice "period", and were they counted separately, the button
        # would report one more than is visible.
        filter_count=(sum(1 for key, value in filters.items()
                          if value and key not in ("kind", "since", "until", "day"))
                      + (1 if kind != stats.KIND_ALL else 0)
                      + (1 if valid_day or period != "all" else 0)),
        user_name=(next((r["name"] for r in db.query_all(
            "SELECT id, name FROM users WHERE id = ?", (user_id,))), user_id)
                   if user_id else ""),
        period_label=i18n.translate(PERIOD_CHOICES[period][0]),
    ))


@router.get("/history/new")
def history_new(
    known: int = 0,
    known_id: int | None = None,
    search: str | None = None,
    user_id: str | None = None,
    day: str | None = None,
    kind: str | None = None,
    period: str | None = None,
    since: str | None = None,
    until: str | None = None,
    method: str | None = None,
    client: str | None = None,
    language: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """How many plays History with this filter would add on top after a reload - its bar "N new plays - show"
    (history.html, asked by static/js/app.js when the plays' marker in /health moved). `known` = how many the page
    showed, `known_id` = the newest play's row it showed (history_last_id).

    The plays recorded after the newest one shown, and at least the difference to the `known` count: plays leaving
    the filter meanwhile (a rolling period passing midnight, the history retention, a deletion) do not cancel the
    new ones out, and a play that ran since before the page and got its watched time only now still counts. An
    address without `known_id` (a page drawn before it) counts the difference only.

    The page does not rewrite its table under the reader's eyes; the bar's link loads it again. So the number is
    counted by the page's own filter and rights (_resolved_filters, the same history_count): a viewer counts only
    their own plays, kinds the account may not see with the person are not counted. Behind History's own gate:
    the path is under /history (access.may_open). The text is in the reader's language, with the number's form;
    nothing new = nought and no text.
    """
    filters, hidden, _period = _resolved_filters(account, user_id, search, day, kind, period, since, until,
                                                 method, client, language)
    count = max(0, stats.history_count(**filters, hidden_kinds=hidden) - max(0, known))
    if known_id is not None:
        count = max(count, stats.history_count(**filters, hidden_kinds=hidden, after_id=max(0, known_id)))
    return {"count": count, "text": i18n.translate_plural("{n} new plays", count) if count else ""}
