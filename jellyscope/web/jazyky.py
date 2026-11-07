"""Jazyky: přehled, položky bez jazyka a upřednostněné jazyky.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, langstats, languages
from .zaklad import _context, _flash, require_admin, require_login, templates
from .obdobi import _days

router = APIRouter()


# ---------------------------------------------------------------------------
# Jazyky
# ---------------------------------------------------------------------------

@router.get("/languages/bez-jazyka", response_class=HTMLResponse)
def languages_undefined(
    request: Request,
    search: str | None = None,
    page: int = 1,
    account: dict[str, Any] = Depends(require_login),
):
    """Seznam souboru, u kterych jazyk zvukove stopy nikdo nevyplnil.

    Vlastni stranka, ne dalsi karta v Zjistenich: tohle neni zjisteni,
    ale pracovni seznam - clovek si ho otevre, kdyz to jde opravovat,
    a jinak mu nema prekazet.
    """
    page = max(1, page)
    per_page = 50
    total = langstats.undefined_language_count(search)

    return templates.TemplateResponse(request, "bez_jazyka.html", _context(
        request, account,
        files=langstats.undefined_language_files(per_page, (page - 1) * per_page, search),
        total=total,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        search=search or "",
    ))


@router.post("/languages/preferred")
def languages_preferred(
    request: Request,
    code: str = Form(""),
    days: int | None = Form(None),
    account: dict[str, Any] = Depends(require_admin),
):
    """Uloží, který jazyk se má na stránce Jazyky brát jako preferovaný.

    Ukládá se do nastavení, ne do adresy: výběr má vydržet i po obnovení
    stránky a po zavření prohlížeče, dokud ho někdo nezmění.

    Změnu smí udělat jen správce - je to nastavení celé aplikace, ne
    osobní filtr. Kdyby ho mohl přepnout kdokoliv, přepsal by ho i všem
    ostatním.
    """
    kod = languages.normalize(code)
    povolene = {row["code"] for row in langstats.library_language_options()}

    # Vybírat jde jen z toho, co v knihovně je. Ne kvůli bezpečnosti (kód
    # jde do SQL jako parametr), ale kvůli smyslu: uložený jazyk, který
    # v knihovně není, by vyrobil stránku samých nul.
    if kod and kod != languages.UNKNOWN and kod in povolene:
        db.set_setting(langstats.PREFERRED_SETTING, kod)
        _flash(request, "Preferovaný jazyk: {jazyk} ✓", "success",
           jazyk=languages.display(kod))
    else:
        _flash(request, "Tenhle jazyk v knihovně není.", "error")

    cil = "/languages" + (f"?days={int(days)}" if days else "")
    return RedirectResponse(cil, status_code=303)


@router.get("/languages", response_class=HTMLResponse)
def languages_page(
    request: Request, days: int | None = None,
    od: str | None = None, do: str | None = None,
    account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days, od, do)

    # Barvy prirazujeme jednou a pouzijeme je ve vsech grafech na strance.
    # Zamerne bez ohledu na obdobi - jinak by zmena filtru prebarvila grafy.
    colours = langstats.colour_map()
    preferred = langstats.preferred_language()

    return templates.TemplateResponse(request, "languages.html", _context(
        request, account,
        days=days,
        # Tohle zavisi na zvolenem obdobi - je to o chovani divaku.
        watched=langstats.watched_languages(days, colours),
        by_user=langstats.languages_by_user(days, colours),
        subtitles=langstats.subtitle_usage(days),
        dubbing=langstats.dubbed_vs_original(days, preferred),
        missing_preferred=langstats.missing_preferred(days, preferred),
        # Výběr preferovaného jazyka. Nabízí se jen to, co v knihovně
        # opravdu je - viz langstats.library_language_options().
        preferred=preferred,
        preferred_name=languages.display(preferred),
        language_options=langstats.library_language_options(),
        # Tohle je stav knihovny tady a ted - s obdobim nema nic spolecneho.
        library=langstats.library_languages(colours),
        combinations=langstats.language_combinations(),
        # Úplný seznam pro okno za řádkem "Ostatní". Posílá se rovnou se
        # stránkou: je to jedno GROUP BY nad tabulkou, kterou už stejně
        # čteme, a doskakovat si pro to zvlášť by byl dotaz navíc za nic.
        all_combinations=langstats.vsechny_kombinace(),
        undefined_items=langstats.undefined_language_items(),
        coverage=langstats.coverage(),
        # Kolik přehrávání se do čísel výše nepočítá, protože přišlo
        # importem a jazyk u sebe nemá. Viz langstats.BEZ_IMPORTU.
        imported_plays=langstats.imported_plays(days),
        # Kolik vlastních přehrávání je na statistiku moc krátkých -
        # kvůli hlášce u prázdné stránky.
        short_plays=langstats.short_plays(days),
        min_play_seconds=langstats.MIN_PLAY_SECONDS,
    ))
