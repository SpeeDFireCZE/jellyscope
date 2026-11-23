"""Old page addresses: each one redirects to its new address.

The table lives in jellyscope/legacy_urls.py. Every old address answers 308
(permanent, a form keeps its method and body) with the new address and the
same query string. The permission guard lets old addresses through (they
only redirect) and checks the new one as usual.

`/` opens the account's default page (navigation.default_page_url: chosen in
Settings > Interface, or the first page the account can open). `/` used to be
the overview of watching, and no page links to `/?...` any more (a test guards
the templates), so `/` with parameters is an old overview bookmark such as
`/?days=30&kind=movies`: it opens the overview with them, whatever the default
page is - as long as the account may open it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import access, navigation
from ..legacy_urls import LEGACY_URLS, SECTION_ROOTS, legacy_query, legacy_target, stale_fields, upgrade_session
from .base import _flash, require_login

router = APIRouter()


class LegacySession:
    """The session's old keys to the new ones (legacy_urls.upgrade_session), before anybody reads them.

    Pure ASGI, right inside SessionMiddleware: it changes the session dict in place, and SessionMiddleware writes
    the changed session into the answer's cookie. Every middleware and route after it sees only the new keys.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and isinstance(scope.get("session"), dict):
            upgrade_session(scope["session"])
        await self.app(scope, receive, send)


class StaleForm(Exception):
    """A POST that sends a form field by its old Czech name: the page is older than the upgrade."""


async def refuse_stale_form(request: Request) -> None:
    """A dependency of every route: a form with an old field name (legacy_urls.LEGACY_FIELDS) saves nothing.

    The names are not read any more, so the route would save empty values for them - a Save from a tab
    left open across the upgrade would wipe the dashboard layout, an account's own permissions, the
    notification address. The form is turned back with a word why. The API has its own JSON answers."""
    # Only plain forms: an upload (multipart) carries no renamed field and is left to its route.
    if (request.method != "POST" or request.url.path.startswith("/api/")
            or not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")):
        return
    # The form is read once and kept for the route - so with the largest limit a route reads it with
    # (deleting a full page of the archive), not the default 1000 fields, or that route answers 400.
    from .library import ARCHIVE_DELETE_LIMIT
    if stale_fields((await request.form(max_fields=ARCHIVE_DELETE_LIMIT + 10)).keys()):
        raise StaleForm()


def stale_form_answer(request: Request, _error: Exception) -> RedirectResponse:
    """Back to the page the form came from (this server only), with the reason."""
    _flash(request, "This page is from an older version of the app. Reload it (F5) and try again - nothing was saved.",
           "error")
    parts = urlsplit(request.headers.get("referer", ""))
    back = parts.path + (f"?{parts.query}" if parts.query else "")
    if parts.netloc != request.url.netloc or not back.startswith("/") or back.startswith(("//", "/\\")):
        back = "/"
    return RedirectResponse(back, status_code=303)


def wire_up(app: Any) -> None:
    """The temporary helpers of the old names (TODO.md item 0), in web/__init__.py's order of middleware:
    old address parameters redirect, a form with an old field name saves nothing."""
    app.middleware("http")(legacy_query_middleware)
    app.router.dependencies.append(Depends(refuse_stale_form))
    app.add_exception_handler(StaleForm, stale_form_answer)


def _redirect(request: Request) -> RedirectResponse:
    target = legacy_target(request.url.path, request.url.query) or "/"
    return RedirectResponse(target, status_code=308)


async def legacy_query_middleware(request: Request, call_next):
    """Old parameter names of a page or an API address redirect to the new ones (308).

    For a while, like the old page addresses (removal in TODO.md): a bookmark with the old period
    parameters keeps its period. Only GET (and HEAD), not a fragment (/partials/) - forms and
    fragments come from pages that already use the new names - and not the API: it never read any of
    these names, so it keeps its own JSON answer to an unknown parameter. Registered before the session and the
    guards (web/__init__.py) - the redirect gives away nothing but an address, and the new one is
    checked as usual. An old page address with old parameters gets both changes in one redirect.
    """
    if (request.method in ("GET", "HEAD") and "/partials/" not in request.url.path
            and not request.url.path.startswith("/api/")):
        query = legacy_query(request.url.query)
        # The path as it came (still encoded): a decoded "%2F" or "%3F" would change the address, and a
        # path that is or decodes to "//host" would make Location an address of another site - this
        # runs before signing in, so it must never send anyone away (review of part 10d2).
        raw = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
        if query is not None and not raw.startswith(("//", "/\\")) and not request.url.path.startswith(("//", "/\\")):
            path = legacy_target(request.url.path, "") or raw
            return RedirectResponse(f"{path}?{query}" if query else path, status_code=308)
    return await call_next(request)


for _old, _new, _methods in LEGACY_URLS:
    router.add_api_route(_old, _redirect, methods=list(_methods), include_in_schema=False,
                         name="legacy_redirect")


def _section_root(section_key: str) -> Callable[..., RedirectResponse]:
    """The route of /video or /music: the section's first page this account may open (404 without one)."""
    def section_root(request: Request, account: dict[str, Any] = Depends(require_login)) -> RedirectResponse:
        target = navigation.landing(section_key, account)
        if target is None:
            raise HTTPException(status_code=404)
        query = request.url.query
        return RedirectResponse(f"{target}?{query}" if query else target, status_code=303)
    return section_root


for _root, _section_key in SECTION_ROOTS.items():
    router.add_api_route(_root, _section_root(_section_key), methods=["GET"], include_in_schema=False,
                         name="section_root")


@router.get("/", include_in_schema=False)
def home(request: Request, account: dict[str, Any] = Depends(require_login)) -> RedirectResponse:
    """The start page: the default page of this account; an old overview bookmark opens the overview."""
    query = request.url.query
    overview = navigation.DEFAULT_PAGES["video"]
    if query and navigation.has_video() and access.may_open(account, overview):
        return RedirectResponse(f"{overview}?{query}", status_code=303)
    return RedirectResponse(navigation.default_page_url(account), status_code=303)
