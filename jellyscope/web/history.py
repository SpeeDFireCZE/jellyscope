"""The play history.

A part of the web package (see jellyscope/web/__init__.py)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .. import access, db, i18n, stats
from .base import _context, _czech_date, own_viewer, require_login, templates
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
        since_iso = ((date.today() - timedelta(days=days)).isoformat()
                     if days is not None and days >= 0 else None)
        until_iso = None

    # The kind is sent here by a click-through in a table, not from the session - a
    # filter in the history is not to overwrite the choice on the Overview. Without a
    # choice it is absolutely everything, music too. "both" (watching, without music)
    # is not in the switch, but comes with a click-through from a day on the Overview:
    # the list then has to show the very numbers the person clicked.
    kind = kind if kind in stats.HISTORY_KINDS or kind == stats.KIND_BOTH else stats.KIND_ALL

    # The play method is compared against a fixed list; players and languages
    # against what really is in the history. The offer is built from the data,
    # so it never holds a choice that would find nothing.
    options = stats.filter_values()
    method = method if method in stats.PLAY_METHODS else None
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

    total = stats.history_count(**filters)
    page_count = max(1, -(-total // per_page))
    page = min(page, page_count)

    return templates.TemplateResponse(request, "history.html", _context(
        request, account,
        rows=stats.history(limit=per_page, offset=(page - 1) * per_page, **filters),
        # `is_disabled` only so that anonymising knows the row is a viewer (a
        # library or a title has a bare id and a name too) - otherwise the
        # offer would show real names and ids. With hidden names by the id,
        # that is by the number of "Divák N": the order by name would give them away.
        users=db.query_all(
            "SELECT id, name, is_disabled FROM users ORDER BY id"
            if access.hides_names(account)
            else "SELECT id, name, is_disabled FROM users ORDER BY name"),
        choices=options,
        total=total,
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
