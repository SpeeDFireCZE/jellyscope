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
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import (accounts, applog, charts, collector, db, dbmigrate, dialect, formatting,
               i18n, importers, insights, langstats, languages, scanner, stats,
               tasks)
# PROJECT_DIR je kořen projektu (tam, kde je run.py a složka data),
# PACKAGE_DIR je tenhle balíček. Nejsou totéž a plete se to snadno -
# proto mají různá jména místo jednoho BASE_DIR.
from .config import BASE_DIR as PROJECT_DIR
from .config import load_config
from .i18n import translate as _t
from .jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError

log = logging.getLogger("jellyscope.web")

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
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


config = load_config()

app = FastAPI(title="Jellyscope", lifespan=lifespan, docs_url=None, redoc_url=None)
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
    return response


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
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


# ---------------------------------------------------------------------------
# Prihlaseni
#
# Aplikace je cela za prihlasenim - bez uctu se nedostanes nikam krome
# prihlasovaci stranky. Ucty jsou v tabulce `accounts` a spravuji se
# v Nastaveni; s uzivateli Jellyfinu nemaji nic spolecneho.
# ---------------------------------------------------------------------------

def current_account(request: Request) -> Optional[dict[str, Any]]:
    """Kdo je prihlaseny. Vrati None, kdyz nikdo.

    V cookie mame jen ID uctu (a to podepsane, takze ho nejde podvrhnout).
    Vsechno ostatni si radeji nacteme z databaze - kdyz uctu mezitim nekdo
    odebral prava, projevi se to hned, ne az po odhlaseni.
    """
    account_id = request.session.get("account_id")
    if not account_id:
        return None

    account = accounts.get(int(account_id))
    if account is None:
        # Ucet byl mezitim smazan - cookie zahodime.
        request.session.clear()
    return account


def require_login(request: Request) -> dict[str, Any]:
    """Zavora pred kazdou strankou. Vraci prihlaseny ucet."""
    if not accounts.any_exists():
        # Uplne prvni spusteni - jeste neexistuje zadny ucet.
        raise HTTPException(status_code=307, headers={"Location": "/setup"})

    account = current_account(request)
    if account is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return account


def require_admin(request: Request) -> dict[str, Any]:
    """Zavora pro veci, ktere smi jen spravce."""
    account = require_login(request)
    if not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Tuhle akci smi jen spravce.")
    return account


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
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


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
    return RedirectResponse("/", status_code=303)


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

    Za reverzní proxy vidí aplikace vždycky adresu té proxy, takže by
    brzda platila pro všechny dohromady. Proto se kouká i na hlavičku
    `X-Forwarded-For` - ale **jen když je proxy nastavená** (viz
    FORWARDED_ALLOW_IPS v .env). Bez toho by si tu hlavičku poslal
    útočník sám a brzdě by pokaždé ukázal jinou adresu.
    """
    if config.forwarded_allow_ips:
        hlavicka = request.headers.get("x-forwarded-for", "")
        prvni = hlavicka.split(",")[0].strip()
        if prvni:
            return prvni[:64]
    return request.client.host if request.client else "?"



def _days(request: Request, value: Optional[int]) -> int:
    """Zvolene obdobi - spolecne pro vsechny stranky.

    Kdyz je v adrese platna hodnota, pouzijeme ji a zapamatujeme si ji.
    Kdyz v adrese nic neni, vezmeme naposledy zvolene. Diky tomu prepnuti
    na "rok" na Prehledu plati i po prechodu na Jazyky - okno je synchronni.

    Hodnota se porovnava proti pevnemu seznamu, takze se do SQL nikdy
    nedostane cislo primo z adresy.
    """
    if value in ALLOWED_DAYS:
        request.session[DAYS_SESSION_KEY] = int(value)
        return int(value)

    remembered = request.session.get(DAYS_SESSION_KEY)
    if remembered in ALLOWED_DAYS:
        return int(remembered)

    return DEFAULT_DAYS


# Rozepsané nastavení databáze.
#
# Tlačítka "Otestovat spojení" a "Přenést data" nic neukládají - po nich se
# stránka načte znovu a formulář by se vrátil k tomu, co je uložené. Vyplníš
# tedy server, port, uživatele i heslo, otestuješ spojení, ono řekne "funguje"
# - a formulář je zase prázdný a přepnutý na SQLite. Proto si rozepsané
# hodnoty na chvíli podržíme.
#
# Proč v paměti a ne v session: session je u nás **podepsaná cookie**, tedy
# něco, co si prohlížeč nese s sebou a co jde přečíst. Heslo k databázi tam
# nepatří. Tady zůstane v paměti procesu a po restartu zmizí.
_DB_DRAFT: dict[tuple[int, str], tuple[float, Any]] = {}
_DRAFT_TTL_SECONDS = 900.0   # čtvrt hodiny


def _draft_save(account: dict[str, Any], name: str, value: Any) -> None:
    _DB_DRAFT[(int(account["id"]), name)] = (time.monotonic(), value)


def _draft_read(account: dict[str, Any], name: str) -> Any:
    """Rozepsané nastavení, pokud ještě nevypršelo."""
    klic = (int(account["id"]), name)
    zaznam = _DB_DRAFT.get(klic)
    if zaznam is None:
        return None
    ulozeno_v, value = zaznam
    if time.monotonic() - ulozeno_v > _DRAFT_TTL_SECONDS:
        _DB_DRAFT.pop(klic, None)
        return None
    return value


def _draft_clear(account: dict[str, Any], name: str) -> None:
    _DB_DRAFT.pop((int(account["id"]), name), None)


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


def _linked_note(result: dict[str, Any]) -> str:
    """Věta o dohledaných položkách - do hlášky po importu.

    Import neposílá tmdb ID, takže se položky dohledávají až po něm.
    Když se něco spárovalo, uživatel to má vědět: jinak by nechápal,
    proč se čísla v knihovně po importu změnila.
    """
    linked = result.get("linked") or {}
    if not linked.get("rows"):
        return ""
    casti = []
    if linked.get("by_tmdb"):
        casti.append(i18n.translate("{n} podle tmdb ID").format(n=linked["by_tmdb"]))
    if linked.get("by_episode"):
        casti.append(i18n.translate("{n} podle čísla dílu")
                     .format(n=linked["by_episode"]))
    if linked.get("by_name"):
        casti.append(i18n.translate("{n} podle názvu").format(n=linked["by_name"]))
    return " " + i18n.translate("Dohledáno {co} ({n} záznamů).").format(
        co=", ".join(casti), n=linked["rows"])


def _known_note(result: dict[str, Any]) -> str:
    """Věta o záznamech, které už byly v databázi z jiného zdroje.

    Bez ní by import z druhého nástroje vypadal, že "skoro nic nenašel".
    Přitom nenašel nic **nového** - a to je dobře, právě proto se nic
    nezdvojilo.
    """
    pocet = result.get("known_elsewhere") or 0
    if not pocet:
        return ""
    return " " + i18n.translate(
        "{n} záznamů už v databázi bylo z jiného zdroje (z collectoru nebo "
        "z druhého importu), takže se nezdvojily.").format(n=pocet)


def _flash(request: Request, message: str, level: str = "info",
           **hodnoty: Any) -> None:
    """Ulozi hlasku, ktera se ukaze na nasledujici strance.

    Preklad se dela uz **tady**, ne az v sablone. Duvod: hlaska se uklada do
    session a zobrazi se az na dalsi strance - kdyby si mezitim nekdo prepnul
    jazyk, ukazala by se v tom starem. Prelozit ji v okamziku, kdy vznikla,
    je jednoznacne.

    Hlasky s cislem nebo jmenem se predavaji jako **sablona a hodnoty
    zvlast**:

        _flash(request, "Naimportováno {n} záznamů.", "success", n=42)

    Nejdriv se prelozi sablona, teprve pak se do ni hodnoty dosadi. Obracene
    to nejde: hotova veta s cislem uvnitr v prekladovem slovniku neni
    a nikdy nebude - a prave takhle drive vsechny hlasky po importu
    a po dobehnuti ulohy zustavaly cesky, i kdyz mel clovek anglicke
    rozhrani.

    Kdyz se dosazeni nepovede (v sablone je jina znacka, nez jake prijdou
    hodnoty), radeji ukazeme neprelozenou vetu nez padneme - hlaska
    o vysledku ulohy nesmi shodit stranku.
    """
    text = i18n.translate(message)
    if hodnoty:
        try:
            text = text.format(**hodnoty)
        except (KeyError, IndexError, ValueError):
            # Zachranny scenar musi byt uplne hloupy: vratime syrovou
            # sablonu. Kdybychom tu zkusili dosadit znovu, spadli bychom
            # na tomtez - a shodili stranku kvuli hlasce o vysledku.
            log.warning("hlasku %r se nepodarilo doplnit hodnotami %r",
                        message, hodnoty)
            text = message
    request.session["flash"] = {"message": text, "level": level}


def _context(request: Request, account: Optional[dict[str, Any]] = None,
             **extra: Any) -> dict[str, Any]:
    """Spolecna data pro kazdou stranku (stav sberace, hlasky, kdo je prihlaseny)."""
    base = {
        "collector_status": db.get_setting(collector.STATUS_KEY, "unknown"),
        "collector_error": db.get_setting(collector.ERROR_KEY, ""),
        "last_poll": db.get_setting(collector.LAST_POLL_KEY, ""),
        "flash": request.session.pop("flash", None),
        "active_count": stats.active_session_count(),
        "account": account,
        "ui_language": i18n.current_language(),
        # `?wait=restart` / `?wait=task` v adrese říká stránce, ať si počká
        # a obnoví se sama, až bude na co. Nastavuje to routa přesměrováním
        # (viz /settings/restart a /settings/stop); pevný seznam hodnot,
        # aby se z adresy nedalo do stránky propašovat nic jiného.
        "wait_for": (request.query_params.get("wait")
                     if request.query_params.get("wait") in ("restart", "task")
                     else None),
        # Kolik úloh doběhlo v okamžiku vykreslení. Stránka to porovnává
        # s /health a podle změny pozná, že mezitím nějaká skončila.
        #
        # Musí to být v HTML, ne až z prvního dotazu na /health: úloha,
        # která skončí mezi vykreslením stránky a prvním dotazem, by jinak
        # propadla a nic by se neobnovilo.
        "tasks_version": scanner.tasks_version(),
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Stranky
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, days: Optional[int] = None, kind: Optional[str] = None,
              top_kind: Optional[str] = None,
              account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days)
    kind = _kind(request, kind)
    top_kind = _kind(request, top_kind, TOP_KIND_SESSION_KEY)
    current = stats.overview(days)
    previous = stats.previous_overview(days)

    def change(now: Any, before: Any) -> Optional[float]:
        """Zmena v procentech oproti minulemu obdobi."""
        try:
            now_value, before_value = float(now or 0), float(before or 0)
        except (TypeError, ValueError):
            return None
        if before_value <= 0:
            return None
        return (now_value - before_value) / before_value * 100

    daily = stats.daily_activity_split(days)

    return templates.TemplateResponse(request, "dashboard.html", _context(
        request, account,
        days=days,
        kind=kind,
        overview=current,
        deltas={
            "watched": change(current.get("watched_seconds"), previous.get("watched_seconds")),
            "plays": change(current.get("plays"), previous.get("plays")),
        },
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
        top_items=stats.top_items(_days(request, days),
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
    days = _days(request, days)
    kind = _kind(request, kind)
    return templates.TemplateResponse(request, "_daily_card.html", _context(
        request, account,
        days=days,
        kind=kind,
        daily=stats.daily_activity_split(days),
        ostatni=stats.rozpad_ostatnich(days),
    ))


@app.get("/library", response_class=HTMLResponse)
def library_index(request: Request, account: dict[str, Any] = Depends(require_login)):
    """Rozcestnik: dlazdice jednotlivych knihoven z Jellyfinu."""
    return templates.TemplateResponse(request, "library_index.html", _context(
        request, account,
        libraries=stats.library_cards(),
        coverage=stats.tech_coverage(),
        codecs=stats.codec_breakdown(),
        resolutions=stats.resolution_breakdown(),
        ranges=stats.video_range_breakdown(),
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
        streams=stats.item_streams(item_id),
        playback=stats.item_playback(item_id),
        summary=stats.item_playback_summary(item_id),
        siblings=stats.sibling_episodes(item_row),
        lang=languages,
    ))


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

    cache_dir = config.database_path.parent / "imagecache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{safe_id}-{kind}-{w}.img"

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
def users(request: Request, days: Optional[int] = None, account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days)
    return templates.TemplateResponse(request, "users.html", _context(
        request, account,
        days=days,
        rows=stats.user_table(days),
    ))


@app.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    request: Request, user_id: str, days: Optional[int] = None, account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days)
    detail = stats.user_detail(user_id, days)
    if not detail:
        raise HTTPException(status_code=404, detail="Uzivatel nenalezen")

    return templates.TemplateResponse(request, "user_detail.html", _context(
        request, account, days=days, **detail
    ))


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, days: Optional[int] = None, account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days)
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


@app.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    page: int = 1,
    search: Optional[str] = None,
    user_id: Optional[str] = None,
    day: Optional[str] = None,
    kind: Optional[str] = None,
    account: dict[str, Any] = Depends(require_login),
):
    page = max(1, page)
    per_page = 50

    # Den bereme z adresy, ale do SQL pustime jen tvar RRRR-MM-DD.
    # Retezec jde do dotazu jako parametr, takze i tak by byl bezpecny -
    # tohle je navic proti preklepum, ktere by tise vratily prazdny seznam.
    if day and not _VALID_DAY.match(day):
        day = None

    # Typ se sem posila z prokliku v tabulce, ne ze session - filtr
    # v historii je jednorazovy a nema prepisovat volbu na Prehledu.
    kind = kind if kind in stats.ALLOWED_KINDS else stats.KIND_BOTH

    total = stats.history_count(user_id, search, day, kind)

    return templates.TemplateResponse(request, "history.html", _context(
        request, account,
        rows=stats.history(per_page, (page - 1) * per_page, user_id, search, day, kind),
        total=total,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        search=search or "",
        user_id=user_id or "",
        day=day or "",
        kind=kind,
        users=db.query_all("SELECT id, name FROM users ORDER BY name"),
    ))


# ---------------------------------------------------------------------------
# Nastaveni
# ---------------------------------------------------------------------------

# Nastaveni je rozdelene na sekce. Drive to byla jedna dlouha stranka,
# na ktere se nedalo nic najit - a navic se pri kazdem otevreni pocitalo
# vsechno naraz, vcetne poctu radku v databazi a vypisu zaloh z disku.
#
# Ted se nacita jen to, co patri k otevrene sekci.
SETTINGS_SECTIONS = [
    ("jellyfin", "Jellyfin", True),
    ("data", "Sběr dat", True),
    ("tasks", "Úlohy a zálohy", True),
    ("import", "Import historie", True),
    ("database", "Databáze", True),
    ("accounts", "Účty", False),      # False = vidí i čtenář
    ("blocks", "Blokace", True),
    ("log", "Log", True),
    ("general", "Obecné", True),
]


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    section: str = "jellyfin",
    # Volby prohlížeče logu. Jsou v adrese, ne v nastavení: je to pohled,
    # ne volba serveru - dva správci se můžou dívat každý na něco jiného.
    log_file: str = "",
    log_lines: int = 0,
    log_level: str = "",
    account: dict[str, Any] = Depends(require_login),
):
    allowed = {key for key, _name, admin_only in SETTINGS_SECTIONS
               if account["is_admin"] or not admin_only}
    if section not in allowed:
        # Čtenář má jen jednu sekci - a je to ta, kde si mění heslo.
        section = "jellyfin" if account["is_admin"] else "accounts"

    # Šabloně dáváme nastavení BEZ tajemství - API klíč se do stránky
    # nesmí dostat ani nedopatřením.
    context: dict[str, Any] = {"section": section, "settings": db.get_public_settings()}

    if section == "jellyfin":
        # Když uživatel jen testoval spojení, nastavení se neuložilo -
        # do formuláře ale patří to, co vyplnil, ne uložená hodnota.
        # Jinak by po každém testu vyplňoval adresu znovu.
        rozepsane_jf = _draft_read(account, "jellyfin") or {}
        ulozeny_klic = db.get_setting("jellyfin_api_key", "").strip()
        context.update(
            jellyfin_url=rozepsane_jf.get("url") or db.get_setting("jellyfin_url", ""),
            has_api_key=bool(rozepsane_jf.get("api_key") or ulozeny_klic),
            jellyfin_draft=bool(rozepsane_jf),
            last_library_scan=scanner.last_scan("library"),
            scan_running=scanner.is_scan_running(),
            stop_pending=scanner.stop_requested(),
        )
    elif section == "data":
        from . import probe  # az tady, at start aplikace nic nezdrzuje
        # Karta s analýzou souborů se přestěhovala mezi Úlohy - tahle
        # sekce už jen vybírá zdroj dat, takže nepotřebuje ani pokrytí,
        # ani stav posledního běhu.
        context.update(
            ffprobe_found=probe.find_ffprobe(db.get_setting("ffprobe_path")),
        )
    elif section == "tasks":
        context.update(
            # Když úloha doběhne, zatímco je člověk na téhle sekci, načte
            # se stránka sama - jinak by tu proužek "úloha běží" zůstal
            # viset a výsledek by se neobjevil. Viz hlídač v base.html.
            reload_on_task=True,
            task_list=tasks.all_statuses(),
            backups=tasks.list_backups(),
            backup_free=tasks.free_space(db.get_setting("backup_path", "")),
            # Co je na stroji za pg_dump a co za server. Bez toho se
            # nesoulad verzí hledá jen podle chybové hlášky po tom, co
            # záloha selže - viz tasks._vyber_pg_dump().
            pg_dumps=(tasks.dostupne_pg_dumpy() if db.database_config().is_postgres
                      else []),
            pg_server=(tasks.server_version() if db.database_config().is_postgres
                       else 0),
            scan_running=scanner.is_scan_running(),
            stop_pending=scanner.stop_requested(),
            last_library_scan=scanner.last_scan("library"),
            last_tech_scan=scanner.last_scan("tech"),
            coverage=stats.tech_coverage(),
        )
    elif section == "blocks":
        context.update(
            blocks=accounts.seznam_blokaci(),
            block_levels=accounts.STUPNE_BLOKACE,
            block_attempts=accounts.POKUSU_DO_BLOKACE,
            block_forget_hours=accounts.ZAPOMENUT_PO_HODINACH,
        )
    elif section == "import":
        context.update(
            import_stats=importers.import_summary(),
            last_import=scanner.last_scan("import"),
            # Kolik je v historii duplicitních a špatně přiřazených záznamů.
            # Ukazuje se předem, ať je vidět, jestli má úklid vůbec smysl.
            duplicate_rows=importers.duplicate_playback_count(),
            misplaced_rows=len(importers.misplaced_episode_rows()),
            orphan_rows=importers.orphan_playback_count(),
            orphan_items=importers.orphan_items_count(),
            stale_name_rows=importers.stale_name_rows(),
            import_duplicate_rows=importers.import_duplicate_count(),
        )
    elif section == "database":
        # Dvě různé konfigurace, a plete se to snadno:
        #
        #   ulozena  = co je v data/database.json, tedy co se použije
        #              po restartu. Tohle patří do formuláře.
        #   bezici   = co aplikace používá teď. Změna databáze se projeví
        #              až restartem, takže do restartu se tyhle dvě liší.
        #
        # Dřív se do formuláře dávala běžící konfigurace - a protože ta
        # je v paměti zakešovaná, po uložení PostgreSQL se formulář
        # přepnul zpátky na SQLite. Vypadalo to, že se uložení nepovedlo,
        # i když soubor byl zapsaný správně.
        ulozena = dialect.load_config(PROJECT_DIR, str(config.database_path))
        bezici = db.database_config()
        # Když má uživatel něco rozepsaného (otestoval spojení, ale ještě
        # neuložil), ukážeme ve formuláři to - jinak by o vyplněné údaje
        # při každém testu přišel.
        rozepsane = _draft_read(account, "database")
        context.update(
            database=rozepsane or ulozena,
            draft_pending=rozepsane is not None,
            running_database=bezici,
            restart_pending=ulozena.to_dict() != bezici.to_dict(),
            database_counts=dbmigrate.summarise(bezici),
            # Cesta k pythonu, kterým aplikace zrovna běží. Návod na
            # doinstalování psycopg tak ukáže příkaz, který jde
            # zkopírovat - ne obecné "pip install", které by v případě
            # virtuálního prostředí instalovalo někam jinam.
            python_path=sys.executable,
            psycopg_available=db.psycopg_available(),
            pool_available=db.pool_available(),
        )
    elif section == "accounts":
        context.update(
            all_accounts=accounts.all_accounts(),
            # Kolik je správců - podle toho se u posledního z nich
            # neukáže tlačítko Smazat. Viz accounts.delete().
            admin_count=accounts.admin_count(),
        )
    elif section == "log":
        # Vstup z adresy nikdy nedůvěřuj - ani vlastnímu odkazu. Jméno
        # souboru si ověří applog sám (viz _bezpecna_cesta), tady stačí
        # počet řádků a úroveň.
        radku = log_lines if log_lines > 0 else applog.DEFAULT_LINES
        uroven = log_level if log_level in applog.levels() else ""
        context.update(
            log=applog.read_lines(log_file, radku, uroven),
            log_lines=radku,
            log_level=uroven,
            log_levels=applog.levels(),
        )

    return templates.TemplateResponse(
        request, "settings.html", _context(request, account, **context)
    )


@app.post("/settings")
def settings_save(
    request: Request,
    tech_source: str = Form("jellyfin"),
    poll_interval: str = Form("10"),
    ffprobe_path: str = Form(""),
    ffprobe_concurrency: str = Form("3"),
    path_mappings: str = Form("[]"),
    account: dict[str, Any] = Depends(require_admin),
):
    # Vstup z formulare se nikdy neuklada bez kontroly. Uzivatel muze
    # (omylem i schvalne) poslat cokoliv.
    if tech_source not in ("jellyfin", "ffprobe"):
        tech_source = "jellyfin"

    db.set_setting("tech_source", tech_source)
    db.set_setting("poll_interval", _clamp(poll_interval, 2, 300, 10))
    # Cas synchronizace knihovny se sem uz nepise - patri k naplanovanym ulohám
    # a meni se ve vlastnim formulari. Kdyby ho ukladaly oba, prepsaly by
    # si hodnotu navzajem.
    db.set_setting("ffprobe_concurrency", _clamp(ffprobe_concurrency, 1, 16, 3))
    db.set_setting("ffprobe_path", ffprobe_path.strip())

    import json
    try:
        parsed = json.loads(path_mappings or "[]")
        if not isinstance(parsed, list):
            raise ValueError
        db.set_setting("path_mappings", json.dumps(parsed))
    except ValueError:
        _flash(request, "Přepis cest není platný JSON - nechal jsem původní hodnotu.", "warning")
        return RedirectResponse("/settings?section=data", status_code=303)

    _flash(request, "Nastavení uloženo.", "success")
    return RedirectResponse("/settings?section=data", status_code=303)


# Strop pro nahrany soubor pri importu historie.
#
# Musi sedet s limitem na reverzni proxy - ta odmita drive nez aplikace
# a hlaska pak prijde od nginxu, ne od nas. Priklady konfigurace ve
# slozce deploy/ maji tutez hodnotu; hlida to test_deploy.py.
#
# 200 MB je s rezervou: soubor tehle velikosti je pres milion radku
# historie, coz zadna domacnost nenasbira. A cely se cte do pameti,
# takze vetsi strop by na malem serveru delal vic skody nez uzitku.
MAX_UPLOAD_MB = 200


def _clamp(raw: str, minimum: int, maximum: int, fallback: int) -> str:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = fallback
    return str(max(minimum, min(maximum, value)))


@app.post("/settings/sync")
async def settings_sync(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Spusti synchronizaci knihovny na pozadi.

    Nespoustime ji primo v teto funkci - u velke knihovny by prohlizec
    cekal na odpoved nekolik minut a vypadalo by to jako zamrznuti.
    Ulohu odpalime a hned odpovime.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(scanner.sync_library())
        _flash(request, "Synchronizace knihovny spuštěna.", "info")
        # `wait=task` necha stranku pockat a po dokonceni ji obnovi -
        # jinak clovek koukа na "bezi" a netusi, kdy uz je hotovo.
        return RedirectResponse("/settings?section=jellyfin&wait=task",
                                status_code=303)
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@app.post("/settings/stop")
async def settings_stop(
    request: Request,
    back: str = Form("tasks"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Poprosi bezici ulohu, aby skoncila.

    Uloha se nepreruší uprostred prace - dodela rozdelanou polozku a teprve
    pak skonci. Proto se tady taky nic necekaji: jen se preda pokyn.
    """
    if scanner.request_stop():
        _flash(
            request,
            "Pokyn k zastavení předán. Úloha dokončí rozpracovanou položku "
            "a skončí - stránka se pak obnoví sama.",
            "info",
        )
    else:
        _flash(request, "Žádná úloha zrovna neběží.", "warning")

    sekce = back if back in ("tasks", "jellyfin", "data") else "tasks"
    # `wait=task` rekne strance, at si pocka a obnovi se sama, jakmile
    # uloha doopravdy skonci. Uzivatel tak nemusi hadat, kdy uz muze.
    cekat = "&wait=task" if scanner.stop_requested() else ""
    return RedirectResponse(f"/settings?section={sekce}{cekat}", status_code=303)


@app.post("/settings/scan")
async def settings_scan(
    request: Request,
    mode: str = Form("missing"),
    library_id: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Spusti technickou analyzu souboru pres ffprobe.

    `library_id` spousti tlacitko primo u hlasky na detailu knihovny -
    analyzuje se pak jen ta knihovna a clovek se na ni zase vrati.
    """
    # Kam se po spusteni vratit. Jen id knihovny, ktera opravdu existuje;
    # cizi hodnota z formulare se nesmi dostat do adresy presmerovani.
    # Bez knihovny zpatky mezi Ulohy - tam ta tlacitka jsou.
    zpet = "/settings?section=tasks"
    if library_id and stats.library(library_id):
        zpet = f"/library/{library_id}"
    else:
        library_id = ""

    if db.get_setting("tech_source") != "ffprobe":
        _flash(
            request,
            "Zdroj technických dat je nastavený na Jellyfin. "
            "Přepni ho na ffprobe a ulož nastavení.",
            "warning",
        )
        return RedirectResponse(zpet, status_code=303)

    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(scanner.run_tech_scan(
            only_missing=(mode == "missing"), library_id=library_id or None))
        _flash(request, "Analýza souborů spuštěna.", "info")
        oddelovac = "&" if "?" in zpet else "?"
        return RedirectResponse(f"{zpet}{oddelovac}wait=task", status_code=303)

    return RedirectResponse(zpet, status_code=303)


# ---------------------------------------------------------------------------
# Pripojeni k Jellyfinu, jazyk rozhrani, restart
# ---------------------------------------------------------------------------

@app.post("/settings/connection")
async def settings_connection(
    request: Request,
    jellyfin_url: str = Form(""),
    jellyfin_api_key: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Ulozi nebo otestuje adresu Jellyfinu a API klic.

    Klic se do formulare nikdy nevypisuje - jen se pozna, jestli uz nejaky
    je. Prazdne pole proto znamena "nech ten stavajici", ne "smaz ho".
    Bez toho by staclo omylem ulozit formular a spojeni by prestalo fungovat.

    Testovani je soucasti **tehoz** formulare, ne samostatneho tlacitka
    vedle. Drive bylo zvlast a nic neposilalo, takze testovalo ulozene
    nastaveni misto toho vyplneneho: vyplnil jsi adresu, kliknul na
    "Otestovat spojeni" a dostal chybu o chybejicim http:// - protoze
    se testovala prazdna ulozena hodnota. A vyplnene udaje se pritom
    zahodily, protoze odeslani jednoho formulare zahodi obsah druheho.
    """
    url = jellyfin_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url

    # Prazdny klic znamena "nech stavajici". Pri testu bereme i rozepsany,
    # aby slo otestovat vic pokusu za sebou bez opakovaneho vypisovani.
    rozepsane = _draft_read(account, "jellyfin") or {}
    key = (jellyfin_api_key.strip()
           or rozepsane.get("api_key", "")
           or db.get_setting("jellyfin_api_key", ""))

    if action == "test":
        # Rozepsane si podrzime, at se formular po testu nevyprazdni.
        _draft_save(account, "jellyfin", {"url": url, "api_key": key})

        if not url:
            _flash(request, "Nejdřív vyplň adresu serveru.", "error")
        else:
            try:
                async with JellyfinClient(url, key, QUICK_TIMEOUT) as client:
                    info = await client.system_info()
                _flash(
                    request,
                    "Spojení v pořádku: {server} (Jellyfin {verze})",
                    "success",
                    server=info.get("ServerName", "?"),
                    verze=info.get("Version", "?"),
                )
            except JellyfinError as exc:
                _flash(request, "Spojení selhalo: {duvod}", "error", duvod=str(exc))
        return RedirectResponse("/settings?section=jellyfin", status_code=303)

    db.set_setting("jellyfin_url", url)
    if key:
        db.set_setting("jellyfin_api_key", key)
    _draft_clear(account, "jellyfin")

    _flash(request, "Připojení uloženo.", "success")
    return RedirectResponse("/settings?section=jellyfin", status_code=303)


@app.post("/settings/database")
def settings_database(
    request: Request,
    kind: str = Form("sqlite"),
    sqlite_path: str = Form("data/jellyscope.db"),
    pg_host: str = Form("localhost"),
    pg_port: str = Form("5432"),
    pg_database: str = Form("jellyscope"),
    pg_user: str = Form("jellyscope"),
    pg_password: str = Form(""),
    # Nezaskrtnute policko se v HTML formulari vubec neposila, proto je
    # vychozi hodnota "" = vypnuto. To je zaroven duvod, proc se sem neda
    # dat Form(True) - to by slo zapnout, ale uz nikdy vypnout.
    pg_use_pool: str = Form(""),
    action: str = Form("save"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Nastaveni databaze: ulozit, otestovat, nebo prenest data.

    Tohle jedine nastaveni nemuze byt v databazi - potrebujeme ho, abychom
    se k ni vubec pripojili. Uklada se proto do souboru data/database.json.
    """
    current = db.database_config()
    # Heslo se do formuláře nikdy nevypisuje, takže prázdné pole znamená
    # "nech to, co už znám". Rozepsané nastavení je v tom pořadí první:
    # po otestování spojení chceme uložit přesně to, co se testovalo.
    rozepsane = _draft_read(account, "database")

    candidate = dialect.DatabaseConfig(
        kind=kind if kind in (dialect.SQLITE, dialect.POSTGRES) else dialect.SQLITE,
        path=sqlite_path.strip() or "data/jellyscope.db",
        host=pg_host.strip() or "localhost",
        port=int(_clamp(pg_port, 1, 65535, 5432)),
        database=pg_database.strip() or "jellyscope",
        user=pg_user.strip() or "jellyscope",
        password=pg_password or (rozepsane.password if rozepsane else "")
                 or current.password,
        use_pool=bool(pg_use_pool),
    )

    # Test ani přenos nic neukládají, ale rozepsané hodnoty si podržíme -
    # jinak by je uživatel po každém kliknutí vyplňoval znovu.
    if action in ("test", "migrate"):
        _draft_save(account, "database", candidate)

    if action == "test":
        ok, message = db.test_connection(candidate)
        _flash(request, message, "success" if ok else "error")
        return RedirectResponse("/settings?section=database", status_code=303)

    if action == "migrate":
        ok, message = db.test_connection(candidate)
        if not ok:
            _flash(request, "Cílová databáze není dostupná: {duvod}", "error",
                   duvod=message)
            return RedirectResponse("/settings?section=database", status_code=303)

        result = dbmigrate.copy_all(current, candidate)
        if result.get("status") != "ok":
            _flash(request, result.get("message", "Přenos selhal."), "error")
        else:
            _flash(
                request,
                "Přeneseno {n} řádků. Ulož nastavení a restartuj, "
                "aby se aplikace na novou databázi přepnula.",
                "success",
                n=result["total"],
            )
        return RedirectResponse("/settings?section=database", status_code=303)

    ok, message = db.test_connection(candidate)
    if not ok:
        _flash(request, "Neukládám - spojení nefunguje: {duvod}", "error",
               duvod=message)
        return RedirectResponse("/settings?section=database", status_code=303)

    dialect.save_config(PROJECT_DIR, candidate)
    _draft_clear(account, "database")   # uloženo, rozepsané už není k čemu
    _flash(
        request,
        "Nastavení databáze uloženo. Změna se projeví po restartu aplikace.",
        "success",
    )
    return RedirectResponse("/settings?section=database", status_code=303)


@app.post("/settings/language")
def settings_language(
    request: Request,
    ui_language: str = Form("cs"),
    log_language: str = Form("cs"),
    account: dict[str, Any] = Depends(require_admin),
):
    if ui_language not in i18n.LANGUAGES:
        ui_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("ui_language", ui_language)

    # Jazyk logu se ukláda ze stejného formuláře, ale je to jiná volba:
    # log často čte někdo jiný, než kdo se dívá do rozhraní.
    if log_language not in i18n.LANGUAGES:
        log_language = i18n.DEFAULT_LANGUAGE
    db.set_setting("log_language", log_language)
    applog.nastav_jazyk(log_language)
    _flash(request, i18n.translate("Uložit jazyk", ui_language) + " ✓", "success")
    return RedirectResponse("/settings?section=general", status_code=303)


@app.post("/settings/restart")
async def settings_restart(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Restartuje **Jellyscope**, ne Jellyfin.

    Nahrazujeme vlastni proces, nic jineho. Na medialni server se tim
    nesaha - beziciho prehravani se to nedotkne. Jedine, co se stane,
    je ze tahle aplikace na par vterin prestane odpovidat.

    Vetsina nastaveni se projevi hned, protoze se cte z databaze pri kazdem
    pouziti. Restart je potreba u veci, ktere se nacitaji jednou pri startu -
    hlavne u zmeny databaze.

    Restart delame tak, ze proces nahradi sam sebe (`os.execv`). Az odpoved
    dorazi do prohlizece, aplikace se zvedne znovu.
    """
    _flash(request, "Aplikace se restartuje. Stránka se obnoví sama, jakmile bude nahoře.", "info")
    _naplanuj_restart()
    # `wait=restart`: stranka si sama pocka, az se zvedne novy proces,
    # a nacte se znovu. Drive to uzivatel musel odhadnout a obnovit rucne.
    return RedirectResponse("/settings?section=general&wait=restart", status_code=303)


def _naplanuj_restart() -> None:
    """Za chvilku nahradí proces sám sebou.

    Vlastní funkce, protože restart potřebuje víc míst - ruční tlačítko
    i obnova zálohy. Dvě kopie téhle logiky by se časem rozešly a jedna
    z nich by zapomněla zavřít spojení.
    """
    async def _restart_soon() -> None:
        # Chvilka na odeslani odpovedi - jinak by prohlizec dostal
        # preruseni spojeni misto presmerovani.
        await asyncio.sleep(1.0)
        log.info("restart na zadost uzivatele")
        # execv nahradi proces, takze zadny uklid uz nikdo neudela -
        # spojeni je potreba zavrit ted, dokud jeste bezime.
        db.close_pool()
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            # Kdyby execv neproslo, aspon slusne skoncime - spravce sluzby
            # (nebo uzivatel) aplikaci nastartuje znovu.
            log.exception("restart pres execv selhal, koncim")
            os._exit(1)

    asyncio.create_task(_restart_soon())


# ---------------------------------------------------------------------------
# Naplanovane ulohy
# ---------------------------------------------------------------------------

@app.post("/settings/tasks")
async def tasks_save(
    request: Request,
    backup_path: str = Form(""),
    backup_keep: str = Form("7"),
    pg_dump_path: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Ulozi nastaveni vsech uloh najednou.

    Zaskrtavatka se do formulare posilaji jen kdyz jsou zaskrtnuta - proto
    se ctou primo z tela pozadavku, ne jako pojmenovane parametry.
    Nezaskrtnute pole se v datech vubec neobjevi.
    """
    form = await request.form()

    for task in tasks.TASKS.values():
        enabled = "1" if form.get(f"enabled_{task.key}") else "0"
        db.set_setting(task.enabled_setting, enabled)

        if task.je_denni:
            # Hodina a minuta chodí ze dvou polí zvlášť, tady se z nich
            # zase složí "HH:MM". `_clamp` ohlídá rozsah a doplní nulu
            # (napsané "3" a "5" je "03:05"), `platny_cas` je poslední
            # pojistka: co by přesto neodpovídalo tvaru, se uloží jako
            # výchozí, ne jako rozbitý rozvrh.
            #
            # Náhradou při nesmyslu je to, co je uložené teď: vymazané
            # pole tak rozvrh nezmění. Nula by z něj tiše udělala půlnoc.
            soucasne = tasks.denni_cas(task).split(":")
            hodina = _clamp(str(form.get(f"time_{task.key}_h", "")),
                            0, 23, int(soucasne[0]))
            minuta = _clamp(str(form.get(f"time_{task.key}_m", "")),
                            0, 59, int(soucasne[1]))
            cas = f"{int(hodina):02d}:{int(minuta):02d}"
            db.set_setting(task.time_setting,
                           tasks.platny_cas(cas, task.default_time))
        else:
            db.set_setting(
                task.interval_setting,
                _clamp(str(form.get(f"minutes_{task.key}", task.default_minutes)),
                       0, 10080, task.default_minutes),
            )

    db.set_setting("backup_path", backup_path.strip())
    db.set_setting("backup_keep", _clamp(backup_keep, 1, 365, 7))
    db.set_setting("pg_dump_path", pg_dump_path.strip())

    _flash(request, "Nastavení úloh uloženo.", "success")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@app.get("/settings/backup/download")
def backup_download(name: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Stáhne jednu zálohu databáze.

    Jméno se ověří proti tomu, co ve složce se zálohami doopravdy leží -
    viz `tasks.backup_file()`. Bez toho by šlo přes adresu stáhnout
    libovolný soubor ze stroje.
    """
    cesta = tasks.backup_file(name)
    if cesta is None:
        raise HTTPException(status_code=404, detail="Taková záloha tu není.")

    # `media_type` schválně octet-stream: prohlížeč soubor uloží, místo
    # aby se pokusil zobrazit SQL jako text ve stránce.
    return FileResponse(cesta, filename=cesta.name,
                        media_type="application/octet-stream")


@app.post("/settings/backup/delete")
def backup_delete(request: Request, name: str = Form(""),
                  account: dict[str, Any] = Depends(require_admin)):
    """Smaže jednu zálohu."""
    if tasks.delete_backup(name):
        _flash(request, "Záloha {nazev} smazána.", "success", nazev=name)
    else:
        _flash(request, "Takovou zálohu se nepodařilo najít.", "error")
    return RedirectResponse("/settings?section=tasks", status_code=303)


@app.post("/settings/backup/restore")
async def backup_restore(request: Request, name: str = Form(""),
                         account: dict[str, Any] = Depends(require_admin)):
    """Obnoví databázi ze zálohy a restartuje aplikaci.

    Restart není kosmetika: aplikace má v paměti nastavení i otevřená
    spojení do databáze, která po obnově už neplatí.
    """
    vysledek = await asyncio.to_thread(tasks.restore_backup, name)
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Obnova selhala."), "error")
        return RedirectResponse("/settings?section=tasks", status_code=303)

    _flash(request,
           "Databáze obnovena ze zálohy {nazev}. Stav před obnovou zůstal "
           "uložený jako {zaloha}. Aplikace se restartuje.",
           "success", nazev=name, zaloha=vysledek["safety"])
    _naplanuj_restart()
    return RedirectResponse("/settings?section=tasks&wait=restart", status_code=303)


@app.post("/settings/tasks/run")
async def tasks_run(
    request: Request,
    key: str = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    """Rucni spusteni ulohy.

    Ulohu odpalime na pozadi a hned odpovime - u velke knihovny by jinak
    prohlizec cekal nekolik minut a vypadalo by to jako zamrznuti.
    """
    task = tasks.TASKS.get(key)
    if task is None:
        _flash(request, "Neznámá úloha.", "error")
    elif scanner.is_scan_running() and key in ("sync", "recent", "tech"):
        # "recent" tu drive chybelo, takze slo spustit soubezne se scanem -
        # a druha uloha pak jen narazila na zamek a tise skoncila.
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
    else:
        asyncio.create_task(tasks.run_now(key))
        # Název úlohy je česká konstanta z tasks.py - v anglickém
        # rozhraní se musí přeložit stejně jako zbytek hlášky.
        _flash(request, "{uloha}: {stav}", "info",
               uloha=i18n.translate(task.name),
               stav=i18n.translate("spuštěno."))
        # Stranka si pocka a po dokonceni se obnovi sama - stejne jako
        # u synchronizace knihovny.
        return RedirectResponse("/settings?section=tasks&wait=task", status_code=303)

    return RedirectResponse("/settings?section=tasks", status_code=303)


# ---------------------------------------------------------------------------
# Import historie
# ---------------------------------------------------------------------------

@app.post("/settings/import/detect")
async def import_detect(request: Request, account: dict[str, Any] = Depends(require_admin)):
    """Zjisti, jestli je v Jellyfinu plugin Playback Reporting."""
    available, message = await importers.playback_reporting_available()
    _flash(request, message, "success" if available else "warning")
    return RedirectResponse("/settings?section=import", status_code=303)


@app.post("/settings/import/playback-reporting")
async def import_playback_reporting(
    request: Request,
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    result = await importers.import_playback_reporting(
        min_seconds=int(_clamp(min_seconds, 0, 3600, 60))
    )
    if result.get("status") == "ok":
        _flash(
            request,
            _t("Playback Reporting: naimportováno {n} záznamů "
               "(z {nalezeno} nalezených, {duplicit} už existovalo).").format(
                   n=result["imported"], nalezeno=result["found"],
                   duplicit=result["duplicate"])
            + _known_note(result) + _linked_note(result),
            "success",
        )
    else:
        _flash(request, result.get("message", "Import selhal."), "error")

    return RedirectResponse("/settings?section=import", status_code=303)


@app.post("/settings/import/playback-reporting-file")
async def import_playback_reporting_file(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Import ze zalohy pluginu Playback Reporting (soubor TSV).

    Zaloha pro pripad, kdy plugin pres API nefunguje - viz
    importers.import_playback_reporting_tsv().
    """
    raw = await backup.read()
    if not raw:
        _flash(request, "Soubor je prázdný.", "error")
        return RedirectResponse("/settings?section=import", status_code=303)

    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        _flash(request, "Soubor je větší než {n} MB.", "error", n=MAX_UPLOAD_MB)
        return RedirectResponse("/settings?section=import", status_code=303)

    result = await importers.import_playback_reporting_tsv(
        raw, min_seconds=int(_clamp(min_seconds, 0, 3600, 60))
    )
    if result.get("status") == "ok":
        _flash(
            request,
            _t("Playback Reporting (záloha): naimportováno {n} záznamů "
               "(z {nalezeno} nalezených, {duplicit} už existovalo).").format(
                   n=result["imported"], nalezeno=result["found"],
                   duplicit=result["duplicate"])
            + _known_note(result) + _linked_note(result),
            "success",
        )
    else:
        _flash(request, result.get("message", "Import selhal."), "error")

    return RedirectResponse("/settings?section=import", status_code=303)


@app.post("/settings/history/cleanup")
def history_cleanup(request: Request,
                    account: dict[str, Any] = Depends(require_admin)):
    """Úklid historie: duplicity a záznamy visící na špatném dílu.

    Obojí jsou následky chyb, které už jsou opravené - tohle napraví, co
    po nich v databázi zůstalo. Pouštět se to dá opakovaně: podruhé
    nenajde nic.
    """
    # Napřed navázat osiřelé záznamy: teprve když vědí, ke které položce
    # patří, dá se u nich poznat správný díl i případná duplicita.
    navazano = importers.relink_orphans()
    vraceno = importers.repair_episode_links()
    slouceno = importers.merge_duplicate_playback()
    # Až nakonec: slučování napříč zdroji porovnává tituly podle názvu
    # z knihovny, takže mu prospěje, když jsou záznamy už navázané.
    z_importu = importers.merge_import_duplicates()
    # Až úplně nakonec: názvy. Když se titul v Jellyfinu přejmenoval (nebo
    # se u něj spravila špatně určená metadata), nese starý záznam historie
    # pořád ten původní název - a ve statistikách pak k jednomu titulu
    # patří jméno druhého.
    nazvy = importers.sjednot_nazvy()

    casti = []
    if navazano["items"]:
        casti.append(f"navázáno na knihovnu: {navazano['rows']} záznamů")
    if vraceno["moved"]:
        casti.append(f"vráceno ke správným dílům: {vraceno['moved']}")
    if slouceno["removed"]:
        casti.append(f"sloučeno duplicit: {slouceno['removed']}")
    if z_importu["removed"]:
        casti.append(f"sloučeno napříč zdroji importu: {z_importu['removed']}")
    if nazvy["rows"]:
        casti.append(f"srovnáno názvů podle knihovny: {nazvy['rows']} "
                     f"({nazvy['items']} titulů)")

    _flash(request, "Úklid historie: {co}", "success",
           co=", ".join(casti) if casti
           else _t("nic k opravě, historie je v pořádku."))
    return RedirectResponse("/settings?section=import#uklid", status_code=303)


@app.get("/settings/history/orphans", response_class=HTMLResponse)
def history_orphans(request: Request, priradit: str = "", q: str = "",
                    account: dict[str, Any] = Depends(require_admin)):
    """Seznam záznamů, které se nepodařilo zařadit - i s důvodem.

    Nic se nikam neukládá, seznam se počítá pokaždé znovu. Uložený by
    ukazoval stav po posledním úklidu, tedy něco, co už nemusí platit -
    mezitím mohla proběhnout synchronizace nebo další import.
    """
    osirele = importers.rozbor_osirelych()
    vybrany = None
    if priradit:
        vybrany = next((o for o in osirele if str(o["item_id"]) == priradit), None)

    return templates.TemplateResponse(request, "orphans.html", _context(
        request, account,
        orphans=osirele,
        duvody=importers.DUVODY_POPIS,
        vybrany=vybrany,
        hledat=q,
        kandidati=importers.kandidati_pro_osireleho(q) if vybrany else [],
    ))


@app.post("/settings/history/assign")
def history_assign(request: Request, item_id: str = Form(""),
                   target_id: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Ruční přiřazení osiřelých záznamů k položce z knihovny."""
    vysledek = importers.prirad_rucne(item_id.strip(), target_id.strip())
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
    else:
        _flash(request, "Přiřazeno k „{nazev}“ – {n} záznamů.", "success",
               nazev=vysledek["name"], n=vysledek["rows"])
    return RedirectResponse("/settings/history/orphans", status_code=303)


@app.post("/settings/history/lookup")
async def history_lookup(request: Request,
                         account: dict[str, Any] = Depends(require_admin)):
    """Osiřelé záznamy zkusí dohledat přímo v Jellyfinu.

    Identifikátor v převzaté historii je pravý Jellyfin ItemId - jen
    k němu u nás nic nevede, protože Jellystat nese jen název dílu
    („7. epizoda"). Jellyfin to id zná a řekne seriál i číslo dílu.
    Čte se, nezapisuje.
    """
    vysledek = await importers.dohledej_osirele_v_jellyfinu()
    if vysledek.get("status") != "ok":
        _flash(request, vysledek.get("message", "Nepovedlo se."), "error")
        return RedirectResponse("/settings?section=import#uklid", status_code=303)

    if not vysledek["dotazano"]:
        zprava = _t("Není co dohledávat - osiřelé záznamy tu nejsou.")
    elif not vysledek["nalezeno"]:
        zprava = _t("Jellyfin nezná ani jeden z {n} titulů. Jsou to tituly, "
                    "které v knihovně už nejsou.").format(n=vysledek["dotazano"])
    else:
        casti = [_t("Jellyfin zná {n} z {celkem}").format(
            n=vysledek["nalezeno"], celkem=vysledek["dotazano"])]
        if vysledek["navazano"]:
            casti.append(_t("navázáno na knihovnu: {n} titulů").format(
                n=vysledek["navazano"]))
        if vysledek["zalozeno"]:
            casti.append(_t("doplněno do knihovny: {n} titulů").format(
                n=vysledek["zalozeno"]))
        # Záznam visel na id seriálu, ne dílu. Položku z toho udělat
        # nejde (seriál není soubor), ale jméno seriálu ano - a tím se
        # záznam v přehledech zařadí pod svůj seriál.
        if vysledek.get("doplneno"):
            casti.append(_t("doplněn seriál u {n} titulů").format(
                n=vysledek["doplneno"]))
        if vysledek["radku"]:
            casti.append(_t("celkem {n} záznamů").format(n=vysledek["radku"]))
        zprava = ", ".join(casti) + "."
    _flash(request, zprava, "success")
    return RedirectResponse("/settings?section=import#uklid", status_code=303)


@app.post("/settings/import/jellystat")
async def import_jellystat(
    request: Request,
    backup: UploadFile = File(...),
    min_seconds: str = Form("60"),
    account: dict[str, Any] = Depends(require_admin),
):
    """Import z nahraneho JSON souboru se zalohou Jellystatu."""
    raw = await backup.read()
    if not raw:
        _flash(request, "Soubor je prázdný.", "error")
        return RedirectResponse("/settings?section=import", status_code=303)

    # Rozumny strop - bez nej by slo pametí serveru poslat gigabajtovy soubor.
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        _flash(request, "Soubor je větší než {n} MB.", "error", n=MAX_UPLOAD_MB)
        return RedirectResponse("/settings?section=import", status_code=303)

    result = await importers.import_jellystat_json(
        raw, min_seconds=int(_clamp(min_seconds, 0, 3600, 60))
    )
    if result.get("status") == "ok":
        _flash(
            request,
            _t("Jellystat: naimportováno {n} záznamů "
               "(z {nalezeno} nalezených, {duplicit} už existovalo).").format(
                   n=result["imported"], nalezeno=result["found"],
                   duplicit=result["duplicate"])
            + _known_note(result) + _linked_note(result),
            "success",
        )
    else:
        _flash(request, result.get("message", "Import selhal."), "error")

    return RedirectResponse("/settings?section=import", status_code=303)


# ---------------------------------------------------------------------------
# Sprava uctu
#
# Vsimni si, ze kazda akce ma vlastni adresu a posila se metodou POST.
# Mazani pres GET (treba odkazem /smazat?id=3) je klasicka chyba: takovou
# adresu si prohlizec muze nacist sam, treba pri predbeznem nacitani odkazu,
# a ucet zmizi bez toho, aby na neco nekdo klikl.
# ---------------------------------------------------------------------------

@app.post("/settings/blocks/unblock")
def blocks_unblock(request: Request, ip: str = Form(""),
                   account: dict[str, Any] = Depends(require_admin)):
    """Zruší blokaci přihlašování pro jednu adresu."""
    if accounts.odblokuj(ip.strip()):
        _flash(request, "Adresa {ip} je odblokovaná.", "success", ip=ip.strip())
    else:
        _flash(request, "Taková blokace v seznamu není.", "warning")
    return RedirectResponse("/settings?section=blocks", status_code=303)


@app.post("/settings/blocks/permanent")
def blocks_permanent(request: Request, ip: str = Form(""),
                     account: dict[str, Any] = Depends(require_admin)):
    """Zablokuje adresu natrvalo - dokud ji správce sám nepustí."""
    adresa = ip.strip()
    if not adresa:
        _flash(request, "Chybí adresa.", "error")
    else:
        accounts.zablokuj_natrvalo(adresa)
        _flash(request, "Adresa {ip} je zablokovaná natrvalo.", "success",
               ip=adresa)
    return RedirectResponse("/settings?section=blocks", status_code=303)


@app.post("/settings/accounts/create")
def account_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_again: str = Form(""),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.create(username, password, password_again, is_admin=bool(is_admin))
        _flash(request, "Účet '{jmeno}' vytvořen.", "success", jmeno=username)
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@app.post("/settings/accounts/password")
def account_password(
    request: Request,
    account_id: int = Form(...),
    password: str = Form(""),
    password_again: str = Form(""),
    account: dict[str, Any] = Depends(require_login),
):
    """Zmena hesla.

    Sve vlastni heslo si smi zmenit kazdy. Cizi jen spravce - proto tahle
    routa nepouziva require_admin, ale kontroluje opravneni sama.
    """
    if account_id != account["id"] and not account["is_admin"]:
        raise HTTPException(status_code=403, detail="Cizi heslo smi menit jen spravce.")

    try:
        accounts.set_password(account_id, password, password_again)
        _flash(request, "Heslo změněno.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@app.post("/settings/accounts/role")
def account_role(
    request: Request,
    account_id: int = Form(...),
    is_admin: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    try:
        accounts.set_admin(account_id, bool(is_admin))
        _flash(request, "Oprávnění změněno.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


@app.post("/settings/accounts/delete")
def account_delete(
    request: Request,
    account_id: int = Form(...),
    account: dict[str, Any] = Depends(require_admin),
):
    if account_id == account["id"]:
        _flash(request, "Vlastní účet smazat nemůžeš.", "warning")
        return RedirectResponse("/settings?section=accounts", status_code=303)

    try:
        accounts.delete(account_id)
        _flash(request, "Účet smazán.", "success")
    except accounts.AccountError as exc:
        _flash(request, exc.prelozena(), "error")
    return RedirectResponse("/settings?section=accounts", status_code=303)


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


@app.get("/languages", response_class=HTMLResponse)
def languages_page(
    request: Request, days: Optional[int] = None, account: dict[str, Any] = Depends(require_login)
):
    days = _days(request, days)

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

# Kdy tenhle proces nastartoval. Po restartu je hodnota jina - a to je
# jediny spolehlivy zpusob, jak z prohlizece poznat, ze uz bezi nova
# instance. Cekat na "prestane odpovidat a zase zacne" nestaci: restart
# trva chvilku a dotaz se do te mezery nemusi vubec trefit.
STARTED_AT = time.time()


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
