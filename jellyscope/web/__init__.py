"""The web application - routes, templates, sign-in.

FastAPI is a library that assigns addresses to functions. When the browser asks
for "/library", the function marked @app.get("/library") is called. There is
nothing more to it.

Every route of the package does the same in three steps:
    1. read the parameters from the address (say the period)
    2. ask the modules stats / insights for the data
    3. hand the data to the template to draw

No SQL, no computing. When a formula shows up in the web package, it belongs
elsewhere.

The package goes by areas (jellyscope/web/). The routes are in submodules as
@router.get("/library") - a router is a group of routes this file attaches to
the application (app.include_router):

* `__init__` - the application: app, middleware, error handling, attaching the routes
* `start` - start and shutdown (lifespan)
* `body_limit` - the ceiling of a request's body, before anything reads it
* `base` - what every page needs (templates, context, flash messages, the sign-in guard)
* `settings` - Settings
* `periods` - the choice of the period and of the kind of content
* `login` - the first run, sign-in, sign-out
* `my_account` - my account
* `dashboards` - custom dashboards (for everyone and My dashboard), shared partials
* `video` - the Movies & series section (/video/…): overview, comparison, insights, languages
* `legacy` - old page addresses: redirects to the new ones (jellyscope/legacy_urls.py)
* `music`, `books` - the Music and Books sections (/music/…, /books/…): overview, artist, comparison…
* `album`, `book` - the album and the book pages of the Library (/album/<id>, /book/<id>)
* `library` - the library, titles, the archive, images
* `library_kinds`, `music_library`, `books_library` - libraries that are not video: what the Library shows for
  each kind, a music and a books library (/library/<id>)
* `disks` - the forms of the Disks card
* `users` - users
* `history` - the play history
* `network` - the network
* `arr`, `seerr` - the *Arr page with a tab for every connected app (/arr/…) and the Seerr page (/seerr)
* `status` - a small JSON interface (/health, /api/now-playing)
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from typing import Any

from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import pass_context
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .. import (access, api, api_query, cards, charts,
                formatting, i18n,
                languages,
                seasonal,
                stats, web_sessions)
# The version of the running process. Fixed at import on purpose: after `git pull`
# a new one lies on the disk, but this process still runs the old code - and the
# waiting room after an update needs to know exactly what runs here, not what is on
# the disk. Once the process is replaced, the file is read again with the new number.
from .. import __version__  # noqa: F401 - read from the outside too
# The base of the web lives next door - every part of the routes needs it, so
# they would otherwise have to import this module and it them.
from .base import (  # noqa: F401 - used from the outside too
    LIMIT_MAX,
    PACKAGE_DIR,
    SETTINGS_SECTIONS,
    STARTED_AT,
    TABLE_LIMITS,
    _context,
    _flash,
    _limits,
    _map_basemap,
    _period_for_template,
    _zoom_mode,
    config,
    current_account,
    log,
    own_viewer,
    templates,
)
from .base import _no_store_signed_in, csp
from .login import _client_address
from .periods import ALLOWED_DAYS, DAY_LABELS
from .start import lifespan

# The submodules with routes - app.include_router at the end of the file attaches them.
from . import (album, arr, body_limit, books, dashboards, disks, history, legacy, library, login, music, my_account, network, seerr, settings,
               status, users, video)
from ..arr.registry import connected as _arr_connected
from ..music import has_music as _has_music
from ..seerr import is_connected as _seerr_connected

# The door: the names from the submodules that are read from the outside too (web.X, tests).
# The package's code takes them straight from the submodule, not from here.
from .library import ARCHIVE_DELETE_LIMIT, item_image  # noqa: F401 - read from the outside too
from .my_account import my_image  # noqa: F401 - read from the outside too
from .periods import _kind  # noqa: F401 - read from the outside too


STATIC_DIR = PACKAGE_DIR / "static"


# No machine description of the app: FastAPI would otherwise hand /openapi.json to anybody, signed in or
# not - the list of every address with the routes' docstrings, a map of the app including the
# administrator's actions. The read API has a guide of its own (Settings -> API); the browsers of the
# description (/docs, /redoc) are off for the same reason. tests/test_security.py guards it.
app = FastAPI(title="Jellyscope", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _referer_path(referer: str | None) -> str:
    """The path from `referer`, or home. Never an address on a foreign server.

    Only the path is returned on purpose - without the scheme and the host. Such a
    link is always on the same server, so it cannot be abused to lead a person
    elsewhere, and still returns them exactly where they were.

    `//evil.example/x` looks like a path, but the browser reads it as an address on
    a foreign server, so it does not pass.
    """
    address = urlsplit(referer or "")
    target = address.path or "/"
    if address.query:
        target = f"{target}?{address.query}"
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


@app.middleware("http")
async def demo_mode_middleware(request: Request, call_next):
    """In the demo nothing changes - a message shows instead of the action.

    The demo runs on a public address and the credentials are right in the sign-in
    window, so anybody gets in. Without this safeguard the first visitor would
    rewrite Jellyfin's address, start an import or a restart - and the demo would
    be over for everybody else.

    It is guarded here, in one place, and not in every route separately: the route
    somebody forgot is exactly the one somebody finds. The list of exceptions is
    in DEMO_ALLOWED.

    The buttons stay visible on purpose - the demo is to show what the app can do.
    They only answer with a message instead of working.
    """
    if _demo_blocks(request):
        _flash(request, "This is a demo, so nothing here "
                        "changes. On your own install the button works.", "info")
        # Back exactly where the person came from - not to the Overview. Whoever
        # explores Settings is to stay in Settings after a click; throwing them out
        # to the start page is a punishment for curiosity.
        #
        # The browser always sends `referer` with a form sent from a page - only
        # it is a header from the visitor, that is data from the outside.
        # Redirecting to a whole address by it would turn the demo into an open
        # redirector.
        #
        # So only the **path** is taken. The host is not compared at all: a check
        # "starts with our address" was here once and let
        # `https://jellyscope.cz.utocnik.cz/` through, because it really does start
        # with our address. A relative path cannot leave, wherever the referer
        # comes from, and it does not break behind a proxy where the scheme and
        # the port differ.
        target = _referer_path(request.headers.get("referer"))
        return RedirectResponse(target, status_code=303)
    return await call_next(request)


# What a viewer from Jellyfin may send (POST). Otherwise they only read. An API
# key of their own they make only with permission - the route itself guards that.
VIEWER_MAY_POST = frozenset({"/logout", "/account/language", "/account/password",
                             "/account/key", "/account/key/revoke",
                             # Without motion in the seasonal decoration - only for themselves.
                             "/account/event-motion",
                             # My dashboard - their own layout, nobody else's.
                             "/account/dashboard"})


@app.middleware("http")
async def viewer_scope_middleware(request: Request, call_next):
    """Whoever is not the administrator sees only what the administrator ticked.

    It is guarded **in one place**, not in every route: were one forgotten, it
    would not show as an error but as somebody else's history on the screen of a
    person it is none of whose business. Hence an allowing list and a locked
    door - what is not in it ends here.

    There are two groups and they differ in what "mine" means:

    * **a viewer from Jellyfin** has an account tied to a particular person, so
      their own numbers always belong to them. The overview sends them to their
      page, another viewer's page is closed and they change nothing - they only read.
    * **a local reader account** belongs to nobody in particular (the administrator
      creates it), so "my history" makes no sense for it. Only the ticked areas
      hold for it.
    """
    account = current_account(request)
    who = access.role(account)
    if who in ("", access.ADMIN):
        return await call_next(request)

    viewer = own_viewer(account)
    path = request.url.path

    # `/` is always open: it only redirects to the account's default page, or the first page
    # it may open (navigation.default_page_url, web/legacy.py) - never to "you may not".
    if viewer:
        # A viewer changes nothing, only reads - except signing out and their own
        # account (My account). A list, not the prefix /account/: every further
        # writing route has to be added here knowingly, otherwise it would be open
        # to viewers by itself. The routes take the account from the session and
        # the password of a Jellyfin account is refused by the route itself
        # (accounts.change_own_password).
        if request.method != "GET" and path not in VIEWER_MAY_POST:
            return _forbidden(request)

    # The same rule draws the links in the templates (may_open), so a link leads
    # only where one can really enter.
    if not access.may_open(account, path):
        return _forbidden(request)

    return await call_next(request)


def _forbidden(request: Request):
    """The answer to "this is not yours". A page, not a bare 403."""
    account = current_account(request)
    return templates.TemplateResponse(
        request, "forbidden.html", _context(request, account), status_code=403)


@app.middleware("http")
async def account_language_middleware(request: Request, call_next):
    """The language the signed-in account chose on the My account page.

    The order of the middleware decides here: every further one wraps the previous
    ones. This one needs the session, so it has to be inside SessionMiddleware (that
    is added right under it), and outside `viewer_scope_middleware` and
    `demo_mode_middleware` - their pages too ("you have no access here", the demo's
    message) are to be in the account's language. Without an account (sign-in, an
    API key) the server's language stays.
    """
    account = current_account(request)
    token = i18n.set_account_language((account or {}).get("ui_language"))
    try:
        return await call_next(request)
    finally:
        i18n.reset_account_language(token)


# The ceiling of a request's body (body_limit.py) and the session's old keys (TODO.md item 0): right inside SessionMiddleware.
app.add_middleware(body_limit.BodyLimit)
app.add_middleware(legacy.LegacySession)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key,
    session_cookie="jellyscope_session",
    max_age=web_sessions.LIFETIME_SECONDS,      # a record on the server lives as long (web_sessions.py)
    # same_site="lax": the cookie is not sent with a request from a foreign site - its form cannot act in your name.
    same_site="lax",
    # Behind an HTTPS proxy SECURE_COOKIES=1 in .env: the cookie goes only over an encrypted connection. On
    # localhost without HTTPS it stays off, otherwise nobody would sign in.
    https_only=config.secure_cookies,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# The read API. A module and a guard of its own: pages prove themselves by the
# sign-in cookie, the API by a token. Nothing under /api/ changes anything - there
# are only GETs.
app.include_router(api.router)


class ApiUsageLog:
    """Every request with an existing key is written down - only after the response.

    Only after it the result is known (200, 403, ...). api.verify_token() leaves the
    key (id, name, owner) here in `request.state`; an invalid key does not leave
    it, and so it is not written. It goes to the API log.

    Pure ASGI, not `@app.middleware`: there the body of the response leaves only
    after the middleware returns, so the write would hold every API response up.
    Here the client has the response before the write.

    The write must not bring the response down: whoever asks for numbers is to get
    them even when the record just failed. And an uncaught error of a route (500) is
    written too - then it is passed on for Starlette to handle as before.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        result = 500

        async def forward(message) -> None:
            nonlocal result
            if message["type"] == "http.response.start":
                result = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, forward)
        except Exception:
            await _record_usage(scope, result)
            raise
        await _record_usage(scope, result)


# When an error of writing the usage was last logged. When the write keeps failing
# (a full disk, permissions), Grafana would add the whole error message to the log
# every few seconds - one per quarter of an hour is enough.
WRITE_ERROR_EVERY_SECONDS = 900
_last_write_error = 0.0


async def _record_usage(scope, result: int) -> None:
    global _last_write_error
    key = (scope.get("state") or {}).get("api_key_in_log")
    if not key:
        return
    path = scope["path"]
    query = scope.get("query_string", b"").decode("latin-1")
    if query:
        path = f"{path}?{query}"
    try:
        await asyncio.to_thread(api.record_use, key, path,
                                _client_address(Request(scope)), result)
    except Exception:  # noqa: BLE001 - the record is an extra, the response is not
        if time.time() - _last_write_error >= WRITE_ERROR_EVERY_SECONDS:
            _last_write_error = time.time()
            log.exception("Could not write the API key usage log entry")


app.add_middleware(ApiUsageLog)


# What the demo mode may change: signing in and out, nothing more.
#
# The demo runs on a public address and anybody can sign in - the credentials
# are in the sign-in window. Anything saved holds for all further visitors, so
# even harmless things like the interface language are locked: whoever switches
# to Czech switches it for the next visitor too.
#
# The buttons stay visible on purpose: the demo is to show what the app can do.
# A message shows instead of the action.
DEMO_ALLOWED = frozenset({
    "/login",
    "/logout",
})


def _demo_blocks(request: Request) -> bool:
    """Is this request to be stopped in the demo?"""
    if not config.demo_mode or request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    return request.url.path not in DEMO_ALLOWED


# Old names (web/legacy.py, TODO.md item 0): old page parameters redirect before the session and the guards
# (the redirect gives away nothing but an address); a form with an old field name saves nothing.
legacy.wire_up(app)


# Headers that tell the browser what it must not do with the response.
# They cost one middleware and close whole groups of attacks in advance.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # The OpenStreetMap basemap changes two headers (CSP and Referrer-Policy).
    # When the settings cannot be read (the database is down right now), the strict
    # variant holds - a header must not bring the response down, nor /health or styles.
    try:
        osm = _map_basemap() == "osm"
    except Exception:  # noqa: BLE001 - any error = strict headers
        osm = False
    # "Do not guess the content type." Without it the browser ignores the declared
    # type of a file that looks like HTML and runs it as a page -
    # it concerns mainly the images passed on from Jellyfin.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # "Do not put me into a frame on a foreign page." Without it the page can be
    # covered with an invisible layer and a person made to click on something
    # other than they think (clickjacking).
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # "When leaving for a foreign site, do not send where the person came from."
    # Jellyscope's addresses hold ids of items and users.
    #
    # With the OSM basemap on, the server's address goes out (without the path, so
    # without ids): OSM refuses tiles without a Referer and not every browser needs
    # to know the referrerpolicy attribute of an SVG <image>. It says so at the choice.
    response.headers.setdefault(
        "Referrer-Policy", "strict-origin-when-cross-origin" if osm else "same-origin")
    # The app needs none of this, so not even a piece of a page that got here by
    # mistake can ask for it.
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    # HSTS only where the app already runs over HTTPS (SECURE_COOKIES) -
    # the browser remembers it for long, so switched on at a server without a
    # certificate it would lock people out. Without `includeSubDomains`
    # and `preload` on purpose: those are decisions about a whole domain, not about us.
    if config.secure_cookies:
        response.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000")
    # Where a page may load anything from and where it may send anything to.
    #
    # Jellyscope has not a single foreign source - no CDN, no font from the internet,
    # images are passed through its own server. So it can afford to say
    # "only from here" and break nothing by it.
    #
    # Scripts only from the app's files (`script-src 'self'`), none inline:
    # were an escaping bug to show up anywhere in the output of data, the browser
    # runs neither an inline <script> nor onerror=. Hence the app itself has no
    # inline code - a new script belongs in static/js, values from a template
    # in the data block `texts-js` or data-… attributes, handlers of elements in
    # data-… (static/js/base.js). tests/test_no_inline_js.py guards it;
    # never put `'unsafe-inline'` back here, not even "for a while".
    #
    # CSP further takes from an attacker what they would want to do with code:
    # download more from elsewhere (script-src), send data to a foreign server
    # (connect-src), send a form elsewhere (form-action) or frame the page
    # (frame-ancestors). Inline styles are allowed (style-src) - there are
    # hundreds of them and the security gain would be small.
    #
    # The only exception: the waiting room after an update. Python puts it together
    # and the old version draws it over the new files, so it must not touch anything
    # that changes with the version (not even static/js). Its script carries a
    # one-time nonce the route leaves in request.state - otherwise the page would
    # never move in the browser after an update.
    nonce = getattr(request.state, "csp_nonce", None)
    response.headers.setdefault("Content-Security-Policy", csp(osm, nonce))
    return _no_store_signed_in(request, response)


formatting.register(templates.env)
# The kind of an item in the interface language ("Audio" -> "Music"). It lives in stats, because
# that is where it is decided what is a movie, a series and "other" - the template only writes it.
templates.env.filters["type_name"] = stats.type_label
# Templates may call the functions of charts - drawing belongs in the template, not in the route.
templates.env.globals["charts"] = charts
templates.env.globals["day_labels"] = DAY_LABELS
# The macro show_card() (_macros.html): a typo in a card's name = an error, not a silent hole.
templates.env.globals["check_card"] = cards.check_card


@pass_context
def _template_card_data(context: Any, key: str) -> Any:
    """The data of a card that returns them under its key (the apps' cards) - see _card.html.

    The card's template reads them as `d`. An old card has nothing under its key
    (it puts its data straight into the context) and gets `None`, which it does not read.
    """
    return context.get(key)


templates.env.globals["card_data"] = _template_card_data
templates.env.globals["allowed_days"] = ALLOWED_DAYS
# `lang` is needed in templates on every page that shows a language -
# hence global, so that not every route has to pass it separately.
templates.env.globals["lang"] = languages
# The interface translation - in templates it is used as the function _("text").
i18n.register(templates.env)


def _asset_version() -> str:
    """The fingerprint of the static files for the address of styles and images.

    What it is for: the browser saves the CSS and does not download it again next
    time. With a reverse proxy that caches /static/ on top (see the examples in
    the deploy/ folder), a change of the look would be invisible for a week - the
    user would see the old page and wonder in vain why the change does not work.

    The fix is an old and reliable trick: a number that changes when a file
    changes is attached to the address. Another address = another file = the
    browser downloads it again. As long as nothing changes, it stays cached.

    The time of the last change of the newest file in static/ is taken. It is
    computed once at start, so after a change of styles a restart is needed - the
    same as with templates.
    """
    try:
        newest = max(path.stat().st_mtime
                     for path in STATIC_DIR.rglob("*") if path.is_file())
    except ValueError:
        return "0"
    return str(int(newest))


templates.env.globals["asset_version"] = _asset_version()
# Seasonal decoration: which holiday it is (a key, or "") and whether it may move for the account.
# A global function, not context - the sign-in pages have no _context.
templates.env.globals["seasonal_event"] = seasonal.for_template
templates.env.globals["seasonal_motion"] = seasonal.motion
# The connected apps and music for Settings (the cards of *Arr, Seerr and the music rows in Interface).
# The menu asks the same by itself (navigation.py).
templates.env.globals["arr_connected"] = _arr_connected
templates.env.globals["seerr_connected"] = _seerr_connected
templates.env.globals["has_music"] = _has_music
templates.env.globals["seasonal_year"] = seasonal.year_for_template
# The template knows the demo mode too: the sign-in page in it says straight away
# which credentials get one in. It is a global variable, because the sign-in
# page has no context of its own.
templates.env.globals["demo_mode"] = config.demo_mode


# The error code of the API's HTTP errors that did not arise as an ApiError (an unknown
# address, another method than GET) - a program knows what is wrong without reading a sentence.
HTTP_ERROR_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                    405: "method_not_allowed"}


@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    """A redirect instead of an error message for a user who is not signed in."""
    if exc.status_code == 307 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    # The API is asked by programs (Grafana, Homepage), not people: an error page
    # in HTML would tell them nothing. There every error is JSON with a reason.
    api_request = request.url.path.startswith("/api/")
    if exc.status_code == 403 and not api_request:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 403, "message": exc.detail}, status_code=403,
        )
    if exc.status_code == 404 and not api_request:
        return templates.TemplateResponse(
            request, "error.html",
            # Starlette's own "Not Found" (an address without a route) gets the app's sentence.
            {"code": 404, "message": exc.detail if exc.detail and exc.detail != "Not Found" else "Page not found."},
            status_code=404,
        )
    # The exception's headers belong in the response. With a 401 from the API they
    # hold `WWW-Authenticate: Bearer`, the only sentence that tells the caller
    # what to prove itself with - without it it is just "you may not".
    body: dict[str, Any] = {"detail": exc.detail}
    if api_request:
        body["error"] = HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    return JSONResponse(body, status_code=exc.status_code,
                        headers=exc.headers or None)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    """A value FastAPI cannot read (a page's `days=abc`, `page=`): the app's error page, not a JSON dump.

    An edited bookmark or a hand-typed address is a person's mistake - a browser would show the raw
    `{"detail": [...]}`. The API keeps FastAPI's JSON answer: programs read its reasons.
    """
    if request.url.path.startswith("/api/"):
        return await request_validation_exception_handler(request, exc)
    return templates.TemplateResponse(
        request, "error.html",
        {"code": 400, "message": "A value in the address or the form is not valid."}, status_code=400,
    )


@app.exception_handler(api_query.ApiError)
async def handle_api_error(request: Request, exc: api_query.ApiError):
    """An API error: always JSON with the same body (`detail` + `error`).

    By `error` a program knows what is wrong without reading a sentence. With a 401
    `WWW-Authenticate: Bearer` goes with the response too.
    """
    return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers)


# The routes live in submodules by areas; the submodules take from `base`,
# not from this file, so nothing goes in a circle. The order does not matter - no
# two routes have the same address with the same method (tests/test_web_package.py).
for _submodule in (login, my_account, legacy, video, dashboards, library, music, books, album, disks, users, history, network, arr,
                   seerr, status, settings):
    app.include_router(_submodule.router)

# Tools for development only (jellyscope/devtools.py - the date for the seasonal decoration).
# They are not copied to prod and demo (deploy/sync-do-produ.py, DEV_ONLY), so
# the file is missing there and is just skipped here. A demo started from the dev folder
# (demo.py) does have it, but switching the date does not belong there. find_spec, not
# `except ImportError`: an error INSIDE devtools.py is not to be swallowed silently.
if not config.demo_mode and importlib.util.find_spec("..devtools", __package__) is not None:
    from .. import devtools  # noqa: E402
    devtools.wire_up(app, templates)
else:
    devtools = None
