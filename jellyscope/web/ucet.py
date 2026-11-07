"""Můj účet: stránka o jednom účtu, obrázek, jazyk, API klíč a heslo.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import accounts, api, api_dotaz, db, i18n, pristup
from ..jellyfin import JellyfinClient, QUICK_TIMEOUT
from .zaklad import (
    _bez_mezipameti,
    _cislo_z_formulare,
    _context,
    _flash,
    _novy_klic,
    log,
    otisk_obrazku_uctu,
    require_login,
    templates,
)
from .prihlaseni import _adresa_klienta, _blokace_hlaska

router = APIRouter()


# ---------------------------------------------------------------------------
# Muj ucet - stranka o jednom uctu, ne zmensene Nastaveni
# ---------------------------------------------------------------------------
#
# Ucet se vzdycky bere z relace, nikdy z formulare: odsud nejde zmenit nic
# ciziho, ani kdyby nekdo do formulare podstrcil cizi id.

@router.get("/account", response_class=HTMLResponse)
def muj_ucet(request: Request, account: dict[str, Any] = Depends(require_login)):
    adresy = api.povolene_adresy(account)
    novy = _novy_klic(request, "novy_klic_uctu")
    odpoved = templates.TemplateResponse(request, "ucet.html", _context(
        request, account,
        jazyk_uctu=account.get("ui_language") or "",
        z_jellyfinu=bool(account.get("jellyfin_user_id")),
        smi_klic=pristup.smi_klic(account),
        api_klice=api.seznam(account["id"]),
        # Okno „Jak se to používá" (_api_navod.html) ukáže jen tyhle adresy.
        api_navod_adresy=adresy,
        api_navod_pole=api.pole_adres(adresy),
        api_navod_operatory=api_dotaz.OPERATORY,
        # Příklad v návodu má fungovat hned: první adresa s daty, kterou
        # účet smí (ne natvrdo /summary - to divák ve výchozím stavu nesmí).
        api_ukazka=next((a for a in adresy if a != "/api/v1/" and "{" not in a),
                        "/api/v1/"),
        # Nový klíč jen jednou, hned po přesměrování - v řádku svého klíče
        # (viz bunka_klice). Pod vlastním jménem v relaci: klíč vyrobený
        # tady se nemá ukázat v Nastavení a naopak.
        novy_klic=novy,
    ))
    return _bez_mezipameti(odpoved) if novy else odpoved


def _bez_obrazku() -> Response:
    """Obrázek není (nebo Jellyfin neodpovídá) - písmeno zůstane.

    Holá 404 bez chybové stránky: <img> by ji stejně nezobrazil. A pět
    minut se prohlížeč neptá znovu, ať každá stránka nezkouší Jellyfin,
    který zrovna neběží.
    """
    return Response(status_code=404, headers={"Cache-Control": "private, max-age=300"})


@router.get("/account/obrazek")
async def muj_obrazek(request: Request, account: dict[str, Any] = Depends(require_login)):
    """Profilový obrázek přihlášeného účtu z Jellyfinu - jen vlastní.

    V adrese není žádné id: kdo se zeptá, dostane svůj obrázek, nikoho
    jiného - parametry navíc se ignorují. `?v=` je jen otisk, aby nový
    obrázek v Jellyfinu znamenal novou adresu.

    Na disk serveru se neukládá (šetří místo). Stáhne se přes server, aby
    se adresa Jellyfinu ani klíč nedostaly do stránky. Prohlížeč si ho
    nechá, ale před každým použitím se zeptá (no-cache + ETag): server
    ověří přihlášení a vrátí 304 bez dotazu do Jellyfinu. Na sdíleném
    počítači tak cizí fotku z mezipaměti nikdo jiný nedostane.
    """
    divak = str(account.get("jellyfin_user_id") or "")
    otisk = await asyncio.to_thread(otisk_obrazku_uctu, account) if divak else ""
    if not otisk:
        return _bez_obrazku()
    hlavicky = {"Cache-Control": "private, no-cache", "ETag": f'"{otisk}"'}
    if request.headers.get("if-none-match") == hlavicky["ETag"]:
        return Response(status_code=304, headers=hlavicky)
    pripojeni = await asyncio.to_thread(db.jellyfin_connection)
    # Krátký limit: čeká na to člověk u stránky, ne úloha na pozadí.
    async with JellyfinClient(*pripojeni, timeout=QUICK_TIMEOUT) as client:
        vysledek = await client.obrazek_uzivatele(divak, otisk)
    if vysledek is None:
        return _bez_obrazku()
    obsah, typ = vysledek
    return Response(obsah, media_type=typ, headers=hlavicky)


@router.post("/account/language")
def muj_ucet_jazyk(request: Request, ui_language: str = Form(""),
                   account: dict[str, Any] = Depends(require_login)):
    """Jazyk rozhraní účtu. Prázdný = jazyk serveru."""
    if ui_language and ui_language not in i18n.LANGUAGES:
        _flash(request, "Takový jazyk tu není.", "error")
        return RedirectResponse("/account", status_code=303)
    accounts.nastav_jazyk(account["id"], ui_language or None)
    # Hláška už v nově zvoleném jazyce - stránka, na kterou se vrací, v něm
    # bude taky. Překládá se rovnou (`_flash` hotovou větu nechá být).
    _flash(request, i18n.translate("Jazyk uložen.", ui_language or i18n.jazyk_serveru()),
           "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/udalosti-pohyb")
def muj_ucet_pohyb_udalosti(request: Request, pohyb: str = Form("1"),
                            account: dict[str, Any] = Depends(require_login)):
    """Hýbe se mi sezónní výzdoba? Vypne ji jen výslovné "0"."""
    accounts.nastav_pohyb_udalosti(account["id"], pohyb != "0")
    _flash(request, "Uloženo.", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/klic")
def muj_ucet_klic(request: Request, name: str = Form(""),
                  account: dict[str, Any] = Depends(require_login)):
    """Vlastní API klíč - jen s povolením od správce.

    Klíč patří účtu z relace, ne z formuláře, a smí přesně to, co účet
    sám (viz api.over_token a api.smi_adresu).
    """
    if not pristup.smi_klic(account):
        _flash(request, "Vlastní API klíče ti správce nepovolil.", "error")
        return RedirectResponse("/account#klice", status_code=303)
    vysledek = api.vytvor(name, account["id"])
    request.session["novy_klic_uctu"] = {"id": vysledek["id"], "token": vysledek["token"]}
    _flash(request, "Klíč „{jmeno}“ vznikl.", "success", jmeno=vysledek["jmeno"])
    return RedirectResponse("/account#klice", status_code=303)


@router.post("/account/klic/zrusit")
def muj_ucet_klic_zrusit(request: Request, token_id: str = Form(""),
                         account: dict[str, Any] = Depends(require_login)):
    """Zneplatní vlastní klíč. I bez povolení - starý klíč se musí dát zahodit.

    Id z formuláře se ověří proti klíčům účtu z relace: cizí klíč tudy
    zneplatnit nejde, ať se do formuláře napíše cokoliv.
    """
    cislo = _cislo_z_formulare(token_id)
    vlastni = {klic["id"] for klic in api.seznam(account["id"])}
    jmeno = api.zrus(cislo) if cislo in vlastni else ""
    if jmeno:
        _flash(request, "Klíč „{jmeno}“ už neplatí.", "success", jmeno=jmeno)
    else:
        _flash(request, "Takový klíč tu není.", "info")
    return RedirectResponse("/account#klice", status_code=303)


@router.post("/account/password")
def muj_ucet_heslo(request: Request, current_password: str = Form(""),
                   password: str = Form(""), password_again: str = Form(""),
                   account: dict[str, Any] = Depends(require_login)):
    """Vlastní heslo, jen se současným heslem. Účet z Jellyfinu ho tu nemá.

    Brzda proti hádání je tatáž jako u přihlášení a se stejným počítadlem
    (adresa klienta). Bez ní by přihlášená relace - třeba v cizím
    prohlížeči - byla neomezené orákulum na heslo, a každý pokus navíc
    stojí čtvrt vteřiny výpočtu PBKDF2.
    """
    zpet = RedirectResponse("/account", status_code=303)
    klic = _adresa_klienta(request)
    zbyva = accounts.blokace_zbyva(klic)
    if zbyva:
        _flash(request, _blokace_hlaska(zbyva), "error")
        return zpet
    try:
        accounts.zmen_vlastni_heslo(account["id"], current_password,
                                    password, password_again)
    except accounts.SpatneSoucasneHeslo as exc:
        blokace = accounts.zapocitej_neuspech(klic)
        if blokace:
            log.warning("zmena hesla z %s zablokovana (%s. stupen)", klic, blokace["level"])
            _flash(request, _blokace_hlaska(-1 if blokace["permanent"]
                                            else blokace["seconds"]), "error")
        else:
            _flash(request, exc.prelozena(), "error")
        return zpet
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
        return zpet
    accounts.zapomen_neuspechy(klic)
    # Tahle relace zustava (novy otisk), ostatni relace uctu skonci -
    # viz current_account.
    request.session["otisk"] = accounts.otisk_relace(accounts.get(account["id"]) or {})
    _flash(request, "Heslo změněno.", "success")
    return zpet
