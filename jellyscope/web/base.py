"""The base of the web: what every page needs.

Templates, putting the context together, flash messages, the sign-in guard and
helpers around the period. It lives here, and not in `__init__.py`, for a single
reason: the routes are split into several files and all of them need these. Were
they to stay with the routes, the modules would have to import each other.

Nothing here knows `app`. That is on purpose - the base must not depend on how the
application is put together, otherwise it could not be used from elsewhere.
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from .. import (access, accounts, cards, collector, conversion, db, detailed_map, formatting, i18n, navigation,
                scanner, seasonal, stats, updates)
from ..config import load_config


log = logging.getLogger("jellyscope.web")


# PROJECT_DIR is the root of the project (where run.py and the data folder are),
# PACKAGE_DIR is the package jellyscope/ (templates, static). They are not the same
# and are easy to mix up - hence different names instead of one BASE_DIR. This
# file lies one folder deeper (web/), hence parent twice.
PACKAGE_DIR = Path(__file__).resolve().parent.parent


TEMPLATES_DIR = PACKAGE_DIR / "templates"


config = load_config()


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def current_account(request: Request) -> dict[str, Any] | None:
    """Who is signed in. Returns None when nobody.

    The cookie holds only the account's ID (signed, so it cannot be forged).
    Everything else is rather read from the database - when somebody took the
    account's rights away meanwhile, it shows at once, not only after signing out.
    """
    account_id = request.session.get("account_id")
    if not account_id:
        return None

    # One query per request, not three. The account is wanted by the language
    # middleware, viewer_scope_middleware and require_login - and an image or a style is a
    # request too. The session's fingerprint is part of the key: after a password
    # change within the same request the account is read again.
    key = (account_id, request.session.get("fingerprint"))
    stored = getattr(request.state, "request_account", None)
    if stored is not None and stored[0] == key:
        return stored[1]

    account = accounts.get(int(account_id))
    if account is None:
        # The account was deleted meanwhile - the cookie is thrown away.
        request.session.clear()
    elif request.session.get("fingerprint") != accounts.session_fingerprint(account):
        # The password changed since the sign-in (by the owner, or by the administrator).
        # A session that does not know about it ends - otherwise a stolen cookie would
        # stay valid and changing the password would solve nothing. A session without a
        # fingerprint (from before this check) ends too: after an update everybody
        # signs in once.
        request.session.clear()
        account = None
    elif accounts.disabled_in_jellyfin(account):
        # Disabled in Jellyfin since the sign-in: the sign-in would refuse them now, so the session ends too.
        request.session.clear()
        account = None
    request.state.request_account = (key, account)
    return account


def start_session(request: Request, account: dict[str, Any]) -> None:
    """Writes the sign-in into the session - one place for the password, the code and the first account.

    The old session is thrown away: it prevents an attack where somebody slips you
    their session ID before you sign in and then "rides along" in it. And the
    password's fingerprint must not be missing - current_account() ends a session
    without it at once, so a path that forgot it would silently sign the person out.
    """
    request.session.clear()
    request.session["account_id"] = account["id"]
    request.session["fingerprint"] = accounts.session_fingerprint(account)


def require_login(request: Request) -> dict[str, Any]:
    """The guard in front of every page. Returns the signed-in account."""
    if not accounts.any_exists():
        # The very first run - no account exists yet.
        raise HTTPException(status_code=307, headers={"Location": "/setup"})

    account = current_account(request)
    if account is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return account


def check_id(value: str) -> None:
    """Accept an id from the address only in the checked shape (ASCII letters and digits, dash, underscore), like /image/.

    A Jellyfin id is a hexadecimal GUID, so nothing valid is lost - anything else (a dot, a space, a non-ASCII
    character) is a 404 before the database is asked. Shared by the pages of an artist, an album and a book.
    """
    if not value or not all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
        raise HTTPException(status_code=404)


def require_content(section_key: str) -> Callable[[Request], None]:
    """A router dependency of a section (web/video/, web/music/): signed in first, then 404 without content.

    A section Jellyfin has no content for is not in the menu (navigation.menu) and none of its
    pages exist - one guard on the section's router covers every page, a new one included. A
    detail page other pages link to (an artist) stays outside it, like an album in the Library.
    """
    def guard(request: Request) -> None:
        require_login(request)
        navigation.require(section_key)
    return guard


# The rule lives in access.py, where the API keys share it too; from here it is
# only exposed under the name the web takes it by.
own_viewer = access.own_viewer


def require_admin(request: Request) -> dict[str, Any]:
    """The guard for what only the administrator may do."""
    account = require_login(request)
    if not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Only an administrator may do this.")
    return account


def _quick_periods() -> list[tuple[str, str, str]]:
    """The offer for the custom period window - (label, since, until) the Czech way.

    They are no further buttons of the switch but values to fill the fields with:
    a month and a year are questions asked often, and working out the date of the
    first day of last month in one's head is a nuisance.
    """
    today = date.today()
    first_of_this = today.replace(day=1)
    last_of_previous = first_of_this - timedelta(days=1)
    first_of_previous = last_of_previous.replace(day=1)
    return [
        ("this month", _czech_date(first_of_this.isoformat()),
         _czech_date(today.isoformat())),
        ("last month", _czech_date(first_of_previous.isoformat()),
         _czech_date(last_of_previous.isoformat())),
        ("this year", _czech_date(today.replace(month=1, day=1).isoformat()),
         _czech_date(today.isoformat())),
    ]


def _period_for_template(value: Any) -> dict[str, Any]:
    """What the switch at the top of the page needs to know about the period.

    The template then need not care whether it got a number or a pair of dates -
    it gets both prepared.
    """
    if isinstance(value, stats.Period):
        # The local bounds, not those of the query: in UTC another day would sit at midnight.
        since = (value.since_local or value.since)[:10]
        until = _last_day(value.until_local or value.until)
        return {"days": None, "since": since, "until": until,
                # The form gets the date the way a person writes it.
                "since_text": _czech_date(since), "until_text": _czech_date(until),
                # A selection dragged in a chart can be a part of a day. The form
                # cannot express it (it has calendars), but the page has to say
                # it - otherwise the selection "from 21:30 to 23:45" looks like a
                # whole day and the numbers under it make no sense.
                "since_label": _period_label(value, value.since_local, since),
                "until_label": _period_label(value, value.until_local, until),
                "own": True,
                "whole_history": value.whole_history}
    return {"days": int(value), "since": "", "until": "",
            "since_text": "", "until_text": "",
            "since_label": "", "until_label": "",
            "own": False, "whole_history": False}


def _period_label(period: stats.Period, local: str, day: str) -> str:
    """A date for a person, with the time only for a span selected in a chart.

    For whole days the time is not written - "20.8.2026 00:00" tells nobody anything.
    For an exact span it is, because without it the selection "from 21:30 to 23:45"
    looks like a whole day and the numbers under it make no sense.
    """
    if period.whole_day or not local:
        return _czech_date(day)
    return f"{_czech_date(local[:10])} {local[11:16]}"


def _last_day(until: str) -> str:
    """The day that still belongs to the period.

    The upper bound is exclusive, so one steps back from it - but by a SECOND, not
    by a day. For a whole day both come out the same (midnight minus a day and
    minus a second both fall on the day before), for a span selected in a chart
    no longer: an end at 23:45 still belongs to the same day, while minus a day
    would show yesterday.
    """
    try:
        return (datetime.strptime(until, db.TIME_FORMAT)
                - timedelta(seconds=1)).strftime("%Y-%m-%d")
    except ValueError:
        return until[:10]


# The bounds of the limits of long lists. Zero would mean "hide even a single
# stream", which is pointless; above fifty the card takes the screen anyway.
# The value itself is in the settings - see _limits().
LIMIT_MIN = 1


LIMIT_MAX = 50


# How zooming the map is controlled. The wheel is more comfortable, but the page
# stops scrolling above the map - a trap for some, a habit for others. Hence a
# choice, not a decision made for the user.
ZOOM_MODES = ("click", "wheel")

# What is under the dots on the map. "drawn" is the outline of the continents
# (offline), "osm" tiles from OpenStreetMap - the browser downloads those from the
# internet, so it is the administrator's choice and off by default. See web.csp().
MAP_BASEMAPS = ("drawn", "osm")


# The skins of the app. "new" are Jellyfin's colours, "classic" the original
# blue on a neutral background - see the end of style.css.
SKINS = ("new", "classic")


def _skin() -> str:
    """The chosen skin. Anything other than a known name is the new one."""
    value = db.get_setting("ui_skin", "new")
    return value if value in SKINS else "new"


def _exact_time() -> bool:
    """Is the time in charts written to the minute, or rounded?"""
    return formatting.exact_time()


# Long tables with a row count of their own in Settings → Interface → Long lists:
# {whose tables: (the settings key, the default count, the default count on a phone)}. The
# phone's key is the same with the suffix `_mobile`. Read from here (`_limits`) and saved
# (web/settings/interface.py); the templates ask by name (the macros trida_radku
# and tlacitko_cela_tabulka, `kde="arr"`). Another group of tables = a row here,
# a field in the form and a parameter in the saving route.
TABLE_LIMITS: dict[str, tuple[str, int, int]] = {
    "insights": ("ui_max_rows", 10, 5),
    "arr": ("ui_max_rows_arr", 10, 5),
    "seerr": ("ui_max_rows_seerr", 10, 5),
    "music": ("ui_max_rows_music", 10, 5),
}


def _limits() -> dict[str, Any]:
    """How many items a card lists straight away before it hides the rest in a window.

    Read from the settings, not from a constant: how many streams fit on the
    screen knows whoever looks at it - two for somebody, ten for somebody else.
    Changed in Settings → Interface.
    """
    def limit(key: str, default: int) -> int:
        return db.get_int_setting(key, minimum=LIMIT_MIN, maximum=LIMIT_MAX, fallback=default)

    # Tables (Insights, *Arr, Seerr): how many rows straight away, the rest in a window.
    # A pair (everywhere, on a phone) for every group of tables separately.
    tables = {where: (limit(key, default), limit(key + "_mobile", default_mobile))
              for where, (key, default, default_mobile) in TABLE_LIMITS.items()}
    return {
        "stream_limit": limit("ui_max_streams", 10),
        # Less fits on a phone: a stream's card takes the whole width there
        # and four under each other already mean the rest of the page is off
        # the screen. CSS decides, not the server - it does not know what the
        # person looks at, so it sends both and what applies is shown.
        "stream_limit_mobile": limit("ui_max_streams_mobile", 3),
        "people_limit": limit("ui_max_viewers", 10),
        # A viewer's bar is lower than a stream's card, but still fewer belong
        # on a phone - under ten bars the legend and the table below them would
        # start only past the second screen.
        "people_limit_mobile": limit("ui_max_viewers_mobile", 5),
        "insights_row_limit": tables["insights"][0],
        "insights_row_limit_mobile": tables["insights"][1],
        "table_limits": tables,
    }


# The context keys that are documentation (names and types of API fields), not data
# about viewers - anonymising does not touch them (see _context).
NOT_ANONYMISED = ("api_guide_fields",)


def _map_basemap() -> str:
    """What is under the dots on the map - the drawn map, or OpenStreetMap."""
    basemap = (db.get_setting("ui_map_base", "drawn") or "").strip()
    return basemap if basemap in MAP_BASEMAPS else "drawn"


# Where the browser takes the map's tiles from when the administrator switches the OSM basemap on.
OSM_TILES = "https://tile.openstreetmap.org"


def csp(osm: bool = False, nonce: str | None = None) -> str:
    """The security header Content-Security-Policy.

    One place, so that the rules do not drift from what the pages really do.
    All images go through /image (not even data: needs allowing) - with a single
    exception: the map's tiles from OpenStreetMap, and those only when the
    administrator switched them on. The basemap off = the header as always.

    `nonce` lets an inline script with this one-time code through - only for the
    waiting room after an update (settings._update_page), which cannot
    do without it. The route leaves it in `request.state.csp_nonce`.
    """
    return "; ".join((
        "default-src 'self'",
        f"img-src 'self' {OSM_TILES}" if osm else "img-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        f"script-src 'self' 'nonce-{nonce}'" if nonce else "script-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
        "base-uri 'self'",
        # No <object>, <embed> or applets - the app does not use them.
        "object-src 'none'",
    ))


def _zoom_mode() -> str:
    """What zooms the map - the wheel, or clicking."""
    mode = (db.get_setting("ui_map_zoom", "click") or "").strip()
    return mode if mode in ZOOM_MODES else "click"


def _linked_note(result: dict[str, Any]) -> str:
    """The sentence about the items found afterwards - for the message after an import.

    An import does not send a tmdb ID, so the items are looked up only after it.
    When something was matched, the user is to know: otherwise it would be unclear
    why the numbers in the library changed after the import.
    """
    linked = result.get("linked") or {}
    if not linked.get("rows"):
        return ""
    parts = []
    if linked.get("by_tmdb"):
        parts.append(i18n.translate("{n} by tmdb ID").format(n=linked["by_tmdb"]))
    if linked.get("by_episode"):
        parts.append(i18n.translate("{n} by episode number")
                     .format(n=linked["by_episode"]))
    if linked.get("by_name"):
        parts.append(i18n.translate("{n} by name").format(n=linked["by_name"]))
    return " " + i18n.translate_plural("Matched {what} ({n} records).", linked["rows"], what=", ".join(parts))


# Where a person comes back to after an import. All four routes of the Import
# section aim here, so the address is in one place.
BACK_TO_IMPORT = "/settings?section=import"


def _import_message(request: Request, result: dict[str, Any],
                    template: str) -> None:
    """The result of an import into a flash message. The same for all three sources.

    Only the first sentence differs (`template`); what follows - the items found
    afterwards, the records known from elsewhere, the repaired episodes - holds for
    all the same. These were three nearly identical pieces of code and twice a new
    sentence was added to only one of them.
    """
    if result.get("status") != "ok":
        _flash(request, result.get("message", "Import failed."), "error")
        return

    # Every sentence is a count with its forms (translate_with / translate_plural); the whole is a
    # finished text, so _flash does not look it up again.
    _flash(
        request,
        i18n.translate_with(template, None, n=result["imported"], found=result["found"],
                            duplicates=result["duplicate"])
        + _fixed_note(result) + _known_note(result) + _linked_note(result),
        "success", translated=True,
    )


def _sentence(result: dict[str, Any], key: str, template: str) -> str:
    """The sentence for one number of an import's result - or nothing when it is zero.

    Zero is left out on purpose: "0 records moved" is a sentence nobody needs to
    read, and the message after an import has enough of them as it is.
    """
    count = result.get(key) or 0
    if not count:
        return ""
    return " " + i18n.translate_plural(template, count)


def _fixed_note(result: dict[str, Any]) -> str:
    """The sentence about the records a repeated import straightened.

    Older imports from Jellystat took the series' id for an episode (see
    importers.import_jellystat_json), so the whole history of a series hung on one
    identifier. When the same backup is uploaded again, it is repaired in place -
    and this is the only place where a person learns of it.
    """
    return _sentence(result, "repaired",
                     "{n} older records were moved from the series to a specific episode.")


def _known_note(result: dict[str, Any]) -> str:
    """The sentence about the records that were in the database already from another source.

    Without it an import from a second tool would look as if it "found next to
    nothing". Yet it found nothing **new** - and that is good, exactly why nothing
    was doubled.
    """
    return _sentence(result, "known_elsewhere",
                     "{n} records were already in the database from another source (the "
                     "collector or a second import), so nothing was duplicated.")


def _flash(request: Request, message: str, level: str = "info", *, translated: bool = False,
           **values: Any) -> None:
    """Saves a message that shows on the next page.

    The translation is done **here** already, not in the template. The reason: the
    message is saved into the session and shows only on the next page - were somebody
    to switch the language meanwhile, it would show in the old one. Translating it
    at the moment it arose is unambiguous.

    Messages with a number or a name are passed as **a template and the values
    separately**:

        _flash(request, "Naimportováno {n} záznamů.", "success", n=42)

    The template is translated first, only then the values are put into it. The other
    way round does not work: a finished sentence with a number inside is not in the
    translation dictionary and never will be - and exactly that way all the messages
    after an import and after a task finished used to stay Czech, even with an
    English interface.

    When putting the values in fails (the template has another placeholder than the
    values that come), an untranslated sentence is shown rather than failing - a
    message about a task's result must not bring the page down.

    `translated=True`: the message is a finished sentence in a language chosen by the caller
    (the newly chosen language after saving it). It is not looked up again - a finished
    English sentence is a key itself and would be translated back into the old language.
    """
    try:
        # A count picks its form by `n` (translate_with); a finished sentence is only filled in.
        text = (message.format(**values) if values else message) if translated \
            else i18n.translate_with(message, None, **values)
    except (KeyError, IndexError, ValueError):
        # The rescue has to be utterly dumb: the raw template is returned.
        # Trying to fill it in again here would fail on the same thing -
        # and bring the page down over a message about a result.
        log.warning("could not fill message %r with values %r",
                    message, values)
        text = message
    request.session["flash"] = {"message": text, "level": level}


def _may_open_for(account: dict[str, Any] | None):
    """A function for the template: may this account open this address?

    The same rule the middleware lets pages through by (access.may_open), so a
    link is drawn only where it really leads. The template asks at every row of a
    table, so the list of allowed addresses is computed once per request (and only
    when needed), not at every link.
    """
    who = access.role(account)
    if who in ("", access.ADMIN):
        return lambda path: True
    allowed = functools.cache(lambda: access.allowed_paths(who, account))
    return functools.cache(lambda path: access.may_open(account, path, allowed()))


def sees_for(account: dict[str, Any] | None):
    """A function for the template: does this account see an area of the permission tree?

    See access.account_sees. Remembered for one request: the route, the panel
    and the template ask about the same area several times.
    """
    return functools.cache(lambda area: access.account_sees(account, area))


def account_image_tag(account: dict[str, Any] | None) -> str:
    """The tag of the account's profile image from Jellyfin, or empty.

    Only an account from Jellyfin (a local one has no image) and only when the
    synchronisation found it at the viewer. The tag is hex from Jellyfin - but it
    goes into an address, so to be safe only letters and digits are kept.
    """
    viewer = str((account or {}).get("jellyfin_user_id") or "")
    # Jellyfin sends the same id once with dashes, once without (see
    # accounts.bare_id) - so the cleaned one is compared. The table `users`
    # is small (one row per viewer), walking all of it costs nothing.
    bare = accounts.bare_id(viewer)
    if not bare:
        return ""
    try:
        row = db.query_one(
            "SELECT image_tag FROM users"
            " WHERE REPLACE(LOWER(id), '-', '') = ? AND image_tag IS NOT NULL",
            (bare,))
    except Exception:  # noqa: BLE001 - the image is cosmetic, the page does not fall over it
        # Say a restored old backup without the column - until the restart (migration)
        # the letter stays.
        log.warning("profile picture tag cannot be read", exc_info=True)
        return ""
    tag = str((row or {}).get("image_tag") or "")
    return "".join(c for c in tag if c.isalnum())[:64]


def account_image_url(account: dict[str, Any] | None) -> str:
    """The address of the account's own image with its tag (a new image = a new address)."""
    tag = account_image_tag(account)
    return f"/account/image?v={tag}" if tag else ""


def _context(request: Request, account: dict[str, Any] | None = None,
             **extra: Any) -> dict[str, Any]:
    """The data shared by every page (the collector's state, flash messages, who is signed in)."""
    # A page with statistics sends `days` - either a number, or a custom
    # period. The switch at the top needs both in the same shape, so it is
    # prepared here and not in every route separately.
    if "days" in extra:
        extra.setdefault("period", _period_for_template(extra["days"]))

    sees = sees_for(account)
    may_open = _may_open_for(account)
    base = {
        "collector_status": db.get_setting(collector.STATUS_KEY, "unknown"),
        # The stripe about the backup before the conversion to English names - for the administrator only.
        "conversion_notice": conversion.noted_backup() if account and account.get("is_admin") else None,
        "collector_error": collector.error_text(),   # in the reader's language
        "last_poll": db.get_setting(collector.LAST_POLL_KEY, ""),
        "flash": request.session.pop("flash", None),
        # The number of running plays in the left panel - it says who watches what right
        # now, so only with the right "Now playing" (0 = the row is not written).
        "active_count": stats.active_session_count() if sees("now_playing") else 0,
        "account": account,
        # By this the templates hide what is about other people.
        # `is_admin` is only a shortcut - `own_viewer` decides, that is whether
        # the account is tied to one viewer.
        "own_viewer": own_viewer(account),
        "is_admin": bool(account and account.get("is_admin")),
        # The profile image from Jellyfin in the My account button, or empty
        # (a letter). See web.my_image(). A function, not a value: the query to
        # the database runs only when a template really draws the button - not
        # for partials refreshed every few seconds.
        "account_image": functools.cache(lambda: account_image_url(account)),
        # The menu is drawn only from what the person may open - the same
        # question `viewer_scope_middleware` in web/__init__.py asks, with the rights
        # of one account too. A link with "you have no access here" behind it
        # is worse than none.
        "may_open": may_open,
        # The menu of this account on this page (jellyscope/navigation.py) and the title of the
        # open page for the burger button. A function, not a value: the menu is computed only
        # when a page draws it, not for partials that refresh themselves.
        "menu": functools.cache(lambda: navigation.menu(account, request.url.path, may_open)),
        "menu_title": navigation.current_title,
        # The same for content from another area and areas without an address (sees_for).
        "sees": sees,
        # The language the page is drawn in (the account's own choice, otherwise
        # the server's), and separately the server's language for Settings -> General.
        # Were the server language choice pre-filled with the first, an administrator
        # with English of their own would set it for everybody with one save.
        "ui_language": i18n.current_language(),
        "server_language": i18n.server_language(),
        # Today for the date fields - going further into the future makes
        # no sense, the statistics would be empty.
        "today": date.today().isoformat(),
        "today_text": _czech_date(date.today().isoformat()),
        # The three most common answers to "which period". A click fills the
        # fields with them, it does not send the form - so that the choice shows.
        "quick_periods": _quick_periods(),
        # `?wait=restart` / `?wait=task` in the address tells the page to wait
        # and refresh itself once there is something to show. A route sets it by
        # a redirect (see /settings/restart and /settings/stop); a fixed list of
        # values, so that nothing else can be smuggled into the page from the address.
        "wait_for": (request.query_params.get("wait")
                     if request.query_params.get("wait") in ("restart", "task")
                     else None),
        # How many tasks had finished at the moment of rendering. The page compares
        # it with /health and by a change knows that one finished meanwhile.
        #
        # It has to be in the HTML, not from the first query to /health: a task
        # that finishes between rendering the page and the first query would
        # otherwise fall through and nothing would refresh.
        "tasks_version": scanner.tasks_version(),
        # When the process that rendered this page started. For the same
        # reason as `tasks_version` a line above: the waiting room after a
        # restart needs to know WHAT it waits from, and must not find out by
        # the first query to /health. A restart comes within a second, so
        # that first answer may already belong to the new process - the waiting
        # room would write it down as the starting state and wait for a change
        # that never comes.
        "running_since": int(STARTED_AT),
        # The version shows in the footer of every page - it is the first thing
        # anybody asks about when a bug is reported.
        "version_info": updates.state(),
        # The limits for long lists - see _limits().
        **_limits(),
        "limit_min": LIMIT_MIN,
        "limit_max": LIMIT_MAX,
        # What zooms the map on the Network page - see _zoom_mode().
        "map_zoom": _zoom_mode(),
        "map_basemap": _map_basemap(),
        # Is the detailed map downloaded (in a shape this version can read)? From
        # the settings, not by a query to the table. The tag goes into the tiles' address.
        "detailed_map": detailed_map.state()["valid"],
        "detailed_map_tag": detailed_map.state()["fingerprint"],
        # The skin - see SKINS and the end of style.css. It is put into <html>
        # on the server already, not by JavaScript: otherwise the page would
        # flash in one skin for a moment and switch to the other.
        "ui_skin": _skin(),
        "ui_exact_time": _exact_time(),
        # Seasonal decoration switched on by the administrator? (Interface and My account.)
        "ui_events": seasonal.enabled(),
        # Switched on in the settings? The switch in Interface needs this.
        "ui_dashboard": cards.is_enabled(),
        # The name of the dashboard for everyone (the menu, the permission tree, Settings).
        "dashboard_name": cards.dashboard_name(),
        # Has the account a My dashboard with at least one card? A function, not a value:
        # the query runs only when the card on My account is drawn (the menu asks
        # by itself, navigation.menu).
        "has_my_dashboard": functools.cache(lambda: bool(
            account and cards.load_layout(account["id"]))),
    }
    base.update(extra)
    # The description of API fields (`{"device": "text", "user_id": "text"}`) is
    # documentation, not data about viewers - anonymising would turn the type into
    # "Zařízení ?" and the guide would lose the field's type. It goes around it.
    documentation = {key: base.pop(key) for key in NOT_ANONYMISED if key in base}
    # Only at the very end: what is hidden is hidden in EVERYTHING that goes to
    # the template - including what a route added through `extra`. One place
    # for all pages: were it handled in the templates, forgetting one would be
    # enough - and that would be the very one somebody opens.
    return {**access.anonymise_for(base, account), **documentation}


def _czech_date(iso: str | None) -> str:
    """The reverse: 2026-08-01 -> 1.8.2026, for writing back into the form.

    `formatting.czech_date` does the work - a card of the custom dashboard needs
    it too and two copies of the same conversion are one too many.
    """
    return formatting.czech_date(iso)


# What the capacity of the storage is entered in. The key is what stands in the choice.
CAPACITY_UNITS = {"GB": 1024 ** 3, "TB": 1024 ** 4}


def _clamp(raw: str, minimum: int, maximum: int, fallback: int) -> str:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = fallback
    return str(max(minimum, min(maximum, value)))


def _new_key(request: Request, key: str) -> dict[str, Any] | None:
    """A freshly made API key from the session - once, and thrown away at once.

    The session holds {"id", "token"}; by the id the key shows whole in the row
    of its key (the macro bunka_klice). Anything else - say a session from the time
    when only a text was there - is thrown away too.
    """
    fresh = request.session.pop(key, None)
    if isinstance(fresh, dict) and fresh.get("token") and fresh.get("id"):
        return fresh
    return None


def _no_cache(response: Response) -> Response:
    """A page the browser must not keep - the whole API key is in it.

    Without it the Back button would return it from the cache (bfcache) with the
    key, even after signing out on a shared computer. The key is to show only
    once.
    """
    response.headers["Cache-Control"] = "no-store"
    return response


def _form_number(text: Any) -> int:
    """A positive whole number from a form or an address; nonsense = 0."""
    try:
        number = int(str(text).strip())
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


# When this process started. After a restart the value differs - and that is
# the only reliable way to tell from the browser that a new instance runs
# already. Waiting for "stops answering and starts again" is not enough: a restart
# takes a moment and a query need not hit that gap at all.
STARTED_AT = time.time()


SETTINGS_SECTIONS = [
    ("jellyfin", "Jellyfin", True),
    ("seerr", "Seerr", True),          # requests
    ("arr", "*Arr", True),             # Sonarr, Radarr - the connected apps are together, right under Jellyfin
    ("data", "Data collection", True),
    ("tasks", "Tasks and backups", True),
    ("notifications", "Notifications", True),
    ("import", "History import", True),
    ("database", "Database", True),
    ("accounts", "Accounts", False),      # False = a reader sees it too
    ("api", "API", True),
    ("blocks", "Blocked logins", True),
    ("log", "Log", True),
    ("interface", "Interface", True),   # the look of the pages
    ("general", "General", True),
]
