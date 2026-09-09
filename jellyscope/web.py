"""Webova aplikace - routy, sablony, prihlaseni.

FastAPI je knihovna, ktera prirazuje adresy funkcim. Kdyz prohlizec pozada
o "/library", zavola se funkce oznacena @app.get("/library"). Nic vic v tom
neni.

Kazda funkce tady dela totez ve trech krocich:
    1. precti parametry z adresy (napr. za jak dlouhe obdobi)
    2. zeptej se modulu stats / insights na data
    3. predej data sablone, at je vykresli

Zadne SQL, zadne pocitani. Kdyz se v tomhle souboru objevi vzorec, patri
jinam.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional


from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import (accounts, api, applog, charts, collector, db, dbmigrate, dialect,
               formatting, geoip,
               updates,
               i18n, importers, insights, jellyfin, langstats, languages,
               odklizeni,
               scanner, sekce,
               stats, tasks)
# Verze běžícího procesu. Schválně natvrdo při importu: po `git pull`
# leží na disku nová, ale tenhle proces pořád běží starý kód - a čekárna
# po aktualizaci potřebuje vědět přesně to, co běží tady, ne co je na
# disku. Až se proces vymění, načte se soubor znovu i s novým číslem.
from . import __version__
# PROJECT_DIR je kořen projektu (tam, kde je run.py a složka data),
# PACKAGE_DIR je tenhle balíček. Nejsou totéž a plete se to snadno -
# proto mají různá jména místo jednoho BASE_DIR.
from .config import BASE_DIR as PROJECT_DIR
from .config import load_config
from .i18n import translate as _t

# Zaklad webu bydli vedle - potrebuji ho vsechny casti rout, takze
# by jinak musely importovat tenhle modul a on je.
from .web_zaklad import (  # noqa: F401 - dal se pouzivaji i zvenku
    KAPACITA_JEDNOTKY,
    PACKAGE_DIR,
    SETTINGS_SECTIONS,
    STARTED_AT,
    STROP_MAX,
    STROP_MIN,
    TEMPLATES_DIR,
    VZHLEDY,
    ZOOM_REZIMY,
    ZPET_NA_IMPORT,
    _cas_presne,
    _cesky_datum,
    _clamp,
    _context,
    _flash,
    _hlaska_importu,
    _known_note,
    _linked_note,
    _obdobi_do_sablony,
    _obdobi_popis,
    _opraveno_note,
    _posledni_den,
    _rychla_obdobi,
    _stropy,
    _veta,
    _vzhled,
    _zoom_rezim,
    config,
    current_account,
    log,
    require_admin,
    require_login,
    templates,
)
from .jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError


STATIC_DIR = PACKAGE_DIR / "static"

# Obdobi, ktera si uzivatel muze vybrat. Pevny seznam - do SQL se nikdy
# nedostane cislo primo z adresy.
ALLOWED_DAYS = (7, 30, 90, 365)
DAY_LABELS = {7: "7 dnů", 30: "30 dnů", 90: "90 dnů", 365: "rok"}

# Jedno vychozi obdobi pro celou aplikaci. Drive melo kazde stranka vlastni
# (nekde 30 dnu, jinde rok) a pri prepinani zalozek se cislo neocekavane
# menilo. Ted je spolecne a zvolena hodnota se drzi napric strankami.
DEFAULT_DAYS = 30
DAYS_SESSION_KEY = "days"

# Tvar data pro filtr v historii (proklik z tabulky na Prehledu).
_VALID_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Co se stane pri startu a pri vypnuti aplikace.

    Tady se poustí sberac dat na pozadi. Bezi soubezne s webem: zatimco
    ty klikas po strankach, on se kazdych par sekund pta Jellyfinu,
    co se prave hraje.
    """
    # Zápis logu do souboru zapínáme jako první, ať se do něj dostane
    # i případná chyba při přípravě databáze - právě ta je nejzajímavější.
    soubor = applog.setup()
    if soubor:
        log.info("log se píše i do souboru: %s", soubor)

    db.init_db()
    log.info("databaze pripravena")

    # Zona aplikace do prostredi procesu.
    #
    # Vypis casu si ji bere sam (formatting.zona()), ale deleni na dny
    # a hodiny dela SQL - SQLite modifikatorem 'localtime', PostgreSQL
    # podle zony spojeni. Obojí cte zonu OPERACNIHO SYSTEMU, ne nasi
    # promennou, takze ji musime nastavit do prostredi drive, nez se
    # otevre prvni spojeni.
    #
    # `tzset()` je jen na unixu. Na Windows se zona procesu za behu
    # prepnout neda, takze tam plati systemova - vypis casu je spravne
    # tak jako tak, jen deleni dnu jde podle stroje. Ostry provoz bezi
    # na Linuxu, kde to plati cele.
    zona_aplikace = (db.get_setting("app_timezone", "") or "").strip()
    if zona_aplikace:
        os.environ["TZ"] = zona_aplikace
        if hasattr(time, "tzset"):
            time.tzset()
            log.info("casova zona aplikace: %s", zona_aplikace)
        else:
            log.warning("casovou zonu %s nejde na tomhle systemu nastavit procesu; "
                        "vypis casu ji respektuje, deleni dnu v grafech ne",
                        zona_aplikace)

    # Do logu, at se pri dotazu "proc mi to rika, ze jsem v kontejneru"
    # nemusi hadat. Rozlisujeme dve veci: jakykoliv kontejner (rozhoduje
    # o zalohach) a nas obraz (rozhoduje o aktualizaci).
    if config.in_docker or config.nas_obraz:
        log.info("prostredi: kontejner=%s, nas obraz=%s",
                 config.in_docker, config.nas_obraz)

    # V kontejneru si slozku na zalohy nastavime sami - viz db.predvyplnene_zalohy().
    zalohy = db.predvyplnene_zalohy()
    if zalohy:
        log.info("v kontejneru: slozka na zalohy nastavena na %s", zalohy)

    # Polozky, ktere do knihovny nikdy nemely prijit (serial misto dilu).
    # Bez toho by se pri kazde synchronizaci znovu tvarily jako zmizele.
    fantomu = scanner.uklid_fantomu()
    if fantomu:
        log.info("z knihovny odstraneno %s polozek, ktere do ni nepatri", fantomu)

    # Vyprsele blokace prihlasovani uz nic nerikaji - trvale zustavaji.
    applog.nastav_jazyk(db.get_setting("log_language", "cs"))

    smazano = accounts.uklid_blokaci()
    if smazano:
        log.info("uklizeno %s starych blokaci prihlasovani", smazano)

    background = []
    if config.demo_mode:
        # V ukazkovem rezimu neni na co se pripojovat. Sberac by jen plnil
        # log chybami a pri startu uzavrel vymyslene prehravani, ktere ma
        # byt na Prehledu videt.
        db.set_setting(collector.STATUS_KEY, "demo")
        log.info("ukazkovy rezim - sberac se nespousti")
    else:
        background.append(
            asyncio.create_task(collector.run_forever(), name="collector")
        )
        background.append(
            asyncio.create_task(tasks.run_scheduler(), name="scheduler")
        )

    try:
        yield
    finally:
        # Pri vypinani ulohy slusne ukoncime a pockame, az doopravdy skonci.
        for job in background:
            job.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        log.info("ulohy na pozadi ukonceny")
        # Zasobnik spojeni drzi u PostgreSQL otevrena spojeni. Kdyz ho
        # nezavreme, zustanou na serveru viset az do jeho vlastniho limitu.
        db.close_pool()



app = FastAPI(title="Jellyscope", lifespan=lifespan, docs_url=None, redoc_url=None)
def _cesta_odkud_prisel(referer: str | None) -> str:
    """Cesta z `referer`, nebo domů. Nikdy adresa na cizí server.

    Vrací se schválně jen cesta - bez schématu a hostitele. Takový odkaz
    je vždycky na tomtéž serveru, takže se nedá zneužít k odvedení
    člověka jinam, a přitom ho vrátí přesně tam, kde byl.

    `//zlo.cz/x` vypadá jako cesta, ale prohlížeč ho čte jako adresu na
    cizí server, takže neprojde.
    """
    adresa = urlsplit(referer or "")
    kam = adresa.path or "/"
    if adresa.query:
        kam = f"{kam}?{adresa.query}"
    if not kam.startswith("/") or kam.startswith("//"):
        return "/"
    return kam


@app.middleware("http")
async def ukazkovy_rezim(request: Request, call_next):
    """V ukázce se nic nemění - místo akce se objeví hláška.

    Ukázka běží na veřejné adrese a přihlašovací údaje jsou rovnou
    v přihlašovacím okně, takže dovnitř se dostane kdokoliv. Bez téhle
    pojistky by první návštěvník přepsal adresu Jellyfinu, spustil import
    nebo restart - a pro všechny ostatní by ukázka skončila.

    Hlídá se to tady, na jednom místě, a ne v každé routě zvlášť: routa,
    na kterou by se zapomnělo, je přesně ta, kterou někdo najde. Seznam
    výjimek je v DEMO_POVOLENO.

    Tlačítka zůstávají vidět schválně - ukázka má ukázat, co aplikace
    umí. Jen místo práce odpoví hláškou.
    """
    if _demo_blokuje(request):
        _flash(request, "Tohle je ukázka – data se v ní nemění. "
                        "Na vlastní instalaci tlačítko funguje.", "info")
        # Zpátky přesně tam, odkud člověk přišel - ne na Přehled. Kdo
        # zkoumá Nastavení, má po kliknutí zůstat v Nastavení; vyhodit ho
        # na úvodní stránku je trest za zvědavost.
        #
        # `referer` posílá prohlížeč u formuláře odeslaného ze stránky
        # vždycky - jenže je to hlavička od návštěvníka, tedy údaj zvenku.
        # Přesměrovat podle ní na celou adresu by z ukázky udělalo
        # otevřený přesměrovávač.
        #
        # Bere se proto jen **cesta**. Hostitel se neporovnává vůbec:
        # kontrola "začíná naší adresou" tu jednou byla a pouštěla
        # `https://jellyscope.cz.utocnik.cz/`, protože naší adresou
        # opravdu začíná. Relativní cesta nikam odejít nemůže, ať referer
        # přijde odkudkoliv, a nerozbije se za proxy, kde se liší schéma
        # i port.
        kam = _cesta_odkud_prisel(request.headers.get("referer"))
        return RedirectResponse(kam, status_code=303)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key,
    session_cookie="jellyscope_session",
    max_age=14 * 24 * 3600,
    # same_site="lax" znamena, ze cookie se neposle pri pozadavku z ciziho
    # webu. Diky tomu nemuze cizi stranka odeslat formular tvym jmenem.
    same_site="lax",
    # Za HTTPS proxy zapnout pres SECURE_COOKIES=1 v .env. Prohlizec pak
    # cookie posle jen po sifrovanem spojeni. Pri behu na localhostu bez
    # HTTPS musi zustat vypnute, jinak by se nikdo neprihlasil.
    https_only=config.secure_cookies,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Tri hlavicky, ktere prohlizeci rikaji, co s odpovedi nesmi delat.
# Stoji jeden middleware a zavirají cele skupiny utoku dopredu.
# Co smi ukazkovy rezim zmenit: prihlaseni a odhlaseni, nic vic.
#
# Ukazka bezi na verejne adrese a prihlasit se do ni muze kdokoliv -
# udaje jsou v prihlasovacim okne. Cokoliv ulozeneho plati pro vsechny
# dalsi navstevniky, takze i neskodne veci jako jazyk rozhrani jsou
# zamcene: kdo si prepne na cestinu, prepne ji i tomu po sobe.
#
# Tlacitka zustavaji videt schvalne: ukazka ma ukazat, co aplikace umi.
# Misto akce se objevi hlaska.
# Ctecí API. Vlastni modul a vlastni zavora: stranky se prokazuji
# prihlasovaci cookie, API tokenem. Nic pod /api/ nic nemeni - jsou tam
# sama GET.
app.include_router(api.router)


DEMO_POVOLENO = frozenset({
    "/login",
    "/logout",
})


def _demo_blokuje(request: Request) -> bool:
    """Ma se tenhle pozadavek v ukazce zastavit?"""
    if not config.demo_mode or request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    return request.url.path not in DEMO_POVOLENO


# Jedno misto, at se pravidla nerozejdou s tim, co stranky opravdu delaji.
CSP = "; ".join((
    "default-src 'self'",
    # Ani data: - obrazky chodi vsechny pres /image, takze neni co povolovat.
    "img-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline'",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    # Zadne <object>, <embed> ani applety - aplikace je nepouziva.
    "object-src 'none'",
))


@app.middleware("http")
async def bezpecnostni_hlavicky(request: Request, call_next):
    response = await call_next(request)
    # "Neuhaduj typ obsahu." Bez toho prohlizec u souboru, ktery vypada
    # jako HTML, ignoruje deklarovany typ a spusti ho jako stranku -
    # tyka se to hlavne obrazku, ktere vodime z Jellyfinu.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # "Nevkladej me do ramecku na cizi strance." Bez toho jde stranku
    # prekryt neviditelnou vrstvou a nechat cloveka klikat na neco
    # jineho, nez si mysli (clickjacking).
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # "Pri odchodu na cizi web neposilej, odkud clovek prisel."
    # Adresy Jellyscope obsahuji id polozek i uzivatelu.
    response.headers.setdefault("Referrer-Policy", "same-origin")
    # Nic z tohohle aplikace nepotrebuje, tak at si to o to nemuze rict
    # ani kus stranky, ktery by se sem nedopatrenim dostal.
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    # HSTS jen tam, kde uz aplikace bezi po HTTPS (SECURE_COOKIES) -
    # prohlizec si ho pamatuje dlouho, takze zaplé na serveru bez
    # certifikatu by lidi zamklo venku. Bez `includeSubDomains`
    # a `preload` zamerne: to jsou rozhodnuti o cele domene, ne o nas.
    if config.secure_cookies:
        response.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000")
    # Odkud smi stranka cokoliv nacist a kam smi cokoliv poslat.
    #
    # Jellyscope nema jediny cizi zdroj - zadne CDN, zadne pismo z internetu,
    # obrazky vodime pres vlastni server. Muzeme si proto dovolit rict
    # "jenom odsud" a nic tim nerozbit.
    #
    # `unsafe-inline` u skriptu je ustupek: stranky maji vlastni <script>
    # bloky a par onclick= primo ve znacce. CSP tedy nezastavi vlozeny
    # skript - zastavi ale to, co s nim utocnik chce delat: stahnout si
    # kod odjinud (script-src), odeslat data na cizi server (connect-src),
    # poslat formular jinam (form-action) nebo stranku zaramovat
    # (frame-ancestors). Kdyby se onclick= jednou prepsaly na
    # addEventListener, da se `unsafe-inline` vymenit za nonce.
    response.headers.setdefault("Content-Security-Policy", CSP)
    return response



formatting.register(templates.env)
# Druh polozky cesky ("Episode" -> "Díl seriálu"). Bydli ve stats, protoze
# tam se rozhoduje, co je film, serial a co "ostatní" - sablona jen vypisuje.
templates.env.filters["type_name"] = stats.nazev_typu
# Sablony smi volat funkce z charts - kresleni patri do sablony, ne do routy.
templates.env.globals["charts"] = charts
templates.env.globals["day_labels"] = DAY_LABELS
templates.env.globals["allowed_days"] = ALLOWED_DAYS
# `lang` je v sablonach potreba na kazde strance, kde se zobrazuje jazyk -
# proto globalne, aby ho nemusela predavat kazda routa zvlast.
templates.env.globals["lang"] = languages
# Preklad rozhrani - v sablonach se pouziva jako funkce _("text").
i18n.register(templates.env)


def _asset_version() -> str:
    """Otisk statickych souboru pro adresu stylu a obrazku.

    K cemu to je: prohlizec si CSS ulozi a priste ho nestahuje znovu.
    U reverzni proxy, ktera /static/ jeste cachuje (viz priklady ve
    slozce deploy/), by zmena vzhledu byla tyden neviditelna - uzivatel
    by videl starou stranku a marne hledal, proc uprava nefunguje.

    Reseni je stara a spolehliva finta: k adrese se pripoji cislo, ktere
    se pri zmene souboru zmeni. Jina adresa = jiny soubor = prohlizec ho
    stahne znovu. Dokud se nic nemeni, cachuje se dal.

    Bereme cas posledni zmeny nejnovejsiho souboru ve static/. Pocita se
    jednou pri startu, takze po uprave stylu je potreba restart - stejne
    jako u sablon.
    """
    try:
        newest = max(path.stat().st_mtime
                     for path in STATIC_DIR.rglob("*") if path.is_file())
    except ValueError:
        return "0"
    return str(int(newest))


templates.env.globals["asset_version"] = _asset_version()
# Ukazkovy rezim pozna i sablona: prihlasovaci stranka v nem rovnou rekne,
# jakymi udaji se dovnitr. Je to globalni promenna, protoze prihlasovaci
# stranka zadny vlastni kontext nema.
templates.env.globals["demo_mode"] = config.demo_mode


# ---------------------------------------------------------------------------
# Prihlaseni
#
# Aplikace je cela za prihlasenim - bez uctu se nedostanes nikam krome
# prihlasovaci stranky. Ucty jsou v tabulce `accounts` a spravuji se
# v Nastaveni; s uzivateli Jellyfinu nemaji nic spolecneho.
# ---------------------------------------------------------------------------







@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, exc: HTTPException):
    """Presmerovani misto chybove hlasky u nepřihlaseneho uzivatele."""
    if exc.status_code == 307 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    if exc.status_code == 403:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 403, "message": exc.detail}, status_code=403,
        )
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 404, "message": exc.detail or "Stranka nenalezena."},
            status_code=404,
        )
    # Hlavičky výjimky patří do odpovědi. U 401 z API je v nich
    # `WWW-Authenticate: Bearer`, tedy jediná věta, která volajícímu
    # řekne, čím se má prokázat - bez ní je to jen "nesmíš".
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                        headers=exc.headers or None)


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


@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    if accounts.any_exists():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", _setup_context())


@app.post("/setup")
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
    request.session["account_id"] = account_id
    _flash(request, i18n.translate("Účet vytvořen. Vítej v Jellyscope.", ui_language),
           "success")
    return RedirectResponse("/settings", status_code=303)


# ---- prihlaseni a odhlaseni -------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not accounts.any_exists():
        return RedirectResponse("/setup", status_code=303)
    if current_account(request) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(
    request: Request, username: str = Form(""), password: str = Form("")
):
    # Brzda proti hádání hesel. Klíčem je adresa, ze které pokus přišel -
    # ne uživatelské jméno: podle jména by šlo cizí účet snadno zamknout
    # a majitele tím vyšoupnout ven.
    klic = _adresa_klienta(request)
    zbyva = accounts.blokace_zbyva(klic)
    if zbyva:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": _blokace_hlaska(zbyva), "username": username},
            status_code=429,
        )

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
                 "username": username},
                status_code=429,
            )
        # Zamerne nerikame, jestli bylo spatne jmeno nebo heslo. Kdybychom
        # to rozlisili, dal by se timhle zpusobem zjistit seznam uctu.
        return templates.TemplateResponse(
            request, "login.html",
            {"error": i18n.translate("Špatné jméno nebo heslo."),
             "username": username},
            status_code=401,
        )

    accounts.zapomen_neuspechy(klic)

    # Pri prihlaseni zahodime starou relaci a zalozime novou. Brani to
    # utoku, pri kterem ti nekdo podstrci sve ID relace jeste pred
    # prihlasenim a pak se do ni "sveze".
    request.session.clear()
    request.session["account_id"] = account["id"]
    # Vlastní přehled je po přihlášení první, co člověk uvidí - o to jde.
    # Prázdný ale ne: to by byl horší začátek než Přehled.
    kam = "/dashboard" if (sekce.je_zapnuty() and sekce.nacti_rozvrzeni()) else "/"
    return RedirectResponse(kam, status_code=303)


@app.post("/logout")
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
    """Odkud požadavek přišel. Slouží jen jako klíč brzdy u přihlašování.

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



def _days(request: Request, value: Optional[int],
          od: Optional[str] = None, do: Optional[str] = None) -> Any:
    """Zvolene obdobi - spolecne pro vsechny stranky.

    Vraci bud pocet dnu (7, 30, 90, 365), nebo `stats.Obdobi` s vlastnimi
    mezemi. Statistiky prijmou obojí - viz stats._meze().

    Kdyz je v adrese platna hodnota, pouzijeme ji a zapamatujeme si ji.
    Kdyz v adrese nic neni, vezmeme naposledy zvolene. Diky tomu prepnuti
    na "rok" na Prehledu plati i po prechodu na Jazyky - okno je synchronni.
    Vlastni obdobi se pamatuje stejne, jen jako dvojice datumu.

    Cislo se porovnava proti pevnemu seznamu a datumy projdou pres
    `strptime`, takze se do SQL nikdy nedostane text primo z adresy.
    """
    # Vyber tazenim v grafu posila okamziky, ne datumy - viz
    # stats.obdobi_z_okamziku(). Ma prednost: kdyz clovek zrovna tahnul
    # mysi, chce videt prave to.
    od_ts = request.query_params.get("od_ts")
    do_ts = request.query_params.get("do_ts")
    if od_ts and do_ts:
        obdobi = stats.obdobi_z_okamziku(od_ts, do_ts)
        if obdobi is not None:
            request.session[DAYS_SESSION_KEY] = _obdobi_do_session(obdobi)
            return obdobi

    if od or do:
        # Datum se pise po cesku (1.8.2026), bere se i RRRR-MM-DD - stejny
        # parser jako ve filtru historie, at se to nikde nechova jinak.
        obdobi = stats.obdobi_od_do(_datum_z_textu(od), _datum_z_textu(do))
        if obdobi is not None:
            request.session[DAYS_SESSION_KEY] = _obdobi_do_session(obdobi)
            return obdobi

    if value in ALLOWED_DAYS:
        request.session[DAYS_SESSION_KEY] = int(value)
        return int(value)

    remembered = request.session.get(DAYS_SESSION_KEY)
    if isinstance(remembered, dict):
        obdobi = stats.obdobi_od_do(remembered.get("od"), remembered.get("do"))
        if obdobi is not None:
            return obdobi
    if remembered in ALLOWED_DAYS:
        return int(remembered)

    return DEFAULT_DAYS




def _obdobi_do_session(obdobi: stats.Obdobi) -> dict[str, str]:
    """Co si o vlastnim obdobi pamatovat mezi strankami.

    Uklada se v MISTNIM case. Kdyby se ukladaly meze z dotazu (UTC),
    precetly by se pri dalsim kroku znovu jako mistni - a obdobi by se
    pri kazdem prechodu jinam posunulo zpatky, casto o cely den.

    Tvar nese vyznam a stejne ho cte i stats.obdobi_od_do():
    samotne datum je cely den, datum s casem je presny usek vybrany
    tazenim v grafu.
    """
    if obdobi.cely_den:
        return {"od": obdobi.od_mistni[:10], "do": _posledni_den(obdobi.do_mistni)}
    return {"od": obdobi.od_mistni[:16], "do": obdobi.do_mistni[:16]}
































KIND_SESSION_KEY = "kind"
# Vlastni pamet pro filtr u nejsledovanejsich titulu - viz _kind().
TOP_KIND_SESSION_KEY = "top_kind"


# Nejsledovanejsi tituly "Ostatní" nenabizeji: skladaji se z nazvu
# titulu, kdezto "Ostatní" je pytlik na zaznamy, u kterych se o titulu
# nic nevi. Seznam by vysel prazdny nebo plny "6. dilu" bez seriálu.
TOP_ALLOWED_KINDS = (stats.KIND_BOTH, stats.KIND_MOVIE, stats.KIND_SERIES)


def _kind(request: Request, value: Optional[str],
          session_key: str = KIND_SESSION_KEY) -> str:
    """Mix / filmy / serialy / ostatni - stejny princip jako u obdobi.

    Volba se pamatuje v session, aby po prekliknuti jinam a zpatky
    zustala. Porovnava se proti pevnemu seznamu, takze se z adresy
    nikdy nedostane nic do SQL.

    `session_key` odlisuje jednotlive filtry. Nejsledovanejsi tituly maji
    vlastni - kdyby sdilely volbu se sledovanosti po dnech, prepnuti
    u jedne karty by beze slova prekreslilo i druhou. Zaroven maji uzsi
    seznam povolenych hodnot (viz TOP_ALLOWED_KINDS), takze se do nich
    "Ostatní" nepropise ani pres adresu.
    """
    povolene = (TOP_ALLOWED_KINDS if session_key == TOP_KIND_SESSION_KEY
                else stats.ALLOWED_KINDS)
    if value in povolene:
        request.session[session_key] = value
        return value

    remembered = request.session.get(session_key)
    if remembered in povolene:
        return str(remembered)

    return stats.KIND_BOTH
























# ---------------------------------------------------------------------------
# Stranky
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, days: Optional[int] = None, kind: Optional[str] = None,
              od: Optional[str] = None, do: Optional[str] = None,
              top_kind: Optional[str] = None,
              account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, od, do)
    kind = _kind(request, kind)
    top_kind = _kind(request, top_kind, TOP_KIND_SESSION_KEY)
    # Souhrn, změny proti minulému období a případné vysvětlení, proč
    # se srovnat nedá - vše na jednom místě, protože tatáž čísla kreslí
    # i sekce vlastního přehledu.
    souhrn = sekce.souhrn_obdobi(days)

    daily = stats.daily_activity_split(days)

    return templates.TemplateResponse(request, "dashboard.html", _context(
        request, account,
        days=days,
        kind=kind,
        **souhrn,
        daily=daily,
        # Z ceho se sklada "Ostatní". Kresli se z toho popisek pod grafem -
        # bez nej je ta serie slepa skvrna: hodiny vidis, ale nevis, co to
        # bylo, a v historii uz to nedohledas.
        ostatni=stats.rozpad_ostatnich(days),
        top_users=stats.top_users(days),
        top_items=stats.top_items(days, kind=top_kind),
        top_kind=top_kind,
        methods=stats.play_method_breakdown(days),
        clients=stats.client_breakdown(days),
        heatmap=stats.hourly_heatmap(days),
        active=stats.active_sessions(),
        recent=stats.recently_added(),
        # Podle čeho stránka pozná, že mezitím doběhla synchronizace
        # a přibyly nové tituly - viz /partials/recently-added.
        library_version=scanner.library_version(),
        # Ve stejném rytmu, v jakém se sběrač ptá Jellyfinu, si stránka
        # vyzvedne kartu "Právě se hraje". Rychleji by to nemělo smysl -
        # novější data zatím nikde nejsou.
        poll_interval=db.get_int_setting("poll_interval", 5, 300, 10),
    ))



# ---------------------------------------------------------------------------
# Srovnani dvou obdobi
# ---------------------------------------------------------------------------
#
# Vlastni pamet, ne ta spolecna: stranka ma obdobi dve a spolecny filtr
# nese jedno. Kdyby si bralo to spolecne, prepnuti tady by prehodilo
# obdobi i na vsech ostatnich strankach - a naopak.
SROVNANI_A_KEY = "srovnani_a"
SROVNANI_B_KEY = "srovnani_b"


def _obdobi_srovnani(request: Request, klic: str, days: Optional[int],
                     od: Optional[str], do: Optional[str], vychozi: Any) -> Any:
    """Jedno ze dvou obdobi na strance Srovnani.

    Chova se jako `_days()`, jen si pamatuje pod vlastnim klicem a nezna
    vyber tazenim v grafu - na teto strance zadny graf k tazeni neni.
    """
    if od or do:
        obdobi = stats.obdobi_od_do(_datum_z_textu(od), _datum_z_textu(do))
        if obdobi is not None:
            request.session[klic] = _obdobi_do_session(obdobi)
            return obdobi

    if days in ALLOWED_DAYS:
        request.session[klic] = int(days)
        return int(days)

    zapamatovane = request.session.get(klic)
    if isinstance(zapamatovane, dict):
        obdobi = stats.obdobi_od_do(zapamatovane.get("od"), zapamatovane.get("do"))
        if obdobi is not None:
            return obdobi
    if zapamatovane in ALLOWED_DAYS:
        return int(zapamatovane)

    return vychozi


def _dotaz_obdobi(predpona: str, zadani: Any) -> dict[str, str]:
    """Obdobi jako dvojice do adresy - aby odkazy jednoho to druhe neztratily.

    Kdyby v odkazu chybelo, vratilo by se pri kazdem prepnuti na to, co je
    v pameti, a vyber druheho obdobi by pod rukama skakal.

    Vraci se slovnik, ne hotovy retezec: sablona ho potrebuje dvakrat -
    v adrese odkazu (`| urlencode`) a jako skryta pole formulare.
    """
    if isinstance(zadani, stats.Obdobi):
        od = (zadani.od_mistni or zadani.od)[:10]
        do = _posledni_den(zadani.do_mistni or zadani.do)
        return {f"{predpona}_od": od, f"{predpona}_do": do}
    return {f"{predpona}_days": str(int(zadani))}


@app.get("/srovnani", response_class=HTMLResponse)
def srovnani(request: Request,
             a_days: Optional[int] = None, a_od: Optional[str] = None,
             a_do: Optional[str] = None,
             b_days: Optional[int] = None, b_od: Optional[str] = None,
             b_do: Optional[str] = None,
             account: dict[str, Any] = Depends(require_login)):
    """Dve libovolna obdobi vedle sebe.

    Vychozi dvojice je to, co uz clovek zna z Prehledu: zvolene obdobi
    a stejne dlouhe okno pred nim. Stranka tak neco ukazuje hned a teprve
    kdo chce "srpen versus prosinec", saha na prepinace.
    """
    prvni = _obdobi_srovnani(request, SROVNANI_A_KEY, a_days, a_od, a_do,
                             vychozi=_days(request, None))
    druhe = _obdobi_srovnani(request, SROVNANI_B_KEY, b_days, b_od, b_do,
                             vychozi=stats.predchozi(prvni))

    return templates.TemplateResponse(request, "srovnani.html", _context(
        request, account,
        srovnani=stats.srovnani(prvni, druhe),
        obdobi_a=_obdobi_do_sablony(prvni),
        obdobi_b=_obdobi_do_sablony(druhe),
        # Do odkazu jednoho prepinace patri stav toho druheho.
        dotaz_a=_dotaz_obdobi("a", prvni),
        dotaz_b=_dotaz_obdobi("b", druhe),
    ))


@app.get("/partials/top-items", response_class=HTMLResponse)
def top_items_partial(
    request: Request,
    kind: Optional[str] = None,
    days: Optional[int] = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Jen karta "Nejsledovanější tituly".

    Přepnutí filtru vymění tuhle jednu kartu, takže stránka zůstane tam,
    kde je. Dřív se načítala celá a odrolovala na začátek - a tahle karta
    je až v druhé polovině Přehledu.
    """
    return templates.TemplateResponse(request, "_top_items.html", _context(
        request, account,
        top_kind=_kind(request, kind, TOP_KIND_SESSION_KEY),
        top_items=stats.top_items(_days(request, days, od, do),
                                  kind=_kind(request, kind, TOP_KIND_SESSION_KEY)),
    ))


@app.get("/partials/recently-added", response_class=HTMLResponse)
def recently_added_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Jen pás "Nedávno přidané".

    Dokud tohle nebylo, musel člověk po doběhnutí synchronizace stránku
    obnovit ručně - jinak se na už otevřeném Přehledu nový film neobjevil.
    Teď si ho stránka vymění sama, jakmile se změní otisk knihovny.

    Vrací se hotové HTML, ne JSON: kreslí se **stejnou šablonou** jako při
    běžném načtení, takže není druhé místo, které se časem rozejde s prvním.
    """
    return templates.TemplateResponse(request, "_recently_added.html", _context(
        request, account,
        recent=stats.recently_added(),
    ))


@app.get("/partials/network-live", response_class=HTMLResponse)
def network_live_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Jen karta "Právě teče" ze stránky Síť.

    Stejný nápad jako u "právě se hraje": stránka Síť ukazovala stav
    z okamžiku načtení, takže kdo ji nechal otevřenou, viděl minulost.
    Obnovuje se jen tenhle výřez - kdyby se načítala celá stránka,
    odrolovala by na začátek a zahodila rozečtenou tabulku adres.
    """
    # Obdobi se bere z relace - tam si ho ulozila stranka, kdyz ho clovek
    # vybral. Vyrez si ho tedy nemusi predavat v adrese a zustane platny
    # i po prepnuti filtru bez obnoveni stranky.
    days = _days(request, None)
    return templates.TemplateResponse(request, "_sit_zive.html", _context(
        request, account,
        days=days,
        ted=stats.tok_ted(),
        zive=stats.bandwidth_zive(days),
    ))


@app.get("/partials/now-playing", response_class=HTMLResponse)
def now_playing_partial(
    request: Request, account: dict[str, Any] = Depends(require_login)
):
    """Jen karta "Právě se hraje".

    Sběrač se ptá Jellyfinu každých pár vteřin, ale stránka o tom nevěděla -
    ukazovala stav z okamžiku načtení, dokud ji člověk neobnovil ručně.
    Tenhle výřez si Přehled ve stejném rytmu vyzvedne sám.

    Vrací se hotové HTML, ne JSON: kreslí se **stejnou šablonou** jako při
    běžném načtení, takže není druhé místo, které se časem rozejde s prvním.
    """
    return templates.TemplateResponse(request, "_now_playing.html", _context(
        request, account,
        active=stats.active_sessions(),
    ))


@app.get("/partials/daily", response_class=HTMLResponse)
def daily_partial(
    request: Request,
    kind: Optional[str] = None,
    days: Optional[int] = None,
    od: Optional[str] = None,
    do: Optional[str] = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Jen karta "Sledovanost po dnech" - bez zbytku stranky.

    K cemu to je: prepnuti filmy/serialy drive znamenalo nacist celou
    stranku znovu, coz prohlizec odmenil skokem na zacatek. Tady se
    vymeni jen ta jedna karta, takze zustanes presne tam, kde jsi byl.

    Kresli se **stejnou sablonou** jako na Prehledu (_daily_card.html),
    ne jeji kopii. Kdyby to byly dva soubory, jeden z nich by casem
    odesel jinam a nikdo by si toho nevsiml.
    """
    days = _days(request, days, od, do)
    kind = _kind(request, kind)
    return templates.TemplateResponse(request, "_daily_card.html", _context(
        request, account,
        days=days,
        kind=kind,
        daily=stats.daily_activity_split(days),
        ostatni=stats.rozpad_ostatnich(days),
    ))


@app.get("/library", response_class=HTMLResponse)
def library_index(request: Request, days: Optional[int] = None,
                  od: Optional[str] = None, do: Optional[str] = None,
                  account: dict[str, Any] = Depends(require_login)):
    """Rozcestnik: dlazdice jednotlivych knihoven z Jellyfinu.

    Obdobi je tytez jako vsude jinde - jedna volba pro celou aplikaci,
    vcetne vlastniho rozmezi. Tyka se jedineho grafu na strance (rust
    knihovny); dlazdice nad nim jsou stav, ne obdobi, a rikaji to samy.
    """
    days = _days(request, days, od, do)
    return templates.TemplateResponse(request, "library_index.html", _context(
        request, account,
        days=days,
        libraries=stats.library_cards(),
        coverage=stats.tech_coverage(),
        codecs=stats.codec_breakdown(),
        resolutions=stats.resolution_breakdown(),
        ranges=stats.video_range_breakdown(),
        **sekce.data_rustu(days),
    ))


@app.get("/library/{library_id}", response_class=HTMLResponse)
def library_detail(
    request: Request,
    library_id: str,
    tab: str = "overview",
    search: Optional[str] = None,
    sort: str = "size",
    page: int = 1,
    archived: int = 0,
    account: dict[str, Any] = Depends(require_login),
):
    """Detail jedne knihovny. Zalozky se prepinaji parametrem v adrese.

    Proc parametrem a ne JavaScriptem: kazda zalozka ma pak vlastni adresu,
    takze funguje zpetne tlacitko, da se poslat odkaz a stranka se nemusi
    nacitat cela dopredu.
    """
    library_row = stats.library(library_id)
    if library_row is None:
        raise HTTPException(status_code=404, detail="Knihovna nenalezena.")

    if tab not in ("overview", "media", "activity"):
        tab = "overview"

    show_archived = bool(archived)
    context: dict[str, Any] = {
        "library": library_row,
        "tab": tab,
        "search": search or "",
        "sort": sort,
        "archived": show_archived,
        "archived_count": stats.archived_count(library_id),
        "overview": stats.library_overview(library_id),
        # Tlacitko "dopocitat technicka data" ma smysl jen pri ffprobe -
        # z Jellyfinu se data berou samy pri synchronizaci.
        "tech_source": db.get_setting("tech_source", "jellyfin"),
    }

    if tab == "overview":
        context.update(
            codecs=stats.codec_breakdown(library_id),
            resolutions=stats.resolution_breakdown(library_id),
            languages_in_library=langstats.library_languages(
                langstats.colour_map(), library_id
            ),
        )
    elif tab == "media":
        page = max(1, page)
        per_page = 48
        # Serialy se v seznamu ukazuji jako jeden radek, ne po dilech -
        # viz stats.library_rows(). Kvuli tomu se pocita i strankovani
        # ze skupin, ne z polozek.
        total = stats.library_rows_count(library_id, search, archived=show_archived)
        context.update(
            items=stats.library_rows(per_page, (page - 1) * per_page, library_id,
                                     search, sort, archived=show_archived),
            total=total,
            page=page,
            pages=max(1, (total + per_page - 1) // per_page),
        )
    else:
        context.update(activity=stats.library_activity(library_id))

    return templates.TemplateResponse(
        request, "library_detail.html", _context(request, account, **context)
    )


@app.get("/series/{series_id}", response_class=HTMLResponse)
def series_detail(
    request: Request, series_id: str, account: dict[str, Any] = Depends(require_login)
):
    """Seriál rozdělený na řady a v nich díly.

    V seznamu knihovny je seriál jeden řádek - jinak by u seriálu o deseti
    řadách zabral půl stránky a všechno ostatní by v něm zaniklo. Rozpad
    na díly je až tady, kde ho člověk doopravdy hledá.
    """
    series = stats.series_detail(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Seriál nenalezen.")

    return templates.TemplateResponse(request, "series_detail.html", _context(
        request, account,
        series=series,
        library=stats.library(series["library_id"]) if series["library_id"] else None,
    ))


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_detail(
    request: Request, item_id: str, account: dict[str, Any] = Depends(require_login)
):
    """Detail jedne polozky - vcetne vsech zvukovych stop a titulku."""
    item_row = stats.item(item_id)
    if item_row is None:
        raise HTTPException(status_code=404, detail="Polozka nenalezena.")

    return templates.TemplateResponse(request, "item_detail.html", _context(
        request, account,
        item=item_row,
        # Rozsah slucuje zmereny udaj s tim, co hlasi Jellyfin - viz
        # stats.ROZSAH_CASE. Sablona nema kde to spocitat.
        rozsah=stats.rozsah_polozky(item_row),
        streams=stats.item_streams(item_id),
        playback=stats.item_playback(item_id),
        summary=stats.item_playback_summary(item_id),
        siblings=stats.sibling_episodes(item_row),
        lang=languages,
    ))


@app.post("/series/{series_id}/refresh")
async def series_refresh(request: Request, series_id: str,
                         account: dict[str, Any] = Depends(require_admin)):
    """Znovu načte metadata všech dílů seriálu - i s obrázky.

    Stejná akce jako u jednoho dílu, jen pro celý seriál: po opravě
    špatně určeného seriálu v Jellyfinu se mění všechny díly najednou.
    """
    vysledek = await scanner.refresh_series(series_id)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
        return RedirectResponse(f"/series/{series_id}", status_code=303)

    zprava = _t("Metadata načtena znovu: {n} dílů").format(n=vysledek["dilu"])
    tech = vysledek.get("tech") or {}
    if tech.get("ok"):
        zprava += " " + _t("(včetně změření souborů)")
    _flash(request, zprava, "success")
    return RedirectResponse(f"/series/{series_id}", status_code=303)


@app.post("/item/{item_id}/refresh")
async def item_refresh(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Znovu načte metadata jedné položky z Jellyfinu.

    Na rozdíl od synchronizace knihovny se ptáme na jediné id - kdo
    v Jellyfinu opravil rok nebo jazyk stopy, nemusí kvůli tomu čekat
    na noční průchod celou knihovnou. Z Jellyfinu se přitom jen čte.
    """
    vysledek = await scanner.refresh_item(item_id)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    zprava = _t("Metadata načtena znovu: {nazev}").format(
        nazev=vysledek.get("name", "")).strip()
    tech = vysledek.get("tech") or {}
    if tech.get("ok"):
        zprava += " " + _t("(včetně změření souboru)")
    elif tech.get("failed"):
        zprava += " " + _t("(soubor se změřit nepodařilo - viz Log)")
    _flash(request, zprava, "success")
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@app.post("/item/{item_id}/delete")
def item_delete(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Nenavratne smaze archivovanou polozku i s jeji historii.

    Jen pro spravce a jen rucne. Automaticky se nemaze nikdy - polozka,
    ktera v Jellyfinu zmizi, se jen archivuje. Kdyz Jellyfin na chvili
    vypadne, prijdes jinak o historii kvuli docasnemu vypadku.
    """
    result = stats.delete_item(item_id)
    if result.get("status") != "ok":
        _flash(request, result.get("message", "Smazání selhalo."), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    nazev = result["name"]
    if result.get("series_name"):
        nazev = f"{result['series_name']} - {nazev}"
    _flash(
        request,
        "Smazáno: {nazev} (a {n} záznamů v historii).",
        "success",
        nazev=nazev, n=result["plays"],
    )
    return RedirectResponse("/library", status_code=303)


@app.get("/image/{item_id}")
async def item_image(
    request: Request,
    item_id: str,
    kind: str = "Primary",
    w: int = 400,
    tag: str = "",
    account: dict[str, Any] = Depends(require_login),
):
    """Obrazek polozky - stazeny z Jellyfinu a ulozeny na disk.

    Obrazky vodime pres nas server zamerne: adresa Jellyfinu ani API klic
    se tak nikdy nedostanou do stranky v prohlizeci.

    Kazdy obrazek stahujeme jen jednou. Bez teto pameti by mrizka o padesati
    dlazdicich znamenala padesat dotazu na Jellyfin pri kazdem nacteni.
    """
    if kind not in ("Primary", "Backdrop", "Thumb", "Logo"):
        kind = "Primary"
    w = max(80, min(1200, w))

    # Id z adresy se dál používá **jen v téhle prověřené podobě** - a to
    # na obou místech: v názvu souboru v mezipaměti i v dotazu do
    # Jellyfinu.
    #
    # Proč i v dotazu: id se skládá do cesty `/Items/<id>/Images/<druh>`.
    # Kdyby v něm zůstal otazník nebo lomítko, přihlášený čtenář by si
    # tou cestou mohl říct o jiný koncový bod Jellyfinu - a ten se ptá
    # naším API klíčem, tedy s právy správce. Id z Jellyfinu je vždycky
    # hexadecimální GUID, takže tenhle filtr nic platného nezahodí.
    safe_id = "".join(c for c in item_id if c.isalnum() or c in "-_")
    if not safe_id or safe_id != item_id:
        raise HTTPException(status_code=404, detail="Neplatne id.")

    # Otisk obrazku (ImageTags z Jellyfinu) je soucasti jmena souboru.
    # Diky tomu neni potreba mezipamet nijak "invalidovat": jiny obrazek
    # ma jiny otisk, tedy jinou adresu i jiny soubor. Bez toho drzel
    # Jellyscope navzdy ten prvni obrazek - i kdyz ho clovek v Jellyfinu
    # opravil, protoze spatne urcena polozka mela spatny plakat.
    safe_tag = "".join(c for c in tag if c.isalnum())[:32]

    cache_dir = config.database_path.parent / "imagecache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{safe_id}-{kind}-{w}{'-' + safe_tag if safe_tag else ''}.img"

    if cached.exists():
        return Response(
            cached.read_bytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    async with JellyfinClient(*db.jellyfin_connection()) as client:
        result = await client.image_bytes(safe_id, kind, w)
        # Ne každá položka má každý druh obrázku. Backdrop bývá u seriálu,
        # ne u dílu; Logo často chybí úplně. Plakát má skoro všechno, tak
        # ho vezmeme jako náhradu - lepší než prázdné šedivé místo.
        if result is None and kind != "Primary":
            result = await client.image_bytes(safe_id, "Primary", w)

    if result is None:
        raise HTTPException(status_code=404, detail="Obrazek neni k dispozici.")

    content, media_type = result
    cached.write_bytes(content)
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/users", response_class=HTMLResponse)
def users(request: Request, days: Optional[int] = None,
          od: Optional[str] = None, do: Optional[str] = None,
          account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, od, do)
    return templates.TemplateResponse(request, "users.html", _context(
        request, account,
        days=days,
        rows=stats.user_table(days),
    ))


@app.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    request: Request, user_id: str, days: Optional[int] = None,
    od: Optional[str] = None, do: Optional[str] = None,
    account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days, od, do)
    detail = stats.user_detail(user_id, days)
    if not detail:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen")

    return templates.TemplateResponse(request, "user_detail.html", _context(
        request, account, days=days, **detail
    ))


@app.get("/dashboard", response_class=HTMLResponse)
def vlastni_prehled(request: Request, days: Optional[int] = None,
                    od: Optional[str] = None, do: Optional[str] = None,
                    account: dict[str, Any] = Depends(require_login)):
    """Přehled poskládaný z existujících sekcí.

    Vypnutý je jako by nebyl: záložka není v menu a adresa vede zpátky
    na Přehled. Zapíná se v Nastavení -> Rozhraní.
    """
    if not sekce.je_zapnuty():
        return RedirectResponse("/", status_code=303)

    rozvrzeni = sekce.nacti_rozvrzeni()
    obdobi = _days(request, days, od, do)
    # Spočítá se JEN to, co je poskládané. Přehled naproti tomu počítá
    # všech deset sekcí každému bez ohledu na to, na co se dívá.
    data = sekce.data_pro(rozvrzeni, obdobi)

    return templates.TemplateResponse(request, "vlastni_prehled.html", _context(
        request, account,
        days=obdobi,
        rozvrzeni=rozvrzeni,
        # Do okna jde CELÝ registr; co už v přehledu je, se jen zašedne.
        # Kdyby se použité sekce vynechávaly, seznam by při přidávání
        # a odebírání poskakoval a člověk by ztrácel místo, kde byl.
        vsechny_sekce=sekce.SEZNAM,
        pouzite={s.klic for s in rozvrzeni},
        sirky=sekce.SIRKY,
        # Přepínač období má smysl jen tehdy, když ho aspoň jedna sekce
        # používá - jinak by tam stál a nic nedělal.
        potrebuje_obdobi=any(s.obdobi for s in rozvrzeni),
        # V ukázce tlačítko zůstává vidět schválně - stejně jako všude
        # jinde. Akci zastaví až `_demo_blokuje` a řekne to nahlas.
        muze_upravovat=bool(account.get("is_admin")),
        **data,
    ))


@app.post("/dashboard/layout")
def vlastni_prehled_uloz(request: Request, poradi: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Uloží poskládané pořadí sekcí.

    Chodí sem "klíč:šířka" oddělené čárkami - přesně to, co drží skryté
    pole formuláře, které při přeskládání aktualizuje JavaScript.
    """
    ulozene = sekce.uloz_rozvrzeni(poradi or "")
    _flash(request, "Uloženo." if ulozene else "Přehled je teď prázdný.",
           "success" if ulozene else "info")
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, days: Optional[int] = None,
                  od: Optional[str] = None, do: Optional[str] = None,
                  account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, od, do)
    return templates.TemplateResponse(request, "insights.html", _context(
        request, account,
        days=days,
        dead=insights.dead_storage(days=days),
        transcodes=insights.transcode_offenders(days),
        reasons=insights.transcode_reasons(days),
        upgrades=insights.upgrade_candidates(days),
        oversized=insights.oversized_rarely_watched(days),
        efficiency=insights.storage_efficiency(days),
        duplicates=insights.duplicate_candidates(),
        abandoned=insights.never_finished(days),
    ))


# Období, která si člověk v historii vybírá. Klíč jde do adresy, takže
# odkaz na "posledních 7 dní" platí i zítra - narozdíl od pevných datumů.
OBDOBI = {
    "vse": ("vše", None),
    "dnes": ("dnes", 0),
    "7": ("posledních 7 dní", 7),
    "30": ("posledních 30 dní", 30),
    "90": ("posledních 90 dní", 90),
    "365": ("poslední rok", 365),
    "vlastni": ("vlastní…", -1),
}


def _datum_z_textu(text: Optional[str]) -> Optional[str]:
    """Datum psané po česku (1.8.2026) na tvar pro databázi.

    Bereme i tvar RRRR-MM-DD - tak chodí proklik z tabulky na Přehledu
    a tak si ho někdo může uložit do záložek.
    """
    text = (text or "").strip()
    if not text:
        return None
    # "2026-08-20 21:30" - takhle chodi ulozeny vyber z grafu.
    presny = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", text)
    if presny:
        return f"{presny.group(1)} {presny.group(2)}"
    if _VALID_DAY.match(text):
        return text
    shoda = re.match(r"^(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{4})$", text)
    if not shoda:
        return None
    den, mesic, rok = (int(c) for c in shoda.groups())
    try:
        return date(rok, mesic, den).isoformat()
    except ValueError:
        # Třicátý únor. Radši nic než datum, které tiše ukáže prázdno.
        return None




@app.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    page: int = 1,
    search: Optional[str] = None,
    user_id: Optional[str] = None,
    day: Optional[str] = None,
    kind: Optional[str] = None,
    obdobi: Optional[str] = None,
    od: Optional[str] = None,
    do: Optional[str] = None,
    method: Optional[str] = None,
    client: Optional[str] = None,
    language: Optional[str] = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Historie přehrávání s filtrem, který se dá skládat.

    Všechny volby jsou v adrese, ne v session: filtr v historii je
    jednorázová otázka („kdo se díval na filmy minulý týden"), ne
    nastavení, které má přežít přechod jinam. Díky tomu se dá výsledek
    poslat odkazem.
    """
    page = max(1, page)
    per_page = 50

    # Den přijde proklikem z tabulky na Přehledu; do SQL ho pustíme jen
    # ve tvaru RRRR-MM-DD. Jde tam jako parametr, takže i tak by byl
    # bezpečný - tohle je navíc proti překlepům, které by tiše vrátily
    # prázdný seznam.
    den = day if day and _VALID_DAY.match(day) else None

    obdobi = obdobi if obdobi in OBDOBI else ("vlastni" if (od or do) else "vse")
    if obdobi == "vlastni":
        od_iso, do_iso = _datum_z_textu(od), _datum_z_textu(do)
    else:
        dni = OBDOBI[obdobi][1]
        od_iso = ((date.today() - timedelta(days=dni)).isoformat()
                  if dni is not None and dni >= 0 else None)
        do_iso = None

    # Typ se sem posílá z prokliku v tabulce, ne ze session - filtr
    # v historii nemá přepisovat volbu na Přehledu.
    kind = kind if kind in stats.ALLOWED_KINDS else stats.KIND_BOTH

    # Způsob přehrání porovnáváme proti pevnému seznamu; přehrávače
    # a jazyky proti tomu, co v historii doopravdy je. Nabídka se staví
    # z dat, takže v ní nikdy není volba, která by nic nenašla.
    nabidka = stats.hodnoty_filtru()
    method = method if method in stats.ZPUSOBY else None
    client = client if client in nabidka["klienti"] else None
    language = language if (language == "und"
                            or language in nabidka["jazyky"]) else None

    filtr = {
        "user_id": user_id or None,
        "search": (search or "").strip() or None,
        "day": den,
        "kind": kind,
        "od": od_iso,
        "do": do_iso,
        "method": method,
        "client": client,
        "language": language,
    }

    total = stats.history_count(**filtr)
    stranek = max(1, -(-total // per_page))
    page = min(page, stranek)

    return templates.TemplateResponse(request, "history.html", _context(
        request, account,
        rows=stats.history(limit=per_page, offset=(page - 1) * per_page, **filtr),
        users=db.query_all("SELECT id, name FROM users ORDER BY name"),
        nabidka=nabidka,
        total=total,
        page=page,
        pages=stranek,
        obdobi=obdobi,
        obdobi_volby=[(klic, i18n.translate(popis))
                      for klic, (popis, _dni) in OBDOBI.items()],
        # Do formuláře se datum vypisuje po česku, do adresy jde ISO.
        od_text=_cesky_datum(od_iso),
        do_text=_cesky_datum(do_iso),
        **filtr,
        filtr_aktivni=(any(hodnota for klic, hodnota in filtr.items()
                           if klic != "kind")
                       or kind != stats.KIND_BOTH or obdobi != "vse"),
        # Do čipů nad tabulkou: kolik voleb je zapnutých a jak se jmenují.
        # Číslo na tlačítku je poznat na první pohled, seznam id ne.
        # Číslo na tlačítku musí sedět s počtem čipů pod ním. Datumy se
        # proto nepočítají: jsou to jen jiná podoba volby "období", a
        # kdyby se počítaly zvlášť, tlačítko by hlásilo o jedna víc,
        # než je vidět.
        pocet_filtru=(sum(1 for klic, hodnota in filtr.items()
                          if hodnota and klic not in ("kind", "od", "do", "day"))
                      + (1 if kind != stats.KIND_BOTH else 0)
                      + (1 if den or obdobi != "vse" else 0)),
        jmeno_uzivatele=(next((r["name"] for r in db.query_all(
            "SELECT id, name FROM users WHERE id = ?", (user_id,))), user_id)
            if user_id else ""),
        obdobi_popis=i18n.translate(OBDOBI[obdobi][0]),
    ))


# ---------------------------------------------------------------------------
# Nastaveni
# ---------------------------------------------------------------------------

























# ---------------------------------------------------------------------------
# Pripojeni k Jellyfinu, jazyk rozhrani, restart
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Upozorneni
# ---------------------------------------------------------------------------






















# ---------------------------------------------------------------------------
# Naplanovane ulohy
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# Import historie
# ---------------------------------------------------------------------------















# ---------------------------------------------------------------------------
# Sprava uctu
#
# Vsimni si, ze kazda akce ma vlastni adresu a posila se metodou POST.
# Mazani pres GET (treba odkazem /smazat?id=3) je klasicka chyba: takovou
# adresu si prohlizec muze nacist sam, treba pri predbeznem nacitani odkazu,
# a ucet zmizi bez toho, aby na neco nekdo klikl.
# ---------------------------------------------------------------------------

















# ---------------------------------------------------------------------------
# Jazyky
# ---------------------------------------------------------------------------

@app.get("/languages/bez-jazyka", response_class=HTMLResponse)
def languages_undefined(
    request: Request,
    search: Optional[str] = None,
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


@app.post("/languages/preferred")
def languages_preferred(
    request: Request,
    code: str = Form(""),
    days: Optional[int] = Form(None),
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


@app.get("/network", response_class=HTMLResponse)
def network_page(request: Request, days: Optional[int] = None,
                 od: Optional[str] = None, do: Optional[str] = None,
                 account: dict[str, Any] = Depends(require_login)):
    """Sit - kolik dat teklo ze serveru k prehravacum.

    Vsechno se pocita z `playback.bitrate`, ktery sberac uklada u kazdeho
    prehravani. Zadny novy sber to nepotrebuje - jen jina otazka nad
    daty, ktera uz mame.
    """
    days = _days(request, days, od, do)
    return templates.TemplateResponse(request, "network.html", _context(
        request, account,
        days=days,
        prehled=stats.bandwidth_prehled(days),
        # Zive: co tece prave ted a posledni hodina po minutach. Nezalezi
        # na vybranem obdobi - "prave ted" je porad ted.
        ted=stats.tok_ted(),
        zive=stats.bandwidth_zive(days),
        denni_spicky=stats.bandwidth_denni_spicky(days),
        podle_uzivatele=stats.bandwidth_podle(days, "user_name"),
        podle_klienta=stats.bandwidth_podle(days, "client"),
        odkud=stats.odkud_se_divaji(days),
        hotspoty=stats.hotspoty(days),
        zeme=stats.zeme_divaku(days),
        geo={
            "knihovna": geoip.knihovna_je(),
            "databaze": geoip.je_k_dispozici(),
            "bajtu": geoip.velikost_databaze(),
            "stari": geoip.stari_databaze(),
            # Cesta k pythonu, kterym aplikace bezi - at jde prikaz
            # zkopirovat a nekonci u obecneho "pip install".
            "python": sys.executable,
        },
    ))


@app.post("/network/geoip")
async def network_geoip(request: Request,
                        account: dict[str, Any] = Depends(require_admin)):
    """Stahne (nebo obnovi) databazi GeoLite2 pro mapu.

    Jedina akce v aplikaci, ktera sahne jinam nez na Jellyfin - a jen na
    kliknuti. Proto ji smi spustit spravce, ne kazdy prihlaseny.
    """
    if not geoip.knihovna_je():
        _flash(request, "Chybí knihovna maxminddb – bez ní se databáze nepřečte.",
               "error")
        return RedirectResponse("/network#mapa", status_code=303)

    vysledek = await geoip.stahni()
    if vysledek.get("status") == "ok":
        _flash(request, "Databáze GeoLite2 stažena ({velikost}).", "success",
               velikost=formatting.bytes_human(vysledek["bajtu"]))
    else:
        _flash(request, "Stažení se nepovedlo: {duvod}", "error",
               duvod=vysledek.get("message", "?"))
    return RedirectResponse("/network#mapa", status_code=303)


@app.get("/languages", response_class=HTMLResponse)
def languages_page(
    request: Request, days: Optional[int] = None,
    od: Optional[str] = None, do: Optional[str] = None,
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


# ---------------------------------------------------------------------------
# Male JSON rozhrani (hodi se na kontrolu, ze aplikace zije)
# ---------------------------------------------------------------------------



@app.get("/health")
def health(request: Request):
    """Stav aplikace. Stránka se sem vrací každých deset vteřin.

    Bez přihlášení odpoví jen "žiju" a kdy nastartovala - na to se ptá
    monitoring (a čekání po restartu, kdy přihlášení ještě neplatí).
    Zbytek, tedy co se právě hraje a jak velká je knihovna, je údaj
    o obsahu serveru a patří až za přihlášení.
    """
    zaklad = {"status": "ok", "started_at": int(STARTED_AT)}
    if current_account(request) is None:
        return zaklad

    otisk = scanner.otisky()
    return {
        **zaklad,
        # Verze beziciho procesu. Cekarna po aktualizaci na ni ceka: je to
        # primo ta otazka, na kterou se pta ("bezi uz nova verze?"), kdezto
        # `started_at` odpovida jen "neco se restartovalo". Neprihlasenemu
        # ji nerikame - monitoringu staci, ze aplikace zije.
        "version": __version__,
        "collector": db.get_setting(collector.STATUS_KEY, "unknown"),
        "last_poll": db.get_setting(collector.LAST_POLL_KEY, ""),
        "active_sessions": stats.active_session_count(),
        "task_running": scanner.is_scan_running(),
        "stop_pending": scanner.stop_requested(),
        "progress": scanner.progress(),
        # Otisk knihovny (Přehled si ho hlídá, aby po doběhnutí
        # synchronizace sám ukázal nově přidané tituly) a otisk úloh
        # (podle jeho změny se pozná, že úloha skončila - i když celá
        # proběhla mezi dvěma dotazy). Obojí jedním dotazem, protože
        # sem se prohlížeč vrací každých deset vteřin.
        "library_version": otisk["library"],
        "tasks_version": otisk["tasks"],
    }


@app.get("/api/now-playing")
def api_now_playing(account: dict[str, Any] = Depends(require_login)):
    return {"sessions": stats.active_sessions()}


# Routy Nastaveni bydli zvlast - je jich pres tricet a s ostatnimi
# strankami nemaji nic spolecneho. Import je az tady dole schvalne:
# modul si bere `web_zaklad`, ne tenhle soubor, takze nic nevznika
# dokola.
from . import web_nastaveni  # noqa: E402

app.include_router(web_nastaveni.router)
