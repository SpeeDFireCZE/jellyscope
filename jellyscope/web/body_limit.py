"""The ceiling of a request's body - before anything reads it, the sign-in check included.

A part of the web package (see jellyscope/web/__init__.py).

Nothing limited a body: uvicorn has no ceiling, every plain form is read by the check of old field names
(legacy.refuse_stale_form, a dependency of every route) and an upload by FastAPI before the routes' guards are
asked. Whoever reached the port could hold a gigabyte in memory, or spool it to the disk, with a POST to /login
before the answer "sign in first" - a few at once and the process is killed for memory or the disk is full.

Pure ASGI, right inside SessionMiddleware (it needs the session to tell the administrator): a declared length
over the ceiling is answered at once, a body sent in pieces is counted and cut at the ceiling.
"""
from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from .. import i18n
from .base import BACK_TO_IMPORT, _flash, current_account
from .settings import MAX_UPLOAD_MB

# The ceiling of every request's body. The largest form of the app is deleting a full page of the archive
# (a thousand ids, some 50 kB); a megabyte leaves a reserve and is no burden for a small server.
MAX_BODY_BYTES = 1024 ** 2

# The uploads of a history backup (Settings -> Import): the upload's own ceiling (MAX_UPLOAD_MB, the route
# measures the file against it) and room for the rest of the form - for the administrator only, everybody
# else has the ceiling of every request. tests/test_security.py guards that the list has every upload route.
UPLOAD_PATHS = frozenset({"/settings/import/playback-reporting-file", "/settings/import/jellystat"})
UPLOAD_ROOM = 1024 ** 2


class _TooLarge(Exception):
    """The body went over the ceiling while it was being read."""


def _is_admin(scope: dict[str, Any]) -> bool:
    account = current_account(Request(scope))
    return bool(account and account["is_admin"])


def _ceiling(scope: dict[str, Any]) -> int:
    if scope["path"] in UPLOAD_PATHS and _is_admin(scope):
        return MAX_UPLOAD_MB * 1024 * 1024 + UPLOAD_ROOM
    return MAX_BODY_BYTES


def _declared_length(scope: dict[str, Any]) -> int:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return 0     # counted while reading instead
    return 0


async def _answer(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """The administrator's upload goes back to Import with the reason, as when the route measured it; else 413."""
    request = Request(scope)
    if scope["path"] in UPLOAD_PATHS and _is_admin(scope):
        account = current_account(request) or {}
        token = i18n.set_account_language(account.get("ui_language"))
        try:
            _flash(request, "The file is larger than {n} MB.", "error", n=MAX_UPLOAD_MB)
        finally:
            i18n.reset_account_language(token)
        response: Any = RedirectResponse(BACK_TO_IMPORT, status_code=303)
    else:
        response = PlainTextResponse(i18n.translate("The request is too large."), status_code=413)
    await response(scope, receive, send)


class BodyLimit:
    """Refuses a body over the ceiling before a route, a form parser or the sign-in check reads it."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ceiling = _ceiling(scope)
        if _declared_length(scope) > ceiling:
            await _answer(scope, receive, send)
            return
        read, over, started = 0, False, False

        async def counted() -> Any:
            nonlocal read, over
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body", b""))
                if read > ceiling:
                    over = True
                    raise _TooLarge()
            return message

        async def guarded(message: Any) -> None:
            nonlocal started
            if over and not started:
                return       # the route's answer to a cut body (400, a fallen dependency) - ours goes instead
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not over:
                raise
        if over and not started:
            await _answer(scope, receive, send)
