"""První spuštění, přihlášení a odhlášení.

Část balíku web (viz jellyscope/web/__init__.py) - kód přesunutý
beze změny z původního web.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import accounts, db, i18n, pristup, sekce
from ..jellyfin import JellyfinClient, JellyfinError, QUICK_TIMEOUT
from .zaklad import _flash, current_account, log, prihlas_do_relace, templates

router = APIRouter()


# ---------------------------------------------------------------------------
# Prihlaseni
#
# Aplikace je cela za prihlasenim - bez uctu se nedostanes nikam krome
# prihlasovaci stranky. Ucty jsou v tabulce `accounts` a spravuji se
# v Nastaveni; s uzivateli Jellyfinu nemaji nic spolecneho.
# ---------------------------------------------------------------------------

# ---- prvni spusteni ---------------------------------------------------

def _setup_context(**extra: Any) -> dict[str, Any]:
    """Data pro uvodni obrazovku. Jazyk se vybira uz tady."""
    # `ui_languages` uz je globalni promenna sablon (viz i18n.register),
    # takze se sem nepredava - jinak by casem existovaly dva seznamy
    # jazyku a jeden z nich by zestarnul.
    return {
        "error": None,
        "ui_language": i18n.current_language(),
        **extra,
    }


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    if accounts.any_exists():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", _setup_context())


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_again: str = Form(""),
    ui_language: str = Form(i18n.DEFAULT_LANGUAGE),
):
    # Kdyby nekdo poslal formular podruhe, ucty uz existuji - druheho
    # "prvniho spravce" tudy nikdo nepropasuje.
    if accounts.any_exists():
        return RedirectResponse("/login", status_code=303)

    # Pevny seznam - do nastaveni se z formulare nedostane nic jineho.
    if ui_language not in i18n.LANGUAGES:
        ui_language = i18n.DEFAULT_LANGUAGE

    try:
        account_id = accounts.create(username, password, password_again, is_admin=True)
    except accounts.AccountError as exc:
        # Jazyk ulozime i pri chybe. Kdo si prepnul na anglictinu a spletl
        # se v hesle, ma dostat anglickou hlasku - ne zase ceskou stranku.
        db.set_setting("ui_language", ui_language)
        # Hlasku prekladame VYSLOVNE do jazyka z formulare. Spolehnout se
        # na ulozene nastaveni nejde: pri uplne prvnim spusteni je v nem
        # jeste cestina a chyba by prisla cesky, i kdyz si clovek prave
        # prepnul na anglictinu.
        return templates.TemplateResponse(
            request, "setup.html",
            _setup_context(error=exc.prelozena(ui_language), username=username,
                           ui_language=ui_language),
            status_code=400,
        )

    db.set_setting("ui_language", ui_language)
    prihlas_do_relace(request, accounts.get(account_id) or {"id": account_id})
    _flash(request, i18n.translate("Účet vytvořen. Vítej v Jellyscope.", ui_language),
           "success")
    return RedirectResponse("/settings", status_code=303)


# ---- prihlaseni a odhlaseni -------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not accounts.any_exists():
        return RedirectResponse("/setup", status_code=303)
    if current_account(request) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "jellyfin_login": pristup.login_zapnuty()})


async def _prihlas_pres_jellyfin(jmeno: str, heslo: str) -> dict[str, Any] | None:
    """Zkusí jméno a heslo proti Jellyfinu. None = nepřihlášen.

    Druhá cesta dovnitř, ne náhrada: místní účty zůstávají, protože když
    je Jellyfin vypnutý nebo se mu změnila adresa, musí se dovnitř dát
    dostat i tak. Proto se zkouší až po nich.

    Chyba spojení se **nerozlišuje od špatného hesla**. Odpověď „server
    neodpovídá" by cizímu člověku prozradila, že tu nějaký Jellyfin je,
    a hlavně by z přihlašovací stránky udělala nástroj, kterým se dá
    zkoušet dostupnost cizí sítě. Do logu se to zapíše.
    """
    if not pristup.login_zapnuty():
        return None

    url, klic = db.jellyfin_connection()
    if not url or not klic:
        return None

    try:
        async with JellyfinClient(url, klic, timeout=QUICK_TIMEOUT) as jf:
            udaje = await jf.over_prihlaseni(jmeno, heslo)
    except JellyfinError as chyba:
        log.warning("prihlaseni pres Jellyfin se nepodarilo overit: %s", chyba)
        return None

    if udaje is None:
        return None
    if udaje["vypnuty"]:
        log.warning("ucet %s je v Jellyfinu vypnuty - dovnitr nesmi",
                    udaje["jmeno"])
        return None
    return accounts.z_jellyfinu(udaje)


@router.post("/login")
async def login_submit(
    request: Request, username: str = Form(""), password: str = Form(""),
    zpusob: str = Form("mistni"),
):
    # Brzda proti hádání hesel. Klíčem je adresa, ze které pokus přišel -
    # ne uživatelské jméno: podle jména by šlo cizí účet snadno zamknout
    # a majitele tím vyšoupnout ven.
    klic = _adresa_klienta(request)
    zbyva = accounts.blokace_zbyva(klic)
    if zbyva:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _blokace_hlaska(zbyva), "username": username,
             "jellyfin_login": pristup.login_zapnuty()},
            status_code=429,
        )

    # Ktere dvere clovek zvolil, tam se klepe. Heslo k mistnimu uctu
    # tak nikdy neodejde do Jellyfinu a heslo z Jellyfinu se neporovnava
    # s mistnimi otisky.
    if zpusob == "jellyfin":
        account = await _prihlas_pres_jellyfin(username, password)
    else:
        account = accounts.authenticate(username, password)
    if account is None:
        blokace = accounts.zapocitej_neuspech(klic)
        if blokace:
            log.warning("prihlaseni z %s zablokovano (%s. stupen, %s)", klic,
                        blokace["level"],
                        "trvale" if blokace["permanent"] else f"{blokace['seconds']} s")
            return templates.TemplateResponse(
                request, "login.html",
                {"error": _blokace_hlaska(-1 if blokace["permanent"]
                                          else blokace["seconds"]),
                 "username": username,
                 "jellyfin_login": pristup.login_zapnuty()},
                status_code=429,
            )
        # Zamerne nerikame, jestli bylo spatne jmeno nebo heslo. Kdybychom
        # to rozlisili, dal by se timhle zpusobem zjistit seznam uctu.
        return templates.TemplateResponse(
            request, "login.html",
            {"error": i18n.translate("Špatné jméno nebo heslo."),
             "username": username,
             "jellyfin_login": pristup.login_zapnuty()},
            status_code=401,
        )

    accounts.zapomen_neuspechy(klic)

    prihlas_do_relace(request, account)
    return RedirectResponse(_kam_po_prihlaseni(account), status_code=303)


def _kam_po_prihlaseni(account: dict[str, Any]) -> str:
    """Kam po přihlášení heslem.

    Přehled pro všechny je první, co člověk uvidí - o to jde. Jen když
    v něm má co vidět (sekce.vidi_prehled, totéž co menu). Jinak Přehled;
    kdo nesmí ani ten, toho `rozsah_divaka` pošle dál na první stránku,
    kterou smí - stejně jako po přihlášení kódem, které jde rovnou na /.
    """
    return "/dashboard" if sekce.vidi_prehled(account) else "/"


@router.post("/login/quick")
async def login_quick(request: Request):
    """Zahájí přihlášení kódem. Heslo se přitom nikde nezadává.

    Tajemství, kterým se přihlášení dokončí, zůstává **v relaci na
    serveru**. Do prohlížeče jde jen šestimístný kód, a ten sám o sobě
    nikoho nikam nepustí - dokud ho člověk nepotvrdí ve svém už
    přihlášeném Jellyfinu, není to jen číslo.
    """
    if current_account(request) is not None:
        return RedirectResponse("/", status_code=303)
    if not pristup.login_zapnuty():
        return _login_chyba(request, "Přihlášení přes Jellyfin není zapnuté.")

    # Tataz brzda jako u hesla. Bez ni by adresa zablokovana za hadani
    # hesel prosla touhle druhou branou - a kdokoli by mohl v kuse
    # zakladat kody v Jellyfinu. Kazde zahajeni se pocita jako pokus:
    # kod, ktery nikdo nepotvrdi, je nedokonceny pokus. Uspesne
    # prihlaseni pocitadlo smaze (viz login_quick_stav). Osm kodu za
    # minutu z jedne adresy nedela clovek, ktery se chce prihlasit.
    adresa = _adresa_klienta(request)
    zbyva = accounts.blokace_zbyva(adresa)
    if zbyva:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _blokace_hlaska(zbyva),
             "jellyfin_login": pristup.login_zapnuty()},
            status_code=429)
    blokace = accounts.zapocitej_neuspech(adresa)
    if blokace:
        log.warning("prihlaseni kodem z %s zablokovano (%s. stupen)",
                    adresa, blokace["level"])
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _blokace_hlaska(-1 if blokace["permanent"]
                                      else blokace["seconds"]),
             "jellyfin_login": pristup.login_zapnuty()},
            status_code=429)

    url, klic = db.jellyfin_connection()
    if not url or not klic:
        return _login_chyba(request, "Jellyfin není nastavený.")

    try:
        async with JellyfinClient(url, klic, timeout=QUICK_TIMEOUT) as jf:
            if not await jf.quick_connect_zapnuty():
                return _login_chyba(
                    request,
                    "Quick Connect je na serveru Jellyfin vypnutý. "
                    "Zapne se v jeho Ovládacím panelu.")
            zahajeni = await jf.quick_connect_zahaj()
    except JellyfinError as chyba:
        log.warning("quick connect se nepodarilo zahajit: %s", chyba)
        return _login_chyba(request, "Jellyfin se neozval. Zkus to za chvíli.")

    if not zahajeni:
        return _login_chyba(request, "Quick Connect se nepodařilo zahájit.")

    # Tajemstvi do relace, ne do stranky: kdo ho ma, prihlasi se s nim.
    request.session["quick_secret"] = zahajeni["tajemstvi"]
    return templates.TemplateResponse(
        request, "login_quick.html", {"kod": zahajeni["kod"]})


@router.get("/login/quick/stav")
async def login_quick_stav(request: Request):
    """Ptá se Jellyfinu, jestli už kód někdo potvrdil.

    Stránka se sem ptá po vteřinách. Odpověď je schválně chudá - jen
    „ještě ne" nebo „hotovo": víc by z ní šlo vyčíst o tom, jaké účty
    na serveru jsou.
    """
    tajemstvi = str(request.session.get("quick_secret") or "")
    if not tajemstvi or not pristup.login_zapnuty():
        return {"hotovo": False, "konec": True}

    url, klic = db.jellyfin_connection()
    try:
        async with JellyfinClient(url, klic, timeout=QUICK_TIMEOUT) as jf:
            if not await jf.quick_connect_potvrzeno(tajemstvi):
                return {"hotovo": False}
            udaje = await jf.quick_connect_dokonci(tajemstvi)
    except JellyfinError as chyba:
        log.warning("quick connect: dotaz na stav selhal: %s", chyba)
        return {"hotovo": False}

    if not udaje or udaje.get("vypnuty"):
        request.session.pop("quick_secret", None)
        return {"hotovo": False, "konec": True}

    ucet = accounts.z_jellyfinu(udaje)
    prihlas_do_relace(request, ucet)
    accounts.zapomen_neuspechy(_adresa_klienta(request))
    log.info("prihlaseni pres Quick Connect: %s", udaje.get("jmeno"))
    return {"hotovo": True}


def _login_chyba(request: Request, hlaska: str):
    """Přihlašovací stránka s vysvětlením, proč to nešlo."""
    return templates.TemplateResponse(
        request, "login.html",
        {"error": i18n.translate(hlaska),
         "jellyfin_login": pristup.login_zapnuty()},
        status_code=400)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Pomocniky
# ---------------------------------------------------------------------------

def _blokace_hlaska(zbyva: int) -> str:
    """Text u zablokovaného přihlášení.

    Záměrně neříká, kolikátá blokace to je ani po kolika pokusech přijde -
    tyhle údaje pomáhají jen tomu, kdo hádá.

    Překládá se tady, ne v šabloně: čas se do věty dosazuje až po překladu,
    jinak by hotová věta v slovníku nebyla k nalezení.
    """
    if zbyva < 0:
        return i18n.translate("Přihlašování z této adresy je zablokované. "
                              "Odblokovat ho může správce v Nastavení.")
    minut = zbyva // 60
    if minut >= 1:
        return i18n.translate("Příliš mnoho pokusů. Zkus to za {n} min.").format(n=minut)
    return i18n.translate("Příliš mnoho pokusů. Zkus to za {n} s.").format(
        n=max(1, zbyva))


def _adresa_klienta(request: Request) -> str:
    """Odkud požadavek přišel. Klíč brzdy u přihlašování a IP v záznamu
    použití API klíčů.

    Adresu bereme z `request.client` a nikam jinam se nedíváme - ačkoliv
    to tady dřív bylo naopak.

    Za reverzní proxy totiž skutečnou adresu doplní **uvicorn sám**: v
    run.py se spouští s `proxy_headers` a se seznamem důvěryhodných proxy
    (FORWARDED_ALLOW_IPS). Hlavičku `X-Forwarded-For` přečte jen tehdy,
    když ji poslal někdo z toho seznamu.

    Původně tu byla druhá, vlastní verze téhož - jenže ta se ptala jen
    "je proxy vůbec nastavená?" a hlavičce pak věřila komukoliv. Kdo se
    dostal na aplikaci přímo (HOST=0.0.0.0 bez proxy před sebou), poslal
    si `X-Forwarded-For` sám a při každém pokusu o heslo vypadal jako
    někdo jiný - brzda proti hádání hesel tím přestala platit.
    """
    return request.client.host if request.client else "?"
