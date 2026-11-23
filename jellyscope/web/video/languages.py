"""Languages: /video/languages, files without a language, the preferred language.

Part of the "Movies & series" section (web/video/).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db, langstats, languages as language_names
from ..base import _context, _flash, require_admin, require_login, templates
from ..periods import _days

router = APIRouter()
PER_PAGE = 50


@router.get("/video/languages/undetermined", response_class=HTMLResponse)
def languages_undetermined(
    request: Request,
    search: str | None = None,
    page: int = 1,
    account: dict[str, Any] = Depends(require_login),
):
    """Files whose audio language nobody filled in.

    A page of its own, not another insight card: it is a work list - one
    opens it to fix things, otherwise it should not be in the way.
    """
    page = max(1, page)
    total = langstats.undefined_language_count(search)
    return templates.TemplateResponse(request, "video/languages_undetermined.html", _context(
        request, account,
        files=langstats.undefined_language_files(PER_PAGE, (page - 1) * PER_PAGE, search),
        total=total,
        page=page,
        pages=max(1, (total + PER_PAGE - 1) // PER_PAGE),
        search=search or "",
    ))


@router.post("/video/languages/preferred")
def languages_preferred(
    request: Request,
    code: str = Form(""),
    days: int | None = Form(None),
    account: dict[str, Any] = Depends(require_admin),
):
    """Store which language the Languages page treats as preferred.

    Stored in the settings, not in the address: the choice survives a reload
    and a closed browser until someone changes it. Only an admin may change
    it - it is a setting of the whole application, not a personal filter.
    """
    code = language_names.normalize(code)
    available = {row["code"] for row in langstats.library_language_options()}

    # Only what is in the library can be chosen. Not for safety (the code
    # goes to SQL as a parameter) but for sense: a stored language that is
    # not in the library would make a page of zeros.
    if code and code != language_names.UNKNOWN and code in available:
        db.set_setting(langstats.PREFERRED_SETTING, code)
        _flash(request, "Preferred language: {language} ✓", "success", language=language_names.display(code))
    else:
        _flash(request, "That language is not in the library.", "error")

    target = "/video/languages" + (f"?days={int(days)}" if days else "")
    return RedirectResponse(target, status_code=303)


@router.get("/video/languages", response_class=HTMLResponse)
def languages(
    request: Request, days: int | None = None,
    since: str | None = None, until: str | None = None,
    account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days, since, until)

    # Colours are assigned once and used in every chart of the page - on
    # purpose regardless of the period, or a filter change would recolour them.
    colours = langstats.colour_map()
    preferred = langstats.preferred_language()

    return templates.TemplateResponse(request, "video/languages.html", _context(
        request, account,
        days=days,
        # These depend on the chosen period - they are about the viewers.
        watched=langstats.watched_languages(days, colours),
        by_user=langstats.languages_by_user(days, colours),
        subtitles=langstats.subtitle_usage(days),
        dubbing=langstats.dubbed_vs_original(days, preferred),
        missing_preferred=langstats.missing_preferred(days, preferred),
        # The choice of the preferred language offers only what is in the
        # library - see langstats.library_language_options().
        preferred=preferred,
        preferred_name=language_names.display(preferred),
        language_options=langstats.library_language_options(),
        # The state of the library right now - nothing to do with the period.
        library=langstats.library_languages(colours),
        combinations=langstats.language_combinations(),
        # The full list for the window behind the "Other" row; sent with the
        # page because it is one GROUP BY over a table read anyway.
        all_combinations=langstats.all_combinations(),
        undefined_items=langstats.undefined_language_items(),
        coverage=langstats.coverage(),
        # How many playbacks the numbers above leave out because they came
        # by import without a language. See langstats.WITHOUT_IMPORTS.
        imported_plays=langstats.imported_plays(days),
        # How many own playbacks are too short for the statistics - for the
        # message of an empty page.
        short_plays=langstats.short_plays(days),
        min_play_seconds=langstats.MIN_PLAY_SECONDS,
    ))
