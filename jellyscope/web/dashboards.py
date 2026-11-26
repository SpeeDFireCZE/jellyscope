"""Custom dashboards (for everyone and My dashboard) and the partials several pages share.

A part of the web package (see jellyscope/web/__init__.py). The watching overview, Comparison,
Insights and the chart moved to the section web/video/ on 16. 11. 2026;
`_card_choices` stays here, because the custom dashboards use it too, and so does `_registry_page`, the
context of a page drawn from the card registry, and /partials/cards, any of its cards drawn again."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import access, cards, stats
from ..i18n import translate as _t
from .base import (
    _context,
    _flash,
    require_admin,
    require_login,
    templates,
)
from .periods import (
    TOP_KIND_SESSION_KEY,
    _days,
    _growth_disk,
    _kind,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _card_choices(request: Request, kind: str | None = None,
                  top_kind: str | None = None) -> dict[str, str]:
    """The choices of the Movies/Series cards from the address, otherwise from the session (see _kind).

    For the Overview and a custom dashboard - it is the same card, so the choice
    holds on both pages.
    """
    # The chips of the cards link to "?kind=…" on the page they are on (16. 11. 2026), so a
    # dashboard reads the choice from its own address too, not only the overview's parameters.
    kind = kind if kind is not None else request.query_params.get("kind")
    top_kind = top_kind if top_kind is not None else request.query_params.get("top_kind")
    return {"kind": _kind(request, kind),
            "top_kind": _kind(request, top_kind, TOP_KIND_SESSION_KEY),
            # The disk above the Library growth chart - from the address of every page
            # with a dashboard (Overview, custom, My dashboard), otherwise from the session.
            "growth_disk": _growth_disk(request, request.query_params.get("growth_disk"))}


def _registry_page(request: Request, account: dict[str, Any] | None, keys: Sequence[str], period: Any,
                   computed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What a page drawn from the card registry hands its template: its cards, their keys and their data.

    `keys` are the page's cards in its order; the template places them by show_card(). Only the cards the
    account may see and that are available are computed and drawn (cards.allowed) - in ONE data_for, so what
    several cards share is computed once. The Overview, the *Arr and Seerr pages go the same way.

    `computed` are the data the page has already computed for some of its cards, by the card's key (a section
    of the Library reads its library once for its numbers and its cards): drawn the same, not computed again.
    """
    computed = computed or {}
    allowed = cards.allowed([cards.BY_KEY[key] for key in keys], account)
    data = cards.data_for([card for card in allowed if card.key not in computed], period, _card_choices(request), account)
    data.update({card.key: computed[card.key] for card in allowed if card.key in computed})
    return {"cards": {card.key: card for card in allowed}, "page_card_keys": list(keys), **data}


# The most keys one request may ask for: every card of the registry once - a custom dashboard can hold all of
# them, and no page holds more. A longer list can come only from a broken client and is refused unread.
MAX_CARD_KEYS = len(cards.ALL_CARDS)


@router.get("/partials/cards", response_class=HTMLResponse)
def cards_partial(request: Request, keys: str = "", in_dashboard: bool = False,
                  days: int | None = None, since: str | None = None, until: str | None = None,
                  account: dict[str, Any] = Depends(require_login)):
    """The asked cards of an open page drawn again - so that the page can refresh its cards by itself.

    `keys` are the cards' keys separated by commas, the period (days / since / until, or a dragged
    selection), the cards' choices (kind, top_kind, growth_disk) and `in_dashboard` are those of the page
    that asks; without them the session's apply, as on every page. Every card is drawn by _card.html, in its
    slot with its key - the browser takes the slots apart by `data-card-key`, in the asked order.

    The rights are those of the account that asks, card by card, as on the page (cards.allowed): a card it
    may not see or that is not available, an unknown key and a repeated one are simply not in the answer -
    the same answer as for a key that does not exist, so it does not tell what is there. ONE data_for for
    the whole list (what cards share is computed once), in a thread: a plain function, FastAPI runs it in
    its pool, and the live cards need that (cards_base.live).

    The page asks by itself, so it must not change what the session remembers: the period and the choices
    it reads are put back, and so is a message waiting for the next page (_context takes it out).
    """
    asked = [key.strip() for key in keys.split(",") if key.strip()]
    if len(asked) > MAX_CARD_KEYS:
        raise HTTPException(status_code=400)
    known = [cards.BY_KEY[key] for key in dict.fromkeys(asked) if key in cards.BY_KEY]
    allowed = cards.allowed(known, account)
    remembered = dict(request.session)
    try:
        period = _days(request, days, since, until)
        data = cards.data_for(allowed, period, _card_choices(request), account)
        context = _context(request, account, days=period, in_dashboard=in_dashboard, asked_cards=allowed, **data)
    finally:
        request.session.clear()
        request.session.update(remembered)
    return templates.TemplateResponse(request, "_cards_partial.html", context)


@router.get("/partials/recently-added", response_class=HTMLResponse)
def recently_added_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Only the "Recently added" strip.

    Until this existed, a person had to refresh the page by hand after a
    synchronisation finished - otherwise a new movie did not show on an Overview
    already open. Now the page replaces it by itself once the library's version changes.

    Finished HTML is returned, not JSON: it is drawn by **the same template** as at
    a normal load, so there is no second place that drifts from the first in time.
    """
    return templates.TemplateResponse(request, "_recently_added.html", _context(
        request, account,
        recent=stats.recently_added(),
    ))


@router.get("/partials/network-live", response_class=HTMLResponse)
def network_live_partial(
    request: Request,
    days: int | None = None,
    since: str | None = None,
    until: str | None = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Only the "Flowing right now" card of the Network page.

    The same idea as with "now playing": the Network page showed the state
    of the moment it loaded, so whoever left it open saw the past.
    Only this partial is refreshed - were the whole page loaded, it would
    scroll to the top and throw away the address table being read.
    """
    # The page sends its own period (network.html): the session's is the last one chosen in any tab, and the card
    # would switch to it at its next refresh. Without the parameters the session's holds, as on every page.
    # The card asks by itself (every few seconds, on coming back to the tab), so its period must not become
    # "the last one chosen": what the session remembers is put back.
    remembered = dict(request.session)
    days = _days(request, days, since, until)
    request.session.clear()
    request.session.update(remembered)
    return templates.TemplateResponse(request, "_network_live.html", _context(
        request, account,
        days=days,
        now=stats.flow_now(),
        live=stats.bandwidth_live(days),
    ))


@router.get("/partials/now-playing", response_class=HTMLResponse)
def now_playing_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Only the "Now playing" card.

    The collector asks Jellyfin every few seconds, but the page did not know -
    it showed the state of the moment it loaded until a person refreshed it by hand.
    The Overview fetches this partial by itself in the same rhythm.

    Finished HTML is returned, not JSON: it is drawn by **the same template** as at
    a normal load, so there is no second place that drifts from the first in time.
    """
    return templates.TemplateResponse(request, "_now_playing.html", _context(
        request, account,
        active=access.visible_plays(stats.active_sessions(), account),
    ))


@router.get("/dashboard", response_class=HTMLResponse)
def custom_dashboard(request: Request, days: int | None = None,
                     since: str | None = None, until: str | None = None,
                     account: dict[str, Any] = Depends(require_login)):
    """A dashboard put together from existing cards.

    Switched off it is as if it were not there: the tab is not in the menu and the
    address leads back to the Overview. It is switched on in Settings -> Interface.
    The same for whoever has nothing to see in it by their rights
    (cards.sees_dashboard, the same as the menu) - only the administrator may come
    here with an empty one too, to build it.
    """
    if not (cards.sees_dashboard(account)
            or (account.get("is_admin") and cards.is_enabled())):
        return RedirectResponse("/", status_code=303)
    return _render_dashboard(request, account, _days(request, days, since, until),
                             personal=False)


@router.get("/account/dashboard", response_class=HTMLResponse)
def my_dashboard(request: Request, days: int | None = None,
                 since: str | None = None, until: str | None = None,
                 account: dict[str, Any] = Depends(require_login)):
    """My dashboard - a page the account puts together itself and only it sees.

    The account is taken from the session, never from the address: somebody else's
    My dashboard cannot be opened. A card the account lost the permission for
    meanwhile stays in its place with a note why - the account added it itself, so
    it is not to guess what broke.
    """
    return _render_dashboard(request, account, _days(request, days, since, until),
                             personal=True)


def _render_dashboard(request: Request, account: dict[str, Any], period: Any,
                      *, personal: bool):
    """The dashboard page - for everyone and My dashboard, one template.

    Both show only the data of the cards the account may see; the others are not
    even computed, otherwise a dashboard would be a back door to pages a person may
    not open. The dashboard for everyone leaves such a card out, My dashboard keeps
    it in place with a note why.

    The same holds for the card of an app the administrator disconnected
    (cards.is_available): it is not computed and the dashboard for everyone does not
    draw it, in My dashboard it stays in place with a sentence. But it does not vanish
    from the layout - whoever builds the dashboard still has its tile in the layout
    window, otherwise the first save of another change would silently throw it out
    and it would not come back once the app is connected.
    """
    if personal:
        layout = cards.load_layout(account["id"])
        heading, page_url = _t("My overview"), "/account/dashboard"
        save_action, can_edit = "/account/dashboard", True
    else:
        # The heading and the window's title: the name from the administrator. Empty
        # only for a dashboard switched on before the name was entered, and without a
        # restart (the migration fills it in at start) - then what it used to be called.
        heading, page_url = cards.dashboard_name() or _t("Custom overview"), "/dashboard"
        # In the demo the button stays visible on purpose - the same as everywhere
        # else. `_demo_blocks` stops the action and says so aloud.
        save_action, can_edit = "/dashboard/layout", bool(account.get("is_admin"))
        saved = cards.load_layout()
        layout = ([card for card in saved if cards.may_see(card, account)] if can_edit
                  else cards.allowed(saved, account))
    allowed = cards.allowed(layout, account)
    # ONLY what is put together, allowed and available is computed - the same
    # as on the Overview (the route overview), which goes through the same registry.
    data = cards.data_for(allowed, period, _card_choices(request), account)
    return templates.TemplateResponse(request, "dashboard.html", _context(
        request, account,
        days=period,
        heading=heading,
        # The cards know they are not on their own page (the map does not offer
        # downloading GeoIP, the users table has another heading).
        in_dashboard=True,
        save_action=save_action,
        page_url=page_url,
        personal=personal,
        layout=layout,
        # Is there anything to draw on the page? My dashboard draws everything (notes
        # in place of cards too), the dashboard for everyone only allowed available cards.
        drawn=bool(layout if personal else allowed),
        # Card -> the area the note names: the one missing (a sub-area's parent when that is off).
        without_permission={card.key: access.missing_area(account, card.area) for card in layout if not cards.may_see(card, account)},
        unavailable={card.key for card in layout if not cards.is_available(card)},
        # The names of the areas for the note "you have no permission for the area …".
        areas=access.BY_KEY,
        # The window gets the whole registry the account may see; what is in the
        # dashboard already only greys out. Were the used cards left out, the list
        # would jump while adding and removing and a person would lose their place.
        # Only for whoever may build - otherwise the window is not even drawn.
        # By groups with a heading (area, app): there are nearly a hundred and fifty cards.
        choices=cards.groups(cards.allowed(list(cards.ALL_CARDS), account)) if can_edit else [],
        used={card.key for card in layout},
        widths=cards.WIDTHS,
        # The period switch makes sense only when at least one card uses
        # it - otherwise it would stand there and do nothing.
        needs_period=any(card.uses_period for card in allowed),
        # The cards with a long table (Insights, *Arr, Seerr, music, books) show a few rows and open the rest in a
        # shared window.
        needs_table_window=any(card.has_table_window for card in allowed),
        may_edit=can_edit,
        **data,
    ))


@router.post("/dashboard/layout")
def custom_dashboard_save(request: Request, order: str = Form(""),
                          account: dict[str, Any] = Depends(require_admin)):
    """Saves the order of the cards as put together.

    "key:width" separated by commas come here - exactly what the form's hidden
    field holds, which JavaScript updates while rearranging.
    """
    saved = cards.save_layout(order or "")
    _flash(request, "Saved." if saved else "The overview is empty now.",
           "success" if saved else "info")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/account/dashboard")
def my_dashboard_save(request: Request, order: str = Form(""),
                      account: dict[str, Any] = Depends(require_login)):
    """Saves My dashboard - always for the account from the session, `account_id` from
    the form is not read at all.

    A card the account may see can be saved, or a card that is in its dashboard
    already (even without the right - otherwise rearranging would silently lose it).
    Somebody else's card cannot be added by a forged form: it would fall among those
    "without the right" and show only a note, but it does not belong in the database.
    """
    permitted = ({card.key for card in cards.allowed(list(cards.ALL_CARDS), account)}
                 | {card.key for card in cards.load_layout(account["id"])})
    items = [(key, width) for key, width in cards.parse_layout(order or "")
             if key in permitted]
    saved = cards.save_layout(items, account["id"])
    _flash(request, "Saved." if saved else "The overview is empty now.",
           "success" if saved else "info")
    return RedirectResponse("/account/dashboard", status_code=303)
