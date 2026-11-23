"""A fallback page when the application has no database.

Without the database nothing can be done: accounts, settings and signing in
are all in it. In such a case the process used to exit - but in a container
that means emptiness. A person opens the address, the browser reports it
cannot connect, and the answer why lies in `docker compose logs`, where
nobody sent them.

This module builds a server that answers every address with one page: what
happened, what causes it and what to do about it. So the container comes up,
the web answers and the solution is visible where the person looks.

It returns 503 (Service Unavailable) on purpose - it is the truth, and the
container's health check thus sees that something is wrong. Were it to return
200, `docker ps` would report a healthy container with a broken application.

The page is in the language the browser asks for (Accept-Language) and in English
below it: the interface language is chosen in the settings, which are stored... in
the database. So the browser is the only one left to ask.
"""
from __future__ import annotations

import html

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from . import i18n

TITLE = "Jellyscope cannot open its database"
INTRO = ("The application is running, but it cannot reach its data. Until that is fixed there is nothing to "
         "sign in to: the accounts and the settings live in that database.")
WHAT = "What happened"
AFTER = "Once that is done, restart the application - in Docker, {command}."
COMMAND = "<code>docker compose restart</code>"

PAGE = """<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark light; }}
  body {{ margin: 0; padding: 32px 20px; background: #101318; color: #e8ecf1;
         font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  h2 {{ font-size: 15px; font-weight: 600; color: #9aa6b2; margin: 28px 0 6px;
        text-transform: uppercase; letter-spacing: .04em; }}
  p {{ margin: 10px 0; }}
  pre {{ background: #0a0d11; border: 1px solid #23303d; border-radius: 8px;
         padding: 14px 16px; overflow-x: auto; white-space: pre-wrap;
         color: #cfe3f7; }}
  code {{ background: #0a0d11; border-radius: 4px; padding: 1px 5px; }}
  .source {{ color: #9aa6b2; margin-top: 40px; padding-top: 24px;
             border-top: 1px solid #23303d; }}
  .source h1 {{ color: #9aa6b2; font-size: 17px; }}
</style>
</head>
<body>
<main>
{sections}
</main>
</body>
</html>
"""

SECTION = """<section{css} lang="{language}">
  <h1>{title}</h1>
  <p>{intro}</p>
  <h2>{what}</h2>
  <pre>{message}</pre>
  <p>{after}</p>
</section>"""


def browser_language(header: str) -> str:
    """The browser's most wanted language (Accept-Language) that has a translation; English otherwise."""
    wanted = []
    for position, part in enumerate(header.split(",")):
        tag, _, parameters = part.strip().partition(";")
        weight = 1.0
        name, _, value = parameters.strip().partition("=")
        if name.strip() == "q":
            try:
                weight = float(value)
            except ValueError:
                weight = 0.0
        if tag.strip() and weight > 0:
            wanted.append((-weight, position, tag.strip().lower()))
    for _, _, tag in sorted(wanted):
        for code in (tag, tag.split("-")[0]):
            if code in i18n.LANGUAGES:
                return code
    return i18n.SOURCE_LANGUAGE


def _section(error: BaseException | str, language: str, css: str = "") -> str:
    text = lambda key: html.escape(i18n.translate(key, language))  # noqa: E731
    message = error.translated(language) if isinstance(error, i18n.TranslatableError) else str(error)
    return SECTION.format(
        css=css, language=language, title=text(TITLE), intro=text(INTRO), what=text(WHAT),
        # The message carries paths from the system - it belongs in the page as text, not as a piece of HTML.
        message=html.escape(message), after=text(AFTER).replace("{command}", COMMAND))


def page(error: BaseException | str, language: str = i18n.SOURCE_LANGUAGE) -> str:
    """The fallback page's HTML: the message (`db.DatabaseUnavailable`) in `language`, English below it."""
    sections = [_section(error, language)]
    if language != i18n.SOURCE_LANGUAGE:
        sections.append(_section(error, i18n.SOURCE_LANGUAGE, ' class="source"'))
    return PAGE.format(language=language, title=html.escape(i18n.translate(TITLE, language)),
                       sections="\n".join(sections))


def app(error: BaseException | str) -> Starlette:
    """A server that answers anything with that one page, in the browser's language."""

    async def anywhere(request: Request) -> HTMLResponse:
        return HTMLResponse(page(error, browser_language(request.headers.get("accept-language", ""))),
                            status_code=503)

    # One path for everything including the root: whoever comes to /settings
    # or /login is to get the same answer - there is no other anyway.
    return Starlette(routes=[Route("/{path:path}", anywhere)])
