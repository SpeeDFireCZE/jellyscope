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
import hashlib
import importlib.util
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any, Optional


from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import (accounts, api, api_dotaz, applog, charts, collector, db, formatting, geoip,
               i18n, langstats, languages, mapa_podrobna,
               pristup,
               scanner, sekce,
               stats, tasks, udalosti)
# Verze běžícího procesu. Schválně natvrdo při importu: po `git pull`
# leží na disku nová, ale tenhle proces pořád běží starý kód - a čekárna
# po aktualizaci potřebuje vědět přesně to, co běží tady, ne co je na
# disku. Až se proces vymění, načte se soubor znovu i s novým číslem.
from . import __version__
# PROJECT_DIR je kořen projektu (tam, kde je run.py a složka data),
# PACKAGE_DIR je tenhle balíček. Nejsou totéž a plete se to snadno -
# proto mají různá jména místo jednoho BASE_DIR.
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
    _bez_mezipameti,
    _cas_presne,
    _cesky_datum,
    _cislo_z_formulare,
    _clamp,
    _context,
    _flash,
    _hlaska_importu,
    _known_note,
    _linked_note,
    _novy_klic,
    _obdobi_do_sablony,
    _obdobi_popis,
    _opraveno_note,
    _posledni_den,
    _rychla_obdobi,
    _stropy,
    _veta,
    _vzhled,
    _podklad_mapy,
    _zoom_rezim,
    config,
    current_account,
    prihlas_do_relace,
    log,
    muj_divak,
    otisk_obrazku_uctu,
    require_admin,
    require_login,
    templates,
)
from .jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError


STATIC_DIR = PACKAGE_DIR / "static"

# Obdobi, ktera si uzivatel muze vybrat. Pevny seznam - do SQL se nikdy
# nedostane cislo primo z adresy.
ALLOWED_DAYS = (7, 30, 90, 365)
# `?days=0` = celá historie (stats.obdobi_cela_historie). Není v přepínači,
# je jako tlačítko v okně vlastního období.
CELA_HISTORIE = 0
DAY_LABELS = {7: "7 dnů", 30: "30 dnů", 90: "90 dnů", 365: "rok"}

# Jedno vychozi obdobi pro celou aplikaci. Drive melo kazde stranka vlastni
# (nekde 30 dnu, jinde rok) a pri prepinani zalozek se cislo neocekavane
# menilo. Ted je spolecne a zvolena hodnota se drzi napric strankami.
DEFAULT_DAYS = 30
DAYS_SESSION_KEY = "days"

# Kolik archivovanych polozek ukazuji okna archivu (nejvic prehravane
# napred). U prirazeni ma kazdy radek vlastni hledani, proto mene; po
# smazani nebo prirazeni se ukazou dalsi.
STROP_PRIRAZENI_ARCHIVU = 500
STROP_MAZANI_ARCHIVU = 1000

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
    # Kolik starých logů držet, i pro rotaci pojistkou podle velikosti.
    # Log se zapíná před databází, takže se to dozví až teď.
    applog.nastav_pocet(tasks.pocet_rotace())
    # Přehled pro všechny má od verze s Mým přehledem název. Stávající
    # zapnutý bez názvu dostane ten, pod kterým ho lidé znali.
    sekce.doplnit_nazev()

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
        # Vymyslena data starnou: jsou vyrobena k okamziku seedu a verejna
        # ukazka bezi tydny. Jednou za den se vyrobi znovu, posunuta
        # k dnesku - jinak z grafu "poslednich 30 dnu" zbyde cara.
        from . import demodata

        background.append(
            asyncio.create_task(demodata.obnovuj(), name="demo-obnova")
        )
        # A v Prave se hraje porad neco bezi - vymysleny stream hraje dal.
        background.append(
            asyncio.create_task(demodata.udrzuj_zive(), name="demo-zivy-stream")
        )
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
        _flash(request, "Tohle je ukázka, data se v ní nemění. "
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


# Co smí divák z Jellyfinu odeslat (POST). Jinak jen čte. Vlastní API
# klíč si vyrobí jen s povolením - to hlídá routa sama.
DIVAK_SMI_POST = frozenset({"/logout", "/account/language", "/account/password",
                            "/account/klic", "/account/klic/zrusit",
                            # Bez pohybu u sezónní výzdoby - jen pro sebe.
                            "/account/udalosti-pohyb",
                            # Můj přehled - vlastní rozvržení, nikoho jiného.
                            "/account/prehled"})


@app.middleware("http")
async def rozsah_divaka(request: Request, call_next):
    """Kdo není správce, vidí jen to, co mu správce zaškrtl.

    Hlídá se to **na jednom místě**, ne v každé routě: kdyby se na jednu
    zapomnělo, neprojeví se to jako chyba, ale jako cizí historie na
    obrazovce někoho, komu do ní nic není. Proto povolující seznam a
    zamčené dveře - co v něm není, končí tady.

    Skupiny jsou dvě a liší se v tom, co znamená „moje":

    * **divák z Jellyfinu** má svůj účet u konkrétního člověka, takže mu
      vlastní čísla patří vždycky. Přehled ho pošle na jeho stránku,
      cizí stránka diváka je zavřená a nic nemění - jen čte.
    * **místní čtenářský účet** nikomu konkrétnímu nepatří (zakládá ho
      správce), takže „moje historie" u něj nedává smysl. Platí pro něj
      jen zaškrtnuté oblasti.
    """
    ucet = current_account(request)
    kdo = pristup.role(ucet)
    if kdo in ("", pristup.SPRAVCE):
        return await call_next(request)

    divak = muj_divak(ucet)
    cesta = request.url.path

    # Kdo nesmi Prehled, toho / posle na prvni stranku, kterou smi (divaka
    # na jeho vlastni) - ne na „sem nemas pristup". Sem miri i prihlaseni,
    # heslem i kodem, takze se po nem nikdo nezasekne na zavrenych dverich.
    # `ucet` jde s kazdym dotazem dal: ucet muze mit vlastni prava, ktera
    # skupinu prebiji (Nastaveni -> Ucty -> Prava).
    if cesta == "/" and not pristup.vidi(kdo, "prehled", ucet):
        return RedirectResponse(pristup.domovska(kdo, divak, ucet), status_code=303)
    if divak:
        if cesta.startswith("/users/"):
            if cesta.rstrip("/") != f"/users/{divak}":
                return _nesmis(request)
            return await call_next(request)
        # Divak nic nemeni, jen cte - s vyjimkou odhlaseni a sveho uctu
        # (Muj ucet). Vycet, ne predpona /account/: kazda dalsi zapisujici
        # routa se sem musi pridat vedome, jinak by byla divakum otevrena
        # sama od sebe. Routy berou ucet z relace a heslo uctu z Jellyfinu
        # odmitne sama routa (accounts.zmen_vlastni_heslo).
        if request.method != "GET" and cesta not in DIVAK_SMI_POST:
            return _nesmis(request)

    if not pristup.smi_cestu(kdo, cesta, ucet):
        return _nesmis(request)

    return await call_next(request)


def _nesmis(request: Request):
    """Odpověď na „tohle ti nepatří". Stránka, ne holé 403."""
    ucet = current_account(request)
    return templates.TemplateResponse(
        request, "nesmis.html", _context(request, ucet), status_code=403)


@app.middleware("http")
async def jazyk_uctu(request: Request, call_next):
    """Jazyk, který si přihlášený účet vybral na stránce Můj účet.

    Pořadí middlewarů tu rozhoduje: každý další obaluje ty předchozí.
    Tenhle potřebuje relaci, takže musí být uvnitř SessionMiddleware (ten
    se přidává hned pod ním), a zároveň vně `rozsah_divaka`
    a `ukazkovy_rezim` - i jejich stránky („sem nemáš přístup", hláška
    ukázky) mají být v jazyce účtu. Bez účtu (přihlášení, API klíčem)
    zůstane jazyk serveru.
    """
    ucet = current_account(request)
    token = i18n.nastav_jazyk_uctu((ucet or {}).get("ui_language"))
    try:
        return await call_next(request)
    finally:
        i18n.vrat_jazyk_uctu(token)


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


class ZaznamPouzitiApi:
    """Každý požadavek s existujícím klíčem se zapíše - až po odpovědi.

    Až teprve po ní je známý výsledek (200, 403, ...). Klíč (id, jméno,
    vlastník) sem nechává api.over_token() v `request.state`; neplatný
    klíč ho nenechá, a tak se nezapíše. Zapisuje se do logu API.

    Čisté ASGI, ne `@app.middleware`: tam odchází tělo odpovědi až po
    návratu middlewaru, takže by zápis zdržel každou odpověď API. Tady má
    klient odpověď dřív, než se zapisuje.

    Zápis nesmí shodit odpověď: kdo se ptá na čísla, má je dostat, i když
    se záznam zrovna nepovedl. A neodchycená chyba routy (500) se zapíše
    taky - pak se pošle dál, ať ji obslouží Starlette jako dřív.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        vysledek = 500

        async def posli(zprava) -> None:
            nonlocal vysledek
            if zprava["type"] == "http.response.start":
                vysledek = zprava["status"]
            await send(zprava)

        try:
            await self.app(scope, receive, posli)
        except Exception:
            await _zapis_pouziti(scope, vysledek)
            raise
        await _zapis_pouziti(scope, vysledek)


# Kdy se naposledy zalogovala chyba zapisu pouziti. Kdyz zapis selhava
# porad (plny disk, prava), Grafana by kazdych par vterin pridala do logu
# celou chybovou hlasku - staci jedna za ctvrt hodiny.
CHYBA_ZAPISU_PO_SEKUNDACH = 900
_posledni_chyba_zapisu = 0.0


async def _zapis_pouziti(scope, vysledek: int) -> None:
    global _posledni_chyba_zapisu
    klic = (scope.get("state") or {}).get("api_klic_do_logu")
    if not klic:
        return
    cesta = scope["path"]
    dotaz = scope.get("query_string", b"").decode("latin-1")
    if dotaz:
        cesta = f"{cesta}?{dotaz}"
    try:
        await asyncio.to_thread(api.zapis_pouziti, klic, cesta,
                                _adresa_klienta(Request(scope)), vysledek)
    except Exception:  # noqa: BLE001 - zaznam je navic, odpoved ne
        if time.time() - _posledni_chyba_zapisu >= CHYBA_ZAPISU_PO_SEKUNDACH:
            _posledni_chyba_zapisu = time.time()
            log.exception("Zapis pouziti API klice se nepovedl")


app.add_middleware(ZaznamPouzitiApi)


DEMO_POVOLENO = frozenset({
    "/login",
    "/logout",
})


def _demo_blokuje(request: Request) -> bool:
    """Ma se tenhle pozadavek v ukazce zastavit?"""
    if not config.demo_mode or request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    return request.url.path not in DEMO_POVOLENO


# Odkud si prohlizec bere dlazdice mapy, kdyz spravce zapne podklad z OSM.
OSM_DLAZDICE = "https://tile.openstreetmap.org"


def csp(osm: bool = False) -> str:
    """Bezpecnostni hlavicka Content-Security-Policy.

    Jedno misto, at se pravidla nerozejdou s tim, co stranky opravdu
    delaji. Obrazky chodi vsechny pres /image (ani data: neni co
    povolovat) - s jedinou vyjimkou: dlazdice mapy z OpenStreetMap, a ty
    jen kdyz je spravce zapnul. Vypnuty podklad = hlavicka jako vzdycky.
    """
    return "; ".join((
        "default-src 'self'",
        f"img-src 'self' {OSM_DLAZDICE}" if osm else "img-src 'self'",
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
    # Podklad mapy z OpenStreetMap meni dve hlavicky (CSP a Referrer-Policy).
    # Kdyz nastaveni nejde precist (databaze zrovna nebezi), plati prisna
    # varianta - hlavicka nesmi shodit odpoved, ani /health nebo styly.
    try:
        osm = _podklad_mapy() == "osm"
    except Exception:  # noqa: BLE001 - jakakoliv chyba = prisne hlavicky
        osm = False
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
    #
    # Se zapnutym podkladem z OSM jde ven adresa serveru (bez cesty, takze
    # bez id): OSM dlazdice bez Refereru odmita a atribut referrerpolicy
    # u SVG <image> nemusi kazdy prohlizec znat. U volby to stoji.
    response.headers.setdefault(
        "Referrer-Policy", "strict-origin-when-cross-origin" if osm else "same-origin")
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
    response.headers.setdefault("Content-Security-Policy", csp(osm))
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
# Sezónní výzdoba: který svátek je (klíč, nebo "") a smí se účtu hýbat.
# Globální funkce, ne kontext - přihlašovací stránky žádný _context nemají.
templates.env.globals["udalost"] = udalosti.pro_sablonu
templates.env.globals["udalost_pohyb"] = udalosti.pohyb
templates.env.globals["udalost_rok"] = udalosti.rok_pro_sablonu
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







# Kód chyby u HTTP chyb API, které nevznikly jako ChybaApi (neznámá adresa,
# jiná metoda než GET) - program pozná, co je špatně, bez čtení věty.
KODY_HTTP_CHYB = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                  405: "method_not_allowed"}


@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    """Presmerovani misto chybove hlasky u nepřihlaseneho uzivatele."""
    if exc.status_code == 307 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    # API se ptaji programy (Grafana, Homepage), ne lide: chybova stranka
    # v HTML by jim nerekla nic. Tam je kazda chyba JSON s duvodem.
    api_pozadavek = request.url.path.startswith("/api/")
    if exc.status_code == 403 and not api_pozadavek:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 403, "message": exc.detail}, status_code=403,
        )
    if exc.status_code == 404 and not api_pozadavek:
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 404, "message": exc.detail or "Stranka nenalezena."},
            status_code=404,
        )
    # Hlavičky výjimky patří do odpovědi. U 401 z API je v nich
    # `WWW-Authenticate: Bearer`, tedy jediná věta, která volajícímu
    # řekne, čím se má prokázat - bez ní je to jen "nesmíš".
    telo: dict[str, Any] = {"detail": exc.detail}
    if api_pozadavek:
        telo["error"] = KODY_HTTP_CHYB.get(exc.status_code, "http_error")
    return JSONResponse(telo, status_code=exc.status_code,
                        headers=exc.headers or None)


@app.exception_handler(api_dotaz.ChybaApi)
async def handle_api_error(request: Request, exc: api_dotaz.ChybaApi):
    """Chyba API: vždy JSON se stejným tělem (`detail` + `error`).

    Program podle `error` pozná, co je špatně, bez čtení věty. U 401 jde
    s odpovědí i `WWW-Authenticate: Bearer`.
    """
    return JSONResponse(exc.telo(), status_code=exc.status, headers=exc.hlavicky)


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
    prihlas_do_relace(request, accounts.get(account_id) or {"id": account_id})
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


@app.post("/login")
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


@app.post("/login/quick")
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


@app.get("/login/quick/stav")
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


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Muj ucet - stranka o jednom uctu, ne zmensene Nastaveni
# ---------------------------------------------------------------------------
#
# Ucet se vzdycky bere z relace, nikdy z formulare: odsud nejde zmenit nic
# ciziho, ani kdyby nekdo do formulare podstrcil cizi id.

@app.get("/account", response_class=HTMLResponse)
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


@app.get("/account/obrazek")
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


@app.post("/account/language")
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


@app.post("/account/udalosti-pohyb")
def muj_ucet_pohyb_udalosti(request: Request, pohyb: str = Form("1"),
                            account: dict[str, Any] = Depends(require_login)):
    """Hýbe se mi sezónní výzdoba? Vypne ji jen výslovné "0"."""
    accounts.nastav_pohyb_udalosti(account["id"], pohyb != "0")
    _flash(request, "Uloženo.", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/klic")
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


@app.post("/account/klic/zrusit")
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


@app.post("/account/password")
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
    if value == CELA_HISTORIE:
        # Pamatuje se volba, ne meze: zítra má zahrnout i zítřek.
        request.session[DAYS_SESSION_KEY] = CELA_HISTORIE
        return stats.obdobi_cela_historie()

    remembered = request.session.get(DAYS_SESSION_KEY)
    if remembered == CELA_HISTORIE and not isinstance(remembered, bool):
        return stats.obdobi_cela_historie()
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
    vychozi = _days(request, None)
    if getattr(vychozi, "cela_historie", False):
        # Cela historie nema stejne dlouhe okno pred sebou - pred prvnim
        # prehranim nic neni a srovnavalo by se vsechno s prazdnem.
        vychozi = DEFAULT_DAYS
    prvni = _obdobi_srovnani(request, SROVNANI_A_KEY, a_days, a_od, a_do,
                             vychozi=vychozi)
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
    # `od` a `do` tu chybely, prestoze se s nimi v tele pocitalo: volani
    # koncilo na NameError, tedy chybou 500. Nikdo si toho nevsiml,
    # protoze stranka po neuspechu nacte celou sebe (viz `.catch`
    # v base.html) - filtr fungoval dal, jen pomalu a se skokem nahoru,
    # tedy presne tim, cemu se timhle vyrezem predchazelo.
    od: Optional[str] = None,
    do: Optional[str] = None,
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


@app.get("/top-items", response_class=HTMLResponse)
def top_items_vse(
    request: Request,
    kind: Optional[str] = None,
    days: Optional[int] = None,
    od: Optional[str] = None,
    do: Optional[str] = None,
    account: dict[str, Any] = Depends(require_login),
):
    """Celý žebříček nejsledovanějších - graf na Přehledu ukazuje deset.

    Jedna routa, dvě podoby: pro `fetch` z okna nad Přehledem vrací jen
    výřez (tabulku), pro obyčejné otevření celou stránku. Bez
    JavaScriptu tak o seznam nikdo nepřijde a obsah je v obou případech
    z téže šablony.

    Strop tisíc řádků není omezení, ale pojistka: seriály se sčítají,
    takže tisíc řádků znamená tisíc různých titulů za období.
    """
    druh = _kind(request, kind, TOP_KIND_SESSION_KEY)
    context = _context(
        request, account,
        top_kind=druh,
        top_items=stats.top_items(_days(request, days, od, do), limit=1000,
                                  kind=druh),
    )
    if request.headers.get("x-requested-with") == "fetch":
        return templates.TemplateResponse(request, "_top_items_vse.html", context)
    return templates.TemplateResponse(request, "top_items_vse.html", context)


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
    days: Optional[int] = None,
    od: Optional[str] = None,
    do: Optional[str] = None,
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
        # Okna „přiřadit k jiným titulům" a „smazat z archivu" - jen
        # v archivu a jen správci. Položky jednotlivě, díly zvlášť:
        # přiřazuje i maže se po souborech. Mazání má delší seznam - řádek
        # je jen zaškrtávátko, kdežto u přiřazení je v každém hledání.
        if show_archived and account.get("is_admin"):
            archiv = stats.archivovane_polozky(library_id, limit=STROP_MAZANI_ARCHIVU)
            context["k_mazani"] = archiv
            context["strop_mazani"] = STROP_MAZANI_ARCHIVU
            context["k_prirazeni"] = archiv[:STROP_PRIRAZENI_ARCHIVU]
    else:
        # Aktivita bere zvolene obdobi jako ostatni stranky (i vlastni
        # rozmezi a i zapamatovane odjinud). Drive mela napevno 90 dni
        # a nebylo to poznat - kdo mel jinde 30 dni, cetl cisla za 90.
        # Prehled a Media zustavaji bez obdobi: jsou o tom, co v knihovne
        # je, ne co se sledovalo.
        obdobi = _days(request, days, od, do)
        context.update(days=obdobi, activity=stats.library_activity(library_id, obdobi))

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
    request: Request, item_id: str, verze: Optional[str] = None,
    account: dict[str, Any] = Depends(require_login)
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
        # Verze titulu (4K vedle 1080p). `vybrana` je ta, jejíž údaje
        # se kreslí; bez parametru je to ta, kterou Jellyfin považuje za
        # hlavní - tedy tatáž, ze které se počítají statistiky.
        # Ne `verze`: tak se v base.html jmenuje verze aplikace v patičce
        # a seznam souborů by ji přepsal - patička by ukázala jen "v".
        verze_souboru=stats.verze_polozky(item_id),
        vybrana_verze=verze,
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
        zprava += " " + _t("(soubor se změřit nepodařilo, viz Log)")
    _flash(request, zprava, "success")
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@app.post("/item/{item_id}/delete")
async def item_delete(
    request: Request,
    item_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Nenavratne smaze archivovanou polozku i s jeji historii.

    Jen pro spravce a jen rucne. Automaticky se nemaze nikdy - polozka,
    ktera v Jellyfinu zmizi, se jen archivuje. Kdyz Jellyfin na chvili
    vypadne, prijdes jinak o historii kvuli docasnemu vypadku.

    Pred smazanim zaloha, stejne jako u zapomenuti divaka: kdo klikl
    na spatny titul, ma odkud se vratit. Kdyz se zaloha nepovede,
    nemaze se.
    """
    # Jen titul z archivu. Titul, který v knihovně je, by přišel
    # o historii a příští synchronizace by ho založila znovu prázdný.
    polozka = stats.item(item_id)
    if polozka is None or not polozka.get("is_missing"):
        _flash(request, "Položka neexistuje." if polozka is None
               else "Smazat jde jen titul z archivu.", "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nesmazalo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

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


@app.get("/archiv/hledat")
def archiv_hledat(q: str = "", account: dict[str, Any] = Depends(require_admin)):
    """Živé tituly pro výběr cíle přiřazení - JSON pro okno v prohlížeči.

    Jen pro správce, stejně jako přiřazení samo. Vrací se jen to, co
    okno ukáže: id, popis, knihovna a jméno souboru (ne celá cesta -
    ta o disku serveru prozradí víc, než výběr potřebuje).
    """
    return JSONResponse({"polozky": stats.hledej_existujici(q)})


@app.post("/item/{item_id}/priradit")
async def item_priradit(
    request: Request,
    item_id: str,
    cil: str = Form(""),
    account: dict[str, Any] = Depends(require_admin),
):
    """Historie archivované položky přejde na vybraný titul, položka zmizí."""
    try:
        _over_prirazeni(item_id, cil)
    except stats.PrirazeniError as chyba:
        _flash(request, str(chyba), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nepřesunulo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(f"/item/{item_id}", status_code=303)

    try:
        vysledek = await asyncio.to_thread(stats.prirad_k_jinemu, item_id, cil)
    except stats.PrirazeniError as chyba:
        _flash(request, str(chyba), "error")
        return RedirectResponse(f"/item/{item_id}", status_code=303)
    _flash(request, "Přiřazeno: {z} → {na} ({n} přehrávání). Položka z archivu je smazaná.",
           "success", z=vysledek["z"], na=vysledek["na"], n=vysledek["prehravani"])
    return RedirectResponse(f"/item/{vysledek['novy_id']}", status_code=303)


@app.post("/library/{library_id}/archiv/priradit")
async def archiv_priradit(
    request: Request,
    library_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Hromadné přiřazení z okna archivu.

    Formulář nese pole `cil_<id archivované položky>`. Prázdné pole =
    ta položka se nechává být. Jedna záloha na celou dávku, ne na každý
    řádek - a před ní kontrola všech dvojic, ať se kvůli překlepu
    v jedné neudělá záloha zbytečně.
    """
    formular = await request.form()
    dvojice: list[tuple[str, str]] = []
    for klic, hodnota in formular.multi_items():
        if not klic.startswith("cil_") or not str(hodnota).strip():
            continue
        dvojice.append((klic[len("cil_"):], str(hodnota).strip()))
    zpet = f"/library/{library_id}?tab=media&archived=1"
    # Meni se az po potvrzeni rekapitulace. Tlacitko "Ano, priradit" je
    # jedine, ktere tohle pole posila - odeslani jinou cestou (Enter ve
    # formulari, stranka bez skriptu) nezmeni nic.
    if formular.get("potvrzeno") != "1":
        _flash(request, "Nic se nezměnilo. Přiřazení se provede až po potvrzení rekapitulace.",
               "info")
        return RedirectResponse(zpet, status_code=303)
    if not dvojice:
        _flash(request, "Žádné přiřazení, u žádného titulu nebyl vybraný cíl.", "info")
        return RedirectResponse(zpet, status_code=303)

    chyby: list[str] = []
    platne: list[tuple[str, str]] = []
    for stary, novy in dvojice:
        try:
            _over_prirazeni(stary, novy)
            platne.append((stary, novy))
        except stats.PrirazeniError as chyba:
            chyby.append(str(chyba))
    if not platne:
        _flash(request, "Nic se nepřiřadilo: {duvod}", "error", duvod=chyby[0])
        return RedirectResponse(zpet, status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nepřesunulo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(zpet, status_code=303)

    titulu = prehravani = 0
    for stary, novy in platne:
        try:
            vysledek = await asyncio.to_thread(stats.prirad_k_jinemu, stary, novy)
        except stats.PrirazeniError as chyba:
            chyby.append(str(chyba))
            continue
        titulu += 1
        prehravani += vysledek["prehravani"]
    _flash(request, "Přiřazeno titulů: {t}, přesunuto přehrávání: {n}. Položky z archivu jsou smazané.",
           "success" if not chyby else "warning", t=titulu, n=prehravani)
    return RedirectResponse(zpet, status_code=303)


@app.post("/library/{library_id}/archiv/smazat")
async def archiv_smazat(
    request: Request,
    library_id: str,
    account: dict[str, Any] = Depends(require_admin),
):
    """Hromadné smazání z okna archivu - tituly i jejich historie.

    Formulář nese pole `smazat` s id každé zaškrtnuté položky. Smaže se
    jen to, co je opravdu v archivu téhle knihovny (viz
    `stats.jen_z_archivu`). Jedna záloha na celou dávku, a jen když je
    co mazat.
    """
    # Zaškrtnout jde až STROP_MAZANI_ARCHIVU položek; Starlette jinak
    # pustí nejvýš 1000 polí a s „Vybrat vše" by formulář neprošel.
    formular = await request.form(max_fields=STROP_MAZANI_ARCHIVU + 10)
    zpet = f"/library/{library_id}?tab=media&archived=1"
    # Maže se až po potvrzení. Tlačítko „Ano, smazat" je jediné, které
    # tohle pole posílá.
    if formular.get("potvrzeno") != "1":
        _flash(request, "Nic se nesmazalo. Smazání se provede až po potvrzení.", "info")
        return RedirectResponse(zpet, status_code=303)
    vybrane = [str(h) for h in formular.getlist("smazat")]
    if not stats.jen_z_archivu(vybrane, library_id):
        _flash(request, "Nic se nesmazalo, nebyla vybraná žádná položka z archivu.", "info")
        return RedirectResponse(zpet, status_code=303)

    zaloha = await tasks.zaloha_pred_mazanim()
    if zaloha.get("status") != "ok":
        _flash(request, "Nic se nesmazalo, protože se nejdřív nepovedla záloha: {duvod}",
               "error", duvod=zaloha.get("message") or "?")
        return RedirectResponse(zpet, status_code=303)

    vysledek = await asyncio.to_thread(stats.smaz_z_archivu, vybrane, library_id)
    _flash(request, "Smazáno z archivu. Titulů: {t}, záznamů v historii: {n}.", "success",
           t=vysledek["titulu"], n=vysledek["prehravani"])
    return RedirectResponse(zpet, status_code=303)


def _over_prirazeni(stary_id: str, novy_id: str) -> None:
    """Kontrola dvojice ještě před zálohou - stejná pravidla jako přesun sám."""
    if not stary_id or not novy_id or stary_id == novy_id:
        raise stats.PrirazeniError("Vyber jiný titul, než je ten v archivu.")
    stary = stats.item(stary_id)
    if stary is None or not stary.get("is_missing"):
        raise stats.PrirazeniError("Přiřazovat jde jen položku z archivu.")
    novy = stats.item(novy_id)
    if novy is None or novy.get("is_missing"):
        raise stats.PrirazeniError("Cíl musí být titul, který v knihovně je.")


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
    # Se skrytymi jmeny vede odkaz na nahradni id (skryto-2) a skutecne
    # id ciziho divaka nenajde nic - viz pristup.divak_z_adresy.
    detail = stats.user_detail(pristup.divak_z_adresy(user_id, account), days)
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
    na Přehled. Zapíná se v Nastavení -> Rozhraní. Stejně tak pro toho,
    kdo v něm podle svých práv nemá co vidět (sekce.vidi_prehled, totéž
    co menu) - jen správce sem smí i s prázdným, aby si ho sestavil.
    """
    if not (sekce.vidi_prehled(account)
            or (account.get("is_admin") and sekce.je_zapnuty())):
        return RedirectResponse("/", status_code=303)
    return _vykresli_prehled(request, account, _days(request, days, od, do),
                             osobni=False)


@app.get("/account/prehled", response_class=HTMLResponse)
def muj_prehled(request: Request, days: Optional[int] = None,
                od: Optional[str] = None, do: Optional[str] = None,
                account: dict[str, Any] = Depends(require_login)):
    """Můj přehled - stránka, kterou si účet poskládá sám a vidí jen on.

    Účet se bere z relace, nikdy z adresy: cizí Můj přehled se otevřít nedá.
    Karta, ke které účet mezitím ztratil oprávnění, zůstane na svém místě
    s hláškou proč - sám si ji přidal, tak nemá hádat, co se rozbilo.
    """
    return _vykresli_prehled(request, account, _days(request, days, od, do),
                             osobni=True)


def _vykresli_prehled(request: Request, account: dict[str, Any], obdobi: Any,
                      *, osobni: bool):
    """Stránka přehledu - pro všechny i Můj přehled, jedna šablona.

    Oba ukazují jen data karet, které účet smí; ostatní se ani nespočítají,
    jinak by přehled byl zadní vrátka na stránky, kam člověk nesmí. Přehled
    pro všechny takovou kartu vynechá, Můj přehled ji nechá na místě
    s hláškou proč.
    """
    if osobni:
        rozvrzeni = sekce.nacti_rozvrzeni(account["id"])
        povolene = sekce.povolene(rozvrzeni, account)
        nadpis, adresa_stranky = _t("Můj přehled"), "/account/prehled"
        akce_ulozeni, muze_upravovat = "/account/prehled", True
    else:
        rozvrzeni = povolene = sekce.povolene(sekce.nacti_rozvrzeni(), account)
        # Nadpis i titulek okna: název od správce. Prázdný jen u přehledu
        # zapnutého dřív, než se název zadával, a bez restartu (migrace
        # ho doplní při startu) - pak to, jak se mu říkalo.
        nadpis, adresa_stranky = sekce.nazev() or _t("Vlastní přehled"), "/dashboard"
        # V ukázce tlačítko zůstává vidět schválně - stejně jako všude
        # jinde. Akci zastaví až `_demo_blokuje` a řekne to nahlas.
        akce_ulozeni, muze_upravovat = "/dashboard/layout", bool(account.get("is_admin"))
    # Spočítá se JEN to, co je poskládané a smí se. Přehled naproti tomu
    # počítá všech deset sekcí každému bez ohledu na to, na co se dívá.
    data = sekce.data_pro(povolene, obdobi)
    return templates.TemplateResponse(request, "vlastni_prehled.html", _context(
        request, account,
        days=obdobi,
        nadpis=nadpis,
        # Karty poznají, že nejsou na své stránce (mapa nenabízí stažení
        # GeoIP, tabulka uživatelů má jiný nadpis).
        v_prehledu=True,
        akce_ulozeni=akce_ulozeni,
        adresa_stranky=adresa_stranky,
        osobni=osobni,
        rozvrzeni=rozvrzeni,
        bez_prava={s.klic for s in rozvrzeni} - {s.klic for s in povolene},
        # Názvy oblastí pro hlášku „nemáš oprávnění k oblasti …".
        oblasti=pristup.PODLE_KLICE,
        # Do okna jde celý registr, co účet smí; co už v přehledu je, se
        # jen zašedne. Kdyby se použité sekce vynechávaly, seznam by při
        # přidávání a odebírání poskakoval a člověk by ztrácel místo.
        # Jen pro toho, kdo smí skládat - jinak se okno ani nekreslí.
        vsechny_sekce=sekce.povolene(list(sekce.SEZNAM), account) if muze_upravovat else [],
        pouzite={s.klic for s in rozvrzeni},
        sirky=sekce.SIRKY,
        # Přepínač období má smysl jen tehdy, když ho aspoň jedna karta
        # používá - jinak by tam stál a nic nedělal.
        potrebuje_obdobi=any(s.obdobi for s in povolene),
        # Karty Zjištění ukážou pár řádků a zbytek otevřou ve společném okně.
        potrebuje_okno_tabulka=any(s.okno_tabulka for s in povolene),
        muze_upravovat=muze_upravovat,
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


@app.post("/account/prehled")
def muj_prehled_uloz(request: Request, poradi: str = Form(""),
                     account: dict[str, Any] = Depends(require_login)):
    """Uloží Můj přehled - vždycky účtu z relace, `account_id` z formuláře
    se nečte vůbec.

    Uložit jde karta, kterou účet smí, nebo karta, která v jeho přehledu
    už je (i bez práva - jinak by se přeskládáním potichu ztratila). Cizí
    kartu si podvrženým formulářem nepřidá: spadla by mezi „bez práva"
    a ukazovala jen hlášku, ale do databáze nepatří.
    """
    smi = ({s.klic for s in sekce.povolene(list(sekce.SEZNAM), account)}
           | {s.klic for s in sekce.nacti_rozvrzeni(account["id"])})
    polozky = [(klic, sirka) for klic, sirka in sekce.rozdel_zadani(poradi or "")
               if klic in smi]
    ulozene = sekce.uloz_rozvrzeni(polozky, account["id"])
    _flash(request, "Uloženo." if ulozene else "Přehled je teď prázdný.",
           "success" if ulozene else "info")
    return RedirectResponse("/account/prehled", status_code=303)


@app.get("/insights", response_class=HTMLResponse)
def insights_page(request: Request, days: Optional[int] = None,
                  od: Optional[str] = None, do: Optional[str] = None,
                  account: dict[str, Any] = Depends(require_login)):
    days = _days(request, days, od, do)
    # Stránka je složená z karet Zjištění, data bere z jejich registru.
    return templates.TemplateResponse(request, "insights.html", _context(
        request, account, days=days, **sekce.data_oblasti("zebricky", days)))


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

    # Divák přihlášený svým jellyfinovým účtem vidí jen svou historii -
    # a nezáleží na tom, co si napíše do adresy. Filtr se mu proto vnutí
    # tady, ne až v šabloně: šablona rozhoduje o tom, co je vidět, ne
    # o tom, co se načte.
    divak = muj_divak(account)
    if divak:
        user_id = divak
    else:
        # Se skrytými jmény přijde z nabídky náhradní id; skutečné id
        # cizího diváka nenajde nic (pristup.divak_z_adresy).
        user_id = pristup.divak_z_adresy(user_id, account)

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
        # `is_disabled` jen proto, aby anonymizace poznala, že řádek je
        # divák (holé id a jméno má i knihovna nebo titul) - jinak by
        # nabídka ukázala skutečná jména i id. Se skrytými jmény podle id,
        # tedy podle čísla „Divák N": pořadí podle jména by je prozradilo.
        users=db.query_all(
            "SELECT id, name, is_disabled FROM users ORDER BY id"
            if pristup.skryva_jmena(account)
            else "SELECT id, name, is_disabled FROM users ORDER BY name"),
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
    # Stránka je složená z karet Sítě, data bere z jejich registru.
    return templates.TemplateResponse(request, "network.html", _context(
        request, account, days=days, **sekce.data_oblasti("sit", days)))


@app.post("/network/geoip")
async def network_geoip(request: Request,
                        account: dict[str, Any] = Depends(require_admin)):
    """Stahne (nebo obnovi) databazi GeoLite2 pro mapu.

    Jedina akce v aplikaci, ktera sahne jinam nez na Jellyfin - a jen na
    kliknuti. Proto ji smi spustit spravce, ne kazdy prihlaseny.
    """
    if not geoip.knihovna_je():
        _flash(request, "Chybí knihovna maxminddb, bez ní se databáze nepřečte.",
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


@app.post("/network/mapa-podrobna")
async def network_mapa_podrobna(request: Request,
                                account: dict[str, Any] = Depends(require_admin)):
    """Stáhne podrobnou mapu (Natural Earth 1:10M) - úloha na pozadí.

    Trvá pár minut (stažení asi 75 MB a rozřezání do dlaždic), proto se
    jen odpálí a stránka si počká (`?wait=task`). Pod zámkem úloh - když
    zrovna běží jiná (nebo tahle), druhá se nespustí.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
        return RedirectResponse("/network#mapa", status_code=303)
    # V jazyce serveru, ne v jazyce toho, kdo kliknul - viz i18n.bez_jazyka_uctu.
    asyncio.create_task(i18n.bez_jazyka_uctu(mapa_podrobna.spust_ulohu()))
    _flash(request, "Stahování podrobné mapy spuštěno. Trvá pár minut, stránka se sama obnoví.",
           "info")
    return RedirectResponse("/network?wait=task#mapa", status_code=303)


@app.post("/network/mapa-podrobna/smazat")
def network_mapa_podrobna_smazat(request: Request,
                                 account: dict[str, Any] = Depends(require_admin)):
    """Smaže podrobnou mapu - mapa se vrátí k zabudovanému obrysu.

    Ne během úlohy: stahování by mapu po dokončení zase zapsalo.
    """
    if scanner.is_scan_running():
        _flash(request, "Jiná úloha už běží, počkej na její dokončení.", "warning")
        return RedirectResponse("/network#mapa", status_code=303)
    mapa_podrobna.smaz()
    _flash(request, "Podrobná mapa je smazaná.", "success")
    return RedirectResponse("/network#mapa", status_code=303)


@app.get("/network/mapa/dlazdice/{podrobnost}/{x}/{y}")
def network_mapa_dlazdice(request: Request, podrobnost: str, x: str, y: str,
                          account: dict[str, Any] = Depends(require_login)):
    """Jedna dlaždice podrobné mapy - JSON s cestami SVG po vrstvách.

    Souřadnice se čtou jako text a kontrolují ručně: nesmysl (záporné,
    mimo síť, ne číslo) je 404, ne chyba validace. Platná dlaždice bez dat
    (moře) je prázdný objekt - prohlížeč se na ni pak neptá znovu.
    Prohlížeč si dlaždice drží týden; ETag se mění s každým stažením mapy.
    """
    if not all(re.fullmatch(r"\d{1,4}", hodnota) for hodnota in (podrobnost, x, y)):
        raise HTTPException(status_code=404)
    nastaveni = mapa_podrobna.PODROBNOSTI.get(int(podrobnost))
    stav = mapa_podrobna.stav()
    if (nastaveni is None or not stav["platna"]
            or int(x) >= nastaveni["n"] or int(y) >= nastaveni["n"]):
        raise HTTPException(status_code=404)
    otisk = hashlib.sha256(f"{stav['kdy']}|{podrobnost}|{x}|{y}".encode()).hexdigest()[:20]
    hlavicky = {"Cache-Control": "private, max-age=604800", "ETag": f'"{otisk}"'}
    if request.headers.get("if-none-match") == hlavicky["ETag"]:
        return Response(status_code=304, headers=hlavicky)
    obsah = mapa_podrobna.dlazdice(int(podrobnost), int(x), int(y)) or "{}"
    return Response(content=obsah, media_type="application/json", headers=hlavicky)


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
    """Právě běžící přehrávání jako JSON pro přihlášeného (mimo API s klíči).

    Se skrytými jmény anonymizované jako stránka „Právě se hraje" - dřív
    tu byla surová data i se jmény, zařízeními a IP adresami.
    """
    return pristup.anonymizuj_pro({"sessions": stats.active_sessions()}, account)


# Routy Nastaveni bydli zvlast - je jich pres tricet a s ostatnimi
# strankami nemaji nic spolecneho. Import je az tady dole schvalne:
# modul si bere `web_zaklad`, ne tenhle soubor, takze nic nevznika
# dokola.
from . import web_nastaveni  # noqa: E402

app.include_router(web_nastaveni.router)

# Nástroje jen pro vývoj (jellyscope/vyvoj.py - datum pro sezónní výzdobu).
# Do produ a dema se nekopírují (deploy/sync-do-produ.py, JEN_DEV), takže
# tam soubor chybí a tady se jen přeskočí. find_spec, ne `except
# ImportError`: chyba UVNITŘ vyvoj.py se nemá tiše spolknout.
if importlib.util.find_spec(f"{__package__}.vyvoj") is not None:
    from . import vyvoj  # noqa: E402
    vyvoj.zapoj(app, templates)
else:
    vyvoj = None
